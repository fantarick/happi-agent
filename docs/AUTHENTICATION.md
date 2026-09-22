# Codex authentication policy for Happi Agent v0.1

## Decision

Happi Agent v0.1 uses **Sign in with ChatGPT** for Codex subscription access.
It does not use OpenAI Platform API keys for unattended runs.

The Linux service account `happi-agent` is not a separate OpenAI account. It owns a
dedicated Codex credential cache authenticated once with the operator's ChatGPT
account.

Official reference: https://developers.openai.com/codex/auth

## Runtime invariant

The runner enforces this policy in two independent ways:

1. `AppServerCodexExecutor` verifies the root-owned config and also passes
   `forced_login_method="chatgpt"` as a highest-precedence App Server override.
   Codex should exit if the cached credentials are incompatible with that policy.
2. `codex_process_environment()` deliberately does not forward `OPENAI_API_KEY`,
   `CODEX_API_KEY`, or `CODEX_ACCESS_TOKEN` from the parent environment.

This prevents an unrelated shell, service manager, or administrator environment from
silently changing a ChatGPT-subscription run into usage-based API billing.

## Dedicated CODEX_HOME

The prepared systemd unit sets:

```text
CODEX_HOME=/var/lib/happi-agent/codex
```

The deployment must create that directory as `happi-agent:happi-agent` with mode
`0700`. If file-based credential storage is used, `auth.json` must be mode `0600` and
must never be committed, copied into reports, or printed in diagnostics.

For a headless Raspberry Pi, OpenAI documents device-code login as the preferred
headless flow when available:

```bash
sudo -u happi-agent -H \
  env CODEX_HOME=/var/lib/happi-agent/codex \
  /opt/codex/0.154.0/bin/codex login --device-auth
```

Then verify the active method without exposing credentials:

```bash
sudo -u happi-agent -H \
  env CODEX_HOME=/var/lib/happi-agent/codex \
  /opt/codex/0.154.0/bin/codex login status
```

Do not copy an existing `rici` credential cache into the service account unless a
separate reviewed migration explicitly requires it.

## Credential-read boundary: mandatory canary before production

The legacy `workspace-write` sandbox was proved insufficient. The replacement uses
App Server permission profiles, but remains a candidate boundary until the same
executor installed for the service UID passes the real canary. The test uses a
**non-secret decoy file**, never `auth.json`.

### Prepare the decoy

As administrator, after the service account and `CODEX_HOME` exist:

```bash
sudo install -o happi-agent -g happi-agent -m 0600 /dev/null \
  /var/lib/happi-agent/codex/CANARY_SECRET
printf '%s\n' 'NON_SECRET_CANARY' | \
  sudo tee /var/lib/happi-agent/codex/CANARY_SECRET >/dev/null
sudo chown happi-agent:happi-agent /var/lib/happi-agent/codex/CANARY_SECRET
sudo chmod 0600 /var/lib/happi-agent/codex/CANARY_SECRET
```

The canary content is intentionally non-sensitive.

### Installed sidecar wrapper (not an accepted boundary)

The following procedure records how the currently installed 0.154.0 wrapper was
deployed. Do not repeat it as a remediation: the post-install real canary returned
`CANARY_READABLE`, and live process inspection showed that model-controlled shell
commands are launched by a sibling native-sandbox branch rather than by the
sidecar. The wrapper attestation proves artifact identity only. It does not satisfy
the credential-read boundary or authorize creation of the gate.

These commands are administrative deployment steps for the operator, not commands
run by Happi Agent. They are deliberately pinned to the inspected ARM64 0.154.0
artifacts and are retained for audit history. Stop if any check differs:

```bash
test "$(/usr/local/bin/codex --version)" = "codex-cli 0.154.0"
printf '%s  %s\n' \
  9b7c1c7abdc26fc3c4f47c77656a8e9121def5483dbae830ef1ee561758448a9 \
  /usr/local/bin/codex | sha256sum --check --strict
printf '%s  %s\n' \
  f31e1c5ffbbca7884aff2f0f8795d3da197f4aafb114033a399dfc17a5119031 \
  /usr/local/bin/codex-code-mode-host | sha256sum --check --strict

sudo install -d -o root -g root -m 0755 /usr/local/libexec/happi-agent
sudo install -o root -g root -m 0755 \
  /usr/local/bin/codex-code-mode-host \
  /usr/local/libexec/happi-agent/codex-code-mode-host-0.154.0
sudo install -o root -g root -m 0755 \
  /opt/happi-agent/deployment/codex-code-mode-host \
  /usr/local/bin/codex-code-mode-host
```

The first two hash checks are also an idempotency guard: never copy the installed
wrapper back over the saved real sidecar. Verify the resulting layout without
starting a model run:

```bash
printf '%s  %s\n' \
  f31e1c5ffbbca7884aff2f0f8795d3da197f4aafb114033a399dfc17a5119031 \
  /usr/local/libexec/happi-agent/codex-code-mode-host-0.154.0 | \
  sha256sum --check --strict
test "$(/usr/local/bin/codex-code-mode-host --happi-isolation-check)" = \
  HAPPI_CODE_MODE_HOST_ISOLATED_V1
stat -c '%U:%G %a %n' \
  /usr/local/bin/codex \
  /usr/local/bin/codex-code-mode-host \
  /usr/local/libexec/happi-agent/codex-code-mode-host-0.154.0
```

All three files must be `root:root` and must not be group/world writable. The
wrapper constants assume `/srv/happi-agent/worktrees` and
`/srv/machine-audits/.git`; review and edit the repository artifact before
installation if the production deployment uses different canonical paths.

These integrity properties do not alter the failed security conclusion. See
`CREDENTIAL_BOUNDARY.md` for the empirically observed process and namespace tree.

### Install the complete 0.154.0 bundle and trusted config

The runner never resolves `codex` through `PATH`. It requires this complete original
ARM64 standalone layout under `/opt/codex/0.154.0`:

```text
codex -> bin/codex
bin/codex
bin/codex-code-mode-host
codex-package.json
codex-path/rg
codex-resources/bwrap
codex-resources/zsh/bin/zsh
```

The source bundle was inventoried recursively; it contains exactly the six regular
files above plus the `codex -> bin/codex` symlink and the required directories.
The observed regular-file sizes are 227,482,840; 63,381,656; 206; 4,506,992;
529,168; and 878,056 bytes respectively in the order shown by the checksum
manifest. App Server uses the main client, while command execution can require the
co-versioned code-mode host, bubblewrap, packaged zsh and packaged `rg`; production
therefore pins and installs the complete layout rather than guessing a binary
subset.

The operator can stage it with the following commands. These are deployment
instructions only; they were not executed during implementation:

```bash
source_bundle=/home/rici/.codex/packages/standalone/releases/0.154.0-aarch64-unknown-linux-musl

sudo install -d -o root -g root -m 0755 \
  /opt/codex/0.154.0/bin \
  /opt/codex/0.154.0/codex-path \
  /opt/codex/0.154.0/codex-resources/zsh/bin \
  /etc/happi-agent
sudo install -o root -g root -m 0755 "$source_bundle/bin/codex" \
  /opt/codex/0.154.0/bin/codex
sudo install -o root -g root -m 0755 "$source_bundle/bin/codex-code-mode-host" \
  /opt/codex/0.154.0/bin/codex-code-mode-host
sudo install -o root -g root -m 0755 "$source_bundle/codex-path/rg" \
  /opt/codex/0.154.0/codex-path/rg
sudo install -o root -g root -m 0755 "$source_bundle/codex-resources/bwrap" \
  /opt/codex/0.154.0/codex-resources/bwrap
sudo install -o root -g root -m 0755 "$source_bundle/codex-resources/zsh/bin/zsh" \
  /opt/codex/0.154.0/codex-resources/zsh/bin/zsh
sudo install -o root -g root -m 0644 "$source_bundle/codex-package.json" \
  /opt/codex/0.154.0/codex-package.json
sudo ln -s bin/codex /opt/codex/0.154.0/codex
sudo install -o root -g root -m 0644 \
  /opt/happi-agent/deployment/codex-config.toml \
  /etc/happi-agent/codex-config.toml
sudo install -o root -g root -m 0644 \
  /opt/happi-agent/deployment/codex-config.toml \
  /var/lib/happi-agent/codex/config.toml
```

Verify every payload before enabling the unit:

```bash
cat <<'EOF' | sudo sha256sum --check --strict
9b7c1c7abdc26fc3c4f47c77656a8e9121def5483dbae830ef1ee561758448a9  /opt/codex/0.154.0/bin/codex
f31e1c5ffbbca7884aff2f0f8795d3da197f4aafb114033a399dfc17a5119031  /opt/codex/0.154.0/bin/codex-code-mode-host
abfae2ff248420c32f531f3ba0cb83ea27bb9ba9355872658b591e1939a60288  /opt/codex/0.154.0/codex-package.json
e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b  /opt/codex/0.154.0/codex-path/rg
58bd88f39d02a0b5ac553c2f334edfff1ec74afb9b8f4233dfc5b69225038f92  /opt/codex/0.154.0/codex-resources/bwrap
7feeacd883e1dc749847936948c378653c80a69ec4a9542f0f126b411882c179  /opt/codex/0.154.0/codex-resources/zsh/bin/zsh
b8d0529fd2968aca8e65a19bbf213b843b1de44b92d95fcf02f95222fd3adc0f  /etc/happi-agent/codex-config.toml
b8d0529fd2968aca8e65a19bbf213b843b1de44b92d95fcf02f95222fd3adc0f  /var/lib/happi-agent/codex/config.toml
EOF
test "$(readlink /opt/codex/0.154.0/codex)" = bin/codex
test "$(/opt/codex/0.154.0/bin/codex --version)" = "codex-cli 0.154.0"
```

The systemd unit bind-mounts `/etc/happi-agent/codex-config.toml` read-only over
`/var/lib/happi-agent/codex/config.toml`. This preserves the normal credential
cache for the client while making the policy configuration root-owned. The runner
also supplies the complete profile as CLI overrides and checks the config hash,
rules, ownership and mode. No `sandbox_mode`, `sandbox_workspace_write`,
`--sandbox` or `sandboxPolicy` is permitted.

Codex 0.154.0 has no App Server option for an arbitrary standalone config path; it
loads `config.toml` from `CODEX_HOME`. The bind mount is therefore the trusted
deployment mechanism. The client keeps `/var/lib/happi-agent/codex` for ChatGPT
authentication and refresh writes, while the model-controlled permission domain
has an explicit deny for that whole directory. The executor also repeats the exact
profile and hardening values as immutable argv overrides, then verifies the active
profile returned by App Server before any model call. It never opens `auth.json`.

The old `/usr/local` client/wrapper pair is outside the new absolute execution
path. After the service-UID canary has passed, and only after checking that no other
service uses those names, the operator may quarantine rather than overwrite them:

```bash
sudo install -d -o root -g root -m 0755 \
  /usr/local/libexec/happi-agent/retired-sidecar-wrapper
sudo mv /usr/local/bin/codex \
  /usr/local/libexec/happi-agent/retired-sidecar-wrapper/codex-0.154.0
sudo mv /usr/local/bin/codex-code-mode-host \
  /usr/local/libexec/happi-agent/retired-sidecar-wrapper/codex-code-mode-host-wrapper
```

Do not run this cleanup during installation or canary collection. The files are
not part of the new boundary, and retaining them temporarily preserves the failed
experiment for audit.

### What the canary tests

Launch Codex through the same `AppServerCodexExecutor` and environment policy used
by Happi Agent, in a disposable workspace. Instruct the agent to run only an **open
probe that reads zero bytes**, for example conceptually:

```bash
if head -c 0 /var/lib/happi-agent/codex/CANARY_SECRET >/dev/null 2>&1; then
  echo CANARY_READABLE
else
  echo CANARY_DENIED
fi
```

The probe never prints the file contents. Preserve the Codex JSONL and stderr as
artifacts so the result is independently inspectable; do not rely only on the
agent's prose summary.

Use the repository canary driver. It invokes the same `AppServerCodexExecutor`,
writes the tool result into the disposable workspace, and stores the raw App Server
event log, stderr, final message and a deterministic summary outside the
model-writable workspace:

```bash
sudo -u happi-agent -H sh -c '
set -eu
workspace=$(mktemp -d -p /srv/happi-agent/worktrees canary.XXXXXX)
artifacts=$(mktemp -d -p /var/lib/happi-agent canary-artifacts.XXXXXX)
git -C "$workspace" init -q
set +e
env CODEX_HOME=/var/lib/happi-agent/codex \
    PYTHONPATH=/opt/happi-agent/src \
    /opt/happi-agent/scripts/credential_read_canary.py \
      --workspace "$workspace" \
      --artifacts "$artifacts"
status=$?
set -e
printf "workspace=%s\nartifacts=%s\nstatus=%s\n" \
  "$workspace" "$artifacts" "$status"
exit "$status"
'
```

Exit `0` means the result file is `CANARY_DENIED`; exit `1` means
`CANARY_READABLE`; exit `2` means inconclusive. In every case, inspect
`canary-summary.json`, `app-server.events.jsonl` and `app-server.stderr.log`.
Confirm that the summary and event log contain exactly one completed
`commandExecution`, exit code zero, the zero-byte probe and the same deterministic
result as the workspace file. The final prose alone is not evidence.

### Linux tool-host diagnostic

On Codex CLI 0.154.0 the portable diagnostic surface is `codex sandbox`; the host
selects the Linux backend. Do not insert a literal `linux` subcommand, because this
version interprets it as the program to execute.

`codex sandbox -- /usr/bin/id` only exercises the sandbox backend. It does not
exercise the code-mode tool host used by `codex exec`. The 0.154.0 standalone Linux
bundle contains two co-versioned executables in its `bin` directory:

```text
codex
codex-code-mode-host
```

Copying only the `codex` ELF into `/usr/local/bin` is an incomplete installation.
The client can still authenticate and return exit code zero, but a requested shell
command fails before bubblewrap starts. The diagnostic signature is:

```text
failed to spawn code-mode host /usr/local/bin/codex-code-mode-host:
No such file or directory (os error 2)
```

Resolve the executable and verify the matching sidecar without reading any Codex
credential file:

```bash
codex_executable="$(readlink -f "$(command -v codex)")"
test -x "$(dirname "$codex_executable")/codex-code-mode-host"
```

The official standalone installer keeps package metadata and the complete release
bundle under `CODEX_HOME/packages/standalone`; the visible `codex` command points
into that bundle. If a system-wide binary is provisioned manually, install the
co-versioned `codex-code-mode-host` beside it as well.

Happi Agent gives model-generated commands an empty baseline plus a fixed `PATH`,
locale and `GIT_OPTIONAL_LOCKS=0`. Codex 0.154.0 prepends its packaged command path
when launching those commands. The App Server executor verifies the complete
bundle before every run.

A non-agentic smoke test for the platform sandbox, before replacing the sidecar, is:

```bash
codex sandbox -- /usr/bin/id
```

This smoke test checks whether the local sandbox can launch a command. It does not
check the agentic App Server path and does not replace the credential-read canary,
which must still return `CANARY_DENIED` through `AppServerCodexExecutor`.

### Gate

- `CANARY_DENIED`: after reviewing the artifacts, the operator may install the
  deterministic gate:

  ```bash
  printf '%s\n' CANARY_DENIED | \
    sudo tee /var/lib/happi-agent/CANARY_DENIED >/dev/null
  sudo chown root:root /var/lib/happi-agent/CANARY_DENIED
  sudo chmod 0644 /var/lib/happi-agent/CANARY_DENIED
  ```

  This permits progression to the next deployment gate; it is not authorization to
  enable recurring scheduling.
- `CANARY_READABLE`: **STOP**. Do not run `machine-audit-happi` with real cached
  credentials. Do not create the gate file. Preserve the evidence and revise the
  boundary.
- `CANARY_INCONCLUSIVE`: **STOP**. Treat it exactly like a failed gate.

Retain `CANARY_SECRET` until the evidence has been reviewed. Its contents are
non-secret; an operator may remove it afterward. Happi Agent never removes it.

## No production scheduling yet

Authentication setup and the read-boundary canary are deployment prerequisites.
They do not authorize installation of the timer or recurring unattended execution.
Scheduling remains disabled until the deployment review and first controlled dry-run
are complete.
