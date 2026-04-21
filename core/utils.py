import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone

from rich.console import Console

console = Console()

_MUTATIVE_WORDS = ["apply", "create", "delete", "edit", "patch", "scale", "replace"]

_current_context: str | None = None


def set_context(context: str | None) -> None:
    global _current_context
    _current_context = context


def get_context() -> str | None:
    return _current_context


def load_kube_config() -> None:
    """Load kubeconfig, respecting the active context set via set_context()."""
    from kubernetes import config as _k8s_config

    _k8s_config.load_kube_config(context=_current_context)


def _inject_context(cmd: list[str]) -> list[str]:
    """Inject --context / --kube-context into kubectl/helm commands when a context is active."""
    if not _current_context:
        return cmd
    if cmd and cmd[0] == "kubectl" and "--context" not in cmd:
        return [cmd[0], "--context", _current_context] + cmd[1:]
    if cmd and cmd[0] == "helm" and "--kube-context" not in cmd:
        return [cmd[0], "--kube-context", _current_context] + cmd[1:]
    return cmd


def copy_to_clipboard(text: str) -> None:
    """Copy text to the system clipboard (macOS, Linux, Windows)."""
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
    elif sys.platform == "win32":
        subprocess.run(["clip"], input=text, text=True, check=True)
    else:
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text,
                text=True,
                check=True,
            )


def _check_mutative(cmd: list[str]):
    """Exit if cmd contains a mutative kubectl verb."""
    if any(w in cmd for w in _MUTATIVE_WORDS):
        console.print(
            f"[bold red]Security Error: Blocked mutative command:[/bold red] {' '.join(cmd)}"
        )
        sys.exit(1)


def fmt_age(timestamp: str) -> str:
    """Convert a Kubernetes ISO timestamp to a human-readable age (e.g. '3m', '2h', '4d')."""
    if not timestamp:
        return "—"
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        seconds = int((datetime.now(timezone.utc) - dt).total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds // 3600}h"
        return f"{seconds // 86400}d"
    except Exception:
        return "—"


def _find_replacement_pod(old_pod_name: str, namespace: str) -> str | None:
    """Find a running pod whose name shares the same deployment prefix as old_pod_name."""
    # Deployment pods: <deploy>-<rs-hash>-<pod-hash> — strip last two segments
    parts = old_pod_name.rsplit("-", 2)
    if len(parts) < 3:
        return None
    base_name = parts[0]
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        pods = json.loads(result.stdout).get("items", [])
    except json.JSONDecodeError:
        return None
    for pod in pods:
        name = pod["metadata"]["name"]
        phase = pod.get("status", {}).get("phase", "")
        if (
            name.startswith(base_name + "-")
            and name != old_pod_name
            and phase == "Running"
        ):
            return name
    return None


def run_cmd(cmd: list[str]) -> str:
    """Run a shell command and return its stdout. Prints errors via Rich."""
    _check_mutative(cmd)
    cmd = _inject_context(cmd)
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        if "NotFound" in e.stderr and len(cmd) > 2 and cmd[1] == "logs":
            pod_name = cmd[2]
            ns_idx = cmd.index("-n") if "-n" in cmd else -1
            namespace = cmd[ns_idx + 1] if 0 <= ns_idx < len(cmd) - 1 else None
            if namespace:
                new_pod = _find_replacement_pod(pod_name, namespace)
                if new_pod:
                    console.print(
                        f"[yellow]Pod restarted — retrying with new pod:[/yellow] {new_pod}"
                    )
                    new_cmd = [new_pod if c == pod_name else c for c in cmd]
                    try:
                        retry = subprocess.run(
                            new_cmd, check=True, text=True, capture_output=True
                        )
                        return retry.stdout
                    except subprocess.CalledProcessError:
                        pass
            console.print(
                f"[yellow]Pod no longer exists and no replacement found: {pod_name}[/yellow]"
            )
        else:
            console.print(f"[bold red]Command failed:[/bold red] {' '.join(cmd)}")
            console.print(f"[dim]{e.stderr}[/dim]")
        return ""


def run_cmd_allow_fail(cmd: list[str]) -> tuple[str, str, int]:
    """Run a command with security check but return (stdout, stderr, returncode) without raising."""
    _check_mutative(cmd)
    cmd = _inject_context(cmd)
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
