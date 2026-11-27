import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_markup import MarkupError, render_markup


class MarkupTest(unittest.TestCase):
    def test_engine_pairs(self):
        cases = json.loads((ROOT / 'tests/engine/markup-cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len(cases), 12)
        for case in cases:
            with self.subTest(src=case['src']):
                self.assertEqual(render_markup(case['src']), case['html'])

    def test_invalid_markup(self):
        for source in ('$$', '$x^{2$', '$\\unknown{1}$', '$\\mathit{12}$', '$x', '**굵게',
                       '{{㉠|정답', '{{|값|0}}', '{{}}', '두\n줄', '두 줄'):
            with self.subTest(source=source), self.assertRaises(MarkupError):
                render_markup(source)

    def test_plain_length_drives_blank_width(self):
        self.assertIn('min-width:22.5mm', render_markup('{{|**다섯글자야**}}'))

    def test_alternative_answers_show_only_first(self):
        from sheet_markup import inspect_markup
        result = inspect_markup('값은 {{㉠|$+5$;;$5$}}입니다.')
        self.assertIn('<mn>5</mn>', result.html)
        self.assertNotIn(';;', result.html)
        self.assertEqual(result.blanks[0].alternatives, ('$5$',))
        with self.assertRaises(MarkupError):
            render_markup('{{㉠|5;;}}')
