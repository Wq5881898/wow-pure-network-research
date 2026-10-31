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
                                min_health=25, sleep=time.sleep,
                                clock=time.monotonic):
    """Wait out of combat without re-engaging.

    Only observation requests are sent. No attack, no loot, no movement: a
    recovery branch must not start a second fight while the first one is
    unresolved.

    ``min_health`` is a safety floor, not a target: waiting for the server to
    clear combat must never be the thing that kills the character. Measured
    aggro lapse is 1-3 s, so a floor well above zero aborts the wait early
    instead of watching the character die while standing still.
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
                'reason': None}
    while clock() - started < budget_seconds:
        sleep(poll_interval)
        observation = backend.observe()
        samples.append({'health': observation.health, 'ghost': observation.ghost,
                        'in_combat': observation.in_combat})
        if observation.in_combat is not True:
            break
        if observation.health is not None and observation.health <= min_health:
            # Standing still until the aggro lapses must not be what kills the
            # character. Stop waiting and report the danger explicitly.
            return {'combat_cleared': False, 'samples': samples,
                    'elapsed_s': round(clock() - started, 3),
                    'final': observation, 'requests': 'observation only',
                    'reason': 'health floor reached while still in combat'}
    cleared = observation.in_combat is not True
    return {'combat_cleared': cleared, 'samples': samples,
            'elapsed_s': round(clock() - started, 3),
            'final': observation, 'requests': 'observation only',
            'reason': None if cleared else 'combat did not clear within budget'}


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
