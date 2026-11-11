"""Regression guards for the position authority scanner.

The scanner answers the question the whole position story depends on: does
the server ever report the character's own position during a session? The
answer measured over the archived corpus is no, which is why the bridge's
"prefer the server position" branch never runs and why a distance built
from the submitted position is a model distance.

These tests pin the scanner's counting rules on synthetic captures, so the
measurement can be trusted when it is repeated on new archives.
"""
import base64
import json
import struct
import tempfile
import unittest
from pathlib import Path

import check_position_authority as scanner

SMSG_LOGIN_VERIFY_WORLD = 0x236
MSG_MOVE_TELEPORT = 0x0C5


def login_packet(sequence=1):
    payload = struct.pack('<I4f', 1, -142.3, -4370.2, 67.27, 0.0)
    return {'sequence': sequence, 'monotonic_ms': sequence,
            'opcode': SMSG_LOGIN_VERIFY_WORLD,
            'payload_base64': base64.b64encode(payload).decode()}


def raw_packet(sequence, opcode):
    return {'sequence': sequence, 'monotonic_ms': sequence, 'opcode': opcode,
            'payload_base64': base64.b64encode(b'\x00' * 8).decode()}


def write_capture(folder, name, packets):
    capture = {'schema': 'agent_wow_bridge_packets', 'session_id': 'aw-test',
               'self_guid': '9', 'capture_complete': True,
               'scope': 'synthetic', 'packets': packets}
    (Path(folder) / name).write_text(json.dumps(capture), encoding='utf-8')


class ScannerTests(unittest.TestCase):
    def test_a_session_reports_its_position_once_at_login(self):
        with tempfile.TemporaryDirectory() as folder:
            write_capture(folder, 'agent_wow_bridge_t1.packets.json',
                          [login_packet(1), raw_packet(2, 0xA9)])
            rows, _ = scanner.scan(folder)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['login_verify_world'], 1)
        self.assertEqual(row['login_verify_world_after_start'], 0)
        self.assertEqual(row['move_teleport'], 0)
        # The synthetic capture carries no object update for the character.
        self.assertEqual(row['self_movement_updates'], 0)

    def test_a_second_login_verify_is_counted_as_arriving_later(self):
        with tempfile.TemporaryDirectory() as folder:
            write_capture(folder, 'agent_wow_bridge_t2.packets.json',
                          [login_packet(1), login_packet(2)])
            rows, _ = scanner.scan(folder)
        self.assertEqual(rows[0]['login_verify_world'], 2)
        self.assertEqual(rows[0]['login_verify_world_after_start'], 1)

    def test_a_move_teleport_is_counted(self):
        with tempfile.TemporaryDirectory() as folder:
            write_capture(folder, 'agent_wow_bridge_t3.packets.json',
                          [login_packet(1), raw_packet(2, MSG_MOVE_TELEPORT)])
            rows, _ = scanner.scan(folder)
        self.assertEqual(rows[0]['move_teleport'], 1)


if __name__ == '__main__':
    unittest.main()
