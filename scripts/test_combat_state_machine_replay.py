"""Replay forensics over five archived captures driving the state machine.

These are real-archive assertions, not synthetic fixtures: run40 must never
report dead from an untransmitted health field, and long01 must never show a
start_attack after an exit. Read-only against results/.
"""
import hashlib
import json
import unittest
from pathlib import Path

from replay_combat_state_machine import (
    DEFAULT_TRACES, UNCONFIRMED_SOURCES, replay_trace)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'results' / 'pure_network'
LONG01 = 'agent_wow_bridge_quest792_long01_unit1_verify.packets.json'
RUN40 = 'agent_wow_bridge_run40_quest788_kill1.packets.json'
RUN51 = 'agent_wow_bridge_run51_retreat_combat.packets.json'
RUN113 = 'agent_wow_bridge_run113_confirmed_combat.packets.json'
RUN116 = 'agent_wow_bridge_run116_final_tail_combat.packets.json'

# Contract strings (kept literal here so the test fails if a module constant
# is renamed).
STATE_DEAD = 'dead'
STATE_GHOST = 'ghost'
STATE_IN_COMBAT = 'in_combat'
STATE_LOW = 'low_health_exit'
STATE_RANGE = 'target_out_of_range'
STATE_DISENGAGING = 'disengaging'
DECISION_START = 'start_attack'
DECISION_STOP = 'stop_attack'
REASON_HEALTH_UNKNOWN = 'health_unknown'
REASON_TARGET_DEAD = 'target_dead'
REASON_DISTANCE_UNCONFIRMED = 'target_distance_unconfirmed'
REASON_RANGE_CONFIRMED = 'target_out_of_range_confirmed'


class ReplayContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = {}
        cls.results_hashes = {
            name: hashlib.sha256((RESULTS / name).read_bytes()).hexdigest()
            for name in DEFAULT_TRACES}
        for name in DEFAULT_TRACES:
            cls.reports[name] = replay_trace(RESULTS / name)

    def test_results_untouched_after_replay(self):
        for name, digest in self.results_hashes.items():
            self.assertEqual(
                hashlib.sha256((RESULTS / name).read_bytes()).hexdigest(),
                digest, 'replay must not modify %s' % name)

    def test_run40_untransmitted_health_is_unknown_never_dead(self):
        report = self.reports[RUN40]
        states = [entry['state'] for entry in report['timeline']]
        self.assertNotIn(STATE_DEAD, states,
                         'run40 health was never transmitted; dead is wrong')
        self.assertFalse(report['summary']['dead_observed'])
        self.assertTrue(report['summary']['health_unknown_seen'])
        first = report['timeline'][0]
        self.assertIn(REASON_HEALTH_UNKNOWN, first['reasons'])
        self.assertFalse(first['observations']['health_transmitted'])
        self.assertEqual(first['observations']['self_max_health'], 70)

    def test_long01_monotonic_timeline_no_restart_after_exit(self):
        report = self.reports[LONG01]
        timeline = report['timeline']
        self.assertEqual([e['index'] for e in timeline],
                         list(range(len(timeline))))
        self.assertEqual([e['sequence'] for e in timeline],
                         sorted(e['sequence'] for e in timeline))
        exit_indices = [i for i, e in enumerate(timeline)
                        if e['state'] in (STATE_LOW, STATE_RANGE)]
        self.assertTrue(exit_indices, 'long01 must show an exit state')
        for entry in timeline[exit_indices[0]:]:
            self.assertNotEqual(entry['decision'], DECISION_START)
        # The archived fight itself: HP 108->95, target 69->5 (docs/76 facts,
        # re-verified from this replay).
        combat = [e for e in timeline if e['state'] == STATE_IN_COMBAT]
        self.assertGreaterEqual(len(combat), 2)
        self.assertEqual(combat[0]['observations']['self_health'], 108)
        self.assertEqual(combat[0]['observations']['target_health'], 69)
        last_combat = combat[-1]
        self.assertEqual(last_combat['observations']['self_health'], 98)
        self.assertEqual(last_combat['observations']['target_health'], 17)
        final = timeline[-2] if timeline[-1]['reasons'] == ['budget_exhausted'] \
            else timeline[-1]
        self.assertEqual(final['observations']['self_health'], 95)
        self.assertEqual(final['observations']['target_health'], 5)

    def test_run51_unconfirmed_distance_stops_without_flight_claim(self):
        report = self.reports[RUN51]
        states = [e['state'] for e in report['timeline']]
        self.assertIn(STATE_RANGE, states)
        stop = next(e for e in report['timeline']
                    if e['decision'] == DECISION_STOP)
        self.assertEqual(stop['reasons'], [REASON_DISTANCE_UNCONFIRMED])
        for entry in report['timeline']:
            self.assertNotIn(REASON_RANGE_CONFIRMED, entry['reasons'])
            if entry['observations'].get('distance_confirmed'):
                self.fail('no replay distance may be confirmed in combat')
        self.assertFalse(report['summary']['dead_observed'])

    def test_run113_target_dead_then_not_single_sample_confirmed(self):
        report = self.reports[RUN113]
        states = [e['state'] for e in report['timeline']]
        self.assertIn(STATE_IN_COMBAT, states)
        self.assertNotIn(STATE_DEAD, states)
        self.assertNotIn(STATE_GHOST, states)
        # Target 71->0 observed as damage, not as a range exit.
        self.assertTrue(any(e['observations'].get('target_health') == 0
                            for e in report['timeline']))
        for entry in report['timeline']:
            self.assertNotIn(REASON_RANGE_CONFIRMED, entry['reasons'])
        # The capture ends before the confirmation window completes, so the
        # machine must not claim confirmed disengagement.
        self.assertFalse(report['summary']['disengaged_confirmed_observed'])

    def test_run116_idle_tail_never_claims_combat_or_death(self):
        report = self.reports[RUN116]
        states = [e['state'] for e in report['timeline']]
        self.assertNotIn(STATE_DEAD, states)
        self.assertNotIn(STATE_GHOST, states)
        self.assertNotIn(STATE_IN_COMBAT, states)
        self.assertNotEqual(report['final_state'], 'disengaged_confirmed')

    def test_every_timeline_entry_uses_contract_strings(self):
        from agent_wow_combat_state_machine import DECISIONS, REASONS, STATES
        for name, report in self.reports.items():
            for entry in report['timeline']:
                self.assertIn(entry['state'], STATES, name)
                self.assertIn(entry['decision'], DECISIONS, name)
                for reason in entry['reasons']:
                    self.assertIn(reason, REASONS, name)

    def test_all_five_archives_covered(self):
        self.assertEqual(len(self.reports), 5)
        for name in DEFAULT_TRACES:
            self.assertIn(name, self.reports)
            self.assertGreater(len(self.reports[name]['timeline']), 0)


if __name__ == '__main__':
    unittest.main()
