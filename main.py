import os
import shlex
import typer
from typer.core import TyperGroup
from rich.console import Console

from rich.panel import Panel
from prompt_toolkit import PromptSession, HTML
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory

from core.kubernetes import (
    check_crashloop_pods,
    check_all_objects,
    describe_object,
    check_logs,
)
from core.flux import check_flux_status
from core.helm import check_helm_status
from core.kong import check_kong_errors
from core.kustomize import check_kustomize_errors
from core.vault import check_vault_status
from core.trace import trace_object
from core.crd import check_crd_status
from core.events import check_events
from core.network import check_network_status
from core.rbac import check_rbac_status


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


@app.command()
def pods(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
):
    """Scan the K8s cluster for pod failures or crashloops."""
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(
        Panel.fit(f"[bold cyan]Running K8s Pod Diagnostic{msg}...[/bold cyan]")
    )
    check_crashloop_pods(namespace)


@app.command(name="all")
def all_objects(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
):
    """Troubleshoot ALL K8s objects (Nodes, PVCs, Workloads, and global warnings)."""
    msg = f" in namespace '{namespace}'" if namespace else " (Global)"
    console.print(
        Panel.fit(f"[bold cyan]Running Cluster Diagnostic{msg}...[/bold cyan]")
    )
    check_all_objects(namespace)


@app.command()
def flux():
    """Scan FluxCD resources (Kustomizations, GitRepositories, HelmReleases) for sync failures."""
    console.print(Panel.fit("[bold cyan]Running FluxCD Diagnostic...[/bold cyan]"))
    check_flux_status()


@app.command()
def helm(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
):
    """Scan Helm releases for failed or pending states."""
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(Panel.fit(f"[bold cyan]Running Helm Diagnostic{msg}...[/bold cyan]"))
    check_helm_status(namespace)


@app.command()
def kustomize(
    namespace: str = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Filter controller logs by a specific namespace.",
    ),
    local_path: str = typer.Option(
        None,
        "--build",
        "-b",
        help="Path to a local directory to run a dry-run kustomize build validation.",
    ),
):
    """Troubleshoot Kustomize by scanning controller logs or validating local YAML directories."""
    from rich.panel import Panel

    console.print(Panel.fit("[bold cyan]Running Kustomize Diagnostic...[/bold cyan]"))
    check_kustomize_errors(namespace, local_path)


@app.command()
def vault(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Vault namespace (auto-detected if omitted)."
    ),
):
    """Scan Vault pods, StatefulSet replicas, and warning events for seal or health issues."""
    console.print(Panel.fit("[bold cyan]Running Vault Diagnostic...[/bold cyan]"))
    check_vault_status(namespace)


@app.command()
def kong():
    """Scan Kong Ingress Controller proxy logs for errors."""
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
):
    """Show a dependency tree for any K8s object to visualize root cause and object relationships."""
    console.print(
        Panel.fit(
            f"[bold cyan]Tracing {kind}/{name} in namespace '{namespace}'...[/bold cyan]"
        )
    )
    trace_object(kind, name, namespace)


@app.command()
def crd(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter instances by a specific namespace."
    ),
):
    """Scan all Custom Resource Definitions and surface instances with non-ready conditions."""
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(Panel.fit(f"[bold cyan]Running CRD Diagnostic{msg}...[/bold cyan]"))
    check_crd_status(namespace)


@app.command()
def events(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
    event_type: str = typer.Option(
        None, "--type", "-t", help="Filter by event type: Warning or Normal."
    ),
    reason: str = typer.Option(
        None, "--reason", "-r", help="Filter by reason (partial, case-insensitive)."
    ),
    since: str = typer.Option(
        None,
        "--since",
        "-s",
        help="Show events newer than a duration (e.g. 30m, 2h, 1d).",
    ),
):
    """Browse and filter Kubernetes events by type, reason, or age."""
    msg = f" in namespace '{namespace}'" if namespace else " (all namespaces)"
    console.print(Panel.fit(f"[bold cyan]Fetching Events{msg}...[/bold cyan]"))
    check_events(namespace, event_type, reason, since)


@app.command()
def network(
    namespace: str = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Filter endpoint and NetworkPolicy checks by namespace.",
    ),
):
    """Check CoreDNS health, services with no endpoints, and NetworkPolicy coverage."""
    console.print(Panel.fit("[bold cyan]Running Network Diagnostic...[/bold cyan]"))
    check_network_status(namespace)


@app.command()
def rbac(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by a specific namespace."
    ),
):
    """Scan RBAC for Forbidden events, unbound ServiceAccounts, and role binding summary."""
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(Panel.fit(f"[bold cyan]Running RBAC Diagnostic{msg}...[/bold cyan]"))
    check_rbac_status(namespace)


@app.command()
def logs(
    name: str = typer.Argument(..., help="Name of the pod, deployment, or resource."),
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by namespace."
    ),
    previous: bool = typer.Option(
        False,
        "--previous",
        "-p",
        help="Get logs for a previously terminated container.",
    ),
    tail: int = typer.Option(100, "--tail", "-t", help="Number of lines to tail."),
):
    """Fetch and print logs for a specific K8s object (safe wrapper)."""
    check_logs(name, namespace, previous, tail)


@app.command()
def describe(
    kind: str = typer.Argument(
        ..., help="Kind of the resource (e.g., pod, deployment, pvc, node)."
    ),
    name: str = typer.Argument(..., help="Name of the resource."),
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="Filter by namespace."
    ),
):
    """Fetch and gracefully format the describe output of any K8s object (safe wrapper)."""
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
            # session.prompt returns the string entered by the user
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
            # Prevent the tool from closing after a subcommand finishes
            continue
        except EOFError:
            break
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")


@app.command()
def dashboard():
    """Launch the TUI Dashboard (Table-style navigation)."""
    # Import locally to keep the CLI fast for standard commands
    from core.tui import K8sToolApp

    ui = K8sToolApp(app)
    ui.run()


if __name__ == "__main__":
    app(prog_name="kubebox")
