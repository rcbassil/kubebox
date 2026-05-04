from __future__ import annotations

import os
import tempfile

import networkx as nx
from kubernetes import client

from core.kubernetes import init_k8s

_KIND_COLORS = {
    "Namespace": "#4a90d9",
    "Node": "#27ae60",
    "Pod": "#e67e22",
    "Deployment": "#8e44ad",
    "StatefulSet": "#9b59b6",
    "DaemonSet": "#6c3483",
    "Service": "#16a085",
    "Ingress": "#e84393",
    "PersistentVolumeClaim": "#f39c12",
    "PersistentVolume": "#d68910",
    "ConfigMap": "#7f8c8d",
    "Secret": "#566573",
}

_KIND_SIZES = {
    "Namespace": 30,
    "Node": 25,
    "Deployment": 20,
    "StatefulSet": 20,
    "DaemonSet": 20,
    "Service": 18,
    "Ingress": 18,
    "Pod": 14,
    "PersistentVolumeClaim": 14,
    "PersistentVolume": 14,
    "ConfigMap": 10,
    "Secret": 10,
}

_KIND_SHORT = {
    "Deployment": "Deploy",
    "StatefulSet": "STS",
    "DaemonSet": "DS",
    "PersistentVolumeClaim": "PVC",
    "PersistentVolume": "PV",
    "ConfigMap": "CM",
    "Namespace": "NS",
}

_SYS_NS = {"kube-system", "kube-public", "kube-node-lease"}


def _nid(kind: str, namespace: str | None, name: str) -> str:
    return f"{kind}/{namespace}/{name}" if namespace else f"{kind}/{name}"


def _label(kind: str, name: str) -> str:
    short = _KIND_SHORT.get(kind, kind)
    n = name if len(name) <= 22 else name[:19] + "…"
    return f"{short}\n{n}"


def build_graph(
    namespace: str | None = None,
    include_configmaps: bool = False,
    include_secrets: bool = False,
) -> nx.DiGraph:
    """Build a directed NetworkX graph of K8s resources and their relationships."""
    if not init_k8s():
        return nx.DiGraph()

    G = nx.DiGraph()
    v1 = client.CoreV1Api()
    apps = client.AppsV1Api()
    net_v1 = client.NetworkingV1Api()

    def add_node(kind: str, ns: str | None, name: str, title: str) -> str:
        nid = _nid(kind, ns, name)
        G.add_node(
            nid,
            kind=kind,
            namespace=ns or "",
            name=name,
            label=_label(kind, name),
            title=title,
            color=_KIND_COLORS.get(kind, "#aaaaaa"),
            size=_KIND_SIZES.get(kind, 12),
        )
        return nid

    def link(src: str, dst: str, rel: str) -> None:
        if G.has_node(src) and G.has_node(dst):
            G.add_edge(src, dst, label=rel)

    # ── Namespaces ────────────────────────────────────────────────────────────
    ns_ids: dict[str, str] = {}
    try:
        for ns_obj in v1.list_namespace().items:
            n = ns_obj.metadata.name
            if namespace and n != namespace:
                continue
            ns_ids[n] = add_node("Namespace", None, n, f"Namespace: {n}")
    except Exception:
        pass

    # ── Nodes ─────────────────────────────────────────────────────────────────
    try:
        for node in v1.list_node().items:
            n = node.metadata.name
            ready = any(
                c.type == "Ready" and c.status == "True"
                for c in (node.status.conditions or [])
            )
            add_node(
                "Node",
                None,
                n,
                f"Node: {n}\nStatus: {'Ready' if ready else 'NotReady'}",
            )
    except Exception:
        pass

    # ── Deployments ───────────────────────────────────────────────────────────
    dep_selectors: dict[str, tuple[str, dict]] = {}
    try:
        items = (
            apps.list_namespaced_deployment(namespace).items
            if namespace
            else apps.list_deployment_for_all_namespaces().items
        )
        for d in items:
            ns, n = d.metadata.namespace, d.metadata.name
            r = d.status.ready_replicas or 0
            t = d.spec.replicas or 0
            nid = add_node("Deployment", ns, n, f"Deployment: {ns}/{n}\nReady: {r}/{t}")
            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")
            sel = (d.spec.selector.match_labels or {}) if d.spec.selector else {}
            dep_selectors[nid] = (ns, sel)
    except Exception:
        pass

    # ── StatefulSets ──────────────────────────────────────────────────────────
    sts_selectors: dict[str, tuple[str, dict]] = {}
    try:
        items = (
            apps.list_namespaced_stateful_set(namespace).items
            if namespace
            else apps.list_stateful_set_for_all_namespaces().items
        )
        for s in items:
            ns, n = s.metadata.namespace, s.metadata.name
            r = s.status.ready_replicas or 0
            t = s.spec.replicas or 0
            nid = add_node(
                "StatefulSet", ns, n, f"StatefulSet: {ns}/{n}\nReady: {r}/{t}"
            )
            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")
            sel = (s.spec.selector.match_labels or {}) if s.spec.selector else {}
            sts_selectors[nid] = (ns, sel)
    except Exception:
        pass

    # ── DaemonSets ────────────────────────────────────────────────────────────
    ds_selectors: dict[str, tuple[str, dict]] = {}
    try:
        items = (
            apps.list_namespaced_daemon_set(namespace).items
            if namespace
            else apps.list_daemon_set_for_all_namespaces().items
        )
        for d in items:
            ns, n = d.metadata.namespace, d.metadata.name
            nid = add_node("DaemonSet", ns, n, f"DaemonSet: {ns}/{n}")
            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")
            sel = (d.spec.selector.match_labels or {}) if d.spec.selector else {}
            ds_selectors[nid] = (ns, sel)
    except Exception:
        pass

    # ── Services ──────────────────────────────────────────────────────────────
    svc_selectors: dict[str, tuple[str, dict]] = {}
    try:
        items = (
            v1.list_namespaced_service(namespace).items
            if namespace
            else v1.list_service_for_all_namespaces().items
        )
        for svc in items:
            ns, n = svc.metadata.namespace, svc.metadata.name
            svc_type = svc.spec.type or "ClusterIP"
            nid = add_node("Service", ns, n, f"Service: {ns}/{n}\nType: {svc_type}")
            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")
            sel = svc.spec.selector or {}
            if sel:
                svc_selectors[nid] = (ns, sel)
    except Exception:
        pass

    # ── Ingresses ─────────────────────────────────────────────────────────────
    try:
        items = (
            net_v1.list_namespaced_ingress(namespace).items
            if namespace
            else net_v1.list_ingress_for_all_namespaces().items
        )
        for ing in items:
            ns, n = ing.metadata.namespace, ing.metadata.name
            nid = add_node("Ingress", ns, n, f"Ingress: {ns}/{n}")
            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")
            for rule in ing.spec.rules or []:
                if rule.http:
                    for path in rule.http.paths or []:
                        if path.backend and path.backend.service:
                            svc_nid = _nid("Service", ns, path.backend.service.name)
                            link(nid, svc_nid, "routes_to")
    except Exception:
        pass

    # ── PVCs ──────────────────────────────────────────────────────────────────
    pvc_map: dict[tuple[str, str], str] = {}
    try:
        items = (
            v1.list_namespaced_persistent_volume_claim(namespace).items
            if namespace
            else v1.list_persistent_volume_claim_for_all_namespaces().items
        )
        for pvc in items:
            ns, n = pvc.metadata.namespace, pvc.metadata.name
            status = pvc.status.phase or "Unknown"
            nid = add_node(
                "PersistentVolumeClaim", ns, n, f"PVC: {ns}/{n}\nStatus: {status}"
            )
            pvc_map[(ns, n)] = nid
            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")
            if pvc.spec.volume_name:
                link(
                    nid,
                    _nid("PersistentVolume", None, pvc.spec.volume_name),
                    "bound_to",
                )
    except Exception:
        pass

    # ── PVs ───────────────────────────────────────────────────────────────────
    try:
        for pv in v1.list_persistent_volume().items:
            n = pv.metadata.name
            status = pv.status.phase or "—"
            add_node("PersistentVolume", None, n, f"PV: {n}\nStatus: {status}")
    except Exception:
        pass

    # ── ConfigMaps ────────────────────────────────────────────────────────────
    if include_configmaps:
        try:
            items = (
                v1.list_namespaced_config_map(namespace).items
                if namespace
                else v1.list_config_map_for_all_namespaces().items
            )
            for cm in items:
                ns, n = cm.metadata.namespace, cm.metadata.name
                if not namespace and ns in _SYS_NS:
                    continue
                nid = add_node("ConfigMap", ns, n, f"ConfigMap: {ns}/{n}")
                if ns in ns_ids:
                    link(ns_ids[ns], nid, "contains")
        except Exception:
            pass

    # ── Secrets ───────────────────────────────────────────────────────────────
    if include_secrets:
        try:
            items = (
                v1.list_namespaced_secret(namespace).items
                if namespace
                else v1.list_secret_for_all_namespaces().items
            )
            for sec in items:
                ns, n = sec.metadata.namespace, sec.metadata.name
                if not namespace and ns in _SYS_NS:
                    continue
                nid = add_node("Secret", ns, n, f"Secret: {ns}/{n}")
                if ns in ns_ids:
                    link(ns_ids[ns], nid, "contains")
        except Exception:
            pass

    # ── Pods ──────────────────────────────────────────────────────────────────
    _BAD = {
        "CrashLoopBackOff",
        "ErrImagePull",
        "ImagePullBackOff",
        "CreateContainerConfigError",
    }
    all_selectors = {**dep_selectors, **sts_selectors, **ds_selectors}
    try:
        items = (
            v1.list_namespaced_pod(namespace).items
            if namespace
            else v1.list_pod_for_all_namespaces().items
        )
        for pod in items:
            ns, n = pod.metadata.namespace, pod.metadata.name
            phase = pod.status.phase or "Unknown"
            for cs in pod.status.container_statuses or []:
                if cs.state.waiting and cs.state.waiting.reason in _BAD:
                    phase = cs.state.waiting.reason
                    break
            failing = phase not in ("Running", "Succeeded", "Completed")
            pod_labels = pod.metadata.labels or {}

            nid = _nid("Pod", ns, n)
            color = "#e74c3c" if failing else _KIND_COLORS["Pod"]
            G.add_node(
                nid,
                kind="Pod",
                namespace=ns,
                name=n,
                label=_label("Pod", n),
                title=f"Pod: {ns}/{n}\nStatus: {phase}",
                color=color,
                size=_KIND_SIZES["Pod"],
            )

            if ns in ns_ids:
                link(ns_ids[ns], nid, "contains")

            if pod.spec.node_name:
                link(nid, _nid("Node", None, pod.spec.node_name), "runs_on")

            for vol in pod.spec.volumes or []:
                if vol.persistent_volume_claim:
                    pvc_nid = pvc_map.get((ns, vol.persistent_volume_claim.claim_name))
                    if pvc_nid:
                        link(nid, pvc_nid, "mounts")

            for wid, (wns, sel) in all_selectors.items():
                if (
                    wns == ns
                    and sel
                    and all(pod_labels.get(k) == v for k, v in sel.items())
                ):
                    link(wid, nid, "manages")
                    break

            for sid, (sns, sel) in svc_selectors.items():
                if (
                    sns == ns
                    and sel
                    and all(pod_labels.get(k) == v for k, v in sel.items())
                ):
                    link(sid, nid, "selects")
    except Exception:
        pass

    return G


def render_graph(G: nx.DiGraph, height: int = 750) -> str:
    """Convert a NetworkX DiGraph to a Pyvis HTML string."""
    from pyvis.network import Network

    net = Network(
        height=f"{height}px",
        width="100%",
        directed=True,
        bgcolor="#0e1117",
        font_color="#ffffff",
    )
    net.force_atlas_2based(
        gravity=-50,
        central_gravity=0.01,
        spring_length=120,
        spring_strength=0.08,
        damping=0.4,
        overlap=0,
    )
    net.set_options(
        """
        {
          "interaction": { "hover": true, "tooltipDelay": 80 },
          "edges": {
            "arrows": { "to": { "enabled": true, "scaleFactor": 0.5 } },
            "color": { "color": "#444444", "highlight": "#aaaaaa" },
            "font": { "color": "#888888", "size": 9, "strokeWidth": 0 },
            "smooth": { "type": "dynamic" }
          },
          "nodes": {
            "font": { "size": 11, "multi": true },
            "borderWidth": 1,
            "borderWidthSelected": 3
          }
        }
        """
    )

    for nid, attrs in G.nodes(data=True):
        net.add_node(
            nid,
            label=attrs.get("label", nid),
            title=attrs.get("title", nid),
            color=attrs.get("color", "#aaaaaa"),
            size=attrs.get("size", 12),
        )

    for src, dst, data in G.edges(data=True):
        net.add_edge(src, dst, title=data.get("label", ""), label=data.get("label", ""))

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w")
    tmp.close()
    try:
        net.write_html(tmp.name)
        with open(tmp.name) as f:
            return f.read()
    finally:
        os.unlink(tmp.name)
