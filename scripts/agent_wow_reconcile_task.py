"""Read-only reconciliation of a closed movement boundary and a new login."""
import argparse
import hashlib
import json
from pathlib import Path
from agent_wow_position import confirm_relogin
from agent_wow_task_boundary import movement_boundary
from agent_wow_worldstate import replay


def reconcile(report, trace, quest=789):
    boundary = movement_boundary(report)
    verification = confirm_relogin(boundary['source_session'], boundary['expected_xyz'],
                                  boundary['map'], trace, character=boundary['character'])
    state = replay(trace)
    me = state['self']
    active = any(q['quest'] == quest for q in me['quest_log'])
    ready = me['health'] > 0 and not me['ghost'] and me['in_combat'] is False
    reasons = []
    if not active:
        reasons.append('required quest not active; completion versus abandonment not established')
    if not verification['confirmed']:
        reasons.append('new login position does not match archived endpoint')
    if not ready:
        reasons.append('character not alive and out of combat')
    return {'status': 'REPLAN_REQUIRED' if reasons else 'BOUNDARY_CONFIRMED',
            'reasons': reasons, 'position_verification': verification,
            'quest_required': quest, 'quest_active': active,
            'current': {k: me[k] for k in ('health', 'max_health', 'level', 'ghost', 'in_combat', 'quest_log')},
            'movement_issued': False, 'combat_authorized': False,
            'scope': 'new login boundary only; original target requires fresh independent recheck'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('output exists; refusing overwrite')
    raw_report, raw_trace = args.report.read_bytes(), args.trace.read_bytes()
    result = reconcile(json.loads(raw_report), json.loads(raw_trace))
    result['source_sha256'] = {'report': hashlib.sha256(raw_report).hexdigest(),
                               'trace': hashlib.sha256(raw_trace).hexdigest()}
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
