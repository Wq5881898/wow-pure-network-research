"""Travel towards a goal in stages, observing after every leg.

This is the movement leg of the hunt for quest 792's kill target (entry 3101).
The discipline is docs/73's: do not attack along the way, and pause on the
first sign of trouble (damage, an engaged fight, a rejected path).

The journey is staged rather than planned in one go: the direct route is
refused by the session's densifier where it drops over 5 yards, so each leg
is planned from where the character actually is.

It logs out cleanly at the end, so the character is never left in the world.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from agent_wow_session import PureNetworkBackend
from agent_wow_travel import travel

ROOT = Path(__file__).resolve().parents[1]
SPAWN_13055 = [-253.6, -4316.1, 56.0]


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

    backend = PureNetworkBackend(args.run_id)
    report = {'run_id': args.run_id, 'scope': 'staged travel towards the hunt goal',
              'status': 'unknown'}
    try:
        backend.open()
        time.sleep(1.5)
        state = backend.drain()
        me = state['self']
        report['initial'] = {'health': me.get('health'), 'ghost': me.get('ghost'),
                             'in_combat': me.get('in_combat'),
                             'position': list((me.get('movement') or {}).get('position_xyzo') or [])[:3]}
        if me.get('ghost') or (me.get('health') or 0) <= 0 or me.get('in_combat') is True:
            report['error'] = 'precondition failed: not alive and out of combat'
            raise SystemExit(1)
        travel(backend, report=report, goal=args.goal, legs=args.legs,
               radius=args.radius, stop_distance=args.stop_distance)
        final = backend.drain()['self']
        report['final'] = {'health': final.get('health'), 'ghost': final.get('ghost'),
                            'in_combat': final.get('in_combat'),
                            'position': list((final.get('movement') or {}).get('position_xyzo') or [])[:3]}
        report['status'] = 'PASS'
        report['logout'] = backend._do_logout({}, 45)
    except SystemExit:
        raise
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            backend.close(force=True)
        except Exception as exc:
            report.setdefault('close_error', f'{type(exc).__name__}: {exc}')
        with (folder / f'agent_wow_travel_{args.run_id}.json').open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
