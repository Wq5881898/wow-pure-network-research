import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_wow_contract import Action, Observation, TaskExecutor


class FakeBackend:
    def __init__(self, effects=None, errors=None, timeouts=None, items=None):
        self.effects = effects or {}
        self.errors = errors or {}
        self.timeouts = timeouts or {}
        self.items = items or {}
        self.calls = []
        self.observations = 0

    def observe(self):
        self.observations += 1
        return Observation(backend='fake', character='9', session_id='s', sequence=self.observations,
                           sampled_at=0.0, health=89, max_health=89, in_combat=False,
                           item_totals=dict(self.items))

    def execute(self, action, timeout=None):
        self.calls.append(action.id)
        if action.id in self.timeouts:
            raise TimeoutError('backend timeout')
        if action.id in self.errors:
            return {'error': self.errors[action.id]}
        effect = self.effects.get(action.id, {})
        self.items = effect.get('items', self.items)
        return effect


def plan(**kwargs):
    return [
        Action('a', 'noop', {}, precondition=kwargs.get('precondition'),
               expectation=kwargs.get('expectation_a'), side_effecting=kwargs.get('side_effecting', False)),
        Action('b', 'noop', {}, side_effecting=True,
               expectation=kwargs.get('expectation_b')),
    ]


class TaskExecutorTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.log = self.folder / 'actions.jsonl'
        self.checkpoint = self.folder / 'checkpoint.json'

    def executor(self, backend, **kwargs):
        return TaskExecutor(backend, 'run', log_path=self.log, checkpoint_path=self.checkpoint, **kwargs)

    def test_success_path_records_log_and_checkpoint(self):
        backend = FakeBackend(effects={'a': {}, 'b': {}})
        executor = self.executor(backend)
        report = executor.run(plan(expectation_a=lambda b, a, e: True, expectation_b=lambda b, a, e: True))
        self.assertEqual(report['status'], 'success')
        self.assertEqual([c for c in backend.calls], ['a', 'b'])
        self.assertEqual(self.checkpoint.exists(), True)
        self.assertEqual(json.loads(self.checkpoint.read_text())['completed'], ['a', 'b'])
        lines = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([l['status'] for l in lines], ['success', 'success'])
        self.assertIn('observation_after', lines[0])

    def test_precondition_failure_stops_and_logs(self):
        backend = FakeBackend()
        executor = self.executor(backend)
        report = executor.run(plan(precondition=lambda o: False))
        self.assertEqual(report['status'], 'failure')
        self.assertEqual(backend.calls, [])
        record = json.loads(self.log.read_text().splitlines()[0])
        self.assertEqual(record['status'], 'failure')
        self.assertEqual(record['detail'], 'precondition not met')

    def test_side_effect_without_postcondition_is_unknown(self):
        backend = FakeBackend()
        executor = self.executor(backend)
        report = executor.run(plan(expectation_a=lambda b, a, e: True, expectation_b=lambda b, a, e: False,
                                   side_effecting=True))
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(report['results'][-1]['detail'], 'request applied but postcondition not observed')

    def test_rejected_request_is_failure_not_unknown(self):
        backend = FakeBackend(errors={'a': 'rejected'})
        executor = self.executor(backend)
        report = executor.run(plan(expectation_a=lambda b, a, e: True))
        self.assertEqual(report['status'], 'failure')
        self.assertEqual(report['results'][-1]['detail'], 'rejected')

    def test_timeout_of_side_effecting_action_is_unknown(self):
        backend = FakeBackend(timeouts={'a'})
        executor = self.executor(backend)
        report = executor.run(plan(side_effecting=True, expectation_a=lambda b, a, e: True))
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('effect state unknown', report['results'][-1]['detail'])

    def test_backend_exception_after_dispatch_is_unknown_not_rejected(self):
        class FaultBackend(FakeBackend):
            def execute(self, action, timeout=None):
                raise OSError('reply lost after request')

        report = self.executor(FaultBackend()).run(
            [Action('a', 'noop', {}, side_effecting=True,
                    expectation=lambda b, a, e: True)])
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('OSError', report['results'][-1]['evidence']['error'])

    def test_explicit_no_dispatch_fails_even_with_generic_error(self):
        backend = FakeBackend(effects={'a': {'error': 'request refused before sending',
                                            'side_effect_applied': False}})
        report = self.executor(backend).run(
            [Action('a', 'noop', {}, side_effecting=True,
                    expectation=lambda b, a, e: False)])
        self.assertEqual(report['status'], 'failure')
        self.assertIn('no side effect applied', report['results'][-1]['detail'])

    def test_postaction_observation_failure_blocks_success(self):
        class LostObservation(FakeBackend):
            def observe(self):
                if self.observations >= 1:
                    raise OSError('poll lost')
                return super().observe()

        report = self.executor(LostObservation()).run(
            [Action('a', 'noop', {}, side_effecting=True,
                    expectation=lambda b, a, e: True)])
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('observation_error', report['results'][-1]['evidence'])

    def test_budget_pauses_before_execution(self):
        backend = FakeBackend()
        executor = self.executor(backend, budget_seconds=0)
        report = executor.run(plan())
        self.assertEqual(report['status'], 'paused')
        self.assertEqual(backend.calls, [])

    def test_resume_skips_verified_action(self):
        backend = FakeBackend(effects={'a': {}})
        first = self.executor(backend)
        first.run([Action('a', 'noop', {}, expectation=lambda b, a, e: True)])
        second = self.executor(backend)
        report = second.run([Action('a', 'noop', {}, expectation=lambda b, a, e: True)], resume=True)
        self.assertEqual(report['status'], 'success')
        self.assertEqual(backend.calls, ['a'])
        self.assertEqual(json.loads(self.log.read_text().splitlines()[-1])['status'], 'skipped_verified')

    def test_resume_refuses_to_resend_unconfirmed_side_effect(self):
        backend = FakeBackend(effects={'a': {}})
        first = self.executor(backend)
        first.run([Action('a', 'noop', {}, side_effecting=True, expectation=lambda b, a, e: False)])
        backend.calls.clear()
        second = self.executor(backend)
        report = second.run([Action('a', 'noop', {}, side_effecting=True, expectation=lambda b, a, e: False)], resume=True)
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(backend.calls, [])
        self.assertIn('refusing to resend', report['results'][-1]['detail'])

    def test_checkpoint_with_wrong_run_id_is_rejected(self):
        self.checkpoint.write_text(json.dumps({'schema': 1, 'run_id': 'other',
                                                'completed': ['a'], 'attempted': [], 'records': []}))
        backend = FakeBackend(effects={'a': {}, 'b': {}})
        report = self.executor(backend).run(plan(expectation_a=lambda b, a, e: True,
                                                  expectation_b=lambda b, a, e: True))
        self.assertEqual(report['status'], 'success')
        self.assertEqual(backend.calls, ['a', 'b'])
        self.assertEqual(json.loads(self.checkpoint.read_text())['run_id'], 'run')

    def test_paused_checkpoint_is_persisted(self):
        backend = FakeBackend()
        report = self.executor(backend, action_budget=0).run(plan())
        self.assertEqual(report['status'], 'paused')
        self.assertEqual(json.loads(self.checkpoint.read_text())['status'], 'paused')

    def test_backend_overrun_of_side_effect_is_unknown(self):
        class SlowBackend(FakeBackend):
            def execute(self, action, timeout=None):
                time.sleep(.02)
                return super().execute(action, timeout)

        backend = SlowBackend(effects={'a': {}, 'b': {}})
        report = self.executor(backend).run(
            [Action('a', 'noop', {}, timeout=.001, side_effecting=True,
                    expectation=lambda b, a, e: True)])
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('executor_deadline_exceeded', report['results'][-1]['evidence'])


if __name__ == '__main__':
    unittest.main()
