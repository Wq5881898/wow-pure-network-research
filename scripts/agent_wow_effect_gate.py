"""Evidence-only gate. Does not dispatch actions or confirm physical arrival."""
import math


def attempt_gate(view, target_distance, health, ghost, in_combat, already_attempted=False):
    reasons = []
    if not view.get('usable_for_estimation'):
        reasons.append('position unavailable or stale')
    if view.get('correction_coverage') != 'verified':
        reasons.append('correction coverage incomplete')
    if not math.isfinite(target_distance) or not 0 <= target_distance <= 5:
        reasons.append('target outside estimated range')
    if ghost is not False or in_combat is not False or health < 50:
        reasons.append('character not ready')
    if already_attempted:
        reasons.append('attempt already dispatched')
    return {'allowed': not reasons, 'reasons': reasons, 'arrival_confirmed': False}


def effect_evidence(state, character, target, after_sequence):
    hits = [e for e in state['event_log']
            if e.get('kind') == 'attacker_state' and e['sequence'] > after_sequence
            and e['attack']['attacker_guid'] == character
            and e['attack']['target_guid'] == target
            and e['attack']['full_damage'] > 0]
    obj = next((o for o in state['visible_objects'] if o['guid'] == target), None)
    dead = bool(obj and obj.get('health') == 0 and obj.get('last_seq', 0) > after_sequence)
    return {'damage_confirmed': bool(hits), 'damage_sequences': [e['sequence'] for e in hits],
            'target_dead_observed': dead,
            'kill_with_damage_evidence': bool(hits) and dead,
            'arrival_confirmed': False,
            'scope': 'server damage/death evidence; not position or exclusive kill attribution'}
