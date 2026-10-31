"""Resolve the character's combat state before the session is closed.

Why this exists: on 2026-11-01 a bounded task unit fought a Vile Familiar
that fled at low health. The fight action ended with the target out of melee
range while the character was still flagged in combat. The executor correctly
stopped the plan (an unknown side-effecting outcome must not be chained), so
the planned settle never ran. The runner's exit path then refused to log out
because the character was in combat, and the session was force-terminated with
the character still in combat. The server kept the character in the world and
the mob killed it (HP 108 -> 71 -> 0).

This module is the planned recovery branch: it never sends an offensive
request, it only observes until the server clears combat, so the session can
be closed cleanly.
"""
import time


def living(observation):
    """The character is alive and not a ghost, regardless of combat state."""
    return observation.health > 0 and not observation.ghost


def resolve_combat_before_exit(backend, *, budget_seconds=60, poll_interval=0.5,
                                min_health=25, passive_seconds=6.0,
                                sleep=time.sleep, clock=time.monotonic):
    """Leave an unresolved combat without re-engaging.

    Measured on 2026-11-01 (esc003): stopping the character's own attack does
    NOT end a hostile that keeps hitting. in_combat stayed True for the whole
    45 s wait while HP fell 108 -> 39. The 1-3 s "aggro lapse" seen in the
    archives only happened after the mob had already stopped hitting.

    So the escape has two stages:
      1. passive observation for a short window, which covers the case where
         the mob has stopped hitting (killed it, or it desisted);
      2. otherwise leave the hostile's reach by reversing the outbound route
         that was actually submitted to get here (docs/51). This is the only
         geometrically justified escape: the server never broadcasts the
         mover's own position, so "did I get out of reach" cannot be
         confirmed in-session (docs/88).

    Only observation requests are sent while waiting. The retreat is a single
    movement request; no attack, no loot.
    """
    started = clock()
    samples = []
    observation = backend.observe()
    samples.append({'health': observation.health, 'ghost': observation.ghost,
                     'in_combat': observation.in_combat})
    if observation.in_combat is not True:
        return {'combat_cleared': True, 'samples': samples,
                'elapsed_s': round(clock() - started, 3),
                'final': observation, 'requests': 'observation only',
                'reason': None, 'retreat': None}

    # Stage 1: passive wait (the mob may have stopped hitting already).
    deadline = started + passive_seconds
    while clock() < deadline:
        sleep(poll_interval)
        observation = backend.observe()
        samples.append({'health': observation.health, 'ghost': observation.ghost,
                        'in_combat': observation.in_combat})
        if observation.in_combat is not True:
            return {'combat_cleared': True, 'samples': samples,
                    'elapsed_s': round(clock() - started, 3),
                    'final': observation, 'requests': 'observation only',
                    'reason': None, 'retreat': None}
        if observation.health is not None and observation.health <= min_health:
            return {'combat_cleared': False, 'samples': samples,
                    'elapsed_s': round(clock() - started, 3),
                    'final': observation, 'requests': 'observation only',
                    'reason': 'health floor reached while still in combat',
                    'retreat': None}

    # Stage 2: leave the hostile's reach along the reversed outbound route.
    retreat = _retreat_out_of_reach(backend, observation)
    if retreat is not None:
        samples.extend(retreat.get('samples', []))
        observation = retreat.get('final', observation)
    if observation.in_combat is not True:
        return {'combat_cleared': True, 'samples': samples,
                'elapsed_s': round(clock() - started, 3),
                'final': observation, 'requests': 'observation + one retreat',
                'reason': None, 'retreat': retreat}

    # Still in combat: keep observing, but never below the health floor.
    while clock() - started < budget_seconds:
        sleep(poll_interval)
        observation = backend.observe()
        samples.append({'health': observation.health, 'ghost': observation.ghost,
                        'in_combat': observation.in_combat})
        if observation.in_combat is not True:
            break
        if observation.health is not None and observation.health <= min_health:
            return {'combat_cleared': False, 'samples': samples,
                    'elapsed_s': round(clock() - started, 3),
                    'final': observation,
                    'requests': 'observation + one retreat',
                    'reason': 'health floor reached while still in combat',
                    'retreat': retreat}
    cleared = observation.in_combat is not True
    return {'combat_cleared': cleared, 'samples': samples,
            'elapsed_s': round(clock() - started, 3),
            'final': observation,
            'requests': 'observation + one retreat',
            'reason': None if cleared else 'combat did not clear within budget',
            'retreat': retreat}


def _retreat_out_of_reach(backend, observation):
    """Reverse the outbound route once, to leave the hostile's reach.

    Returns None when no route is known: without a submitted route there is no
    geometrically justified escape, and inventing a straight line is exactly
    the shortcut docs/51 rejects.
    """
    route = getattr(backend, 'outbound_route', None) or []
    origin = getattr(backend, 'outbound_origin', None)
    if not route or not origin:
        return None
    try:
        from agent_wow_escape_control import retreat
        result, state = retreat(backend.rpc, backend.drain, route, origin,
                                wait_seconds=12)
    except Exception as exc:  # noqa: BLE001 - a failed retreat must not abort the exit
        return {'outcome': 'retreat_failed', 'error': f'{type(exc).__name__}: {exc}',
                'samples': [], 'final': observation}
    result['final'] = state['self']
    return result



def close_session(backend, *, report, budget_seconds=60, poll_interval=0.5,
                  min_health=25, sleep=time.sleep, clock=time.monotonic):
    """Close the session, resolving an unresolved in-combat state first.

    The character is never left in combat for the session teardown to decide:
    the exit path waits for the server to clear combat before logging out, and
    it always attempts the logout so the outcome is recorded either way.
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
