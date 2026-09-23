"""Classify every MEDIUM finding concurrently under one deadline, with a verdict cache.

Cache entries are keyed by finding fingerprint + model + prompt version, and only store the
validated verdict (enum, number, short reason). No values are cached.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import llm, prompt, schema
from .. import gitutil
from ..audit import exceptions as audit_exceptions
from ..audit.fingerprint import of_finding

_CACHE_TTL_SECONDS = 30 * 24 * 3600


def _cache_path() -> str:
    return os.path.join(gitutil.state_dir(), "cache", "verdicts.json")


def _load_cache() -> dict:
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1, sort_keys=True)
    except OSError:
        pass


def _key(finding, cfg) -> str:
    return f"{of_finding(finding)}|{cfg.model_name}|{cfg.model_digest}|p{prompt.PROMPT_VERSION}"


def _needs_model(findings: list, cfg) -> list[int]:
    return [i for i, f in enumerate(findings)
            if f.severity in cfg.warn_severity and not audit_exceptions.is_active(of_finding(f))]


def _from_cache(findings: list, cfg, todo: list[int], cache: dict, now: float):
    """(cached verdicts, indices still to classify)."""
    cached: dict[int, schema.Verdict] = {}
    pending: list[int] = []
    for i in todo:
        entry = cache.get(_key(findings[i], cfg))
        if entry and now - entry.get("at", 0) < _CACHE_TTL_SECONDS:
            cached[i] = schema.Verdict(entry["classification"], float(entry["confidence"]),
                                       entry["reason"])
        else:
            pending.append(i)
    return cached, pending


def _classify_pending(findings: list, cfg, pending: list[int], cache: dict, now: float):
    """Classify concurrently under one deadline; anything unfinished fails closed to WARN."""
    results: dict[int, schema.Verdict | None] = {}
    all_values = tuple({f.matched_value for f in findings if f.matched_value})
    pool = ThreadPoolExecutor(max_workers=max(1, cfg.model_max_parallel))
    futures = {pool.submit(llm.classify, findings[i], cfg, all_values): i for i in pending}
    done, not_done = wait(futures, timeout=cfg.model_timeout_seconds + 2.0)
    for future in done:
        index = futures[future]
        try:
            verdict = future.result()
        except Exception:
            verdict = None
        results[index] = verdict
        if verdict is not None:
            cache[_key(findings[index], cfg)] = {
                "classification": verdict.classification, "confidence": verdict.confidence,
                "reason": verdict.reason, "at": now,
            }
    for future in not_done:
        results[futures[future]] = None       # deadline passed -> WARN
    pool.shutdown(wait=False, cancel_futures=True)
    return results


def classify_medium(findings: list, cfg) -> dict[int, "schema.Verdict | None"]:
    """Return {index: verdict-or-None} for each MEDIUM finding that needs the model."""
    todo = _needs_model(findings, cfg)
    if not todo:
        return {}
    cache = _load_cache()
    now = time.time()
    results, pending = _from_cache(findings, cfg, todo, cache, now)
    if pending:
        results.update(_classify_pending(findings, cfg, pending, cache, now))
        _save_cache(cache)
    return results
