"""Real multi-process checkpoint single-executor tests.

Both processes are started together; a readiness barrier guarantees both
TaskExecutors are constructed (each seeing no checkpoint) before either may
attempt the first dispatch. The race is decided by the OS lock create, not by
sleeps: the winner holds the lock for a bounded window while the peer must
already have returned its refusal.
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
WORKER = SCRIPTS / 'test_checkpoint_lock_worker.py'
WAIT_LIMIT_SECONDS = 30


def run_worker(checkpoint, marker, hold, ready, gate):
    return subprocess.run(
        [sys.executable, str(WORKER), str(checkpoint), str(marker),
         str(hold), str(ready), str(gate)],
        capture_output=True, cwd=str(SCRIPTS.parent))


class TwoProcessRaceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.checkpoint = self.folder / 'checkpoint.json'
        self.ready = self.folder / 'ready.count'
        self.gate = self.folder / 'gate'

    def _wait(self, condition, limit=WAIT_LIMIT_SECONDS):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.02)
        return False

    def _touch_ready(self):
        # Both workers signal construction-complete through an append file,
        # which every OS allows concurrently for a single byte each.
        with self.ready.open('a') as handle:
            handle.write('r')

    def test_two_processes_one_dispatch(self):
        marker_a = self.folder / 'dispatch.a'
        marker_b = self.folder / 'dispatch.b'
        processes = []
        for marker, hold in ((marker_a, 1.0), (marker_b, 0.0)):
            process = subprocess.Popen(
                [sys.executable, str(WORKER), str(self.checkpoint),
                 str(marker), str(hold), str(self.ready), str(self.gate)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(SCRIPTS.parent))
            processes.append((marker, process))
        try:
            self.assertTrue(
                self._wait(lambda: self.ready.exists()
                           and self.ready.read_text(encoding='utf-8').count('r') == 2),
                'both workers did not reach the construction barrier')
            self.gate.touch()  # release both at the same time
            outputs = {}
            for marker, process in processes:
                stdout, stderr = process.communicate(timeout=WAIT_LIMIT_SECONDS)
                self.assertEqual(process.returncode, 0,
                                 stderr.decode('utf-8', 'replace'))
                outputs[marker.name] = json.loads(
                    stdout.decode('utf-8').strip().splitlines()[-1])
        except Exception:
            for _, process in processes:
                process.kill()
            raise
        dispatched = [name for name in (marker_a.name, marker_b.name)
                      if (self.folder / name).exists()]
        # Positive proof: exactly one executor really called backend.execute.
        self.assertEqual(len(dispatched), 1, f'dispatch markers: {outputs}')
        # The winner's action is deliberately unverifiable (its expectation
        # fails while the peer is locked out), so BOTH end UNKNOWN: one via
        # postcondition-not-observed, the other via lock refusal. What must
        # hold is: exactly one dispatch, and exactly one lock refusal.
        refusals = [out for out in outputs.values()
                   if 'checkpoint lock' in out['detail']]
        self.assertEqual(len(refusals), 1, f'lock refusals: {outputs}')
        # The winner completes (or, if its post-check races its own hold
        # window, ends UNKNOWN); the locked-out peer is always UNKNOWN.
        statuses = sorted(out['status'] for out in outputs.values())
        self.assertEqual(statuses[1], 'unknown')
        self.assertIn(statuses[0], ('success', 'unknown'))
        # The durable checkpoint is complete JSON written by the winner only.
        disk = json.loads(self.checkpoint.read_text(encoding='utf-8'))
        self.assertEqual(disk['run_id'], 'conc-run')
        self.assertEqual(disk['attempted'], ['a'])
        # The loser never wrote a checkpoint of its own.
        self.assertFalse((self.folder / 'checkpoint.json.lock').exists())

    def test_peer_holding_lock_blocks_second_process(self):
        # The holder is this test process (a genuinely different owner ID
        # holding the same lock file); the peer is a real subprocess that
        # must refuse, never dispatch, and never write the checkpoint.
        from agent_wow_checkpoint import CheckpointLock
        holder = CheckpointLock(self.checkpoint, 'holder-run')
        holder.acquire()
        self.gate.touch()  # no gate wait needed; the holder already blocks
        try:
            second = subprocess.run(
                [sys.executable, str(WORKER), str(self.checkpoint),
                 str(self.folder / 'dispatch.loser'), '0', str(self.ready),
                 str(self.gate)],
                capture_output=True, timeout=WAIT_LIMIT_SECONDS,
                cwd=str(SCRIPTS.parent))
        finally:
            holder.release()
        self.assertEqual(second.returncode, 0,
                         second.stderr.decode('utf-8', 'replace'))
        report = json.loads(second.stdout.decode('utf-8').strip().splitlines()[-1])
        self.assertEqual(report['status'], 'unknown')
        self.assertFalse((self.folder / 'dispatch.loser').exists())
        self.assertIn('checkpoint lock', report['detail'])
        self.assertFalse(self.checkpoint.exists())
        self.assertFalse((self.folder / 'checkpoint.json.lock').exists())


if __name__ == '__main__':
    unittest.main()
