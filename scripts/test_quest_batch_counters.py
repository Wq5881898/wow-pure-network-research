"""Batch counters use explicit item evidence and unpack the chosen kill slot."""
import unittest

from agent_wow_quest_batch import _counter


class BatchCounterTests(unittest.TestCase):
    def test_missing_inventory_is_unknown_not_zero(self):
        self.assertIsNone(_counter({'self': {}}, 794, 'item', 4859))
        self.assertEqual(_counter({'self': {'item_totals': {}}}, 794, 'item', 4859), 0)

    def test_item_progress_is_not_quest_kill_counter(self):
        report = {'self': {'item_totals': {'4859': 1}, 'quest_log': [
            {'quest': 794, 'counters': [0, 0]}]}}
        self.assertEqual(_counter(report, 794, 'item', 4859), 1)

    def test_item_count_malformed_is_unknown(self):
        for value in (-1, True, '1', None):
            self.assertIsNone(_counter({'self': {'item_totals': {'4859': value}}},
                                       794, 'item', 4859))

    def test_all_four_kill_slots_are_unpacked(self):
        report = {'self': {'quest_log': [{'quest': 792,
                                         'counters': [1 | (2 << 16), 3 | (4 << 16)]}]}}
        for slot in range(4):
            self.assertEqual(_counter(report, 792, slot=slot), slot + 1)

    def test_absent_or_duplicate_quest_is_unknown(self):
        self.assertIsNone(_counter({'self': {'quest_log': []}}, 792))
        quest = {'quest': 792, 'counters': [1, 0]}
        self.assertIsNone(_counter({'self': {'quest_log': [quest, quest]}}, 792))


if __name__ == '__main__':
    unittest.main()
