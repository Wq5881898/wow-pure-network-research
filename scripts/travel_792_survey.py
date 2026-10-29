"""Walk verified short segments; stop at first live familiar, without attacking."""
import json
import subprocess
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def call(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=180)
    if result.returncode:
        raise RuntimeError(result.stdout[-1500:] + result.stderr[-1500:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--last', type=int, default=22)
    parser.add_argument('--max-target-level', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.start <= args.last <= 22:
        parser.error('segment range must be within 1..22')
    source = FOLDER / ('agent_wow_readonly_quest792_accept_verify.json' if args.start == 1
                      else f'agent_wow_readonly_height_Q792_segment{args.start-1}_verify.json')
    with (FOLDER / f'quest792_travel_survey_{args.start}_{args.last}.jsonl').open('x', encoding='utf-8') as journal:
        for number in range(args.start, args.last+1):
            observation = json.loads(source.read_bytes())['observation']
            if not any(q['quest'] == 792 for q in observation['quest_log']):
                raise RuntimeError('quest792 missing')
            call([sys.executable, 'scripts/height_segment_trial.py', '--case', 'Q792',
                  '--segment', str(number), '--distance', '20', '--from-observation', str(source)])
            call(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--', 'python3',
                  'scripts/height_segment_trial.py', '--case', 'Q792', '--segment', str(number), '--execute'])
            call(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--', 'python3',
                  'scripts/read_only_character.py', '--run-id', f'height_Q792_segment{number}_verify'])
            call([sys.executable, 'scripts/verify_height_segment.py', '--case', 'Q792', '--segment', str(number)])
            source = FOLDER / f'agent_wow_readonly_height_Q792_segment{number}_verify.json'
            verify = json.loads(source.read_bytes())
            targets = [o for o in verify['visible_candidates'] if o['entry'] == 3101]
            row = {'segment': number, 'position': verify['observation']['position'], 'targets': targets,
                   'health': verify['observation']['health']}
            journal.write(json.dumps(row) + '\n')
            journal.flush()
            print(json.dumps(row), flush=True)
            if any(o.get('health', 0) > 0 and o.get('level', 999) <= args.max_target_level for o in targets):
                print('LIVE_TARGETS_VISIBLE_STOP_NO_ATTACK', flush=True)
                return
        raise RuntimeError('travel budget reached without live target; inspect before retry')


if __name__ == '__main__':
    main()
