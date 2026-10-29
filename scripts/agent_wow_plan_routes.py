"""Generate local Detour routes from the most recent confirmed login position."""
import argparse
import json
import math
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/".cache/agent-wow-nav-build/Release/agent_wow_nav.exe"
NAV=r"C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps"

def route(start,end):
    run=subprocess.run([str(EXE),NAV,"1",*map(str,start),*map(str,end)],capture_output=True,text=True,timeout=10)
    return json.loads(run.stdout) if run.returncode==0 else None

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("result");parser.add_argument("kind",choices=["straight","bend","obstacle","questgiver","hunt","corpse","trainer"]);args=parser.parse_args()
    data=json.loads(Path(args.result).read_text())
    start=data["login_position"]["position_xyzo"][:3]
    if 'path_response' in data and 'result' in data['path_response']:
        start=data['path_response']['result']['submittedPosition'][:3]
    if args.kind=="straight":
        targets=[[start[0]+2,start[1],start[2]]]
    elif args.kind=="bend":
        targets=[[start[0],start[1]+2,start[2]],[start[0]-2,start[1]+2,start[2]]]
    elif args.kind=='trainer':
        targets=[[-639.343994140625,-4230.18994140625,38.56050109863281]]
    elif args.kind=='corpse':
        targets=[next(e['corpse']['position_xyz'] for e in data['recovery_events'] if e.get('kind')=='corpse' and e['corpse']['found'])]
    elif args.kind in ('questgiver','hunt'):
        targets=[[-600.1320190429688,-4186.18994140625,41.266300201416016] if args.kind=='questgiver' else [-680.926025390625,-4284.81982421875,40.711769104003906]]
    else:
        candidates=[]
        for radius in (8,12,16,20,28,36):
            for i in range(16):
                end=[start[0]+radius*math.cos(i*math.pi/8),start[1]+radius*math.sin(i*math.pi/8),start[2]]
                r=route(start,end)
                if not r:continue
                pts=r["waypoints"]
                length=sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))
                direct=math.dist(pts[0],pts[-1])
                if len(pts)>=3 and length>direct+.5 and length<48 and max(abs(p[2]-start[2]) for p in pts)<5:
                    candidates.append((length-direct,end,r))
            if candidates:break
        if not candidates:raise SystemExit("No local navmesh detour candidate found")
        _,end,r=max(candidates,key=lambda x:x[0])
        targets=[end]
    points=[];legs=[];current=start
    for end in targets:
        r=route(current,end)
        if not r:raise SystemExit("No complete navmesh route")
        pts=r["waypoints"]
        projection_limit=3 if args.kind=='corpse' else 1
        if math.dist(current,pts[0])>projection_limit:raise SystemExit("Navmesh start projection too far")
        legs.append(r)
        # Include projected start if height differs, then subdivide long legs.
        for p in pts:
            distance=math.dist(current,p)
            n=max(1,math.ceil(distance/4),math.ceil(abs(p[2]-current[2])/2))
            old=current
            for i in range(1,n+1):
                q=[old[j]+(p[j]-old[j])*i/n for j in range(3)]
                if math.dist(current,q)>.005:
                    points.append(dict(zip(("x","y","z"),q)));current=q
    if args.kind in ('questgiver','hunt','corpse','trainer'):
        kept=[];previous=start;length=0
        for p in points:
            d=math.dist(previous,list(p.values()))
            if length+d>32:break
            kept.append(p);length+=d;previous=list(p.values())
        points=kept
    result={"start_server":start,"kind":args.kind,"points":points,"detour_legs":legs,"path_length":sum(math.dist(a,b) for a,b in zip([start]+[list(p.values()) for p in points],[list(p.values()) for p in points]))}
    output=ROOT/f"results/pure_network/agent_wow_route_{args.kind}.json"
    output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
