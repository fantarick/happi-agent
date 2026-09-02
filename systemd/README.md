# systemd files

`happi-agent@.service` is a deployment template only. This repository does not
install, enable or start it.

Before installation, create a dedicated unprivileged `happi-agent` account, place
the canonical repository outside the worktree root, and override `ReadWritePaths`
so they exactly match the deployment configuration. Do not make the canonical
working tree writable merely for convenience; only its shared Git directory is
needed by the orchestrator for worktree administration.

