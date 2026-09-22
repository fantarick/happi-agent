from __future__ import annotations

import json
import unittest

from happi_agent.config import ConfigError, load_validation_policy
from tests.helpers import ProjectFixture


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ProjectFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_loads_declared_validation_policy(self) -> None:
        policy = load_validation_policy("test-policy", self.fixture.app)
        self.assertEqual(policy.max_files, 5)
        self.assertEqual(policy.allowed_paths, ("docs/**",))
        self.assertEqual(policy.forbidden_paths, (".github/**", ".git/**"))

    def test_rejects_unknown_policy_keys(self) -> None:
        path = self.fixture.policies / "test-policy.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["command"] = "whoami"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_validation_policy("test-policy", self.fixture.app)
        self.assertEqual(caught.exception.code, "UNKNOWN_CONFIG_KEY")

    def test_rejects_policy_id_mismatch(self) -> None:
        path = self.fixture.policies / "test-policy.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["id"] = "other"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_validation_policy("test-policy", self.fixture.app)
        self.assertEqual(caught.exception.code, "POLICY_ID_MISMATCH")

    def test_rejects_duplicate_paths(self) -> None:
        path = self.fixture.policies / "test-policy.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["allowed_paths"] = ["docs/**", "docs/**"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_validation_policy("test-policy", self.fixture.app)
        self.assertEqual(caught.exception.code, "INVALID_VALIDATION_POLICY")


if __name__ == "__main__":
    unittest.main()
