"""Negative local transport evidence, never a claim of world connectivity."""
import base64
import struct
import math
import time


def validate_core_evidence(reply, character_guid):
    """Validate one optional RPC snapshot; never compare remote/local clocks."""
    unknown = {'schema_version': 1, 'valid_pong': None, 'world_connected': None}
    if not isinstance(reply, dict):
        return {**unknown, 'reason': 'invalid_rpc_envelope'}
    if 'error' in reply:
        error = reply['error']
        code = error.get('code') if isinstance(error, dict) else None
        return {**unknown, 'reason': 'unsupported' if code == -32601 else 'rpc_error'}
    data = reply.get('result')
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        return {**unknown, 'reason': 'unsupported_or_invalid_schema'}
    if data.get('character_guid') != str(character_guid):
        return {**unknown, 'reason': 'character_mismatch'}
    status = data.get('status')
    if status not in ('entering_world', 'in_world', 'logging_out', 'closed', 'failed'):
        return {**unknown, 'reason': 'invalid_status'}
    if status in ('closed', 'failed'):
        return {**unknown, 'valid_pong': False, 'world_connected': False,
                'reason': 'session_ended', 'status': status}
    sampled, last, valid_for = (data.get(key) for key in (
        'sampled_at_ms', 'last_valid_pong_ms', 'valid_for_ms'))
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    if not number(sampled) or not number(valid_for):
        return {**unknown, 'reason': 'invalid_timing'}
    flag = data.get('valid_pong')
    if type(flag) is not bool:
        return {**unknown, 'reason': 'invalid_validity_flag'}
    if last is None and flag is False:
        return {**unknown, 'valid_pong': False, 'status': status,
                'reason': 'no_valid_pong_observed'}
    if not number(last) or sampled < last:
        return {**unknown, 'reason': 'invalid_timing'}
    age = sampled - last
    if flag and (age >= valid_for or status not in ('in_world', 'logging_out')):
        return {**unknown, 'reason': 'inconsistent_validity'}
    return {**unknown, 'valid_pong': flag, 'status': status,
            'age_at_remote_sample_ms': age, 'valid_for_ms': valid_for,
            'reason': 'recent_matched_pong' if flag else 'expired_or_invalid_pong',
            'scope': 'snapshot at remote sample; not current connectivity or combat safety'}


def query_core_evidence(rpc, character_guid, *, expected_session=None,
                        previous_sample_ms=None,
                        clock=time.monotonic, max_round_trip_seconds=2.0):
    if (type(max_round_trip_seconds) not in (int, float)
            or not math.isfinite(max_round_trip_seconds) or max_round_trip_seconds <= 0):
        raise ValueError('positive finite round-trip limit required')
    started = clock()
    try:
        reply = rpc('session.connectionEvidenceV1', {})
    except Exception as exc:
        return {'schema_version': 1, 'valid_pong': None, 'world_connected': None,
                'reason': 'query_failed', 'error_type': type(exc).__name__}
    received = clock()
    elapsed = received - started
    result = validate_core_evidence(reply, character_guid)
    result.update(request_started_monotonic=started,
                  received_at_monotonic=received, round_trip_seconds=elapsed)
    data = reply.get('result') if isinstance(reply, dict) else None
    if not isinstance(data, dict):
        return result
    session_id = data.get('session_id') if isinstance(data, dict) else None
    reason = None
    if not isinstance(session_id, str) or not session_id:
        reason = 'session_identity_unavailable'
    elif expected_session is not None and session_id != expected_session:
        reason = 'session_changed'
    elif not math.isfinite(elapsed) or not 0 <= elapsed <= max_round_trip_seconds:
        reason = 'round_trip_too_old_or_invalid'
    elif (previous_sample_ms is not None and result.get('status') is not None
          and isinstance(data.get('sampled_at_ms'), (int, float))
          and data['sampled_at_ms'] <= previous_sample_ms):
        reason = 'snapshot_replayed_or_time_regressed'
    elif result.get('valid_pong') is True:
        remaining = result['valid_for_ms'] - result['age_at_remote_sample_ms'] - elapsed * 1000
        if remaining <= 0:
            reason = 'pong_expired_in_transit'
        else:
            result['use_before_monotonic'] = received + min(
                remaining / 1000, max_round_trip_seconds)
    if reason is not None:
        result.update(valid_pong=None, world_connected=None, reason=reason)
    result['session_id'] = session_id
    if result.get('status') is not None:
        result['remote_sample_ms'] = data.get('sampled_at_ms')
    return result


def usable_core_snapshot(evidence, expected_session, now):
    """Recent matched-Pong evidence only; never grants combat/action safety."""
    if not isinstance(evidence, dict) or not expected_session:
        return False
    received = evidence.get('received_at_monotonic')
    deadline = evidence.get('use_before_monotonic')
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in (now, received, deadline)):
        return False
    return (evidence.get('schema_version') == 1
            and evidence.get('session_id') == expected_session
            and evidence.get('valid_pong') is True
            and evidence.get('reason') == 'recent_matched_pong'
            and received <= now < deadline)


class ConnectionEvidence:
    """Track received packet metadata without claiming core Ping validation."""

    def __init__(self):
        self.session_id = None
        self.last_sequence = 0
        self.last_packet = None
        self.last_pong = None

    def update(self, session_id, packets):
        if self.session_id is not None and session_id != self.session_id:
            raise ValueError('connection evidence session changed')
        self.session_id = session_id
        for packet in packets:
            sequence = packet['sequence']
            if sequence != self.last_sequence + 1:
                raise ValueError('connection evidence packet sequence gap')
            self.last_sequence = sequence
            self.last_packet = {'sequence': sequence,
                                'packet_ms': packet.get('monotonic_ms')}
            if packet['opcode'] == 0x1DD:
                body = base64.b64decode(packet['payload_base64'], validate=True)
                if len(body) != 4:
                    raise ValueError('invalid Pong payload length')
                self.last_pong = {**self.last_packet,
                                  'ping_id': struct.unpack('<I', body)[0]}

    def view(self):
        return {'schema_version': 1, 'session_id': self.session_id,
                'last_received_packet': dict(self.last_packet) if self.last_packet else None,
                'last_received_pong': dict(self.last_pong) if self.last_pong else None,
                'valid_pong': None, 'world_connected': None,
                'scope': 'received payload only; core Ping matching and expiry not exposed'}


def session_process_evidence(session):
    process = getattr(session, 'process', None)
    if process is None:
        return {'state': 'unavailable', 'world_connected': None}
    exit_code = process.poll()
    if exit_code is not None:
        raise RuntimeError('session process exited (code=%s); cached world state is unusable'
                           % exit_code)
    return {'state': 'running', 'world_connected': None,
            'scope': 'local process only; not evidence of a live world connection'}
