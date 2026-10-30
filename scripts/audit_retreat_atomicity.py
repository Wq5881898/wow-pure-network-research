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
    valid = bool(chunks)
    for index, points in enumerate(chunks):
        if len(points) < 2 or any(len(point) != 3 or not all(math.isfinite(v) for v in point)
                                  for point in points):
            raise ValueError('route points must be finite XYZ triples')
        if index and math.dist(chunks[index - 1][-1], points[0]) > .1:
            valid = False
        if index and points[0] != chunks[index - 1][-1]:
            # Concatenation omits a repeated start. Even a sub-yard mismatch
            # creates an unaccounted bridge step and invalidates this audit.
            valid = False
        horizontal = 0.0
        for a, b in zip(points, points[1:]):
            step = math.hypot(b[0] - a[0], b[1] - a[1])
            if step > 15 or abs(b[2] - a[2]) > 3:
                valid = False
            horizontal += step
        lengths.append(horizontal)
    # A single FollowPath has one 50-yard path budget, regardless of how many
    # independently verified 20-yard chunks could be concatenated.
    combined_points = sum(len(points) for points in chunks) - len(chunks) + 1 if chunks else 0
    return {
        'source': 'height_R792_segment1..6_plan.json and *_reconciled.json',
        'chunk_horizontal_lengths': lengths,
        'total_horizontal_length': sum(lengths),
        'single_call_horizontal_limit': BRIDGE_MAX_HORIZONTAL,
        'single_call_waypoint_limit': BRIDGE_MAX_WAYPOINTS,
        'combined_waypoints': combined_points,
        'route_bridge_segments_valid': valid,
        'one_call_full_route_possible': (valid and .05 <= sum(lengths) <= BRIDGE_MAX_HORIZONTAL
                                         and combined_points <= BRIDGE_MAX_WAYPOINTS),
        'one_call_parameters_fit': (valid and .05 <= sum(lengths) <= BRIDGE_MAX_HORIZONTAL
                                    and combined_points <= BRIDGE_MAX_WAYPOINTS),
        'arrival_confirmed': False,
        'scope': 'offline bridge input capacity only',
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
