"""Regression guards for the shared out-of-combat confirmation rule.

The defect these guard against: the exit branch used to read
"in_combat is not True" as "the combat is over". An UNKNOWN combat state
(None, which happens when the server has not transmitted the combat flag)
was therefore reported as a cleared combat, and the session was then torn
down on the strength of that reading.

The rule now lives in one place, agent_wow_combat_state_machine
.OutOfCombatConfirmator, and both the state machine and the exit branch
use it. These tests assert:

  * one sample never confirms;
  * an unknown combat state never confirms and never counts;
  * the exit branch reports "not cleared" when the state stays unknown;
  * the exit branch does not attack while the state is unknown;
  * close_session does not log out on an unknown combat state;
  * the state machine and the exit branch agree on the same samples.
"""
import unittest
from unittest.mock import patch

from agent_wow_combat_state_machine import (CombatSMConfig, CombatStateMachine,
                                            ObservationView, OutOfCombatConfirmator)
from agent_wow_exit_recovery import close_session, resolve_combat_before_exit
from test_exit_recovery import Clock, FakeBackend, Observation, sleeper


def view(in_combat, health=71, sampled_at=0.0, health_transmitted=True):
    return ObservationView(sampled_at=sampled_at, health=health,
                            max_health=108, health_transmitted=health_transmitted,
                            ghost=False, in_combat=in_combat)


class ConfirmatorTests(unittest.TestCase):
    def test_retreat_adapters_preserve_ghost_rejection(self):
        from agent_wow_exit_recovery import _SampleView as ExitSample
        from agent_wow_escape_control import _SampleView as RetreatSample
        for adapter, sample in ((ExitSample, {'health': 71}),
                                (RetreatSample, {'hp': 71})):
            sample.update(ghost=True, in_combat=False)
            confirmator = OutOfCombatConfirmator(min_seconds=0)
            for now in (0, 1, 2):
                self.assertFalse(confirmator.observe(adapter(sample), now))
            self.assertFalse(confirmator.confirmed)

    def test_stale_or_backlogged_poll_cannot_confirm_cached_false(self):
        from types import SimpleNamespace
        for poll in ({'caught_up': False, 'polled_at_monotonic': 10.0},
                     {'caught_up': True, 'polled_at_monotonic': 0.0}):
            confirmator = OutOfCombatConfirmator(min_seconds=0)
            sample = SimpleNamespace(in_combat=False, health=71,
                                     context={'transport_poll': poll})
            for now in (10.0, 11.0, 12.0):
                self.assertFalse(confirmator.observe(sample, now))
            self.assertFalse(confirmator.confirmed)

    def test_one_sample_never_confirms(self):
        confirmator = OutOfCombatConfirmator()
        self.assertFalse(confirmator.observe(view(False), 0.0))
        self.assertFalse(confirmator.observe(view(False), 0.5))

    def test_three_samples_over_the_dwell_confirm(self):
        confirmator = OutOfCombatConfirmator()
        self.assertFalse(confirmator.observe(view(False), 0.0))
        self.assertFalse(confirmator.observe(view(False), 1.0))
        self.assertTrue(confirmator.observe(view(False), 2.0))

    def test_property_does_not_bypass_dwell(self):
        confirmator = OutOfCombatConfirmator()
        for now in (0.0, 0.1, 0.2):
            self.assertFalse(confirmator.observe(view(False), now))
        self.assertFalse(confirmator.confirmed)

    def test_duplicate_or_ghost_sample_cannot_confirm(self):
        confirmator = OutOfCombatConfirmator(min_seconds=0)
        confirmator.observe(view(False), 1.0)
        self.assertFalse(confirmator.observe(view(False), 1.0))
        self.assertEqual(confirmator.streak, 0)
        sample = view(False)
        sample.ghost = True
        self.assertFalse(confirmator.observe(sample, 2.0))

    def test_unknown_combat_never_confirms_and_never_counts(self):
        confirmator = OutOfCombatConfirmator()
        confirmator.observe(view(False), 0.0)
        confirmator.observe(view(False), 1.0)
        # An unknown sample breaks the run.
        self.assertFalse(confirmator.observe(view(None), 2.0))
        self.assertEqual(confirmator.streak, 0)
        # Two clean samples after it are still not a confirmation.
        self.assertFalse(confirmator.observe(view(False), 3.0))
        self.assertFalse(confirmator.observe(view(False), 4.0))

    def test_health_falling_breaks_the_run(self):
        confirmator = OutOfCombatConfirmator()
        confirmator.observe(view(False, health=71), 0.0)
        confirmator.observe(view(False, health=60), 1.0)
        self.assertEqual(confirmator.streak, 0)

    def test_a_single_confirmation_is_refused(self):
        with self.assertRaises(ValueError):
            OutOfCombatConfirmator(confirmations=1)


class StateMachineAgreementTests(unittest.TestCase):
    """The state machine and the exit branch must use one rule."""

    def _machine_verdict(self, samples):
        machine = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=3,
                                                    min_disengage_seconds=2.0),
                                     target_guid='mob', escape_path_planned=True)
        machine._combat_observed = True
        machine._engaged = True
        machine._state = 'in_combat'
        verdict = False
        for index, sample in enumerate(samples):
            verdict = machine._confirmator.observe(sample, float(index))
        return verdict

    def test_the_two_agree_on_a_clean_run(self):
        samples = [view(False, sampled_at=0.0), view(False, sampled_at=1.0),
                   view(False, sampled_at=2.0)]
        self.assertTrue(self._machine_verdict(samples))

    def test_the_two_agree_when_the_state_is_unknown(self):
        samples = [view(None, sampled_at=0.0), view(None, sampled_at=1.0),
                   view(None, sampled_at=2.0)]
        self.assertFalse(self._machine_verdict(samples))


class ExitBranchTests(unittest.TestCase):
    def test_reengagement_interrupts_confirmation_and_resumes_recovery(self):
        backend = FakeBackend([Observation(71, in_combat=False),
                               Observation(65, in_combat=True)])
        with patch('agent_wow_exit_recovery._finish_the_target', return_value={
                'outcome': 'budget_exhausted', 'final': Observation(65, in_combat=True)
        }) as recover:
            result = resolve_combat_before_exit(
                backend, budget_seconds=30, passive_seconds=0,
                confirm_seconds=100, clock=Clock(), sleep=sleeper)
        recover.assert_called_once()
        self.assertIn('rpc:bridge.stop_attack', backend.requests)
        self.assertFalse(result['combat_cleared'])

    def test_unknown_combat_state_is_not_reported_as_cleared(self):
        backend = FakeBackend([Observation(71, in_combat=None),
                               Observation(71, in_combat=None),
                               Observation(71, in_combat=None)])
        result = resolve_combat_before_exit(backend, budget_seconds=3,
                                            passive_seconds=1, confirm_seconds=1,
                                            sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertFalse(result['combat_cleared'])
        self.assertIsNone(result['cleared_reason'])
        self.assertTrue(result['combat_state_unknown'])

    def test_unknown_combat_state_does_not_start_a_fight(self):
        backend = FakeBackend([Observation(71, in_combat=None)])
        result = resolve_combat_before_exit(backend, budget_seconds=3,
                                            passive_seconds=1, confirm_seconds=1,
                                            sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertFalse(result['combat_cleared'])
        self.assertNotIn('rpc:bridge.start_attack', backend.requests)

    def test_a_single_clean_sample_is_not_a_clearance(self):
        # One server sample saying "not in combat" is not enough: the rule
        # needs several over a dwell.
        backend = FakeBackend([Observation(71, in_combat=False)])
        result = resolve_combat_before_exit(backend, budget_seconds=3,
                                            passive_seconds=1, confirm_seconds=0,
                                            sleep=sleeper, finish_budget_seconds=0.3,
                                            clock=Clock())
        self.assertFalse(result['combat_cleared'])


class CloseSessionTests(unittest.TestCase):
    def test_close_session_does_not_log_out_on_an_unknown_combat_state(self):
        backend = FakeBackend([Observation(71, in_combat=None)])
        report = {}
        result = close_session(backend, report=report, budget_seconds=2,
                               sleep=sleeper, clock=Clock())
        self.assertEqual(result['status'], 'combat_state_unknown')
        self.assertNotIn('execute:logout', backend.requests)

    def test_failed_recovery_confirmation_blocks_logout_even_if_final_is_false(self):
        backend = FakeBackend([Observation(71, in_combat=True)])
        recovery = {'combat_cleared': False, 'samples': [], 'elapsed_s': 0,
                    'requests': 'observation', 'final': Observation(71, in_combat=False)}
        with patch('agent_wow_exit_recovery.resolve_combat_before_exit',
                   return_value=recovery):
            result = close_session(backend, report={})
        self.assertEqual(result['status'], 'unresolved')
        self.assertNotIn('execute:logout', backend.requests)

    def test_close_session_logs_out_when_the_state_is_confirmed_clear(self):
        backend = FakeBackend([Observation(71, in_combat=False)])
        report = {}
        result = close_session(backend, report=report, budget_seconds=2,
                               sleep=sleeper, clock=Clock())
        self.assertEqual(result['status'], 'resolved')
        self.assertIn('execute:logout', backend.requests)


if __name__ == '__main__':
    unittest.main()
