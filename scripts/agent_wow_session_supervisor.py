"""Same-session recovery before logout; never interpret disconnect as combat end."""
import math
import time
from agent_wow_combat_control import fight, estimated_position


def settle(rpc,drain,position,now_ms,budget=100):
    history=[];deadline=time.monotonic()+budget
    state=drain()
    while state['self']['in_combat'] is True and not state['self']['ghost'] and state['self']['health']>0 and time.monotonic()<deadline:
        # Prefer the creature explicitly targeting this player, not an old GUID.
        enemies=[o for o in state['visible_objects'] if o.get('type_id')==3 and o.get('health',0)>0 and (o.get('movement',{}).get('victim_guid')=='9' or o.get('server_motion',{}).get('facing_guid')=='9')]
        if not enemies:
            time.sleep(.3);state=drain();continue
        enemy=min(enemies,key=lambda o:math.dist(position[:3],estimated_position(o,now_ms())[0]))
        # One explicit defensive phase; controller archives zero-damage and misses.
        phase,state=fight(rpc,drain,enemy['guid'],position,now_ms,budget=min(35,max(1,deadline-time.monotonic())))
        history.append({'target':enemy['guid'],'phase':phase})
        if phase['outcome']=='health_budget':
            # Do not drop the connection. Stop further offense and observe the
            # actual dead/alive transition so normal spirit release is possible.
            while state['self']['in_combat'] is True and state['self']['health']>0 and time.monotonic()<deadline:
                time.sleep(.3);state=drain()
            break
    if state['self']['health']==0 and not state['self']['ghost']:
        release=rpc('bridge.release_spirit',{});history.append({'release_after_observed_death':release})
        for _ in range(20):
            time.sleep(.2);state=drain()
            if state['self']['ghost']:break
    return {'phases':history,'final_self':state['self'],'safe_to_logout':state['self']['in_combat'] is False},state
