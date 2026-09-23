"""Model call over plain HTTP (stdlib only). Runtimes: Ollama (/api/chat) or any
OpenAI-compatible server (vLLM, LiteLLM, SageMaker/Bedrock gateways, Ollama's /v1).

Local endpoints bypass proxy env vars, so a redacted prompt can't be routed off-box by a
proxy setting. Remote endpoints need `model.allow_remote: true` AND https.
Every failure returns None, and the policy engine then fails closed to WARN.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import prompt, redact, schema

_digest_lock = threading.Lock()
_digest_cache: dict[tuple[str, str], str | None] = {}


class EndpointRefused(RuntimeError):
    pass


def check_endpoint(cfg) -> None:
    url = urlparse(cfg.model_endpoint)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise EndpointRefused(f"invalid model endpoint {cfg.model_endpoint!r}")
    if cfg.model_is_remote:
        if not cfg.model_allow_remote:
            raise EndpointRefused(
                f"remote model endpoint {url.hostname} refused: set model.allow_remote: true "
                "(only redacted features are ever sent)")
        if url.scheme != "https":
            raise EndpointRefused("remote model endpoints must use https://")


def opener(cfg):
    """The URL opener for this endpoint. A local endpoint bypasses every proxy env var, so a
    redacted prompt - or a model download - cannot be routed off-box by a proxy setting."""
    if cfg.model_is_remote:
        return urllib.request.build_opener()  # corporate proxies allowed for remote
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def forget_digest() -> None:
    """Drop the cached model identity.

    The cache remembers "not served" too, which is the right answer for the length of one
    commit and the wrong one the moment something starts the container - `zerotrace model up`
    and `zerotrace setup` call this after they change what is running.
    """
    with _digest_lock:
        _digest_cache.clear()


def _headers(cfg) -> dict:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get(cfg.model_auth_env or "", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def http_json(cfg, method: str, path: str, payload: dict | None, timeout: float) -> dict:
    check_endpoint(cfg)
    url = cfg.model_endpoint.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers(cfg))
    with opener(cfg).open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _openai_path(cfg, suffix: str) -> str:
    return suffix if cfg.model_endpoint.rstrip("/").endswith("/v1") else "/v1" + suffix


def chat(cfg, messages: list[dict], timeout: float | None = None) -> str:
    timeout = timeout or cfg.model_timeout_seconds
    if cfg.model_runtime == "ollama":
        resp = http_json(cfg, "POST", "/api/chat", {
            "model": cfg.model_name, "messages": messages, "stream": False,
            "format": schema.JSON_SCHEMA, "keep_alive": cfg.model_keep_alive,
            "options": {"temperature": 0, "seed": 0, "num_predict": 96, "num_ctx": 4096},
        }, timeout)
        return (resp.get("message") or {}).get("content") or ""
    if cfg.model_runtime == "openai":
        resp = http_json(cfg, "POST", _openai_path(cfg, "/chat/completions"), {
            "model": cfg.model_name, "messages": messages, "temperature": 0, "seed": 0,
            "max_tokens": 96,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "verdict", "schema": schema.JSON_SCHEMA, "strict": True}},
        }, timeout)
        choices = resp.get("choices") or [{}]
        return (choices[0].get("message") or {}).get("content") or ""
    raise EndpointRefused(f"unsupported runtime {cfg.model_runtime!r}")


def model_digest(cfg, timeout: float = 3.0) -> str | None:
    """Identity of the served model (Ollama digest; OpenAI-compatible: the model id)."""
    key = (cfg.model_endpoint, cfg.model_name)
    with _digest_lock:
        if key in _digest_cache:
            return _digest_cache[key]
    digest: str | None = None
    try:
        if cfg.model_runtime == "ollama":
            tags = http_json(cfg, "GET", "/api/tags", None, timeout)
            for m in tags.get("models", []):
                if m.get("name") == cfg.model_name or m.get("model") == cfg.model_name:
                    digest = m.get("digest")
                    break
        elif cfg.model_runtime == "openai":
            models = http_json(cfg, "GET", _openai_path(cfg, "/models"), None, timeout)
            ids = {m.get("id") for m in models.get("data", [])}
            digest = cfg.model_name if cfg.model_name in ids else None
    except Exception:
        digest = None
    with _digest_lock:
        _digest_cache[key] = digest
    return digest


def integrity_ok(cfg) -> bool:
    if not cfg.model_digest:
        return True  # not pinned; `zerotrace doctor` reports it
    served = model_digest(cfg)
    return served is not None and served.startswith(cfg.model_digest.removeprefix("sha256:"))


def build_messages(finding, extra_values: tuple[str, ...] = ()) -> list[dict] | None:
    candidate = finding.matched_value or ""
    window = redact.scrub_window(finding.context_snippet or finding.line_text or "",
                                 candidate=candidate, extra_values=extra_values)
    feats = redact.features(finding)
    msgs = prompt.messages(feats, window)
    final = msgs[-1]["content"]
    if candidate and len(candidate) >= 4 and candidate in final:
        return None  # never send a raw value, whatever went wrong upstream
    return msgs


def classify(finding, cfg, extra_values: tuple[str, ...] = ()) -> "schema.Verdict | None":
    if cfg.model_runtime not in ("ollama", "openai"):
        return None  # off / unsupported -> fail closed to WARN
    msgs = build_messages(finding, extra_values)
    if msgs is None:
        return None
    try:
        if not integrity_ok(cfg):
            return None  # served model doesn't match the pinned digest
        raw = chat(cfg, msgs)
    except (EndpointRefused, urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None  # unreachable/timeout/refused/bad JSON -> fail closed to WARN

    verdict = schema.parse(raw)
    if verdict is None:
        return None
    candidate = finding.matched_value or ""
    # Defense in depth: discard if the model echoed the real (unredacted) value.
    if candidate and len(candidate) >= 6 and candidate in verdict.reason:
        return None
    return verdict


def warm(cfg) -> float | None:
    """Load the model into memory (Ollama). Returns seconds taken, or None on failure."""
    start = time.monotonic()
    try:
        if cfg.model_runtime == "ollama":
            http_json(cfg, "POST", "/api/generate", {
                "model": cfg.model_name, "prompt": "", "keep_alive": cfg.model_keep_alive,
            }, timeout=max(cfg.model_timeout_seconds, 120))
        else:
            chat(cfg, [{"role": "user", "content": "ping"}], timeout=60)
    except Exception:
        return None
    return time.monotonic() - start
