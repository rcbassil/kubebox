import importlib
import io
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import anthropic
import pandas as pd
import streamlit as st
from kubernetes import client
from kubernetes import config as k8s_config
from rich.console import Console

import core.crd as _crd_mod
import core.events as _events_mod
import core.flux as _flux_mod
import core.helm as _helm_mod
import core.kubernetes as _k8s_mod
import core.report as _report_mod
import core.utils as _utils_mod
from core.kubernetes import init_k8s
from core.utils import fmt_age, set_context

# ─── Console Capture ─────────────────────────────────────────────────────────
# Each module imports `console` from core.utils as a local binding.  Patching
# each module's own `console` attribute redirects all Rich output to our buffer.

_PATCH_MODS = [
    _utils_mod,
    _k8s_mod,
    _events_mod,
    _report_mod,
    _flux_mod,
    _helm_mod,
    _crd_mod,
]
for _extra in [
    "core.vault",
    "core.rbac",
    "core.network",
    "core.kong",
    "core.kustomize",
    "core.trace",
]:
    try:
        _PATCH_MODS.append(importlib.import_module(_extra))
    except Exception:
        pass


@contextmanager
def _capture(width: int = 120):
    buf = io.StringIO()
    new_con = Console(file=buf, no_color=True, width=width)
    saved = {mod: mod.console for mod in _PATCH_MODS if hasattr(mod, "console")}
    for mod in saved:
        mod.console = new_con
    try:
        yield buf
    finally:
        for mod, orig in saved.items():
            mod.console = orig


def run_diag(func, *args, **kwargs) -> str:
    with _capture() as buf:
        try:
            func(*args, **kwargs)
        except Exception as exc:
            return f"Error: {exc}"
    return buf.getvalue()


# ─── Date Helper ─────────────────────────────────────────────────────────────


def _age(dt) -> str:
    if dt is None:
        return "—"
    iso = dt.isoformat() if not isinstance(dt, str) else dt
    return fmt_age(iso)


# ─── Cached K8s Fetch Functions ──────────────────────────────────────────────
# `ctx` is included in every signature so cache keys are context-aware.
# Inside each function we just call init_k8s() which reads the global context
# set by set_context() before we ever reach these cached calls.


@st.cache_data(ttl=30, show_spinner=False)
def fetch_namespaces(ctx: str) -> list[str]:
    if not init_k8s():
        return []
    try:
        return [ns.metadata.name for ns in client.CoreV1Api().list_namespace().items]
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def fetch_nodes(ctx: str) -> list[dict]:
    if not init_k8s():
        return []
    try:
        nodes = client.CoreV1Api().list_node().items
    except Exception:
        return []
    rows = []
    for n in nodes:
        ready = any(
            c.type == "Ready" and c.status == "True"
            for c in (n.status.conditions or [])
        )
        roles = [
            lbl.split("/")[1]
            for lbl in (n.metadata.labels or {})
            if lbl.startswith("node-role.kubernetes.io/")
        ]
        ver = n.status.node_info.kubelet_version if n.status.node_info else "—"
        rows.append(
            {
                "Name": n.metadata.name,
                "Status": "Ready" if ready else "NotReady",
                "Roles": ",".join(roles) or "worker",
                "Version": ver,
                "Age": _age(n.metadata.creation_timestamp),
            }
        )
    return rows


@st.cache_data(ttl=20, show_spinner=False)
def fetch_pods(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    v1 = client.CoreV1Api()
    try:
        pods = (
            v1.list_namespaced_pod(ns).items
            if ns
            else v1.list_pod_for_all_namespaces().items
        )
    except Exception:
        return []
    rows = []
    for pod in pods:
        phase = pod.status.phase or "Unknown"
        restarts = 0
        ready = 0
        total = len(pod.spec.containers)
        for cs in pod.status.container_statuses or []:
            restarts += cs.restart_count
            if cs.ready:
                ready += 1
            if cs.state.waiting and cs.state.waiting.reason:
                phase = cs.state.waiting.reason
            elif cs.state.terminated and (cs.state.terminated.exit_code or 0) != 0:
                phase = cs.state.terminated.reason or "Error"
        rows.append(
            {
                "Namespace": pod.metadata.namespace,
                "Name": pod.metadata.name,
                "Status": phase,
                "Ready": f"{ready}/{total}",
                "Restarts": restarts,
                "Node": pod.spec.node_name or "—",
                "Age": _age(pod.metadata.creation_timestamp),
            }
        )
    return rows


@st.cache_data(ttl=30, show_spinner=False)
def fetch_deployments(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    apps = client.AppsV1Api()
    try:
        items = (
            apps.list_namespaced_deployment(ns).items
            if ns
            else apps.list_deployment_for_all_namespaces().items
        )
    except Exception:
        return []
    return [
        {
            "Namespace": d.metadata.namespace,
            "Name": d.metadata.name,
            "Ready": f"{d.status.ready_replicas or 0}/{d.spec.replicas or 0}",
            "Up-to-date": d.status.updated_replicas or 0,
            "Available": d.status.available_replicas or 0,
            "Age": _age(d.metadata.creation_timestamp),
        }
        for d in items
    ]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_statefulsets(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    apps = client.AppsV1Api()
    try:
        items = (
            apps.list_namespaced_stateful_set(ns).items
            if ns
            else apps.list_stateful_set_for_all_namespaces().items
        )
    except Exception:
        return []
    return [
        {
            "Namespace": s.metadata.namespace,
            "Name": s.metadata.name,
            "Ready": f"{s.status.ready_replicas or 0}/{s.spec.replicas or 0}",
            "Age": _age(s.metadata.creation_timestamp),
        }
        for s in items
    ]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_daemonsets(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    apps = client.AppsV1Api()
    try:
        items = (
            apps.list_namespaced_daemon_set(ns).items
            if ns
            else apps.list_daemon_set_for_all_namespaces().items
        )
    except Exception:
        return []
    return [
        {
            "Namespace": ds.metadata.namespace,
            "Name": ds.metadata.name,
            "Desired": ds.status.desired_number_scheduled or 0,
            "Ready": ds.status.number_ready or 0,
            "Available": ds.status.number_available or 0,
            "Age": _age(ds.metadata.creation_timestamp),
        }
        for ds in items
    ]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_services(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    v1 = client.CoreV1Api()
    try:
        svcs = (
            v1.list_namespaced_service(ns).items
            if ns
            else v1.list_service_for_all_namespaces().items
        )
        eps_items = (
            v1.list_namespaced_endpoints(ns).items
            if ns
            else v1.list_endpoints_for_all_namespaces().items
        )
    except Exception:
        return []
    eps_map = {
        (ep.metadata.namespace, ep.metadata.name): sum(
            len(s.addresses or []) for s in (ep.subsets or [])
        )
        for ep in eps_items
    }
    return [
        {
            "Namespace": svc.metadata.namespace,
            "Name": svc.metadata.name,
            "Type": svc.spec.type or "ClusterIP",
            "Ports": ", ".join(
                f"{p.port}/{p.protocol}" for p in (svc.spec.ports or [])
            ),
            "Endpoints": eps_map.get((svc.metadata.namespace, svc.metadata.name), 0),
            "Age": _age(svc.metadata.creation_timestamp),
        }
        for svc in svcs
    ]


@st.cache_data(ttl=20, show_spinner=False)
def fetch_events(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    v1 = client.CoreV1Api()
    try:
        events = (
            v1.list_namespaced_event(ns).items
            if ns
            else v1.list_event_for_all_namespaces().items
        )
    except Exception:
        return []
    rows = []
    for e in events:
        ts = e.last_timestamp or e.metadata.creation_timestamp
        rows.append(
            {
                "Age": _age(ts),
                "Namespace": e.metadata.namespace or "—",
                "Type": e.type or "?",
                "Reason": e.reason or "?",
                "Object": f"{e.involved_object.kind}/{e.involved_object.name}"
                if e.involved_object
                else "?",
                "Count": e.count or 1,
                "Message": (e.message or "")[:200],
                "_ts": ts,
            }
        )
    rows.sort(
        key=lambda r: r["_ts"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for r in rows:
        del r["_ts"]
    return rows


@st.cache_data(ttl=30, show_spinner=False)
def fetch_pvcs(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    v1 = client.CoreV1Api()
    try:
        pvcs = (
            v1.list_namespaced_persistent_volume_claim(ns).items
            if ns
            else v1.list_persistent_volume_claim_for_all_namespaces().items
        )
    except Exception:
        return []
    return [
        {
            "Namespace": p.metadata.namespace,
            "Name": p.metadata.name,
            "Status": p.status.phase or "Unknown",
            "Capacity": (p.status.capacity or {}).get("storage", "—"),
            "StorageClass": p.spec.storage_class_name or "—",
            "Access Modes": ",".join(p.spec.access_modes or []),
            "Age": _age(p.metadata.creation_timestamp),
        }
        for p in pvcs
    ]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_pvs(ctx: str) -> list[dict]:
    if not init_k8s():
        return []
    try:
        pvs = client.CoreV1Api().list_persistent_volume().items
    except Exception:
        return []
    return [
        {
            "Name": p.metadata.name,
            "Capacity": (p.spec.capacity or {}).get("storage", "—"),
            "Access Modes": ",".join(p.spec.access_modes or []),
            "Reclaim": p.spec.persistent_volume_reclaim_policy or "—",
            "Status": p.status.phase or "—",
            "StorageClass": p.spec.storage_class_name or "—",
            "Claim": (
                f"{p.spec.claim_ref.namespace}/{p.spec.claim_ref.name}"
                if p.spec.claim_ref
                else "—"
            ),
            "Age": _age(p.metadata.creation_timestamp),
        }
        for p in pvs
    ]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_storageclasses(ctx: str) -> list[dict]:
    if not init_k8s():
        return []
    try:
        scs = client.StorageV1Api().list_storage_class().items
    except Exception:
        return []
    return [
        {
            "Name": sc.metadata.name,
            "Provisioner": sc.provisioner,
            "Reclaim": sc.reclaim_policy or "—",
            "Binding Mode": sc.volume_binding_mode or "—",
            "Default": "✓"
            if (sc.metadata.annotations or {}).get(
                "storageclass.kubernetes.io/is-default-class"
            )
            == "true"
            else "",
            "Age": _age(sc.metadata.creation_timestamp),
        }
        for sc in scs
    ]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_configmaps(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    _SYS = {"kube-system", "kube-public", "kube-node-lease"}
    try:
        cms = (
            client.CoreV1Api().list_namespaced_config_map(ns).items
            if ns
            else client.CoreV1Api().list_config_map_for_all_namespaces().items
        )
    except Exception:
        return []
    return [
        {
            "Namespace": c.metadata.namespace,
            "Name": c.metadata.name,
            "Keys": len(c.data or {}) + len(c.binary_data or {}),
            "Age": _age(c.metadata.creation_timestamp),
        }
        for c in cms
        if ns or c.metadata.namespace not in _SYS
    ]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_secrets(ctx: str, ns: str) -> list[dict]:
    if not init_k8s():
        return []
    _SYS = {"kube-system", "kube-public", "kube-node-lease"}
    try:
        secrets = (
            client.CoreV1Api().list_namespaced_secret(ns).items
            if ns
            else client.CoreV1Api().list_secret_for_all_namespaces().items
        )
    except Exception:
        return []
    return [
        {
            "Namespace": s.metadata.namespace,
            "Name": s.metadata.name,
            "Type": s.type or "Opaque",
            "Keys": len(s.data or {}),
            "Age": _age(s.metadata.creation_timestamp),
        }
        for s in secrets
        if ns or s.metadata.namespace not in _SYS
    ]


# ─── AI Helpers ──────────────────────────────────────────────────────────────

_AI_SYSTEM = """You are an expert Kubernetes SRE embedded in kubebox, a read-only diagnostic dashboard.

You receive live cluster diagnostic data and answer questions about it.

Rules:
- Identify root causes from the provided data
- Suggest only read-only remediation steps (kubectl describe, logs, get, top)
- Never suggest mutative commands (apply, delete, patch, scale, edit)
- Be concise and direct — use markdown for formatting"""


def _gather_context(ctx: str, ns: str) -> str:
    pods = fetch_pods(ctx, ns)
    failing = [
        p for p in pods if p["Status"] not in ("Running", "Succeeded", "Completed")
    ]
    events = fetch_events(ctx, ns)
    warnings = [e for e in events[:100] if e["Type"] == "Warning"]

    lines = ["=== Failing Pods ==="]
    lines += [
        f"{p['Namespace']}/{p['Name']}: {p['Status']} (restarts={p['Restarts']})"
        for p in failing[:20]
    ] or ["None"]
    lines += ["\n=== Recent Warning Events ==="]
    lines += [
        f"{e['Age']} [{e['Namespace']}] {e['Object']} — {e['Reason']}: {e['Message']}"
        for e in warnings[:20]
    ] or ["None"]
    return "\n".join(lines)


def _stream_ai(question: str, context: str):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield "**Error:** `ANTHROPIC_API_KEY` is not set. Export it and restart the app."
        return
    try:
        ai_client = anthropic.Anthropic(api_key=api_key)
        user_content = (
            f"Cluster diagnostic output:\n\n{context}\n\nQuestion: {question}"
            if context.strip()
            else question
        )
        with ai_client.messages.stream(
            model="claude-opus-4-7",
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _AI_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                yield text
    except anthropic.AuthenticationError:
        yield "**Error:** Invalid `ANTHROPIC_API_KEY`."
    except anthropic.APIConnectionError:
        yield "**Error:** Could not connect to Anthropic API."
    except anthropic.APIStatusError as exc:
        yield f"**API error {exc.status_code}:** {exc.message}"


# ─── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Kubebox",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 12px 16px;
}
</style>
""",
    unsafe_allow_html=True,
)

# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Kubebox")
    st.caption("Kubernetes Diagnostics Dashboard")
    st.divider()

    # Context selector
    try:
        all_contexts, active_ctx = k8s_config.list_kube_config_contexts()
        ctx_names = [c["name"] for c in all_contexts]
        default_idx = (
            ctx_names.index(active_ctx["name"])
            if active_ctx and active_ctx["name"] in ctx_names
            else 0
        )
    except Exception:
        ctx_names, default_idx = [], 0

    if ctx_names:
        sel_ctx = st.selectbox("Context", ctx_names, index=default_idx)
    else:
        sel_ctx = st.text_input("Context", value="", placeholder="(default)")

    set_context(sel_ctx or None)

    # Namespace selector
    ns_list = fetch_namespaces(sel_ctx or "")
    ns_options = ["(all namespaces)"] + ns_list
    sel_ns_label = st.selectbox("Namespace", ns_options)
    sel_ns = "" if sel_ns_label == "(all namespaces)" else sel_ns_label

    st.divider()
    if st.button("🔄 Refresh all", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption(f"**Context:** `{sel_ctx or 'default'}`")
    st.caption(f"**Namespace:** `{sel_ns or 'all'}`")

# Convenience aliases used throughout
_ctx = sel_ctx or ""
_ns = sel_ns or ""

# ─── Top-level Tabs ──────────────────────────────────────────────────────────

(
    tab_overview,
    tab_pods,
    tab_workloads,
    tab_events,
    tab_storage,
    tab_config,
    tab_gitops,
    tab_ai,
) = st.tabs(
    [
        "📊 Overview",
        "🫙 Pods",
        "🚀 Workloads",
        "📅 Events",
        "💾 Storage",
        "⚙️ Config",
        "🔮 GitOps",
        "🤖 AI Assistant",
    ]
)

# ─── Overview ────────────────────────────────────────────────────────────────

with tab_overview:
    st.header("Cluster Overview")

    with st.spinner("Loading…"):
        pods = fetch_pods(_ctx, _ns)
        nodes = fetch_nodes(_ctx)

    failing_pods = [
        p for p in pods if p["Status"] not in ("Running", "Succeeded", "Completed")
    ]
    ready_nodes = sum(1 for n in nodes if n["Status"] == "Ready")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pods", len(pods))
    c2.metric(
        "Failing Pods",
        len(failing_pods),
        delta=f"-{len(failing_pods)}" if failing_pods else None,
        delta_color="inverse",
    )
    c3.metric("Nodes", len(nodes))
    c4.metric(
        "Ready Nodes",
        ready_nodes,
        delta=f"{ready_nodes - len(nodes)}" if ready_nodes < len(nodes) else None,
        delta_color="inverse",
    )

    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("Nodes")
        if nodes:
            df = pd.DataFrame(nodes)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No nodes found — is the cluster reachable?")

    with right:
        st.subheader("Failing Pods")
        if failing_pods:
            df = pd.DataFrame(failing_pods)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.success("✓ All pods are healthy")

    st.divider()
    st.subheader("Cluster Health Report")
    if st.button("Generate full report", key="gen_report"):
        with st.spinner("Generating…"):
            from core.report import generate_report

            with _capture():
                md, has_issues = generate_report(namespace=_ns or None)
        if has_issues:
            st.warning("Issues detected — see report below.")
        else:
            st.success("No issues detected.")
        st.markdown(md)

# ─── Pods ────────────────────────────────────────────────────────────────────

with tab_pods:
    st.header("Pods")

    fcol1, fcol2 = st.columns([1, 3])
    with fcol1:
        failing_only = st.checkbox("Failing only", key="pods_fail")
    with fcol2:
        pod_filter = st.text_input(
            "Filter by name", key="pod_filter", placeholder="e.g. nginx"
        )

    with st.spinner("Loading pods…"):
        pods = fetch_pods(_ctx, _ns)

    if failing_only:
        pods = [
            p for p in pods if p["Status"] not in ("Running", "Succeeded", "Completed")
        ]
    if pod_filter:
        pods = [p for p in pods if pod_filter.lower() in p["Name"].lower()]

    if pods:
        st.caption(f"{len(pods)} pods")
        df = pd.DataFrame(pods)
        st.dataframe(df, use_container_width=True, hide_index=True, height=420)
    else:
        st.info("No pods match the current filters.")

    st.divider()
    st.subheader("Pod Logs")

    lcol1, lcol2, lcol3 = st.columns([1, 2, 1])
    with lcol1:
        log_ns = st.text_input("Namespace", key="log_ns", value=_ns or "default")
    with lcol2:
        log_pod = st.text_input("Pod name", key="log_pod")
    with lcol3:
        log_tail = st.number_input("Tail lines", 10, 1000, 100, key="log_tail")

    log_prev = st.checkbox("Previous container (--previous)", key="log_prev")

    if st.button("Fetch Logs", key="fetch_logs"):
        if not log_pod:
            st.warning("Enter a pod name.")
        else:
            from core.utils import run_cmd

            cmd = ["kubectl", "logs", log_pod, "-n", log_ns, f"--tail={log_tail}"]
            if log_prev:
                cmd.append("--previous")
            with st.spinner("Fetching logs…"):
                output = run_cmd(cmd)
            if output:
                st.code(output, language="text", line_numbers=True)
            else:
                st.warning("No output returned.")

    st.divider()
    st.subheader("Describe Object")

    dcol1, dcol2, dcol3 = st.columns([1, 1, 2])
    with dcol1:
        desc_kind = st.selectbox(
            "Kind",
            ["pod", "deployment", "service", "node", "pvc", "ingress"],
            key="desc_kind",
        )
    with dcol2:
        desc_ns = st.text_input("Namespace", key="desc_ns", value=_ns or "default")
    with dcol3:
        desc_name = st.text_input("Name", key="desc_name")

    if st.button("Describe", key="do_describe"):
        if not desc_name:
            st.warning("Enter a name.")
        else:
            from core.utils import run_cmd

            cmd = ["kubectl", "describe", desc_kind, desc_name]
            if desc_kind != "node":
                cmd += ["-n", desc_ns]
            with st.spinner("Running describe…"):
                output = run_cmd(cmd)
            if output:
                st.code(output, language="yaml")
            else:
                st.warning("No output returned.")

# ─── Workloads ───────────────────────────────────────────────────────────────

with tab_workloads:
    st.header("Workloads")

    wtab_deploy, wtab_sts, wtab_ds, wtab_svc = st.tabs(
        ["Deployments", "StatefulSets", "DaemonSets", "Services"]
    )

    with wtab_deploy:
        with st.spinner("Loading deployments…"):
            deps = fetch_deployments(_ctx, _ns)
        if deps:
            st.dataframe(pd.DataFrame(deps), use_container_width=True, hide_index=True)
        else:
            st.info("No deployments found.")

    with wtab_sts:
        with st.spinner("Loading StatefulSets…"):
            sts = fetch_statefulsets(_ctx, _ns)
        if sts:
            st.dataframe(pd.DataFrame(sts), use_container_width=True, hide_index=True)
        else:
            st.info("No StatefulSets found.")

    with wtab_ds:
        with st.spinner("Loading DaemonSets…"):
            ds_list = fetch_daemonsets(_ctx, _ns)
        if ds_list:
            st.dataframe(
                pd.DataFrame(ds_list), use_container_width=True, hide_index=True
            )
        else:
            st.info("No DaemonSets found.")

    with wtab_svc:
        with st.spinner("Loading services…"):
            svcs = fetch_services(_ctx, _ns)
        if svcs:
            st.dataframe(pd.DataFrame(svcs), use_container_width=True, hide_index=True)
        else:
            st.info("No services found.")

    st.divider()
    st.subheader("Full Workload Diagnostics")
    st.caption(
        "Runs a comprehensive scan for degraded workloads, services with no endpoints, and recent warning events."
    )
    if st.button("Run diagnostics", key="diag_workloads"):
        with st.spinner("Scanning…"):
            out = run_diag(_k8s_mod.check_all_objects, _ns or None)
        st.code(out, language="text")

# ─── Events ──────────────────────────────────────────────────────────────────

with tab_events:
    st.header("Events")

    ef1, ef2, ef3 = st.columns([1, 2, 1])
    with ef1:
        evt_type = st.selectbox("Type", ["All", "Warning", "Normal"], key="evt_type")
    with ef2:
        evt_reason = st.text_input(
            "Reason filter", key="evt_reason", placeholder="e.g. OOMKilling"
        )
    with ef3:
        evt_limit = st.number_input("Max rows", 10, 500, 100, key="evt_limit", step=10)

    with st.spinner("Loading events…"):
        events = fetch_events(_ctx, _ns)

    if evt_type != "All":
        events = [e for e in events if e["Type"] == evt_type]
    if evt_reason:
        events = [e for e in events if evt_reason.lower() in e["Reason"].lower()]

    events = events[: int(evt_limit)]

    warn_count = sum(1 for e in events if e["Type"] == "Warning")
    st.caption(f"{len(events)} events — {warn_count} warnings")

    if events:
        df = pd.DataFrame(events)

        def _color_row(row):
            if row["Type"] == "Warning":
                return ["color: #ff6b6b"] * len(row)
            return [""] * len(row)

        st.dataframe(
            df.style.apply(_color_row, axis=1),
            use_container_width=True,
            hide_index=True,
            height=520,
        )
    else:
        st.info("No events match the current filters.")

# ─── Storage ─────────────────────────────────────────────────────────────────

with tab_storage:
    st.header("Storage")

    stab_pvc, stab_pv, stab_sc = st.tabs(["PVCs", "PVs", "StorageClasses"])

    with stab_pvc:
        with st.spinner("Loading PVCs…"):
            pvcs = fetch_pvcs(_ctx, _ns)
        if pvcs:
            df = pd.DataFrame(pvcs)
            unbound = sum(1 for p in pvcs if p["Status"] != "Bound")
            if unbound:
                st.warning(f"{unbound} unbound PVC(s)")
            else:
                st.success("✓ All PVCs are Bound")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No PVCs found.")

    with stab_pv:
        with st.spinner("Loading PVs…"):
            pvs = fetch_pvs(_ctx)
        if pvs:
            st.dataframe(pd.DataFrame(pvs), use_container_width=True, hide_index=True)
        else:
            st.info("No PVs found.")

    with stab_sc:
        with st.spinner("Loading StorageClasses…"):
            scs = fetch_storageclasses(_ctx)
        if scs:
            st.dataframe(pd.DataFrame(scs), use_container_width=True, hide_index=True)
        else:
            st.info("No StorageClasses found.")

# ─── Config ──────────────────────────────────────────────────────────────────

with tab_config:
    st.header("Configuration")

    ctab_cm, ctab_sec = st.tabs(["ConfigMaps", "Secrets"])

    with ctab_cm:
        with st.spinner("Loading ConfigMaps…"):
            cms = fetch_configmaps(_ctx, _ns)
        if cms:
            st.dataframe(pd.DataFrame(cms), use_container_width=True, hide_index=True)
        else:
            st.info("No ConfigMaps found.")

    with ctab_sec:
        st.caption("Values are never shown — names and types only.")
        with st.spinner("Loading Secrets…"):
            secrets = fetch_secrets(_ctx, _ns)
        if secrets:
            st.dataframe(
                pd.DataFrame(secrets), use_container_width=True, hide_index=True
            )
        else:
            st.info("No Secrets found.")

# ─── GitOps ──────────────────────────────────────────────────────────────────

with tab_gitops:
    st.header("GitOps & Helm")

    gtab_flux, gtab_helm, gtab_crd = st.tabs(["Flux CD", "Helm", "CRDs"])

    with gtab_flux:
        st.caption(
            "Checks GitRepository, Kustomization, and HelmRelease Flux resources."
        )
        if st.button("Check Flux status", key="flux_btn"):
            with st.spinner("Checking Flux…"):
                out = run_diag(_flux_mod.check_flux_status)
            st.code(out, language="text")

    with gtab_helm:
        st.caption("Lists all Helm releases and flags failed or stuck ones.")
        if st.button("Check Helm releases", key="helm_btn"):
            with st.spinner("Checking Helm…"):
                out = run_diag(_helm_mod.check_helm_status, _ns or None)
            st.code(out, language="text")

    with gtab_crd:
        st.caption(
            "Scans Custom Resource Definitions for instances with condition=False/Unknown."
        )
        if st.button("Check CRD status", key="crd_btn"):
            with st.spinner("Checking CRDs…"):
                out = run_diag(_crd_mod.check_crd_status, _ns or None)
            st.code(out, language="text")

# ─── AI Assistant ────────────────────────────────────────────────────────────

with tab_ai:
    st.header("AI Assistant")
    st.caption(
        "Powered by **Claude** — automatically gathers failing pods and warning events "
        "as context before answering your question."
    )

    if "ai_messages" not in st.session_state:
        st.session_state.ai_messages = []

    # Render chat history
    for msg in st.session_state.ai_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # New message
    if prompt := st.chat_input("Ask about your cluster…", key="ai_input"):
        st.session_state.ai_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.spinner("Gathering cluster context…"):
            ctx_data = _gather_context(_ctx, _ns)

        with st.chat_message("assistant"):
            response = st.write_stream(_stream_ai(prompt, ctx_data))

        st.session_state.ai_messages.append({"role": "assistant", "content": response})

    if st.session_state.ai_messages:
        if st.button("Clear chat", key="clear_ai"):
            st.session_state.ai_messages = []
            st.rerun()
