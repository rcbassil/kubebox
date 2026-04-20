import json
from rich.table import Table
from core.utils import run_cmd, console, print_tip

_RESOURCE_KIND_MAP = {
    "Git Repositories": "gitrepositories",
    "Kustomizations": "kustomizations",
    "Helm Releases": "helmreleases",
}


def check_flux_status():
    """Check Flux components (Kustomizations, HelmReleases, GitRepositories) that are failing."""
    console.print("[bold blue]Checking FluxCD resources...[/bold blue]")

    resources = {
        "Git Repositories": ["kubectl", "get", "gitrepositories", "-A", "-o", "json"],
        "Kustomizations": ["kubectl", "get", "kustomizations", "-A", "-o", "json"],
        "Helm Releases": ["kubectl", "get", "helmreleases", "-A", "-o", "json"],
    }

    for name, cmd in resources.items():
        out = run_cmd(cmd)
        if out:
            _check_generic_conditions(out, name)


def _check_generic_conditions(json_output: str, resource_type: str):
    try:
        data = json.loads(json_output)
    except json.JSONDecodeError:
        console.print(
            f"[yellow]Warning: could not parse {resource_type} response as JSON[/yellow]"
        )
        return

    items = data.get("items", [])
    if not items:
        return

    failing = False
    rows = []
    for item in items:
        meta = item.get("metadata", {})
        conditions = item.get("status", {}).get("conditions", [])
        is_ready = "Unknown"
        message = ""
        for c in conditions:
            if c.get("type") == "Ready":
                is_ready = c.get("status")
                message = c.get("message", "")
                break
        if is_ready != "True":
            failing = True
        rows.append((meta.get("namespace"), meta.get("name"), is_ready, message))

    if failing:
        fail_table = Table(
            title=f"Failed {resource_type}",
            show_header=True,
            header_style="bold magenta",
        )
        fail_table.add_column("Namespace", style="cyan")
        fail_table.add_column("Name", style="blue")
        fail_table.add_column("Ready", justify="center")
        fail_table.add_column("Message", style="dim white")
        for ns, name, is_ready, message in rows:
            if is_ready != "True":
                status_text = (
                    f"[red]{is_ready}[/red]"
                    if is_ready == "False"
                    else f"[yellow]{is_ready}[/yellow]"
                )
                fail_table.add_row(
                    ns,
                    name,
                    status_text,
                    message[:100] + "..." if len(message) > 100 else message,
                )
        console.print(f"\n[bold yellow]Failed {resource_type}:[/bold yellow]")
        console.print(fail_table)
        kind_cmd = _RESOURCE_KIND_MAP.get(
            resource_type, resource_type.lower().replace(" ", "")
        )
        print_tip(
            f"If {resource_type} are failing to reconcile, check the exact status condition message or inspect the source-controller/kustomize-controller logs.",
            f"kubectl describe {kind_cmd} -A",
        )
    else:
        console.print(f"[green]✓ All {resource_type} are Ready[/green]")

    all_table = Table(
        title=f"All {resource_type}", show_header=True, header_style="bold magenta"
    )
    all_table.add_column("Namespace", style="cyan")
    all_table.add_column("Name", style="blue")
    all_table.add_column("Ready", justify="center")
    for ns, name, is_ready, _ in rows:
        color = (
            "green"
            if is_ready == "True"
            else ("red" if is_ready == "False" else "yellow")
        )
        all_table.add_row(ns, name, f"[{color}]{is_ready}[/{color}]")
    console.print(all_table)
