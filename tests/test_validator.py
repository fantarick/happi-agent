from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from happi_agent.models import ValidationPolicy
from happi_agent.validator import Validator
from happi_agent.workspace import WorkspaceManager
from tests.helpers import ProjectFixture


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ProjectFixture()
        self.manager = WorkspaceManager(
            self.fixture.app.canonical_repo, self.fixture.app.worktree_root
        )
        self.workspace = self.manager.create(uuid.uuid4().hex)

    def tearDown(self) -> None:
        if self.workspace.path.exists():
            self.manager.cleanup(self.workspace)
        self.fixture.close()

    def policy(self, *, max_files: int = 5, max_diff: int = 65536) -> ValidationPolicy:
        return ValidationPolicy(
            max_files=max_files,
            max_diff_bytes=max_diff,
            forbidden_paths=(".github/**", ".git/**"),
            allowed_paths=(),
        )

    def failed_codes(self, result: object) -> set[str]:
        return {check.code for check in result.checks if not check.passed}  # type: ignore[attr-defined]

    def test_forbidden_paths(self) -> None:
        target = self.workspace.path / ".github" / "workflows"
        target.mkdir(parents=True)
        (target / "publish.yml").write_text("publish: true\n", encoding="utf-8")
        result = Validator().validate(self.workspace, self.policy())
        self.assertFalse(result.ok)
        self.assertIn("FORBIDDEN_PATH", self.failed_codes(result))

    def test_max_files(self) -> None:
        (self.workspace.path / "one.txt").write_text("one\n", encoding="utf-8")
        (self.workspace.path / "two.txt").write_text("two\n", encoding="utf-8")
        result = Validator().validate(self.workspace, self.policy(max_files=1))
        self.assertIn("MAX_FILES_EXCEEDED", self.failed_codes(result))

    def test_max_diff(self) -> None:
        (self.workspace.path / "docs" / "audit.md").write_text(
            "x" * 5000 + "\n", encoding="utf-8"
        )
        result = Validator().validate(self.workspace, self.policy(max_diff=100))
        self.assertIn("MAX_DIFF_EXCEEDED", self.failed_codes(result))

    def test_new_symlink(self) -> None:
        (self.workspace.path / "link").symlink_to("README.md")
        result = Validator().validate(self.workspace, self.policy())
        self.assertIn("NEW_SYMLINK", self.failed_codes(result))

    def test_unexpected_binary(self) -> None:
        (self.workspace.path / "payload.bin").write_bytes(b"header\0payload")
        result = Validator().validate(self.workspace, self.policy())
        self.assertIn("UNEXPECTED_BINARY", self.failed_codes(result))


if __name__ == "__main__":
    unittest.main()
