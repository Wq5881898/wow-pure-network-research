import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_wow_retreat import execute


CHUNK = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]


def observation(position=(0.0, 0.0, 0.0), health=100, in_combat=True,
                position_confirmed=True):
    obs = MagicMock()
    obs.to_dict.return_value = {
        'backend': 'pure-network', 'session_id': 'aw-test',
        'character': '9',
        'map_id': 1,
        'ghost': False,
        'health': health,
        'in_combat': in_combat,
        'position': list(position),
        'context': {'position_evidence':
                    {'current_position_confirmed': position_confirmed,
                     'source': 'fresh_server_self_movement', 'session_id': 'aw-test',
                     'movement_sequence': 5, 'login_sequence': 3}},
    }
    return obs


class ExecuteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(
            __import__('tempfile').TemporaryDirectory()))

    def journal(self, name='journal.json'):
        return self.tmp / name

    def test_missing_fresh_position_evidence_rejects_without_rpc(self):
        backend = MagicMock()
        backend.observe.return_value = observation(position_confirmed=False)
        journal = self.journal()

        report = execute(backend, [CHUNK], journal)

        self.assertEqual(report['status'], 'precondition_failed')
        backend.rpc.assert_not_called()
        self.assertTrue(journal.exists())
        recorded = json.loads(journal.read_text(encoding='utf-8'))
        self.assertEqual(recorded['status'], 'precondition_failed')
        self.assertEqual(recorded['chunks'], [])

    def test_six_chunks_rejected_before_journal_creation(self):
        backend = MagicMock()
        journal = self.journal()

        with self.assertRaises(ValueError):
            execute(backend, [CHUNK] * 6, journal)

        self.assertFalse(journal.exists())
        backend.observe.assert_not_called()
        backend.rpc.assert_not_called()

    def test_rpc_timeout_records_unknown_and_blocks_resend(self):
        backend = MagicMock()
        backend.observe.return_value = observation()
        backend.rpc.side_effect = [
            {'result': {}},
            TimeoutError('no bridge reply'),
        ]
        journal = self.journal()

        report = execute(backend, [CHUNK], journal)

        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(
            report['error'], 'TimeoutError: no bridge reply')
        self.assertEqual(len(report['chunks']), 1)
        self.assertEqual(report['chunks'][0]['index'], 1)
        self.assertEqual(report['chunks'][0]['status'], 'unknown')
        recorded = json.loads(journal.read_text(encoding='utf-8'))
        self.assertEqual(recorded['status'], 'unknown')
        self.assertEqual(recorded['chunks'][0]['index'], 1)

        with self.assertRaises(FileExistsError):
            execute(backend, [CHUNK], journal)
        self.assertEqual(backend.rpc.call_count, 2)


if __name__ == '__main__':
    unittest.main()
