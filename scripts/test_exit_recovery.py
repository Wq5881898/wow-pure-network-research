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
    def __init__(self, health, ghost=False, in_combat=False):
        self.health = health
        self.ghost = ghost
        self.in_combat = in_combat


class FakeBackend:
    """Serves a scripted sequence of observations and records every request."""

    def __init__(self, observations, logout_result=None):
        self.observations = list(observations)
        self.requests = []
        self.logout_result = (logout_result if logout_result is not None
                              else {'status': 'closed', 'reply': {'result': {'status': 'closed'}}})

    def observe(self):
        self.requests.append('observe')
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]

    def execute(self, action):
        self.requests.append('execute:%s' % action.kind)
        return self.logout_result


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
                                            sleep=sleeper, clock=Clock())
        self.assertTrue(result['combat_cleared'])
        self.assertEqual(result['final'].in_combat, False)
        self.assertEqual(set(backend.requests), {'observe'},
                         'the recovery branch must only observe')

    def test_resolve_reports_uncleared_combat_without_acting(self):
        backend = FakeBackend([Observation(71, in_combat=True)])
        result = resolve_combat_before_exit(backend, budget_seconds=3,
                                            sleep=sleeper, clock=Clock())
        self.assertFalse(result['combat_cleared'])
        self.assertIsNotNone(result['reason'])
        self.assertEqual(set(backend.requests), {'observe'})

    def test_close_session_logs_out_when_already_out_of_combat(self):
        backend = FakeBackend([Observation(108, in_combat=False)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, clock=Clock())
        self.assertEqual(report['finalize']['logout']['status'], 'closed')
        self.assertNotIn('combat_recovery', report['finalize'])

    def test_close_session_resolves_combat_before_logout(self):
        # The exact death scenario: alive, in combat, and the server only
        # clears combat after a few observations.
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, clock=Clock())
        self.assertTrue(report['finalize']['combat_recovery']['combat_cleared'])
        self.assertEqual(report['finalize']['logout']['status'], 'closed')
        self.assertIn('execute:logout', backend.requests)
        self.assertEqual(backend.requests.index('execute:logout'),
                         len(backend.requests) - 1,
                         'the logout must be the last request')

    def test_close_session_does_not_log_out_a_dead_character(self):
        backend = FakeBackend([Observation(0, in_combat=True)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, clock=Clock())
        self.assertEqual(report['finalize']['status'], 'unresolved')
        self.assertNotIn('execute:logout', backend.requests)

    def test_close_session_records_a_refused_logout(self):
        backend = FakeBackend([Observation(108, in_combat=False)],
                              logout_result={'status': 'error', 'error': {'message': 'in combat'}})
        report = {}
        close_session(backend, report=report, sleep=sleeper, clock=Clock())
        self.assertNotEqual(report['finalize']['logout'].get('status'), 'closed')
        self.assertIn('logout_note', report['finalize'])


if __name__ == '__main__':
    unittest.main()
