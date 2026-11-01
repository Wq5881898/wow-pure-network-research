"""Enforcement verified on REAL samples, not on a synthetic world.

esc020 is a real fight on the real server: the character's HP fell 91 -> 31
while the target was left at 22 of 71, and the character died.

Two thresholds are in play:
  - the fight controller's own floor: health < 25 (absolute),
  - the state machine's low-health exit: health / max_health < 0.5 (a ratio,
    so it scales with the character's level).

In esc020 the HP never reached 25, so the controller's floor never fired and
the fight simply ran on. The state machine's ratio line fires much earlier.
That difference is exactly what enforcement changes, and it is asserted here
against the archived capture.
"""
import json
import pathlib
import unittest

from replay_combat_state_machine import replay_trace

RESULTS = pathlib.Path(__file__).resolve().parents[1] / 'results/pure_network'
TRACE = RESULTS / 'agent_wow_bridge_esc020.packets.json'
REPORT = RESULTS / 'agent_wow_fallback_esc020.json'

# The fight controller's absolute floor, from agent_wow_combat_control.fight.
FIGHT_FLOOR = 25


def _load():
    if not TRACE.exists() or not REPORT.exists():
        raise unittest.SkipTest('the esc020 archive is not present')
    guid = json.loads(REPORT.read_bytes())['target']['guid']
    return guid, json.loads(TRACE.read_bytes())


class RealSampleEnforcementTests(unittest.TestCase):
    def test_the_state_machine_veto_fires_before_the_controller_floor(self):
        guid, trace = _load()
        result = replay_trace(str(TRACE), target_guid=guid,
                               escape_path_planned=True)
        timeline = result.get('timeline') or []
        veto = [row for row in timeline
                if row.get('state') == 'low_health_exit'
                and row.get('decision') in ('stop_attack', 'request_escape')]
        self.assertTrue(veto, 'the machine must reach the low-health exit')

        # The lowest HP the archived fight ever saw, read from the capture
        # itself rather than from the replay's summary.
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from agent_wow_worldstate import IncrementalWorld
        engine = IncrementalWorld(trace['session_id'], '9')
        lowest = None
        for packet in trace['packets']:
            state = engine.apply([packet])
            me = state.get('self') or {}
            health = me.get('health')
            if health is not None:
                lowest = health if lowest is None else min(lowest, health)
        self.assertIsNotNone(lowest)
        self.assertGreater(lowest, FIGHT_FLOOR,
                           'the controller floor never fired in this fight')
        self.assertLessEqual(lowest, 37,
                             'the character fell well below the ratio line')

    def test_enforcement_is_what_makes_the_difference(self):
        # With enforcement the fight ends at the machine's decision; without
        # it, the controller's absolute floor is the only stop and it was
        # never reached in this fight.
        guid, trace = _load()
        result = replay_trace(str(TRACE), target_guid=guid,
                               escape_path_planned=True)
        summary = result.get('summary') or {}
        self.assertTrue(summary.get('low_exit_observed'))
        self.assertNotIn('hold_and_observe', summary.get('decisions', []))


if __name__ == '__main__':
    unittest.main()
