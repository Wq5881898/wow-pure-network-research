"""Offline route safety: does the path to a target pass through hostile ground?

The travel from the graveyard to the XP farm died twice on the way (2026-11-01
travelfarm154734 and travelfarm011745): the navmesh corridor it chose walked
through a pack of 3099 Dire Mottled Boar (level 6) and the character died
there. The corridor is not stable between runs, so the route has to be
checked instead of trusted.

This runs entirely offline: the nav tool computes the path, the local world
database supplies the hostile spawns, and the closest approach of the path to
each of them is reported. No login, no movement, no combat.

  python scripts/check_route_safety.py <x> <y> <z> <gx> <gy> <gz> [radius]

The character is a level 5 Horde orc, so a spawn is hostile when its faction
is hostile to faction group 2 and its level is close to the character's.
"""
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAV = ROOT / '.cache/nav-height-build/agent_wow_nav.exe'
CONFIG = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\configs\worldserver.conf')
MYSQL = r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe'

# Factions hostile to the character's faction group (2), from FactionTemplate.dbc.
HOSTILE_FACTIONS = (0, 2, 3, 8, 9, 19, 49, 51, 52, 53, 60, 74, 75, 79, 83, 91,
                    110, 116, 117, 118, 130, 131, 132, 140, 142, 144, 156, 176,
                    189, 196, 207, 208, 211, 222, 223, 225, 226, 227, 228, 233,
                    234, 242, 243, 252, 253, 270, 272, 276, 277, 278, 279, 281,
                    284, 285, 295, 302, 305, 306, 313, 319, 320, 321, 323, 325,
                    326, 327, 328, 329, 330, 335, 336, 347, 348, 352, 366, 367,
                    375, 379, 380, 381, 402, 403, 404, 405, 413, 414, 418, 436, 437,
                    438, 483, 499, 570, 571, 572, 577, 578, 598, 604, 605, 612, 628,
                    650, 651, 653, 654, 670, 671, 679, 695, 699, 700, 701, 711,
                    714, 764, 804, 816, 817, 822, 825)
MIN_LEVEL = 3
MAX_LEVEL = 12


def database_settings():
    for line in CONFIG.read_text(encoding='utf-8', errors='ignore').splitlines():
        if line.strip().startswith('WorldDatabaseInfo'):
            parts = line.split('"')[1].split(';')
            return parts[0], parts[1], parts[2], parts[3], parts[4]
    raise RuntimeError('no WorldDatabaseInfo in the world server config')


def nav_path(start, goal):
    mmaps = r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps'
    run = subprocess.run([str(NAV), mmaps, '1', *(str(v) for v in start),
                          *(str(v) for v in goal)],
                         capture_output=True, text=True, timeout=90)
    if run.returncode:
        return None, (run.stderr or '').strip()[:300]
    payload = json.loads(run.stdout)
    if payload.get('status') != 'PASS':
        return None, str(payload.get('status'))
    return payload['waypoints'], None


def hostile_spawns(min_level=MIN_LEVEL, max_level=MAX_LEVEL):
    host, port, user, password, database = database_settings()
    query = ("select c.id1, ct.name, ct.minlevel, round(c.position_x,2), "
             "round(c.position_y,2), round(c.position_z,2) from `{database}`.creature c "
             "join `{database}`.creature_template ct on ct.entry=c.id1 "
             "where c.map=1 and ct.minlevel between {lo} and {hi} "
             "and ct.faction in ({factions})").format(
        database=database, lo=min_level, hi=max_level,
        factions=','.join(str(f) for f in HOSTILE_FACTIONS))
    environment = {'MYSQL_PWD': password, 'PATH': r'C:\Windows\System32'}
    run = subprocess.run([MYSQL, '-h', host, '-P', port, '-u', user, '-N', '-B',
                          '-e', query], capture_output=True, text=True,
                         env=environment, timeout=120)
    rows = []
    for line in run.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 6:
            continue
        try:
            rows.append({'entry': int(parts[0]), 'name': parts[1],
                         'level': int(parts[2]), 'x': float(parts[3]),
                         'y': float(parts[4]), 'z': float(parts[5])})
        except ValueError:
            continue
    return rows


def closest_approach(points, spawn):
    best = None
    for point in points:
        distance = math.dist((point[0], point[1]), (spawn['x'], spawn['y']))
        if best is None or distance < best[0]:
            best = (distance, point)
    return best


def main(argv):
    if len(argv) < 7:
        print(__doc__)
        return 2
    start = [float(v) for v in argv[1:4]]
    goal = [float(v) for v in argv[4:7]]
    radius = float(argv[7]) if len(argv) > 7 else 60.0
    points, error = nav_path(start, goal)
    if points is None:
        print('no nav path:', error)
        return 1
    length = sum(math.dist(a[:2], b[:2]) for a, b in zip(points, points[1:]))
    print('path: %d waypoints, %.1f yards' % (len(points), length))
    spawns = hostile_spawns()
    print('hostile spawns in the world: %d' % len(spawns))
    scored = []
    for spawn in spawns:
        approach = closest_approach(points, spawn)
        if approach and approach[0] <= radius:
            scored.append((approach[0], spawn))
    scored.sort(key=lambda row: row[0])
    if not scored:
        print('no hostile spawn within %d yards of the path' % radius)
        return 0
    print('hostile spawns within %d yards of the path:' % radius)
    for distance, spawn in scored[:12]:
        print('  %5.1f y  entry %-6d %-24s level %d at (%.1f, %.1f, %.1f)'
              % (distance, spawn['entry'], spawn['name'], spawn['level'],
                 spawn['x'], spawn['y'], spawn['z']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
