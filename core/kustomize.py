import subprocess
from kubernetes import client, config
from rich.table import Table
from core.utils import console, print_tip


def check_kustomize_errors(namespace: str = None, local_path: str = None):
    """Troubleshoot Kustomize by either checking the flux controller logs or running a local build."""

    if local_path:
        console.print(
            f"[bold blue]Running local dry-run for Kustomize path:[/bold blue] {local_path}"
        )
        try:
            # Run kustomize build to see if there are YAML formatting or ref errors
            result = subprocess.run(
                ["kubectl", "kustomize", local_path], capture_output=True, text=True
            )
            if result.returncode != 0:
                console.print("[bold red]Kustomize Build Failed![/bold red]")
                console.print(f"[dim]{result.stderr}[/dim]")
            else:
                console.print(
                    "[green]✓ Local Kustomize build succeeded (YAML is valid).[/green]"
                )
        except FileNotFoundError:
            console.print(
                "[bold red]kubectl command not found on the system.[/bold red]"
            )
        return

    # If no local path, check cluster Flux Kustomizations
    try:
        config.load_kube_config()
    except Exception:
        return

    v1 = client.CoreV1Api()
    msg = f" in namespace '{namespace}'" if namespace else " in flux-system"
    console.print(
        f"[bold blue]Checking Kustomize-Controller logs for parse/apply errors{msg}...[/bold blue]"
    )

    try:
        ns = namespace if namespace else "flux-system"
        pods = v1.list_namespaced_pod(ns).items
        kustomize_pods = [
            p for p in pods if "kustomize-controller" in p.metadata.name.lower()
        ]

        if not kustomize_pods:
            console.print(
                f"[dim]No kustomize-controller pods found in namespace {ns}.[/dim]"
            )
            return

        failing = False
        for pod in kustomize_pods:
            try:
                logs = v1.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    tail_lines=200,
                )
                # Parse logs for level=error
                errors = [
                    line
                    for line in logs.split("\n")
                    if '"level":"error"' in line.lower()
                    or "error" in line.lower().split(" ", 1)[0]
                ]

                # Deduplicate similar sequential errors
                unique_errors = list(dict.fromkeys(errors[-15:]))

                if unique_errors:
                    console.print(
                        f"\n[bold red]Found {len(unique_errors)} recent errors in {pod.metadata.name}:[/bold red]"
                    )
                    for err in unique_errors:
                        console.print(f"  [red]-[/red] [dim]{err}[/dim]")
                    failing = True
                    print_tip(
                        "Kustomize-controller errors usually indicate bad YAML references, missing ConfigMaps, or invalid patches in Git.",
                        "kubectl kustomize <local-path-to-faulty-directory>",
                    )
            except Exception as e:
                console.print(
                    f"[dim]Could not read logs for {pod.metadata.name}: {e}[/dim]"
                )

        if not failing:
            console.print(
                "[green]✓ No recent errors discovered in Kustomize Controller logs.[/green]"
            )

        table = Table(
            title="All Kustomize Controller Pods",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Namespace", style="cyan")
        table.add_column("Pod", style="blue")
        table.add_column("Phase")
        table.add_column("Ready", justify="center")
        for pod in kustomize_pods:
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
