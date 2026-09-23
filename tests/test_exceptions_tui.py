"""The exceptions browser, driven through Textual's Pilot. Assertions are on the stores."""
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("textual", reason="install the tui extra to test the exceptions browser")
pytest.importorskip("pytest_asyncio", reason="pytest-asyncio drives the Textual Pilot")

from textual.widgets import Static

from zerotrace.audit import exceptions as store
from zerotrace.audit.exceptions import Entry
from zerotrace.ui.tui_common import ConfirmScreen
from zerotrace.ui.tui_exceptions import ExceptionsApp, _expiry_long, _expiry_short

LOCAL_FP, SHARED_FP, EXPIRED_FP = "a" * 64, "b" * 64, "c" * 64


def _seed() -> None:
    store.add(LOCAL_FP, "vendor sample key", 30, rule_id="stripe-live-key", path="pay.py")
    store.add(SHARED_FP, "fixture from the docs", 30, shared=True, rule_id="github-pat",
              path="docs/setup.md")
    store.add(EXPIRED_FP, "old workaround", -1, rule_id="aws-access-key", path="old.py")


def _plain(app) -> str:
    return str(app.query_one("#detail_body", Static).render())


def _select(app, fingerprint: str) -> None:
    app.table.move_cursor(row=[e.fingerprint for e in app.listed].index(fingerprint))


def _scopes() -> dict[str, str]:
    return {entry.fingerprint: entry.scope for entry in store.listing()}


async def test_both_stores_are_listed_with_what_each_entry_covers(repo):
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.table.row_count == 3
        assert "2 active" in app.status_text and "1 expired" in app.status_text
        _select(app, LOCAL_FP)
        await pilot.pause()
        shown = _plain(app)
    assert "stripe-live-key" in shown and "pay.py" in shown and "vendor sample key" in shown
    assert "on this machine only" in shown


async def test_promote_moves_a_local_exception_into_the_reviewed_file(repo):
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        _select(app, LOCAL_FP)
        await pilot.press("P")
        await pilot.pause()
        current = app.current()
        assert current.fingerprint == LOCAL_FP and current.scope == store.SHARED, \
            "the cursor follows the entry into its new scope"
    assert _scopes()[LOCAL_FP] == store.SHARED
    assert (repo / store.SHARED_FILE).exists()


async def test_revoking_asks_first_and_enter_alone_cancels(repo):
    """Revoking cannot be undone from here, so the dialog opens on Cancel."""
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        _select(app, SHARED_FP)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen) and app.focused.id == "no"
        await pilot.press("enter")
        await pilot.pause()
        assert SHARED_FP in _scopes()

        await pilot.press("D")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert SHARED_FP not in _scopes()
    assert not store.is_active(SHARED_FP)


async def test_prune_removes_only_what_has_expired(repo):
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("left", "enter")          # ← to Remove, Enter presses it
        await pilot.pause()
        assert app.table.row_count == 2
    assert set(_scopes()) == {LOCAL_FP, SHARED_FP}


async def test_only_active_hides_the_expired(repo):
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("o")
        await pilot.pause()
        assert app.table.row_count == 2 and all(entry.active for entry in app.listed)
        await pilot.press("O")
        await pilot.pause()
        assert app.table.row_count == 3


async def test_the_mouse_selects_a_row_and_runs_an_action(repo):
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        row = [e.fingerprint for e in app.listed].index(LOCAL_FP)
        await pilot.click("#exceptions", offset=(3, row + 1))     # row 0 is the header
        await pilot.pause()
        assert app.current().fingerprint == LOCAL_FP
        await pilot.click("#do-promote")
        await pilot.pause()
    assert _scopes()[LOCAL_FP] == store.SHARED


async def test_enter_reaches_the_actions_that_apply(repo):
    _seed()
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        _select(app, SHARED_FP)
        await pilot.pause()
        shown = {button.id for button in app.query(".action") if button.display}
        assert shown == {"do-revoke", "do-prune"}, "a shared entry has nothing to promote"
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused.id == "do-revoke"


async def test_a_reason_is_shown_as_typed_even_when_it_looks_like_markup(repo):
    store.add(LOCAL_FP, "[/] not markup [bold]", 30)
    app = ExceptionsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "[/] not markup [bold]" in _plain(app)


async def test_with_no_exceptions_it_says_how_to_record_one(repo):
    app = ExceptionsApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "No exceptions recorded" in app.detail_text
        assert not app.query_one("#actions").display
        await pilot.press("q")
        await pilot.pause()
    assert app.return_value == 0


_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _expiring(expires_at: str) -> Entry:
    return Entry(scope=store.LOCAL, fingerprint=LOCAL_FP, reason="r", created_at="",
                 expires_at=expires_at, active=False)


@pytest.mark.parametrize("left,short,long_", [
    (timedelta(days=3, hours=2), "in 3 d", "(in 3 days)"),
    (timedelta(days=1, minutes=1), "in 1 d", "(in 1 day)"),
    (timedelta(hours=5), "< 1 d", "(within a day)"),
    (timedelta(0), "expired", "expired 2026-09-22"),      # the expiry instant is expired
    (timedelta(hours=-5), "expired", "expired"),
    (timedelta(days=-2, hours=-1), "2 d ago", "expired"),
])
def test_expiry_is_described_in_whole_days(left, short, long_):
    entry = _expiring((_NOW + left).isoformat())
    assert _expiry_short(entry, _NOW) == short
    assert long_ in _expiry_long(entry, _NOW)


@pytest.mark.parametrize("expires_at", ["2026-10-01T00:00:00", "next tuesday", ""])
def test_an_unreadable_expiry_is_reported_not_guessed(expires_at):
    """A date without a timezone, or no date at all, cannot be compared: say so."""
    entry = _expiring(expires_at)
    assert _expiry_short(entry, _NOW) == "unknown"
    assert "cannot be read" in _expiry_long(entry, _NOW)
