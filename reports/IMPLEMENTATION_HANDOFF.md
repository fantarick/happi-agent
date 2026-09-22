# Happi Agent v0.1 — Implementation Handoff

## Scopo del documento

Questo documento descrive l'implementazione locale corrente di `happi-agent` per
consentirne la revisione indipendente. Non formula un giudizio PASS/FAIL
sull'architettura e non sostituisce un audit di sicurezza.

## Obiettivo della v0.1

`happi-agent` è un orchestratore locale, deterministico e fail-closed per esecuzioni
Codex CLI unattended su Raspberry Pi 5 Linux. Python controlla configurazione,
sequenza degli stati, raccolta dei dati host, workspace Git, timeout, validazione,
retention e audit trail. Codex è trattato esclusivamente come worker cognitivo in un
worktree sacrificabile e non riceve autorità di commit, push, PR, merge, sudo o
controllo dell'orchestratore.

## Stato Git dell'handoff

- Repository root locale: `/home/rici/Coding/OpenAI-Codex/happi-agent`
- Repository GitHub canonico: `https://github.com/fantarick/happi-agent`
- Branch base: `main`
- Baseline `main`: `8350039ae5378c50a4f98d3413b2a056d0653367`
- Branch di handoff: `agent/implementation-v0.1`
- HEAD verificato prima di questa diagnosi:
  `3e0ed4d814a6ab354b4af69c60ee454b73e60b52`.
- La canary reale post-installazione ha restituito `CANARY_READABLE`. La diagnosi
  nel working tree documenta la topologia reale e il fallimento del wrapper. Una
  successiva canary App Server con permission profile, eseguita come `rici`, ha
  restituito `CANARY_DENIED`; il meccanismo è ora implementato come boundary
  candidato. Il P0 resta aperto fino alla canary sotto l'UID `happi-agent`. Codex
  non ha autorità di commit, push o modifica PR.
- Remote: `origin https://github.com/fantarick/happi-agent.git`

Il working tree, non la Draft PR, è al momento la sorgente autoritativa della
diagnosi. Dopo revisione, l'operatore può verificarne l'eventuale commit e
pubblicazione con:

```bash
git rev-parse agent/implementation-v0.1
git ls-remote origin refs/heads/agent/implementation-v0.1
```

## Struttura implementata

```text
.
├── AGENTS.md
├── README.md
├── config.example.toml
├── deployment/codex-code-mode-host
├── deployment/codex-config.toml
├── deployment/codex-0.154.0-aarch64.sha256
├── deployment/happi-agent.toml
├── docs/CREDENTIAL_BOUNDARY.md
├── jobs/machine-audit-happi.yaml
├── prompts/machine-audit-happi.md
├── pyproject.toml
├── schemas/job.schema.json
├── scripts/credential_read_canary.py
├── src/happi_agent/
│   ├── cli.py
│   ├── codex.py
│   ├── config.py
│   ├── models.py
│   ├── runner.py
│   ├── security.py
│   ├── state.py
│   ├── validator.py
│   ├── workspace.py
│   └── collectors/
│       ├── base.py
│       ├── host_basic.py
│       ├── network.py
│       ├── services.py
│       └── storage.py
├── systemd/
├── tests/
└── reports/IMPLEMENTATION_HANDOFF.md
```

## Componenti principali

- `cli.py`: espone esclusivamente `run JOB_ID`, `runs` e `show RUN_ID`.
- `runner.py`: orchestra in sequenza preflight, preparazione, collector, Codex,
  validazione, artifact e retention.
- `models.py`: definisce modelli tipizzati, stati, policy e risultati strutturati.
- `config.py`: carica TOML e un sottoinsieme YAML ristretto e fail-closed, rigetta
  chiavi sconosciute e collector non registrati e produce l'hash della configurazione
  risolta.
- `state.py`: mantiene run, eventi, artifact e transizioni in SQLite.
- `workspace.py`: crea e rimuove worktree Git detached sotto una root separata.
- `codex.py`: avvia un App Server stdio nuovo per ogni job, implementa il protocollo
  JSON-RPC, verifica bundle/config/versione, enumera e seleziona il permission
  profile, rifiuta instruction source file e termina l'intero process group su
  timeout. Il vecchio wrapper non è più un controllo del runner.
- `validator.py`: valuta deterministicamente il contenuto del worktree e produce un
  `ValidationResult` strutturato.
- `security.py`: contiene hash SHA-256, kill switch, lock globale e controlli sui
  confini dei path.
- `collectors/`: contiene il registry chiuso e i collector host supportati.

## State machine

Gli stati non terminali implementati sono:

```text
QUEUED
PREFLIGHT
PREPARING
COLLECTING
RUNNING_AGENT
VALIDATING
```

Gli stati terminali sono:

```text
SUCCESS
QUARANTINED
BLOCKED
FAILED
TIMEOUT
```

Le transizioni consentite sono dichiarate in `models.py` e applicate
transazionalmente da `StateStore.transition`. Una transizione non prevista produce
l'errore strutturato `ILLEGAL_TRANSITION` e non modifica lo stato corrente.

## SQLite e audit trail

Il database contiene almeno le tabelle `runs`, `events` e `artifacts`. Ogni run usa
un UUID esadecimale e registra job, stato, timestamp iniziale/finale, base commit,
versione Codex, SHA-256 del prompt esatto, SHA-256 della configurazione risolta, exit
code, error code, dettaglio errore e workspace. Gli eventi registrano ogni
transizione. Gli artifact registrano nome, path, dimensione e SHA-256.

SQLite è inizializzato con foreign key abilitate, journal WAL e synchronous FULL. Il
database e gli artifact sono creati con permessi restrittivi (`0600` per i file,
`0700` per le directory gestite).

## Isolamento tramite worktree

Per ogni run viene creato un nuovo worktree detached sotto una directory identificata
dal run ID. Il repository canonico e la Git common directory devono essere esterni
alla root dei worktree. App Server riceve il worktree come `cwd`; il profilo risolve
la regola dinamica `:workspace_roots = { "." = "write" }` contro quel path esatto.
La directory padre non è una root scrivibile.

Il marker `.git` del worktree viene acquisito e sottoposto a SHA-256 prima della run,
reso read-only e verificato dal validator. Anche `HEAD` deve rimanere uguale al base
commit registrato. La common Git directory non è aggiunta alle writable roots del
sandbox Codex.

## Configurazione Codex executor

L'executor usa `subprocess.Popen` con argv e `shell=False`. Avvia
`codex app-server --stdio --strict-config` dal percorso assoluto verificato, invia
`initialize` con `experimentalApi=true`, quindi `initialized`,
`permissionProfile/list`, `thread/start` e `turn/start`. Richiede esplicitamente
`happi-workspace-only`, `approvalPolicy=never` e una sessione ephemeral. Non invia
alcun selettore legacy sandbox. Il probe diretto della 0.154.0 ha mostrato che un
`runtimeWorkspaceRoots` top-level viene ignorato; l'implementazione non lo usa come
controllo.

Il profilo nega `:root`, permette `:minimal` in lettura, nega `/tmp`, rende
scrivibile solo il worktree, espone `/srv/machine-audits/.git` in lettura, nega
`/var/lib/happi-agent/codex` e disabilita la rete tool. Il file root-owned è
duplicato da override CLI ad alta precedenza. Web search, MCP, app, plugin, hook,
multi-agent, browser, computer use e image generation sono disabilitati.

Prima del modello l'executor richiede `instructionSources=[]`, profilo attivo
esatto, cwd esatta, multi-agent non proattivo, summary sandbox read-only/rete off e
nessun runtime root aggiuntivo. Il campo response `sandbox` è metadata legacy
obbligatorio della 0.154.0, non un selettore inviato dal client; `sandboxPolicy`
resta vietato. Archivia il raw event log,
stderr, ultimo messaggio, profilo, stato del turno e tutti i
`commandExecution`. Il timeout tenta `turn/interrupt`, quindi SIGTERM e SIGKILL
all'intero process group.

## Collector implementati

Il job può indicare soltanto ID presenti nel registry Python:

- `host.basic`: release del sistema, `uname`, uptime e CPU;
- `host.storage`: block device e filesystem;
- `host.services`: servizi running/failed tramite systemd;
- `network.summary`: indirizzi, route e socket in ascolto.

I comandi dei collector sono definiti nel codice con argv strutturati, timeout,
ambiente minimo, output limitato e `shell=False`. Il YAML non può fornire comandi,
argomenti o shell fragment. I risultati vengono serializzati in
`collector-snapshot.json` e incorporati nel prompt auditabile.

## Validator

La validazione esterna a Codex comprende:

- integrità del marker `.git`;
- permanenza di `HEAD` sul base commit;
- `git status` strutturato per file tracked e untracked;
- `git diff --check` anche sui file untracked;
- massimo numero di file modificati;
- massima dimensione del diff;
- percorsi proibiti e allowlist opzionale;
- nuovi symlink;
- file speciali inattesi;
- file binari inattesi;
- generazione deterministica del patch, inclusi file untracked.

Un exit code Codex zero con una qualsiasi verifica rifiutata termina in
`QUARANTINED`, non in `FAILED`.

## Kill switch e global lock

Un lock non bloccante basato su `flock(2)` garantisce una sola run per volta tra
processi. Una seconda run viene comunque registrata e termina `BLOCKED` con
`GLOBAL_LOCK_BUSY`. La presenza del sentinel configurato blocca una nuova run prima
del preflight con `KILL_SWITCH_ACTIVE`.
L'assenza o invalidità dell'attestazione operatore `CANARY_DENIED` blocca inoltre la
run con `CREDENTIAL_BOUNDARY_UNVERIFIED` prima di invocare Codex.

## Retention e quarantine

- `SUCCESS`: conserva snapshot, log, risultato Codex, validazione e diff; quindi
  elimina il worktree.
- `FAILED`: conserva artifact diagnostici e tenta di eliminare il worktree
  sacrificabile.
- `QUARANTINED`: conserva l'intero workspace e gli artifact di validazione.
- `TIMEOUT`: conserva workspace, log e artifact diagnostici.
- `BLOCKED`: conserva la run e l'evento che identifica la causa del blocco.

## Test eseguiti prima della pubblicazione

Comando eseguito integralmente:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Risultato osservato il 21 settembre 2026:

```text
Ran 59 tests
OK
```

- Passati: 59
- Falliti: 0
- Skipped: 0

La suite copre parsing configurazione, collector registry, transizioni, lock tra
processi, gate, timeout e process group, validazione e retention. I test App Server
coprono handshake e `experimentalApi`, enumerazione/selezione del profilo,
instruction source, divieto del legacy sandbox, eventi malformati/errore,
`commandExecution`, final message, completamento, workspace root dinamico, rete
tool disabilitata, deny del `CODEX_HOME`, Git dir read-only, versione e inventario
del bundle, hash e parsing canary. Il wrapper resta coperto solo come artefatto
diagnostico deprecato; tali test non lo qualificano come boundary. Nessun test unit
contatta OpenAI o richiede autenticazione.

## Funzionalità non ancora testate realmente

- Una run unattended completa (vietata finché il P0 resta aperto).
- Una nuova canary reale dopo una futura remediation: il gate resta chiuso finché
  non restituisce `CANARY_DENIED`.
- L'esecuzione unattended completa su Raspberry Pi 5 target con tutti i collector.
- La verifica empirica dell'assenza di egress dai comandi nel sandbox Codex sulla
  macchina target.
- L'installazione, l'hardening e l'avvio dell'unità systemd preparata.
- Il comportamento sotto esaurimento reale di disco, memoria, inode o database.
- Il recupero operativo manuale di worktree quarantinati in produzione.
- Compatibilità con future versioni Codex CLI differenti da quella ispezionata
  durante lo sviluppo.
- Installazione manuale del bundle completo verificato sotto `/opt/codex/0.154.0`
  e del bind mount read-only della configurazione. Il runner ne verifica tutti i
  payload, non soltanto `codex` e `codex-code-mode-host`.

## Diagnosi reale della credential canary

La canary post-installazione ha eseguito realmente il comando shell e ha prodotto
`CANARY_READABLE`. Una successiva prova controllata su Happi, con il client ARM64
0.154.0 esatto, il sidecar reale e un wrapper diagnostico interposto, ha osservato
due rami fratelli creati dal client:

```text
codex
├── wrapper -> bwrap -> real codex-code-mode-host
└── codex-linux-sandbox -> bwrap -> shell model-controlled
```

Il sidecar e la shell avevano mount, PID e network namespace diversi. Il bubblewrap
nativo del ramo shell usava `--ro-bind / /`, rendendo il root host non scrivibile ma
ancora leggibile. L'empty `CODEX_HOME` creato dal wrapper del sidecar non è quindi
ereditato dalla shell. La topologia, gli inode dei namespace e la causa completa
sono registrati in `docs/CREDENTIAL_BOUNDARY.md`.

La successiva diagnosi ha verificato sulla stessa 0.154.0 i permission profile App
Server. Il probe non agentico ha prodotto `TOOLS_OK WRITE_OK DECOY_DENIED`; il
probe zero-byte sul `CODEX_HOME` di `rici` e la canary agentica hanno prodotto
`CANARY_DENIED`. L'evento App Server provava una `commandExecution` completata con
exit code zero e profilo attivo corretto. La canary di produzione sotto
`happi-agent` non è stata eseguita in questa fase.

## Limitazioni note e rischi di sicurezza residui

- Il client Codex necessita del proprio canale di rete verso OpenAI;
  `network_access=false` si applica ai comandi nel sandbox, non al processo client.
- Nella configurazione di sviluppo orchestratore e Codex condividono lo stesso UID.
  Il deployment previsto richiede un utente dedicato non privilegiato e
  `NoNewPrivileges`.
- Il sandbox Linux di Codex rimane parte del trust boundary.
- Il sidecar wrapper installato non protegge le credenziali dai comandi shell e non
  è più usato come boundary. Il permission profile è il boundary candidato, ma
  nessun job reale è autorizzato finché la canary installata sotto `happi-agent`
  non restituisce `CANARY_DENIED` e l'operatore non crea il gate.
- La v0.1 non implementa custom execpolicy. Non intercetta un comando in base al solo
  argv `git commit`; la common Git directory è però esterna alle writable roots e
  alterazioni di `.git`, `HEAD` o repository annidati vengono rifiutate/quarantinate.
- stdout e stderr Codex vengono acquisiti in memoria; un output patologico può creare
  pressione di memoria.
- Non sono ancora implementate quote specifiche su spazio, inode, CPU o memoria del
  worktree.
- I collector possono produrre indirizzi di rete, mount point e nomi di servizi. Gli
  artifact e il prompt inviato a OpenAI devono essere considerati sensibili.
- Il sistema non firma gli artifact e non fornisce remote attestation.
- Il parser job supporta deliberatamente un sottoinsieme YAML, non YAML generale.
- Le valutazioni di sicurezza e liceità documentate non costituiscono parere legale.

## Istruzioni per una futura dry-run

Usare un account Linux non privilegiato, verificare prima tutti i path in
`config.example.toml` e assicurarsi che il repository canonico abbia un `HEAD`
valido. Non installare l'unità systemd per la prima esecuzione.

```bash
cd /home/rici/Coding/OpenAI-Codex/happi-agent
/opt/codex/0.154.0/bin/codex --version
test ! -e .local/state/KILL_SWITCH

HAPPI_AGENT_CONFIG="$PWD/config.example.toml" PYTHONPATH="$PWD/src" \
  python3 -m happi_agent run machine-audit-happi

HAPPI_AGENT_CONFIG="$PWD/config.example.toml" PYTHONPATH="$PWD/src" \
  python3 -m happi_agent runs

HAPPI_AGENT_CONFIG="$PWD/config.example.toml" PYTHONPATH="$PWD/src" \
  python3 -m happi_agent show RUN_ID
```

La dry-run esegue realmente Codex ma non effettua commit, push, PR o merge. Prima di
usarla occorre valutare la sensibilità dello snapshot che sarà trasmesso a OpenAI.

## Stato di consegna

Questa fase lascia intenzionalmente modifiche non committate sul branch
`agent/implementation-v0.1`. Non esegue commit, push, merge né modifica `main`.
L'operatore deve revisionare il diff e decidere separatamente come conservarlo o
pubblicarlo.
