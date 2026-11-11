"""Replay actual grind loot evidence without altering historical outcomes."""
import json
import unittest
from pathlib import Path
from agent_wow_loot_evidence import loot_confirmed


class GrindLootTests(unittest.TestCase):
    def test_four_archived_loot_records_confirm_general_not_unrelated_quest_item(self):
        folder = Path(__file__).resolve().parents[1] / 'results/pure_network'
        for index in range(1, 5):
            path = folder / ('agent_wow_task_grind10m_actual_20261106_c_%02d_u1.json' % index)
            if not path.exists():
                self.skipTest('local grind archive unavailable')
            record = next(r for r in json.loads(path.read_text(encoding='utf-8'))['run']['records']
                          if r['action_id'] == 'loot')
            with self.subTest(index=index):
                args = (record['observation_before'], record['observation_after'], record['evidence'])
                self.assertEqual(loot_confirmed(*args), index == 1)
                self.assertFalse(loot_confirmed(*args, required_entry=4862))

    def test_inventory_gain_alone_or_unattempted_push_cannot_confirm(self):
        before, after = {'item_totals': {}}, {'item_totals': {'7074': 1}}
        evidence = {'released': {'result': {}}, 'confirmed_item_pushes': [{'entry': 7074, 'count': 1}]}
        self.assertFalse(loot_confirmed(before, after, evidence))
        evidence['attempted_take_entries'] = [7074]
        self.assertTrue(loot_confirmed(before, after, evidence))
        evidence['release_error'] = 'failed'
        self.assertFalse(loot_confirmed(before, after, evidence))


if __name__ == '__main__':
    unittest.main()
