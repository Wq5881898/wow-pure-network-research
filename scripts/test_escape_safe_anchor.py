import unittest
from unittest.mock import Mock, patch
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import _plan_escape_route, _perform_retreat
from types import SimpleNamespace


class EscapeAnchorTests(unittest.TestCase):
    def backend(self):
        b = PureNetworkBackend('offline_anchor')
        b.target = {'guid':'42','movement':{'position_xyzo':[1,0,0]}}
        b.submitted_position = [0,0,0]
        b.escape_safe_anchors = [[200,0,5]]
        b._nav = Mock(return_value={'waypoints':[[0,0,0],[200,0,5]],
                                   'surface_samples':[[0,0,0],[100,0,3],[200,0,5]]})
        return b

    def test_full_anchor_route_keeps_surface_and_anchor(self):
        b = self.backend()
        with patch('agent_wow_session.estimated_position', return_value=([1,0,0],None)):
            route = b.plan_escape_route()
        self.assertEqual(route['safe_anchor'],[200,0,5])
        self.assertIn({'x':100.0,'y':0.0,'z':3.0},route['points'])
        b._nav.assert_called_once_with([0,0,0],[200,0,5])

    def test_short_anchor_is_not_an_escape(self):
        b = self.backend()
        b.escape_safe_anchors = [[4,0,0]]
        with patch('agent_wow_session.estimated_position', return_value=([1,0,0],None)):
            self.assertIsNone(b.plan_escape_route(candidates=0))
        b._nav.assert_not_called()

    def test_displaced_endpoint_is_not_anchor_arrival(self):
        b = self.backend()
        b._nav.return_value = {'waypoints':[[0,0,0],[170,0,0]]}
        with patch('agent_wow_session.estimated_position', return_value=([1,0,0],None)):
            self.assertIsNone(b.plan_escape_route(candidates=0))

    def test_actual_exit_entry_prefers_fresh_anchor_over_48_yard_patrol(self):
        b = self.backend()
        b.travel_return_route = {'origin':[0,0,0], 'points':[
            {'x':x,'y':0,'z':0} for x in (0,-12,-24,-36,-48)]}
        with patch('agent_wow_session.estimated_position', return_value=([1,0,0],None)):
            route = _plan_escape_route(b)
        self.assertEqual(route['safe_anchor'], [200,0,5])
        self.assertEqual(route['points'][-1], {'x':200.0,'y':0.0,'z':5.0})

    def test_second_recovery_replans_from_synchronized_landing(self):
        b = self.backend()
        b.observe = Mock(return_value=SimpleNamespace(health=200,in_combat=True))
        state = {'self':{'health':200,'in_combat':True}}
        old_route = {'origin':[0,0,0], 'points':[{'x':0,'y':0,'z':0},
                     {'x':100,'y':0,'z':3}]}
        with patch('agent_wow_escape_control.retreat', return_value=({
                'movement':[{'result':{'submittedPosition':[100,0,3]}}]},state)):
            _perform_retreat(b, b.observe(), old_route)
        self.assertEqual(b.current_position,[100,0,3])
        with patch('agent_wow_session.estimated_position', return_value=([1,0,0],None)):
            route = _plan_escape_route(b)
        self.assertEqual(route['origin'],[100,0,3])
        self.assertEqual(b._nav.call_args.args[0],[100,0,3])

    def test_stale_shared_origin_cannot_override_valid_local_emergency(self):
        b = SimpleNamespace(submitted_position=[20,0,0], escape_route=Mock(return_value={
            'origin':[0,0,0], 'points':[{'x':0,'y':0,'z':0},{'x':200,'y':0,'z':0}]}),
            travel_return_route={'origin':[30,0,0], 'points':[
                {'x':x,'y':0,'z':0} for x in (30,20,10,0)]})
        route = _plan_escape_route(b)
        self.assertEqual([p['x'] for p in route['points']], [20,10,0])
        self.assertFalse(route['full_escape_distance'])
        b.submitted_position = [0,0,0]
        b.escape_route.return_value = None
        self.assertIsNone(_plan_escape_route(b))

    def test_engaged_precondition_failure_enters_recovery_without_passive_wait(self):
        b = self.backend()
        b._refresh_target = Mock()
        b.escape_route = Mock(return_value={'points':[{'x':200,'y':0,'z':0}]})
        b._close_distance = Mock(return_value={'skipped':'already in combat'})
        b._await_engagement = Mock(return_value={'engaged':True})
        b.drain = Mock(return_value={'self':{'in_combat':True}})
        phase = {'outcome':'preconditions_not_met','start_sent_once':False,
                 'observations':[{'self_health':203,'in_combat':True,
                                  'target_health':102,'distance_estimate':12}]}
        with patch('agent_wow_session.estimated_position', return_value=([12,0,0],None)), \
                patch('agent_wow_session.fight', return_value=(phase,{})), \
                patch('agent_wow_exit_recovery.resolve_combat_before_exit',
                      return_value={'combat_cleared':True}) as recover:
            result = b._do_fight({'attack_attempts':1},75)
        self.assertEqual(result['outcome'],'preconditions_not_met')
        self.assertEqual(recover.call_args.kwargs['passive_seconds'],0)
        self.assertEqual(recover.call_args.kwargs['finish_budget_seconds'],0)


if __name__ == '__main__':
    unittest.main()
