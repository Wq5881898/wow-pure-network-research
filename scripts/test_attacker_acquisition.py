import unittest
import base64
import copy
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agent_wow_attackers import active_attackers, bind_single_attacker, planned_attacker


class AttackerAcquisitionTests(unittest.TestCase):
    def test_planned_contact_checks_identity_level_health_and_count(self):
        state = self.state([self.event('attack_start')])
        state['self'].update(health=203,max_health=203,ghost=False)
        state['visible_objects'][0].update(entry=3125,level=6)
        self.assertEqual(planned_attacker(state,'9',3125,6,6)['guid'],'42')
        for changes in ({'entry':3099}, {'level':5}):
            changed = copy.deepcopy(state)
            changed['visible_objects'][0].update(changes)
            self.assertIsNone(planned_attacker(changed,'9',3125,6,6))
        changed = copy.deepcopy(state)
        changed['self']['health'] = 60
        self.assertIsNone(planned_attacker(changed,'9',3125,6,6))
        state['event_log'].append(self.event('attack_start','43'))
        self.assertIsNone(planned_attacker(state,'9',3125,6,6))

    def state(self, events):
        return {'self': {'in_combat': True}, 'event_log': events,
                'visible_objects': [{'guid': '42', 'type_id': 3, 'health': 100},
                                    {'guid': '43', 'type_id': 3, 'health': 100}]}

    def event(self, kind, attacker='42', victim='9'):
        return {'kind': kind, 'attack': {'attacker_guid': attacker,
                                        'target_guid': victim}}

    def test_damage_identifies_attacker_not_nearest_bystander(self):
        state = self.state([self.event('attacker_state')])
        backend = SimpleNamespace(drain=lambda: state, self_guid='9', target=None)
        self.assertTrue(bind_single_attacker(backend))
        self.assertEqual(backend.target['guid'], '42')

    def test_stop_or_changed_victim_cancels_old_attack(self):
        for ending in [self.event('attack_stop'), self.event('attack_start', victim='88')]:
            self.assertEqual(active_attackers(self.state([
                self.event('attack_start'), ending]), '9'), [])

    def test_multiple_or_no_attackers_never_guess(self):
        for events in [[], [self.event('attack_start'), self.event('attack_start', '43')]]:
            backend = SimpleNamespace(drain=lambda: self.state(events), self_guid='9', target=None)
            self.assertFalse(bind_single_attacker(backend))
            self.assertIsNone(backend.target)

    def test_recovery_binds_attacker_before_planning(self):
        from test_exit_recovery import FakeBackend, Observation
        from agent_wow_exit_recovery import resolve_combat_before_exit
        from unittest.mock import patch
        backend = FakeBackend([Observation(100, in_combat=True)])
        target = backend.target
        backend.target = None
        def acquire():
            backend.target = target
            return True
        backend.acquire_attacker = Mock(side_effect=acquire)
        with patch('agent_wow_exit_recovery._plan_escape_route', return_value=None) as planner, \
                patch('agent_wow_exit_recovery._finish_the_target', return_value={
                    'outcome': 'target_dead', 'final': Observation(100, in_combat=False)}), \
                patch('agent_wow_exit_recovery._target_reachable', return_value=True):
            resolve_combat_before_exit(backend, passive_seconds=0, budget_seconds=0)
        backend.acquire_attacker.assert_called_once()
        planner.assert_called_once_with(backend)
        self.assertEqual(backend.target, target)

    def test_actual_death_prefix_identifies_attacker(self):
        from agent_wow_worldstate import replay
        path = Path(__file__).resolve().parents[1] / 'results/pure_network/agent_wow_bridge_scorp_to9_20261110_cl.packets.json'
        trace = json.loads(path.read_text())
        trace['packets'] = [p for p in trace['packets'] if p['sequence'] <= 17]
        state = replay(trace)
        self.assertEqual(state['errors'], [])
        self.assertEqual([o['guid'] for o in active_attackers(state, trace['self_guid'])],
                         ['17379391014452066403'])

    def test_attack_start_requires_two_full_guids(self):
        from agent_wow_worldstate import replay
        path = Path(__file__).resolve().parents[1] / 'results/pure_network/agent_wow_bridge_scorp_to9_20261110_cl.packets.json'
        trace = json.loads(path.read_text())
        packet = next(p for p in trace['packets'] if p['opcode'] == 0x143)
        for payload in [struct.pack('<QQ', 42, 9)[:n] for n in range(16)] + [struct.pack('<QQB', 42, 9, 0)]:
            broken = copy.deepcopy(trace)
            next(p for p in broken['packets'] if p['sequence'] == packet['sequence'])['payload_base64'] = base64.b64encode(payload).decode()
            self.assertTrue(replay(broken)['errors'])


if __name__ == '__main__':
    unittest.main()
