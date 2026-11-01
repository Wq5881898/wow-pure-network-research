"""Resolve the character's combat state before the session is closed.

Why this exists: on 2026-11-01 a bounded task unit fought a Vile Familiar
that fled at low health. The fight action ended with the target out of melee
range while the character was still flagged in combat. The executor correctly
stopped the plan (an unknown side-effecting outcome must not be chained), so
the planned settle never ran. The runner's exit path then refused to log out
because the character was in combat, and the session was force-terminated with
the character still in combat. The server kept the character in the world and
the mob killed it (HP 108 -> 71 -> 0).

This module is the planned recovery branch: it stops the character's own
attack, observes until the server clears combat, and otherwise reverses the
route that was actually submitted to reach the hostile.

Measured on 2026-11-01 (esc003): stopping the character's own attack does NOT
end a hostile that keeps hitting. in_combat stayed True for the whole 45 s
wait while HP fell 108 -> 39. The 1-3 s "aggro lapse" seen in the archives
only happened after the mob had already stopped hitting.

Measured on 2026-11-01 (esc008): an escape that only observes is not passive
unless the character has stopped attacking. The probe left the auto-attack
running, so the character kept hitting the mob until it died (71 -> 0 HP)
25 s later. The combat cleared because the *target* died, and the report read
like a successful escape. Every escape therefore stops the character's own
attack first, and the result states which of the two endings actually happened:
the target died, the character left the hostile's reach, or the server simply
cleared the flag.
"""
import math
import time


def living(observation):
    """The character is alive and not a ghost, regardless of combat state."""
    return observation.health > 0 and not observation.ghost


def _target_health(observation):
    """Live health of the tracked target, or None when no target is tracked.

    The value comes from the live visible-object set, not from the snapshot
    taken at select time: only a live value can tell a kill from a retreat.
    """
    context = getattr(observation, 'context', None) or {}
    health = context.get('target_health')
    if health is not None:
        return health
    target = context.get('target') or {}
    return target.get('health')


def _sample(observation):
    return {'health': observation.health, 'ghost': observation.ghost,
            'in_combat': observation.in_combat,
            'target_health': _target_health(observation)}


def _cleared_reason(samples, retreated, final=None):
    """Why the combat ended, derived from the recorded samples alone.

    'target_died' is the ending that must never be reported as an escape: the
    combat cleared because the hostile was killed, not because the character
    got out of its reach (esc008).
    """
    if any(sample.get('target_health') == 0 for sample in samples):
        return 'target_died'
    if final is not None and _target_health(final) == 0:
        return 'target_died'
    if retreated:
        return 'left_reach'
    return 'server_cleared'


def _request_label(actions):
    parts = ['observation']
    if 'stop_attack' in actions:
        parts.append('stop_attack')
    if 'retreat' in actions:
        parts.append('one retreat')
    return ' + '.join(parts)


def resolve_combat_before_exit(backend, *, budget_seconds=60, poll_interval=0.5,
                                min_health=25, passive_seconds=6.0,
                                sleep=time.sleep, clock=time.monotonic):
    """Leave an unresolved combat without re-engaging.

    Measured on 2026-11-01 (esc003): stopping the character's own attack does
    NOT end a hostile that keeps hitting. in_combat stayed True for the whole
    45 s wait while HP fell 108 -> 39. The 1-3 s "aggro lapse" seen in the
    archives only happened after the mob had already stopped hitting.

    Measured on 2026-11-01 (esc008): the character must stop attacking
    *before* observing, otherwise the observation is not passive: the probe
    kept hitting the mob until it died 25 s later and the resulting combat
    clear looked like a successful escape.

    So the escape has three stages:
      1. stop the character's own attack, so nothing here is offensive;
      2. passive observation for a short window, which covers the case where
         the mob has already stopped hitting (killed it, or it desisted);
      3. otherwise leave the hostile's reach by reversing the outbound route
         that was actually submitted to get here (docs/51). This is the only
         geometrically justified escape: the server never broadcasts the
         mover's own position, so "did I get out of reach" cannot be
         confirmed in-session (docs/88).

    The retreat is a single movement request; no attack, no loot.
    """
    started = clock()
    samples = []
    actions = []
    observation = backend.observe()
    samples.append(_sample(observation))
    if observation.in_combat is not True:
        return {'combat_cleared': True, 'samples': samples,
                'elapsed_s': round(clock() - started, 3),
                'final': observation, 'requests': _request_label(actions),
                'actions': actions, 'cleared_reason': 'not_in_combat',
                'reason': None, 'retreat': None}

    # Stage 1: stop swinging. Without this the wait below is not passive: the
    # character keeps hitting the hostile, which is how esc008 turned a kill
    # into an apparent escape.
    stop = _stop_attack(backend)
    actions.append('stop_attack')

    # Stage 2: passive wait (the mob may have stopped hitting already).
    #
    # The wait is only useful while the mob has stopped hitting: measured on
    # 2026-11-01 (esc003), a hostile that keeps hitting never lets the combat
    # clear, so standing still only spends the character's health. The mob
    # still hitting is directly observable: the character's own health drops
    # between two samples. As soon as it does, the wait is abandoned and the
    # retreat starts, instead of burning the health floor (esc013, where the
    # character lost 43 HP standing still and died).
    deadline = started + passive_seconds
    while clock() < deadline:
        if observation.health is not None and observation.health <= min_health:
            break
        previous_health = observation.health
        sleep(poll_interval)
        observation = backend.observe()
        samples.append(_sample(observation))
        if observation.in_combat is not True:
            return {'combat_cleared': True, 'samples': samples,
                    'elapsed_s': round(clock() - started, 3),
                    'final': observation, 'requests': _request_label(actions),
                    'actions': actions,
                    'cleared_reason': _cleared_reason(samples, retreated=False),
                    'reason': None, 'retreat': None}
        if observation.health is not None and observation.health <= min_health:
            break
        if (observation.health is not None and previous_health is not None
                and observation.health < previous_health):
            # The mob is still hitting: waiting cannot end the combat.
            break

    # Stage 3: leave the hostile's reach along the reversed outbound route.
    retreat = _retreat_out_of_reach(backend, observation, sleep=sleep, clock=clock)
    if retreat is not None:
        actions.append('retreat')
        samples.extend(retreat.get('samples', []))
        observation = retreat.get('final', observation)
    if observation.in_combat is not True:
        return {'combat_cleared': True, 'samples': samples,
                'elapsed_s': round(clock() - started, 3),
                'final': observation, 'requests': _request_label(actions),
                'actions': actions,
                'cleared_reason': _cleared_reason(samples, retreated=retreat is not None,
                                                   final=observation),
                'reason': None, 'retreat': retreat}

    # Still in combat: keep observing, but never below the health floor.
    while clock() - started < budget_seconds:
        sleep(poll_interval)
        observation = backend.observe()
        samples.append(_sample(observation))
        if observation.in_combat is not True:
            break
        if observation.health is not None and observation.health <= min_health:
            return {'combat_cleared': False, 'samples': samples,
                    'elapsed_s': round(clock() - started, 3),
                    'final': observation, 'requests': _request_label(actions),
                    'actions': actions, 'cleared_reason': None,
                    'reason': 'health floor reached while still in combat',
                    'retreat': retreat}
    cleared = observation.in_combat is not True
    return {'combat_cleared': cleared, 'samples': samples,
            'elapsed_s': round(clock() - started, 3),
            'final': observation,
            'requests': _request_label(actions),
            'actions': actions,
            'cleared_reason': (_cleared_reason(samples, retreated=retreat is not None,
                                               final=observation)
                               if cleared else None),
            'reason': None if cleared else 'combat did not clear within budget',
            'retreat': retreat}


def _stop_attack(backend):
    """Stop the character's own attack. Never sends anything offensive."""
    try:
        return backend.rpc('bridge.stop_attack', {})
    except Exception as exc:  # noqa: BLE001 - a failed stop must not abort the exit
        return {'error': f'{type(exc).__name__}: {exc}'}


def _retreat_out_of_reach(backend, observation, sleep=time.sleep, clock=time.monotonic):
    """Leave the hostile's reach along a route that actually gets away.

    Measured on 2026-11-01 (esc010): reversing the outbound approach route is
    NOT an escape. The approach had only walked about 6 yards, so the
    reversed route left the character inside the scorpid's aggro range: the
    hostile kept hitting for 45 s and the character died. The escape has to
    END far from the hostile, so the route is planned with the navmesh
    instead of replaying the approach.

    The navmesh is the same geometric authority the approach uses, and the
    destination is snapped onto it, so the walk stays on walkable ground
    rather than inventing a straight line (docs/51).

    Returns None when no route can be planned: without a route there is no
    justified escape.
    """
    route = _plan_escape_route(backend)
    if not route:
        return None
    points = route['points']
    origin = route['origin']
    # The walk out has to actually cover the route: the bridge needs about
    # 12 s per 30-yard chunk, so scale the wait with the route's real length
    # instead of using a fixed timeout that ends before the hostile's reach
    # is left behind. The length is measured on the path, not on the number
    # of waypoints: a densified route has a waypoint every few yards.
    length = _route_length(points)
    wait_seconds = max(15.0, 12.0 * (length / 30.0))
    try:
        from agent_wow_escape_control import retreat
        result, state = retreat(backend.rpc, backend.drain, route['points'],
                                route['origin'], wait_seconds=wait_seconds,
                                sleep=sleep, clock=clock, route=route['points'])
    except Exception as exc:  # noqa: BLE001 - a failed retreat must not abort the exit
        return {'outcome': 'retreat_failed', 'error': f'{type(exc).__name__}: {exc}',
                'samples': [], 'final': observation, 'wait_seconds': wait_seconds,
                'route': route}
    # The retreat helper returns the raw world-state dict, whose 'self' is
    # keyed by field name. The rest of this module works with Observation
    # objects, so a dict here would make the caller read `.in_combat` off a
    # dict and raise AttributeError.
    result['final'] = _as_observation(backend, observation, state)
    result['wait_seconds'] = wait_seconds
    result['route_length'] = round(length, 3)
    result['route'] = route
    return result


def _plan_escape_route(backend, min_distance=40.0):
    """Plan a navmesh route that ends well away from the hostile.

    The character must get out of the hostile's reach, which means distance,
    not movement: esc010 reversed a 6-yard approach and stayed in aggro
    range. The destination is placed away from the tracked hostile and then
    snapped onto the navmesh by the same tool the approach uses.

    The reversed outbound route is the fallback: it is the only route that is
    known to be walkable, and it is used when no fresh route can be planned.
    """
    nav = getattr(backend, 'plan_escape_route', None)
    if nav is not None:
        try:
            planned = nav(min_distance=min_distance)
        except Exception:  # noqa: BLE001 - fall back to the known route
            planned = None
        if planned and planned.get('points'):
            return planned
    route = getattr(backend, 'outbound_route', None) or []
    origin = getattr(backend, 'outbound_origin', None)
    if not route or not origin:
        return None
    return {'points': list(reversed(route[:-1])) + [dict(zip(('x', 'y', 'z'), origin[:3]))],
            'origin': list(origin[:3]),
            'source': 'reversed submitted outbound route',
            'planned_distance': None}


def _as_observation(backend, previous, state):
    """Refresh the observation after a retreat.

    The retreat returns the world-state dict, which carries the same fields as
    an observation but not the same shape. A fresh observation keeps the
    contract: every consumer of the result reads attributes, not keys.
    """
    try:
        return backend.observe()
    except Exception:  # noqa: BLE001 - fall back to the last good observation
        return previous


def _route_length(route):
    """Length of the submitted route in yards, measured along the path."""
    total = 0.0
    previous = None
    for point in route:
        current = (point['x'], point['y'], point['z'])
        if previous is not None:
            total += math.dist(previous, current)
        previous = current
    return total


def close_session(backend, *, report, budget_seconds=60, poll_interval=0.5,
                  min_health=25, sleep=time.sleep, clock=time.monotonic):
    """Close the session, resolving an unresolved in-combat state first.

    The character is never left in combat for the session teardown to decide:
    the exit path waits for the server to clear combat before logging out, and
    it always attempts the logout so the outcome is recorded either way.

    When the escape cannot clear the combat, the session is deliberately NOT
    closed: the close guard in the backend refuses to tear down a living
    in-combat character, because the server keeps attacking it after the
    client is gone (2026-11-01 cs002, esc003). The caller is told to keep
    the session alive and supervise it instead of abandoning the character.
    """
    observation = backend.observe()
    result = {'initial_self': {'health': observation.health,
                               'ghost': observation.ghost,
                               'in_combat': observation.in_combat}}
    if living(observation) and observation.in_combat is True:
        recovery = resolve_combat_before_exit(
            backend, budget_seconds=budget_seconds, poll_interval=poll_interval,
            min_health=min_health, sleep=sleep, clock=clock)
        result['combat_recovery'] = {
            'combat_cleared': recovery['combat_cleared'],
            'elapsed_s': recovery['elapsed_s'],
            'sample_count': len(recovery['samples']),
            'requests': recovery['requests'],
            'actions': recovery.get('actions', []),
            'cleared_reason': recovery.get('cleared_reason'),
            'reason': recovery.get('reason'),
            'retreat_outcome': (recovery.get('retreat') or {}).get('outcome'),
        }
        observation = recovery['final']
    if not living(observation):
        result['status'] = 'unresolved'
        result['reason'] = 'character is dead or a ghost; no logout attempted'
        result['ghost'] = observation.ghost
        report['finalize'] = result
        return result
    if observation.in_combat is True:
        # The escape failed. Logging out now would be refused by the server
        # and the character would keep being attacked; tearing the session
        # down would kill it. Neither is acceptable, so the session stays open
        # and the caller must keep supervising it.
        result['status'] = 'still_in_combat'
        result['reason'] = ('escape did not clear combat; the session must stay open '
                            'and be supervised - do not log out and do not close')
        report['finalize'] = result
        return result
    result['status'] = 'resolved'
    result['logout'] = backend.execute(_logout_action())
    if result['logout'].get('status') != 'closed':
        # The server refuses a logout while the character is still in combat.
        # Record that explicitly instead of reporting a clean close.
        result['logout_note'] = 'logout was not accepted by the server'
    report['finalize'] = result
    return result


def _logout_action():
    from agent_wow_contract import Action
    return Action('logout', 'logout', {}, timeout=45)
