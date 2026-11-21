"""Reusable Netprobe login-and-hold command (run in WoWLab)."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
LOCK = CACHE / 'level15_chain.lock'
CLI = Path.home() / 'wow-research/agent-wow-cli-local18'
PROFILE = Path.home() / 'wow-research/spellcapture18-session2'


def identity(pid):
    try:
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, ValueError, TypeError, IndexError):
        return None


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def active_idle():
    for path in sorted(CACHE.glob('netprobe_idle_*_status.json'), reverse=True):
        state = load(path)
        owner = load(path.with_name(path.name.replace('_status.json', '_owner.json')))
        cli_identity = identity(state.get('cli'))
        if (state.get('cli_alive') and cli_identity is not None
                and state.get('cli_identity', cli_identity) == cli_identity
                and owner.get('pid') == state.get('worker')
                and owner.get('identity') is not None
                and identity(owner['pid']) == owner['identity']):
            return {**state, 'sample_age_seconds': round(time.time()-state['epoch'], 2)}
    return None


def hold(run):
    import agent_wow_session as session
    from agent_wow_run_artifacts import validate_run_artifacts

    class IdleBackend(session.PureNetworkBackend):
        def rpc(self, method, params):
            if method != 'bridge.poll':
                raise RuntimeError(f'idle RPC forbidden: {method}')
            return super().rpc(method, params)

    start = time.monotonic()
    owner = CACHE / f'{run}_owner.json'
    status = CACHE / f'{run}_status.json'
    console = CACHE / f'{run}_console.log'
    with LOCK.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_run_artifacts(ROOT, run, Path(__file__).read_text(),
                               [str(p) for p in (owner, status, status.with_suffix('.tmp'), console)])
        with console.open('x', buffering=1) as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            db = json.loads(subprocess.check_output(
                ['/mnt/c/APP/Python/python.exe', '-B',
                 r'C:\APP\opencode\project\wow pure network\.cache\chain_database.py'],
                cwd=ROOT, text=True, timeout=20))
            processes = subprocess.check_output(['ps', '-eo', 'pid,args'], text=True).splitlines()
            if db['online'] or any('agent-wow-cli' in p or ('_worker.py' in p and 'python' in p)
                                   for p in processes):
                raise RuntimeError('existing game session or worker; no login attempted')
            checked = time.monotonic()
            with owner.open('x') as out:
                json.dump({'pid': os.getpid(), 'identity': identity(os.getpid())}, out)
            session.folder = PROFILE
            session.ensure_bridge_module = lambda: None  # reuse installed bridge
            backend = IdleBackend(run)
            error = None
            try:
                backend.open()
            except Exception as exc:
                error = repr(exc)
            opened = time.monotonic()
            cli = getattr(backend.session, 'process', None)
            cli_identity = identity(cli.pid) if cli else None
            while True:
                state = backend.state or {}
                me = state.get('self') or {}
                report = {'run': run, 'epoch': time.time(), 'worker': os.getpid(),
                          'cli': cli.pid if cli else None, 'cli_identity': cli_identity,
                          'cli_alive': bool(cli and cli.poll() is None), 'error': error,
                          'login_position': (state.get('login') or {}).get('position_xyzo'),
                          'cursor': backend.cursor, 'actions': 0,
                          'timing_seconds': {'preflight': checked-start, 'open': opened-checked},
                          'self_state': {k: me.get(k) for k in ('guid', 'level', 'health', 'in_combat', 'ghost')}}
                tmp = status.with_suffix('.tmp')
                tmp.write_text(json.dumps(report))
                tmp.replace(status)
                if not report['cli_alive']:
                    return
                time.sleep(2)
                try:
                    backend.drain()
                    error = None
                except Exception as exc:
                    error = repr(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status', '_hold'])
    parser.add_argument('--run', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == '_hold':
        hold(args.run)
        return 0
    existing = active_idle()
    if existing is not None or args.action == 'status':
        print(json.dumps(existing or {'status': 'no active idle owner'}))
        return 0
    if not CLI.is_file() or not (PROFILE / 'config.yaml').is_file():
        parser.error('prebuilt CLI or installed profile missing')
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'busy', 'reason': 'game owner lock held'}))
            return 1
    run = 'netprobe_idle_' + uuid.uuid4().hex
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'scripts'), AGENT_WOW_LOCAL_REALM_ID='1',
               AGENT_WOW_LOCAL_REALM_ADDRESS='127.0.0.1:8085', AGENT_WOW_CLI=str(CLI))
    worker = subprocess.Popen([sys.executable, '-uB', __file__, '_hold', '--run', run],
                              cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True)
    status = CACHE / f'{run}_status.json'
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = load(status)
        if state.get('error') or state.get('login_position'):
            print(json.dumps(state))
            return int(bool(state.get('error')) or not state.get('cli_alive'))
        if worker.poll() is not None:
            print(json.dumps({'status': 'failed', 'run': run,
                              'console': str(CACHE / f'{run}_console.log')}))
            return 1
        time.sleep(.1)
    print(json.dumps({'status': 'starting', 'run': run, 'worker': worker.pid,
                      'status_file': str(status)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
