# systemd

Happi Agent v0.2 deliberately ships **no unattended model-job systemd unit**.

The v0.1 `happi-agent@.service` template was removed together with the direct
Codex executor. Cognitive-worker invocation is a human-visible workflow boundary
in protocol v0.2.

A future systemd unit may automate deterministic housekeeping or observation only
after that behavior has a bounded contract and does not invoke a model, merge,
deploy, or require model credentials.
