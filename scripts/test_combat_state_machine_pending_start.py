"""Regression guards for the pending-start window and terminal-state revival.

Controller-authored. These encode the two failures found while replaying the
real long01 combat capture:

1. A dispatched-but-unobserved attack opened a *recovery* window, so three
   out-of-combat samples plus the dwell completed `disengaged_confirmed`
   BEFORE the server ever flagged combat. The machine then froze for the whole
   real fight and never reached `in_combat`/`continue_attack`.

2. `disengaged_confirmed` is terminal: once frozen, a later `in_combat=True`
   observation could not revive the machine, so a stale safety claim poisoned
   the remainder of the session.
"""
import json
import unittest
from pathlib import Path

from agent_wow_combat_state_machine import (
    CombatSMConfig, CombatStateMachine, ObservationView)
from agent_wow_worldstate import IncrementalWorld

RESULTS = Path(__file__).resolve().parents[1] / 'results' / 'pure_network'
LONG01 = 'agent_wow_bridge_quest792_long01_unit1_verify.packets.json'
LONG01_TARGET = '17379391014048896448'


def obs(now, health=108, max_health=108, transmitted=True, ghost=False,
        in_combat=None, target=True, target_health=69, distance=2.0,
        distance_confirmed=False):
    return ObservationView(
        sampled_at=now, health=health if transmitted else None,
        max_health=max_health, health_transmitted=transmitted, ghost=ghost,
        in_combat=in_combat, target_guid='T' if target else None,
        target_present=target,
        target_health=target_health if target else None,
        distance=distance, distance_confirmed=distance_confirmed)


class PendingStartWindowTests(unittest.TestCase):
    """A pending attack must never be mistaken for a finished fight."""

    def test_pending_start_does_not_confirm_out_of_combat(self):
        # The attack was advised (confirmed not-in-combat), then the server has
        # not yet flagged combat. These samples must NOT complete a recovery
        # window: the fight outcome is still unknown.
        machine = CombatStateMachine(CombatSMConfig(), target_guid='T')
        first = machine.step(obs(0.0, in_combat=False))
        self.assertEqual(first.decision, 'start_attack')
        states = [first.state]
        for i in range(1, 12):
            step = machine.step(obs(i * 0.25, in_combat=False))
            states.append(step.state)
            self.assertNotEqual(
                step.state, 'disengaged_confirmed',
                'a pending, unobserved attack must not confirm out-of-combat '
                '(states seen: %s)' % sorted(set(states)))
        self.assertNotEqual(machine.state_name(), 'disengaged_confirmed')

    def test_confirmed_state_revives_when_combat_starts(self):
        # A stale confirmation must not freeze the machine: a later confirmed
        # in-combat sample has to bring it back to tracking combat.
        machine = CombatStateMachine(CombatSMConfig(), target_guid='T')
        machine.step(obs(0.0, in_combat=True))
        for i in range(1, 12):
            machine.step(obs(i * 0.25, in_combat=False))
        step = machine.step(obs(3.0, in_combat=True))
        self.assertNotEqual(
            machine.state_name(), 'disengaged_confirmed',
            'a later in-combat sample must revive a confirmed state')
        self.assertEqual(step.state, 'in_combat')
        self.assertEqual(step.decision, 'continue_attack')


class RealCaptureRegressionTests(unittest.TestCase):
    """The archived real fight must be tracked as a fight."""

    def test_long01_real_fight_is_tracked_as_combat(self):
        trace = json.loads((RESULTS / LONG01).read_bytes())
        world = IncrementalWorld(trace['session_id'], trace.get('self_guid', '9'))
        machine = CombatStateMachine(CombatSMConfig(), target_guid=LONG01_TARGET)
        states, decisions = [], []
        for packet in trace['packets']:
            try:
                state = world.apply([packet])
            except Exception:
                break
            me = state.get('self')
            if not me:
                continue
            target = next((o for o in state.get('visible_objects', [])
                           if o['guid'] == LONG01_TARGET), None)
            step = machine.step(obs(packet['monotonic_ms'] / 1000.0,
                                    health=me.get('health') or 0,
                                    transmitted=me.get('health_transmitted') is not False,
                                    ghost=bool(me.get('ghost')),
                                    in_combat=me.get('in_combat'),
                                    target=target is not None,
                                    target_health=(target or {}).get('health')))
            states.append(step.state)
            decisions.append(step.decision)
        self.assertIn(
            'in_combat', states,
            'the real capture contains confirmed in-combat samples; the machine '
            'must have reached in_combat (visited: %s)' % sorted(set(states)))
        self.assertIn('continue_attack', decisions,
                      'the machine must advise continuing the fight while the '
                      'server reports combat')


if __name__ == '__main__':
    unittest.main()
