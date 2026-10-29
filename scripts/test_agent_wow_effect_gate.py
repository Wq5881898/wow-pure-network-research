import json
import unittest
from pathlib import Path
from agent_wow_effect_gate import attempt_gate, effect_evidence
from agent_wow_worldstate import replay


class EffectTests(unittest.TestCase):
    def test_incomplete_coverage_blocks_prediction(self):
        view = {'usable_for_estimation': True, 'correction_coverage': 'incomplete'}
        self.assertFalse(attempt_gate(view, 2, 89, False, False)['allowed'])
        view['correction_coverage'] = 'verified'
        self.assertTrue(attempt_gate(view, 2, 89, False, False)['allowed'])
        self.assertFalse(attempt_gate(view, 2, 89, False, False, True)['allowed'])
        self.assertFalse(attempt_gate(view, float('nan'), 89, False, False)['allowed'])

    def test_real_damage_is_scoped_and_not_arrival(self):
        path = Path(__file__).resolve().parents[1] / 'results/pure_network/agent_wow_bridge_run15_attack.packets.json'
        state = replay(json.loads(path.read_text()))
        self.assertEqual(state['errors'], [])
        hits = [e for e in state['event_log'] if e.get('kind') == 'attacker_state'
                and e['attack']['attacker_guid'] == '9' and e['attack']['full_damage'] > 0]
        self.assertTrue(hits)
        hit = hits[0]
        target = hit['attack']['target_guid']
        result = effect_evidence(state, '9', target, hit['sequence'] - 1)
        self.assertTrue(result['damage_confirmed'])
        self.assertFalse(result['arrival_confirmed'])
        self.assertFalse(effect_evidence(state, '9', 'wrong-target', 0)['damage_confirmed'])
        self.assertFalse(effect_evidence(state, '9', target, state['packets'])['damage_confirmed'])


if __name__ == '__main__':
    unittest.main()
