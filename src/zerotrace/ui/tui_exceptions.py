"""The exceptions browser (`zerotrace exceptions -i`).

An exception silences a security control, so every one of them should stay easy to find, to
read and to remove. This lists both stores — the committed file reviewers see and the private
one the reviewer's `E` key writes to — and does the three things worth doing with an
exception: promote it for review, revoke it, or clear out the ones that have expired.
"""
import contextlib
from datetime import UTC, datetime, timedelta

from ..audit import exceptions as store
from ..audit import log as audit_log
from ..audit.exceptions import Entry
from .tui_common import (
    NAVIGATION, ConfirmScreen, ListDetailApp, cell, esc, key, run_app, styled,
)

_SCOPE = {
    store.SHARED: f"In {store.SHARED_FILE}: committed, so a reviewer sees the reason and the "
                  "expiry in a pull request.",
    store.LOCAL: "In .git/zerotrace/exceptions.json: on this machine only, and nobody has "
                 "reviewed it. P moves it into the shared file.",
}

_HELP = f"""\
## Exceptions

| Key | What it does |
| --- | --- |
| `P` | Promote a local exception into `{store.SHARED_FILE}`, so it is reviewed with the code |
| `D` | Revoke an exception: the finding it covers blocks again (asks first) |
| `X` | Remove every expired exception from both stores (asks first) |
| `O` | Show only the exceptions that are still active |

## Moving around

| Key | What it does |
| --- | --- |
| `J` / `K`, arrows | Next / previous exception (the mouse works too) |
| `Q` | Leave |

An exception covers one exact line: when the line changes, the exception stops matching and
the finding is reported again.
"""


_ZERO = timedelta(0)


def _time_left(entry: Entry, now: datetime) -> timedelta | None:
    """Time until the exception expires; negative once it has. None when it cannot be read."""
    try:
        return datetime.fromisoformat(entry.expires_at) - now
    except (TypeError, ValueError):     # unparseable, or a date without a timezone
        return None


def _expiry_short(entry: Entry, now: datetime) -> str:
    """The table cell: whole days either side of the expiry (`timedelta.days` floors)."""
    left = _time_left(entry, now)
    if left is None:
        return "unknown"
    if left > _ZERO:
        return f"in {left.days} d" if left.days else "< 1 d"
    overdue = (-left).days
    return f"{overdue} d ago" if overdue else "expired"


def _expiry_long(entry: Entry, now: datetime) -> str:
    left = _time_left(entry, now)
    date = entry.expires_at[:10]
    if left is None:
        return "the expiry cannot be read, so the exception does not apply"
    if left <= _ZERO:                # the store treats the expiry instant as expired too
        return f"expired {date}"
    if not left.days:
        return f"expires {date} (within a day)"
    return f"expires {date} (in {left.days} day{'' if left.days == 1 else 's'})"


class ExceptionsApp(ListDetailApp[Entry]):
    """Browse, promote and revoke the recorded exceptions."""

    TITLE = "ZeroTrace exceptions"
    TABLE_ID = "exceptions"
    COLUMNS = (("Scope", 6), ("Expires", 8), ("Reason", 24))
    KEYS_HELP = _HELP
    ACTIONS = (
        ("promote", "P  promote for review"),
        ("revoke", "D  revoke"),
        ("prune", "X  remove every expired one"),
    )
    BINDINGS = [
        key("p", "promote", "promote"),
        key("d", "revoke", "revoke", also="delete"),
        key("x", "prune", "prune expired"),
        key("o", "toggle_expired", "only active"),
        key("q", "leave", "quit", also="escape"),
        *NAVIGATION,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[Entry] = []
        self.hide_expired = False

    def loaded(self) -> None:
        self.reload()

    def reload(self, fingerprint: str | None = None) -> None:
        """Re-read both stores. The cursor follows `fingerprint` (an entry that just changed
        scope) or else stays on the entry it was on."""
        before = self.current()
        self.entries = store.listing()
        self.sub_title = f"{len(self.entries)} recorded"
        keep = before if fingerprint is None else \
            next((e for e in self.entries if e.fingerprint == fingerprint), None)
        self.redraw(keep=keep)

    # --- what the frame shows -------------------------------------------------------------
    def shown_items(self) -> list[Entry]:
        return [entry for entry in self.entries if entry.active or not self.hide_expired]

    def cells(self, item: Entry) -> tuple[str, ...]:
        expiry = cell(_expiry_short(item, datetime.now(UTC)), 8)
        return (item.scope, f"[green]{expiry}[/]" if item.active else f"[dim]{expiry}[/]",
                cell(item.reason, 24))

    def describe(self, item: Entry | None) -> str:
        if item is None:
            if self.entries:
                return "Every exception has expired. O shows them again; X removes them."
            return ("No exceptions recorded.\n\nThe reviewer's E key records one. It needs a "
                    "written reason, and it expires.")
        state = styled("active", "green") if item.active else styled("expired", "dim")
        where = item.path or "file not recorded (made before ZeroTrace stored it)"
        return "\n".join([
            f"[bold]{esc(item.rule_id or 'exception')}[/]  {state}",
            f"[dim]{esc(where)}[/]",
            "",
            "[bold]Reason[/]",
            f"  {esc(item.reason) if item.reason else '[dim](none given)[/]'}",
            "",
            f"[bold]Scope[/]  {esc(item.scope)}",
            f"  {esc(_SCOPE.get(item.scope, ''))}",
            "",
            f"recorded {esc(item.created_at[:10] or '?')} · "
            f"{esc(_expiry_long(item, datetime.now(UTC)))}",
            f"[dim]fingerprint {esc(item.fingerprint)}[/]",
        ])

    def actions_for(self, item: Entry | None) -> set[str]:
        available = {"prune"} if any(not entry.active for entry in self.entries) else set()
        if item is not None:
            available.add("revoke")
            if item.scope == store.LOCAL and item.active:
                available.add("promote")
        return available

    def summary(self) -> str:
        if not self.entries:
            return "no exceptions · [dim]? for keys[/]"
        active = sum(1 for entry in self.entries if entry.active)
        shared = sum(1 for entry in self.entries if entry.scope == store.SHARED)
        filtered = " · showing active only" if self.hide_expired else ""
        return (f"[green]{active} active[/] · [dim]{len(self.entries) - active} expired[/] · "
                f"{shared} shared, {len(self.entries) - shared} local{filtered} · "
                "[dim]? for keys[/]")

    # --- actions -------------------------------------------------------------------------------
    def action_promote(self) -> None:
        entry = self.current()
        if entry is None:
            return
        if entry.scope == store.SHARED:
            self.notify("Already in the shared file, where reviewers see it.", timeout=4)
            return
        if not entry.active:
            self.notify("An expired exception is not promoted. Record a new one with a current "
                        "reason.", severity="warning", timeout=5)
            return
        moved, _ = store.promote([entry.fingerprint])
        if moved:
            self.notify(f"Moved into {store.SHARED_FILE}. Commit it so a reviewer sees the "
                        "reason and the expiry.", timeout=6, markup=False)
        else:
            self.notify("Nothing moved: the entry changed on disk.", severity="warning")
        self.reload(fingerprint=entry.fingerprint)

    def action_revoke(self) -> None:
        entry = self.current()
        if entry is None:
            return

        def go(confirmed: bool | None) -> None:
            if not confirmed:
                return
            if store.revoke(entry.fingerprint, entry.scope):
                with contextlib.suppress(OSError):   # the revocation stands without a log line
                    audit_log.append({"fingerprint": entry.fingerprint, "path": entry.path,
                                      "action": "exception_revoked", "scope": entry.scope})
                self.notify("Revoked. The finding blocks again on the next commit that "
                            "contains it.", timeout=5)
            self.reload()

        committed = f" This edits {store.SHARED_FILE}; commit the change." \
            if entry.scope == store.SHARED else ""
        self.push_screen(ConfirmScreen(
            f"Revoke this {entry.scope} exception? The finding it covers will block again."
            f"{committed}", ok_label="Revoke", destructive=True), go)

    def action_prune(self) -> None:
        expired = sum(1 for entry in self.entries if not entry.active)
        if not expired:
            self.notify("Nothing has expired.", timeout=3)
            return

        def go(confirmed: bool | None) -> None:
            if confirmed:
                self.notify(f"Removed {store.prune()} expired exception(s).", timeout=5)
                self.reload()

        self.push_screen(ConfirmScreen(
            f"Remove {expired} expired exception(s) from both stores? They no longer apply, "
            "but their reasons are deleted with them.", ok_label="Remove", destructive=True), go)

    def action_toggle_expired(self) -> None:
        """Hide what no longer applies. The active ones are what silence findings."""
        self.hide_expired = not self.hide_expired
        self.redraw(keep=self.current())


def show() -> int:
    """Run the exceptions browser. Always returns 0: browsing is not a check."""
    return run_app(ExceptionsApp())
