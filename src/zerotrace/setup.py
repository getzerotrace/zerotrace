"""`zerotrace setup`: the guided half of an install, after a Python environment exists.

`install.sh` and `install.ps1` bootstrap an interpreter, build the environment and install the
package - everything that must happen in a shell because Python is not there yet. From that
point the two of them hand over to this module, so Docker handling, the hook install, the
proof that it works and the closing summary exist ONCE for macOS, Linux, WSL and Windows
rather than three times in three languages.

Run on its own it is a repair tool: `zerotrace setup` re-checks Docker, brings the model up,
re-registers the hooks and proves a commit is really blocked.

Order matters, and it is not quite the order the steps are numbered in. Docker is *inspected*
before anything is installed, because its state changes what the rest of the run will say -
but the model image is only *pulled* after the hooks are in place. A pull is gigabytes and
minutes; someone who gives up on it mid-way should still be left with a working guardrail
rather than an install that did nothing.
"""
import os
import secrets
import string
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

from . import __version__, gitutil, installer, modelhost
from .config import Config, load_config
from .ui import glyphs, theme

OK, WARN, FAIL = "ok", "warn", "fail"

STEPS = (
    "Checking Docker",
    "Installing the git hooks",
    "Preparing the local model",
    "Validating the install",
)


@dataclass
class Outcome:
    """What the run did, as rows a caller can print and a code the shell can act on."""
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    guardrail_ok: bool = False
    model_status: modelhost.Status | None = None
    next_steps: list[str] = field(default_factory=list)

    def add(self, state: str, check: str, result: str) -> None:
        self.rows.append((state, check, result))

    @property
    def code(self) -> int:
        """0 when the guardrail is in place. A missing model is never a failed install: the
        deterministic rules are the product, the model only settles ambiguous findings."""
        return 0 if self.guardrail_ok else 1


# --- the steps ----------------------------------------------------------------------------

def _check_docker(outcome: Outcome, cfg: Config) -> modelhost.Status:
    status = modelhost.probe(cfg)
    outcome.model_status = status
    for state, check, result in modelhost.rows(status):
        if check == "model":
            continue          # the model step below owns that row; one machine, one sentence
        outcome.add(state, check, result)
    return status


def _install_hooks(outcome: Outcome, scope: str, on_step: Callable[[str], None] | None) -> bool:
    try:
        lines = installer.install(scope, on_step=on_step)
    except (PermissionError, gitutil.GitError) as exc:
        outcome.add(FAIL, "git hooks", f"could not install: {exc}")
        return False
    hooks_dir = installer.default_hooks_dir(scope)
    outcome.add(OK, "git hooks", f"{len(installer.HOOK_NAMES)} shims in {hooks_dir} "
                                 f"({scope} core.hooksPath)")
    for line in lines:
        if "chained" in line:            # another tool's hooks are still running: worth saying
            outcome.add(OK, "existing hooks", line)
    return True


def _prepare_model(outcome: Outcome, cfg: Config, status: modelhost.Status,
                   pull: bool, say: Callable[[str], None]) -> None:
    if not pull:
        outcome.add(WARN, "model", "skipped (--no-model). Run `zerotrace model up` when you "
                                   "want the AI tie-break")
        return
    if status.usable:
        outcome.add(OK, "model", f"{cfg.model_name} already answers at {cfg.model_endpoint}")
        return
    if status.docker != modelhost.RUNNING or not status.local:
        # Already reported by the Docker step, with the fix; do not say it twice.
        outcome.next_steps.append(status.hint)
        return
    result = modelhost.up(cfg, on_event=say)
    outcome.model_status = result.status or status
    if result.ok:
        outcome.add(OK, "model", f"{cfg.model_name} is served at {cfg.model_endpoint}")
    else:
        outcome.add(WARN, "model", result.lines[-1] if result.lines else
                    "the model did not come up; MEDIUM findings will WARN")
        outcome.next_steps.append("run `zerotrace model up` once Docker settles")


def _validate(outcome: Outcome, scope: str) -> bool:
    """Prove the install, rather than assert it.

    Three questions, in the order they can fail: is the tool runnable, does git point at our
    hooks, and does a commit carrying a secret actually get stopped. The last one is the only
    one that tests the whole chain - the shim, the interpreter path, the rule pack and the
    policy - which is exactly the chain that breaks silently.
    """
    value = installer.HOOKS_PATH_KEY
    configured = gitutil.config_get(value, scope)
    if not configured or not installer.is_managed(configured):
        outcome.add(FAIL, "hooks registered", f"git {scope} {value} is {configured or 'unset'}")
        return False
    outcome.add(OK, "hooks registered", f"git {scope} {value} = {configured}")

    blocked, detail = _commit_is_blocked(configured)
    outcome.add(OK if blocked else FAIL, "a secret really is blocked", detail)
    return blocked


def _commit_is_blocked(hooks_dir: str) -> tuple[bool, str]:
    """Stage a credential-shaped value in a throwaway repo and try to commit it.

    The value is built at run time: a realistic-looking key must never be a literal in this
    repository, not even in a test (our own scanner would flag it, correctly).
    """
    # Assembled, not written out: a 32-character base32 alphabet in one literal is itself a
    # high-entropy string, and our own scanner is right to flag it (it just did).
    alphabet = string.ascii_uppercase + "234567"
    fake = "AKIA" + "".join(secrets.choice(alphabet) for _ in range(16))
    try:
        with tempfile.TemporaryDirectory(prefix="zerotrace-selftest-") as work:
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            git = ["git", "-c", f"core.hooksPath={hooks_dir}",
                   "-c", "user.name=ZeroTrace self-test",
                   "-c", "user.email=self-test@zerotrace.invalid",
                   "-c", "commit.gpgsign=false", "-C", work]
            _quiet(git + ["init", "-q", "."], env)
            with open(os.path.join(work, "config.py"), "w", encoding="utf-8") as handle:
                handle.write(f'AWS_ACCESS_KEY_ID = "{fake}"\n')
            _quiet(git + ["add", "-A"], env)
            done = subprocess.run(git + ["commit", "-m", "zerotrace self-test"], env=env,
                                  capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run the self-test ({exc})"
    if done.returncode != 0:
        return True, "a staged credential was refused by the pre-commit hook"
    return False, ("the hook let a staged credential through - run `zerotrace doctor` "
                   "and check core.hooksPath")


def _quiet(args: list[str], env: dict) -> None:
    subprocess.run(args, env=env, capture_output=True, text=True, timeout=120, check=True)


# --- the run ------------------------------------------------------------------------------

def run(scope: str = "global", pull_model: bool = True, step_offset: int = 0,
        total_steps: int = 0, quiet: bool = False, file=None) -> Outcome:
    """Do the guided half of an install and report it. Never raises for a missing Docker."""
    from rich.console import Console

    from .ui.progress import Bar

    stream = file or sys.stdout
    console = Console(file=stream)
    cfg = load_config()
    outcome = Outcome()
    total = total_steps or len(STEPS)
    bar = Bar(total, file=stream, console=console)
    bar.done = step_offset

    def step(label: str) -> None:
        bar.step(label)

    def sub_step(label: str) -> None:
        """A step inside a step (the hook installer's own six): retitle the bar, do not
        advance it - the percentage belongs to the install as a whole."""
        bar.label = label
        bar.draw()

    def say(line: str) -> None:
        if not quiet:
            bar.log(f"  {glyphs.sanitize(line, console)}")

    step(STEPS[0])
    status = _check_docker(outcome, cfg)

    step(STEPS[1])
    hooks_ok = _install_hooks(outcome, scope, on_step=sub_step)

    step(STEPS[2])
    _prepare_model(outcome, cfg, status, pull_model, say)

    step(STEPS[3])
    outcome.guardrail_ok = hooks_ok and _validate(outcome, scope)
    bar.finish()
    if not quiet:
        _print(console, outcome)
    return outcome


def _print(console, outcome: Outcome) -> None:
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table

    marks = glyphs.for_console(console)
    table = Table(show_header=False, box=glyphs.box_for(console))
    table.add_column("", width=2)
    table.add_column("check", style="bold")
    table.add_column("result", overflow="fold")
    for state, check, result in outcome.rows:
        colour = {OK: "green", WARN: "yellow", FAIL: "red"}[state]
        table.add_row(f"[{colour}]{marks.get(state, state)}[/]", check,
                      escape(glyphs.sanitize(result, console)))
    console.print(table)

    if outcome.guardrail_ok:
        model = outcome.model_status
        line = (f"[{theme.ACCENT_BOLD}]ZeroTrace {__version__}[/] is protecting every repo on "
                "this machine.")
        if model is not None and not model.usable:
            line += "\nThe AI tie-break is off: ambiguous findings will WARN instead of being settled."
        console.print(Panel.fit(glyphs.sanitize(line, console),
                                border_style=theme.ACCENT_STYLE, box=glyphs.box_for(console)))
        commands = (("zerotrace doctor", "is this machine (and this repo) protected?"),
                    ("zerotrace review", "fix a blocked commit, full-screen"),
                    ("zerotrace model status", "where the AI tie-break stands"),
                    ("zerotrace-uninstall", "remove everything the installer added"))
        width = max(len(command) for command, _ in commands)
        for command, what in commands:
            console.print(f"  [bold]{command.ljust(width)}[/]  [dim]{escape(what)}[/]",
                          highlight=False)
    for hint in dict.fromkeys(step for step in outcome.next_steps if step):
        console.print(glyphs.sanitize(f"→ {hint}", console), highlight=False)
