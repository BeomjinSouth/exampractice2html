import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import sheet_answer as answer


class AnswerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT / 'tests/engine/answer-cases.json').read_text(encoding='utf-8'))

    def test_contract_cases(self):
        self.assertEqual(set(answer.UNITS), set(self.cases['units']))
        for name in ('norm', 'num', 'number_parts', 'text_candidates', 'plain', 'line', 'points'):
            for source, expected in self.cases[name]:
                with self.subTest(kind=name, source=source):
                    self.assertEqual(getattr(answer, name)(source), expected)
        for name in ('inferred_unit', 'number_forms', 'input_mode'):
            for args, expected in self.cases[name]:
                with self.subTest(kind=name, args=args):
                    self.assertEqual(getattr(answer, name)(*args), expected)
        for value, expected in [('11', True), ('11일', True), ('11권', False)]:
            number, unit = answer.number_parts(value)
            self.assertEqual((f'{number}@{unit}' if unit else number) in answer.number_forms(['11'], '일'), expected)
        for salt, kind, value, expected in self.cases['hash']:
            with self.subTest(kind='hash', value=value):
                self.assertEqual(answer.check_hash(salt, kind, value), expected)
        for salt, payload, expected in self.cases['reveal']:
            with self.subTest(kind='reveal'):
                self.assertEqual(answer.reveal(salt, payload), expected)

    def test_thousands_separator_matches_plain_integer(self):
        self.assertEqual(answer.num('6,000'), answer.num('6000'))
        self.assertEqual(answer.number_forms(['6,000']), ['6000'])

    def test_other_kinds_and_snap(self):
        self.assertEqual(answer.canonical('choice', [3, 1]), '1,3')
        self.assertEqual(answer.canonical('ox', '○'), 'o')
        self.assertEqual(answer.canonical('order', ['t2', 't1']), 't2,t1')
        self.assertEqual(answer.canonical('match', {'l1': 'r2', 'l2': 'r1'}), 'l1:r2,l2:r1')
        self.assertEqual(answer.points([[-0.5, 1.5]], 1), '0,2')
        self.assertEqual(answer.bars([0.5, 1.5], 1), '1,2')
        self.assertIsNone(answer.bars([1, None], 1))
        self.assertIsNone(answer.line([[1, 1], [1, 1]]))

    def test_salt_override(self):
        with patch.dict(os.environ, {'SHEET_TEST_SALT': 'test'}):
            self.assertEqual(answer.make_salt(), 'test')
        with patch.dict(os.environ, {}, clear=True):
            self.assertRegex(answer.make_salt(), r'^[0-9a-f]{32}$')
