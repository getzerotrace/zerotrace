"""`zerotrace doctor`: is this machine/repo actually protected, and is the model trustworthy?

Each check is a small function that appends rows to a Report, so adding a check never grows one
big function. A new check also gets a line in ABOUT, which the full-screen view shows next to
its result.
"""
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__, gitutil, installer, platform_env
from .ui import glyphs
from .config import Config, load_config

# Status keys, not glyphs: Report.table() renders whatever the destination console can
# encode (a legacy cp437 console gets "+"/"x" instead of "✓"/"✗", not "?").
OK, WARN, FAIL = "ok", "warn", "fail"
_STATUS_STYLE = {"ok": "green", "warn": "yellow", "fail": "red"}
MODEL_INTEGRITY = "model integrity"
MODEL_AVAILABLE = "model available"
MODEL_WARM_UP = "model warm-up"
REPO_OVERRIDE = "repo override"
REPO_PROTECTED = "this repo protected"

# What each check means and why it matters, in a sentence or two. The table shows only the
# result; the full-screen view (`zerotrace doctor -i`) shows this beside it.
ABOUT = {
    "python": "The Python running ZeroTrace. The hook scripts call it by this absolute path, "
              "so if the interpreter moves or is removed, the hooks stop running.",
    "git": "Git itself. The machine-wide install relies on core.hooksPath (git 2.9 or later).",
    "environment": "Where ZeroTrace is running. Windows and WSL keep separate git "
                   "configurations, so each side needs its own install.",
    "WSL interop": "Whether this WSL distro can start Windows programs. Without it, set up "
                   "the Windows side from Windows.",
    "repo filesystem": "A repo on a Windows drive (/mnt/<drive>) is slow from WSL and can "
                       "lose the executable bit on hook scripts.",
    "system hooksPath": "The hooks directory git uses for every repo of every user. An IT "
                        "rollout (`install --system`) owns it.",
    "global hooksPath": "The hooks directory git uses for every repo of this user. "
                        "`zerotrace install --global` owns it; if another tool does, commits "
                        "are not scanned.",
    "system templateDir (fallback)": "Hooks git copies into newly created or cloned repos. "
                                     "Only a fallback: the hooksPath protects existing repos.",
    "global templateDir (fallback)": "Hooks git copies into newly created or cloned repos. "
                                     "Only a fallback: the hooksPath protects existing repos.",
    "repo": "Repo checks run only inside a git repository.",
    "repo ownership": "Git refuses to run hooks in a repo owned by another user until it is "
                      "listed as a safe.directory.",
    REPO_OVERRIDE: "This repo sets its own core.hooksPath (husky and similar tools do), which "
                   "replaces the global one. Unless those hooks call ZeroTrace, nothing is "
                   "scanned here.",
    REPO_PROTECTED: "Whether a commit in this repo actually runs ZeroTrace, whichever hooks "
                    "directory wins.",
    "config layers": "Where the policy came from, lowest first: built-in defaults, the org "
                     "policy, ~/.zerotrace/config.yml, then this repo's .zerotrace.yml.",
    "org-locked keys": "Settings your organisation fixed. Repo and user config cannot "
                       "override them.",
    "rule pack": "The deterministic detectors. They decide every finding on their own; the "
                 "model is never asked about a HIGH or CRITICAL one.",
    "AI tie-break": "The local model settles only ambiguous (MEDIUM) findings. It sees "
                    "redacted shape features, never a value, and it cannot unblock anything "
                    "severe.",
    "model endpoint": "Where the tie-break model is served. A remote endpoint needs "
                      "allow_remote and https.",
    MODEL_AVAILABLE: "Whether the model answers. When it does not, ambiguous findings warn "
                     "instead of being settled: ZeroTrace fails closed.",
    "docker": "Docker runs the local model container. It is optional: without it the "
              "deterministic rules still block every HIGH/CRITICAL finding and ambiguous "
              "ones warn.",
    "model image": "The pinned Ollama image the model container runs. `zerotrace model up` "
                   "downloads it the first time.",
    "model container": "The zerotrace-ollama container. `zerotrace model up` starts it, "
                       "`zerotrace model down` stops it.",
    "model host": "Where the model is served from when it is not a local container.",
    "next step": "The one thing to do to get the AI tie-break working here.",
    MODEL_WARM_UP: "Loading the model into memory ahead of time, so the first ambiguous "
                   "finding does not wait for it.",
    MODEL_INTEGRITY: "The served model's digest, compared with the one pinned in "
                     ".zerotrace.yml, so a swapped or tampered model is noticed.",
    "egress": "What leaves this machine for the model: redacted shape features only.",
}


class Check(NamedTuple):
    """One row of the report."""
    status: str             # OK | WARN | FAIL
    name: str
    result: str


@dataclass
class Report:
    rows: list[Check] = field(default_factory=list)
    failures: int = 0
    # Told about each check as it completes, so a live view can show results as they arrive
    # instead of after the slowest (network) check.
    listener: Callable[[Check], None] | None = None

    def add(self, status: str, check: str, result: str) -> None:
        row = Check(status, check, result)
        self.rows.append(row)
        self.failures += status == FAIL
        if self.listener is not None:
            self.listener(row)

    def table(self, console: Console | None = None) -> Table:
        marks = glyphs.for_console(console or Console())
        title = glyphs.sanitize(f"ZeroTrace doctor · v{__version__}", console or Console())
        table = Table(title=title, show_header=False, expand=False,
                      box=glyphs.box_for(console or Console()))
        table.add_column("", width=2)
        table.add_column("Check", style="bold")
        table.add_column("Result", overflow="fold")
        for status, check, result in self.rows:
            mark = f"[{_STATUS_STYLE.get(status, 'white')}]{marks.get(status, status)}[/]"
            # Results quote paths and commands: data, never markup.
            table.add_row(mark, check, escape(glyphs.sanitize(result, console or Console())))
        return table


def _pin_digest(root: str, digest: str) -> tuple[bool, str]:
    """Write the digest into the repo config. Returns (pinned, what happened)."""
    path = os.path.join(root, ".zerotrace.yml")
    if not os.path.exists(path):
        return False, "not pinned: no .zerotrace.yml here (run `zerotrace init` first)"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if re.search(r"(?m)^(\s+)(digest|sha256):.*$", text):
        text = re.sub(r"(?m)^(\s+)(digest|sha256):.*$", rf'\1digest: "{digest}"', text, count=1)
    elif re.search(r"(?m)^model:\s*$", text):
        text = re.sub(r"(?m)^model:\s*$", f'model:\n  digest: "{digest}"', text, count=1)
    else:
        text += f'\nmodel:\n  digest: "{digest}"\n'
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True, f"pinned {digest[:19]}… in .zerotrace.yml"


def _check_toolchain(report: Report) -> None:
    report.add(OK, "python", sys.executable)
    version = gitutil.git("--version", check=False).strip()
    report.add(OK if version else FAIL, "git", version or "git not found")


def _check_environment(report: Report) -> None:
    env = platform_env.detect()
    if env.kind == "wsl":
        label = f"WSL{env.wsl_version or '?'}" + (f" ({env.distro_name})" if env.distro_name else "")
        report.add(OK, "environment", label)
        if not env.interop_available:
            report.add(WARN, "WSL interop",
                       "disabled for this distro; the Windows side must be installed "
                       "separately (run `zerotrace install --global` there too)")
        if gitutil.in_repo():
            fs_class = platform_env.classify_path(gitutil.repo_root())
            if fs_class == "drvfs":
                report.add(WARN, "repo filesystem",
                           "this repo is on a Windows drive (/mnt/<drive>); performance and "
                           "hook install may be degraded. Consider cloning under $HOME instead")
    else:
        report.add(OK, "environment", env.kind)


def _check_install(report: Report) -> None:
    for scope in ("system", "global"):
        value = gitutil.config_get(installer.HOOKS_PATH_KEY, scope)
        if not value:
            report.add(WARN if scope == "global" else OK, f"{scope} hooksPath", "not set")
            continue
        managed = installer.is_managed(value)
        report.add(OK if managed else WARN, f"{scope} hooksPath",
                   f"{value} ({'ZeroTrace-managed' if managed else 'not ZeroTrace'})")

    for scope in ("system", "global"):
        value = gitutil.config_get(installer.TEMPLATE_DIR_KEY, scope)
        if not value:
            continue  # optional fallback; absent is fine as long as hooksPath is set
        managed = installer.is_managed(os.path.join(value, "hooks"))
        report.add(OK if managed else WARN, f"{scope} templateDir (fallback)",
                   f"{value} ({'ZeroTrace-managed' if managed else 'not ZeroTrace'})")


def _repo_runs_zerotrace(hooks_dir: str) -> bool:
    if installer.is_managed(hooks_dir):
        return True
    pre_commit = os.path.join(hooks_dir, "pre-commit")
    if not os.path.exists(pre_commit):
        return False
    with open(pre_commit, encoding="utf-8", errors="replace") as f:
        return "zerotrace" in f.read()


def _check_repo(report: Report) -> None:
    if not gitutil.in_repo():
        report.add(OK, "repo", "not inside a git repo (repo checks skipped)")
        return
    dubious = gitutil.git_stderr("status")
    if "dubious ownership" in dubious.lower():
        report.add(FAIL, "repo ownership",
                   "git refuses this repo (root-owned repo, network share, or `sudo`): run "
                   "`git config --global --add safe.directory <path>` or the hook will never run")
        return
    os.chdir(gitutil.repo_root())
    hooks_dir = installer.effective_hooks_dir()
    runs = _repo_runs_zerotrace(hooks_dir)
    local = gitutil.config_get(installer.HOOKS_PATH_KEY, "local")
    if local and not installer.is_managed(local):
        report.add(OK if runs else FAIL, REPO_OVERRIDE,
                   f"local {installer.HOOKS_PATH_KEY}={local} overrides global"
                   + ("" if runs else ". Not protected: run `zerotrace doctor --fix`"))
    report.add(OK if runs else FAIL, REPO_PROTECTED,
               f"yes (hooks: {hooks_dir})" if runs else
               "no. Run `zerotrace install --global`")


def _check_policy(report: Report, cfg: Config) -> None:
    report.add(OK, "config layers", ", ".join(cfg.sources) or "built-in defaults")
    if cfg.locked:
        report.add(OK, "org-locked keys", ", ".join(cfg.locked))
    from .detectors import rulepack
    rules = rulepack.load_rules(cfg.rules_extra, cfg.repo_root)
    report.add(OK, "rule pack", f"{len(rules)} provider rules + code-assignment, sensitive-file, "
                                "detect-secrets and PII detectors")


def _check_endpoint(report: Report, cfg: Config) -> None:
    from .classifier import llm
    where = "REMOTE" if cfg.model_is_remote else "local"
    try:
        llm.check_endpoint(cfg)
        report.add(OK, "model endpoint", f"{cfg.model_runtime} @ {cfg.model_endpoint} ({where})")
    except llm.EndpointRefused as exc:
        report.add(FAIL, "model endpoint", str(exc))


def _check_integrity(report: Report, cfg: Config, digest: str, pin_model: bool) -> None:
    if cfg.model_digest:
        match = digest.startswith(cfg.model_digest.removeprefix("sha256:"))
        report.add(OK if match else FAIL, MODEL_INTEGRITY,
                   "served model matches the pinned digest" if match else
                   f"MISMATCH: pinned {cfg.model_digest[:19]}…, served {digest[:19]}…")
    elif pin_model and gitutil.in_repo():
        pinned, what = _pin_digest(cfg.repo_root, digest)
        report.add(OK if pinned else WARN, MODEL_INTEGRITY, what)
    else:
        report.add(WARN, MODEL_INTEGRITY, "digest not pinned (`zerotrace doctor --pin-model`)")


def _check_model_host(report: Report, cfg: Config) -> str:
    """Why the model is not answering: Docker, the image, the container - in that order.

    Only asked when the endpoint is silent. When the model answers, how it is being served is
    not a question anybody has, and `docker info` on a sleeping Docker Desktop is slow.
    Returns the one thing to do about it, which the caller prints under the model row.
    """
    from . import modelhost
    status = modelhost.probe(cfg, check_model=False)
    for state, check, result in modelhost.rows(status):
        if check == "model":
            continue                       # the model row is the caller's to add
        report.add(OK if state == "ok" else WARN, check, result)
    return status.hint


def _check_warm(report: Report, cfg: Config) -> None:
    from .classifier import llm
    took = llm.warm(cfg)
    report.add(OK if took is not None else WARN, MODEL_WARM_UP,
               f"loaded and pinned in memory for {cfg.model_keep_alive} ({took:.1f} s)"
               if took is not None else "warm-up failed; the first MEDIUM finding will be slow")


def _check_model(report: Report, cfg: Config, pin_model: bool, warm: bool) -> None:
    if not cfg.model_enabled:
        report.add(OK, "AI tie-break", "disabled; MEDIUM findings will WARN (fail closed)")
        return
    from .classifier import llm
    _check_endpoint(report, cfg)

    start = time.monotonic()
    digest = llm.model_digest(cfg)
    latency = (time.monotonic() - start) * 1000
    if digest is None:
        hint = _check_model_host(report, cfg)
        report.add(WARN, MODEL_AVAILABLE,
                   f"{cfg.model_name} not reachable/served ({latency:.0f} ms). MEDIUM findings "
                   "will WARN. Start it: `zerotrace model up`")
        if hint:
            report.add(WARN, "next step", hint)
    else:
        report.add(OK, MODEL_AVAILABLE, f"{cfg.model_name} · {digest[:19]}… ({latency:.0f} ms)")
        if warm:
            _check_warm(report, cfg)
        _check_integrity(report, cfg, digest, pin_model)

    report.add(OK, "egress",
               "only redacted shape features are sent (no raw values), over TLS"
               if cfg.model_is_remote else
               "localhost only; proxy env vars bypassed for model calls")


def collect(pin_model: bool = False, warm: bool = False,
            listener: Callable[[Check], None] | None = None) -> Report:
    """Run every check and return the results, printing nothing.

    Separate from `doctor()` so the full-screen view can re-run the checks without the
    table landing in the middle of its screen. `listener` hears about each check as soon as
    it completes.
    """
    report = Report(listener=listener)
    _check_toolchain(report)
    _check_environment(report)
    _check_install(report)
    _check_repo(report)
    cfg = load_config()
    _check_policy(report, cfg)
    _check_model(report, cfg, pin_model, warm)
    return report


def doctor(pin_model: bool = False, warm: bool = False) -> int:
    report = collect(pin_model, warm)
    console = Console()
    console.print(report.table(console))
    return 1 if report.failures else 0
