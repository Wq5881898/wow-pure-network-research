import unittest
import json
from pathlib import Path
from agent_wow_position import landing_evidence, confirm_relogin


class PositionEvidenceTests(unittest.TestCase):
    def test_health_update_does_not_confirm_landing(self):
        state = {'self': {'last_seq': 20, 'movement': {'position_xyzo': [1, 2, 3, 0]}},
                 'event_log': [{'sequence': 3, 'guid': '9', 'movement': {}},
                               {'sequence': 20, 'guid': '9', 'fields': {'health': 89}}]}
        evidence = landing_evidence(state, '9', 3, [10, 20, 30])
        self.assertFalse(evidence['confirmed'])
        state['event_log'].append({'sequence': 21, 'guid': '9', 'movement': {}})
        self.assertFalse(landing_evidence(state, '9', 3, [10, 20, 30])['confirmed'])
        self.assertTrue(landing_evidence(state, '9', 3, [1, 2, 3])['confirmed'])

    def test_real_submission_confirmed_by_next_login(self):
        folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
        source = json.loads((folder / 'agent_wow_bridge_run100_task_unit.packets.json').read_text())
        trace = json.loads((folder / 'agent_wow_bridge_run101_collection_verify.packets.json').read_text())
        report = json.loads((folder / 'agent_wow_task_run100_task_unit.json').read_text())
        expected = next(r['evidence']['submitted_position'] for r in report['run']['records']
                        if r['action_id'] == 'approach')
        evidence = confirm_relogin(source['session_id'], expected, 1, trace)
        self.assertTrue(evidence['confirmed'])
        self.assertFalse(confirm_relogin(source['session_id'], expected, 0, trace)['confirmed'])
        with self.assertRaises(ValueError):
            confirm_relogin(trace['session_id'], expected, 1, trace)
        with self.assertRaises(ValueError):
            confirm_relogin(source['session_id'], [float('nan'), 0, 0], 1, trace)
        with self.assertRaises(ValueError):
            confirm_relogin(source['session_id'], expected, 1, trace, character='10')


if __name__ == '__main__':
    unittest.main()
