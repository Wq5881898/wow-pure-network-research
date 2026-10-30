"""Contract tests for single-chunk retreat, not a claim of live safety."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent_wow_retreat import execute


CHUNK = [[[0, 0, 0], [2, 0, 0]]]


def sample(hp=108, combat=True, position=None):
    return {'character': '9', 'map_id': 1, 'health': hp, 'ghost': False,
            'in_combat': combat, 'position': position or [0, 0, 0],
            'context': {'position_evidence': {'current_position_confirmed': True}}}


def backend(states, reply=None):
    client = Mock()
    client.observe.side_effect = [Mock(to_dict=Mock(return_value=s)) for s in states]
    client.rpc.side_effect = lambda method, params: (
        {'result': {'submittedPosition': [2, 0, 0]}} if method == 'bridge.follow_path' and reply is None else
        reply if method == 'bridge.follow_path' else {'result': {}})
    return client


class RetreatControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name) / 'attempt.json'

    def test_submitted_not_confirmed_and_no_repeat(self):
        client = backend([sample(), sample(), sample(hp=102)])
        result = execute(client, CHUNK, self.journal)
        self.assertEqual(result['status'], 'combat_unresolved')
        self.assertEqual(result['chunks'][0]['status'], 'submitted_unconfirmed')
        self.assertEqual([c.args[0] for c in client.rpc.call_args_list],
                         ['bridge.stop_attack', 'bridge.follow_path'])
        with self.assertRaises(FileExistsError):
            execute(client, CHUNK, self.journal)

    def test_ambiguous_reply_is_not_resent(self):
        client = backend([sample(), sample()], reply={'error': {'message': 'unknown outcome'}})
        result = execute(client, CHUNK, self.journal)
        self.assertEqual(result['status'], 'movement_unknown')
        self.assertEqual(result['chunks'][0]['status'], 'unknown')
        self.assertEqual(len(result['chunks']), 1)

    def test_cached_login_position_does_not_authorize_retreat(self):
        stale = sample()
        stale['context']['position_evidence']['current_position_confirmed'] = False
        client = backend([stale])
        self.assertEqual(execute(client, CHUNK, self.journal)['status'], 'precondition_failed')
        client.rpc.assert_not_called()

    def test_preflight_rejects_wrong_location_and_multi_chunk(self):
        client = backend([sample(position=[4, 0, 0])])
        self.assertEqual(execute(client, CHUNK, self.journal)['status'], 'precondition_failed')
        self.assertEqual(client.rpc.call_count, 0)
        with self.assertRaises(ValueError):
            execute(client, CHUNK * 2, self.journal)


if __name__ == '__main__':
    unittest.main()
