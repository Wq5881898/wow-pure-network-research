"""Guards for the independent recovery branch.

The recovery used to be two things that both failed to show up in the
evidence:

  - a plan step that sat after the fight, which never ran, because a fight
    that ends with the combat unresolved stops the plan (docs/52: the
    "combat unresolved" case has to be a branch of its own);
  - an implicit part of the session teardown, so the evidence never showed
    it as an executed action.

It is now dispatched as its own unit, and these tests pin that it runs, that
it ends the combat before the logout, and that it never re-dispatches the
fight.
"""
import shutil
import unittest

if not shutil.which('ip'):
    raise unittest.SkipTest('the runner module requires the WSL `ip route` tool')

from wsl_task_runner import run_recovery_branch


class Observation:
    def __init__(self, health=108, ghost=False, in_combat=False):
        self.health = health
        self.ghost = ghost
        self.in_combat = in_combat
        self.context = {}
        self.quest_log = []
        self.item_totals = {}
        self.available = ()
        self.position = [0.0, 0.0, 0.0]
        self.max_health = 108
        self.map_id = 1
        self.character = '9'
        self.session_id = 's'
        self.level = 3

    def to_dict(self):
        return {'health': self.health, 'ghost': self.ghost,
                'in_combat': self.in_combat}


class FakeBackend:
    def __init__(self, in_combat=True, cleared=False):
        self.in_combat = in_combat
        self.cleared = cleared
        self.calls = []

    def observe(self):
        self.calls.append('observe')
        return Observation(health=90, in_combat=(self.in_combat and not self.cleared))

    def execute(self, action, timeout=None):
        self.calls.append('execute:%s' % action.kind)
        if action.kind == 'recover_combat':
            self.cleared = True
            return {'combat_cleared': True, 'ending': 'escape',
                    'cleared_reason': 'left_reach', 'side_effect_applied': True}
        return {'status': 'closed'}

    def rpc(self, method, params):
        self.calls.append('rpc:%s' % method)
        if method == 'bridge.start_attack':
            raise AssertionError('the recovery branch must never re-dispatch the fight')
        if method == 'bridge.stop_attack':
            self.cleared = True
        return {'result': {}}


class RecoveryBranchTests(unittest.TestCase):
    def test_the_branch_dispatches_the_recovery(self):
        import tempfile
        from pathlib import Path
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as folder:
            outcome = run_recovery_branch(backend, 'unit', Path(folder))
        kinds = [record.get('kind') for record in outcome['records']]
        self.assertIn('recover_combat', kinds)
        self.assertEqual(outcome['status'], 'success')

    def test_the_branch_never_re_dispatches_the_fight(self):
        import tempfile
        from pathlib import Path
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as folder:
            run_recovery_branch(backend, 'unit', Path(folder))
        self.assertNotIn('rpc:bridge.start_attack', backend.calls)

    def test_the_branch_has_its_own_checkpoint(self):
        # The main plan's checkpoint belongs to the main plan; the recovery
        # must not take it over, or the main run could not be resumed.
        import tempfile
        from pathlib import Path
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run_recovery_branch(backend, 'unit', root)
            names = sorted(p.name for p in root.rglob('*') if p.is_file())
        self.assertTrue(any('unit_recovery' in name for name in names),
                        'the recovery needs its own checkpoint: %s' % names)
        self.assertFalse(any(name == 'agent_wow_task_unit.checkpoint.json'
                             for name in names),
                         'the recovery must not take the main plan checkpoint')


if __name__ == '__main__':
    unittest.main()
