"""The session must refuse to tear down a living character that is in combat.

Two real deaths on 2026-11-01 (cs002, esc003) shared one cause: the session
was closed while the character was alive and flagged in combat. The server
keeps attacking such a character after the client is gone, and it dies there.

This is enforced in the session layer, not by convention, so a caller that
forgets to resolve the combat state cannot kill the character by accident.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

# The backend module resolves the gateway through `ip route` at import time, so
# it is only importable where that tool exists (WSL). The guard itself is
# platform independent, but the module cannot be imported on Windows.
if not shutil.which('ip'):
    raise unittest.SkipTest('backend module requires the WSL `ip route` tool')

import agent_wow_session
from agent_wow_session import PureNetworkBackend


class CloseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.root = root
        # Redirect the trace path into the temp dir.
        self.original_root = agent_wow_session.root
        agent_wow_session.root = root
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
        trace = json.loads((self.root / 'trace.json').read_text(encoding='utf-8'))
        self.assertFalse(trace['capture_complete'])


if __name__ == '__main__':
    unittest.main()
