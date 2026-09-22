# systemd files

`happi-agent@.service` is a deployment template only. This repository does not
install, enable or start it.

Before installation, create a dedicated unprivileged `happi-agent` account, place
the canonical repository outside the worktree root, and override `ReadWritePaths`
so they exactly match the deployment configuration. Do not make the canonical
working tree writable merely for convenience; only its shared Git directory is
needed by the orchestrator for worktree administration.

Install the complete Codex 0.154.0 standalone bundle under
`/opt/codex/0.154.0`, not individual binaries under `/usr/local/bin`. Install
`deployment/codex-config.toml` as `/etc/happi-agent/codex-config.toml`, owned by
root and mode `0644`. The unit bind-mounts that file read-only over
`$CODEX_HOME/config.toml`; the remaining credential cache stays writable by the
client. The Python executor independently checks the bundle inventory, hashes,
ownership, modes, config hash and loaded permission profile before a model turn.

The service `PATH` intentionally excludes `/usr/local/bin`, so the retired wrapper
cannot be selected accidentally. `KillMode=control-group` complements the
executor's process-group termination on timeout.

Do not add `--sandbox`, `sandbox_mode`, `sandbox_workspace_write` or a legacy
`sandboxPolicy`. The 0.154.0 permission profile and legacy sandbox selectors are
mutually exclusive.

The unit requires `/var/lib/happi-agent/CANARY_DENIED`. This is an operator
attestation, not a test fixture: do not create it until the real procedure in
`docs/AUTHENTICATION.md` has returned `CANARY_DENIED` and its raw artifacts have
been reviewed. The Python runner independently checks the same gate content and
permissions.

## Staged installation plan

The commands below are for a future operator session; this implementation phase
does not run them. They assume the reviewed source tree is installed root-owned at
`/opt/happi-agent` and the `happi-agent` console script already exists at the
absolute path used by the unit. Review user/group IDs and all storage paths first.

```bash
sudo install -d -o root -g root -m 0755 /etc/happi-agent
sudo install -d -o happi-agent -g happi-agent -m 0700 \
  /var/lib/happi-agent /var/lib/happi-agent/codex
sudo install -d -o happi-agent -g happi-agent -m 0700 \
  /srv/happi-agent/worktrees
sudo install -o root -g root -m 0644 \
  /opt/happi-agent/deployment/happi-agent.toml \
  /etc/happi-agent/config.toml
sudo install -o root -g root -m 0644 \
  /opt/happi-agent/deployment/codex-config.toml \
  /etc/happi-agent/codex-config.toml
sudo install -o root -g root -m 0644 \
  /opt/happi-agent/deployment/codex-config.toml \
  /var/lib/happi-agent/codex/config.toml
sudo install -o root -g root -m 0644 \
  /opt/happi-agent/systemd/happi-agent@.service \
  /etc/systemd/system/happi-agent@.service
sudo systemd-analyze verify /etc/systemd/system/happi-agent@.service
sudo systemctl daemon-reload
```

Install and verify the complete Codex bundle first using
`docs/AUTHENTICATION.md`. Do not enable or start this unit yet: direct execution of
the canary driver under `happi-agent` must pass before the operator creates the
gate. Creating the gate, enabling a timer, starting a real job and retiring the old
`/usr/local` files are separate manual decisions.
