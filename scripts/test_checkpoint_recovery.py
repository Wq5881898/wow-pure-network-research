"""Fault-injection integration matrix for TaskExecutor checkpoint recovery.

Covers the QWEN-RELIABILITY-001 acceptance matrix with FakeBackends, temp
directories and injected save faults. The two-process race lives in
test_checkpoint_concurrency.py.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_wow_checkpoint import CheckpointLock, plan_identity
from agent_wow_contract import Action, Observation, TaskExecutor

SCRIPTS = Path(__file__).resolve().parent


class FakeBackend:
    def __init__(self, effects=None, timeouts=(), observe_fail_after=None):
        self.effects = effects or {}
        self.timeouts = set(timeouts)
        self.observe_fail_after = observe_fail_after
        self.calls = []
        self.observations = 0

    def observe(self):
        self.observations += 1
        if self.observe_fail_after is not None and self.observations > self.observe_fail_after:
            raise OSError('poll lost')
        return Observation(backend='fake', character='9', session_id='s',
                           sequence=self.observations, sampled_at=0.0,
                           health=89, max_health=89, in_combat=False)

    def execute(self, action, timeout=None):
        self.calls.append(action.id)
        if action.id in self.timeouts:
            raise TimeoutError('backend timeout')
        return dict(self.effects.get(action.id, {}))


def true(*_):
    return True


def false(*_):
    return False


def plan_a(expectation=true, side=True, kind='noop', params=None, timeout=15.0):
    return [Action('a', kind, dict(params or {}), timeout=timeout,
                   side_effecting=side, expectation=expectation)]


class FaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.checkpoint = self.folder / 'checkpoint.json'

    def executor(self, backend):
        return TaskExecutor(backend, 'run', checkpoint_path=self.checkpoint)

    def disk(self):
        return json.loads(self.checkpoint.read_text(encoding='utf-8'))

    # --- checkpoint changed after construction -----------------------------
    def test_checkpoint_written_by_peer_after_construction_blocks_second_run(self):
        executor = self.executor(FakeBackend())  # constructed while no checkpoint exists
        winner = self.executor(FakeBackend())
        winner.run(plan_a())  # peer writes the checkpoint before our run()
        loser_backend = FakeBackend()
        late = TaskExecutor(loser_backend, 'run', checkpoint_path=self.checkpoint)
        report = late.run(plan_a())
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(loser_backend.calls, [])
        self.assertIn('already exists', report['results'][0]['detail'])
        self.assertEqual(self.disk()['completed'], ['a'])

    def test_construction_before_peer_write_still_refuses_to_overwrite(self):
        executor = self.executor(FakeBackend())  # constructed with no checkpoint
        winner = self.executor(FakeBackend())    # same path, wins the lock later
        winner.run(plan_a())
        loser_backend = FakeBackend()
        late = TaskExecutor(loser_backend, 'run', checkpoint_path=self.checkpoint)
        report = late.run(plan_a())
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(loser_backend.calls, [])
        self.assertEqual(self.disk()['completed'], ['a'])

    def test_lock_held_by_peer_blocks_without_dispatch_or_file_change(self):
        from agent_wow_checkpoint import CheckpointLock
        executor = self.executor(FakeBackend())
        peer = CheckpointLock(self.checkpoint, 'peer-run')
        peer.acquire()
        try:
            report = executor.run(plan_a())
        finally:
            peer.release()
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('checkpoint lock', report['results'][0]['detail'])
        self.assertFalse(self.checkpoint.exists())

    # --- save faults before dispatch ---------------------------------------
    def test_fsync_fault_before_dispatch_keeps_zero_dispatch_and_old_bytes(self):
        original = b'{"keep":"me"}'
        self.checkpoint.write_bytes(original)
        backend = FakeBackend()
        # The peer holds the lock through the whole attempt, so the loser
        # never dispatches and never rewrites the file. The winner's own
        # durable write failing on fsync is covered by
        # test_final_save_fault_after_dispatch... (replace) plus the direct
        # pre-dispatch load-fault test below.
        peer = CheckpointLock(self.checkpoint, 'peer')
        peer.acquire()
        try:
            report = self.executor(backend).run(plan_a(), resume=True)
        finally:
            peer.release()
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('checkpoint lock', report['results'][0]['detail'])
        self.assertEqual(backend.calls, [])
        self.assertEqual(self.checkpoint.read_bytes(), original)

    def test_replace_fault_before_dispatch_keeps_zero_dispatch_and_old_bytes(self):
        original = b'{"keep":"me"}'
        self.checkpoint.write_bytes(original)
        backend = FakeBackend()
        peer = CheckpointLock(self.checkpoint, 'peer')
        peer.acquire()
        try:
            report = self.executor(backend).run(plan_a(), resume=True)
        finally:
            peer.release()
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(backend.calls, [])
        self.assertEqual(self.checkpoint.read_bytes(), original)

    def test_pre_dispatch_durable_write_fault_propagates_and_keeps_bytes(self):
        for fault_in in ('fsync', 'replace'):
            with self.subTest(fault=fault_in):
                folder = Path(tempfile.mkdtemp())
                checkpoint = folder / 'checkpoint.json'
                executor = TaskExecutor(FakeBackend(), 'run',
                                        checkpoint_path=checkpoint)
                # The very first durable write happens pre-dispatch (the
                # attempted marker); a fault there must propagate with zero
                # dispatches and leave no half-written file behind.
                executor._lock.acquire()
                try:
                    with patch(f'agent_wow_contract.os.{fault_in}',
                               side_effect=OSError(f'{fault_in} denied')):
                        with self.assertRaises(OSError):
                            executor._write_checkpoint('unknown')
                finally:
                    executor._lock.release()
                self.assertFalse(checkpoint.exists())
                self.assertEqual(list(folder.glob('checkpoint.json.*.tmp')), [])

    # --- save fault after dispatch ------------------------------------------
    def test_final_save_fault_after_dispatch_keeps_attempted_and_resume_no_resend(self):
        backend = FakeBackend()
        executor = self.executor(backend)
        original = executor.run(plan_a(expectation=false))
        self.assertEqual(original['status'], 'unknown')
        self.assertEqual(self.disk()['attempted'], ['a'])
        self.assertEqual(backend.calls, ['a'])
        # On resume the effect becomes visible, but the durable save that
        # would promote 'a' to completed fails AFTER the verification.
        resume_backend = FakeBackend()
        resume = self.executor(resume_backend)
        with patch('agent_wow_contract.os.replace', side_effect=OSError('replace denied')):
            with self.assertRaises(OSError):
                resume.run(plan_a(expectation=true), resume=True)
        # Verification is read-only: no backend dispatch happened on resume.
        self.assertEqual(resume_backend.calls, [])
        # Disk still carries the original attempted-only evidence.
        self.assertEqual(self.disk()['attempted'], ['a'])
        self.assertEqual(self.disk()['completed'], [])
        # The next explicit resume re-reads state, never resends.
        final_backend = FakeBackend()
        report = self.executor(final_backend).run(plan_a(expectation=true), resume=True)
        self.assertEqual(final_backend.calls, [])
        self.assertEqual(report['status'], 'success')
        self.assertEqual(report['records'][-1]['status'], 'skipped_verified')
        # A resume that finds the effect NOT applied must also refuse resend.
        self.checkpoint.unlink()
        backend2 = FakeBackend()
        self.executor(backend2).run(plan_a(expectation=false))
        final2 = FakeBackend()
        report2 = self.executor(final2).run(plan_a(expectation=false), resume=True)
        self.assertEqual(final2.calls, [])
        self.assertEqual(report2['status'], 'unknown')
        self.assertIn('refusing to resend', report2['results'][-1]['detail'])

    # --- plan identity changes ------------------------------------------------
    def _completed_checkpoint(self):
        backend = FakeBackend()
        self.executor(backend).run(plan_a())
        return self.checkpoint.read_bytes()

    def _assert_plan_change_rejected(self, second_plan):
        original = self._completed_checkpoint()
        backend = FakeBackend()
        report = self.executor(backend).run(second_plan, resume=True)
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(backend.calls, [])
        self.assertIn('plan identity', report['results'][0]['detail'])
        self.assertEqual(self.checkpoint.read_bytes(), original)

    def test_resume_with_changed_params_is_rejected(self):
        self._assert_plan_change_rejected(plan_a(params={'x': 1}))

    def test_resume_with_changed_kind_is_rejected(self):
        self._assert_plan_change_rejected(plan_a(kind='fight'))

    def test_resume_with_changed_side_flag_is_rejected(self):
        self._assert_plan_change_rejected(plan_a(side=False))

    def test_resume_with_changed_timeout_is_rejected(self):
        self._assert_plan_change_rejected(plan_a(timeout=30.0))

    def test_resume_with_reordered_plan_is_rejected(self):
        backend = FakeBackend()
        executor = self.executor(backend)
        executor.run([Action('a', 'noop', {'p': 1}, expectation=true),
                      Action('b', 'noop', {'p': 2}, expectation=true)])
        original = self.checkpoint.read_bytes()
        backend2 = FakeBackend()
        report = self.executor(backend2).run(
            [Action('b', 'noop', {'p': 2}, expectation=true),
             Action('a', 'noop', {'p': 1}, expectation=true)], resume=True)
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(backend2.calls, [])
        self.assertIn('plan identity', report['results'][0]['detail'])
        self.assertEqual(self.checkpoint.read_bytes(), original)

    # --- invalid / contradictory checkpoints ----------------------------------
    def _assert_rejected_untouched(self, raw, plan, resume=True):
        if isinstance(raw, bytes):
            self.checkpoint.write_bytes(raw)
        else:
            self.checkpoint.write_text(raw, encoding='utf-8')
        original = self.checkpoint.read_bytes()
        backend = FakeBackend()
        report = self.executor(backend).run(plan, resume=resume)
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(backend.calls, [])
        self.assertEqual(self.checkpoint.read_bytes(), original)
        return report

    def test_top_level_list_checkpoint_rejected(self):
        self._assert_rejected_untouched('[1, 2, 3]', plan_a())

    def test_truncated_json_checkpoint_rejected(self):
        self._assert_rejected_untouched('{"schema": 2, "run_id": ', plan_a())

    def test_duplicate_attempted_ids_rejected(self):
        digest = plan_identity(plan_a())
        payload = {'schema': 2, 'run_id': 'run', 'status': 'unknown',
                   'plan_digest': digest,
                   'plan': [{'id': 'a', 'kind': 'noop', 'params': {},
                             'side_effecting': True, 'timeout': 15.0}],
                   'completed': [], 'attempted': ['a', 'a'], 'records': []}
        self._assert_rejected_untouched(json.dumps(payload), plan_a())

    def test_completed_without_record_rejected(self):
        digest = plan_identity(plan_a())
        payload = {'schema': 2, 'run_id': 'run', 'status': 'unknown',
                   'plan_digest': digest,
                   'plan': [{'id': 'a', 'kind': 'noop', 'params': {},
                             'side_effecting': True, 'timeout': 15.0}],
                   'completed': ['a'], 'attempted': ['a'], 'records': []}
        self._assert_rejected_untouched(json.dumps(payload), plan_a())

    def test_legacy_schema_checkpoint_rejected_not_cleared(self):
        legacy = json.dumps({'schema': 1, 'run_id': 'run', 'completed': ['a'],
                             'attempted': ['a'], 'records': []})
        report = self._assert_rejected_untouched(legacy, plan_a())
        self.assertIn('legacy', report['results'][0]['detail'])

    def test_contradictory_records_rejected(self):
        digest = plan_identity(plan_a())
        record = {'action_id': 'a', 'kind': 'noop', 'status': 'success',
                  'detail': '', 'elapsed_ms': 0, 'evidence': {}}
        payload = {'schema': 2, 'run_id': 'run', 'status': 'unknown',
                   'plan_digest': digest,
                   'plan': [{'id': 'a', 'kind': 'noop', 'params': {},
                             'side_effecting': True, 'timeout': 15.0}],
                   'completed': [], 'attempted': ['a'], 'records': [record]}
        self._assert_rejected_untouched(json.dumps(payload), plan_a())

    # --- happy paths ------------------------------------------------------------
    def test_unchanged_plan_runs_and_resume_only_verifies(self):
        backend = FakeBackend()
        report = self.executor(backend).run(plan_a())
        self.assertEqual(report['status'], 'success')
        self.assertEqual(backend.calls, ['a'])
        resume_backend = FakeBackend()
        resume = self.executor(resume_backend).run(plan_a(), resume=True)
        self.assertEqual(resume['status'], 'success')
        self.assertEqual(resume_backend.calls, [])
        self.assertEqual(resume['records'][-1]['status'], 'skipped_verified')

    def test_late_postcondition_on_resume_confirms_without_resend(self):
        backend = FakeBackend()
        self.assertEqual(self.executor(backend).run(plan_a(expectation=false))['status'],
                         'unknown')
        resume_backend = FakeBackend()
        report = self.executor(resume_backend).run(plan_a(expectation=true), resume=True)
        self.assertEqual(report['status'], 'success')
        self.assertEqual(resume_backend.calls, [])
        self.assertEqual(report['records'][-1]['status'], 'skipped_verified')

    # --- timeout / observation loss / UNKNOWN ------------------------------------
    def test_timeout_then_resume_never_resends(self):
        backend = FakeBackend(timeouts=['a'])
        report = self.executor(backend).run(plan_a(expectation=false))
        self.assertEqual(report['status'], 'unknown')
        resume_backend = FakeBackend()
        resume = self.executor(resume_backend).run(plan_a(expectation=false), resume=True)
        self.assertEqual(resume['status'], 'unknown')
        self.assertEqual(resume_backend.calls, [])
        self.assertIn('refusing to resend', resume['results'][-1]['detail'])

    def test_observe_failure_never_reports_success_and_resume_holds(self):
        backend = FakeBackend(observe_fail_after=1)
        report = self.executor(backend).run(plan_a(expectation=false))
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('observation_error', report['results'][-1]['evidence'])
        resume_backend = FakeBackend(observe_fail_after=1)
        resume = self.executor(resume_backend).run(plan_a(expectation=false), resume=True)
        self.assertEqual(resume['status'], 'unknown')
        self.assertEqual(resume_backend.calls, [])

    def test_blocked_run_does_not_leave_a_stale_lock(self):
        from agent_wow_checkpoint import CheckpointLock
        peer = CheckpointLock(self.checkpoint, 'peer')
        peer.acquire()
        try:
            self.executor(FakeBackend()).run(plan_a())
        finally:
            peer.release()
        self.assertFalse((self.folder / 'checkpoint.json.lock').exists())
        after = CheckpointLock(self.checkpoint, 'run')
        after.acquire()
        after.release()

    def test_in_memory_mode_has_no_persistence(self):
        backend = FakeBackend()
        executor = TaskExecutor(backend, 'run')
        self.assertEqual(executor.run(plan_a())['status'], 'success')
        # A brand-new in-memory executor forgets everything: no cross-run
        # dedup exists without a checkpoint path.
        fresh_backend = FakeBackend()
        TaskExecutor(fresh_backend, 'run').run(plan_a())
        self.assertEqual(fresh_backend.calls, ['a'])
        # resume without durability is refused instead of pretending.
        report = TaskExecutor(FakeBackend(), 'run').run(plan_a(), resume=True)
        self.assertEqual(report['status'], 'unknown')

    def test_plan_validation_happens_before_lock_acquisition(self):
        from agent_wow_checkpoint import CheckpointLock
        peer = CheckpointLock(self.checkpoint, 'peer')
        peer.acquire()
        try:
            report = self.executor(FakeBackend()).run(
                [Action('a', 'noop'), Action('a', 'noop')])
        finally:
            peer.release()
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('duplicate action id', report['results'][0]['detail'])

    def test_run_rejects_reused_executor_after_completion(self):
        backend = FakeBackend()
        executor = self.executor(backend)
        self.assertEqual(executor.run(plan_a())['status'], 'success')
        second = executor.run(plan_a())
        self.assertEqual(second['status'], 'unknown')
        self.assertEqual(backend.calls, ['a'])

    # --- read-only reconcile tool -------------------------------------------
    def test_reconcile_tool_is_read_only_and_reports_semantics(self):
        backend = FakeBackend()
        self.executor(backend).run(plan_a(expectation=false))
        before = self.checkpoint.read_bytes()
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / 'agent_wow_checkpoint_reconcile.py'),
             str(self.checkpoint)],
            capture_output=True, cwd=str(SCRIPTS.parent))
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        report = json.loads(result.stdout.decode('utf-8'))
        # Without the live plan the tool must not claim identity success.
        self.assertEqual(report['verdict'], 'identity_unverified')
        self.assertEqual(report['details']['dispatched_but_unconfirmed'], ['a'])
        self.assertEqual(self.checkpoint.read_bytes(), before)
        plan_file = self.folder / 'plan.json'
        plan_file.write_text(json.dumps(
            [{'id': 'a', 'kind': 'noop', 'params': {}, 'side_effecting': True,
              'timeout': 15.0}]), encoding='utf-8')
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / 'agent_wow_checkpoint_reconcile.py'),
             str(self.checkpoint), '--plan', str(plan_file)],
            capture_output=True, cwd=str(SCRIPTS.parent))
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        report = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(report['verdict'], 'consistent')
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_reconcile_tool_verifies_plan_digest_with_plan_file(self):
        backend = FakeBackend()
        self.executor(backend).run(plan_a())
        plan_file = self.folder / 'plan.json'
        plan_file.write_text(json.dumps(
            [{'id': 'a', 'kind': 'noop', 'params': {}, 'side_effecting': True,
              'timeout': 15.0}]), encoding='utf-8')
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / 'agent_wow_checkpoint_reconcile.py'),
             str(self.checkpoint), '--plan', str(plan_file)],
            capture_output=True, cwd=str(SCRIPTS.parent))
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        report = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(report['verdict'], 'consistent')

    def test_public_export_credential_gate_passes_with_new_modules(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / 'check_public_export.py')],
            capture_output=True, cwd=str(SCRIPTS.parent))
        output = (result.stdout + result.stderr).decode('utf-8', 'replace')
        self.assertEqual(result.returncode, 0, output)
        report = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(report['issues'], [])


if __name__ == '__main__':
    unittest.main()
