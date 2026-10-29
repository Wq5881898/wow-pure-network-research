"""Build and exercise read-only bridge via real Docker and JSON-RPC."""
import json
import argparse
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from wsl_lab_connect import Relay, Server, root, gateway
from agent_wow_session import ensure_bridge_module
from agent_wow_worldstate import replay, IncrementalWorld
from agent_wow_combat_control import fight, estimated_position
from agent_wow_escape_control import retreat
from agent_wow_session_supervisor import settle

work = Path.home()/"wow-research"
parser=argparse.ArgumentParser()
parser.add_argument("--run-id",default="run02")
parser.add_argument("--step",action="store_true")
parser.add_argument("--path-file",type=Path)
parser.add_argument("--attack-guid")
parser.add_argument("--skip-build",action="store_true")
parser.add_argument("--approach-entry",type=int)
parser.add_argument("--attack-near",action="store_true")
parser.add_argument("--stop-attack",action="store_true")
parser.add_argument("--settle-combat",action="store_true",help="Observe combat flag; bounded single-result attacks on the same live target until dead or budget ends")
parser.add_argument("--loot",action="store_true")
parser.add_argument("--quest-entry",type=int)
parser.add_argument("--query-quest",type=int)
parser.add_argument("--accept-quest",type=int)
parser.add_argument("--reward-quest",type=int)
parser.add_argument("--release-spirit",action="store_true")
parser.add_argument("--query-corpse",action="store_true")
parser.add_argument("--reclaim-corpse",action="store_true")
parser.add_argument("--controlled-combat",action="store_true")
parser.add_argument("--run-path",action="store_true")
parser.add_argument("--spirit-healer",action="store_true")
parser.add_argument("--observe-seconds",type=float,default=0)
parser.add_argument("--combat-budget",type=float,default=35)
parser.add_argument("--max-target-level",type=int,default=2)
parser.add_argument("--combat-entry",type=int,default=3098)
options=parser.parse_args()
if not options.run_id.replace('_','').isalnum():raise SystemExit("invalid run ID")
folder = work/"bridge-live"
ensure_bridge_module()

result={"status":"INCOMPLETE","gateway":gateway,"scope":"Docker bridge world-state substream","operations":[]}
packets=[];cursor=0;session_id=None
incremental=None;incremental_cursor=0
servers=[];process=None
run_id=options.run_id
logpath=root/f"results/pure_network/agent_wow_bridge_{run_id}.log"
checkpoint_path=root/f"results/pure_network/agent_wow_bridge_{run_id}.checkpoint.packets.json"
if (root/f"results/pure_network/agent_wow_bridge_{run_id}.json").exists():raise SystemExit('Run ID already archived; choose a new one')

def rpc(method,params):
    req=urllib.request.Request("http://127.0.0.1:18086/rpc",data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=35) as response:return json.load(response)

def drain():
    global cursor,incremental,incremental_cursor
    for _ in range(32):
        body=rpc('bridge.poll',{'after':str(cursor),'limit':128})['result']
        if body.get('gap') or body['sessionId']!=session_id:raise RuntimeError('capture gap/session mismatch')
        for p in body.get('packets',[]):
            seq=int(p['sequence'])
            if seq!=cursor+1:raise RuntimeError('sequence gap')
            packets.append({'sequence':seq,'monotonic_ms':p.get('monotonicMs',0),'opcode':p['opcode'],'payload_base64':p['payload']});cursor=seq
        if cursor>=int(body.get('lastSequence',0)):break
    if incremental is None:incremental=IncrementalWorld(session_id,'9')
    if incremental.session_id!=session_id:raise RuntimeError('incremental session mismatch')
    decoded=incremental.apply(packets[incremental_cursor:])
    incremental_cursor=len(packets)
    if decoded['errors']:raise RuntimeError('world state decode error: '+str(decoded['errors']))
    checkpoint_path.write_text(json.dumps({'schema':1,'session_id':session_id,'self_guid':'9','capture_complete':False,'scope':'live checkpoint; prefix only','packets':packets}))
    return decoded

def follow_chunks(points, origin):
    chunks=[];chunk=[];length=0;previous=origin[:3];reply=None
    for point in points:
        xyz=[point[k] for k in ('x','y','z')]
        distance=math.dist(previous,xyz)
        if chunk and length+distance>30:
            reply=rpc('bridge.follow_path',{'points':chunk})
            chunks.append(reply)
            if 'error' in reply:return reply
            drain();chunk=[];length=0
        chunk.append(point);length+=distance;previous=xyz
    if chunk:
        reply=rpc('bridge.follow_path',{'points':chunk});chunks.append(reply)
    result.setdefault('path_chunks',[]).extend(chunks)
    return reply

try:
    for port in (3724,8085):
        s=Server(("127.0.0.1",port),Relay);threading.Thread(target=s.serve_forever,daemon=True).start();servers.append(s)
    with logpath.open("w") as log:
        process=subprocess.Popen([str(work/"agent-wow-cli"),"--config",str(folder/"config.yaml"),"char","play","9"],stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline and process.poll() is None:
            if "world session ready" in logpath.read_text():break
            time.sleep(.2)
        if "world session ready" not in logpath.read_text():raise RuntimeError("bridge session not ready")
        for _ in range(12):
            page=rpc("bridge.poll",{"after":str(cursor),"limit":4})
            if "error" in page:raise RuntimeError(str(page["error"]))
            body=page["result"]
            if body.get("gap"):raise RuntimeError("bridge history gap")
            if session_id and session_id!=body["sessionId"]:raise RuntimeError("session changed")
            session_id=body["sessionId"]
            for p in body.get("packets",[]):
                seq=int(p["sequence"])
                if seq!=cursor+1:raise RuntimeError("sequence gap")
                packets.append({"sequence":seq,"monotonic_ms":p.get("monotonicMs",0),"opcode":p["opcode"],"payload_base64":p["payload"]});cursor=seq
            result["operations"].append({"cursor":cursor,"last_sequence":body.get("lastSequence"),"page_count":len(body.get("packets",[]))})
            time.sleep(.25)
        result["future_cursor_rejection"]=rpc("bridge.poll",{"after":"18446744073709551615"})
        result["oversize_page_rejection"]=rpc("bridge.poll",{"limit":129})
        state=replay({"session_id":session_id,"self_guid":"9","capture_complete":True,"packets":packets})
        if state["status"]!="PASS":raise RuntimeError("initial object decode incomplete")
        result['initial_self']=state['self']
        if (state['self']['health']==0 or (state['self']['ghost'] and not options.reclaim_corpse)) and (options.approach_entry or options.path_file or options.attack_guid or options.attack_near or options.settle_combat):
            result['blocked_action']='character dead at login; resurrection workflow required'
            result['logout']=rpc('session.logout',{})
            result['returncode']=process.wait(timeout=40)
            raise RuntimeError(result['blocked_action'])
        candidates={o["entry"]:o["guid"] for o in state["visible_objects"] if o.get("type_id")==3 and o["entry"]}
        result["queries"]=[]
        for entry,guid in list(candidates.items())[:5]:
            result["queries"].append({"entry":entry,"guid":guid,"response":rpc("bridge.query_creature",{"entry":entry,"guid":guid})})
        result["invalid_query_rejection"]=rpc("bridge.query_creature",{"entry":0,"guid":"0"})
        # A missing template returns a four-byte negative reply, not a name.
        result["missing_template_request"]=rpc("bridge.query_creature",{"entry":2147483647,"guid":"9"})
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            page=rpc("bridge.poll",{"after":str(cursor),"limit":64})["result"]
            if page.get("gap"):raise RuntimeError("query stream gap")
            for p in page.get("packets",[]):
                seq=int(p["sequence"])
                if seq!=cursor+1:raise RuntimeError("query sequence gap")
                packets.append({"sequence":seq,"monotonic_ms":p.get("monotonicMs",0),"opcode":p["opcode"],"payload_base64":p["payload"]});cursor=seq
            state=replay({"session_id":session_id,"self_guid":"9","capture_complete":True,"packets":packets})
            if len(state["creature_templates"])>=len(result["queries"])+1:break
            time.sleep(.1)
        result["creature_templates"]=state["creature_templates"]
        if state["status"]!="PASS" or len(state["creature_templates"])!=len(result["queries"])+1:raise RuntimeError("query replies incomplete")
        for q in result["queries"]:
            if not state["creature_templates"].get(str(q["entry"]),{}).get("found"):raise RuntimeError("visible template lookup failed")
        if state["creature_templates"]["2147483647"]["found"]:raise RuntimeError("negative template unexpectedly exists")
        result["login_position"]=state["login"]
        sample_wall=time.monotonic()
        sample_ms=packets[-1]['monotonic_ms'] if packets else 0
        stream_now=lambda:sample_ms+(time.monotonic()-sample_wall)*1000
        if options.controlled_combat:
            until=time.monotonic()+30
            while (state['self']['health']<60 or state['self']['in_combat'] is not False) and time.monotonic()<until:
                time.sleep(.5);state=drain()
            if state['self']['ghost'] or state['self']['health']<60 or state['self']['in_combat'] is not False:raise RuntimeError('not recovered for controlled combat')
            start=state['login']['position_xyzo']
            choices=[o for o in state['visible_objects'] if o.get('type_id')==3 and o['entry']==options.combat_entry and o['health']>0 and o['level']<=options.max_target_level]
            if not choices:raise RuntimeError('no suitable visible quest target')
            target=min(choices,key=lambda o:math.dist(start[:3],estimated_position(o,stream_now())[0]))
            destination,source=estimated_position(target,stream_now())
            result['target_candidate']={'guid':target['guid'],'entry':options.combat_entry,'health':target['health'],'position_source':source}
            nav=subprocess.run([str(root/'.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,start[:3]),*map(str,destination)],capture_output=True,text=True,timeout=10)
            if nav.returncode:raise RuntimeError('controlled approach path unavailable')
            route=json.loads(nav.stdout);points=[];previous=start[:3]
            for end in route['waypoints']:
                distance=math.dist(previous,end)
                if distance<.02:continue
                n=max(1,math.ceil(distance/3),math.ceil(abs(end[2]-previous[2])/2));origin=previous
                for i in range(1,n+1):
                    p=[origin[j]+(end[j]-origin[j])*i/n for j in range(3)];points.append(dict(zip(('x','y','z'),p)))
                previous=end
            current=start
            retreat_points=list(points)
            if points:
                result['controlled_path']=follow_chunks(points,start)
                if 'error' in result['controlled_path']:raise RuntimeError('controlled path rejected')
                current=result['controlled_path']['result']['submittedPosition']
            result['approach_refreshes']=[]
            for refresh in range(3):
                updated=drain()
                live=next((o for o in updated['visible_objects'] if o['guid']==target['guid'] and o.get('health',0)>0),None)
                if live is None:raise RuntimeError('target disappeared before start')
                tp,source=estimated_position(live,stream_now())
                if math.dist(current[:3],tp)<=4:break
                nav=subprocess.run([str(root/'.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,current[:3]),*map(str,tp)],capture_output=True,text=True,timeout=10)
                if nav.returncode:raise RuntimeError('refresh path unavailable')
                route=json.loads(nav.stdout);points=[];previous=current[:3]
                for end in route['waypoints']:
                    distance=math.dist(previous,end)
                    if distance<.02:continue
                    n=max(1,math.ceil(distance/3),math.ceil(abs(end[2]-previous[2])/2));origin=previous
                    for i in range(1,n+1):points.append(dict(zip(('x','y','z'),[origin[j]+(end[j]-origin[j])*i/n for j in range(3)])))
                    previous=end
                if not points:break
                # Quest prey walks at 2.5; approach at observed base run speed.
                response=rpc('bridge.follow_path',{'points':points,'run':True})
                retreat_points.extend(points)
                result['approach_refreshes'].append({'position_source':source,'response':response})
                if 'error' in response:raise RuntimeError('refresh path rejected')
                current=response['result']['submittedPosition']
            result['controlled_combat'],observed=fight(rpc,drain,target['guid'],current,stream_now,budget=options.combat_budget)
            if result['controlled_combat']['outcome']!='target_dead' and observed['self']['in_combat'] is True:
                result['retreat'],observed=retreat(rpc,drain,retreat_points,start)
                if result['retreat']['outcome']=='still_in_combat' and observed['self']['health']>=25:
                    # Returning to the origin is not sufficient to reset a leash.
                    # Continue observing/defending in this same connection.
                    last=result['retreat']['movement'].get('result',{}).get('submittedPosition',start)
                    result['defensive_finish'],observed=fight(rpc,drain,target['guid'],last,stream_now,budget=45)
            until=time.monotonic()+8
            while observed['self']['in_combat'] is not False and time.monotonic()<until:
                time.sleep(.2);observed=drain()
            result['controlled_final_self']=observed['self']
            result['post_action_state']=observed
            if options.loot and result['controlled_combat']['outcome']=='target_dead':
                result['open_loot']=rpc('bridge.open_loot',{'guid':target['guid']})
                deadline=time.monotonic()+5;loot=None
                while time.monotonic()<deadline:
                    observed=drain()
                    replies=[e['loot'] for e in observed['event_log'] if e.get('opcode')==0x160 and e['loot']['guid']==target['guid']]
                    if replies:loot=replies[-1];break
                    time.sleep(.1)
                if loot is None:raise RuntimeError('controlled loot response missing')
                result['loot_response']=loot;result['loot_takes']=[]
                for item in loot.get('items',[]):
                    result['loot_takes'].append({'item':item,'response':rpc('bridge.take_loot',{'slot':item['slot']})})
                    time.sleep(.2);observed=drain()
                result['release_loot']=rpc('bridge.release_loot',{'guid':target['guid']})
                time.sleep(.2);observed=drain()
                result['loot_events']=[e for e in observed['event_log'] if e.get('kind')=='loot']
                result['post_action_state']=observed
        if options.approach_entry:
            start=state['login']['position_xyzo'][:3]
            choices=[o for o in state['visible_objects'] if o.get('type_id')==3 and o['entry']==options.approach_entry and o.get('health',0)>0]
            if not choices:raise RuntimeError('requested live creature entry not visible')
            target=min(choices,key=lambda o:math.dist(start,o['movement']['position_xyzo'][:3]))
            destination=target.get('server_motion',{}).get('start_xyz',target['movement']['position_xyzo'][:3])
            result['target_candidate']={k:target[k] for k in ('guid','entry','health','level')}
            result['target_candidate']['position_source']=destination
            if math.dist(start,destination)>3:
                nav=subprocess.run([str(root/'.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,start),*map(str,destination)],capture_output=True,text=True,timeout=10)
                if nav.returncode:raise RuntimeError('approach nav failed: '+nav.stderr)
                route=json.loads(nav.stdout);result['approach_detour']=route
                points=[];previous=start;total=0
                for end in route['waypoints']:
                    d=math.dist(previous,end)
                    if d<.01:continue
                    n=max(1,math.ceil(d/4));origin=previous
                    for i in range(1,n+1):
                        p=[origin[j]+(end[j]-origin[j])*i/n for j in range(3)]
                        length=math.dist(previous,p)
                        if total+length>35:break
                        points.append(dict(zip(('x','y','z'),p)));total+=length;previous=p
                    if total+length>35:break
                if not points:raise RuntimeError('no bounded approach segment')
                result['approach_request']={'points':points}
                result['approach_response']=rpc('bridge.follow_path',{'points':points})
                if 'error' in result['approach_response']:raise RuntimeError('approach submission rejected')
                if options.attack_near:
                    time.sleep(.5)
                    page=rpc('bridge.poll',{'after':str(cursor),'limit':128})['result']
                    for pp in page.get('packets',[]):
                        seq=int(pp['sequence']);packets.append({'sequence':seq,'monotonic_ms':pp.get('monotonicMs',0),'opcode':pp['opcode'],'payload_base64':pp['payload']});cursor=seq
                    fresh=replay({'session_id':session_id,'self_guid':'9','capture_complete':True,'packets':packets})
                    selfp=fresh['self']['movement']['position_xyzo']; live=[o for o in fresh['visible_objects'] if o.get('guid')==target['guid']]
                    if live:
                        tp=live[0]['movement']['position_xyzo'];angle=math.atan2(tp[1]-selfp[1],tp[0]-selfp[0]);result['face_before_attack']=rpc('bridge.face',{'angle':angle})
                        time.sleep(.2)
                    result['attack']=rpc('bridge.attack_once',{'guid':target['guid']})
                    result['explicit_stop_after_attack']=rpc('bridge.stop_attack',{})
                    time.sleep(2)
            if 'attack' in result and 'error' in result['attack']:
                result['attack_stop_after_error']=rpc('bridge.stop_attack',{})
                time.sleep(2)
            elif options.attack_near and 'attack' not in result:
                angle=math.atan2(destination[1]-start[1],destination[0]-start[0])
                result['face_before_attack']=rpc('bridge.face',{'angle':angle})
                result['attack']=rpc('bridge.attack_once',{'guid':target['guid']})
            options.path_file=True
        if options.path_file:
            if options.path_file is not True:
                points=json.loads(options.path_file.read_text())["points"]
                result["invalid_path_rejection"]=rpc("bridge.follow_path",{"points":[{"x":999999,"y":0,"z":0}]})
                result["path_request"]={"points":points}
                result["path_response"]=rpc("bridge.follow_path",{"points":points,"run":options.run_path})
                if "error" in result["path_response"]:raise RuntimeError("path submission failed")
            time.sleep(.5)
        if options.attack_guid:
            result["attack"]=rpc("bridge.attack_once",{"guid":options.attack_guid})
            # Keep reply even on timeout; collect actual combat packets for diagnosis.
            time.sleep(.2)
            result["explicit_stop_after_attack"]=rpc("bridge.stop_attack",{})
            time.sleep(2)
        if options.stop_attack:
            result["stop_attack"]=rpc("bridge.stop_attack",{})
            time.sleep(.5)
        if options.path_file or options.attack_guid:
            for _ in range(5):
                page=rpc("bridge.poll",{"after":str(cursor),"limit":128})["result"]
                if page.get("gap"):raise RuntimeError("post-action capture gap")
                for p in page.get("packets",[]):
                    seq=int(p["sequence"])
                    if seq!=cursor+1:raise RuntimeError("post-action sequence gap")
                    packets.append({"sequence":seq,"monotonic_ms":p.get("monotonicMs",0),"opcode":p["opcode"],"payload_base64":p["payload"]});cursor=seq
                if cursor>=int(page.get("lastSequence",0)):break
            result["post_action_state"]=replay({"session_id":session_id,"self_guid":"9","capture_complete":True,"packets":packets})
        if options.step:
            result["invalid_step"]=rpc("bridge.step",{"distance":2})
            result["step"]=rpc("bridge.step",{"distance":0.5})
            if "error" in result["step"]:raise RuntimeError("step failed")
            result["repeat_step_rejection"]=rpc("bridge.step",{"distance":0.5})
            time.sleep(1)
        if options.settle_combat:
            observed=drain()
            combat_target=result.get('target_candidate',{}).get('guid',options.attack_guid)
            result['combat_phases']=[]
            for attempt in range(6):
                targets=[o for o in observed['visible_objects'] if o['guid']==combat_target]
                self_state=observed['self']
                row={'attempt':attempt,'health':self_state['health'],'self_in_combat':self_state['in_combat'],'target_health':targets[0]['health'] if targets else None,'sequence':cursor}
                result['combat_phases'].append(row)
                if self_state['in_combat'] is False and attempt>0:break
                if self_state['health']<25:row['halt']='self health budget';break
                if not targets or targets[0]['health']==0:
                    time.sleep(1);observed=drain();continue
                sp=result.get('approach_response',{}).get('result',{}).get('submittedPosition',observed['login']['position_xyzo'])
                motion=targets[0].get('server_motion',{})
                tp=motion.get('path_xyz',[targets[0]['movement']['position_xyzo'][:3]])[-1]
                row['facing_source']='motion endpoint estimate, not observed arrival'
                row['face']=rpc('bridge.face',{'angle':math.atan2(tp[1]-sp[1],tp[0]-sp[0])})
                row['action']=rpc('bridge.attack_once',{'guid':combat_target})
                row['stop']=rpc('bridge.stop_attack',{})
                time.sleep(.4)
                observed=drain()
                if 'error' in row['action']:row['halt']='attack outcome requires investigation';break
            result['stop_before_combat_wait']=rpc('bridge.stop_attack',{})
            wait_deadline=time.monotonic()+12
            while time.monotonic()<wait_deadline:
                observed=drain()
                if observed['self']['in_combat'] is False:break
                time.sleep(.5)
            result['final_combat_state']={'self':observed['self'],'target':[o for o in observed['visible_objects'] if o['guid']==combat_target]}
            result['post_action_state']=observed
            if options.loot:
                corpses=[o for o in observed['visible_objects'] if o['guid']==combat_target and o.get('health')==0]
                if not corpses:raise RuntimeError('no confirmed killed target for loot')
                result['open_loot']=rpc('bridge.open_loot',{'guid':combat_target})
                until=time.monotonic()+5;loot=None
                while time.monotonic()<until:
                    observed=drain()
                    replies=[e['loot'] for e in observed['event_log'] if e.get('opcode')==0x160 and e['loot']['guid']==combat_target]
                    if replies:loot=replies[-1];break
                    time.sleep(.1)
                if loot is None:raise RuntimeError('loot reply missing')
                result['loot_response']=loot
                result['loot_takes']=[]
                for item in loot.get('items',[]):
                    result['loot_takes'].append({'item':item,'response':rpc('bridge.take_loot',{'slot':item['slot']})})
                    time.sleep(.2)
                    observed=drain()
                result['loot_events']=[e for e in observed['event_log'] if e.get('kind')=='loot']
                result['release_loot']=rpc('bridge.release_loot',{'guid':combat_target})
                time.sleep(.2);observed=drain()
                result['loot_events']=[e for e in observed['event_log'] if e.get('kind')=='loot']
                result['post_action_state']=observed
        if options.quest_entry:
            observed=drain()
            npcs=[o for o in observed['visible_objects'] if o.get('type_id')==3 and o['entry']==options.quest_entry]
            if len(npcs)!=1:raise RuntimeError('questgiver not uniquely visible')
            npc=npcs[0];result['questgiver']={k:npc[k] for k in ('guid','entry')}
            result['questgiver']['position']=npc['movement']['position_xyzo']
            result['quest_log_before']=observed['self']['quest_log']
            result['quest_hello']=rpc('bridge.quest_hello',{'guid':npc['guid']})
            if options.query_quest:
                result['query_quest']=rpc('bridge.query_quest',{'guid':npc['guid'],'quest':options.query_quest})
            time.sleep(.5);observed=drain()
            result['quest_events']=[e for e in observed['event_log'] if e.get('kind')=='quest']
            if options.reward_quest:
                if not any(q['quest']==options.reward_quest for q in observed['self']['quest_log']):raise RuntimeError('reward quest not active; do not resubmit')
                result['complete_quest']=rpc('bridge.complete_quest',{'guid':npc['guid'],'quest':options.reward_quest})
                time.sleep(.3);observed=drain()
                result['reward_quest']=rpc('bridge.reward_quest',{'guid':npc['guid'],'quest':options.reward_quest})
                time.sleep(.5);observed=drain()
                result['quest_log_after']=observed['self']['quest_log']
                result['quest_reward_events']=[e for e in observed['event_log'] if e.get('kind')=='quest_response']
            if options.accept_quest:
                if any(q['quest']==options.accept_quest for q in observed['self']['quest_log']):raise RuntimeError('quest already accepted; do not repeat')
                result['accept_quest']=rpc('bridge.accept_quest',{'guid':npc['guid'],'quest':options.accept_quest})
                end=time.monotonic()+5
                while time.monotonic()<end:
                    time.sleep(.2);observed=drain()
                    if any(q['quest']==options.accept_quest for q in observed['self']['quest_log']):break
                result['quest_log_after']=observed['self']['quest_log']
            result['post_action_state']=observed
        if options.release_spirit:
            current=drain()
            if current['self']['health']>0 and not current['self']['ghost']:raise RuntimeError('living character: spirit release refused by experiment')
            if not current['self']['ghost']:
                result['release_spirit']=rpc('bridge.release_spirit',{})
                time.sleep(1)
        if options.query_corpse or options.release_spirit or options.reclaim_corpse:
            result['query_corpse']=rpc('bridge.query_corpse',{})
            time.sleep(.5);current=drain()
            result['recovery_events']=[e for e in current['event_log'] if e.get('kind') in ('corpse','recovery')]
            if options.reclaim_corpse:
                result['reclaim_corpse']=rpc('bridge.reclaim_corpse',{'guid':'9'})
                time.sleep(.5);current=drain()
            result['recovery_self']=current['self']
        if options.spirit_healer:
            current=drain()
            if not current['self']['ghost']:raise RuntimeError('spirit healer experiment requires ghost')
            healers=[o for o in current['visible_objects'] if o.get('type_id')==3 and o['entry']==6491]
            if len(healers)!=1:raise RuntimeError('unique nearby spirit healer not found')
            healer=healers[0]
            distance=math.dist(current['login']['position_xyzo'][:3],healer['movement']['position_xyzo'][:3])
            if distance>6:raise RuntimeError('healer interaction distance not verified')
            result['spirit_healer_request']={'guid':healer['guid'],'entry':healer['entry'],'distance':distance,'response':rpc('bridge.spirit_healer',{'guid':healer['guid']})}
            time.sleep(.3)
        if options.observe_seconds:
            if not 0<options.observe_seconds<=60:raise RuntimeError('observation budget must be 0..60 seconds')
            result['observation_samples']=[]
            until=time.monotonic()+options.observe_seconds
            while time.monotonic()<until:
                snapshot=drain()['self']
                result['observation_samples'].append({k:snapshot[k] for k in ('health','max_health','ghost','in_combat')})
                time.sleep(.5)
        final_state=drain()
        if final_state['self']['in_combat'] is True:
            submitted=result.get('retreat',{}).get('movement',{}).get('result',{}).get('submittedPosition',result.get('controlled_path',{}).get('result',{}).get('submittedPosition',final_state['login']['position_xyzo']))
            result['session_supervisor'],final_state=settle(rpc,drain,submitted,stream_now)
        result['final_quest_log']=final_state['self']['quest_log']
        result['final_self']=final_state['self']
        result['owned_item_totals']={}
        for obj in final_state['visible_objects']:
            if obj.get('type_id') in (1,2) and obj.get('owner_guid')=='9':
                key=str(obj['entry']);result['owned_item_totals'][key]=result['owned_item_totals'].get(key,0)+obj['stack_count']
        result['quest_progress_events']=[e for e in final_state['event_log'] if e.get('kind') in ('quest_kill','quest_objectives_complete')]
        result["logout"]=rpc("session.logout",{})
        if 'error' in result['logout']:
            result['exit_phase']='server rejected logout; archive evidence then close connection explicitly'
            process.terminate()
        else:
            result["returncode"]=process.wait(timeout=40)
            # Observe final packet callbacks before containers disappear if available;
            # polled trace is a prefix, session completion is separately recorded.
        result["status"]="PASS" if result.get("returncode")==0 and result["logout"].get("result",{}).get("status")=="closed" and packets and "error" in result["future_cursor_rejection"] and "error" in result["oversize_page_rejection"] else "INCOMPLETE"
except Exception as exc:result["error"]=f"{type(exc).__name__}: {exc}"
finally:
    if process and process.poll() is None:process.terminate();process.wait(timeout=40)
    for s in servers:s.shutdown();s.server_close()
    trace={"schema":1,"session_id":session_id,"self_guid":"9","capture_complete":result["status"]=="PASS","scope":"bridge packets polled before logout; not entire session","packets":packets}
    (root/f"results/pure_network/agent_wow_bridge_{run_id}.packets.json").write_text(json.dumps(trace,indent=2)+"\n")
    (root/f"results/pure_network/agent_wow_bridge_{run_id}.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k not in ('post_action_state','creature_templates','queries','operations','controlled_combat')},indent=2))
