"""Keep bridge capacity and evidence interpretation separate."""
import unittest

from audit_retreat_atomicity import audit


class AtomicityTests(unittest.TestCase):
    def test_two_short_chunks_still_need_fresh_start(self):
        result = audit([[[0, 0, 0], [30, 0, 0]],
                        [[30, 0, 0], [60, 0, 0]]])
        self.assertFalse(result['one_call_full_route_possible'])
        self.assertEqual(result['minimum_calls_from_distance_only'], 2)

    def test_waypoint_limit_also_blocks_one_call(self):
        chunk = [[i / 10, 0, 0] for i in range(75)]
        result = audit([chunk, [[7.4 + i / 10, 0, 0] for i in range(75)]])
        self.assertLess(result['total_horizontal_length'], 50)
        self.assertGreater(result['combined_waypoints'], 128)
        self.assertFalse(result['one_call_full_route_possible'])


if __name__ == '__main__':
    unittest.main()
