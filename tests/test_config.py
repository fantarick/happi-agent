from __future__ import annotations

import unittest

from happi_agent.config import ConfigError, load_job_config, parse_strict_yaml
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


if __name__ == "__main__":
    unittest.main()

