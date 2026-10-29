"""Replay the failed long-test session without issuing game requests."""
import json
from pathlib import Path
from agent_wow_worldstate import replay

folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
trace = json.loads((folder / 'agent_wow_bridge_quest792_long01_unit1_verify.packets.json').read_bytes())
state = replay(trace)
events = state['event_log']
result = {'errors': state['errors'], 'self': state['self'],
          'attack_stops': [e for e in events if e.get('kind') == 'attack_stop'],
          'combat_events': [e for e in events if e.get('kind') == 'attacker_state'],
          'quest_events': [e for e in events if e.get('kind') in ('quest', 'quest_response')],
          'scope': 'offline replay after nullable AttackStop fix; no new side effects'}
with (folder / 'quest792_long01_replay_audit.json').open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps({'errors': result['errors'], 'attack_stops': result['attack_stops'],
                  'damage_event_count': len(result['combat_events']),
                  'health': state['self'].get('health'), 'quest_log': state['self'].get('quest_log')}, indent=2))
