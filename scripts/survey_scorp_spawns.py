"""Read-only spawn survey; credentials stay in the mysql environment."""
import json
import os
import subprocess
from pathlib import Path
from check_route_safety import database_settings, MYSQL


def main():
    host, port, user, password, database = database_settings()
    query = (f'SELECT c.guid,c.position_x,c.position_y,c.position_z,c.wander_distance,'
             f'c.spawntimesecs,ct.minlevel,ct.maxlevel FROM `{database}`.creature c '
             f'JOIN `{database}`.creature_template ct ON ct.entry=c.id1 '
             'WHERE c.map=1 AND c.id1=3125 AND c.position_x BETWEEN -1100 AND -400 '
             'AND c.position_y BETWEEN -5400 AND -4600 ORDER BY c.position_x,c.position_y')
    run = subprocess.run([MYSQL, '-h', host, '-P', port, '-u', user, '-N', '-B', '-e', query],
                         env=dict(os.environ, MYSQL_PWD=password), capture_output=True,
                         text=True, timeout=60)
    if run.returncode:
        raise RuntimeError('read-only spawn query failed')
    keys = ['spawn_guid','x','y','z','wander_distance','respawn_seconds','min_level','max_level']
    rows = [dict(zip(keys, map(float, line.split('\t')))) for line in run.stdout.splitlines()]
    path = Path('results/pure_network/scorp_spawn_survey_20261110_dg.json')
    with path.open('x', encoding='utf-8') as handle:
        json.dump({'scope':'template/spawn data; actual level must be observed','spawns':rows},handle,indent=2)
    print(json.dumps(rows))


if __name__ == '__main__':
    main()
