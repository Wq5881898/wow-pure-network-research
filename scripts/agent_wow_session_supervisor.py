"""Same-session recovery before logout; never interpret disconnect as combat end."""
import math
import time
from agent_wow_combat_control import fight, estimated_position
from agent_wow_combat_state_machine import (
    CombatSMConfig, CombatStateMachine, ObservationView,
    distance_confirmed_for)


def settle(rpc,drain,position,now_ms,budget=100,advisory=False,config=None,
           escape_path_planned=False):
    history=[];deadline=time.monotonic()+budget
    advisor=CombatStateMachine(config or CombatSMConfig(),
                               escape_path_planned=escape_path_planned) if advisory else None
    state=drain()
    while state['self']['in_combat'] is True and not state['self']['ghost'] and state['self']['health']>0 and time.monotonic()<deadline:
        # Prefer the creature explicitly targeting this player, not an old GUID.
        enemies=[o for o in state['visible_objects'] if o.get('type_id')==3 and o.get('health',0)>0 and (o.get('movement',{}).get('victim_guid')=='9' or o.get('server_motion',{}).get('facing_guid')=='9')]
        if not enemies:
            time.sleep(.3);state=drain();continue
        enemy=min(enemies,key=lambda o:math.dist(position[:3],estimated_position(o,now_ms())[0]))
        if advisor is not None:
            # Advisory only: recorded, never enforced this round.
            ep,source=estimated_position(enemy,now_ms())
            step=advisor.step(ObservationView.from_mapping(
                state['self'], time.monotonic(), target=enemy,
                distance=math.dist(position[:3],ep),
                distance_confirmed=distance_confirmed_for(source)))
            history.append({'advisory_pre_phase':{'target':enemy['guid'],'state':step.state,
                                                  'decision':step.decision,'reasons':list(step.reasons)}})
        # One explicit defensive phase; controller archives zero-damage and misses.
        phase,state=fight(rpc,drain,enemy['guid'],position,now_ms,budget=min(35,max(1,deadline-time.monotonic())),
                          advisory=advisory,config=config,escape_path_planned=escape_path_planned)
        history.append({'target':enemy['guid'],'phase':phase})
        if phase['outcome']=='health_budget':
            # Do not drop the connection. Stop further offense and observe the
            # actual dead/alive transition so normal spirit release is possible.
            while state['self']['in_combat'] is True and state['self']['health']>0 and time.monotonic()<deadline:
                if advisor is not None:
                    step=advisor.step(ObservationView.from_mapping(
                        state['self'], time.monotonic()))
                    history.append({'advisory_low_hold':{'state':step.state,
                                                         'decision':step.decision,
                                                         'reasons':list(step.reasons)}})
                time.sleep(.3);state=drain()
            break
    if state['self']['health']==0 and not state['self']['ghost']:
        release=rpc('bridge.release_spirit',{});history.append({'release_after_observed_death':release})
        for _ in range(20):
            time.sleep(.2);state=drain()
            if state['self']['ghost']:break
    result={'phases':history,'final_self':state['self'],'safe_to_logout':state['self']['in_combat'] is False}
    if advisor is not None:
        result['advisory_final_state']=advisor.state_name()
    return result,state
