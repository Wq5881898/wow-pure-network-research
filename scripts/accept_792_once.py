"""One recorded auto-accept query at Zureetha; no retries or other actions."""
import json
import math
import subprocess
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/pure_network/quest792_accept_once.json'


def save(report):
    temporary = OUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(OUT)


def main():
    report = {'status': 'attempt_started', 'quest': 792, 'request_dispatched': False}
    with OUT.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)
    backend = PureNetworkBackend('quest792_accept_once')
    try:
        active = subprocess.run(['pgrep', '-x', 'agent-wow-cli'], capture_output=True)
        if active.returncode != 1:
            raise RuntimeError('active session or failed process check')
        backend.open()
        before = backend.observe().to_dict()
        report['before'] = before
        if before['ghost'] or before['in_combat'] is not False or before['health'] != before['max_health']:
            raise RuntimeError('unsafe character state')
        if any(q['quest'] == 792 for q in before['quest_log']):
            report['status'] = 'already_active_no_request'
        else:
            candidates = [obj for obj in backend.state['visible_objects'] if obj.get('entry') == 3145
                          and math.dist(before['position'], obj['movement']['position_xyzo'][:3]) <= 4]
            if len(candidates) != 1:
                raise RuntimeError('no unique nearby questgiver')
            report['npc'] = candidates[0]['guid']
            report['request_dispatched'] = True
            report['status'] = 'unknown'
            save(report)  # durable BEFORE possible side effect
            report['reply'] = backend.rpc('bridge.query_quest', {'guid': report['npc'], 'quest': 792})
            for _ in range(10):
                report['after'] = backend.observe().to_dict()
                if any(q['quest'] == 792 for q in report['after']['quest_log']):
                    report['status'] = 'active_observed'
                    break
                time.sleep(.5)
        final = backend.observe().to_dict()
        report['final'] = final
        if final['health'] > 0 and not final['ghost'] and final['in_combat'] is False:
            report['logout'] = backend._do_logout({}, 45)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        backend.close()
        save(report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
