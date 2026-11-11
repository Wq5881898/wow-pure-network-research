"""Read-only local support/spawn queries, excluding authentication output."""
import json
import os
import subprocess
from pathlib import Path
from check_route_safety import database_settings, MYSQL


def main():
    host, port, user, password, database = database_settings()
    queries = {
        'local_repairer_details': f'''SELECT c.guid,c.id1,ct.name,c.position_x,c.position_y,c.position_z,
            c.phaseMask,ct.faction,ct.npcflag,c.spawnMask FROM `{database}`.creature c
            JOIN `{database}`.creature_template ct ON ct.entry=c.id1
            WHERE c.map=1 AND c.id1 IN (10369,3160)''',
        'nearby_repairers': f'''SELECT c.id1,ct.name,c.position_x,c.position_y,c.position_z,
            ct.npcflag FROM `{database}`.creature c JOIN `{database}`.creature_template ct
            ON ct.entry=c.id1 WHERE c.map=1 AND (ct.npcflag & 4096)<>0
            ORDER BY POW(c.position_x+778,2)+POW(c.position_y+4985,2) LIMIT 12''',
        'nearby_hunt_templates': f'''SELECT ct.entry,ct.name,ct.minlevel,ct.maxlevel,
            ct.faction,COUNT(*),MIN(c.spawntimesecs),MAX(c.spawntimesecs)
            FROM `{database}`.creature c JOIN `{database}`.creature_template ct ON ct.entry=c.id1
            WHERE c.map=1 AND c.position_x BETWEEN -1100 AND -400
            AND c.position_y BETWEEN -5300 AND -4600 AND ct.minlevel BETWEEN 5 AND 8
            GROUP BY ct.entry,ct.name,ct.minlevel,ct.maxlevel,ct.faction'''}
    report = {'scope':'read-only database templates/spawns; not live reachability or hostility validation'}
    for name, query in queries.items():
        run = subprocess.run([MYSQL,'-h',host,'-P',port,'-u',user,'-N','-B','-e',query],
                             env=dict(os.environ,MYSQL_PWD=password),capture_output=True,text=True,timeout=60)
        if run.returncode:
            raise RuntimeError('read-only support query failed')
        report[name] = [line.split('\t') for line in run.stdout.splitlines()]
    path = Path('results/pure_network/hunt_support_details_20261110_dr.json')
    with path.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,indent=2,ensure_ascii=False)
    print(json.dumps(report,ensure_ascii=True))


if __name__ == '__main__':
    main()
