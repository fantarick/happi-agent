from __future__ import annotations

import multiprocessing
import tempfile
import unittest
import uuid
from pathlib import Path

from happi_agent.models import RunState
from happi_agent.security import GlobalRunLock
from happi_agent.state import IllegalTransition, StateStore


def _try_lock(path: str, queue: multiprocessing.Queue[bool]) -> None:
    lock = GlobalRunLock(Path(path))
    acquired = lock.acquire()
    queue.put(acquired)
    if acquired:
        lock.release()


class StateAndLockTests(unittest.TestCase):
    def test_illegal_state_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = StateStore(Path(temporary) / "state.sqlite3")
            store.initialize()
            run_id = uuid.uuid4().hex
            store.create_run(run_id, "job", "0" * 64)
            store.transition(run_id, RunState.PREFLIGHT)
            with self.assertRaises(IllegalTransition):
                store.transition(run_id, RunState.SUCCESS)
            self.assertEqual(store.current_state(run_id), RunState.PREFLIGHT)

    def test_global_lock_blocks_another_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run.lock"
            parent = GlobalRunLock(path)
            self.assertTrue(parent.acquire())
            context = multiprocessing.get_context("fork")
            queue = context.Queue()
            process = context.Process(target=_try_lock, args=(str(path), queue))
            process.start()
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
            self.assertFalse(queue.get(timeout=1))
            parent.release()


if __name__ == "__main__":
    unittest.main()

