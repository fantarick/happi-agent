# Security invariants

`happi-agent` is a deterministic orchestrator. Codex is an untrusted cognitive
worker, never the control plane.

- Keep orchestration, state transitions, collection, validation and retention in
  deterministic Python.
- Run at most one job at a time. Do not add subagents, multi-agent execution,
  parallel jobs, a web UI, Docker or `--full-auto`.
- Never give Codex sudo, host network access, arbitrary collector commands,
  writable access to the canonical repository/shared Git directory, or authority
  to commit, push, open a PR or merge.
- Happi Agent v0.1 uses cached **ChatGPT** authentication for Codex subscription
  access. Do not add API-key or access-token fallback, do not forward
  `OPENAI_API_KEY`, `CODEX_API_KEY` or `CODEX_ACCESS_TOKEN`, and keep the dedicated
  `CODEX_HOME` credential cache out of logs, reports and Git.
- Treat the credential-read boundary as unverified until the documented non-secret
  canary in `docs/AUTHENTICATION.md` returns `CANARY_DENIED` on Happi. Do not run a
  real unattended job if the canary is readable.
- Invoke subprocesses with structured argv, `shell=False`, bounded timeouts where
  applicable and structured errors.
- Treat job YAML as untrusted configuration. Only registered collector IDs are
  permitted; reject unknown keys and unsupported YAML features.
- Validation is external to Codex. A successful Codex exit with a rejected diff is
  `QUARANTINED`, never `FAILED` or `SUCCESS`.
- Do not delete existing pins, perform garbage collection, or mutate unrelated
  host/repository state.

## IPFS safety defaults

Quando opera su IPFS, Codex non espone mai l'API Kubo 5001 su interfacce non-loopback
e non rende pubblico il gateway 8080 senza richiesta esplicita e preventiva analisi
dei rischi. Non configura upload pubblici o pinning per terzi, non apre porte sul
router e non modifica il firewall senza istruzione esplicita. Non pinna contenuti di
provenienza sconosciuta e non pubblica dati personali, credenziali o informazioni
riferibili a minori. Non cancella pin preesistenti, non esegue garbage collection e
non modifica il repository senza backup e richiesta esplicita. Distingue sempre
cache temporanea, pin intenzionale e pubblicazione; se provenienza, licenza o liceità
sono incerte, blocca l'operazione e chiede una decisione umana. Le sue valutazioni
non sono parere legale.

