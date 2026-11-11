"""One explicitly minimum-level filtered, ground-nav verified combat trial."""
import json
import math
import sys
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_combat_control import estimated_position
from agent_wow_exit_recovery import finish_session
from agent_wow_travel import prepare_return

def main():
    run = sys.argv[1]
    entries = (int(sys.argv[2]),) if len(sys.argv) > 2 else (3099, 3106, 3104, 3121)
    maximum_level = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    path = Path(__file__).resolve().parents[1] / f'results/pure_network/near_level_{run}.json'
    if path.exists():
        raise SystemExit('existing run')
    backend = PureNetworkBackend(run)
    report = {'run_id': run, 'status': 'unknown', 'rejected': []}
    def save(data=None):
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        tmp.replace(path)
    try:
        backend.open()
        before = backend.observe()
        report['before'] = before.to_dict()
        if before.in_combat is not False or before.ghost or before.health < before.max_health*.9:
            raise RuntimeError('unsafe precondition')
        candidates = [o for o in backend.state['visible_objects']
                      if o.get('type_id') == 3 and 6 <= o.get('level', 0) <= maximum_level
                      and o.get('entry') in entries
                      and o.get('health', 0) > 0]
        candidates.sort(key=lambda o: math.dist(before.position,
                         estimated_position(o, backend.stream_now())[0][:3]))
        for target in candidates:
            position = estimated_position(target, backend.stream_now())[0][:3]
            try:
                raw = backend._nav(before.position, position)
                points = backend._densify(raw, before.position, slope_tolerance=.7)
                _, reverse = prepare_return(backend, [before.position], points)
            except RuntimeError as exc:
                report['rejected'].append({'guid': target['guid'], 'entry': target['entry'],
                                            'level': target['level'], 'reason': str(exc)})
                continue
            backend.target = target
            backend.travel_return_route = {'points': reverse, 'origin': list(before.position),
                                          'safe_anchor': list(before.position),
                                          'source': 'prevalidated approach return corridor'}
            report['return_plan'] = backend.travel_return_route
            backend.start_position = list(before.position)
            report['target'] = {k: target.get(k) for k in ('guid', 'entry', 'level', 'health')}
            report['dispatched_at'] = time.time()
            save()
            result = backend._do_fight({'budget': 60, 'attack_attempts': 1}, 90)
            report['fight'] = result
            report['after'] = backend.observe().to_dict()
            report['status'] = result.get('outcome')
            break
        else:
            report['status'] = 'no_reachable_min_level_target'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True, checkpoint=save)
        save()

if __name__ == '__main__':
    main()
