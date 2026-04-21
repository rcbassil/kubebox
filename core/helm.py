import json
from rich.table import Table
from core.utils import run_cmd, console, print_tip


def _show_helm_history(name: str, namespace: str) -> None:
    """Print the last 5 revisions of a Helm release."""
    out = run_cmd(
        ["helm", "history", name, "-n", namespace, "-o", "json", "--max", "5"]
    )
    if not out:
        return
    try:
        history = json.loads(out)
    except json.JSONDecodeError:
        return
    if not history:
        return

    table = Table(
        title=f"Release History: {name}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Rev", justify="right", style="dim")
    table.add_column("Updated", style="dim", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Chart", style="dim")
    table.add_column("App Version", style="dim")
    table.add_column("Description")

    for rev in history:
        status = rev.get("status", "unknown")
        color = (
            "green"
            if status == "deployed"
            else "red"
            if status == "failed"
            else "yellow"
        )
        updated = (rev.get("updated") or "")[:19]
        desc = (rev.get("description") or "")[:70]
        table.add_row(
            str(rev.get("revision", "?")),
            updated,
            f"[{color}]{status}[/{color}]",
            rev.get("chart", "?"),
            rev.get("app_version", "?"),
            desc,
        )
    console.print(table)


def check_helm_status(namespace: str = None):
    """Check Helm releases for non-deployed status (e.g. failed, pending-*)."""
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(f"[bold blue]Checking Helm Releases status{msg}...[/bold blue]")

    cmd = ["helm", "list", "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    else:
        cmd.append("-A")

    out = run_cmd(cmd)
    if not out:
        return

    try:
        releases = json.loads(out)
    except json.JSONDecodeError:
        console.print(
            "[bold red]Failed to parse helm output. Is helm installed?[/bold red]"
        )
        return

    if not releases:
        console.print("[dim]No Helm releases found.[/dim]")
        return

    _STABLE = {"deployed", "superseded", "uninstalled"}
    _FAILED = {"failed", "pending-install", "pending-upgrade", "pending-rollback"}

    failing = False
    for r in releases:
        if r.get("status", "unknown") not in _STABLE:
            failing = True
            break

    if failing:
        fail_table = Table(
            title="Problematic Helm Releases",
            show_header=True,
            header_style="bold magenta",
        )
        fail_table.add_column("Namespace", style="cyan")
        fail_table.add_column("Name", style="blue")
        fail_table.add_column("Status", justify="center")
        fail_table.add_column("Chart", style="dim white")
        fail_table.add_column("App Version", style="dim white")
        first_bad = None
        for r in releases:
            status = r.get("status", "unknown")
            if status not in _STABLE:
                color = "red" if status in _FAILED else "yellow"
                fail_table.add_row(
                    r.get("namespace", "default"),
                    r.get("name", "unknown"),
                    f"[{color}]{status}[/{color}]",
                    r.get("chart", "unknown"),
                    r.get("app_version", "unknown"),
                )
                if not first_bad:
                    first_bad = r
        console.print(fail_table)
        if first_bad:
            print_tip(
                "A pending-install/failed release means the chart templates were invalid, hooks failed, or a timeout occurred and it rolled back.",
                f"helm history -n {first_bad.get('namespace', 'default')} {first_bad.get('name', '<release>')}",
            )
        for r in releases:
            if r.get("status", "unknown") not in _STABLE:
                _show_helm_history(r.get("name", ""), r.get("namespace", "default"))
    else:
        console.print("[green]✓ All Helm releases are stable[/green]")

    all_table = Table(
        title="All Helm Releases", show_header=True, header_style="bold magenta"
    )
    all_table.add_column("Namespace", style="cyan")
    all_table.add_column("Name", style="blue")
    all_table.add_column("Status", justify="center")
    all_table.add_column("Chart", style="dim white")
    all_table.add_column("App Version", style="dim white")
    for r in releases:
        status = r.get("status", "unknown")
        color = (
            "green" if status in _STABLE else ("red" if status in _FAILED else "yellow")
        )
        all_table.add_row(
            r.get("namespace", "default"),
            r.get("name", "unknown"),
            f"[{color}]{status}[/{color}]",
            r.get("chart", "unknown"),
            r.get("app_version", "unknown"),
        )
    console.print(all_table)
