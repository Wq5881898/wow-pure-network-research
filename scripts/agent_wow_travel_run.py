"""Travel towards a goal in stages, observing after every leg.

This is the movement leg of the hunt for quest 792's kill target (entry 3101).
The discipline is docs/73's: do not attack along the way, and pause on the
first sign of trouble (damage, an engaged fight, a rejected path).

The journey is staged rather than planned in one go: the direct route is
refused by the session's densifier where it drops over 5 yards, so each leg
is planned from where the character actually is.

Completion and guarded session termination are reported separately.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from agent_wow_travel import travel
from agent_wow_exit_recovery import finish_session

ROOT = Path(__file__).resolve().parents[1]
SPAWN_13055 = [-253.6, -4316.1, 56.0]


def run_travel(backend, report, *, goal, legs, radius, stop_distance,
               sleep=time.sleep, finish=finish_session, checkpoint=None):
    """Run one journey and always enter the shared guarded termination path."""
    opened = False
    try:
        backend.open()
        opened = True
        sleep(1.5)
        me = backend.drain()['self']
        report['initial'] = {key: me.get(key) for key in ('health', 'ghost', 'in_combat')}
        if (me.get('ghost') is not False or me.get('health') is None
                or me['health'] <= 0 or me.get('in_combat') is not False):
            report['error'] = 'precondition failed: not confirmed alive and out of combat'
            report['status'] = 'PRECONDITION_FAILED'
            return
        progress = travel(backend, report=report, goal=goal, legs=legs,
                          radius=radius, stop_distance=stop_distance, sleep=sleep)
        report['final'] = backend.drain()['self']
        report['status'] = ('GOAL_REACHED' if progress['reached_goal_area']
                            else 'INCOMPLETE')
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['status'] = 'ERROR'
    finally:
        if opened or getattr(backend, 'session_id', None):
            ending = finish(backend, report=report, hold_ownership=True,
                            checkpoint=checkpoint)
            if ending['transport_closed'] and report.get('status') == 'GOAL_REACHED':
                if ending.get('closed_after') == 'logout':
                    report['status'] = 'PASS'
                else:
                    report['status'] = 'INCOMPLETE'
            elif not ending['transport_closed']:
                report['status'] = 'REQUIRES_SUPERVISION'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--goal', type=float, nargs=3,
                        default=SPAWN_13055,
                        help='the point to travel towards')
    parser.add_argument('--legs', type=int, default=6)
    parser.add_argument('--radius', type=float, default=40.0)
    parser.add_argument('--stop-distance', type=float, default=60.0)
    args = parser.parse_args()
    if not args.run_id.replace('_', '').isalnum():
        parser.error('invalid run ID')
    folder = ROOT / 'results/pure_network'
    if list(folder.glob(f'*{args.run_id}*')):
        raise SystemExit('run artifacts exist')
    active = subprocess.run(['pgrep', '-x', 'agent-wow-cli'], capture_output=True, text=True)
    if active.returncode != 1:
        raise SystemExit('active bridge or failed occupancy check')

    from agent_wow_session import PureNetworkBackend
    backend = PureNetworkBackend(args.run_id)
    report = {'run_id': args.run_id, 'scope': 'staged travel towards the hunt goal',
              'status': 'unknown'}
    def checkpoint(data):
        # Keep the owner-visible report current during a prolonged recovery.
        path = folder / f'agent_wow_travel_{args.run_id}.supervision.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        temporary.replace(path)
    try:
        run_travel(backend, report, goal=args.goal, legs=args.legs,
                   radius=args.radius, stop_distance=args.stop_distance,
                   checkpoint=checkpoint)
    finally:
        with (folder / f'agent_wow_travel_{args.run_id}.json').open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
