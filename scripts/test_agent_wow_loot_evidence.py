import unittest

from agent_wow_loot_evidence import item_pushes


class ItemPushEvidenceTests(unittest.TestCase):
    def test_only_fresh_matching_player_and_entry(self):
        state = {'event_log': [
            {'sequence': 3, 'opcode': 0x166, 'loot': {'player_guid': '9', 'entry': 4862,
                                                      'received': 0, 'created': 0,
                                                      'count': 1, 'inventory_count': 1}},
            {'sequence': 5, 'opcode': 0x166, 'loot': {'player_guid': '10', 'entry': 4862,
                                                      'received': 0, 'created': 0,
                                                      'count': 1, 'inventory_count': 1}},
            {'sequence': 6, 'opcode': 0x166, 'loot': {'player_guid': '9', 'entry': 7101,
                                                      'received': 0, 'created': 0,
                                                      'count': 1, 'inventory_count': 1}},
            {'sequence': 7, 'opcode': 0x166, 'loot': {'player_guid': '9', 'entry': 4862,
                                                      'received': 0, 'created': 0,
                                                      'count': 1, 'inventory_count': 0}},
            {'sequence': 8, 'opcode': 0x166, 'loot': {'player_guid': '9', 'entry': 4862,
                                                      'received': 0, 'created': 0,
                                                      'count': 1, 'inventory_count': 1}},
        ]}
        self.assertEqual([p['sequence'] for p in item_pushes(state, 3, '9', 4862)], [8])

    def test_requests_are_not_item_pushes(self):
        state = {'event_log': [{'sequence': 10, 'opcode': 0x160,
                                'loot': {'player_guid': '9', 'entry': 4862, 'count': 1}},
                               {'sequence': 11, 'opcode': 0x166,
                                'loot': {'player_guid': '9', 'entry': 4862, 'count': 0,
                                         'inventory_count': 1}}]}
        self.assertEqual(item_pushes(state, 9, '9', 4862), [])

    def test_boolean_count_does_not_masquerade_as_one_item(self):
        state = {'event_log': [{'sequence': 12, 'opcode': 0x166,
                                'loot': {'player_guid': '9', 'entry': 4862,
                                         'received': 0, 'created': 0,
                                         'count': True, 'inventory_count': 1}}]}
        self.assertEqual(item_pushes(state, 11, '9', 4862), [])

    def test_npc_reward_is_not_loot_confirmation(self):
        state = {'event_log': [{'sequence': 12, 'opcode': 0x166,
                                'loot': {'player_guid': '9', 'entry': 4862,
                                         'received': 1, 'created': 0,
                                         'count': 1, 'inventory_count': 1}}]}
        self.assertEqual(item_pushes(state, 11, '9', 4862), [])

    def test_push_total_must_exceed_pre_take_inventory(self):
        state = {'event_log': [{'sequence': 12, 'opcode': 0x166,
                                'loot': {'player_guid': '9', 'entry': 4862,
                                         'received': 0, 'created': 0,
                                         'count': 1, 'inventory_count': 7}}]}
        self.assertEqual(item_pushes(state, 11, '9', 4862, 7), [])
        self.assertEqual(len(item_pushes(state, 11, '9', 4862, 6)), 1)
