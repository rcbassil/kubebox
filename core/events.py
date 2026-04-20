import re
from datetime import datetime, timedelta, timezone

from kubernetes import client, config
from rich.table import Table

from core.utils import console, fmt_age

_SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


def _parse_since(since: str) -> datetime | None:
    if not since:
        return None
    m = re.fullmatch(r"(\d+)([smhd])", since.strip().lower())
    if not m:
        console.print(
            f"[yellow]Could not parse --since '{since}'. Expected format: 30m, 2h, 1d[/yellow]"
        )
        return None
    value, unit = int(m.group(1)), m.group(2)
    delta = {
        "s": timedelta(seconds=value),
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
    }[unit]
    return datetime.now(timezone.utc) - delta


def _event_ts(e) -> datetime:
    t = e.last_timestamp or e.metadata.creation_timestamp
    return t if t else datetime.min.replace(tzinfo=timezone.utc)


def check_events(
    namespace: str = None,
    event_type: str = None,
    reason: str = None,
    since: str = None,
):
    """List and filter Kubernetes events with optional type, reason, and age filters."""
    try:
        config.load_kube_config()
    except Exception as e:
        console.print(f"[bold red]Failed to load kube config:[/bold red] {e}")
        return

    v1 = client.CoreV1Api()
    cutoff = _parse_since(since)

    try:
        if namespace:
            events = v1.list_namespaced_event(namespace).items
        else:
            events = v1.list_event_for_all_namespaces().items
    except Exception as e:
        console.print(f"[bold red]Error fetching events:[/bold red] {e}")
        return

    if event_type:
        events = [e for e in events if (e.type or "").lower() == event_type.lower()]
    if reason:
        events = [e for e in events if reason.lower() in (e.reason or "").lower()]
    if cutoff:
        events = [e for e in events if _event_ts(e) >= cutoff]

    events.sort(key=_event_ts, reverse=True)

    warning_count = sum(1 for e in events if e.type == "Warning")
    normal_count = sum(1 for e in events if e.type == "Normal")

    if not events:
        console.print("[green]✓ No events found matching the given filters.[/green]")
        return

    console.print(
        f"[dim]Found {len(events)} events — {warning_count} Warning, {normal_count} Normal[/dim]\n"
    )

    table = Table(
        title=f"Kubernetes Events ({len(events)})",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Age", style="dim", no_wrap=True)
    if not namespace:
        table.add_column("Namespace", style="cyan")
    table.add_column("Type", no_wrap=True)
    table.add_column("Reason", style="yellow")
    table.add_column("Object", style="blue")
    table.add_column("Count", justify="right", style="dim")
    table.add_column("Message")

    for e in events:
        ts = e.last_timestamp or e.metadata.creation_timestamp
        age = fmt_age(ts.isoformat() if ts else "")
        evt_type = e.type or "?"
        type_str = (
            f"[red]{evt_type}[/red]"
            if evt_type == "Warning"
            else f"[green]{evt_type}[/green]"
        )
        obj = (
            f"{e.involved_object.kind}/{e.involved_object.name}"
            if e.involved_object
            else "?"
        )
        msg = e.message or ""
        msg_display = msg[:120] + "..." if len(msg) > 120 else msg
        count = str(e.count or 1)

        if namespace:
            table.add_row(age, type_str, e.reason or "?", obj, count, msg_display)
        else:
            table.add_row(
                age,
                e.metadata.namespace or "?",
                type_str,
                e.reason or "?",
                obj,
                count,
                msg_display,
            )

    console.print(table)
