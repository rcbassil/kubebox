from kubernetes import client, config
from rich.tree import Tree
from rich.panel import Panel
from core.utils import console

_OK = "[green]✓[/green]"
_FAIL = "[red]✗[/red]"
_WARN = "[yellow]⚠[/yellow]"
_UNK = "[dim]?[/dim]"

_SUPPORTED_KINDS = [
    "pod",
    "deployment",
    "statefulset",
    "daemonset",
    "service",
    "ingress",
    "pvc",
    "persistentvolumeclaim",
]


def _init() -> bool:
    try:
        config.load_kube_config()
        return True
    except Exception as e:
        console.print(f"[bold red]Failed to load kube config:[/bold red] {e}")
        return False


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def _pod_icon_label(pod) -> tuple[str, str]:
    phase = pod.status.phase or "Unknown"
    for cs in pod.status.container_statuses or []:
        if cs.state.waiting and cs.state.waiting.reason in (
            "CrashLoopBackOff",
            "ErrImagePull",
            "ImagePullBackOff",
            "CreateContainerConfigError",
        ):
            return _FAIL, cs.state.waiting.reason
    if phase in ("Running", "Succeeded"):
        ready = all(cs.ready for cs in pod.status.container_statuses or [])
        return (_OK, phase) if ready else (_WARN, f"{phase} — not all containers ready")
    return _FAIL, phase


def _rs_icon_label(rs) -> tuple[str, str]:
    desired = rs.spec.replicas or 0
    ready = rs.status.ready_replicas or 0
    if desired == 0:
        return _WARN, "scaled to 0"
    return (
        (_OK, f"{ready}/{desired} ready")
        if ready == desired
        else (_FAIL, f"{ready}/{desired} ready")
    )


def _dep_icon_label(dep) -> tuple[str, str]:
    desired = dep.spec.replicas or 0
    ready = dep.status.ready_replicas or 0
    return (
        (_OK, f"{ready}/{desired} ready")
        if ready == desired
        else (_FAIL, f"{ready}/{desired} ready")
    )


def _sts_icon_label(sts) -> tuple[str, str]:
    desired = sts.spec.replicas or 0
    ready = sts.status.ready_replicas or 0
    return (
        (_OK, f"{ready}/{desired} ready")
        if ready == desired
        else (_FAIL, f"{ready}/{desired} ready")
    )


# ---------------------------------------------------------------------------
# Event helper — attaches the last N warning events to a tree node
# ---------------------------------------------------------------------------


def _attach_events(branch: Tree, name: str, kind: str, ns: str, v1, limit: int = 4):
    try:
        evts = v1.list_namespaced_event(
            ns,
            field_selector=f"involvedObject.name={name},involvedObject.kind={kind},type=Warning",
        )
        warnings = evts.items[-limit:]
        if warnings:
            evts_branch = branch.add("[yellow]Warning Events[/yellow]")
            for e in warnings:
                evts_branch.add(f"[yellow]{e.reason}[/yellow]: [dim]{e.message}[/dim]")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pod subtree — containers + events
# ---------------------------------------------------------------------------


def _pod_subtree(pod, ns: str, v1) -> Tree:
    icon, label = _pod_icon_label(pod)
    name = pod.metadata.name
    node = Tree(
        f"{icon} [magenta]Pod[/magenta]/[blue]{name}[/blue] [dim]({label})[/dim]"
    )

    for cs in pod.status.container_statuses or []:
        restarts = cs.restart_count
        if cs.ready:
            c_icon, c_label = _OK, "Ready"
        elif cs.state.waiting:
            c_icon, c_label = _FAIL, cs.state.waiting.reason or "Waiting"
        elif cs.state.terminated:
            code = cs.state.terminated.exit_code
            c_icon = _OK if code == 0 else _FAIL
            c_label = f"Terminated (exit {code})"
        else:
            c_icon, c_label = _UNK, "Unknown"
        node.add(
            f"{c_icon} [cyan]container[/cyan]/{cs.name} — {c_label} "
            f"[dim](restarts: {restarts})[/dim]"
        )

    _attach_events(node, name, "Pod", ns, v1)
    return node


# ---------------------------------------------------------------------------
# ReplicaSet subtree — pods inside
# ---------------------------------------------------------------------------


def _rs_subtree(rs, ns: str, v1, apps) -> Tree:
    icon, label = _rs_icon_label(rs)
    name = rs.metadata.name
    node = Tree(
        f"{icon} [yellow]ReplicaSet[/yellow]/[blue]{name}[/blue] [dim]({label})[/dim]"
    )

    selector = ",".join(
        f"{k}={v}" for k, v in (rs.spec.selector.match_labels or {}).items()
    )
    try:
        pods = v1.list_namespaced_pod(ns, label_selector=selector).items
        for pod in pods:
            node.add_renderable(_pod_subtree(pod, ns, v1))
    except Exception:
        node.add("[dim]could not list pods[/dim]")

    _attach_events(node, name, "ReplicaSet", ns, v1)
    return node


# ---------------------------------------------------------------------------
# Service → Endpoints → Pods subtree
# ---------------------------------------------------------------------------


def _svc_subtree(svc, ns: str, v1) -> Tree:
    name = svc.metadata.name
    svc_type = svc.spec.type or "ClusterIP"
    try:
        ep = v1.read_namespaced_endpoints(name, ns)
        ready_count = sum(len(s.addresses or []) for s in ep.subsets or [])
        not_ready = sum(len(s.not_ready_addresses or []) for s in ep.subsets or [])
    except Exception:
        ready_count, not_ready = 0, 0

    icon = _OK if ready_count > 0 else _WARN
    node = Tree(
        f"{icon} [green]Service[/green]/[blue]{name}[/blue] "
        f"[dim]({svc_type}, {ready_count} ready / {not_ready} not-ready endpoints)[/dim]"
    )

    # Show pods backing this service
    selector = ",".join(f"{k}={v}" for k, v in (svc.spec.selector or {}).items())
    if selector:
        try:
            pods = v1.list_namespaced_pod(ns, label_selector=selector).items
            for pod in pods:
                node.add_renderable(_pod_subtree(pod, ns, v1))
        except Exception:
            pass

    return node


# ---------------------------------------------------------------------------
# Public trace entry points
# ---------------------------------------------------------------------------


def trace_pod(name: str, ns: str):
    v1 = client.CoreV1Api()
    apps = client.AppsV1Api()
    try:
        pod = v1.read_namespaced_pod(name, ns)
    except Exception as e:
        console.print(f"[red]Pod '{name}' not found in '{ns}': {e}[/red]")
        return

    icon, label = _pod_icon_label(pod)
    root = Tree(
        f"{icon} [magenta]Pod[/magenta]/[blue]{name}[/blue] [dim]({label})[/dim]"
    )

    # Walk owner chain upward
    for ref in pod.metadata.owner_references or []:
        owner_node = root.add(
            f"[dim]↑ owned by[/dim] [yellow]{ref.kind}[/yellow]/[blue]{ref.name}[/blue]"
        )
        if ref.kind == "ReplicaSet":
            try:
                rs = apps.read_namespaced_replica_set(ref.name, ns)
                for dep_ref in rs.metadata.owner_references or []:
                    owner_node.add(
                        f"[dim]↑ owned by[/dim] [yellow]{dep_ref.kind}[/yellow]/[blue]{dep_ref.name}[/blue]"
                    )
            except Exception:
                pass

    # Containers
    containers = root.add("[bold]Containers[/bold]")
    for cs in pod.status.container_statuses or []:
        restarts = cs.restart_count
        if cs.ready:
            c_icon, c_label = _OK, "Ready"
        elif cs.state.waiting:
            c_icon, c_label = _FAIL, cs.state.waiting.reason or "Waiting"
        elif cs.state.terminated:
            code = cs.state.terminated.exit_code
            c_icon = _OK if code == 0 else _FAIL
            c_label = f"Terminated (exit {code})"
        else:
            c_icon, c_label = _UNK, "Unknown"
        containers.add(
            f"{c_icon} [cyan]{cs.name}[/cyan] — {c_label} [dim](restarts: {restarts})[/dim]"
        )

    _attach_events(root, name, "Pod", ns, v1)
    console.print(Panel(root, border_style="blue", title=f"Object Trace — Pod/{name}"))


def trace_deployment(name: str, ns: str):
    v1 = client.CoreV1Api()
    apps = client.AppsV1Api()
    try:
        dep = apps.read_namespaced_deployment(name, ns)
    except Exception as e:
        console.print(f"[red]Deployment '{name}' not found in '{ns}': {e}[/red]")
        return

    icon, label = _dep_icon_label(dep)
    root = Tree(
        f"{icon} [bold]Deployment[/bold]/[blue]{name}[/blue] [dim]({label})[/dim]"
    )

    # ReplicaSets owned by this Deployment
    try:
        all_rs = apps.list_namespaced_replica_set(ns).items
        owned = [
            r
            for r in all_rs
            if any(
                ref.name == name and ref.kind == "Deployment"
                for ref in r.metadata.owner_references or []
            )
        ]
        owned.sort(key=lambda r: -(r.spec.replicas or 0))

        for rs in owned:
            root.add_renderable(_rs_subtree(rs, ns, v1, apps))
    except Exception as e:
        root.add(f"[red]Error fetching ReplicaSets: {e}[/red]")

    # Services that select this Deployment's pods
    dep_labels = dep.spec.selector.match_labels or {}
    try:
        svcs = v1.list_namespaced_service(ns).items
        for svc in svcs:
            sel = svc.spec.selector or {}
            if sel and all(dep_labels.get(k) == v for k, v in sel.items()):
                root.add_renderable(_svc_subtree(svc, ns, v1))
    except Exception:
        pass

    _attach_events(root, name, "Deployment", ns, v1)
    console.print(
        Panel(root, border_style="blue", title=f"Object Trace — Deployment/{name}")
    )


def trace_statefulset(name: str, ns: str):
    v1 = client.CoreV1Api()
    apps = client.AppsV1Api()
    try:
        sts = apps.read_namespaced_stateful_set(name, ns)
    except Exception as e:
        console.print(f"[red]StatefulSet '{name}' not found in '{ns}': {e}[/red]")
        return

    icon, label = _sts_icon_label(sts)
    root = Tree(
        f"{icon} [bold]StatefulSet[/bold]/[blue]{name}[/blue] [dim]({label})[/dim]"
    )

    selector = ",".join(
        f"{k}={v}" for k, v in (sts.spec.selector.match_labels or {}).items()
    )
    try:
        pods = v1.list_namespaced_pod(ns, label_selector=selector).items
        for pod in pods:
            root.add_renderable(_pod_subtree(pod, ns, v1))
    except Exception as e:
        root.add(f"[red]Error fetching pods: {e}[/red]")

    _attach_events(root, name, "StatefulSet", ns, v1)
    console.print(
        Panel(root, border_style="blue", title=f"Object Trace — StatefulSet/{name}")
    )


def trace_daemonset(name: str, ns: str):
    v1 = client.CoreV1Api()
    apps = client.AppsV1Api()
    try:
        ds = apps.read_namespaced_daemon_set(name, ns)
    except Exception as e:
        console.print(f"[red]DaemonSet '{name}' not found in '{ns}': {e}[/red]")
        return

    desired = ds.status.desired_number_scheduled or 0
    ready = ds.status.number_ready or 0
    icon = _OK if ready == desired else _FAIL
    root = Tree(
        f"{icon} [bold]DaemonSet[/bold]/[blue]{name}[/blue] [dim]({ready}/{desired} ready)[/dim]"
    )

    selector = ",".join(
        f"{k}={v}" for k, v in (ds.spec.selector.match_labels or {}).items()
    )
    try:
        pods = v1.list_namespaced_pod(ns, label_selector=selector).items
        for pod in pods:
            root.add_renderable(_pod_subtree(pod, ns, v1))
    except Exception as e:
        root.add(f"[red]Error fetching pods: {e}[/red]")

    _attach_events(root, name, "DaemonSet", ns, v1)
    console.print(
        Panel(root, border_style="blue", title=f"Object Trace — DaemonSet/{name}")
    )


def trace_service(name: str, ns: str):
    v1 = client.CoreV1Api()
    try:
        svc = v1.read_namespaced_service(name, ns)
    except Exception as e:
        console.print(f"[red]Service '{name}' not found in '{ns}': {e}[/red]")
        return

    root = Tree("")
    root.add_renderable(_svc_subtree(svc, ns, v1))
    console.print(
        Panel(root, border_style="blue", title=f"Object Trace — Service/{name}")
    )


def trace_ingress(name: str, ns: str):
    v1 = client.CoreV1Api()
    net = client.NetworkingV1Api()
    try:
        ing = net.read_namespaced_ingress(name, ns)
    except Exception as e:
        console.print(f"[red]Ingress '{name}' not found in '{ns}': {e}[/red]")
        return

    root = Tree(f"[bold]Ingress[/bold]/[blue]{name}[/blue]")

    for rule in ing.spec.rules or []:
        host_node = root.add(f"[cyan]host:[/cyan] {rule.host or '*'}")
        for path in rule.http.paths if rule.http else []:
            svc_name = (
                path.backend.service.name
                if path.backend and path.backend.service
                else None
            )
            path_str = path.path or "/"
            if svc_name:
                path_node = host_node.add(
                    f"[dim]{path_str}[/dim] → [green]Service[/green]/[blue]{svc_name}[/blue]"
                )
                try:
                    svc = v1.read_namespaced_service(svc_name, ns)
                    path_node.add_renderable(_svc_subtree(svc, ns, v1))
                except Exception:
                    path_node.add(f"[red]Service '{svc_name}' not found[/red]")

    console.print(
        Panel(root, border_style="blue", title=f"Object Trace — Ingress/{name}")
    )


def trace_pvc(name: str, ns: str):
    v1 = client.CoreV1Api()
    try:
        pvc = v1.read_namespaced_persistent_volume_claim(name, ns)
    except Exception as e:
        console.print(f"[red]PVC '{name}' not found in '{ns}': {e}[/red]")
        return

    phase = pvc.status.phase or "Unknown"
    pv_name = pvc.spec.volume_name or "—"
    sc = pvc.spec.storage_class_name or "—"
    capacity = (pvc.status.capacity or {}).get("storage", "—")
    icon = _OK if phase == "Bound" else _FAIL

    root = Tree(
        f"{icon} [bold]PVC[/bold]/[blue]{name}[/blue] [dim]({phase}, {capacity})[/dim]"
    )
    root.add(f"[dim]StorageClass:[/dim] {sc}")

    if pv_name != "—":
        try:
            pv = v1.read_persistent_volume(pv_name)
            pv_phase = pv.status.phase or "Unknown"
            pv_icon = _OK if pv_phase == "Bound" else _FAIL
            pv_node = root.add(
                f"{pv_icon} [yellow]PersistentVolume[/yellow]/[blue]{pv_name}[/blue] [dim]({pv_phase})[/dim]"
            )
            reclaim = pv.spec.persistent_volume_reclaim_policy or "—"
            pv_node.add(f"[dim]Reclaim Policy:[/dim] {reclaim}")
        except Exception:
            root.add(
                f"[yellow]PersistentVolume[/yellow]/[blue]{pv_name}[/blue] [dim](could not fetch)[/dim]"
            )
    else:
        root.add("[red]No PersistentVolume bound yet[/red]")

    _attach_events(root, name, "PersistentVolumeClaim", ns, v1)
    console.print(Panel(root, border_style="blue", title=f"Object Trace — PVC/{name}"))


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------


def trace_object(kind: str, name: str, namespace: str = None):
    if not _init():
        return

    ns = namespace or "default"
    k = kind.lower()

    dispatch = {
        "pod": trace_pod,
        "deployment": trace_deployment,
        "statefulset": trace_statefulset,
        "daemonset": trace_daemonset,
        "service": trace_service,
        "svc": trace_service,
        "ingress": trace_ingress,
        "ing": trace_ingress,
        "pvc": trace_pvc,
        "persistentvolumeclaim": trace_pvc,
    }

    fn = dispatch.get(k)
    if not fn:
        supported = ", ".join(sorted(set(dispatch.keys())))
        console.print(
            f"[yellow]Trace not supported for kind '[bold]{kind}[/bold]'.[/yellow]"
        )
        console.print(f"[dim]Supported: {supported}[/dim]")
        return

    fn(name, ns)
