# Manual v0.2 workflow example

This example demonstrates the protocol surface without automating any cognitive worker.

## 1. Configure a disposable canonical repository

Copy `config.example.toml` and point `canonical_repo` to a disposable Git
repository. Do not use production data for the first manual exercise.

## 2. Start

```bash
export HAPPI_AGENT_CONFIG=/path/to/config.toml

happi-agent workflow start demo \
  --intent examples/manual-v0.2/intent.txt
```

Expected next action:

`complete_repository_discovery`

## 3. Record discovery

```bash
happi-agent workflow discovery-complete demo \
  --evidence examples/manual-v0.2/discovery.txt
```

Expected next action:

`approve_feature_contract`

## 4. Approve the contract

```bash
happi-agent workflow contract demo \
  examples/manual-v0.2/CONTRACT.json
```

Happi creates the detached worktree and reports:

`invoke_repository_engineer`

Use `happi-agent workflow status demo` to obtain the recorded base commit and
workspace path.

## 5. Human invokes the repository engineer

The operator starts the chosen repository-aware coding agent manually and points
it at the prepared worktree plus the contract/project rules.

The agent does not run inside Happi and Happi owns no model credential.

Create an engineer handoff from
`ENGINEER_HANDOFF.template.json`:

- replace `<BASE_COMMIT>` with the workflow base commit;
- keep the correct iteration;
- list actual verification truthfully.

Then:

```bash
happi-agent workflow engineer-handoff demo /path/to/ENGINEER_HANDOFF.json
happi-agent workflow verify demo --policy machine-audit-happi
```

## 6. Human invokes the independent reviewer

Only after deterministic verification reaches `REVIEW_REQUIRED`, create a
review following `ARCHITECT_REVIEW.example.json`.

```bash
happi-agent workflow review demo /path/to/ARCHITECT_REVIEW.json
```

An `APPROVE` verdict stops at:

`HUMAN_MERGE_REQUIRED`

It does not merge.

## 7. Human merge and completion

After the human merges/updates the canonical repository, record the observed full
commit SHA:

```bash
happi-agent workflow complete demo --merged-commit <40-char-sha>
```

Happi verifies that canonical `HEAD` equals that SHA before transitioning to
`COMPLETE`.

## Inspection

At any point:

```bash
happi-agent workflow status demo
happi-agent workflow next demo
```

The SQLite events and SHA-256 artifact records are the durable audit trail.
