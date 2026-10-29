"""Compare bounded retreat destinations without weakening corridor checks."""
import json
import subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[1]
folder = root / 'results/pure_network'
start = json.loads((folder/'agent_wow_readonly_quest792_recovery_check04.json').read_bytes())['observation']['position']
rows = []
for segment in (14, 13, 12, 11):
    end = json.loads((folder/f'agent_wow_readonly_height_Q792_segment{segment}_verify.json').read_bytes())['observation']['position']
    run = subprocess.run([str(root/'.cache/nav-height-build/agent_wow_nav.exe'),
        r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps', '1',
        *map(str,start), *map(str,end)], capture_output=True, text=True, timeout=30)
    row = {'destination_segment': segment, 'end': end, 'returncode': run.returncode,
           'error': run.stderr.strip()}
    if run.returncode == 0:
        row['nav'] = json.loads(run.stdout)
    rows.append(row)
with (folder/'quest792_retreat_alternatives01.json').open('x',encoding='utf-8') as handle:
    json.dump({'start': start, 'candidates':rows,'game_actions':False},handle,indent=2)
print(json.dumps([{k:v for k,v in r.items() if k!='nav'} for r in rows],indent=2))
