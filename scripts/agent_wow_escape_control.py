"""Stop and traverse a previously planned return path; never assert leash reset."""
import time


def retreat(rpc, drain, outbound_points, origin, wait_seconds=15,
            sleep=time.sleep, clock=time.monotonic, route=None):
    # Reverse the actual submitted route rather than invent a straight escape,
    # unless the caller already planned an ordered escape route (a route that
    # ends far from the hostile, planned on the navmesh).
    if route is None:
        points = list(reversed(outbound_points[:-1])) + [dict(zip(('x', 'y', 'z'), origin[:3]))]
        source = 'reversed submitted navmesh route'
    else:
        points = list(route)
        source = 'navmesh route planned away from the hostile'
    result = {'stop': rpc('bridge.stop_attack', {}), 'path_source': source, 'points': points}
    result['movement'] = rpc('bridge.follow_path', {'points': points, 'run': True})
    if 'error' in result['movement']:
        result['outcome']='retreat_movement_rejected'
        return result,drain()
    deadline=clock()+wait_seconds
    result['samples']=[]
    while True:
        state=drain();me=state['self']
        result['samples'].append({'hp':me['health'],'ghost':me['ghost'],'in_combat':me['in_combat']})
        if me['health']==0 or me['ghost']:result['outcome']='dead';break
        if me['in_combat'] is False:result['outcome']='out_of_combat';break
        if clock()>=deadline:result['outcome']='still_in_combat';break
        sleep(.3)
    return result,state
