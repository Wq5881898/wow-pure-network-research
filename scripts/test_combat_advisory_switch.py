"""The advisory switch must only add evidence; it must never change dispatch.

Default (advisory off) is the shipped behavior: identical outcome strings,
identical bridge call sequence, identical start_sent_once, and no advisory
keys anywhere in the evidence.

The backend import needs the WSL gateway module, so the backend-shape cases
are skipped where `ip route` is unavailable (Windows). The fight/settle
equivalence cases run everywhere.
"""
import shutil
import unittest
from unittest.mock import patch

from agent_wow_combat_control import fight
from agent_wow_session_supervisor import settle


def state(hp=108, combat=True, thp=69, x=2.0):
    return {'self': {'ghost': False, 'health': hp, 'in_combat': combat,
                     'health_transmitted': True, 'max_health': 108},
            'visible_objects': [{'guid': 'enemy', 'health': thp, 'level': 3,
                                 'movement': {'position_xyzo': [x, 0, 0, 0]}}]}


class AdvisorySwitchTests(unittest.TestCase):
    def test_fight_default_path_is_unchanged(self):
        calls = []

        def rpc(method, params):
            calls.append(method)
            return {'result': {}}

        # The sample source cycles instead of running out: how many samples
        # the fight consumes depends on how it ends, and the point of this
        # test is that the advisory switch changes nothing at all.
        def samples():
            cycle = [state(), state(8.0), state(8.0)]
            while True:
                for item in cycle:
                    yield item

        with patch('agent_wow_combat_control.time.sleep'):
            default, _ = fight(rpc, unittest.mock.Mock(side_effect=samples()),
                               'enemy', [0, 0, 0], lambda: 0, budget=1)
        default_calls = list(calls)

        calls.clear()
        with patch('agent_wow_combat_control.time.sleep'):
            advised, _ = fight(rpc, unittest.mock.Mock(side_effect=samples()),
                               'enemy', [0, 0, 0], lambda: 0, budget=1, advisory=True)

        self.assertEqual(default['outcome'], advised['outcome'])
        self.assertEqual(default['start_sent_once'], advised['start_sent_once'])
        self.assertEqual(default_calls, calls)
        self.assertNotIn('advisory_final_state', default)
        self.assertTrue(all('advisory_state' not in row for row in default['observations']))
        self.assertIn('advisory_final_state', advised)
        self.assertTrue(all('advisory_state' in row for row in advised['observations']))

    def test_settle_default_path_is_unchanged(self):
        calls = []

        def rpc(method, params):
            calls.append(method)
            return {'result': {}}

        def drain():
            return state(combat=True)

        default, _ = settle(rpc, drain, [0, 0, 0], lambda: 0, budget=0.01)
        default_calls = list(calls)

        calls.clear()
        advised, _ = settle(rpc, drain, [0, 0, 0], lambda: 0, budget=0.01, advisory=True)

        self.assertEqual(default_calls, calls)
        self.assertNotIn('advisory_final_state', default)
        self.assertIn('advisory_final_state', advised)


class AdvisoryBackendFlagTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ip'), 'backend bootstrap requires WSL ip route')
    def test_backend_defaults_to_advisory_off(self):
        from agent_wow_session import PureNetworkBackend  # WSL-only gateway module
        self.assertFalse(PureNetworkBackend('offline_default').advisory)
        self.assertTrue(PureNetworkBackend('offline_on', advisory=True).advisory)


if __name__ == '__main__':
    unittest.main()
