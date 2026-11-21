"""Launch the shared 18→19 rectangle worker against reviewed regional data."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '.cache')
CLI = '/home/wowlab/wow-research/agent-wow-cli-local18'


def prepare(spec, output):
    from rectangle18_east_plan import path
    out = path(spec['login_anchor'], spec['goal'])
    back = path(out[-1], spec['refuge'])
    if spec.get('return_to_login'):
        back += path(back[-1], spec['login_anchor'])[1:]
    if sum(math.dist(a, b) for a,b in zip(back, back[1:])) < 50:
        raise ValueError('local exit shorter than 50 yards')
    start = out[-1]
    legs = []
    for dest in spec['patrol']:
        leg = path(start, dest)
        legs.append(leg)
        start = leg[-1]
    for start, dest in spec.get('patrol_branches', []):
        nodes = [leg[0] for leg in legs] + [leg[-1] for leg in legs]
        node = min(nodes, key=lambda p: math.dist(p, start))
        if math.dist(node, start) > .15:
            raise ValueError('unconnected patrol branch')
        legs.append(path(node, dest))
    x0,x1,y0,y1 = spec['bounds']
    if not all(x0<=p[0]<=x1 and y0<=p[1]<=y1 for leg in [out, back, *legs] for p in leg):
        raise ValueError('surface path outside rectangle')
    spec.update(base=dict(route_id=output.stem, login_anchor=spec['login_anchor'], goal=out[-1], out=out, back=back), legs=legs)
    with output.open('x') as f:
        json.dump(spec,f)


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('action', choices=['prepare','start','_worker'])
    a.add_argument('plan', type=Path)
    a.add_argument('value')
    args=a.parse_args()
    spec=json.loads(args.plan.read_text())
    if args.action=='prepare':
        prepare(spec,Path(args.value)); return
    if (spec['start_level'],spec['stop_level']) != (18,19):
        a.error('this worker is verified only for 18→19')
    if args.action=='_worker':
        import rectangle18_worker as worker
        worker.configure(args.value,args.plan)
        worker.main()
        return
    db=json.loads(subprocess.check_output(['/mnt/c/APP/Python/python.exe','-B',r'C:\APP\opencode\project\wow pure network\.cache\chain_database.py'],text=True,timeout=15))
    ps=subprocess.check_output(['ps','-eo','args'],text=True)
    if (db['online'] or db['flags'] or db['level']!=18 or db['health']!=418 or db['durability_ratio']<.3
        or math.dist(db['position'], spec['base']['login_anchor'])>.1 or 'agent-wow-cli' in ps or 'netprobe_idle.py _hold' in ps):
        a.error('role not safely offline at reviewed login anchor, or an owner is present')
    env=dict(os.environ,PYTHONPATH='scripts:.cache', AGENT_WOW_NAV_CORRIDOR_AB='1', AGENT_WOW_CLI=CLI,
             AGENT_WOW_LOCAL_REALM_ID='1', AGENT_WOW_LOCAL_REALM_ADDRESS='127.0.0.1:8085',
             AGENT_WOW_MMAPS='/mnt/c/Azerothcore/azerothcore-wotlk/build/bin/Debug/data/mmaps')
    p=subprocess.Popen([sys.executable,'-B',__file__,'_worker',str(args.plan),args.value], env=env,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(json.dumps(dict(run=args.value,pid=p.pid,db=db)))


if __name__=='__main__':
    main()
