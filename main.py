import io
import os
import shlex
import time
from contextlib import redirect_stdout
from typing import Optional

import typer
from typer.core import TyperGroup
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from prompt_toolkit import PromptSession, HTML
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory

from core.utils import set_context, run_cmd
from core.kubernetes import (
    check_crashloop_pods,
    check_deployments,
    check_all_objects,
    describe_object,
    check_logs,
    get_failing_pods,
)
from core.flux import check_flux_status
from core.helm import check_helm_status
from core.kong import check_kong_errors
from core.kustomize import check_kustomize_errors
from core.vault import check_vault_status
from core.trace import trace_object
from core.ai import analyze_logs, ask as ai_ask
from core.crd import check_crd_status
from core.events import check_events
from core.network import check_network_status
from core.rbac import check_rbac_status
from core.report import generate_report
from core.utils import copy_to_clipboard


class _SortedGroup(TyperGroup):
    def list_commands(self, ctx):
        return sorted(super().list_commands(ctx))


app = typer.Typer(
    help="🤖 Read-Only Kubernetes DevOps/SRE Assistant Toolbox",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=_SortedGroup,
)
console = Console()

_OUTPUT_CHOICES = ("json", "yaml")


def _apply_context(context: Optional[str]) -> None:
    set_context(context or None)


def _validate_output(output: Optional[str]) -> Optional[str]:
    if output and output.lower() not in _OUTPUT_CHOICES:
        console.print(
            f"[bold red]--output must be one of: {', '.join(_OUTPUT_CHOICES)}[/bold red]"
        )
        raise typer.Exit(1)
    return output.lower() if output else None


def _raw_output(
    resource: str, namespace: Optional[str], output: str, all_ns: bool = True
) -> None:
    """Delegate structured output to kubectl get <resource> -o json/yaml."""
    cmd = ["kubectl", "get", resource]
    if namespace:
        cmd.extend(["-n", namespace])
    elif all_ns:
        cmd.append("-A")
    cmd.extend(["-o", output])
    out = run_cmd(cmd)
    if out:
        print(out)


def _watch_loop(interval: int, fn, *args, **kwargs) -> None:
    """Repeatedly clear the screen and call fn(*args, **kwargs) every interval seconds."""
    try:
        while True:
            console.clear()
            console.print(
                f"[dim]Watch mode — refreshing every {interval}s. Press Ctrl+C to stop.[/dim]\n"
            )
            fn(*args, **kwargs)
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped.[/dim]")


@app.command(name="contexts")
def list_contexts():
    """List all available kubeconfig contexts and highlight the active one."""
    from kubernetes import config as k8s_config

    try:
        ctx_list, active = k8s_config.list_kube_config_contexts()
    except Exception as e:
        console.print(f"[bold red]Failed to load kubeconfig:[/bold red] {e}")
        raise typer.Exit(1)

    active_name = active["name"] if active else None
    table = Table(
        title="Kubeconfig Contexts",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Active", justify="center", width=6)
    table.add_column("Name", style="blue")
    table.add_column("Cluster", style="cyan")
    table.add_column("Namespace", style="dim")
    table.add_column("User", style="dim")

    for ctx in ctx_list:
        name = ctx["name"]
        info = ctx.get("context", {})
        is_active = name == active_name
        marker = "[green]✓[/green]" if is_active else ""
        table.add_row(
            marker,
            f"[bold]{name}[/bold]" if is_active else name,
            info.get("cluster", "—"),
            info.get("namespace", "default"),
            info.get("user", "—"),
        )
    console.print(table)


@app.command()
def pods(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Continuously poll for changes."
    ),
    interval: int = typer.Option(
        5, "--interval", "-i", help="Poll interval in seconds (with --watch)."
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output format: json or yaml."
    ),
):
    """Scan the K8s cluster for pod failures or crashloops."""
    _apply_context(context)
    output = _validate_output(output)
    if output:
        _raw_output("pods", namespace, output)
        return
    msg = f" in namespace '{namespace}'" if namespace else ""
    header = f"[bold cyan]Running K8s Pod Diagnostic{msg}...[/bold cyan]"
    if watch:
        _watch_loop(
            interval,
            lambda: (console.print(Panel.fit(header)), check_crashloop_pods(namespace)),
        )
    else:
        console.print(Panel.fit(header))
        check_crashloop_pods(namespace)


@app.command()
def deployments(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Continuously poll for changes."
    ),
    interval: int = typer.Option(
        5, "--interval", "-i", help="Poll interval in seconds (with --watch)."
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output format: json or yaml."
    ),
):
    """Scan the K8s cluster for degraded or unavailable deployments."""
    _apply_context(context)
    output = _validate_output(output)
    if output:
        _raw_output("deployments", namespace, output)
        return
    msg = f" in namespace '{namespace}'" if namespace else ""
    header = f"[bold cyan]Running K8s Deployment Diagnostic{msg}...[/bold cyan]"
    if watch:
        _watch_loop(
            interval,
            lambda: (console.print(Panel.fit(header)), check_deployments(namespace)),
        )
    else:
        console.print(Panel.fit(header))
        check_deployments(namespace)


@app.command()
def ask(
    question: str = typer.Argument(
        ..., help="Question to ask about the cluster state."
    ),
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Focus on a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Ask Claude to analyze live cluster diagnostics and answer your question."""
    _apply_context(context)
    console.print(Panel.fit("[bold cyan]Gathering diagnostics...[/bold cyan]"))

    buf = io.StringIO()
    with redirect_stdout(buf):
        check_crashloop_pods(namespace)
        check_events(namespace, event_type="Warning")

    diagnostic_context = buf.getvalue()

    # Auto-fetch logs from up to 3 failing pods to give Claude more signal
    failing = get_failing_pods(namespace)
    if failing:
        console.print(
            f"[dim]Fetching logs from {min(len(failing), 3)} failing pod(s)...[/dim]"
        )
        log_sections = []
        for pod_ns, pod_name in failing[:3]:
            log_out = run_cmd(["kubectl", "logs", pod_name, "-n", pod_ns, "--tail=50"])
            if log_out:
                log_sections.append(f"Logs for {pod_ns}/{pod_name}:\n{log_out}")
        if log_sections:
            diagnostic_context += "\n\n--- Failing Pod Logs ---\n" + "\n\n".join(
                log_sections
            )

    ai_ask(question, diagnostic_context)


@app.command(name="all")
def all_objects(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Troubleshoot ALL K8s objects (Nodes, PVCs, Workloads, and global warnings)."""
    _apply_context(context)
    msg = f" in namespace '{namespace}'" if namespace else " (Global)"
    console.print(
        Panel.fit(f"[bold cyan]Running Cluster Diagnostic{msg}...[/bold cyan]")
    )
    check_all_objects(namespace)


@app.command()
def flux(
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Scan FluxCD resources (Kustomizations, GitRepositories, HelmReleases) for sync failures."""
    _apply_context(context)
    console.print(Panel.fit("[bold cyan]Running FluxCD Diagnostic...[/bold cyan]"))
    check_flux_status()


@app.command()
def helm(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output format: json or yaml."
    ),
):
    """Scan Helm releases for failed or pending states."""
    _apply_context(context)
    output = _validate_output(output)
    if output:
        cmd = ["helm", "list", "-o", output]
        if namespace:
            cmd.extend(["-n", namespace])
        else:
            cmd.append("-A")
        out = run_cmd(cmd)
        if out:
            print(out)
        return
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(Panel.fit(f"[bold cyan]Running Helm Diagnostic{msg}...[/bold cyan]"))
    check_helm_status(namespace)


@app.command()
def kustomize(
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Filter controller logs by a specific namespace.",
    ),
    local_path: Optional[str] = typer.Option(
        None,
        "--build",
        "-b",
        help="Path to a local directory to run a dry-run kustomize build validation.",
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Troubleshoot Kustomize by scanning controller logs or validating local YAML directories."""
    _apply_context(context)
    console.print(Panel.fit("[bold cyan]Running Kustomize Diagnostic...[/bold cyan]"))
    check_kustomize_errors(namespace, local_path)


@app.command()
def vault(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Vault namespace (auto-detected if omitted)."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Scan Vault pods, StatefulSet replicas, and warning events for seal or health issues."""
    _apply_context(context)
    console.print(Panel.fit("[bold cyan]Running Vault Diagnostic...[/bold cyan]"))
    check_vault_status(namespace)


@app.command()
def kong(
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Scan Kong Ingress Controller proxy logs for errors."""
    _apply_context(context)
    console.print(Panel.fit("[bold cyan]Running Kong Diagnostic...[/bold cyan]"))
    check_kong_errors()


@app.command()
def trace(
    kind: str = typer.Argument(
        ...,
        help="Resource kind (pod, deployment, statefulset, daemonset, service, ingress, pvc).",
    ),
    name: str = typer.Argument(..., help="Name of the resource."),
    namespace: str = typer.Option(
        "default", "--namespace", "-n", help="Namespace of the resource."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Show a dependency tree for any K8s object to visualize root cause and object relationships."""
    _apply_context(context)
    console.print(
        Panel.fit(
            f"[bold cyan]Tracing {kind}/{name} in namespace '{namespace}'...[/bold cyan]"
        )
    )
    trace_object(kind, name, namespace)


@app.command()
def crd(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter instances by a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output format: json or yaml."
    ),
):
    """Scan all Custom Resource Definitions and surface instances with non-ready conditions."""
    _apply_context(context)
    output = _validate_output(output)
    if output:
        _raw_output("crds", namespace, output, all_ns=False)
        return
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(Panel.fit(f"[bold cyan]Running CRD Diagnostic{msg}...[/bold cyan]"))
    check_crd_status(namespace)


@app.command()
def events(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    event_type: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter by event type: Warning or Normal."
    ),
    reason: Optional[str] = typer.Option(
        None, "--reason", "-r", help="Filter by reason (partial, case-insensitive)."
    ),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        "-s",
        help="Show events newer than a duration (e.g. 30m, 2h, 1d).",
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Continuously poll for new events."
    ),
    interval: int = typer.Option(
        10, "--interval", "-i", help="Poll interval in seconds (with --watch)."
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output format: json or yaml."
    ),
):
    """Browse and filter Kubernetes events by type, reason, or age."""
    _apply_context(context)
    output = _validate_output(output)
    if output:
        _raw_output("events", namespace, output)
        return
    msg = f" in namespace '{namespace}'" if namespace else " (all namespaces)"
    header = f"[bold cyan]Fetching Events{msg}...[/bold cyan]"
    if watch:
        _watch_loop(
            interval,
            lambda: (
                console.print(Panel.fit(header)),
                check_events(namespace, event_type, reason, since),
            ),
        )
    else:
        console.print(Panel.fit(header))
        check_events(namespace, event_type, reason, since)


@app.command()
def network(
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Filter endpoint and NetworkPolicy checks by namespace.",
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Check CoreDNS health, services with no endpoints, and NetworkPolicy coverage."""
    _apply_context(context)
    console.print(Panel.fit("[bold cyan]Running Network Diagnostic...[/bold cyan]"))
    check_network_status(namespace)


@app.command()
def report(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Scope report to a specific namespace."
    ),
    title: Optional[str] = typer.Option(
        None, "--title", "-t", help="Custom report title."
    ),
    fail_on_issues: bool = typer.Option(
        False,
        "--fail-on-issues",
        "-f",
        help="Exit with code 1 if any issues are found (for CI pipelines).",
    ),
    copy: bool = typer.Option(
        False,
        "--copy",
        "-C",
        help="Copy the Markdown report to the clipboard instead of printing it.",
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Generate a Markdown cluster health report for CI or scheduled digests."""
    _apply_context(context)
    try:
        md, has_issues = generate_report(namespace=namespace, title=title)
    except Exception as e:
        console.print(f"[bold red]Report failed:[/bold red] {e}")
        raise typer.Exit(code=1)
    if copy:
        try:
            copy_to_clipboard(md)
            console.print("[green]✓ Report copied to clipboard.[/green]")
        except Exception as e:
            console.print(f"[bold red]Clipboard error:[/bold red] {e}")
            raise typer.Exit(code=1)
    else:
        print(md)
    if fail_on_issues and has_issues:
        raise typer.Exit(code=1)


@app.command()
def rbac(
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Scan RBAC for Forbidden events, unbound ServiceAccounts, and role binding summary."""
    _apply_context(context)
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(Panel.fit(f"[bold cyan]Running RBAC Diagnostic{msg}...[/bold cyan]"))
    check_rbac_status(namespace)


@app.command()
def logs(
    name: str = typer.Argument(..., help="Name of the pod, deployment, or resource."),
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by namespace."
    ),
    previous: bool = typer.Option(
        False,
        "--previous",
        "-p",
        help="Get logs for a previously terminated container.",
    ),
    tail: int = typer.Option(100, "--tail", "-t", help="Number of lines to tail."),
    analyze: bool = typer.Option(
        False, "--analyze", "-a", help="Send logs to AI for root-cause analysis."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Fetch and print logs for a specific K8s object (safe wrapper)."""
    _apply_context(context)
    if analyze:
        buf = io.StringIO()
        with redirect_stdout(buf):
            check_logs(name, namespace, previous, tail)
        logs_output = buf.getvalue()
        print(logs_output)
        resource = name + (f" -n {namespace}" if namespace else "")
        analyze_logs(logs_output, resource)
    else:
        check_logs(name, namespace, previous, tail)


@app.command()
def describe(
    kind: str = typer.Argument(
        ..., help="Kind of the resource (e.g., pod, deployment, pvc, node)."
    ),
    name: str = typer.Argument(..., help="Name of the resource."),
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Filter by namespace."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubeconfig context to use."
    ),
):
    """Fetch and gracefully format the describe output of any K8s object (safe wrapper)."""
    _apply_context(context)
    describe_object(kind, name, namespace)


@app.command()
def interactive():
    """Interactive mode with autocomplete and history."""
    history_file = os.path.expanduser("~/.k8s_tool_history")

    command_names = [
        str(cmd.name) for cmd in app.registered_commands if cmd.name is not None
    ]
    all_completions = command_names + ["exit", "quit", "help"]

    completer = WordCompleter(all_completions, ignore_case=True)
    session = PromptSession(completer=completer, history=FileHistory(history_file))

    console.print("[bold magenta]Interactive Shell Started.[/bold magenta]")

    while True:
        try:
            text = session.prompt(HTML("<cyan><b>kubebox> </b></cyan>"))

            if not text:
                continue

            cleaned_text = text.strip()
            if cleaned_text.lower() in ("exit", "quit"):
                break

            cmd = cleaned_text.split()[0].lower()
            if cmd in ("dashboard", "interactive"):
                console.print(
                    f"[yellow]'{cmd}' cannot be run from interactive mode.[/yellow]"
                )
                continue

            app(shlex.split(cleaned_text))

        except SystemExit:
            continue
        except EOFError:
            break
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")


@app.command()
def dashboard():
    """Launch the TUI Dashboard (Table-style navigation)."""
    from core.tui import K8sToolApp

    ui = K8sToolApp(app)
    ui.run()


if __name__ == "__main__":
    app(prog_name="kubebox")
