import unittest
from agent_wow_recovery_plan import length,select_recovery
from agent_wow_recovery_control import reclaim_and_retreat


class RecoveryPlanTests(unittest.TestCase):
    def test_bent_route_not_chord(self):
        path=[[0,0,0],[0,20,0],[30,20,0],[60,20,0]]
        plan=select_recovery(path,path[0])
        self.assertLessEqual(plan['reclaim_distance_3d'],32)
        self.assertGreater(plan['path_progress_before_revive'],5)
        self.assertEqual(plan['retreat_chunks'][-1][-1],{'x':60,'y':20,'z':0})
        self.assertGreater(plan['corpse_to_graveyard_path_length'],60)

    def test_no_skip_outside_radius(self):
        plan=select_recovery([[0,0,0],[0,40,0],[10,0,0],[80,0,0]],[0,0,0])
        self.assertEqual(plan['reclaim_xyz'][0],0)

    def test_invalid_or_no_retreat(self):
        for path in ([[0,0,0]],[[0,0,0],[float('nan'),1,0]],[[0,0,0],[2,0,0]]):
            with self.assertRaises(ValueError):select_recovery(path,[0,0,0])

    def test_revive_then_movement_and_changed_corpse_rejected(self):
        plan=select_recovery([[0,0,0],[60,0,0]],[0,0,0])
        calls=[]
        ghost={'self':{'ghost':True,'health':1,'in_combat':False},'event_log':[{'kind':'corpse','corpse':{'found':True,'position_xyz':[0,0,0]}}]}
        alive={'self':{'ghost':False,'health':35,'in_combat':False}}
        samples=iter([ghost,alive]+[alive]*20)
        def rpc(method,params):calls.append(method);return {'result':{}}
        result,_=reclaim_and_retreat(rpc,lambda:next(samples),plan,plan['reclaim_xyz'])
        self.assertEqual(result['outcome'],'alive_out_of_combat')
        self.assertEqual(calls[:2],['bridge.reclaim_corpse','bridge.follow_path'])
        ghost['event_log'][0]['corpse']['position_xyz']=[20,0,0]
        with self.assertRaises(ValueError):reclaim_and_retreat(rpc,lambda:ghost,plan,plan['reclaim_xyz'])


if __name__=='__main__':unittest.main()
