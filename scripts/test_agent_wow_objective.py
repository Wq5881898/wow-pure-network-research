import unittest
from agent_wow_objective import progress, increased


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.goal = {'kind': 'kill', 'quest': 792, 'slot': 0, 'total': 8}

    def obs(self, count, quest=792):
        return {'quest_log': [{'quest': quest, 'counters': [count, 0]}]}

    def test_credit_and_missing_quest(self):
        self.assertTrue(increased(self.obs(0), self.obs(1), self.goal))
        self.assertFalse(increased(self.obs(0), self.obs(0), self.goal))
        self.assertFalse(increased(self.obs(0, 788), self.obs(1), self.goal))
        self.assertFalse(increased(self.obs(0), {'quest_log': []}, self.goal))
        self.assertFalse(increased(self.obs(8), self.obs(9), self.goal))

    def test_packed_slots(self):
        observation = {'quest_log': [{'quest': 792, 'counters': [1 | (2 << 16), 3 | (4 << 16)]}]}
        for slot in range(4):
            self.assertEqual(progress(observation, {**self.goal, 'slot': slot}), slot + 1)

    def test_duplicate_quest_entries(self):
        duplicated = {'quest_log': [
            {'quest': 792, 'counters': [6, 0]},
            {'quest': 792, 'counters': [7, 0]},
        ]}
        self.assertIsNone(progress(duplicated, self.goal))
        self.assertFalse(increased(self.obs(6), duplicated, self.goal))

    def test_slot3_high_bits_of_second_uint32(self):
        before = {'quest_log': [{'quest': 792,
                                 'counters': [0x0102 | (0x0304 << 16), 0x0506 | (6 << 16)]}]}
        after = {'quest_log': [{'quest': 792,
                                'counters': [0x0102 | (0x0304 << 16), 0x0506 | (7 << 16)]}]}
        slot3 = {**self.goal, 'slot': 3}
        self.assertEqual(progress(before, slot3), 6)
        self.assertEqual(progress(after, slot3), 7)
        self.assertTrue(increased(before, after, slot3))
        for slot, sentinel in ((0, 0x0102), (1, 0x0304), (2, 0x0506)):
            self.assertEqual(progress(after, {**self.goal, 'slot': slot}), sentinel)


if __name__ == '__main__':
    unittest.main()
