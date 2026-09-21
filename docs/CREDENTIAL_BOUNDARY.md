# Credential boundary for Codex CLI 0.154.0

## Status and acceptance gate

The observed real result `CANARY_READABLE` is a P0 failure. Unit tests and local
namespace probes do not close it. The boundary remains **unverified** until the
post-install real canary on Happi returns `CANARY_DENIED` through
`SubprocessCodexExecutor`.

No real unattended job is authorized before all three conditions hold:

1. the wrapper and the original co-versioned sidecar are installed as root-owned,
   non-group/world-writable files;
2. the real canary result file says `CANARY_DENIED` and the raw JSONL proves the
   requested shell tool was actually invoked;
3. an operator creates `/var/lib/happi-agent/CANARY_DENIED` with the exact content
   `CANARY_DENIED\n` only after reviewing that evidence.

Both systemd and the Python runner fail closed when the gate is absent. The executor
also probes the sidecar wrapper before `codex --version` and again before execution.

## Observed 0.154.0 process boundary

The installed ARM64 bundle was inspected without opening any credential file:

- `/usr/local/bin/codex` reports `codex-cli 0.154.0`;
- `/usr/local/bin/codex-code-mode-host` exposes `--listen`, including stdio;
- the client's diagnostic identifies a missing sidecar at the path
  `codex-code-mode-host` beside the resolved client executable;
- a standalone `codex sandbox` trace shows the native Linux sandbox invoking
  bubblewrap and creating helper aliases under `$CODEX_HOME/tmp/arg0`;
- an outer unprivileged bubblewrap namespace with a private empty `CODEX_HOME`
  successfully ran an inner `codex sandbox` command. User namespaces therefore
  remain available; the wrapper does not disable the native sandbox.

The relevant process tree after installation is:

```text
happi-agent (trusted deterministic control plane)
└── codex client (network + real CODEX_HOME, trusted credential holder)
    └── codex-code-mode-host wrapper (stdio inherited from client)
        └── bwrap: minimal mount/PID/IPC/network namespace
            └── real codex-code-mode-host 0.154.0
                └── native Codex sandbox (nested bwrap)
                    └── model-requested shell process
```

The client never enters the extra namespace and can authenticate normally. The
wrapper inherits stdin/stdout/stderr, so the stdio transport remains connected.
Before starting bubblewrap it closes every other inherited file descriptor, so an
accidentally non-`CLOEXEC` credential descriptor cannot bypass the mount boundary
through `/proc/self/fd`. Only the sidecar and descendants enter the namespace.

## Filesystem view inside the wrapper

The outer namespace starts with an empty tmpfs root and exposes:

- `/usr` and `/etc` read-only, for the static sidecar, normal executables, locale
  data and the `bwrap` binary;
- `/dev`, a private `/proc`, and a private tmpfs `/tmp`;
- an empty tmpfs mounted at `/var/lib/happi-agent/codex`;
- the one current worktree read-write;
- `/srv/machine-audits/.git` read-only, so Git inspection can resolve the worktree
  pointer without granting commit authority.

No `/var`, `/home`, `/run`, canonical working tree or other host path is inherited.
The private `CODEX_HOME` is writable only inside the ephemeral namespace because
0.154.0 needs to create helper aliases there. It contains no credential cache. The
only persistent host-writable bind is the approved worktree.

The wrapper requires the exact production `CODEX_HOME`, requires the current
directory to resolve below `/srv/happi-agent/worktrees`, rejects overlap with
`CODEX_HOME`, drops capabilities, creates a private PID namespace and unshares the
network. The client keeps its own network access to OpenAI; the sidecar has none.

## Threat model

Protected asset: every path below `/var/lib/happi-agent/codex`, especially cached
ChatGPT authentication. The canary uses only the non-secret `CANARY_SECRET` decoy.

Adversary: model output controlling code-mode tool requests and all descendant
shell commands, including arbitrary argv, symlink traversal attempts and reads as
the `happi-agent` UID.

Trusted components: deterministic Python orchestration, the Codex 0.154.0 client,
the root-owned wrapper and real sidecar, the kernel/user-namespace implementation,
bubblewrap, systemd policy and operator handling of the final gate.

Out of scope for this minimal boundary: compromise of the Codex client before it
spawns the wrapper, compromise of root/kernel/bubblewrap, malicious replacement of
root-owned executables, credentials duplicated through hard links or bind mounts
outside the declared cache, and unrelated readable secrets intentionally placed in
the worktree or `/etc`.

## Alternatives considered

- Unix mode `0700/0600` cannot separate processes sharing one UID; the real canary
  demonstrated this directly.
- `workspace-write`, Landlock or the native Codex sandbox alone is too late as a
  credential boundary because the sidecar exists outside the command sandbox.
- `ProtectSystem`, read-only mounts and `ReadOnlyPaths` prevent mutation, not reads.
- Applying `InaccessiblePaths` to the whole systemd unit would also blind the Codex
  client that must authenticate.
- A second UID or a credential broker is stronger in principle, but Codex 0.154.0
  has no documented credential-FD/broker interface. UID switching would add a
  privileged launcher and ownership/IPC design. It is not the minimum v0.1 change.
- Wrapping the whole Codex client cannot work: descendants inherit its mount view,
  so either both client and sidecar see credentials or neither does.

The chosen sidecar-specific mount namespace is the smallest boundary at the actual
exec seam. A future Codex release must be treated as incompatible until sidecar
resolution, wrapper attestation, nested sandbox operation and the real canary are
revalidated.
