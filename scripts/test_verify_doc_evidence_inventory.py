import json
import tempfile
import unittest
from pathlib import Path

from verify_doc_evidence_inventory import SOURCES, check


class EvidenceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = sorted(SOURCES)[0]
        path = self.root / self.document
        path.parent.mkdir(parents=True)
        path.write_text('Evidence: example.json\n', encoding='utf-8')
        self.inventory = self.root / 'inventory.json'
        self.ref = {'document': self.document, 'line': 1,
                    'quote': 'example.json', 'basename': 'example.json'}

    def run_check(self, **changes):
        self.inventory.write_text(json.dumps({'references': [{**self.ref, **changes}]}), encoding='utf-8')
        return check(self.inventory, self.root)

    def test_new_documents_are_in_approved_scope(self):
        for number in (76, 80, 81):
            self.assertTrue(any(path.startswith(f'docs/{number}_') for path in SOURCES))

    def test_present_requires_canonical_root_file(self):
        archive = self.root / 'results/pure_network/recovery/example.json'
        archive.parent.mkdir(parents=True)
        archive.write_text('{}', encoding='utf-8')
        self.assertEqual(self.run_check()[0]['status'], 'missing')
        canonical = self.root / 'results/pure_network/example.json'
        canonical.write_text('{}', encoding='utf-8')
        self.assertEqual(self.run_check()[0]['status'], 'present')
        self.assertEqual(len(self.run_check()[0]['sha256']), 64)

    def test_invalid_line_and_path_rejected(self):
        for change in ({'line': 2}, {'quote': 'made up example.json'},
                       {'basename': '../example.json'}, {'line': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_check(**change)
