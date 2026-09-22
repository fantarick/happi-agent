# Happi Agent v0.2 — Implementation Handoff

## Purpose

This document describes the current reference-implementation draft for
`agentic-dev-playbook/v0.2`.

It replaces the historical v0.1 unattended-Codex handoff as the active
architecture document. The v0.1 experiment remains preserved in Git history and
Draft PR #1.

## Repository state

- Repository: `fantarick/happi-agent`
- Branch: `architecture/playbook-reference-v0.2`
- Base: `main`
- Origin baseline used for the redesign:
  `a97843254d4b83e1941b7d788aa602473e36f306`
- Normative protocol draft: `fantarick/agentic-dev-playbook#1`

## Objective

Happi Agent v0.2 is a deterministic workflow orchestrator. It does not start or
authenticate a coding model.

Its role is to turn the playbook into an auditable state machine around
human-supervised cognitive workers.

## Implemented architecture

```text
Human intent
   ↓
Happi workflow store
   ↓
DISCOVERY_REQUIRED
   ↓
repository discovery evidence
   ↓
CONTRACT_REQUIRED
   ↓
approved structured contract
   ↓
detached Git worktree
   ↓
ENGINEER_REQUIRED
   ↓
human invokes repository engineer
   ↓
structured engineer handoff
   ↓
VERIFYING
   ↓
external deterministic validator
   ↓
REVIEW_REQUIRED
   ↓
independent structured review
   ↓
Happi
   ├─ APPROVE         -> HUMAN_MERGE_REQUIRED
   ├─ REQUEST_CHANGES -> ENGINEER_REQUIRED
   └─ ESCALATE        -> ESCALATED
```

## Components

### `protocol.py`

Implements:

- protocol version constant;
- workflow states;
- allowed transitions;
- iteration counting;
- three-pass circuit breaker;
- strict feature-contract parser;
- strict engineer-handoff parser;
- strict architect-review parser;
- semantic review invariants.

### `workflow.py`

Implements:

- additive SQLite workflow/event/artifact tables;
- content-hashed workflow artifacts;
- intent/discovery/contract ingestion;
- worktree preparation;
- engineer-handoff ingestion;
- deterministic validation routing;
- independent-review ingestion;
- correction routing;
- human-merge completion gate;
- next-action reporting.

### `workspace.py`

Retained from the useful v0.1 deterministic layer:

- detached worktree creation;
- canonical repository preflight;
- worktree-root separation;
- Git common-dir checks;
- worktree cleanup.

### `validator.py`

Retained as the external verification layer:

- Git metadata integrity;
- base HEAD preservation;
- changed-path inspection;
- allow/deny path policy;
- new symlink rejection;
- special-file rejection;
- binary checks;
- `git diff --check`;
- diff size limits;
- deterministic patch generation.

### `config.py`

v0.2 has no model or prompt configuration.

It loads only:

- state directory;
- worktree root;
- canonical repository;
- deterministic validation-policy directory;
- optional lock/kill-switch paths.

Validation profiles are strict JSON documents in `policies/`.

### CLI

Primary commands:

```text
happi-agent workflow start
happi-agent workflow discovery-complete
happi-agent workflow contract
happi-agent workflow engineer-handoff
happi-agent workflow verify
happi-agent workflow review
happi-agent workflow status
happi-agent workflow next
happi-agent workflow list
happi-agent workflow complete
```

There is no v0.2 `run` command.

Read-only `legacy-runs` and `legacy-show` remain solely to inspect historical
v0.1 SQLite records.

## Removed v0.1 execution surfaces

The v0.2 branch removes:

- `src/happi_agent/codex.py`;
- `src/happi_agent/runner.py`;
- direct Codex execution tests;
- unattended-runner tests;
- model prompt/job files;
- `codex_binary` application configuration;
- legacy job schema;
- unattended systemd model-job unit.

No v0.2 control-plane operation requires a ChatGPT/Codex credential.

## Evidence and persistence

The same SQLite database file may contain historical v0.1 tables and new v0.2
workflow tables.

New v0.2 tables:

- `workflows`;
- `workflow_events`;
- `workflow_artifacts`.

Workflow artifacts are stored under the configured state directory with SHA-256,
size and path metadata recorded in SQLite.

## Circuit breaker

Default:

```text
MAX_AGENT_ITERATIONS = 3
```

An iteration is consumed only when a valid engineer handoff advances
`ENGINEER_REQUIRED -> VERIFYING`.

If deterministic verification or independent review asks for another correction
after the limit, Happi transitions to `ESCALATED`.

## Human authority

An architect `APPROVE` verdict can move only to:

`HUMAN_MERGE_REQUIRED`

Completion requires a separately observed canonical-repository merge commit.

## Security boundary

The v0.2 security problem is intentionally simpler than v0.1:

- Happi owns no model credential;
- no model process runs under the service identity;
- no model sandbox is part of the v0.2 control-plane trust boundary;
- invalid state transitions fail closed;
- malformed/semantically inconsistent handoffs fail closed;
- kill switch and global process lock remain external deterministic controls.

The credential-boundary work from v0.1 is retained as research evidence, not as a
runtime dependency.

## Conformance

See `docs/CONFORMANCE.md`.

Current CI evidence on the redesign branch has remained green through the removal
of the model executor and unattended runner. The exact final test count must be
read from the CI run associated with the current branch HEAD.

## Remaining work before release

- pin the exact merged playbook protocol commit;
- execute one operator-visible manual workflow against a disposable repository;
- inspect the resulting SQLite events and hashed artifacts;
- independently review the final diff;
- make the human merge decision;
- design deployment separately from model invocation.

## Non-goals for v0.2

- automatic Codex/ChatGPT invocation;
- service-owned model credentials;
- automatic correction loops;
- automatic merge;
- automatic deployment;
- product decisions made by the orchestrator.
