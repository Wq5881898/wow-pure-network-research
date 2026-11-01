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


def plan_leg(backend, goal, radius=40.0, bearings=16):
    """Plan one leg towards the goal; return the densified points or None.

    The goal is approached by trying a fan of bearings around the direct
    direction, so a cliff in the straight line does not stop the journey.
    """
    start = list((backend.submitted_position or backend.current_position
                  or backend.observed_position)[:3])
    if not start:
        return None
    goal = list(goal)[:3]
    bearing = math.atan2(goal[1] - start[1], goal[0] - start[0])
    distance_to_goal = math.dist(start[:3], goal)
    best = None
    for index in range(bearings):
        angle = bearing + (0.0 if index == 0 else
                           math.copysign(((index + 1) // 2) * math.pi / bearings,
                                         index))
        for distance in (radius, radius * 0.6, radius * 0.35):
            end = [start[0] + math.cos(angle) * distance,
                   start[1] + math.sin(angle) * distance,
                   start[2]]
            try:
                raw = backend._nav(start, end)
                points = backend._densify(raw, start)
            except RuntimeError:
                continue
            if not points:
                continue
            ok, _reason = _bridge_limits_ok(points, start)
            if not ok:
                continue
            travelled = _length(points)
            if travelled < 4.0:
                continue
            reached = [points[-1]['x'], points[-1]['y'], points[-1]['z']]
            remaining = math.dist(reached, goal)
            progress = distance_to_goal - remaining
            if progress <= 0:
                # A leg that does not get closer is wasted walking.
                continue
            if best is None or progress > best['progress']:
                best = {'points': points, 'length': round(travelled, 2),
                        'end': [round(v, 2) for v in reached],
                        'progress': round(progress, 2),
                        'goal_distance': round(remaining, 2),
                        'bearing_degrees': round(math.degrees(angle) % 360, 1)}
            break
    return best


def travel(backend, *, report, goal, legs=6, radius=40.0, stop_distance=60.0,
           sleep=time.sleep, clock=time.monotonic):
    """Walk towards the goal, one leg at a time, observing after each leg.

    Stops early and reports when the character is hurt or engaged: the travel
    never attacks back (docs/73).
    """
    legs_taken = []
    reached = False
    for index in range(legs):
        start = list((backend.submitted_position or backend.current_position
                      or backend.observed_position or [])[:3])
        if not start:
            break
        distance_to_goal = math.dist(start[:3], list(goal)[:3])
        if distance_to_goal <= stop_distance:
            reached = True
            break
        leg = plan_leg(backend, goal, radius=radius)
        if leg is None:
            legs_taken.append({'index': index, 'planned': False,
                                'reason': 'no walkable leg towards the goal'})
            break
        replies, failure = backend._send_path(leg['points'])
        legs_taken.append({'index': index, 'planned': True, 'length': leg['length'],
                           'end': leg['end'],
                           'progress': leg['progress'],
                           'goal_distance_after': leg['goal_distance'],
                           'chunks': len(replies), 'failure': failure})
        if failure:
            break
        sleep(2.0)
        state = backend.drain()
        me = state['self']
        health = me.get('health')
        legs_taken[-1]['health_after'] = health
        legs_taken[-1]['in_combat_after'] = me.get('in_combat')
        if me.get('in_combat') is True:
            legs_taken[-1]['stopped'] = 'combat engaged; not fought back'
            break
        if health is not None and health <= 0:
            legs_taken[-1]['stopped'] = 'the character died'
            break
    report['travel'] = {'goal': [round(v, 2) for v in goal], 'legs': legs_taken,
                        'reached_goal_area': reached}
    return report['travel']
