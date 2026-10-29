import unittest
from agent_wow_position_context import PositionContext


def state(sequence=3, position=(1, 2, 3), health=89):
    return {'session_id': 's', 'login': {'map': 1},
            'self': {'health': health, 'ghost': False},
            'event_log': [{'guid': '9', 'sequence': sequence,
                           'movement': {'position_xyzo': list(position) + [0]}}]}


class ContextTests(unittest.TestCase):
    def test_poll_does_not_refresh_and_submission_is_prediction(self):
        c = PositionContext()
        c.update(state(), '9', 10)
        c.update(state(), '9', 20)
        self.assertEqual(c.view(20)['observed']['at'], 10)
        self.assertFalse(c.view(20)['usable_for_estimation'])
        c.submitted([4, 5, 6, 0], 5, 'path', 21)
        self.assertFalse(c.view(22)['position_confirmed'])
        c.update(state(), '9', 22)
        self.assertIsNotNone(c.view(22)['predicted'])
        c.update(state(6, (4, 5, 6)), '9', 23)
        self.assertIsNone(c.view(23)['predicted'])

    def test_death_and_identity_invalidate(self):
        for change in ('death', 'map', 'session'):
            c = PositionContext()
            c.update(state(), '9', 10)
            changed = state()
            if change == 'death': changed['self']['health'] = 0
            elif change == 'map': changed['login']['map'] = 2
            else: changed['session_id'] = 'new'
            c.update(changed, '9', 11)
            self.assertFalse(c.view(11)['usable_for_estimation'])

    def test_new_identity_reacquires_context_after_one_invalidating_update(self):
        c = PositionContext()
        c.update(state(), '9', 10)
        changed = state()
        changed['session_id'] = 'new'
        c.update(changed, '9', 11)
        self.assertFalse(c.view(11)['usable_for_estimation'])
        c.update({'session_id': 'new', 'login': {'map': 1},
                  'self': {'health': 89, 'ghost': False},
                  'event_log': [{'guid': '9', 'sequence': 20,
                                 'movement': {'position_xyzo': [7, 8, 9, 0]}}]}, '9', 21)
        self.assertEqual(c.model.identity, ('new', '9', 1))
        self.assertEqual(c.view(21)['observed']['at'], 21)


if __name__ == '__main__':
    unittest.main()
