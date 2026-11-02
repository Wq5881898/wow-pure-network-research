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


def _chunks(points, max_segment=MAX_SEGMENT_YARDS, max_total=MAX_TOTAL_YARDS,
             max_height=MAX_HEIGHT_YARDS):
    """Split a route into the pieces the bridge accepts.

    The bridge also refuses a segment whose height change is over 3 yards,
    and it refuses the WHOLE path when one segment breaks a rule. The
    height change is checked here for the same reason the length is: a
    rejected path means the character does not move at all, which is how a
    retreat turned into standing still and being killed (2026-11-01 b29_u1:
    the escape route of 208 yards was planned and then refused outright).
    """
    chunks = []
    current = []
    length = 0.0
    previous = None
    for point in points:
        xyz = (point['x'], point['y'], point['z'])
        if previous is None:
            previous = xyz
        step = math.dist(previous, xyz)
        climb = abs(xyz[2] - previous[2])
        if current and (step > max_segment or length + step > max_total
                        or climb > max_height):
            chunks.append(current)
            current = []
            length = 0.0
        current.append(point)
        length += step
        previous = xyz
    if current:
        chunks.append(current)
    return chunks


def retreat(rpc, drain, outbound_points, origin, wait_seconds=15,
            sleep=time.sleep, clock=time.monotonic, route=None):
    # Reverse the actual submitted route rather than invent a straight escape,
    # unless the caller already planned an ordered escape route (a route that
    # ends far from the hostile, planned on the navmesh).
    if route is None:
        points = list(reversed(outbound_points[:-1])) + [dict(zip(('x', 'y', 'z'), origin[:3]))]
        source = 'reversed submitted navmesh route'
    else:
        points = list(route)
        source = 'navmesh route planned away from the hostile'
    result = {'stop': rpc('bridge.stop_attack', {}), 'path_source': source, 'points': points}
    # The bridge rejects a path whose segments or total length are too long,
    # so the walk is submitted in the sizes it accepts.
    sent = []
    rejected = []
    for chunk in _chunks(points):
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
