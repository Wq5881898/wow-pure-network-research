"""Guards for the enforced combat recovery state machine.

Enforcement is the step where the state machine stops only recording and
starts vetoing dispatch. It is off by default, and the contract of the fight
controller must not widen: the outcome strings the rest of the project reads
are the ones it already knows.

These tests pin:
  - the veto decisions and how an enforced veto is reported,
  - that the attack is stopped and no further start is dispatched,
  - that the existing outcome strings are reused, not invented.
"""
import unittest

from agent_wow_combat_control import ENFORCED_OUTCOME, VETO_DECISIONS, fight
from agent_wow_combat_state_machine import (DECISION_ABORT_UNIT,
                                             DECISION_REQUEST_ESCAPE,
                                             DECISION_STOP_ATTACK,
                                             LOW_HEALTH_EXIT, TARGET_OUT_OF_RANGE)


class FakeRpc:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == 'bridge.start_attack':
            return {'result': {}}
        return {'result': {}}


def _world(health=108, in_combat=True, target_health=71, distance=2.0):
    return {
        'self': {'health': health, 'max_health': 108, 'ghost': False,
                 'in_combat': in_combat, 'health_transmitted': True,
                 'movement': {'position_xyzo': [0, 0, 0]},
                 'level': 3, 'quest_log': [], 'item_totals': {}},
        'visible_objects': [{'guid': 'mob', 'entry': 3124, 'health': target_health,
                             'level': 3, 'max_health': 71,
                             'movement': {'position_xyzo': [distance, 0, 0]}}],
    }


class EnforcementTests(unittest.TestCase):
    def test_the_veto_decisions_are_exactly_the_three(self):
        self.assertEqual(set(VETO_DECISIONS),
                         {DECISION_STOP_ATTACK, DECISION_REQUEST_ESCAPE,
                          DECISION_ABORT_UNIT})

    def test_the_enforced_outcomes_reuse_the_known_strings(self):
        self.assertEqual(ENFORCED_OUTCOME[LOW_HEALTH_EXIT], 'health_budget')
        self.assertEqual(ENFORCED_OUTCOME[TARGET_OUT_OF_RANGE], 'target_left_melee')

    def test_enforcement_is_off_by_default(self):
        rpc = FakeRpc()
        world = _world(health=40, in_combat=True)
        phase, _ = fight(rpc, lambda: world, 'mob', [0, 0, 0], lambda: 0.0,
                         budget=2, advisory=True)
        self.assertNotIn('enforced', phase)
        self.assertNotEqual(phase['outcome'], 'health_budget')

    def test_a_low_health_veto_stops_the_attack_and_reports_health_budget(self):
        rpc = FakeRpc()
        world = _world(health=40, in_combat=True)
        phase, _ = fight(rpc, lambda: world, 'mob', [0, 0, 0], lambda: 0.0,
                         budget=5, advisory=True, enforce=True)
        self.assertEqual(phase['outcome'], 'health_budget')
        self.assertIn('enforced', phase)
        self.assertEqual(phase['enforced']['state'], LOW_HEALTH_EXIT)
        self.assertIn('bridge.stop_attack', rpc.calls)

    def test_a_veto_never_dispatches_a_second_start(self):
        rpc = FakeRpc()
        world = _world(health=40, in_combat=True)
        phase, _ = fight(rpc, lambda: world, 'mob', [0, 0, 0], lambda: 0.0,
                         budget=5, advisory=True, enforce=True)
        self.assertLessEqual(rpc.calls.count('bridge.start_attack'), 1)
        self.assertFalse(phase['start_sent_once'] and phase['outcome'] == 'health_budget'
                         and rpc.calls.count('bridge.start_attack') > 1)


if __name__ == '__main__':
    unittest.main()
