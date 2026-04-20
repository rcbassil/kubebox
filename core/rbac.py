from kubernetes import client, config
from rich.table import Table

from core.utils import console, print_tip

_SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


def check_rbac_status(namespace: str = None):
    """Scan RBAC config for Forbidden events, unbound ServiceAccounts, and role binding summary."""
    try:
        config.load_kube_config()
    except Exception as e:
        console.print(f"[bold red]Failed to load kube config:[/bold red] {e}")
        return

    v1 = client.CoreV1Api()
    rbac = client.RbacAuthorizationV1Api()

    _check_forbidden_events(v1, namespace)
    _check_unbound_service_accounts(v1, rbac, namespace)
    _check_role_bindings(rbac, namespace)


def _check_forbidden_events(v1, namespace: str):
    console.print("[bold blue]Forbidden / Unauthorized Events:[/bold blue]")
    try:
        events = (
            v1.list_namespaced_event(namespace).items
            if namespace
            else v1.list_event_for_all_namespaces().items
        )

        forbidden = [
            e
            for e in events
            if "forbidden" in (e.message or "").lower()
            or "unauthorized" in (e.message or "").lower()
        ]

        if not forbidden:
            console.print("[green]✓ No Forbidden/Unauthorized events found[/green]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Namespace", style="cyan")
        table.add_column("Object", style="blue")
        table.add_column("Reason", style="yellow")
        table.add_column("Message")

        for e in forbidden[-20:]:
            obj = (
                f"{e.involved_object.kind}/{e.involved_object.name}"
                if e.involved_object
                else "?"
            )
            msg = (e.message or "")[:120]
            table.add_row(e.metadata.namespace or "?", obj, e.reason or "?", msg)

        console.print(table)

        first = forbidden[-1]
        if first.involved_object:
            print_tip(
                "Forbidden events usually mean the pod's ServiceAccount lacks the required RBAC permissions. Check what role bindings exist for that ServiceAccount.",
                f"kubectl describe {first.involved_object.kind.lower()} {first.involved_object.name} -n {first.metadata.namespace}",
            )

    except Exception as e:
        console.print(f"[bold red]Error fetching events:[/bold red] {e}")


def _check_unbound_service_accounts(v1, rbac, namespace: str):
    console.print("\n[bold blue]ServiceAccounts with No Role Bindings:[/bold blue]")
    try:
        sas = (
            v1.list_namespaced_service_account(namespace).items
            if namespace
            else v1.list_service_account_for_all_namespaces().items
        )
        rbs = (
            rbac.list_namespaced_role_binding(namespace).items
            if namespace
            else rbac.list_role_binding_for_all_namespaces().items
        )
        crbs = rbac.list_cluster_role_binding().items

        bound: set[tuple[str, str]] = set()
        for rb in rbs:
            for sub in rb.subjects or []:
                if sub.kind == "ServiceAccount":
                    bound.add((sub.namespace or rb.metadata.namespace, sub.name))
        for crb in crbs:
            for sub in crb.subjects or []:
                if sub.kind == "ServiceAccount" and sub.namespace:
                    bound.add((sub.namespace, sub.name))

        unbound = [
            sa
            for sa in sas
            if (sa.metadata.namespace, sa.metadata.name) not in bound
            and sa.metadata.name != "default"
            and sa.metadata.namespace not in _SYSTEM_NAMESPACES
        ]

        if not unbound:
            console.print(
                "[green]✓ All non-default ServiceAccounts have at least one role binding[/green]"
            )
            return

        table = Table(
            title=f"ServiceAccounts with No Bindings ({len(unbound)})",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Namespace", style="cyan")
        table.add_column("ServiceAccount", style="blue")
        for sa in unbound[:30]:
            table.add_row(sa.metadata.namespace, sa.metadata.name)
        if len(unbound) > 30:
            table.add_row("[dim]...[/dim]", f"[dim]and {len(unbound) - 30} more[/dim]")
        console.print(table)
        console.print(
            "[dim]Unbound ServiceAccounts may cause Forbidden errors if pods use them to call the Kubernetes API.[/dim]"
        )

    except Exception as e:
        console.print(f"[bold red]Error checking ServiceAccounts:[/bold red] {e}")


def _check_role_bindings(rbac, namespace: str):
    console.print("\n[bold blue]Role Bindings Summary:[/bold blue]")
    try:
        rbs = (
            rbac.list_namespaced_role_binding(namespace).items
            if namespace
            else rbac.list_role_binding_for_all_namespaces().items
        )
        crbs = [] if namespace else rbac.list_cluster_role_binding().items

        if not rbs and not crbs:
            console.print("[dim]No role bindings found.[/dim]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Namespace", style="cyan")
        table.add_column("Binding", style="blue")
        table.add_column("Role", style="yellow")
        table.add_column("Subjects", style="dim")

        for rb in rbs:
            subjects = ", ".join(f"{s.kind}/{s.name}" for s in (rb.subjects or []))
            table.add_row(
                rb.metadata.namespace or "?",
                rb.metadata.name,
                rb.role_ref.name,
                subjects[:80] + "..." if len(subjects) > 80 else subjects,
            )

        for crb in crbs:
            subjects = ", ".join(f"{s.kind}/{s.name}" for s in (crb.subjects or []))
            table.add_row(
                "[dim]cluster-wide[/dim]",
                crb.metadata.name,
                crb.role_ref.name,
                subjects[:80] + "..." if len(subjects) > 80 else subjects,
            )

        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Error fetching role bindings:[/bold red] {e}")
