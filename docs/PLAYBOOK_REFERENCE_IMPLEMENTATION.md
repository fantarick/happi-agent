# Happi Agent as playbook reference implementation

## Relationship

`agentic-dev-playbook` is the normative methodology and protocol.

`happi-agent` is the first executable reference implementation of the
deterministic orchestration layer.

The repositories remain physically separate so the playbook can stay
tool-agnostic and Happi can evolve as a Python/Linux/Git implementation. They are
version-linked rather than merged into a monorepo.

## v0.2 boundary

Happi owns:

- workflow state and legal transitions;
- SQLite events and artifact metadata;
- worktree lifecycle;
- deterministic validation;
- contract and handoff validation;
- iteration limits;
- block/escalate routing;
- next-action reporting.

Happi does not own:

- ChatGPT/Codex authentication;
- unattended model invocation;
- product decisions;
- independent review reasoning;
- merge authority.

## Implemented control flow

```text
intent
  ↓
Happi persists workflow
  ↓
discovery evidence
  ↓
approved feature contract
  ↓
Happi creates detached worktree
  ↓
ENGINEER_REQUIRED
  ↓
human invokes repository engineer
  ↓
ENGINEER_HANDOFF.json
  ↓
Happi validates + increments iteration
  ↓
deterministic Validator
  ↓
REVIEW_REQUIRED
  ↓
ARCHITECT_REVIEW.json
  ↓
Happi
  ├─ APPROVE         -> HUMAN_MERGE_REQUIRED
  ├─ REQUEST_CHANGES -> ENGINEER_REQUIRED or ESCALATED at circuit breaker
  └─ ESCALATE        -> ESCALATED
```

## Migration from v0.1

The redesign branch started from
`a97843254d4b83e1941b7d788aa602473e36f306`.

Retained:

- Git worktree manager;
- validator;
- SHA-256 artifact evidence;
- SQLite audit concepts;
- process-safe global lock;
- kill switch;
- historical run-store readability;
- CI.

Removed from the v0.2 branch:

- direct Codex executor;
- unattended Runner;
- daemon-owned model prompt/job format;
- `codex_binary` configuration;
- unattended systemd model-job unit.

Validation configuration is now model-independent and lives in
`policies/*.json`.

Credential-boundary research remains historical evidence in Git and Draft PR #1;
it is no longer a production prerequisite.

## Version linkage

A release should record:

```text
Implements: agentic-dev-playbook/v0.2
Playbook reference: protocol/v0.2 (commit pinned at release time)
```

Do not vendor or submodule the playbook merely to create coupling. Protocol
compatibility must be explicit and testable.
