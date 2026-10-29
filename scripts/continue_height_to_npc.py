"""Bounded H001 continuation, one fresh login reconciliation per segment."""
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
        raise RuntimeError(f'{command}: {result.stdout[-1500:]} {result.stderr[-1500:]}')


def main():
    journal = FOLDER / 'height_H001_to_npc_continuation.jsonl'
    with journal.open('x', encoding='utf-8') as handle:
        previous = 4
        for number in range(5, 30):
            source = FOLDER / f'agent_wow_readonly_height_H001_segment{previous}_verify.json'
            observation = json.loads(source.read_bytes())
            call([sys.executable, 'scripts/height_segment_trial.py', '--segment', str(number),
                  '--distance', '20', '--from-observation', str(source)])
            call(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--', 'python3',
                  'scripts/height_segment_trial.py', '--segment', str(number), '--execute'])
            call(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--', 'python3',
                  'scripts/read_only_character.py', '--run-id', f'height_H001_segment{number}_verify'])
            call([sys.executable, 'scripts/verify_height_segment.py', '--segment', str(number)])
            verify = json.loads((FOLDER / f'agent_wow_readonly_height_H001_segment{number}_verify.json').read_bytes())
            reconciled = json.loads((FOLDER / f'height_H001_segment{number}_reconciled.json').read_bytes())
            position = verify['observation']['position']
            targets = [o for o in verify.get('visible_candidates', []) if o['entry'] == 3145]
            distance = min((math.dist(position, o['movement']['position_xyzo'][:3]) for o in targets), default=None)
            row = {'segment': number, 'position': position, 'hp': verify['observation']['health'],
                   'path_length': reconciled['path_length'], 'npc_visible': bool(targets),
                   'npc_distance_3d': distance, 'confirmed': reconciled['confirmed']}
            handle.write(json.dumps(row) + '\n')
            handle.flush()
            print(json.dumps(row), flush=True)
            if distance is not None and distance <= 4:
                print('NPC_REACHED_NO_INTERACTION', flush=True)
                return
            previous = number
        raise RuntimeError('segment budget exhausted; inspect before continuing')


if __name__ == '__main__':
    main()
