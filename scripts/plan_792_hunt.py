"""Rank static spawn routes offline; static spawns are not live target evidence."""
import json
import math
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'
source = json.loads((FOLDER / 'agent_wow_readonly_quest792_accept_verify.json').read_bytes())
start = source['observation']['position']
sql = Path('C:/Azerothcore/azerothcore-wotlk/data/sql/base/db_world/creature.sql')
candidates = []
for line in sql.read_text(encoding='utf-8').splitlines():
    if not re.match(r'^\(\d+,3101,', line):
        continue
    columns = line.lstrip('(').split(',')
    if columns[4] != '1':
        continue
    end = list(map(float, columns[10:13]))
    candidates.append((math.dist(start, end), int(columns[0]), end))
rows = []
for distance, guid, end in sorted(candidates)[:5]:
    run = subprocess.run([str(ROOT / '.cache/nav-height-build/agent_wow_nav.exe'),
        r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps', '1',
        *map(str, start), *map(str, end)], capture_output=True, text=True, timeout=30)
    row = {'static_spawn_id': guid, 'end': end, 'direct_distance': distance,
           'nav_returncode': run.returncode}
    if run.returncode:
        row['error'] = run.stderr.strip()
    else:
        row['nav'] = json.loads(run.stdout)
        points = row['nav']['surface_samples']
        row['path_length'] = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    rows.append(row)
result = {'start': start, 'scope': 'static DB baseline; not online availability or combat authorization', 'candidates': rows}
with (FOLDER / 'quest792_static_hunt_routes.json').open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps([{k: v for k, v in row.items() if k != 'nav'} for row in rows], indent=2))
