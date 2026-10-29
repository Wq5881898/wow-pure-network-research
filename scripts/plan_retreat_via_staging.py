"""Try explicit historical staging waypoints under unchanged corridor checks."""
import json
import subprocess
import math
from pathlib import Path
root=Path(__file__).resolve().parents[1];folder=root/'results/pure_network'
start=json.loads((folder/'quest792_retreat_plan01_rejected.json').read_bytes())['start']
end=json.loads((folder/'quest792_retreat_plan01_rejected.json').read_bytes())['end']
middle=json.loads((folder/'agent_wow_readonly_height_Q792_segment16_verify.json').read_bytes())['observation']['position']
rows=[]
for a,b in ((start,middle),(middle,end)):
    run=subprocess.run([str(root/'.cache/nav-height-build/agent_wow_nav.exe'),r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps','1',*map(str,a),*map(str,b)],capture_output=True,text=True,timeout=30)
    row={'start':a,'end':b,'returncode':run.returncode,'stderr':run.stderr}
    if not run.returncode:
        row['nav']=json.loads(run.stdout)
        pts=row['nav']['surface_samples'];row['length']=sum(math.dist(x,y) for x,y in zip(pts,pts[1:]))
    rows.append(row)
with (folder/'retreat_via_staging01.json').open('x',encoding='utf-8') as f:json.dump(rows,f,indent=2)
print(json.dumps([{k:v for k,v in row.items() if k!='nav'} for row in rows],indent=2))
