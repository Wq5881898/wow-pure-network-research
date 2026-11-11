"""Offline repair-route assessment; no game login or movement."""
import json
import os
import subprocess
import sys
from pathlib import Path
from agent_wow_session import PureNetworkBackend


def main():
    if '--resume-check' in sys.argv or '--blocked-check' in sys.argv or '--surface-check' in sys.argv:
        from agent_wow_travel import plan_leg, prepare_return
        backend = PureNetworkBackend('offline_repair_resume')
        surface = '--surface-check' in sys.argv
        blocked = '--blocked-check' in sys.argv or surface
        backend.submitted_position = ([-640.64557, -4474.223, 48.090023] if blocked else
                                      [-637.4378, -4525.2954, 45.20812])
        rows = []
        for radius in (20, 12, 8):
            try:
                leg = plan_leg(backend, [-581.734, -4109.510, 43.608], radius=radius)
                prepare_return(backend, [backend.submitted_position], leg['points'])
                rows.append({'radius': radius, 'status': 'checked', 'end': leg['end']})
            except Exception as exc:
                rows.append({'radius': radius, 'error': str(exc)})
        path = Path('results/pure_network/' + ('repair_route_surface_20261110_cw.json' if surface else 'repair_route_blocked_20261110_cu.json' if blocked else
                                             'repair_route_resume_20261110_cr.json'))
        with path.open('x', encoding='utf-8') as handle:
            json.dump(rows, handle, indent=2)
        print(json.dumps(rows))
        return
    path = Path('results/pure_network/repair_route_scorp_20261110_cp.json')
    if path.exists():
        raise SystemExit('report exists')
    start = [-778, -4985, 19]
    goal = [-581.734, -4109.510, 43.608]
    command = [os.environ['AGENT_WOW_NAV'], os.environ['AGENT_WOW_MMAPS'],
               '1', *map(str, start), *map(str, goal)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    report = {'scope': 'offline nav only; not hostile clearance', 'start': start,
              'goal': goal, 'returncode': result.returncode, 'stderr': result.stderr}
    if result.returncode == 0:
        route = json.loads(result.stdout)
        report['route'] = route
        try:
            points = PureNetworkBackend._densify(route, start, slope_tolerance=.7)
            report.update(status='geometry_checked_only', point_count=len(points))
        except RuntimeError as exc:
            report.update(status='geometry_rejected', error=str(exc))
    else:
        report['status'] = 'nav_failed'
    with path.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != 'route'}))


if __name__ == '__main__':
    main()
