"""Read-only bounded reconstruction of ds contact; output never overwrites evidence."""
import json
import math
from pathlib import Path
from agent_wow_worldstate import IncrementalWorld
from agent_wow_combat_control import estimated_position
from agent_wow_attackers import active_attackers

ROOT = Path(__file__).resolve().parents[1]
GUID = '17379391014452081297'
HERE = [-725.11304, -5012.455, 17.743908]


def reconstruct():
    folder = ROOT / 'results/pure_network'
    capture = json.loads((folder / 'agent_wow_bridge_scorp_running_prep_20261110_ds.packets.json').read_text())
    world = IncrementalWorld(capture['session_id'], capture['self_guid'])
    world.max_packets = 100000
    rows = []
    for packet in capture['packets']:
        state = world.apply([packet])
        if not 285000 <= packet['monotonic_ms'] <= 345000:
            continue
        target = next((o for o in state['visible_objects'] if o['guid'] == GUID), None)
        attackers = active_attackers(state, capture['self_guid'])
        if target:
            pos, source = estimated_position(target, packet['monotonic_ms'])
            rows.append({'sequence': packet['sequence'], 'monotonic_ms': packet['monotonic_ms'],
                         'opcode': packet['opcode'], 'selected_guid': GUID,
                         'static': target.get('movement'), 'motion': target.get('server_motion'),
                         'estimate': pos, 'source': source, 'distance': math.dist(HERE, pos),
                         'player_submitted': HERE, 'player_observed': state['self'].get('movement'),
                         'health': state['self']['health'], 'combat': state['self']['in_combat'],
                         'attackers': [{'guid': a['guid'], 'health': a['health'],
                                        'motion': a.get('server_motion'),
                                        'estimate': estimated_position(a, packet['monotonic_ms'])}
                                       for a in attackers],
                         'events': [e for e in state['event_log'] if e.get('sequence') == packet['sequence']]})
    activity = [json.loads(s) for s in (folder / 'activity_scorp_running_prep_20261110_ds.jsonl').read_text().splitlines()]
    report = {'decoder': 'IncrementalWorld batches=1 <=4096; max_packets=100000',
              'packet_count': len(capture['packets']), 'errors': state['errors'],
              'timeline': rows, 'activity': [a for a in activity if 415 <= a.get('started_monotonic', 0) <= 425],
              'scope': 'offline reconstruction; submitted coordinates are predictions, not acknowledgements'}
    return report


if __name__ == '__main__':
    output = ROOT / 'results/pure_network/ds_contact_root_cause_audit.json'
    if output.exists():
        raise FileExistsError(output)
    report = reconstruct()
    with output.open('x') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({'output': str(output), 'packets': report['packet_count'], 'rows': len(report['timeline']),
                      'contact': [{'seq': r['sequence'], 'ms': r['monotonic_ms'], 'distance': r['distance'],
                                   'motion': r['motion'], 'attackers': r['attackers'],
                                   'events': r['events']}
                                  for r in report['timeline'] if r['sequence'] in (630,636,637,638,655)]}, indent=2))
