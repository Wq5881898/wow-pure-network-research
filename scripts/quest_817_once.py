"""Accept nearby Practical Prey once and preserve actual questgiver replies."""
import json
import math
import time
import sys
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session
from agent_wow_combat_control import estimated_position

RUN = sys.argv[1] if len(sys.argv) > 1 else 'practical_prey_20261110_al'
QUEST = int(sys.argv[2]) if len(sys.argv) > 2 else 817
NPC = int(sys.argv[3]) if len(sys.argv) > 3 else 3194
PATH = Path(__file__).resolve().parents[1] / f'results/pure_network/quest_{RUN}.json'

def main():
    if PATH.exists():
        raise SystemExit('report exists; reconcile instead of resubmitting')
    backend = PureNetworkBackend(RUN)
    report = {'quest': QUEST, 'status': 'unknown'}
    def save(data=None):
        temp = PATH.with_suffix('.json.tmp')
        temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temp.replace(PATH)
    try:
        backend.open()
        before = backend.observe()
        report['before'] = before.to_dict()
        if before.in_combat is not False or before.ghost or before.health <= 0:
            raise RuntimeError('unsafe state')
        if any(q['quest'] == QUEST for q in before.quest_log):
            report['status'] = 'already_active'
        else:
            if '--approach-npc' in sys.argv:
                for _ in range(12):
                    state = backend.drain()
                    live = [o for o in state['visible_objects'] if o.get('entry') == NPC]
                    if len(live) != 1:
                        raise RuntimeError('quest NPC not uniquely visible')
                    here = list((backend.submitted_position or before.position)[:3])
                    destination = estimated_position(live[0], backend.stream_now())[0][:3]
                    if math.dist(here, destination) <= 3:
                        break
                    from agent_wow_travel import _bounded_leg
                    raw = backend._nav(here, destination)
                    prefix = _bounded_leg([dict(zip(('x','y','z'), p[:3]))
                                           for p in raw['waypoints']], here, 15)
                    points = backend._densify({'waypoints': [[p['x'],p['y'],p['z']] for p in prefix]},
                                              here, slope_tolerance=.7)
                    _, failure = backend._send_path(points, run=True)
                    if failure:
                        raise RuntimeError(failure)
                    check = backend.observe()
                    if check.in_combat is not False or check.ghost or check.health <= 0:
                        raise RuntimeError('NPC approach interrupted by combat')
            npcs = [o for o in backend.state['visible_objects'] if o.get('entry') == NPC
                    and math.dist(backend.submitted_position or before.position,
                                  estimated_position(o, backend.stream_now())[0][:3]) < 5]
            if len(npcs) != 1:
                raise RuntimeError('no unique nearby Velrin Fang')
            guid = npcs[0]['guid']
            cursor = backend.cursor
            report['npc_guid'] = guid
            report['query_reply'] = backend.rpc('bridge.query_quest', {'guid': guid, 'quest': QUEST})
            time.sleep(.5)
            state = backend.drain()
            report['questgiver_events'] = [e for e in state['event_log']
                                           if e['sequence'] > cursor]
            report['accept_dispatched'] = True
            save()
            report['accept_reply'] = backend.rpc('bridge.accept_quest', {'guid': guid, 'quest': QUEST})
            for _ in range(10):
                time.sleep(.3)
                after = backend.observe()
                report['after'] = after.to_dict()
                if any(q['quest'] == QUEST for q in after.quest_log):
                    report['status'] = 'active_observed'
                    break
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()

if __name__ == '__main__':
    main()
