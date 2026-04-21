import inspect
import io
import shlex
from collections import deque
from contextlib import redirect_stdout
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)
from textual.containers import Horizontal, Vertical


_RUN_SENTINEL = "__run__"
_MAX_HISTORY = 20

# Commands that never accept --namespace
_NO_NS_CMDS = frozenset({"contexts", "flux", "kong"})


# ── Data classes ──────────────────────────────────────────────────────────────


class _HistoryEntry:
    __slots__ = ("command", "output", "timestamp")

    def __init__(self, command: str, output: Text) -> None:
        self.command = command
        self.output = output
        self.timestamp = datetime.now().strftime("%H:%M")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _cmd_name(cmd) -> str:
    return cmd.name or cmd.callback.__name__.replace("_", "-")


def _usage_hint(cmd_name: str, callback) -> str:
    parts = [cmd_name]
    for pname, param in inspect.signature(callback).parameters.items():
        d = param.default
        if hasattr(d, "default"):
            if d.default is ...:
                parts.append(f"<{pname}>")
            elif hasattr(d, "param_decls") and d.param_decls:
                flag = d.param_decls[0]
                parts.append(f"[{flag} <{pname}>]")
        elif param.default is inspect.Parameter.empty:
            parts.append(f"<{pname}>")
    return " ".join(parts)


def _has_required_args(callback) -> bool:
    for param in inspect.signature(callback).parameters.values():
        d = param.default
        if hasattr(d, "default") and d.default is ...:
            return True
        if param.default is inspect.Parameter.empty:
            return True
    return False


def _inject_namespace(command: str, namespace: str) -> str:
    """Append -n <namespace> when no namespace flag is already present."""
    if not namespace:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts or parts[0] in _NO_NS_CMDS:
        return command
    if "-n" in parts or "--namespace" in parts:
        return command
    return command + f" -n {namespace}"


# ── List item widgets ─────────────────────────────────────────────────────────


class CommandItem(ListItem):
    def __init__(self, command_name: str, help_text: str, has_args: bool) -> None:
        super().__init__()
        self.command_name = command_name
        self.help_text = help_text
        self.has_args = has_args

    def compose(self) -> ComposeResult:
        if self.command_name == _RUN_SENTINEL:
            yield Label("[bold green]▶[/bold green] [dim]run any…[/dim]")
            return
        suffix = " [dim yellow]\\[…][/dim yellow]" if self.has_args else ""
        yield Label(f"[bold cyan]{self.command_name}[/bold cyan]{suffix}")


class HistoryItem(ListItem):
    def __init__(self, entry: _HistoryEntry) -> None:
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        cmd_display = self.entry.command[:18]
        yield Label(f"[dim]{self.entry.timestamp}[/dim] [cyan]{cmd_display}[/cyan]")


# ── Main app ──────────────────────────────────────────────────────────────────


class K8sToolApp(App):
    TITLE = "kubebox"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "focus_output", "Output"),
        Binding("l", "focus_list", "Commands"),
        Binding("h", "toggle_history", "History"),
        Binding("n", "focus_namespace", "Namespace"),
        Binding("r", "rerun_last", "Re-run", show=False),
        Binding("escape", "close_input", "Close"),
    ]

    CSS = """
    /* ── Namespace bar ─────────────────────────────────── */
    #ns-bar {
        height: 1;
        background: $boost;
        padding: 0 1;
    }
    #ns-label {
        width: auto;
        padding: 0 1 0 0;
        color: $warning;
        text-style: bold;
    }
    #ns-input {
        width: 1fr;
        height: 1;
        background: $boost;
        color: $text;
        border: none;
        padding: 0;
    }
    #ns-input:focus {
        background: $panel-lighten-1;
        border: none;
    }
    /* ── Main split ────────────────────────────────────── */
    #main {
        height: 1fr;
    }
    #left-panel {
        width: 26;
    }
    /* ── Command list ──────────────────────────────────── */
    #command-list {
        width: 1fr;
        height: 1fr;
        border: solid cyan;
    }
    /* ── History section ───────────────────────────────── */
    #history-section {
        width: 1fr;
        height: auto;
        max-height: 10;
        display: none;
        border: solid $boost;
    }
    #history-section.visible {
        display: block;
    }
    #history-header {
        background: $boost;
        color: $text-muted;
        height: 1;
        padding: 0 1;
    }
    #history-list {
        width: 1fr;
        height: auto;
        max-height: 9;
    }
    /* ── Output area ───────────────────────────────────── */
    #output-area {
        width: 1fr;
        border: solid magenta;
        background: $surface;
    }
    #output-area:focus {
        border: double green;
    }
    /* ── Input bar ─────────────────────────────────────── */
    #input-bar {
        height: auto;
        display: none;
        border: solid yellow;
        padding: 0 1;
    }
    #input-bar.active {
        display: block;
    }
    #input-label {
        color: $warning;
        text-style: bold;
        height: 1;
    }
    #input-row {
        height: 3;
        align: left middle;
    }
    #input-prefix {
        width: auto;
        padding: 0 1 0 0;
        color: $warning;
        text-style: bold;
    }
    #cmd-input {
        width: 1fr;
        background: $panel;
        color: $text;
        border: tall $accent;
    }
    #cmd-input:focus {
        border: tall yellow;
    }
    /* ── Help bar ──────────────────────────────────────── */
    #help-bar {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, typer_app) -> None:
        super().__init__()
        self.typer_app = typer_app
        skip = {"dashboard", "interactive"}
        self._commands = dict(
            sorted(
                (
                    (_cmd_name(cmd), cmd)
                    for cmd in typer_app.registered_commands
                    if cmd.callback and _cmd_name(cmd) not in skip
                ),
                key=lambda x: x[0],
            )
        )
        self._history: deque[_HistoryEntry] = deque(maxlen=_MAX_HISTORY)
        self._active_namespace: str = ""
        self._last_command: str = ""
        self._showing_history: bool = False

    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="ns-bar"):
            yield Static("Namespace:", id="ns-label")
            yield Input(
                placeholder="all namespaces   (n to edit, Enter to confirm)",
                id="ns-input",
            )

        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield ListView(
                    *[
                        CommandItem(
                            name,
                            cmd.help
                            or (cmd.callback.__doc__ or "").strip().splitlines()[0],
                            _has_required_args(cmd.callback),
                        )
                        for name, cmd in self._commands.items()
                    ],
                    CommandItem(_RUN_SENTINEL, "Run any kubebox command", True),
                    id="command-list",
                )
                with Vertical(id="history-section"):
                    yield Static("── history (Enter=replay) ──", id="history-header")
                    yield ListView(id="history-list")

            yield RichLog(id="output-area", highlight=True, markup=True)

        with Vertical(id="input-bar"):
            yield Static("", id="input-label")
            with Horizontal(id="input-row"):
                yield Static("▶", id="input-prefix")
                yield Input(id="cmd-input")

        yield Static("", id="help-bar")
        yield Footer()

    # ── Namespace bar ─────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "ns-input":
            return
        self._active_namespace = event.value.strip()
        self.sub_title = (
            f"ns: {self._active_namespace}" if self._active_namespace else ""
        )

    def action_focus_namespace(self) -> None:
        ns = self.query_one("#ns-input", Input)
        ns.focus()
        ns.cursor_position = len(ns.value)

    # ── Standard actions ──────────────────────────────────────────────────

    def action_focus_output(self) -> None:
        self.query_one("#output-area").focus()

    def action_focus_list(self) -> None:
        self.query_one("#command-list").focus()

    def action_close_input(self) -> None:
        bar = self.query_one("#input-bar")
        if "active" in bar.classes:
            bar.remove_class("active")
            self.query_one("#command-list").focus()
        elif self.query_one("#ns-input", Input).has_focus:
            self.query_one("#command-list").focus()

    def action_rerun_last(self) -> None:
        if self._last_command:
            self._run_raw(self._last_command, skip_namespace_inject=True)

    # ── History toggle ────────────────────────────────────────────────────

    def action_toggle_history(self) -> None:
        self._showing_history = not self._showing_history
        section = self.query_one("#history-section")
        if self._showing_history:
            section.add_class("visible")
        else:
            section.remove_class("visible")

    # ── Help bar ──────────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and hasattr(event.item, "help_text"):
            self.query_one("#help-bar", Static).update(
                f"[dim]{event.item.help_text}[/dim]"
            )

    # ── Selection routing ─────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, CommandItem):
            self.trigger_command(item.command_name)
        elif isinstance(item, HistoryItem):
            self._show_history_entry(item.entry)
            self._last_command = item.entry.command

    def trigger_command(self, command_name: str) -> None:
        inp = self.query_one("#cmd-input", Input)
        label = self.query_one("#input-label", Static)
        if command_name == _RUN_SENTINEL:
            label.update("Type a kubebox command and press Enter  •  Esc to cancel")
            inp.value = ""
            inp.cursor_position = 0
            self.query_one("#input-bar").add_class("active")
            inp.focus()
            return
        cmd = self._commands.get(command_name)
        if cmd and _has_required_args(cmd.callback):
            label.update(
                "Edit the command below and press Enter to run  •  Esc to cancel"
            )
            hint = _usage_hint(command_name, cmd.callback)
            inp.value = hint
            inp.cursor_position = len(hint)
            self.query_one("#input-bar").add_class("active")
            inp.focus()
        else:
            self._run_raw(command_name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ns-input":
            self.query_one("#command-list").focus()
            return

        raw = event.value.strip()
        self.query_one("#input-bar").remove_class("active")
        if not raw:
            return
        try:
            parts = shlex.split(raw)
        except ValueError:
            out = self.query_one("#output-area", RichLog)
            out.clear()
            out.write("[bold red]Error:[/bold red] Invalid command syntax.")
            return
        if parts[0] not in self._commands:
            out = self.query_one("#output-area", RichLog)
            out.clear()
            known = "  ".join(sorted(self._commands))
            out.write(
                f"[bold red]Unknown command:[/bold red] [cyan]{parts[0]}[/cyan]\n\n"
                f"[dim]Available commands:[/dim] {known}"
            )
            return
        self._run_raw(raw)

    # ── History ───────────────────────────────────────────────────────────

    def _show_history_entry(self, entry: _HistoryEntry) -> None:
        out = self.query_one("#output-area", RichLog)
        out.clear()
        out.write(
            f"[dim]── history: [cyan]{entry.command}[/cyan]  [{entry.timestamp}]"
            f"  (r to re-run) ──[/dim]\n"
        )
        out.write(entry.output)

    def _add_to_history(self, command: str, output: Text) -> None:
        entry = _HistoryEntry(command, output)
        self._history.appendleft(entry)
        hist_list = self.query_one("#history-list", ListView)
        hist_list.clear()
        for e in self._history:
            hist_list.append(HistoryItem(e))

    # ── Command execution ─────────────────────────────────────────────────

    def _run_raw(self, raw: str, *, skip_namespace_inject: bool = False) -> None:
        effective = (
            raw
            if skip_namespace_inject
            else _inject_namespace(raw, self._active_namespace)
        )
        out = self.query_one("#output-area", RichLog)
        out.clear()
        if effective != raw:
            header = (
                f"[bold yellow]Running [cyan]{raw}[/cyan]"
                f" [dim](+ns: {self._active_namespace})[/dim]...[/bold yellow]"
            )
        else:
            header = f"[bold yellow]Running [cyan]{effective}[/cyan]...[/bold yellow]"
        out.write(header)
        self._last_command = effective
        self._execute(effective)

    @work(thread=True)
    def _execute(self, raw: str) -> None:
        class _FakeTTY(io.StringIO):
            def isatty(self):
                return True

        f = _FakeTTY()
        with redirect_stdout(f):
            try:
                self.typer_app(shlex.split(raw), standalone_mode=False)
            except Exception as e:
                print(f"[bold red]Error:[/bold red] {e}")

        result = Text.from_ansi(f.getvalue())
        out = self.query_one("#output-area", RichLog)
        self.call_from_thread(out.clear)
        self.call_from_thread(out.write, result)
        self.call_from_thread(out.focus)
        self.call_from_thread(self._add_to_history, raw, result)
