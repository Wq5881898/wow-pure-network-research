"""Exercise the actual backend observation shape, without a network session."""
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import Mock

from agent_wow_retreat import execute


class RetreatBackendContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ip'), 'actual backend bootstrap requires WSL ip route')
    def test_real_observation_denies_movement_even_during_combat(self):
        from agent_wow_session import PureNetworkBackend  # WSL-only gateway module
        backend = PureNetworkBackend('offline_retreat_shape')
        backend.session_id = 'offline'
        backend.state = {'self': {
            'guid': '9', 'movement': {'position_xyzo': [0., 0., 0., 0.]},
            'last_seq': 3, 'health': 108, 'max_health': 108, 'ghost': False,
            'in_combat': True, 'level': 3, 'quest_log': []},
            'login': {'map': 1, 'position_xyzo': [0., 0., 0., 0.]},
            'visible_objects': [], 'event_log': []}
        backend.drain = Mock(return_value=backend.state)
        backend.rpc = Mock()
        with tempfile.TemporaryDirectory() as folder:
            report = execute(backend, [[[0, 0, 0], [2, 0, 0]]],
                             Path(folder) / 'attempt.json')
        self.assertEqual(report['status'], 'precondition_failed')
        self.assertIs(report['initial']['context']['position_evidence']['current_position_confirmed'], False)
        backend.rpc.assert_not_called()


if __name__ == '__main__':
    unittest.main()
