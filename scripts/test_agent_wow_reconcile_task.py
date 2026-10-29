import copy
import json
import unittest
from pathlib import Path
from agent_wow_reconcile_task import reconcile

ROOT = Path(__file__).resolve().parents[1] / 'results/pure_network'


class ReconcileTests(unittest.TestCase):
    def test_real_current_state_invalidates_old_task(self):
        report = json.loads((ROOT/'agent_wow_task_p3_batch01_unit1.json').read_text())
        trace = json.loads((ROOT/'agent_wow_bridge_p3_position_reconcile01.packets.json').read_text())
        result = reconcile(report, trace)
        self.assertEqual(result['status'], 'REPLAN_REQUIRED')
        self.assertFalse(result['quest_active'])
        self.assertFalse(result['position_verification']['confirmed'])
        self.assertFalse(result['combat_authorized'])

    def test_real_adjacent_boundary_confirms_without_authorizing_combat(self):
        report = json.loads((ROOT/'agent_wow_task_run111_movement_boundary.json').read_text())
        trace = json.loads((ROOT/'agent_wow_bridge_run112_movement_verify.packets.json').read_text())
        result = reconcile(report, trace)
        self.assertEqual(result['status'], 'BOUNDARY_CONFIRMED')
        self.assertFalse(result['combat_authorized'])
        corrupted = copy.deepcopy(trace)
        corrupted['self_guid'] = 'other'
        with self.assertRaises(ValueError):
            reconcile(report, corrupted)


if __name__ == '__main__':
    unittest.main()
