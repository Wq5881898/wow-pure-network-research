import unittest
from unittest.mock import Mock
from agent_wow_finalize import finalize


class FinalizeTests(unittest.TestCase):
    def backend(self, observations):
        backend = Mock()
        backend.observe.side_effect = [Mock(to_dict=Mock(return_value=o)) for o in observations]
        backend._do_logout.return_value = {'status': 'closed'}
        return backend

    def obs(self, hp=108, combat=False):
        return {'health': hp, 'ghost': False, 'in_combat': combat}

    def test_damage_does_not_wait_or_logout_or_attack(self):
        b = self.backend([self.obs(97, True), self.obs(94, True)])
        result = finalize(b, sleep=lambda _: None)
        self.assertEqual(result['status'], 'unresolved')
        b._do_logout.assert_not_called()
        self.assertEqual([c.args[0] for c in b.rpc.call_args_list], ['bridge.stop_attack'])

    def test_safety_must_be_observed(self):
        b = self.backend([self.obs(97, True), self.obs(97, False)])
        self.assertEqual(finalize(b, sleep=lambda _: None)['status'], 'closed_after_observed_safety')

    def test_decode_failure_remains_unresolved(self):
        b = Mock()
        b.observe.side_effect = RuntimeError('bad packet')
        self.assertEqual(finalize(b)['status'], 'unresolved')
        b._do_logout.assert_not_called()
