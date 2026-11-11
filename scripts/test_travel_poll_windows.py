"""Long travel must not leave the bounded incoming queue unread."""
import math
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent_wow_travel import _bounded_leg


class BoundedLegTests(unittest.TestCase):
    def test_origin_segment_counts_and_clipped_point_stays_on_path(self):
        points = [{'x': x, 'y': 0, 'z': 0} for x in (3, 6, 9, 12)]
        clipped = _bounded_leg(points, [0, 0, 0], 10)
        self.assertEqual([p['x'] for p in clipped], [3, 6, 9, 10])
        self.assertEqual(points[-1]['x'], 12)

    def test_height_and_turn_are_counted(self):
        points = [{'x': 3, 'y': 0, 'z': 4}, {'x': 3, 'y': 10, 'z': 4}]
        clipped = _bounded_leg(points, [0, 0, 0], 7)
        self.assertEqual(clipped[-1], {'x': 3, 'y': 2, 'z': 4})


@unittest.skipUnless(shutil.which('ip'), 'backend import requires WSL')
class ChunkPollTests(unittest.TestCase):
    def test_each_path_chunk_is_followed_by_drain(self):
        from agent_wow_session import PureNetworkBackend
        backend = object.__new__(PureNetworkBackend)
        backend.run_id = 'offline_chunk_poll'
        backend.cursor = 0
        backend.position_context = SimpleNamespace(submitted=Mock())
        calls = []
        def rpc(method, params):
            calls.append('send')
            point = params['points'][-1]
            return {'result': {'submittedPosition': [point['x'], point['y'], point['z']]}}
        backend.rpc = rpc
        backend.drain = lambda: calls.append('drain')
        points = [{'x': x, 'y': 0, 'z': 0} for x in range(0, 94, 3)]
        replies, failure = backend._send_path(points)
        self.assertIsNone(failure)
        self.assertGreater(len(replies), 1)
        self.assertEqual(calls, ['send', 'drain'] * len(replies))


if __name__ == '__main__':
    unittest.main()
