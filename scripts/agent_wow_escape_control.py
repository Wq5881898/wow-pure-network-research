"""Stop and traverse a previously planned return path; never assert leash reset.

The bridge validates every path it is given: each segment must be at most 15
yards long with at most a 3-yard height change, and the whole path at most 50
yards (bridge modules/bridge/actions.go, validatePath). A path that breaks
those rules is rejected outright, so the walk is sent in the sizes the bridge
accepts.
"""
import math
import time

MAX_SEGMENT_YARDS = 15.0
MAX_TOTAL_YARDS = 50.0
MAX_HEIGHT_YARDS = 3.0
MAX_POINTS = 128
MAX_MIN_YARDS = 0.05


def _chunk_ok(chunk, anchor, max_segment=MAX_SEGMENT_YARDS,
              max_total=MAX_TOTAL_YARDS, max_height=MAX_HEIGHT_YARDS,
              require_minimum=True):
    """True when the bridge would accept this chunk from this anchor.

    This is the bridge's own rule (probes/AgentWowBridge/actions.go,
    validatePath): the first segment runs from the bridge's tracked position,
    every segment is at most 15 yards long with at most a 3 yard height
    change, and the whole path is between .05 and 50 yards.

    The minimum length belongs to the finished chunk, not to a chunk that is
    still being built: the first point of a route is often the character's
    own position, and that zero length segment must not stop the rest of
    the route from being added.
    """
    if not chunk or len(chunk) > MAX_POINTS:
        return False
    previous = list(anchor[:3])
    total = 0.0
    for point in chunk:
        xyz = (point['x'], point['y'], point['z'])
        distance = math.hypot(xyz[0] - previous[0], xyz[1] - previous[1])
        if distance > max_segment or abs(xyz[2] - previous[2]) > max_height:
            return False
        total += distance
        previous = list(xyz)
    if total > max_total:
        return False
    return (not require_minimum) or total >= MAX_MIN_YARDS


def _subdivide(points, max_segment=MAX_SEGMENT_YARDS, max_height=MAX_HEIGHT_YARDS):
    """Split every step that the bridge would refuse.

    The bridge refuses a segment over 15 yards or a height change over 3
    yards. Both are per-segment limits, so a straight step can be split
    into several legal ones without changing where the character walks.
    """
    out = []
    previous = None
    for point in points:
        if previous is None:
            out.append(dict(point))
            previous = point
            continue
        horizontal = math.hypot(point['x'] - previous['x'],
                                point['y'] - previous['y'])
        climb = abs(point['z'] - previous['z'])
        count = max(1,
                    math.ceil(horizontal / (max_segment * 0.5)) if horizontal else 1,
                    math.ceil(climb / (max_height * 0.5)) if climb else 1)
        for index in range(1, count + 1):
            fraction = index / count
            out.append({'x': previous['x'] + (point['x'] - previous['x']) * fraction,
                        'y': previous['y'] + (point['y'] - previous['y']) * fraction,
                        'z': previous['z'] + (point['z'] - previous['z']) * fraction})
        previous = point
    return out


def _chunks(points, origin=None, max_segment=MAX_SEGMENT_YARDS,
            max_total=MAX_TOTAL_YARDS, max_height=MAX_HEIGHT_YARDS):
    """Split a route into the pieces the bridge accepts.

    The split is driven by the bridge's rule, not by a length estimate: a
    point is only added to the current chunk when the whole chunk still
    passes _chunk_ok from the position the bridge will validate it against
    (the character's own position for the first chunk, the end of the
    previous chunk for the rest).

    Two earlier versions of this function failed in the field, both times
    because the length was counted differently from the bridge's:
      - the first segment from the character to the first point of the route
        was left out, so a chunk of 50 yards looked like 51.09 to the bridge
        and was refused ("path length must be .05..50 yards", 2026-11-01
        b29_u1, b36_u1);
      - a chunk that was cut to a single point has a length of 0 and is
        refused for the same reason (2026-11-01 b39_u1).
    A chunk that the bridge would refuse is worse than no chunk at all: the
    character does not move and keeps taking hits.
    """
    chunks = []
    current = []
    anchor = list(origin[:3]) if origin else None
    if anchor is not None:
        current = [{'x': anchor[0], 'y': anchor[1], 'z': anchor[2]}]
    for point in _subdivide(points, max_segment, max_height):
        if anchor is None:
            anchor = [point['x'], point['y'], point['z']]
            current = [{'x': anchor[0], 'y': anchor[1], 'z': anchor[2]}]
        if _chunk_ok(current + [point], anchor, max_segment, max_total, max_height, require_minimum=False):
            current.append(point)
            continue
        if len(current) > 1:
            if _chunk_ok(current, anchor):
                chunks.append(current)
            # The anchor always moves on, even when the chunk was dropped:
            # a chunk the bridge would refuse must not freeze the rest of
            # the route.
            anchor = [current[-1]['x'], current[-1]['y'], current[-1]['z']]
            current = [{'x': anchor[0], 'y': anchor[1], 'z': anchor[2]}]
        if _chunk_ok(current + [point], anchor, max_segment, max_total, max_height, require_minimum=False):
            current.append(point)
        # A point that does not fit even from the new anchor is skipped: the
        # bridge would refuse the whole path for it, and skipping keeps the
        # rest of the route walkable.
    if len(current) > 1 and _chunk_ok(current, anchor):
        chunks.append(current)
    return chunks


def retreat(rpc, drain, outbound_points, origin, wait_seconds=15,
            sleep=time.sleep, clock=time.monotonic, route=None):
    # Reverse the actual submitted route rather than invent a straight escape,
    # unless the caller already planned an ordered escape route (a route that
    # ends far from the hostile, planned on the navmesh).
    if route is None:
        # The reversed outbound route, walked back to where the character
        # started. The far end of the outbound route has to be kept:
        # dropping it left a route that starts and ends on the same spot,
        # and a path like that is refused by the bridge (its length is 0),
        # so the character did not move at all.
        points = list(reversed(outbound_points)) + [dict(zip(('x', 'y', 'z'), origin[:3]))]
        source = 'reversed submitted navmesh route'
    else:
        points = list(route)
        source = 'navmesh route planned away from the hostile'
    result = {'stop': rpc('bridge.stop_attack', {}), 'path_source': source, 'points': points}
    # The bridge rejects a path whose segments or total length are too long,
    # so the walk is submitted in the sizes it accepts.
    sent = []
    rejected = []
    for chunk in _chunks(points, origin):
        reply = rpc('bridge.follow_path', {'points': chunk, 'run': True})
        sent.append(reply)
        if 'error' in reply:
            rejected.append(reply['error'].get('message', 'path rejected'))
            break
    result['movement'] = sent
    result['chunks_sent'] = len(sent)
    if rejected:
        result['outcome'] = 'retreat_movement_rejected'
        result['rejection'] = rejected[-1]
        return result, drain()
    deadline = clock() + wait_seconds
    result['samples'] = []
    while True:
        state = drain(); me = state['self']
        result['samples'].append({'hp': me['health'], 'ghost': me['ghost'], 'in_combat': me['in_combat']})
        if me['health'] == 0 or me['ghost']: result['outcome'] = 'dead'; break
        if me['in_combat'] is False: result['outcome'] = 'out_of_combat'; break
        if clock() >= deadline: result['outcome'] = 'still_in_combat'; break
        sleep(.3)
    return result, state
