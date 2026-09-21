# systemd files

`happi-agent@.service` is a deployment template only. This repository does not
install, enable or start it.

Before installation, create a dedicated unprivileged `happi-agent` account, place
the canonical repository outside the worktree root, and override `ReadWritePaths`
so they exactly match the deployment configuration. Do not make the canonical
working tree writable merely for convenience; only its shared Git directory is
needed by the orchestrator for worktree administration.

The unit requires `/var/lib/happi-agent/CANARY_DENIED`. This is an operator
attestation, not a test fixture: do not create it until the real procedure in
`docs/AUTHENTICATION.md` has returned `CANARY_DENIED` and its raw artifacts have
been reviewed. The Python runner independently checks the same gate content and
permissions.
