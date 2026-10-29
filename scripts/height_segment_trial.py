"""H001 short segment plan, then explicit single-dispatch live trial."""
import argparse
import json
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--segment', type=int, default=1)
    parser.add_argument('--distance', type=float, default=6)
    parser.add_argument('--from-observation', type=Path)
    parser.add_argument('--case', choices=['H001', 'Q792', 'R792'], default='H001')
    args = parser.parse_args()
    if args.segment < 1 or not 1 <= args.distance <= 20:
        parser.error('segment must be positive and distance 1..20')
    run_id = f'height_{args.case}_segment{args.segment}'
    plan_path = FOLDER / f'{run_id}_plan.json'
    if not args.execute:
        if args.case == 'R792':
            parser.error('R792 uses prevalidated staged plans from trial_retreat_route.py')
        original = json.loads((FOLDER / 'height_route_H001_20261029.json').read_bytes())
        if args.case == 'Q792':
            routes = json.loads((FOLDER / 'quest792_static_hunt_routes.json').read_bytes())
            original['end'] = next(c['end'] for c in routes['candidates'] if c['static_spawn_id'] == 13055)
        if args.from_observation:
            source = json.loads(args.from_observation.read_bytes())
            if source.get('status') != 'observed_and_closed':
                raise RuntimeError('source observation was not closed normally')
            original['start'] = source['observation']['position']
        elif args.segment != 1:
            raise RuntimeError('later segment requires fresh observation')
        result = subprocess.run([str(ROOT / '.cache/nav-height-build/agent_wow_nav.exe'),
            r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps', '1',
            *map(str, original['start']), *map(str, original['end'])],
            capture_output=True, text=True, check=True, timeout=30)
        nav = json.loads(result.stdout)
        points = [nav['surface_samples'][0]]
        length = 0
        for point in nav['surface_samples'][1:]:
            distance = math.dist(points[-1], point)
            if length + distance > args.distance:
                break
            points.append(point)
            length += distance
        with plan_path.open('x', encoding='utf-8') as handle:
            json.dump({'nav': nav, 'points': points, 'length': length,
                       'source_observation': str(args.from_observation) if args.from_observation else None,
                       'scope': f'bounded {args.case} segment only'}, handle, indent=2)
        print(json.dumps({'length': length, 'start': points[0], 'end': points[-1]}))
        return
    from agent_wow_session import PureNetworkBackend
    plan = json.loads(plan_path.read_bytes())
    report_path = FOLDER / f'{run_id}_trial.json'
    # Persist attempt before any connection. Never replay an existing attempt.
    with report_path.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'attempt_started', 'plan': plan_path.name}, handle)
    backend = PureNetworkBackend(run_id)
    report = {'status': 'unknown', 'plan': plan_path.name}
    try:
        active = subprocess.run(['pgrep', '-x', 'agent-wow-cli'], capture_output=True)
        if active.returncode != 1:
            raise RuntimeError('active session or occupancy check failed')
        backend.open()
        before = backend.observe().to_dict()
        report['before'] = before
        if (math.dist(before['position'], plan['points'][0]) > .1 or before['ghost']
                or before['in_combat'] is not False or before['health'] != before['max_health']):
            raise RuntimeError('start position or safety state changed')
        report['movement'] = backend.rpc('bridge.follow_path', {
            'points': [dict(zip(('x', 'y', 'z'), p)) for p in plan['points']], 'run': False})
        if 'error' in report['movement']:
            raise RuntimeError('movement rejected; inspect recorded reply')
        report['after'] = backend.observe().to_dict()
        if report['after']['in_combat'] is False and not report['after']['ghost']:
            report['logout'] = backend._do_logout({}, 45)
        report['status'] = 'submitted_requires_fresh_login'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        backend.close()
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
