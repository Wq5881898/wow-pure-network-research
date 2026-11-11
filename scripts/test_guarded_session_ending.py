"""Real runner boundaries: failed recovery must never become forced close."""
import unittest
from unittest.mock import Mock, patch

from agent_wow_exit_recovery import finish_session
from agent_wow_travel_run import run_travel
from test_exit_recovery import Clock, Observation, sleeper


class EndingTests(unittest.TestCase):
    def test_owner_continues_after_window_expiry_until_logout_is_accepted(self):
        backend = Mock()
        backend.observe.return_value = Observation(71, in_combat=None)
        checkpoint = Mock()
        with patch('agent_wow_exit_recovery.close_session', side_effect=[
                {'status': 'combat_state_unknown'},
                {'logout': {'status': 'closed'}}]):
            result = finish_session(backend, report={}, budget_seconds=3,
                                    hold_ownership=True, checkpoint=checkpoint,
                                    clock=Clock(), sleep=sleeper)
        self.assertTrue(result['transport_closed'])
        self.assertGreaterEqual(result['windows_expired'], 1)
        self.assertEqual(result['attempt_count'], 2)
        backend.close.assert_called_once_with()
        self.assertGreaterEqual(checkpoint.call_count, 2)

    def test_observation_error_does_not_drop_ownership(self):
        backend = Mock()
        backend.session.process.poll.return_value = None
        with patch('agent_wow_exit_recovery.close_session', side_effect=[
                RuntimeError('lost observation'), {'logout': {'status': 'closed'}}]):
            result = finish_session(backend, report={}, budget_seconds=3,
                                    hold_ownership=True, clock=Clock(), sleep=sleeper)
        self.assertTrue(result['transport_closed'])
        self.assertIn('lost observation', result['attempts'][0]['error'])
        backend.close.assert_called_once_with()

    def test_unresolved_budget_never_closes_transport(self):
        backend = Mock()
        backend.observe.return_value = Observation(71, in_combat=None)
        with patch('agent_wow_exit_recovery.close_session',
                   return_value={'status': 'combat_state_unknown'}):
            result = finish_session(backend, report={}, budget_seconds=5,
                                    clock=Clock(), sleep=sleeper)
        self.assertFalse(result['transport_closed'])
        self.assertEqual(result['status'], 'requires_supervision')
        backend.close.assert_not_called()

    def test_accepted_logout_allows_guarded_close(self):
        backend = Mock()
        with patch('agent_wow_exit_recovery.close_session',
                   return_value={'logout': {'status': 'closed'}}):
            result = finish_session(backend, report={}, clock=Clock(), sleep=sleeper)
        self.assertTrue(result['transport_closed'])
        backend.close.assert_called_once_with()

    def test_logout_rejection_never_counts_as_success(self):
        backend = Mock()
        backend.observe.return_value = Observation(71, in_combat=False)
        with patch('agent_wow_exit_recovery.close_session',
                   return_value={'logout': {'error': 'rejected'}}):
            result = finish_session(backend, report={}, budget_seconds=5,
                                    clock=Clock(), sleep=sleeper)
        self.assertFalse(result['transport_closed'])
        backend.close.assert_not_called()


class TravelRunnerTests(unittest.TestCase):
    def backend(self):
        backend = Mock()
        backend.drain.return_value = {'self': {'health': 71, 'ghost': False,
                                              'in_combat': False}}
        return backend

    def run_case(self, backend, finish, **kwargs):
        report = {'status': 'unknown'}
        run_travel(backend, report, goal=[1, 2, 3], legs=1, radius=10,
                   stop_distance=2, sleep=sleeper, finish=finish, **kwargs)
        backend.close.assert_not_called()
        return report

    def test_exception_still_enters_guarded_ending(self):
        backend = self.backend()
        finish = Mock(return_value={'transport_closed': False})
        with patch('agent_wow_travel_run.travel', side_effect=RuntimeError('path rejected')):
            report = self.run_case(backend, finish)
        finish.assert_called_once()
        self.assertEqual(report['status'], 'REQUIRES_SUPERVISION')
        self.assertIn('path rejected', report['error'])

    def test_not_reaching_goal_cannot_pass_after_safe_logout(self):
        finish = Mock(return_value={'transport_closed': True, 'closed_after': 'logout'})
        with patch('agent_wow_travel_run.travel', return_value={'reached_goal_area': False}):
            report = self.run_case(self.backend(), finish)
        self.assertEqual(report['status'], 'INCOMPLETE')

    def test_reaching_goal_then_dying_cannot_pass(self):
        finish = Mock(return_value={'transport_closed': True, 'closed_after': 'death_or_ghost'})
        with patch('agent_wow_travel_run.travel', return_value={'reached_goal_area': True}):
            report = self.run_case(self.backend(), finish)
        self.assertEqual(report['status'], 'INCOMPLETE')

    def test_goal_and_accepted_logout_can_pass(self):
        finish = Mock(return_value={'transport_closed': True, 'closed_after': 'logout'})
        with patch('agent_wow_travel_run.travel', return_value={'reached_goal_area': True}):
            report = self.run_case(self.backend(), finish)
        self.assertEqual(report['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
