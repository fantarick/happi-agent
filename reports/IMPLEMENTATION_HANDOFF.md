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
- HEAD SHA finale dell'implementazione sottoposta ad audit:
  `880fbc70696e37019509939b21be995fcd099698`
- Remote: `origin https://github.com/fantarick/happi-agent.git`

Il report è aggiunto in un commit documentale successivo alla baseline immutabile
dell'implementazione indicata sopra. Lo SHA del commit che contiene questo stesso
file non può essere incorporato letteralmente nel file senza cambiare il commit; il
branch head autoritativo è quello esposto dalla Draft PR e da:

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
├── jobs/machine-audit-happi.yaml
├── prompts/machine-audit-happi.md
├── pyproject.toml
├── schemas/job.schema.json
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
- `codex.py`: invoca Codex CLI con argv strutturati, JSONL, policy esplicite e timeout
  dell'intero process group.
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
alla root dei worktree. Codex riceve il worktree sia come `cwd` del processo sia come
working root tramite `-C`.

Il marker `.git` del worktree viene acquisito e sottoposto a SHA-256 prima della run,
reso read-only e verificato dal validator. Anche `HEAD` deve rimanere uguale al base
commit registrato. La common Git directory non è aggiunta alle writable roots del
sandbox Codex.

## Configurazione Codex executor

L'executor usa `subprocess.Popen` con argv e `shell=False`. Il prompt è passato su
stdin. La configurazione per-run comprende:

- `codex exec`;
- `--ignore-user-config` e `--ignore-rules`;
- `--strict-config`;
- `--sandbox workspace-write`;
- `approval_policy="never"`;
- `sandbox_workspace_write.network_access=false`;
- writable roots aggiuntive vuote;
- esclusione di `/tmp` e `$TMPDIR` dalle writable roots;
- sessione `--ephemeral`;
- output `--json` JSONL;
- web search disabilitata;
- MCP servers e app vuoti;
- multi-agent, app, plugin, hook, browser, computer use e image generation
  disabilitati;
- ambiente dei comandi Codex con baseline `none` e PATH/locale espliciti.

stdout JSONL, stderr, ultimo messaggio, exit code, versione e risultato di protocollo
sono archiviati. Il parser richiede un evento `turn.completed` e un messaggio finale.
Il timeout invia prima SIGTERM e poi SIGKILL all'intero process group.

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

Risultato osservato il 2 settembre 2026:

```text
Ran 19 tests in 2.437s
OK
```

- Passati: 19
- Falliti: 0
- Skipped: 0

La suite copre parsing configurazione, collector registry, transizioni, lock tra
processi, run bloccata dal lock, kill switch, timeout e process group, percorsi
proibiti, massimo file, massimo diff, symlink, binari inattesi, quarantine, cleanup
success e retention failure. I test Codex usano un fake executor o un eseguibile
locale fittizio e non contattano OpenAI.

## Funzionalità non ancora testate realmente

- Una run completa con Codex CLI autenticato e servizi OpenAI reali.
- L'esecuzione unattended completa su Raspberry Pi 5 target con tutti i collector.
- La verifica empirica dell'assenza di egress dai comandi nel sandbox Codex sulla
  macchina target.
- L'installazione, l'hardening e l'avvio dell'unità systemd preparata.
- Il comportamento sotto esaurimento reale di disco, memoria, inode o database.
- Il recupero operativo manuale di worktree quarantinati in produzione.
- Compatibilità con future versioni Codex CLI differenti da quella ispezionata
  durante lo sviluppo.

## Limitazioni note e rischi di sicurezza residui

- Il client Codex necessita del proprio canale di rete verso OpenAI;
  `network_access=false` si applica ai comandi nel sandbox, non al processo client.
- Nella configurazione di sviluppo orchestratore e Codex condividono lo stesso UID.
  Il deployment previsto richiede un utente dedicato non privilegiato e
  `NoNewPrivileges`.
- Il sandbox Linux di Codex rimane parte del trust boundary.
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
codex --version
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

## Git status finale

Al termine dell'handoff, dopo il push del commit documentale, lo stato atteso e da
verificare è:

```text
## agent/implementation-v0.1...origin/agent/implementation-v0.1
nothing to commit, working tree clean
```

Il branch non deve essere fuso da questa procedura: rimane in attesa di audit
indipendente tramite Draft Pull Request verso `main`.
