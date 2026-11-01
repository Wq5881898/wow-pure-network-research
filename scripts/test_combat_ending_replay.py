"""Regression guards for the aligned combat ending, over a REAL fight.

The alignment (2026-11-01) gave the combat recovery state machine and the
exit branch one shared rule for how an unresolved combat ends, and removed
the one recommendation that is fatal: idling while a hostile keeps hitting.

esc020 is the archive that exercises it: a real fight where the character
fell from 91 to 31 HP and the target was left at 22 of 71. Replaying it
shows the decision the machine now makes on real samples.
"""
import json
import pathlib
import unittest

from replay_combat_state_machine import replay_trace

RESULTS = pathlib.Path(__file__).resolve().parents[1] / 'results/pure_network'
TRACE = RESULTS / 'agent_wow_bridge_esc020.packets.json'
REPORT = RESULTS / 'agent_wow_fallback_esc020.json'


def _timeline(escape_path_planned):
    if not TRACE.exists() or not REPORT.exists():
        raise unittest.SkipTest('the esc020 archive is not present')
    guid = json.loads(REPORT.read_bytes())['target']['guid']
    result = replay_trace(str(TRACE), target_guid=guid,
                           escape_path_planned=escape_path_planned)
    return result, result.get('timeline') or []


class AlignedEndingReplayTests(unittest.TestCase):
    def test_the_low_health_exit_is_reached_on_real_samples(self):
        result, timeline = _timeline(False)
        self.assertTrue(result['summary']['low_exit_observed'])
        states = {row.get('state') for row in timeline}
        self.assertIn('low_health_exit', states)

    def test_without_a_route_the_machine_never_recommends_idling(self):
        # The old latched answer was hold_and_observe: standing still while a
        # hostile keeps hitting is what killed the character three times.
        result, timeline = _timeline(False)
        decisions = {row.get('decision') for row in timeline}
        self.assertNotIn('hold_and_observe', decisions)
        self.assertIn('continue_attack', decisions)

    def test_with_a_route_the_machine_asks_to_escape(self):
        result, timeline = _timeline(True)
        decisions = {row.get('decision') for row in timeline}
        self.assertIn('request_escape', decisions)

    def test_the_low_health_exit_stops_attacking_first(self):
        # The contract requires the low-health exit to stop attacking before
        # it decides anything else.
        result, timeline = _timeline(False)
        low = [row for row in timeline if row.get('state') == 'low_health_exit']
        self.assertTrue(low)
        self.assertEqual(low[0].get('decision'), 'stop_attack')


if __name__ == '__main__':
    unittest.main()
