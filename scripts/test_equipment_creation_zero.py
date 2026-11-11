import unittest
from repair_equipped_once import snapshot


class EquipmentCreationZeroTests(unittest.TestCase):
    def state(self, kind):
        return {'self': {}, 'login': {}, 'event_log': [
            {'guid': '9', 'kind': 2, 'fields': {'354': 42}},
            {'guid': '42', 'kind': kind, 'fields': {'3': 12282, '61': 25}}]}

    def test_create_omitted_durability_is_known_zero(self):
        item = snapshot(self.state(2))['equipped'][0]
        self.assertEqual(item['durability'], 0)
        self.assertTrue(item['current_known'])
        self.assertEqual(item['current_source'], 'creation_zero_default')

    def test_delta_only_omission_remains_unknown(self):
        item = snapshot(self.state(0))['equipped'][0]
        self.assertFalse(item['current_known'])
        self.assertEqual(item['current_source'], 'unknown')


if __name__ == '__main__':
    unittest.main()
