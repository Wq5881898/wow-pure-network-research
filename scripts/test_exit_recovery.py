"""Regression guards for the 2026-11-01 combat death.

The defect: a bounded unit fought a mob that fled at low health. The fight
action ended while the character was still flagged in combat, the executor
stopped the plan, and the exit path refused to log out *because* the character
was in combat. The session was then torn down with the character still in
combat and the server killed it.

These tests encode the rule that a living character must never be left in
combat for the session teardown to decide.
"""
import unittest

from agent_wow_exit_recovery import close_session, living, resolve_combat_before_exit


class Observation:
    def __init__(self, health, ghost=False, in_combat=False, target_health=None,
                 target_max_health=None, max_health=108):
        self.health = health
        self.ghost = ghost
        self.in_combat = in_combat
        self.max_health = max_health
        self.context = {'target_health': target_health,
                        'target_max_health': target_max_health}



class FakeBackend:
    """Serves a scripted sequence of observations and records every request."""

    def __init__(self, observations, logout_result=None, retreat_clears_combat=False):
        self.observations = list(observations)
        self.requests = []
        self.logout_result = (logout_result if logout_result is not None
                              else {'status': 'closed', 'reply': {'result': {'status': 'closed'}}})
        # No outbound route by default: without one there is no justified escape.
        self.outbound_route = []
        self.outbound_origin = None
        # Models route B: once the character has walked the reversed route out
        # of the hostile's reach, the server clears the combat flag.
        self.retreat_clears_combat = retreat_clears_combat
        self.target = {'guid': 'mob-1', 'entry': 3124, 'health': 71, 'level': 3,
                       'movement': {'position_xyzo': [0.0, 0.0, 0.0]}}
        # The target's reachability decides whether the kill ending is
        # executable: a target that has broken contact cannot be fought from
        # where the character stands (2026-11-01 huntD).
        self.target_reachable = True

        self.current_position = [0.0, 0.0, 0.0]
        self.observed_position = [0.0, 0.0, 0.0]
        # Models route A: the target dies once the character attacks again.
        self.target_dies = False

    def stream_now(self):
        return 1000.0

    def _current(self):
        """The effective state, with the route A/B effects applied."""
        latest = self.observations[-1]
        if self.target_dies and 'rpc:bridge.start_attack' in self.requests:
            return Observation(latest.health, ghost=latest.ghost, in_combat=False,
                               target_health=0)
        if (self.retreat_clears_combat
                and 'rpc:bridge.follow_path' in self.requests
                and latest.in_combat):
            return Observation(latest.health, ghost=latest.ghost, in_combat=False,
                               target_health=latest.context.get('target_health'))
        return latest

    def observe(self):
        self.requests.append('observe')
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self._current()

    def execute(self, action):
        self.requests.append('execute:%s' % action.kind)
        return self.logout_result

    def rpc(self, method, params):
        self.requests.append('rpc:%s' % method)
        return {'result': {}}

    def drain(self):
        self.requests.append('drain')
        # The production drain() returns the raw world state, whose 'self' is
        # a dict keyed by field name (not an attribute-bearing object).
        latest = self._current()
        in_combat = latest.in_combat
        target_health = latest.context.get('target_health')
        if target_health is None:
            target_health = 71
        if self.target_dies and 'rpc:bridge.start_attack' in self.requests:
            # The target dies and the server clears the combat flag with it.
            target_health = 0
            in_combat = False
        elif self.retreat_clears_combat and 'rpc:bridge.follow_path' in self.requests:
            in_combat = False
        return {'self': {'health': latest.health, 'ghost': latest.ghost,
                         'in_combat': in_combat},
                'visible_objects': [{'guid': self.target['guid'],
                                     'entry': self.target['entry'],
                                     'health': target_health,
                                     'level': self.target['level'],
                                     'movement': {'position_xyzo': [0.0, 0.0, 0.0, 0.0]}}]}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0
        return self.now


def sleeper(_seconds):
    return None


class ExitRecoveryTests(unittest.TestCase):
    def test_living_ignores_combat_state(self):
        self.assertTrue(living(Observation(71, in_combat=True)))
        self.assertFalse(living(Observation(0)))
        self.assertFalse(living(Observation(71, ghost=True)))

    def test_resolve_waits_for_combat_to_clear_without_attacking(self):
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        result = resolve_combat_before_exit(backend, budget_seconds=10,
                                            sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertTrue(result['combat_cleared'])
        self.assertEqual(result['final'].in_combat, False)
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)
        self.assertNotIn('rpc:bridge.face', backend.requests)

    def test_resolve_stops_the_characters_own_attack_first(self):
        # esc008: the escape branch observed while the character kept hitting
        # the mob, so the mob died and the resulting combat clear was reported
        # as a successful escape. The branch must stop its own attack before
        # it observes anything.
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        result = resolve_combat_before_exit(backend, budget_seconds=10,
                                            sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertIn('rpc:bridge.stop_attack', backend.requests)
        self.assertEqual(backend.requests.index('rpc:bridge.stop_attack'), 1,
                         'the stop must precede the first observation')
        self.assertEqual(result['actions'][0], 'stop_attack')

    def test_resolve_reports_uncleared_combat_without_acting(self):
        backend = FakeBackend([Observation(71, in_combat=True)])
        result = resolve_combat_before_exit(backend, budget_seconds=3,
                                            sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertFalse(result['combat_cleared'])
        self.assertIsNotNone(result['reason'])
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)

    def test_resolve_stops_at_the_health_floor(self):
        # Waiting for the server to clear combat must never be what kills the
        # character: below the floor the wait aborts and says so. With a
        # route available the character gets out instead of fighting on.
        backend = FakeBackend([Observation(20, in_combat=True)])
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                              {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            min_health=25, sleep=sleeper,
                                            finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)


    def test_resolve_does_not_abort_above_the_health_floor(self):
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            min_health=25, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertTrue(result['combat_cleared'])

    def test_passive_stage_clears_when_the_mob_stopped_hitting(self):
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=3, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertTrue(result['combat_cleared'])
        self.assertEqual(result['cleared_reason'], 'server_cleared')
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)

    def test_a_kill_is_never_reported_as_an_escape(self):
        # The esc008 ending: the combat clears because the hostile died.
        # That must be distinguishable from leaving the hostile's reach.
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=False, target_health=0)])
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=3, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertTrue(result['combat_cleared'])
        self.assertEqual(result['cleared_reason'], 'target_died')

    def test_retreat_ending_is_reported_as_leaving_reach(self):
        backend = FakeBackend([Observation(71, in_combat=True)],
                              retreat_clears_combat=True)
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                                  {'x': 3.0, 'y': 0.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertIn('rpc:bridge.follow_path', backend.requests)
        self.assertTrue(result['combat_cleared'])
        self.assertEqual(result['cleared_reason'], 'left_reach')
        self.assertEqual(result['requests'], 'observation + stop_attack + one retreat')

    def test_the_retreat_result_is_a_usable_observation(self):
        # The retreat helper hands back the raw world-state dict, whose 'self'
        # is keyed by field name. Assigning it as the final observation made
        # the caller read `.in_combat` off a dict and raise AttributeError.
        backend = FakeBackend([Observation(71, in_combat=True)],
                              retreat_clears_combat=True)
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                              {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertTrue(result['combat_cleared'])
        self.assertFalse(result['final'].in_combat)

    def test_persistent_hostile_triggers_the_reverse_route_retreat(self):
        # The measured failure mode (esc003): the mob keeps hitting, so the
        # passive stage cannot end the combat and a retreat is required.
        backend = FakeBackend([Observation(71, in_combat=True)])
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                                  {'x': 3.0, 'y': 0.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertIn('rpc:bridge.stop_attack', backend.requests,
                      'a persistent hostile must trigger the retreat branch')
        self.assertIn('rpc:bridge.follow_path', backend.requests,
                      'the retreat must use the reversed outbound route')
        self.assertIsNotNone(result['retreat'])

    def test_without_an_escape_route_the_character_fights_instead_of_idling(self):
        # esc010/esc013/esc014 all ended with the character standing still
        # while a hostile kept hitting. With no route available, fighting is
        # strictly better: same damage, but it can end the combat.
        backend = FakeBackend([Observation(83, in_combat=True)])
        backend.target_dies = True
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertIn('rpc:bridge.start_attack', backend.requests,
                      'without a route the character must fight, not idle')
        self.assertIn('finish_target', result['actions'])
        self.assertEqual(result['fallback']['outcome'], 'target_dead')
        self.assertEqual(result['cleared_reason'], 'target_died')

    def test_the_fight_fallback_is_reported_as_a_kill_not_an_escape(self):
        backend = FakeBackend([Observation(83, in_combat=True)])
        backend.target_dies = True
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertNotEqual(result.get('cleared_reason'), 'left_reach')

    def test_no_retreat_without_a_known_route(self):
        # Without a submitted route there is no justified escape; inventing a
        # straight line is the shortcut docs/51 rejects.
        backend = FakeBackend([Observation(71, in_combat=True)])
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertIsNone(result['retreat'])
        self.assertNotIn('rpc:bridge.follow_path', backend.requests)

    def test_a_planned_escape_route_is_preferred_over_the_short_approach(self):
        # esc010: reversing a ~6 yard approach route left the character inside
        # the hostile's aggro range and it was killed. A planned route that
        # ends far away must be used instead.
        backend = FakeBackend([Observation(71, in_combat=True)],
                              retreat_clears_combat=True)
        backend.plan_escape_route = lambda min_distance=40.0: {
            'points': [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                       {'x': 0.0, 'y': 40.0, 'z': 0.0}],
            'origin': [0.0, 0.0, 0.0],
            'source': 'navmesh route planned away from the hostile',
            'planned_distance': 40.0}
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                                  {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertEqual(result['retreat']['route']['source'],
                         'navmesh route planned away from the hostile')
        self.assertEqual(result['retreat']['route']['planned_distance'], 40.0)
        # The submitted path must be the planned one, split into the sizes the
        # bridge accepts (at most 15 yards per segment, 50 per path). A 40
        # yard route fits in one path, but it must be sent in segments the
        # bridge accepts, so the steps are subdivided.
        follow = [r for r in backend.requests if r == 'rpc:bridge.follow_path']
        self.assertGreaterEqual(len(follow), 1,
                                'the planned escape route must be sent')

        self.assertGreater(result['retreat']['route_length'], 30.0)

    def test_the_short_approach_route_is_the_fallback(self):
        backend = FakeBackend([Observation(71, in_combat=True)],
                              retreat_clears_combat=True)
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                                  {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertEqual(result['retreat']['route']['source'],
                         'reversed submitted outbound route')

    def test_a_mob_that_keeps_hitting_ends_the_wait_immediately(self):
        # esc003 and esc013: waiting cannot end the combat while the hostile
        # keeps hitting, and standing still only spends the character's
        # health. The wait must end as soon as the damage is observed.
        backend = FakeBackend([Observation(83, in_combat=True),
                               Observation(76, in_combat=True),
                               Observation(70, in_combat=True)],
                              retreat_clears_combat=True)
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                              {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=20, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertIn('rpc:bridge.follow_path', backend.requests,
                      'a hostile that keeps hitting must trigger the retreat')
        self.assertLessEqual(len(result['samples']), 4,
                             'the wait must not run the full passive window')

    def test_a_stationary_mob_gets_the_full_passive_window(self):
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=20, sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertTrue(result['combat_cleared'])
        self.assertEqual(result['cleared_reason'], 'server_cleared')

    def test_a_nearly_dead_target_is_killed_not_escaped_from(self):
        # esc020: the target was at 22 of 71 HP and the character at 91 of
        # 108, yet the escape route was taken: the character ran 87 yards,
        # took 60 damage and the combat did not clear. One hit would have
        # ended it.
        backend = FakeBackend([Observation(91, in_combat=True,
                                           target_health=22, target_max_health=71)])
        backend.target_dies = True
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                                  {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper,
                                            finish_budget_seconds=0.3, clock=Clock())
        self.assertIn('finish_target', result['actions'])
        self.assertNotIn('rpc:bridge.follow_path', backend.requests,
                         'a nearly dead target must not be escaped from')
        self.assertEqual(result['cleared_reason'], 'target_died')

    def test_a_healthy_target_is_escaped_from(self):
        backend = FakeBackend([Observation(91, in_combat=True,
                                           target_health=71, target_max_health=71)],
                              retreat_clears_combat=True)
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                                  {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper,
                                            finish_budget_seconds=0.3, clock=Clock())
        self.assertIn('retreat', result['actions'])
        self.assertNotIn('finish_target', result['actions'])
        self.assertEqual(result['cleared_reason'], 'left_reach')

    def test_no_route_means_fighting_even_for_a_healthy_target(self):
        # Idling while a hostile keeps hitting guarantees death, so with no
        # route the character must fight.
        backend = FakeBackend([Observation(91, in_combat=True,
                                           target_health=71, target_max_health=71)])
        backend.target_dies = True
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper,
                                            finish_budget_seconds=0.3, clock=Clock())
        self.assertIn('finish_target', result['actions'])

    def test_a_healthy_character_does_not_start_a_fight_below_the_floor(self):
        backend = FakeBackend([Observation(20, in_combat=True,
                                           target_health=10, target_max_health=71)])
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                              {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            min_health=25, passive_seconds=2,
                                            sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)
        self.assertEqual(result['ending'], 'escape')

    def test_with_no_route_the_character_fights_instead_of_idling(self):
        # Idling while a hostile keeps hitting guarantees death, so with no
        # route the only ending the server offers is the target dying.
        backend = FakeBackend([Observation(20, in_combat=True)])
        backend.target_dies = True
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            min_health=25, passive_seconds=2,
                                            sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertEqual(result['ending'], 'fight')
        self.assertIn('rpc:bridge.start_attack', backend.requests)

    def test_an_unreachable_target_is_not_killed_but_escaped_from(self):
        # huntD (2026-11-01): the target had broken contact, so the fight
        # controller refused to start ("preconditions_not_met"). Asking for
        # the kill then left the character standing in combat until it died.
        backend = FakeBackend([Observation(91, in_combat=True,
                                           target_health=22, target_max_health=71)])
        backend.target['movement'] = {'position_xyzo': [500.0, 500.0, 0.0]}
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                              {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper,
                                            finish_budget_seconds=0.3, clock=Clock())
        self.assertEqual(result['ending'], 'escape')
        self.assertNotIn('finish_target', result['actions'])
        self.assertIn('retreat', result['actions'])

    def test_the_kill_fallback_walks_to_the_target_first(self):
        # The kill ending is only executable when the character can reach the
        # target, so the fallback closes the distance before fighting.
        backend = FakeBackend([Observation(91, in_combat=True,
                                           target_health=22, target_max_health=71)])
        closed = {}
        backend._close_distance = lambda params: closed.setdefault('called', True) and None
        result = resolve_combat_before_exit(backend, budget_seconds=30,
                                            passive_seconds=2, sleep=sleeper,
                                            finish_budget_seconds=0.3, clock=Clock())
        self.assertTrue(closed.get('called'),
                        'the kill attempt must close the distance first')

    def test_never_sends_an_attack_while_escaping(self):

        backend = FakeBackend([Observation(71, in_combat=True)])
        backend.outbound_route = [{'x': 0.0, 'y': 0.0, 'z': 0.0},
                              {'x': 0.0, 'y': 6.0, 'z': 0.0}]
        backend.outbound_origin = [0.0, 0.0, 0.0]
        resolve_combat_before_exit(backend, budget_seconds=30,
                                   passive_seconds=2, sleep=sleeper, finish_budget_seconds=0.3,
                                   clock=Clock())
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)
        self.assertNotIn('rpc:bridge.face', backend.requests)

    def test_close_session_logs_out_when_already_out_of_combat(self):
        backend = FakeBackend([Observation(108, in_combat=False)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertEqual(report['finalize']['logout']['status'], 'closed')
        self.assertNotIn('combat_recovery', report['finalize'])

    def test_close_session_resolves_combat_before_logout(self):
        # The exact death scenario: alive, in combat, and the server only
        # clears combat after a few observations.
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertTrue(report['finalize']['combat_recovery']['combat_cleared'])
        self.assertEqual(report['finalize']['logout']['status'], 'closed')
        self.assertIn('execute:logout', backend.requests)
        self.assertEqual(backend.requests.index('execute:logout'),
                         len(backend.requests) - 1,
                         'the logout must be the last request')

    def test_close_session_does_not_log_out_a_dead_character(self):
        backend = FakeBackend([Observation(0, in_combat=True)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertEqual(report['finalize']['status'], 'unresolved')
        self.assertNotIn('execute:logout', backend.requests)

    def test_close_session_records_a_refused_logout(self):
        backend = FakeBackend([Observation(108, in_combat=False)],
                              logout_result={'status': 'error', 'error': {'message': 'in combat'}})
        report = {}
        close_session(backend, report=report, sleep=sleeper, finish_budget_seconds=0.3, clock=Clock())
        self.assertNotEqual(report['finalize']['logout'].get('status'), 'closed')
        self.assertIn('logout_note', report['finalize'])


if __name__ == '__main__':
    unittest.main()
