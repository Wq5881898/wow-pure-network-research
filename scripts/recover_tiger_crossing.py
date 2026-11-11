"""Recover this incident with a Spirit Healer, preserving unique artifacts."""
import json
import time
import sys
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session

RUN = sys.argv[1] if len(sys.argv) > 1 else 'tiger_recovery_20261110_ax'

def main():
    path = Path(__file__).resolve().parents[1] / f'results/pure_network/recovery_{RUN}.json'
    if path.exists():
        raise SystemExit('existing recovery')
    backend = PureNetworkBackend(RUN)
    report = {'run_id': RUN, 'status': 'unknown'}
    def save(data=None):
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temp.replace(path)
    try:
        backend.open()
        before = backend.observe()
        report['before'] = before.to_dict()
        if before.health <= 0 and not before.ghost:
            report['release_dispatched'] = True
            save()
            report['release'] = backend.rpc('bridge.release_spirit', {})
        deadline = time.monotonic() + 20
        healer = None
        while time.monotonic() < deadline:
            state = backend.drain()
            if state['self']['ghost']:
                healer = next((o for o in state['visible_objects'] if o.get('entry') == 6491), None)
                if healer:
                    break
            time.sleep(.3)
        if healer is None:
            raise RuntimeError('no graveyard ghost/healer confirmation')
        report['healer'] = healer
        report['revive_dispatched'] = True
        save()
        report['revive'] = backend.rpc('bridge.spirit_healer', {'guid': healer['guid']})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            obs = backend.observe()
            report['after'] = obs.to_dict()
            if obs.health > 0 and not obs.ghost and obs.in_combat is False:
                report['status'] = 'revived_observed'
                break
            time.sleep(.3)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()

if __name__ == '__main__':
    main()
