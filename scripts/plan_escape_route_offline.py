"""Offline check: can an escape route long enough to break sight be planned?

The escape only ends a combat when the character ends up further than the
visibility range away from the hostile's CURRENT position
(Creature::CanCreatureAttack -> ThreatReference::ShouldBeOffline; see
docs/kb/mechanisms/机制-怪何时真正放弃-NO_PATH.md). The session planner
places the destination a fixed reach away from the hostile's SPAWN, so this
script reproduces that geometry offline and reports, per direction:

  - whether the navmesh accepts a path at all,
  - the real path length (the bridge caps a request at 50 yards and a
    segment at 15 yards, so the length also gives the number of requests),
  - the running time at the bridge's run speed (7 yards/s),
  - the distance of the landing point from the hostile's spawn.

It never touches the bridge: no login, no movement, no combat.

Usage:
  python scripts/plan_escape_route_offline.py <char_x> <char_y> <char_z> \
      <home_x> <home_y> <home_z> [reach]
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plan_no_path_retreat import nav_query  # noqa: E402

BRIDGE_RUN_YARDS_PER_SECOND = 7.0
BRIDGE_MAX_REQUEST_YARDS = 50.0
BRIDGE_MAX_SEGMENT_YARDS = 15.0


def bearing_fan(base, candidates=12):
    """The same fan the session planner uses around the away direction."""
    for index in range(candidates):
        offset = 0.0 if index == 0 else math.copysign(
            ((index + 1) // 2) * math.radians(30), index)
        yield base + offset


def check(char, home, reach, candidates=12):
    base = math.atan2(char[1] - home[1], char[0] - home[0])
    rows = []
    for bearing in bearing_fan(base, candidates):
        destination = [home[0] + math.cos(bearing) * reach,
                       home[1] + math.sin(bearing) * reach,
                       home[2]]
        result = nav_query(char, destination)
        if not result['ok']:
            rows.append({'bearing': round(math.degrees(bearing) % 360, 1),
                         'ok': False, 'error': result['error'][:120]})
            continue
        points = result['waypoints']
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        segments = max(1, math.ceil(length / BRIDGE_MAX_SEGMENT_YARDS))
        rows.append({'bearing': round(math.degrees(bearing) % 360, 1),
                     'ok': True,
                     'length': round(length, 3),
                     'run_seconds': round(length / BRIDGE_RUN_YARDS_PER_SECOND, 2),
                     'requests': max(1, math.ceil(length / BRIDGE_MAX_REQUEST_YARDS)),
                     'segments': segments,
                     'end_distance_from_home': round(
                         math.dist(points[-1], home), 3),
                     'end': [round(v, 3) for v in points[-1]]})
    return rows


def main(argv):
    if len(argv) < 7:
        print(__doc__)
        return 2
    char = [float(v) for v in argv[1:4]]
    home = [float(v) for v in argv[4:7]]
    reach = float(argv[7]) if len(argv) > 7 else 115.0
    rows = check(char, home, reach)
    ok = [r for r in rows if r['ok']]
    print(f'character={char} home={home} reach={reach}')
    print(f'directions tried={len(rows)} walkable={len(ok)}')
    for row in sorted(ok, key=lambda r: r['length']):
        print(f"  bearing {row['bearing']:>6} len={row['length']:>8} "
              f"run={row['run_seconds']:>6}s requests={row['requests']} "
              f"segments={row['segments']} "
              f"end_from_home={row['end_distance_from_home']:>7} end={row['end']}")
    for row in rows:
        if not row['ok']:
            print(f"  bearing {row['bearing']:>6} REJECTED: {row['error']}")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
