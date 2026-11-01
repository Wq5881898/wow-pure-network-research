"""Keep corpse reclaim and immediate preplanned retreat in one live session.

The danger-zone corpse run is an independent task (docs/52): the plan comes
from agent_wow_recovery_plan, the control from here.

Live facts this encodes (measured 2026-11-01):
  - a character that has just died is still awaiting the release prompt and is
    not a ghost yet; the ghost (and therefore the corpse query) only exists
    after the release (CMSG_RELEASE_SPIRIT, 0x15A);
  - the corpse object carries the player's own guid (ObjectTypeId Corpse
    reuses it), so the corpse is identified by guid and never by a guess;
  - a release teleports the ghost to the graveyard without telling the
    bridge, which keeps validating paths against the place the character
    fell at and then rejects the first segment ("segment exceeds 15 yards").
    The corpse run therefore has to run in a session that starts with the
    character already a ghost.
"""
import math
import time


def corpse_from_events(events):
    """The corpse the server reported, or None.

    The server answers a corpse query with the corpse's map and position; the
    position is the server's own answer, never an estimate.
    """
    for event in events or ():
        if event.get('kind') != 'corpse':
            continue
        corpse = event.get('corpse') or {}
        if corpse.get('position_xyz'):
            return corpse
    return None


def release_if_dead(rpc, drain, sleep=time.sleep, clock=time.monotonic, timeout=20.0):
    """Release the spirit when the character is dead but not yet a ghost.

    Returns the observation. A character that is alive is left alone.
    """
    state = drain()
    me = state['self']
    if me.get('ghost') or (me.get('health') or 0) > 0:
        return state
    rpc('bridge.release_spirit', {})
    deadline = clock() + timeout
    while clock() < deadline:
        sleep(0.05)
        state = drain()
        if state['self'].get('ghost'):
            break
    return state


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
