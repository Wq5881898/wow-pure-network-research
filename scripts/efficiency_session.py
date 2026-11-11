"""Thirty-minute same-session baseline; no fixed respawn sleeps or per-kill login."""
import argparse
import json
import math
import time
from pathlib import Path

from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session
from agent_wow_combat_control import estimated_position
from agent_wow_travel import prepare_return, travel
from agent_wow_attackers import planned_attacker

ROOT = Path(__file__).resolve().parents[1]


def player_progress(state):
    fields = {}
    for event in state['event_log']:
        if event.get('guid') == '9':
            fields.update(event.get('fields', {}))
    me = state['self']
    return {'level': me['level'], 'xp': fields.get('634', 0),
            'next_xp': fields.get('635'), 'health': me['health'],
            'max_health': me['max_health'], 'in_combat': me['in_combat'],
             'ghost': me['ghost']}


def reconcile_selection(backend, selected, entry, minimum, maximum):
    """Navigation is not an atomic selection: rebind only from attack events."""
    state = backend.drain()
    if state['self'].get('in_combat') is True:
        selected = planned_attacker(state, backend.self_guid, entry, minimum, maximum)
        if selected is None:
            raise RuntimeError('contact changed during planning; recovery required')
    else:
        selected = next((o for o in state['visible_objects']
                         if o['guid'] == selected['guid'] and o.get('health', 0) > 0), None)
        if selected is None:
            raise RuntimeError('selected target disappeared during planning')
    backend.target = selected
    return selected, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--entry', type=int, default=3102)
    parser.add_argument('--min-level', type=int, default=1)
    parser.add_argument('--max-level', type=int, default=4)
    parser.add_argument('--stop-level', type=int)
    parser.add_argument('--hunt-goal', type=float, nargs=3)
    parser.add_argument('--return-goal', type=float, nargs=3)
    parser.add_argument('--hunt-stop-distance', type=float, default=30)
    parser.add_argument('--max-target-wait', type=float, default=60)
    parser.add_argument('--patrol-plan', type=Path)
    parser.add_argument('--escape-anchor', type=float, nargs=3, action='append', default=[])
    parser.add_argument('--return-relative', action='store_true')
    args = parser.parse_args()
    if (not args.run_id.replace('_', '').isalnum() or args.seconds <= 0
            or not 1 <= args.min_level <= args.max_level):
        parser.error('invalid run or duration')
    folder = ROOT / 'results/pure_network'
    path = folder / f'efficiency_{args.run_id}.json'
    if list(folder.glob(f'*{args.run_id}*')):
        raise SystemExit('run artifacts exist')
    backend = PureNetworkBackend(args.run_id)
    backend.escape_safe_anchors = args.escape_anchor
    report = {'run_id': args.run_id, 'scope': 'single session timed leveling',
              'entry': args.entry, 'min_target_level': args.min_level,
              'max_target_level': args.max_level, 'stop_level': args.stop_level,
              'preparation_started_at': time.time(),
              'requested_seconds': args.seconds, 'status': 'preparing', 'actions': [],
              'timings': {'setup': 0, 'selection': 0, 'fight_and_approach': 0,
                          'recovery_wait': 0, 'target_wait': 0, 'teardown': 0},
              'kills': 0, 'minimum_health': None, 'skip_loot': True}
    began = time.monotonic()
    patrol = json.loads(args.patrol_plan.read_text())['points'][1:] if args.patrol_plan else []
    patrol_index = 0
    empty_patrol_stops = 0
    def save(data=None):
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(path)
    try:
        backend.open()
        # Explicit bounded budget for this 30-minute experiment. Existing
        # short-unit default remains unchanged; never discard packet history.
        backend.incremental.max_packets = 100000
        state = backend.drain()
        report['initial'] = player_progress(state)
        if args.hunt_goal:
            me = state['self']
            if me['in_combat'] is not False or me['ghost'] or me['health'] < me['max_health']:
                raise RuntimeError('unsafe departure; no travel authorized')
            report['outbound'] = {}
            def handoff(state):
                target = planned_attacker(state, backend.self_guid, args.entry,
                                          max(1, args.min_level-1), args.max_level)
                if target is None or not getattr(backend, 'travel_return_route', None):
                    return False
                backend.target = target
                return True
            result = travel(backend, report=report['outbound'], goal=args.hunt_goal,
                            legs=80, radius=20, stop_distance=args.hunt_stop_distance,
                            combat_handoff=handoff)
            if not result['reached_goal_area'] and not report['outbound'].get('combat_handoff'):
                raise RuntimeError('hunt area not reached; no timed combat started')
            state = backend.drain()
        report['timed_initial'] = player_progress(state)
        report['timings']['setup'] = time.monotonic() - began
        started = time.monotonic()
        deadline = started + args.seconds
        report.update(status='running', started_at=time.time(), session_id=backend.session_id)
        rejected = set()
        rejection_expires = {}
        idle_since = None
        while time.monotonic() < deadline:
            state = backend.drain()
            me = state['self']
            report['current'] = player_progress(state)
            report['experiment_elapsed'] = time.monotonic() - started
            if args.stop_level is not None and me['level'] >= args.stop_level:
                report['status'] = 'target_level_reached'
                break
            hp = me.get('health')
            if hp is not None:
                report['minimum_health'] = min(report['minimum_health'] or hp, hp)
            engaged = planned_attacker(state, backend.self_guid, args.entry,
                                       max(1, args.min_level-1), args.max_level)
            if (hp is None or hp <= 0 or me['ghost']
                    or (me['in_combat'] is not False and engaged is None)):
                raise RuntimeError('life/combat state requires recovery; no new unit')
            if hp < me['max_health'] and engaged is None:
                tick = time.monotonic()
                time.sleep(.5)
                report['timings']['recovery_wait'] += time.monotonic() - tick
                report['current'] = player_progress(state)
                save()
                continue
            tick = time.monotonic()
            for guid, expiry in list(rejection_expires.items()):
                if tick >= expiry:
                    rejected.discard(guid)
                    del rejection_expires[guid]
            here = list((backend.submitted_position or backend.observed_position)[:3])
            candidates = [o for o in state['visible_objects'] if o.get('entry') == args.entry
                           and o.get('health', 0) > 0
                           and args.min_level <= o.get('level', 0) <= args.max_level
                          and o['guid'] not in rejected]
            candidates.sort(key=lambda o: math.dist(
                here, estimated_position(o, backend.stream_now())[0][:3]))
            selected = None
            if engaged is not None:
                candidates = [engaged]
            for candidate in candidates:
                if engaged is not None:
                    selected = candidate
                    break
                position = estimated_position(candidate, backend.stream_now())[0][:3]
                if math.dist(here, position) > 45:
                    continue
                try:
                    raw = backend._nav(here, position)
                    points = backend._densify(raw, here, slope_tolerance=.7)
                    _, reverse = prepare_return(backend, [here], points)
                except RuntimeError:
                    rejected.add(candidate['guid'])
                    rejection_expires[candidate['guid']] = time.monotonic() + 15
                    continue
                selected = candidate
                break
            report['timings']['selection'] += time.monotonic() - tick
            if selected is None:
                if idle_since is None:
                    idle_since = time.monotonic()
                report['selection_diagnostics'] = {
                    'eligible_visible': len(candidates), 'temporarily_rejected': len(rejected),
                    'continuous_wait_seconds': time.monotonic()-idle_since,
                    'visible_scorp_levels': [o.get('level') for o in state['visible_objects']
                                            if o.get('entry') == args.entry and o.get('health',0)>0]}
                if patrol and time.monotonic()-idle_since >= 8:
                    if empty_patrol_stops >= len(patrol):
                        report['status'] = 'patrol_no_targets_returning'
                        save()
                        break
                    destination = patrol[patrol_index % len(patrol)]
                    patrol_index += 1
                    empty_patrol_stops += 1
                    move_report = {'destination':destination}
                    report.setdefault('patrol_moves',[]).append(move_report)
                    def patrol_handoff(snapshot):
                        return planned_attacker(snapshot, backend.self_guid, args.entry,
                                                max(1,args.min_level-1),args.max_level) is not None
                    travel(backend,report=move_report,goal=destination,legs=15,radius=12,
                           stop_distance=18,combat_handoff=patrol_handoff)
                    idle_since = None
                    save()
                    if (not move_report.get('combat_handoff')
                            and not move_report['travel']['reached_goal_area']):
                        raise RuntimeError('patrol leg incomplete; return for reconciliation')
                    continue
                if time.monotonic() - idle_since >= args.max_target_wait:
                    report['status'] = 'target_wait_limit_returning'
                    save()
                    break
                tick = time.monotonic()
                time.sleep(1)
                report['timings']['target_wait'] += time.monotonic() - tick
                report['current'] = player_progress(state)
                save()
                continue
            idle_since = None
            empty_patrol_stops = 0
            selected, state = reconcile_selection(backend, selected, args.entry,
                                                  max(1, args.min_level-1), args.max_level)
            engaged = selected if state['self'].get('in_combat') is True else None
            backend.target = selected
            if engaged is None:
                # A short approach is not a retreat. Ask the shared escape
                # planner for a fresh full-length route for this target.
                backend.travel_return_route = None
                backend._escape_route = None
                backend._escape_route_target = None
                escape = backend.escape_route(refresh=True)
                if not escape or not escape.get('points'):
                    raise RuntimeError('no full escape route; do not start new fight')
                backend.travel_return_route = escape
            backend.start_position = here
            # Selection/nav may have taken seconds. Drain before using its
            # target: a different creature can have engaged during planning.
            selected, state = reconcile_selection(backend, selected, args.entry,
                                                  max(1, args.min_level-1), args.max_level)
            before = player_progress(state)
            tick = time.monotonic()
            result = backend._do_fight({'budget': min(45, max(1, deadline-time.monotonic())),
                                         'engage_timeout': 1, 'attack_attempts': 1}, 75)
            elapsed = time.monotonic() - tick
            report['timings']['fight_and_approach'] += elapsed
            rows = (result.get('phase') or {}).get('observations') or []
            for row in rows:
                if row.get('self_health') is not None:
                    report['minimum_health'] = min(report['minimum_health'] or row['self_health'],
                                                   row['self_health'])
            state = backend.drain()
            after = player_progress(state)
            report['actions'].append({'guid': selected['guid'], 'entry': selected['entry'],
                'level': selected['level'], 'outcome': result.get('outcome'),
                'before': before, 'after': after, 'seconds': elapsed,
                 'start_sent': (result.get('phase') or {}).get('start_sent_once')})
            report['actions'][-1].update({k: result.get(k) for k in (
                'approach', 'attempts', 'reference_position', 'precondition_recovery')})
            report['actions'][-1]['fight_observations'] = rows
            report['current'] = after
            report['experiment_elapsed'] = time.monotonic() - started
            if result.get('outcome') != 'target_dead':
                save()
                if (result.get('outcome') == 'target_left_melee'
                        and after['in_combat'] is False and not after['ghost']
                        and after['health'] > 0):
                    rejected.add(selected['guid'])
                    continue  # Never chase; select a different independent target.
                raise RuntimeError('combat outcome unresolved; stop for reconciliation')
            report['kills'] += 1
            save()
        if report['status'] == 'running':
            report['status'] = 'duration_completed'
        report['experiment_elapsed'] = time.monotonic() - started
        report['final_before_logout'] = player_progress(backend.drain())
    except Exception as exc:
        report.update(status='stopped_for_reconciliation', error=f'{type(exc).__name__}: {exc}')
    finally:
        tick = time.monotonic()
        if (args.return_goal or args.return_relative) and backend.session_id:
            try:
                me = backend.drain()['self']
                if me['in_combat'] is False and not me['ghost'] and me['health'] > 0:
                    destination = args.return_goal
                    if args.return_relative:
                        here = list((backend.submitted_position or backend.observed_position)[:3])
                        history = getattr(backend, 'relative_safe_history', [])
                        destination = next((r['position'] for r in reversed(history)
                                            if time.time()-r['sampled_at'] <= 900
                                            and 20 <= math.dist(here,r['position']) <= 80), None)
                        if destination is None:
                            raise RuntimeError('no recent relative return point; explicit reconciliation required')
                    report['return'] = {}
                    report['return']['destination_semantics'] = 'recent observed clear endpoint' if args.return_relative else 'explicit goal'
                    travel(backend, report=report['return'], goal=destination,
                           legs=80, radius=20, stop_distance=3)
                    save()
            except Exception as exc:
                report['return_error'] = f'{type(exc).__name__}: {exc}'
        finish_session(backend, report=report, hold_ownership=True, checkpoint=save)
        report['timings']['teardown'] = time.monotonic() - tick
        report['total_seconds'] = time.monotonic() - began
        report['finished_at'] = time.time()
        save()


if __name__ == '__main__':
    main()
