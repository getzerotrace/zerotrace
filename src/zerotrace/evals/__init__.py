"""`zerotrace eval`: measure the AI tie-break on labelled synthetic cases.

Values are generated at run time, so the repo holds no realistic-looking secrets.
Labels: real (must not be allowed), placeholder / fixture (ideally allowed).
"""
import json
import os
import secrets
import statistics
import string
import time
from dataclasses import dataclass, field, replace
from importlib import resources

from rich.console import Console
from rich.table import Table

from ..config import load_config
from ..detectors import Finding
from ..collectors.staged_diff import classify_file

_WORDS = ["blue", "falcon", "river", "stone", "maple", "orbit", "cedar", "lunar", "quartz"]


def _shuffled(items: list[str]) -> list[str]:
    """Fisher-Yates driven by `secrets`. `SystemRandom().shuffle` would be just as random, but it
    is a `random.Random` subclass, and a generated password should never read as coming from
    the non-cryptographic module."""
    items = list(items)
    for i in range(len(items) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        items[i], items[j] = items[j], items[i]
    return items


def _gen(spec: str) -> str:
    kind, _, arg = spec.partition(":")
    if kind == "lit":
        return arg
    n = int(arg)
    alphabet = {
        "base62": string.ascii_letters + string.digits,
        "hex": "0123456789abcdef",
        "b64": string.ascii_letters + string.digits + "+/",
        "digits": string.digits,
    }.get(kind)
    if alphabet:
        return "".join(secrets.choice(alphabet) for _ in range(n))
    if kind == "pw":
        core = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase),
                secrets.choice(string.digits), secrets.choice("!@#%^*-_")]
        core += [secrets.choice(string.ascii_letters + string.digits + "!@#%^*-_")
                 for _ in range(n - 4)]
        return "".join(_shuffled(core))
    if kind == "words":
        return "-".join(secrets.choice(_WORDS) for _ in range(n))
    raise ValueError(f"unknown generator {spec}")


def load_cases(path: str | None) -> list[dict]:
    if path:
        resolved = os.path.realpath(os.path.expanduser(path))
        if not os.path.isfile(resolved):
            raise SystemExit(f"zerotrace eval: no such cases file: {path}")
        with open(resolved, encoding="utf-8") as f:
            text = f.read()
    else:
        text = resources.files("zerotrace.evals").joinpath("classifier_cases.jsonl").read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _finding(case: dict) -> Finding:
    value = _gen(case["gen"])
    text = case["line"].replace("{v}", value)
    line = text.splitlines()[-1]
    return Finding(
        rule_id="hardcoded-secret", kind="hardcoded_secret", severity="medium", confidence=0.6,
        path=case["path"], line_no=1, file_class=classify_file(case["path"]), line_text=line,
        context_snippet=text, identifier=case.get("identifier", ""), source="code_assign",
        matched_value=value,
    )


@dataclass
class Score:
    """Outcome counters for one model across all cases."""
    real: int = 0
    benign: int = 0
    unsafe: int = 0          # a real secret the model would ALLOW: the metric that matters
    escalated: int = 0
    allowed_benign: int = 0
    failed: int = 0
    latencies: list[float] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)

    def percentile(self, fraction: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[max(0, int(len(ordered) * fraction) - 1)]

    def row(self, model: str) -> tuple[str, ...]:
        return (model, f"{self.unsafe}/{self.real}", f"{self.escalated}/{self.real}",
                f"{self.allowed_benign}/{self.benign}", str(self.failed),
                f"{statistics.median(self.latencies) if self.latencies else 0:.0f}",
                f"{self.percentile(0.95):.0f}")


def _judge(score: Score, case: dict, verdict, cfg) -> None:
    is_real = case["label"] == "real"
    score.real += is_real
    score.benign += not is_real
    if verdict is None:
        score.failed += 1
        return
    allow = (verdict.classification == "TEST_FIXTURE_OR_PLACEHOLDER"
             and verdict.confidence >= cfg.model_allow_threshold)
    block = (verdict.classification == "REAL_SECRET"
             and verdict.confidence >= cfg.model_escalate_threshold)
    if is_real and allow:
        score.unsafe += 1
        score.misses.append(f"{case['id']}: {verdict.reason}")
    if is_real and block:
        score.escalated += 1
    if not is_real and allow:
        score.allowed_benign += 1


def _score_model(cfg, cases: list[dict], runs: int) -> Score:
    from ..classifier import llm
    score = Score()
    for _ in range(runs):
        for case in cases:
            finding = _finding(case)
            start = time.monotonic()
            verdict = llm.classify(finding, cfg)
            score.latencies.append((time.monotonic() - start) * 1000)
            _judge(score, case, verdict, cfg)
    return score


def run_eval(cases_path: str | None, models: list[str], runs: int = 1) -> int:
    from ..classifier import llm
    console = Console()
    base = load_config()
    cases = load_cases(cases_path)
    table = Table(title=f"AI tie-break eval · {len(cases)} cases × {runs} run(s)")
    for col in ("model", "unsafe allows (real→allow)", "escalated real→block",
                "noise removed (placeholder/fixture→allow)", "no verdict", "p50 ms", "p95 ms"):
        table.add_column(col, justify="right" if col != "model" else "left")

    worst = 0
    for model in models or [base.model_name]:
        cfg = replace(base, model_name=model)
        if llm.warm(cfg) is None:
            table.add_row(model, "-", "-", "-", "unreachable", "-", "-")
            continue
        score = _score_model(cfg, cases, runs)
        worst = max(worst, score.unsafe)
        table.add_row(*score.row(model))
        for miss in score.misses:
            console.print(f"[red]unsafe allow[/] {model}: {miss}")
    console.print(table)
    console.print("[dim]Unsafe allows are the metric that matters: a MEDIUM finding the model "
                  "let through that was actually real. HIGH findings never reach the model.[/]")
    return 1 if worst else 0


__all__ = ["run_eval", "load_cases"]
