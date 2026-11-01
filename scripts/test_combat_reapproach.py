"""Guards for the bounded re-approach against a kiting target.

A roaming caster (entry 3101) steps back as the character closes, so a fight
that never moves can never finish. The combat controller re-approaches, but
only within limits:

  - a target that is still nearby is KITING and is walked to again;
  - a target that has gone further away has LEFT and is not chased, because
    chasing it would drag the character into unknown ground;
  - the number of attempts is bounded, and the fight's own budget bounds the
    whole thing.
"""
import unittest

from agent_wow_combat_control import fight


class FakeRpc:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params):
        self.calls.append(method)
        return {'result': {}}


class World:
    """A world where the target steps back once, then stays in reach."""

    def __init__(self, target_positions, self_position):
        self.target_positions = list(target_positions)
        self.self_position = list(self_position)
        self.index = 0

    def __call__(self):
        position = self.target_positions[min(self.index, len(self.target_positions) - 1)]
        self.index += 1
        return {'self': {'health': 100, 'max_health': 108, 'ghost': False,
                         'in_combat': True, 'health_transmitted': True,
                         'movement': {'position_xyzo': self.self_position + [0.0]},
                         'level': 3, 'quest_log': []},
                'visible_objects': [{'guid': 'mob', 'entry': 3101, 'health': 69,
                                     'level': 3, 'max_health': 69,
                                     'movement': {'position_xyzo': position + [0.0]}}]}


class ReapproachTests(unittest.TestCase):
    def test_a_kiting_target_is_walked_to_again(self):
        # The target steps from 3 yards to 9: that is kiting, so it is
        # re-approached rather than abandoned.
        world = World([[3, 0, 0], [9, 0, 0], [3, 0, 0]], [0, 0, 0])
        rpc = FakeRpc()
        walks = []

        def reapproach():
            walks.append(True)
            world.self_position = [7, 0, 0]
            return {'moved': True, 'reference': [7, 0, 0]}

        phase, _ = fight(rpc, world, 'mob', [0, 0, 0], lambda: 0.0, budget=10,
                         reapproach=reapproach)
        self.assertTrue(walks, 'a kiting target must be walked to again')
        self.assertLessEqual(len(walks), 3, 'the re-approach must be bounded')

    def test_a_target_that_left_is_not_chased(self):
        # The target moves far away: that is leaving, not kiting, and chasing
        # it would drag the character into unknown ground.
        world = World([[3, 0, 0], [60, 0, 0]], [0, 0, 0])
        rpc = FakeRpc()
        walks = []
        phase, _ = fight(rpc, world, 'mob', [0, 0, 0], lambda: 0.0, budget=10,
                         reapproach=lambda: (walks.append(True),
                                            {'moved': True, 'reference': [0, 0, 0]})[1])
        self.assertEqual(walks, [], 'a target that left must not be chased')
        self.assertEqual(phase['outcome'], 'target_left_melee')

    def test_without_a_reapproach_callback_the_target_is_still_abandoned(self):
        world = World([[3, 0, 0], [9, 0, 0]], [0, 0, 0])
        rpc = FakeRpc()
        phase, _ = fight(rpc, world, 'mob', [0, 0, 0], lambda: 0.0, budget=10)
        self.assertEqual(phase['outcome'], 'target_left_melee')


if __name__ == '__main__':
    unittest.main()
