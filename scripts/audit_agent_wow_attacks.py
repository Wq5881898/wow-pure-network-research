"""Audit every saved attack trace; do not infer outcomes from RPC/session status."""
import base64
import json
from pathlib import Path
from agent_wow_worldstate import attacker_state, replay

root=Path(__file__).resolve().parents[1]
rows=[]
for path in sorted((root/'results/pure_network').glob('agent_wow_bridge_run*.packets.json')):
    trace=json.loads(path.read_text())
    hits=[];errors=[]
    for p in trace['packets']:
        if p['opcode']!=0x14a:continue
        try:hits.append({'sequence':p['sequence'],**attacker_state(base64.b64decode(p['payload_base64']))})
        except ValueError as e:errors.append({'sequence':p['sequence'],'error':str(e)})
    if not hits and not errors:continue
    state=replay(trace)
    target_guids={h['target_guid'] for h in hits if h['attacker_guid']=='9'}
    changes=[{'sequence':e['sequence'],'guid':e['guid'],'kind':e['kind'],'health':e['fields']['24']} for e in state['event_log'] if e.get('guid') in target_guids and '24' in e.get('fields',{})]
    rows.append({'capture':path.name,'session_capture_complete':trace.get('capture_complete'),'hits':hits,'outgoing_damage':sum(h['full_damage'] for h in hits if h['attacker_guid']=='9'),'target_health_events':changes,'decode_errors':errors,'replay_errors':state['errors']})
result={'scope':'retrospective raw combat packet audit; no new gameplay actions','runs':rows}
(root/'results/pure_network/agent_wow_attack_audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
