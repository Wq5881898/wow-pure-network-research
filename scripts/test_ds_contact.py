import unittest
from unittest.mock import Mock, patch
from agent_wow_session import PureNetworkBackend
from agent_wow_combat_control import fight
from audit_ds_contact import reconstruct, GUID


class ContactTests(unittest.TestCase):
    def backend(self, distances=(8, 6, 4)):
        b = PureNetworkBackend('offline_contact')
        b.self_guid = '9'
        b.submitted_position = [0, 0, 0]
        b.current_position = [0, 0, 0]
        states = []
        for i, distance in enumerate(distances):
            target = {'guid': '42', 'type_id': 3, 'health': 100,
                      'movement': {'position_xyzo': [40, 0, 0]},
                      'server_motion': {'source_sequence': i, 'received_monotonic_ms': 1000,
                                        'start_xyz': [distance, 0, 0],
                                        'path_xyz': [[distance, 0, 0], [2, 0, 0]],
                                        'duration_ms': 1000, 'path_kind': 'linear',
                                        'facing_guid': '9'}}
            states.append({'self': {'guid':'9', 'health': 203, 'max_health':203,
                                    'ghost':False, 'in_combat':True},
                           'visible_objects': [target], 'event_log': [
                               {'kind':'attack_start','attack':{'attacker_guid':'42','target_guid':'9'}}]})
        b.target = states[0]['visible_objects'][0]
        def drain():
            b.state = states.pop(0) if len(states) > 1 else states[0]
            return b.state
        b.drain = drain
        b.stream_now = Mock(return_value=1000)
        return b

    def test_eight_yards_advancing_same_attacker_reaches_legal_contact(self):
        b = self.backend()
        with patch('agent_wow_session.time.sleep'):
            result = b._contact_gate()
        self.assertTrue(result['ready'])
        self.assertEqual([r['distance'] for r in result['samples']], [8,6,4])
        self.assertEqual(result['contact']['target_guid'], '42')

    def test_wrong_attacker_never_authorizes_selected_bystander(self):
        b = self.backend((46,))
        b.target = dict(b.target, guid='77')
        result = b._contact_gate()
        self.assertFalse(result['ready'])

    def test_stale_motion_does_not_become_close_by_waiting(self):
        b = self.backend((4,))
        b.stream_now.return_value = 10000
        self.assertFalse(b._contact_gate()['ready'])

    def test_production_fight_entry_uses_one_guid_and_contact_snapshot(self):
        b = self.backend((4,))
        from efficiency_session import reconcile_selection
        b.target.update(entry=3125, level=6)
        selected, _ = reconcile_selection(b, {'guid':'77'},3125,6,6)
        self.assertEqual(selected['guid'],'42')
        b.escape_route = Mock(side_effect=AssertionError('no nav in combat'))
        b.travel_return_route = {'points':[{'x':-180,'y':0,'z':0}]}
        b._await_engagement = Mock(return_value={'engaged':True})
        b.rpc = Mock(return_value={'result':{}})
        b.advisory = b.enforce = False
        with patch('agent_wow_session.fight', return_value=({'outcome':'target_dead',
                   'start_sent_once':True,'observations':[]}, {})) as combat:
            result = b._do_fight({'attack_attempts':1}, 30)
        args, kwargs = combat.call_args
        self.assertEqual(args[2:4], ('42', [0,0,0]))
        self.assertEqual(kwargs['target_position'], [4,0,0])
        self.assertEqual(kwargs['contact_evidence']['target_guid'], '42')
        self.assertEqual(kwargs['contact_evidence']['monotonic_ms'], 1000)
        self.assertNotIn('range_override', kwargs)
        self.assertEqual(result['outcome'],'target_dead')

    def test_selection_rejects_unplanned_attacker_level(self):
        from efficiency_session import reconcile_selection
        b = self.backend((4,))
        b.target.update(entry=3125, level=5)
        with self.assertRaises(RuntimeError):
            reconcile_selection(b, {'guid':'77'},3125,6,6)

    def test_far_distance_cannot_be_overridden_by_old_nav(self):
        b = self.backend((46,))
        state = b.drain()
        rpc = Mock(return_value={'result':{}})
        phase, _ = fight(rpc, lambda:state, '42', [0,0,0], lambda:1000,
                         budget=.1, target_position=[46,0,0], range_override=2)
        self.assertFalse(phase['start_sent_once'])
        self.assertFalse(any(c.args[0]=='bridge.start_attack' for c in rpc.call_args_list))

    def test_real_combat_dispatch_after_eight_yard_contact(self):
        b = self.backend()
        with patch('agent_wow_session.time.sleep'):
            contact = b._contact_gate()['contact']
        state = b.drain()
        rpc = Mock(return_value={'result':{}})
        with patch('agent_wow_combat_control.time.sleep'):
            phase, _ = fight(rpc, lambda:state, '42', [0,0,0], lambda:1000,
                             budget=.01, start_range=5, target_position=contact['target_position'],
                             contact_evidence=contact)
        self.assertTrue(phase['start_sent_once'])
        starts = [c for c in rpc.call_args_list if c.args[0]=='bridge.start_attack']
        self.assertEqual(len(starts),1)
        self.assertEqual(starts[0].args[1],{'guid':'42'})

    def test_real_ds_replay_proves_selected_is_not_event_attacker(self):
        report = reconstruct()
        self.assertEqual(report['errors'], [])
        row = next(r for r in report['timeline'] if r['sequence']==655)
        self.assertEqual(row['selected_guid'],GUID)
        self.assertGreater(row['distance'],46)
        self.assertEqual([a['guid'] for a in row['attackers']], ['17379391014452081282'])
        self.assertLess(__import__('math').dist(row['player_submitted'],row['attackers'][0]['estimate'][0]),3)


if __name__ == '__main__':
    unittest.main()
