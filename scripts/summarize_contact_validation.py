"""Bounded offline final audit of the unique dt run."""
import json
from pathlib import Path
from agent_wow_worldstate import IncrementalWorld
from agent_wow_attackers import active_attackers

folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
capture = json.loads((folder/'agent_wow_bridge_contact_fixed_20261110_dt.packets.json').read_text())
world = IncrementalWorld(capture['session_id'], capture['self_guid'])
world.max_packets = 100000
events = []
minimum = 9999
for p in capture['packets']:
    state = world.apply([p])
    if state.get('self'):
        minimum = min(minimum, state['self'].get('health',minimum))
    for e in state['event_log']:
        if e.get('sequence') == p['sequence'] and e.get('kind') in ('attack_start','attacker_state','attack_stop'):
            events.append({'ms':p['monotonic_ms'], **e,
                           'attackers':[{'guid':a['guid'],'entry':a.get('entry'),'level':a.get('level')}
                                        for a in active_attackers(state,capture['self_guid'])]})
report = json.loads((folder/'efficiency_contact_fixed_20261110_dt.json').read_text())
summary = {'packet_count':len(capture['packets']), 'decoder':'IncrementalWorld batch=1 max_packets=100000',
           'errors':state['errors'], 'login':state['login'], 'final':state['self'],
           'minimum_health_from_packets':minimum, 'attack_events':events,
           'status':report['status'],'error':report.get('error'), 'kills':report['kills'],
           'return':report.get('return'), 'return_error':report.get('return_error'),
           'ending':report.get('session_ending')}
output = folder/'dt_contact_validation_audit.json'
with output.open('x') as f:
    json.dump(summary,f,indent=2)
print(json.dumps({k:v for k,v in summary.items() if k not in ('final','return','ending')},indent=2))
print(json.dumps({'final_hp':state['self']['health'],'final_combat':state['self']['in_combat'],
                  'ending':report.get('session_ending')},indent=2))
