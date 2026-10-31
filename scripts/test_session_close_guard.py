"""The session must never be closed while the character is alive in combat.

Two real deaths on 2026-11-01 (cs002, esc003) shared one cause: the session
was closed while the character was alive and flagged in combat. The server
keeps attacking such a character after the client is gone, and it dies there.

Two layers now prevent it:
  - the backend close() refuses outright, and
  - the runner keeps the session open and supervises it until the character is
    safe, instead of abandoning it unattended.
"""
import shutil
import unittest
from unittest.mock import Mock

if not shutil.which('ip'):
    raise unittest.SkipTest('backend module requires the WSL `ip route` tool')

import agent_wow_session
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import close_session


class Observation:
    def __init__(self, health, ghost=False, in_combat=False):
        self.health = health
        self.ghost = ghost
        self.in_combat = in_combat


class FakeBackend:
    def __init__(self, observations, logout_result=None):
        self.observations = list(observations)
        self.requests = []
        self.logout_result = (logout_result if logout_result is not None
                              else {'status': 'closed', 'reply': {'result': {'status': 'closed'}}})
        self.outbound_route = []
        self.outbound_origin = None

    def observe(self):
        self.requests.append('observe')
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]

    def execute(self, action):
        self.requests.append('execute:%s' % action.kind)
        return self.logout_result

    def rpc(self, method, params):
        self.requests.append('rpc:%s' % method)
        return {'result': {}}

    def drain(self):
        self.requests.append('drain')
        return {'self': self.observations[-1]}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0
        return self.now


def sleeper(_s):
    return None


class CloseGuardTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original_root = agent_wow_session.root
        agent_wow_session.root = self.root
        self.addCleanup(lambda: setattr(agent_wow_session, 'root', self.original_root))
        self.backend = PureNetworkBackend('close_guard_test')

    def make_backend(self, health, ghost, in_combat):
        backend = self.backend
        backend.state = {'self': {'health': health, 'ghost': ghost,
                                  'in_combat': in_combat,
                                  'movement': {'position_xyzo': [0, 0, 0, 0]}}}
        backend.session = Mock()
        backend.trace_path = self.root / 'trace.json'
        backend.packets = []
        return backend

    def test_refuses_to_close_a_living_character_in_combat(self):
        backend = self.make_backend(71, False, True)
        with self.assertRaises(RuntimeError) as ctx:
            backend.close()
        self.assertIn('still in combat', str(ctx.exception))
        backend.session.close.assert_not_called()
        self.assertIn('close_refused', backend.result)

    def test_closes_when_the_character_is_out_of_combat(self):
        backend = self.make_backend(108, False, False)
        backend.close()
        backend.session.close.assert_called_once()

    def test_closes_a_dead_character(self):
        # A corpse has nothing to lose: the server is not attacking it.
        backend = self.make_backend(0, False, True)
        backend.close()
        backend.session.close.assert_called_once()

    def test_closes_a_ghost(self):
        backend = self.make_backend(1, True, True)
        backend.close()
        backend.session.close.assert_called_once()

    def test_force_overrides_the_refusal_but_is_recorded(self):
        backend = self.make_backend(71, False, True)
        backend.close(force=True)
        backend.session.close.assert_called_once()
        self.assertTrue(backend.result.get('close_forced_in_combat'))

    def test_refusal_still_writes_the_trace(self):
        backend = self.make_backend(71, False, True)
        with self.assertRaises(RuntimeError):
            backend.close()
        self.assertTrue((self.root / 'trace.json').exists())
        import json
        trace = json.loads((self.root / 'trace.json').read_text(encoding='utf-8'))
        self.assertFalse(trace['capture_complete'])


class CloseSessionEscalationTests(unittest.TestCase):
    def test_does_not_log_out_while_still_in_combat(self):
        # The escape cannot clear the combat. Logging out now would be refused
        # by the server and the character would keep being attacked.
        backend = FakeBackend([Observation(71, in_combat=True)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, clock=Clock())
        self.assertEqual(report['finalize']['status'], 'still_in_combat')
        self.assertNotIn('execute:logout', backend.requests)
        self.assertIn('do not log out', report['finalize']['reason'])

    def test_logs_out_once_the_escape_clears_the_combat(self):
        backend = FakeBackend([Observation(71, in_combat=True),
                               Observation(71, in_combat=False)])
        report = {}
        close_session(backend, report=report, sleep=sleeper, clock=Clock())
        self.assertEqual(report['finalize']['status'], 'resolved')
        self.assertEqual(report['finalize']['logout']['status'], 'closed')
        self.assertEqual(backend.requests[-1], 'execute:logout')


if __name__ == '__main__':
    unittest.main()
