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

## v0.2 protocol loop

```text
Human intent
   ↓
DISCOVERY_REQUIRED
   ↓
CONTRACT_REQUIRED
   ↓
approved structured contract
   ↓
workspace prepared
   ↓
ENGINEER_REQUIRED
   ↓
human invokes repository engineer
   ↓
structured engineer handoff
   ↓
VERIFYING
   ↓
deterministic validation
   ↓
REVIEW_REQUIRED
   ↓
independent structured review
   ├─ APPROVE ─────────► HUMAN_MERGE_REQUIRED
   ├─ REQUEST_CHANGES ─► ENGINEER_REQUIRED
   └─ ESCALATE ────────► ESCALATED
```

The default engineering circuit breaker is `MAX_AGENT_ITERATIONS = 3`.

## CLI

The primary interface contains no command that starts Codex or another model:

```text
happi-agent workflow start WORKFLOW_ID --intent FILE
happi-agent workflow discovery-complete WORKFLOW_ID --evidence FILE
happi-agent workflow contract WORKFLOW_ID CONTRACT.json
happi-agent workflow engineer-handoff WORKFLOW_ID ENGINEER_HANDOFF.json
happi-agent workflow verify WORKFLOW_ID --policy POLICY_ID
happi-agent workflow review WORKFLOW_ID ARCHITECT_REVIEW.json
happi-agent workflow status WORKFLOW_ID
happi-agent workflow next WORKFLOW_ID
happi-agent workflow list
happi-agent workflow complete WORKFLOW_ID --merged-commit SHA
```

Read-only commands remain for historical v0.1 SQLite records:

```text
happi-agent legacy-runs
happi-agent legacy-show RUN_ID
```

## Deterministic validation policies

Validation policy is independent of any model prompt and lives under
`policies/*.json`.

Example:

```json
{
  "version": 1,
  "id": "machine-audit-happi",
  "max_files": 12,
  "max_diff_bytes": 262144,
  "forbidden_paths": [".git/**", ".github/**"],
  "allowed_paths": ["README.md", "audits/raspberry-pi-5/**"],
  "allowed_binary_extensions": []
}
```

Runtime enforcement is in standard-library Python. The public shape is documented
by `schemas/validation-policy.schema.json`.

## What Happi owns

- deterministic protocol state and transitions;
- SQLite workflow/event/artifact persistence;
- repository/worktree lifecycle;
- artifact SHA-256 records;
- deterministic diff validation;
- structured contract/engineer/reviewer validation;
- iteration limits;
- kill switch and global lock;
- block/escalate routing;
- next-action reporting.

## What Happi does not own in v0.2

- ChatGPT or Codex credentials;
- unattended model invocation;
- product decisions;
- reviewer reasoning;
- autonomous merge;
- autonomous deployment.

## Migration from v0.1

This branch started from
`a97843254d4b83e1941b7d788aa602473e36f306`, the last pre-auth-hardening baseline.

The deterministic pieces were retained and repurposed. The direct Codex executor,
unattended runner, model prompt/job format and unattended systemd unit have now
been removed from the v0.2 branch.

Their history and the credential-boundary investigation remain preserved in Git
and in historical Draft PR #1.

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

This remains an architectural migration branch, not a production release.
