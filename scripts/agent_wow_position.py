"""Position evidence must come from a movement update, not a health delta."""
import math


def xyz(value):
    if len(value) != 3 or not all(math.isfinite(v) for v in value):
        raise ValueError('finite XYZ coordinates required')
    return list(value)


def movement_sequence(state, guid):
    return max((event['sequence'] for event in state['event_log']
                if event.get('guid') == guid and 'movement' in event), default=0)


def landing_evidence(state, guid, before_sequence, expected):
    sequence = movement_sequence(state, guid)
    position = state['self']['movement']['position_xyzo'][:3]
    distance = math.dist(xyz(position), xyz(expected))
    return {'confirmed': sequence > before_sequence and distance <= 3,
            'fresh': sequence > before_sequence, 'distance': distance,
            'movement_sequence': sequence, 'before_sequence': before_sequence,
            'last_update_position': position, 'submitted_position': expected,
            'scope': 'movement update required; polling does not refresh position'}


def confirm_relogin(source_session, expected, map_id, trace, tolerance=1, character='9'):
    """Read a new session's login evidence; never issue movement or reconnect."""
    from agent_wow_worldstate import replay

    if not source_session or not trace.get('session_id') or trace['session_id'] == source_session:
        raise ValueError('distinct nonempty source and verification sessions required')
    if not 0 < tolerance <= 3:
        raise ValueError('tolerance must be in (0, 3] yards')
    if trace.get('self_guid') != character:
        raise ValueError('verification character mismatch')
    state = replay(trace)
    if state['errors'] or not state.get('login') or not state.get('self'):
        raise ValueError('valid login and character evidence required')
    login = state['login']
    distance = math.dist(xyz(expected), xyz(login['position_xyzo'][:3]))
    return {'confirmed': login['map'] == map_id and distance <= tolerance,
            'source_session': source_session, 'verification_session': trace['session_id'],
            'character': trace['self_guid'], 'map': login['map'],
            'expected_map': map_id, 'expected_xyz': expected,
            'login_xyz': login['position_xyzo'][:3], 'distance': distance,
            'tolerance': tolerance, 'scope': 'later login boundary; not live movement acknowledgement'}
