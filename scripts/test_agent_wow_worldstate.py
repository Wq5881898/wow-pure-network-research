"""Replay real capture and reject corrupted, oversized and incomplete streams."""
import base64
import copy
import json
import struct
import unittest
import zlib
from pathlib import Path

from agent_wow_worldstate import DecodeError, IncrementalWorld, MAX_BYTES, Reader, quest_packet, loot_packet, attacker_state, attack_stop, creature_query, monster_move, decompress, replay, updates


class DecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = json.loads((Path(__file__).resolve().parents[1] / "results/pure_network/agent_wow_capture_20261021_run01.json.packets.json").read_text())

    def test_real_replay_and_independent_position(self):
        a, b = replay(self.trace), replay(self.trace)
        self.assertEqual(a, b)
        self.assertEqual(a["status"], "PASS")
        self.assertEqual(a["self"]["movement"]["position_xyzo"], a["login"]["position_xyzo"])
        self.assertEqual((a["self"]["level"], a["self"]["health"], a["self"]["max_health"]), (1, 70, 70))
        self.assertTrue(any(o.get("type_id") == 3 for o in a["visible_objects"]))

    def test_all_real_update_prefixes_rejected(self):
        for p in self.trace["packets"]:
            if p["opcode"] not in (0xa9, 0x1f6): continue
            raw = base64.b64decode(p["payload_base64"])
            raw = decompress(raw) if p["opcode"] == 0x1f6 else raw
            for n in range(len(raw)):
                with self.assertRaises(DecodeError): updates(raw[:n])

    def test_compression_bounds_and_trailing_stream(self):
        for raw in (struct.pack("<I", MAX_BYTES+1)+zlib.compress(b"a"),
                    struct.pack("<I", 1)+zlib.compress(b"ab"),
                    struct.pack("<I", 1)+zlib.compress(b"a")+b"extra"):
            with self.assertRaises(DecodeError): decompress(raw)

    def test_sequence_gap_and_corruption_mark_incomplete(self):
        trace = copy.deepcopy(self.trace)
        trace["packets"][2]["sequence"] += 1
        self.assertEqual(replay(trace)["status"], "INCOMPLETE")
        trace = copy.deepcopy(self.trace)
        trace["packets"][1]["payload_base64"] = "!!!"
        self.assertTrue(replay(trace)["errors"])

    def test_packed_guid_and_unknown_update(self):
        self.assertEqual(Reader(bytes([0x81, 9, 0xf1])).guid(), str(9+(0xf1<<56)))
        with self.assertRaises(DecodeError): updates(struct.pack("<I", 1)+b"\xff")

    def test_real_creature_queries_and_truncation(self):
        path = Path(__file__).resolve().parents[1] / "results/pure_network/agent_wow_bridge_run02.packets.json"
        trace = json.loads(path.read_text())
        decoded = replay(trace)
        self.assertEqual(decoded["status"], "PASS")
        self.assertEqual(decoded["creature_templates"]["3143"]["name"], "Gornek")
        self.assertFalse(decoded["creature_templates"]["2147483647"]["found"])
        for p in trace["packets"]:
            if p["opcode"] != 0x61: continue
            raw = base64.b64decode(p["payload_base64"])
            for n in range(len(raw)):
                with self.assertRaises(DecodeError): creature_query(raw[:n])

    def test_real_movement_and_truncation(self):
        trace = json.loads((Path(__file__).resolve().parents[1]/"results/pure_network/agent_wow_bridge_run05_motion.packets.json").read_text())
        state = replay(trace)
        self.assertEqual(state["status"], "PASS")
        self.assertEqual(len(state["motions"]), 2)
        for p in trace["packets"]:
            if p["opcode"] != 0xdd: continue
            raw = base64.b64decode(p["payload_base64"])
            motion = monster_move(raw)
            self.assertGreater(motion["duration_ms"], 0)
            self.assertEqual(motion["path_xyz"][0], motion["start_xyz"])
            for n in range(len(raw)):
                with self.assertRaises(DecodeError): monster_move(raw[:n])

    def test_motion_stop_and_signed_packed_offsets(self):
        prefix = b"\x01\x09\x00" + struct.pack("<3fIB", 0, 0, 0, 7, 1)
        self.assertTrue(monster_move(prefix)["stopped"])
        prefix = prefix[:-1]+b"\x00"
        # Midpoint (2,2,2) minus signed offsets (-1,+1,-.5).
        packed = (2044 | (4 << 11) | (1022 << 22))
        raw = prefix+struct.pack("<III3fI",0,1000,2,4,4,4,packed)
        self.assertEqual(monster_move(raw)["path_xyz"][1],[3,1,2.5])

    def test_attack_samples(self):
        trace=json.loads((Path(__file__).resolve().parents[1]/"results/pure_network/agent_wow_bridge_run18_attack_stop.packets.json").read_text())
        state=replay(trace)
        stops=[e for e in state["event_log"] if e.get("kind")=="attack_stop"]
        hits=[e for e in state["event_log"] if e.get("kind")=="attacker_state"]
        self.assertTrue(stops);self.assertFalse(hits)
        self.assertEqual(stops[0]["attack"]["attacker_guid"],"9")

    def test_actual_outgoing_damage_and_all_hit_prefixes(self):
        trace=json.loads((Path(__file__).resolve().parents[1]/"results/pure_network/agent_wow_bridge_run15_attack.packets.json").read_text())
        hits=[]
        for p in trace['packets']:
            if p['opcode']!=0x14a:continue
            raw=base64.b64decode(p['payload_base64'])
            hit=attacker_state(raw);hits.append(hit)
            for n in range(len(raw)):
                with self.assertRaises(DecodeError):attacker_state(raw[:n])
        outgoing=[h for h in hits if h['attacker_guid']=='9']
        self.assertEqual(outgoing[0]['full_damage'],11)
        self.assertEqual(outgoing[0]['target_guid'],'17379391013998560059')

    def test_damage_optional_flags_from_local_unit_header(self):
        # FULL_ABSORB|PARTIAL_RESIST|BLOCK|RAGE_GAIN; flags match Unit.h.
        flags=0x20|0x100|0x2000|0x800000
        raw=struct.pack('<I',flags)+b'\x01\x09\x01\x0a'
        raw+=struct.pack('<IIBIfI',3,0,1,1,3.,3)
        raw+=struct.pack('<II',2,1)+struct.pack('<BIIII',1,0,0,4,0)
        parsed=attacker_state(raw)
        self.assertEqual(parsed['absorbs'],[2]);self.assertEqual(parsed['resists'],[1]);self.assertEqual(parsed['blocked'],4)

    def test_real_kill_and_combat_end(self):
        trace=json.loads((Path(__file__).resolve().parents[1]/'results/pure_network/agent_wow_bridge_run23_combat_phases.packets.json').read_text())
        state=replay(trace)
        self.assertEqual(state['status'],'PASS')
        self.assertIs(state['self']['in_combat'],False)
        target=next(o for o in state['visible_objects'] if o['guid']=='17379391013998560059')
        self.assertEqual(target['health'],0)
        hits=[e['attack'] for e in state['event_log'] if e['kind']=='attacker_state' and e['attack']['attacker_guid']=='9']
        self.assertEqual([h['full_damage'] for h in hits],[11,12,12,12])
        self.assertEqual(hits[-1]['overkill'],5)

    def test_real_loot_and_inventory_confirmation(self):
        trace=json.loads((Path(__file__).resolve().parents[1]/'results/pure_network/agent_wow_bridge_run24_loot.packets.json').read_text())
        state=replay(trace)
        self.assertEqual(state['status'],'PASS')
        pushes=[e['loot'] for e in state['event_log'] if e.get('opcode')==0x166]
        self.assertEqual((pushes[0]['entry'],pushes[0]['count'],pushes[0]['inventory_count']),(7098,1,1))
        self.assertTrue(any(o['type_id']==1 and o['entry']==7098 for o in state['visible_objects']))
        for p in trace['packets']:
            if p['opcode'] not in (0x160,0x161,0x162,0x166):continue
            raw=base64.b64decode(p['payload_base64'])
            for n in range(len(raw)):
                with self.assertRaises(DecodeError):loot_packet(p['opcode'],raw[:n])

    def test_real_quest_delivery_and_next_autoaccept(self):
        trace=json.loads((Path(__file__).resolve().parents[1]/'results/pure_network/agent_wow_bridge_run31_quest_turnin.packets.json').read_text())
        state=replay(trace)
        self.assertEqual(state['status'],'PASS')
        self.assertEqual([q['quest'] for q in state['self']['quest_log']],[788])
        replies=[e for e in state['event_log'] if e.get('opcode')==0x191]
        payload=base64.b64decode(replies[0]['payload_base64'])
        self.assertEqual(struct.unpack_from('<II',payload),(4641,40))
        for p in trace['packets']:
            if p['opcode']!=0x17d:continue
            raw=base64.b64decode(p['payload_base64'])
            self.assertEqual(quest_packet(0x17d,raw)['quests'][0]['quest'],4641)
            for n in range(len(raw)):
                with self.assertRaises(DecodeError):quest_packet(0x17d,raw[:n])

    def test_real_ghost_reclaim(self):
        root=Path(__file__).resolve().parents[1]/'results/pure_network'
        ghost=replay(json.loads((root/'agent_wow_bridge_run41_release.packets.json').read_text()))
        alive=replay(json.loads((root/'agent_wow_bridge_run42_reclaim.packets.json').read_text()))
        self.assertEqual(ghost['status'],'PASS');self.assertTrue(ghost['self']['ghost'])
        self.assertEqual(ghost['self']['health'],1)
        self.assertFalse(alive['self']['ghost']);self.assertEqual(alive['self']['health'],35)
        self.assertEqual(alive['self']['quest_log'][0]['quest'],788)
        self.assertTrue(any(e.get('kind')=='corpse' and e['corpse']['found'] for e in alive['event_log']))

    def test_real_quest788_first_credit(self):
        trace=json.loads((Path(__file__).resolve().parents[1]/'results/pure_network/agent_wow_bridge_run43_controlled_quest.packets.json').read_text())
        state=replay(trace)
        self.assertEqual(state['status'],'PASS')
        self.assertFalse(state['self']['ghost']);self.assertFalse(state['self']['in_combat'])
        self.assertEqual(state['self']['quest_log'][0]['counters'][0],1)
        credits=[e for e in state['event_log'] if e.get('kind')=='quest_kill']
        self.assertEqual((credits[0]['quest'],credits[0]['entry'],credits[0]['current'],credits[0]['required']),(788,3098,1,8))

    def test_quest788_full_credit_reward_and_relogin(self):
        folder=Path(__file__).resolve().parents[1]/'results/pure_network'
        runs=['run43_controlled_quest','run58_supervised_kill','run59_quest788_controlled','run61_quest788_controlled','run62_quest788_controlled','run64_quest788_controlled','run65_quest788_controlled','run66_quest788_controlled']
        counts=[]
        for run in runs:
            state=replay(json.loads((folder/f'agent_wow_bridge_{run}.packets.json').read_text()))
            self.assertEqual(state['status'],'PASS')
            counts.extend(e['current'] for e in state['event_log'] if e.get('kind')=='quest_kill' and e['quest']==788)
        self.assertEqual(counts,list(range(1,9)))
        reward=replay(json.loads((folder/'agent_wow_bridge_run72_quest788_reward.packets.json').read_text()))
        replies=[e for e in reward['event_log'] if e.get('opcode')==0x191]
        self.assertEqual(struct.unpack_from('<II',base64.b64decode(replies[0]['payload_base64'])),(788,220))
        final=replay(json.loads((folder/'agent_wow_bridge_run73_reward_verify.packets.json').read_text()))
        self.assertEqual(final['status'],'PASS');self.assertEqual(final['self']['quest_log'],[])
        self.assertEqual((final['self']['level'],final['self']['health'],final['self']['max_health']),(2,89,89))
        self.assertFalse(final['self']['ghost']);self.assertFalse(final['self']['in_combat'])

    def test_class_quest_reward_preserves_collection_quest(self):
        folder=Path(__file__).resolve().parents[1]/'results/pure_network'
        state=replay(json.loads((folder/'agent_wow_bridge_run77_trainer_reward.packets.json').read_text()))
        self.assertEqual(state['status'],'PASS')
        self.assertEqual([q['quest'] for q in state['self']['quest_log']],[789])
        rewards=[e for e in state['event_log'] if e.get('opcode')==0x191]
        self.assertEqual(struct.unpack_from('<II',base64.b64decode(rewards[0]['payload_base64'])),(2383,40))

    def test_collection_stack_and_recovery_preserve_items(self):
        folder=Path(__file__).resolve().parents[1]/'results/pure_network'
        for name in ('run79_scorpid_collection','run92_collection_recovery'):
            state=replay(json.loads((folder/f'agent_wow_bridge_{name}.packets.json').read_text()))
            self.assertEqual(state['status'],'PASS')
            tails=sum(o['stack_count'] for o in state['visible_objects'] if o.get('owner_guid')=='9' and o['entry']==4862)
            self.assertEqual(tails,2)
        # Recovery snapshot was intermediate: actual final trace shows re-death.
        self.assertTrue(state['self']['ghost'])
        self.assertEqual(state['self']['health'],1)
        self_events=[e for e in state['event_log'] if e.get('guid')=='9']
        self.assertTrue(any(e.get('fields',{}).get('24')==44 and e.get('fields',{}).get('150')==0 for e in self_events))

    def test_spirit_healer_recovery_and_independent_relogin(self):
        folder=Path(__file__).resolve().parents[1]/'results/pure_network'
        for run in ('run94_spirit_healer','run95_alive_verify'):
            state=replay(json.loads((folder/f'agent_wow_bridge_{run}.packets.json').read_text()))
            self.assertEqual(state['status'],'PASS')
            self.assertFalse(state['self']['ghost']);self.assertFalse(state['self']['in_combat'])
            self.assertEqual((state['self']['health'],state['self']['max_health']),(89,89))
            self.assertEqual(sum(o['stack_count'] for o in state['visible_objects'] if o['entry']==4862 and o.get('owner_guid')=='9'),2)

    def test_incremental_real_pages_equal_full_replay(self):
        folder=Path(__file__).resolve().parents[1]/'results/pure_network'
        for run in ('run23_combat_phases','run78_scorpid_collection','run92_collection_recovery','run95_alive_verify'):
            trace=json.loads((folder/f'agent_wow_bridge_{run}.packets.json').read_text())
            engine=IncrementalWorld(trace['session_id'],trace['self_guid'])
            prefix=[]
            for offset in range(0,len(trace['packets']),17):
                batch=trace['packets'][offset:offset+17];prefix.extend(batch)
                actual=engine.apply(batch)
                expected=replay({**trace,'capture_complete':True,'packets':prefix})
                self.assertEqual(actual,expected)
            self.assertEqual(engine.apply([]),actual)
            with self.assertRaises(DecodeError):engine.apply(trace['packets'][:1])

    def test_incremental_collection_and_relogin_three_tails(self):
        folder=Path(__file__).resolve().parents[1]/'results/pure_network'
        for run in ('run97_scorpid_incremental','run98_collection_verify'):
            trace=json.loads((folder/f'agent_wow_bridge_{run}.packets.json').read_text())
            engine=IncrementalWorld(trace['session_id'],'9')
            for i in range(0,len(trace['packets']),32):state=engine.apply(trace['packets'][i:i+32])
            self.assertEqual(state,replay(trace))
            self.assertEqual(sum(o['stack_count'] for o in state['visible_objects'] if o['entry']==4862 and o.get('owner_guid')=='9'),3)
            self.assertEqual(state['self']['health'],89)
            self.assertFalse(state['self']['ghost']);self.assertFalse(state['self']['in_combat'])


if __name__ == "__main__":
    unittest.main()
