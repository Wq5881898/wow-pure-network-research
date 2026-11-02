"""Guards for the shared combat-ending rule.

The combat recovery state machine records the decision and the exit branch
carries it out. Two copies of that rule would drift, and the drift would
show up as the executor acting on one rule while the evidence records
another - the same failure that produced the false "escape" result in
esc008 (docs/118).

So there is exactly ONE rule, in
agent_wow_combat_state_machine.choose_combat_ending, and these tests pin it
and pin that both sides use it.
"""
import unittest

from agent_wow_combat_state_machine import (ENDING_ESCAPE, ENDING_FIGHT,
                                             ENDING_FINISH_TARGET,
                                             CombatStateMachine, ObservationView,
                                             choose_combat_ending)


class EndingRuleTests(unittest.TestCase):
    def test_no_route_means_fight_on_never_idle(self):
        # Idling while a hostile keeps hitting guarantees the character's
        # death without changing the outcome (esc010, esc013, esc014).
        self.assertEqual(
            choose_combat_ending(self_health=90, self_max_health=108,
                                  escape_route_available=False),
            ENDING_FIGHT)

    def test_a_healthy_character_escapes_from_a_healthy_target(self):
        self.assertEqual(
            choose_combat_ending(self_health=90, self_max_health=108,
                                  target_health=71, target_max_health=71,
                                  escape_route_available=True),
            ENDING_ESCAPE)

    def test_a_nearly_dead_target_is_killed_instead_of_escaped_from(self):
        # esc020: the target was at 22 of 71 HP and the character at 91 of
        # 108, and escaping cost 60 damage and did not clear the combat.
        self.assertEqual(
            choose_combat_ending(self_health=91, self_max_health=108,
                                  target_health=22, target_max_health=71,
                                  escape_route_available=True),
            ENDING_FINISH_TARGET)

    def test_a_weak_character_does_not_open_a_new_fight(self):
        self.assertEqual(
            choose_combat_ending(self_health=20, self_max_health=108,
                                  target_health=5, target_max_health=71,
                                  escape_route_available=True),
            ENDING_ESCAPE)

    def test_an_unknown_target_cannot_justify_killing_it(self):
        self.assertEqual(
            choose_combat_ending(self_health=90, self_max_health=108,
                                  target_health=None, target_max_health=None,
                                  escape_route_available=True),
            ENDING_ESCAPE)


    def test_an_unreachable_target_cannot_be_killed(self):
        # huntD (2026-11-01): the target had broken contact, the fight
        # controller refused to start, and the character died standing there.
        # The kill ending must not be offered when the target cannot be
        # reached from where the character stands.
        self.assertEqual(
            choose_combat_ending(self_health=91, self_max_health=108,
                                  target_health=22, target_max_health=71,
                                  escape_route_available=True,
                                  target_reachable=False),
            ENDING_ESCAPE)

    def test_a_reachable_nearly_dead_target_is_still_killed(self):
        self.assertEqual(
            choose_combat_ending(self_health=91, self_max_health=108,
                                  target_health=22, target_max_health=71,
                                  escape_route_available=True,
                                  target_reachable=True),
            ENDING_FINISH_TARGET)


class StateMachineUsesTheSharedRuleTests(unittest.TestCase):
    def _machine(self, escape_path_planned):
        return CombatStateMachine(escape_path_planned=escape_path_planned)

    def _low_health_step(self, machine, target_health=None, target_max_health=None,
                         health=30):
        view = ObservationView(sampled_at=1.0, health=health, max_health=108,
                               health_transmitted=True, in_combat=True,
                               target_health=target_health,
                               target_max_health=target_max_health)
        return machine.step(view)

    def test_a_latched_low_health_exit_never_recommends_idling(self):
        # The state machine used to answer hold_and_observe when no escape
        # path was planned - the one thing that is fatal while a hostile
        # keeps hitting.
        machine = self._machine(escape_path_planned=False)
        self._low_health_step(machine)
        step = self._low_health_step(machine)
        self.assertNotEqual(step.decision, 'hold_and_observe')
        self.assertEqual(step.decision, 'continue_attack')

    def test_a_latched_low_health_exit_requests_escape_when_a_route_exists(self):
        machine = self._machine(escape_path_planned=True)
        self._low_health_step(machine)
        step = self._low_health_step(machine)
        self.assertEqual(step.decision, 'request_escape')

    def test_a_latched_exit_kills_a_nearly_dead_target_when_it_can(self):
        # The character has to be healthy enough for the trade to be worth
        # it: at 28% of its health it escapes even from a nearly dead target.
        # The machine is already latched into the low-health exit, so the
        # later samples decide how the combat ends.
        machine = self._machine(escape_path_planned=True)
        self._low_health_step(machine, target_health=10, target_max_health=71,
                              health=30)
        step = self._low_health_step(machine, target_health=10,
                                      target_max_health=71, health=90)
        self.assertEqual(step.decision, 'continue_attack')

    def test_a_weak_character_kills_a_nearly_dead_target_instead_of_running(self):
        # The escape route has to clear the hostile's visibility range, which
        # is about 24 s of running and roughly a dozen mob attacks. At 28% of
        # the health budget that run is a death march, and the target is one
        # hit from dying, so the kill is the ending that does not need a run.
        self.assertEqual(
            choose_combat_ending(self_health=30, self_max_health=108,
                                 target_health=10, target_max_health=71,
                                 escape_route_available=True),
            ENDING_FINISH_TARGET)

    def test_a_weak_character_still_gets_out_when_the_target_is_healthy(self):
        # The run is a death march and the target is not close to dying, so
        # neither ending is cheap. Getting out still wins: if the character
        # dies on the way it dies AWAY from the hostile, and the corpse run
        # is much safer from there.
        self.assertEqual(
            choose_combat_ending(self_health=40, self_max_health=108,
                                 target_health=71, target_max_health=71,
                                 escape_route_available=True),
            ENDING_ESCAPE)

    def test_the_escape_is_offered_when_the_run_can_be_survived(self):
        # 90 of 108 is 83%, above the survival ratio for the 24 s run.
        self.assertEqual(
            choose_combat_ending(self_health=90, self_max_health=108,
                                 target_health=71, target_max_health=71,
                                 escape_route_available=True),
            ENDING_ESCAPE)


    def test_a_latched_exit_kills_a_nearly_dead_target_it_cannot_outrun(self):
        # The state machine has to reach the same conclusion as the rule:
        # at 28% of the health budget the 24 s run is fatal, so a nearly
        # dead target is killed instead.
        machine = self._machine(escape_path_planned=True)
        self._low_health_step(machine, target_health=10, target_max_health=71,
                              health=30)
        step = self._low_health_step(machine, target_health=10,
                                     target_max_health=71, health=30)
        self.assertEqual(step.decision, 'continue_attack')


class ExitBranchUsesTheSharedRuleTests(unittest.TestCase):
    def test_the_exit_branch_calls_the_shared_rule(self):
        import agent_wow_exit_recovery as exit_recovery
        with open(exit_recovery.__file__, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn('choose_combat_ending', source,
                      'the exit branch must use the shared rule, not a copy of it')
        self.assertNotIn('def _finish_the_target_is_better', source,
                         'the duplicated rule must be gone')


if __name__ == '__main__':
    unittest.main()
