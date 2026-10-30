"""Test the actual loot backend with synthetic packet events, without RPC I/O."""
import shutil
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(shutil.which('ip'), 'backend bootstrap requires WSL')
class LootBackendEvidenceTests(unittest.TestCase):
    def backend(self, events, take_reply=None):
        from agent_wow_session import PureNetworkBackend
        backend = PureNetworkBackend.__new__(PureNetworkBackend)
        backend.target = {'guid': '123'}
        backend.self_guid = '9'
        backend.cursor = 10
        backend.state = {'visible_objects': []}
        backend.rpc_calls = []

        def rpc(method, params):
            backend.rpc_calls.append(method)
            if method == 'bridge.take_loot':
                if isinstance(take_reply, Exception):
                    raise take_reply
                return take_reply if take_reply is not None else {'result': {}}
            return {'result': {}}

        backend.rpc = Mock(side_effect=rpc)
        loot = {'sequence': 11, 'opcode': 0x160,
                'loot': {'guid': '123', 'items': [{'slot': 0, 'entry': 4862}]}}
        def drain():
            if backend.rpc_calls.count('bridge.take_loot'):
                backend.cursor = 14
                return {'event_log': [loot, *events]}
            backend.cursor = 11
            return {'event_log': [loot]}
        backend.drain = Mock(side_effect=drain)
        return backend

    @patch('agent_wow_session.time.sleep', return_value=None)
    def test_attempt_only_is_not_a_server_push(self, _sleep):
        backend = self.backend([])
        result = backend._do_loot({}, 30)
        self.assertEqual(result['attempted_take_entries'], [4862])
        self.assertEqual(result['confirmed_item_pushes'], [])

    @patch('agent_wow_session.time.sleep', return_value=None)
    def test_fresh_matching_push_is_separate_evidence(self, _sleep):
        pushes = [
            {'sequence': 9, 'opcode': 0x166, 'loot': {
                'player_guid': '9', 'entry': 4862, 'received': 0, 'created': 0,
                'count': 1, 'inventory_count': 1}},
            {'sequence': 13, 'opcode': 0x166, 'loot': {
                'player_guid': '9', 'entry': 7101, 'received': 0, 'created': 0,
                'count': 1, 'inventory_count': 1}},
            {'sequence': 14, 'opcode': 0x166, 'loot': {
                'player_guid': '9', 'entry': 4862, 'received': 0, 'created': 0,
                'count': 1, 'inventory_count': 1}},
        ]
        result = self.backend(pushes)._do_loot({}, 30)
        self.assertEqual([p['sequence'] for p in result['confirmed_item_pushes']], [14])

    @patch('agent_wow_session.time.sleep', return_value=None)
    def test_same_inventory_count_not_taken_as_new_receipt(self, _sleep):
        backend = self.backend([{'sequence': 14, 'opcode': 0x166,
                                'loot': {'player_guid': '9', 'entry': 4862,
                                         'received': 0, 'created': 0,
                                         'count': 1, 'inventory_count': 7}}])
        backend.state['visible_objects'] = [
            {'type_id': 1, 'owner_guid': '9', 'entry': 4862, 'stack_count': 7}]
        result = backend._do_loot({}, 30)
        self.assertEqual(result['confirmed_item_pushes'], [])

    @patch('agent_wow_session.time.sleep', return_value=None)
    def test_response_racing_open_rpc_is_not_discarded(self, _sleep):
        backend = self.backend([])
        def rpc(method, params):
            backend.rpc_calls.append(method)
            if method == 'bridge.open_loot':
                backend.cursor = 11  # a packet arrived before RPC returned
            return {'result': {}}
        backend.rpc.side_effect = rpc
        result = backend._do_loot({}, 30)
        self.assertEqual(result['loot']['guid'], '123')
        self.assertEqual(result['attempted_take_entries'], [4862])

    @patch('agent_wow_session.time.sleep', return_value=None)
    def test_ambiguous_take_does_not_release_or_continue(self, _sleep):
        backend = self.backend([], take_reply=TimeoutError('reply lost'))
        result = backend._do_loot({}, 30)
        self.assertIn('TimeoutError', result['take_error'])
        self.assertEqual(result['confirmed_item_pushes'], [])
        self.assertNotIn('bridge.release_loot', [call.args[0] for call in backend.rpc.call_args_list])

    @patch('agent_wow_session.time.sleep', return_value=None)
    def test_release_failure_does_not_confirm_success(self, _sleep):
        backend = self.backend([])
        original = backend.rpc.side_effect
        def rpc(method, params):
            if method == 'bridge.release_loot':
                return {'error': {'message': 'reply rejected'}}
            return original(method, params)
        backend.rpc.side_effect = rpc
        result = backend._do_loot({}, 30)
        self.assertIn('error', result['released'])
        self.assertTrue(result['side_effect_applied'])


if __name__ == '__main__':
    unittest.main()
