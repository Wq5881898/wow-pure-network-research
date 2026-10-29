"""H001 short segment plan, then explicit single-dispatch live trial."""
import argparse
import json
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'
PLAN = FOLDER / 'height_H001_segment1_plan.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        original = json.loads((FOLDER / 'height_route_H001_20261029.json').read_bytes())
        result = subprocess.run([str(ROOT / '.cache/nav-height-build/agent_wow_nav.exe'),
            r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps', '1',
            *map(str, original['start']), *map(str, original['end'])],
            capture_output=True, text=True, check=True, timeout=30)
        nav = json.loads(result.stdout)
        points = [nav['surface_samples'][0]]
        length = 0
        for point in nav['surface_samples'][1:]:
            distance = math.dist(points[-1], point)
            if length + distance > 6:
                break
            points.append(point)
            length += distance
        with PLAN.open('x', encoding='utf-8') as handle:
            json.dump({'nav': nav, 'points': points, 'length': length,
                       'scope': 'first <=6 yards of H001 only'}, handle, indent=2)
        print(json.dumps({'length': length, 'start': points[0], 'end': points[-1]}))
        return
    from agent_wow_session import PureNetworkBackend
    plan = json.loads(PLAN.read_bytes())
    report_path = FOLDER / 'height_H001_segment1_trial.json'
    # Persist attempt before any connection. Never replay an existing attempt.
    with report_path.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'attempt_started', 'plan': PLAN.name}, handle)
    backend = PureNetworkBackend('height_H001_segment1')
    report = {'status': 'unknown', 'plan': PLAN.name}
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
