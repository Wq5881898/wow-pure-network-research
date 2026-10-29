"""Bounded login/poll/logout observation; sends no movement or combat requests."""
import argparse
import json
import subprocess
from pathlib import Path
from agent_wow_session import PureNetworkBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not args.run_id.replace('_', '').isalnum():
        parser.error('invalid run ID')
    folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
    if list(folder.glob(f'*{args.run_id}*')):
        raise RuntimeError('run artifacts exist')
    active = subprocess.run(['pgrep', '-x', 'agent-wow-cli'], capture_output=True, text=True)
    if active.returncode != 1:
        raise RuntimeError('active bridge or failed occupancy check')
    backend = PureNetworkBackend(args.run_id)
    report = {'run_id': args.run_id, 'scope': 'login/poll/logout only', 'status': 'unknown'}
    try:
        backend.open()
        report['observation'] = backend.observe().to_dict()
        report['visible_candidates'] = [
            {key: obj.get(key) for key in ('guid', 'entry', 'health', 'level', 'movement')}
            for obj in backend.state['visible_objects'] if obj.get('entry') in (3145, 3101)]
        report['logout'] = backend._do_logout({}, 45)
        if report['logout'].get('status') == 'closed':
            report['status'] = 'observed_and_closed'
            backend.result['status'] = 'PASS'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        backend.close()
        with (folder / f'agent_wow_readonly_{args.run_id}.json').open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
