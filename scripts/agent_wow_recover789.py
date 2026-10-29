"""Recover the existing character to its queried corpse in bounded travel runs."""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LINUX='/mnt/c/APP/opencode/project/WoW自动化预研/pure-network'
previous=ROOT/'results/pure_network/agent_wow_bridge_run86_collection_recovery.json'
for n in range(87,101):
    data=json.loads(previous.read_text())
    if data.get('recovery_self',{}).get('ghost') is False:
        print('Recovered',flush=True);break
    run=subprocess.run([sys.executable,str(ROOT/'scripts/agent_wow_plan_routes.py'),str(previous),'corpse'],capture_output=True,text=True,timeout=30)
    if run.returncode:raise SystemExit(run.stdout+run.stderr)
    name=f'run{n}_collection_recovery';path=ROOT/f'results/pure_network/agent_wow_bridge_{name}.json'
    if path.exists():raise SystemExit('Existing result; resume explicitly')
    cmd=['wsl','-d','WoWLab','-u','wowlab','--exec','python3',LINUX+'/scripts/wsl_bridge_probe.py','--run-id',name,'--path-file',LINUX+'/results/pure_network/agent_wow_route_corpse.json','--query-corpse','--reclaim-corpse']
    r=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=150)
    if not path.exists():raise SystemExit(r.stdout+r.stderr)
    d=json.loads(path.read_text());me=d.get('recovery_self',{})
    print(json.dumps({'run':name,'status':d['status'],'hp':me.get('health'),'ghost':me.get('ghost'),'error':d.get('error'),'position':d.get('path_response',{}).get('result',{}).get('submittedPosition')},ensure_ascii=False),flush=True)
    if d['status']!='PASS':raise SystemExit('Recovery failed; inspect result')
    previous=path
