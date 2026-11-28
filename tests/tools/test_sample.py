"""견본 활동지(examples/전체-블록-견본)의 수학과 앞 답 유출을 원고에서 직접 검산한다."""
from fractions import Fraction
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_answer import num, plain
from sheet_plan import validate

SAMPLE = ROOT / 'examples/전체-블록-견본/구성.json'


def value(text):
    return Fraction(num(plain(text)))


def blanks(text):
    """`{{㉠|정답;;다른 답}}`의 첫째 정답 목록."""
    return [body.split(';;')[0] for body in re.findall(r'\{\{[^|}]*\|([^}]*)\}\}', text)]


def proportional(f, xs=(1, 2, 3, 4)):
    """x의 값이 2배, 3배, …가 될 때 y의 값도 2배, 3배, …가 되는지."""
    return all(f(k * x) == k * f(x) for x in xs for k in (2, 3))


class SampleMathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(SAMPLE.read_text(encoding='utf-8'))
        cls.items = {item['id']: item for item in cls.data['items']}

    def block(self, item, kind, index=0):
        return [b for b in self.items[item]['blocks'] if b['type'] == kind][index]

    def inner(self, item, kind):
        """split 안까지 포함해 kind 블록을 차례로 모은다."""
        found = []
        for b in self.items[item]['blocks']:
            for part in ([b] if b['type'] != 'split' else b['left'] + b['right']):
                if part['type'] == kind:
                    found.append(part)
        return found

    def test_table_is_y_equals_2x(self):
        table = self.block('q1', 'table')
        xs = [value(c) for c in table['header'][1:5]]
        cells = table['rows'][0][1:5]
        ys = [value(c) if '{{' not in c else value(blanks(c)[0]) for c in cells]
        self.assertEqual(xs, [1, 2, 3, 4])
        self.assertEqual(ys, [2 * x for x in xs])
        solution = next(s for s in self.data['teacher']['solutions'] if s['item'] == 'q1')
        self.assertEqual([(v['given']['x'], v['given']['y']) for v in solution['verify']],
                         list(zip(xs[1:], ys[1:])))
        self.assertFalse([i for i in validate(self.data, SAMPLE.parent) if i.code.startswith('verify-')])

    def test_definition_and_relation(self):
        line = self.block('q2', 'callout')['lines'][0]
        first, term = blanks(line)
        self.assertEqual(plain(first).replace(' ', ''), '2배,3배,4배')
        self.assertEqual(term, '정비례')
        flow = self.inner('q3', 'flow')[0]
        self.assertEqual(value(blanks(flow['nodes'][1])[0]), 2)
        row = self.inner('q3', 'answer')[0]['rows'][0]
        self.assertEqual(plain(row['answer']), 'y=2x')
        self.assertEqual(row.get('check'), 'text')
        self.assertNotIn('accept', row, '곱셈 기호를 생략하지 않은 꼴을 정답으로 받지 않는다')
        self.assertNotIn('grow', row, '한 줄 식 답에는 큰 칸을 주지 않는다')

    def test_ox_judgements(self):
        ox = self.block('q4', 'ox')['items']
        relations = [lambda x: 500 * x, lambda x: 10 - x, lambda x: 4 * x]
        self.assertEqual(len(ox), len(relations))
        for row, f in zip(ox, relations):
            self.assertEqual(row['answer'], 'O' if proportional(f, xs=(1, 2, 3)) else 'X', row['text'])

    def test_natural_number_points_stay_points(self):
        grid = self.inner('q5', 'grid')[0]
        points = grid['plot']['answer']
        self.assertEqual(grid['plot']['mode'], 'points')
        self.assertEqual(points, [[x, 2 * x] for x in (1, 2, 3, 4)])
        for x, y in points:
            self.assertTrue(grid['x'][0] <= x <= grid['x'][1] and grid['y'][0] <= y <= grid['y'][1])
        self.assertGreaterEqual(min(x for x, _ in points), 1, '봉지 수는 자연수다')

    def test_extension_to_all_numbers(self):
        match = self.block('q6', 'match')
        xs = [value(x.replace('x=', '')) for x in match['left']]
        ys = [value(y.replace('y=', '')) for y in match['right']]
        self.assertEqual(ys, [2 * x for x in xs])
        self.assertTrue(any(x < 0 for x in xs) and any(x.denominator != 1 for x in xs))
        grids = self.inner('q7', 'grid')
        points = grids[0]['plot']['answer']
        self.assertEqual(sorted(map(tuple, points)), sorted((float(x), float(y)) for x, y in zip(xs, ys)))
        for x, y in points:
            self.assertEqual(Fraction(x) % Fraction(grids[0]['plot']['snap']), 0)
        self.assertEqual(grids[1]['plot'], {'mode': 'line', 'answer': '2*x', 'snap': 1})

    def test_which_shop_is_proportional(self):
        tables = self.inner('q9', 'table')
        rows = [[value(c) for c in t['rows'][0][1:]] for t in tables]
        xs = [value(c) for c in tables[0]['header'][1:]]
        for ys in rows:
            self.assertEqual({b - a for a, b in zip(ys, ys[1:])}, {3}, '두 가게 모두 3씩 늘어난다')
        verdicts = [all(y == ys[0] * x for x, y in zip(xs, ys)) for ys in rows]
        self.assertEqual(verdicts, [True, False])
        choices = self.block('q9', 'choices')
        self.assertEqual(choices['options'][choices['answer'] - 1], '$A$ 가게')

    def test_explore_excludes_zero_and_prints_both_signs(self):
        explore = self.block('q10', 'explore')
        param = explore['params'][0]
        self.assertIn(0, param['except'])
        self.assertNotIn(param['value'], param['except'])
        signs = {p['a'] > 0 for p in explore['print']}
        self.assertEqual(signs, {True, False})
        self.assertNotIn('비례상수', json.dumps(explore, ensure_ascii=False))
        rows = self.block('q10', 'answer')['rows']

        def quadrants(a):
            names = set()
            for x in (-2, -1, 1, 2):
                y = a * x
                names.add('제1사분면' if x > 0 and y > 0 else '제2사분면' if x < 0 < y else
                          '제3사분면' if x < 0 and y < 0 else '제4사분면')
            return names
        for a in (1, 2, 3):
            self.assertEqual(quadrants(a), {'제1사분면', '제3사분면'})
            self.assertEqual(quadrants(-a), {'제2사분면', '제4사분면'})
        self.assertEqual(rows[0]['answer'], '제1사분면, 제3사분면')
        self.assertEqual(rows[1]['answer'], '제2사분면, 제4사분면')

    def test_later_items_do_not_show_earlier_answers(self):
        """앞에서 구하게 한 관계식·표 값이 뒤 문항의 학생용 글·힌트에 다시 나오지 않는다."""
        order = [item['id'] for item in self.data['items']]
        visible = {}
        for item in self.data['items']:
            text = json.dumps({'blocks': item['blocks'], 'hints': item.get('hints', [])}, ensure_ascii=False)
            text = re.sub(r'\{\{[^}]*\}\}', '', text)  # 빈칸의 정답은 학생에게 보이지 않는다
            text = re.sub(r'"answer":\s*("([^"\\]|\\.)*"|\[[^\]]*\]|\d+)', '', text)
            visible[item['id']] = plain(text).replace(' ', '')
        after_q3 = ''.join(visible[i] for i in order[order.index('q3') + 1:])
        self.assertNotIn('y=2x', after_q3)
        q1_values = [value(blanks(c)[0]) for c in self.block('q1', 'table')['rows'][0] if '{{' in c]
        for table in self.inner('q9', 'table'):
            ys = [value(c) for c in table['rows'][0][1:]]
            self.assertFalse(set(q1_values) <= set(ys), '뒤 자료표가 앞 빈칸의 값을 보여 준다')
        checklist = ' '.join(self.block('q11', 'checklist')['items'])
        self.assertNotIn('원점', checklist)


if __name__ == '__main__':
    unittest.main()
