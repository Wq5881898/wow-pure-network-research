"""Finite collection experiments, verified by actual owned item counts."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LINUX='/mnt/c/APP/opencode/project/WoW自动化预研/pure-network'
if __name__=='__main__':
    for n in range(79,91):
        name=f'run{n}_scorpid_collection';path=ROOT/f'results/pure_network/agent_wow_bridge_{name}.json'
        if path.exists():raise SystemExit('Existing run: inspect before resume')
        run=subprocess.run(['wsl','-d','WoWLab','-u','wowlab','--exec','python3',LINUX+'/scripts/wsl_bridge_probe.py','--run-id',name,'--controlled-combat','--combat-entry','3124','--max-target-level','3','--loot'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900)
        if not path.exists():raise SystemExit(run.stdout+run.stderr)
        d=json.loads(path.read_text());count=d.get('owned_item_totals',{}).get('4862',0)
        print(json.dumps({'run':name,'status':d['status'],'outcome':d.get('controlled_combat',{}).get('outcome'),'tails':count,'quest':d.get('final_quest_log'),'error':d.get('error')},ensure_ascii=False),flush=True)
        if d['status']!='PASS' or d.get('controlled_combat',{}).get('outcome')!='target_dead':raise SystemExit('Inspect incomplete collection run')
        if count>=8:break
