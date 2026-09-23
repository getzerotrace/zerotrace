"""The full-screen doctor (`zerotrace doctor -i`): the same checks, and their fixes, in one place.

Plain `zerotrace doctor` prints a table and exits, which is what installers and CI need. This
view is for a person at a terminal: results appear as each check finishes, the pane beside
them says what the check means, and the fixes doctor knows (run again, warm the model, pin
its digest, repair a repo's hook override) are one key, click or Enter away.
"""
import contextlib
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError

from .. import doctor, gitutil, installer
from ..doctor import FAIL, OK, WARN, Check
from .tui_common import (
    NAVIGATION, ConfirmScreen, ListDetailApp, cell, esc, key, run_app, styled,
)

_MARK = {OK: "[green]✓[/]", WARN: "[yellow]![/]", FAIL: "[red]✗[/]"}
_WORD = {OK: ("ok", "green"), WARN: ("warning", "yellow"), FAIL: ("failed", "bold red")}
_REPAIRABLE = (doctor.REPO_OVERRIDE, doctor.REPO_PROTECTED)

_HELP = """\
## Checks

| Key | What it does |
| --- | --- |
| `R` | Run every check again |
| `W` | Load the model into memory now, then check again (a minute or two on a CPU) |
| `P` | Pin the served model's digest in `.zerotrace.yml` (asks first) |
| `F` | Patch this repo's own hook override so it runs ZeroTrace (asks first) |

`W`, `P` and `F` appear only when they apply: the model answers, its digest is not pinned,
or this repo's hooks skip ZeroTrace.

## Moving around

| Key | What it does |
| --- | --- |
| `J` / `K`, arrows | Next / previous check (the mouse works too) |
| `Q` | Leave. The exit code is 1 while any check fails, as with `zerotrace doctor` |
"""


class DoctorApp(ListDetailApp[Check]):
    """Every doctor check, what it means, and the fixes that apply."""

    TITLE = "ZeroTrace doctor"
    TABLE_ID = "checks"
    COLUMNS = ((" ", 1), ("Check", 20), ("Result", 20))
    KEYS_HELP = _HELP
    ACTIONS = (
        ("rerun", "R  run the checks again"),
        ("warm", "W  load the model now"),
        ("pin", "P  pin the model's digest"),
        ("repair", "F  fix this repo's hook"),
    )
    BINDINGS = [
        key("r", "rerun", "run again"),
        key("w", "warm", "warm model"),
        key("p", "pin", "pin digest"),
        key("f", "repair", "fix repo hook"),
        key("q", "leave", "quit", also="escape"),
        *NAVIGATION,
    ]

    def __init__(self, pin_model: bool = False, warm: bool = False) -> None:
        super().__init__()
        self.checks: list[Check] = []
        self.running = False
        self.worker: threading.Thread | None = None     # the run in progress, for tests
        self._first_run = (pin_model, warm)

    def loaded(self) -> None:
        self._start(*self._first_run)

    # --- what the frame shows -------------------------------------------------------------
    def shown_items(self) -> list[Check]:
        return list(self.checks)

    def cells(self, item: Check) -> tuple[str, ...]:
        return _MARK.get(item.status, " "), cell(item.name, 20), cell(item.result, 20)

    def describe(self, item: Check | None) -> str:
        if item is None:
            return "Running the checks…" if self.running else "No checks ran."
        word, style = _WORD.get(item.status, (item.status, ""))
        lines = [f"[bold]{esc(item.name)}[/]  {styled(word, style)}", "", esc(item.result)]
        if about := doctor.ABOUT.get(item.name):
            lines += ["", f"[dim]{esc(about)}[/]"]
        return "\n".join(lines)

    def actions_for(self, item: Check | None) -> set[str]:
        """Doctor's fixes are machine-wide, so they do not depend on the highlighted row."""
        if self.running:
            return set()
        available = {"rerun"}
        found = {check.name: check for check in self.checks}
        model = found.get(doctor.MODEL_AVAILABLE)
        if model is not None and model.status == OK:
            available.add("warm")
            integrity = found.get(doctor.MODEL_INTEGRITY)
            if integrity is not None and integrity.status == WARN:
                available.add("pin")
        if any(found.get(name, Check(OK, name, "")).status == FAIL for name in _REPAIRABLE):
            available.add("repair")
        return available

    def summary(self) -> str:
        if self.running:
            return f"checking… {len(self.checks)} done · [dim]? for keys[/]"
        counts = {status: sum(1 for c in self.checks if c.status == status)
                  for status in (OK, WARN, FAIL)}
        if not counts[WARN] and not counts[FAIL]:
            return f"[green]all {counts[OK]} checks passed[/] · [dim]? for keys[/]"
        return (f"[green]{counts[OK]} ok[/] · [yellow]{counts[WARN]} warning(s)[/] · "
                f"[red]{counts[FAIL]} failed[/] · [dim]? for keys[/]")

    def outcome(self) -> int:
        """As `zerotrace doctor`: 1 while anything fails. Unfinished checks are not a pass."""
        return 1 if self.running or any(c.status == FAIL for c in self.checks) else 0

    # --- running the checks ------------------------------------------------------------------
    def _start(self, pin_model: bool = False, warm: bool = False) -> None:
        if self.running:
            self.notify("The checks are still running.", severity="warning", timeout=3)
            return
        self.running = True
        self.checks = []
        self.sub_title = "checking…"
        self.redraw()
        # A plain daemon thread rather than a Textual thread worker: those run on the event
        # loop's default executor, which the interpreter joins at exit, so quitting during a
        # two-minute model warm-up would leave the terminal hanging after the screen closed.
        self.worker = threading.Thread(target=self._collect, args=(pin_model, warm),
                                       name="zerotrace-doctor", daemon=True)
        self.worker.start()

    def _collect(self, pin_model: bool, warm: bool) -> None:
        """Runs on the background thread. Every change to the screen goes through `_post`."""
        try:
            doctor.collect(pin_model=pin_model, warm=warm,
                           listener=lambda check: self._post(self._add, check))
        except Exception as exc:        # a check that crashed failed; it never passed
            self._post(self._add, Check(FAIL, "doctor", f"a check crashed: {exc!r}"))
        self._post(self._finish)

    def _post(self, callback: Callable[..., None], *args: object) -> None:
        """Hand a result to the UI thread, unless the app has closed while a check ran.

        `closing` is checked here and again in the callback, because the app can start to
        exit after the result was queued. RuntimeError (the loop has stopped) and
        CancelledError (it stopped mid-call) are the same situation seen from this thread.
        """
        if self.closing:
            return
        with contextlib.suppress(RuntimeError, CancelledError):
            self.call_from_thread(callback, *args)

    def _add(self, check: Check) -> None:
        if self.closing:
            return
        self.checks.append(check)
        self.redraw(keep=self.current())

    def _finish(self) -> None:
        if self.closing:
            return
        self.running = False
        failed = sum(1 for check in self.checks if check.status == FAIL)
        self.sub_title = f"{failed} failing" if failed else "no failures"
        self.show_detail()
        self.show_status()

    # --- actions -------------------------------------------------------------------------------
    def _can(self, action: str, why_not: str) -> bool:
        """Keys and buttons share one rule: an action runs only where its button would show."""
        if action in self.actions_for(self.current()):
            return True
        self.notify("The checks are still running." if self.running else why_not,
                    severity="warning", timeout=4)
        return False

    def action_rerun(self) -> None:
        self._start()

    def action_warm(self) -> None:
        if self._can("warm", "The model is not answering: start it, then press R."):
            self.notify("Loading the model. On a CPU this can take a minute or two.", timeout=5)
            self._start(warm=True)

    def action_pin(self) -> None:
        if not self._can("pin", "Nothing to pin: the model is not answering, or its digest "
                                "is already pinned."):
            return

        def go(confirmed: bool | None) -> None:
            if confirmed:
                self._start(pin_model=True)

        self.push_screen(ConfirmScreen(
            "Pin the digest of the model being served in .zerotrace.yml? Commits then check "
            "they are talking to exactly this model. Commit the file afterwards so the whole "
            "team checks the same digest.", ok_label="Pin"), go)

    def action_repair(self) -> None:
        if not self._can("repair", "This repo already runs ZeroTrace; there is nothing to fix."):
            return

        def go(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                lines = installer.install_repo()
            except (OSError, gitutil.GitError) as exc:
                self.notify(str(exc), severity="error", timeout=8, markup=False)
                return
            self.notify("\n".join(lines), timeout=6, markup=False)
            self._start()

        self.push_screen(ConfirmScreen(
            "Patch this repo's hook so it runs ZeroTrace? The repo keeps its own hooks "
            "(husky or similar); ZeroTrace is added to their pre-commit step.",
            ok_label="Patch"), go)


def show(pin_model: bool = False, warm: bool = False) -> int:
    """Run the doctor app. Returns 1 while any check fails, 0 otherwise."""
    return run_app(DoctorApp(pin_model=pin_model, warm=warm))
