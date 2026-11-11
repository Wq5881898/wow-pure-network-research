"""Explicit level-seven experiment using verified one-to-three-unit batches.

No forced process timeout, no retries of failed gameplay units, no database writes.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from agent_wow_worldstate import replay

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def state_from_observation(run):
    observation = json.loads((FOLDER / f'agent_wow_huntobs_{run}.json').read_text())
    if (observation.get('status') != 'PASS' or observation.get('error')
            or (observation.get('session_ending') or {}).get('closed_after') != 'logout'):
        raise RuntimeError('observation did not safely finish')
    me = observation['self']
    if me['health'] <= 0 or me['ghost'] or me['in_combat'] is not False:
        raise RuntimeError('unsafe character state')
    trace = json.loads((FOLDER / f'agent_wow_bridge_{run}.packets.json').read_text())
    state = replay(trace)
    if state['errors']:
        raise RuntimeError('invalid observation capture')
    fields = {key: value for e in state['event_log'] if e.get('guid') == '9'
              for key, value in e.get('fields', {}).items()}
    used = sum(bool(fields.get(str(i), 0) | fields.get(str(i+1), 0) << 32)
               for i in range(370, 402, 2))
    return {'level': me['level'], 'xp': fields.get('634', 0),
            'next_xp': fields.get('635'), 'health': me['health'],
            'backpack_used': used, 'position': me['position'], 'source': run}


def main():
    run = sys.argv[1]
    skip_loot = '--skip-loot' in sys.argv[2:]
    return_to_farm = '--return-to-farm' in sys.argv[2:]
    def option(name, default):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
    target_level = int(option('--target-level', '7'))
    combat_entry = int(option('--entry', '3102'))
    max_level = int(option('--max-level', '4'))
    units = int(option('--units', '3'))
    cooldown = float(option('--cooldown', '0'))
    if not run.replace('_', '').isalnum():
        raise SystemExit('invalid run')
    path = FOLDER / f'grind_goal_{run}.json'
    if path.exists():
        raise SystemExit('run already exists')
    report = {'run_id': run, 'target_level': target_level, 'started_at': time.time(),
              'combat_entry': combat_entry, 'max_target_level': max_level,
              'status': 'running', 'events': []}
    def save():
        report['elapsed_seconds'] = round(time.time() - report['started_at'], 2)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(path)
    def execute(script, identity, *args):
        report['active'] = identity
        save()
        # Child entrypoints reject *any* artifact containing their run ID.
        # Console filenames must not pre-create one under that identity.
        with (FOLDER / f'goal_console_{run}_{len(report["events"])}_{time.monotonic_ns()}.log').open('x') as out:
            process = subprocess.Popen([sys.executable, '-B', f'scripts/{script}', *args],
                                       cwd=ROOT, stdout=out, stderr=out)
            report['active_pid'] = process.pid
            save()
            code = process.wait()  # Owner may still be supervising; never kill it.
        if code != 0:
            raise RuntimeError(f'{identity} exited {code}')
    def observe(identity):
        execute('agent_wow_hunt_observe.py', identity, '--run-id', identity,
                '--target-entry', str(combat_entry), '--max-level', str(max_level), '--seconds', '2')
        state = state_from_observation(identity)
        report['current'] = state
        save()
        return state
    save()
    try:
        current = observe(run + '_start')
        report['initial'] = current
        report['skip_loot'] = skip_loot
        if return_to_farm:
            identity = run + '_return'
            execute('agent_wow_travel_run.py', identity, '--run-id', identity,
                    '--goal', '-142.3', '-4370.2', '67', '--legs', '30',
                    '--radius', '30', '--stop-distance', '18')
            travel = json.loads((FOLDER / f'agent_wow_travel_{identity}.json').read_text())
            if travel['status'] != 'PASS':
                raise RuntimeError('initial return failed')
        stagnant = 0
        for index in range(1, 161):
            if current['level'] >= target_level:
                report['status'] = 'target_level_reached'
                break
            if current['backpack_used'] >= 13 and not skip_loot:
                identity = f'{run}_s{index:02d}'
                execute('agent_wow_travel_run.py', identity + 'out', '--run-id', identity + 'out',
                        '--goal', '-581.734', '-4109.51', '43.608', '--legs', '30',
                        '--radius', '30', '--stop-distance', '3')
                travel = json.loads((FOLDER / f'agent_wow_travel_{identity}out.json').read_text())
                if travel['status'] != 'PASS':
                    raise RuntimeError('resupply travel failed')
                execute('sell_gray_once.py', identity + 'sell', identity + 'sell', '3160')
                sale = json.loads((FOLDER / f'vendor_sale_{identity}sell.json').read_text())
                if sale['status'] != 'sales_verified' or sale.get('error'):
                    raise RuntimeError('resupply sale unconfirmed')
                current = observe(identity + 'check')
                if current['backpack_used'] >= 13:
                    raise RuntimeError('junk sale insufficient; retained equipment needs review')
                execute('agent_wow_travel_run.py', identity + 'back', '--run-id', identity + 'back',
                        '--goal', '-142.3', '-4370.2', '67', '--legs', '30',
                        '--radius', '30', '--stop-distance', '18')
                travel = json.loads((FOLDER / f'agent_wow_travel_{identity}back.json').read_text())
                if travel['status'] != 'PASS':
                    raise RuntimeError('return from resupply failed')
                report['events'].append({'kind': 'resupply', 'id': identity})
            identity = f'{run}_b{index:02d}'
            execute('agent_wow_quest_batch.py', identity, '--batch-id', identity,
                    '--combat-entry', str(combat_entry), '--max-target-level', str(max_level), '--units', str(units),
                    '--combat-budget', '60', '--objective-optional', '--stop-level', str(target_level),
                    *(['--skip-loot'] if skip_loot else []))
            batch = json.loads((FOLDER / f'agent_wow_quest_batch_{identity}.json').read_text())
            if batch['status'] not in ('batch_complete', 'target_level_reached'):
                raise RuntimeError('batch stopped: ' + batch['status'])
            updated = state_from_observation(identity + '_final')
            stagnant = stagnant + 1 if (updated['level'], updated['xp']) == (current['level'], current['xp']) else 0
            report['events'].append({'kind': 'batch', 'id': identity,
                                     'units': len(batch['units']), 'state': updated})
            current = updated
            report['current'] = current
            save()
            if stagnant >= 2:
                raise RuntimeError('two batches without XP progress')
            if current['level'] < target_level and cooldown:
                report['active'] = 'waiting_for_respawn'
                save()
                time.sleep(cooldown)
        else:
            report['status'] = 'batch_limit_reached'
        if current['level'] >= target_level:
            report['status'] = 'target_level_reached'
    except Exception as exc:
        report['status'] = 'stopped_for_reconciliation'
        report['error'] = f'{type(exc).__name__}: {exc}'
    report.pop('active_pid', None)
    report['finished_at'] = time.time()
    save()


if __name__ == '__main__':
    main()
