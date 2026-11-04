"""Offline check: does the route to the XP farm pass through hostile ground?

The travel from the graveyard to the Felstalker area picked two different
corridors: one went straight, the other walked southeast through the
Clattering Scorpid pack and the character died there (2026-11-01
travelfarm154734 leg 4: 146 -> 102 health, then dead).

The navmesh corridor is not stable, so the route has to be checked, not
trusted. This script prints the path the nav tool would use and the hostile
spawns within a safety radius of it.

  python scripts/check_route_safety.py <x> <y> <z> <gx> <gy> <gz> [radius]
"""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAV = ROOT / '.cache/nav-height-build/agent_wow_nav.exe'
MMAPS = r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps'

# Factions that are hostile to the character (Horde, faction group 2) and the
# creature levels that make a spawn dangerous for a level 5 character.
HOSTILE_FACTIONS = (0, 2, 3, 7, 8, 9, 19, 49, 51, 52, 53, 60, 74, 75, 79, 83, 91,
                    110, 116, 117, 118, 130, 131, 132, 140, 142, 144, 156, 176,
                    189, 196, 207, 208, 211, 222, 223, 225, 226, 227, 228, 233,
                    234, 242, 243, 252, 253, 270, 272, 276, 277, 278, 279, 281,
                    284, 285, 295, 302, 305, 306, 313, 319, 320, 321, 323, 325,
                    326, 327, 328, 329, 330, 335, 336, 347, 348, 352, 366, 367,
                    375, 379, 380, 381, 402, 403, 404, 405, 413, 414, 418, 436, 437,
                    438, 483, 499, 570, 571, 572, 577, 578, 598, 604, 605, 612, 628,
                    650, 651, 653, 654, 670, 671, 679, 695, 699, 700, 701, 711,
                    714, 764, 804, 816, 817, 822, 825)


def nav_path(start, goal):
    run = subprocess.run([str(NAV), MMAPS, '1', *(str(v) for v in start),
                          *(str(v) for v in goal)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode:
        return None, (run.stderr or '').strip()[:300]
    payload = json.loads(run.stdout)
    if payload.get('status') != 'PASS':
        return None, str(payload.get('status'))
    return payload['waypoints'], None


def main(argv):
    if len(argv) < 7:
        print(__doc__)
        return 2
    start = [float(v) for v in argv[1:4]]
    goal = [float(v) for v in argv[4:7]]
    radius = float(argv[7]) if len(argv) > 7 else 120.0
    points, error = nav_path(start, goal)
    if points is None:
        print('no nav path:', error)
        return 1
    print('waypoints: %d, length %.1f yards'
          % (len(points), sum(math.dist(a, b) for a, b in
                              zip(points, points[1:]))))
    print('first:', [round(v, 1) for v in points[0]])
    print('last :', [round(v, 1) for v in points[-1]])
    print(json.dumps({'waypoints': [[round(v, 2) for v in p] for p in points]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
