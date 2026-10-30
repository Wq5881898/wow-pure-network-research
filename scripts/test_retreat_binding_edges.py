import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import agent_wow_retreat


def write(folder, name, payload):
    (folder / name).write_text(json.dumps(payload), encoding='utf-8')


def fixture(folder):
    for i in range(1, 7):
        start, end = float(i - 1), float(i)
        write(folder, f'height_R792_segment{i}_plan.json',
              {'points': [[start, 0, 0], [end, 0, 0]], 'length': 1})
        write(folder, f'height_R792_segment{i}_reconciled.json',
              {'confirmed': True, 'landing_error': 0,
               'checks': {key: True for key in
                          ('no_trial_error', 'movement_reply', 'separate_session',
                           'same_character_map', 'landing_within_01',
                           'health_preserved', 'safe', 'both_closed')}})
        write(folder, f'height_R792_segment{i}_trial.json',
              {'status': 'submitted_requires_fresh_login',
               'plan': f'height_R792_segment{i}_plan.json',
               'before': {'session_id': f't{i}', 'character': '9', 'map_id': 1,
                          'position': [start, 0, 0], 'health': 108,
                          'max_health': 108, 'ghost': False, 'in_combat': False},
               'movement': {'result': {'submittedPosition': [end, 0, 0, 0]}},
               'logout': {'status': 'closed'}})
        write(folder, f'agent_wow_readonly_height_R792_segment{i}_verify.json',
              {'status': 'observed_and_closed', 'scope': 'login/poll/logout only',
               'run_id': f'height_R792_segment{i}_verify',
               'observation': {'session_id': f'v{i}', 'character': '9', 'map_id': 1,
                               'position': [end, 0, 0], 'health': 108,
                               'max_health': 108, 'ghost': False, 'in_combat': False},
               'logout': {'status': 'closed'}})


class RetreatBindingEdgeTests(unittest.TestCase):
    def test_baseline(self):
        with TemporaryDirectory() as temp:
            fixture(Path(temp))
            self.assertEqual(len(agent_wow_retreat.prepare(temp)), 6)

    def test_tampered_landing_error_rejected(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            fixture(folder)
            path = folder / 'height_R792_segment3_reconciled.json'
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['landing_error'] = 0.5
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                agent_wow_retreat.prepare(temp)

    def test_tampered_movement_position_rejected(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            fixture(folder)
            path = folder / 'height_R792_segment4_trial.json'
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['movement']['result']['submittedPosition'][0] += 2
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                agent_wow_retreat.prepare(temp)

    def test_tampered_before_map_id_rejected(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            fixture(folder)
            path = folder / 'height_R792_segment3_trial.json'
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['before']['map_id'] = 0
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                agent_wow_retreat.prepare(temp)

    def test_tampered_observation_ghost_rejected(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            fixture(folder)
            path = folder / 'agent_wow_readonly_height_R792_segment3_verify.json'
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['observation']['ghost'] = True
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                agent_wow_retreat.prepare(temp)


if __name__ == '__main__':
    unittest.main()
