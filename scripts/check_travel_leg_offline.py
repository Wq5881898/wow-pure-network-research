"""Offline check: can a travel leg be planned from the character's position?

The travel walks towards a goal one leg at a time. A leg failed on
2026-11-01 (travel3101a/travel3101b) with "no walkable leg towards the goal"
even though the navmesh had a path, because the character was standing on a
ledge eight yards above the corridor and every route down was refused as a
cliff. This script reproduces the leg search offline, so the answer costs no
character life:

  python scripts/check_travel_leg_offline.py <x> <y> <z> <goal_x> <goal_y> <goal_z>

It never touches the bridge: no login, no movement, no combat.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_wow_session import PureNetworkBackend  # noqa: E402
from agent_wow_travel import _bridge_limits_ok, _length  # noqa: E402
from plan_no_path_retreat import nav_query  # noqa: E402

SLOPE_TOLERANCE = 0.7


def search(current, goal, radius=45.0, bearings=16):
    current = [float(v) for v in current[:3]]
    goal = [float(v) for v in goal[:3]]
    bearing = math.atan2(goal[1] - current[1], goal[0] - current[0])
    candidates = [(bearing, math.dist(current, goal), goal)]
    away = bearing + math.pi
    candidates.append((away, radius, [
        current[0] + math.cos(away) * radius,
        current[1] + math.sin(away) * radius,
        current[2]]))
    for index in range(bearings):
        angle = bearing + (0.0 if index == 0 else math.copysign(
            ((index + 1) // 2) * math.pi / bearings, index))
        for distance in (radius, radius * 0.6, radius * 0.35):
            candidates.append((angle, distance, [
                current[0] + math.cos(angle) * distance,
                current[1] + math.sin(angle) * distance,
                current[2]]))
    rows = []
    fallback = None
    for angle, distance, end in candidates:
        query = nav_query(current, end)
        if not query['ok']:
            rows.append({'angle': round(math.degrees(angle) % 360, 1),
                         'distance': round(distance, 2), 'ok': False,
                         'error': query['error'][:120]})
            continue
        try:
            points = PureNetworkBackend._densify(
                {'waypoints': query['waypoints']}, current,
                slope_tolerance=SLOPE_TOLERANCE)
        except RuntimeError as exc:
            rows.append({'angle': round(math.degrees(angle) % 360, 1),
                         'distance': round(distance, 2), 'ok': False,
                         'error': str(exc)[:120]})
            continue
        if not points:
            continue
        legal, reason = _bridge_limits_ok(points, current)
        if not legal:
            rows.append({'angle': round(math.degrees(angle) % 360, 1),
                         'distance': round(distance, 2), 'ok': False,
                         'error': reason})
            continue
        length = _length(points)
        if length < 4.0:
            continue
        reached = [points[-1]['x'], points[-1]['y'], points[-1]['z']]
        progress = math.dist(current, goal) - math.dist(reached, goal)
        if progress <= 0:
            if fallback is None or length > fallback['length']:
                fallback = {'angle': round(math.degrees(angle) % 360, 1),
                            'distance': round(distance, 2), 'ok': True,
                            'length': round(length, 2), 'progress': round(progress, 2),
                            'goal_distance': round(math.dist(reached, goal), 2),
                            'end': [round(v, 2) for v in reached], 'detour': True}
            continue
        rows.append({'angle': round(math.degrees(angle) % 360, 1),
                     'distance': round(distance, 2), 'ok': True,
                     'length': round(length, 2), 'progress': round(progress, 2),
                     'goal_distance': round(math.dist(reached, goal), 2),
                     'end': [round(v, 2) for v in reached]})
    if fallback is not None:
        rows.append(fallback)
    return rows


def main(argv):
    if len(argv) < 7:
        print(__doc__)
        return 2
    rows = search(argv[1:4], argv[4:7])
    usable = [row for row in rows if row['ok']]
    print(f'tried={len(rows)} usable={len(usable)}')
    for row in sorted(usable, key=lambda r: -r['progress']):
        print('  ok  angle={angle:>6} length={length:>7} progress={progress:>7} '
              'goal_distance={goal_distance:>7} end={end}'.format(**row))
    for row in rows:
        if not row['ok']:
            print('  no  angle={angle:>6} {error}'.format(**row))
    return 0 if usable else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
