"""Keep corpse reclaim and immediate preplanned retreat in one live session."""
import math
import time


def reclaim_and_retreat(rpc,drain,plan,current_position,timeout=8):
    state=drain();me=state['self']
    if not me['ghost']:raise ValueError('expected live ghost observation')
    if math.dist(current_position[:3],plan['reclaim_xyz'])>1:raise ValueError('not at planned reclaim point')
    corpse_replies=[e['corpse'] for e in state['event_log'] if e.get('kind')=='corpse' and e['corpse']['found']]
    if not corpse_replies or math.dist(corpse_replies[-1]['position_xyz'],plan['corpse_xyz'])>1:
        raise ValueError('corpse changed; discard and replan')
    result={'reclaim':rpc('bridge.reclaim_corpse',{'guid':'9'}),'phases':[]}
    if 'error' in result['reclaim']:return {**result,'outcome':'request_rejected'},state
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        state=drain()
        if state['self']['health']>0 and not state['self']['ghost']:break
        time.sleep(.05)
    else:return {**result,'outcome':'revival_unconfirmed'},state
    # No sleep/reward queries between confirmed revival and first movement.
    for chunk in plan['retreat_chunks']:
        reply=rpc('bridge.follow_path',{'points':chunk,'run':True})
        state=drain();me=state['self']
        result['phases'].append({'movement':reply,'health':me['health'],'ghost':me['ghost'],'in_combat':me['in_combat']})
        if me['health']==0 or me['ghost']:return {**result,'outcome':'died_during_retreat'},state
        if 'error' in reply:return {**result,'outcome':'movement_unconfirmed'},state
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        state=drain();me=state['self']
        if me['health']==0 or me['ghost']:return {**result,'outcome':'died_after_retreat'},state
        if me['in_combat'] is False:return {**result,'outcome':'alive_out_of_combat'},state
        time.sleep(.2)
    return {**result,'outcome':'still_in_combat'},state
