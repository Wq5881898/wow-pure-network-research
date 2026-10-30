"""Synthetic boundary tests proposed by Qwen and reviewed by the controller.

These fixtures do not establish agreement with server serialization.
"""
import struct
import unittest

from agent_wow_worldstate import DecodeError, quest_packet


def packet():
    return (struct.pack('<Q', 0xF80000000123) + b'Well met\0'
            + struct.pack('<IIB', 5000, 1, 1)
            + struct.pack('<IIiIB', 789, 2, -3, 0x400, 1)
            + b'Synthetic quest\0')


class QuestListProposalTests(unittest.TestCase):
    def test_single_quest_fields(self):
        self.assertEqual(quest_packet(0x185, packet()), {
            'guid': str(0xF80000000123), 'greeting': 'Well met',
            'emote_delay': 5000, 'emote': 1,
            'quests': [{'quest': 789, 'icon': 2, 'level': -3,
                        'flags': 0x400, 'repeatable': 1, 'title': 'Synthetic quest'}]})

    def test_missing_title_terminator(self):
        with self.assertRaisesRegex(DecodeError, 'unterminated'):
            quest_packet(0x185, packet()[:-1])

    def test_trailing_bytes(self):
        with self.assertRaisesRegex(DecodeError, 'trailing bytes'):
            quest_packet(0x185, packet() + b'\xff')


if __name__ == '__main__':
    unittest.main()
