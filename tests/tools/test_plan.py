from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_plan import EXPLORE_NUMBER, function_tree, function_value, load_plan, load_registry, slider_samples, validate


def document(*blocks):
    return {'version': 1, 'meta': {'title': '좌표 읽기', 'kind': '평가', 'columns': 1},
            'items': [{'id': 'q1', 'blocks': list(blocks)}]}


class PlanTest(unittest.TestCase):
    def errors(self, data):
        return {i.path for i in validate(data, ROOT) if i.level == 'error'}

    def test_registry_examples_are_valid(self):
        for kind, entry in load_registry().items():
            with self.subTest(kind=kind):
                data = document(entry['example'])
                if kind == 'checklist':
                    data['meta']['kind'] = '수업'
                # The video example deliberately leaves the teacher's actual address to fill in.
                expected = {'items[0].blocks[0].url'} if kind == 'video' else set()
                self.assertEqual(self.errors(data), expected)

    def test_template_and_bad_fields(self):
        template = json.loads((ROOT / 'assets/templates/구성.json').read_text(encoding='utf-8'))
        self.assertIn('items[0].blocks[0].text', self.errors(template))
        data = document({'type': 'header'}, {'type': 'question'}, {'type': 'text', 'text': 7})
        data['meta']['columns'] = 2
        data['items'].append(deepcopy(data['items'][0]))
        self.assertTrue({'items[1].id', 'items[0].blocks[0].type',
                         'items[0].blocks[1].text', 'items[0].blocks[2].text'} <= self.errors(data))

    def test_markup_and_image_errors(self):
        for source in ('수식 $x^{$', '두\n줄', '답: ___', 'https://example.test', '{{㉠}}', '{{㉡|값}}'):
            with self.subTest(source=source):
                self.assertIn('items[0].blocks[0].text', self.errors(document({'type': 'text', 'text': source})))
        for src in ('그림/없는.png', 'https://example.test/그림.png', '../밖.png'):
            self.assertIn('items[0].blocks[0].src', self.errors(document(
                {'type': 'figure', 'src': src, 'width_mm': 10, 'alt': '그림'})))
        self.assertIn('items[0].blocks[0].svg', self.errors(document(
            {'type': 'figure', 'svg': '<svg><image href="https://example.test/그림.png"/></svg>', 'width_mm': 10})))

    def test_answer_contract(self):
        data = document({'type': 'answer', 'rows': [{'label': '풀이', 'lines': 1, 'points': 2},
                                                     {'label': '작도', 'kind': 'draw', 'answer': '원'}]},
                        {'type': 'choices', 'options': ['가', '나']},
                        {'type': 'steps', 'columns': ['결과'], 'rows': 1, 'answers': [['']]},
                        {'type': 'ask', 'tag': '질문', 'text': '값을 쓰시오.', 'lines': 31},
                        {'type': 'table', 'header': ['값'], 'rows': [['{{㉠}}']]})
        data['meta']['total_points'] = 3
        self.assertTrue({'meta.total_points', 'items[0].blocks[0].rows[0].lines',
                         'items[0].blocks[0].rows[0].answer', 'items[0].blocks[0].rows[1].height_mm',
                         'items[0].blocks[1].answer', 'items[0].blocks[2].answers[0][0]',
                         'items[0].blocks[3].lines', 'items[0].blocks[3].answer',
                         'items[0].blocks[4].rows[0][0]'} <= self.errors(data))

    def test_group_points_and_marks(self):
        data = document({'type': 'situation', 'lines': ['값은 {{㉠|$2$}}입니다.']},
                        {'type': 'question', 'text': '다른 값은 {{㉡|$3$}}입니다.'},
                        {'type': 'answer', 'rows': [{'group': 'a', 'label': '풀이', 'mark': '㉠', 'points': 2, 'answer': '풀이'},
                                                     {'group': 'a', 'points': 9, 'answer': '결과'}]})
        data['meta']['total_points'] = 2
        self.assertEqual(self.errors(data), set())

    def test_grid_expression_and_point(self):
        block = {'type': 'grid', 'mode': 'coord', 'x': [-5, 5], 'y': [-5, 5], 'unit_mm': 6,
                 'draw': [{'fn': '-(x**2)/2+3*x'}, {'points': [[1, 2.5000000005]], 'on': 0}]}
        self.assertEqual(self.errors(document(block)), set())
        block['draw'][1]['points'][0][1] = 2.5001
        self.assertIn('items[0].blocks[0].draw[1].points[0]', self.errors(document(block)))
        for invalid in ('f(x)', 'x//2', 'x%2', 'x.y', 'y', 'True'):
            block['draw'][0]['fn'] = invalid
            with self.subTest(invalid=invalid):
                self.assertIn('items[0].blocks[0].draw[0].fn', self.errors(document(block)))

    def test_interaction_validation(self):
        data = document({'type': 'split', 'left': [{'type': 'split', 'left': [{'type': 'text', 'text': '가'}],
                                                  'right': [{'type': 'text', 'text': '나'}]}],
                         'right': [{'type': 'text', 'text': '다'}]},
                        {'type': 'checklist', 'items': ['확인']},
                        {'type': 'numberline', 'range': [0, 4], 'step': 1, 'plot': {'answer': [5, 0.5], 'snap': 1}},
                        {'type': 'grid', 'mode': 'coord', 'x': [-3, 3], 'y': [-3, 3], 'unit_mm': 4,
                         'plot': {'mode': 'line', 'answer': 'x**2'}},
                        {'type': 'chart', 'kind': 'bar', 'labels': ['가', '나'], 'values': [1]},
                        {'type': 'video', 'url': 'https://youtube.com.evil.test/watch?v=abcdefghijk', 'title': '영상'},
                        {'type': 'explore', 'fn': '__import__(x)', 'x': [-2, 2], 'y': [-2, 2], 'unit_mm': 5,
                         'params': [{'name': 'x', 'min': 0, 'max': 2, 'step': 1, 'value': 1}]})
        data['meta'].update(columns=2, interactive='true', reveal='틀림', theme='틀림', accent='틀림')
        data['items'][0]['hints'] = ['가', '나', '다', '라']
        errors = self.errors(data)
        self.assertTrue({'items[0].blocks[0]', 'items[0].blocks[0].left[0]', 'items[0].blocks[1]',
                         'items[0].blocks[2].plot.answer[0]', 'items[0].blocks[2].plot.answer[1]',
                         'items[0].blocks[3].plot.answer', 'items[0].blocks[4].labels',
                         'items[0].blocks[5].url', 'items[0].blocks[6].fn', 'items[0].blocks[6].params[0].name',
                         'meta.interactive', 'meta.reveal', 'meta.theme', 'meta.accent', 'items[0].hints'} <= errors)

    def test_line_function_snap_and_malformed_ranges(self):
        from sheet_plan import function_tree, line_grid_points
        for expression, snap, expected in [('2*x+1', 1, [[-3, -5], [2, 5]]),
                                            ('x/2+0.5', 0.5, [[-3, -1], [3, 2]])]:
            with self.subTest(expression=expression):
                self.assertEqual(line_grid_points(function_tree(expression), [-3, 3], [-6, 6], snap), expected)
        self.assertEqual(len(line_grid_points(function_tree('2*x'), [-3, 3], [-6, 6], 1e-9)), 2)
        block = {'type': 'grid', 'mode': 'coord', 'x': ['가', 3], 'y': [-3, 3], 'unit_mm': 3,
                 'plot': {'mode': 'points', 'answer': [[0, 0]]}}
        self.assertIn('items[0].blocks[0].x', self.errors(document(block)))
        block['x'] = [-1, 1]
        block['plot'] = {'mode': 'line', 'answer': 'x**3'}
        self.assertIn('items[0].blocks[0].plot.answer', self.errors(document(block)))

    def test_accept_and_teacher_video(self):
        data = document({'type': 'answer', 'rows': [{'label': '값', 'answer': '$x=2$', 'accept': ['2']}]})
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '2', 'steps': ['계산한다.'],
                                           'video': {'url': 'https://youtu.be/abcdefghijk', 'start': 2}}]}
        self.assertEqual(self.errors(data), set())
        self.assertTrue(any('accept' in issue.hint for issue in validate(data, ROOT)))
        data['teacher']['solutions'][0]['video']['url'] = 'https://bad.test/abcdefghijk'
        self.assertIn('teacher.solutions[0].video.url', self.errors(data))

    def test_explore_exclusions_and_print_values(self):
        block = {'type': 'explore', 'fn': 'a*x+b', 'x': [-2, 2], 'y': [-3, 3], 'unit_mm': 5,
                 'params': [{'name': 'a', 'min': -2, 'max': 2, 'step': .5, 'value': 1, 'except': [0]},
                            {'name': 'b', 'min': -1, 'max': 1, 'step': 1, 'value': 0}],
                 'print': [{'a': 2}, {'a': -2, 'b': 1}]}
        block['params'][0]['label'] = '$a$의 값'
        self.assertEqual(self.errors(document(block)), set())
        for updates, field in [({'label': '$a^{$의 값'}, 'label'), ({'label': '{{|값}}'}, 'label'),
                               ({'value': 0}, 'value'), ({'except': [3]}, 'except[0]'),
                               ({'except': [False]}, 'except[0]'), ({'except': 0}, 'except')]:
            with self.subTest(updates=updates):
                invalid = deepcopy(block)
                invalid['params'][0].update(updates)
                self.assertIn('items[0].blocks[0].params[0].' + field, self.errors(document(invalid)))
        for snapshots, field in [([{'c': 1}], '[0].c'), ([{'a': 3}], '[0].a'),
                                 ([{'a': 0}], '[0].a'), ([{'a': True}], '[0].a'),
                                 ([{'a': 2}] * 5, ''), ([], ''), ([{}], '[0]')]:
            with self.subTest(snapshots=snapshots):
                self.assertIn('items[0].blocks[0].print' + field,
                              self.errors(document({**block, 'print': snapshots})))

    def test_explore_numeric_grammar_and_precedence(self):
        block = deepcopy(load_registry()['explore']['example'])
        path = 'items[0].blocks[0].fn'
        for expression, expected in [('00.5*a*x + 1.', 2.5), (' .5 * (a + x) ', 2),
                                     ('-x**2 + (-x)**2', 0), ('2**-2**2', .0625),
                                     ('x--a+8/2/2', 6), (' a * x﻿', 3),
                                     ('(' * 255 + 'x' + ')' * 255, 3)]:
            with self.subTest(expression=expression):
                self.assertEqual(self.errors(document({**block, 'fn': expression})), set())
                self.assertEqual(function_value(function_tree(expression, {'a'}), 3, {'a': 1}), expected)
        for expression in ('1e-2*a*x', '1_000*a*x', '0x10*a*x', '2x', '+x', 'x++a', 'x//2',
                           'a**', 'x* *a', 'ax', 'A*x', 'b*x', '１*x', 'x\x85+a', '()', 'x(x)', '(x', 'x)'):
            with self.subTest(expression=expression):
                issues = validate(document({**block, 'fn': expression}), ROOT)
                self.assertTrue(any(i.level == 'error' and i.path == path and i.code == 'explore-expression'
                                    for i in issues), issues)

    def test_explore_number_pattern_matches_engine_source(self):
        source = (ROOT / 'assets/engine/solve.js').read_text(encoding='utf-8')
        parser = source.split('function parseExpression(', 1)[1].split('\n  }', 1)[0]
        match = re.search(r'consume\(/\^\\s\*\(([^/]+)\)/\)', parser.split('const numeral', 1)[1])
        self.assertIsNotNone(match, 'solve.js 탐구 숫자 토큰을 찾지 못했습니다.')
        self.assertEqual(match[1], EXPLORE_NUMBER.pattern)
        self.assertTrue(EXPLORE_NUMBER.flags & re.ASCII)

    def test_checklist_print_is_boolean(self):
        data = document({'type': 'checklist', 'items': ['확인'], 'print': True})
        data['meta']['kind'] = '수업'
        self.assertEqual(self.errors(data), set())
        data['items'][0]['blocks'][0]['print'] = 'true'
        self.assertEqual(self.errors(data), {'items[0].blocks[0].print'})

    def test_grow_warns_only_for_short_answer_rows(self):
        rows = [{'label': '답', 'answer': answer, 'grow': grow} for answer, grow in
                [('가' * 20, True), ('가' * 21, True), ('원점', False), ('가\n나', True)]]
        warnings = [i for i in validate(document({'type': 'answer', 'rows': rows}), ROOT)
                    if i.level == 'warning' and '쪽 채움' in i.message]
        self.assertEqual([(i.path, i.hint) for i in warnings],
                         [('items[0].blocks[0].rows[0].grow', 'grow를 빼고 lines로 정하세요.')])

    def test_grade_terms_student_text_and_private_answers(self):
        block = {'type': 'question', 'text': '기울기와 $x$절편을 알아봅시다.'}
        data = document(block, {'type': 'text', 'text': '소인수분해와 {{|기울기}}'},
                        {'type': 'answer', 'rows': [{'label': '답', 'answer': '기울기'}]})
        data['meta']['grade'] = '중학교 1학년'
        data['teacher'] = {'memo': ['기울기'], 'solutions': [{'item': 'q1', 'answer': '기울기', 'steps': ['기울기']}]}
        terms = [i for i in validate(data, ROOT) if i.code == 'term-beyond-grade']
        self.assertEqual(len(terms), 2)
        self.assertTrue(all(i.path == 'items[0].blocks[0].text' for i in terms))
        self.assertTrue(any('x절편' in i.message for i in terms))
        for grade in ('중2', '2학년', '중학교 2학년', '고등학교 1학년', '미상'):
            with self.subTest(grade=grade):
                data['meta']['grade'] = grade
                self.assertFalse([i for i in validate(data, ROOT) if i.code == 'term-beyond-grade'])

    def test_incenter_term_uses_local_geometry_context(self):
        fields = ['삼각형의 내심을 찾으시오.', '내접원을 그리시오.', '접선을 이용해 내심을 찾으시오.',
                  '$\\triangle ABC$의 내심을 찾으시오.', '내심 기대했다.',
                  '삼각형을 그렸다. 내심 기대했다.', '학원에서 내심 기대했다.',
                  '내심 기대했다. 참가비는 $10$원이다.', '$100$원을 받을까 내심 기대했다.']
        data = document(*({'type': 'text', 'text': field} for field in fields))
        data['meta'].update(grade='중1', unit='삼각형')
        paths = {i.path for i in validate(data, ROOT) if i.code == 'term-beyond-grade'}
        self.assertEqual(paths, {f'items[0].blocks[{i}].text' for i in range(4)})
        data['meta']['grade'] = '중2'
        self.assertFalse(any(i.code == 'term-beyond-grade' for i in validate(data, ROOT)))

    def test_grade_terms_in_public_solution_fields_only(self):
        data = document({'type': 'question', 'text': '식으로 나타내시오.'})
        data['meta'].update(grade='중1', kind='수업')
        text = '연립일차방정식으로 풀어 봅시다.'
        data['teacher'] = {'memo': [text], 'solutions': [{'item': 'q1', 'answer': text, 'steps': [text],
                           'rubric': [text], 'alternatives': [text], 'misconceptions': [text]}]}
        public = {'teacher.solutions[0].' + name for name in ('answer', 'steps[0]', 'rubric[0]')}
        for interactive, policy, expected in [(None, None, public), (True, '시도 후', public), (True, '바로', public),
                                              (True, '교사만', set()), (True, '없음', set()), (False, '바로', set())]:
            with self.subTest(interactive=interactive, policy=policy):
                for key, value in [('interactive', interactive), ('reveal', policy)]:
                    if value is not None:
                        data['meta'][key] = value
                    else:
                        data['meta'].pop(key, None)
                self.assertEqual({i.path for i in validate(data, ROOT) if i.code == 'term-beyond-grade'}, expected)

    def test_grade_terms_nested_fields_and_teacher_graph(self):
        data = document({'type': 'split', 'left': [{'type': 'explore', 'prompt': '기울기', 'fn': 'a*x',
                    'x': [-1, 1], 'y': [-1, 1], 'unit_mm': 4,
                    'params': [{'name': 'a', 'label': '기울기', 'min': 0, 'max': 2, 'step': 1, 'value': 1}]}],
                    'right': [{'type': 'grid', 'mode': 'coord', 'x': [-1, 1], 'y': [-1, 1], 'unit_mm': 4,
                    'draw': [{'points': [[0, 0]], 'names': ['기울기']},
                             {'points': [[1, 1]], 'names': ['기울기'], 'teacher': True}]}]})
        data['meta']['grade'] = '1학년'
        paths = [i.path for i in validate(data, ROOT) if i.code == 'term-beyond-grade']
        self.assertEqual(len(paths), 3, paths)
        self.assertTrue(any('params[0].label' in p for p in paths))
        self.assertTrue(any('draw[0].names[0]' in p for p in paths))
        self.assertFalse(any('draw[1]' in p for p in paths))

    def test_grade_terms_across_student_facing_blocks(self):
        blocks = [
            {'type': 'notice', 'label': '기울기', 'lines': ['기울기']},
            {'type': 'situation', 'title': '기울기', 'lines': ['기울기'], 'note': '기울기'},
            {'type': 'callout', 'kind': '개념', 'title': '기울기', 'lines': ['기울기']},
            {'type': 'table', 'header': ['기울기'], 'rows': [['기울기']]},
            {'type': 'choices', 'options': ['기울기', '보기'], 'answer': 1},
            {'type': 'ox', 'items': [{'text': '기울기', 'answer': 'O'}]},
            {'type': 'match', 'left': ['기울기', '가'], 'right': ['나', '다']},
            {'type': 'order', 'items': ['기울기', '가']},
            {'type': 'ask', 'tag': '질문', 'text': '기울기', 'lines': 2, 'answer': '기울기'},
            {'type': 'chart', 'kind': 'bar', 'labels': ['기울기'], 'values': [1], 'x_label': '기울기'},
            {'type': 'checklist', 'items': ['기울기']},
        ]
        data = document(*blocks)
        data['meta'].update(grade='중1', kind='수업')
        data['items'][0]['hints'] = ['기울기']
        terms = [i for i in validate(data, ROOT) if i.code == 'term-beyond-grade']
        self.assertEqual(len(terms), 18, [(i.path, i.message) for i in terms])
        self.assertFalse(any(i.path.endswith('.answer') for i in terms))
        self.assertTrue(any(i.path.endswith('.hints[0]') for i in terms))

    def test_histogram_polygon_frequency_validation(self):
        for kind in ('histogram', 'polygon'):
            with self.subTest(kind=kind):
                block = {'type': 'chart', 'kind': kind, 'bins': [0, 1, 3], 'values': [-1.5, 2.5],
                         'plot': {'answer': [-2, 1.5]}}
                issues = validate(document(block), ROOT)
                self.assertTrue(any(i.code == 'unequal-bin-width' for i in issues))
                self.assertTrue(any(i.level == 'error' and i.path.endswith('values[0]') for i in issues))
                self.assertTrue(any(i.level == 'error' and i.path.endswith('plot.answer[0]') for i in issues))
                self.assertEqual(sum(i.code == 'frequency-not-integer' for i in issues), 3)
                for label in ('상대도수', '비율'):
                    block['y_label'] = label
                    issues = validate(document(block), ROOT)
                    self.assertFalse(any(i.code == 'frequency-not-integer' for i in issues))
                    self.assertTrue(any(i.level == 'error' and i.path.endswith('values[0]') for i in issues))
                block.update(bins=[0, .1, .2], values=[1, 2], plot={'answer': [1, 2]})
                self.assertFalse(any(i.code == 'unequal-bin-width' for i in validate(document(block), ROOT)))

    def test_verify_substitutes_every_given_value(self):
        data = document({'type': 'question', 'text': '상자의 수를 구하시오.'})
        solution = {'item': 'q1', 'answer': '작은 상자와 큰 상자', 'steps': ['식에 대입한다.'],
                    'verify': [{'given': {'x': 11.5, 'y': 13.5},
                                'holds': ['x+y=25', '4*x+6*y=127']} ]}
        data['teacher'] = {'solutions': [solution]}
        self.assertEqual(self.errors(data), set())
        solution['verify'][0]['given'] = {'x': 13.5, 'y': 11.5}
        issues = [i for i in validate(data, ROOT) if i.code == 'verify-failed']
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].path, 'teacher.solutions[0].verify[0].holds[1]')
        self.assertIn('123', issues[0].message)
        self.assertIn('127', issues[0].message)

    def test_verify_rejects_bad_syntax_and_undefined_values(self):
        data = document({'type': 'question', 'text': '계산하시오.'})
        solution = {'item': 'q1', 'answer': '답', 'steps': ['계산한다.']}
        data['teacher'] = {'solutions': [solution]}
        for equation, given in [('2x=4', {'x': 2}), ('x==2', {'x': 2}),
                                ('x+y=3', {'x': 2}), ('x/0=2', {'x': 2}),
                                ('__import__(x)=2', {'x': 2}), ('x^2=4', {'x': 2})]:
            with self.subTest(equation=equation):
                solution['verify'] = [{'given': given, 'holds': [equation]}]
                self.assertTrue(any(i.code == 'verify-expression' and i.level == 'error'
                                    for i in validate(data, ROOT)))
        for given in (None, {'x': True}, {'xy': 2}, {'x': float('inf')}):
            with self.subTest(given=given):
                solution['verify'] = [{'given': given, 'holds': ['x=2']}]
                self.assertTrue(any(i.code == 'verify-expression' for i in validate(data, ROOT)))
        solution['verify'] = [{'given': {'a': 0.1, 'b': 0.2, 'z': -2},
                               'holds': ['a+b=0.3', 'z**2=4', '1/2=0.5']}]
        self.assertEqual(self.errors(data), set())
        solution['verify'] = [{'given': {'x': 10**20}, 'holds': ['x+1=x']}]
        self.assertTrue(any(i.code == 'verify-failed' for i in validate(data, ROOT)))
        solution['verify'] = [{'given': {'x': 2}, 'holds': ['x**0.5=2']}]
        self.assertTrue(any(i.code == 'verify-expression' for i in validate(data, ROOT)))
        solution['verify'] = []
        self.assertIn('teacher.solutions[0].verify', self.errors(data))

    def test_verify_is_exact_unless_tolerance_is_explicit(self):
        data = document({'type': 'question', 'text': '값을 구하시오.'})
        check = {'given': {'x': 0}, 'holds': ['x=1/1000000000']}
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '답', 'steps': ['풀이'], 'verify': [check]}]}
        self.assertTrue(any(i.code == 'verify-failed' for i in validate(data, ROOT)))
        for tolerance, expected in [(0, True), (0.0000000009, True), (0.000000001, False), (0.000000002, False)]:
            with self.subTest(tolerance=tolerance):
                check['tolerance'] = tolerance
                self.assertEqual(bool(self.errors(data)), expected)
        for invalid in (False, -0.001, '0.001', None, float('nan')):
            with self.subTest(invalid=invalid):
                check['tolerance'] = invalid
                self.assertEqual(self.errors(data), {'teacher.solutions[0].verify[0].tolerance'})

    def test_verify_reads_decimal_literals_without_float_rounding(self):
        from unittest.mock import patch
        data = document({'type': 'question', 'text': '값을 구하시오.'})
        check = {'given': {'x': 0.1}, 'holds': ['x=0.1000000000000000001']}
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '답', 'steps': ['풀이'], 'verify': [check]}]}
        self.assertTrue(any(i.code == 'verify-failed' for i in validate(data, ROOT)))
        raw = json.dumps(data).replace('"x": 0.1', '"x": 0.1000000000000000001')
        with patch.object(Path, 'read_bytes', return_value=raw.encode('utf-8')):
            parsed, _, issues = load_plan(ROOT)
        self.assertEqual(parsed['teacher']['solutions'][0]['verify'][0]['given']['x'], 0.1)
        self.assertFalse(any(i.level == 'error' for i in issues), issues)

    def test_build_keeps_verify_out_of_teacher_and_student_content(self):
        from sheet_build import Renderer, document_html, strip_teacher
        data = document({'type': 'question', 'text': '수를 구하시오.'})
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '$2$', 'steps': ['계산한다.'],
                                          'verify': [{'given': {'x': 2}, 'holds': ['9*x=18']}]}]}
        html = document_html(Renderer(data, ROOT, []), {}, '')
        self.assertNotIn('9*x=18', html)
        self.assertNotIn('9*x=18', strip_teacher(html))

    def test_student_solution_payload_matches_documented_reveal_policies(self):
        from sheet_build import Renderer, document_html, strip_teacher
        data = document({'type': 'question', 'text': '수를 구하시오.'})
        data['teacher'] = {'memo': ['배부 지침'], 'solutions': [
            {'item': 'q1', 'answer': '정답 두 개', 'steps': ['각각 계산한다.'], 'rubric': ['관계를 바르게 적는다.'],
             'alternatives': ['교사용 대안'], 'misconceptions': ['교사용 오개념']}]}
        for interactive, policy, public in [(True, '시도 후', True), (True, '바로', True), (True, '교사만', False),
                                            (True, '없음', False), (False, '바로', False)]:
            with self.subTest(policy=policy, interactive=interactive):
                data['meta'].update(interactive=interactive, reveal=policy)
                renderer = Renderer(data, ROOT, [])
                html = strip_teacher(document_html(renderer, {}, ''))
                self.assertEqual('id="sheet-reveal"' in html, public)
                payload = renderer.reveal_data['items']
                if public:
                    self.assertIn('정답 두 개', payload['q1']['solution_html'])
                    self.assertIn('각각 계산한다.', payload['q1']['solution_html'])
                    self.assertEqual(payload['q1']['rubric'], ['관계를 바르게 적는다.'])
                else:
                    self.assertEqual(payload, {})
                for text in ('정답 두 개', '각각 계산한다.', '관계를 바르게 적는다.', '배부 지침', '교사용 대안', '교사용 오개념'):
                    self.assertNotIn(text, html)
                for text in ('배부 지침', '교사용 대안', '교사용 오개념'):
                    self.assertNotIn(text, json.dumps(payload, ensure_ascii=False))

    def test_assessment_rubric_and_scored_text_rows(self):
        data = document({'type': 'question', 'text': '풀이를 쓰시오.'},
                        {'type': 'answer', 'rows': [{'label': '풀이와 답', 'points': 5, 'answer': '예시'}]})
        solution = {'item': 'q1', 'answer': '답', 'steps': ['풀이']}
        data['teacher'] = {'solutions': [solution]}
        codes = lambda: {i.code for i in validate(data, ROOT)}
        self.assertIn('rubric-missing', codes())
        solution['rubric'] = ['미지수 1점', '식 2점', '풀이 1점', '확인 1점']
        self.assertIn('rubric-coverage', codes())
        rows = data['items'][0]['blocks'][1]['rows']
        rows[0].update(label='미지수와 식', points=3)
        rows.append({'label': '풀이와 확인', 'points': 2, 'answer': '예시'})
        self.assertNotIn('rubric-coverage', codes())
        self.assertNotIn('rubric-missing', codes())
        data['teacher']['solutions'] = []
        self.assertIn('rubric-missing', codes())
        data['meta']['kind'] = '수업'
        self.assertNotIn('rubric-missing', codes())

    def test_assessment_grouped_rows_in_split_are_one_scored_label(self):
        data = document({'type': 'split',
                         'left': [{'type': 'answer', 'rows': [
                             {'group': 'a', 'label': '풀이와 답', 'points': 5, 'answer': '풀이'},
                             {'group': 'a', 'answer': '답'}]}],
                         'right': [{'type': 'text', 'text': '자료'}]})
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '답', 'steps': ['풀이'],
                                           'rubric': ['식 2점', '계산 3점']}]}
        self.assertTrue(any(i.code == 'rubric-coverage' for i in validate(data, ROOT)))

    def test_quadratic_textbook_expressions_respect_context_and_visibility(self):
        fields = ['이차함수 그래프가 열리는 방향을 쓰시오.', '포물선은 아래로 열린다.',
                  '$y=0.5x^{2}$의 그래프는 위로 열린다.', '포물선을 살펴본다. 위로 열리는 뚜껑을 찾아본다.',
                  '위로 열리는 뚜껑을 본다.', '포물선은 {{|위로 열린다}}.', '{{|포물선}}은 위로 열린다.',
                  '$y=x^2$의 그래프는 위로 열린다. 빈칸 {{|답}}을 써 본다.']
        data = document(*({'type': 'text', 'text': field} for field in fields))
        data['meta'].update(title='이차함수 그래프', kind='수업')
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '아래로 열린다.',
                                           'steps': ['포물선은 아래로 열리는 모양이다.']}]}
        paths = {i.path for i in validate(data, ROOT) if i.code == 'textbook-expression'}
        self.assertEqual(paths, {*(f'items[0].blocks[{i}].text' for i in (0, 1, 2, 7)),
                                 'teacher.solutions[0].steps[0]'})

    def test_denominator_rule_and_deliberately_false_ox(self):
        wrong = '분모를 이항한다.'
        data = document({'type': 'text', 'text': wrong}, {'type': 'ox', 'items': [
            {'text': wrong, 'answer': 'X'}, {'text': wrong, 'answer': 'O'},
            {'text': '분모의 최소공배수를 양변에 곱한다.', 'answer': 'O'}]},
            {'type': 'choices', 'options': [wrong, '양변에 같은 수를 곱한다.'], 'answer': 2})
        paths = {i.path for i in validate(data, ROOT) if i.code == 'textbook-expression'}
        self.assertEqual(paths, {'items[0].blocks[0].text', 'items[0].blocks[1].items[1].text',
                                 'items[0].blocks[2].options[0]'})

    def test_arc_expression_uses_unit_or_title_and_whole_words(self):
        examples = ('호를 그린다.', '두 호가 만난다.', '두 호와 직선을 그린다.', '두 호의 교점을 찾는다.',
                    '이 호는 같은 원의 일부다.', '다른 호도 그린다.', '짧은 호로 나타낸다.', '호 $AB$를 그린다.')
        controls = ('1호, 호수, 기호, 번호, 괄호, 호도법을 살펴본다.', '컴퍼스로 그린 두 원의 교점을 찾는다.',
                    '호를 쓰지 말고 원의 일부라고 쓴다.')
        data = document(*({'type': 'text', 'text': text} for text in (*examples, *controls)))
        data['meta'].update(grade='중1', interactive=True, reveal='바로')
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '두 호의 교점이다.',
                                           'steps': ['두 호가 만나는 점을 잡는다.']}]}
        expected_paths = {*(f'items[0].blocks[{i}].text' for i in range(len(examples))),
                          'teacher.solutions[0].answer', 'teacher.solutions[0].steps[0]'}
        for unit, title, expected in [('작도와 합동', '도형 탐구', expected_paths),
                                      ('', '삼각형의 작도', expected_paths),
                                      ('기본 도형', '도형 탐구', expected_paths),
                                      ('삼각형의 합동', '도형 탐구', expected_paths),
                                      ('원과 부채꼴', '호의 길이', set()),
                                      ('원과 부채꼴', '부채꼴의 작도', set()),
                                      ('평면도형', '호의 길이', set())]:
            with self.subTest(unit=unit, title=title):
                data['meta'].update(unit=unit, title=title)
                self.assertEqual({i.path for i in validate(data, ROOT) if i.code == 'textbook-expression'}, expected)

    def test_circular_arc_has_one_unit_specific_recommendation(self):
        examples = ('원호를 남겨 봅시다.', '첫 원호 위에 점을 찍는다.', '이 원호만으로 점이 정해질까?',
                    '원호를 그려 반직선과 만나게 한다.', '두 원호가 만나는 점을 찾는다.', '다른 원호와의 교점을 찾는다.',
                    '그린 부분을 원호라고 부른다.')
        controls = ('타원호를 그린다.', '원호형 무늬를 살펴본다.', '원호라는 표현을 쓰지 않는다.')
        data = document(*({'type': 'text', 'text': text} for text in (*examples, *controls)))
        data['meta']['grade'] = '중1'
        for unit, title, construction in (('작도와 합동', '길이 옮기기', True), ('기본 도형', '도형 탐구', True),
                                          ('', '삼각형의 합동', True), ('원과 부채꼴', '부채꼴의 작도', False),
                                          ('평면도형', '둘레를 구하는 방법', False), ('', '도형 탐구', False)):
            with self.subTest(unit=unit, title=title):
                data['meta'].update(unit=unit, title=title)
                warnings = [i for i in validate(data, ROOT) if i.code == 'textbook-expression']
                self.assertEqual([i.path for i in warnings], [f'items[0].blocks[{i}].text' for i in range(len(examples))])
                for warning in warnings:
                    self.assertIn('‘원호’', warning.message)
                    self.assertIn('컴퍼스로 그린 원의 흔적을 남긴다' if construction else '‘호’', warning.hint)
                    self.assertNotIn('틀린 말', warning.hint)

    def test_radius_and_diameter_expression_only_suggests_length_for_values(self):
        examples = ('반지름이 $8$ cm이고 중심각의 크기가 같다.', '지름은 3.5cm이다.',
                    '반지름은 $r$이다.', r'지름이 $\sqrt{3}$인 원을 그린다.', '반지름이 $a+1$인 원을 그린다.',
                    r'지름이 $\frac{1}{2}$인 원을 그린다.', '반지름이 $R$일 때 넓이를 구한다.',
                    r'반지름이 $8\text{ cm}$이고 중심각의 크기가 같다.', '지름이 $8$이다. 빈칸 {{|답}}을 채운다.',
                    '반지름이 $r$보다 큰 원을 그린다.', '지름이 1,000mm인 원을 그린다.', '지름이 $8$ 이상이다.')
        controls = ('반지름 $OA$', '반지름을 그린다.', '반지름이 접선과 수직이다.', '반지름이 같은 두 원을 그린다.',
                    '반지름의 길이가 $8$ cm이다.', '반지름이 $OA$와 겹친다.', '반지름이 2개 표시되어 있다.',
                    '지름이 {{|$8$}} cm이다.')
        data = document(*({'type': 'text', 'text': text} for text in (*examples, *controls)))
        data['meta'].update(grade='중1', unit='원과 부채꼴')
        warnings = [i for i in validate(data, ROOT) if i.code == 'textbook-expression']
        self.assertEqual([i.path for i in warnings], [f'items[0].blocks[{i}].text' for i in range(len(examples))])
        for example, warning in zip(examples, warnings):
            term = '반지름' if example.startswith('반지름') else '지름'
            self.assertIn(f'‘{term}의 길이가’', warning.hint)

    def test_variance_uses_statistical_context_in_the_same_sentence(self):
        fields = ('자료의 분산을 구하시오.', '분산과 평균을 비교하시오.', '편차의 제곱으로 분산을 알아보자.',
                  '분산을 구해 봅시다.', '분산 투자로 위험을 줄인다.', '평균 수익률과 분산 투자를 비교한다.',
                  '자료 분산 처리 방법을 찾는다.', '자료를 모았다. 분산하자.')
        data = document(*({'type': 'text', 'text': text} for text in fields))
        for grade, expected in [('중1', range(4)), ('중2', range(4)), ('중3', ())]:
            with self.subTest(grade=grade):
                data['meta']['grade'] = grade
                warnings = {i.path for i in validate(data, ROOT) if i.code == 'term-beyond-grade'}
                self.assertEqual(warnings, {f'items[0].blocks[{i}].text' for i in expected})

    def test_variance_uses_statistics_unit_or_title_and_ignores_everyday_verbs(self):
        fields = ('분산은 얼마인가요?', '자료를 분산하여 보관한다.', '자료를 분산해 보관한다.', '분산한 자료를 읽는다.',
                  '자료를 분산시키는 방법을 고른다.', '자료를 분산시켜 보관한다.', '분산된 자료를 살펴본다.',
                  '자료가 분산되었다.', '분산될 자료를 고른다.', '자료를 분산할 방법을 찾는다.',
                  '분산 투자와 분산처리를 비교한다.', '자료를 분산 배치한다.', '자료를 분산했다. 분산은 얼마인가요?')
        data = document(*({'type': 'text', 'text': text} for text in fields))
        expected = {'items[0].blocks[0].text', 'items[0].blocks[12].text'}
        for grade, unit, title, paths in (('중1', '자료의 정리', '활동', expected), ('중1', '', '대푯값 알아보기', expected),
                                           ('중2', '도수분포표', '활동', expected), ('중1', '통계', '활동', expected),
                                           ('중1', '산포도', '활동', expected | {'meta.unit'}),
                                           ('중3', '', '산포도 살펴보기', set()),
                                           ('중1', '도형', '활동', set())):
            with self.subTest(grade=grade, unit=unit, title=title):
                data['meta'].update(grade=grade, unit=unit, title=title)
                self.assertEqual({i.path for i in validate(data, ROOT) if i.code == 'term-beyond-grade'}, paths)

    def test_terms_ignore_unrelated_compounds_but_keep_math_compounds(self):
        controls = ('성적분포를 비교한다.', '누적분포를 살핀다.', '미분양과 미분류 자료를 살핀다.', '소인수분해를 한다.')
        examples = ('함수의 미분법을 살핀다.', '정적분과 부정적분을 구한다.', '함수의 적분을 구한다.',
                    '미분 양쪽의 결과를 비교한다.', '적분 포스터를 만든다.', '인수분해를 한다.')
        data = document(*({'type': 'text', 'text': text} for text in (*controls, *examples)))
        data['meta']['grade'] = '중2'
        warnings = {i.path for i in validate(data, ROOT) if i.code == 'term-beyond-grade'}
        self.assertEqual(warnings, {f'items[0].blocks[{i}].text' for i in range(len(controls), len(controls) + len(examples))})

    def test_textbook_expression_ignores_explicit_correction_not_other_occurrences(self):
        controls = ('분모를 이항하면 안 됩니다.', '분모를 이항하지 말고 양변에 같은 수를 곱하시오.',
                    '분모를 이항해서는 안 된다.', '분모를 이항하는 것은 올바르지 않다.', '분모를 이항할 수 없다.',
                    '분모를 이항할 수는 없다.', '분모를 이항할 수 없습니다.', '포물선은 위로 열릴 수 없다.')
        examples = ('분모를 이항하면 양변이 같지 않다.', '분모를 이항하지 마세요. 그런데 여기서는 분모를 이항한다.',
                    '분모를 이항하지 말고, 분모를 이항하시오.', '분모를 이항할 수 있다.',
                    '분모를 이항할 수 없지만 분모를 이항해 보자.', '포물선은 위로 열리지 않고 아래로 열린다.')
        data = document(*({'type': 'text', 'text': text} for text in (*controls, *examples)))
        issues = [i for i in validate(data, ROOT) if i.code == 'textbook-expression']
        self.assertEqual([i.path for i in issues],
                         [f'items[0].blocks[{i}].text' for i in range(len(controls), len(controls) + len(examples))])
        self.assertIn('아래로 열', issues[-1].message)

    def test_round9_expression_and_term_regressions(self):
        cases = (
            ('원과 부채꼴', '호 $AB$는 원호입니다.', 'textbook-expression', True),
            ('원과 부채꼴', '이것은 원호이다.', 'textbook-expression', True),
            ('원과 부채꼴', '원호는 원의 일부이다.', 'textbook-expression', True),
            ('원과 부채꼴', '원호가 보인다.', 'textbook-expression', True),
            ('원과 부채꼴', '타원호를 그린다.', 'textbook-expression', False),
            ('원과 부채꼴', '반지름이 3π cm인 원', 'textbook-expression', True),
            ('원과 부채꼴', r'반지름이 $3\pi$ cm인 원', 'textbook-expression', True),
            ('원과 부채꼴', r'지름이 $\pi$인 원', 'textbook-expression', True),
            ('원과 부채꼴', '반지름의 길이가 3π cm인 원', 'textbook-expression', False),
            ('일차방정식', '분모를 이항할 수가 없다.', 'textbook-expression', False),
            ('일차방정식', '분모를 이항할 수는 없다.', 'textbook-expression', False),
            ('일차방정식', '분모를 이항할 수도 없다.', 'textbook-expression', False),
            ('일차방정식', '분모를 이항할 수가 있다.', 'textbook-expression', True),
            ('자료의 정리와 해석', '자료를 분산 보관한다.', 'term-beyond-grade', False),
            ('자료의 정리와 해석', '분산 투자와 분산 처리.', 'term-beyond-grade', False),
            ('자료의 정리와 해석', '자료를 분산시키거나 분산되도록 한다.', 'term-beyond-grade', False),
            ('자료의 정리와 해석', '자료의 분산을 구한다.', 'term-beyond-grade', True),
            ('원의 성질', r'$\angle APB$가 보는 호를 찾는다.', 'textbook-expression', True),
            ('원주각', '각이 보는 호를 비교한다.', 'textbook-expression', True),
            ('원의 성질', '같은 호에 대한 원주각을 찾는다.', 'textbook-expression', False),
            ('원의 성질', '호를 보는 눈을 그린다.', 'textbook-expression', False),
            ('독서', '그림을 보는 호기심을 기른다.', 'textbook-expression', False),
        )
        for unit, text, code, expected in cases:
            with self.subTest(text=text):
                data = document({'type': 'text', 'text': text})
                data['meta'].update(grade='중1', unit=unit)
                issues = [i for i in validate(data, ROOT) if i.code == code and i.path.startswith('items')]
                self.assertEqual(bool(issues), expected, issues)
                if '보는 호' in text and expected:
                    self.assertIn('포함하지 않는 호', issues[0].hint)

    def test_screen_only_instruction_in_printed_student_text(self):
        bad = ('‘곧은 선’을 골라 이으세요.', '"지우개"를 고르세요.', "'되돌리기'를 누르세요.",
               '“모두 지우기”를 고르세요.', '‘크게’를 고르세요.', '‘펜’을 선택하세요.',
               '확인 단추를 누르세요.', '정답 보기 단추를 누르시오.')
        good = ('꼭짓점에서 $A$, $B$까지 각각 선분을 그으세요.', '지우개로 잘못 그린 부분을 지운다.',
                '펜으로 선을 긋는다.', '각의 크기를 크게 그린다.', '‘펜’으로 글씨를 쓴다.')
        for text in (*bad, *good):
            with self.subTest(text=text):
                data = document({'type': 'question', 'text': text})
                self.assertEqual(any(i.code == 'screen-only-instruction' for i in validate(data, ROOT)), text in bad)
        data = document({'type': 'situation', 'title': bad[0], 'lines': [bad[1]]},
                        {'type': 'answer', 'rows': [{'label': bad[2], 'answer': bad[3]}]},
                        {'type': 'callout', 'kind': '개념', 'title': bad[4], 'lines': [bad[5]]},
                        {'type': 'checklist', 'items': [bad[6]]},
                        {'type': 'checklist', 'print': True, 'items': [bad[7]]},
                        {'type': 'figure', 'svg': '<svg/>', 'width_mm': 40, 'teacher_only': True,
                         'caption': bad[0]})
        data['meta']['kind'] = '수업'
        data['items'][0]['hints'] = [bad[0]]
        data['teacher'] = {'memo': [bad[1]], 'solutions': [{'item': 'q1', 'answer': bad[2], 'steps': [bad[3]]}]}
        paths = {i.path for i in validate(data, ROOT) if i.code == 'screen-only-instruction'}
        self.assertEqual(paths, {'items[0].blocks[0].title', 'items[0].blocks[0].lines[0]',
                                 'items[0].blocks[1].rows[0].label', 'items[0].blocks[2].title',
                                 'items[0].blocks[2].lines[0]', 'items[0].blocks[4].items[0]'})
        data = document({'type': 'text', 'text': '설명을 쓰세요.'})
        data['meta'].update(kind='수업', title=bad[0], unit=bad[1], name_fields=[bad[4]], goal=bad[6])
        paths = lambda: {i.path for i in validate(data, ROOT) if i.code == 'screen-only-instruction'}
        self.assertEqual(paths(), {'meta.title', 'meta.unit', 'meta.name_fields[0]'})
        data['meta']['header_title'] = '제목'
        self.assertEqual(paths(), {'meta.unit', 'meta.name_fields[0]'})

    def test_print_color_theme_warning_only_for_practice_and_assessment(self):
        data = document({'type': 'text', 'text': '생각을 씁니다.'})
        for kind, theme, expected in (('수업', '산뜻', False), ('연습', '산뜻', True), ('평가', '산뜻', True),
                                      ('연습', '정본', False), ('평가', None, False)):
            with self.subTest(kind=kind, theme=theme):
                data['meta']['kind'] = kind
                if theme is None:
                    data['meta'].pop('theme', None)
                else:
                    data['meta']['theme'] = theme
                warnings = [i for i in validate(data, ROOT) if i.code == 'theme-print-color']
                self.assertEqual(len(warnings), int(expected))
                if warnings:
                    self.assertEqual(warnings[0].path, 'meta.theme')
                    self.assertIn('인쇄 PDF에도 색 채움이 남습니다', warnings[0].hint)

    def test_grade_long_and_short_simultaneous_equations(self):
        for term in ('연립방정식', '연립일차방정식', '피타고라스 정리', '피타고라스의 정리'):
            data = document({'type': 'question', 'text': term + '을 푸시오.'})
            for grade, expected in [('중1', True), ('중2', False), ('중3', False)]:
                with self.subTest(term=term, grade=grade):
                    data['meta']['grade'] = grade
                    issues = [i for i in validate(data, ROOT) if i.code == 'term-beyond-grade']
                    self.assertEqual(bool(issues), expected)
                    if expected:
                        self.assertEqual(len(issues), 1)

    def test_explore_slider_steps_and_table_range(self):
        block = {'type': 'explore', 'fn': 'a*x**2', 'x': [-4, 4], 'y': [-9, 9], 'unit_mm': 5,
                 'params': [{'name': 'a', 'min': -3, 'max': 3, 'step': 0.5, 'value': 0.5, 'except': [0]}],
                 'table': [-2, 0, 2], 'print': [{'a': -2}, {'a': 2}]}
        data = document(block)
        issues = [i for i in validate(data, ROOT) if i.code == 'explore-table-out-of-range']
        self.assertEqual(len(issues), 1)
        self.assertIn('y=-12', issues[0].message)
        block['y'] = [-12, 12]
        self.assertFalse([i for i in validate(data, ROOT) if i.code == 'explore-table-out-of-range'])
        block['params'][0]['step'] = 1
        block['print'] = [{'a': 0.5}]
        issues = [i for i in validate(data, ROOT) if i.code == 'explore-step']
        self.assertEqual({i.path for i in issues}, {'items[0].blocks[0].params[0].value',
                                                    'items[0].blocks[0].print[0].a'})
        block['params'][0].update(min=-0.3, max=0.3, step=0.1, value=0.3, **{'except': []})
        block['print'] = [{'a': 0.1}]
        self.assertFalse([i for i in validate(data, ROOT) if i.code == 'explore-step'])

    def test_explore_checks_interior_ticks_and_x_bounds(self):
        block = {'type': 'explore', 'fn': '(4-a**2)*x', 'x': [-1, 1], 'y': [-2, 2], 'unit_mm': 5,
                 'params': [{'name': 'a', 'min': -2, 'max': 2, 'step': 1, 'value': 2}], 'table': [1, 2]}
        codes = lambda: {i.code: i for i in validate(document(block), ROOT) if i.code.startswith('explore-table-')}
        issues = codes()
        self.assertEqual(issues['explore-table-x-out-of-range'].path, 'items[0].blocks[0].table[1]')
        self.assertIn('a=-1일 때 y=3', issues['explore-table-out-of-range'].message)
        block['table'] = [-1, 1]
        block['params'][0]['except'] = [-1, 1]
        self.assertIn('a=0일 때 y=-4', codes()['explore-table-out-of-range'].message)
        block['params'][0]['except'].append(0)
        self.assertEqual(codes(), {})

    def test_explore_samples_large_sliders_evenly_without_visiting_excluded_ticks(self):
        self.assertEqual(slider_samples(0, 60, 1, {0, 59}), [*range(1, 59), 60])
        sampled = slider_samples(-301, 301, 1, {-301, 301})
        self.assertEqual(sampled, list(range(-300, 301, 10)))
        block = {'type': 'explore', 'fn': '3-a**2', 'x': [-1, 1], 'y': [-100, 2], 'unit_mm': 5,
                 'params': [{'name': 'a', 'min': -6, 'max': 6, 'step': 0.1, 'value': 6}], 'table': [0]}
        issues = validate(document(block), ROOT)
        self.assertTrue(any(i.code == 'explore-table-out-of-range' and 'y=' in i.message for i in issues))

    def test_explore_endpoints_exclusions_print_and_multi_parameter(self):
        block = {'type': 'explore', 'fn': '(1-a**2)*x', 'x': [-2, 2], 'y': [-6, 1], 'unit_mm': 5,
                 'params': [{'name': 'a', 'min': -2, 'max': 2, 'step': 1, 'value': 1}],
                 'table': [2], 'print': [{'a': 0}]}
        data = document(block)
        self.assertTrue(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)))
        block.pop('print')
        self.assertTrue(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)))
        block['params'][0]['except'] = [0]
        self.assertFalse(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)))
        block.update(fn='a*x+b', x=[-1, 1], y=[-1, 1], table=[1], params=[
            {'name': 'a', 'min': -1, 'max': 1, 'step': 1, 'value': 0, 'except': [-1]},
            {'name': 'b', 'min': -1, 'max': 1, 'step': 1, 'value': 0, 'except': [-1]}])
        self.assertTrue(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)))
        block['params'][0]['except'] = [-1, 1]
        self.assertFalse(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)))
        block.update(fn='2*a*(1-b**2)', table=[0], params=[
            {'name': 'a', 'min': -1, 'max': 1, 'step': 1, 'value': 0},
            {'name': 'b', 'min': -1, 'max': 1, 'step': 1, 'value': 0}])
        self.assertTrue(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)),
                        '한 슬라이더만 끝으로 움직인 경우도 검사한다')

    def test_math_unit_warning_only_for_bare_units_after_numbers(self):
        bad = ('$12 cm$', '$12cm$', '$3.5 mm$', '$8 km$', '$2 kg$', '$.5 mL$', '$12 cm²$', '$1 m²$',
               '$1 cm^{2}$', '$6{,}000cm$', r'$\frac{1}{2 cm}$')
        good = ('$12$ cm', r'$12\text{ cm}$', r'$12\mathrm{cm}$', r'$\text{12cm}$', r'$\mathrm{12cm}$',
                '$12 m$', '$1g$', '$2L$', '$2x$', '$12cmx$', '$x^2cm$', r'금액 \$12cm\$')
        for source, expected in [(source, True) for source in bad] + [(source, False) for source in good]:
            with self.subTest(source=source):
                issues = validate(document({'type': 'text', 'text': source}), ROOT)
                self.assertEqual(any(i.code == 'math-unit-upright' and i.level == 'warning' for i in issues), expected)

    def test_segment_length_warning_only_for_bare_comparisons(self):
        bad = ('$OA=OB=OC$', '$AB<CD$', r'$AB\le CD$', r'$\overline{AB}=CD$',
               r'$\overline{AB}=\overline{CD}=EF$', r'$AB=\overline{CD}$')
        good = (r'$\overline{OA}=\overline{OB}$', r'$\angle ABC=\angle DEF$', r'$\angle AB=CD$',
                r'$\triangle ABC\equiv\triangle DEF$', r'$\square ABCD=\square EFGH$', r'$\square AB=CD$',
                r'$\triangle AB=CD$', '변 AB를 보시오.', '변 $AB$와 직선 $CD$', '점 $A=B$',
                r'$AB\parallel CD$', r'$AB\perp CD$', r'$\text{AB=CD}$', '$ABC=DEF$', '$AAB=CD$')
        for source, expected in [(source, True) for source in bad] + [(source, False) for source in good]:
            with self.subTest(source=source):
                issues = validate(document({'type': 'text', 'text': source}), ROOT)
                self.assertEqual(any(i.code == 'segment-length-overline' and i.level == 'warning' for i in issues), expected)

    def test_segment_numeric_equality_requires_length_context(self):
        bad = ('선분 $AB$의 길이는 $AB=6$ cm이다.', '길이는 $AB=6$이다.', '길이는 $6=AB$이다.',
               '$AB=6$ mm이다.', '$AB=6 cm$이다.', r'$AB=6\text{ cm}$이다.', '$AB=2.5$ m이다.',
               '선분 $AB$의 길이는 $AB=6.5$이다.')
        good = (r'선분 $AB$의 길이는 $\overline{AB}=6$ cm이다.', '선분 $AB$를 찾으시오.',
                '행렬 $AB=6$을 비교하시오.', r'집합 $\{AB=6\}$을 말하시오.', '길이를 구한다. $AB=6$을 본다.',
                '$AB=6$ kg이다.', '$AB=6$ cm²이다.', '상자의 길이는 $3$ cm이고 행렬 $AB=6$을 비교한다.',
                r'각의 길이를 묻지 않는다. $\angle AB=6$이다.')
        for source in (*bad, *good):
            with self.subTest(source=source):
                issues = validate(document({'type': 'text', 'text': source}), ROOT)
                self.assertEqual(sum(i.code == 'segment-length-overline' for i in issues), int(source in bad))

    def test_single_letter_m_warns_only_as_spaced_length_unit(self):
        bad = ('철사의 길이는 $3 m$이다.', '학교까지의 거리는 $1.5 m$이다.', '$3 m$인 높이를 잰다.',
               '선분의 길이가 $AB=3 m$이다.')
        good = ('식은 $y=mx$이다.', '기울기는 $3 m$이다.', '철사의 길이는 $3m$으로 나타낸다.',
                '철사의 길이는 $3$ m이다.', r'철사의 길이는 $3\mathrm{m}$이다.',
                '길이를 확인했다. 기울기는 $3 m$이다.', '길이 관계는 $y=3 m x$이다.')
        for source in (*bad, *good):
            with self.subTest(source=source):
                issues = validate(document({'type': 'text', 'text': source}), ROOT)
                self.assertEqual(sum(i.code == 'math-unit-upright' for i in issues), int(source in bad))

    def test_screen_drawing_requires_student_space_in_the_same_item(self):
        question = {'type': 'question', 'text': '삼각형의 수직이등분선을 그어 봅시다.'}
        figure = {'type': 'figure', 'svg': '<svg/>', 'width_mm': 40}
        draw = {'type': 'answer', 'rows': [{'label': '그리기', 'kind': 'draw', 'height_mm': 40, 'answer': '선'}]}
        for blocks, expected in [([question, figure], True), ([question, {**figure, 'pen': True}], False),
                                 ([question, {**figure, 'pen': True, 'teacher_only': True}], True),
                                 ([question, draw], False), ([{'type': 'split', 'left': [question], 'right': [draw]}], False)]:
            with self.subTest(blocks=blocks):
                data = document(*blocks)
                data['meta']['kind'] = '수업'
                data['items'].append({'id': 'q2', 'blocks': [draw]})
                issues = validate(data, ROOT)
                self.assertEqual(any(i.code == 'interactive-drawing-missing' for i in issues), expected)
                data['meta']['interactive'] = False
                self.assertFalse(any(i.code == 'interactive-drawing-missing' for i in validate(data, ROOT)))

    def test_screen_drawing_does_not_flag_paper_or_non_drawing_prompts(self):
        cases = [('원은 인쇄본에 그리세요.', False), ('종이에 원을 그려 봅시다.', False),
                 ('종이(인쇄본)에 작도해 봅시다.', False), ('그래프를 그리는 과정을 순서대로 쓰시오.', False),
                 ('주어진 그림을 살펴봅시다. 그리고 값을 씁시다.', False), ('그려진 선을 보고 답을 쓰시오.', False),
                 ('각 보기에서 옳은 답에 표시해 봅시다.', False), ('좌표평면에 점을 표시해 봅시다.', True),
                 ('원을 그리고 그 까닭을 쓰시오.', True), ('삼각형을 작도하시오.', True), ('원을 그려 봅시다.', True)]
        for text, expected in cases:
            with self.subTest(text=text):
                data = document({'type': 'ask', 'tag': '질문', 'text': text, 'lines': 2, 'answer': '답'})
                data['meta']['interactive'] = True
                self.assertEqual(any(i.code == 'interactive-drawing-missing' for i in validate(data, ROOT)), expected)
        data = document({'type': 'question', 'text': '두 점을 지나는 직선을 그려 봅시다.'},
                        {'type': 'grid', 'mode': 'coord', 'unit_mm': 4, 'x': [-3, 3], 'y': [-3, 3],
                         'plot': {'mode': 'line', 'answer': [[-1, -1], [1, 1]]}})
        data['meta']['interactive'] = True
        self.assertFalse(any(i.code == 'interactive-drawing-missing' for i in validate(data, ROOT)))

    def test_screen_drawing_handles_connected_verbs_and_graph_targets(self):
        prompts = ('그래프를 그린 후 설명하시오.', '삼각형을 그린 뒤 두 변을 비교하시오.',
                   '삼각형을 그려서 비교하시오.', '그래프를 그려 보시오.', '삼각형을 작도한 후 이유를 쓰시오.',
                   '자료를 상자그림으로 나타내시오.', '자료를 히스토그램으로 나타내시오.',
                   '자료를 도수분포다각형으로 나타내시오.', '관계를 그래프로 나타내시오.',
                   '값을 수직선 위에 나타내시오.', '종이에 답을 쓰고, 그래프를 그린 뒤 설명하시오.')
        controls = ('관계를 식으로 나타내시오.', '크기를 기호로 나타내시오.', '그려 놓은 그래프로 설명하시오.',
                    '그려놓은 그림을 보시오.', '작도한 삼각형이 주어져 있다.', '인쇄본에 그리세요.',
                    '공책에 그래프를 그린 후 설명하시오.', '종이 위에 상자그림으로 나타내시오.',
                    '수직선 위에 나타낸 점의 값을 쓰시오.', '그래프로 나타내는 과정을 말하시오.')
        for text in (*prompts, *controls):
            with self.subTest(text=text):
                data = document({'type': 'question', 'text': text})
                data['meta']['interactive'] = True
                issues = [i for i in validate(data, ROOT) if i.code == 'interactive-drawing-missing']
                self.assertEqual(len(issues), int(text in prompts))
                if issues:
                    self.assertIn('같은 viewBox', issues[0].hint)
                    self.assertIn('kind: "box"', issues[0].hint)
                    self.assertNotIn('teacher_only', issues[0].hint)

    def test_graph_representation_can_use_matching_plot(self):
        cases = (
            ('자료를 히스토그램으로 나타내시오.',
             {'type': 'chart', 'kind': 'histogram', 'bins': [0, 1, 2], 'values': [0, 0], 'plot': {'answer': [1, 2]}}),
            ('수를 수직선 위에 나타내시오.',
             {'type': 'numberline', 'range': [-2, 2], 'step': 1, 'plot': {'answer': [1]}}),
        )
        for prompt, plot in cases:
            with self.subTest(prompt=prompt):
                data = document({'type': 'question', 'text': prompt}, plot)
                data['meta']['interactive'] = True
                self.assertFalse(any(i.code == 'interactive-drawing-missing' for i in validate(data, ROOT)))
                data['items'][0]['blocks'][0]['text'] = '자료를 상자그림으로 나타내시오.'
                self.assertTrue(any(i.code == 'interactive-drawing-missing' for i in validate(data, ROOT)))

    def test_table_unit_warns_only_on_explicit_category_headers(self):
        data = document({'type': 'table',
                         'header': ['모둠 (단위: 분)', '시간(분)', '거리 (단위: m)', '번호(단위 : 번)', '항목 (비고)'],
                         'rows': [['가 모둠', '$20$', '$4$', '$1$', '나']]},
                        {'type': 'text', 'text': '통학 시간 (단위: 분)'})
        warnings = [i for i in validate(data, ROOT) if i.code == 'table-category-unit']
        self.assertEqual([i.path for i in warnings], ['items[0].blocks[0].header[0]', 'items[0].blocks[0].header[3]'])

    def test_frequency_graph_y_max_must_be_positive(self):
        for kind in ('histogram', 'polygon'):
            block = {'type': 'chart', 'kind': kind, 'bins': [0, 1, 2], 'values': [0, 0]}
            for maximum in (0, -1):
                with self.subTest(kind=kind, maximum=maximum):
                    block['y_max'] = maximum
                    self.assertTrue(any(i.code == 'frequency-y-max' and i.level == 'error'
                                        for i in validate(document(block), ROOT)))
            block['y_max'] = 1
            self.assertFalse(any(i.code == 'frequency-y-max' for i in validate(document(block), ROOT)))

    def test_production_guidance_covers_assessment_and_delivery(self):
        design = (ROOT / 'references/내용-설계.md').read_text(encoding='utf-8')
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        authoring = (ROOT / 'references/구성-쓰기.md').read_text(encoding='utf-8')
        for phrase in ('채점 요소마다 학생이 실제 답을 남길 칸', '원래 조건과 상황에 맞는지 확인하기',
                       '교사 메모와 문항별 채점 요소에 같은 말', '$a=0.5$일 때의 모양',
                       '허용 눈금이 61개', '$x=\\pm1,\\pm2$', '학생의 관찰 표현',
                       '활동 제목·꼬리표·그림 설명', '두 양 사이 관계를 옳은 등식으로 나타냄',
                       '16상자일 때의 비교 결과와 근거',
                       '짧은 판단이나 까닭을 받는 칸은 `lines`로 높이를 정하고 `grow`를 주지 않는다',
                       '남는 높이는 긴 서술 칸에만 `grow: true`로 나누거나 쪽 아래 여백으로 둔다', '`grow-excess` 경고'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, design)
        for phrase in ('⭐ 안내 줄을 답의 마지막 줄에 글자 그대로', '사용자가 빼 달라고 하면 뺀다',
                       'teacher.solutions[].verify', '대입 결과를 남긴다'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill)
        for phrase in ('4*x+6*y=127', '곱셈 기호는 생략하지 않고', '양변이 정확히 같을 때만',
                       '"tolerance": 0.001', '### 공개 정책별 필드', '교사용 최종 답과 일치', '대입한 결과를 한 줄로'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, authoring)

    def test_drawing_and_statistics_guidance_keeps_answers_at_student_scale(self):
        design = (ROOT / 'references/내용-설계.md').read_text(encoding='utf-8')
        authoring = (ROOT / 'references/구성-쓰기.md').read_text(encoding='utf-8')
        for phrase in ('같은 `viewBox`의 SVG', '그 행의 `answer`에 그림 객체', '`viewBox` 1단위 = 1mm',
                       '현의 길이', '셋째 단계의 원과 둘째 단계의 원이 만나는 점',
                       '교사용 정답 그림과 풀이에 그 점을 같은 이름으로 표시한다',
                       '원 전체를 기준으로 다른 원이나 주어진 그림과 만나는 점도 계산한다',
                       '기준 반직선의 위치와 방향, 정답 반직선의 방향을 모두 바꾼다', '앞 문항에서 그린 획을 그대로 옮겨 보고',
                       'kind: "box"', '가운데 약 절반',
                       '상자 바깥 두 구간', '채점 요소에도 그림과 해석', '20~25mm', '쪽 아래 여백'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, design)
        self.assertNotIn('`teacher_only: true`', design)
        self.assertNotIn('남는 높이를 `kind: "draw"` 칸에 먼저', design)
        self.assertIn('‘시간(분)’처럼 양을 나타내는 머리', authoring)
        self.assertIn('범주를 나타내는 머리에는 단위를 붙이지 않는다', authoring)

    def test_all_warnings_nonblocking(self):
        data = document({'type': 'question', 'text': '1. 점 A □ ' + '가' * 161},
                        {'type': 'answer', 'rows': [{'label': '풀이', 'mark': '㉠', 'answer': '예시 답안'}]})
        result = validate(data, ROOT)
        self.assertEqual({i.level for i in result}, {'warning'})
        self.assertEqual(len(result), 6)
        self.assertTrue(all(i.hint for i in result))


class FigurePlanTest(unittest.TestCase):
    def check(self, *blocks, columns=1, interactive=False):
        data = document(*blocks)
        data['meta'].update(columns=columns, interactive=interactive)
        return validate(data, ROOT)

    def test_box_quartiles_for_odd_even_and_repeated_values(self):
        from sheet_common import box_five
        for data, expected in [([17, 10, 21, 12, 15, 8, 19, 24, 14], [8, 11, 15, 20, 24]),
                               ([15, 6, 11, 18, 8, 14, 12, 9], [6, 8.5, 11.5, 14.5, 18]),
                               ([2, 2, 1, 2, 3], [1, 1.5, 2, 2.5, 3]),
                               ([4, 4, 4, 4], [4] * 5)]:
            self.assertEqual(box_five({'data': data}), expected)
        self.assertEqual(box_five({'five': [1, 1, 1, 2, 3]}), [1, 1, 1, 2, 3])

    def test_box_validates_groups_bounds_ticks_and_tools(self):
        chart = deepcopy(load_registry()['chart']['example'])
        for change, suffix in [({'groups': []}, '.groups'), ({'groups': chart['groups'] * 3}, '.groups'),
                               ({'groups': [{'label': '가', 'five': [1, 8, 4, 9, 10]}]}, '.groups[0].five'),
                               ({'groups': [{'label': '가', 'data': [3, 5, 6]}]}, '.groups[0].data'),
                               ({'groups': [{'label': '가', 'data': [True, 3, 6, 8]}]}, '.groups[0].data[0]'),
                               ({'groups': [{'label': '가', 'data': [1, 2, 3, 4], 'five': [1] * 5}]}, '.groups[0]'),
                               ({'x': [4, 7]}, '.groups[0].data'), ({'x_step': 1e-300}, '.x_step'),
                               ({'x_step': 11}, '.x_step'), ({'pen': 'true'}, '.pen'),
                               ({'plot': {'answer': [1]}}, '.plot'), ({'guides': 1}, '.guides'),
                               ({'height_mm': 60}, '.height_mm')]:
            with self.subTest(change=change):
                errors = {i.path for i in self.check({**chart, **change}) if i.level == 'error'}
                self.assertIn('items[0].blocks[0]' + suffix, errors)
        question = {'type': 'question', 'text': '상자그림을 그리시오.'}
        for pen in (True, False):
            issues = self.check(question, {**chart, 'pen': pen}, interactive=True)
            self.assertEqual(any(i.code == 'interactive-drawing-missing' for i in issues), not pen)

    def test_box_chart_checks_raw_data_and_five_values_across_items(self):
        raw = {'type': 'table', 'header': ['구분', '퀴즈 점수 (점)'],
               'rows': [['1반', '$90$, $60$, $45$, $60$']]}
        five = {'type': 'table', 'header': ['구분', '최솟값', '제1사분위수', '중앙값', '제3사분위수', '최댓값'],
                'rows': [['1반', '$45$', '{{|$52.5$}}', '$60$', '$75$', '$90$']]}
        chart = {'type': 'chart', 'kind': 'box', 'x': [0, 100], 'x_step': 10, 'x_label': '(점)',
                 'groups': [{'label': '1반', 'data': [45, 60, 60, 90]}]}
        data = document(raw, five)
        data['items'].append({'id': 'q2', 'blocks': [chart]})

        def mismatches():
            return {i.code: i.path for i in validate(data, ROOT) if i.code.startswith('box-table-')}

        self.assertEqual(mismatches(), {})
        raw['rows'][0][1] = '$90$, $55$, $45$, $60$'
        five['rows'][0][2] = '{{|$55$}}'
        self.assertEqual(mismatches(), {
            'box-table-data-mismatch': 'items[0].blocks[0].rows[0][1]',
            'box-table-five-mismatch': 'items[0].blocks[1].rows[0][2]',
        })
        raw['rows'][0][0] = '1반 평균'
        five['header'][2] = '평균'
        self.assertEqual(mismatches(), {})
        raw['rows'][0][0] = '1반'
        raw['header'][1] = '평균 점수 (점)'
        self.assertEqual(mismatches(), {})
        raw['header'][1] = '퀴즈 점수 (점)'
        raw['rows'][0][1] = '$90$, $90$, $45$, $60$'  # Same distinct values, different counts.
        self.assertEqual(set(mismatches()), {'box-table-data-mismatch'})
        data['items'].append({'id': 'q3', 'blocks': [deepcopy(chart)]})
        self.assertEqual(mismatches(), {})  # Ambiguous across items.
        data['items'][0]['blocks'].append(chart)
        self.assertEqual(set(mismatches()), {'box-table-data-mismatch'})  # Prefer the same item.
        raw['rows'][0][1] = '$45$, $60$, $90$'  # A repeated value was left out.
        self.assertEqual(set(mismatches()), {'box-table-data-mismatch'})

    def test_box_table_and_chart_units_prevent_unrelated_comparisons(self):
        chart = {'type': 'chart', 'kind': 'box', 'x': [0, 100], 'x_step': 10, 'x_label': '(분)',
                 'groups': [{'label': '1반', 'data': [10, 20, 30, 40]}]}
        raw = {'type': 'table', 'header': ['구분', '점수'], 'unit': '점',
               'rows': [['1반', '$1$, $2$, $3$, $4$']]}
        five = {'type': 'table', 'header': ['구분', '최솟값', '제1사분위수', '중앙값', '제3사분위수', '최댓값'],
                'unit': '점', 'rows': [['1반', '$1$', '$1.5$', '$2.5$', '$3.5$', '$4$']]}
        data = document(raw, five)
        data['items'].append({'id': 'q2', 'blocks': [chart]})
        def codes():
            return {i.code for i in validate(data, ROOT) if i.code.startswith('box-table-')}
        self.assertEqual(codes(), set())
        raw.pop('unit')
        raw['header'][1] = '점수 (점)'
        self.assertEqual(codes(), set())
        raw['header'][1] = '점수 (분)'
        five['unit'] = '분'
        self.assertEqual(codes(), {'box-table-data-mismatch', 'box-table-five-mismatch'})
        raw['header'][1] = '점수'
        five.pop('unit')
        self.assertEqual(codes(), {'box-table-data-mismatch', 'box-table-five-mismatch'})

    def test_figure_answer_requires_svg_with_matching_viewbox(self):
        figure = deepcopy(load_registry()['figure']['example'])
        for answer in ('<svg viewBox="0,0,40.0,20"/>', {'svg': '<svg viewBox="0 0 40 20"/>'}):
            self.assertFalse(self.check({**figure, 'answer': answer}))
        for answer in ('<svg/>', '<svg viewBox="0 1 40 20"/>', '<svg viewBox="0 0 50 20"/>',
                       '<svg viewBox="0 0 40 20"><script/></svg>', {'svg': '<svg/>', 'width_mm': 40}):
            self.assertTrue(any(i.path == 'items[0].blocks[0].answer' and i.level == 'error'
                                for i in self.check({**figure, 'answer': answer})))
        figure['src'] = '그림/밑그림.svg'
        figure.pop('svg')
        self.assertTrue(any(i.path.endswith('.answer') and i.level == 'error' for i in self.check(figure)))

    def test_figure_alt_warning_only_for_unlabelled_student_math_svg(self):
        figure = {'type': 'figure', 'svg': '<svg><path/></svg>', 'width_mm': 50}
        for updates, expected in [({}, True), ({'alt': '도형'}, False), ({'teacher_only': True}, False),
                                  ({'svg': '<svg aria-label="선분"><path/></svg>'}, False),
                                  ({'svg': '<svg><title>선분</title></svg>'}, False)]:
            with self.subTest(updates=updates):
                self.assertEqual(any(i.code == 'figure-alt-missing' for i in self.check({**figure, **updates})), expected)
        pict = {k: v for k, v in figure.items() if k != 'type'}
        heading = {'type': 'heading', 'tag': '활동', 'text': '살펴보기', 'image': pict}
        self.assertFalse(any(i.code == 'figure-alt-missing' for i in self.check(heading)))

    def test_figure_label_warning_uses_effective_width_and_inherited_size(self):
        svg = ("<svg viewBox='0 0 170 30' aria-label='수직선' font-size='4'>"
               "<g font-size='8' style='font-size:3.4px!important;font-size:20px'><text>10</text></g></svg>")
        picture = {'svg': svg, 'width_mm': 170, 'caption': '수직선'}
        figure = {'type': 'figure', **picture}
        pair = {'type': 'figures', 'items': [{**picture, 'width_mm': 86, 'teacher_only': True}, picture]}
        situation = {'type': 'situation', 'lines': ['자료'], 'figure': picture}
        cases = [(figure, 1, []), (figure, 2, ['5.15']), (pair, 1, ['4.88', '5.13']), (situation, 1, ['3.63']),
                 ({'type': 'split', 'ratio': '2:1', 'left': [figure], 'right': [figure]}, 1, ['3.46'])]
        for block, columns, sizes in cases:
            with self.subTest(block=block['type'], columns=columns):
                messages = [i.message for i in self.check(block, columns=columns) if i.code == 'figure-label-small']
                self.assertEqual(len(messages), len(sizes), messages)
                for message, size in zip(messages, sizes):
                    self.assertIn(size + 'pt', message)

    def test_answer_scale_warning_uses_answer_cell_width_and_two_percent_limit(self):
        def drawing(width, viewbox=105):
            return {'width_mm': width, 'alt': '도형', 'svg': f'<svg viewBox="0 0 {viewbox} 40"/>'}
        figure = {'type': 'figure', **drawing(166, 166)}
        for width, expected in [(102, True), (102.9, False), (105, False), (107.1, False), (108, True)]:
            draw = {'type': 'answer', 'rows': [{'kind': 'draw', 'label': '그리기', 'height_mm': 22, 'answer': drawing(width)}]}
            self.assertEqual(any(i.code == 'answer-scale-mismatch' for i in self.check(figure, draw)), expected)
        draw['rows'][0]['answer'] = drawing(190, 188 - 25.1 - 3.6)
        self.assertFalse(any(i.code == 'answer-scale-mismatch' for i in self.check(figure, draw)))
        teacher = {'type': 'figure', **drawing(102), 'teacher_only': True}
        self.assertTrue(any(i.code == 'answer-scale-mismatch' for i in self.check(figure, teacher)))
        data = document(figure)
        data['items'].append({'id': 'q2', 'blocks': [teacher]})
        self.assertFalse(any(i.code == 'answer-scale-mismatch' for i in validate(data, ROOT)))

    def test_distorted_svg_warns_for_pen_overlay_draw_and_scale_pairs(self):
        svg = '<svg viewBox="0 0 174 108" width="174" height="54" preserveAspectRatio="none"/>'
        base = {'type': 'figure', 'svg': svg, 'width_mm': 174, 'alt': '두 선분'}
        draw = {'type': 'answer', 'rows': [{'kind': 'draw', 'label': '작도', 'height_mm': 40,
                                            'answer': {'svg': svg, 'width_mm': 174, 'alt': '작도 결과'}}]}
        teacher = {**base, 'teacher_only': True}
        plain = {**base, 'svg': '<svg viewBox="0 0 174 108"/>'}

        def distorted(*blocks):
            return [i.path for i in self.check(*blocks) if i.code == 'figure-aspect-distorted']

        self.assertEqual(distorted({**base, 'pen': True, 'answer': svg}),
                         ['items[0].blocks[0].svg', 'items[0].blocks[0].answer'])
        self.assertEqual(distorted({**plain, 'pen': True, 'answer': svg}), ['items[0].blocks[0].answer'])
        self.assertEqual(distorted(draw), ['items[0].blocks[0].rows[0].answer.svg'])
        self.assertEqual(distorted(base, teacher), ['items[0].blocks[0].svg', 'items[0].blocks[1].svg'])
        self.assertFalse(any(i.code == 'answer-scale-mismatch' for i in self.check(base, teacher)))
        differences = [i.message for i in self.check(plain, teacher) if i.code == 'answer-scale-mismatch']
        self.assertEqual(len(differences), 1)
        self.assertIn('세로', differences[0])
        for keep in ('xMidYMid meet', 'xMidYMid slice'):
            self.assertEqual(distorted({**base, 'svg': svg.replace('none', keep), 'pen': True}), [])

    def test_svg_label_size_composes_transforms(self):
        for outer, inner, expected in [('scale(.2)', '', '2.27'),
                                       ('translate(20,-10) scale(2)', 'matrix(1 0 0 .1 0 0)', '2.27'),
                                       ('matrix(0 1 -1 0 0 0)', 'scale(.2,1)', None),
                                       ('scale(2) translate(4)', 'scale(.5)', None)]:
            with self.subTest(outer=outer, inner=inner):
                svg = (f'<svg viewBox="0 0 100 20" font-size="4"><g transform="{outer}">'
                       f'<g transform="{inner}"><text>A</text></g></g></svg>')
                figure = {'type': 'figure', 'width_mm': 100, 'svg': svg, 'alt': '점 A'}
                messages = [i.message for i in self.check(figure) if i.code == 'figure-label-small']
                self.assertEqual(len(messages), int(expected is not None))
                if expected:
                    self.assertIn(expected + 'pt', messages[0])

    def test_svg_label_size_composes_nested_viewports(self):
        cases = (
            ('<svg width="10" height="10" viewBox="0 0 100 100"><text font-size="10">A</text></svg>', '2.83'),
            ('<g transform="scale(2)"><svg width="10" height="10" viewBox="0 0 100 100">'
             '<text font-size="10">A</text></svg></g>', '5.67'),
            ('<svg width="10" height="20" viewBox="0 0 100 100" preserveAspectRatio="none">'
             '<text font-size="10">A</text></svg>', '5.67'),
            ('<svg width="10" height="20" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">'
             '<text font-size="10">A</text></svg>', '2.83'),
            ('<svg width="10%" height="10%" viewBox="0 0 100 100"><text font-size="1">A</text></svg>', None),
        )
        for child, expected in cases:
            with self.subTest(child=child):
                svg = f'<svg viewBox="0 0 100 100">{child}</svg>'
                figure = {'type': 'figure', 'width_mm': 100, 'svg': svg, 'alt': '점 A'}
                messages = [i.message for i in self.check(figure) if i.code == 'figure-label-small']
                self.assertEqual(len(messages), int(expected is not None), messages)
                if expected:
                    self.assertIn(expected + 'pt', messages[0])

    def test_figures_fields_and_chart_examples_match_plan(self):
        entry = load_registry()['figures']
        self.assertFalse(self.check(entry['example']))
        for field in ('src', 'svg', 'width_mm', 'alt', 'caption', 'teacher_only', 'split'):
            self.assertIn(field, entry['fields']['items'])
        for key in ('pen', 'answer', 'type'):
            pair = deepcopy(entry['example'])
            pair['items'][0][key] = True
            self.assertTrue(any(i.path.endswith(f'.items[0].{key}') and i.level == 'error' for i in self.check(pair)))
        doc = (ROOT / 'references/구성-쓰기.md').read_text(encoding='utf-8')
        self.assertIn('`figures.items[]`에는', doc)
        examples = [json.loads(example) for example in re.findall(r'```json\n(.*?)\n```', doc, re.S)]
        split = next(example for example in examples if example.get('type') == 'split')
        charts = [example for example in examples if example.get('kind') == 'box']
        self.assertEqual(len(charts), 4)
        self.assertFalse(self.check(split, *charts, interactive=True))
        self.assertTrue(all(split[side][0].get('pen') is True for side in ('left', 'right')))


class PlanWarningCodeTest(unittest.TestCase):
    def test_every_plan_warning_has_a_code(self):
        # 전달 관문(status)은 원고 경고를 코드별로 판단하게 하므로, 코드 없는 경고가 다시 생기지 않게 막는다.
        source = (Path(__file__).resolve().parents[2] / 'scripts' / 'sheet_plan.py').read_text(encoding='utf-8')
        missing = []
        for match in re.finditer(r'self\.warn\(', source):
            end, depth = match.end(), 1
            while depth:
                depth += {'(': 1, ')': -1}.get(source[end], 0)
                end += 1
            body = source[match.end():end - 1].strip()
            if not re.search(r"(?:'[a-z]+(?:-[a-z0-9]+)*'|\bcode\b[^,]*)\s*$", body):
                missing.append(source.count(chr(10), 0, match.start()) + 1)
        self.assertEqual(missing, [], f'코드 없는 원고 경고 행: {missing}')
