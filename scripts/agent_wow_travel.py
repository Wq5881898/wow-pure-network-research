"""Staged travel towards a goal, one walkable leg at a time.

The direct navmesh route to a distant goal is often refused: the session's
densifier rejects a route that drops more than 5 yards in one step ("route
drops more than 5 yards; refusing cliff path"). docs/73 says a rejection does
not mean the map is unreachable - it may be a corridor choice - and that the
check must not be bypassed.

So the journey is staged: repeatedly find the FARTHEST point that a real
navmesh route can reach AND that survives the same densifier and the
bridge's own limits, walk that leg, then replan from where the character
actually is.

The travel is bounded and observed: after every leg the character is checked
for damage and for combat. A hit or an engaged fight stops the travel - it is
reported, not fought. That is the discipline in docs/73: do not attack along
the way, and pause on the first sign of trouble.
"""
import math
import time


def _length(points):
    total = 0.0
    previous = None
    for point in points:
        xyz = [point['x'], point['y'], point['z']]
        if previous is not None:
            total += math.dist(previous[:2], xyz[:2])
        previous = xyz
    return total


def _bridge_limits_ok(points, start):
    """The bridge rejects a segment over 15 yards or a 3-yard height change."""
    previous = list(start)[:3]
    for point in points:
        xyz = [point['x'], point['y'], point['z']]
        if math.dist(previous[:2], xyz[:2]) > 15.0:
            return False, 'segment over 15 yards'
        if abs(xyz[2] - previous[2]) > 3.0:
            return False, 'height change over 3 yards'
        previous = xyz
    return True, None


def _legal_prefix(points, start, min_length=4.0):
    """The longest prefix of a path the bridge will accept, and its length.

    A path can be walkable on the navmesh and still be refused by the bridge
    part way along, because the bridge caps a segment at 15 yards and a
    height change at 3 yards. Cutting the path at the first illegal step
    turns a long route into a legal leg instead of throwing it away.
    """
    legal = []
    previous = list(start)[:3]
    total = 0.0
    for point in points:
        xyz = [point['x'], point['y'], point['z']]
        if math.dist(previous[:2], xyz[:2]) > 15.0:
            break
        if abs(xyz[2] - previous[2]) > 3.0:
            break
        total += math.dist(previous[:2], xyz[:2])
        legal.append(point)
        previous = xyz
    if total < min_length:
        return None, total
    return legal, total


def _already_visited(reached, visited, tolerance=8.0):
    """True when the character has already stood on this ground."""
    if not visited:
        return False
    return any(math.dist(list(reached)[:3], list(point)[:3]) <= tolerance
               for point in visited)


def plan_leg(backend, goal, radius=40.0, bearings=16, allow_detour=True,
             visited=None):
    """Plan one leg towards the goal; return the densified points or None.

    The goal is approached by trying a fan of bearings around the direct
    direction, so a cliff in the straight line does not stop the journey.

    The fan only tries short straight steps, so a place where the ground
    refuses every straight step stopped the journey even though the navmesh
    had a path (2026-11-01 travel3101a: leg 2 failed with "no walkable leg
    towards the goal" while the navmesh path from the same point to the goal
    existed). The goal itself is therefore a candidate too, and its path is
    followed for as long as the bridge accepts it.
    """
    start = list((backend.submitted_position or backend.current_position
                  or backend.observed_position)[:3])
    if not start:
        return None
    goal = list(goal)[:3]
    bearing = math.atan2(goal[1] - start[1], goal[0] - start[0])
    distance_to_goal = math.dist(start[:3], goal)
    best = None
    fallback = None
    # The goal itself is the best candidate when it works, because its path
    # follows the real navmesh around obstacles; it is tried first so a
    # working route does not pay for the whole fan.
    # The fan covers the whole circle, not just the cone around the goal.
    # A character that is stranded on a plateau can only walk sideways
    # (2026-11-01 travel3125a: from the plateau at z=93 the walkable
    # directions were 60..135 degrees while the goal lay at 280, and the
    # goal-centred fan never looked there).
    candidates = [(bearing, distance_to_goal, goal)]
    for index in range(bearings):
        angle = bearing + (0.0 if index == 0 else
                           math.copysign(((index + 1) // 2) * 2 * math.pi / bearings,
                                         index))
        for distance in (radius, radius * 0.6, radius * 0.35):
            candidates.append((angle, distance, [
                start[0] + math.cos(angle) * distance,
                start[1] + math.sin(angle) * distance,
                start[2]]))
    for angle, distance, end in candidates:
        try:
            raw = backend._nav(start, end)
            # The travel judges a drop by its slope, so a ramp is walkable and
            # only a real cliff is refused. The combat approach keeps the
            # stricter 5 yard rule.
            points = backend._densify(raw, start, slope_tolerance=0.7)
        except RuntimeError:
            continue
        if not points:
            continue
        ok, _reason = _bridge_limits_ok(points, start)
        if not ok:
            points, travelled = _legal_prefix(points, start)
            if not points:
                continue
        else:
            travelled = _length(points)
        if travelled < 4.0:
            continue
        reached = [points[-1]['x'], points[-1]['y'], points[-1]['z']]
        remaining = math.dist(reached, goal)
        progress = distance_to_goal - remaining
        if progress <= 0:
            # A leg that does not get closer is wasted walking -- unless
            # nothing gets closer at all. A character that is stranded has to
            # be able to walk sideways first: the ground that leads towards
            # the goal may be a cliff the densifier refuses, while the ground
            # beside the character is walkable. A detour that lands on ground
            # the character has already stood on is a loop, not a detour
            # (2026-11-01 travel3125a: legs 4..17 alternated between two
            # positions 42.8 yards apart).
            if (allow_detour and (fallback is None or travelled > fallback['length'])
                    and not _already_visited(reached, visited)):
                fallback = {'points': points, 'length': round(travelled, 2),
                            'end': [round(v, 2) for v in reached],
                            'progress': round(progress, 2),
                            'goal_distance': round(remaining, 2),
                            'detour': True,
                            'bearing_degrees': round(math.degrees(angle) % 360, 1)}
            continue
        if best is None or progress > best['progress']:
            best = {'points': points, 'length': round(travelled, 2),
                    'end': [round(v, 2) for v in reached],
                    'progress': round(progress, 2),
                    'goal_distance': round(remaining, 2),
                    'bearing_degrees': round(math.degrees(angle) % 360, 1)}
        if angle == bearing and end is goal:
            break
    return best if best is not None else fallback


def travel(backend, *, report, goal, legs=6, radius=40.0, stop_distance=60.0,
           sleep=time.sleep, clock=time.monotonic):
    """Walk towards the goal, one leg at a time, observing after each leg.

    Stops early and reports when the character is hurt or engaged: the travel
    never attacks back (docs/73).
    """
    legs_taken = []
    reached = False
    detours = 0
    visited = []
    for index in range(legs):
        start = list((backend.submitted_position or backend.current_position
                      or backend.observed_position or [])[:3])
        if not start:
            break
        distance_to_goal = math.dist(start[:3], list(goal)[:3])
        if distance_to_goal <= stop_distance:
            reached = True
            break
        leg = plan_leg(backend, goal, radius=radius,
                       allow_detour=detours < 3, visited=visited)
        if leg is None:
            legs_taken.append({'index': index, 'planned': False,
                                'reason': 'no walkable leg towards the goal'})
            break
        visited.append(list(leg['end']))
        replies, failure = backend._send_path(leg['points'])
        legs_taken.append({'index': index, 'planned': True, 'length': leg['length'],
                           'end': leg['end'],
                           'progress': leg['progress'],
                           'detour': bool(leg.get('detour')),
                           'goal_distance_after': leg['goal_distance'],
                           'chunks': len(replies), 'failure': failure})
        if failure:
            break
        if leg.get('detour'):
            # A detour is a sideways move to get around ground that refuses
            # the direct line. Several may be needed, but the character must
            # not walk back over ground it has already stood on: that was an
            # endless two-position loop (2026-11-01 travel3125a: legs 4..17
            # alternated +42.8 and -42.8 yards of progress).
            detours += 1
        sleep(2.0)
        state = backend.drain()
        me = state['self']
        health = me.get('health')
        legs_taken[-1]['health_after'] = health
        legs_taken[-1]['in_combat_after'] = me.get('in_combat')
        if me.get('in_combat') is True:
            # Stopping is not enough: a character that stands in combat is
            # killed, because the server keeps hitting it and closing the
            # session does not stop that (2026-11-01 travelfarm2 leg 4: the
            # travel stopped at 109 health and the character was dead).
            # The verified way out is the escape branch: run out of the
            # hostile's reach, which the server answers by dropping the
            # target (docs/kb/mechanisms/机制-怪何时真正放弃-NO_PATH).
            legs_taken[-1]['stopped'] = 'combat engaged; leaving reach'
            try:
                from agent_wow_contract import Action
                evidence = backend.execute(
                    Action('recover_combat', 'recover_combat', {'budget': 60},
                           timeout=90, side_effecting=True))
            except Exception as exc:  # noqa: BLE001 - the travel must still finish
                evidence = {'error': f'{type(exc).__name__}: {exc}'}
            legs_taken[-1]['escape'] = evidence
            break
        if health is not None and health <= 0:
            legs_taken[-1]['stopped'] = 'the character died'
            break
    report['travel'] = {'goal': [round(v, 2) for v in goal], 'legs': legs_taken,
                        'reached_goal_area': reached}
    return report['travel']
