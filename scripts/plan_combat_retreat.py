"""Offline escape route back to the previously observed staging position."""
import json
import math
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / 'results/pure_network'
start = json.loads((folder / 'agent_wow_readonly_quest792_recovery_check04.json').read_bytes())['observation']
destination = json.loads((folder / 'agent_wow_readonly_height_Q792_segment15_verify.json').read_bytes())['observation']
run = subprocess.run([str(root / '.cache/nav-height-build/agent_wow_nav.exe'),
    r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps', '1',
    *map(str, start['position']), *map(str, destination['position'])],
    capture_output=True, text=True, timeout=30)
if run.returncode:
    failure = {'status': 'rejected', 'start': start['position'], 'end': destination['position'],
               'stderr': run.stderr, 'returncode': run.returncode, 'game_actions': False}
    with (folder / 'quest792_retreat_plan01_rejected.json').open('x', encoding='utf-8') as handle:
        json.dump(failure, handle, indent=2)
    print(json.dumps(failure))
    raise SystemExit(1)
nav = json.loads(run.stdout)
points = nav['surface_samples']
chunks, chunk, length = [], [points[0]], 0
for point in points[1:]:
    distance = math.dist(chunk[-1], point)
    if length + distance > 20:
        chunks.append(chunk)
        chunk, length = [chunk[-1]], 0
    chunk.append(point)
    length += distance
if len(chunk) > 1:
    chunks.append(chunk)
result = {'character': '9', 'map': 1, 'start': start['position'], 'end': destination['position'],
          'chunks': chunks, 'nav': nav, 'scope': 'offline corridor; staging point is historically safe, not guaranteed safe now',
          'path_length': sum(math.dist(a,b) for a,b in zip(points,points[1:]))}
with (folder / 'quest792_retreat_plan01.json').open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps({'length': result['path_length'], 'chunks': len(chunks), 'start': result['start'], 'end': result['end']}))
