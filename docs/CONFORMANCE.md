# v0.2 conformance matrix

Implementation: `happi-agent 0.2.0.dev0`

Protocol: `agentic-dev-playbook/v0.2`

Normative draft: `fantarick/agentic-dev-playbook#1`

## Matrix

| Clause | Implementation | Evidence |
| --- | --- | --- |
| C1 State machine | `happi_agent.protocol` | `tests/test_protocol.py` |
| C2 Circuit breaker | protocol transition + controller routing | protocol/workflow tests |
| C3 Structured artifacts | strict contract/handoff parsers | protocol/workflow tests |
| C4 Review semantics | semantic review validation | `test_approve_rejects_failed_or_unknown_criteria`, `test_request_changes_requires_blocking_finding` |
| C5 Deterministic verification | `Validator` runs outside cognitive worker | validator + workflow tests |
| C6 Durable evidence | SQLite events + SHA-256 artifact metadata | workflow controller tests |
| C7 Secrets | no model credential/config surface in v0.2 | `tests/test_cli.py` |
| C8 Human authority | `APPROVE -> HUMAN_MERGE_REQUIRED` | protocol/workflow tests |
| C9 Invocation boundary | manual cognitive-worker invocation | CLI boundary tests + absence of executor modules |

## Important interpretation

CI success proves only the tested clauses. It does not turn the implementation
into an autonomous coding system and does not authorize merge or deployment.

The v0.2 reference implementation intentionally has no model executor.

## Release gate

Before declaring a v0.2 release conformant:

1. pin the exact playbook protocol commit;
2. run the full test suite from a clean checkout;
3. execute one operator-visible manual workflow against a disposable repository;
4. verify the event/artifact audit trail;
5. review the diff independently;
6. obtain the human merge decision.
