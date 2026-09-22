from __future__ import annotations

import unittest
from pathlib import Path

from happi_agent.config import (
    ConfigError,
    load_app_config,
    load_job_config,
    parse_strict_yaml,
)
from tests.helpers import ProjectFixture


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ProjectFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_parses_declared_job(self) -> None:
        job = load_job_config(
            "test-job", self.fixture.app, self.fixture.registry.ids()
        )
        self.assertEqual(job.job_id, "test-job")
        self.assertEqual(job.collectors, ("test.snapshot",))
        self.assertEqual(job.validation.max_files, 5)
        self.assertEqual(job.validation.allowed_paths, ("docs/**",))

    def test_rejects_duplicate_and_unknown_keys(self) -> None:
        with self.assertRaises(ConfigError) as duplicate:
            parse_strict_yaml("version: 1\nversion: 1\n")
        self.assertEqual(duplicate.exception.code, "DUPLICATE_CONFIG_KEY")
        source = self.fixture.jobs / "test-job.yaml"
        source.write_text(source.read_text() + "command: whoami\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as unknown:
            load_job_config("test-job", self.fixture.app, self.fixture.registry.ids())
        self.assertEqual(unknown.exception.code, "UNKNOWN_CONFIG_KEY")

    def test_rejects_unregistered_collector(self) -> None:
        self.fixture.write_job(collector="host.arbitrary")
        with self.assertRaises(ConfigError) as context:
            load_job_config("test-job", self.fixture.app, self.fixture.registry.ids())
        self.assertEqual(context.exception.code, "UNKNOWN_COLLECTOR")

    def test_app_config_requires_absolute_codex_binary(self) -> None:
        config = self.fixture.root / "app.toml"
        config.write_text(
            'state_dir = "state"\n'
            'worktree_root = "worktrees"\n'
            'canonical_repo = "canonical"\n'
            'jobs_dir = "jobs"\n'
            'prompts_dir = "prompts"\n'
            'codex_binary = "codex"\n',
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError) as context:
            load_app_config(config)
        self.assertEqual(context.exception.code, "INVALID_CODEX_BINARY")

    def test_production_config_uses_exact_security_paths(self) -> None:
        config = load_app_config(
            Path(__file__).parents[1] / "deployment" / "happi-agent.toml"
        )
        self.assertEqual(
            config.codex_binary, "/opt/codex/0.154.0/bin/codex"
        )
        self.assertEqual(
            config.codex_config,
            Path("/var/lib/happi-agent/codex/config.toml"),
        )
        self.assertEqual(
            config.worktree_root, Path("/srv/happi-agent/worktrees")
        )
        self.assertEqual(
            config.canonical_repo, Path("/srv/machine-audits")
        )
        self.assertEqual(
            config.effective_credential_boundary_gate,
            Path("/var/lib/happi-agent/CANARY_DENIED"),
        )


if __name__ == "__main__":
    unittest.main()
