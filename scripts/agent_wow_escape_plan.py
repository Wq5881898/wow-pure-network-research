"""Build a bounded cached retreat route from the latest submitted position."""
import json
import math
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=json.loads((root/'results/pure_network/agent_wow_bridge_run48_recovery_reclaim.json').read_text())
start=source['path_response']['result']['submittedPosition'][:3]
end=[-664.88885,-4287.92725,40.61064]
run=subprocess.run([str(root/'.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,start),*map(str,end)],capture_output=True,text=True,timeout=10)
if run.returncode:raise SystemExit(run.stderr)
nav=json.loads(run.stdout);points=[];previous=start
for p in nav['waypoints']:
    distance=math.dist(previous,p)
    if distance<.01:continue
    n=max(1,math.ceil(distance/3),math.ceil(abs(p[2]-previous[2])/2));origin=previous
    for i in range(1,n+1):points.append(dict(zip(('x','y','z'),[origin[j]+(p[j]-origin[j])*i/n for j in range(3)])))
    previous=p
result={'start_submitted':start,'points':points,'detour':nav,'purpose':'noncombat run-mode retreat validation, not verified combat escape'}
(root/'results/pure_network/agent_wow_escape_route.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'waypoints':len(points),'start':start,'end':end},indent=2))
