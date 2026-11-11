"""Exercise drain itself: cached safe flags never survive transport failure."""
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

if not shutil.which('ip'):
    raise unittest.SkipTest('session import requires WSL ip route')

from agent_wow_session import PureNetworkBackend


class SessionTransportFailureTests(unittest.TestCase):
    def test_read_connection_evidence_pins_identity_and_rejects_replay(self):
        backend = self.backend()
        backend.self_guid = '9'
        data = {'schema_version': 1, 'session_id': 's1', 'character_guid': '9',
                'status': 'in_world', 'sampled_at_ms': 1000,
                'last_valid_pong_ms': 900, 'valid_for_ms': 90000, 'valid_pong': True}
        backend.rpc.return_value = {'result': data}
        first = backend.read_connection_evidence()
        self.assertTrue(first['valid_pong'])
        self.assertEqual(backend.core_session_identity, 's1')
        replayed = backend.read_connection_evidence()
        self.assertIsNone(replayed['valid_pong'])
        data['session_id'] = 's2'
        data['sampled_at_ms'] = 2000
        changed = backend.read_connection_evidence()
        self.assertEqual(changed['reason'], 'session_changed')
        self.assertEqual(backend.core_session_identity, 's1')

    def backend(self, exit_code=None):
        backend = object.__new__(PureNetworkBackend)
        process = Mock()
        process.poll.return_value = exit_code
        backend.session = SimpleNamespace(process=process)
        backend.cursor = 0
        backend.state = {'self': {'health': 100, 'in_combat': False}}
        backend.rpc = Mock()
        return backend

    def test_process_exit_rejects_cached_false_before_polling(self):
        backend = self.backend(0)
        with self.assertRaisesRegex(RuntimeError, 'cached world state is unusable'):
            backend.drain()
        backend.rpc.assert_not_called()

    def test_rpc_error_rejects_cached_false_without_fallback(self):
        backend = self.backend()
        backend.rpc.return_value = {'error': {'message': 'session closed'}}
        with self.assertRaisesRegex(RuntimeError, 'bridge poll failed'):
            backend.drain()

    def test_http_disconnect_rejects_cached_false(self):
        backend = self.backend()
        backend.rpc.side_effect = ConnectionResetError('RPC stopped')
        with self.assertRaises(ConnectionResetError):
            backend.drain()


if __name__ == '__main__':
    unittest.main()
