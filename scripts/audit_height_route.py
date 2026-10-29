"""Offline height-route case capture; never sends game movement."""
import json
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'results/pure_network/agent_wow_readonly_quest792_visibility_20261029_01.json'
    observation = json.loads(source.read_bytes())
    start = observation['observation']['position']
    target = next(o for o in observation['visible_candidates'] if o['entry'] == 3145)
    end = target['movement']['position_xyzo'][:3]
    exe = ROOT / '.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'
    mmaps = r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps'
    run = subprocess.run([str(exe), mmaps, '1', *map(str, start), *map(str, end)],
                         capture_output=True, text=True, timeout=30, check=True)
    nav = json.loads(run.stdout)
    points = nav['waypoints']
    segments = []
    for a, b in zip(points, points[1:]):
        horizontal = math.hypot(b[0]-a[0], b[1]-a[1])
        segments.append({'length': math.dist(a, b), 'horizontal': horizontal, 'dz': b[2]-a[2],
                         'grade': abs(b[2]-a[2])/horizontal if horizontal else None})
    length = sum(s['length'] for s in segments)
    result = {'case': 'H001_plateau_to_zureetha', 'scope': 'offline mesh route, NOT live traversal',
              'source_observation': source.relative_to(ROOT).as_posix(), 'start': start, 'end': end,
              'metrics': {'height_difference': end[2]-start[2], 'direct_distance': math.dist(start, end),
                          'route_length': length, 'detour_ratio': length/math.dist(start, end),
                          'start_projection': math.dist(start, points[0]),
                          'end_projection': math.dist(end, points[-1]),
                          'ascent': sum(max(s['dz'], 0) for s in segments),
                          'descent': sum(max(-s['dz'], 0) for s in segments)},
              'segments': segments, 'nav': nav,
              'limitations': ['corner projection is not dense surface validation',
                              'mesh connectivity does not prove server movement acceptance',
                              'short high-grade segments require review before execution']}
    output = ROOT / 'results/pure_network/height_route_H001_20261029.json'
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result['metrics'], indent=2))


if __name__ == '__main__':
    main()
