from kubernetes import client, config
from core.utils import console, run_cmd, print_tip
from rich.table import Table
import json


def init_k8s():
    try:
        config.load_kube_config()
        return True
    except Exception as e:
        console.print(f"[bold red]Failed to load kube config:[/bold red] {e}")
        return False


def check_crashloop_pods(namespace: str = None):
    """Scan all namespaces (or a specific namespace) for any pods that are pending, pulling images, or in a CrashLoop."""
    if not init_k8s():
        return

    v1 = client.CoreV1Api()
    msg = f" in namespace '{namespace}'" if namespace else ""
    console.print(
        f"[bold blue]Checking Kubernetes for Failing Pods{msg}...[/bold blue]"
    )
    try:
        pods = (
            v1.list_namespaced_pod(namespace).items
            if namespace
            else v1.list_pod_for_all_namespaces().items
        )
    except Exception as e:
        console.print(f"[bold red]Error calling Kubernetes API:[/bold red] {e}")
        return

    rows = []
    failing = False
    for pod in pods:
        status_string = pod.status.phase
        restarts = 0

        if pod.status.container_statuses:
            for container_status in pod.status.container_statuses:
                restarts += container_status.restart_count
                if (
                    container_status.state.waiting
                    and container_status.state.waiting.reason
                    in [
                        "CrashLoopBackOff",
                        "ErrImagePull",
                        "ImagePullBackOff",
                        "CreateContainerConfigError",
                    ]
                ):
                    status_string = container_status.state.waiting.reason
                elif (
                    container_status.state.terminated
                    and container_status.state.terminated.exit_code != 0
                ):
                    status_string = container_status.state.terminated.reason or "Error"

        healthy = status_string in ("Running", "Succeeded") or pod.status.phase in (
            "Running",
            "Succeeded",
        )
        if not healthy:
            failing = True
        rows.append(
            (
                pod.metadata.namespace,
                pod.metadata.name,
                status_string,
                restarts,
                healthy,
            )
        )

    if failing:
        fail_table = Table(
            title="Failing Pods", show_header=True, header_style="bold magenta"
        )
        fail_table.add_column("Namespace", style="cyan", width=20)
        fail_table.add_column("Pod Name", style="blue")
        fail_table.add_column("Status", style="red")
        fail_table.add_column("Restarts", justify="right", style="green")
        for ns, name, status, restarts, healthy in rows:
            if not healthy:
                fail_table.add_row(ns, name, status, str(restarts))
        console.print(fail_table)
        print_tip(
            "CrashLoopBackOff or Error states generally mean the application crashed on startup or lost connection to a critical resource.",
            f"kubectl get events {'-n ' + namespace if namespace else '-A'} --sort-by='.metadata.creationTimestamp' | tail -n 15",
        )
    else:
        console.print("[green]✓ All pods are healthy and running normally![/green]")

    console.print("\n[bold blue]All Pods:[/bold blue]")
    all_table = Table(show_header=True, header_style="bold magenta")
    all_table.add_column("Namespace", style="cyan", width=20)
    all_table.add_column("Pod Name", style="blue")
    all_table.add_column("Status")
    all_table.add_column("Restarts", justify="right")
    for ns, name, status, restarts, healthy in rows:
        color = "green" if healthy else "red"
        all_table.add_row(ns, name, f"[{color}]{status}[/{color}]", str(restarts))
    console.print(all_table)


def check_all_objects(namespace: str = None):
    """Troubleshoot ALL objects in the cluster (Nodes, PVCs, Workloads) by looking at warning events and rollout status."""
    if not init_k8s():
        return

    scope = f"Namespace: {namespace}" if namespace else "Cluster-Wide"
    console.print(f"\n[bold blue]=== {scope} Object Diagnostics ===[/bold blue]")

    if not namespace:
        # Check 1: Nodes (Global only)
        v1 = client.CoreV1Api()
        nodes = v1.list_node().items
        not_ready_nodes = []
        for node in nodes:
            ready = any(
                c.type == "Ready" and c.status == "True" for c in node.status.conditions
            )
            if not ready:
                not_ready_nodes.append(node.metadata.name)

        if not_ready_nodes:
            console.print(
                f"[bold red]⚠️  Found {len(not_ready_nodes)} NotReady nodes: {', '.join(not_ready_nodes)}[/bold red]"
            )
            print_tip(
                "A node is NotReady if the Kubelet stopped posting status or if there's extreme memory/disk pressure.",
                f"kubectl describe node {not_ready_nodes[0]}",
            )
        else:
            console.print("[green]✓ All Nodes are Ready[/green]")

        node_table = Table(
            title="All Nodes", show_header=True, header_style="bold magenta"
        )
        node_table.add_column("Node", style="blue")
        node_table.add_column("Status")
        for node in nodes:
            ready = any(
                c.type == "Ready" and c.status == "True" for c in node.status.conditions
            )
            node_table.add_row(
                node.metadata.name,
                "[green]Ready[/green]" if ready else "[red]NotReady[/red]",
            )
        console.print(node_table)
    else:
        v1 = client.CoreV1Api()

    # Check 2: PVCs not Bound
    pvcs = (
        v1.list_namespaced_persistent_volume_claim(namespace).items
        if namespace
        else v1.list_persistent_volume_claim_for_all_namespaces().items
    )
    unbound_pvcs = [
        (p.metadata.namespace, p.metadata.name, p.status.phase)
        for p in pvcs
        if p.status.phase != "Bound"
    ]

    if unbound_pvcs:
        fail_table = Table(
            title="Unbound/Failing PVCs", show_header=True, header_style="bold magenta"
        )
        fail_table.add_column("Namespace", style="cyan")
        fail_table.add_column("PVC Name", style="blue")
        fail_table.add_column("Status", style="red")
        for p in unbound_pvcs:
            fail_table.add_row(p[0], p[1], p[2])
        console.print(fail_table)
        print_tip(
            "Unbound PVCs indicate the StorageClass provisioner failed, the requested size is unavailable, or it is waiting for first consumer.",
            f"kubectl describe pvc {unbound_pvcs[0][1]} -n {unbound_pvcs[0][0]}",
        )
    else:
        console.print("[green]✓ All PersistentVolumeClaims are Bound[/green]")

    if pvcs:
        pvc_table = Table(
            title="All PVCs", show_header=True, header_style="bold magenta"
        )
        pvc_table.add_column("Namespace", style="cyan")
        pvc_table.add_column("PVC Name", style="blue")
        pvc_table.add_column("Status")
        pvc_table.add_column("Storage Class", style="dim")
        for p in pvcs:
            color = "green" if p.status.phase == "Bound" else "red"
            pvc_table.add_row(
                p.metadata.namespace,
                p.metadata.name,
                f"[{color}]{p.status.phase}[/{color}]",
                p.spec.storage_class_name or "—",
            )
        console.print(pvc_table)

    # Check 3: Check for Degraded Workloads (Deployments, StatefulSets, DaemonSets)
    apps_v1 = client.AppsV1Api()
    degraded = False
    first_degraded = None

    deps = (
        apps_v1.list_namespaced_deployment(namespace).items
        if namespace
        else apps_v1.list_deployment_for_all_namespaces().items
    )
    sts = (
        apps_v1.list_namespaced_stateful_set(namespace).items
        if namespace
        else apps_v1.list_stateful_set_for_all_namespaces().items
    )

    fail_table = Table(
        title="Degraded Workloads", show_header=True, header_style="bold magenta"
    )
    fail_table.add_column("Type", style="yellow")
    fail_table.add_column("Namespace", style="cyan")
    fail_table.add_column("Name", style="blue")
    fail_table.add_column("Ready/Desired", style="red")

    for d in deps:
        if d.spec.replicas and (d.status.ready_replicas or 0) != d.spec.replicas:
            fail_table.add_row(
                "Deployment",
                d.metadata.namespace,
                d.metadata.name,
                f"{d.status.ready_replicas or 0}/{d.spec.replicas}",
            )
            if not first_degraded:
                first_degraded = ("deployment", d.metadata.name, d.metadata.namespace)
            degraded = True
    for s in sts:
        if s.spec.replicas and (s.status.ready_replicas or 0) != s.spec.replicas:
            fail_table.add_row(
                "StatefulSet",
                s.metadata.namespace,
                s.metadata.name,
                f"{s.status.ready_replicas or 0}/{s.spec.replicas}",
            )
            if not first_degraded:
                first_degraded = ("statefulset", s.metadata.name, s.metadata.namespace)
            degraded = True

    if degraded:
        console.print(fail_table)
    if degraded and first_degraded:
        kind, name, ns = first_degraded
        print_tip(
            "Workloads missing replicas usually suffer from inadequate node capacity, persistent volume locks, or image pull errors.",
            f"kubectl describe {kind} {name} -n {ns}",
        )
    else:
        console.print(
            "[green]✓ All Workloads (Deployments/StatefulSets) have desired replicas ready[/green]"
        )

    all_workloads = Table(
        title="All Workloads", show_header=True, header_style="bold magenta"
    )
    all_workloads.add_column("Type", style="yellow")
    all_workloads.add_column("Namespace", style="cyan")
    all_workloads.add_column("Name", style="blue")
    all_workloads.add_column("Ready/Desired")
    for d in deps:
        desired = d.spec.replicas or 0
        ready = d.status.ready_replicas or 0
        color = "green" if ready == desired else "red"
        all_workloads.add_row(
            "Deployment",
            d.metadata.namespace,
            d.metadata.name,
            f"[{color}]{ready}/{desired}[/{color}]",
        )
    for s in sts:
        desired = s.spec.replicas or 0
        ready = s.status.ready_replicas or 0
        color = "green" if ready == desired else "red"
        all_workloads.add_row(
            "StatefulSet",
            s.metadata.namespace,
            s.metadata.name,
            f"[{color}]{ready}/{desired}[/{color}]",
        )
    if deps or sts:
        console.print(all_workloads)

    # Check 4: The Ultimate Catch-All -> K8s Warning Events in the last 15m
    console.print(
        "\n[bold yellow]Gathering Recent Warnings (All Object Types)...[/bold yellow]"
    )
    cmd = [
        "kubectl",
        "get",
        "events",
        "--field-selector",
        "type=Warning",
        "--sort-by=.metadata.creationTimestamp",
        "-o",
        "json",
    ]
    if namespace:
        cmd.extend(["-n", namespace])
    else:
        cmd.append("-A")
    events_out = run_cmd(cmd)
    if events_out:
        try:
            events_data = json.loads(events_out)
            items = events_data.get("items", [])

            if items:
                table = Table(
                    title="Recent Warnings (Last 50 recorded globally)",
                    show_header=True,
                    header_style="bold magenta",
                )
                table.add_column("Namespace", style="cyan")
                table.add_column("Object", style="blue")
                table.add_column("Reason", style="yellow")
                table.add_column("Message", style="white")

                # Show last 50 only to prevent spam
                for e in items[-50:]:
                    obj = f"{e.get('involvedObject', {}).get('kind', 'Unknown')}/{e.get('involvedObject', {}).get('name', 'Unknown')}"
                    table.add_row(
                        e.get("involvedObject", {}).get("namespace", "cluster"),
                        obj,
                        e.get("reason", ""),
                        (lambda m: m[:100] + "..." if len(m) > 100 else m)(
                            e.get("message", "")
                        ),
                    )
                console.print(table)
                _suggest_from_events(items)
            else:
                console.print(
                    "[green]✓ No Warning events discovered in the cluster.[/green]"
                )
        except json.JSONDecodeError:
            console.print(
                "[yellow]Warning: could not parse events response as JSON[/yellow]"
            )


_EVENT_TIPS = {
    # reason → (tip text, command template)
    # {kind}, {name}, {ns} are substituted at runtime
    "BackOff": (
        "A container is repeatedly crashing and backing off restarts.",
        "kubectl logs {name} -n {ns}",
    ),
    "OOMKilling": (
        "A container was killed due to out-of-memory. Consider raising its memory limit.",
        "kubectl describe pod {name} -n {ns}",
    ),
    "Unhealthy": (
        "A liveness or readiness probe is failing. The pod may be starting slowly or misconfigured.",
        "kubectl logs {name} -n {ns}",
    ),
    "FailedScheduling": (
        "Pod could not be scheduled — check node capacity, taints, tolerations, or affinity rules.",
        "kubectl describe pod {name} -n {ns}",
    ),
    "FailedMount": (
        "A volume failed to mount. The PVC may be unbound or the node may lack access to the storage backend.",
        "kubectl describe pod {name} -n {ns}",
    ),
    "FailedAttachVolume": (
        "Volume attachment failed. Check PVC/PV binding and StorageClass provisioner health.",
        "kubectl describe pod {name} -n {ns}",
    ),
    "FailedBinding": (
        "PVC could not bind to a PV. Check StorageClass, access modes, and available capacity.",
        "kubectl describe pvc {name} -n {ns}",
    ),
    "Evicted": (
        "Pod was evicted, likely due to node memory or disk pressure.",
        "kubectl describe pod {name} -n {ns}",
    ),
    "NodeNotReady": (
        "A node went NotReady. Check kubelet status and node resource pressure.",
        "kubectl describe node {name}",
    ),
    "Failed": (
        "A resource operation failed. Inspect the object for misconfiguration or missing dependencies.",
        "kubectl describe {kind_lower} {name} -n {ns}",
    ),
}

_LOG_REASONS = {"BackOff", "Unhealthy"}


def _suggest_from_events(items: list):
    """Emit targeted print_tip suggestions based on the most actionable warning events."""
    seen: set[tuple] = set()
    suggestions = 0

    for e in reversed(items):
        if suggestions >= 5:
            break

        reason = e.get("reason", "")
        obj = e.get("involvedObject", {})
        kind = obj.get("kind", "")
        name = obj.get("name", "")
        ns = obj.get("namespace", "")

        tip_cfg = _EVENT_TIPS.get(reason)
        if not tip_cfg:
            continue

        dedup_key = (reason, kind, name, ns)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        tip_text, cmd_tpl = tip_cfg
        cmd = cmd_tpl.format(kind_lower=kind.lower(), name=name, ns=ns)
        print_tip(f"[{reason}] {tip_text}", cmd)
        suggestions += 1


def describe_object(kind: str, name: str, namespace: str = None):
    """Fetch and print the describe output of any k8s object."""
    cmd = ["kubectl", "describe", kind, name]
    if namespace:
        cmd.extend(["-n", namespace])

    console.print(f"[bold blue]Executing:[/bold blue] {' '.join(cmd)}")
    out = run_cmd(cmd)
    if out:
        from rich.syntax import Syntax

        # Since it's unstructured text, printing it as raw string or YAML-like syntax works well.
        console.print(Syntax(out, "yaml", theme="monokai", word_wrap=True))


def check_logs(
    name: str, namespace: str = None, previous: bool = False, tail: int = 100
):
    """Fetch and print logs for a pod or deployment."""
    cmd = ["kubectl", "logs", name, f"--tail={tail}"]
    if namespace:
        cmd.extend(["-n", namespace])
    if previous:
        cmd.append("--previous")

    console.print(f"[bold blue]Executing:[/bold blue] {' '.join(cmd)}")
    out = run_cmd(cmd)
    if out:
        console.print(out)
