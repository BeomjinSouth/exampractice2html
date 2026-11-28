from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_plan import load_registry, validate


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

    def test_build_keeps_verify_out_of_teacher_and_student_content(self):
        from sheet_build import Renderer, document_html, strip_teacher
        data = document({'type': 'question', 'text': '수를 구하시오.'})
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '$2$', 'steps': ['계산한다.'],
                                          'verify': [{'given': {'x': 2}, 'holds': ['9*x=18']}]}]}
        html = document_html(Renderer(data, ROOT, []), {}, '')
        self.assertNotIn('9*x=18', html)
        self.assertNotIn('9*x=18', strip_teacher(html))

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
        data = document({'type': 'question', 'text': '그래프가 열리는 방향을 쓰시오.'},
                        {'type': 'answer', 'rows': [{'label': '모양', 'answer': '위로 열린다.'}]})
        data['meta'].update(title='이차함수 그래프', kind='수업')
        data['teacher'] = {'solutions': [{'item': 'q1', 'answer': '아래로 열린다.',
                                           'steps': ['아래로 열리는 모양이다.']}]}
        issues = [i for i in validate(data, ROOT) if i.code == 'textbook-expression']
        self.assertEqual({i.path for i in issues}, {'items[0].blocks[0].text', 'items[0].blocks[1].rows[0].answer',
                                                     'teacher.solutions[0].answer', 'teacher.solutions[0].steps[0]'})
        data['meta']['title'] = '문이 열리는 방향'
        self.assertFalse([i for i in validate(data, ROOT) if i.code == 'textbook-expression'])
        data['meta']['title'] = '포물선'
        data['items'][0]['blocks'][0]['text'] = '빈칸 {{|위로 열린다}}을 쓰시오.'
        self.assertFalse(any(i.path == 'items[0].blocks[0].text' and i.code == 'textbook-expression'
                             for i in validate(data, ROOT)))

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

    def test_explore_endpoints_exclusions_print_and_multi_parameter(self):
        block = {'type': 'explore', 'fn': '(1-a**2)*x', 'x': [-2, 2], 'y': [-6, 1], 'unit_mm': 5,
                 'params': [{'name': 'a', 'min': -2, 'max': 2, 'step': 1, 'value': 1}],
                 'table': [2], 'print': [{'a': 0}]}
        data = document(block)
        self.assertTrue(any(i.code == 'explore-table-out-of-range' for i in validate(data, ROOT)))
        block.pop('print')
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
                       '끝 눈금과 인쇄값', '$x=\\pm1,\\pm2$', '학생의 관찰 표현'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, design)
        for phrase in ('⭐ 안내 줄을 답의 마지막 줄에 글자 그대로', '사용자가 빼 달라고 하면 뺀다',
                       'teacher.solutions[].verify', '대입 결과를 남긴다'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill)
        for phrase in ('4*x+6*y=127', '곱셈 기호는 생략하지 않고', '절대 차이 `1e-9`',
                       '교사용 최종 답과 일치', '대입한 결과를 한 줄로'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, authoring)

    def test_all_warnings_nonblocking(self):
        data = document({'type': 'question', 'text': '1. 점 A □ ' + '가' * 161},
                        {'type': 'answer', 'rows': [{'label': '풀이', 'mark': '㉠', 'answer': '예시 답안'}]})
        result = validate(data, ROOT)
        self.assertEqual({i.level for i in result}, {'warning'})
        self.assertEqual(len(result), 6)
        self.assertTrue(all(i.hint for i in result))
