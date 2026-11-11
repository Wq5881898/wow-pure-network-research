import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_wow_activity import ActivityRecorder


class ActivityTests(unittest.TestCase):
    def test_nested_wait_and_error_have_times_and_clear_current_state(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / 'activity'
            recorder = ActivityRecorder(base)
            with self.assertRaises(ValueError):
                with recorder.phase('quest'):
                    with recorder.phase('rpc_wait', method='session.logout'):
                        current = json.loads(base.with_suffix('.active.json').read_text())
                        self.assertEqual([r['phase'] for r in current['operations']], ['quest','rpc_wait'])
                        raise ValueError('test')
            rows = [json.loads(line) for line in base.with_suffix('.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r['outcome'] == 'raised' and r['elapsed_seconds'] >= 0 for r in rows))
            self.assertEqual(json.loads(base.with_suffix('.active.json').read_text())['operations'], [])

    def test_long_wait_marker_is_written_before_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / 'activity'
            recorder = ActivityRecorder(base)
            with patch('agent_wow_activity.threading.Timer') as timer:
                with recorder.phase('navigation_query'):
                    timer.call_args.args[1]()
                    rows = base.with_suffix('.jsonl').read_text()
                    self.assertIn('long_operation', rows)


if __name__ == '__main__':
    unittest.main()
