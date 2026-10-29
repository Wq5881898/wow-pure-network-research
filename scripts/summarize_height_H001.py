"""Summarize measured segment evidence, including original first segment."""
import json
import math
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1] / 'results/pure_network'
rows = []
for number in range(1, 23):
    name = f'height_H001_segment{number}'
    plan = json.loads((FOLDER / f'{name}_plan.json').read_bytes())
    trial = json.loads((FOLDER / f'{name}_trial.json').read_bytes())
    verify = json.loads((FOLDER / f'agent_wow_readonly_{name}_verify.json').read_bytes())
    before, final = trial['before'], verify['observation']
    error = math.dist(plan['points'][-1], final['position'])
    valid = (not trial.get('error') and before['session_id'] != final['session_id']
             and before['character'] == final['character'] == '9' and before['map_id'] == final['map_id']
             and error <= .1 and final['health'] == before['health'] == 108
             and final['ghost'] is False and final['in_combat'] is False
             and trial.get('logout', {}).get('status') == 'closed'
             and verify.get('status') == 'observed_and_closed')
    rows.append({'segment': number, 'length': plan['length'], 'landing_error': error,
                 'confirmed': valid, 'start': before['position'], 'end': final['position']})
result = {'segments': rows, 'all_confirmed': all(r['confirmed'] for r in rows),
          'total_path_length': sum(r['length'] for r in rows),
          'net_height_change': rows[-1]['end'][2]-rows[0]['start'][2],
          'maximum_landing_error': max(r['landing_error'] for r in rows),
          'scope': '22 segment endpoints and final health; not continuous collision validation'}
with (FOLDER / 'height_H001_full_summary.json').open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps({k: v for k, v in result.items() if k != 'segments'}, indent=2))
raise SystemExit(not result['all_confirmed'])
