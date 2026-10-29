"""Bounded return to Gornek; archive submissions separately from login evidence."""
import json
from agent_wow_session import PureNetworkBackend, root
from agent_wow_contract import Action


backend = PureNetworkBackend('run118_return789')
path = root / 'results/pure_network/agent_wow_task_run118_return789.json'
if path.exists():
    raise SystemExit('run already archived')
report = {}
try:
    backend.open()
    observation = backend.observe()
    if observation.ghost or observation.health <= 0 or observation.in_combat is not False:
        raise ValueError('living out-of-combat character required')
    if observation.item_totals.get('4862', 0) < 8:
        raise ValueError('eight tails required before return')
    start = backend.state['login']['position_xyzo'][:3]
    nav = backend._nav(start, [-600.132019, -4186.189941, 41.18079])
    points = backend._densify(nav, start)
    report['route'] = nav
    # Execute short chunks, read health/combat after each, without claiming
    # the cached world-state position is movement confirmation.
    report['segments'] = []
    for i in range(0, len(points), 6):
        replies, failure = backend._send_path(points[i:i+6], run=True)
        current = backend.observe()
        report['segments'].append({'replies': replies, 'failure': failure,
                                   'observation': current.to_dict()})
        if failure or current.ghost or current.health <= 0 or current.in_combat is not False:
            raise ValueError('return interrupted; inspect current state')
    report['final_observation'] = backend.observe().to_dict()
except Exception as exc:
    report['error'] = f'{type(exc).__name__}: {exc}'
finally:
    if backend.session_id:
        try:
            current = backend.observe()
            report['final_observation'] = current.to_dict()
            if current.health > 0 and not current.ghost and current.in_combat is False:
                report['logout'] = backend.execute(Action('logout', 'logout'))
        except Exception as exc:
            report['finalize_error'] = str(exc)
    backend.close()
    path.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k not in ('route', 'segments')}, indent=2))
