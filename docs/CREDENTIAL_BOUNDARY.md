# Credential boundary for Codex CLI 0.154.0

## Status and acceptance gate

The post-install real canary returned `CANARY_READABLE`. This is a P0 failure.
The sidecar wrapper is **not** a credential boundary for model-controlled shell
commands and must not be treated as one.

No real unattended job is authorized. A replacement candidate is now implemented:
Codex App Server 0.154.0 with the named permission profile
`happi-workspace-only`. The operator must not create
`/var/lib/happi-agent/CANARY_DENIED` until the installed implementation is tested
under the real `happi-agent` UID and its agentic canary returns `CANARY_DENIED`.
Unit tests, the earlier diagnostic run as `rici`, wrapper attestation and namespace
smoke tests cannot substitute for that deployment result.

Both systemd and the Python runner still fail closed while the gate is absent. The
production executor no longer uses or attests the wrapper. The wrapper artifact is
retained only as evidence of the failed experiment.

## Empirically observed 0.154.0 topology

The topology was sampled on Happi on 2026-09-21 using the installed ARM64 0.154.0
client and real sidecar. The controlled model command was
`/usr/bin/bash -c '/usr/bin/sleep 60'`, allowing `/proc` to be inspected while the
processes were alive. No credential file or canary content was opened.

The installed artifacts were also checked without reading credentials:

- `/usr/local/bin/codex` reported `codex-cli 0.154.0` and SHA-256
  `9b7c1c7abdc26fc3c4f47c77656a8e9121def5483dbae830ef1ee561758448a9`;
- the installed wrapper matched `deployment/codex-code-mode-host` byte for byte;
- the real sidecar SHA-256 was
  `f31e1c5ffbbca7884aff2f0f8795d3da197f4aafb114033a399dfc17a5119031`.

An instrumented, diagnostic-only copy of the co-located wrapper recorded that it
was invoked by the exact 0.154.0 client before it entered bubblewrap. The observed
tree was:

```text
codex client (PID 557732; real CODEX_HOME; host namespaces)
├── wrapper, then outer bwrap (PID 558012)
│   └── bwrap namespace init (PID 558020)
│       └── real codex-code-mode-host 0.154.0 (PID 558021)
└── codex-linux-sandbox (PID 558028)
    └── native bwrap (PID 558033)
        └── sandbox init (PID 558034, namespace PID 1)
            └── model-controlled command (PID 558035, namespace PID 2)
```

The JSONL independently recorded one `command_execution` with the expected command
and exit code zero. The wrapper log recorded PID 558012 with PPID 557732. Thus the
wrapper is traversed for the code-mode sidecar, but the shell path is a separate
child branch created by the client. The shell is neither a direct nor an indirect
descendant of the sidecar.

A second sample kept `/usr/bin/bash` alive rather than allowing it to `exec` the
final `sleep`. In that run the client was PID 559218, the sidecar was its child PID
559487, and `codex-linux-sandbox` was a separate child PID 559494. Native bwrap PID
559500 created sandbox init PID 559501, which launched bash PID 559502. Bash had
namespace PID 2 and PPID 559501; it was therefore an indirect descendant of the
client and not a descendant of PID 559487.

The namespace identifiers made the separation explicit:

| Process | mount namespace | PID namespace | network namespace |
| --- | --- | --- | --- |
| client and pre-unshare helpers | `4026531841` | `4026531836` | `4026531840` |
| real sidecar | `4026532640` | `4026532643` | `4026532645` |
| model-controlled command | `4026532889` | `4026532891` | `4026532892` |

The sidecar and command therefore share none of the sampled mount, PID or network
namespaces. Their user and IPC namespaces were also distinct. PIDs are ephemeral,
but the parentage, command lines and namespace inode relationships were captured
during the same live execution.

## Where `workspace-write` is applied

The client directly launched a helper alias below its real
`$CODEX_HOME/tmp/arg0`, with a managed permission profile containing:

- read access to the filesystem root;
- write access to the selected worktree;
- read-only handling for the worktree `.git` metadata;
- restricted network access.

That helper launched native bubblewrap with `--ro-bind / /`, then rebound the
worktree read-write and unshared user, PID, IPC and network namespaces. The command
mount table consequently contained the host root mounted read-only and the
worktree mounted read-write.

This is the `workspace-write` boundary. It protects host files from writes; it is
not a read allowlist. A file readable by the service UID remains readable through
the read-only root mount unless it is separately masked in this command namespace.

## Cause of `CANARY_READABLE`

The sidecar wrapper creates an empty mount at
`/var/lib/happi-agent/codex`, but that mount exists only in the sidecar branch. A
mount namespace change is inherited by descendants, not by siblings or their later
descendants. The client creates the native sandbox branch from its own host mount
namespace, where the real `CODEX_HOME` is visible.

Native `workspace-write` then copies that host view into the command namespace by
read-only-binding `/`. Read-only changes mutation rights, not read rights. Because
the client and model-controlled command run under the same host UID, Unix mode
`0600` on `CANARY_SECRET` does not separate them. These facts account for the real
`CANARY_READABLE` result without requiring any read of the decoy contents.

## Supported permission-profile boundary candidate

The experimental schema and runtime behavior of the exact standalone Codex CLI
0.154.0 installed on Happi were tested directly. This release exposes
`permissionProfile/list`, `permissions` on `thread/start` and `turn/start`,
`activePermissionProfile`, `instructionSources`, and the filesystem tokens
`:root`, `:minimal`, `:workspace_roots`, `:tmpdir` and `:slash_tmp`.

The tested profile denies the root by default, exposes only the minimal runtime,
makes the one current worktree writable, exposes
`/srv/machine-audits/.git` read-only, explicitly denies
`/var/lib/happi-agent/codex`, denies both temporary-directory aliases and disables
tool network access. A non-agentic probe returned:

```text
TOOLS_OK WRITE_OK DECOY_DENIED
```

A zero-byte probe against a non-secret decoy in the normal user `CODEX_HOME`
returned `CANARY_DENIED`. A real agentic App Server turn then emitted a completed
`commandExecution` with exit code zero and `aggregatedOutput` equal to
`CANARY_DENIED\n`; the active profile ID was `happi-workspace-only`. The Codex
client retained its own authentication and network access while bubblewrap used
the restricted profile and an unshared tool network.

The production implementation starts a fresh stdio App Server per job, negotiates
`experimentalApi`, enumerates the profile using the worktree as `cwd`, and checks
the active profile before the first model turn. It passes no `sandbox`,
`sandboxPolicy`, `sandbox_mode` or `sandbox_workspace_write`. Codex 0.154.0 rejects
attempts to combine the legacy and profile mechanisms.

The generated 0.154.0 JSON Schema does not expose every experimental field, so the
runtime was also probed directly without starting a model turn. A top-level
`runtimeWorkspaceRoots` request was accepted but ignored and the response contained
an empty list. The implementation therefore does not treat that field as a
security control. The single writable root comes from
`permissions.happi-workspace-only.filesystem.":workspace_roots"." = "write"`,
resolved by Codex against the exact `cwd` supplied to both
`permissionProfile/list` and `thread/start`. The executor rejects any additional
runtime root reported by the server.

`thread/start` always returns a legacy compatibility summary named `sandbox` in
0.154.0 even when a permission profile is active. This is response metadata, not a
selector sent by Happi Agent. The accepted observed value is exactly
`{"type":"readOnly","networkAccess":false}` together with the named active
profile. A `sandboxPolicy` field, a write-capable summary, enabled tool network, or
a missing/wrong active profile fails closed.

App Server has no `--ignore-rules` flag. For v0.1 the trusted config sets
`project_doc_max_bytes=0`, and `thread/start` was empirically observed to return an
empty `instructionSources` list. The executor nevertheless requires that exact
empty list before sending `turn/start`; any file source or missing field fails
closed. Built-in model instructions are not file paths and are not reported in
this list.

This validates the mechanism, not the deployment gate. The bundle under `/opt`,
the root-owned config bind mount, the service UID and the real
`/var/lib/happi-agent/codex` path must still be tested together.

## Boundaries that can apply at the real execution seam

Any replacement must constrain the branch beginning at `codex-linux-sandbox` (or
the eventual command process), not merely `codex-code-mode-host`. Viable classes of
boundary are:

1. The supported Codex 0.154.0 permission profile now implemented by
   `AppServerCodexExecutor`. This is the preferred next deployment because it
   constrains the native command sandbox at the observed execution seam.
2. A distinct tool-executor UID, reached through a small privileged launcher or a
   broker, so model-controlled descendants cannot pass DAC checks on the credential
   cache. The launcher must be tied to the actual native-sandbox branch and must
   not accept arbitrary commands from unrelated callers.
3. A credential broker outside the tool security domain. The client may request
   authentication material, while shell descendants have neither filesystem nor
   IPC authority to request it. This likely requires Codex support or a reviewed
   patch; moving the same readable secret into another file is insufficient.
4. A container or VM dedicated to tool execution, with only the worktree and
   required read-only runtime files exposed. The credential-bearing client must
   remain outside it and communicate through a constrained tool protocol. Putting
   both the client and its credential file inside one container reproduces the
   same flaw.
5. A mandatory-access-control transition applied specifically on the tool branch,
   if the target kernel and policy engine can enforce it for every descendant.
   This is platform-specific and must be validated against bypasses and the real
   canary.

Wrapping the whole client while also placing a readable credential file in that
same namespace does not solve the problem: descendants inherit the client's view.
A bootstrap scheme that removes the secret after login could work only if it is
proved that 0.154.0 never needs the credential store again for refresh or writes;
that property has not been established and should not be assumed.

## Recommendation

Retire the sidecar wrapper as the claimed credential boundary and deploy the
complete pinned 0.154.0 bundle plus the App Server permission profile. Keep
scheduling and the operator gate disabled until the service-UID canary passes.

If the installed App Server path cannot return `CANARY_DENIED`, cannot operate with
the documented minimal readable set, or cannot keep instruction sources empty,
stop and move to an external boundary: a second tool UID and credential broker, or
a separately isolated tool container. Do not fall back to `workspace-write` or the
sidecar wrapper.

Acceptance remains one deterministic condition: after implementation, a new real
canary through the production executor must execute the requested shell and return
`CANARY_DENIED`. Until then the P0 is open.
