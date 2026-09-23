"""Pieces shared by the three full-screen apps: review, doctor and exceptions.

They are here rather than in any one app because a dialog, a key or a layout that differs
depending on which screen you reached it from is how a tool starts to feel improvised.

Every option can be reached three ways, in every app and every dialog: its letter (bound in
both cases, `v,V`, because the footer shows capitals), the arrow keys and Enter, or a click.
"""
from typing import ClassVar, Generic, TypeVar

from rich.console import RenderableType
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, DataTable, Footer, Header, Label, Static
from textual.widgets import Markdown as MarkdownView

from . import theme

ItemT = TypeVar("ItemT")

# The accent every app borrows from `ui/theme.py`. Textual derives the rest of its palette
# (borders, focus, selection, zebra stripes) from these, so the three full-screen apps, the
# inline flow and the installer are visibly one product. Success/warning/error keep Textual's
# defaults on purpose: a verdict must not be drawable in the brand colour.
BRAND = Theme(name=theme.TUI_THEME, primary=theme.TUI_PRIMARY, secondary=theme.TUI_SECONDARY,
              accent=theme.TUI_ACCENT, dark=True)

def key(letter: str, action: str, description: str, *, also: str = "") -> Binding:
    """A letter bound in both cases and shown as the capital, the way the help and the
    buttons show it. `also` adds other keys for the same action (`q,escape`)."""
    keys = ",".join(part for part in (letter.lower(), letter.upper(), also) if part)
    return Binding(keys, action, description, key_display=letter.upper())


# Listed last by every app, so the footer ends the same way on every screen.
NAVIGATION: list[BindingType] = [
    Binding("j,J,down", "next_row", "next", show=False),
    Binding("k,K,up", "previous_row", "prev", show=False),
    Binding("question_mark", "help", "help"),
]

# How the action list is reached, for each app's `?` screen.
ACTIONS_HELP = """\
## Actions

Every key above is also a button under the details. `Enter` or `→` on the list moves to
those buttons, `↑` / `↓` choose one, `Enter` runs it, and `Esc` or `←` goes back to the list.
A click works anywhere: on a row, on a button, or on a key in the footer.
"""


def clip(text: str, width: int) -> str:
    """Mark a truncation. A silently cut identifier reads as a different identifier."""
    return text if len(text) <= width else text[:width - 1] + "…"


def esc(text: object) -> str:
    """Make data safe to place inside markup.

    Staged lines, paths, reasons and check results are data, not markup: unescaped,
    `cfg[api_key]` vanishes from the pane (read as a style tag), `app/[slug]/page.tsx` loses
    its directory, and a stray `[/]` raises MarkupError and takes the whole app down.
    """
    return escape(str(text))


def cell(text: str, width: int) -> str:
    """A table cell: clipped first and escaped second, so the cut never splits an escape."""
    return esc(clip(text, width))


def styled(text: object, style: str) -> str:
    """`text` in `style`. An empty style is plain text: `[]x[/]` is not valid markup."""
    return f"[{style}]{esc(text)}[/]" if style else esc(text)


def _step_focus(container: Widget, step: int) -> None:
    """Move focus to the next or previous usable button in `container`, stopping at the ends."""
    buttons = [b for b in container.query(Button) if b.display and not b.disabled]
    if not buttons:
        return
    focused = container.screen.focused
    index = buttons.index(focused) if focused in buttons else (0 if step > 0 else len(buttons))
    buttons[max(0, min(index + step, len(buttons) - 1))].focus()


class ButtonRow(Horizontal):
    """Side-by-side buttons that the arrow keys reach, not only Tab and the mouse.

    Textual moves focus with Tab alone; here ← and → do too. Enter (or a click) presses the
    focused button, which is Button's own behaviour.
    """

    DEFAULT_CSS = """
    ButtonRow { height: auto; margin-top: 1; }
    ButtonRow Button { margin-right: 1; }
    """
    BINDINGS = [Binding("left", "step(-1)", show=False), Binding("right", "step(1)", show=False)]

    def action_step(self, step: int) -> None:
        _step_focus(self, step)


class ActionList(Vertical):
    """What can be done to the chosen item, as a column of buttons.

    ↑ and ↓ choose, Enter or a click runs one, Esc or ← goes back to the list. Each button
    runs the same action as its key, so there is exactly one code path per action.
    """

    DEFAULT_CSS = """
    ActionList { height: auto; padding-top: 1; }
    ActionList > Label { color: $text-muted; }
    ActionList Button { width: 100%; content-align: left middle; text-align: left; }
    """
    BINDINGS = [
        Binding("up", "step(-1)", show=False),
        Binding("down", "step(1)", show=False),
        Binding("escape,left", "back", "back to the list", show=False),
    ]

    class Closed(Message):
        """The user left the actions and wants the list back."""

    def action_step(self, step: int) -> None:
        _step_focus(self, step)

    def action_back(self) -> None:
        self.post_message(self.Closed())


class ItemTable(DataTable):
    """The list pane. Enter or → opens the chosen item's actions instead of doing nothing."""

    BINDINGS = [Binding("enter,right", "open_actions", "actions", show=False)]

    class Opened(Message):
        """The user asked for the actions of the highlighted row."""

    def action_open_actions(self) -> None:
        self.post_message(self.Opened())


class HelpScreen(ModalScreen[None]):
    """The full key list. A toast is too small to hold it and vanishes while you read it."""

    CSS = """
    HelpScreen { align: center middle; }
    /* A fixed height, so the Close button stays on screen however long the text is. */
    #help_box { width: 80%; max-width: 86; height: 80%;
                border: round $accent; background: $surface; padding: 1 2; }
    #help_text { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape,question_mark,q,Q", "dismiss_help", "Close"),
        # Focus sits on Close, so the arrows scroll the text rather than move focus.
        Binding("up,k,K", "scroll_help('up')", show=False),
        Binding("down,j,J", "scroll_help('down')", show=False),
        Binding("pageup", "scroll_help('page_up')", show=False),
        Binding("pagedown", "scroll_help('page_down')", show=False),
    ]

    def __init__(self, markdown: str) -> None:
        super().__init__()
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        with Vertical(id="help_box"):
            with VerticalScroll(id="help_text"):
                yield MarkdownView(self.markdown)
            # A mouse user needs something to click; Enter presses it for everyone else.
            with ButtonRow():
                yield Button("Close", variant="primary", id="close")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    def action_scroll_help(self, direction: str) -> None:
        text = self.query_one("#help_text", VerticalScroll)
        {"up": text.scroll_up, "down": text.scroll_down,
         "page_up": text.scroll_page_up, "page_down": text.scroll_page_down}[direction]()

    @on(Button.Pressed, "#close")
    def action_dismiss_help(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """For anything that changes more than one thing at once, or cannot be taken back."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm_box { width: 64; max-width: 90%; height: auto; border: round $accent;
                   padding: 1 2; background: $surface; }
    /* Without an explicit width a Label sizes to its content and the question is cut off. */
    #confirm_box Label { width: 100%; }
    """
    BINDINGS = [Binding("escape,n,N", "refuse", "Cancel"), Binding("y,Y", "accept", "Yes")]

    def __init__(self, question: str, ok_label: str = "Apply", destructive: bool = False) -> None:
        super().__init__()
        self.question = question
        self.ok_label = ok_label
        self.destructive = destructive

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            # Plain text: a question that quotes a path must not be read as markup.
            yield Label(self.question, markup=False)
            with ButtonRow():
                yield Button(self.ok_label, variant="error" if self.destructive else "primary",
                             id="yes")
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        # Enter presses the focused button. When the answer cannot be undone, a reflexive
        # Enter should cancel, not confirm; Y (or ← then Enter) still confirms.
        self.query_one("#no" if self.destructive else "#yes", Button).focus()

    @on(Button.Pressed, "#yes")
    def action_accept(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_refuse(self) -> None:
        self.dismiss(False)


class ListDetailApp(App[int], Generic[ItemT]):
    """A table of items on the left, the chosen item in full on the right, a status line below.

    An app says which items to list (`shown_items`), how one reads as a row (`cells`) and in
    full (`describe`), which of its `ACTIONS` apply to it (`actions_for`), what the status
    line says (`summary`) and what leaving means (`outcome`). The layout, the narrow-terminal
    stacking, cursor handling, the action buttons, the key list and ctrl+q are the same in
    every app, so learning one of them teaches the others.
    """

    CSS = """
    #body { height: 1fr; }
    #list { width: 52%; border-right: solid $panel; }
    #detail { width: 48%; padding: 0 1; }
    #detail_scroll { height: 1fr; }
    DataTable { height: 1fr; }
    /* Not docked: the footer already docks to the bottom, and two docked widgets fight
       over the same row — the status line simply never appeared. */
    #status { height: 1; padding: 0 1; background: $panel; }

    /* Under 80 columns a 48% detail pane is four words wide, so the panes stack instead.
       Split IDE terminals and 80x24 SSH sessions land here. */
    #body.narrow { layout: vertical; }
    #body.narrow #list { width: 100%; height: 45%; border-right: none;
                         border-bottom: solid $panel; }
    #body.narrow #detail { width: 100%; height: 1fr; }
    """
    NARROW_AT = 80              # below this the panes always stack
    TABLE_ID = "items"
    # The footer is for this app's own keys; Textual's command palette (themes, screenshots,
    # its own quit) is one more way in that nobody here needs.
    ENABLE_COMMAND_PALETTE = False
    # (label, width). Fixed widths: a DataTable grows past its pane and clips the LAST
    # column, and the last column is usually the one that must stay readable.
    COLUMNS: ClassVar[tuple[tuple[str, int], ...]] = ()
    # (action name, button label). Each is also an `action_<name>` method bound to a key.
    ACTIONS: ClassVar[tuple[tuple[str, str], ...]] = ()
    KEYS_HELP = ""              # markdown for the ? screen

    def __init__(self) -> None:
        super().__init__()
        # The items the table is showing, in table order. A filter means a table index is
        # not an index into the app's own list, so every lookup goes through this one.
        self.listed: list[ItemT] = []
        # What the two panes say, kept as markup strings so tests can assert on them.
        self.detail_text = ""
        self.status_text = ""
        self._leaving = False

    @property
    def closing(self) -> bool:
        """True from the moment the app starts to exit. Its widgets may already be gone, so
        work finishing on another thread must drop its result instead of drawing it."""
        return self._leaving or not self.is_running

    def exit(self, result: int | None = None, return_code: int = 0,
             message: RenderableType | None = None) -> None:
        self._leaving = True
        super().exit(result, return_code, message)

    # --- what each app decides ---------------------------------------------------------
    def shown_items(self) -> list[ItemT]:
        raise NotImplementedError

    def cells(self, item: ItemT) -> tuple[str, ...]:
        """One table row. Anything that is data goes through `cell()` or `esc()`."""
        raise NotImplementedError

    def describe(self, item: ItemT | None) -> str:
        """The detail pane for `item`, or for an empty list when it is None."""
        raise NotImplementedError

    def actions_for(self, item: ItemT | None) -> set[str]:
        """Which of ACTIONS make sense for `item` right now. Only those are shown."""
        return set()

    def summary(self) -> str:
        raise NotImplementedError

    def outcome(self) -> int:
        """The exit code for leaving now."""
        return 0

    def loaded(self) -> None:
        """Runs once the frame is on screen. Listing the items is the usual first step."""
        self.redraw()

    # --- the frame -----------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="list"):
                yield ItemTable(id=self.TABLE_ID, cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail"):
                with VerticalScroll(id="detail_scroll"):
                    yield Static(id="detail_body")
                with ActionList(id="actions"):
                    yield Label("Actions · ↑/↓ and Enter, or click · Esc back")
                    for name, label in self.ACTIONS:
                        yield Button(label, id=f"do-{name}", classes="action", compact=True,
                                     action=f"app.{name}")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        # The brand theme before anything is drawn, so `$accent` (borders, focus, selection)
        # is the same emerald the logo, the panels and the installer use.
        self.register_theme(BRAND)
        self.theme = theme.TUI_THEME
        for label, width in self.COLUMNS:
            self.table.add_column(label, width=width)
        self.table.focus()
        self._apply_layout()
        self.loaded()

    @property
    def table(self) -> DataTable:
        return self.query_one(f"#{self.TABLE_ID}", DataTable)

    def current(self) -> ItemT | None:
        index = self.table.cursor_row
        return self.listed[index] if 0 <= index < len(self.listed) else None

    def redraw(self, keep: ItemT | None = None) -> None:
        """Re-list the items and refresh both panes. `keep` stays under the cursor if listed."""
        table = self.table
        previous = table.cursor_row
        table.clear()
        self.listed = self.shown_items()
        for item in self.listed:
            table.add_row(*self.cells(item))
        if self.listed:
            target = self.listed.index(keep) if keep in self.listed else previous
            table.move_cursor(row=max(0, min(target, len(self.listed) - 1)))
        self.show_detail()
        self.show_status()

    def show_detail(self) -> None:
        item = self.current()
        self.detail_text = self.describe(item)
        self.query_one("#detail_body", Static).update(self.detail_text)
        self._show_actions(self.actions_for(item))

    def show_status(self) -> None:
        self.status_text = self.summary()
        self.query_one("#status", Static).update(self.status_text)

    def _action_buttons(self) -> list[Button]:
        return list(self.query_one("#actions", ActionList).query(".action").results(Button))

    def _show_actions(self, available: set[str]) -> None:
        panel = self.query_one("#actions", ActionList)
        for button in self._action_buttons():
            button.display = (button.id or "").removeprefix("do-") in available
        panel.display = bool(available)
        # A button that just disappeared (the item it acted on is resolved) cannot keep the
        # focus: hand it to the next action that still applies, or back to the list.
        focused = self.screen.focused
        stranded = isinstance(focused, Button) and "action" in focused.classes and \
            not (focused.display and panel.display)
        if stranded and not self.focus_actions():
            self.table.focus()

    def focus_actions(self) -> bool:
        """Put the focus on the first action that applies. False when there is none."""
        visible = [b for b in self._action_buttons() if b.display]
        if not visible or not self.query_one("#actions", ActionList).display:
            return False
        visible[0].focus()
        return True

    @on(ItemTable.Opened)
    def _open_actions(self) -> None:
        if not self.focus_actions():
            self.notify("Nothing to do for this one.", timeout=3)

    @on(ActionList.Closed)
    def _close_actions(self) -> None:
        self.table.focus()

    def on_resize(self) -> None:
        self._apply_layout()

    def _apply_layout(self) -> None:
        """Stack the panes when the list pane could not show every column side by side.

        Each column costs its width plus a cell of padding either side, and the pane loses
        two more to its border and scrollbar. At 80 columns the reviewer's four columns need
        51 but get 41, and the verdict — the column that must stay readable — was the one cut.
        """
        needed = sum(width + 2 for _, width in self.COLUMNS) + 2
        width = self.size.width
        self.query_one("#body").set_class(width < self.NARROW_AT or width * 0.52 < needed,
                                          "narrow")

    @on(DataTable.RowHighlighted)
    def _row_changed(self) -> None:
        self.show_detail()

    # --- keys every app shares -------------------------------------------------------------
    def action_next_row(self) -> None:
        self.table.action_cursor_down()

    def action_previous_row(self) -> None:
        self.table.action_cursor_up()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen(self.KEYS_HELP + "\n" + ACTIONS_HELP))

    def action_leave(self) -> None:
        self.exit(self.outcome())

    async def action_quit(self) -> None:
        """Textual binds ctrl+q to a plain exit(), which returns None and reads as success.

        ctrl+q is muscle memory, so it must mean exactly what Q means in each app: for the
        reviewer, leaving with findings open keeps the commit blocked.
        """
        self.action_leave()


def run_app(app: App[int]) -> int:
    """Run a full-screen app and return its exit code.

    An app that ends without a code (a crash Textual caught and printed) did not finish its
    job, so it counts as a failure rather than as a clean exit.
    """
    code = app.run()
    return 1 if code is None else code
