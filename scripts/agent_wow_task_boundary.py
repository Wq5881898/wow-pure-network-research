"""Validate an archived movement boundary before opening a verification session."""
import math
import time

from agent_wow_position import xyz
from agent_wow_combat_control import estimated_position


def verify_target(state, guid, entry, max_level, now_ms, position):
    me = state['self']
    if me['ghost'] or me['health'] < 60 or me['in_combat'] is not False:
        raise ValueError('character not ready for a new fight')
    targets = [obj for obj in state['visible_objects'] if obj.get('guid') == guid]
    if len(targets) != 1:
        raise ValueError('original target not uniquely visible')
    target = targets[0]
    if (target.get('type_id') != 3 or target.get('entry') != entry
            or target.get('health', 0) <= 0 or not 1 <= target.get('level', 0) <= max_level):
        raise ValueError('original target no longer suitable')
    target_position, source = estimated_position(target, now_ms)
    if source.startswith('unsupported'):
        raise ValueError('unsupported target movement')
    distance = math.dist(xyz(position), xyz(target_position))
    if distance > 4:
        raise ValueError(f'original target outside conservative attack range: {distance:.3f}')
    return target, {'guid': guid, 'entry': entry, 'distance_estimate': distance,
                    'position_source': source, 'scope': 'range estimate; LOS not confirmed'}


def movement_boundary(report, max_age_seconds=None, now=None):
    records = report['run']['records']
    approach_index = max(i for i, record in enumerate(records) if record['action_id'] == 'approach')
    approach = records[approach_index]
    if any(record.get('action_id') in ('fight', 'loot') for record in records[approach_index + 1:]):
        raise ValueError('combat followed movement; not a movement-only boundary')
    final = report.get('final_observation', {})
    if not (final.get('health', 0) > 0 and final.get('ghost') is False
            and final.get('in_combat') is False):
        raise ValueError('living out-of-combat boundary required')
    logout = report.get('finalize', {})
    if logout.get('status') != 'closed':
        raise ValueError('confirmed boundary logout required')
    evidence = approach['evidence']
    replies = evidence.get('replies', [])
    if any('error' in reply for reply in replies):
        raise ValueError('failed movement submissions in boundary session')
    if not replies:
        # The target was already within striking distance, so the session never
        # sent a path. Zero movement is not a cross-session landing proof, so
        # the verification session re-approaches from login; require the run to
        # have observed both endpoints (start position and target in range).
        if not evidence.get('reached'):
            raise ValueError('no movement submissions and approach unconfirmed')
        if approach.get('observation_before') is None:
            raise ValueError('in-range boundary lacks a start observation')
        submitted = evidence.get('submitted_position')
        if not isinstance(submitted, list) or len(submitted) < 3:
            raise ValueError('in-range boundary lacks a submitted position')
        expected = xyz(submitted[:3])
    else:
        last = replies[-1].get('result', {})
        if not isinstance(last, dict) or not isinstance(last.get('submittedPosition'), list) or len(last['submittedPosition']) < 3:
            raise ValueError('movement reply lacks submitted position')
        expected = xyz(last['submittedPosition'][:3])
    before = approach['observation_before']
    finished_at = final.get('sampled_at')
    if max_age_seconds is not None:
        if not isinstance(finished_at, (int, float)):
            raise ValueError('boundary lacks a usable completion timestamp')
        age = (now if now is not None else time.time()) - float(finished_at)
        if age > max_age_seconds:
            raise ValueError(f'movement boundary expired: {age:.1f}s > {max_age_seconds}s')
    return {'source_session': before['session_id'], 'character': before['character'],
            'map': before['map_id'], 'expected_xyz': expected,
            'finished_at': finished_at,
            'movement_submitted': bool(replies),
            'approach_confirmed': bool(evidence.get('reached')),
            'target': next(record['evidence']['target'] for record in records
                           if record['action_id'] == 'select_target')}
