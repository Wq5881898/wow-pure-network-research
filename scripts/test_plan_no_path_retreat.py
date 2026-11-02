"""Unit tests for plan_no_path_retreat (the NO_PATH separation search)."""
import math
import unittest

from plan_no_path_retreat import find_separation_point, nav_query


def fake_nav(origin, end, map_id=1, timeout=30):
    """A navmesh split by a wall at x=0.

    The character stands on the x>=0 side, the hostile on the x<=0 side, so a
    spot on the character's side is reachable for the character and
    unreachable for the hostile -- exactly the NO_PATH situation.
    """
    if (origin[0] >= 0) != (end[0] >= 0):
        return {'ok': False, 'waypoints': [], 'error': 'no path'}
    return {'ok': True, 'waypoints': [list(origin), list(end)], 'error': ''}


class SeparationSearchTest(unittest.TestCase):
    def test_finds_a_destination_the_hostile_cannot_reach(self):
        verdict = find_separation_point([10.0, 0.0, 0.0], [-5.0, 0.0, 0.0],
                                        nav=fake_nav, angles=8, radii=(45.0,))
        self.assertIsNotNone(verdict['destination'])
        self.assertFalse(verdict['hostile_reachable'])
        self.assertGreaterEqual(verdict['destination'][0], 0.0)
        self.assertLessEqual(verdict['character_path_length'], 45.0)

    def test_reports_no_separation_when_everywhere_is_reachable(self):
        def everywhere(origin, end, map_id=1, timeout=30):
            return {'ok': True, 'waypoints': [list(origin), list(end)], 'error': ''}

        verdict = find_separation_point([10.0, 0.0, 0.0], [-5.0, 0.0, 0.0],
                                        nav=everywhere, angles=8, radii=(45.0,))
        self.assertIsNone(verdict['destination'])
        self.assertTrue(verdict['attempts'])

    def test_unreachable_candidates_are_skipped_not_accepted(self):
        def nothing(origin, end, map_id=1, timeout=30):
            return {'ok': False, 'waypoints': [], 'error': 'no path'}

        verdict = find_separation_point([10.0, 0.0, 0.0], [-5.0, 0.0, 0.0],
                                        nav=nothing, angles=8, radii=(45.0,))
        self.assertIsNone(verdict['destination'])
        self.assertTrue(all(a['character_reachable'] is False
                            for a in verdict['attempts']))

    def test_rejects_a_destination_beyond_the_bridge_reach(self):
        def nav(origin, end, map_id=1, timeout=30):
            return {'ok': True, 'waypoints': [list(origin), list(end)], 'error': ''}

        verdict = find_separation_point([10.0, 0.0, 0.0], [-5.0, 0.0, 0.0],
                                        nav=nav, angles=8, radii=(85.0,))
        self.assertIsNone(verdict['destination'])

    def test_validates_the_endpoints(self):
        with self.assertRaises(ValueError):
            nav_query([0.0, 0.0], [1.0, 1.0, 1.0])
        with self.assertRaises(ValueError):
            find_separation_point([0.0, 0.0, 0.0], None, nav=fake_nav)


if __name__ == '__main__':
    unittest.main()
