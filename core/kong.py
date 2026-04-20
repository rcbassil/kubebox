from kubernetes import client, config
from rich.table import Table
from core.utils import console, print_tip


def check_kong_errors():
    """Scan latest Kong ingress controller logs for Errors."""
    try:
        config.load_kube_config()
    except Exception:
        return

    v1 = client.CoreV1Api()
    console.print(
        "[bold blue]Checking Kong Ingress Controller logs for recent errors...[/bold blue]"
    )

    try:
        pods = v1.list_pod_for_all_namespaces().items
        kong_pods = [p for p in pods if "kong" in p.metadata.name.lower()]

        if not kong_pods:
            console.print("[dim]No Kong pods found in cluster.[/dim]")
            return

        failing = False
        for pod in kong_pods:
            try:
                logs = v1.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    tail_lines=100,
                )
                errors = [
                    line
                    for line in logs.split("\n")
                    if "[error]" in line.lower() or 'level="error"' in line.lower()
                ]

                if errors:
                    console.print(
                        f"\n[bold red]Found {len(errors)} errors in {pod.metadata.name} (Namespace: {pod.metadata.namespace}):[/bold red]"
                    )
                    for err in errors[-10:]:
                        console.print(f"  [red]-[/red] [dim]{err}[/dim]")
                    failing = True
                    print_tip(
                        "Kong proxy errors usually trigger from unresolvable upstream Services or invalid KongPlugin objects blocking the config sync.",
                        f"kubectl get kongplugins -n {pod.metadata.namespace} && kubectl get ingress -n {pod.metadata.namespace}",
                    )
            except Exception as e:
                console.print(
                    f"[dim]Could not read logs for {pod.metadata.name}: {e}[/dim]"
                )

        if not failing:
            console.print("[green]✓ No recent errors in Kong proxy logs.[/green]")

        table = Table(
            title="All Kong Pods", show_header=True, header_style="bold magenta"
        )
        table.add_column("Namespace", style="cyan")
        table.add_column("Pod", style="blue")
        table.add_column("Phase")
        table.add_column("Ready", justify="center")
        for pod in kong_pods:
            phase = pod.status.phase or "Unknown"
            ready = sum(1 for cs in (pod.status.container_statuses or []) if cs.ready)
            total = len(pod.status.container_statuses or [])
            healthy = phase == "Running" and ready == total
            color = "green" if healthy else "red"
            table.add_row(
                pod.metadata.namespace,
                pod.metadata.name,
                f"[{color}]{phase}[/{color}]",
                f"[{color}]{ready}/{total}[/{color}]",
            )
        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Error calling Kubernetes API:[/bold red] {e}")
