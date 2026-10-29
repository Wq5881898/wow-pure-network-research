"""Plan corpse recovery from validated navmesh paths, not direct-line retreat."""
import argparse
import json
import math
import subprocess
from pathlib import Path


def length(points):
    return sum(math.dist(a,b) for a,b in zip(points,points[1:]))


def subdivide(points, segment=3, vertical=1):
    out=[list(points[0])]
    for end in points[1:]:
        start=out[-1]
        n=max(1,math.ceil(math.dist(start,end)/segment),math.ceil(abs(end[2]-start[2])/vertical))
        for i in range(1,n+1):out.append([a+(b-a)*i/n for a,b in zip(start,end)])
    return out


def select_recovery(path,corpse,radius=32,minimum_gain=5):
    if len(path)<2 or any(len(p)!=3 or not all(map(math.isfinite,p)) for p in path):
        raise ValueError('finite complete XYZ path required')
    if math.dist(path[0],corpse)>1.5:raise ValueError('corpse projection too distant')
    dense=subdivide(path)
    chosen=0;walked=0
    # Stay in the connected prefix inside the reclaim sphere; do not skip an
    # impassable section by choosing a later point that happens to be nearby.
    for i,p in enumerate(dense[1:],1):
        if math.dist(p,corpse)>radius:break
        walked+=math.dist(dense[i-1],p);chosen=i
    if walked<minimum_gain:raise ValueError('no useful graveyard-directed reclaim point')
    retreat=dense[chosen:]
    if len(retreat)<2:raise ValueError('graveyard already inside reclaim range; no retreat experiment')
    chunks=[];chunk=[];distance=0;previous=retreat[0]
    for p in retreat[1:]:
        d=math.dist(previous,p)
        if chunk and (distance+d>25 or len(chunk)>=100):chunks.append(chunk);chunk=[];distance=0
        chunk.append(dict(zip(('x','y','z'),p)));distance+=d;previous=p
    if chunk:chunks.append(chunk)
    return {'corpse_xyz':corpse,'reclaim_xyz':dense[chosen],'reclaim_distance_3d':math.dist(dense[chosen],corpse),
            'corpse_to_graveyard_path_length':length(dense),'reclaim_to_graveyard_path_length':length(retreat),
            'path_progress_before_revive':walked,'retreat_chunks':chunks,
            'selection_basis':'connected navmesh prefix toward graveyard; threats/LOS not yet ranked'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    corpse=[-528.5177612304688,-4114.5126953125,63.498409271240234]
    graveyard=[-634.635009765625,-4296.02978515625,40.52539825439453]
    cmd=[str(root/'.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,corpse),*map(str,graveyard)]
    run=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
    if run.returncode:raise SystemExit(run.stderr)
    nav=json.loads(run.stdout)
    plan=select_recovery(nav['waypoints'],corpse)
    plan.update(scope='historical danger-zone geometry; no live reclaim sent',graveyard_xyz=graveyard,navmesh=nav)
    args.output.write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps({k:v for k,v in plan.items() if k not in ('navmesh','retreat_chunks')},indent=2))
