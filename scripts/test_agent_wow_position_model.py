import json
import unittest
from pathlib import Path
from agent_wow_position_model import PositionModel


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.identity = ('session', '9', 1)
        self.model = PositionModel(*self.identity)
        self.model.observe(self.identity, [0, 0, 0], 10, 3, 'login')

    def test_prediction_is_not_confirmation(self):
        self.model.submit(self.identity, [1, 0, 0], 11, 3, 'move1')
        self.assertTrue(self.model.view(12)['usable_for_estimation'])
        self.assertFalse(self.model.view(12)['position_confirmed'])

    def test_cached_observation_does_not_refresh(self):
        self.assertFalse(self.model.observe(self.identity, [0, 0, 0], 20, 3))
        self.assertFalse(self.model.view(20)['usable_for_estimation'])

    def test_correction_replaces_prediction(self):
        self.model.submit(self.identity, [5, 0, 0], 11, 3, 'move1')
        self.model.observe(self.identity, [2, 0, 0], 12, 4, 'correction')
        self.assertTrue(self.model.view(13)['position_confirmed'])
        self.assertEqual(self.model.view(13)['observed']['xyz'], (2, 0, 0))

    def test_identity_change_invalidates(self):
        with self.assertRaises(ValueError):
            self.model.observe(('other', '9', 1), [0, 0, 0], 12, 4)
        self.assertFalse(self.model.view(12)['usable_for_estimation'])

    def test_death_requires_reacquisition(self):
        self.model.invalidate('death')
        self.assertFalse(self.model.view(11)['usable_for_estimation'])
        with self.assertRaises(ValueError):
            self.model.submit(self.identity, [1, 0, 0], 12, 3, 'move')

    def test_expiry_and_bad_values(self):
        self.model.submit(self.identity, [1, 0, 0], 11, 3, 'move')
        self.assertFalse(self.model.view(17)['usable_for_estimation'])
        self.assertFalse(self.model.view(9)['usable_for_estimation'])
        with self.assertRaises(ValueError):
            self.model.observe(self.identity, [float('nan'), 0, 0], 12, 4)

    def test_real_batch_cached_position_cannot_confirm(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / 'results/pure_network/p3_batch01_position_audit.json').read_text())
        c = data['position_confirmation']
        m = PositionModel(*self.identity)
        m.observe(self.identity, c['last_update_position'], 0, c['before_sequence'], 'login')
        m.submit(self.identity, c['submitted_position'], 1, c['before_sequence'], 'approach')
        self.assertFalse(m.observe(self.identity, c['last_update_position'], 2, c['movement_sequence']))
        self.assertFalse(m.view(2)['position_confirmed'])
        self.assertIsNotNone(m.view(2)['predicted'])


if __name__ == '__main__':
    unittest.main()
