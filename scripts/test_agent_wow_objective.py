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


if __name__ == '__main__':
    unittest.main()
