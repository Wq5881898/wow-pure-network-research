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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'scripts' / 'wsl_task_runner.py'
FOLDER = ROOT / 'results/pure_network'


def _run(argv):
    return subprocess.run([sys.executable, str(RUNNER), *argv],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=900)


def _observe(run_id, target_entry, max_level, seconds=6.0):
    """A read-only login: the character's state and the objective counters."""
    script = ROOT / 'scripts' / 'agent_wow_hunt_observe.py'
    result = subprocess.run([sys.executable, str(script), '--run-id', run_id,
                              '--target-entry', str(target_entry),
                              '--max-level', str(max_level),
                              '--seconds', str(seconds)],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    report = FOLDER / f'agent_wow_huntobs_{run_id}.json'
    if not report.exists():
        return None
    return json.loads(report.read_text(encoding='utf-8'))


def _counter(report, quest_id=None):
    """The objective counter of the quest this batch is working on.

    The quest has to be picked by id: a character can carry more than one
    quest, and the first one with counters is not necessarily the one the
    batch is about. For a collecting quest the server counts the item in the
    quest log too, so the same counter answers both kinds.
    """
    log = (report.get('self') or {}).get('quest_log') or []
    for quest in log:
        if quest_id is not None and quest.get('quest') != quest_id:
            continue
        counters = quest.get('counters') or []
        if counters:
            return counters[0]
    return None


def _safe(report):
    """True when the character is alive, not a ghost and out of combat."""
    if not report:
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
    parser.add_argument('--kill-slot', type=int, default=0)
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
    args = parser.parse_args()
    if not args.batch_id.replace('_', '').isalnum():
        parser.error('invalid batch ID')
    if args.units > 3:
        parser.error('a batch is at most three task units (docs/52)')

    report = {'batch_id': args.batch_id, 'quest_id': args.quest_id,
              'objective_total': args.objective_total, 'units': [], 'status': 'unknown'}
    try:
        before = _observe(args.batch_id + '_pre', args.combat_entry,
                          args.max_target_level)
        report['before'] = {'safe': _safe(before),
                            'counter': _counter(before or {}, args.quest_id),
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
            if counter >= args.objective_total:
                report['status'] = 'objective_reached'
                break
            unit_id = f'{args.batch_id}_u{index + 1}'
            unit = {'unit': index + 1, 'run_id': unit_id, 'counter_before': counter}

            move = _run(['--run-id', unit_id + 'move', '--movement-only',
                         '--combat-entry', str(args.combat_entry),
                         '--max-target-level', str(args.max_target_level),
                         '--budget-seconds', '200'])
            boundary = FOLDER / f'agent_wow_task_{unit_id}move.json'
            if not boundary.exists():
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
                          '--quest-id', str(args.quest_id),
                          '--kill-slot', str(args.kill_slot),
                          '--objective-total', str(args.objective_total),
                          '--combat-entry', str(args.combat_entry),
                          '--max-target-level', str(args.max_target_level),
                          '--combat-budget', str(args.combat_budget),
                          '--budget-seconds', '300', '--action-budget', '10',
                          '--objective-kind', args.objective_kind,
                          '--quest-item-entry', str(args.quest_item_entry)]
                          + (['--objective-optional'] if args.objective_optional else [])
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

            after = _observe(unit_id + '_post', args.combat_entry,
                             args.max_target_level)
            unit['after'] = {'safe': _safe(after),
                              'counter': _counter(after or {}, args.quest_id),
                              'health': ((after or {}).get('self') or {}).get('health')}
            report['units'].append(unit)
            new_counter = unit['after']['counter']
            if not unit['after']['safe']:
                report['status'] = 'the character is not safe after the unit'
                break
            if (not args.objective_optional and args.quest_id is not None
                    and (not isinstance(new_counter, int) or new_counter <= counter)):
                report['status'] = 'the objective counter did not advance'
                break
            counter = new_counter
            unit['counter_after'] = new_counter
        else:
            report['status'] = 'batch_complete'

        after = _observe(args.batch_id + '_final', args.combat_entry,
                          args.max_target_level)
        report['final'] = {'safe': _safe(after),
                           'counter': _counter(after or {}, args.quest_id),
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
