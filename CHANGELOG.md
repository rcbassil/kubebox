# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added
- **`trace` command** — renders a color-coded Rich dependency tree for any Kubernetes object (`pod`, `deployment`, `statefulset`, `daemonset`, `service`, `ingress`, `pvc`). Walks owner references upward and children downward, surfacing warning events at each level.
- **`vault` command** — diagnoses HashiCorp Vault deployments. Auto-detects namespace via pod labels, checks pod readiness, StatefulSet replica health, and warning events. Detects sealed state via `vault operator status` and prints step-by-step unseal instructions with live unseal progress.
- **Full object listings** — every command now prints a complete table of all scanned objects (healthy and unhealthy, color-coded) after its diagnostic summary.
- **Smart auto-execution** — when a diagnostic tip recommends a `kubectl describe` or `kubectl logs` command, the tool now runs it automatically and prints the output inline.
- **Pre-commit hooks** — `.pre-commit-config.yaml` added with `pre-commit-hooks`, `ruff` linting, and `ruff` formatting.

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
