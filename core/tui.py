import inspect
import io
import shlex
from contextlib import redirect_stdout

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    ListItem,
    ListView,
    Label,
    Static,
    RichLog,
    Input,
)
from textual.containers import Horizontal, Vertical


def _cmd_name(cmd) -> str:
    return cmd.name or cmd.callback.__name__.replace("_", "-")


def _usage_hint(cmd_name: str, callback) -> str:
    parts = [cmd_name]
    for pname, param in inspect.signature(callback).parameters.items():
        d = param.default
        if hasattr(d, "default"):
            if d.default is ...:
                parts.append(f"<{pname}>")
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


class CommandItem(ListItem):
    def __init__(self, command_name: str, help_text: str, has_args: bool):
        super().__init__()
        self.command_name = command_name
        self.help_text = help_text
        self.has_args = has_args

    def compose(self) -> ComposeResult:
        suffix = " [dim yellow]\\[…][/dim yellow]" if self.has_args else ""
        yield Label(f"[bold cyan]{self.command_name}[/bold cyan]{suffix}")


class K8sToolApp(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "focus_output", "Focus Output"),
        ("l", "focus_list", "Focus List"),
        ("escape", "close_input", "Cancel"),
    ]

    CSS = """
    #main {
        height: 1fr;
    }
    #command-list {
        width: 20;
        border: solid cyan;
    }
    #output-area {
        width: 1fr;
        border: solid magenta;
        background: $surface;
    }
    #output-area:focus {
        border: double green;
    }
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
    #help-bar {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, typer_app):
        super().__init__()
        self.typer_app = typer_app
        skip = {"dashboard", "interactive"}
        self._commands = {
            _cmd_name(cmd): cmd
            for cmd in typer_app.registered_commands
            if cmd.callback and _cmd_name(cmd) not in skip
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
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
                id="command-list",
            )
            yield RichLog(id="output-area", highlight=True, markup=True)
        with Vertical(id="input-bar"):
            yield Static(
                "Edit the command below and press Enter to run  •  Esc to cancel",
                id="input-label",
            )
            with Horizontal(id="input-row"):
                yield Static("▶", id="input-prefix")
                yield Input(id="cmd-input")
        yield Static("", id="help-bar")
        yield Footer()

    def action_focus_output(self) -> None:
        self.query_one("#output-area").focus()

    def action_focus_list(self) -> None:
        self.query_one("#command-list").focus()

    def action_close_input(self) -> None:
        bar = self.query_one("#input-bar")
        if "active" in bar.classes:
            bar.remove_class("active")
            self.query_one("#command-list").focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and hasattr(event.item, "help_text"):
            self.query_one("#help-bar", Static).update(
                f"[dim]{event.item.help_text}[/dim]"
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.trigger_command(event.item.command_name)

    def trigger_command(self, command_name: str) -> None:
        cmd = self._commands.get(command_name)
        if cmd and _has_required_args(cmd.callback):
            hint = _usage_hint(command_name, cmd.callback)
            inp = self.query_one("#cmd-input", Input)
            inp.value = hint
            inp.cursor_position = len(hint)
            self.query_one("#input-bar").add_class("active")
            inp.focus()
        else:
            self._run_raw(command_name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        self.query_one("#input-bar").remove_class("active")
        if raw:
            self._run_raw(raw)

    def _run_raw(self, raw: str) -> None:
        out = self.query_one("#output-area", RichLog)
        out.clear()
        out.write(f"[bold yellow]Running [cyan]{raw}[/cyan]...[/bold yellow]")
        f = io.StringIO()
        with redirect_stdout(f):
            try:
                self.typer_app(shlex.split(raw), standalone_mode=False)
            except Exception as e:
                print(f"Error: {e}")
        out.clear()
        out.write(Text.from_ansi(f.getvalue()))
        out.focus()
