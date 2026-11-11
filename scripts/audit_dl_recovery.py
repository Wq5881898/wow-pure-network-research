"""Offline bounded incremental audit of dl's third contact; never logs in."""
import json
import math
from pathlib import Path
from agent_wow_worldstate import IncrementalWorld
from agent_wow_combat_control import estimated_position

ROOT = Path(__file__).resolve().parents[1]


def decode(capture, cutoff=None):
    world = IncrementalWorld(capture['session_id'], capture['self_guid'])
    world.max_packets = 100000
    packets = [p for p in capture['packets']
               if cutoff is None or p['monotonic_ms'] <= cutoff]
    state = None
    for offset in range(0, len(packets), 1000):
        state = world.apply(packets[offset:offset+1000])
    return state, packets


def main():
    folder = ROOT / 'results/pure_network'
    capture = json.loads((folder / 'agent_wow_bridge_scorp_anchor_patrol_20261110_dl.packets.json').read_text())
    state, packets = decode(capture)
    guid = '17379391014452080920'
    here = [-722.955, -5029.322, 16.671305]
    samples = []
    for cutoff in (220313, 221000, 221500):
        snapshot, prefix = decode(capture, cutoff)
        target = next(o for o in snapshot['visible_objects'] if o['guid'] == guid)
        position, source = estimated_position(target, cutoff)
        samples.append({'packet_time_ms': cutoff, 'last_sequence': prefix[-1]['sequence'],
                        'player_submitted_position_from_activity': here,
                        'target_estimate': position, 'position_source': source,
                        'distance_estimate': math.dist(here, position[:3]),
                        'health': snapshot['self']['health'],
                        'in_combat': snapshot['self']['in_combat'],
                        'motion_sequence': target['server_motion']['source_sequence']})
    report = {'scope':'offline evidence, not live validation', 'packets':len(packets),
              'decoder':'IncrementalWorld batches <=1000, max_packets=100000',
              'third_target_guid':guid, 'samples_near_refusal':samples,
              'nav_distance':None,
              'nav_distance_reason':'_close_distance skips already-in-combat before nav; no range override',
              'verified_failure_branch':'HP203 + combat true excludes low-health clause; estimated range >5 refuses start',
              'timing_limit':'packet samples bracket contact; action did not retain exact first fight row',
              'final_health':state['self']['health']}
    output = folder / 'dl_recovery_offline_audit_20261110.json'
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
