"""Run a finite sequence of independently archived quest-credit experiments."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LINUX='/mnt/c/APP/opencode/project/WoW自动化预研/pure-network'

if __name__=='__main__':
    for n in range(64,76):
        name=f'run{n}_quest788_controlled'
        path=ROOT/f'results/pure_network/agent_wow_bridge_{name}.json'
        if path.exists():
            data=json.loads(path.read_text())
        else:
            run=subprocess.run(['wsl','-d','WoWLab','-u','wowlab','--exec','python3',LINUX+'/scripts/wsl_bridge_probe.py','--run-id',name,'--controlled-combat','--max-target-level','1'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
            if not path.exists():raise SystemExit(run.stdout+run.stderr)
            data=json.loads(path.read_text())
        combat=data.get('controlled_combat',{})
        me=data.get('controlled_final_self',{})
        quests=data.get('final_quest_log',[])
        q=next((q for q in quests if q['quest']==788),None)
        print(json.dumps({'run':name,'status':data['status'],'outcome':combat.get('outcome'),'health':me.get('health'),'combat':me.get('in_combat'),'quest':q,'credits':data.get('quest_progress_events'),'error':data.get('error')},ensure_ascii=False),flush=True)
        if data['status']!='PASS' or combat.get('outcome')!='target_dead' or me.get('in_combat') is not False or me.get('ghost') or not q:
            raise SystemExit('Stopped: inspect this run; no blind continuation')
        if q['counters'][0]>=8 or q['state']&1:break
