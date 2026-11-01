"""Guards for the danger-zone corpse run.

The corpse run is the project's designated research item, and it is the
recovery path that the Spirit Healer cannot stand in for: the healer puts
the character back at the graveyard and says nothing about whether the body
can be reached.

These tests pin the parts that must not silently regress:
  - the corpse position comes from the server's own answer,
  - the route is planned on the navmesh, never as a straight line,
  - a character that is not a ghost is refused instead of running at nothing,
  - a missing corpse or a missing route is reported, never faked.
"""
import unittest

from agent_wow_corpse_run import (corpse_position_from_events, plan_route,
                                   run_corpse_run)


class Observation:
    def __init__(self, health=1, ghost=True, in_combat=False):
        self.health = health
        self.ghost = ghost
        self.in_combat = in_combat


class FakeBackend:
    def __init__(self, events=None, corpse=None, self_objects=None):
        self.events = events or []
        self.corpse = corpse
        self.self_objects = self_objects or []
        self.requests = []
        self.positions = [[0.0, 0.0, 0.0]]
        self.routes = []
        self.self_guid = '9'

    def drain(self):
        self.requests.append('drain')
        if len(self.self_objects) > 1:
            latest = self.self_objects.pop(0)
        else:
            latest = self.self_objects[-1] if self.self_objects else Observation()
        # The production drain() returns the raw world state, whose 'self' is
        # a dict keyed by field name.
        return {'self': {'health': latest.health, 'ghost': latest.ghost,
                         'in_combat': latest.in_combat,
                         'movement': {'position_xyzo': list(self.positions[-1]) + [0.0]}},
                'visible_objects': [],
                'event_log': self.events}

    def rpc(self, method, params):
        self.requests.append('rpc:%s' % method)
        return {'result': {}}

    def _nav(self, start, destination):
        self.routes.append(('nav', list(start), list(destination)))
        if self.corpse is None:
            raise RuntimeError('nav path unavailable: navigation probe: no endpoint polygon')
        return {'waypoints': [start, destination]}

    def _densify(self, route, start, step=3, vertical=2):
        points = []
        previous = list(start)
        for end in route['waypoints']:
            distance = max(abs(end[i] - previous[i]) for i in range(3))
            count = max(1, int(distance // step) + 1)
            for index in range(1, count + 1):
                point = [previous[i] + (end[i] - previous[i]) * index / count
                         for i in range(3)]
                points.append(dict(zip(('x', 'y', 'z'), point)))
                previous = point
        return points

    def _send_path(self, points, run=False):
        self.requests.append('walk:%d' % len(points))
        return [{'result': {}}], None


class CorpseRunTests(unittest.TestCase):
    def test_the_corpse_position_comes_from_the_server_answer(self):
        events = [{'kind': 'corpse', 'corpse': {'found': True, 'map': 0,
                                                'position_xyz': [1.0, 2.0, 3.0]}}]
        corpse = corpse_position_from_events(events)
        self.assertEqual(corpse['position_xyz'], [1.0, 2.0, 3.0])

    def test_a_corpse_event_without_a_position_is_not_a_corpse(self):
        self.assertIsNone(corpse_position_from_events(
            [{'kind': 'corpse', 'corpse': {'found': False}}]))
        self.assertIsNone(corpse_position_from_events([]))

    def test_a_character_that_is_not_a_ghost_is_refused(self):
        backend = FakeBackend(self_objects=[Observation(health=108, ghost=False)])
        report = {}
        result = run_corpse_run(backend, report=report, query_timeout=0.1,
                                walk_timeout=0.1, reclaim_timeout=0.1)
        self.assertEqual(result['status'], 'not_a_ghost')
        self.assertNotIn('rpc:bridge.query_corpse', backend.requests)

    def test_a_dead_character_is_released_before_the_corpse_is_queried(self):
        # The server only makes the character a ghost after the release, and
        # the corpse query needs the ghost. Skipping the release is why the
        # first in-session death reported no corpse at all.
        backend = FakeBackend(
            events=[{'kind': 'corpse', 'corpse': {'found': True, 'map': 0,
                                                  'position_xyz': [10.0, 0.0, 0.0]}}],
            corpse=[10.0, 0.0, 0.0],
            self_objects=[Observation(health=0, ghost=False),
                          Observation(health=1, ghost=True),
                          Observation(health=54, ghost=False)])
        report = {}
        result = run_corpse_run(backend, report=report, query_timeout=0.1,
                                walk_timeout=0.1, reclaim_timeout=0.1)
        self.assertIn('rpc:bridge.release_spirit', backend.requests)
        self.assertEqual(result['status'], 'reclaimed')


    def test_a_missing_corpse_is_reported_not_faked(self):
        backend = FakeBackend(events=[], self_objects=[Observation()])
        report = {}
        result = run_corpse_run(backend, report=report, query_timeout=0.1, walk_timeout=0.1, reclaim_timeout=0.1)
        self.assertEqual(result['status'], 'no_corpse_reported')
        self.assertIn('rpc:bridge.query_corpse', backend.requests)

    def test_the_route_is_planned_on_the_navmesh(self):
        backend = FakeBackend(corpse=[1.0, 2.0, 3.0])
        route = plan_route(backend, [0.0, 0.0, 0.0], [1.0, 2.0, 3.0])
        self.assertIsNotNone(route)
        self.assertTrue(backend.routes)
        self.assertEqual(backend.routes[0][0], 'nav')

    def test_an_unwalkable_destination_is_reported_not_substituted(self):
        backend = FakeBackend(corpse=None)
        self.assertIsNone(plan_route(backend, [0.0, 0.0, 0.0], [1.0, 2.0, 3.0]))

    def test_a_stale_bridge_position_is_detected_before_walking(self):
        # A release teleports the ghost to the graveyard without telling the
        # bridge, so the bridge still validates the path against the place
        # the character fell at and rejects the first segment (revive02).
        backend = FakeBackend(
            events=[{'kind': 'corpse', 'corpse': {'found': True, 'map': 0,
                                                  'position_xyz': [10.0, 0.0, 0.0]}}],
            corpse=[10.0, 0.0, 0.0],
            self_objects=[Observation(health=1, ghost=True)])
        backend.submitted_position = [-700.0, -4340.0, 48.0]
        report = {}
        result = run_corpse_run(backend, report=report, query_timeout=0.1,
                                walk_timeout=0.1, reclaim_timeout=0.1)
        self.assertEqual(result['status'], 'bridge_position_stale')
        self.assertGreater(result['bridge_position_drift'], 10.0)
        self.assertNotIn('rpc:bridge.reclaim_corpse', backend.requests)

    def test_the_full_run_queries_walks_and_reclaims(self):
        backend = FakeBackend(
            events=[{'kind': 'corpse', 'corpse': {'found': True, 'map': 0,
                                                  'position_xyz': [10.0, 0.0, 0.0]}}],
            corpse=[10.0, 0.0, 0.0],
            self_objects=[Observation(health=1, ghost=True),
                          Observation(health=54, ghost=False)])
        report = {}
        result = run_corpse_run(backend, report=report, query_timeout=0.1, walk_timeout=0.1, reclaim_timeout=0.1)
        self.assertEqual(result['status'], 'reclaimed')
        self.assertTrue(result['reclaimed'])
        self.assertIn('rpc:bridge.query_corpse', backend.requests)
        self.assertIn('rpc:bridge.reclaim_corpse', backend.requests)
        self.assertTrue(any(r.startswith('walk:') for r in backend.requests))
        self.assertIn('corpse_run', report)


if __name__ == '__main__':
    unittest.main()
