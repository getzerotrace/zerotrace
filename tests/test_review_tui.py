"""The full-screen reviewer, driven through Textual's Pilot.

Every case runs against a real repository with a real staged secret, so the assertions are
about what ends up in the index — not about what the widget printed.
"""
import subprocess

import pytest

# `textual` is an optional extra (`pip install "zerotrace[tui]"`). A machine that only wants
# the guardrail should not fail the suite for missing it; CI installs `.[dev]`, which has it.
pytest.importorskip("textual", reason="install the tui extra to test the full-screen reviewer")
pytest.importorskip("pytest_asyncio", reason="pytest-asyncio drives the Textual Pilot")

from zerotrace import pipeline
from zerotrace.audit import exceptions as audit_exceptions
from zerotrace.collectors.staged_diff import collect_staged
from zerotrace.config import load_config
from zerotrace.ui.tui import ReasonScreen, ReviewApp
from zerotrace.ui.tui_common import ConfirmScreen, HelpScreen

from .conftest import git, write

pytestmark = pytest.mark.asyncio


def _staged(repo, fake, path: str = "pay.py") -> tuple[list, object, str]:
    value = fake.stripe_live()
    write(path, f'KEY = "{value}"\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    return decisions, cfg, value


async def test_opens_with_the_findings_listed(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        table = app.query_one("#findings")
        assert table.row_count == len(decisions)
        assert "1 open" in app.status_text
        await pilot.pause()


@pytest.mark.parametrize("key", ["v", "V"])
async def test_either_case_applies_the_env_reference(repo, fake, key):
    """The menu shows capitals, so typing a capital must work."""
    decisions, cfg, value = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press(key)
        await pilot.pause()
    staged = git("show", ":pay.py").stdout
    assert value not in staged
    assert "os.environ[" in staged, "the value is replaced by an environment lookup"


@pytest.mark.parametrize("key", ["r", "R"])
async def test_either_case_applies_the_placeholder(repo, fake, key):
    decisions, cfg, value = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press(key)
        await pilot.pause()
    staged = git("show", ":pay.py").stdout
    assert value not in staged and staged.startswith("KEY = \"<")


async def test_unstage_removes_the_file_from_the_commit(repo):
    write(".env", "API_KEY=abc\n")
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("U")
        await pilot.pause()
    staged_names = git("diff", "--cached", "--name-only").stdout.split()
    assert ".env" not in staged_names, "the secret file is out of the commit"
    assert ".gitignore" in staged_names, "and it cannot be staged again by accident"


async def test_exception_requires_a_reason_and_then_silences_the_finding(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    fingerprint_app = ReviewApp(decisions, cfg)
    async with fingerprint_app.run_test() as pilot:
        await pilot.press("E")
        await pilot.pause()
        assert isinstance(fingerprint_app.screen, ReasonScreen)

        await pilot.press("enter")            # empty reason: refused, modal stays
        await pilot.pause()
        assert isinstance(fingerprint_app.screen, ReasonScreen)

        for char in "vendor sample":
            await pilot.press(char)
        await pilot.press("enter")
        await pilot.pause()

    from zerotrace.audit.fingerprint import of_finding
    assert audit_exceptions.is_active(of_finding(decisions[0].finding))


async def test_quitting_with_findings_open_returns_one(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
    assert app.return_value == 1


async def test_quitting_after_resolving_everything_returns_zero(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app.return_value == 0


async def test_no_raw_value_is_ever_rendered(repo, fake):
    decisions, cfg, value = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = app.detail_text
        assert value not in detail
        assert "len=" in detail, "the value is shown as a typed, length-hinted token"


async def test_navigation_keys_move_between_findings(repo, fake):
    write("a.py", f'KEY = "{fake.stripe_live()}"\n')
    write("b.py", f'TOKEN = "{fake.github()}"\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    assert len(decisions) == 2
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        first = app.query_one("#findings").cursor_row
        await pilot.press("j")
        await pilot.pause()
        assert app.query_one("#findings").cursor_row == first + 1
        await pilot.press("k")
        await pilot.pause()
        assert app.query_one("#findings").cursor_row == first


async def test_manual_fix_findings_refuse_an_automatic_rewrite(repo, fake):
    """A composed secret has no value on the line, so V must decline rather than guess."""
    key = fake.stripe_live()
    write("c.py", f'a = "{key[:17]}"\nb = "{key[17:]}"\nstripe_key = a + b\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("v")
        await pilot.pause()
    assert git("show", ":c.py").stdout.count(key[:17]) == 1, "the file is untouched"
    assert app.rows[0].state == "open"


async def test_cli_falls_back_to_the_inline_flow_without_a_terminal(repo, fake):
    """`review` piped into something is still usable: no half-drawn full-screen app."""
    _staged(repo, fake)
    import sys
    result = subprocess.run([sys.executable, "-m", "zerotrace", "review"],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 1
    assert "stripe-live-key" in result.stdout


async def test_help_opens_a_screen_rather_than_a_toast(repo, fake):
    """A toast disappears while you are still reading it, so the keys live on a screen."""
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_fix_all_asks_first_and_then_rewrites_every_auto_fixable_finding(repo, fake):
    write("a.py", f'KEY = "{fake.stripe_live()}"\n')
    write("b.py", f'TOKEN = "{fake.github()}"\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("F")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen), "a multi-file rewrite is confirmed first"

        await pilot.press("escape")                    # refusing changes nothing
        await pilot.pause()
        assert all(row.state == "open" for row in app.rows)

        await pilot.press("F")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

    assert all(row.state == "fixed" for row in app.rows)
    for path in ("a.py", "b.py"):
        assert "os.environ[" in git("show", f":{path}").stdout


async def test_only_open_filter_hides_what_is_already_done(repo, fake):
    write("a.py", f'KEY = "{fake.stripe_live()}"\n')
    write("b.py", f'TOKEN = "{fake.github()}"\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("v")                         # resolve the first one
        await pilot.pause()
        assert app.query_one("#findings").row_count == 2, "resolved rows stay visible by default"

        await pilot.press("o")
        await pilot.pause()
        assert app.query_one("#findings").row_count == 1
        assert all(row.state == "open" for row in app.listed)

        await pilot.press("O")                         # the same key in either case
        await pilot.pause()
        assert app.query_one("#findings").row_count == 2


async def test_the_cursor_moves_to_the_next_open_finding_after_a_fix(repo, fake):
    write("a.py", f'KEY = "{fake.stripe_live()}"\n')
    write("b.py", f'TOKEN = "{fake.github()}"\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("v")
        await pilot.pause()
        current = app.listed[app.query_one("#findings").cursor_row]
        assert current.state == "open", "you land on the next thing needing a decision"


@pytest.mark.parametrize("size,stacked", [((60, 20), True), ((120, 30), False)])
async def test_the_panes_stack_in_a_narrow_terminal(repo, fake, size, stacked):
    """A split IDE terminal is often under 80 columns; a 48% detail pane there is unreadable."""
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.query_one("#body").has_class("narrow") is stacked


async def test_ctrl_q_does_not_report_a_clean_review(repo, fake):
    """Textual's own ctrl+q exits with no value, which would read as success."""
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.return_value == 1
    assert app.rows[0].state == "open"


async def test_a_dumb_terminal_gets_the_inline_flow(monkeypatch):
    """TERM=dumb cannot move a cursor, so the full-screen app must not be chosen."""
    from zerotrace import cli
    monkeypatch.setenv("TERM", "dumb")
    assert cli._tui_available() is False
    monkeypatch.setenv("TERM", "xterm-256color")
    assert cli._tui_available() is True


def _plain(app) -> str:
    """The detail pane as the developer reads it: markup applied, styles dropped."""
    from textual.widgets import Static
    return str(app.query_one("#detail_body", Static).render())


def _two_findings(fake) -> tuple[list, object]:
    write("a.py", f'KEY = "{fake.stripe_live()}"\n')
    write("b.py", f'TOKEN = "{fake.github()}"\n')
    git("add", "-A")
    cfg = load_config()
    return [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
            if d.action in ("block", "warn")], cfg


async def test_capital_q_leaves_too(repo, fake):
    """The status line says 'press Q'; the capital used to be unbound."""
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("Q")
        await pilot.pause()
    assert app.return_value == 1


async def test_bracketed_code_is_shown_as_written_not_read_as_markup(repo, fake):
    """Unescaped, `cfg[api_key]` vanished from the pane, a Next.js `[slug]` directory lost its
    name, and a `[/]` in the staged line crashed the whole app."""
    value = fake.stripe_live()
    write("app/[slug]/pay.py", f'cfg[api_key] = "{value}"  # [/] [bold]\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        shown = _plain(app)
    assert "cfg[api_key]" in shown and "# [/] [bold]" in shown
    assert "app/[slug]/pay.py" in shown
    assert value not in shown


async def test_enter_opens_the_actions_and_the_arrow_keys_choose_one(repo, fake):
    decisions, cfg, value = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused.id == "do-fix_reference", "the recommended fix takes the focus"
        await pilot.press("down")
        await pilot.pause()
        assert app.focused.id == "do-fix_placeholder"
        await pilot.press("enter")
        await pilot.pause()
    staged = git("show", ":pay.py").stdout
    assert value not in staged and staged.startswith('KEY = "<')


async def test_right_arrow_opens_the_actions_and_escape_goes_back(repo, fake):
    """Esc inside the actions returns to the list; it does not abort the review."""
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("right")
        await pilot.pause()
        assert app.focused.id == "do-fix_reference"
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is app.table and app.is_running


async def test_clicking_an_action_applies_it(repo, fake):
    decisions, cfg, value = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#do-fix_reference")
        await pilot.pause()
    staged = git("show", ":pay.py").stdout
    assert value not in staged and "os.environ[" in staged


async def test_clicking_a_row_selects_that_finding(repo, fake):
    decisions, cfg = _two_findings(fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#findings", offset=(6, 2))        # row 0 is the header
        await pilot.pause()
        assert app.table.cursor_row == 1
        assert app.listed[1].decision.finding.path in _plain(app)


async def test_only_the_actions_that_apply_are_offered(repo, fake):
    """A composed secret has nothing to rewrite automatically, so only E is offered."""
    key = fake.stripe_live()
    write("c.py", f'a = "{key[:17]}"\nb = "{key[17:]}"\nstripe_key = a + b\n')
    git("add", "-A")
    cfg = load_config()
    decisions = [d for d in pipeline.scan(collect_staged(), cfg, use_model=False)
                 if d.action in ("block", "warn")]
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        shown = {button.id for button in app.query(".action") if button.display}
    assert shown == {"do-exception"}


async def test_the_confirmation_answers_arrow_keys_and_enter(repo, fake):
    decisions, cfg = _two_findings(fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("F")
        await pilot.pause()
        assert app.focused.id == "yes"
        await pilot.press("right")
        await pilot.pause()
        assert app.focused.id == "no"
        await pilot.press("enter")                      # Cancel
        await pilot.pause()
        assert all(row.state == "open" for row in app.rows)

        await pilot.press("F")
        await pilot.pause()
        await pilot.press("enter")                      # Apply, focused by default
        await pilot.pause()
    assert all(row.state == "fixed" for row in app.rows)


async def test_the_help_screen_closes_with_a_click(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.click("#close")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_the_reason_dialog_reaches_its_buttons_with_the_arrow_keys(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("E")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused.id == "ok"
        await pilot.press("right")
        await pilot.pause()
        assert app.focused.id == "cancel"
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ReasonScreen)
    assert app.rows[0].state == "open"


async def test_recording_an_exception_names_the_rule_and_file(repo, fake):
    decisions, cfg, _ = _staged(repo, fake)
    app = ReviewApp(decisions, cfg)
    async with app.run_test() as pilot:
        await pilot.press("E")
        await pilot.pause()
        for char in "sample":
            await pilot.press(char)
        await pilot.press("enter")
        await pilot.pause()
    (entry,) = audit_exceptions.listing()
    assert (entry.rule_id, entry.path) == ("stripe-live-key", "pay.py")


async def test_the_reviewer_lists_findings_in_priority_order(repo):
    """Same order as the inline table and panels: blocking first, most severe first."""
    from .test_priority import EXPECTED, MIXED
    app = ReviewApp(list(reversed(MIXED)), load_config())
    assert [row.decision.finding.rule_id for row in app.rows] == EXPECTED
