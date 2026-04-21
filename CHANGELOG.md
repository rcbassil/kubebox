# Changelog

All notable changes to this project are documented here.

## [0.8.0] — 2026-04-21

### Added
- **`ask` command** — gathers live pod and Warning event diagnostics, then streams an AI root-cause analysis from Claude (`claude-opus-4-7`) in response to a plain-English question. Supports `-n` for namespace scoping. Requires `ANTHROPIC_API_KEY`.
- **`logs --analyze` / `-a` flag** — after fetching logs, sends the output to Claude for root-cause analysis and streams the response inline.
- **`core/ai.py`** — new module housing the Anthropic SDK integration. System prompt is prompt-cached for efficiency. Streams responses token-by-token. Handles auth, connection, and API errors with clear messages.

### Dependencies
- Added `anthropic>=0.96.0`.

## [0.7.0] — 2026-04-20

### Added
- **`deployments` command** — scans all namespaces (or a specific namespace with `-n`) for degraded deployments. Shows a failing table with Ready/Desired counts and a diagnostic tip for the first offender, then a full table of every deployment with Ready/Desired, Up-to-date, Available, and Age columns.
- **Dashboard `run` entry** — a "run any command…" item pinned at the bottom of the TUI command list. Selecting it opens the input bar empty so any kubebox command (with arguments) can be typed and executed directly. Non-kubebox input is rejected with a clear error and the list of valid commands.

## [0.6.0] — 2026-04-20

### Added
- **`dashboard` command** — full-screen TUI built with Textual. Left panel lists all commands alphabetically; right panel shows scrollable Rich output. Commands with required arguments open an inline input bar pre-filled with a usage hint. Keybindings: `s` focus output, `l` focus list, `Esc` cancel input, `q` quit.
- **`interactive` command** — REPL shell with tab-completion and persistent history (`~/.k8s_tool_history`).
- **`events` command** — browse and filter Kubernetes events by namespace (`-n`), type (`--type`), reason (`--reason`), and age (`--since`, e.g. `30m`, `2h`, `1d`).
- **`network` command** — checks CoreDNS health, services with no ready endpoints, and NetworkPolicy coverage.
- **`rbac` command** — surfaces Forbidden/Unauthorized events, ServiceAccounts with no role bindings, and a full role binding summary.
- **`all` command expanded** — now checks Services (no-endpoint detection), Ingresses, Jobs & CronJobs (failed job detection), HPAs, PersistentVolumes, Namespaces (stuck-terminating detection), ConfigMaps, and Secrets (names and types only — values never shown), in addition to the existing Nodes, PVCs, and Workloads checks.
- **DaemonSets** added to workload checks in the `all` command.
- **Alphabetical command ordering** — both the CLI help output and the TUI command list are sorted alphabetically.
- **`-h` alias** for `--help` on all commands.

### Removed
- **`verify-readonly` command** — removed; the read-only safeguard in `run_cmd` remains in place.
- **`--install-completion` / `--show-completion`** flags removed (`add_completion=False`).

### Changed
- App renamed from `k8s-assist` / `kubemyriad` to **`kubebox`** everywhere (binary name, TITLE, prompt, `pyproject.toml`).
- PyInstaller spec updated to `kubebox.spec` producing `dist/kubebox`.

### Fixed
- `_check_nodes` no longer iterates the node list twice; ready-state is computed once into a dict.
- `_check_workloads` color markup no longer uses unreadable nested ternary f-strings.
- Stale numbered check comments removed from `check_all_objects`.
- Stale inline step-comment blocks removed from `interactive()`.

## [0.5.0] — 2026-04-19

### Added
- **`crd` command** — discovers all Custom Resource Definitions, fetches their instances cluster-wide or in a specific namespace, and surfaces any with `Ready=False` (or `Available`, `Synced`, `Healthy`) conditions. Shows a summary table grouped by CRD and namespace, plus a detailed failing-instances table with truncated messages.

### Fixed
- **crd.py** — CRD spec key access (`spec.names.plural`, `spec.group`) is now wrapped in `try/except (KeyError, TypeError)` to skip malformed CRD objects instead of crashing.
- **crd.py** — Removed unused `kind` variable from the failing-instances tip section.
- **vault.py** — `kubectl exec` commands in unseal instructions now use `shlex.quote()` for pod and namespace names, preventing shell injection with unusual names.
- **kong.py** — `kubectl get` tip command now uses `shlex.quote()` for the namespace argument.
- **utils.py** — Fixed potential `IndexError` when `-n` appears as the last element of a command list in the pod-restart recovery path.

## [0.4.0] — 2026-04-19

### Added
- **Last Seen column on warning event tables** — both the cluster-wide events table (`all` command) and the Vault events table now show a human-readable age (e.g. `3m`, `2h`, `4d`) derived from `lastTimestamp`.

### Fixed
- **vault.py** — `vault-agent-injector` pods are now excluded from Vault diagnostics; previously the injector sidecar pods were incorrectly included in the server pod scan and unseal checks.
- **vault.py** — HA unseal Step 2 now lists only the other *sealed* (unhealthy) pods instead of all vault pods including already-healthy replicas.
- **utils.py** — when `kubectl logs` fails with a `NotFound` error (e.g. after a rollout restart), the tool now automatically looks up the replacement running pod that shares the same deployment base name and retries the log command against it, instead of showing an error.

## [0.3.0] — 2026-04-19

### Security
- Replaced direct `subprocess.run()` calls in `vault.py` and `kustomize.py` with a new `run_cmd_allow_fail()` utility that applies the same mutative-command safety check as `run_cmd()` before executing. Previously those code paths bypassed the read-only safeguard entirely.
- Replaced `command.split()` with `shlex.split()` in `print_tip()` so commands containing quoted arguments or spaces in resource names are parsed correctly and cannot be misinterpreted.

### Fixed
- **kubernetes.py** — Removed redundant `dict()` cast when reading a terminated container's exit reason; now uses `.reason or "Error"` directly.
- **kubernetes.py** — Events tip command now uses `-A` (all namespaces) when no namespace is specified, instead of incorrectly defaulting to `-n default`.
- **kubernetes.py** — Message truncation in the events table no longer appends `"..."` to messages that are already under 100 characters.
- **kubernetes.py** — JSON decode failure on the events response now prints a yellow warning instead of silently doing nothing.
- **kubernetes.py** — `first_degraded` unpacking is now guarded with a null check, preventing a `TypeError` crash on edge-case API responses.
- **kubernetes.py** — Ready replica comparisons now use `(ready_replicas or 0)` to prevent false degraded alerts when the field is `None`.
- **vault.py** — Sealed detection replaced fragile exact-whitespace string match with `re.search(r"Sealed\s+true", out)`.
- **vault.py** — Unseal instructions no longer hardcode `vault-1` / `vault-2`; replica pod names are now derived dynamically from the live StatefulSet pods.
- **vault.py** — Pod names and namespace are single-quoted in the displayed `kubectl exec` commands so they remain valid with unusual names.
- **kustomize.py** — Local path is validated with `os.path.isdir()` before running `kubectl kustomize`, providing a clear error instead of a confusing subprocess failure.
- **kustomize.py** — Error log parsing no longer applies `.lower()` to JSON-structured log lines, which was breaking the `"level":"error"` match.
- **kustomize.py** — Added `if not logs: continue` guard before `.split("\n")` to prevent `AttributeError` when the pod log API returns `None`.
- **flux.py** — `kubectl describe` tip now uses an explicit `_RESOURCE_KIND_MAP` instead of fragile string transformation.
- **flux.py** — JSON decode failure now prints a yellow warning instead of silently returning.
- **helm.py** — `print_tip()` is now guarded with `if first_bad:` to prevent `AttributeError` on the edge case where the failing loop never sets it.
- **trace.py** — `rule.http.paths` is now accessed as `rule.http.paths or []` to prevent `TypeError` when the field is `None` on an Ingress rule.
- **trace.py** — All bare `except Exception: pass` blocks now surface the error message into the tree node so failures are visible instead of silently swallowed.
- **kong.py** — Added `if not logs: continue` guard before `.split("\n")`.
- **kong.py** — Log-read failure message elevated from `[dim]` (nearly invisible) to `[yellow]`.
- **kong.py** — Namespace is single-quoted in the displayed `kubectl get` tip command.

## [0.2.0] — 2026-04-19

### Added
- **`trace` command** — renders a color-coded Rich dependency tree for any Kubernetes object (`pod`, `deployment`, `statefulset`, `daemonset`, `service`, `ingress`, `pvc`). Walks owner references upward and children downward, surfacing warning events at each level.
- **`vault` command** — diagnoses HashiCorp Vault deployments. Auto-detects namespace via pod labels, checks pod readiness, StatefulSet replica health, and warning events. Detects sealed state via `vault status` and prints step-by-step unseal instructions with live unseal progress.
- **Full object listings** — every command now prints a complete table of all scanned objects (healthy and unhealthy, color-coded) after its diagnostic summary.
- **Smart auto-execution** — when a diagnostic tip recommends a `kubectl describe` or `kubectl logs` command, the tool runs it automatically and prints the output inline.
- **Event-driven command suggestions** — the `all` command now analyzes warning events and emits up to 5 targeted, deduplicated tips after the events table. Covered reasons: `BackOff`, `OOMKilling`, `Unhealthy`, `FailedScheduling`, `FailedMount`, `FailedAttachVolume`, `FailedBinding`, `Evicted`, `NodeNotReady`, `Failed`.
- **Pre-commit hooks** — `.pre-commit-config.yaml` with `pre-commit-hooks`, `ruff` linting, and `ruff` formatting.

### Changed
- **`cluster` command renamed to `pods`** for clarity.
- Degraded workload tips now use real resource names and namespaces instead of `<name>` / `<namespace>` placeholders.

### Fixed
- Vault sealed detection was using log-string matching, which never fires because Vault does not log sealed state repeatedly. Replaced with `kubectl exec -- vault status` (handles exit code 2 correctly via a non-raising subprocess call).

## [0.1.0] — Initial Release

### Added
- **`cluster` command** — scans all namespaces for pods in `CrashLoopBackOff`, `ImagePullBackOff`, `Pending`, or `Error` states.
- **`all` command** — full cluster diagnostic covering Nodes, PVCs, Deployments, StatefulSets, and cluster-wide Warning events.
- **`flux` command** — checks FluxCD `GitRepository`, `Kustomization`, and `HelmRelease` objects for `Ready=False` status.
- **`helm` command** — finds Helm releases in non-`deployed` states.
- **`kong` command** — scans Kong Ingress Controller proxy logs for errors.
- **`kustomize` command** — parses kustomize-controller logs for errors; supports local `kustomize build` dry-run validation.
- **`describe` command** — safe, syntax-highlighted wrapper around `kubectl describe`.
- **`logs` command** — safe wrapper around `kubectl logs` with tail and previous-container support.
- **`verify-readonly` command** — confirms mutative kubectl commands are blocked.
- Read-only safeguard in `run_cmd` that blocks `apply`, `create`, `delete`, `edit`, `patch`, `scale`, and `replace`.
- PyInstaller support for building a standalone binary (`dist/k8s-assist`).
