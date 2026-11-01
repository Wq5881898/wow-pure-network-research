"""Guards for the bridge's path validation rules.

The bridge rejects any path whose segments exceed 15 yards or whose total
length exceeds 50 yards (bridge modules/bridge/actions.go, validatePath).
A rejected path is not a failed attack: the character simply does not move,
which is what left it in the hostile's aggro range on 2026-11-01 (esc014).

These tests pin the chunking rules the escape walk depends on.
"""
import unittest

from agent_wow_escape_control import MAX_SEGMENT_YARDS, MAX_TOTAL_YARDS, _chunks


def _route(segments, step=10.0):
    points = []
    x = 0.0
    for _ in range(segments):
        points.append({'x': x, 'y': 0.0, 'z': 0.0})
        x += step
    return points


class PathChunkingTests(unittest.TestCase):
    def test_a_long_route_is_split_into_bridge_sized_pieces(self):
        chunks = _chunks(_route(10, 10.0))
        self.assertGreater(len(chunks), 1, 'a 90 yard route cannot be one path')
        for chunk in chunks:
            length = 0.0
            previous = None
            for point in chunk:
                if previous is not None:
                    length += ((point['x'] - previous[0]) ** 2
                               + (point['y'] - previous[1]) ** 2) ** 0.5
                previous = (point['x'], point['y'], point['z'])
            self.assertLessEqual(length, MAX_TOTAL_YARDS)

    def test_a_short_route_is_sent_whole(self):
        chunks = _chunks(_route(2, 5.0))
        self.assertEqual(len(chunks), 1)

    def test_no_segment_exceeds_the_bridge_limit(self):
        # The route is densified to ~3 yard steps, so a single densified
        # segment is far below the limit; the chunker must not merge points
        # that are far apart.
        chunks = _chunks(_route(4, 40.0))
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            for index in range(1, len(chunk)):
                step = ((chunk[index]['x'] - chunk[index - 1]['x']) ** 2
                        + (chunk[index]['y'] - chunk[index - 1]['y']) ** 2) ** 0.5
                self.assertLessEqual(step, MAX_SEGMENT_YARDS)

    def test_the_limits_match_the_bridge(self):
        self.assertEqual(MAX_SEGMENT_YARDS, 15.0)
        self.assertEqual(MAX_TOTAL_YARDS, 50.0)


if __name__ == '__main__':
    unittest.main()
