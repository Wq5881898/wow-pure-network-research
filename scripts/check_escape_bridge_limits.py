"""Offline check: will the bridge accept the escape route?

The bridge validates every path it is given (probes/AgentWowBridge/actions.go,
validatePath):

    if len(points)==0 || len(points)>128 -> "path requires 1..128 points"
    prev := start                         (the bridge's own tracked position)
    for each point:
        d := hypot(dx, dy)                (2D!)
        if d > 15 || |dz| > 3 -> "segment exceeds 15 yards or 3-yard height change"
        total += d
    if total < .05 || total > 50 -> "path length must be .05..50 yards"

A path that breaks any of those rules is refused outright, and the character
does not move at all. That is what happened on 2026-11-01 (b29_u1, b36_u1):
the escape route of 195..208 yards was planned and then refused, and the
character stood still while the hostile kept hitting it.

This script reproduces the whole thing offline -- no login, no movement, no
combat -- and says which chunk breaks which rule.

  python scripts/check_escape_bridge_limits.py <char_x> <char_y> <char_z> \
      <home_x> <home_y> <home_z> [reach]
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_wow_combat_control import estimated_position  # noqa: E402,F401
from agent_wow_escape_control import _chunks  # noqa: E402
from agent_wow_session import PureNetworkBackend  # noqa: E402
from plan_no_path_retreat import nav_query  # noqa: E402

MAX_POINTS = 128
MAX_SEGMENT_YARDS = 15.0
MAX_TOTAL_YARDS = 50.0
MAX_HEIGHT_YARDS = 3.0


def _xyz(point):
    if isinstance(point, dict):
        return [point['x'], point['y'], point['z']]
    return list(point[:3])


def validate_path(start, points):
    """The bridge's own rule, copied from actions.go."""
    if len(points) == 0 or len(points) > MAX_POINTS:
        return 'path requires 1..128 points', None
    total = 0.0
    prev = list(start[:3])
    for point in points:
        point = _xyz(point)
        dx = point[0] - prev[0]
        dy = point[1] - prev[1]
        dz = point[2] - prev[2]
        d = math.hypot(dx, dy)
        if d > MAX_SEGMENT_YARDS or abs(dz) > MAX_HEIGHT_YARDS:
            return ('segment exceeds 15 yards or 3-yard height change',
                    {'point': [round(v, 3) for v in point],
                     'distance_2d': round(d, 3), 'height_change': round(dz, 3),
                     'from': [round(v, 3) for v in prev]})
        total += d
        prev = list(point[:3])
    if total < .05 or total > MAX_TOTAL_YARDS:
        return 'path length must be .05..50 yards', {'total_2d': round(total, 3)}
    return None, {'total_2d': round(total, 3)}


def plan_escape_route(current, home, reach=165.0, map_id=1):
    """The session's escape geometry: a destination `reach` away from the
    hostile's spawn, on the navmesh, away from the hostile."""
    base = math.atan2(current[1] - home[1], current[0] - home[0])
    for index in range(12):
        angle = base + (0.0 if index == 0 else math.copysign(
            ((index + 1) // 2) * math.radians(30), index))
        destination = [home[0] + math.cos(angle) * reach,
                       home[1] + math.sin(angle) * reach,
                       home[2]]
        query = nav_query(current, destination, map_id=map_id)
        if not query['ok']:
            continue
        points = PureNetworkBackend._densify(
            {'waypoints': query['waypoints']}, current, slope_tolerance=0.7)
        if not points:
            continue
        return [{'x': p['x'], 'y': p['y'], 'z': p['z']} for p in points], destination
    return None, None


def main(argv):
    if len(argv) < 7:
        print(__doc__)
        return 2
    current = [float(v) for v in argv[1:4]]
    home = [float(v) for v in argv[4:7]]
    reach = float(argv[7]) if len(argv) > 7 else 165.0
    route, destination = plan_escape_route(current, home, reach)
    if route is None:
        print('no escape route could be planned')
        return 1
    print('character=%s home=%s reach=%s' % (current, home, reach))
    print('destination=%s points=%d'
          % ([round(v, 2) for v in destination], len(route)))
    chunks = _chunks(route, current)
    print('chunks=%d' % len(chunks))
    failures = 0
    # The bridge tracks its own position: after each accepted path it sets
    # b.position to the last point it walked to, so the next chunk is
    # validated against the end of the previous one, not against where the
    # character started.
    anchor = list(current)
    for index, chunk in enumerate(chunks):
        problem, detail = validate_path(anchor, chunk)
        if problem:
            failures += 1
            print('  chunk %d REJECTED: %s %s' % (index, problem, detail))
        else:
            print('  chunk %d ok (%d points, %s)'
                  % (index, len(chunk), detail))
        anchor = _xyz(chunk[-1])
    if not failures:
        print('the bridge should accept every chunk')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
