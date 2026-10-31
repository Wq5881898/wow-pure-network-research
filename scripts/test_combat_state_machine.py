"""Offline exhaustive tests for the pure combat recovery state machine."""
import unittest
from unittest.mock import Mock, patch

from agent_wow_combat_state_machine import (
    CombatSMConfig, CombatStateMachine, ObservationView,
    distance_confirmed_for,
    DEAD, DISENGAGED_CONFIRMED, DISENGAGING, ENGAGING, GHOST, IN_COMBAT,
    LOW_HEALTH_EXIT, PRECHECK, TARGET_OUT_OF_RANGE, UNRESOLVED,
    DECISION_NONE, DECISION_START_ATTACK, DECISION_CONTINUE_ATTACK,
    DECISION_STOP_ATTACK, DECISION_REQUEST_ESCAPE, DECISION_HOLD_AND_OBSERVE,
    DECISION_ABORT_UNIT,
    REASON_HEALTH_UNKNOWN, REASON_HEALTH_RATIO_BELOW_LOW,
    REASON_HEALTH_RATIO_BELOW_CRITICAL, REASON_SELF_DEAD, REASON_SELF_GHOST,
    REASON_TARGET_DEAD, REASON_TARGET_MISSING,
    REASON_TARGET_OUT_OF_RANGE_CONFIRMED, REASON_TARGET_DISTANCE_UNCONFIRMED,
    REASON_OUT_OF_COMBAT_CONFIRMED, REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,
    REASON_BUDGET_EXHAUSTED, STATES, DECISIONS, REASONS)


def obs(now=0.0, health=100, max_health=108, transmitted=True, ghost=False,
        in_combat=True, target_present=True, target_health=50,
        distance=2.0, distance_confirmed=False, target_guid='t1'):
    return ObservationView(sampled_at=now, health=health if transmitted else None,
                           max_health=max_health, health_transmitted=transmitted,
                           ghost=ghost, in_combat=in_combat,
                           target_guid=target_guid if target_present else None,
                           target_present=target_present,
                           target_health=target_health if target_present else None,
                           distance=distance,
                           distance_confirmed=distance_confirmed)


def drive(sm, observations):
    return [sm.step(o) for o in observations]


class ConfigTests(unittest.TestCase):
    def test_ratios_reject_inverted_and_single_sample_config(self):
        with self.assertRaises(ValueError):
            CombatSMConfig(low_health_ratio=0.2, critical_health_ratio=0.5)
        with self.assertRaises(ValueError):
            CombatSMConfig(out_of_combat_confirmations=1)

    def test_distance_confirmation_sources(self):
        # Every estimated-position source used by combat_control is unconfirmed.
        for source in ('last object update', 'server stop',
                       'unsupported motion; start only',
                       'linear receive-time estimate', 'linear endpoint estimate'):
            self.assertFalse(distance_confirmed_for(source))
        self.assertFalse(distance_confirmed_for(None))
        self.assertTrue(distance_confirmed_for('login position'))
        self.assertTrue(distance_confirmed_for('landing confirmed position'))

    def test_contract_strings_are_exact(self):
        self.assertEqual(set(STATES), {
            'precheck', 'engaging', 'in_combat', 'low_health_exit',
            'target_out_of_range', 'disengaging', 'disengaged_confirmed',
            'dead', 'ghost', 'unresolved'})
        self.assertEqual(set(DECISIONS), {
            'none', 'start_attack', 'continue_attack', 'stop_attack',
            'request_escape', 'hold_and_observe', 'abort_unit'})
        self.assertEqual(set(REASONS), {
            'health_unknown', 'health_ratio_below_low',
            'health_ratio_below_critical', 'self_dead', 'self_ghost',
            'target_dead', 'target_missing', 'target_out_of_range_confirmed',
            'target_distance_unconfirmed', 'out_of_combat_confirmed',
            'out_of_combat_not_yet_confirmed', 'budget_exhausted'})


class HealthTruthTests(unittest.TestCase):
    def test_untransmitted_health_is_unknown_not_dead(self):
        # Regression guard for run40: max_health=70 sent, health never sent.
        sm = CombatStateMachine()
        steps = drive(sm, [obs(transmitted=False, in_combat=True) for _ in range(4)])
        self.assertNotEqual(sm.state, DEAD)
        for step in steps:
            self.assertNotEqual(sm.state, DEAD)
            self.assertIn(REASON_HEALTH_UNKNOWN, step.reasons)
            self.assertNotIn(step.decision, (DECISION_STOP_ATTACK,
                                             DECISION_REQUEST_ESCAPE,
                                             DECISION_ABORT_UNIT))

    def test_transmitted_zero_is_dead(self):
        sm = CombatStateMachine()
        step = sm.step(obs(health=0, in_combat=True))
        self.assertEqual((step.state, step.decision), (DEAD, DECISION_NONE))
        self.assertEqual(sm.state, DEAD)

    def test_unknown_health_without_baseline_is_unknown(self):
        view = ObservationView.from_mapping(
            {'health': 0, 'health_transmitted': True, 'max_health': 0,
             'ghost': False, 'in_combat': True}, 0.0)
        self.assertIsNone(view.health)

    def test_from_mapping_normalizes_untransmitted_to_none(self):
        view = ObservationView.from_mapping(
            {'health': 0, 'health_transmitted': False, 'max_health': 70,
             'ghost': False, 'in_combat': False}, 1.5)
        self.assertIsNone(view.health)
        self.assertFalse(view.health_transmitted)
        self.assertEqual(view.max_health, 70)
        self.assertEqual(view.sampled_at, 1.5)


class HealthRatioTests(unittest.TestCase):
    def test_ratio_thresholds_track_max_health(self):
        # 30/108=27.8% is low-only at 108 but critical at 70 (42.9%>25%? no:
        # 30/70=42.9% is low-only too; use 17: 17/108=15.7% critical, while
        # 17/70=24.3% is still below critical there too. Contrast properly:
        # 20/70=28.6% is low-only, but 20/108=18.5% is critical at 108? No:
        # 18.5% > 25%? No. The drift case: 25/108=23.1% critical, 25/70=35.7%
        # low-only.)
        config = CombatSMConfig()
        low_at_70 = CombatStateMachine(config=config)
        step = low_at_70.step(obs(health=25, max_health=70, in_combat=True))
        self.assertEqual(step.state, LOW_HEALTH_EXIT)
        self.assertEqual(step.reasons, (REASON_HEALTH_RATIO_BELOW_LOW,))
        critical_at_108 = CombatStateMachine(config=config)
        step = critical_at_108.step(obs(health=25, max_health=108, in_combat=True))
        self.assertEqual(step.state, LOW_HEALTH_EXIT)
        self.assertEqual(step.reasons, (REASON_HEALTH_RATIO_BELOW_CRITICAL,))

    def test_low_exit_stops_attack(self):
        sm = CombatStateMachine()
        steps = drive(sm, [obs(in_combat=True),
                           obs(health=40, in_combat=True)])
        self.assertEqual(steps[1].decision, DECISION_STOP_ATTACK)
        self.assertEqual(steps[1].state, LOW_HEALTH_EXIT)

    def test_low_exit_latches_against_reentry_and_restart(self):
        # Even a later full-health in-combat observation must not re-enter
        # combat or emit start_attack after a low exit.
        sm = CombatStateMachine()
        drive(sm, [obs(in_combat=True), obs(health=10, in_combat=True)])
        self.assertEqual(sm.state, LOW_HEALTH_EXIT)
        for _ in range(5):
            step = sm.step(obs(health=108, in_combat=True))
            self.assertEqual(step.state, LOW_HEALTH_EXIT)
            self.assertNotEqual(step.decision, DECISION_START_ATTACK)
            self.assertNotEqual(step.state, IN_COMBAT)

    def test_low_exit_with_escape_path_requests_escape(self):
        sm = CombatStateMachine(escape_path_planned=True)
        drive(sm, [obs(in_combat=True)])
        step = sm.step(obs(health=10, in_combat=True))
        self.assertEqual((step.state, step.decision),
                         (LOW_HEALTH_EXIT, DECISION_REQUEST_ESCAPE))
        step = sm.step(obs(health=9, in_combat=True))
        self.assertEqual(step.decision, DECISION_REQUEST_ESCAPE)


class TargetRangeTests(unittest.TestCase):
    def test_confirmed_out_of_range_stops_and_never_restarts(self):
        sm = CombatStateMachine()
        drive(sm, [obs(in_combat=True)])
        step = sm.step(obs(in_combat=True, distance=9.0,
                           distance_confirmed=True))
        self.assertEqual(step.state, TARGET_OUT_OF_RANGE)
        self.assertEqual(step.decision, DECISION_STOP_ATTACK)
        self.assertEqual(step.reasons, (REASON_TARGET_OUT_OF_RANGE_CONFIRMED,))
        for _ in range(4):
            step = sm.step(obs(in_combat=True, distance=2.0,
                               distance_confirmed=True))
            self.assertEqual(step.state, TARGET_OUT_OF_RANGE)
            self.assertNotEqual(step.decision, DECISION_START_ATTACK)

    def test_unconfirmed_distance_never_claims_flight(self):
        sm = CombatStateMachine()
        drive(sm, [obs(in_combat=True)])
        step = sm.step(obs(in_combat=True, distance=9.0,
                           distance_confirmed=False))
        self.assertEqual(step.state, TARGET_OUT_OF_RANGE)
        self.assertEqual(step.decision, DECISION_STOP_ATTACK)
        self.assertEqual(step.reasons, (REASON_TARGET_DISTANCE_UNCONFIRMED,))
        self.assertNotIn(REASON_TARGET_OUT_OF_RANGE_CONFIRMED, step.reasons)

    def test_dead_target_is_not_out_of_range(self):
        sm = CombatStateMachine()
        drive(sm, [obs(in_combat=True)])
        step = sm.step(obs(in_combat=True, target_health=0, distance=99.0))
        self.assertEqual(step.reasons, (REASON_TARGET_DEAD,))
        self.assertEqual(step.decision, DECISION_STOP_ATTACK)
        self.assertNotEqual(step.state, TARGET_OUT_OF_RANGE)

    def test_missing_target_is_not_out_of_range(self):
        sm = CombatStateMachine()
        drive(sm, [obs(in_combat=True)])
        step = sm.step(obs(in_combat=True, target_present=False, distance=99.0))
        self.assertEqual(step.reasons, (REASON_TARGET_MISSING,))
        self.assertEqual(step.decision, DECISION_ABORT_UNIT)
        self.assertNotEqual(step.state, TARGET_OUT_OF_RANGE)

    def test_missing_target_after_stop_is_not_restarted(self):
        sm = CombatStateMachine()
        drive(sm, [obs(in_combat=True), obs(in_combat=True, target_present=False)])
        step = sm.step(obs(in_combat=True, target_present=False))
        self.assertEqual(step.state, UNRESOLVED)
        self.assertEqual(step.decision, DECISION_NONE)
        self.assertNotEqual(step.decision, DECISION_START_ATTACK)


class DisengagementTests(unittest.TestCase):
    def test_single_sample_never_confirms(self):
        sm = CombatStateMachine(start_sent=True)
        step = sm.step(obs(in_combat=False))
        self.assertEqual(step.state, DISENGAGING)
        self.assertEqual(step.decision, DECISION_HOLD_AND_OBSERVE)
        self.assertIn(REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED, step.reasons)
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)

    def test_confirmation_needs_streak_and_minimum_dwell(self):
        sm = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=3,
                                               min_disengage_seconds=2.0))
        drive(sm, [obs(now=0.0, in_combat=True)])
        steps = drive(sm, [obs(now=1.0, in_combat=False),
                           obs(now=2.5, in_combat=False),
                           obs(now=3.0, in_combat=False)])
        # Streak reaches 3 at t=2.5 but the dwell is only 2.5 >= 2.0 -> ok
        self.assertEqual(steps[2].state, DISENGAGED_CONFIRMED)
        self.assertEqual(steps[2].reasons, (REASON_OUT_OF_COMBAT_CONFIRMED,))
        self.assertEqual(steps[2].decision, DECISION_NONE)

    def test_streak_alone_without_dwell_does_not_confirm(self):
        sm = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=2,
                                               min_disengage_seconds=10.0))
        drive(sm, [obs(now=0.0, in_combat=True)])
        steps = drive(sm, [obs(now=0.1, in_combat=False),
                           obs(now=0.2, in_combat=False)])
        self.assertEqual(steps[1].state, DISENGAGING)
        self.assertIn(REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED, steps[1].reasons)

    def test_unknown_in_combat_resets_streak_and_never_counts(self):
        sm = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=2,
                                               min_disengage_seconds=0.0))
        drive(sm, [obs(now=0.0, in_combat=True)])
        steps = drive(sm, [obs(now=1.0, in_combat=False),
                           obs(now=2.0, in_combat=None),
                           obs(now=3.0, in_combat=False)])
        self.assertEqual(steps[2].state, DISENGAGING)
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)
        final = sm.step(obs(now=4.0, in_combat=False))
        self.assertEqual(final.state, DISENGAGED_CONFIRMED)

    def test_unknown_health_resets_streak_and_never_counts(self):
        sm = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=2,
                                               min_disengage_seconds=0.0))
        drive(sm, [obs(now=0.0, in_combat=True)])
        drive(sm, [obs(now=1.0, in_combat=False)])
        unknown = sm.step(obs(now=2.0, in_combat=False, transmitted=False))
        # The unknown-health sample must flag why the window is invalid,
        # reset the streak, and never count toward confirmation.
        self.assertEqual(unknown.state, DISENGAGING)
        self.assertIn(REASON_HEALTH_UNKNOWN, unknown.reasons)
        self.assertEqual(sm.out_of_combat_streak, 0)
        after = sm.step(obs(now=3.0, in_combat=False))
        self.assertEqual(after.state, DISENGAGING)
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)

    def test_health_drop_resets_streak(self):
        sm = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=3,
                                               min_disengage_seconds=0.0))
        drive(sm, [obs(now=0.0, health=100, in_combat=True)])
        steps = drive(sm, [obs(now=1.0, health=100, in_combat=False),
                           obs(now=2.0, health=90, in_combat=False),
                           obs(now=3.0, health=90, in_combat=False),
                           obs(now=4.0, health=90, in_combat=False),
                           obs(now=5.0, health=90, in_combat=False),
                           obs(now=6.0, health=90, in_combat=False)])
        self.assertEqual(steps[1].state, DISENGAGING)  # drop resets
        self.assertEqual(sm.state, DISENGAGED_CONFIRMED)
        self.assertEqual(steps[5].state, DISENGAGED_CONFIRMED)

    def test_no_sampling_means_no_confirmation(self):
        sm = CombatStateMachine()
        self.assertEqual(sm.state_name(), PRECHECK)
        self.assertNotEqual(sm.state_name(), DISENGAGED_CONFIRMED)

    def test_engaging_without_combat_still_requires_confirmation(self):
        sm = CombatStateMachine(CombatSMConfig(out_of_combat_confirmations=2,
                                               min_disengage_seconds=0.0),
                                start_sent=True)
        steps = drive(sm, [obs(now=0.0, in_combat=False),
                           obs(now=1.0, in_combat=False)])
        self.assertEqual(steps[0].state, DISENGAGING)
        self.assertEqual(steps[1].state, DISENGAGED_CONFIRMED)


class DeathGhostTests(unittest.TestCase):
    def test_ghost_is_terminal_and_distinct_from_low(self):
        sm = CombatStateMachine()
        step = sm.step(obs(ghost=True, health=1, in_combat=True))
        self.assertEqual(step.state, GHOST)
        self.assertEqual(step.reasons, (REASON_SELF_GHOST,))
        step = sm.step(obs(ghost=True, health=108, in_combat=True))
        self.assertEqual(step.state, GHOST)
        self.assertEqual(step.decision, DECISION_NONE)

    def test_dead_is_terminal(self):
        sm = CombatStateMachine()
        sm.step(obs(health=0, in_combat=True))
        step = sm.step(obs(health=50, in_combat=False))
        self.assertEqual(step.state, DEAD)
        self.assertEqual(step.decision, DECISION_NONE)

    def test_terminal_states_emit_no_attack_decisions(self):
        for terminal in (DEAD, GHOST):
            sm = CombatStateMachine()
            if terminal == DEAD:
                sm.step(obs(health=0, in_combat=True))
            else:
                sm.step(obs(ghost=True, in_combat=True))
            for _ in range(3):
                step = sm.step(obs(in_combat=True))
                self.assertEqual(step.state, terminal)
                self.assertNotIn(step.decision,
                                 (DECISION_START_ATTACK, DECISION_CONTINUE_ATTACK))


class StartAndEngageTests(unittest.TestCase):
    def test_engaging_waits_then_confirms_combat(self):
        # An attack dispatched during precheck engages on the first
        # confirmed in-combat sample; an unconfirmed combat flag is
        # uninformative and holds in precheck (fix-3 semantics).
        sm = CombatStateMachine(start_sent=True)
        step = sm.step(obs(in_combat=None))
        self.assertEqual(step.state, PRECHECK)
        self.assertNotEqual(step.decision, DECISION_START_ATTACK)
        step = sm.step(obs(in_combat=True))
        self.assertEqual(step.state, IN_COMBAT)
        self.assertEqual(step.decision, DECISION_CONTINUE_ATTACK)

    def test_precheck_unknown_combat_does_not_advise_start_attack(self):
        # Second-round spec: unknown combat never authorizes an action (S6);
        # only a confirmed non-combat sample may advise start_attack (S7).
        sm = CombatStateMachine()
        step = sm.step(obs(in_combat=None))
        self.assertEqual(step.state, PRECHECK)
        self.assertNotEqual(step.decision, DECISION_START_ATTACK)
        second = sm.step(obs(in_combat=False))
        self.assertEqual(second.state, PRECHECK)
        self.assertEqual(second.decision, DECISION_START_ATTACK)
        third = sm.step(obs(in_combat=False))
        self.assertNotEqual(third.decision, DECISION_START_ATTACK)

    def test_advisory_start_attack_opens_recovery_on_next_noncombat_sample(self):
        # A pending advisory start could already have entered combat, so a
        # later non-combat sample is handled as recovery, not re-advising.
        sm = CombatStateMachine()
        first = sm.step(obs(now=0.0, in_combat=False))
        self.assertEqual(first.decision, DECISION_START_ATTACK)
        second = sm.step(obs(now=1.0, in_combat=False))
        self.assertEqual(second.state, DISENGAGING)
        self.assertNotEqual(second.decision, DECISION_START_ATTACK)

    def test_advisory_start_attack_unknown_combat_stays_precheck(self):
        # A pending advisory start with an uninformative combat flag must
        # not open a recovery window: it stays precheck, never disengaging.
        sm = CombatStateMachine()
        first = sm.step(obs(now=0.0, in_combat=False))
        self.assertEqual(first.decision, DECISION_START_ATTACK)
        second = sm.step(obs(now=1.0, in_combat=None))
        self.assertEqual(second.state, PRECHECK)
        self.assertNotEqual(second.state, DISENGAGING)
        self.assertNotEqual(second.decision, DECISION_START_ATTACK)

    def test_health_unknown_in_combat_holds(self):
        sm = CombatStateMachine()
        step = sm.step(obs(transmitted=False, in_combat=True))
        self.assertEqual(step.state, IN_COMBAT)
        self.assertEqual(step.decision, DECISION_HOLD_AND_OBSERVE)
        self.assertEqual(step.reasons, (REASON_HEALTH_UNKNOWN,))


class BudgetTests(unittest.TestCase):
    def test_budget_exhaustion_ends_unresolved(self):
        sm = CombatStateMachine()
        sm.step(obs(in_combat=True))
        step = sm.finish_budget()
        self.assertEqual(step.state, UNRESOLVED)
        self.assertEqual(step.decision, DECISION_ABORT_UNIT)
        self.assertEqual(step.reasons, (REASON_BUDGET_EXHAUSTED,))
        again = sm.finish_budget()
        self.assertEqual(again.state, UNRESOLVED)
        self.assertEqual(again.decision, DECISION_NONE)

    def test_no_diagnostic_states_exist(self):
        # The machine only ever visits contract states; there is no place a
        # sampling/replay/diagnostic loop could live.
        sm = CombatStateMachine()
        seen = set()
        timeline = [obs(in_combat=True), obs(health=44, in_combat=True),
                    obs(health=44, in_combat=False), obs(health=44, in_combat=False),
                    obs(health=44, in_combat=False)]
        seen.add(sm.step(timeline[0]).state)
        for o in timeline[1:]:
            seen.add(sm.step(o).state)
        self.assertTrue(seen.issubset(set(STATES)))
        self.assertFalse(any('diag' in s or 'sample' in s for s in seen))


class ExhaustiveSmallSpaceTests(unittest.TestCase):
    def test_all_short_input_sequences_stay_in_contract(self):
        healths = [None, 0, 10, 60, 108]
        combs = [True, False, None]
        presences = [True, False]
        distances = [1.0, 9.0]
        sm_config = CombatSMConfig(out_of_combat_confirmations=2,
                                   min_disengage_seconds=0.0)
        for health in healths:
            for in_combat in combs:
                for present in presences:
                    for distance in distances:
                        sm = CombatStateMachine(sm_config)
                        for t in range(5):
                            step = sm.step(obs(
                                now=float(t),
                                health=None if health is None else health,
                                transmitted=health is not None,
                                in_combat=in_combat,
                                target_present=present,
                                target_health=None if not present else 30,
                                distance=distance,
                                distance_confirmed=False))
                            self.assertIn(step.state, STATES)
                            self.assertIn(step.decision, DECISIONS)
                            for reason in step.reasons:
                                self.assertIn(reason, REASONS)
                            if step.state in (DEAD, GHOST, DISENGAGED_CONFIRMED,
                                              UNRESOLVED):
                                self.assertNotIn(step.decision,
                                                 (DECISION_START_ATTACK,
                                                  DECISION_CONTINUE_ATTACK))


class AdvisoryIntegrationTests(unittest.TestCase):
    """D: advisory consult is recorded in evidence but changes no behavior."""

    def test_fight_default_rows_have_no_advisory_keys(self):
        import unittest.mock as mock
        from agent_wow_combat_control import fight

        def state(x, health=5):
            return {'self': {'ghost': False, 'health': 108, 'in_combat': True},
                    'visible_objects': [{'guid': 'enemy', 'health': health,
                                         'movement': {'position_xyzo': [x, 0, 0, 0]}}]}
        calls = []
        def rpc(method, params):
            calls.append(method)
            return {'result': {}}
        with patch('agent_wow_combat_control.time.sleep'):
            result, _ = fight(rpc, mock.Mock(side_effect=[state(3), state(8), state(8)]),
                              'enemy', [0, 0, 0], lambda: 0, budget=1)
        self.assertEqual(result['outcome'], 'target_left_melee')
        self.assertNotIn('advisory_final_state', result)
        for row in result['observations']:
            self.assertNotIn('advisory_state', row)

    def test_fight_advisory_records_state_without_changing_outcome(self):
        import unittest.mock as mock
        from agent_wow_combat_control import fight

        def state(x, health=5):
            return {'self': {'ghost': False, 'health': 108, 'in_combat': True},
                    'visible_objects': [{'guid': 'enemy', 'health': health,
                                         'movement': {'position_xyzo': [x, 0, 0, 0]}}]}
        calls = []
        def rpc(method, params):
            calls.append(method)
            return {'result': {}}
        with patch('agent_wow_combat_control.time.sleep'):
            result, _ = fight(rpc, mock.Mock(side_effect=[state(3), state(8), state(8)]),
                              'enemy', [0, 0, 0], lambda: 0, budget=1,
                              advisory=True)
        # Same outcome, same dispatch sequence, same start_sent_once.
        self.assertEqual(result['outcome'], 'target_left_melee')
        self.assertTrue(result['start_sent_once'])
        self.assertEqual(calls.count('bridge.start_attack'), 1)
        self.assertEqual(calls[-1], 'bridge.stop_attack')
        # Advisory evidence is present and honest about unconfirmed distance.
        self.assertIn('advisory_final_state', result)
        rows = result['observations']
        self.assertTrue(all('advisory_state' in r and 'advisory_decision' in r
                            and 'advisory_reasons' in r for r in rows))
        self.assertEqual(rows[0]['advisory_state'], 'in_combat')
        last = rows[-1]
        self.assertEqual(last['advisory_state'], 'target_out_of_range')
        self.assertEqual(last['advisory_decision'], 'stop_attack')
        self.assertEqual(last['advisory_reasons'],
                         ['target_distance_unconfirmed'])

    def test_fight_advisory_flags_untransmitted_health_as_unknown(self):
        import unittest.mock as mock
        from agent_wow_combat_control import fight
        state = {'self': {'ghost': False, 'health': 0, 'health_transmitted': False,
                          'max_health': 70, 'in_combat': True},
                 'visible_objects': [{'guid': 'enemy', 'health': 50,
                                      'movement': {'position_xyzo': [2, 0, 0, 0]}}]}
        def rpc(method, params):
            return {'result': {}}
        result, _ = fight(rpc, lambda: state, 'enemy', [0, 0, 0], lambda: 0,
                          budget=1, advisory=True)
        # Legacy loop still breaks on health==0 before its first row (behavior
        # unchanged); the advisor still consulted that sample and recorded the
        # field as unknown, not dead, at the final-state evidence level.
        self.assertEqual(result['outcome'], 'self_dead')
        self.assertEqual(result['observations'], [])
        self.assertNotEqual(result['advisory_final_state'], 'dead')

    def test_advisory_decision_helper_flags_untransmitted_health_unknown(self):
        from agent_wow_combat_control import advisory_decision
        me = {'health': 0, 'health_transmitted': False, 'max_health': 70,
              'ghost': False, 'in_combat': True}
        target = {'guid': 'enemy', 'health': 50,
                  'movement': {'position_xyzo': [2, 0, 0, 0]}}
        machine, step = advisory_decision(me, target, 2.0, 'last object update', 0.0)
        self.assertNotEqual(step.state, 'dead')
        self.assertIn('health_unknown', step.reasons)

    def test_settle_default_evidence_unchanged_and_advisory_additive(self):
        import unittest.mock as mock
        from agent_wow_session_supervisor import settle

        def in_combat_state():
            return {'self': {'ghost': False, 'health': 10, 'in_combat': True},
                    'visible_objects': [{'guid': 'e1', 'type_id': 3, 'health': 40,
                                         'movement': {'position_xyzo': [2, 0, 0, 0],
                                                      'victim_guid': '9'}}]}
        def out_state():
            return {'self': {'ghost': False, 'health': 10, 'in_combat': False},
                    'visible_objects': []}
        def rpc(method, params):
            return {'result': {}}
        with patch('agent_wow_session_supervisor.time.sleep'), \
             patch('agent_wow_combat_control.time.sleep'):
            plain, _ = settle(rpc, mock.Mock(side_effect=[in_combat_state(), in_combat_state(), out_state()]),
                              [0, 0, 0], lambda: 0, budget=5)
        self.assertNotIn('advisory_final_state', plain)
        self.assertTrue(all('advisory_pre_phase' not in item for item in plain['phases']))
        with patch('agent_wow_session_supervisor.time.sleep'), \
             patch('agent_wow_combat_control.time.sleep'):
            advised, _ = settle(rpc, mock.Mock(side_effect=[in_combat_state(), in_combat_state(), out_state()]),
                                [0, 0, 0], lambda: 0, budget=5, advisory=True)
        self.assertEqual(advised['safe_to_logout'], plain['safe_to_logout'])
        self.assertEqual([item['phase']['outcome'] for item in advised['phases']
                          if 'phase' in item],
                         [item['phase']['outcome'] for item in plain['phases']
                          if 'phase' in item])
        self.assertIn('advisory_final_state', advised)
        self.assertTrue(any('advisory_pre_phase' in item for item in advised['phases']))


class ControllerAcceptanceFixTests(unittest.TestCase):
    """Tests demanded by the controller acceptance round (fixes 1-3)."""

    def test_dwell_restarts_after_health_drop(self):
        # Fix 1: a contaminated window must not confirm via three clean
        # samples that only span 0.2s; the dwell must be covered by the
        # current clean run alone.
        cfg = CombatSMConfig(out_of_combat_confirmations=3,
                             min_disengage_seconds=2.0)
        # start_sent: the unit is recovering from a dispatched fight, so the
        # first non-combat sample opens the window (precheck with no start
        # never does).
        sm = CombatStateMachine(cfg, start_sent=True)
        drive(sm, [obs(now=1.0, health=100, in_combat=False)])
        drive(sm, [obs(now=2.0, health=92, in_combat=False)])
        steps = drive(sm, [obs(now=9.0, health=92, in_combat=False),
                           obs(now=9.1, health=92, in_combat=False),
                           obs(now=9.2, health=92, in_combat=False)])
        self.assertEqual(steps[2].state, DISENGAGING)
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)
        # Only once the clean run itself spans the dwell can it confirm.
        final = sm.step(obs(now=11.2, health=92, in_combat=False))
        self.assertEqual(final.state, DISENGAGED_CONFIRMED)

    def test_dwell_restarts_after_unknown_health(self):
        cfg = CombatSMConfig(out_of_combat_confirmations=3,
                             min_disengage_seconds=2.0)
        # start_sent: the unit is recovering from a dispatched fight, so the
        # first non-combat sample opens the window (precheck with no start
        # never does).
        sm = CombatStateMachine(cfg, start_sent=True)
        drive(sm, [obs(now=1.0, health=100, in_combat=False)])
        drive(sm, [obs(now=2.0, health=100, in_combat=False, transmitted=False)])
        steps = drive(sm, [obs(now=9.0, health=100, in_combat=False),
                           obs(now=9.1, health=100, in_combat=False),
                           obs(now=9.2, health=100, in_combat=False)])
        self.assertEqual(steps[2].state, DISENGAGING)
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)
        final = sm.step(obs(now=11.2, health=100, in_combat=False))
        self.assertEqual(final.state, DISENGAGED_CONFIRMED)

    def test_dwell_restarts_after_unknown_in_combat(self):
        cfg = CombatSMConfig(out_of_combat_confirmations=3,
                             min_disengage_seconds=2.0)
        # start_sent: the unit is recovering from a dispatched fight, so the
        # first non-combat sample opens the window (precheck with no start
        # never does).
        sm = CombatStateMachine(cfg, start_sent=True)
        drive(sm, [obs(now=1.0, health=100, in_combat=False)])
        drive(sm, [obs(now=2.0, health=100, in_combat=None)])
        steps = drive(sm, [obs(now=9.0, health=100, in_combat=False),
                           obs(now=9.1, health=100, in_combat=False),
                           obs(now=9.2, health=100, in_combat=False)])
        self.assertEqual(steps[2].state, DISENGAGING)
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)
        final = sm.step(obs(now=11.2, health=100, in_combat=False))
        self.assertEqual(final.state, DISENGAGED_CONFIRMED)

    def test_clean_run_spanning_dwell_confirms(self):
        # Spec S1: a completely clean run spanning the dwell must confirm;
        # guards against a fix that can never confirm anything.
        cfg = CombatSMConfig(out_of_combat_confirmations=3,
                             min_disengage_seconds=2.0)
        sm = CombatStateMachine(cfg, start_sent=True)
        drive(sm, [obs(now=0.0, health=100, in_combat=False)])
        steps = drive(sm, [obs(now=9.0, health=100, in_combat=False),
                           obs(now=10.0, health=100, in_combat=False),
                           obs(now=11.2, health=100, in_combat=False)])
        self.assertEqual(steps[2].state, DISENGAGED_CONFIRMED)

    def test_clean_run_can_confirm_after_its_own_span(self):
        # Spec S3: after a contamination reset, once the new clean run
        # itself spans the dwell, confirmation is allowed again.
        cfg = CombatSMConfig(out_of_combat_confirmations=3,
                             min_disengage_seconds=2.0)
        sm = CombatStateMachine(cfg, start_sent=True)
        drive(sm, [obs(now=1.0, health=100, in_combat=False),
                   obs(now=2.0, health=92, in_combat=False)])
        drive(sm, [obs(now=9.0, health=92, in_combat=False),
                   obs(now=9.1, health=92, in_combat=False),
                   obs(now=9.2, health=92, in_combat=False)])
        self.assertNotEqual(sm.state, DISENGAGED_CONFIRMED)
        final = sm.step(obs(now=11.2, health=92, in_combat=False))
        self.assertEqual(final.state, DISENGAGED_CONFIRMED)

    def test_direct_view_untransmitted_zero_is_not_dead(self):
        # Fix 2: the decision core itself must respect health_transmitted,
        # even for a directly constructed view (no caller normalization).
        view = ObservationView(sampled_at=0.0, health=0, max_health=70,
                               health_transmitted=False, in_combat=True,
                               target_present=True, target_health=50)
        sm = CombatStateMachine()
        step = sm.step(view)
        self.assertNotEqual(step.state, DEAD)
        self.assertEqual(step.state, IN_COMBAT)
        self.assertIn(REASON_HEALTH_UNKNOWN, step.reasons)
        self.assertNotIn(REASON_SELF_DEAD, step.reasons)

    def test_direct_view_transmitted_zero_is_dead(self):
        view = ObservationView(sampled_at=0.0, health=0, max_health=70,
                               health_transmitted=True, in_combat=True,
                               target_present=True, target_health=50)
        sm = CombatStateMachine()
        step = sm.step(view)
        self.assertEqual(step.state, DEAD)
        self.assertEqual(step.reasons, (REASON_SELF_DEAD,))

    def test_direct_view_untransmitted_low_is_not_low_health(self):
        # Untransmitted raw 10 with a 70 baseline must not read as the low
        # ratio; only the transmitted flag can ground any ratio decision.
        view = ObservationView(sampled_at=0.0, health=10, max_health=70,
                               health_transmitted=False, in_combat=True,
                               target_present=True, target_health=50)
        sm = CombatStateMachine()
        step = sm.step(view)
        self.assertNotEqual(step.state, LOW_HEALTH_EXIT)
        self.assertNotIn(REASON_HEALTH_RATIO_BELOW_LOW, step.reasons)
        self.assertNotIn(REASON_HEALTH_RATIO_BELOW_CRITICAL, step.reasons)

    def test_precheck_start_attack_is_reachable(self):
        # Fix 3 + second-round spec (S7): the decision is reachable on a
        # confirmed non-combat sample, so the low-exit latch assertion
        # below is not vacuous.
        sm = CombatStateMachine()
        step = sm.step(obs(in_combat=False, health=100, target_present=True,
                           distance=2.0))
        self.assertEqual(step.state, PRECHECK)
        self.assertEqual(step.decision, DECISION_START_ATTACK)

    def test_low_exit_latch_never_emits_start_attack(self):
        # First prove reachability in the same setup, then prove the latch.
        reachable = CombatStateMachine()
        first = reachable.step(obs(in_combat=False, health=100))
        self.assertEqual(first.decision, DECISION_START_ATTACK)
        sm = CombatStateMachine()
        drive(sm, [obs(now=0.0, in_combat=False, health=100)])
        drive(sm, [obs(now=1.0, in_combat=True, health=10)])
        self.assertEqual(sm.state, LOW_HEALTH_EXIT)
        followups = [obs(now=2.0, in_combat=False, health=108),
                     obs(now=3.0, in_combat=None, health=108),
                     obs(now=4.0, in_combat=False, health=108,
                         target_present=False),
                     obs(now=5.0, in_combat=False, health=108),
                     obs(now=6.0, in_combat=None, health=None,
                         transmitted=False)]
        for o in followups:
            step = sm.step(o)
            self.assertEqual(step.state, LOW_HEALTH_EXIT)
            self.assertNotEqual(step.decision, DECISION_START_ATTACK)


if __name__ == '__main__':
    unittest.main()
