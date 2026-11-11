"""A bounded batch of quest kill units.

docs/52 sets the batch discipline: at most three task units per batch, with
the cumulative progress, the failure budget and the final state verified. This
runs that batch for a kill-count objective:

  for each unit
    1. a fresh read-only login to confirm the character is alive and out of
       combat, and to read the current objective count;
    2. a movement-only unit that produces the boundary;
    3. the combat unit that kills one target;
    4. a read-only login to confirm the count actually advanced.

The batch stops at the first failure, the first death, or the unit limit.
It never re-dispatches a unit whose outcome is unknown: a unit that ends
without a confirmed counter increase ends the batch for reconciliation.

The units are dispatched through the production runner, so the evidence is
the same evidence the project normally produces.
"""
import argparse
import json
import subprocess
import sys
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'scripts' / 'wsl_task_runner.py'
FOLDER = ROOT / 'results/pure_network'


def _wait_for_owner(command, *, window_seconds, on_timeout=None):
    """Wait without killing a child that may still own a gameplay session.

    Output goes to temporary files so a prolonged recovery cannot block on a
    pipe or grow the parent's memory without limit. Only bounded tails are
    returned. Expiry is diagnostic, never authorization to terminate.
    """
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=str(ROOT), stdout=stdout, stderr=stderr)
        expired = 0
        while True:
            try:
                code = process.wait(timeout=window_seconds)
                break
            except subprocess.TimeoutExpired:
                expired += 1
                if on_timeout is not None:
                    try:
                        on_timeout({'pid': process.pid, 'windows_expired': expired,
                                    'status': 'waiting_for_session_owner'})
                    except OSError:
                        # A failed diagnostic write cannot release ownership.
                        continue
        def tail(handle):
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 65536))
            return handle.read().decode('utf-8', errors='replace')
        return subprocess.CompletedProcess(command, code, tail(stdout), tail(stderr))


def _run(argv):
    run_id = argv[argv.index('--run-id') + 1]
    return _wait_for_owner([sys.executable, str(RUNNER), *argv], window_seconds=900,
                           on_timeout=lambda state: _record_wait(run_id, state))


def _record_wait(run_id, state):
    path = FOLDER / f'agent_wow_owner_wait_{run_id}.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
    temporary.replace(path)


def _observe(run_id, target_entry, max_level, seconds=6.0):
    """A read-only login: the character's state and the objective counters."""
    script = ROOT / 'scripts' / 'agent_wow_hunt_observe.py'
    result = _wait_for_owner([sys.executable, str(script), '--run-id', run_id,
                              '--target-entry', str(target_entry),
                              '--max-level', str(max_level),
                              '--seconds', str(seconds)],
                             window_seconds=300,
                             on_timeout=lambda state: _record_wait(run_id, state))
    report = FOLDER / f'agent_wow_huntobs_{run_id}.json'
    if not report.exists():
        return None
    data = json.loads(report.read_text(encoding='utf-8'))
    if result.returncode != 0 or data.get('status') != 'PASS':
        data['error'] = data.get('error') or 'observation did not finish with accepted logout'
    return data


def _counter(report, quest_id=None, kind='kill', entry=None, slot=0):
    """The objective counter of the quest this batch is working on.

    A kill objective counts the quest log; an ITEM objective counts the item
    in the bag, because that is where a collecting quest's progress lives
    (quest 794 collects item 4859 and its quest log counters stayed at 0
    while the item was already in the bag, 2026-11-01 q794e).

    The quest has to be picked by id: a character can carry more than one
    quest, and the first one with counters is not necessarily the one the
    batch is about.
    """
    if kind == 'item':
        totals = (report.get('self') or {}).get('item_totals')
        if not isinstance(totals, dict) or type(entry) is not int or entry <= 0:
            return None
        value = totals.get(str(entry), 0)
        return value if type(value) is int and value >= 0 else None
    if quest_id is None:
        return None
    from agent_wow_objective import progress
    return progress(report.get('self') or {},
                    {'kind': kind, 'quest': quest_id, 'slot': slot})


def _safe(report):
    """True when the character is alive, not a ghost and out of combat."""
    if not report:
        return False
    if report.get('error'):
        return False
    me = report.get('self') or {}
    health = me.get('health')
    return (me.get('ghost') is False and me.get('in_combat') is False
            and isinstance(health, int) and health > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--quest-id', type=int, default=None,
                        help='the quest whose counter is the objective; '
                             'omit it for a run whose purpose is not progress '
                             '(the quest may already have been handed in)')
    parser.add_argument('--kill-slot', type=int, choices=range(4), default=0)
    parser.add_argument('--objective-total', type=int, default=8)
    parser.add_argument('--combat-entry', type=int, default=3101)
    parser.add_argument('--max-target-level', type=int, default=3)
    parser.add_argument('--units', type=int, default=3,
                        help='task units in this batch (docs/52 allows at most 3)')
    parser.add_argument('--combat-budget', type=float, default=60)
    parser.add_argument('--low-health-ratio', type=float, default=None,
                        help='the health ratio at which the fight latches the '
                             'low-health exit; the escape branch then decides '
                             'how the combat ends')
    parser.add_argument('--objective-kind', choices=('kill', 'item'), default='kill',
                        help='kill counts the quest log counters, item counts '
                             'the quest item in the bag; a collecting quest '
                             'has a quest id too (quest 794 collects item 4859)')
    parser.add_argument('--quest-item-entry', type=int, default=4862,
                        help='the item the collecting objective needs')
    parser.add_argument('--objective-optional', action='store_true',
                        help='the objective counter does not have to advance; '
                              'for a run whose purpose is experience')
    parser.add_argument('--stop-level', type=int, default=None,
                        help='stop opening new units once this level is observed')
    parser.add_argument('--skip-loot', action='store_true')
    args = parser.parse_args()
    if not args.batch_id.replace('_', '').isalnum():
        parser.error('invalid batch ID')
    if not 1 <= args.units <= 3:
        parser.error('a batch is at most three task units (docs/52)')
    if args.objective_total <= 0 or args.quest_item_entry <= 0:
        parser.error('objective total and item entry must be positive')

    report = {'batch_id': args.batch_id, 'quest_id': args.quest_id,
              'objective_total': args.objective_total, 'units': [], 'status': 'unknown'}
    try:
        before = _observe(args.batch_id + '_pre', args.combat_entry,
                          args.max_target_level)
        report['before'] = {'safe': _safe(before),
                            'counter': _counter(before or {}, args.quest_id, args.objective_kind,
                        args.quest_item_entry, args.kill_slot),
                            'health': ((before or {}).get('self') or {}).get('health')}
        if not before or before.get('error'):
            report['error'] = 'the pre-batch read-only login failed'
            raise SystemExit(1)
        if not report['before']['safe']:
            report['error'] = 'the character is not alive and out of combat'
            raise SystemExit(1)
        counter = report['before']['counter']
        if not isinstance(counter, int):
            if args.quest_id is None:
                # No quest to count: the run's purpose is not progress.
                counter = 0
            else:
                report['error'] = 'the objective counter is not readable'
                raise SystemExit(1)

        for index in range(args.units):
            if (args.stop_level is not None and
                    ((before or {}).get('self') or {}).get('level', 0) >= args.stop_level):
                report['status'] = 'target_level_reached'
                break
            if args.quest_id is not None and counter >= args.objective_total:
                report['status'] = 'objective_reached'
                break
            unit_id = f'{args.batch_id}_u{index + 1}'
            unit = {'unit': index + 1, 'run_id': unit_id, 'counter_before': counter}

            move = _run(['--run-id', unit_id + 'move', '--movement-only',
                         '--combat-entry', str(args.combat_entry),
                         '--max-target-level', str(args.max_target_level),
                         '--budget-seconds', '200'])
            boundary = FOLDER / f'agent_wow_task_{unit_id}move.json'
            if move.returncode != 0 or not boundary.exists():
                unit['failed'] = 'the movement unit produced no report'
                unit['stderr'] = (move.stderr or '')[-300:]
                report['units'].append(unit)
                report['status'] = 'failed_at_movement'
                break
            unit['movement'] = json.loads(boundary.read_text(encoding='utf-8'))
            # Validate the boundary here: a movement unit that never reached
            # an approach step (no target in view) has no boundary, and the
            # combat unit would fail while reading it.
            try:
                from agent_wow_task_boundary import movement_boundary
                movement_boundary(unit['movement'], max_age_seconds=1800)
            except Exception as exc:  # noqa: BLE001
                unit['failed'] = 'the movement unit produced no usable boundary: %s' % exc
                report['units'].append(unit)
                report['status'] = 'failed_at_movement'
                break

            fight = _run(['--run-id', unit_id,
                          '--verify-movement-from', str(boundary),
                          '--combat-after-verification', '--advisory-record',
                          '--kill-slot', str(args.kill_slot),
                          '--objective-total', str(args.objective_total),
                          '--combat-entry', str(args.combat_entry),
                          '--max-target-level', str(args.max_target_level),
                          '--combat-budget', str(args.combat_budget),
                          '--budget-seconds', '300', '--action-budget', '10',
                          '--objective-kind', args.objective_kind,
                           '--quest-item-entry', str(args.quest_item_entry)]
                           + ([] if args.quest_id is None else ['--quest-id', str(args.quest_id)])
                           + (['--objective-optional'] if args.objective_optional else [])
                           + (['--skip-loot'] if args.skip_loot else [])
                          + ([] if args.low_health_ratio is None else
                             ['--low-health-ratio', str(args.low_health_ratio)]))
            unit_report = FOLDER / f'agent_wow_task_{unit_id}.json'
            if not unit_report.exists():
                unit['failed'] = 'the combat unit produced no report'
                unit['stderr'] = (fight.stderr or '')[-300:]
                report['units'].append(unit)
                report['status'] = 'failed_at_combat'
                break
            unit['combat'] = json.loads(unit_report.read_text(encoding='utf-8'))
            records = (unit['combat'].get('run') or {}).get('records') or []
            unit['records'] = [(r.get('action_id'), r.get('status')) for r in records]
            if (fight.returncode != 0 or unit['combat'].get('error')
                    or (unit['combat'].get('run') or {}).get('status') != 'success'):
                unit['failed'] = 'combat runner failed; reconcile before another login'
                report['units'].append(unit)
                report['status'] = 'failed_at_combat'
                break

            after = _observe(unit_id + '_post', args.combat_entry,
                             args.max_target_level)
            unit['after'] = {'safe': _safe(after),
                              'counter': _counter(after or {}, args.quest_id, args.objective_kind,
                    args.quest_item_entry, args.kill_slot),
                              'health': ((after or {}).get('self') or {}).get('health')}
            report['units'].append(unit)
            new_counter = unit['after']['counter']
            if not unit['after']['safe']:
                report['status'] = 'the character is not safe after the unit'
                break
            if (not args.objective_optional
                    and (args.quest_id is not None or args.objective_kind == 'item')
                    and (not isinstance(new_counter, int) or new_counter <= counter)):
                report['status'] = 'the objective counter did not advance'
                break
            counter = new_counter if isinstance(new_counter, int) else counter
            unit['counter_after'] = new_counter
            before = after
        else:
            report['status'] = 'batch_complete'

        after = _observe(args.batch_id + '_final', args.combat_entry,
                          args.max_target_level)
        report['final'] = {'safe': _safe(after),
                           'counter': _counter(after or {}, args.quest_id, args.objective_kind,
                    args.quest_item_entry, args.kill_slot),
                           'health': ((after or {}).get('self') or {}).get('health'),
                           'quest_log': (after or {}).get('self', {}).get('quest_log')}
    except SystemExit:
        raise
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        with (FOLDER / f'agent_wow_quest_batch_{args.batch_id}.json').open(
                'x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
