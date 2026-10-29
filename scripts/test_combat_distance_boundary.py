import unittest
from unittest.mock import patch
from agent_wow_combat_control import fight


def state(x, health=5):
    return {'self': {'ghost': False, 'health': 108, 'in_combat': True},
            'visible_objects': [{'guid': 'enemy', 'health': health,
                                 'movement': {'position_xyzo': [x, 0, 0, 0]}}]}


class CombatBoundaryTests(unittest.TestCase):
    def test_flee_stops_without_restart(self):
        calls = []
        def rpc(method, params):
            calls.append(method)
            return {'result': {}}
        with patch('agent_wow_combat_control.time.sleep'):
            result, _ = fight(rpc, unittest.mock.Mock(side_effect=[state(3), state(8), state(8)]),
                              'enemy', [0, 0, 0], lambda: 0, budget=1)
        self.assertEqual(result['outcome'], 'target_left_melee')
        self.assertEqual(calls.count('bridge.start_attack'), 1)
        self.assertEqual(calls[-1], 'bridge.stop_attack')

    def test_dead_target_not_mistaken_for_flee(self):
        calls = []
        def rpc(method, params):
            calls.append(method)
            return {'result': {}}
        result, _ = fight(rpc, lambda: state(8, 0), 'enemy', [0, 0, 0], lambda: 0, budget=1)
        self.assertEqual(result['outcome'], 'target_dead')
        self.assertNotIn('bridge.start_attack', calls)
