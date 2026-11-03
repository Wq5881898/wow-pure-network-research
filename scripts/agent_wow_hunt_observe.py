"""Read-only observation of the hunt spawn area.

docs/73: stop outside the spawn area, then a fresh login observes whether a
live level<=3 3101 is there and whether it has a workable approach route.
Static spawn points are not live target evidence.

This logs in, lists the visible creatures with their entry and level, and
checks the quest counter. It attacks nothing.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from agent_wow_session import PureNetworkBackend

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--target-entry', type=int, default=3101)
    parser.add_argument('--max-level', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=20.0)
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
    report = {'run_id': args.run_id, 'scope': 'read-only observation of the hunt spawn area',
              'status': 'unknown'}
    try:
        backend.open()
        time.sleep(1.5)
        deadline = time.monotonic() + args.seconds
        creatures = {}
        while time.monotonic() < deadline:
            state = backend.drain()
            me = state['self']
            report['self'] = {'health': me.get('health'), 'ghost': me.get('ghost'),
                              'in_combat': me.get('in_combat'),
                              'level': me.get('level'),
                              'position': list((me.get('movement') or {}).get('position_xyzo') or [])[:3],
                              'quest_log': me.get('quest_log')}
            creatures = {}
            for obj in state.get('visible_objects', []):
                if obj.get('type_id') != 3:
                    continue
                key = (obj.get('entry'), obj.get('level'))
                creatures[key] = creatures.get(key, 0) + 1
            if any(entry == args.target_entry for entry, _level in creatures):
                break
            time.sleep(0.5)
        report['visible_creatures'] = [
            {'entry': entry, 'level': level, 'count': count}
            for (entry, level), count in sorted(creatures.items(), key=lambda kv: str(kv[0]))]
        candidates = [entry for (entry, level), count in creatures.items()
                      if entry == args.target_entry and (level or 0) <= args.max_level]
        report['target_entry'] = args.target_entry
        report['live_candidates'] = candidates
        report['verdict'] = ('live level<=%d %d present' % (args.max_level, args.target_entry)
                              if candidates else
                              'no live level<=%d %d in view' % (args.max_level, args.target_entry))
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
        with (folder / f'agent_wow_huntobs_{args.run_id}.json').open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
