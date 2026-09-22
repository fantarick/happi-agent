# Project Rules — Happi Agent v0.2

## Identity

Happi Agent is the reference deterministic orchestrator for `agentic-dev-playbook/v0.2`.

## Required discovery

Before editing:

- read `.ai/CONTRACT.md`;
- inspect the active branch and base commit;
- inspect affected tests;
- inspect the corresponding protocol files in `fantarick/agentic-dev-playbook`.

## Standard verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check
```

## Scope

- keep the orchestrator deterministic;
- no unattended model invocation in v0.2 core;
- no service-owned ChatGPT credential dependency;
- no autonomous merge;
- no unrelated dependency upgrades or broad refactors;
- preserve rollbackability and v0.1 historical evidence.

## Autonomy limits

```text
MAX_AGENT_ITERATIONS = 3
HUMAN_MERGE_REQUIRED = true
AUTONOMOUS_AGENT_INVOCATION = false
AUTONOMOUS_DEPLOY = false
DESTRUCTIVE_ACTIONS_REQUIRE_APPROVAL = true
```

## Escalate immediately when

- repository reality contradicts the contract;
- protocol semantics are ambiguous;
- a secret would need to be exposed;
- a destructive action lacks explicit authority;
- the iteration limit is reached.
