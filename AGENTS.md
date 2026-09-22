# Happi Agent v0.2 project rules

Happi Agent is the deterministic reference implementation of
`agentic-dev-playbook/v0.2`.

Before editing, read:

- `.ai/CONTRACT.md`
- `.ai/PROJECT_RULES.md`
- `docs/PLAYBOOK_REFERENCE_IMPLEMENTATION.md`

## Core invariants

- Keep workflow orchestration deterministic.
- Do not add unattended model invocation to the v0.2 core.
- Do not require service-owned ChatGPT/Codex credentials for protocol transitions.
- Human merge remains mandatory.
- Enforce the protocol state machine and iteration circuit breaker outside agents.
- Structured handoffs must be strictly validated before they can authorize a
  transition.
- Tests and repository evidence outrank agent narrative.
- Preserve append-only/auditable evidence for important transitions.
- Never expose secrets, tokens, private keys, cookies or credential-bearing logs.
- Do not silently expand scope, weaken acceptance criteria, or broaden autonomy.

## Migration rule

The direct Codex executor inherited from v0.1 is legacy during this branch.
Do not extend it. Remove or isolate it only in a bounded follow-up change with
tests proving that deterministic worktree, validation, lock, kill-switch and audit
behavior are preserved.

## Git authority

Agents may inspect, edit and test within the active contract. Commit, push, PR,
merge, destructive history changes and deployment remain human-authorized unless
the active project rules explicitly say otherwise.

## IPFS safety defaults

Quando opera su IPFS, il repository engineer non espone mai l'API Kubo 5001 su
interfacce non-loopback e non rende pubblico il gateway 8080 senza richiesta
esplicita e preventiva analisi dei rischi. Non configura upload pubblici o pinning
per terzi, non apre porte sul router e non modifica il firewall senza istruzione
esplicita. Non pinna contenuti di provenienza sconosciuta e non pubblica dati
personali, credenziali o informazioni riferibili a minori. Non cancella pin
preesistenti, non esegue garbage collection e non modifica il repository senza
backup e richiesta esplicita.
