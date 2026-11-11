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

from agent_wow_combat_state_machine import (ENDING_ESCAPE, ENDING_FIGHT,
                                            ENDING_FINISH_TARGET,
                                            OutOfCombatConfirmator)


class _SampleView:
    """A retreat sample in the shape the shared confirmation rule reads."""

    __slots__ = ('in_combat', 'health', 'health_transmitted', 'context', 'ghost')

    def __init__(self, sample):
        self.in_combat = sample.get('in_combat')
        self.health = sample.get('health')
        self.ghost = sample.get('ghost', False)
        self.health_transmitted = sample.get('health') is not None
        self.context = {'transport_poll': sample['transport_poll']} if 'transport_poll' in sample else {}

# Server-side facts, taken from the local AzerothCore sources:
#   CONFIG_CREATURE_LEASH_RADIUS ("CreatureLeashRadius") = 30.0 yards
#     (src/server/game/World/WorldConfig.cpp)
#   Creature::GetLeashTimer() = 11 seconds for low level mobs (1-5)
#     (src/server/game/Entities/Creature/Creature.cpp)
#   Creature::CanCreatureAttack() compares the victim against the creature's
#   HOME position (m_homePosition), not its current position, and skips that
#   check while the creature was recently damaged ("allow kiting away from
#   spawnpoint").
#
# IMPORTANT: those leash facts govern whether a creature may ACQUIRE a
# target again, NOT when it lets go of one it already has. The real
# disengage condition is upstream's EVADE_REASON_NO_PATH: when the path to
# the victim becomes unreachable, the creature evades and the combat ends
# (src/server/game/AI/UnitAI.cpp -> CreatureAI::_EnterEvadeMode()).
# Measured on 2026-11-01 (b24_u1): both leash conditions were satisfied
# (40.2 yards from the spawn, waited 20.2 s) and the hostile still did not
# break off -- see docs/kb/mechanisms/机制-怪何时真正放弃-NO_PATH.md.
# So the constants below are kept as DIAGNOSTICS ONLY; the escape has to put
# the character where the hostile cannot path to it (corridor boundary).
LEASH_RADIUS_YARDS = 30.0
LEASH_TIMER_SECONDS = 11.0

# The condition that actually ends a combat against a chasing mob.
# Creature::CanCreatureAttack() drops the victim when it is further than the
# visibility range away from the creature's CURRENT position
# (src/server/game/Entities/Creature/Creature.cpp: visibility =
# max(GetVisibilityRange(), victim->GetVisibilityRange()); then
# if (!IsWithinDist(victim, visibility)) return false;). That check sits
# BEFORE the "recently damaged" exemption, so it works even while the mob is
# still hitting the character. The victim's range is the map's, and
# DEFAULT_VISIBILITY_DISTANCE is 100 yards on continents
# (src/server/game/Entities/Object/ObjectDefines.h).
#
# The leash radius above is NOT the escape distance: its check is skipped
# while the mob is in melee range, because TargetedMovementGenerator refreshes
# the leash extension on every attack tick ("Mobs should chase you infinitely
# if you stop and wait every few seconds",
# src/server/game/Movement/TargetedMovementGenerator.cpp). So the 30 yard
# leash can essentially never fire while the mob keeps hitting.
VISIBILITY_RANGE_YARDS = 100.0
# How far the escape has to reach, measured from the hostile's spawn, so the
# gap to the hostile's CURRENT position really clears the visibility range.
#
# The gap grows at (run speed - hostile chase speed):
#   the character runs at the bridge's run speed, 7 yards/s,
#   the hostile chases at speed_walk * 2.5 yards/s; the local world DB has
#   speed_walk = 1.0 for entry 3101 (Vile Familiar) and 3124 (Scorpid Worker),
#   so 2.5 yards/s, and the gap opens at 4.5 yards/s.
# The fight starts at melee range, about 8 yards, so clearing 100 yards needs
# (100 - 8) / 4.5 = 20.4 s of running, which is 143 yards of path; 165 yards
# of reach gives a path of about 167 yards (measured offline with
# scripts/plan_escape_route_offline.py) and a final gap of about 115 yards.
ESCAPE_MIN_DISTANCE_YARDS = 165.0
# The share of the health budget the escape run costs. The rule that applies
# it lives in agent_wow_combat_state_machine.ESCAPE_MIN_HEALTH_RATIO, so
# there is one number and not two that can drift. For the level 3 budget of
# 108 health that ratio is 85 health.
ESCAPE_MIN_HEALTH = 85


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
            'transport_poll': (getattr(observation, 'context', None) or {}).get('transport_poll'),
            'combat_flag_evidence': (getattr(observation, 'context', None) or {}).get('combat_flag_evidence'),
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
                                finish_budget_seconds=20.0,
                                confirmations=3, min_dwell_seconds=2.0,
                                confirm_seconds=10.0,
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

    "The combat is over" is decided by the shared confirmation rule
    (agent_wow_combat_state_machine.OutOfCombatConfirmator): the server has
    to say so, more than once, over a dwell. The branch used to read
    "in_combat is not True" as "combat cleared", which turned an UNKNOWN
    combat state into a reported success.
    """
    started = clock()
    samples = []
    actions = []
    # Samples taken while the passive wait was running. The wait has to end as
    # soon as the hostile is seen to keep hitting: standing still only spends
    # the character's health (2026-11-01 esc013).
    wait_samples = 0
    observation = backend.observe()
    # The shared rule: the server has to say the combat is over, more than
    # once, over a dwell. An unknown combat state is never a confirmation.
    confirmator = OutOfCombatConfirmator(confirmations=confirmations,
                                         min_seconds=min_dwell_seconds)

    def feed(current):
        """Record one sample and ask the shared rule whether it confirms."""
        samples.append(_sample(current))
        return confirmator.observe(current, clock())

    def confirm_by(deadline):
        """Sample until the rule confirms out of combat, or the deadline."""
        nonlocal observation
        while clock() < deadline:
            sleep(poll_interval)
            observation = backend.observe()
            if feed(observation):
                return True
            if observation.in_combat is True:
                return False  # Resume recovery immediately on re-engagement.
        return False

    def settled(reason, **extra):
        """The combat is confirmed over."""
        result = {'combat_cleared': True, 'samples': samples,
                  'elapsed_s': round(clock() - started, 3),
                  'final': observation, 'requests': _request_label(actions),
                  'actions': actions, 'cleared_reason': reason,
                  'reason': None, 'wait_samples': wait_samples,
                  'out_of_combat_confirmations': confirmator.streak}
        result.update(extra)
        return result

    def unsettled(reason, **extra):
        """The combat was never confirmed over; say what is known."""
        result = {'combat_cleared': False, 'samples': samples,
                  'elapsed_s': round(clock() - started, 3),
                  'final': observation, 'requests': _request_label(actions),
                  'actions': actions, 'cleared_reason': None,
                  'reason': reason, 'wait_samples': wait_samples,
                  'combat_state_unknown': observation.in_combat is None,
                  'out_of_combat_confirmations': confirmator.streak}
        result.update(extra)
        return result

    feed(observation)
    if observation.in_combat is not True:
        # Either the combat is already over, or its state is unknown. Neither
        # may be reported as a success on a single sample, so the rule has to
        # confirm it before the character counts as safe. Nothing is attacked
        # here: the server already says there is nothing to fight.
        already_clear = observation.in_combat is False
        if confirm_by(clock() + confirm_seconds):
            return settled('not_in_combat' if already_clear
                           else _cleared_reason(samples, retreated=False),
                           retreat=None)
        if observation.in_combat is not True:
            return unsettled('the combat state was never confirmed out of combat',
                             retreat=None)

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
    #
    # The wait also ends as soon as the server says the combat is over: the
    # branch must not start a new fight in that case. It still has to
    # CONFIRM the clearance before reporting it.
    deadline = started + passive_seconds
    server_clear = False
    while clock() < deadline:
        if observation.health is not None and observation.health <= min_health:
            break
        previous_health = observation.health
        sleep(poll_interval)
        observation = backend.observe()
        wait_samples += 1
        if feed(observation):
            return settled(_cleared_reason(samples, retreated=False), retreat=None)
        if observation.in_combat is False:
            # The server says the combat is over: stop acting, only confirm.
            server_clear = True
            break
        if observation.health is not None and observation.health <= min_health:
            break
        if (observation.health is not None and previous_health is not None
                and observation.health < previous_health):
            # The mob is still hitting: waiting cannot end the combat.
            break

    if server_clear:
        # Confirm the clearance. No fight is started in this state: the server
        # said the combat is over, so acting would be a new, unwanted fight.
        if confirm_by(clock() + confirm_seconds):
            return settled(_cleared_reason(samples, retreated=False), retreat=None)
        if observation.in_combat is not True:
            return unsettled('the combat state was never confirmed out of combat',
                             retreat=None)

    if observation.in_combat is not True:
        return unsettled('combat state unknown; no offensive action authorized',
                         retreat=None)

    # Stage 3: choose how to end the combat.
    #
    # Both endings are available: kill the target, or leave its reach. Which
    # one is cheaper depends on the state. Measured on 2026-11-01 (esc020):
    # the target was at 22 of 71 HP and the character at 91 of 108, and the
    # escape route was taken - the character ran 87 yards, took 60 damage and
    # the combat did not clear. Killing a 22 HP target takes one hit. So a
    # target that is nearly dead is killed, not escaped from.
    #
    # The route is planned first, because "no route" changes the decision:
    # with no route, standing still guarantees death, so fighting wins.
    acquire = getattr(backend, 'acquire_attacker', None)
    if acquire is not None and acquire():
        actions.append('acquire_attacker')
        observation = backend.observe()
        if feed(observation):
            return settled(_cleared_reason(samples, retreated=False), retreat=None)
    route = _plan_escape_route(backend)
    retreat = None
    ending = _combat_ending(observation, route, min_health=min_health,
                             target_reachable=_target_reachable(backend))
    if ending in (ENDING_FINISH_TARGET, ENDING_FIGHT):
        fallback = _finish_the_target(backend, observation, started=started,
                                      budget_seconds=budget_seconds,
                                      finish_budget_seconds=finish_budget_seconds,
                                      min_health=min_health, clock=clock)
        actions.append('finish_target')
        observation = fallback.get('final', observation)
        if feed(observation):
            return settled(_cleared_reason(samples, retreated=False, final=observation),
                           retreat=None, fallback=fallback, ending=ending)
        if fallback.get('outcome') in ('preconditions_not_met', 'no_target_to_finish',
                                       'finish_failed', 'no_budget_left'):
            # The kill ending was not executable after all: the target broke
            # contact and could not be reached. Falling back to the escape is
            # the only ending left (2026-11-01 huntD: standing there while
            # the hostile kept hitting is what killed the character).
            ending = ENDING_ESCAPE
    else:
        ending = ENDING_ESCAPE
    if ending == ENDING_ESCAPE:
        retreat = _perform_retreat(backend, observation, route, sleep=sleep, clock=clock)
        if retreat is not None:
            actions.append('retreat')
            # The retreat already polled the server's combat flag while it
            # waited. Those samples are the evidence, so they are fed to the
            # shared rule instead of being sampled again here.
            for raw in retreat.get('samples', []):
                sample = {'health': raw.get('hp'), 'ghost': raw.get('ghost'),
                          'in_combat': raw.get('in_combat'),
                          'target_health': _target_health(observation)}
                if 'transport_poll' in raw:
                    sample['transport_poll'] = raw['transport_poll']
                samples.append(sample)
                if raw.get('at') is not None:
                    confirmator.observe(_SampleView(sample), raw['at'])
            observation = retreat.get('final', observation)
        if confirmator.confirmed:
            return settled(_cleared_reason(samples, retreated=True, final=observation),
                           retreat=retreat, ending=ending)

    # Still in combat: keep observing, but never below the health floor.
    while clock() - started < budget_seconds:
        sleep(poll_interval)
        observation = backend.observe()
        if feed(observation):
            break
        if observation.health is not None and observation.health <= min_health:
            return unsettled('health floor reached while still in combat',
                             retreat=retreat, ending=ending)
    cleared = confirmator.confirmed
    if cleared:
        return settled(_cleared_reason(samples, retreated=retreat is not None,
                                       final=observation),
                       retreat=retreat, ending=ending,
                       fallback=fallback if retreat is None else None)
    return unsettled('combat did not clear within budget',
                     retreat=retreat, ending=ending,
                     fallback=fallback if retreat is None else None)


def _finish_the_target(backend, observation, *, started, budget_seconds=60,
                       finish_budget_seconds=20.0, min_health=25,
                       clock=time.monotonic):
    """Resume the fight, bounded, when no escape route is available.

    Re-engaging is the last resort and it is bounded: the combat can only end
    by killing the target, which is the server's other ending. The character
    stops at the health floor, and the outcome is reported as a kill rather
    than as an escape.
    """
    target = getattr(backend, 'target', None)
    if not target or not target.get('guid'):
        return {'outcome': 'no_target_to_finish', 'samples': [], 'final': observation}
    remaining = budget_seconds - (clock() - started)
    budget = max(0.0, min(finish_budget_seconds, remaining))
    if budget <= 0:
        return {'outcome': 'no_budget_left', 'samples': [], 'final': observation}
    # A target that has broken contact cannot be fought from where the
    # character stands: the fight controller refuses to start beyond reach
    # (2026-11-01 huntD, where this exact situation killed the character).
    # Close the distance first, the same way the fight action does.
    closed = None
    closer = getattr(backend, '_close_distance', None)
    if closer is not None:
        try:
            closed = closer({})
        except Exception as exc:  # noqa: BLE001 - a failed walk is not fatal here
            closed = {'error': f'{type(exc).__name__}: {exc}'}
    try:
        from agent_wow_combat_control import fight
        # The bridge's submitted position is the authoritative one: the
        # server never broadcasts the mover's own position (docs/88), so the
        # observed position can still be the login point. Measuring the
        # distance from a stale position makes the fight refuse to start.
        reference = list((getattr(backend, 'submitted_position', None)
                          or getattr(backend, 'current_position', None)
                          or getattr(backend, 'observed_position', None) or [])[:3])
        phase, observed = fight(backend.rpc, backend.drain, target['guid'],
                                reference, backend.stream_now, budget=budget)
    except Exception as exc:  # noqa: BLE001 - a failed attempt must not abort the exit
        return {'outcome': 'finish_failed', 'error': f'{type(exc).__name__}: {exc}',
                'samples': [], 'final': observation}
    me = observed.get('self') or {}
    final = _as_observation(backend, observation, observed)
    return {'outcome': phase.get('outcome'), 'samples': [], 'final': final,
            'closed': closed,
            'fight_rows': len(phase.get('observations') or []),
            'start_sent_once': phase.get('start_sent_once'),
            'final_health': me.get('health')}


def _stop_attack(backend):
    """Stop the character's own attack. Never sends anything offensive."""
    try:
        return backend.rpc('bridge.stop_attack', {})
    except Exception as exc:  # noqa: BLE001 - a failed stop must not abort the exit
        return {'error': f'{type(exc).__name__}: {exc}'}


def _combat_ending(observation, route, min_health=25, target_reachable=True):
    """How this unresolved combat should end: flee, kill the target, or fight on.

    The rule is shared with the combat recovery state machine
    (agent_wow_combat_state_machine.choose_combat_ending) so the decision the
    state machine records and the decision the executor carries out can never
    drift apart.
    """
    from agent_wow_combat_state_machine import choose_combat_ending
    context = getattr(observation, 'context', None) or {}
    max_health = getattr(observation, 'max_health', 0) or 0
    return choose_combat_ending(
        self_health=getattr(observation, 'health', None),
        self_max_health=max_health,
        target_health=context.get('target_health'),
        target_max_health=context.get('target_max_health'),
        escape_route_available=bool(route),
        target_reachable=target_reachable,
        health_floor_ratio=(min_health / max_health if max_health else 0.25))


def _target_reachable(backend, max_range=5.0):
    """True when the tracked target is in melee range right now.

    A target that has broken contact cannot be killed from where the
    character stands: the fight controller refuses to start beyond reach
    (huntD, 2026-11-01), so the kill ending is not executable.
    """
    target = getattr(backend, 'target', None)
    if not target:
        return False
    try:
        from agent_wow_combat_control import estimated_position
        position = estimated_position(target, backend.stream_now())[0]
    except Exception:  # noqa: BLE001
        return False
    here = list((getattr(backend, 'submitted_position', None)
                 or getattr(backend, 'current_position', None)
                 or getattr(backend, 'observed_position', None) or [])[:3])
    if not here:
        return False
    return math.dist(here, list(position)[:3]) <= max_range


def _perform_retreat(backend, observation, route, sleep=time.sleep, clock=time.monotonic):
    """Walk the planned route out of the hostile's reach.

    Returns None when no route is known: without a route there is no
    geometrically justified escape, and inventing a straight line is exactly
    the shortcut docs/51 rejects.
    """
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
    # The walk has to cover the route AND outlast the leash timer: the
    # hostile only breaks off when the character is beyond the leash radius
    # from its SPAWN and has not damaged it for the leash timer.
    walk_seconds = length / 7.0
    wait_seconds = max(20.0, walk_seconds + LEASH_TIMER_SECONDS + 5.0)
    try:
        from agent_wow_escape_control import retreat
        result, state = retreat(backend.rpc, backend.drain, points, origin,
                                wait_seconds=wait_seconds, sleep=sleep, clock=clock,
                                route=points)
    except Exception as exc:  # noqa: BLE001 - a failed retreat must not abort the exit
        return {'outcome': 'retreat_failed', 'error': f'{type(exc).__name__}: {exc}',
                'samples': [], 'final': observation, 'wait_seconds': wait_seconds,
                'route': route}
    # The retreat helper returns the raw world-state dict, whose 'self' is
    # keyed by field name. The rest of this module works with Observation
    # objects, so a dict here would make the caller read `.in_combat` off a
    # dict and raise AttributeError.
    result['wait_seconds'] = wait_seconds
    result['route_length'] = round(length, 3)
    result['route'] = route
    landing = _actual_landing(result.get('movement'))
    if landing:
        backend.submitted_position = list(landing)
        backend.current_position = list(landing)
        # Subsequent recovery must plan from the landing, never the old route.
        backend._escape_route = None
        backend._escape_route_target = None
    result['final'] = _as_observation(backend, observation, state)
    # Did the character actually move? The bridge reports the position the
    # SERVER confirms, so this is measurable now: a retreat that does not
    # advance is blocked where it is, and saying so is worth more than a
    # timeout (2026-11-01: a retreat walked the character into a building
    # and kept it walking forward).
    landing = _actual_landing(result.get('movement'))
    origin = route.get('origin') or []
    if landing and origin:
        result['moved_yards'] = round(math.dist(list(landing)[:3],
                                                  list(origin)[:3]), 2)
        result['route_blocked'] = result['moved_yards'] < 1.0
    # The leash verdict, from the ACTUAL landing point rather than the
    # planned one: the hostile breaks off only when the character is beyond
    # the leash radius from its SPAWN and has not damaged it for the leash
    # timer. Recording the real landing makes that judgeable without
    # another life (2026-11-01 b12_u2: the escape was sent and the combat
    # did not clear, with no evidence of which condition failed).
    home = route.get('hostile_home') or []
    actual_end = _actual_landing(result.get('movement'))
    if home and actual_end:
        end_distance = round(math.dist(list(actual_end)[:3], list(home)[:3]), 3)
        result['actual_end'] = [round(v, 3) for v in actual_end[:3]]
        result['actual_end_distance_from_hostile_spawn'] = end_distance
        result['beyond_leash_radius'] = end_distance > LEASH_RADIUS_YARDS
    result['leash_radius_yards'] = LEASH_RADIUS_YARDS
    result['leash_timer_seconds'] = LEASH_TIMER_SECONDS
    result['waited_past_leash_timer'] = wait_seconds >= LEASH_TIMER_SECONDS
    # The condition that can actually end the combat. The reference point is
    # the hostile's CURRENT position, not its spawn, so the distance to the
    # spawn is only a lower bound and is recorded under that name.
    result['visibility_range_yards'] = VISIBILITY_RANGE_YARDS
    result['escape_min_distance_yards'] = ESCAPE_MIN_DISTANCE_YARDS
    if home and actual_end:
        spawn_distance = round(math.dist(list(actual_end)[:3], list(home)[:3]), 3)
        result['distance_from_hostile_spawn'] = spawn_distance
        result['beyond_visibility_from_spawn'] = spawn_distance > VISIBILITY_RANGE_YARDS
    return result


def _actual_landing(movement):
    """Where the walk actually ended, from the bridge's own report."""
    replies = movement if isinstance(movement, list) else [movement]
    for reply in reversed(replies):
        if not isinstance(reply, dict):
            continue
        submitted = (reply.get('result') or {}).get('submittedPosition')
        if isinstance(submitted, list) and len(submitted) >= 3:
            return list(submitted[:3])
    return None



def _plan_escape_route(backend, min_distance=ESCAPE_MIN_DISTANCE_YARDS):
    """Plan a navmesh route that ends out of the hostile's sight range.

    The character must get out of the hostile's reach, which means distance,
    not movement: esc010 reversed a 6-yard approach and stayed in aggro
    range. The destination is placed away from the tracked hostile and then
    snapped onto the navmesh by the same tool the approach uses.

    The distance is the visibility range plus a margin, not the leash radius:
    the leash check is skipped while the mob is in melee range (it refreshes
    itself on every attack tick), so a 30-yard hop can never make a chasing
    mob let go, while clearing the visibility range does.

    The route comes from the session's shared planner, so the combat state
    machine and this branch see the same answer.

    The reversed outbound route is the fallback: it is the only route that is
    known to be walkable, and it is used when no fresh route can be planned.
    """
    current = (getattr(backend, 'submitted_position', None)
               or getattr(backend, 'current_position', None)
               or getattr(backend, 'observed_position', None))

    def usable(route, trim=False):
        if not route or not route.get('points'):
            return None
        points = route['points']
        here = list(current[:3]) if current else route.get('origin')
        if not here:
            return None
        if trim:
            # Only resume at a coincident visited waypoint. Merely replacing
            # origin would send the character back to the old route start.
            matches = [i for i, p in enumerate(points)
                       if math.dist(here, [p[k] for k in ('x', 'y', 'z')]) <= 1.0]
            if not matches:
                return None
            points = points[max(matches):]
        first = [points[0][k] for k in ('x', 'y', 'z')]
        if math.dist(here, first) > 8.0:
            return None
        if _route_length([dict(zip(('x','y','z'), here))] + points) < .05:
            return None
        return {**route, 'points': points, 'origin': here}

    shared = getattr(backend, 'escape_route', None)
    if shared is not None:
        try:
            route = usable(shared(refresh=True))
        except Exception:  # A failed planner still permits emergency reversal.
            route = None
        if route:
            return route
    nav = getattr(backend, 'plan_escape_route', None)
    if nav is not None:
        try:
            planned = nav(min_distance=min_distance)
        except Exception:  # noqa: BLE001 - fall back to the known route
            planned = None
        planned = usable(planned)
        if planned:
            return planned
    travel_return = usable(getattr(backend, 'travel_return_route', None), trim=True)
    if travel_return:
        length = _route_length(travel_return['points'])
        return {**travel_return, 'fallback_distance_yards': round(length, 3),
                'full_escape_distance': length >= min_distance,
                'fallback_reason': 'fresh escape unavailable; emergency visited corridor only'}
    route = getattr(backend, 'outbound_route', None) or []
    origin = getattr(backend, 'outbound_origin', None)
    if not route or not origin:
        return None
    # The far end of the outbound route has to be kept: dropping it left a
    # route that starts and ends on the same spot, and the bridge refuses a
    # path whose length is 0 ("path length must be .05..50 yards",
    # 2026-11-01 b39_u1), so the character did not move at all.
    return usable({'points': list(reversed(route)) + [dict(zip(('x', 'y', 'z'), origin[:3]))],
            'origin': list(current[:3]) if current else list(origin[:3]),
            'source': 'reversed submitted outbound route',
            'planned_distance': None})


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
                  min_health=25, finish_budget_seconds=20.0,
                  sleep=time.sleep, clock=time.monotonic):
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
    recovery = None
    result = {'initial_self': {'health': observation.health,
                               'ghost': observation.ghost,
                               'in_combat': observation.in_combat}}
    if living(observation) and observation.in_combat is True:
        recovery = resolve_combat_before_exit(
            backend, budget_seconds=budget_seconds, poll_interval=poll_interval,
            min_health=min_health, finish_budget_seconds=finish_budget_seconds,
            sleep=sleep, clock=clock)
        retreat = recovery.get('retreat') or {}
        result['combat_recovery'] = {
            'combat_cleared': recovery['combat_cleared'],
            'elapsed_s': recovery['elapsed_s'],
            'sample_count': len(recovery['samples']),
            'requests': recovery['requests'],
            'actions': recovery.get('actions', []),
            'cleared_reason': recovery.get('cleared_reason'),
            'reason': recovery.get('reason'),
            'retreat_outcome': retreat.get('outcome'),
            'fallback_outcome': (recovery.get('fallback') or {}).get('outcome'),
            # The leash verdict, from the ACTUAL landing point: whether the
            # character really got beyond the leash radius from the hostile's
            # spawn, and whether the wait covered the leash timer. Without
            # these, "the escape did not clear the combat" says nothing about
            # which condition failed (2026-11-01 b12_u2, b13_u1).
            'leash_verdict': {key: retreat.get(key) for key in (
                'actual_end', 'actual_end_distance_from_hostile_spawn',
                'beyond_leash_radius', 'leash_radius_yards',
                'leash_timer_seconds', 'waited_past_leash_timer',
                'wait_seconds', 'route_length') if key in retreat},
            # The bridge's own refusal has to be in the evidence: without it
            # "retreat_movement_rejected" says nothing about which of the
            # bridge's path limits was broken (2026-11-01 b29_u1: the escape
            # route of 208 yards was planned and then refused, and the
            # reason was nowhere in the report).
            'retreat_rejection': retreat.get('rejection'),
            'retreat_chunks_sent': retreat.get('chunks_sent'),
            'retreat_path_source': retreat.get('path_source'),
            'retreat_route_selection': {k: (retreat.get('route') or {}).get(k)
                                       for k in ('source', 'origin', 'safe_anchor',
                                                 'fallback_distance_yards', 'full_escape_distance')},
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
    poll = (getattr(observation, 'context', None) or {}).get('transport_poll')
    poll_valid = (poll is None or (
        poll.get('caught_up') is True
        and isinstance(poll.get('polled_at_monotonic'), (int, float))
        and 0 <= clock() - poll['polled_at_monotonic'] <= 2.0))
    if observation.in_combat is None or not poll_valid:
        # The combat state is unknown, which is not the same as "no combat".
        # Logging out on an unknown state would tear the session down on a
        # guess, and the server keeps hitting a character that is still in
        # combat after the client is gone. The session stays open and the
        # caller supervises it.
        result['status'] = 'combat_state_unknown'
        result['reason'] = ('the combat state was never confirmed; the session must stay '
                            'open and be supervised - do not log out and do not close')
        report['finalize'] = result
        return result
    if recovery is not None and recovery['combat_cleared'] is not True:
        result['status'] = 'unresolved'
        result['reason'] = 'out-of-combat confirmation incomplete; no logout attempted'
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


def finish_session(backend, *, report, budget_seconds=120, poll_interval=0.5,
                   hold_ownership=False, checkpoint=None,
                   finish_budget_seconds=20.0,
                   sleep=time.sleep, clock=time.monotonic):
    """Share guarded teardown across runners, including their exception paths.

    Recovery/confirmation is retried while this function owns supervision.
    Only an accepted logout or observed death/ghost permits transport close.
    With hold_ownership, expiry starts another recovery window in this same
    process. Otherwise expiry returns an explicit handoff requirement.
    The budget bounds retries; a backend RPC already in progress has its own
    timeout and may finish after this deadline.
    """
    if budget_seconds <= 0 or poll_interval <= 0:
        raise ValueError('supervision budget and poll interval must be positive')
    deadline = clock() + budget_seconds
    attempts = []
    result = {'status': 'requires_supervision', 'transport_closed': False,
              'attempts': attempts, 'attempt_count': 0, 'windows_expired': 0}
    report['session_ending'] = result
    while True:
        if clock() >= deadline:
            result['windows_expired'] += 1
            if not hold_ownership:
                break
            result['status'] = 'supervising'
            deadline = clock() + budget_seconds
        if checkpoint is not None:
            try:
                checkpoint(report)
            except Exception as exc:
                result['checkpoint_error'] = f'{type(exc).__name__}: {exc}'
        try:
            final = close_session(
                backend, report=report,
                budget_seconds=max(0, min(60, deadline - clock())),
                finish_budget_seconds=finish_budget_seconds,
                poll_interval=poll_interval, sleep=sleep, clock=clock)
            attempts.append(final)
            result['attempt_count'] += 1
            del attempts[:-20]
            logout_closed = (final.get('logout') or {}).get('status') == 'closed'
            dead_or_ghost = False
            if not logout_closed:
                observation = backend.observe()
                health = getattr(observation, 'health', None)
                dead_or_ghost = (getattr(observation, 'ghost', False) is True
                                 or (getattr(observation, 'health_transmitted', True)
                                     and health is not None and health <= 0))
            if logout_closed or dead_or_ghost:
                backend.close()
                result.update(status='closed', transport_closed=True,
                              closed_after='logout' if logout_closed else 'death_or_ghost')
                break
        except Exception as exc:  # Preserve ownership on observation/recovery errors.
            attempts.append({'error': f'{type(exc).__name__}: {exc}'})
            result['attempt_count'] += 1
            del attempts[:-20]
            # There is no session to supervise if startup never produced an
            # owner process or the owner has already exited. Do not claim the
            # character is safe, but return for explicit reconciliation rather
            # than spin forever on a dead RPC endpoint.
            session = getattr(backend, 'session', None)
            process = getattr(session, 'process', None)
            if session is None or (process is not None and process.poll() is not None):
                result['status'] = 'connection_lost_requires_reconciliation'
                result['reason'] = 'no live session owner; character state not inferred'
                break
        sleep(poll_interval)
    if not result['transport_closed']:
        result['reason'] = ('safe termination not established; transport left open; '
                            'an active supervisor must take ownership')
    report['session_ending'] = result
    if checkpoint is not None:
        try:
            checkpoint(report)
        except Exception as exc:
            result['checkpoint_error'] = f'{type(exc).__name__}: {exc}'
    return result
