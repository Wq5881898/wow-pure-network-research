"""Offline navigation regression preserving previously rejected case inputs."""
import json
import math
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
folder=root/'results/pure_network'
failed=json.loads((folder/'quest792_retreat_plan01_rejected.json').read_bytes())
alternatives=json.loads((folder/'quest792_retreat_alternatives01.json').read_bytes())
h001=json.loads((folder/'height_route_H001_20261029.json').read_bytes())
cases=[('retreat15',failed['start'],failed['end'])]+[(f"retreat{r['destination_segment']}",failed['start'],r['end']) for r in alternatives['candidates']]
cases += [('H001',h001['start'],h001['end']),('invalid_height',[*failed['start'][:2],10000],failed['end'])]
rows=[]
for name,start,end in cases:
    run=subprocess.run([str(root/'.cache/nav-height-build/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,start),*map(str,end)],capture_output=True,text=True,timeout=30)
    row={'case':name,'returncode':run.returncode,'stderr':run.stderr,'start':start,'end':end}
    if not run.returncode:
        row['nav']=json.loads(run.stdout)
        pts=row['nav']['surface_samples']
        row['length']=sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))
    rows.append(row)
with (folder/'retreat_boundary_regression01.json').open('x',encoding='utf-8') as handle:
    json.dump(rows,handle,indent=2)
print(json.dumps([{k:v for k,v in r.items() if k!='nav'}|{'boundary_snaps':r.get('nav',{}).get('boundary_snaps')} for r in rows],indent=2))
raise SystemExit(any((r['returncode']==0)!=(r['case']!='invalid_height') for r in rows))
