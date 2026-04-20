import typer
from rich.console import Console
from rich.panel import Panel

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

app = typer.Typer(
    help="🤖 Read-Only Kubernetes DevOps/SRE Assistant Toolbox", no_args_is_help=True
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
def verify_readonly():
    """Run a check to confirm no mutative actions are allowed."""
    from core.utils import run_cmd

    console.print("[dim]Attempting a dummy mutative command to test blocks...[/dim]")
    run_cmd(["kubectl", "apply", "-f", "dummy.yaml"])


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


if __name__ == "__main__":
    app()
