"""Bounded stationary-window live experiment."""
import json
import math
import subprocess
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_combat_control import estimated_position
from agent_wow_objective import progress

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def main():
    path = FOLDER / 'quest792_window_long03.json'
    report = {'status': 'started', 'scope': 'stationary wait <=90s, one fight <=35s, no pursuit',
              'samples': [], 'attack_dispatch_recorded': False}
    with path.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)
    def save():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(path)
    backend = PureNetworkBackend('quest792_window_long03')
    objective = {'kind': 'kill', 'quest': 792, 'slot': 0, 'total': 8}
    try:
        check = subprocess.run(['pgrep', '-x', 'agent-wow-cli'], capture_output=True)
        if check.returncode != 1:
            raise RuntimeError('active session or failed occupancy check')
        backend.open()
        before = backend.observe().to_dict()
        report['before'] = before
        if (before['health'] != before['max_health'] or before['ghost']
                or before['in_combat'] is not False or progress(before, objective) is None):
            raise RuntimeError('start state not eligible')
        deadline = time.monotonic() + 90
        selected = None
        while time.monotonic() < deadline:
            observation = backend.observe().to_dict()
            if observation['health'] < before['health'] or observation['ghost'] or observation['in_combat']:
                report['status'] = 'state_changed_during_wait'
                break
            candidates = []
            for obj in backend.state['visible_objects']:
                if obj.get('entry') != 3101 or obj.get('health', 0) <= 0 or obj.get('level', 999) > 3:
                    continue
                xyz, source = estimated_position(obj, backend.stream_now())
                distance = math.dist(before['position'], xyz)
                candidates.append((distance, obj, source))
            nearest = min(candidates, key=lambda row: row[0]) if candidates else None
            report['samples'].append({'at': time.time(), 'nearest_distance': nearest[0] if nearest else None,
                                      'guid': nearest[1]['guid'] if nearest else None})
            if nearest and nearest[0] <= 4:
                selected = nearest[1]
                break
            time.sleep(.5)
        if selected:
            backend.target = selected
            report['target'] = {'guid': selected['guid'], 'entry': selected['entry'], 'level': selected['level']}
            report['attack_dispatch_recorded'] = True
            report['status'] = 'unknown'
            save()
            report['fight'] = backend._do_fight({'budget': 35}, 65)
            report['after_fight'] = backend.observe().to_dict()
            report['status'] = 'credit_observed' if progress(report['after_fight'], objective) > progress(before, objective) else 'no_credit'
        elif report['status'] == 'started':
            report['status'] = 'window_timeout_no_attack'
        final = backend.observe().to_dict()
        report['final'] = final
        if final['health'] > 0 and not final['ghost'] and final['in_combat'] is False:
            report['logout'] = backend._do_logout({}, 45)
        else:
            report['logout'] = {'status': 'not_safe', 'in_combat': final['in_combat']}
    except Exception as exc:
        report['status'] = 'unknown'
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        backend.close()
        save()
    print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, indent=2))


if __name__ == '__main__':
    main()
