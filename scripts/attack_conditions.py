"""Compute auditable attack preconditions from a replayed world-state result."""
import argparse
import json
import math
from pathlib import Path

def angle_delta(a,b):
    return abs((a-b+math.pi)%(2*math.pi)-math.pi)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('state');p.add_argument('--target-guid',required=True);p.add_argument('--target-entry',type=int)
    args=p.parse_args();d=json.loads(Path(args.state).read_text())
    self_obj=d.get('self');targets=[o for o in d.get('visible_objects',[]) if o.get('guid')==args.target_guid]
    if args.target_entry is not None:targets=[o for o in targets if o.get('entry')==args.target_entry]
    if not self_obj or len(targets)!=1:raise SystemExit('self or unique target not found')
    target=targets[0];sp=self_obj['movement']['position_xyzo'];tp=target['movement']['position_xyzo']
    dx,dy,dz=tp[0]-sp[0],tp[1]-sp[1],tp[2]-sp[2]
    horizontal=math.hypot(dx,dy);distance=math.sqrt(dx*dx+dy*dy+dz*dz);bearing=math.atan2(dy,dx);facing=sp[3]
    result={'self_guid':self_obj['guid'],'target_guid':target['guid'],'target_entry':target.get('entry'),'self_position_xyzo':sp,'target_position_xyzo':tp,'horizontal_distance':horizontal,'distance_3d':distance,'height_delta':dz,'bearing_rad':bearing,'self_facing_rad':facing,'facing_error_rad':angle_delta(facing,bearing),'target_health':target.get('health'),'target_max_health':target.get('max_health'),'target_movement_flags':target['movement'].get('movement_flags'),'target_server_motion':target.get('server_motion'),'los':'not available from wire-only state','status':'OBSERVED_CONDITIONS_NOT_ATTACK_SUCCESS'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
