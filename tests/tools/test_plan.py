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

    def test_all_warnings_nonblocking(self):
        data = document({'type': 'question', 'text': '1. 점 A □ ' + '가' * 161},
                        {'type': 'answer', 'rows': [{'label': '풀이', 'mark': '㉠', 'answer': '예시 답안'}]})
        result = validate(data, ROOT)
        self.assertEqual({i.level for i in result}, {'warning'})
        self.assertEqual(len(result), 6)
        self.assertTrue(all(i.hint for i in result))
