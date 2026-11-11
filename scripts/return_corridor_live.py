"""Short actual outbound/return probe, using the production retreat function."""
import json
import math
import sys
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_travel import prepare_return, _bounded_leg
from agent_wow_escape_control import retreat
from agent_wow_exit_recovery import finish_session


def main():
    run = sys.argv[1]
    path = Path(__file__).resolve().parents[1] / f'results/pure_network/return_probe_{run}.json'
    if path.exists():
        raise SystemExit('existing probe')
    backend = PureNetworkBackend(run)
    report = {'run_id': run, 'status': 'unknown', 'scope': 'non-offensive short return probe'}
    def save(data=None):
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        tmp.replace(path)
    try:
        backend.open()
        before = backend.observe()
        report['before'] = before.to_dict()
        if before.health != before.max_health or before.in_combat is not False or before.ghost:
            raise RuntimeError('unsafe start')
        anchor = list(before.position)
        expected = list(map(float, sys.argv[2:5])) if len(sys.argv) >= 5 else [-778, -4985, 19]
        destination = list(map(float, sys.argv[5:8])) if len(sys.argv) >= 8 else [-774.774, -4830.36, 19.833]
        if math.dist(anchor, expected) > 3:
            raise RuntimeError('unexpected anchor; re-plan before movement')
        raw = backend._nav(anchor, destination)
        prefix = _bounded_leg([dict(zip(('x','y','z'), p[:3])) for p in raw['waypoints']], anchor, 12)
        points = backend._densify({'waypoints': [[p['x'],p['y'],p['z']] for p in prefix]},
                                  anchor, slope_tolerance=.7)
        _, reverse = prepare_return(backend, [anchor], points)
        report['anchor'] = anchor
        report['return_route'] = reverse
        report['forward_points'] = points
        backend.travel_return_route = {'points': reverse, 'origin': anchor,
                                      'safe_anchor': anchor, 'source': 'prevalidated original corridor'}
        report['forward_dispatched'] = True
        save()
        report['forward'], failure = backend._send_path(points)
        if failure:
            raise RuntimeError(failure)
        observation = backend.observe()
        report['outbound_observation'] = observation.to_dict()
        # Execute the same return regardless of whether an unexpected enemy
        # engaged; never idle or attempt target selection first.
        report['return_dispatched'] = True
        save()
        current = list(backend.submitted_position[:3])
        result, state = retreat(backend.rpc, backend.drain, points, current,
                                route=reverse, wait_seconds=10)
        report['retreat'] = result
        report['after'] = {k: state['self'].get(k) for k in ('health','max_health','ghost','in_combat')}
        if result.get('outcome') == 'out_of_combat':
            report['status'] = 'returned_submission_and_quiet_confirmed'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()


if __name__ == '__main__':
    main()
