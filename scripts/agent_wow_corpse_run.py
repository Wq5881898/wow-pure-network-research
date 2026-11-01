"""Danger-zone corpse run: ghost -> corpse -> reclaim -> move away.

Why this exists: on 2026-11-01 several deaths happened in the same place,
the scorpid pack about 91 yards from the graveyard. Every one of them was
recovered with the Spirit Healer. The Spirit Healer is a separate recovery
means: it puts the character back at the graveyard and says nothing about
whether the corpse can be reached. Running to the corpse is the question, and
it needs its own implementation.

The bridge already speaks the whole chain:
    bridge.query_corpse    (0x216) -> the server reports the corpse position
    bridge.reclaim_corpse  (0x1D2) -> revive where the body is
    bridge.release_spirit  (0x15A) -> release to the graveyard
    bridge.spirit_healer   (0x21C) -> the separate healer path

Measured on 2026-11-01 (corpse02), the first corpse run ever run here:
    ghost at the graveyard        (-634.64, -4296.03, 40.53)
    corpse reported by the server (-708.35, -4348.38, 49.03), 90.81 yd away
    navmesh route                16 waypoints, 94.35 yd (90.81 yd direct)
    walk                         4 chunks, no rejection
    reclaim                      alive at 54/108, no longer a ghost
    fresh login                  108/108, alive, out of combat

The route is planned on the navmesh, never as a straight line: a ghost that
walks a chord over a ledge falls, and the corpse would then be further away
than before (docs/51).
"""
import math
import time


def corpse_position_from_events(events):
    """The corpse position the server reported, or None.

    The server answers a corpse query with the corpse's map and position.
    The position is the server's own answer, never an estimate.
    """
    for event in events or ():
        if event.get('kind') != 'corpse':
            continue
        corpse = event.get('corpse') or {}
        if corpse.get('position_xyz'):
            return corpse
    return None


def plan_route(backend, start, destination):
    """Plan a navmesh route and densify it for the bridge.

    Returns None when no route exists. A straight line is never substituted:
    it would walk the character over ledges (docs/51).
    """
    try:
        raw = backend._nav(list(start)[:3], list(destination)[:3])
        points = backend._densify(raw, list(start)[:3])
    except RuntimeError:
        return None
    if not points:
        return None
    length = 0.0
    previous = list(start)[:3]
    for point in points:
        length += math.dist(previous, [point['x'], point['y'], point['z']])
        previous = [point['x'], point['y'], point['z']]
    return {'points': [{'x': p['x'], 'y': p['y'], 'z': p['z']} for p in points],
            'length': round(length, 3),
            'direct_distance': round(math.dist(list(start)[:3], list(destination)[:3]), 3),
            'waypoints': len(raw.get('waypoints', []))}


def run_corpse_run(backend, *, report, move_away_to=None, poll_interval=0.4,
                   query_timeout=15.0, walk_timeout=60.0, reclaim_timeout=20.0,
                   sleep=time.sleep, clock=time.monotonic):
    """Reclaim the corpse: query it, walk to it, reclaim it, then move away.

    The character must already be a ghost. The session must stay open: the
    corpse query is a request/answer inside the session, and a session torn
    down with the character dead leaves the corpse unreachably reported.
    """
    result = {'status': 'unknown'}
    state = backend.drain()
    me = state['self']
    ghost = list((me.get('movement') or {}).get('position_xyzo') or [])[:3]
    result['ghost_position'] = ghost
    if not me.get('ghost'):
        result['status'] = 'not_a_ghost'
        result['reason'] = 'the character is not a ghost; there is no corpse to run to'
        report['corpse_run'] = result
        return result

    # The corpse object carries the player's own guid, so it is identified by
    # guid and never by a guessed value.
    corpses = [o for o in state.get('visible_objects', []) if o.get('type_id') == 4]
    result['corpse_objects'] = [str(o['guid']) for o in corpses]

    # --- ask the server where the corpse is -------------------------------
    result['query_corpse'] = backend.rpc('bridge.query_corpse', {})
    deadline = clock() + query_timeout
    corpse = None
    while clock() < deadline:
        state = backend.drain()
        corpse = corpse_position_from_events(state.get('event_log'))
        if corpse:
            break
        sleep(poll_interval)
    result['corpse'] = corpse
    if not corpse:
        result['status'] = 'no_corpse_reported'
        result['reason'] = 'the server reported no corpse position'
        report['corpse_run'] = result
        return result
    corpse_position = list(corpse['position_xyz'])
    result['corpse_position'] = [round(v, 3) for v in corpse_position]
    result['corpse_distance_from_ghost'] = round(
        math.dist(ghost, corpse_position), 3)

    # --- walk the navmesh route to it --------------------------------------
    route = plan_route(backend, ghost, corpse_position)
    result['route'] = route
    if route is None:
        result['status'] = 'no_route_to_corpse'
        result['reason'] = 'no walkable route from the ghost to the corpse'
        report['corpse_run'] = result
        return result
    replies, failure = backend._send_path(route['points'])
    result['walk_chunks'] = len(replies)
    result['walk_failure'] = failure
    if failure:
        result['status'] = 'walk_rejected'
        report['corpse_run'] = result
        return result

    deadline = clock() + walk_timeout
    while clock() < deadline:
        sleep(poll_interval)
        state = backend.drain()
        me = state['self']
        if not me.get('ghost') and (me.get('health') or 0) > 0:
            break

    # --- reclaim the corpse ------------------------------------------------
    result['reclaim_corpse'] = backend.rpc('bridge.reclaim_corpse',
                                            {'guid': str(backend.self_guid)})
    deadline = clock() + reclaim_timeout
    while clock() < deadline:
        state = backend.drain()
        me = state['self']
        if not me.get('ghost') and (me.get('health') or 0) > 0:
            break
        sleep(poll_interval)
    final = backend.drain()['self']
    result['after_reclaim'] = {'health': final.get('health'),
                               'ghost': final.get('ghost'),
                               'in_combat': final.get('in_combat')}
    result['reclaimed'] = (not final.get('ghost') and (final.get('health') or 0) > 0)
    if not result['reclaimed']:
        result['status'] = 'not_reclaimed'
        report['corpse_run'] = result
        return result

    # --- move away immediately, on a navmesh route -------------------------
    # Standing still where the character revived puts it back in the hostile
    # pack it just died in. The destination is a known walkable point, never
    # a scaled straight line: an off-mesh point has no endpoint polygon.
    destination = move_away_to
    if destination is None:
        destination = ghost
    away = plan_route(backend, final.get('movement', {}).get('position_xyzo', [])[:3],
                       destination)
    result['move_away_route'] = away
    if away:
        replies, failure = backend._send_path(away['points'])
        result['move_away_chunks'] = len(replies)
        result['move_away_failure'] = failure
        sleep(2.0)
    after = backend.drain()['self']
    result['after_escape'] = {'health': after.get('health'),
                              'ghost': after.get('ghost'),
                              'in_combat': after.get('in_combat')}
    result['status'] = 'reclaimed'
    report['corpse_run'] = result
    return result
