# happi-agent 0.1

`happi-agent` is a conservative local orchestrator for unattended Codex CLI jobs on
Raspberry Pi 5 Linux. Python owns the control plane; Codex is an untrusted worker
whose output is accepted only after deterministic validation.

## Commands

```text
happi-agent run JOB_ID
happi-agent runs
happi-agent show RUN_ID
```

There are intentionally no publish, commit, retry, delete, garbage-collection or
service-management commands in v0.1.

## Execution model

Each run is inserted into SQLite as `QUEUED`, acquires a process-safe global lock,
checks the kill-switch sentinel, creates a detached Git worktree at the configured
base `HEAD`, runs registered Python collectors, and sends their JSON snapshot to a
fresh `codex app-server --stdio` process. The client negotiates the experimental
0.154.0 API, enumerates and selects the `happi-workspace-only` permission profile,
checks that no instruction source file was loaded, and only then starts the model
turn. The profile denies root reads by default, allows the minimal runtime, grants
write access only to the current worktree, grants read access only to the canonical
`.git` directory, explicitly denies `CODEX_HOME`, and disables tool network access.

The complete standalone 0.154.0 bundle and root-owned Codex config are checked by
path, version, SHA-256, owner and mode. Multi-agent, apps, plugins, hooks,
browser/computer tools, image generation, MCP servers and web search are disabled.
Model-generated commands start from an empty environment with a fixed `PATH`,
`C.UTF-8` locale and `GIT_OPTIONAL_LOCKS=0`. No legacy `--sandbox`,
`sandboxPolicy`, `sandbox_mode` or `sandbox_workspace_write` setting is used.

On Happi, the co-located `/usr/local/bin/codex-code-mode-host` is currently the old
root-owned wrapper. A real post-install canary proved that this is not a credential boundary
for shell tools: Codex 0.154.0 launches `command_execution` through a separate
native-sandbox branch directly from the credential-bearing client. Scheduling
therefore remains deprecated and is not consulted by the new executor. A separate
diagnostic App Server run as `rici` produced `CANARY_DENIED`, establishing the new
mechanism as a candidate. Scheduling remains blocked until the same canary passes
under the installed `happi-agent` UID. See `docs/CREDENTIAL_BOUNDARY.md`.

The Codex client itself still needs outbound connectivity to OpenAI as its control
channel. `network_access=false` applies to commands executed inside the Codex
sandbox, preventing repository code or model-generated shell commands from using
the host network. It is not an air gap for the parent Codex client.

State transitions are checked transactionally. The only states are:

```text
QUEUED -> PREFLIGHT -> PREPARING -> COLLECTING -> RUNNING_AGENT -> VALIDATING
```

Terminal states are `SUCCESS`, `QUARANTINED`, `BLOCKED`, `FAILED` and `TIMEOUT`.
Illegal transitions raise a structured `ILLEGAL_TRANSITION` error.

## Security invariants

- Exactly one run may own the global `flock`; a contender is recorded as `BLOCKED`.
- A configured sentinel blocks new work before preflight.
- A missing, malformed or weakly-permissioned `CANARY_DENIED` operator gate blocks
  every run before Codex is invoked.
- Job files cannot supply commands, argv, shell fragments or collector parameters.
  They may only name collector IDs compiled into the Python registry.
- All orchestrator subprocesses use argv arrays and `shell=False`.
- Codex always has the sacrificial worktree as both cwd and the single dynamic
  runtime workspace root. The profile entry `:workspace_roots = { "." = "write" }`
  is resolved against that exact cwd; no parent worktree directory is writable.
- The canonical repository and shared Git directory are outside every sandbox
  writable root. The worktree `.git` marker is hashed and checked after execution.
- Codex has no approval path, sudo grant, networked tools, MCP, app/plugin or
  multi-agent facility. The prompt also forbids commit, push, PR and merge.
- `HEAD` must remain equal to the recorded base commit. The validator checks
  `git diff --check`, changed-file count, diff size, forbidden/allowed paths, new
  symlinks and unexpected binaries.
- A zero Codex exit with any rejected validation check becomes `QUARANTINED` and
  preserves the whole worktree.
- `SUCCESS` preserves the snapshot, prompt/config hashes, JSONL, stderr, final
  message, validation record and diff, then removes the worktree.
- `FAILED` preserves diagnostics and removes a disposable worktree when possible;
  `TIMEOUT` and `QUARANTINED` preserve it for inspection.
- The runner never commits, pushes, creates a PR, merges, installs systemd units,
  modifies firewall/router settings, or invokes sudo.

The application config is TOML. Job files use a deliberately small YAML subset:
nested mappings and scalar lists only. Anchors, aliases, tags, flow collections,
multiline scalars, tabs, duplicate/unknown keys and arbitrary objects are rejected.
The JSON Schema in `schemas/job.schema.json` documents the same public contract;
runtime enforcement remains in standard-library Python.

## Artifacts and database

SQLite contains `runs`, `events` and `artifacts`. A run records its UUID, job,
timestamps, state, base commit, Codex version, exact prompt SHA-256, resolved config
SHA-256, exit/error codes and workspace path. Artifact rows include path, size and
SHA-256. Files are stored under `state_dir/artifacts/RUN_ID` with mode `0600`.

## Development and tests

Python 3.11 and Git are required. There are no runtime or test dependencies outside
the standard library.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Manual dry-run

The example config points at `../../machine-audits` and writes only under this
project's ignored `.local/` directory. It is intentionally blocked unless its
credential boundary gate exists. Do not fabricate that gate for convenience: use
these commands only after the real Happi canary has passed and the reviewed
deployment boundary is active.

```bash
HAPPI_AGENT_CONFIG="$PWD/config.example.toml" PYTHONPATH="$PWD/src" \
  python3 -m happi_agent run machine-audit-happi
HAPPI_AGENT_CONFIG="$PWD/config.example.toml" PYTHONPATH="$PWD/src" \
  python3 -m happi_agent runs
```

This is a real Codex call but a publication dry-run: no commit or push is performed.
Inspect the run with `happi-agent show RUN_ID` and review `diff.patch`. To test the
kill switch without invoking Codex, create `.local/state/KILL_SWITCH`, run the job,
confirm `BLOCKED`, then remove only that sentinel.

## Residual limits

- Codex CLI's permission-profile Linux sandbox is part of the trust boundary. v0.1 pins 0.154.0; treat
  any upgrade as incompatible until its actual tool-execution topology and a new
  real canary are validated.
- The orchestrator, Codex client and model-controlled commands share an OS UID.
  The new permission profile denied the user credential canary in a real agentic
  diagnostic, but the service-UID deployment has not yet been tested. This open P0
  prevents unattended operation and keeps the operator gate absent.
- stdout/stderr capture is in memory in v0.1; a malicious or defective client could
  cause memory pressure before artifacts are saved.
- Because v0.1 intentionally has no custom execpolicy, it cannot reject a command
  solely because its argv contains `git commit`. A commit to the real worktree is
  blocked by the shared Git directory being outside sandbox write roots; replacing
  `.git` or creating a nested disposable repository is detected by metadata/path
  validation and quarantined, but the attempted local command is not intercepted.
- Collector output describes the host and may contain addresses, mount names or
  service names. Treat artifacts and the prompt sent to OpenAI as sensitive and
  review data-governance requirements before unattended production use.
- This system validates and archives changes; it does not sign artifacts, provide
  remote attestation, publish changes or make legal/compliance determinations.
