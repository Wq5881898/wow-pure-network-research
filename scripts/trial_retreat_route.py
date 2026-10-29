"""Non-combat reverse-route trial; one dispatch then fresh login per chunk."""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def call(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=180)
    if result.returncode:
        raise RuntimeError(result.stdout[-1500:] + result.stderr[-1500:])


def main():
    routes = json.loads((FOLDER / 'retreat_via_staging01.json').read_bytes())
    chunks = []
    for leg, route in enumerate(routes):
        if route['returncode'] != 0:
            raise RuntimeError('unverified offline leg')
        points = route['nav']['surface_samples']
        chunk, length = [points[0]], 0
        for point in points[1:]:
            distance = math.dist(chunk[-1], point)
            if length + distance > 20:
                chunks.append({'points': chunk, 'length': length, 'leg': leg})
                chunk, length = [chunk[-1]], 0
            chunk.append(point)
            length += distance
        if len(chunk) > 1:
            chunks.append({'points': chunk, 'length': length, 'leg': leg})
    with (FOLDER / 'retreat_noncombat_trial01.jsonl').open('x', encoding='utf-8') as journal:
        for i, chunk in enumerate(chunks, 1):
            with (FOLDER / f'height_R792_segment{i}_plan.json').open('x', encoding='utf-8') as handle:
                json.dump({**chunk, 'scope': 'prevalidated staged retreat, non-combat walk',
                           'source': 'retreat_via_staging01.json'}, handle, indent=2)
            call(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--', 'python3',
                  'scripts/height_segment_trial.py', '--case', 'R792', '--segment', str(i), '--execute'])
            call(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--', 'python3',
                  'scripts/read_only_character.py', '--run-id', f'height_R792_segment{i}_verify'])
            call([sys.executable, 'scripts/verify_height_segment.py', '--case', 'R792', '--segment', str(i)])
            report = json.loads((FOLDER / f'height_R792_segment{i}_reconciled.json').read_bytes())
            row = {'segment': i, **report}
            journal.write(json.dumps(row) + '\n')
            journal.flush()
            print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
