# Feature Contract — Happi Agent v0.2 redesign

## Status

APPROVED

## Objective

Redesign Happi Agent as the deterministic reference implementation of `agentic-dev-playbook/v0.2`.

Happi should automate protocol state, repository preparation, evidence, verification, circuit breakers and next-action reporting without requiring unattended invocation of Codex or another cognitive worker.

## Why

The v0.1 unattended-Codex experiment proved valuable but made credential isolation and tool-execution boundaries more complex than the orchestration problem. The playbook itself requires incremental trust and the smallest sufficient solution.

## Non-goals

- unattended Codex invocation;
- service-owned ChatGPT credentials;
- automatic merge;
- autonomous product decisions;
- automatic correction loops;
- deleting the v0.1 experiment history.

## Acceptance criteria

- AC1 — Happi declares conformance with `agentic-dev-playbook/v0.2`.
- AC2 — Protocol states and transitions are deterministic and unit tested.
- AC3 — The three-iteration circuit breaker is enforced independently of agents.
- AC4 — Structured engineer handoffs and architect reviews are strictly validated.
- AC5 — Human merge remains mandatory.
- AC6 — No v0.2 control-plane transition depends on a service-owned model credential.
- AC7 — The v0.1 unattended experiment remains auditable as historical evidence.

## Invariants

- repository truth beats model narrative;
- deterministic verification remains external to cognitive workers;
- no secret is required in a handoff;
- invalid protocol transitions fail closed;
- merge authority remains human.

## Human decisions reserved

- merging the v0.2 redesign;
- enabling any future automated agent invocation;
- weakening iteration, secret, destructive-action or merge guardrails.

## Escalation conditions

Escalate if implementing the protocol would require model credentials in the daemon, destructive repository operations, weakening deterministic verification, or silently changing the playbook protocol.
