"""Historical item-push events are parsed but cannot prove a new take request."""
import json
import unittest
from pathlib import Path

from agent_wow_loot_evidence import item_pushes
from agent_wow_worldstate import replay


class RealTraceLootGateTests(unittest.TestCase):
    def test_archived_loot_response_and_push_have_distinct_evidence(self):
        root = Path(__file__).resolve().parents[1]
        trace = json.loads((root / 'results/pure_network/agent_wow_bridge_run24_loot.packets.json').read_bytes())
        state = replay(trace)
        self.assertFalse(state['errors'])
        responses = [e for e in state['event_log'] if e.get('opcode') == 0x160]
        pushes = [e for e in state['event_log'] if e.get('opcode') == 0x166]
        self.assertTrue(responses)
        self.assertTrue(pushes)
        push = pushes[0]
        self.assertEqual(push['loot']['player_guid'], trace['self_guid'])
        self.assertEqual(push['loot']['entry'], 7098)
        self.assertEqual(push['loot']['received'], 0)
        self.assertEqual(push['loot']['created'], 0)
        self.assertEqual(push['loot']['count'], 1)
        self.assertEqual(push['loot']['inventory_count'], 1)
        self.assertEqual([e['sequence'] for e in item_pushes(
            state, responses[0]['sequence'], trace['self_guid'], 7098, 0)],
            [push['sequence']])
        self.assertEqual(item_pushes(state, push['sequence'], trace['self_guid'], 7098, 0), [])


if __name__ == '__main__':
    unittest.main()
