"""Tests for the per-packet real-combat replay (combat-state-002 deliverable D).

Real-archive assertions against the archived long01 melee capture: the
timeline must track the real fight (in_combat + continue_attack), must not
confirm disengagement before the fight began, must never claim death or a
confirmed range exit, and must never write under results/.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from replay_combat_state_machine_combat import (
    DEFAULT_TRACE, LONG01_TARGET, ROOT, assert_combat_tracked,
    replay_combat_trace)

RESULTS = ROOT / 'results' / 'pure_network'
TRACE_PATH = RESULTS / DEFAULT_TRACE


class PerPacketCombatReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest_before = hashlib.sha256(
            TRACE_PATH.read_bytes()).hexdigest()
        cls.report = replay_combat_trace(TRACE_PATH)

    def test_timeline_is_per_packet_and_ordered(self):
        timeline = self.report['timeline']
        self.assertGreater(len(timeline), 100,
                           'a 129-packet capture must yield a per-sample '
                           'timeline, not a deduplicated one')
        self.assertEqual([e['index'] for e in timeline],
                         list(range(len(timeline))))
        # The final budget-end entry carries no packet; every sample entry
        # does, and packet order is monotonic.
        samples = [e for e in timeline if e['sequence'] is not None]
        self.assertEqual(len(samples), len(timeline) - 1)
        self.assertEqual([e['sequence'] for e in samples],
                         sorted(e['sequence'] for e in samples))
        self.assertEqual(timeline[-1]['sequence'], None)

    def test_real_fight_is_tracked(self):
        states = [e['state'] for e in self.report['timeline']]
        decisions = [e['decision'] for e in self.report['timeline']]
        self.assertIn('in_combat', states)
        self.assertIn('continue_attack', decisions)

    def test_no_disengaged_confirmed_before_combat(self):
        states = [e['state'] for e in self.report['timeline']]
        first_combat = states.index('in_combat')
        for entry in self.report['timeline'][:first_combat]:
            self.assertNotEqual(entry['state'], 'disengaged_confirmed')
        self.assertFalse(
            self.report['summary']['disengaged_confirmed_before_combat'])

    def test_no_death_ghost_or_confirmed_range_exit(self):
        states = [e['state'] for e in self.report['timeline']]
        self.assertNotIn('dead', states)
        self.assertNotIn('ghost', states)
        for entry in self.report['timeline']:
            self.assertNotIn('target_out_of_range_confirmed',
                             entry['reasons'])
            if 'distance_confirmed' in entry['observations']:
                self.assertFalse(entry['observations']['distance_confirmed'])

    def test_archived_hp_facts_match_the_fight(self):
        # docs/76 facts re-verified packet-by-packet: player 108->95,
        # target 69->5, health transmitted throughout, never below 95.
        transmitted = [e['observations']['self_health']
                       for e in self.report['timeline']
                       if e['observations'].get('health_transmitted')]
        self.assertTrue(transmitted)
        self.assertEqual(min(transmitted), 95)
        self.assertEqual(max(transmitted), 108)
        target_hp = [e['observations']['target_health']
                     for e in self.report['timeline']
                     if e['observations'].get('target_present')
                     and e['observations'].get('target_health') is not None]
        self.assertEqual(max(target_hp), 69)
        self.assertEqual(min(target_hp), 5)

    def test_assert_combat_tracked_passes(self):
        self.assertEqual(assert_combat_tracked(self.report), [])

    def test_results_untouched_after_replay(self):
        self.assertEqual(
            hashlib.sha256(TRACE_PATH.read_bytes()).hexdigest(),
            self.digest_before)

    def test_cli_writes_to_caller_chosen_path_and_refuses_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'timeline.json'
            result = subprocess.run(
                [sys.executable,
                 str(ROOT / 'scripts' / 'replay_combat_state_machine_combat.py'),
                 str(out)],
                capture_output=True, cwd=str(ROOT))
            self.assertEqual(result.returncode, 0,
                             result.stderr.decode('utf-8', 'replace'))
            payload = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(payload['violations'], [])
            self.assertTrue(payload['report']['summary']['in_combat_seen'])
            refused = subprocess.run(
                [sys.executable,
                 str(ROOT / 'scripts' / 'replay_combat_state_machine_combat.py'),
                 str(RESULTS / 'forbidden_timeline.json')],
                capture_output=True, cwd=str(ROOT))
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('must not live under results',
                          refused.stderr.decode('utf-8', 'replace'))
            self.assertFalse((RESULTS / 'forbidden_timeline.json').exists())


if __name__ == '__main__':
    unittest.main()
