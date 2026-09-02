from __future__ import annotations

import unittest
from pathlib import Path

from happi_agent.models import RunState
from happi_agent.runner import Runner
from happi_agent.security import GlobalRunLock
from happi_agent.state import StateStore
from tests.helpers import FakeExecutor, ProjectFixture


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ProjectFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def runner(self, executor: FakeExecutor) -> Runner:
        return Runner(
            self.fixture.app,
            executor=executor,
            collectors=self.fixture.registry,
        )

    def record(self, run_id: str) -> dict[str, object]:
        store = StateStore(self.fixture.app.state_dir / "state.sqlite3")
        record = store.get_run(run_id)
        self.assertIsNotNone(record)
        return record  # type: ignore[return-value]

    def test_kill_switch_blocks_before_codex(self) -> None:
        self.fixture.app.state_dir.mkdir(parents=True)
        self.fixture.app.effective_kill_switch.write_text("stop\n", encoding="utf-8")
        executor = FakeExecutor()
        outcome = self.runner(executor).run("test-job")
        self.assertEqual(outcome.state, RunState.BLOCKED)
        self.assertEqual(outcome.error_code, "KILL_SWITCH_ACTIVE")
        self.assertFalse(executor.version_called)
        self.assertFalse(executor.execute_called)

    def test_busy_global_lock_records_blocked_run(self) -> None:
        lock = GlobalRunLock(self.fixture.app.effective_lock_file)
        self.assertTrue(lock.acquire())
        try:
            executor = FakeExecutor()
            outcome = self.runner(executor).run("test-job")
        finally:
            lock.release()
        self.assertEqual(outcome.state, RunState.BLOCKED)
        self.assertEqual(outcome.error_code, "GLOBAL_LOCK_BUSY")
        self.assertFalse(executor.version_called)

    def test_timeout_retains_workspace_and_diagnostics(self) -> None:
        outcome = self.runner(FakeExecutor(timed_out=True, exit_code=-15)).run(
            "test-job"
        )
        self.assertEqual(outcome.state, RunState.TIMEOUT)
        record = self.record(outcome.run_id)
        workspace = Path(str(record["workspace_path"]))
        self.assertTrue(workspace.is_dir())
        names = {artifact["name"] for artifact in record["artifacts"]}  # type: ignore[index]
        self.assertIn("codex.stdout.jsonl", names)
        self.assertIn("diagnostic-validation.json", names)

    def test_validation_failure_quarantines_workspace(self) -> None:
        def forbidden(workspace: Path) -> None:
            target = workspace / ".github" / "workflows"
            target.mkdir(parents=True)
            (target / "publish.yml").write_text("publish: true\n", encoding="utf-8")

        outcome = self.runner(FakeExecutor(forbidden)).run("test-job")
        self.assertEqual(outcome.state, RunState.QUARANTINED)
        record = self.record(outcome.run_id)
        self.assertTrue(Path(str(record["workspace_path"])).is_dir())
        names = {artifact["name"] for artifact in record["artifacts"]}  # type: ignore[index]
        self.assertIn("validation.json", names)
        self.assertIn("diff.patch", names)

    def test_success_cleans_workspace_after_archiving_diff(self) -> None:
        def update_docs(workspace: Path) -> None:
            path = workspace / "docs" / "audit.md"
            path.write_text(path.read_text() + "updated\n", encoding="utf-8")

        outcome = self.runner(FakeExecutor(update_docs)).run("test-job")
        self.assertEqual(outcome.state, RunState.SUCCESS)
        record = self.record(outcome.run_id)
        self.assertFalse(Path(str(record["workspace_path"])).exists())
        names = {artifact["name"] for artifact in record["artifacts"]}  # type: ignore[index]
        self.assertIn("diff.patch", names)
        self.assertIn("collector-snapshot.json", names)
        self.assertTrue(record["prompt_sha256"])
        self.assertTrue(record["config_sha256"])

    def test_failure_retains_diagnostics_and_removes_workspace(self) -> None:
        def partial_change(workspace: Path) -> None:
            (workspace / "docs" / "partial.md").write_text("partial\n", encoding="utf-8")

        outcome = self.runner(FakeExecutor(partial_change, exit_code=2)).run(
            "test-job"
        )
        self.assertEqual(outcome.state, RunState.FAILED)
        record = self.record(outcome.run_id)
        self.assertFalse(Path(str(record["workspace_path"])).exists())
        names = {artifact["name"] for artifact in record["artifacts"]}  # type: ignore[index]
        self.assertIn("error.json", names)
        self.assertIn("diagnostic-diff.patch", names)
        self.assertIn("codex.stderr.log", names)


if __name__ == "__main__":
    unittest.main()
