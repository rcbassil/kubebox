import os

import anthropic

from core.utils import console

_SYSTEM_PROMPT = """You are an expert Kubernetes SRE and DevOps engineer embedded in kubebox, a read-only Kubernetes diagnostic CLI.

You receive diagnostic output from a live cluster (pod statuses, events, deployment states, logs, etc.) and answer the user's question based on that data.

Your responsibilities:
- Identify root causes of issues from the diagnostic data provided
- Explain clearly what is wrong and why
- Suggest specific, actionable next steps
- Flag any additional concerns visible in the data, even if not directly asked about

Constraints:
- kubebox is strictly read-only — never suggest commands that modify the cluster (no apply, delete, patch, scale, edit, replace, create)
- Limit kubectl suggestions to read-only operations: describe, logs, get, top, explain
- Be concise and direct — this is a terminal tool, not a web UI
- Do not use markdown headers or decorative formatting; plain paragraphs work best in a terminal
- If the diagnostic output shows no issues, say so clearly"""


def _get_client() -> anthropic.Anthropic | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print(
            "[bold red]Error:[/bold red] ANTHROPIC_API_KEY environment variable is not set.\n"
            "Export it with: export ANTHROPIC_API_KEY=your-key"
        )
        return None
    return anthropic.Anthropic(api_key=api_key)


def ask(question: str, context: str) -> None:
    """Send diagnostic context and a question to Claude and stream the response."""
    client = _get_client()
    if not client:
        return

    user_content = (
        f"Cluster diagnostic output:\n\n{context}\n\nQuestion: {question}"
        if context.strip()
        else question
    )

    console.print("\n[bold cyan]AI Analysis[/bold cyan]\n")
    try:
        with client.messages.stream(
            model="claude-opus-4-7",
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
        print()
    except anthropic.AuthenticationError:
        console.print("[bold red]Error:[/bold red] Invalid ANTHROPIC_API_KEY.")
    except anthropic.APIConnectionError:
        console.print(
            "[bold red]Error:[/bold red] Could not connect to Anthropic API. Check your internet connection."
        )
    except anthropic.APIStatusError as e:
        console.print(f"[bold red]API error {e.status_code}:[/bold red] {e.message}")


def analyze_logs(logs_output: str, resource: str) -> None:
    """Send captured log output to Claude for root-cause analysis."""
    ask(
        question="Analyze these logs for errors, warnings, and root causes. What is happening and what should I investigate next?",
        context=f"Logs for {resource}:\n\n{logs_output}",
    )
