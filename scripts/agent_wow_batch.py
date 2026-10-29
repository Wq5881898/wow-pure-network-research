"""Bounded batch coordinator. Reuses wsl_task_runner; never retries unknown units.

Runs natively inside WSL. Set AGENT_WOW_WINDOWS_ROOT when the project lives on a
Windows drive so launch can fall back to `wsl.exe` (Windows task scheduler case).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ROOT = os.environ.get('AGENT_WOW_WINDOWS_ROOT', '')
ACTIVE_PROCESS_PATTERN = r'agent-wow-cli|wsl_task_runner|wsl_bridge_probe'


def classify(report):
    records = report.get('run', {}).get('records', [])
    # Two terminal unknowns carry their own evidence and can still be a unit
    # success: an approach confirmed only across the session boundary, and a
    # loot whose effect is proven by the fresh post-loot observation. The
    # classifier re-derives the verdict from records instead of trusting the
    # executor's status field.
    approach_cross = any(r.get('action_id') == 'approach'
                         and r.get('evidence', {}).get('confirmed_by') == 'fresh_session_login'
                         for r in records)
    loot_confirmed = any(r.get('action_id') == 'loot' and r.get('status') == 'unknown'
                         and bool(r.get('evidence', {}).get('received')) for r in records)
    terminal_unknown = (report.get('run', {}).get('status') == 'unknown'
                        and (approach_cross or loot_confirmed))
    if report.get('run', {}).get('status') != 'success' and not terminal_unknown:
        return 'STOPPED', None
    rows = [r for r in records
            if r.get('evidence', {}).get('confirmed_by') != 'fresh_session_login']
    by_id = {r['action_id']: r for r in rows}
    # The movement leg is proven either in-session (executed approach row) or
    # across the session boundary (synthesized record: the fresh login landed
    # on the previous session's submitted endpoint). A unified two-session
    # report may therefore lack in-session select/approach rows. Settle and
    # logout may also be done by the executor's finalization (confirmed closed
    # logout) instead of contract actions. select_target is optional: it
    # re-points the original entry when the boundary target died or respawned.
    required = {'verify_alive', 'fight', 'loot'}
    allowed = set(required) | {'approach', 'select_target', 'settle', 'logout'}
    if not approach_cross:
        required |= {'select_target', 'approach'}
    if not required <= set(by_id):
        return 'UNKNOWN', None
    if any(r.get('action_id') not in ('approach', 'loot') and r.get('status') != 'success'
           for r in rows):
        return 'UNKNOWN', None
    if any(r.get('action_id') == 'loot' and r.get('status') == 'unknown'
           and not r.get('evidence', {}).get('received') for r in rows):
        return 'UNKNOWN', None
    # The executed approach row may legitimately stay unconfirmed in-session;
    # an executed approach (single-session unit) must then be backed by the
    # fresh-session login confirmation record.
    if not approach_cross and by_id['approach'].get('status') != 'success':
        return 'UNKNOWN', None
    final = report.get('final_observation', {})
    loot = by_id['loot']
    if loot.get('status') not in ('success', 'unknown') or (
            loot.get('status') == 'unknown' and not loot.get('evidence', {}).get('received')):
        return 'UNKNOWN', None
    before = loot.get('observation_before', {}).get('item_totals', {}).get('4862')
    after = loot.get('observation_after', {}).get('item_totals', {}).get('4862')
    if not (isinstance(before, int) and isinstance(after, int) and after > before):
        # A loot whose in-session echo never showed the new inventory total can
        # still count when the loot was actually received and the fresh final
        # observation carries a strictly higher total than before the fight.
        if not (isinstance(before, int)
                and isinstance(final.get('item_totals', {}).get('4862'), int)
                and final['item_totals']['4862'] > before
                and loot.get('evidence', {}).get('received')):
            return 'UNKNOWN', None
        after = final['item_totals']['4862']
    # The logout leg is proven either by an explicit logout action record or by
    # the executor's finalization (confirmed closed logout in 'finalize').
    logout = by_id['logout'] if 'logout' in by_id else report.get('finalize', {})
    if logout.get('status') != 'closed':
        logout = logout.get('evidence', {})
    if (final.get('character') != '9' or final.get('health', 0) <= 0
            or final.get('ghost') is not False or final.get('in_combat') is not False
            or final.get('item_totals', {}).get('4862', after) != after
            or logout.get('status') != 'closed'):
        return 'UNKNOWN', None
    return 'CONFIRMED', {'before': before, 'after': after}


def coordinate(folder, batch_id, limit, goal, launch, unit_args=None):
    if not re.fullmatch(r'[A-Za-z0-9_]+', batch_id) or not 1 <= limit <= 3 or goal < 1:
        raise ValueError('invalid batch ID, limit (1..3) or goal')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder / f'agent_wow_batch_{batch_id}.json'
    config = {'batch_id': batch_id, 'limit': limit, 'goal': goal, 'unit_args': unit_args or {}}
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
        if state['config'] != config:
            raise ValueError('checkpoint configuration mismatch')
    else:
        state = {'config': config, 'status': 'READY', 'units': []}
    if state['status'] in ('GOAL_RECORDED', 'BUDGET_REACHED', 'STOPPED', 'RECONCILE_REQUIRED'):
        return state

    def save():
        temporary = checkpoint.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        temporary.replace(checkpoint)

    # Dispatch is persisted before launch. A recovery unit with a late report is
    # only classified (never re-dispatched, since its movement side effect may
    # already have happened); a recovery unit with no report at all is
    # re-dispatched once without the pre-launch occupancy guard.
    for unit in state['units']:
        if unit['status'] != 'DISPATCHED':
            continue
        report_path = folder / f"agent_wow_task_{unit['run_id']}_unified.json"
        if not report_path.exists():
            try:
                launch(unit['run_id'], check_session=False)
                status, progress = classify(json.loads(report_path.read_text(encoding='utf-8')))
            except Exception as exc:
                state['status'] = 'UNKNOWN'
                unit['error'] = f'{type(exc).__name__}: {exc}'
                save()
                return state
        else:
            status, progress = classify(json.loads(report_path.read_text(encoding='utf-8')))
        unit.update(status=status, progress=progress)
        state['status'] = 'READY' if status == 'CONFIRMED' else status
        save()
    for previous, current in zip(state['units'], state['units'][1:]):
        if (previous.get('progress') and current.get('progress')
                and previous['progress']['after'] != current['progress']['before']):
            state['status'] = 'RECONCILE_REQUIRED'
            save()
            return state
    if any(u['status'] != 'CONFIRMED' for u in state['units']):
        return state
    while len(state['units']) < limit:
        if state['units'] and state['units'][-1]['progress']['after'] >= goal:
            state['status'] = 'GOAL_RECORDED'
            save()
            return state
        run_id = f'{batch_id}_unit{len(state["units"])+1}'
        if list(folder.glob(f'agent_wow*{run_id}*')):
            raise ValueError('run artifacts already exist; refusing overwrite')
        unit = {'run_id': run_id, 'status': 'DISPATCHED'}
        state['units'].append(unit)
        state['status'] = 'RUNNING'
        save()
        try:
            launch(run_id)
            path = folder / f'agent_wow_task_{run_id}_unified.json'
            status, progress = classify(json.loads(path.read_text(encoding='utf-8')))
        except Exception as exc:
            state['status'] = 'UNKNOWN'
            unit['error'] = f'{type(exc).__name__}: {exc}'
            save()
            return state
        unit.update(status=status, progress=progress)
        if status != 'CONFIRMED':
            state['status'] = status
            save()
            return state
        if len(state['units']) > 1 and progress['before'] != state['units'][-2]['progress']['after']:
            state['status'] = 'RECONCILE_REQUIRED'
            save()
            return state
        save()
    state['status'] = 'GOAL_RECORDED' if state['units'][-1]['progress']['after'] >= goal else 'BUDGET_REACHED'
    save()
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--goal', type=int, default=8)
    parser.add_argument('--combat-entry', type=int, default=3124)
    parser.add_argument('--max-target-level', type=int, default=3)
    parser.add_argument('--quest-item-entry', type=int, default=4862)
    parser.add_argument('--combat-budget', type=float, default=35)
    parser.add_argument('--budget-seconds', type=float, default=240)
    parser.add_argument('--action-budget', type=int, default=10)
    parser.add_argument('--boundary-max-age-seconds', type=float, default=1800)
    args = parser.parse_args()
    unit_args = {'combat_entry': args.combat_entry, 'max_target_level': args.max_target_level,
                 'quest_item_entry': args.quest_item_entry, 'combat_budget': args.combat_budget,
                 'budget_seconds': args.budget_seconds, 'action_budget': args.action_budget,
                 'boundary_max_age_seconds': args.boundary_max_age_seconds}

    def check_no_active_session():
        pattern = ACTIVE_PROCESS_PATTERN.replace("'", r"'\''")
        # Print a marker before the check. A later marker means pgrep itself saw
        # a task process; a marker without a match leaves only the two markers.
        command = (f"printf '__pgrep_start__\\n'; "
                   f"pgrep -fa '{pattern}' | grep -v -- '--pgrep-check-only' || true; "
                   f"printf '__pgrep_done__\\n'")
        if shutil.which('wsl'):
            active = subprocess.run(['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--exec', 'sh', '-c',
                                     command, '--pgrep-check-only'], capture_output=True, text=True)
            if active.returncode != 0:
                raise RuntimeError('process occupancy check failed')
            if '__pgrep_done__' not in active.stdout:
                raise RuntimeError('process occupancy check incomplete')
            body = active.stdout.split('__pgrep_start__', 1)[-1].split('__pgrep_done__', 1)[0]
        else:
            # Native WSL coordinator: pgrep sees our own sh -c here, so drop any
            # line carrying the check marker. The pattern itself is never echoed
            # by this command, so a surviving line can only be a real session.
            active = subprocess.run(['sh', '-c',
                                     f"pgrep -fa '{pattern}' | grep -v -- '--pgrep-check-only' || true"],
                                    capture_output=True, text=True)
            body = active.stdout
        matches = body.strip()
        if matches:
            raise RuntimeError('existing task or bridge session is active')

    def base_command(run_id):
        common = ['--run-id', run_id, '--combat-entry', str(args.combat_entry),
                  '--max-target-level', str(args.max_target_level),
                  '--quest-item-entry', str(args.quest_item_entry),
                  '--combat-budget', str(args.combat_budget),
                  '--budget-seconds', str(args.budget_seconds),
                  '--action-budget', str(args.action_budget)]
        if shutil.which('wsl'):
            drive, rest = str(ROOT)[0].lower(), str(ROOT)[2:].replace('\\', '/')
            return ['wsl', '-d', 'WoWLab', '-u', 'wowlab', '--exec',
                    'python3', f'/mnt/{drive}{rest}/scripts/wsl_task_runner.py', *common]
        return ['python3', str(ROOT / 'scripts' / 'wsl_task_runner.py'), *common]

    def run_phase(command, timeout, check_session=True):
        if check_session:
            check_no_active_session()
        subprocess.run(command, check=True, timeout=timeout,
                       encoding='utf-8', errors='replace')

    def launch(run_id, check_session=True):
        # A unit is two bounded sessions: a movement-only session that closes a
        # safe boundary, then a fresh verification session that confirms the
        # submitted endpoint on login and only then runs one combat. The server
        # never echoes the mover's own position in-session, so the approach leg
        # is confirmed across this session boundary, not inside one session.
        run_phase(base_command(run_id) + ['--movement-only'], 600, check_session)
        # A phase can end without a report (crash, bridge failure). Write a
        # minimal failed report so the movement leg always leaves an archive
        # and recovery has a uniform artifact to inspect.
        boundary = ROOT / 'results' / 'pure_network' / f'agent_wow_task_{run_id}.json'
        if not boundary.exists():
            write_failed = {'run_id': run_id, 'phase': 'movement_only', 'status': 'failed',
                            'error': 'movement-only phase exited without a report',
                            'run': {'status': 'failed', 'results': [], 'records': []},
                            'final_observation': {}, 'finalize': {}}
            boundary.write_text(json.dumps(write_failed, indent=2) + '\n', encoding='utf-8')
        run_phase(base_command(f'{run_id}_verify') + ['--verify-movement-from', str(boundary),
                                                      '--combat-after-verification',
                                                      '--boundary-max-age-seconds',
                                                      str(args.boundary_max_age_seconds)], 600,
                  check_session)

    # Cooperative lock for this coordinator; legacy launchers do not honor it.
    lock = ROOT / '.cache' / 'task_batch.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    try:
        with lock.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps({'batch_id': args.batch_id, 'pid': os.getpid(),
                                     'acquired_at': time.time()}) + '\n')
        acquired = True
    except FileExistsError:
        try:
            owner = json.loads(lock.read_text(encoding='utf-8'))
            if time.time() - float(owner.get('acquired_at', 0)) > 3600:
                lock.unlink()
                with lock.open('x', encoding='utf-8') as handle:
                    handle.write(json.dumps({'batch_id': args.batch_id, 'pid': os.getpid(),
                                             'acquired_at': time.time()}) + '\n')
                acquired = True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    if not acquired:
        raise RuntimeError('another task batch holds the lock')
    try:
        print(json.dumps(coordinate(ROOT / 'results' / 'pure_network', args.batch_id, args.limit, args.goal,
                                    launch, unit_args), indent=2))
    finally:
        if acquired:
            lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
