"""Batch budgets must not kill a still-supervising child."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
import json

import agent_wow_quest_batch as batch


class OwnerWaitTests(unittest.TestCase):
    def test_timeout_retains_child_until_it_finishes(self):
        child = Mock(pid=123)
        child.wait.side_effect = [subprocess.TimeoutExpired('owner', 1), 0]
        notify = Mock()
        with patch.object(batch.subprocess, 'Popen', return_value=child):
            result = batch._wait_for_owner(['owner'], window_seconds=1, on_timeout=notify)
        self.assertEqual(result.returncode, 0)
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
        self.assertEqual(child.wait.call_count, 2)
        notify.assert_called_once()

    def test_failed_diagnostic_does_not_abandon_owner(self):
        child = Mock(pid=123)
        child.wait.side_effect = [subprocess.TimeoutExpired('owner', 1), 0]
        with patch.object(batch.subprocess, 'Popen', return_value=child):
            result = batch._wait_for_owner(['owner'], window_seconds=1,
                                           on_timeout=Mock(side_effect=OSError('disk full')))
        self.assertEqual(result.returncode, 0)
        child.kill.assert_not_called()


class BatchEntryTests(unittest.TestCase):
    def test_no_quest_batch_does_not_send_none_to_runner(self):
        safe = {'self': {'health': 100, 'ghost': False, 'in_combat': False,
                         'quest_log': [], 'item_totals': {}}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def run(argv):
                run_id = argv[argv.index('--run-id') + 1]
                (root / ('agent_wow_task_' + run_id + '.json')).write_text(
                    json.dumps({'run': {'status': 'success', 'records': []}}), encoding='utf-8')
                return subprocess.CompletedProcess(argv, 0, '', '')
            with patch.object(batch, 'FOLDER', root), \
                    patch.object(sys, 'argv', ['batch', '--batch-id', 'offline', '--units', '2',
                                             '--objective-optional']), \
                    patch.object(batch, '_observe', return_value=safe), \
                    patch.object(batch, '_run', side_effect=run) as runner, \
                    patch('agent_wow_task_boundary.movement_boundary', return_value={}), \
                    patch('builtins.print'):
                batch.main()
            self.assertEqual(runner.call_count, 4)
            for call in runner.call_args_list:
                self.assertNotIn('--quest-id', call.args[0])
                self.assertNotIn('None', call.args[0])
            report = json.loads((root / 'agent_wow_quest_batch_offline.json').read_text())
            self.assertEqual(report['status'], 'batch_complete')
            self.assertEqual(len(report['units']), 2)


if __name__ == '__main__':
    unittest.main()
