# happi-agent 0.2 (draft)

`happi-agent` is the first deterministic reference implementation of
`agentic-dev-playbook/v0.2`.

The v0.2 goal is not to run a coding model unattended. Happi implements the
control plane around human-supervised cognitive workers: workflow state,
worktree preparation, evidence, deterministic verification, structured handoffs,
circuit breakers, escalation and next-action gates.

## Repository relationship

```text
agentic-dev-playbook
        │
        │ normative protocol
        ▼
    happi-agent
        │
        │ reference implementation
        ▼
 project repositories
```

The playbook and Happi remain separate repositories. The playbook is
tool-agnostic; Happi is a concrete Python/Linux/Git implementation. Releases
declare the protocol version they implement instead of using a Git submodule.

## v0.2 protocol states

```text
INTENT
  ↓
DISCOVERY_REQUIRED
  ↓
CONTRACT_REQUIRED
  ↓
CONTRACT_READY
  ↓
ENGINEER_REQUIRED
  ↓
VERIFYING
  ↓
REVIEW_REQUIRED
  ├── APPROVE ─────────► HUMAN_MERGE_REQUIRED
  ├── REQUEST_CHANGES ─► CHANGES_REQUIRED ─► ENGINEER_REQUIRED
  └── ESCALATE ────────► ESCALATED
```

`BLOCKED`, `ESCALATED`, and `COMPLETE` are terminal workflow states.

The default engineering circuit breaker is:

```text
MAX_AGENT_ITERATIONS = 3
```

## What Happi owns

- deterministic protocol state and transitions;
- repository/worktree lifecycle;
- persistent audit trail;
- deterministic validation and evidence;
- structured engineer/reviewer handoff validation;
- iteration limits;
- kill switch and global lock;
- quarantine/block/escalation;
- next-action reporting.

## What Happi does not own in v0.2

- ChatGPT or Codex credentials;
- unattended model invocation;
- product decisions;
- reviewer reasoning;
- autonomous merge;
- autonomous deployment.

The human invokes the repository engineer and independent reviewer at explicit
workflow boundaries.

## Protocol kernel

The first v0.2 executable kernel lives in:

```text
src/happi_agent/protocol.py
tests/test_protocol.py
```

It implements the playbook state machine, strict transition checks, the
three-iteration circuit breaker, and strict parsing of machine-readable engineer
handoffs and architect reviews.

Project-local redesign rules live under `.ai/`.

## Migration from v0.1

This branch intentionally starts at commit:

`a97843254d4b83e1941b7d788aa602473e36f306`

That point retains the useful deterministic v0.1 foundation while predating most
of the credential-boundary hardening experiment.

The following v0.1 components are candidates for reuse:

- SQLite state/audit trail;
- global lock and kill switch;
- worktree manager;
- validators;
- artifact hashing;
- quarantine semantics;
- CI and tests.

The old direct Codex execution path is **legacy during the migration**. It is not
the desired v0.2 architecture and must not be deployed as the new reference
implementation merely because it still exists in this transitional branch.

See `docs/PLAYBOOK_REFERENCE_IMPLEMENTATION.md`.

## Development checks

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check
```

## Safety defaults

```text
MAX_AGENT_ITERATIONS = 3
HUMAN_MERGE_REQUIRED = true
AUTONOMOUS_AGENT_INVOCATION = false
AUTONOMOUS_DEPLOY = false
DESTRUCTIVE_ACTIONS_REQUIRE_APPROVAL = true
```

## Current status

This is an architectural migration branch, not a production release.

The historical unattended-Codex experiment remains in Draft PR #1 and its
security evidence should be preserved rather than rewritten as if it never
happened.
