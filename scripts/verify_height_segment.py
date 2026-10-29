"""Reconcile one height trial with independent login evidence."""
import argparse
import json
import math
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1] / 'results/pure_network'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--segment', type=int, required=True)
    parser.add_argument('--case', choices=['H001', 'Q792', 'R792'], default='H001')
    args = parser.parse_args()
    name = f'height_{args.case}_segment{args.segment}'
    plan = json.loads((FOLDER / f'{name}_plan.json').read_bytes())
    trial = json.loads((FOLDER / f'{name}_trial.json').read_bytes())
    verify = json.loads((FOLDER / f'agent_wow_readonly_{name}_verify.json').read_bytes())
    before, final = trial['before'], verify['observation']
    error = math.dist(final['position'], plan['points'][-1])
    checks = {
        'no_trial_error': not trial.get('error'),
        'movement_reply': 'submittedPosition' in trial.get('movement', {}).get('result', {}),
        'separate_session': before['session_id'] != final['session_id'],
        'same_character_map': before['character'] == final['character'] == '9' and before['map_id'] == final['map_id'],
        'landing_within_01': error <= .1,
        'health_preserved': before['health'] == final['health'] == final['max_health'],
        'safe': final['ghost'] is False and final['in_combat'] is False,
        'both_closed': trial.get('logout', {}).get('status') == 'closed' and verify.get('status') == 'observed_and_closed',
    }
    result = {'checks': checks, 'confirmed': all(checks.values()), 'landing_error': error,
              'path_length': plan['length'], 'net_height_change': final['position'][2]-before['position'][2],
              'scope': 'segment endpoint and final safety; not continuous trajectory validation'}
    with (FOLDER / f'{name}_reconciled.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, indent=2))
    raise SystemExit(not result['confirmed'])


if __name__ == '__main__':
    main()
