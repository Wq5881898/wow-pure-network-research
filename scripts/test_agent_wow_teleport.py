import base64
import struct
import unittest
from agent_wow_teleport import decode_teleport
from agent_wow_position_context import PositionContext
from test_agent_wow_position_context import state


def payload(flags=0, extra=0, optional=b''):
    return b'\x01\x09' + struct.pack('<IIHI4f', 1, flags, extra, 100, 1, 2, 3, 0) + optional


class TeleportTests(unittest.TestCase):
    def test_basic_and_all_truncations(self):
        raw = payload(optional=struct.pack('<I', 0))
        self.assertEqual(decode_teleport(raw)['position_xyzo'], [1, 2, 3, 0])
        for n in range(len(raw)):
            with self.assertRaises(ValueError):
                decode_teleport(raw[:n])
        with self.assertRaises(ValueError):
            decode_teleport(raw + b'\x00')

    def test_conditional_fields(self):
        optional = b'\x00' + bytes(21) + bytes(4) + bytes(4) + bytes(4) + bytes(16) + bytes(4)
        result = decode_teleport(payload(0x200 | 0x200000 | 0x1000 | 0x4000000, 0x400, optional))
        self.assertEqual(result['guid'], '9')

    def test_teleport_invalidates_prediction_not_promoted_to_arrival(self):
        c = PositionContext()
        c.update(state(), '9', 1)
        c.submitted([4, 5, 6], 3, 'move', 2)
        s = state()
        s['event_log'].append({'kind': 'recovery', 'opcode': 0xc7, 'sequence': 4,
                              'payload_base64': base64.b64encode(payload(optional=bytes(4))).decode()})
        c.update(s, '9', 3)
        self.assertFalse(c.view(3)['usable_for_estimation'])
        self.assertFalse(c.view(3)['position_confirmed'])
        self.assertEqual(c.view(3)['teleport_evidence']['guid'], '9')


if __name__ == '__main__':
    unittest.main()
