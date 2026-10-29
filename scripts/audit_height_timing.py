"""Measure recorded movement RPC time separately from login-ready span."""
import json
import re
from datetime import datetime
from pathlib import Path

folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
rows = []
for i in range(1, 23):
    trial = json.loads((folder / f'height_H001_segment{i}_trial.json').read_bytes())
    plan = json.loads((folder / f'height_H001_segment{i}_plan.json').read_bytes())
    rows.append({'segment': i, 'length': plan['length'],
                 'movement_seconds': trial['movement']['result']['elapsedMs'] / 1000})

def ready(number, suffix=''):
    text = (folder / f'agent_wow_bridge_height_H001_segment{number}{suffix}.log').read_text()
    return datetime.fromisoformat(re.search(r'time=(\S+)', text)[1])

result = {'segments': rows, 'total_length': sum(r['length'] for r in rows),
          'movement_rpc_seconds': sum(r['movement_seconds'] for r in rows),
          'segment5_to22_movement_seconds': sum(r['movement_seconds'] for r in rows[4:]),
          'segment5_ready_to22_verify_ready_seconds': (ready(22, '_verify')-ready(5)).total_seconds(),
          'segment1_ready_to22_verify_ready_seconds': (ready(22, '_verify')-ready(1)).total_seconds(),
          'scope': 'ready spans exclude initial bootstrap and final observation/logout; long span includes pauses'}
with (folder / 'height_H001_timing.json').open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps({k: v for k, v in result.items() if k != 'segments'}, indent=2))
