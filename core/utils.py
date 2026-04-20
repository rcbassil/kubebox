import subprocess
from rich.console import Console
import sys

console = Console()


def run_cmd(cmd: list[str]) -> str:
    """Run a shell command and return its stdout. Prints errors via Rich."""
    # Safety Check for mutative commands
    mutative_words = ["apply", "create", "delete", "edit", "patch", "scale", "replace"]
    if any(w in cmd for w in mutative_words):
        console.print(
            f"[bold red]Security Error: Blocked mutative command:[/bold red] {' '.join(cmd)}"
        )
        sys.exit(1)

    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Command failed:[/bold red] {' '.join(cmd)}")
        console.print(f"[dim]{e.stderr}[/dim]")
        return ""


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
        cmd_parts = command.split()
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
