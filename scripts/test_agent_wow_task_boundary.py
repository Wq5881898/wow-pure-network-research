import copy
import json
import unittest
from pathlib import Path
from agent_wow_task_boundary import movement_boundary, verify_target
from agent_wow_position import confirm_relogin


class BoundaryTests(unittest.TestCase):
    def test_target_recheck_matrix(self):
        target = {'guid': '42', 'entry': 3124, 'type_id': 3, 'level': 3,
                  'health': 71, 'movement': {'position_xyzo': [2, 0, 0, 0]}}
        state = {'self': {'health': 89, 'ghost': False, 'in_combat': False},
                 'visible_objects': [target]}
        self.assertEqual(verify_target(state, '42', 3124, 3, 0, [0, 0, 0])[0], target)
        for field, value in [('health', 0), ('level', 4), ('entry', 3098)]:
            changed = copy.deepcopy(state)
            changed['visible_objects'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_target(changed, '42', 3124, 3, 0, [0, 0, 0])
        with self.assertRaises(ValueError):
            verify_target(state, 'missing', 3124, 3, 0, [0, 0, 0])
        with self.assertRaises(ValueError):
            verify_target(state, '42', 3124, 3, 0, [20, 0, 0])
        changed = copy.deepcopy(state)
        changed['self']['in_combat'] = True
        with self.assertRaises(ValueError):
            verify_target(changed, '42', 3124, 3, 0, [0, 0, 0])

    def test_adjacent_live_movement_and_verification(self):
        root = Path(__file__).resolve().parents[1] / 'results/pure_network'
        report = json.loads((root / 'agent_wow_task_run111_movement_boundary.json').read_text())
        trace = json.loads((root / 'agent_wow_bridge_run112_movement_verify.packets.json').read_text())
        boundary = movement_boundary(report)
        evidence = confirm_relogin(boundary['source_session'], boundary['expected_xyz'],
                                   boundary['map'], trace, character=boundary['character'])
        self.assertTrue(evidence['confirmed'])
        self.assertLess(evidence['distance'], .001)

    def test_boundary_expiry_and_approach_confirmation(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root / 'results/pure_network/agent_wow_task_run106_task_unit_repeat.json').read_text())
        finished = report['final_observation']['sampled_at']
        self.assertFalse(movement_boundary(report)['approach_confirmed'])
        with self.assertRaises(ValueError):
            movement_boundary(report, max_age_seconds=1, now=finished + 2)

    def test_archived_boundary_and_rejected_combat(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root / 'results/pure_network/agent_wow_task_run106_task_unit_repeat.json').read_text())
        boundary = movement_boundary(report)
        self.assertEqual(boundary['character'], '9')
        self.assertAlmostEqual(boundary['expected_xyz'][0], -709.0517)
        changed = copy.deepcopy(report)
        changed['final_observation']['in_combat'] = True
        with self.assertRaises(ValueError):
            movement_boundary(changed)
        changed = copy.deepcopy(report)
        changed['run']['records'].append({'action_id': 'fight'})
        with self.assertRaises(ValueError):
            movement_boundary(changed)
        changed = copy.deepcopy(report)
        changed['finalize']['status'] = 'unknown'
        with self.assertRaises(ValueError):
            movement_boundary(changed)


if __name__ == '__main__':
    unittest.main()
