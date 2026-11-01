import unittest
from agent_wow_recovery_plan import (bridge_position_is_stale, corpse_guid,
                                      length, select_recovery)
from agent_wow_recovery_control import (corpse_from_events, reclaim_and_retreat,
                                        release_if_dead)


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


class LiveFactsTests(unittest.TestCase):
    """Facts measured on 2026-11-01 that the corpse run depends on."""

    def test_the_corpse_is_identified_by_the_player_own_guid(self):
        # The corpse object carries the player's own guid (ObjectTypeId Corpse
        # reuses it), so it is never identified by a guessed value.
        objects=[{'guid':'9','type_id':4,'entry':0},
                 {'guid':'12345','type_id':3,'entry':3124}]
        self.assertEqual(corpse_guid(objects),'9')
        self.assertIsNone(corpse_guid([{'guid':'1','type_id':3}]))

    def test_the_corpse_position_comes_from_the_server_answer(self):
        events=[{'kind':'corpse','corpse':{'found':True,'map':1,
                                          'position_xyz':[1.0,2.0,3.0]}}]
        self.assertEqual(corpse_from_events(events)['position_xyz'],[1.0,2.0,3.0])
        self.assertIsNone(corpse_from_events(
            [{'kind':'corpse','corpse':{'found':False}}]))
        self.assertIsNone(corpse_from_events([]))

    def test_a_dead_character_is_released_before_it_is_a_ghost(self):
        # A character that just died is still awaiting the release prompt;
        # the ghost, and therefore the corpse query, only exists afterwards.
        calls=[]
        dead={'self':{'ghost':False,'health':0}}
        ghost={'self':{'ghost':True,'health':1}}
        samples=iter([dead,ghost])
        def rpc(method,params):calls.append(method);return {'result':{}}
        state=release_if_dead(rpc,lambda:next(samples))
        self.assertIn('bridge.release_spirit',calls)
        self.assertTrue(state['self']['ghost'])

    def test_a_living_character_is_not_released(self):
        calls=[]
        alive={'self':{'ghost':False,'health':108}}
        state=release_if_dead(lambda m,p:{'result':{}},lambda:alive)
        self.assertEqual(calls,[])
        self.assertFalse(state['self']['ghost'])

    def test_a_stale_bridge_position_is_detected(self):
        # A release teleports the ghost to the graveyard without telling the
        # bridge, so the bridge rejects the first path segment. The corpse run
        # has to happen in a session that starts with the character a ghost.
        graveyard=[-634.635,-4296.030,40.525]
        pack=[-707.979,-4348.810,49.211]
        self.assertTrue(bridge_position_is_stale(graveyard,pack))
        self.assertFalse(bridge_position_is_stale(graveyard,graveyard))
        self.assertFalse(bridge_position_is_stale(graveyard,None))


if __name__=='__main__':unittest.main()
