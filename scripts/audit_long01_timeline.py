"""Direction-scoped damage and HP timeline from the failed live capture."""
import json
from pathlib import Path
from agent_wow_worldstate import IncrementalWorld

folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
trace = json.loads((folder / 'agent_wow_bridge_quest792_long01_unit1_verify.packets.json').read_bytes())
target = '17379391014048896448'
world = IncrementalWorld(trace['session_id'], '9')
rows, previous = [], None
for packet in trace['packets']:
    state = world.apply([packet])
    me = state.get('self') or {}
    enemy = next((o for o in state['visible_objects'] if o['guid'] == target), {})
    sample = {'self_hp': me.get('health'), 'enemy_hp': enemy.get('health'),
              'combat': me.get('in_combat')}
    if sample != previous:
        rows.append({'sequence': packet['sequence'], 'ms': packet['monotonic_ms'], **sample})
        previous = sample
events = [e['attack'] for e in state['event_log'] if e.get('kind') == 'attacker_state']
outgoing = [e for e in events if e['attacker_guid'] == '9' and e['target_guid'] == target]
incoming = [e for e in events if e['attacker_guid'] == target and e['target_guid'] == '9']
result = {'timeline': rows, 'outgoing': outgoing, 'incoming': incoming,
          'outgoing_full_damage': sum(e['full_damage'] for e in outgoing),
          'incoming_full_damage': sum(e['full_damage'] for e in incoming),
          'errors': state['errors'], 'capture_complete': trace.get('capture_complete')}
with (folder / 'quest792_long01_damage_timeline.json').open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps({k: v for k, v in result.items() if k not in ('outgoing', 'incoming')}, indent=2))
