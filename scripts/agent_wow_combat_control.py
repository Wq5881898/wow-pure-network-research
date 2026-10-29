"""Position estimation and bounded attack observation (no timeout auto-retry)."""
import math
import time


def estimated_position(obj, now_ms):
    motion=obj.get('server_motion')
    if not motion:
        return obj['movement']['position_xyzo'][:3], 'last object update'
    if motion.get('stopped'):
        return motion['start_xyz'], 'server stop'
    if motion.get('path_kind')!='linear' or motion.get('spline_flags',0)&(0x800|0x80000|0x200):
        return motion['start_xyz'], 'unsupported motion; start only'
    points=motion['path_xyz']
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    elapsed=max(0,now_ms-motion['received_monotonic_ms'])
    fraction=min(1,elapsed/max(1,motion['duration_ms']))
    remaining=sum(lengths)*fraction
    for a,b,length in zip(points,points[1:],lengths):
        if remaining<=length and length:
            return [x+(y-x)*remaining/length for x,y in zip(a,b)], 'linear receive-time estimate'
        remaining-=length
    return points[-1], 'linear endpoint estimate'


def fight(rpc, drain, target_guid, self_position, now_ms, budget=18):
    rows=[];start=time.monotonic();deadline=start+budget;sent=False;outcome='budget_exhausted';last_angle=None
    observed=drain()
    try:
        while time.monotonic()<deadline:
            targets=[o for o in observed['visible_objects'] if o['guid']==target_guid]
            me=observed['self']
            if me['ghost'] or me['health']==0:outcome='self_dead';break
            if not targets:outcome='target_missing';break
            target=targets[0]
            if target['health']==0:outcome='target_dead';break
            tp,source=estimated_position(target,now_ms())
            distance=math.dist(self_position[:3],tp)
            angle=math.atan2(tp[1]-self_position[1],tp[0]-self_position[0])
            row={'elapsed_ms':round((time.monotonic()-start)*1000),'self_health':me['health'],'in_combat':me['in_combat'],'target_health':target['health'],'target_position_estimate':tp,'position_source':source,'distance_estimate':distance}
            rows.append(row)
            # Face continues while the game executes the attack timer.
            if last_angle is None or abs((angle-last_angle+math.pi)%(2*math.pi)-math.pi)>.12:
                row['face']=rpc('bridge.face',{'angle':angle})
                if 'error' in row['face']:outcome='face_rejected';break
                last_angle=angle
            if not sent:
                if (me['health']<50 and me['in_combat'] is not True) or distance>5:outcome='preconditions_not_met';break
                row['start']=rpc('bridge.start_attack',{'guid':target_guid})
                if 'error' in row['start']:outcome='start_rejected';break
                sent=True
            if me['health']<25:outcome='health_budget';break
            time.sleep(.2);observed=drain()
    finally:
        stop=rpc('bridge.stop_attack',{})
    observed=drain()
    return {'outcome':outcome,'start_sent_once':sent,'observations':rows,'stop':stop},observed
