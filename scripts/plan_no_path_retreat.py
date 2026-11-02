"""Find a retreat destination that the HOSTILE cannot path to.

Why this exists (2026-11-01):
  The server clears a combat when the hostile's path to the victim becomes
  unreachable -- upstream calls it EVADE_REASON_NO_PATH
  (src/server/game/AI/UnitAI.cpp -> CreatureAI::_EnterEvadeMode()).
  The leash radius (30 yards) and the leash timer (11 s) that
  Creature::CanCreatureAttack() uses govern whether a creature may ACQUIRE a
  target again, NOT when it lets go of one it already has.

  Measured on b24_u1: the character stopped attacking, ended 40.2 yards from
  the hostile's spawn and waited 20.2 s -- both leash conditions satisfied --
  and the hostile still held the combat. The navmesh confirms why: a path from
  the fight area to that spot EXISTS. So "run far away" is not a retreat; the
  destination has to be somewhere the hostile cannot reach.

This module is offline and side-effect free: it only queries the navmesh. The
nav query is injectable so the search can be unit tested without the server.

The hostile's position is taken from its static spawn field
(movement.position_xyzo). For the mobs used so far that field never changed
(62 consecutive records), so it is a sound stand-in for the hostile's current
position, but it is an approximation: a chasing hostile has moved away from
its spawn. Every result therefore records the hostile position it used.
"""
import json
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAV_EXE = ROOT / '.cache/nav-height-build/agent_wow_nav.exe'
MMAPS_DIR = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps')

# The bridge refuses a path longer than 50 yards per request and a segment
# longer than 15 yards, so a single hop may not exceed this.
MAX_HOP_YARDS = 15.0
# How far from the character a destination may be. The bridge limit is the
# hard one; this keeps one dispatch within the reachable set.
MAX_TOTAL_YARDS = 45.0


def nav_query(start, end, map_id=1, timeout=30):
    """Query the navmesh. Returns {'ok', 'waypoints', 'error'}.

    ok=True means a walkable path exists between the two points.
    """
    for point in (start, end):
        if (not isinstance(point, (list, tuple)) or len(point) != 3
                or not all(isinstance(x, (int, float)) and math.isfinite(x)
                           for x in point)):
            raise ValueError('nav endpoints must be finite 3d points')
    command = [str(NAV_EXE), str(MMAPS_DIR), str(map_id),
               *(str(x) for x in start), *(str(x) for x in end)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if run.returncode:
        return {'ok': False, 'waypoints': [], 'error': (run.stderr or '').strip()[:400]}
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError:
        return {'ok': False, 'waypoints': [], 'error': 'unparsable nav output'}
    if payload.get('status') != 'PASS':
        return {'ok': False, 'waypoints': [],
                'error': str(payload.get('status') or 'unknown nav status')}
    waypoints = payload.get('waypoints') or []
    if not waypoints:
        return {'ok': False, 'waypoints': [], 'error': 'nav returned no waypoints'}
    return {'ok': True, 'waypoints': waypoints, 'error': '',
            'path_polygons': payload.get('path_polygons'),
            'boundary_snaps': payload.get('boundary_snaps')}


def candidate_destinations(origin, radii=(45.0, 65.0, 85.0), angles=16):
    """Ground candidates around the character.

    The candidates are raw directions; only the ones the character can walk
    to survive the nav_query check, so no height guess is baked in here.
    """
    positions = []
    for radius in radii:
        for step in range(angles):
            bearing = 2 * math.pi * step / angles
            x = origin[0] + radius * math.cos(bearing)
            y = origin[1] + radius * math.sin(bearing)
            positions.append((bearing, radius, [x, y, origin[2]]))
    return positions


def find_separation_point(origin, hostile, nav=nav_query, map_id=1,
                          radii=(45.0, 65.0, 85.0), angles=16):
    """Find a spot the character can reach but the hostile cannot.

    Returns a verdict dict; 'destination' is None when no such spot was
    found within the searched radii.
    """
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        raise ValueError('origin must be a 3d point')
    if not isinstance(hostile, (list, tuple)) or len(hostile) != 3:
        raise ValueError('hostile must be a 3d point')
    attempts = []
    candidates = candidate_destinations(origin, radii=radii, angles=angles)
    for bearing, radius, raw in candidates:
        forward = nav(origin, raw, map_id=map_id)
        if not forward['ok']:
            attempts.append({'bearing': round(bearing, 4), 'radius': radius,
                             'character_reachable': False,
                             'hostile_reachable': None})
            continue
        destination = forward['waypoints'][-1]
        backward = nav(hostile, destination, map_id=map_id)
        attempts.append({'bearing': round(bearing, 4), 'radius': radius,
                         'character_reachable': True,
                         'destination': destination,
                         'hostile_reachable': backward['ok']})
        if not backward['ok']:
            path = forward['waypoints']
            length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
            if length > MAX_TOTAL_YARDS:
                continue
            return {'destination': destination,
                    'character_path': path,
                    'character_path_length': length,
                    'hostile_position': list(hostile),
                    'hostile_reachable': False,
                    'hostile_error': backward['error'],
                    'bearing': round(bearing, 4),
                    'radius': radius,
                    'attempts': attempts}
    return {'destination': None, 'hostile_position': list(hostile),
            'hostile_reachable': None, 'attempts': attempts}
