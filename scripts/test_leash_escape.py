"""Guards for the leash-based escape, taken from the server sources.

The escape is not "run far away and hope". AzerothCore decides it explicitly:

  - ``CONFIG_CREATURE_LEASH_RADIUS`` ("CreatureLeashRadius") = 30.0 yards
    (src/server/game/World/WorldConfig.cpp)
  - ``Creature::CanCreatureAttack()`` compares the victim against the
    creature's HOME position (``m_homePosition``), not against where the
    creature currently is, and skips that comparison while the creature was
    recently damaged ("allow kiting away from spawnpoint")
  - ``Creature::GetLeashTimer()`` = 11 seconds for low level mobs (1-5)
    (src/server/game/Entities/Creature/Creature.cpp)

So the hostile breaks off only when the character is more than 30 yards from
the hostile's SPAWN and has not damaged it for 11 seconds. The escape has to
be planned against the spawn, and it has to outlast the timer.
"""
import shutil
import unittest

if not shutil.which('ip'):
    raise unittest.SkipTest('the backend module requires the WSL `ip route` tool')

from agent_wow_exit_recovery import LEASH_RADIUS_YARDS, LEASH_TIMER_SECONDS


class LeashRuleTests(unittest.TestCase):
    def test_the_constants_match_the_server(self):
        self.assertEqual(LEASH_RADIUS_YARDS, 30.0)
        self.assertEqual(LEASH_TIMER_SECONDS, 11.0)

    def test_the_escape_waits_longer_than_the_leash_timer(self):
        # The wait must cover the walk AND the leash timer, otherwise the
        # hostile is still in its extended leash when the wait ends
        # (2026-11-01 b10_u1: the escape was sent and the combat did not
        # clear).
        from agent_wow_exit_recovery import _perform_retreat
        import inspect
        source = inspect.getsource(_perform_retreat)
        self.assertIn('LEASH_TIMER_SECONDS', source,
                      'the escape wait must account for the leash timer')

    def test_the_route_is_planned_against_the_spawn(self):
        import inspect
        from agent_wow_session import PureNetworkBackend
        source = inspect.getsource(PureNetworkBackend.plan_escape_route)
        self.assertIn('home', source,
                      'the escape must be planned against the hostile spawn')
        self.assertIn('LEASH_RADIUS_YARDS', source,
                      'the destination must clear the leash radius')


if __name__ == '__main__':
    unittest.main()
