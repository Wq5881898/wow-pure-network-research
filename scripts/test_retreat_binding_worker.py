"""Fabricated status flags cannot replace independent relog evidence."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_wow_retreat import prepare


CHECKS = ('no_trial_error', 'movement_reply', 'separate_session',
          'same_character_map', 'landing_within_01', 'health_preserved',
          'safe', 'both_closed')


def write(folder, name, data):
    folder.joinpath(name).write_text(json.dumps(data), encoding='utf-8')


def build(folder, *, verify_sessions=None, final_offsets=None):
    verify_sessions = verify_sessions or [f'v{i}' for i in range(1, 7)]
    final_offsets = final_offsets or [(0.0, 0.0, 0.0)] * 6
    for i in range(1, 7):
        start, end = (i - 1) * 2.0, i * 2.0
        points = [[start, 0.0, 0.0], [(start + end) / 2, 0.0, 0.0],
                  [end, 0.0, 0.0]]
        observed = [points[-1][0] + final_offsets[i - 1][0], 0.0, 0.0]
        write(folder, f'height_R792_segment{i}_plan.json',
              {'points': points, 'length': 2.0})
        write(folder, f'height_R792_segment{i}_reconciled.json',
              {'confirmed': True,
               'checks': {name: True for name in CHECKS},
               'landing_error': 0.0})
        write(folder, f'height_R792_segment{i}_trial.json',
              {'status': 'submitted_requires_fresh_login',
               'plan': f'height_R792_segment{i}_plan.json',
               'before': {'session_id': f't{i}', 'character': '9',
                          'map_id': 1, 'position': points[0],
                          'health': 108, 'max_health': 108,
                          'ghost': False, 'in_combat': False},
               'movement': {'result': {'submittedPosition':
                                       [*points[-1], 0.0]}},
               'logout': {'status': 'closed'}})
        write(folder, f'agent_wow_readonly_height_R792_segment{i}_verify.json',
              {'status': 'observed_and_closed',
               'scope': 'login/poll/logout only',
               'run_id': f'height_R792_segment{i}_verify',
               'observation': {'session_id': verify_sessions[i - 1],
                               'character': '9', 'map_id': 1,
                               'position': observed, 'health': 108,
                               'max_health': 108, 'ghost': False,
                               'in_combat': False},
               'logout': {'status': 'closed'}})


class RetreatBindingTest(unittest.TestCase):
    def test_rejects_far_relogin_position(self):
        with TemporaryDirectory() as d:
            build(Path(d), final_offsets=[(0.0, 0.0, 0.0)] * 5 + [(2.0, 0.0, 0.0)])
            with self.assertRaises(ValueError):
                prepare(d)

    def test_rejects_shared_verify_and_trial_session(self):
        with TemporaryDirectory() as d:
            build(Path(d), verify_sessions=['v1', 't2', 'v3', 'v4', 'v5', 'v6'])
            with self.assertRaises(ValueError):
                prepare(d)

    def test_accepts_six_segment_binding(self):
        with TemporaryDirectory() as d:
            build(Path(d))
            self.assertEqual(len(prepare(d)), 6)


if __name__ == '__main__':
    unittest.main()
