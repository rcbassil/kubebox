"""Generate a Markdown cluster health report for CI / scheduled digests."""

import json
from datetime import datetime, timezone

from kubernetes import client

from core.utils import fmt_age, get_context, load_kube_config, run_cmd

_SYSTEM_NS = frozenset({"kube-system", "kube-public", "kube-node-lease"})


# ── Markdown helpers ──────────────────────────────────────────────────────────


def _row(*cells: str) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return ""
    lines = [_row(*headers), _row(*["---"] * len(headers))]
    for row in rows:
        lines.append(_row(*row))
    return "\n".join(lines)


def _section(title: str, body: str) -> str:
    return f"### {title}\n\n{body}"


# ── Report generator ──────────────────────────────────────────────────────────


def generate_report(
    namespace: str | None = None,
    title: str | None = None,
) -> tuple[str, bool]:
    """Return (markdown, has_issues). Raises on k8s connection failure."""
    load_kube_config()
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    batch_v1 = client.BatchV1Api()

    ctx = get_context() or "default"
    scope = f"`{namespace}`" if namespace else "all namespaces"
    report_title = title or "Cluster Health Report"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    summary_rows: list[list] = []
    issue_blocks: list[str] = []
    has_issues = False

    def _flag(rows: list) -> str:
        return "❌ Issues" if rows else "✅ Healthy"

    def _add_issue(title: str, headers: list[str], rows: list[list]) -> None:
        nonlocal has_issues
        if rows:
            has_issues = True
            issue_blocks.append(_section(title, _table(headers, rows)))

    # ── Nodes ─────────────────────────────────────────────────────────────
    if not namespace:
        try:
            nodes = v1.list_node().items
            not_ready = []
            for n in nodes:
                ready = any(
                    c.type == "Ready" and c.status == "True"
                    for c in n.status.conditions
                )
                if not ready:
                    not_ready.append([n.metadata.name])
            _add_issue("❌ NotReady Nodes", ["Node"], not_ready)
            detail = f"{len(nodes) - len(not_ready)}/{len(nodes)} ready"
            summary_rows.append(["Nodes", _flag(not_ready), detail])
        except Exception as e:
            summary_rows.append(["Nodes", "⚠️ Error", str(e)[:60]])

    # ── Pods ──────────────────────────────────────────────────────────────
    try:
        pods = (
            v1.list_namespaced_pod(namespace).items
            if namespace
            else v1.list_pod_for_all_namespaces().items
        )
        failing: list[list] = []
        for pod in pods:
            status_str = pod.status.phase or "Unknown"
            restarts = 0
            for cs in pod.status.container_statuses or []:
                restarts += cs.restart_count
                if cs.state.waiting and cs.state.waiting.reason in (
                    "CrashLoopBackOff",
                    "ErrImagePull",
                    "ImagePullBackOff",
                    "CreateContainerConfigError",
                ):
                    status_str = cs.state.waiting.reason
                elif cs.state.terminated and cs.state.terminated.exit_code != 0:
                    status_str = cs.state.terminated.reason or "Error"
            if status_str not in ("Running", "Succeeded"):
                failing.append(
                    [
                        pod.metadata.namespace,
                        pod.metadata.name,
                        status_str,
                        str(restarts),
                    ]
                )
        _add_issue(
            "❌ Failing Pods",
            ["Namespace", "Pod", "Status", "Restarts"],
            failing,
        )
        summary_rows.append(["Pods", _flag(failing), f"{len(failing)} failing"])
    except Exception as e:
        summary_rows.append(["Pods", "⚠️ Error", str(e)[:60]])

    # ── Workloads ─────────────────────────────────────────────────────────
    try:
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
        degraded: list[list] = []
        for d in deps:
            if d.spec.replicas and (d.status.ready_replicas or 0) != d.spec.replicas:
                degraded.append(
                    [
                        "Deployment",
                        d.metadata.namespace,
                        d.metadata.name,
                        f"{d.status.ready_replicas or 0}/{d.spec.replicas}",
                    ]
                )
        for s in sts:
            if s.spec.replicas and (s.status.ready_replicas or 0) != s.spec.replicas:
                degraded.append(
                    [
                        "StatefulSet",
                        s.metadata.namespace,
                        s.metadata.name,
                        f"{s.status.ready_replicas or 0}/{s.spec.replicas}",
                    ]
                )
        for ds in dss:
            desired = ds.status.desired_number_scheduled or 0
            ready = ds.status.number_ready or 0
            if desired and ready != desired:
                degraded.append(
                    [
                        "DaemonSet",
                        ds.metadata.namespace,
                        ds.metadata.name,
                        f"{ready}/{desired}",
                    ]
                )
        _add_issue(
            "❌ Degraded Workloads",
            ["Type", "Namespace", "Name", "Ready/Desired"],
            degraded,
        )
        summary_rows.append(["Workloads", _flag(degraded), f"{len(degraded)} degraded"])
    except Exception as e:
        summary_rows.append(["Workloads", "⚠️ Error", str(e)[:60]])

    # ── PVCs ──────────────────────────────────────────────────────────────
    try:
        pvcs = (
            v1.list_namespaced_persistent_volume_claim(namespace).items
            if namespace
            else v1.list_persistent_volume_claim_for_all_namespaces().items
        )
        unbound = [
            [p.metadata.namespace, p.metadata.name, p.status.phase or "Unknown"]
            for p in pvcs
            if p.status.phase != "Bound"
        ]
        _add_issue("❌ Unbound PVCs", ["Namespace", "PVC", "Status"], unbound)
        summary_rows.append(["PVCs", _flag(unbound), f"{len(unbound)} unbound"])
    except Exception as e:
        summary_rows.append(["PVCs", "⚠️ Error", str(e)[:60]])

    # ── Services ──────────────────────────────────────────────────────────
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
        no_ep: list[list] = []
        for svc in svcs:
            key = (svc.metadata.namespace, svc.metadata.name)
            if (
                svc.spec.selector
                and eps_map.get(key, 0) == 0
                and svc.spec.type != "ExternalName"
            ):
                no_ep.append(
                    [
                        svc.metadata.namespace,
                        svc.metadata.name,
                        svc.spec.type or "ClusterIP",
                    ]
                )
        _add_issue(
            "❌ Services with No Ready Endpoints",
            ["Namespace", "Service", "Type"],
            no_ep,
        )
        summary_rows.append(["Services", _flag(no_ep), f"{len(no_ep)} no-endpoint"])
    except Exception as e:
        summary_rows.append(["Services", "⚠️ Error", str(e)[:60]])

    # ── Jobs ──────────────────────────────────────────────────────────────
    try:
        jobs = (
            batch_v1.list_namespaced_job(namespace).items
            if namespace
            else batch_v1.list_job_for_all_namespaces().items
        )
        failed_jobs = [
            [j.metadata.namespace, j.metadata.name, str(j.status.failed or 0)]
            for j in jobs
            if (j.status.failed or 0) > 0 and not j.status.completion_time
        ]
        _add_issue(
            "❌ Failed Jobs", ["Namespace", "Job", "Failed Attempts"], failed_jobs
        )
        summary_rows.append(["Jobs", _flag(failed_jobs), f"{len(failed_jobs)} failed"])
    except Exception as e:
        summary_rows.append(["Jobs", "⚠️ Error", str(e)[:60]])

    # ── Warning Events ────────────────────────────────────────────────────
    try:
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
        out = run_cmd(cmd)
        event_rows: list[list] = []
        if out:
            items = json.loads(out).get("items", [])
            for e in items[-30:]:
                obj = "{}/{}".format(
                    e.get("involvedObject", {}).get("kind", ""),
                    e.get("involvedObject", {}).get("name", ""),
                )
                age = fmt_age(
                    e.get("lastTimestamp")
                    or e.get("metadata", {}).get("creationTimestamp")
                )
                msg = e.get("message", "")
                if len(msg) > 80:
                    msg = msg[:77] + "..."
                event_rows.append(
                    [
                        age,
                        e.get("involvedObject", {}).get("namespace", "cluster"),
                        obj,
                        e.get("reason", ""),
                        msg,
                    ]
                )
        if event_rows:
            has_issues = True
            issue_blocks.append(
                _section(
                    "⚠️ Warning Events",
                    _table(
                        ["Age", "Namespace", "Object", "Reason", "Message"], event_rows
                    ),
                )
            )
        ev_status = "⚠️ Warning" if event_rows else "✅ Clean"
        summary_rows.append(["Events", ev_status, f"{len(event_rows)} warnings"])
    except Exception as e:
        summary_rows.append(["Events", "⚠️ Error", str(e)[:60]])

    # ── Assemble ──────────────────────────────────────────────────────────
    overall = "❌ Issues detected" if has_issues else "✅ Cluster healthy"
    parts: list[str] = [
        f"# {report_title}",
        "",
        f"**Generated:** {generated}  ",
        f"**Context:** `{ctx}`  ",
        f"**Scope:** {scope}",
        "",
        f"**Overall: {overall}**",
        "",
        "---",
        "",
        "## Summary",
        "",
        _table(["Category", "Status", "Detail"], summary_rows),
    ]

    if issue_blocks:
        parts += ["", "---", "", "## Issues", ""]
        parts.append("\n\n".join(issue_blocks))

    parts += [
        "",
        "---",
        "",
        "*Generated by [kubebox](https://github.com/rcbassil/kubebox)*",
    ]

    return "\n".join(parts), has_issues
