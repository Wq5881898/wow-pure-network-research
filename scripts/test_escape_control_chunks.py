"""The escape chunks must be accepted by the bridge.

The bridge validates every path it is given (probes/AgentWowBridge/actions.go,
validatePath) and refuses the WHOLE path when one segment breaks a rule.
A refused path means the character does not move at all, which is how a
retreat turned into standing still and being killed (2026-11-01 b29_u1,
b36_u1 and b39_u1). These tests pin the bridge's rule onto the chunker.
"""
import math
import unittest

from agent_wow_escape_control import (MAX_HEIGHT_YARDS, MAX_POINTS,
                                      MAX_SEGMENT_YARDS, MAX_TOTAL_YARDS,
                                      _chunk_ok, _chunks)


def bridge_accepts(chunk, anchor):
    """The bridge's own rule, copied from actions.go."""
    if not chunk or len(chunk) > MAX_POINTS:
        return False, 'point count'
    previous = list(anchor[:3])
    total = 0.0
    for point in chunk:
        distance = math.hypot(point['x'] - previous[0], point['y'] - previous[1])
        if distance > MAX_SEGMENT_YARDS or abs(point['z'] - previous[2]) > MAX_HEIGHT_YARDS:
            return False, 'segment'
        total += distance
        previous = [point['x'], point['y'], point['z']]
    if total < .05 or total > MAX_TOTAL_YARDS:
        return False, 'length'
    return True, round(total, 3)


def line(from_xyz, to_xyz, step=3.0):
    distance = math.dist(from_xyz, to_xyz)
    count = max(1, int(distance / step))
    return [{'x': from_xyz[0] + (to_xyz[0] - from_xyz[0]) * i / count,
             'y': from_xyz[1] + (to_xyz[1] - from_xyz[1]) * i / count,
             'z': from_xyz[2] + (to_xyz[2] - from_xyz[2]) * i / count}
            for i in range(1, count + 1)]


class EscapeChunkTests(unittest.TestCase):
    def test_every_chunk_is_accepted_by_the_bridge(self):
        origin = [0.0, 0.0, 0.0]
        route = line([0.0, 0.0, 0.0], [120.0, 0.0, 0.0])
        chunks = _chunks(route, origin)
        self.assertGreater(len(chunks), 1)
        anchor = origin
        for chunk in chunks:
            ok, detail = bridge_accepts(chunk, anchor)
            self.assertTrue(ok, 'chunk refused by the bridge: %s' % detail)
            anchor = [chunk[-1]['x'], chunk[-1]['y'], chunk[-1]['z']]

    def test_the_first_segment_counts_from_the_character(self):
        # The bridge measures from its own tracked position, so the chunk
        # that starts there must include that segment. Leaving it out made
        # a 50 yard chunk look like 51.09 to the bridge.
        origin = [0.0, 0.0, 0.0]
        route = [{'x': 1.0, 'y': 0.0, 'z': 0.0}] + line([1.0, 0.0, 0.0],
                                                        [200.0, 0.0, 0.0])
        chunks = _chunks(route, origin)
        ok, detail = bridge_accepts(chunks[0], origin)
        self.assertTrue(ok, detail)

    def test_a_chunk_never_collapses_to_a_single_point(self):
        # A one point chunk has a length of 0 and the bridge refuses it
        # ("path length must be .05..50 yards", 2026-11-01 b39_u1).
        origin = [0.0, 0.0, 0.0]
        route = [{'x': 1.0, 'y': 0.0, 'z': 0.0},
                 {'x': 1.2, 'y': 0.0, 'z': 0.0}]
        chunks = _chunks(route, origin)
        for chunk in chunks:
            ok, detail = bridge_accepts(chunk, origin)
            self.assertTrue(ok, detail)

    def test_a_point_that_cannot_fit_is_skipped_not_fatal(self):
        origin = [0.0, 0.0, 0.0]
        route = [{'x': 0.5, 'y': 0.0, 'z': 0.0},
                 {'x': 40.0, 'y': 0.0, 'z': 0.0},
                 {'x': 44.0, 'y': 0.0, 'z': 0.0}]
        chunks = _chunks(route, origin)
        anchor = origin
        for chunk in chunks:
            ok, detail = bridge_accepts(chunk, anchor)
            self.assertTrue(ok, detail)
            anchor = [chunk[-1]['x'], chunk[-1]['y'], chunk[-1]['z']]

    def test_chunk_ok_rejects_a_bridge_illegal_chunk(self):
        self.assertFalse(_chunk_ok([{'x': 20.0, 'y': 0.0, 'z': 0.0}],
                                    [0.0, 0.0, 0.0]))
        self.assertFalse(_chunk_ok([{'x': 1.0, 'y': 0.0, 'z': 5.0}],
                                    [0.0, 0.0, 0.0]))
        self.assertFalse(_chunk_ok([{'x': 0.0, 'y': 0.0, 'z': 0.0}],
                                    [0.0, 0.0, 0.0]))


if __name__ == '__main__':
    unittest.main()
