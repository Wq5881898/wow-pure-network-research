"""Per-packet replay of the real long01 fight against the fixed state machine.

The existing replay_combat_state_machine only feeds samples whose
decision-relevant signature changed. The combat-state-002 defects lived in
the confirmation *timing*, which needs every packet's in_combat sample
(consecutive-sample counts and dwell), so this replay drives the machine
with one observation per packet that carries a readable self state.

Strictly read-only against results/: the archive is opened for reading only
and the JSON decision timeline goes to a caller-chosen output path that
must not live under results/. Observed-vs-unobserved discipline: a state is
only reported when a sample witnessed it; missing samples are never
inferred.
"""
import argparse
import hashlib
import json
from pathlib import Path

from agent_wow_worldstate import IncrementalWorld
from agent_wow_combat_state_machine import (
    CombatSMConfig, CombatStateMachine, ObservationView)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = 'agent_wow_bridge_quest792_long01_unit1_verify.packets.json'
LONG01_TARGET = '17379391014048896448'


def replay_combat_trace(path, config=None, target_guid=LONG01_TARGET):
    """Replay one capture packet-by-packet; return timeline and evidence.

    One timeline entry per packet with a readable self state. The machine
    starts fresh (no injected start): start_attack may only be advised by
    the machine itself, and a pending, never-observed start must never open
    a confirmation window (combat-state-002 fix A).
    """
    path = Path(path)
    before_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    trace = json.loads(path.read_bytes())
    engine = IncrementalWorld(trace['session_id'], trace.get('self_guid', '9'))
    sm = CombatStateMachine(config or CombatSMConfig(),
                            target_guid=target_guid)
    timeline = []
    for packet in trace['packets']:
        try:
            state = engine.apply([packet])
        except Exception:
            # Undecodable tail packet: stop, exactly like the controller's
            # regression guard; never guess state past the decode failure.
            break
        me = state.get('self')
        if not me:
            continue
        target = next((o for o in state.get('visible_objects', [])
                       if o['guid'] == target_guid), None)
        observation = ObservationView(
            sampled_at=packet['monotonic_ms'] / 1000.0,
            health=me.get('health') or 0,
            max_health=int(me.get('max_health') or 0),
            health_transmitted=me.get('health_transmitted') is not False,
            ghost=bool(me.get('ghost')),
            in_combat=me.get('in_combat'),
            target_guid=target['guid'] if target else None,
            target_present=target is not None,
            target_health=target.get('health') if target else None,
            # In combat the server never broadcasts the mover's own
            # position, so no distance here is a confirmed distance.
            distance=None, distance_confirmed=False)
        step = sm.step(observation)
        timeline.append({'index': len(timeline),
                         'sequence': packet.get('sequence'),
                         'monotonic_ms': packet.get('monotonic_ms'),
                         'state': step.state,
                         'decision': step.decision,
                         'reasons': list(step.reasons),
                         'observations': {
                             'self_health': me.get('health'),
                             'health_transmitted': bool(
                                 me.get('health_transmitted', False)),
                             'self_max_health': me.get('max_health'),
                             'self_in_combat': me.get('in_combat'),
                             'self_ghost': bool(me.get('ghost')),
                             'target_present': target is not None,
                             'target_health': (target or {}).get('health'),
                             'distance_confirmed': False}})
    if sm.state_name() != 'unresolved':
        step = sm.finish_budget()
        timeline.append({'index': len(timeline),
                         'sequence': None,
                         'monotonic_ms': None,
                         'state': step.state,
                         'decision': step.decision,
                         'reasons': list(step.reasons),
                         'observations': {
                             'note': 'capture ended; budget end injected '
                                     'by caller'}})
    if hashlib.sha256(path.read_bytes()).hexdigest() != before_digest:
        raise RuntimeError('replay must not modify %s' % path)
    states = [entry['state'] for entry in timeline]
    decisions = [entry['decision'] for entry in timeline]
    in_combat_indices = [i for i, e in enumerate(timeline)
                         if e['state'] == 'in_combat']
    confirmed_indices = [i for i, e in enumerate(timeline)
                         if e['state'] == 'disengaged_confirmed']
    return {
        'trace': str(path),
        'session_id': trace['session_id'],
        'packets': len(trace['packets']),
        'samples': len(states),
        'target_guid': target_guid,
        'final_state': sm.state_name(),
        'timeline': timeline,
        'summary': {
            'states_visited': sorted(set(states)),
            'in_combat_seen': 'in_combat' in states,
            'continue_attack_seen': 'continue_attack' in decisions,
            'revived_after_confirmation': any(
                timeline[i]['state'] == 'in_combat'
                and any(j < i and timeline[j]['state']
                        == 'disengaged_confirmed'
                        for j in range(i))
                for i in range(len(timeline))),
            'first_in_combat_index': (in_combat_indices[0]
                                      if in_combat_indices else None),
            'first_disengaged_confirmed_index': (confirmed_indices[0]
                                                 if confirmed_indices
                                                 else None),
            'disengaged_confirmed_before_combat': bool(
                confirmed_indices and in_combat_indices
                and confirmed_indices[0] < in_combat_indices[0]),
            'dead_observed': 'dead' in states,
            'ghost_observed': 'ghost' in states,
            'target_out_of_range_confirmed_seen': any(
                'target_out_of_range_confirmed' in entry['reasons']
                for entry in timeline),
            'min_self_health_observed': min(
                (e['observations']['self_health']
                 for e in timeline
                 if e['observations'].get('health_transmitted')
                 and e['observations'].get('self_health') is not None),
                default=None)}}


def assert_combat_tracked(report):
    """D-requirements as code; returns the list of violated check names."""
    summary = report['summary']
    failures = []
    if not summary['in_combat_seen']:
        failures.append('timeline must contain in_combat')
    if not summary['continue_attack_seen']:
        failures.append('timeline must contain continue_attack')
    if summary['disengaged_confirmed_before_combat']:
        failures.append('disengaged_confirmed appeared before combat began')
    if summary['dead_observed'] or summary['ghost_observed']:
        failures.append('dead/ghost must not appear (HP stays >= 95)')
    if summary['target_out_of_range_confirmed_seen']:
        failures.append('target_out_of_range_confirmed must not appear '
                        '(self position is never broadcast in combat)')
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path,
                        help='JSON decision-timeline output path; must not '
                             'live under results/ (caller-chosen)')
    parser.add_argument('--results-dir', type=Path,
                        default=ROOT / 'results/pure_network',
                        help='directory holding the archived capture '
                             '(read-only)')
    parser.add_argument('--trace', default=DEFAULT_TRACE,
                        help='capture file name inside --results-dir')
    parser.add_argument('--target-guid', default=LONG01_TARGET,
                        help='target creature GUID to track')
    parser.add_argument('--low-health-ratio', type=float, default=0.5)
    parser.add_argument('--critical-health-ratio', type=float, default=0.25)
    parser.add_argument('--out-of-combat-confirmations', type=int, default=3)
    parser.add_argument('--min-disengage-seconds', type=float, default=2.0)
    parser.add_argument('--max-range', type=float, default=5.0)
    args = parser.parse_args(argv)
    resolved_output = args.output.resolve()
    resolved_results = args.results_dir.resolve()
    if resolved_results in resolved_output.parents:
        raise SystemExit('output %s must not live under results/'
                         % resolved_output)
    config = CombatSMConfig(
        low_health_ratio=args.low_health_ratio,
        critical_health_ratio=args.critical_health_ratio,
        out_of_combat_confirmations=args.out_of_combat_confirmations,
        min_disengage_seconds=args.min_disengage_seconds,
        max_range=args.max_range)
    report = replay_combat_trace(args.results_dir / args.trace,
                                 config=config,
                                 target_guid=args.target_guid)
    violations = assert_combat_tracked(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema': 1, 'report': report,
                                       'violations': violations},
                                      ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({'output': str(args.output),
                      'trace': report['trace'],
                      'packets': report['packets'],
                      'samples': report['samples'],
                      'final_state': report['final_state'],
                      'violations': violations,
                      'summary': report['summary']},
                     ensure_ascii=False, indent=2))
    return 1 if violations else 0


if __name__ == '__main__':
    raise SystemExit(main())
