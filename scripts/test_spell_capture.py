import unittest
import base64
from agent_wow_worldstate import replay


class SpellCaptureTests(unittest.TestCase):
    def test_subscribed_spell_packets_preserved_not_interpreted(self):
        for opcode in (0x250, 0x24e):
            state = replay({'session_id': 'test', 'self_guid': '9', 'capture_complete': False,
                'packets': [{'sequence': 1, 'monotonic_ms': 0, 'opcode': opcode,
                             'payload_base64': base64.b64encode(b'raw').decode()}]})
            self.assertEqual(state['errors'], [])
            self.assertEqual(state['event_log'][0]['kind'], 'spell_damage_raw')
            self.assertEqual(state['event_log'][0]['payload_base64'], 'cmF3')
