"""Grind experience with a full record of what it cost.

The run logs, per level: how many mobs were killed, how many times the
character died, and how long it took. The point is not only to reach the
target level but to have the numbers for the next decision (which mob, which
zone, whether the experience rate should be changed).

  python scripts/leveling_run.py --goal X Y Z --entry 3125 --max-level 6 \
      --target-level 7 [--log results/pure_network/leveling_log.json]

The run is defensive on purpose: it revives a dead character, retries the
transient bridge "capture gap", and stops when the experience stops moving
instead of looping forever.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results' / 'pure_network'
CONFIG = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\configs\worldserver.conf')
MYSQL = r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe'


def database_settings():
    for line in CONFIG.read_text(encoding='utf-8', errors='ignore').splitlines():
        if line.strip().startswith('CharacterDatabaseInfo'):
            parts = line.split('"')[1].split(';')
            return parts[0], parts[1], parts[2], parts[3], parts[4]
    raise RuntimeError('no CharacterDatabaseInfo in the world server config')


def character_state():
    """(level, xp, health) straight from the character table."""
    host, port, user, password, database = database_settings()
    query = 'select level, xp, health from `%s`.characters where guid=9' % database
    env = dict(os.environ, MYSQL_PWD=password)
    run = subprocess.run([MYSQL, '-h', host, '-P', port, '-u', user, '-N', '-B',
                          '-e', query], capture_output=True, text=True,
                         env=env, timeout=60)
    parts = run.stdout.strip().split('\t')
    if len(parts) < 3:
        return None
    return {'level': int(parts[0]), 'xp': int(parts[1]), 'health': int(parts[2])}


def run(command, timeout=1800):
    return subprocess.run(command, cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout)


def alive_state():
    """The character's live state through the bridge (ghost, combat, health)."""
    script = Path('/mnt/c/Users/qrz/AppData/Local/Temp/opencode/esc010_check.py')
    result = subprocess.run(['wsl', '-d', 'WoWLab', '--', 'bash', '-lc',
                             'cd %s && PYTHONPATH=scripts python3 %s'
                             % (ROOT, script)],
                            capture_output=True, text=True, timeout=300)
    text = result.stdout
    ghost = '"ghost": true' in text
    return {'ghost': ghost, 'raw': text[-400:]}


def revive(run_id):
    script = Path('/mnt/c/Users/qrz/AppData/Local/Temp/opencode/recover.py')
    result = subprocess.run(['wsl', '-d', 'WoWLab', '--', 'bash', '-lc',
                             'cd %s && PYTHONPATH=scripts python3 %s'
                             % (ROOT, script)],
                            capture_output=True, text=True, timeout=600)
    return '"status": "revived"' in result.stdout


def count_kills(batch_report):
    """How many targets actually died in one batch."""
    report = json.loads(batch_report.read_text(encoding='utf-8'))
    kills = 0
    for unit in report.get('units') or []:
        combat = unit.get('combat') or {}
        for record in (combat.get('run') or {}).get('records', []):
            evidence = record.get('evidence') or {}
            if record.get('action_id') == 'fight' and evidence.get('outcome') == 'target_dead':
                kills += 1
    return kills


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal', type=float, nargs=3, required=True)
    parser.add_argument('--entry', type=int, required=True)
    parser.add_argument('--max-level', type=int, required=True)
    parser.add_argument('--target-level', type=int, required=True)
    parser.add_argument('--radius', type=float, default=45.0)
    parser.add_argument('--stop-distance', type=float, default=20.0)
    parser.add_argument('--units', type=int, default=3)
    parser.add_argument('--sleep', type=float, default=220.0)
    parser.add_argument('--max-rounds', type=int, default=200)
    parser.add_argument('--log', default=str(FOLDER / 'leveling_log.json'))
    options = parser.parse_args()
    log_path = Path(options.log)
    FOLDER.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime('%H%M%S')
    state = character_state()
    log = {'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
           'target_level': options.target_level,
           'farm': {'entry': options.entry, 'goal': options.goal,
                    'max_level': options.max_level},
           'levels': [], 'totals': {'kills': 0, 'deaths': 0, 'seconds': 0.0},
           'events': []}
    if state:
        log['start_state'] = state
        log['levels'].append({'level': state['level'], 'xp_start': state['xp'],
                              'xp_end': None, 'kills': 0, 'deaths': 0,
                              'seconds': 0.0})
    log_path.write_text(json.dumps(log, indent=2), encoding='utf-8')

    def flush():
        log_path.write_text(json.dumps(log, indent=2), encoding='utf-8')

    def note(kind, **fields):
        entry = {'at': time.strftime('%H:%M:%S'), 'kind': kind}
        entry.update(fields)
        log['events'].append(entry)
        flush()

    def current():
        level_log = log['levels'][-1]
        state = character_state()
        if state:
            if state['level'] != level_log['level']:
                log['levels'].append({'level': state['level'], 'xp_start': state['xp'],
                                      'xp_end': None, 'kills': 0, 'deaths': 0,
                                      'seconds': 0.0})
                level_log = log['levels'][-1]
            level_log['xp_end'] = state['xp']
        return state

    round_index = 0
    while round_index < options.max_rounds:
        round_index += 1
        state = current()
        if state and state['level'] >= options.target_level:
            note('target_reached', level=state['level'], xp=state['xp'])
            break
        live = alive_state()
        if live['ghost']:
            level_log = log['levels'][-1]
            level_log['deaths'] += 1
            log['totals']['deaths'] += 1
            note('death', level=level_log['level'])
            if revive('leveling'):
                note('revived')
            else:
                note('revive_failed')
                time.sleep(10)
            continue
        began = time.time()
        batch_id = 'lvl%s_%d' % (stamp, round_index)
        run(['wsl', '-d', 'WoWLab', '--', 'bash', '-lc',
             'cd %s && timeout 900 python3 scripts/agent_wow_quest_batch.py '
             '--batch-id %s --kill-slot 0 --objective-total 1 --combat-entry %d '
             '--max-target-level %d --units %d --combat-budget 90 '
             '--objective-optional' % (ROOT, batch_id, options.entry,
                                       options.max_level, options.units)],
            timeout=1200)
        report = FOLDER / ('agent_wow_quest_batch_%s.json' % batch_id)
        kills = 0
        if report.exists():
            try:
                kills = count_kills(report)
            except Exception as exc:  # noqa: BLE001
                note('batch_unreadable', error=str(exc))
        level_log = log['levels'][-1]
        level_log['kills'] += kills
        log['totals']['kills'] += kills
        level_log['seconds'] += time.time() - began
        log['totals']['seconds'] += time.time() - began
        note('round', round=round_index, kills=kills,
             level=level_log['level'], xp=(character_state() or {}).get('xp'))
        flush()
        time.sleep(options.sleep)
    flush()
    print(json.dumps({'levels': log['levels'], 'totals': log['totals']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
