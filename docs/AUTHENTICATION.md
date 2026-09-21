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

1. `SubprocessCodexExecutor` passes `forced_login_method="chatgpt"` to Codex. Codex
   should exit if the active cached credentials are incompatible with that policy.
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
  codex login --device-auth
```

Then verify the active method without exposing credentials:

```bash
sudo -u happi-agent -H \
  env CODEX_HOME=/var/lib/happi-agent/codex \
  codex login status
```

Do not copy an existing `rici` credential cache into the service account unless a
separate reviewed migration explicitly requires it.

## Credential-read boundary: mandatory canary before production

`workspace-write` must not be assumed to make the entire host filesystem unreadable.
Before any real unattended job is accepted, test the actual Codex/sandbox version on
Happi. The test must use a **non-secret decoy file**, never `auth.json`.

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

### Install the sidecar boundary

These commands are administrative deployment steps for the operator, not commands
run by Happi Agent. They are deliberately pinned to the inspected ARM64 0.154.0
artifacts. Stop if any check differs:

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

### What the canary tests

Launch Codex through the same `SubprocessCodexExecutor` and environment policy used by
Happi Agent, in a disposable workspace. Instruct the agent to run only an **open
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

Use the repository canary driver. It invokes the same `SubprocessCodexExecutor`,
writes the tool result into the disposable workspace, and stores raw JSONL, stderr,
final message and a deterministic summary outside the model-writable workspace:

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
`canary-summary.json`, `codex.stdout.jsonl` and `codex.stderr.log`. Confirm from the
JSONL that the requested shell tool actually ran; the final prose alone is not
evidence.

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

Happi Agent gives model-generated commands an empty baseline plus a fixed `PATH`
and locale. Codex 0.154.0 prepends its packaged command path when launching those
commands. `SubprocessCodexExecutor` also treats the known tool-host failure as a
protocol error even when Codex exits zero.

A non-agentic smoke test for the platform sandbox, before replacing the sidecar, is:

```bash
codex sandbox -- /usr/bin/id
```

This smoke test checks whether the local sandbox can launch a command. It does not
check the code-mode sidecar and does not replace the credential-read canary, which
must still return `CANARY_DENIED` through `SubprocessCodexExecutor`.

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
