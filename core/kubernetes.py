from kubernetes import client, config
from core.utils import console, run_cmd, print_tip, fmt_age
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
    dss = (
        apps_v1.list_namespaced_daemon_set(namespace).items
        if namespace
        else apps_v1.list_daemon_set_for_all_namespaces().items
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
    for ds in dss:
        desired = ds.status.desired_number_scheduled or 0
        ready = ds.status.number_ready or 0
        if desired and ready != desired:
            fail_table.add_row(
                "DaemonSet",
                ds.metadata.namespace,
                ds.metadata.name,
                f"{ready}/{desired}",
            )
            if not first_degraded:
                first_degraded = ("daemonset", ds.metadata.name, ds.metadata.namespace)
            degraded = True

    if degraded:
        console.print(fail_table)
        if first_degraded:
            kind, name, ns = first_degraded
            print_tip(
                "Workloads missing replicas usually suffer from inadequate node capacity, persistent volume locks, or image pull errors.",
                f"kubectl describe {kind} {name} -n {ns}",
            )
    else:
        console.print(
            "[green]✓ All Workloads (Deployments/StatefulSets/DaemonSets) have desired replicas ready[/green]"
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
    for ds in dss:
        desired = ds.status.desired_number_scheduled or 0
        ready = ds.status.number_ready or 0
        color = "green" if ready == desired else "red"
        all_workloads.add_row(
            "DaemonSet",
            ds.metadata.namespace,
            ds.metadata.name,
            f"[{color}]{ready}/{desired}[/{color}]",
        )
    if deps or sts or dss:
        console.print(all_workloads)

    # Check 4: Services
    _check_services(v1, namespace)

    # Check 5: Ingresses
    _check_ingresses(namespace)

    # Check 6: Jobs & CronJobs
    _check_jobs(namespace)

    # Check 7: HPAs
    _check_hpas(namespace)

    # Check 8: PersistentVolumes (global only)
    if not namespace:
        _check_persistent_volumes(v1)

    # Check 9: Namespaces (global only)
    if not namespace:
        _check_namespaces(v1)

    # Check 10: ConfigMaps
    _check_configmaps(v1, namespace)

    # Check 11: Secrets (names and types only — values never shown)
    _check_secrets(v1, namespace)

    # Check 7: The Ultimate Catch-All -> K8s Warning Events in the last 15m
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
                table.add_column("Last Seen", style="dim", no_wrap=True)
                table.add_column("Namespace", style="cyan")
                table.add_column("Object", style="blue")
                table.add_column("Reason", style="yellow")
                table.add_column("Message", style="white")

                # Show last 50 only to prevent spam
                for e in items[-50:]:
                    obj = f"{e.get('involvedObject', {}).get('kind', 'Unknown')}/{e.get('involvedObject', {}).get('name', 'Unknown')}"
                    age = fmt_age(
                        e.get("lastTimestamp")
                        or e.get("metadata", {}).get("creationTimestamp")
                    )
                    table.add_row(
                        age,
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


_SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


def _check_services(v1, namespace: str = None):
    console.print("\n[bold blue]Checking Services...[/bold blue]")
    try:
        svcs = (
            v1.list_namespaced_service(namespace).items
            if namespace
            else v1.list_service_for_all_namespaces().items
        )
        eps_map: dict[tuple, int] = {}
        try:
            eps = (
                v1.list_namespaced_endpoints(namespace).items
                if namespace
                else v1.list_endpoints_for_all_namespaces().items
            )
            for ep in eps:
                ready = sum(len(s.addresses or []) for s in ep.subsets or [])
                eps_map[(ep.metadata.namespace, ep.metadata.name)] = ready
        except Exception:
            pass

        rows = []
        no_endpoint_svcs = []
        for svc in svcs:
            ns = svc.metadata.namespace
            name = svc.metadata.name
            svc_type = svc.spec.type or "ClusterIP"
            ports = ", ".join(f"{p.port}/{p.protocol}" for p in (svc.spec.ports or []))
            has_selector = bool(svc.spec.selector)
            ready = eps_map.get((ns, name), 0)
            no_eps = has_selector and ready == 0 and svc_type != "ExternalName"
            if no_eps:
                no_endpoint_svcs.append((ns, name))
            rows.append((ns, name, svc_type, ports, ready, no_eps))

        if no_endpoint_svcs:
            fail_table = Table(
                title="Services with No Ready Endpoints",
                show_header=True,
                header_style="bold magenta",
            )
            fail_table.add_column("Namespace", style="cyan")
            fail_table.add_column("Name", style="blue")
            fail_table.add_column("Type", style="yellow")
            fail_table.add_column("Ports", style="dim")
            for ns, name, svc_type, ports, _, no_eps in rows:
                if no_eps:
                    fail_table.add_row(ns, name, svc_type, ports)
            console.print(fail_table)
            ns0, name0 = no_endpoint_svcs[0]
            print_tip(
                "A Service with no ready endpoints means its selector matches no Running pods. Check pod labels or deployment health.",
                f"kubectl describe service {name0} -n {ns0}",
            )
        else:
            console.print("[green]✓ All Services have ready endpoints[/green]")

        all_table = Table(
            title="All Services", show_header=True, header_style="bold magenta"
        )
        all_table.add_column("Namespace", style="cyan")
        all_table.add_column("Name", style="blue")
        all_table.add_column("Type", style="yellow")
        all_table.add_column("Ports", style="dim")
        all_table.add_column("Ready Endpoints", justify="right")
        for ns, name, svc_type, ports, ready, no_eps in rows:
            color = "red" if no_eps else "green"
            all_table.add_row(ns, name, svc_type, ports, f"[{color}]{ready}[/{color}]")
        if rows:
            console.print(all_table)
    except Exception as e:
        console.print(f"[bold red]Error checking Services:[/bold red] {e}")


def _check_configmaps(v1, namespace: str = None):
    console.print("\n[bold blue]ConfigMaps...[/bold blue]")
    try:
        cms = (
            v1.list_namespaced_config_map(namespace).items
            if namespace
            else v1.list_config_map_for_all_namespaces().items
        )
        if not namespace:
            cms = [c for c in cms if c.metadata.namespace not in _SYSTEM_NAMESPACES]

        if not cms:
            console.print("[dim]No ConfigMaps found.[/dim]")
            return

        table = Table(title="ConfigMaps", show_header=True, header_style="bold magenta")
        table.add_column("Namespace", style="cyan")
        table.add_column("Name", style="blue")
        table.add_column("Keys", justify="right", style="dim")
        for cm in cms:
            keys = len(cm.data or {}) + len(cm.binary_data or {})
            table.add_row(cm.metadata.namespace, cm.metadata.name, str(keys))
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error checking ConfigMaps:[/bold red] {e}")


def _check_secrets(v1, namespace: str = None):
    console.print(
        "\n[bold blue]Secrets (names and types only — values never shown)...[/bold blue]"
    )
    try:
        secrets = (
            v1.list_namespaced_secret(namespace).items
            if namespace
            else v1.list_secret_for_all_namespaces().items
        )
        if not namespace:
            secrets = [
                s for s in secrets if s.metadata.namespace not in _SYSTEM_NAMESPACES
            ]

        if not secrets:
            console.print("[dim]No Secrets found.[/dim]")
            return

        table = Table(
            title="Secrets — names and types only",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Namespace", style="cyan")
        table.add_column("Name", style="blue")
        table.add_column("Type", style="yellow")
        table.add_column("Keys", justify="right", style="dim")
        for secret in secrets:
            keys = len(secret.data or {})
            table.add_row(
                secret.metadata.namespace,
                secret.metadata.name,
                secret.type or "Opaque",
                str(keys),
            )
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error checking Secrets:[/bold red] {e}")


def _check_ingresses(namespace: str = None):
    console.print("\n[bold blue]Checking Ingresses...[/bold blue]")
    try:
        networking_v1 = client.NetworkingV1Api()
        ingresses = (
            networking_v1.list_namespaced_ingress(namespace).items
            if namespace
            else networking_v1.list_ingress_for_all_namespaces().items
        )
        if not ingresses:
            console.print("[dim]No Ingresses found.[/dim]")
            return
        table = Table(title="Ingresses", show_header=True, header_style="bold magenta")
        table.add_column("Namespace", style="cyan")
        table.add_column("Name", style="blue")
        table.add_column("Class", style="yellow")
        table.add_column("Hosts", style="dim")
        table.add_column("Address", style="green")
        for ing in ingresses:
            hosts = (
                ", ".join(r.host or "*" for r in (ing.spec.rules or []) if r.host)
                or "*"
            )
            lb = ing.status.load_balancer
            address = ""
            if lb and lb.ingress:
                address = lb.ingress[0].ip or lb.ingress[0].hostname or ""
            ing_class = ing.spec.ingress_class_name or (
                (ing.metadata.annotations or {}).get("kubernetes.io/ingress.class", "—")
            )
            table.add_row(
                ing.metadata.namespace,
                ing.metadata.name,
                ing_class,
                hosts,
                address or "[yellow]pending[/yellow]",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error checking Ingresses:[/bold red] {e}")


def _check_jobs(namespace: str = None):
    console.print("\n[bold blue]Checking Jobs & CronJobs...[/bold blue]")
    try:
        batch_v1 = client.BatchV1Api()
        jobs = (
            batch_v1.list_namespaced_job(namespace).items
            if namespace
            else batch_v1.list_job_for_all_namespaces().items
        )
        cronjobs = (
            batch_v1.list_namespaced_cron_job(namespace).items
            if namespace
            else batch_v1.list_cron_job_for_all_namespaces().items
        )

        if jobs:
            failed_jobs = [
                j
                for j in jobs
                if (j.status.failed or 0) > 0 and not j.status.completion_time
            ]
            if failed_jobs:
                fail_table = Table(
                    title="Failed Jobs", show_header=True, header_style="bold magenta"
                )
                fail_table.add_column("Namespace", style="cyan")
                fail_table.add_column("Name", style="blue")
                fail_table.add_column("Failed", style="red", justify="right")
                for j in failed_jobs:
                    fail_table.add_row(
                        j.metadata.namespace, j.metadata.name, str(j.status.failed or 0)
                    )
                console.print(fail_table)
                print_tip(
                    "Jobs with failures may be due to application errors, missing config, or resource limits.",
                    f"kubectl describe job {failed_jobs[0].metadata.name} -n {failed_jobs[0].metadata.namespace}",
                )
            else:
                console.print("[green]✓ No actively failing Jobs[/green]")

            job_table = Table(
                title="Jobs", show_header=True, header_style="bold magenta"
            )
            job_table.add_column("Namespace", style="cyan")
            job_table.add_column("Name", style="blue")
            job_table.add_column("Status")
            job_table.add_column("Succeeded", justify="right", style="green")
            job_table.add_column("Failed", justify="right", style="red")
            for j in jobs:
                if j.status.completion_time:
                    status = "[green]Complete[/green]"
                elif (j.status.failed or 0) > 0:
                    status = "[red]Failed[/red]"
                else:
                    status = "[yellow]Running[/yellow]"
                job_table.add_row(
                    j.metadata.namespace,
                    j.metadata.name,
                    status,
                    str(j.status.succeeded or 0),
                    str(j.status.failed or 0),
                )
            console.print(job_table)

        if cronjobs:
            cj_table = Table(
                title="CronJobs", show_header=True, header_style="bold magenta"
            )
            cj_table.add_column("Namespace", style="cyan")
            cj_table.add_column("Name", style="blue")
            cj_table.add_column("Schedule", style="yellow")
            cj_table.add_column("Suspended", justify="center")
            cj_table.add_column("Active", justify="right")
            cj_table.add_column("Last Schedule", style="dim")
            for cj in cronjobs:
                suspended = "[red]Yes[/red]" if cj.spec.suspend else "[green]No[/green]"
                active = str(len(cj.status.active or []))
                last = (
                    fmt_age(
                        cj.status.last_schedule_time.isoformat()
                        if cj.status.last_schedule_time
                        else None
                    )
                    if cj.status.last_schedule_time
                    else "Never"
                )
                cj_table.add_row(
                    cj.metadata.namespace,
                    cj.metadata.name,
                    cj.spec.schedule,
                    suspended,
                    active,
                    last,
                )
            console.print(cj_table)

        if not jobs and not cronjobs:
            console.print("[dim]No Jobs or CronJobs found.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Error checking Jobs/CronJobs:[/bold red] {e}")


def _check_hpas(namespace: str = None):
    console.print("\n[bold blue]Checking HorizontalPodAutoscalers...[/bold blue]")
    try:
        autoscaling_v2 = client.AutoscalingV2Api()
        hpas = (
            autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(namespace).items
            if namespace
            else autoscaling_v2.list_horizontal_pod_autoscaler_for_all_namespaces().items
        )
        if not hpas:
            console.print("[dim]No HPAs found.[/dim]")
            return
        table = Table(title="HPAs", show_header=True, header_style="bold magenta")
        table.add_column("Namespace", style="cyan")
        table.add_column("Name", style="blue")
        table.add_column("Target", style="yellow")
        table.add_column("Min", justify="right", style="dim")
        table.add_column("Max", justify="right", style="dim")
        table.add_column("Current/Desired", justify="right")
        for hpa in hpas:
            current = hpa.status.current_replicas or 0
            desired = hpa.status.desired_replicas or 0
            color = "green" if current == desired else "yellow"
            ref = hpa.spec.scale_target_ref
            target = f"{ref.kind}/{ref.name}"
            table.add_row(
                hpa.metadata.namespace,
                hpa.metadata.name,
                target,
                str(hpa.spec.min_replicas or 1),
                str(hpa.spec.max_replicas),
                f"[{color}]{current}/{desired}[/{color}]",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error checking HPAs:[/bold red] {e}")


def _check_persistent_volumes(v1):
    console.print("\n[bold blue]Checking PersistentVolumes...[/bold blue]")
    try:
        pvs = v1.list_persistent_volume().items
        if not pvs:
            console.print("[dim]No PersistentVolumes found.[/dim]")
            return
        failed_pvs = [p for p in pvs if p.status.phase not in ("Bound", "Available")]
        if failed_pvs:
            fail_table = Table(
                title="Problem PersistentVolumes",
                show_header=True,
                header_style="bold magenta",
            )
            fail_table.add_column("Name", style="blue")
            fail_table.add_column("Status", style="red")
            fail_table.add_column("Reclaim Policy", style="dim")
            for p in failed_pvs:
                fail_table.add_row(
                    p.metadata.name,
                    p.status.phase or "Unknown",
                    p.spec.persistent_volume_reclaim_policy or "—",
                )
            console.print(fail_table)
            print_tip(
                "Released or Failed PVs may indicate orphaned storage or provisioner errors.",
                f"kubectl describe pv {failed_pvs[0].metadata.name}",
            )
        else:
            console.print(
                "[green]✓ All PersistentVolumes are Bound or Available[/green]"
            )

        table = Table(
            title="All PersistentVolumes", show_header=True, header_style="bold magenta"
        )
        table.add_column("Name", style="blue")
        table.add_column("Capacity", style="yellow")
        table.add_column("Access Modes", style="dim")
        table.add_column("Reclaim Policy", style="dim")
        table.add_column("Status")
        table.add_column("Claim", style="cyan")
        for p in pvs:
            color = "green" if p.status.phase in ("Bound", "Available") else "red"
            capacity = (p.spec.capacity or {}).get("storage", "—")
            modes = ", ".join(p.spec.access_modes or [])
            claim = ""
            if p.spec.claim_ref:
                claim = f"{p.spec.claim_ref.namespace}/{p.spec.claim_ref.name}"
            table.add_row(
                p.metadata.name,
                capacity,
                modes,
                p.spec.persistent_volume_reclaim_policy or "—",
                f"[{color}]{p.status.phase}[/{color}]",
                claim,
            )
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error checking PersistentVolumes:[/bold red] {e}")


def _check_namespaces(v1):
    console.print("\n[bold blue]Checking Namespaces...[/bold blue]")
    try:
        namespaces = v1.list_namespace().items
        if not namespaces:
            console.print("[dim]No Namespaces found.[/dim]")
            return
        terminating = [n for n in namespaces if n.status.phase == "Terminating"]
        if terminating:
            fail_table = Table(
                title="Stuck Terminating Namespaces",
                show_header=True,
                header_style="bold magenta",
            )
            fail_table.add_column("Name", style="blue")
            for n in terminating:
                fail_table.add_row(n.metadata.name)
            console.print(fail_table)
            print_tip(
                "A namespace stuck in Terminating usually has finalizers blocking deletion.",
                f"kubectl get namespace {terminating[0].metadata.name} -o json | jq '.spec.finalizers'",
            )
        else:
            console.print("[green]✓ All Namespaces are Active[/green]")

        table = Table(
            title="All Namespaces", show_header=True, header_style="bold magenta"
        )
        table.add_column("Name", style="blue")
        table.add_column("Status")
        table.add_column("Age", style="dim")
        for n in namespaces:
            color = "green" if n.status.phase == "Active" else "red"
            age = fmt_age(
                n.metadata.creation_timestamp.isoformat()
                if n.metadata.creation_timestamp
                else None
            )
            table.add_row(n.metadata.name, f"[{color}]{n.status.phase}[/{color}]", age)
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error checking Namespaces:[/bold red] {e}")


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
