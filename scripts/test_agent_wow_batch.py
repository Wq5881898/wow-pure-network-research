"""Coordinator tests use synthetic unit reports, not claimed live replay."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import agent_wow_batch
from agent_wow_batch import classify, coordinate


def report(before=6, after=7):
    rows = [{'action_id': name, 'status': 'success', 'evidence': {}}
            for name in ('verify_alive', 'select_target', 'approach', 'fight', 'loot', 'settle', 'logout')]
    rows[4].update(observation_before={'item_totals': {'4862': before}},
                   observation_after={'item_totals': {'4862': after}})
    rows[-1]['evidence'] = {'status': 'closed'}
    return {'run': {'status': 'success', 'records': rows}, 'final_observation': {
        'character': '9', 'health': 89, 'ghost': False, 'in_combat': False, 'item_totals': {'4862': after}}}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.calls = []

    def launch(self, run_id):
        self.calls.append(run_id)
        n = len(self.calls)
        # A real unit writes a unified two-session report; the coordinator reads
        # that unified file, so the synthetic launch must produce it too.
        (self.folder / f'agent_wow_task_{run_id}_unified.json').write_text(json.dumps(report(5+n, 6+n)))

    def test_goal_stops_and_restart_does_not_dispatch(self):
        state = coordinate(self.folder, 'test', 3, 8, self.launch)
        self.assertEqual(state['status'], 'GOAL_RECORDED')
        self.assertEqual(len(self.calls), 2)
        coordinate(self.folder, 'test', 3, 8, self.launch)
        self.assertEqual(len(self.calls), 2)

    def test_budget(self):
        state = coordinate(self.folder, 'test', 1, 8, self.launch)
        self.assertEqual(state['status'], 'BUDGET_REACHED')

    def test_failure_stops(self):
        def fail(run_id):
            self.calls.append(run_id)
            (self.folder / f'agent_wow_task_{run_id}_unified.json').write_text(json.dumps({'run': {'status': 'unknown'}}))
        state = coordinate(self.folder, 'test', 3, 8, fail)
        self.assertEqual(state['status'], 'STOPPED')
        self.assertEqual(len(self.calls), 1)

    def test_interrupted_dispatch_not_repeated(self):
        def interrupt(run_id):
            self.calls.append(run_id)
            raise TimeoutError('unknown effect')
        state = coordinate(self.folder, 'test', 3, 8, interrupt)
        self.assertEqual(state['status'], 'UNKNOWN')
        coordinate(self.folder, 'test', 3, 8, self.launch)
        self.assertEqual(len(self.calls), 1)

    def test_late_report_reconciled_without_resend(self):
        def interrupt(run_id):
            raise TimeoutError()
        coordinate(self.folder, 'test', 1, 8, interrupt)
        (self.folder / 'agent_wow_task_test_unit1_unified.json').write_text(json.dumps(report()))
        state = coordinate(self.folder, 'test', 1, 8, self.launch)
        self.assertEqual(state['status'], 'BUDGET_REACHED')
        self.assertEqual(self.calls, [])

    def test_false_success_rejected(self):
        data = report()
        data['final_observation']['in_combat'] = True
        self.assertEqual(classify(data)[0], 'UNKNOWN')

    def test_movement_only_report_is_not_confirmed_until_boundary_verified(self):
        # The movement-only session alone lacks fight/loot/settle/logout; it is
        # only the first half of a unit and must never count as CONFIRMED.
        movement_only = {'run': {'status': 'unknown', 'records': [
            {'action_id': 'verify_alive', 'status': 'success', 'evidence': {}},
            {'action_id': 'select_target', 'status': 'success', 'evidence': {'target': {}}},
            {'action_id': 'approach', 'status': 'unknown', 'evidence': {'reached': False}}]}}
        self.assertEqual(classify(movement_only)[0], 'STOPPED')

    def test_synthesized_approach_record_classifies_confirmed(self):
        # A unified report is one verification session: the approach was
        # confirmed only across the session boundary (synthesized record), and
        # settle/logout were executed in the verification session.
        data = report()
        data['run']['records'] = [r for r in data['run']['records']
                                  if r['action_id'] in ('verify_alive', 'fight', 'loot', 'settle', 'logout')]
        data['run']['records'].insert(1, {
            'action_id': 'approach', 'kind': 'approach', 'status': 'success',
            'detail': 'submitted endpoint confirmed by fresh-session login', 'elapsed_ms': 0,
            'evidence': {'reached': True, 'confirmed_by': 'fresh_session_login',
                         'distance': 2.1e-05, 'submitted_position': [-710.6, -4344.7, 47.0]},
            'observation_before': {}, 'observation_after': {}})
        # The verification session has no select_target/approach before the
        # loot, so the loot baseline must come from an earlier observation.
        data['run']['records'][0]['observation_before'] = {'item_totals': {'4862': 6}}
        self.assertEqual(classify(data)[0], 'CONFIRMED')

    def test_loot_confirmed_by_final_observation_counts(self):
        # A loot whose in-session echo missed the new total still counts when
        # the loot was received and the fresh final observation is higher.
        data = report()
        for row in data['run']['records']:
            if row['action_id'] == 'loot':
                row['status'] = 'unknown'
                row['observation_after'] = {'item_totals': {'4862': 5}}
                row['evidence'] = {'received': [4862]}
        self.assertEqual(classify(data)[0], 'CONFIRMED')

    def test_loot_without_receipt_needs_in_session_total(self):
        data = report()
        for row in data['run']['records']:
            if row['action_id'] == 'loot':
                row['status'] = 'unknown'
                row['observation_after'] = {'item_totals': {'4862': 5}}
                row['evidence'] = {'received': None}
        self.assertEqual(classify(data)[0], 'UNKNOWN')

    def test_changed_configuration_rejected(self):
        coordinate(self.folder, 'test', 1, 8, self.launch)
        with self.assertRaises(ValueError):
            coordinate(self.folder, 'test', 3, 8, self.launch)

    def test_launch_uses_full_process_pattern_and_two_session_phases(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            check = (command[0] == 'wsl' and command[-1] == '--pgrep-check-only') or \
                    (command[0] == 'sh' and '--pgrep-check-only' in ' '.join(command))
            if check:
                return subprocess.CompletedProcess(command, 0, '__pgrep_start__\n__pgrep_done__\n', '')
            folder = Path(temp) / 'results' / 'pure_network'
            folder.mkdir(parents=True, exist_ok=True)
            run_id = command[command.index('--run-id') + 1]
            if run_id.endswith('_verify'):
                (folder / f'agent_wow_task_{run_id[:-len("_verify")]}_unified.json').write_text(
                    json.dumps(report(5, 6)))
            else:
                (folder / f'agent_wow_task_{run_id}.json').write_text(json.dumps(report(5, 6)))
            return subprocess.CompletedProcess(command, 0, '', '')

        with tempfile.TemporaryDirectory() as temp, patch.object(agent_wow_batch, 'ROOT', Path(temp)), \
                patch.object(Path, 'unlink', lambda self, **kwargs: None), \
                patch.object(agent_wow_batch.shutil, 'which', lambda name: '/mock/wsl'), \
                patch.object(agent_wow_batch.subprocess, 'run', side_effect=fake_run), \
                patch('sys.argv', ['batch', '--batch-id', 'main', '--limit', '1', '--goal', '8',
                                   '--combat-budget', '11']):
            agent_wow_batch.main()

        def is_pgrep(command):
            return command[0] == 'wsl' and command[-1] == '--pgrep-check-only'

        pgrep = [c for c in calls if is_pgrep(c[0])]
        self.assertEqual(len(pgrep), 2)
        self.assertIn(agent_wow_batch.ACTIVE_PROCESS_PATTERN, pgrep[0][0][-2])
        self.assertEqual(pgrep[0][0][-4:-2], ['sh', '-c'])
        runner = [c[0] for c in calls if not is_pgrep(c[0])]
        self.assertEqual(len(runner), 2)
        movement, verify = runner
        self.assertIn('--movement-only', movement)
        self.assertNotIn('--verify-movement-from', movement)
        self.assertEqual(movement[movement.index('--combat-budget') + 1], '11.0')
        self.assertIn('--verify-movement-from', verify)
        self.assertIn('--combat-after-verification', verify)
        self.assertEqual(verify[verify.index('--run-id') + 1], 'main_unit1_verify')
        self.assertTrue(verify[verify.index('--verify-movement-from') + 1].endswith('agent_wow_task_main_unit1.json'))

    def test_existing_session_blocks_launch(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(agent_wow_batch, 'ROOT', Path(temp)), \
                patch.object(agent_wow_batch.shutil, 'which', lambda name: None), \
                patch.object(agent_wow_batch.subprocess, 'run',
                             return_value=subprocess.CompletedProcess(
                                 [], 0, '123 agent-wow-cli\n', '')), \
                patch('sys.argv', ['batch', '--batch-id', 'busy_launch', '--limit', '1', '--goal', '8']):
            agent_wow_batch.main()
            state = json.loads((Path(temp) / 'results' / 'pure_network' / 'agent_wow_batch_busy_launch.json').read_text())
        self.assertEqual(state['status'], 'UNKNOWN')
        self.assertIn('existing task or bridge session is active', state['units'][0]['error'])


if __name__ == '__main__':
    unittest.main()
