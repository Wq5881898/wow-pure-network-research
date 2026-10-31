"""Offline replay forensics: drive the combat state machine from real traces.

Reads archived packet captures with the existing IncrementalWorld decoder,
reconstructs an observation timeline, and emits a JSON decision timeline.
Strictly read-only against results/: nothing here opens a results file for
writing. All time is injected from the capture's monotonic stamps; the state
machine itself never touches a clock.

Observed-vs-unobserved discipline: a state is only reported when a sample
witnessed it. Missing samples are reported as unobserved, never inferred.
"""
import argparse
import json
import math
from pathlib import Path

from agent_wow_worldstate import IncrementalWorld
from agent_wow_combat_state_machine import (
    CombatSMConfig, CombatStateMachine, ObservationView,
    REASON_TARGET_MISSING, REASON_TARGET_DEAD, REASON_HEALTH_UNKNOWN,
    REASON_OUT_OF_COMBAT_CONFIRMED, REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,
    DISENGAGED_CONFIRMED, DEAD, GHOST, UNRESOLVED)

# Estimated-position sources that are not a confirmed self position; during
# combat the server never broadcasts the mover's own position, so every
# source reachable here is unconfirmed.
UNCONFIRMED_SOURCES = ('last object update', 'server stop',
                       'unsupported motion; start only',
                       'linear receive-time estimate', 'linear endpoint estimate')

DEFAULT_TRACES = (
    'agent_wow_bridge_quest792_long01_unit1_verify.packets.json',
    'agent_wow_bridge_run40_quest788_kill1.packets.json',
    'agent_wow_bridge_run51_retreat_combat.packets.json',
    'agent_wow_bridge_run113_confirmed_combat.packets.json',
    'agent_wow_bridge_run116_final_tail_combat.packets.json')


def _estimate_position(obj, now_ms):
    """Same source taxonomy as agent_wow_combat_control.estimated_position.

    Replay has no wall clock; the capture's own monotonic stamp is the
    injection point, so receive-time interpolation is not attempted and the
    start point stands in for unsupported or stopped motion.
    """
    motion = obj.get('server_motion')
    if not motion:
        return obj['movement']['position_xyzo'][:3], 'last object update'
    if motion.get('stopped'):
        return motion['start_xyz'], 'server stop'
    if motion.get('path_kind') != 'linear' or motion.get('spline_flags', 0) & (0x800 | 0x80000 | 0x200):
        return motion['start_xyz'], 'unsupported motion; start only'
    return motion['path_xyz'][-1], 'linear endpoint estimate'


def select_target(state, self_position, engaged_guid=None):
    """Deterministic target: the creature in combat with this player.

    A creature counts as engaging the player when its movement victim GUID or
    its server-directed facing GUID is the player GUID, or when the player or
    that creature appears in the decoded damage log (attacker_state). Once a
    target is engaged it is sticky until it dies or vanishes; ties break on
    estimated distance then GUID so replays are reproducible.
    """
    engaged = engaged_guid or _damage_engaged(state)
    candidates = []
    for obj in state['visible_objects']:
        if obj.get('type_id') != 3:
            continue
        movement = obj.get('movement', {})
        motion = obj.get('server_motion', {})
        hostile = (movement.get('victim_guid') == '9'
                   or motion.get('facing_guid') == '9'
                   or obj['guid'] == engaged)
        if not hostile:
            continue
        position, source = _estimate_position(obj, 0)
        candidates.append((math.dist(self_position[:3], position),
                           str(obj['guid']), obj))
    if not candidates:
        return None
    if engaged is not None:
        for _, _, obj in candidates:
            if obj['guid'] == engaged:
                return obj
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _damage_engaged(state):
    """First creature GUID in a damage exchange with the player, or None."""
    for event in state['event_log']:
        if event.get('kind') != 'attacker_state':
            continue
        attack = event['attack']
        if attack['attacker_guid'] == '9' and attack['target_guid'] != '9':
            return attack['target_guid']
        if attack['target_guid'] == '9' and attack['attacker_guid'] != '9':
            return attack['attacker_guid']
    return None


def replay_trace(path, config=None, target_guid=None,
                 escape_path_planned=False, start_sent=False,
                 engage_on_damage=True):
    """Replay one capture; return the decision timeline and evidence summary.

    The timeline has one entry per packet that changed the self or the
    selected target's decision-relevant state. When the capture's damage log
    proves a player/creature exchange (engage_on_damage), the machine starts
    in engaging so its decisions model the dispatched attack. The final entry
    is the budget-exhaustion end if the machine is still open at capture end.
    """
    trace = json.loads(Path(path).read_text(encoding='utf-8'))
    engine = IncrementalWorld(trace['session_id'], trace['self_guid'])
    sm = CombatStateMachine(config or CombatSMConfig(),
                            target_guid=target_guid,
                            escape_path_planned=escape_path_planned,
                            start_sent=start_sent)
    timeline = []
    previous = None
    engaged = bool(start_sent)
    last_sequence = 0
    damage_engaged = None

    def relevant(me, target):
        return (me.get('health'), me.get('health_transmitted'),
                me.get('max_health'), me.get('ghost'), me.get('in_combat'),
                None if target is None else (target['guid'],
                                             target.get('health')))

    def build_view(now, me, target, self_position):
        transmitted = bool(me.get('health_transmitted', False))
        health = me.get('health') if transmitted else None
        max_health = int(me.get('max_health') or 0)
        if transmitted and health is not None and max_health <= 0:
            health = None
        distance, source, confirmed = None, None, False
        if target is not None:
            position, source = _estimate_position(target, 0)
            distance = math.dist(self_position[:3], position)
            confirmed = source not in UNCONFIRMED_SOURCES
        return ObservationView(
            sampled_at=now / 1000.0, health=health, max_health=max_health,
            health_transmitted=transmitted, ghost=bool(me.get('ghost')),
            in_combat=me.get('in_combat'),
            target_guid=target['guid'] if target is not None else None,
            target_present=target is not None,
            target_health=target.get('health') if target is not None else None,
            distance=distance, distance_confirmed=confirmed)

    for offset in range(0, len(trace['packets']), 16):
        batch = trace['packets'][offset:offset + 16]
        state = engine.apply(batch)
        if state['errors']:
            raise RuntimeError('decode errors at batch %s: %s'
                               % (offset, state['errors']))
        me = state['self']
        if me is None:
            continue
        last_sequence = batch[-1]['sequence']
        now = batch[-1].get('monotonic_ms', 0)
        self_position = me['movement']['position_xyzo'][:3]
        if engage_on_damage and damage_engaged is None:
            damage_engaged = _damage_engaged(state)
            if damage_engaged is not None:
                engaged = True
                sm._engaged = True
        target = (next((o for o in state['visible_objects']
                        if o['guid'] == target_guid), None)
                  if target_guid else select_target(
                      state, self_position, engaged_guid=damage_engaged))
        signature = relevant(me, target)
        if signature == previous:
            continue
        previous = signature
        observation = build_view(now, me, target, self_position)
        step = sm.step(observation)
        timeline.append({'index': len(timeline),
                         'sequence': last_sequence,
                         'monotonic_ms': now,
                         'state': step.state,
                         'decision': step.decision,
                         'reasons': list(step.reasons),
                         'observations': {
                             'self_health': me.get('health'),
                             'health_transmitted': bool(me.get('health_transmitted', False)),
                             'self_max_health': me.get('max_health'),
                             'self_in_combat': me.get('in_combat'),
                             'self_ghost': bool(me.get('ghost')),
                             'target_guid': target['guid'] if target else None,
                             'target_health': target.get('health') if target else None,
                             'distance_estimate': None if observation.distance is None else round(observation.distance, 3),
                             'distance_confirmed': observation.distance_confirmed}})
    if not sm.finished:
        step = sm.finish_budget()
        timeline.append({'index': len(timeline),
                         'sequence': last_sequence,
                         'monotonic_ms': None,
                         'state': step.state,
                         'decision': step.decision,
                         'reasons': list(step.reasons),
                         'observations': {'note': 'capture ended; budget end injected by caller'}})
    decisions = [entry['decision'] for entry in timeline]
    states = [entry['state'] for entry in timeline]
    combat_observed = (engaged or 'in_combat' in states
                       or 'disengaging' in states
                       or 'low_health_exit' in states
                       or 'target_out_of_range' in states)
    return {
        'trace': str(path),
        'session_id': trace['session_id'],
        'packets': len(trace['packets']),
        'capture_complete': bool(trace.get('capture_complete')),
        'engaged_observed': combat_observed,
        'final_state': sm.state_name(),
        'timeline': timeline,
        'summary': {
            'states_visited': sorted(set(states)),
            'decisions': decisions,
            'start_attack_decisions': decisions.count('start_attack'),
            'low_exit_observed': 'low_health_exit' in states,
            'target_out_of_range_observed': 'target_out_of_range' in states,
            'disengaged_confirmed_observed': 'disengaged_confirmed' in states,
            'dead_observed': 'dead' in states,
            'ghost_observed': 'ghost' in states,
            'health_unknown_seen': any(
                REASON_HEALTH_UNKNOWN in entry['reasons'] for entry in timeline),
            'unobserved': {
                'low_health_exit': 'low_health_exit' not in states,
                'target_out_of_range': 'target_out_of_range' not in states,
                'disengaged_confirmed': 'disengaged_confirmed' not in states,
                'dead': 'dead' not in states,
                'ghost': 'ghost' not in states}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path,
                        help='JSON decision-timeline output path (caller-chosen)')
    parser.add_argument('--results-dir', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'results/pure_network',
                        help='directory holding archived .packets.json captures (read-only)')
    parser.add_argument('--trace', action='append', default=None,
                        help='specific capture file name; repeatable. Default: the five contract archives')
    parser.add_argument('--low-health-ratio', type=float, default=0.5)
    parser.add_argument('--critical-health-ratio', type=float, default=0.25)
    parser.add_argument('--out-of-combat-confirmations', type=int, default=3)
    parser.add_argument('--min-disengage-seconds', type=float, default=2.0)
    parser.add_argument('--max-range', type=float, default=5.0)
    args = parser.parse_args(argv)
    config = CombatSMConfig(
        low_health_ratio=args.low_health_ratio,
        critical_health_ratio=args.critical_health_ratio,
        out_of_combat_confirmations=args.out_of_combat_confirmations,
        min_disengage_seconds=args.min_disengage_seconds,
        max_range=args.max_range)
    names = args.trace or list(DEFAULT_TRACES)
    reports = []
    for name in names:
        reports.append(replay_trace(args.results_dir / name, config=config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema': 1, 'reports': reports},
                                      ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({'output': str(args.output),
                      'reports': [{'trace': Path(r['trace']).name,
                                   'packets': r['packets'],
                                   'final_state': r['final_state'],
                                   'summary': r['summary']}
                                  for r in reports]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
