"""Run the danger-zone corpse recovery for real, using the existing design.

docs/51 left this open: the recovery plan and the recovery controller were
callable functions, not wired into a full real recovery run. This is that
entry point.

The design it uses is unchanged:
  agent_wow_recovery_plan.select_recovery
      corpse -> graveyard navmesh route, reclaim point inside the server's
      reclaim radius but as far toward the graveyard as the radius allows.
  agent_wow_recovery_control.reclaim_and_retreat
      reclaim, confirm the ghost really cleared, and immediately run toward
      the graveyard with no sleep in between.

What the wiring adds:
  - the first leg. The ghost appears at the GRAVEYARD while the corpse is
    out in the hostile area, so the ghost walks the path in reverse, down
    from the graveyard to the reclaim point. Without it the path starts at
    the corpse and the bridge rejects it ("segment exceeds 15 yards").
  - the release. A character that has just died is still awaiting the
    release prompt and is not a ghost yet.
  - the bridge position check. A release teleports the ghost without telling
    the bridge, so the bridge would validate the path against the place the
    character fell at. The run therefore has to happen in a session that
    starts with the character already a ghost.
  - the verification that a normal session exit is NOT a successful revive
    (docs/43 run47): the ghost has to be observed cleared, and the final
    position has to be confirmed by an independent fresh login.
"""
import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from agent_wow_session import PureNetworkBackend
from agent_wow_recovery_plan import (bridge_position_is_stale, corpse_guid,
                                      select_recovery)
from agent_wow_recovery_control import corpse_from_events, reclaim_and_retreat

GRAVEYARD = [-634.635009765625, -4296.02978515625, 40.52539825439453]
ROOT = Path(__file__).resolve().parents[1]


def _run_id_ok(run_id):
    if not run_id.replace('_', '').isalnum():
        raise SystemExit('invalid run ID')
    return run_id


def _occupied():
    active = subprocess.run(['pgrep', '-x', 'agent-wow-cli'],
                             capture_output=True, text=True)
    if active.returncode != 1:
        raise SystemExit('active bridge or failed occupancy check')


def _drain_until(backend, predicate, timeout, interval=0.4):
    deadline = time.monotonic() + timeout
    state = backend.drain()
    while time.monotonic() < deadline:
        if predicate(state):
            return state
        time.sleep(interval)
        state = backend.drain()
    return state


def _make_a_corpse(backend, args, report):
    """Die in-session with the session open, so the corpse is reported.

    A death after the session was torn down is never captured: the client is
    gone before the corpse query would be answered.
    """
    state = _drain_until(
        backend, lambda s: [o for o in s['visible_objects']
                            if o.get('type_id') == 3 and o.get('entry') == args.combat_entry
                            and (o.get('health') or 0) > 0
                            and o.get('level', 99) <= args.max_level], 15.0)
    choices = [o for o in state['visible_objects']
               if o.get('type_id') == 3 and o.get('entry') == args.combat_entry
               and (o.get('health') or 0) > 0 and o.get('level', 99) <= args.max_level]
    if not choices:
        raise SystemExit('no visible target')
    pos = state['self']['movement']['position_xyzo'][:3]
    target = min(choices, key=lambda o: math.dist(
        pos, (o.get('movement') or {}).get('position_xyzo', pos)[:3]))
    report['target'] = {'guid': target['guid'], 'entry': target['entry'],
                        'distance': round(math.dist(
                            pos, (target.get('movement') or {}).get('position_xyzo', pos)[:3]), 2)}
    backend.target = target
    # Arrival can only be observed through the mob: the server never
    # broadcasts the character's own position.
    engaged = False
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline and not engaged:
        backend._do_approach({}, 90)
        walk_until = time.monotonic() + args.walk_seconds
        while time.monotonic() < walk_until:
            time.sleep(0.5)
            backend.drain()
        backend.rpc('bridge.start_attack', {'guid': target['guid']})
        attack_until = time.monotonic() + 12.0
        while time.monotonic() < attack_until:
            time.sleep(0.3)
            state = backend.drain()
            me = state['self']
            if me['health'] <= 0 or me['ghost']:
                break
            live = next((o for o in state['visible_objects']
                         if o['guid'] == target['guid']), None)
            if live and (live.get('health') or 0) < target['health']:
                engaged = True
                break
            if me['in_combat'] is True:
                engaged = True
                break
    report['engaged'] = engaged
    if not engaged:
        raise SystemExit('never reached the target')
    backend.rpc('bridge.stop_attack', {})
    deadline = time.monotonic() + 150.0
    died = False
    while time.monotonic() < deadline:
        time.sleep(0.5)
        state = backend.drain()
        me = state['self']
        if me.get('ghost') or (me.get('health') or 0) == 0:
            died = True
            break
    report['died_in_session'] = died
    if not died:
        raise SystemExit('did not die within the budget')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--combat-entry', type=int, default=3124)
    parser.add_argument('--max-level', type=int, default=3)
    parser.add_argument('--walk-seconds', type=float, default=8.0)
    args = parser.parse_args()
    _run_id_ok(args.run_id)
    folder = ROOT / 'results/pure_network'
    if list(folder.glob(f'*{args.run_id}*')):
        raise SystemExit('run artifacts exist')
    _occupied()

    backend = PureNetworkBackend(args.run_id)
    report = {'run_id': args.run_id,
              'scope': 'danger-zone recovery: the existing design, wired for real',
              'status': 'unknown'}
    try:
        backend.open()
        time.sleep(1.5)
        state = backend.drain()
        if not state['self'].get('ghost'):
            _make_a_corpse(backend, args, report)
            report['logout_before_recovery'] = backend._do_logout({}, 45)
            backend.close(force=True)
            time.sleep(2.0)
            backend = PureNetworkBackend(args.run_id + 'ghost')
            backend.open()
            time.sleep(2.0)
            state = backend.drain()

        me = state['self']
        ghost = list((me.get('movement') or {}).get('position_xyzo') or [])[:3]
        report['initial'] = {'health': me.get('health'), 'ghost': me.get('ghost'),
                             'in_combat': me.get('in_combat'), 'position': ghost}
        if not me.get('ghost'):
            raise SystemExit('the character must be a ghost for this run')
        tracked = list(getattr(backend, 'submitted_position', None) or [])[:3]
        report['bridge_tracked_position'] = tracked
        report['bridge_position_stale'] = bridge_position_is_stale(ghost, tracked)
        if report['bridge_position_stale']:
            raise SystemExit('the bridge tracked position is stale; run this in a '
                             'session where the character starts as a ghost')

        # --- the corpse, from the server's own answer ------------------------
        report['query_corpse'] = backend.rpc('bridge.query_corpse', {})
        state = _drain_until(backend, lambda s: bool(corpse_from_events(s.get('event_log'))), 15.0)
        corpse = corpse_from_events(state.get('event_log'))
        report['corpse_object_guid'] = corpse_guid(state.get('visible_objects'))
        report['corpse'] = corpse
        if not corpse:
            raise SystemExit('the server reported no corpse position')
        corpse_position = list(corpse['position_xyz'])
        report['corpse_position'] = [round(v, 3) for v in corpse_position]
        report['corpse_distance_to_graveyard'] = round(
            math.dist(corpse_position, GRAVEYARD), 3)

        # --- the plan ----------------------------------------------------------
        raw = backend._nav(corpse_position, GRAVEYARD)
        plan = select_recovery(raw['waypoints'], corpse_position)
        report['plan'] = {k: v for k, v in plan.items()
                          if k not in ('retreat_chunks', 'approach_chunks')}
        report['plan']['approach_chunk_count'] = len(plan['approach_chunks'])
        report['plan']['retreat_chunk_count'] = len(plan['retreat_chunks'])

        # --- leg one: the graveyard down to the reclaim point -------------------
        approach_failed = None
        for chunk in plan['approach_chunks']:
            reply = backend.rpc('bridge.follow_path', {'points': chunk, 'run': True})
            if 'error' in reply:
                approach_failed = reply['error'].get('message', 'path rejected')
                break
            time.sleep(0.5)
            backend.drain()
        report['approach_failed'] = approach_failed
        if approach_failed:
            raise SystemExit('the walk to the reclaim point was rejected')

        # --- leg two: reclaim, then immediately run toward the graveyard --------
        result, state = reclaim_and_retreat(backend.rpc, backend.drain, plan,
                                            plan['reclaim_xyz'])
        report['reclaim_outcome'] = result.get('outcome')
        report['reclaim_phases'] = result.get('phases')
        # A normal session exit is NOT a successful revive (docs/43 run47).
        report['ghost_cleared'] = (state['self'].get('ghost') is False
                                   and (state['self'].get('health') or 0) > 0)
        report['after_reclaim'] = {'health': state['self'].get('health'),
                                   'ghost': state['self'].get('ghost'),
                                   'in_combat': state['self'].get('in_combat')}
        report['status'] = 'PASS' if report['ghost_cleared'] else 'INCONCLUSIVE'
        report['logout'] = backend._do_logout({}, 45)
        backend.close(force=True)
        time.sleep(2.0)

        # --- independent fresh login: the real position and state -------------
        verify = PureNetworkBackend(args.run_id + 'verify')
        verify.open()
        time.sleep(2.0)
        state = verify.drain()
        me = state['self']
        fresh = list((me.get('movement') or {}).get('position_xyzo') or [])[:3]
        report['fresh_login_position'] = [round(v, 3) for v in fresh]
        report['fresh_health'] = me.get('health')
        report['fresh_ghost'] = me.get('ghost')
        report['fresh_in_combat'] = me.get('in_combat')
        report['fresh_distance_to_corpse'] = round(math.dist(fresh, corpse_position), 3)
        report['fresh_distance_to_graveyard'] = round(math.dist(fresh, GRAVEYARD), 3)
        verify._do_logout({}, 45)
        verify.close(force=True)
    except SystemExit:
        raise
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            backend.close(force=True)
        except Exception as exc:
            report.setdefault('close_error', f'{type(exc).__name__}: {exc}')
        with (folder / f'agent_wow_danger_recovery_{args.run_id}.json').open('x', encoding='utf-8') as h:
            json.dump(report, h, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
