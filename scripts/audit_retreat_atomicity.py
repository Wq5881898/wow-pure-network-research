"""Offline capability audit; no login, RPC or route rewriting."""
import json
import math
from pathlib import Path

from agent_wow_retreat import prepare


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'results' / 'pure_network'
BRIDGE_MAX_HORIZONTAL = 50  # probes/AgentWowBridge/actions.go:28
BRIDGE_MAX_WAYPOINTS = 128    # probes/AgentWowBridge/actions.go:18
BRIDGE_RUNNING_SPEED = 7     # probes/AgentWowBridge/actions.go:46-47


def audit(chunks):
    lengths = []
    for points in chunks:
        lengths.append(sum(math.hypot(b[0] - a[0], b[1] - a[1])
                           for a, b in zip(points, points[1:])))
    # A single FollowPath has one 50-yard path budget, regardless of how many
    # independently verified 20-yard chunks could be concatenated.
    combined_points = sum(len(points) for points in chunks) - len(chunks) + 1
    return {
        'source': 'height_R792_segment1..6_plan.json and *_reconciled.json',
        'chunk_horizontal_lengths': lengths,
        'total_horizontal_length': sum(lengths),
        'single_call_horizontal_limit': BRIDGE_MAX_HORIZONTAL,
        'single_call_waypoint_limit': BRIDGE_MAX_WAYPOINTS,
        'combined_waypoints': combined_points,
        'one_call_full_route_possible': (sum(lengths) <= BRIDGE_MAX_HORIZONTAL
                                         and combined_points <= BRIDGE_MAX_WAYPOINTS),
        'minimum_calls_from_distance_only': math.ceil(sum(lengths) / BRIDGE_MAX_HORIZONTAL),
        'running_motion_seconds_lower_bound': sum(lengths) / BRIDGE_RUNNING_SPEED,
        'semantics': ('speed is a nominal command timing bound, not observed server motion; '
                      'submitting a segment or returning submittedPosition does not confirm arrival'),
    }


def main():
    chunks = prepare(EVIDENCE)
    result = audit(chunks)
    if result['one_call_full_route_possible']:
        raise RuntimeError('expected single-call route to exceed bridge bounds')
    output = EVIDENCE / 'retreat_atomicity_audit01.json'
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
