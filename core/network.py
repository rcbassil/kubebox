from kubernetes import client, config
from rich.table import Table

from core.utils import console, print_tip

_SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


def check_network_status(namespace: str = None):
    """Check CoreDNS health, services with no endpoints, and NetworkPolicy coverage."""
    try:
        config.load_kube_config()
    except Exception as e:
        console.print(f"[bold red]Failed to load kube config:[/bold red] {e}")
        return

    v1 = client.CoreV1Api()
    net = client.NetworkingV1Api()

    _check_coredns(v1)
    _check_endpoints(v1, namespace)
    _check_network_policies(v1, net, namespace)


def _check_coredns(v1):
    console.print("[bold blue]CoreDNS Health:[/bold blue]")
    try:
        pods = v1.list_pod_for_all_namespaces(label_selector="k8s-app=kube-dns").items
        if not pods:
            console.print(
                "[yellow]⚠ No CoreDNS pods found (label: k8s-app=kube-dns)[/yellow]"
            )
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Pod", style="blue")
        table.add_column("Namespace", style="cyan")
        table.add_column("Phase")
        table.add_column("Ready", justify="center")
        table.add_column("Restarts", justify="right", style="yellow")

        issues = []
        for pod in pods:
            phase = pod.status.phase or "Unknown"
            ready = sum(1 for cs in (pod.status.container_statuses or []) if cs.ready)
            total = len(pod.status.container_statuses or [])
            restarts = sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            )
            healthy = phase == "Running" and ready == total
            color = "green" if healthy else "red"
            table.add_row(
                pod.metadata.name,
                pod.metadata.namespace,
                f"[{color}]{phase}[/{color}]",
                f"[{color}]{ready}/{total}[/{color}]",
                str(restarts),
            )
            if not healthy:
                issues.append((pod.metadata.name, pod.metadata.namespace))

        console.print(table)

        if issues:
            pod_name, pod_ns = issues[0]
            print_tip(
                "CoreDNS pods that are not Ready will cause DNS resolution failures cluster-wide.",
                f"kubectl logs {pod_name} -n {pod_ns}",
            )
        else:
            console.print("[green]✓ All CoreDNS pods are Running and Ready[/green]")

    except Exception as e:
        console.print(f"[bold red]Error checking CoreDNS:[/bold red] {e}")


def _check_endpoints(v1, namespace: str):
    console.print("\n[bold blue]Services with No Ready Endpoints:[/bold blue]")
    try:
        services = (
            v1.list_namespaced_service(namespace).items
            if namespace
            else v1.list_service_for_all_namespaces().items
        )

        no_endpoints = []
        for svc in services:
            if svc.spec.type == "ExternalName" or svc.spec.cluster_ip == "None":
                continue
            if not svc.spec.selector:
                continue
            ns = svc.metadata.namespace
            name = svc.metadata.name
            try:
                ep = v1.read_namespaced_endpoints(name, ns)
                ready = sum(len(s.addresses or []) for s in (ep.subsets or []))
                if ready == 0:
                    no_endpoints.append((ns, name, svc.spec.type or "ClusterIP"))
            except Exception:
                pass

        if no_endpoints:
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Namespace", style="cyan")
            table.add_column("Service", style="blue")
            table.add_column("Type", style="dim")
            for ns, name, svc_type in no_endpoints:
                table.add_row(ns, name, svc_type)
            console.print(table)
            first_ns, first_name, _ = no_endpoints[0]
            print_tip(
                "A service with no ready endpoints means its selector matches no running pods. Check if the backing pods are healthy.",
                f"kubectl describe service {first_name} -n {first_ns}",
            )
        else:
            console.print(
                "[green]✓ All services with selectors have at least one ready endpoint[/green]"
            )

    except Exception as e:
        console.print(f"[bold red]Error checking endpoints:[/bold red] {e}")


def _check_network_policies(v1, net, namespace: str):
    console.print("\n[bold blue]NetworkPolicy Coverage:[/bold blue]")
    try:
        if namespace:
            policies = net.list_namespaced_network_policy(namespace).items
            pods = v1.list_namespaced_pod(namespace).items
        else:
            policies = net.list_network_policy_for_all_namespaces().items
            pods = v1.list_pod_for_all_namespaces().items

        running_pods = [
            p
            for p in pods
            if p.status.phase == "Running"
            and p.metadata.namespace not in _SYSTEM_NAMESPACES
        ]

        if not policies:
            console.print(
                "[dim]No NetworkPolicies found — all pod-to-pod traffic is unrestricted.[/dim]"
            )
            return

        console.print(f"[dim]Found {len(policies)} NetworkPolicies.[/dim]")

        uncovered = [
            p
            for p in running_pods
            if not any(
                _labels_match(
                    pol.spec.pod_selector.match_labels or {},
                    p.metadata.labels or {},
                )
                for pol in policies
                if pol.metadata.namespace == p.metadata.namespace
            )
        ]

        if uncovered:
            table = Table(
                title=f"Running Pods Not Covered by Any NetworkPolicy ({len(uncovered)})",
                show_header=True,
                header_style="bold magenta",
            )
            table.add_column("Namespace", style="cyan")
            table.add_column("Pod", style="blue")
            for pod in uncovered[:20]:
                table.add_row(pod.metadata.namespace, pod.metadata.name)
            if len(uncovered) > 20:
                table.add_row(
                    "[dim]...[/dim]", f"[dim]and {len(uncovered) - 20} more[/dim]"
                )
            console.print(table)
            console.print(
                "[dim]These pods have unrestricted ingress/egress. Consider adding NetworkPolicies for sensitive workloads.[/dim]"
            )
        else:
            console.print(
                "[green]✓ All running pods are covered by at least one NetworkPolicy[/green]"
            )

        policy_table = Table(
            title="NetworkPolicies", show_header=True, header_style="bold magenta"
        )
        policy_table.add_column("Namespace", style="cyan")
        policy_table.add_column("Name", style="blue")
        policy_table.add_column("Policy Types", style="dim")
        for p in policies:
            types = ", ".join(p.spec.policy_types or []) or "Ingress"
            policy_table.add_row(p.metadata.namespace, p.metadata.name, types)
        console.print(policy_table)

    except Exception as e:
        console.print(f"[bold red]Error checking NetworkPolicies:[/bold red] {e}")


def _labels_match(selector: dict, pod_labels: dict) -> bool:
    """Return True if all selector key/value pairs are present in pod_labels. Empty selector matches all pods."""
    return all(pod_labels.get(k) == v for k, v in selector.items())
