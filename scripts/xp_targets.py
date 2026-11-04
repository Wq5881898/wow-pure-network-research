"""Which nearby mobs are worth killing for experience?

AzerothCore's rule (src/server/game/Server/Formulas.cpp,
Acore::XP::BaseGain): a mob more than a few levels BELOW the character still
gives experience, and only a true grey mob gives none. The real cutoff is

    gray_level = GetGrayLevel(pl_level)      (classic: five levels below)
    baseGain   = (pl_level * 5 + 45) * (ZD + mob_level - pl_level) / ZD

with the per-player experience rate multiplied on top. Critters give nothing
regardless of level, which is why killing 3098/3124/3101 never moved the
experience.

This script lists the hostile spawns near a point, with the experience each
would actually give, so the target is chosen by the rule and not by guesswork.

  python scripts/xp_targets.py <x> <y> <z> [player_level] [radius]
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\configs\worldserver.conf')
MYSQL = r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe'

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


def gray_level(player_level):
    """GetGrayLevel: the level at which a mob stops giving experience."""
    return max(1, player_level - 5)


def zero_difference(player_level):
    """GetZeroDifference: the divisor of the below-level experience ramp."""
    return 5


def base_gain(player_level, mob_level):
    if mob_level >= player_level:
        difference = min(mob_level - player_level, 4)
        return ((player_level * 5 + 45) * (20 + difference) // 10 + 1) // 2
    if mob_level > gray_level(player_level):
        zd = zero_difference(player_level)
        return (player_level * 5 + 45) * (zd + mob_level - player_level) // zd
    return 0


def database_settings():
    for line in CONFIG.read_text(encoding='utf-8', errors='ignore').splitlines():
        if line.strip().startswith('WorldDatabaseInfo'):
            parts = line.split('"')[1].split(';')
            return parts[0], parts[1], parts[2], parts[3], parts[4]
    raise RuntimeError('no WorldDatabaseInfo in the world server config')


def spawns_near(x, y, radius):
    host, port, user, password, database = database_settings()
    query = ("select c.id1, ct.name, ct.minlevel, ct.maxlevel, ct.type, "
             "round(c.position_x,2), round(c.position_y,2), round(c.position_z,2) "
             "from `{db}`.creature c join `{db}`.creature_template ct on ct.entry=c.id1 "
             "where c.map=1 and ct.faction in ({factions}) and ct.minlevel between 1 and 12 "
             "and sqrt(power(c.position_x-({x}),2)+power(c.position_y-({y}),2))<{radius}").format(
        db=database, factions=','.join(str(f) for f in HOSTILE_FACTIONS),
        x=x, y=y, radius=radius)
    env = dict(os.environ, MYSQL_PWD=password)
    run = subprocess.run([MYSQL, '-h', host, '-P', port, '-u', user, '-N', '-B',
                          '-e', query], capture_output=True, text=True, env=env,
                         timeout=120)
    rows = []
    for line in run.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 8:
            continue
        try:
            rows.append({'entry': int(parts[0]), 'name': parts[1],
                         'level': int(parts[2]), 'max_level': int(parts[3]),
                         'type': int(parts[4]), 'x': float(parts[5]),
                         'y': float(parts[6]), 'z': float(parts[7])})
        except ValueError:
            continue
    return rows


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    x, y, z = (float(v) for v in argv[1:4])
    player_level = int(argv[4]) if len(argv) > 4 else 5
    radius = float(argv[5]) if len(argv) > 5 else 300.0
    rows = spawns_near(x, y, radius)
    print('player level %d, grey at level %d, %d hostile spawns within %.0f yards'
          % (player_level, gray_level(player_level), len(rows), radius))
    scored = []
    for row in rows:
        gain = base_gain(player_level, row['level'])
        distance = math.dist((x, y), (row['x'], row['y']))
        scored.append((gain, distance, row))
    scored.sort(key=lambda item: (-item[0], item[1]))
    for gain, distance, row in scored:
        print('  xp=%-6d %6.1f y  entry %-6d %-26s level %-2d type %-2d at (%.1f, %.1f, %.1f)'
              % (gain, distance, row['entry'], row['name'], row['level'],
                 row['type'], row['x'], row['y'], row['z']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
