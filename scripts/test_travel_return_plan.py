"""An executable return corridor must exist before forward dispatch."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from agent_wow_travel import prepare_return, travel, _same_return_geometry, plan_leg, movement_batches


class ReturnPlanTests(unittest.TestCase):
    def test_running_batches_preserve_samples_and_feedback_bounds(self):
        points = [{'x':i*.5,'y':0,'z':i*.01} for i in range(1,41)]
        batches = list(movement_batches(points,[0,0,0]))
        self.assertEqual([p for batch in batches for p in batch],points)
        self.assertEqual(len(batches),3)
        self.assertTrue(all(len(batch)<=14 for batch in batches))

    def test_planned_contact_hands_off_instead_of_retreat(self):
        backend = self.backend()
        def send(points, run=False):
            backend.submitted_position = [3,0,0]
            return [{}], None
        backend._send_path = send
        backend.drain = Mock(return_value={'self':{'health':100,'in_combat':True}})
        report = {}
        with patch('agent_wow_travel.plan_leg', return_value={
                'points':[{'x':3,'y':0,'z':0}], 'end':[3,0,0],
                'length':3,'progress':3,'goal_distance':17}), \
                patch('agent_wow_escape_control.retreat') as retreat:
            travel(backend, report=report, goal=[20,0,0], stop_distance=1,
                   combat_handoff=lambda state: True)
        retreat.assert_not_called()
        self.assertTrue(report['combat_handoff'])
        self.assertEqual(report['travel_return_plan']['safe_anchor'], [0,0,0])

    def test_plan_uses_detail_surface_instead_of_portal_height_chord(self):
        backend = self.backend()
        backend._nav.side_effect = None
        backend._nav.return_value = {'waypoints': [[0,0,0],[8,0,0]],
                                     'surface_samples': [[0,0,0],[4,0,1],[8,0,0]]}
        leg = plan_leg(backend, [8,0,0], radius=10)
        self.assertIn({'x':4,'y':0,'z':1}, leg['points'])

    def test_whole_leg_reverse_checks_once_when_geometry_matches(self):
        backend=self.backend()
        points=[{'x':x,'y':0,'z':0} for x in (1,2,3,4)]
        prepare_return(backend,[[0,0,0]],points)
        self.assertEqual(backend._nav.call_count,1)

    def test_geometry_check_rejects_corner_cut_and_other_height(self):
        self.assertFalse(_same_return_geometry([[0,0,0],[0,3,0],[3,3,0]],[[0,0,0],[3,3,0]]))
        self.assertFalse(_same_return_geometry([[0,0,0],[3,0,0]],[[0,0,2],[3,0,2]]))

    def test_surface_projection_requires_bidirectional_agreement(self):
        backend = self.backend()
        backend._nav.side_effect = None
        backend._nav.return_value = {'waypoints': [[0, 0, .333]]}
        backend._densify = lambda *args, **kw: []
        prepare_return(backend, [[0,0,0]], [{'x':0,'y':0,'z':.333}])
        backend._nav.side_effect = [
            {'waypoints': [[0,0,.333]]}, {'waypoints': [[0,0,0]]}]
        with self.assertRaisesRegex(RuntimeError, 'no validated points'):
            prepare_return(backend, [[0,0,0]], [{'x':0,'y':0,'z':.333}])

    def test_only_coincident_small_surface_correction_can_have_empty_reverse(self):
        backend = self.backend()
        backend._nav.return_value = {'waypoints': [[0, 0, .147]]}
        backend._nav.side_effect = None
        backend._densify = lambda *args, **kw: []
        _, route = prepare_return(backend, [[0,0,0]], [{'x':0,'y':0,'z':.147}])
        self.assertEqual(route[-1]['z'], 0)
        with self.assertRaisesRegex(RuntimeError, 'no validated points'):
            prepare_return(backend, [[0,0,0]], [{'x':1,'y':0,'z':.147}])

    def backend(self):
        backend = SimpleNamespace(submitted_position=[0, 0, 0], current_position=None,
                                  observed_position=[0, 0, 0])
        backend._nav = Mock(side_effect=lambda a, b: {'waypoints': [b]})
        backend._densify = lambda raw, start, **kw: [dict(zip(('x','y','z'), p))
                                                     for p in raw['waypoints']]
        return backend

    def test_reverse_corridor_ends_at_original_anchor(self):
        backend = self.backend()
        corridor, reverse = prepare_return(backend, [[0, 0, 0]],
                                            [{'x': 3, 'y': 0, 'z': 0}])
        self.assertEqual(reverse[-1], {'x': 0, 'y': 0, 'z': 0})
        backend._nav.assert_called_once_with([3, 0, 0], [0, 0, 0])

    def test_failed_reverse_check_prevents_forward_movement(self):
        backend = self.backend()
        backend._nav.side_effect = RuntimeError('reverse not reachable')
        backend._send_path = Mock()
        with patch('agent_wow_travel.plan_leg', return_value={
                'points': [{'x': 3, 'y': 0, 'z': 0}], 'end': [3,0,0]}):
            result = travel(backend, report={}, goal=[20,0,0], stop_distance=1)
        backend._send_path.assert_not_called()
        self.assertIn('return route precheck failed', result['legs'][0]['reason'])

    def test_shorter_leg_must_pass_same_reverse_check_before_moving(self):
        backend = self.backend()
        backend._nav.side_effect = [RuntimeError('projection rejected'),
                                    {'waypoints': [[0,0,0]]}]
        def leg(x):
            return {'points': [{'x':x,'y':0,'z':0}], 'end':[x,0,0],
                    'length':x,'progress':x,'goal_distance':20-x}
        def send(points, run=False):
            backend.submitted_position = [points[-1]['x'],0,0]
            return [{}], None
        backend._send_path = Mock(side_effect=send)
        backend.drain = Mock(return_value={'self':{'health':100,'ghost':False,'in_combat':False}})
        report = {}
        with patch('agent_wow_travel.plan_leg', side_effect=[leg(10),leg(5)]):
            travel(backend, report=report, goal=[20,0,0], legs=1, stop_distance=1)
        self.assertEqual(backend._send_path.call_args.args[0][-1]['x'],5)
        self.assertTrue(report['return_precheck_retries'][0]['shorter_leg_checked'])

    def test_combat_uses_prepared_retreat_without_target_or_attack(self):
        backend = self.backend()
        def send(points, run=False):
            backend.submitted_position = [3, 0, 0]
            return [{}], None
        backend._send_path = send
        backend.rpc = Mock()
        backend.drain = Mock(return_value={'self': {'health': 90, 'in_combat': True}})
        report = {}
        with patch('agent_wow_travel.plan_leg', return_value={
                'points': [{'x':3,'y':0,'z':0}], 'end':[3,0,0],
                'length':3, 'progress':3, 'goal_distance':17}), \
                patch('agent_wow_escape_control.retreat', return_value=(
                    {'outcome':'out_of_combat'}, {})) as retreat:
            travel(backend, report=report, goal=[20,0,0], stop_distance=1)
        retreat.assert_called_once()
        self.assertEqual(retreat.call_args.kwargs['route'][-1], {'x':0,'y':0,'z':0})
        self.assertEqual(report['travel_return_plan']['safe_anchor'], [0,0,0])
        backend.rpc.assert_not_called()

    def test_mid_leg_combat_never_visits_or_returns_through_unvisited_suffix(self):
        backend = self.backend()
        sent = []
        def send(points, run=False):
            self.assertTrue(run)
            sent.append(points[-1]['x'])
            backend.submitted_position = [points[-1]['x'], 0, 0]
            return [{}], None
        backend._send_path = send
        backend.rpc = Mock()
        backend.drain = Mock(return_value={'self': {'health': 90, 'in_combat': True}})
        with patch('agent_wow_travel.plan_leg', return_value={
                'points': [{'x':3,'y':0,'z':0}, {'x':9,'y':0,'z':0}],
                'end':[9,0,0], 'length':9, 'progress':9, 'goal_distance':11}), \
                patch('agent_wow_escape_control.retreat', return_value=(
                    {'outcome':'out_of_combat'}, {})) as retreat:
            travel(backend, report={}, goal=[20,0,0], stop_distance=1)
        self.assertEqual(sent, [3])
        self.assertEqual([p['x'] for p in retreat.call_args.kwargs['route']], [3,0])


if __name__ == '__main__':
    unittest.main()
