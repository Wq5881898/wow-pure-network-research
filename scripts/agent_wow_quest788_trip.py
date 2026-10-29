"""Bounded travel and quest-kill experiments; retain each run separately."""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LINUX='/mnt/c/APP/opencode/project/WoW自动化预研/pure-network'

def experiment(name,*args):
    dest=ROOT/f'results/pure_network/agent_wow_bridge_{name}.json'
    if dest.exists():raise RuntimeError(f'Refusing to overwrite {dest.name}')
    run=subprocess.run(['wsl','-d','WoWLab','-u','wowlab','--exec','python3',LINUX+'/scripts/wsl_bridge_probe.py','--run-id',name,*args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
    if not dest.exists():raise RuntimeError(run.stdout+run.stderr)
    data=json.loads(dest.read_text())
    print(json.dumps({'run':name,'status':data['status'],'error':data.get('error'),'login':data.get('login_position'),'end':data.get('path_response'),'quest_log':data.get('final_quest_log'),'progress':data.get('quest_progress_events')},ensure_ascii=False),flush=True)
    if data['status']!='PASS':raise RuntimeError(f'{name} incomplete: inspect raw result')
    return data,dest

if __name__=='__main__':
    previous=ROOT/'results/pure_network/agent_wow_bridge_run36_quest788_travel.json'
    for n in range(37,39):
        data=json.loads(previous.read_text())
        pos=data.get('path_response',{}).get('result',{}).get('submittedPosition',data['login_position']['position_xyzo'])[:3]
        if math.dist(pos,[-680.926,-4284.82,40.71])<2:break
        plan=subprocess.run([sys.executable,str(ROOT/'scripts/agent_wow_plan_routes.py'),str(previous),'hunt'],cwd=ROOT,capture_output=True,text=True,timeout=30)
        if plan.returncode:raise RuntimeError(plan.stderr+plan.stdout)
        data,previous=experiment(f'run{n}_quest788_travel','--path-file',LINUX+'/results/pure_network/agent_wow_route_hunt.json')
    data,previous=experiment('run39_quest788_kill1','--approach-entry','3098','--attack-near','--settle-combat','--loot')
