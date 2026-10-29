"""Return completed quest to its known NPC in archived bounded travel phases."""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LINUX='/mnt/c/APP/opencode/project/WoW自动化预研/pure-network'
previous=ROOT/'results/pure_network/agent_wow_bridge_run66_quest788_controlled.json'
data=json.loads(previous.read_text())
current=data.get('approach_refreshes',[])
position=(current[-1]['response'] if current else data['controlled_path'])['result']['submittedPosition']
for n in range(67,82):
    plan_input=ROOT/'.cache/agent_wow_return_plan_input.json'
    plan_input.write_text(json.dumps({'login_position':{'position_xyzo':position}}))
    plan=subprocess.run([sys.executable,str(ROOT/'scripts/agent_wow_plan_routes.py'),str(plan_input),'questgiver'],capture_output=True,text=True,timeout=30)
    if plan.returncode:raise SystemExit(plan.stdout+plan.stderr)
    route=json.loads((ROOT/'results/pure_network/agent_wow_route_questgiver.json').read_text())
    name=f'run{n}_quest788_return'
    output=ROOT/f'results/pure_network/agent_wow_bridge_{name}.json'
    if output.exists():raise SystemExit('Do not overwrite '+name)
    run=subprocess.run(['wsl','-d','WoWLab','-u','wowlab','--exec','python3',LINUX+'/scripts/wsl_bridge_probe.py','--run-id',name,'--path-file',LINUX+'/results/pure_network/agent_wow_route_questgiver.json','--run-path'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=150)
    if not output.exists():raise SystemExit(run.stdout+run.stderr)
    data=json.loads(output.read_text())
    print(json.dumps({'run':name,'status':data['status'],'error':data.get('error'),'end':data.get('path_response'),'quest':data.get('final_quest_log')},ensure_ascii=False),flush=True)
    if data['status']!='PASS':raise SystemExit('Inspect failed return phase')
    position=data['path_response']['result']['submittedPosition']
    if math.dist(position[:3],[-600.13202,-4186.18994,41.18079])<2:break
