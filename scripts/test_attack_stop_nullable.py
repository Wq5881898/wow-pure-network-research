import unittest
from agent_wow_worldstate import attack_stop, DecodeError


class NullableAttackStopTests(unittest.TestCase):
    def test_nullable_enemy_from_server(self):
        self.assertEqual(attack_stop(bytes([1, 9])), {
            'attacker_guid': '9', 'target_guid': None, 'target_dead': None})

    def test_partial_guid_still_rejected(self):
        with self.assertRaises(DecodeError):
            attack_stop(bytes([3, 9]))
