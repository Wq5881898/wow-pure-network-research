"""Unit tests for checkpoint identity, semantic validation and the lock."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_wow_checkpoint import (
    CheckpointLock, CheckpointRejected, canonical_json, plan_identity,
    read_checkpoint, validate_checkpoint)
from agent_wow_contract import Action


def actions(kind='noop', params=None, side=True, timeout=15.0, ids=('a', 'b')):
    return [Action(action_id, kind, dict(params or {}), timeout=timeout,
                   side_effecting=side)
            for action_id in ids]


def checkpoint_payload(digest, run_id='run', schema=2, plan=None, completed=(),
                       attempted=(), records=(), status='unknown'):
    plan = plan if plan is not None else [
        {'id': 'a', 'kind': 'noop', 'params': {}, 'side_effecting': True,
         'timeout': 15.0},
        {'id': 'b', 'kind': 'noop', 'params': {}, 'side_effecting': True,
         'timeout': 15.0}]
    return {'schema': schema, 'run_id': run_id, 'status': status,
            'plan_digest': digest, 'plan': plan,
            'completed': list(completed), 'attempted': list(attempted),
            'records': list(records)}


def success_record(action_id):
    return {'action_id': action_id, 'kind': 'noop', 'status': 'success',
            'detail': '', 'elapsed_ms': 0, 'evidence': {}}


class PlanIdentityTests(unittest.TestCase):
    def test_digest_is_reproducible_and_order_sensitive(self):
        first = plan_identity(actions())
        self.assertEqual(first, plan_identity(actions()))
        swapped = [Action('b', 'noop'), Action('a', 'noop')]
        self.assertNotEqual(first, plan_identity(swapped))

    def test_digest_binds_kind_params_side_effecting_timeout(self):
        base = plan_identity(actions())
        self.assertNotEqual(base, plan_identity(actions(kind='fight')))
        self.assertNotEqual(base, plan_identity(actions(params={'x': 1})))
        self.assertNotEqual(base, plan_identity(actions(side=False)))
        self.assertNotEqual(base, plan_identity(actions(timeout=16.0)))

    def test_params_key_order_does_not_change_digest(self):
        left = plan_identity([Action('a', 'noop', {'x': 1, 'y': 2})])
        right = plan_identity([Action('a', 'noop', {'y': 2, 'x': 1})])
        self.assertEqual(left, right)

    def test_canonical_json_is_stable(self):
        self.assertEqual(canonical_json({'b': 1, 'a': [1, 2]}),
                         canonical_json({'a': [1, 2], 'b': 1}))

    def test_rejects_bad_plans(self):
        with self.assertRaises(ValueError):
            plan_identity([Action('', 'noop')])
        with self.assertRaises(ValueError):
            plan_identity([Action('a', 'noop'), Action('a', 'noop')])
        with self.assertRaises(ValueError):
            plan_identity([Action('a', 'noop', {'x': float('nan')})])
        with self.assertRaises(ValueError):
            plan_identity([Action('a', 'noop', {}, timeout=float('inf'))])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.digest = plan_identity(actions())

    def validate(self, payload, digest=None):
        return validate_checkpoint(payload, 'run',
                                   self.digest if digest is None else digest)

    def test_valid_checkpoint_passes(self):
        payload = checkpoint_payload(self.digest, completed=['a', 'b'],
                                     attempted=['a', 'b'],
                                     records=[success_record('a'), success_record('b')])
        self.assertIs(self.validate(payload), payload)

    def test_non_object_top_level_rejected(self):
        with self.assertRaises(CheckpointRejected):
            validate_checkpoint(['not', 'an', 'object'], 'run', self.digest)

    def test_wrong_run_id_rejected(self):
        with self.assertRaisesRegex(CheckpointRejected, 'run_id'):
            self.validate(checkpoint_payload(self.digest, run_id='other'))

    def test_legacy_schema_refuses_instead_of_clearing(self):
        legacy = {'schema': 1, 'run_id': 'run', 'completed': ['a'],
                  'attempted': ['a'], 'records': []}
        with self.assertRaisesRegex(CheckpointRejected, 'legacy'):
            validate_checkpoint(legacy, 'run', self.digest)

    def test_future_schema_rejected(self):
        with self.assertRaisesRegex(CheckpointRejected, 'schema'):
            self.validate(checkpoint_payload(self.digest, schema=3))

    def test_plan_digest_mismatch_rejected(self):
        with self.assertRaisesRegex(CheckpointRejected, 'plan identity'):
            self.validate(checkpoint_payload('deadbeef'))

    def test_missing_plan_digest_rejected(self):
        payload = checkpoint_payload(None, completed=[], attempted=[], records=[])
        with self.assertRaisesRegex(CheckpointRejected, 'plan_digest'):
            self.validate(payload)

    def test_duplicate_action_ids_in_completed_rejected(self):
        payload = checkpoint_payload(self.digest, completed=['a', 'a'],
                                     attempted=['a'],
                                     records=[success_record('a')])
        with self.assertRaisesRegex(CheckpointRejected, 'duplicate'):
            self.validate(payload)

    def test_completed_outside_plan_rejected(self):
        payload = checkpoint_payload(self.digest, completed=['ghost'],
                                     records=[success_record('ghost')])
        payload['plan'] = [{'id': 'a', 'kind': 'noop', 'params': {},
                            'side_effecting': True, 'timeout': 15.0}]
        with self.assertRaisesRegex(CheckpointRejected, 'outside the plan'):
            self.validate(payload)

    def test_record_with_unknown_action_id_rejected(self):
        payload = checkpoint_payload(self.digest, records=[success_record('zz')])
        with self.assertRaisesRegex(CheckpointRejected, 'unknown action ID'):
            self.validate(payload)

    def test_record_with_unknown_status_rejected(self):
        record = success_record('a')
        record['status'] = 'probably_fine'
        payload = checkpoint_payload(self.digest, completed=['a'],
                                     attempted=['a'], records=[record])
        with self.assertRaisesRegex(CheckpointRejected, 'unknown status'):
            self.validate(payload)

    def test_completed_without_success_record_rejected(self):
        payload = checkpoint_payload(self.digest, completed=['a'],
                                     attempted=['a'], records=[])
        with self.assertRaisesRegex(CheckpointRejected, 'without a successful'):
            self.validate(payload)

    def test_success_record_missing_from_completed_rejected(self):
        payload = checkpoint_payload(self.digest, completed=[],
                                     attempted=['a'],
                                     records=[success_record('a')])
        with self.assertRaisesRegex(CheckpointRejected, 'not present in completed'):
            self.validate(payload)

    def test_duplicate_success_records_rejected(self):
        payload = checkpoint_payload(self.digest, completed=['a'],
                                     attempted=['a'],
                                     records=[success_record('a'), success_record('a')])
        with self.assertRaisesRegex(CheckpointRejected, 'duplicate successful'):
            self.validate(payload)

    def test_side_effecting_completed_without_attempted_rejected(self):
        payload = checkpoint_payload(self.digest, completed=['a'],
                                     attempted=[],
                                     records=[success_record('a')])
        with self.assertRaisesRegex(CheckpointRejected, 'attempted record'):
            self.validate(payload)

    def test_non_side_effecting_completed_needs_no_attempted(self):
        plan = [{'id': 'a', 'kind': 'noop', 'params': {},
                 'side_effecting': False, 'timeout': 15.0}]
        digest = plan_identity([Action('a', 'noop')])
        payload = checkpoint_payload(digest, plan=plan, completed=['a'],
                                     attempted=[], records=[success_record('a')])
        self.assertIs(self.validate(payload, digest), payload)

    def test_read_checkpoint_reports_corrupt_bytes_without_touching_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'checkpoint.json'
            path.write_text('{"schema": 2, run id', encoding='utf-8')
            before = path.read_bytes()
            with self.assertRaisesRegex(CheckpointRejected, 'valid UTF-8 JSON'):
                read_checkpoint(path, 'run', self.digest)
            self.assertEqual(path.read_bytes(), before)


class CheckpointLockTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.checkpoint = self.folder / 'checkpoint.json'

    def lock(self, run_id='run'):
        return CheckpointLock(self.checkpoint, run_id)

    def test_acquire_write_release_cycle(self):
        lock = self.lock()
        lock.acquire()
        self.assertTrue(lock.path.exists())
        holder = json.loads(lock.path.read_text(encoding='utf-8'))
        self.assertEqual(holder['run_id'], 'run')
        self.assertTrue(holder['owner_id'])
        self.assertEqual(holder['pid'], os.getpid())
        lock.release()
        self.assertFalse(lock.path.exists())

    def test_second_holder_is_blocked_with_diagnostics(self):
        first = self.lock()
        first.acquire()
        second = self.lock()
        with self.assertRaises(CheckpointRejected) as caught:
            second.acquire()
        diagnostics = json.loads(str(caught.exception))
        self.assertEqual(diagnostics['holder_run_id'], 'run')
        self.assertEqual(diagnostics['holder_owner_id'], first.owner_id)
        self.assertFalse(second.held)
        first.release()
        third = self.lock()
        third.acquire()
        third.release()

    def test_release_never_steals_another_owner_lock(self):
        first = self.lock()
        first.acquire()
        first.held = False  # simulate a holder that lost interest but not the file
        first.release()
        self.assertTrue(first.path.exists())
        other = self.lock()
        other.held = True
        other.owner_id = 'ghost'
        other.release()
        self.assertTrue(first.path.exists())

    def test_stale_lock_blocks_conservatively_and_is_not_stolen(self):
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        lock = self.lock()
        lock.path.write_text(json.dumps({'owner_id': 'gone', 'run_id': 'run',
                                         'pid': 999999}), encoding='utf-8')
        with self.assertRaises(CheckpointRejected):
            lock.acquire()
        # No auto-preemption by age or unknown PID: the file stays.
        self.assertTrue(lock.path.exists())

    def test_malformed_stale_lock_still_blocks(self):
        lock = self.lock()
        lock.path.write_text('not json at all', encoding='utf-8')
        with self.assertRaises(CheckpointRejected):
            lock.acquire()
        self.assertTrue(lock.path.exists())

    def test_release_after_external_removal_is_quiet(self):
        lock = self.lock()
        lock.acquire()
        # Windows cannot unlink an open file, so close the handle first to
        # simulate another actor removing the lock file underneath us.
        os.close(lock._fd)
        lock._fd = None
        lock.path.unlink()
        lock.release()  # must not raise
        self.assertFalse(lock.path.exists())

    def test_different_checkpoints_do_not_share_a_lock(self):
        first = CheckpointLock(self.folder / 'cp-a.json', 'run')
        second = CheckpointLock(self.folder / 'cp-b.json', 'run')
        first.acquire()
        second.acquire()
        second.release()
        first.release()


if __name__ == '__main__':
    unittest.main()
