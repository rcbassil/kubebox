import json
import subprocess
from rich.table import Table
from rich.panel import Panel
from core.utils import run_cmd, console, print_tip


def _run_allow_fail(cmd: list[str]) -> str:
    """Run a command and return stdout even when exit code is non-zero."""
    result = subprocess.run(cmd, text=True, capture_output=True)
    return result.stdout


_VAULT_LABEL_SELECTORS = [
    "app.kubernetes.io/name=vault",
    "app=vault",
]


def _find_vault_namespace() -> str | None:
    """Scan all namespaces for Vault pods using common label selectors."""
    for selector in _VAULT_LABEL_SELECTORS:
        out = run_cmd(["kubectl", "get", "pods", "-A", "-l", selector, "-o", "json"])
        if out:
            try:
                items = json.loads(out).get("items", [])
                if items:
                    return items[0]["metadata"]["namespace"]
            except (json.JSONDecodeError, KeyError):
                continue
    return None


def check_vault_status(namespace: str = None):
    """Scan Vault pods and StatefulSet for seal status, readiness, and errors."""
    ns = namespace or _find_vault_namespace()

    if not ns:
        console.print(
            "[bold red]Could not locate Vault pods in the cluster. Is Vault installed?[/bold red]"
        )
        return

    console.print(f"[bold blue]Checking Vault in namespace '{ns}'...[/bold blue]")

    _check_vault_pods(ns)
    _check_vault_statefulset(ns)
    _check_vault_events(ns)


def _check_vault_pods(ns: str):
    out = run_cmd(["kubectl", "get", "pods", "-n", ns, "-o", "json"])
    if not out:
        return

    try:
        pods = json.loads(out).get("items", [])
    except json.JSONDecodeError:
        return

    vault_pods = [p for p in pods if "vault" in p["metadata"]["name"]]
    if not vault_pods:
        console.print(f"[yellow]No Vault pods found in namespace '{ns}'[/yellow]")
        return

    table = Table(title="Vault Pods", show_header=True, header_style="bold magenta")
    table.add_column("Pod", style="blue")
    table.add_column("Phase", style="cyan")
    table.add_column("Ready", justify="center")
    table.add_column("Restarts", justify="right", style="yellow")

    issues = []
    for pod in vault_pods:
        name = pod["metadata"]["name"]
        phase = pod.get("status", {}).get("phase", "Unknown")
        restarts = 0
        ready_count = 0
        total = 0

        for cs in pod.get("status", {}).get("containerStatuses", []):
            restarts += cs.get("restartCount", 0)
            total += 1
            if cs.get("ready"):
                ready_count += 1

        ready_str = f"{ready_count}/{total}"
        is_healthy = phase == "Running" and ready_count == total

        phase_str = f"[green]{phase}[/green]" if is_healthy else f"[red]{phase}[/red]"
        ready_display = (
            f"[green]{ready_str}[/green]" if is_healthy else f"[red]{ready_str}[/red]"
        )

        table.add_row(name, phase_str, ready_display, str(restarts))

        if not is_healthy:
            issues.append((name, ns))

    console.print(table)

    if issues:
        pod_name, pod_ns = issues[0]
        if _is_pod_sealed(pod_name, pod_ns):
            _print_unseal_tips(pod_name, pod_ns)
        else:
            print_tip(
                "A Vault pod that is not Ready is typically sealed, initializing, or has lost quorum with other cluster members.",
                f"kubectl logs {pod_name} -n {pod_ns}",
            )
    else:
        console.print("[green]✓ All Vault pods are Running and Ready[/green]")


def _is_pod_sealed(pod_name: str, ns: str) -> bool:
    """Return True if vault status reports Sealed: true (exits 2 when sealed, hence allow_fail)."""
    out = _run_allow_fail(
        ["kubectl", "exec", "-n", ns, pod_name, "--", "vault", "status"]
    )
    return "Sealed             true" in out


def _print_unseal_tips(pod_name: str, ns: str):
    status_out = _run_allow_fail(
        ["kubectl", "exec", "-n", ns, pod_name, "--", "vault", "status"]
    )
    progress_line = next(
        (line for line in status_out.splitlines() if "Unseal Progress" in line), None
    )
    threshold_line = next(
        (line for line in status_out.splitlines() if "Threshold" in line), None
    )

    progress_info = ""
    if progress_line and threshold_line:
        progress_info = f"\n[cyan]Current status → {progress_line.strip()}  |  {threshold_line.strip()}[/cyan]\n"

    content = (
        "[bold red]Vault is Sealed[/bold red]\n"
        + progress_info
        + "\nData is inaccessible until enough unseal keys (or a cloud auto-unseal) are applied.\n\n"
        "[bold yellow]Step 1:[/bold yellow] Apply an unseal key (repeat until the threshold is met):\n"
        f"[bold green]> kubectl exec -n {ns} {pod_name} -- vault operator unseal <unseal-key>[/bold green]\n\n"
        "[bold yellow]Step 2:[/bold yellow] For HA clusters, unseal each replica individually:\n"
        f"[bold green]> kubectl exec -n {ns} vault-1 -- vault operator unseal <unseal-key>[/bold green]\n"
        f"[bold green]> kubectl exec -n {ns} vault-2 -- vault operator unseal <unseal-key>[/bold green]\n\n"
        "[dim]If you use auto-unseal (AWS KMS, GCP CKMS, Azure Key Vault), check that the IAM/service account permissions are intact.[/dim]"
    )
    console.print(Panel(content, border_style="red", title="Unseal Instructions"))


def _check_vault_statefulset(ns: str):
    out = run_cmd(["kubectl", "get", "statefulsets", "-n", ns, "-o", "json"])
    if not out:
        return

    try:
        items = json.loads(out).get("items", [])
    except json.JSONDecodeError:
        return

    vault_sts = [s for s in items if "vault" in s["metadata"]["name"]]
    if not vault_sts:
        return

    console.print("\n[bold blue]Vault StatefulSet Replica Status:[/bold blue]")
    degraded = False
    for sts in vault_sts:
        name = sts["metadata"]["name"]
        desired = sts.get("spec", {}).get("replicas", 0)
        ready = sts.get("status", {}).get("readyReplicas", 0)

        if ready != desired:
            console.print(f"[red]⚠  {name}: {ready}/{desired} replicas ready[/red]")
            degraded = True
            print_tip(
                f"StatefulSet '{name}' has fewer ready replicas than desired. This often means some pods are sealed or in a crash loop.",
                f"kubectl describe statefulset {name} -n {ns}",
            )
        else:
            console.print(f"[green]✓ {name}: {ready}/{desired} replicas ready[/green]")

    if not degraded:
        console.print("[green]✓ All Vault StatefulSets have desired replicas[/green]")


def _check_vault_events(ns: str):
    console.print("\n[bold blue]Recent Warning Events in Vault Namespace:[/bold blue]")
    out = run_cmd(
        [
            "kubectl",
            "get",
            "events",
            "-n",
            ns,
            "--field-selector",
            "type=Warning",
            "--sort-by=.metadata.creationTimestamp",
            "-o",
            "json",
        ]
    )
    if not out:
        return

    try:
        items = json.loads(out).get("items", [])
    except json.JSONDecodeError:
        return

    if not items:
        console.print("[green]✓ No Warning events in the Vault namespace[/green]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Object", style="blue")
    table.add_column("Reason", style="yellow")
    table.add_column("Message", style="white")

    for e in items[-20:]:
        obj = f"{e.get('involvedObject', {}).get('kind', '?')}/{e.get('involvedObject', {}).get('name', '?')}"
        msg = e.get("message", "")
        table.add_row(
            obj, e.get("reason", ""), msg[:120] + "..." if len(msg) > 120 else msg
        )

    console.print(table)
