"""Run a bounded offline OpenCode worker; preserve raw UTF-8 JSON event bytes.

This is a logging boundary, not an OS sandbox. The caller supplies an isolated
clone, scoped permission JSON and prompt file. Never supply credentials here.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def run_worker(directory, log_dir, prompt, permissions, model, *, timeout=120,
               executable='opencode', session=None):
    directory = Path(directory).resolve()
    log_dir = Path(log_dir).resolve()
    if not directory.is_dir() or not (directory / '.git').exists():
        raise ValueError('worker must run in an existing independent clone')
    if not log_dir.is_dir() or log_dir == directory or directory in log_dir.parents:
        raise ValueError('log directory must exist outside worker clone')
    config = json.loads(Path(permissions).read_text(encoding='utf-8'))
    if config.get('share') != 'disabled' or not isinstance(config.get('permission'), dict):
        raise ValueError('worker requires explicit permission map and disabled sharing')
    if not 1 <= timeout <= 600:
        raise ValueError('timeout must be 1..600 seconds')
    command = [executable, 'run', '--pure', '--dir', str(directory), '--model', model,
               '--format', 'json', '--title', 'Bounded offline worker']
    # A bounded task may exceed one budget. Continuing the same session keeps
    # the worker's own context instead of restarting from the task book.
    if session:
        command.extend(['--session', session])
    command.append(Path(prompt).read_text(encoding='utf-8'))
    env = os.environ.copy()
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config, ensure_ascii=False)
    started = time.monotonic()
    # Exclusive creation avoids destroying a previous run's evidence. Direct
    # file descriptors avoid PowerShell's text redirection and transcoding.
    with (log_dir / 'events.jsonl').open('xb') as events, (log_dir / 'stderr.txt').open('xb') as errors:
        process = subprocess.Popen(command, cwd=directory, env=env,
                                   stdout=events, stderr=errors, stdin=subprocess.DEVNULL)
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait()
    return {'returncode': returncode, 'timeout': timed_out,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'events': str(log_dir / 'events.jsonl'), 'stderr': str(log_dir / 'stderr.txt')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clone', required=True, type=Path)
    parser.add_argument('--log-dir', required=True, type=Path)
    parser.add_argument('--prompt', required=True, type=Path)
    parser.add_argument('--permissions', required=True, type=Path)
    parser.add_argument('--model', required=True)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--session', help='continue an existing worker session')
    args = parser.parse_args()
    print(json.dumps(run_worker(args.clone, args.log_dir, args.prompt,
                                args.permissions, args.model, timeout=args.timeout,
                                session=args.session)))
