import base64
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_answer import canonical, check_hash, num, plain


class Tags(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / '새 폴더'
        self.title = '좌표: 확인'
        self.teacher = self.folder / '좌표_ 확인 (교사용).html'
        self.student = self.folder / '좌표_ 확인.html'
        self.record = self.folder / '검사/build.json'
        self.source = self.folder / '구성.json'
        self.cli('new', self.folder, '--title', self.title, '--kind', '평가')

    def cli(self, *args, status=0):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT / 'scripts/sheet.py'), *map(str, args)],
                                env={**os.environ, 'SHEET_FONT_DIR': self.temp.name, 'SHEET_SYNC_CONFIG': str(Path(self.temp.name) / 'sync.json'), 'SHEET_TEST_SALT': '00112233445566778899aabbccddeeff'}, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, status, (result.stdout + result.stderr)[:600])
        self.assertIn('다음:', result.stdout)
        return result.stdout

    def fixture(self, kind='평가', columns=1):
        svg = "<svg viewBox='0 0 60 30'><rect width='60' height='30' fill='none' stroke='black'/></svg>"
        picture = {'svg': svg, 'width_mm': 24}
        private_picture = {**picture, 'teacher_only': True, 'caption': '교사 전용 캡션'}
        data = {'version': 1, 'meta': {'title': self.title, 'kind': kind, 'columns': columns,
                                      'unit': '좌표평면', 'pair': True, 'total_points': 5},
                'items': [{'id': 'q1', 'blocks': [
                    {'type': 'notice', 'label': '평가 안내', 'lines': ['과정을 쓰시오.']},
                    {'type': 'situation', 'lines': ['값은 {{㉠|숨은 빈칸 정답}}입니다.'], 'figure': picture},
                    {'type': 'question', 'text': '좌표를 쓰시오.'},
                    {'type': 'answer', 'rows': [
                        {'label': '좌표', 'mark': '㉠', 'points': 5, 'grow': True, 'lines': 6, 'group': 'p', 'answer': '오직 교사만 볼 정답'},
                        {'group': 'p', 'kind': 'draw', 'height_mm': 25, 'answer': picture}]},
                    {'type': 'choices', 'options': ['보기 가', '보기 나'], 'answer': [2]},
                    {'type': 'grid', 'mode': 'coord', 'unit_mm': 5, 'x': [-4, 4], 'y': [-4, 4],
                     'draw': [{'fn': '2*x+1', 'teacher': True}, {'points': [[1, 3]], 'on': 0}]},
                    {'type': 'figure', **private_picture},
                    {'type': 'figures', 'items': [{**picture, 'caption': '공개 캡션'}, private_picture]},
                    {'type': 'steps', 'columns': ['풀이'], 'rows': 1, 'answers': [['단계별 해설']]},
                    {'type': 'table', 'header': ['가'], 'rows': [['{{|표 속 정답}}']]},
                    {'type': 'ask', 'tag': '<질문>', 'text': '까닭은?', 'lines': 3, 'answer': '질문의 모범 답안'},
                    {'type': 'text', 'text': '정리 글'}]},
                    {'id': 'band', 'break': True, 'blocks': [{'type': 'nameband', 'label': '둘째 장'}]}],
                'teacher': {'memo': ['교사 전용 메모'], 'solutions': [{'item': 'q1', 'answer': '교사 전용 풀이'}]}}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def manifest(self):
        return json.loads(self.record.read_text(encoding='utf-8'))

    def test_creation_refuses_to_replace_and_requires_edits(self):
        original = self.source.read_bytes()
        self.assertTrue((self.folder / '그림').is_dir())
        self.cli('new', self.folder, '--title', '다른 제목', status=1)
        self.cli('plan', self.folder, status=1)
        self.assertEqual(self.source.read_bytes(), original)

    def test_build_contract_and_student_has_no_answer_data(self):
        self.fixture()
        self.cli('build', self.folder)
        teacher = self.teacher.read_text(encoding='utf-8')
        student = self.student.read_text(encoding='utf-8')
        tags = Tags(teacher).tags
        self.assertIn(('section', {'class': 'ws-item', 'data-id': 'head', 'data-span': 'all'}), tags)
        self.assertIn(('section', {'class': 'ws-item', 'data-id': 'band', 'data-break': '1'}), tags)
        for kind in ('ws-head--평가', 'ws-band', 'ws-sit--fig', 'ws-ans-table', 'ws-steps', 'ws-choices', 'ws-data',
                     'ws-grid', 'ws-figs', 'ws-ask', 'ws-notice', 'ws-q-text'):
            self.assertTrue(any(kind in attrs.get('class', '').split() for _, attrs in tags), kind)
        for width in ('122.2', '9.2', '23.2', '10.2', '25.1', '60', '128'):
            self.assertIn(('col', {'style': f'width:{width}mm'}), tags)
        for id_ in ('sheet-src', 'sheet-teacher', 'sheet-css', 'sheet-js'):
            self.assertTrue(any(attrs.get('id') == id_ for _, attrs in tags), id_)
        self.assertTrue(any(tag == 'g' and a.get('class') == 'ws-key' for tag, a in tags))
        self.assertIn(('td', {'class': 'ws-ans', 'data-kind': 'draw', 'data-height-mm': '25'}), tags)
        self.assertTrue(any(a.get('rowspan') == '2' and a.get('class') == 'ws-lab' for _, a in tags))
        clean = Tags(student).tags
        self.assertEqual(next(attrs for tag, attrs in clean if tag == 'html')['data-role'], 'student')
        for _, attrs in clean:
            self.assertFalse({'ws-key', 'ws-key-choice', 'ws-teacher-only'} & set(attrs.get('class', '').split()))
            self.assertFalse({'data-src', 'data-answers'} & attrs.keys())
            self.assertNotEqual(attrs.get('id'), 'sheet-teacher')
        for secret in ('숨은 빈칸 정답', '오직 교사만 볼 정답', '교사 전용 캡션', '교사 전용 메모', '교사 전용 풀이',
                       '단계별 해설', '표 속 정답', '질문의 모범 답안'):
            self.assertFalse(secret in student, secret)
        record = self.manifest()
        self.assertEqual(record['tool'], 'pbj-html-sheet')
        self.assertEqual(record['version'], 1)
        self.assertEqual(record['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        for key, path in [('teacher', self.teacher), ('student', self.student)]:
            self.assertEqual(record[key], {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.assertEqual(record['fonts'], {'embedded': False, 'bytes': 0})

    def test_saved_teacher_survives_derive_and_requires_force_to_overwrite(self):
        self.fixture()
        self.cli('build', self.folder)
        baseline = self.manifest()['teacher']
        edited = self.teacher.read_text(encoding='utf-8').replace('data-sheet="1"', 'data-sheet="1" data-saved-at="2026-11-27T00:00:00Z"')
        edited = edited.replace('정리 글', '편집 후 정리')
        self.teacher.write_text(edited, encoding='utf-8')
        self.cli('derive', self.teacher)
        self.assertTrue('편집 후 정리' in self.student.read_text(encoding='utf-8'))
        self.assertEqual(self.manifest()['teacher'], baseline)
        self.assertEqual(self.manifest()['student']['sha256'], hashlib.sha256(self.student.read_bytes()).hexdigest())
        self.assertIn('--force', self.cli('build', self.folder, status=1))
        self.assertTrue('편집 후 정리' in self.teacher.read_text(encoding='utf-8'))
        self.cli('build', self.folder, '--force')
        self.assertTrue('편집 후 정리' not in self.teacher.read_text(encoding='utf-8'))

    def test_local_raster_is_embedded_at_print_resolution(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow가 없는 환경입니다.')
        Image.new('RGB', (1200, 600), 'white').save(self.folder / '그림/좌표.png')
        self.fixture()
        data = json.loads(self.source.read_text(encoding='utf-8'))
        data['items'][0]['blocks'][1]['figure'] = {'src': '그림/좌표.png', 'width_mm': 25.4, 'alt': '그림'}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        images = (attrs for tag, attrs in Tags(self.student.read_text(encoding='utf-8')).tags if tag == 'img')
        image = next(images)
        self.assertEqual(image['style'], 'width:25.4mm')
        mime, payload = image['src'].split(',', 1)
        self.assertEqual(mime, 'data:image/png;base64')
        with Image.open(BytesIO(base64.b64decode(payload))) as drawn:
            self.assertEqual(drawn.size, (300, 150))

    def interactive_fixture(self, policy='시도 후', interactive=True):
        data = {'version': 1, 'meta': {'title': self.title, 'kind': '수업', 'interactive': interactive,
                                       'reveal': policy, 'theme': '정본'},
                'items': [{'id': 'q1', 'hints': ['수를 차례로 확인하세요.'], 'blocks': [
                    {'type': 'heading', 'tag': '활동', 'text': '수 확인', 'icon': 'pencil'},
                    {'type': 'callout', 'kind': '생각', 'lines': ['결과는 {{㉠|$5$;;$+5$}}입니다.']},
                    {'type': 'split', 'left': [{'type': 'table', 'header': ['값'], 'rows': [['{{㉡|$2$}}']]}],
                     'right': [{'type': 'text', 'text': '비교해 봅니다.'}]},
                    {'type': 'ox', 'items': [{'text': '$1<2$이다.', 'answer': 'O'}]},
                    {'type': 'match', 'left': ['한 개', '두 개'], 'right': ['하나', '둘']},
                    {'type': 'order', 'items': ['처음', '중간', '끝']},
                    {'type': 'flow', 'nodes': ['입력', '계산', '결과'], 'arrows': ['더하기', '확인']},
                    {'type': 'numberline', 'range': [-3, 3], 'step': 1, 'plot': {'answer': [-2, 1]}},
                    {'type': 'grid', 'mode': 'coord', 'x': [-3, 3], 'y': [-3, 3], 'unit_mm': 5,
                     'plot': {'mode': 'points', 'answer': [[1, 2], [-1, 0]]}},
                    {'type': 'grid', 'mode': 'coord', 'x': [-3, 3], 'y': [-3, 3], 'unit_mm': 5,
                     'plot': {'mode': 'line', 'answer': 'x+1'}},
                    {'type': 'chart', 'kind': 'bar', 'labels': ['가', '나'], 'values': [2, 3],
                     'plot': {'answer': [2, 3]}, 'y_max': 4},
                    {'type': 'explore', 'fn': 'a*x', 'params': [{'name': 'a', 'min': -2, 'max': 2, 'step': 1, 'value': 1}],
                     'x': [-3, 3], 'y': [-3, 3], 'unit_mm': 5, 'table': [-1, 0, 1]},
                    {'type': 'video', 'url': 'https://youtu.be/abcdefghijk', 'title': '영상'},
                    {'type': 'checklist', 'items': ['다시 확인한다.']},
                    {'type': 'choices', 'options': ['하나', '둘', '셋'], 'answer': [1, 3]},
                    {'type': 'answer', 'rows': [{'label': '설명', 'answer': '잘 숨긴 답안', 'check': 'text', 'accept': ['다른 표현']}]}]}],
                'teacher': {'solutions': [{'item': 'q1', 'answer': '교사용 모범 풀이', 'steps': ['직접 계산한다.'],
                                           'rubric': ['계산 확인'], 'video': {'url': 'https://youtu.be/abcdefghijk'}}]}}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_interactive_blocks_hashes_and_filter(self):
        self.interactive_fixture()
        self.cli('build', self.folder)
        teacher = self.teacher.read_text(encoding='utf-8')
        student = self.student.read_text(encoding='utf-8')
        tags, student_tags = Tags(teacher).tags, Tags(student).tags
        roots = [a for tag, a in tags if tag == 'html'][0]
        self.assertEqual((roots['data-interactive'], roots['data-theme']), ('1', '정본'))
        slots = [a for _, a in tags if a.get('data-slot', '').startswith('q1.')]
        self.assertEqual([a['data-slot'] for a in slots], [f'q1.{i}' for i in range(1, len(slots)+1)])
        for name in ('ws-heading', 'ws-callout', 'ws-split', 'ws-ox', 'ws-match', 'ws-order', 'ws-flow',
                     'ws-nl', 'ws-grid', 'ws-chart', 'ws-explore', 'ws-video', 'ws-checklist', 'ws-hints'):
            self.assertTrue(any(name in a.get('class', '').split() for _, a in tags), name)
        self.assertTrue(any(a.get('style') == 'width:91.5mm' for tag, a in tags if tag == 'col'))
        self.assertTrue(any(a.get('data-map') for tag, a in tags if tag == 'svg'))
        self.assertTrue(any(a.get('data-ymap') for tag, a in tags if tag == 'svg'))
        self.assertTrue(any(a.get('data-yt') == 'abcdefghijk' for _, a in tags))
        self.assertTrue(all('data-check' not in a and 'data-src' not in a for _, a in student_tags))
        self.assertNotIn('잘 숨긴 답안', student)
        self.assertNotIn('교사용 모범 풀이', student)
        table = json.loads((self.folder / '검사/정답표.json').read_text(encoding='utf-8'))
        self.assertEqual(table['salt'], '00112233445566778899aabbccddeeff')
        checks = {a['data-slot']: a for a in slots if 'data-check' in a}
        self.assertEqual(set(checks), {entry['slot'] for entry in table['slots']})
        for entry in table['slots']:
            a = checks[entry['slot']]
            snap = float(a['data-snap']) if 'data-snap' in a else None
            correct = canonical(entry['kind'], entry['right'], snap)
            wrong = canonical(entry['kind'], entry['wrong'], snap)
            self.assertIn(check_hash(table['salt'], entry['kind'], correct), a['data-check-hash'])
            self.assertNotEqual(correct, wrong)
        import re
        encrypted = re.search(r'<script type="application/octet-stream" id="sheet-reveal">([^<]+)</script>', student).group(1)
        raw = base64.b64decode(encrypted)
        key = hashlib.sha256((table['salt'] + '|reveal').encode()).digest()
        payload = json.loads(bytes(v ^ key[i % len(key)] for i, v in enumerate(raw)))
        self.assertEqual(len(payload['items']), 1)
        self.assertIn('모범 풀이', payload['items']['q1']['solution_html'])
        self.assertEqual(payload['items']['q1']['video']['yt'], 'abcdefghijk')

    def test_chart_kinds_and_histogram_bin_widths(self):
        charts = [
            {'type': 'chart', 'kind': 'line', 'labels': ['가', '나'], 'values': [1, 2]},
            {'type': 'chart', 'kind': 'histogram', 'bins': [0, 1, 3], 'values': [2, 3], 'plot': {'answer': [2, 3]}},
            {'type': 'chart', 'kind': 'polygon', 'bins': [0, 1, 3], 'values': [2, 3], 'teacher': True},
            {'type': 'chart', 'kind': 'scatter', 'points': [[-1, 2], [1, -2]], 'x': [-2, 2], 'y': [-3, 3]},
            {'type': 'chart', 'kind': 'bar', 'labels': ['가', '나'], 'values': [-2, 2], 'plot': {'answer': [-2, 2]}},
        ]
        data = {'version': 1, 'meta': {'title': self.title, 'kind': '수업', 'theme': '정본'},
                'items': [{'id': 'q1', 'blocks': charts}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        tags = Tags(self.teacher.read_text(encoding='utf-8')).tags
        hits = [a for _, a in tags if 'ws-bar-hit' in a.get('class', '').split()]
        self.assertEqual(len(hits), 4)
        self.assertAlmostEqual(float(hits[1]['width']) / float(hits[0]['width']), 2)
        self.assertEqual(sum('ws-chart' in a.get('class', '').split() for _, a in tags), 5)
        self.assertTrue(any(a.get('data-ymap', '').startswith('-2 2 ') for _, a in tags))
        student = Tags(self.student.read_text(encoding='utf-8')).tags
        self.assertEqual(sum('ws-chart' in a.get('class', '').split() for _, a in student), 5)
        self.assertFalse(any('ws-key' in a.get('class', '').split() for _, a in student))

    def test_reveal_policies_and_static_mode(self):
        for policy, hashes, reveal_present in [('바로', True, True), ('교사만', True, False), ('없음', False, False)]:
            with self.subTest(policy=policy):
                self.interactive_fixture(policy)
                self.cli('build', self.folder)
                student = self.student.read_text(encoding='utf-8')
                tags = Tags(student).tags
                self.assertEqual(any('data-check-hash' in a for _, a in tags), hashes)
                self.assertEqual(any(a.get('id') == 'sheet-reveal' for _, a in tags), reveal_present)
        self.interactive_fixture(interactive=False)
        self.cli('build', self.folder)
        tags = Tags(self.student.read_text(encoding='utf-8')).tags
        self.assertFalse(any('data-slot' in a or a.get('name') == 'sheet-salt' or a.get('id') == 'sheet-reveal' for _, a in tags))
        self.assertTrue(any('ws-chart' in a.get('class', '').split() for _, a in tags))
        self.assertFalse((self.folder / '검사/정답표.json').exists())
        self.assertTrue((self.folder / '검사/_이전/정답표.json').exists())

    def test_teacher_only_blanks_do_not_consume_student_slots(self):
        picture = {'svg': '<svg viewBox="0 0 10 10"><rect width="10" height="10"/></svg>',
                   'width_mm': 10, 'teacher_only': True, 'caption': '{{|비공개}}'}
        data = {'version': 1, 'meta': {'title': self.title, 'kind': '수업', 'theme': '정본'},
                'items': [{'id': 'q1', 'blocks': [
                    {'type': 'figure', **picture, 'pen': True},
                    {'type': 'situation', 'lines': ['상황'], 'figure': picture},
                    {'type': 'text', 'text': '{{|$2$}}'}]}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        student = self.student.read_text(encoding='utf-8')
        self.assertNotIn('비공개', student)
        slots = [a['data-slot'] for _, a in Tags(student).tags if a.get('data-slot', '').startswith('q1.')]
        self.assertEqual(slots, ['q1.1'])
        table = json.loads((self.folder / '검사/정답표.json').read_text(encoding='utf-8'))
        self.assertEqual([row['slot'] for row in table['slots']], ['q1.1'])

    def test_wrong_answer_avoids_every_alternative(self):
        data = {'version': 1, 'meta': {'title': self.title, 'kind': '수업', 'theme': '정본'},
                'items': [{'id': 'q1', 'blocks': [{'type': 'answer', 'rows': [
                    {'label': '수', 'answer': '$5$', 'accept': ['$6$', '$7$']},
                    {'label': '서술', 'answer': '정답', 'check': 'text', 'accept': ['정답오답']}]}]}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        table = json.loads((self.folder / '검사/정답표.json').read_text(encoding='utf-8'))
        self.assertEqual(table['slots'][0]['wrong'], '8')
        self.assertEqual(table['slots'][1]['wrong'], '정답오답오답')
        teacher = Tags(self.teacher.read_text(encoding='utf-8')).tags
        attrs = [a for _, a in teacher if a.get('data-slot') in ('q1.1', 'q1.2')]
        self.assertEqual(len(attrs[0]['data-check-hash'].split(',')), 3)
        self.assertEqual(len(attrs[1]['data-check-hash'].split(',')), 2)

    def test_derive_reads_teacher_data_check(self):
        self.interactive_fixture()
        data = json.loads(self.source.read_text(encoding='utf-8'))
        data['items'][0]['blocks'][-1]['rows'].append({'label': '자유 응답', 'answer': '설명', 'check': 'off'})
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        source = self.teacher.read_text(encoding='utf-8')
        old = '&quot;right&quot;:&quot;5&quot;'
        self.assertIn(old, source)
        self.teacher.write_text(source.replace(old, '&quot;right&quot;:&quot;7&quot;', 1), encoding='utf-8')
        self.cli('derive', self.teacher)
        table = json.loads((self.folder / '검사/정답표.json').read_text(encoding='utf-8'))
        self.assertEqual(table['slots'][0]['right'], '7')

    def test_sample_math(self):
        data = json.loads((ROOT / 'examples/전체-블록-견본/구성.json').read_text(encoding='utf-8'))
        by_id = {item['id']: item for item in data['items']}
        split = next(b for b in by_id['q1']['blocks'] if b['type'] == 'split')
        cells = split['left'][0]['rows']
        self.assertEqual([int(num(plain(c[0]))) for c in cells], [1, 2, 3])
        self.assertEqual([2*x for x in (1, 2, 3)], [2, 4, 6])
        self.assertIn('$4$', cells[1][1])
        self.assertIn('$6$', cells[2][1])
        self.assertEqual(split['right'][0]['rows'][0]['answer'], '$y=2x$')
        self.assertEqual(plain(split['right'][0]['rows'][0]['accept'][1]), 'y=2×x')
        ox = next(b for b in by_id['q2']['blocks'] if b['type'] == 'ox')
        self.assertEqual([row['answer'] for row in ox['items']], ['O', 'X'])
        self.assertIn('$2$', by_id['q2']['blocks'][-1]['nodes'][1])
        choices = next(b for b in by_id['q3']['blocks'] if b['type'] == 'choices')
        self.assertEqual(num(plain(choices['options'][choices['answer'] - 1])), str(2 * 5))
        match = next(b for b in by_id['q3']['blocks'] if b['type'] == 'match')
        self.assertEqual([2 * int(num(plain(x))) for x in match['left']], [int(num(plain(y))) for y in match['right']])
        split = next(b for b in by_id['q4']['blocks'] if b['type'] == 'split')
        for x, y in split['left'][0]['plot']['answer']:
            self.assertEqual(y, 2*x)
        self.assertEqual(split['right'][0]['plot']['answer'], '2*x')
        self.assertEqual(by_id['q5']['blocks'][1]['plot']['answer'], [2, 4, 6])
        self.assertEqual(by_id['q6']['blocks'][-1]['plot']['answer'], [2, 4, 6])
        self.assertEqual([2*1, 2*2, -2*1, -2*2], [2, 4, -2, -4])

    def test_lesson_and_practice_heads(self):
        for kind, cols, head in [('수업', 2, '짝의 이름'), ('연습', 1, '수학 연습지')]:
            with self.subTest(kind=kind):
                self.fixture(kind, cols)
                self.cli('build', self.folder)
                teacher = self.teacher.read_text(encoding='utf-8')
                self.assertIn(f'class="ws-head ws-head--{kind}"', teacher)
                self.assertIn(f'data-columns="{cols}"', teacher)
                self.assertIn(head, teacher)
