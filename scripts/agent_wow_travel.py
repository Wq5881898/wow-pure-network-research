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
from contextlib import nullcontext


def _length(points):
    total = 0.0
    previous = None
    for point in points:
        xyz = [point['x'], point['y'], point['z']]
        if previous is not None:
            total += math.dist(previous[:2], xyz[:2])
        previous = xyz
    return total


def movement_batches(points, start, distance=7.0):
    """Preserve terrain samples; poll about once per second of running."""
    batch = []
    previous = list(start)[:3]
    length = 0.0
    for point in points:
        xyz = [point[k] for k in ('x','y','z')]
        edge = math.dist(previous[:2],xyz[:2])
        if batch and (length+edge > distance or len(batch) >= 100):
            yield batch
            batch = []
            length = 0.0
        batch.append(point)
        length += edge
        previous = xyz
    if batch:
        yield batch


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


def _bounded_leg(points, start, max_length):
    """Clip a navmesh polyline, including origin-to-first-point distance."""
    if not math.isfinite(max_length) or max_length <= 0:
        raise ValueError('positive finite leg length required')
    result = []
    previous = list(start)[:3]
    remaining = max_length
    for point in points:
        end = [point['x'], point['y'], point['z']]
        distance = math.dist(previous, end)
        if distance > remaining:
            fraction = remaining / distance
            result.append(dict(zip(('x', 'y', 'z'),
                                   [a + (b - a) * fraction for a, b in zip(previous, end)])))
            break
        result.append(dict(point))
        remaining -= distance
        previous = end
        if remaining <= 1e-8:
            break
    return result


def _distance_to_segment(point, a, b):
    delta = [y-x for x,y in zip(a,b)]
    length2 = sum(x*x for x in delta)
    fraction = max(0, min(1, sum((x-y)*d for x,y,d in zip(point,a,delta))/length2)) if length2 else 0
    return math.dist(point, [x+fraction*d for x,d in zip(a,delta)])


def _same_return_geometry(reference, candidate, tolerance=.25):
    """Symmetric 3D polyline check; long candidate chords cannot skip corners."""
    def covered(source, target):
        if len(target) < 2:
            return False
        for a,b in zip(source, source[1:]):
            steps = max(1, math.ceil(math.dist(a,b)/.2))
            for i in range(steps+1):
                p = [x+(y-x)*i/steps for x,y in zip(a,b)]
                if min(_distance_to_segment(p,c,d) for c,d in zip(target,target[1:])) > tolerance:
                    return False
        return True
    return covered(reference,candidate) and covered(candidate,reference)


def prepare_return(backend, corridor, planned_points):
    """Validate every reversed submitted edge before entering the next leg.

    Retain the actual outbound polyline, not a newly invented straight line.
    Reverse nav queries validate direction; their geometry must stay near that
    edge. No terrain/path limit is relaxed by this check.
    """
    combined = [list(p)[:3] for p in corridor]
    combined.extend([p['x'], p['y'], p['z']] for p in planned_points)
    # Earlier legs were checked before their own dispatch; do not repeatedly
    # spend navigation time revalidating the whole accumulated corridor.
    new_edges = [combined[len(corridor)-1], *combined[len(corridor):]]
    # Fast path only for an independently queried reverse with the SAME 3D
    # geometry. A detour or corner cut falls back to the existing edge checks.
    if len(new_edges) > 3 and math.dist(new_edges[0],new_edges[-1]) > 1:
        try:
            end, start = new_edges[-1], new_edges[0]
            raw = backend._nav(end,start)
            raw = {**raw, 'waypoints': raw.get('surface_samples') or raw['waypoints']}
            validated = backend._densify(raw,end,slope_tolerance=.7)
            reverse = [end] + [[p[k] for k in ('x','y','z')] for p in validated]
            original = list(reversed(new_edges))
            if (validated and math.dist(reverse[-1],start) <= .25
                    and _same_return_geometry(original,reverse)
                    and _bridge_limits_ok(validated,end)[0]
                    and _bridge_limits_ok([dict(zip(('x','y','z'),p)) for p in original],end)[0]):
                return combined, [dict(zip(('x','y','z'),p)) for p in reversed(combined)]
        except RuntimeError:
            pass
    for end, start in zip(reversed(new_edges[1:]), reversed(new_edges[:-1])):
        if math.dist(end, start) < .05:
            continue
        try:
            raw = backend._nav(end, start)
        except RuntimeError as exc:
            raise RuntimeError(f'reverse edge end={end} start={start}: {exc}') from exc
        points = backend._densify(raw, end, slope_tolerance=.7)
        if not points:
            # Login Z and the same local nav surface can differ by centimetres.
            # Accept only an explicitly projected, coincident XY edge with a
            # small vertical correction; an arbitrary empty route still fails.
            waypoints = raw.get('waypoints') or []
            if (waypoints and math.dist(end[:2], start[:2]) < .01
                    and abs(end[2] - start[2]) <= .2
                    and math.dist(list(waypoints[-1])[:3], start) <= .2):
                continue
            # A zero-XY surface correction may exceed the older 0.2m special
            # case. Require independent forward AND reverse queries to resolve
            # to the same single surface, within the existing 1m endpoint
            # tolerance. This is not permission to traverse a vertical edge.
            if (waypoints and len(waypoints) == 1
                    and math.dist(end[:2], start[:2]) < .01
                    and abs(end[2] - start[2]) <= 1):
                forward = backend._nav(start, end).get('waypoints') or []
                if (len(forward) == 1
                        and math.dist(list(forward[0])[:3], list(waypoints[0])[:3]) < .01
                        and math.dist(list(waypoints[0])[:3], end) < .01):
                    continue
            raise RuntimeError('reverse edge has no validated points: '
                               f'end={end} start={start} projected={waypoints}')
        if math.dist([points[-1][k] for k in ('x', 'y', 'z')], start) > 1:
            raise RuntimeError('reverse edge did not reach original corridor')
        # Avoid accepting a reverse query that silently detours to another floor.
        if any(math.dist([p[k] for k in ('x', 'y', 'z')], end)
               + math.dist([p[k] for k in ('x', 'y', 'z')], start)
               > math.dist(end, start) + 3 for p in points):
            raise RuntimeError('reverse edge leaves original corridor')
        ok, reason = _bridge_limits_ok(points, end)
        if not ok:
            raise RuntimeError(reason)
    return combined, [dict(zip(('x', 'y', 'z'), p)) for p in reversed(combined)]


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
            # Validate only the leg we will actually submit. A cliff hundreds
            # of yards later must not veto the safe prefix of a distant route.
            # The probe's connected detail-surface samples carry the actual
            # terrain height. Interpolating only portal vertices can cut through
            # a slope, producing points rejected by the reverse endpoint gate.
            vertices = raw.get('surface_samples') or raw['waypoints']
            prefix = _bounded_leg([dict(zip(('x', 'y', 'z'), p[:3]))
                                    for p in vertices], start, radius)
            raw = {**raw, 'waypoints': [[p['x'], p['y'], p['z']] for p in prefix]}
            # The travel judges a drop by its slope, so a ramp is walkable and
            # only a real cliff is refused. The combat approach keeps the
            # stricter 5 yard rule.
            points = backend._densify(raw, start, slope_tolerance=0.7)
            # The distant goal candidate must not turn one leg into a whole
            # journey. Return to the travel observer at least every radius.
            points = _bounded_leg(points, start, radius)
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
           sleep=time.sleep, clock=time.monotonic, combat_handoff=None):
    """Walk towards the goal, one leg at a time, observing after each leg.

    Stops early and reports when the character is hurt or engaged: the travel
    never attacks back (docs/73).
    """
    legs_taken = []
    reached = False
    detours = 0
    visited = []
    corridor = None
    for index in range(legs):
        start = list((backend.submitted_position or backend.current_position
                      or backend.observed_position or [])[:3])
        if not start:
            break
        if corridor is None:
            corridor = [list(start)]
        distance_to_goal = math.dist(start[:3], list(goal)[:3])
        if distance_to_goal <= stop_distance:
            reached = True
            break
        activity = getattr(backend, 'activity', None)
        with (activity.phase('travel_leg_planning', leg=index) if activity else nullcontext()):
            leg = plan_leg(backend, goal, radius=radius,
                           allow_detour=detours < 3, visited=visited)
        if leg is None:
            legs_taken.append({'index': index, 'planned': False,
                                'reason': 'no walkable leg towards the goal'})
            break
        visited.append(list(leg['end']))
        try:
            with (activity.phase('return_route_precheck', leg=index) if activity else nullcontext()):
                next_corridor, return_points = prepare_return(backend, corridor, leg['points'])
        except RuntimeError as exc:
            failures = [str(exc)]
            recovered = False
            for shorter in (radius / 2, radius / 4):
                if shorter < 4:
                    continue
                candidate = plan_leg(backend, goal, radius=shorter,
                                     allow_detour=False, visited=visited)
                if candidate is None:
                    continue
                try:
                    next_corridor, return_points = prepare_return(backend, corridor, candidate['points'])
                except RuntimeError as retry_exc:
                    failures.append(str(retry_exc))
                    continue
                leg = candidate
                recovered = True
                break
            report.setdefault('return_precheck_retries', []).append(
                {'leg': index, 'errors': failures, 'shorter_leg_checked': recovered})
            if not recovered:
                legs_taken.append({'index': index, 'planned': False,
                                    'reason': 'return route precheck failed: ' + str(exc)})
                break
        # Persist the fallback before any new movement. It does not need a
        # hostile GUID and remains available to the exit path.
        backend.travel_return_route = {'points': [dict(zip(('x','y','z'), p)) for p in reversed(corridor)], 'origin': list(start),
                                       'safe_anchor': list(corridor[0]),
                                       'source': 'prevalidated reversed outbound corridor'}
        report['travel_return_plan'] = backend.travel_return_route
        before = list((backend.submitted_position or backend.current_position
                       or backend.observed_position or [])[:3])
        replies, failure = [], None
        # Consume state after each short edge, not only after the entire leg.
        # The prevalidated full return is authorization; the active return
        # contains ONLY edges actually submitted, never the unvisited suffix.
        previous = list(start)
        for batch in movement_batches(leg['points'], previous):
            point = batch[-1]
            destination = [point[k] for k in ('x','y','z')]
            if math.dist(previous[:2], destination[:2]) < .05:
                continue
            sent, failure = backend._send_path([dict(zip(('x','y','z'), previous)), *batch], run=True)
            replies.extend(sent)
            landed = list((backend.submitted_position or previous)[:3])
            if failure:
                break
            if math.dist(landed, destination) > 1:
                failure = 'submitted endpoint differs from validated edge; reconcile return'
                break
            corridor.extend([[p[k] for k in ('x','y','z')] for p in batch])
            corridor[-1] = list(landed)
            previous = landed
            return_points = [dict(zip(('x','y','z'), p)) for p in reversed(corridor)]
            backend.travel_return_route = {**backend.travel_return_route,
                                           'points': return_points, 'origin': landed}
            report['travel_return_plan'] = backend.travel_return_route
            state = backend.drain()
            me = state['self']
            if me.get('health') is None or me['health'] <= 0 or me.get('ghost'):
                failure = 'life state unavailable or dead during travel'
                break
            if me.get('in_combat') is not False:
                break
            history = getattr(backend, 'relative_safe_history', None)
            if history is None:
                history = []
                backend.relative_safe_history = history
            history.append({'position': list(landed), 'sampled_at': time.time(),
                            'health': me['health'], 'in_combat': False,
                            'semantics': 'submitted endpoint; no combat observed at sample',
                            'corridor_points': len(corridor)})
            report['relative_safe_history'] = history
        after = list((backend.submitted_position or backend.current_position
                      or backend.observed_position or [])[:3])
        advanced = math.dist(before[:2], after[:2]) if (before and after) else 0.0
        blocked = bool(leg['points']) and advanced < 1.0 and not failure
        legs_taken.append({'index': index, 'planned': True, 'length': leg['length'],
                           'end': leg['end'],
                           'progress': leg['progress'],
                           'detour': bool(leg.get('detour')),
                           'goal_distance_after': leg['goal_distance'],
                           'chunks': len(replies), 'failure': failure,
                           'moved_yards': round(advanced, 2),
                           'blocked': blocked})
        if blocked:
            # The character did not advance: the route is blocked where it is.
            # Walking it again only walks deeper into whatever it is stuck
            # against (2026-11-01: the character was seen walking forward
            # inside a building, forever).
            legs_taken[-1]['stopped'] = ('the character did not advance; '
                                         'the route is blocked here')
            break
        if leg.get('detour'):
            # A detour is a sideways move to get around ground that refuses
            # the direct line. Several may be needed, but the character must
            # not walk back over ground it has already stood on: that was an
            # endless two-position loop (2026-11-01 travel3125a: legs 4..17
            # alternated +42.8 and -42.8 yards of progress).
            detours += 1
        state = backend.drain()
        me = state['self']
        health = me.get('health')
        legs_taken[-1]['health_after'] = health
        legs_taken[-1]['in_combat_after'] = me.get('in_combat')
        if me.get('in_combat') is not False:
            if combat_handoff is not None and combat_handoff(state):
                legs_taken[-1]['stopped'] = 'planned attacker handed to combat controller'
                report['combat_handoff'] = True
                break
            # Stopping is not enough: a character that stands in combat is
            # killed, because the server keeps hitting it and closing the
            # session does not stop that (2026-11-01 travelfarm2 leg 4: the
            # travel stopped at 109 health and the character was dead).
            # The verified way out is the escape branch: run out of the
            # hostile's reach, which the server answers by dropping the
            # target (docs/kb/mechanisms/机制-怪何时真正放弃-NO_PATH).
            #
            # An UNKNOWN combat state counts as "may be in combat": walking on
            # while the state is unresolved risks leaving the character in the
            # middle of a fight. The recovery branch reports an unresolved
            # state as unresolved instead of pretending it is clear.
            legs_taken[-1]['stopped'] = 'combat engaged or unresolved; leaving reach'
            try:
                from agent_wow_escape_control import retreat
                # Retreat immediately. No target selection, offensive fallback
                # or passive wait while the hostile continues attacking.
                current = list((backend.submitted_position or backend.current_position
                                or backend.observed_position)[:3])
                evidence, _ = retreat(backend.rpc, backend.drain, return_points,
                                      current, route=return_points, wait_seconds=20,
                                       sleep=sleep, clock=clock)
                from agent_wow_exit_recovery import _actual_landing
                landing = _actual_landing(evidence.get('movement'))
                if landing:
                    backend.submitted_position = list(landing)
                    backend.current_position = list(landing)
            except Exception as exc:  # noqa: BLE001 - the travel must still finish
                evidence = {'error': f'{type(exc).__name__}: {exc}'}
            legs_taken[-1]['escape'] = evidence
            break
        if health is not None and health <= 0:
            legs_taken[-1]['stopped'] = 'the character died'
            break
        if failure:
            break
    # The last permitted leg can reach the goal without another loop iteration.
    # This remains submitted/model position evidence; independent relog confirms
    # actual arrival, as in resume_step/resume_verify_20261106_a.
    end = list((backend.submitted_position or backend.current_position
                or backend.observed_position or [])[:3])
    if end and (not legs_taken or not legs_taken[-1].get('stopped')):
        reached = math.dist(end, list(goal)[:3]) <= stop_distance
    report['travel'] = {'goal': [round(v, 2) for v in goal], 'legs': legs_taken,
                        'reached_goal_area': reached}
    return report['travel']
