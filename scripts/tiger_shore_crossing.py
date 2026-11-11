"""Explicit shallow-water corridor probe; no attack or teleport requests."""
import json
import math
import time
import sys
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session

RUN = sys.argv[1] if len(sys.argv) > 1 else 'tiger_crossing_20261110_at'
ROOT = Path(__file__).resolve().parents[1]
# findPath GROUND|WATER diagnostic portal vertices, not a ground PASS route.
CORRIDOR = [[-960,-5006.833,1.335],[-962.888,-5010.602,.604],
            [-964.982,-5013.334,.490],[-981.333,-5034.667,.071],
            [-986.667,-5056,.071],[-992,-5077.334,.071],[-997.333,-5098.667,.071],
            [-1002.667,-5120,.071],[-1009.024,-5136.312,.604],
            [-1010.894,-5141.111,1.96],[-1012.25,-5144.59,2.604]]


def main():
    path = ROOT / f'results/pure_network/crossing_{RUN}.json'
    if path.exists():
        raise SystemExit('existing probe; reconcile')
    backend = PureNetworkBackend(RUN)
    report = {'run_id': RUN, 'status': 'unknown', 'legs': [],
              'scope': 'explicit surface swim probe; independent login required'}
    def save(data=None):
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temp.replace(path)
    try:
        backend.open()
        obs = backend.observe()
        report['before'] = obs.to_dict()
        if obs.in_combat is not False or obs.ghost or obs.health != obs.max_health:
            raise RuntimeError('unsafe initial state')
        current = list(obs.position)
        if math.dist(current, [-945.21814,-4990.19385,5.63169]) > 4:
            raise RuntimeError('unexpected north shore start')
        for end in CORRIDOR:
            start = current
            steps = max(1, math.ceil(math.dist(start,end)/8), math.ceil(abs(start[2]-end[2])/2))
            points = [dict(zip(('x','y','z'), [a+(b-a)*i/steps for a,b in zip(start,end)]))
                      for i in range(1,steps+1)]
            report['legs'].append({'from': start, 'to': end, 'dispatched': True})
            save()
            reply = backend.rpc('bridge.follow_path', {'points': points, 'swim': True})
            report['legs'][-1]['reply'] = reply
            if 'error' in reply:
                raise RuntimeError('crossing segment rejected')
            current = reply['result']['submittedPosition'][:3]
            backend.submitted_position = current
            obs = backend.observe()
            report['legs'][-1]['health'] = obs.health
            report['legs'][-1]['in_combat'] = obs.in_combat
            save()
            if obs.ghost or obs.health < obs.max_health*.8 or obs.in_combat is not False:
                raise RuntimeError('crossing state requires recovery')
        report['status'] = 'submitted_corridor'
        report['submitted_end'] = current
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()


if __name__ == '__main__':
    main()
