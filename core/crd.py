import json
from rich.table import Table
from core.utils import run_cmd, console, print_tip

_READY_CONDITIONS = {"Ready", "Available", "Synced", "Healthy"}


def check_crd_status(namespace: str = None):
    """Discover all CRDs, fetch their instances, and surface any with non-ready conditions."""
    console.print("[bold blue]Discovering Custom Resource Definitions...[/bold blue]")

    out = run_cmd(["kubectl", "get", "crds", "-o", "json"])
    if not out:
        return

    try:
        crds = json.loads(out).get("items", [])
    except json.JSONDecodeError:
        console.print("[yellow]Warning: could not parse CRD list[/yellow]")
        return

    if not crds:
        console.print("[dim]No Custom Resource Definitions found in the cluster.[/dim]")
        return

    console.print(f"[dim]Found {len(crds)} CRDs — scanning instances...[/dim]\n")

    # (crd_name, instance_ns, instance_name, ready, message)
    failing: list[tuple] = []
    # keyed by (crd_name, namespace) → [healthy, failing]
    summary: dict[tuple, list[int]] = {}

    for crd in crds:
        try:
            crd_name = crd["metadata"]["name"]
            plural = crd["spec"]["names"]["plural"]
            group = crd["spec"]["group"]
        except (KeyError, TypeError):
            continue

        cmd = ["kubectl", "get", f"{plural}.{group}", "-o", "json"]
        if namespace:
            cmd.extend(["-n", namespace])
        else:
            cmd.append("-A")

        raw = run_cmd(cmd)
        if not raw:
            continue

        try:
            items = json.loads(raw).get("items", [])
        except json.JSONDecodeError:
            console.print(
                f"[yellow]Warning: could not parse instances for {crd_name}[/yellow]"
            )
            continue

        if not items:
            continue

        for item in items:
            meta = item.get("metadata", {})
            item_ns = meta.get("namespace", "cluster")
            item_name = meta.get("name", "unknown")
            conditions = item.get("status", {}).get("conditions", [])

            ready_status = "Unknown"
            message = ""
            for c in conditions:
                if c.get("type") in _READY_CONDITIONS:
                    ready_status = c.get("status", "Unknown")
                    message = c.get("message", "")
                    break

            key = (crd_name, item_ns)
            if key not in summary:
                summary[key] = [0, 0]  # [healthy, failing]

            if ready_status == "False":
                summary[key][1] += 1
                failing.append((crd_name, item_ns, item_name, ready_status, message))
            else:
                summary[key][0] += 1

    # --- Summary table ---
    summary_table = Table(
        title="Custom Resource Summary", show_header=True, header_style="bold magenta"
    )
    summary_table.add_column("CRD", style="blue")
    summary_table.add_column("Namespace", style="cyan")
    summary_table.add_column("Instances", justify="right", style="dim")
    summary_table.add_column("Healthy", justify="right")
    summary_table.add_column("Failing", justify="right")

    for (crd_name, ns), (healthy, fail_count) in summary.items():
        total = healthy + fail_count
        fail_str = (
            f"[red]{fail_count}[/red]" if fail_count else f"[green]{fail_count}[/green]"
        )
        summary_table.add_row(
            crd_name, ns, str(total), f"[green]{healthy}[/green]", fail_str
        )

    if summary:
        console.print(summary_table)
    else:
        console.print("[dim]No custom resource instances found.[/dim]")
        return

    # --- Failing instances table ---
    if failing:
        fail_table = Table(
            title="Failing Custom Resource Instances",
            show_header=True,
            header_style="bold magenta",
        )
        fail_table.add_column("CRD", style="blue")
        fail_table.add_column("Namespace", style="cyan")
        fail_table.add_column("Name", style="blue")
        fail_table.add_column("Ready", justify="center")
        fail_table.add_column("Message", style="dim white")

        for crd_name, item_ns, item_name, ready, message in failing:
            msg = message[:100] + "..." if len(message) > 100 else message
            fail_table.add_row(crd_name, item_ns, item_name, "[red]False[/red]", msg)

        console.print(fail_table)

        first_crd, first_ns, first_name, *_ = failing[0]
        print_tip(
            "A custom resource with Ready=False usually means its controller detected a reconciliation error. Check the controller logs or describe the object.",
            f"kubectl describe {first_crd.split('.')[0]} {first_name} -n {first_ns}",
        )
    else:
        console.print("\n[green]✓ All custom resource instances are healthy[/green]")
