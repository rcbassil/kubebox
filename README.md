# Kubernetes CLI Toolbox

A standalone, read-only Python CLI designed to act as a DevOps/SRE assistant for troubleshooting Kubernetes clusters. It automatically gathers diagnostics, analyzes states, and highlights failures across Kubernetes, FluxCD, Kong, Helm, and HashiCorp Vault without making any modifications to the cluster.

> **Smart auto-execution:** When a diagnostic tip suggests a `kubectl describe` or `kubectl logs` command, the tool runs it automatically and prints the output inline — no copy-pasting required.

> **Full object listings:** Every command shows a complete table of all scanned objects (healthy and unhealthy) after its diagnostic summary, so you always have the full picture.

> **Event-driven suggestions:** The `all` command analyzes warning events and automatically emits targeted command suggestions (with auto-execution for `describe` and `logs`) for each actionable issue found.

## Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (for dependency management)
- `kubectl` configured and authenticated against your target cluster
- `helm` CLI (if you intend to use the Helm diagnostics)

## Installation

1. Clone or navigate to the repository directory.
2. Sync the virtual environment:

```bash
uv sync
```

## Running

```bash
uv run main.py --help
```

## Building a Standalone Executable

Package the toolbox into a single binary using [PyInstaller](https://pyinstaller.org/):

```bash
# Install PyInstaller (once)
uv add --dev pyinstaller

# Build
uv run pyinstaller --onefile --name k8s-assist main.py

# Run or install globally
./dist/k8s-assist --help
sudo mv ./dist/k8s-assist /usr/local/bin/k8s-assist
```

## Commands

### 1. `pods` — Scan for Pod Failures

Scans all namespaces for pods in `CrashLoopBackOff`, `ImagePullBackOff`, `Pending`, or `Error` states, then prints a full listing of every pod with its status.

```bash
uv run main.py pods
uv run main.py pods -n my-app-namespace
```

### 2. `all` — Full Cluster Diagnostic

Checks Nodes (NotReady), PVCs (Unbound), and Workloads (desired vs ready replicas), then scans cluster-wide Warning events. Prints a complete listing of all nodes, PVCs, and workloads after each section. The warning events table includes a **Last Seen** column showing human-readable ages (`3m`, `2h`, `4d`). After displaying warning events, emits up to 5 targeted command suggestions based on the event reasons (`BackOff`, `OOMKilling`, `FailedScheduling`, `FailedMount`, `Unhealthy`, `Evicted`, `NodeNotReady`, etc.).

```bash
uv run main.py all
uv run main.py all -n my-app-namespace
```

### 3. `trace` — Object Dependency Tree

Walks the full Kubernetes dependency chain for any object and renders it as a color-coded tree. Navigates upward (owner references) and downward (ReplicaSets → Pods → containers → Services) and surfaces warning events at each level.

Supported kinds: `pod`, `deployment`, `statefulset`, `daemonset`, `service` / `svc`, `ingress` / `ing`, `pvc` / `persistentvolumeclaim`.

```
╭─ Object Trace — Deployment/my-app ──────────────────────────────╮
│ ✗ Deployment/my-app (0/3 ready)                                  │
│ ├── ✗ ReplicaSet/my-app-abc123 (0/3 ready)                       │
│ │   ├── ✗ Pod/my-app-abc123-xyz (CrashLoopBackOff)               │
│ │   │   ├── ✗ container/app — CrashLoopBackOff (restarts: 7)     │
│ │   │   └── Warning Events                                        │
│ │   │       └── BackOff: Back-off restarting failed container     │
│ └── ⚠ Service/my-app (ClusterIP, 0 ready / 3 not-ready)         │
╰──────────────────────────────────────────────────────────────────╯
```

```bash
uv run main.py trace deployment my-app -n prod
uv run main.py trace ingress my-ingress -n prod
uv run main.py trace pvc my-claim -n prod
uv run main.py trace pod my-crashing-pod-xyz -n prod
```

### 4. `flux` — FluxCD Synchronization

Scans `GitRepository`, `Kustomization`, and `HelmRelease` objects for `Ready=False` status. Lists all Flux resources with their Ready state after each check.

```bash
uv run main.py flux
```

### 5. `helm` — Helm Releases

Finds releases not in `deployed` state (e.g. `failed`, `pending-install`, `pending-upgrade`). Prints a full listing of all releases with status, chart, and app version.

```bash
uv run main.py helm
uv run main.py helm -n ingress-nginx
```

### 6. `vault` — HashiCorp Vault

Locates Vault **server** pods automatically by label (`app.kubernetes.io/name=vault` or `app=vault`), excluding injector sidecars. Checks pod readiness, StatefulSet replica health, and warning events (with **Last Seen** ages). If Vault is **sealed**, detects it via `vault status`, shows current unseal progress, and prints step-by-step unseal instructions listing only the other sealed replicas for HA deployments.

```bash
uv run main.py vault
uv run main.py vault -n vault-system
```

### 7. `kong` — Kong Ingress Controller

Scans Kong proxy pod logs for `[error]` and `level=error` entries. Lists all Kong pods with their phase and readiness.

```bash
uv run main.py kong
```

### 8. `kustomize` — Kustomize Controller

Parses `kustomize-controller` logs for `level=error` entries to surface GitOps sync failures. Optionally runs a local `kustomize build` dry-run. Lists all controller pods at the end.

```bash
uv run main.py kustomize
uv run main.py kustomize -n custom-flux-system
uv run main.py kustomize -b ./clusters/my-local-cluster
```

### 9. `crd` — Custom Resource Definitions

Discovers all CRDs in the cluster, fetches their instances, and surfaces any with non-ready conditions (`Ready`, `Available`, `Synced`, or `Healthy` = `False`). Shows a summary table grouped by CRD and namespace, then a detailed failing-instances table with condition messages.

```bash
uv run main.py crd
uv run main.py crd -n my-namespace
```

### 11. `describe` — Safe Describe Wrapper

Fetches and syntax-highlights the describe output of any Kubernetes object.

```bash
uv run main.py describe deployment frontend -n prod
uv run main.py describe node my-node
```

### 12. `logs` — Safe Logs Wrapper

Fetches and prints logs for any pod or deployment. Supports tail size and previous-container flags.

```bash
uv run main.py logs my-crashing-pod-123 -n prod
uv run main.py logs my-crashing-pod-123 -n prod -t 50 -p
```

### 13. `verify-readonly` — Read-Only Safeguard Check

Confirms that the internal `run_cmd` utility blocks mutative commands (`apply`, `delete`, `patch`, etc.).

```bash
uv run main.py verify-readonly
```
