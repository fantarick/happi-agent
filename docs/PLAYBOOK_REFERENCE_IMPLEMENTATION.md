# Happi Agent as playbook reference implementation

## Relationship

`agentic-dev-playbook` is the normative methodology and protocol.

`happi-agent` is the first executable reference implementation of the deterministic orchestration layer.

The repositories remain physically separate so the playbook can stay tool-agnostic and Happi can evolve as a Python/Linux/GitHub implementation. They are version-linked rather than merged into a monorepo.

## v0.2 boundary

Happi v0.2 owns:

- workflow state;
- allowed transitions;
- worktree lifecycle;
- durable evidence;
- deterministic verification;
- handoff validation;
- iteration limits;
- block/escalate/quarantine behavior;
- next-action reporting.

Happi v0.2 does not own:

- ChatGPT/Codex authentication;
- unattended model invocation;
- product decisions;
- independent review reasoning;
- merge authority.

## Intended loop

```text
Human intent
   ↓
Happi: state + contract gate
   ↓
ENGINEER_REQUIRED
   ↓
Human invokes repository engineer
   ↓
structured ENGINEER_HANDOFF
   ↓
Happi: deterministic verification
   ↓
REVIEW_REQUIRED
   ↓
Independent reviewer
   ↓
structured ARCHITECT_REVIEW
   ↓
Happi
   ├─ REQUEST_CHANGES -> ENGINEER_REQUIRED (within circuit breaker)
   ├─ ESCALATE        -> ESCALATED
   └─ APPROVE         -> HUMAN_MERGE_REQUIRED
```

## Migration from v0.1

The branch starts from the last pre-auth-hardening implementation baseline `a97843254d4b83e1941b7d788aa602473e36f306`.

Useful v0.1 components should be retained:

- SQLite audit trail;
- process-safe lock;
- kill switch;
- worktree manager;
- validation policy;
- artifact hashing;
- quarantine semantics;
- CI and tests.

The direct Codex execution path becomes legacy and is removed or isolated in a subsequent bounded change. Credential-boundary research remains documentation/history, not a production prerequisite for v0.2.

## Version linkage

A release should record:

```text
Implements: agentic-dev-playbook/v0.2
Playbook reference: protocol/v0.2 (commit pinned at release time)
```

Do not vendor or submodule the playbook merely to create coupling. Protocol compatibility must be explicit and testable.
