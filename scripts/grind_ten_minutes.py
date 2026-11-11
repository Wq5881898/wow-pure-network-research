"""One explicitly requested ten-minute experiment; bounded batches, no forced kill."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def main():
    run_id = sys.argv[1]
    path = FOLDER / f'grind_ten_minutes_{run_id}.json'
    if path.exists() or not run_id.replace('_', '').isalnum():
        raise SystemExit('invalid or existing run')
    started = time.monotonic()
    report = {'run_id': run_id, 'started_at': time.time(), 'minimum_seconds': 600,
              'status': 'running', 'batches': []}
    def save():
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temp.replace(path)
    save()
    for index in range(1, 21):
        if time.monotonic() - started >= 600:
            break
        batch_id = f'{run_id}_{index:02d}'
        command = [sys.executable, '-B', 'scripts/agent_wow_quest_batch.py',
                   '--batch-id', batch_id, '--combat-entry', '3102',
                   '--max-target-level', '4', '--units', '1',
                   '--combat-budget', '45', '--objective-optional']
        # Parent waits for the session owner; no timeout that kills recovery.
        with (FOLDER / f'grind_console_{batch_id}.log').open('x') as out:
            child = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=out)
            code = child.wait()
        batch_path = FOLDER / f'agent_wow_quest_batch_{batch_id}.json'
        batch = json.loads(batch_path.read_text()) if batch_path.exists() else {}
        final = batch.get('final') or {}
        report['batches'].append({'batch_id': batch_id, 'returncode': code,
                                  'status': batch.get('status'), 'final': final})
        save()
        if code != 0 or not final.get('safe'):
            report['status'] = 'stopped_for_reconciliation'
            save()
            return
        time.sleep(5)
    report['status'] = ('completed_duration' if time.monotonic() - started >= 600
                        else 'unit_limit_reached')
    report['finished_at'] = time.time()
    save()


if __name__ == '__main__':
    main()
