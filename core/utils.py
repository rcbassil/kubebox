import shlex
import subprocess
from rich.console import Console
import sys

console = Console()

_MUTATIVE_WORDS = ["apply", "create", "delete", "edit", "patch", "scale", "replace"]


def _check_mutative(cmd: list[str]):
    """Exit if cmd contains a mutative kubectl verb."""
    if any(w in cmd for w in _MUTATIVE_WORDS):
        console.print(
            f"[bold red]Security Error: Blocked mutative command:[/bold red] {' '.join(cmd)}"
        )
        sys.exit(1)


def run_cmd(cmd: list[str]) -> str:
    """Run a shell command and return its stdout. Prints errors via Rich."""
    _check_mutative(cmd)
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Command failed:[/bold red] {' '.join(cmd)}")
        console.print(f"[dim]{e.stderr}[/dim]")
        return ""


def run_cmd_allow_fail(cmd: list[str]) -> tuple[str, str, int]:
    """Run a command with security check but return (stdout, stderr, returncode) without raising."""
    _check_mutative(cmd)
    result = subprocess.run(cmd, text=True, capture_output=True)
    return result.stdout, result.stderr, result.returncode


def _is_auto_executable(cmd_parts: list[str]) -> bool:
    return (
        len(cmd_parts) >= 2
        and cmd_parts[0] == "kubectl"
        and cmd_parts[1] in ("describe", "logs")
    )


def print_tip(tip_text: str, command: str = None):
    """Print an actionable suggestion. Auto-executes kubectl describe/logs commands."""
    from rich.panel import Panel

    content = f"[bold yellow]💡 Tip:[/bold yellow] {tip_text}"
    if command:
        cmd_parts = shlex.split(command)
        if _is_auto_executable(cmd_parts):
            content += (
                f"\n\n[dim]Auto-running:[/dim]\n[bold green]> {command}[/bold green]"
            )
            console.print(Panel(content, border_style="yellow"))
            out = run_cmd(cmd_parts)
            if out:
                from rich.syntax import Syntax

                console.print(Syntax(out, "yaml", theme="monokai", word_wrap=True))
            return
        content += f"\n\n[dim]To investigate or fix, you can run:[/dim]\n[bold green]> {command}[/bold green]"

    console.print(Panel(content, border_style="yellow"))
