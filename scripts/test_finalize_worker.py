import unittest
from unittest.mock import MagicMock

from agent_wow_finalize import finalize


def make_backend(observation, logout_result=None, logout_exc=None):
    backend = MagicMock()
    obs = MagicMock()
    obs.to_dict.return_value = dict(observation)
    backend.observe.return_value = obs
    if logout_exc is not None:
        backend._do_logout.side_effect = logout_exc
    else:
        backend._do_logout.return_value = logout_result or {'status': 'closed'}
    return backend


class FakeClock:
    def __init__(self, start=0.0, step=0.25):
        self.now = start
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


class TestFinalize(unittest.TestCase):
    def assert_no_offensive_rpc(self, backend):
        rpc_calls = [c.args[0] for c in backend.rpc.call_args_list]
        self.assertEqual(rpc_calls, ['bridge.stop_attack'])

    def test_combat_unresolved_exhausts_budget_without_logout(self):
        backend = make_backend(
            {'ghost': False, 'health': 100, 'in_combat': True}
        )
        result = finalize(
            backend, budget=1, sleep=lambda s: None, clock=FakeClock(step=0.25)
        )
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result.get('reason'), 'combat unresolved within budget')
        self.assertNotIn('logout', result)
        backend._do_logout.assert_not_called()
        self.assert_no_offensive_rpc(backend)

    def test_logout_error_prevents_closed_status(self):
        backend = make_backend(
            {'ghost': False, 'health': 100, 'in_combat': False},
            logout_result={'status': 'error'},
        )
        result = finalize(
            backend, budget=15, sleep=lambda s: None, clock=FakeClock()
        )
        self.assertEqual(result['status'], 'unresolved')
        self.assertNotEqual(result['status'], 'closed_after_observed_safety')
        self.assertEqual(result['logout'], {'status': 'error'})
        self.assert_no_offensive_rpc(backend)

    def test_death_or_ghost_never_logs_out(self):
        cases = [
            ('health_zero', {'ghost': False, 'health': 0, 'in_combat': True}),
            ('ghost', {'ghost': True, 'health': 100, 'in_combat': True}),
        ]
        for name, observation in cases:
            with self.subTest(case=name):
                backend = make_backend(observation)
                result = finalize(
                    backend,
                    budget=15,
                    sleep=lambda s: None,
                    clock=FakeClock(),
                )
                self.assertEqual(result['status'], 'unresolved')
                self.assertNotIn('logout', result)
                self.assertIn('separate recovery required', result.get('reason', ''))
                backend._do_logout.assert_not_called()
                self.assert_no_offensive_rpc(backend)


if __name__ == '__main__':
    unittest.main()
