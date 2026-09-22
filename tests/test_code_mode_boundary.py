from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class DeprecatedSidecarWrapperTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is required")
    def test_wrapper_hides_only_sidecar_codex_home_and_allows_nested_userns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex-home"
            worktree_root = root / "worktrees"
            workspace = worktree_root / "run-1"
            shared_git = root / "canonical.git"
            outside = root / "outside-secret"
            for path in (codex_home, workspace, shared_git):
                path.mkdir(parents=True)
            (codex_home / "CANARY_SECRET").write_text(
                "NON_SECRET_CANARY\n", encoding="utf-8"
            )
            outside.write_text("OUTSIDE\n", encoding="utf-8")

            fake_host = workspace / "codex-code-mode-host-0.154.0"
            fake_host.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "test ! -e \"$CODEX_HOME/CANARY_SECRET\"\n"
                "test ! -e " + str(outside) + "\n"
                "test ! -e /proc/self/fd/9\n"
                "/usr/bin/bwrap --unshare-user --unshare-pid "
                "--ro-bind / / --dev /dev --proc /proc -- /usr/bin/true\n"
                "printf '%s\\n' ISOLATED > boundary.result\n",
                encoding="utf-8",
            )
            fake_host.chmod(0o755)

            source = Path("deployment/codex-code-mode-host").read_text(
                encoding="utf-8"
            )
            source = source.replace(
                "/usr/local/libexec/happi-agent/codex-code-mode-host-0.154.0",
                str(fake_host),
            ).replace(
                "f31e1c5ffbbca7884aff2f0f8795d3da197f4aafb114033a399dfc17a5119031",
                hashlib.sha256(fake_host.read_bytes()).hexdigest(),
            ).replace(
                "/var/lib/happi-agent/codex", str(codex_home)
            ).replace(
                "/srv/happi-agent/worktrees", str(worktree_root)
            ).replace(
                "/srv/machine-audits/.git", str(shared_git)
            )
            wrapper = root / "codex-code-mode-host"
            wrapper.write_text(source, encoding="utf-8")
            wrapper.chmod(0o755)

            inherited = os.open(outside, os.O_RDONLY)
            try:
                if inherited != 9:
                    os.dup2(inherited, 9, inheritable=True)
                else:
                    os.set_inheritable(9, True)
                completed = subprocess.run(
                    (str(wrapper), "--listen", "stdio"),
                    cwd=workspace,
                    env={"CODEX_HOME": str(codex_home), "PATH": "/usr/bin:/bin"},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15,
                    check=False,
                    pass_fds=(9,),
                )
            finally:
                os.close(9)
                if inherited != 9:
                    os.close(inherited)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                (workspace / "boundary.result").read_text(encoding="utf-8"),
                "ISOLATED\n",
            )

    def test_wrapper_attestation_is_exact(self) -> None:
        completed = subprocess.run(
            ("deployment/codex-code-mode-host", "--happi-isolation-check"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "HAPPI_CODE_MODE_HOST_ISOLATED_V1\n")


if __name__ == "__main__":
    unittest.main()
