import json
import tempfile
import unittest
from pathlib import Path

from validate_document_drift import ALLOWED, audit_candidates, validate


class DriftValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.later = sorted(ALLOWED)[:2]
        for name in (self.source, self.later):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('historical fact\n', encoding='utf-8')
        self.output = self.root / 'candidate.json'
        self.row = {'path': self.source, 'line': 1, 'quote': 'historical fact',
                    'tag': 'historical_status', 'later_path': self.later, 'reason': 'later state differs'}

    def check(self, **changes):
        self.output.write_text(json.dumps({'scope': 'historical documentation; no current game state inferred',
                                           'candidates': [{**self.row, **changes}]}), encoding='utf-8')
        return validate(self.output, self.root)

    def test_exact_source_line(self):
        self.assertEqual(self.check(), 1)
        for changes in ({'line': 2}, {'quote': 'invented'}, {'line': True},
                        {'path': '../secret.txt'}, {'later_path': self.source}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(**changes)

    def test_scope_does_not_prove_semantics(self):
        self.assertEqual(self.check(reason='nonsensical but nonempty'), 1)

    def test_backward_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not later'):
            self.check(path=self.later, later_path=self.source)

    def test_partial_audit_preserves_valid_and_invalid_rows(self):
        self.output.write_text(json.dumps({
            'scope': 'historical documentation; no current game state inferred',
            'candidates': [self.row, {**self.row, 'path': self.later, 'later_path': self.source}],
        }), encoding='utf-8')
        self.assertEqual([row['valid'] for row in audit_candidates(self.output, self.root)],
                         [True, False])
