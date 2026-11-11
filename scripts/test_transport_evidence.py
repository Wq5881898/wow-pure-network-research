"""A responsive bridge must not conceal an already exited session owner."""
import unittest
import base64
import struct
from types import SimpleNamespace
from unittest.mock import Mock

from agent_wow_transport_evidence import (ConnectionEvidence, session_process_evidence,
                                         validate_core_evidence, query_core_evidence,
                                         usable_core_snapshot)


class CoreEvidenceTests(unittest.TestCase):
    def test_snapshot_reuse_checks_deadline_identity_and_local_clock(self):
        reply = self.reply()
        reply['result']['session_id'] = 's1'
        result = query_core_evidence(Mock(return_value=reply), '9',
                                     clock=Mock(side_effect=(10, 10.1)))
        self.assertTrue(usable_core_snapshot(result, 's1', 10.2))
        self.assertFalse(usable_core_snapshot(result, 's2', 10.2))
        self.assertFalse(usable_core_snapshot(result, 's1', 10.0))
        self.assertFalse(usable_core_snapshot(result, 's1', result['use_before_monotonic']))
        self.assertFalse(usable_core_snapshot(result, 's1', float('nan')))

    def test_replayed_remote_sample_cannot_receive_new_local_deadline(self):
        reply = self.reply()
        reply['result']['session_id'] = 's1'
        result = query_core_evidence(Mock(return_value=reply), '9',
                                     previous_sample_ms=1000,
                                     clock=Mock(side_effect=(10, 10.1)))
        self.assertEqual(result['reason'], 'snapshot_replayed_or_time_regressed')
        self.assertIsNone(result['valid_pong'])
        self.assertNotIn('use_before_monotonic', result)

    def test_query_rejects_wrong_session_slow_response_and_transit_expiry(self):
        reply = self.reply()
        reply['result']['session_id'] = 'session-a'
        cases = [('session-b', (0, 0.1), 'session_changed'),
                 ('session-a', (0, 3), 'round_trip_too_old_or_invalid')]
        for expected, times, reason in cases:
            result = query_core_evidence(Mock(return_value=reply), '9',
                expected_session=expected, clock=Mock(side_effect=times))
            self.assertIsNone(result['valid_pong'])
            self.assertEqual(result['reason'], reason)
        reply['result']['valid_for_ms'] = 600
        result = query_core_evidence(Mock(return_value=reply), '9',
            clock=Mock(side_effect=(0, 0.2)))
        self.assertEqual(result['reason'], 'pong_expired_in_transit')

    def test_query_has_local_use_deadline_but_no_connectivity_claim(self):
        reply = self.reply()
        reply['result']['session_id'] = 'session-a'
        result = query_core_evidence(Mock(return_value=reply), '9',
            clock=Mock(side_effect=(10, 10.1)))
        self.assertTrue(result['valid_pong'])
        self.assertLessEqual(result['use_before_monotonic'], 12.1)
        self.assertIsNone(result['world_connected'])

    def reply(self, **values):
        data = {'schema_version': 1, 'character_guid': '9', 'status': 'in_world',
                'sampled_at_ms': 1000, 'last_valid_pong_ms': 500,
                'valid_for_ms': 90000, 'valid_pong': True}
        data.update(values)
        return {'result': data}

    def test_valid_snapshot_is_not_current_connectivity(self):
        result = validate_core_evidence(self.reply(), '9')
        self.assertTrue(result['valid_pong'])
        self.assertIsNone(result['world_connected'])
        self.assertEqual(result['age_at_remote_sample_ms'], 500)

    def test_legacy_missing_identity_bad_version_and_expiry_remain_unknown(self):
        replies = [{'error': {'code': -32601}}, self.reply(character_guid='10'),
                   self.reply(schema_version=2), self.reply(last_valid_pong_ms=2000),
                   self.reply(valid_for_ms=100), self.reply(sampled_at_ms=float('nan'))]
        for reply in replies:
            with self.subTest(reply=reply):
                self.assertIsNone(validate_core_evidence(reply, '9')['valid_pong'])

    def test_failed_session_is_negative_evidence(self):
        result = validate_core_evidence(self.reply(status='failed'), '9')
        self.assertFalse(result['world_connected'])
        self.assertFalse(result['valid_pong'])

    def test_optional_query_only_calls_read_interface(self):
        rpc = Mock(return_value={'error': {'code': -32601}})
        self.assertEqual(query_core_evidence(rpc, '9')['reason'], 'unsupported')
        rpc.assert_called_once_with('session.connectionEvidenceV1', {})
        rpc.side_effect = ConnectionError()
        self.assertIsNone(query_core_evidence(rpc, '9')['valid_pong'])


class ConnectionEvidenceTests(unittest.TestCase):
    def test_received_pong_does_not_claim_validated_liveness(self):
        evidence = ConnectionEvidence()
        evidence.update('s1', [{'sequence': 1, 'monotonic_ms': 100,
                              'opcode': 0x1DD, 'payload_base64': base64.b64encode(
                                  struct.pack('<I', 7)).decode()}])
        result = evidence.view()
        self.assertEqual(result['last_received_pong']['ping_id'], 7)
        self.assertIsNone(result['valid_pong'])
        self.assertIsNone(result['world_connected'])
        evidence.update('s1', [])
        self.assertEqual(evidence.view()['last_received_pong']['packet_ms'], 100)

    def test_unrelated_packet_does_not_refresh_pong(self):
        evidence = ConnectionEvidence()
        evidence.update('s1', [{'sequence': 1, 'opcode': 0xA9, 'monotonic_ms': 200}])
        self.assertIsNone(evidence.view()['last_received_pong'])

    def test_session_switch_and_duplicate_sequence_are_rejected(self):
        evidence = ConnectionEvidence()
        evidence.update('s1', [{'sequence': 1, 'opcode': 0xA9}])
        with self.assertRaises(ValueError):
            evidence.update('s2', [])
        with self.assertRaises(ValueError):
            evidence.update('s1', [{'sequence': 1, 'opcode': 0xA9}])


class TransportEvidenceTests(unittest.TestCase):
    def test_exited_process_rejects_cached_state_even_with_zero_exit_code(self):
        for code in (0, 1, -15):
            with self.subTest(code=code):
                session = SimpleNamespace(process=Mock())
                session.process.poll.return_value = code
                with self.assertRaisesRegex(RuntimeError, 'cached world state is unusable'):
                    session_process_evidence(session)

    def test_running_process_does_not_prove_world_connectivity(self):
        session = SimpleNamespace(process=Mock())
        session.process.poll.return_value = None
        evidence = session_process_evidence(session)
        self.assertEqual(evidence['state'], 'running')
        self.assertIsNone(evidence['world_connected'])

    def test_absent_process_handle_is_unknown_not_connected(self):
        evidence = session_process_evidence(None)
        self.assertEqual(evidence['state'], 'unavailable')
        self.assertIsNone(evidence['world_connected'])


if __name__ == '__main__':
    unittest.main()
