import base64
import builtins
import contextlib
import hashlib
import io
from html.parser import HTMLParser
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_answer import canonical, check_hash


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

    def cli(self, *args, status=0, salt='00112233445566778899aabbccddeeff'):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT / 'scripts/sheet.py'), *map(str, args)],
                                env={**os.environ, 'SHEET_FONT_DIR': self.temp.name, 'SHEET_SYNC_CONFIG': str(Path(self.temp.name) / 'sync.json'), 'SHEET_TEST_SALT': salt}, capture_output=True, encoding='utf-8')
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
        for content in (teacher, student):
            script = re.search(r'<script id="sheet-js">(.*?)</script>', content, re.S)[1]
            self.assertEqual(record['sheet_js_sha256'], hashlib.sha256(script.encode('utf-8')).hexdigest())

    def test_build_without_fonttools_recommends_doctor_and_alternate_python(self):
        import sheet_build

        self.fixture(kind='수업')
        fonts = Path(self.temp.name)
        (fonts / 'NotoSansKR-VF.ttf').touch()
        alternate = fonts / '대체 환경' / 'python.exe'
        import_original = builtins.__import__

        def without_fonttools(name, *args, **kwargs):
            if name == 'fontTools':
                raise ImportError(name)
            return import_original(name, *args, **kwargs)

        with (patch.dict(os.environ, {'SHEET_FONT_DIR': str(fonts), 'SHEET_SYNC_CONFIG': str(fonts / 'sync.json')}),
              patch.object(builtins, '__import__', side_effect=without_fonttools),
              patch.object(sheet_build, 'find_usable_python', return_value=alternate),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(sheet_build.cmd_build(self.folder), 0, output.getvalue())
        self.assertIn('글꼴을 내장하지 않았습니다', output.getvalue())
        self.assertIn('sheet.py" doctor', output.getvalue())
        self.assertIn(f'"{alternate}" -X utf8 "{ROOT / "scripts/sheet.py"}" build', output.getvalue())
        self.assertEqual(self.manifest()['fonts'], {'embedded': False, 'bytes': 0})

    def test_doctor_tries_other_pythons_in_order_and_only_recommends_one_that_imports_everything(self):
        import sheet
        import sheet_common

        candidates = [Path(self.temp.name) / f'python {i}.exe' for i in range(4)]
        for path in candidates:
            path.touch()
        for usable in (True, False):
            with self.subTest(usable=usable):
                results = [subprocess.TimeoutExpired('python', 5), subprocess.CompletedProcess([], 1),
                           subprocess.CompletedProcess([], 0 if usable else 1), subprocess.CompletedProcess([], 1)]
                with (patch.object(sheet_common, '_python_candidates', return_value=candidates),
                      patch.object(sheet_common.subprocess, 'run', side_effect=results) as probe,
                      patch.object(sheet.importlib, 'import_module', side_effect=ImportError),
                      contextlib.redirect_stdout(io.StringIO()) as output):
                    self.assertEqual(sheet.cmd_doctor(), 0)
                tried = candidates[:3] if usable else candidates
                self.assertEqual([call.args[0][0] for call in probe.call_args_list], list(map(str, tried)))
                for call in probe.call_args_list:
                    self.assertEqual(call.kwargs['timeout'], 5)
                    self.assertIn('import fontTools, PIL, pymupdf', call.args[0][-1])
                if usable:
                    self.assertIn(f'"{candidates[2]}" -X utf8 "{ROOT / "scripts/sheet.py"}" build', output.getvalue())
                    self.assertNotIn('pip install', output.getvalue())
                else:
                    self.assertIn('python -m pip install fonttools pillow pymupdf', output.getvalue())

    def test_derive_refuses_changed_engine_and_keeps_all_outputs(self):
        self.interactive_fixture()
        self.cli('build', self.folder)
        teacher = self.teacher.read_text(encoding='utf-8')
        edited = teacher.replace('<script id="sheet-js">', '<script id="sheet-js">\n/* 직접 편집 */', 1)
        self.assertNotEqual(edited, teacher)
        self.teacher.write_text(edited, encoding='utf-8')
        paths = (self.teacher, self.student, self.record, self.folder / '검사/정답표.json')
        before = {path: path.read_bytes() for path in paths}
        output = self.cli('derive', self.teacher, status=1)
        self.assertIn('foreign-code', output)
        self.assertIn('구성.json', output)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_derive_accepts_edited_sheet_from_an_older_build_record(self):
        # 스크립트 해시가 없는 예전 조립 기록이면, 스킬을 업데이트한 뒤에도 예전 엔진으로 만든 편집본을 derive할 수 있어야 한다.
        self.interactive_fixture()
        self.cli('build', self.folder)
        record = self.manifest()
        record.pop('sheet_js_sha256')
        self.record.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        teacher = self.teacher.read_text(encoding='utf-8')
        older = teacher.replace('<script id="sheet-js">', '<script id="sheet-js">\n/* 예전 엔진 */', 1)
        self.assertNotEqual(older, teacher)
        self.teacher.write_text(older, encoding='utf-8')
        output = self.cli('derive', self.teacher)
        self.assertNotIn('foreign-code', output)
        # 덧붙인 스크립트는 예전 기록이어도 막는다.
        self.teacher.write_text(older.replace('</body>', '<script id="extra"></script></body>'), encoding='utf-8')
        self.assertIn('foreign-code', self.cli('derive', self.teacher, status=1))

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

    def edition(self, path):
        return next(json.loads(a['content'])['edition'] for _, a in Tags(path.read_text(encoding='utf-8')).tags
                    if a.get('name') == 'sheet-build')

    def public_data(self):
        import re
        student = self.student.read_text(encoding='utf-8')
        salt = next(a['content'] for _, a in Tags(student).tags if a.get('name') == 'sheet-salt')
        encrypted = re.search(r'<script type="application/octet-stream" id="sheet-reveal">([^<]+)</script>', student).group(1)
        key = hashlib.sha256((salt + '|reveal').encode()).digest()
        return json.loads(bytes(v ^ key[i % len(key)] for i, v in enumerate(base64.b64decode(encrypted))))

    def test_derive_refreshes_public_solution_and_keeps_edition_if_unchanged(self):
        self.interactive_fixture()
        self.cli('build', self.folder)
        old_edition, old_public = self.edition(self.student), self.public_data()
        self.cli('derive', self.teacher)
        self.assertEqual((self.edition(self.student), self.public_data()), (old_edition, old_public))
        edited = self.teacher.read_text(encoding='utf-8').replace('직접 계산한다.', '다른 식으로 계산한다.')
        self.teacher.write_text(edited.replace('계산 확인', '대입 확인'), encoding='utf-8')
        self.cli('derive', self.teacher)
        updated = self.public_data()
        self.assertIn('다른 식으로 계산한다.', updated['items']['q1']['solution_html'])
        self.assertNotIn('직접 계산한다.', updated['items']['q1']['solution_html'])
        self.assertEqual(updated['items']['q1']['rubric'], ['대입 확인'])
        self.assertEqual(updated['items']['q1']['video'], old_public['items']['q1']['video'])
        self.assertEqual(updated['slots'], old_public['slots'])
        new_edition = self.edition(self.student)
        self.assertNotEqual(new_edition, old_edition)
        self.assertEqual(self.edition(self.teacher), new_edition)
        self.cli('derive', self.teacher)
        self.assertEqual(self.edition(self.student), new_edition)

    def test_edition_tracks_embedded_image_bytes_not_random_salt(self):
        self.interactive_fixture()
        data = json.loads(self.source.read_text(encoding='utf-8'))
        data['items'][0]['blocks'].append({'type': 'figure', 'src': '그림/도형.svg', 'width_mm': 25, 'alt': '도형'})
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        image = self.folder / '그림/도형.svg'
        image.write_text('<svg viewBox="0 0 5 5"><path d="M0 0L5 5"/></svg>', encoding='utf-8')
        self.cli('build', self.folder)
        before = self.edition(self.student)
        self.cli('build', self.folder, salt='abcdefabcdefabcdefabcdefabcdefab')
        self.assertEqual(self.edition(self.student), before)
        image.write_text('<svg viewBox="0 0 5 5"><path d="M0 5L5 0"/></svg>', encoding='utf-8')
        self.cli('build', self.folder)
        self.assertNotEqual(self.edition(self.student), before)

    def test_order_print_note_matches_worksheet_kind(self):
        from sheet_build import font_chars
        self.assertTrue(set('써 봅시다') <= set(font_chars({})))
        for kind, ending in [(None, '써 봅시다.'), ('수업', '써 봅시다.'),
                             ('연습', '쓰시오.'), ('평가', '쓰시오.')]:
            with self.subTest(kind=kind):
                meta = {'title': self.title}
                if kind is not None:
                    meta['kind'] = kind
                data = {'version': 1, 'meta': meta,
                        'items': [{'id': 'q1', 'blocks': [{'type': 'order', 'items': ['처음', '중간', '끝']}]}]}
                self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                self.cli('build', self.folder)
                note = f'<p class="ws-print-only">□ 안에 순서대로 1, 2, 3, …을 {ending}</p>'
                for path in (self.teacher, self.student):
                    self.assertIn(note, path.read_text(encoding='utf-8'))

    def test_table_uses_widest_blank_in_header_and_body_only(self):
        from sheet_build import Renderer
        block = {'type': 'table', 'header': ['{{㉠|둘}}', '값'],
                 'rows': [['{{|짧음}}', '{{㉡|$5$;;다섯|30}}'], ['전: {{㉢|둘}}', '—']]}
        renderer = Renderer({'meta': {'title': '표 시험', 'interactive': False}}, self.folder, [])
        tags = Tags(renderer.block(block, 'q1')).tags
        self.assertEqual([attrs['style'] for _, attrs in tags if attrs.get('class') == 'ws-blank'],
                         ['--ws-blank-min:30mm'] * 4)
        self.assertIn('--ws-blank-min:9mm', renderer.block({'type': 'text', 'text': '{{㉣|둘}}'}, 'q1'))

    def test_table_unit_overrides_ambiguous_sentence(self):
        from sheet_build import Renderer
        data = {'meta': {'title': self.title, 'interactive': True}, 'items': [{'id': 'q1', 'blocks': [
            {'type': 'table', 'header': ['항목', '길이(cm)'], 'rows': [['가', '{{|3}}일 수 있다']]}]}]}
        renderer = Renderer(data, self.folder, [])
        tags = Tags(renderer.render_items()).tags
        slot = next(attrs for _, attrs in tags if attrs.get('data-slot') == 'q1.1')
        hashes = slot['data-check-hash'].split(',')
        self.assertIn(check_hash(renderer.salt, 'number', '3@cm'), hashes)
        self.assertNotIn(check_hash(renderer.salt, 'number', '3@일'), hashes)

    def test_table_unit_defaults_and_overrides_survive_derive(self):
        data = {'version': 1, 'meta': {'title': self.title, 'interactive': True}, 'items': [{'id': 'q1', 'blocks': [
            {'type': 'table', 'unit': '점', 'header': ['반', '최솟값', '길이(cm)', '{{|2}}'],
             'rows': [['1반', '{{|45}}', '{{|3}}', ''], ['시간(분)', '{{|50}}', '{{|4}}', '']]}]}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        expected = [('2', '점'), ('45', '점'), ('3', 'cm'), ('50', '분'), ('4', 'cm')]
        for after_derive in (False, True):
            with self.subTest(after_derive=after_derive):
                if after_derive:
                    self.cli('derive', self.teacher)
                slots = [a for _, a in Tags(self.student.read_text(encoding='utf-8')).tags
                         if a.get('data-slot', '').startswith('q1.')]
                self.assertEqual(len(slots), len(expected))
                salt = '00112233445566778899aabbccddeeff'
                for attrs, (number, unit) in zip(slots, expected):
                    self.assertEqual(attrs.get('data-check-unit'), '1')
                    self.assertEqual(set(attrs['data-check-hash'].split(',')),
                                     {check_hash(salt, 'number', v) for v in (number, f'{number}@{unit}', f'written:{number}')})

    def test_plan_rejects_invalid_table_unit(self):
        data = {'version': 1, 'meta': {'title': self.title}, 'items': [{'id': 'q1', 'blocks': [
            {'type': 'table', 'unit': '점수', 'header': ['값'], 'rows': [['{{|45}}']]}]}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.assertIn('table-unit', self.cli('plan', self.folder, status=1))

    def test_coordinate_names_clear_tick_labels(self):
        from itertools import combinations
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer
        renderer = Renderer({'meta': {'title': '축 시험', 'interactive': False}}, self.folder, [])
        for x, y in [([0, 5], [0, 10]), ([-3, 3], [-6, 6]), ([0, 10], [-5, 5]), ([-5, 0], [0, 8])]:
            with self.subTest(x=x, y=y):
                svg = ET.fromstring(renderer.grid({'mode': 'coord', 'x': x, 'y': y, 'unit_mm': 6})).find('svg')
                labels = {}
                for node in svg.findall('text'):
                    left, base, size = map(float, (node.get('x'), node.get('y'), node.get('font-size')))
                    width = sum(.8 if c == 'O' else .65 for c in node.text) * size
                    anchor = {'start': 0, 'middle': .5, 'end': 1}[node.get('text-anchor', 'start')]
                    left -= width * anchor
                    labels[node.text, left, base] = (left, base - .85 * size, left + width, base + .25 * size)
                self.assertTrue({'x', 'y', 'O'} <= {key[0] for key in labels})
                for a, b in combinations(labels, 2):
                    if not {a[0], b[0]} & {'x', 'y', 'O'}:
                        continue
                    l, t, r, d = labels[a]
                    bl, bt, br, bd = labels[b]
                    self.assertTrue(r <= bl or br <= l or d <= bt or bd <= t, (a, b))

    def test_polygon_extends_to_zero_neighbor_midpoints(self):
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer
        renderer = Renderer({'meta': {'title': self.title, 'interactive': False}}, self.folder, [])
        svg = ET.fromstring(renderer.chart({'kind': 'polygon', 'bins': [15, 20, 25, 30, 35, 40],
                                            'values': [2, 4, 6, 5, 3], 'y_max': 6})).find('svg')
        self.assertEqual([e.text for e in svg.findall('text') if e.get('y') == '57'], ['10', '15', '20', '25', '30', '35', '40', '45'])
        points = [tuple(map(float, pair.split(','))) for pair in svg.find('polyline').get('points').split()]
        self.assertEqual(len(points), 7)
        for point, target in [(points[0], (12.5, 0)), (points[-1], (42.5, 0))]:
            self.assertAlmostEqual(10 + (point[0] - 14) / 112 * 35, target[0])
            self.assertAlmostEqual((53 - point[1]) / 49 * 6, target[1])

    def test_frequency_zero_uses_positive_axis(self):
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer
        renderer = Renderer({'meta': {'title': self.title, 'interactive': False}}, self.folder, [])
        for kind in ('histogram', 'polygon'):
            for maximum in (0, -2):
                with self.subTest(kind=kind, maximum=maximum):
                    svg = ET.fromstring(renderer.chart({'kind': kind, 'bins': [0, 5, 10],
                                                         'values': [0, 0], 'y_max': maximum})).find('svg')
                    low, high, bottom, top = map(float, svg.get('data-ymap').split())
                    self.assertEqual(low, 0)
                    self.assertGreater(high, 0)
                    if kind == 'polygon':
                        self.assertTrue(all(float(p.split(',')[1]) == bottom
                                            for p in svg.find('polyline').get('points').split()))

    def test_snapshots_use_one_row_for_tall_graphs(self):
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer
        renderer = Renderer({'meta': {'title': self.title, 'interactive': False}}, self.folder, [])
        block = {'fn': 'a*x**2', 'params': [{'name': 'a', 'value': 1}], 'x': [-4, 4], 'y': [-9, 9],
                 'unit_mm': 5, 'print': [{'a': x} for x in (.5, -.5, 2, -2)]}
        root = ET.fromstring(renderer.explore(block))
        snapshots = root.find(".//div[@class='ws-explore-snapshots']")
        self.assertIn('--ws-snapshot-cols:4', snapshots.get('style', ''))
        figures = list(snapshots)
        self.assertEqual(len(figures), 4)
        for figure in figures:
            svg = figure.find('svg')
            width = float(svg.get('width')[:-2])
            height = float(svg.get('height')[:-2])
            viewbox = list(map(float, svg.get('viewBox').split()))
            self.assertGreater(width, 28)
            self.assertGreater(height, 60)
            for text in svg.findall('text'):
                size = float(text.get('font-size')) * height / viewbox[3] * 72 / 25.4
                self.assertGreaterEqual(size, 6)

    def test_svg_labels_are_upright_for_points_in_both_sources(self):
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer, figure_svg
        source = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
                  '<defs><path id="p" d="M0 0L1 1"/></defs><use xlink:href="#p"/>'
                  '<text>A′</text><text>a₂</text></svg>')
        direct = figure_svg(source)
        file = self.folder / '그림' / 'labels.svg'
        file.write_text(source, encoding='utf-8')
        renderer = Renderer({'meta': {'title': self.title, 'interactive': False}}, self.folder, [])
        image = next(a for tag, a in Tags(renderer.image({'src': '그림/labels.svg', 'width_mm': 30}, 30, math_labels=True)).tags if tag == 'img')
        local = base64.b64decode(image['src'].split(',', 1)[1]).decode('utf-8')
        for svg in (direct, local):
            root = ET.fromstring(svg)
            ns = {'s': 'http://www.w3.org/2000/svg'}
            self.assertEqual([x.text for x in root.findall('s:text', ns)], ['A′', '𝑎₂'])
            self.assertEqual(root.find('s:use', ns).get('{http://www.w3.org/1999/xlink}href'), '#p')
            self.assertTrue(all('font-family:Cambria Math' in x.get('style') for x in root.findall('s:text', ns)))

    def test_svg_math_labels_override_author_styles(self):
        from xml.etree import ElementTree as ET
        from sheet_build import figure_svg, font_chars
        svg = ET.fromstring(figure_svg('<svg><text font-family="Arial" style="font-family:Arial!important">l′</text>'
                                       '<text>A<tspan font-family="Arial">₂</tspan></text><text>64°</text></svg>'))
        labels = list(svg)
        self.assertEqual([''.join(label.itertext()) for label in labels], ['𝑙′', 'A₂', '64°'])
        self.assertTrue(all('font-family:Cambria Math' in label.get('style') for label in labels))
        self.assertIn('𝑙', font_chars({}))

    def test_figure_overlay_is_red_private_and_does_not_change_student_edition(self):
        from xml.etree import ElementTree as ET
        figure = {'type': 'figure', 'width_mm': 166, 'pen': True,
                  'svg': '<svg viewBox="0 0 166 40" width="166" height="50" preserveAspectRatio="xMinYMid">'
                         '<title>밑그림의 설명</title><circle cx="20" cy="28" r="2"/></svg>'}
        overlay = ('<svg viewBox="0,0,166,40" width="40" style="height:20px">'
                   '<g fill="none" stroke="black"><path style="stroke:blue!important;fill:none" d="M20 28H80"/></g>'
                   '<circle cx="80" cy="28" r="2"/></svg>')
        data = {'version': 1, 'meta': {'title': self.title, 'kind': '수업', 'sync': False},
                'items': [{'id': 'q1', 'blocks': [figure]}]}
        self.source.write_text(json.dumps(data), encoding='utf-8')
        self.cli('build', self.folder)
        baseline = self.edition(self.student)
        figure['answer'] = {'svg': overlay}
        self.source.write_text(json.dumps(data), encoding='utf-8')
        self.cli('build', self.folder)
        teacher, student = (path.read_text(encoding='utf-8') for path in (self.teacher, self.student))
        tags = Tags(teacher).tags
        pen = next(a for tag, a in tags if tag == 'figure')
        self.assertEqual((pen['data-pen'], pen['data-check-kind'], pen['data-slot']), ('1', 'draw', 'q1.1'))
        base, answer = [a for tag, a in tags if tag == 'img']
        self.assertEqual((base['alt'], answer['alt'], answer['aria-hidden']), ('밑그림의 설명', '', 'true'))
        self.assertEqual(answer['class'], 'ws-key ws-fig-answer')
        self.assertEqual(base['style'], answer['style'])
        root = ET.fromstring(base64.b64decode(answer['src'].split(',', 1)[1]))
        self.assertEqual((root.get('width'), root.get('height'), root.get('preserveAspectRatio')), ('166', '50', 'xMinYMid'))
        self.assertEqual(root.get('fill'), '#c00000')
        self.assertEqual(root[0].attrib, {'fill': 'none', 'stroke': '#c00000'})
        self.assertEqual(root[0][0].get('style'), 'stroke:#c00000!important;fill:none')
        self.assertEqual([a for tag, a in Tags(student).tags if tag == 'img'], [base])
        self.assertNotIn(answer['src'], student)
        self.assertEqual(self.edition(self.student), baseline)
        self.assertEqual(self.public_data()['slots'], {})
        self.cli('derive', self.teacher)
        self.assertNotIn(answer['src'], self.student.read_text(encoding='utf-8'))

    def test_svg_answer_recolors_without_losing_transparency_or_local_paint(self):
        from xml.etree import ElementTree as ET
        figure = {'type': 'figure', 'width_mm': 60, 'alt': '두 점', 'svg': '<svg viewBox="0 0 60 20"/>',
                  'answer': '<svg viewBox="0 0 60 20"><defs><linearGradient id="fade">'
                            '<stop stop-color="rgba(0,0,0,.3)" stop-opacity=".5"/></linearGradient></defs>'
                            '<rect width="60" height="20" fill="rgba(0,0,0,0)"/>'
                            '<path fill="url(#fade)" opacity=".7" style="fill-opacity:.4;stroke:hsla(240 50% 50% / 25%) !important"/>'
                            '<circle fill="transparent" style="stroke:rgba(0,0,0,.5)"/></svg>'}
        self.source.write_text(json.dumps({'version': 1, 'meta': {'title': self.title, 'sync': False},
                                          'items': [{'id': 'q1', 'blocks': [figure]}]}), encoding='utf-8')
        self.cli('build', self.folder)
        answer = next(a['src'] for tag, a in Tags(self.teacher.read_text(encoding='utf-8')).tags
                      if tag == 'img' and 'ws-fig-answer' in a.get('class', ''))
        defs, rect, path, circle = ET.fromstring(base64.b64decode(answer.split(',', 1)[1]))
        self.assertEqual(rect.get('fill'), 'rgba(192,0,0,0)')
        self.assertEqual(defs[0][0].attrib, {'stop-color': 'rgba(192,0,0,.3)', 'stop-opacity': '.5'})
        self.assertEqual(path.attrib, {'fill': 'url(#fade)', 'opacity': '.7',
                                       'style': 'fill-opacity:.4;stroke:rgba(192,0,0,25%)!important'})
        self.assertEqual(circle.attrib, {'fill': 'transparent', 'style': 'stroke:rgba(192,0,0,.5)'})
        self.assertNotIn(answer, self.student.read_text(encoding='utf-8'))

    def test_source_audit_distinguishes_public_box_coordinates_and_private_images(self):
        from sheet_build import strip_teacher
        from sheet_check import _audit_source
        uri = 'data:image/svg+xml;base64,PHN2Zy8+'
        teacher = ('<html data-sheet="1" data-role="teacher"><div id="sheet-src">'
                   '<figure class="ws-chart"><svg data-chart="box"><path d="M111.597 1V15"/>'
                   '<g class="ws-key"><path d="M11.5 8H16"/></g></svg></figure>'
                   f'<img class="ws-key ws-fig-answer" src="{uri}"></div></html>')
        student = strip_teacher(teacher)
        problems = []
        _audit_source(teacher, student, ['11.5'], problems)
        self.assertEqual(problems, [])
        for extra in ('<p>11.5</p>', '<p aria-label="11.5"></p>', f'<img src="{uri}">'):
            with self.subTest(extra=extra):
                problems = []
                _audit_source(teacher, student.replace('</div>', extra + '</div>'), ['11.5'], problems)
                self.assertEqual([issue['code'] for issue in problems], ['answer-leak'])
        problems = []
        _audit_source(teacher, student.replace('</svg>', '<path d="M11.5 8H16"/></svg>'), ['11.5'], problems)
        self.assertEqual([issue['code'] for issue in problems], ['answer-leak'])

    def test_svg_alt_priority_and_local_svg_fallback(self):
        from sheet_build import Renderer
        renderer = Renderer({'meta': {'title': self.title}}, self.folder, [])
        aria = '<svg aria-label="읽어 줄 &amp; 설명"><title>제목</title></svg>'
        title = '<svg xmlns="http://www.w3.org/2000/svg"><title>다른\n제목</title></svg>'
        (self.folder / '그림/제목.svg').write_text(title, encoding='utf-8')
        for picture, expected in [({'svg': aria}, '읽어 줄 & 설명'), ({'svg': title}, '다른 제목'),
                                  ({'svg': aria, 'alt': '직접 쓴 설명'}, '직접 쓴 설명'),
                                  ({'src': '그림/제목.svg'}, '다른 제목')]:
            tags = Tags(renderer.image({**picture, 'width_mm': 20}, 40)).tags
            self.assertEqual(tags[0][1]['alt'], expected)

    def test_box_chart_has_common_axis_and_private_marks_only(self):
        import re
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer
        chart = {'type': 'chart', 'kind': 'box', 'x': [0, 10], 'x_step': 2, 'x_label': '(분)',
                 'width_mm': 130, 'groups': [{'label': '가', 'data': [8, 2, 4, 6, 10]},
                                             {'label': '나', 'five': [5, 5, 5, 5, 5]}],
                 'teacher': True, 'pen': True, 'guides': True}
        data = {'version': 1, 'meta': {'title': self.title, 'reveal': '바로', 'sync': False},
                'items': [{'id': 'q1', 'blocks': [chart]}]}
        figure = ET.fromstring(Renderer(data, self.folder, []).chart(chart))
        svg = figure.find('svg')
        self.assertEqual((figure.get('class'), figure.get('data-pen'), figure.get('data-check-kind')),
                         ('ws-fig ws-chart', '1', 'draw'))
        ticks = [t for t in svg.findall('text') if t.text.isdecimal()]
        self.assertEqual([t.text for t in ticks], ['0', '2', '4', '6', '8', '10'])
        self.assertTrue(all(float(t.get('font-size')) * 72 / 25.4 >= 6 for t in ticks))
        self.assertEqual(svg.find("path[@class='ws-chart-guides']").get('d').count('V'), len(ticks))
        key = svg.find("g[@class='ws-key']")
        self.assertEqual(len(key), 2)
        self.assertEqual(key.get('stroke'), 'currentColor')
        axis = {int(t.text): float(t.get('x')) for t in ticks}
        expected = [axis[2], (axis[2] + axis[4]) / 2, (axis[8] + axis[10]) / 2, axis[10]]
        segment = re.match(r'M([\d.]+) 8H([\d.]+)M([\d.]+) 8H([\d.]+)', key[0].get('d'))
        for x, result in zip(map(float, segment.groups()), expected):
            self.assertAlmostEqual(x, result)
        self.assertTrue(key[0].get('d').endswith(f'M{axis[6]:.12g} 4.2V11.8'))
        self.assertNotIn('Z', key[1].get('d'), '상자의 길이가 0이면 중앙값 선이 남는다')
        single = ET.fromstring(Renderer(data, self.folder, []).chart({**chart, 'groups': chart['groups'][:1]})).find('svg')
        self.assertEqual((svg.get('height'), single.get('height')), ('44.5mm', '30.5mm'))
        self.source.write_text(json.dumps(data), encoding='utf-8')
        self.cli('build', self.folder)
        student = self.student.read_text(encoding='utf-8')
        self.assertNotIn(key[0].get('d'), student)
        self.assertNotIn(key[1].get('d'), student)
        self.assertEqual(self.public_data()['slots'], {})
        before = self.edition(self.student)
        chart['groups'][1]['five'] = [4] * 5
        self.source.write_text(json.dumps(data), encoding='utf-8')
        self.cli('build', self.folder)
        self.assertEqual(self.edition(self.student), before)
        chart['teacher'] = False
        self.source.write_text(json.dumps(data), encoding='utf-8')
        self.cli('build', self.folder)
        self.assertIn(key[0].get('d'), self.student.read_text(encoding='utf-8'))
        self.assertNotEqual(self.edition(self.student), before)

    def test_dense_chart_axes_space_numbers_without_removing_ticks_or_guides(self):
        from xml.etree import ElementTree as ET
        from sheet_build import Renderer
        renderer = Renderer({'meta': {'title': self.title}}, self.folder, [])
        dense = {'type': 'chart', 'kind': 'box', 'x': [0, 100], 'x_step': 5, 'width_mm': 65,
                 'guides': True, 'groups': [{'label': '1반', 'data': [45, 55, 60, 85, 100]}]}
        polygon = {'type': 'chart', 'kind': 'polygon', 'width_mm': 65, 'height_mm': 28,
                   'bins': list(range(0, 101, 5)), 'values': [20] * 20, 'y_step': 1}
        histogram = {**polygon, 'kind': 'histogram', 'teacher': True}
        scatter = {'type': 'chart', 'kind': 'scatter', 'width_mm': 65, 'points': [[10, 10]],
                   'x': [0, 100], 'x_step': 2, 'y': [0, 20], 'y_step': 1, 'height_mm': 28}
        for block, ends, tick_count in [(dense, ('0', '100'), 21), (polygon, ('−5', '105'), 23),
                                        (histogram, ('0', '100'), 21), (scatter, ('0', '100'), 51)]:
            with self.subTest(kind=block['kind']):
                svg = ET.fromstring(renderer.chart(block)).find('svg')
                xlabels = [t for t in svg.findall('text') if t.get('text-anchor') == 'middle']
                self.assertEqual((xlabels[0].text, xlabels[-1].text), ends)
                self.assertLess(len(xlabels), tick_count)
                for a, b in zip(xlabels, xlabels[1:]):
                    # With the print font, a digit is about 0.56 em wide.
                    width = (len(a.text) + len(b.text)) * float(a.get('font-size')) * .56
                    self.assertGreater(float(b.get('x')) - float(a.get('x')), width / 2)
                if block is dense:
                    self.assertEqual([t.text for t in xlabels[:3]], ['0', '10', '20'])
                    self.assertEqual(svg.find("path[@class='ws-chart-guides']").get('d').count('V'), 21)
                    self.assertEqual(svg.findall('path')[1].get('d').count('v'), 21)
                elif block is polygon:
                    self.assertEqual(sum(p.get('d').count('v1.2') for p in svg.findall('path')), 23)
                elif block is scatter:
                    self.assertEqual(sum(p.get('d').count('v1.2') for p in svg.findall('path')), 51)
                elif block is histogram:
                    guides = svg.find("path[@class='ws-chart-guides']").get('d')
                    self.assertEqual((guides.count('V'), guides.count('H')), (20, 20))
                if block is not dense:
                    ylabels = [t for t in svg.findall('text') if t.get('text-anchor') == 'end']
                    self.assertEqual((ylabels[0].text, ylabels[-1].text), ('0', '20'))
                    self.assertLess(len(ylabels), 21)

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
        self.assertEqual(set(attrs[0]['data-check-hash'].split(',')),
                         {check_hash('00112233445566778899aabbccddeeff', 'number', v)
                          for v in ('5', '6', '7', 'written:5', 'written:6', 'written:7')})
        self.assertEqual(len(attrs[1]['data-check-hash'].split(',')), 2)

    def test_derive_rebuilds_edited_quartile_and_writing_slots_like_build(self):
        data = {'version': 1, 'meta': {'title': self.title, 'reveal': '교사만', 'theme': '정본'},
                'items': [{'id': 'q2', 'blocks': [
                    {'type': 'text', 'text': '자료: $8, 10, 12, 14, 15, 16, 19, 21, 24$'},
                    {'type': 'table', 'header': ['항목', '시간 (분)'], 'rows': [['제1사분위수', '{{|$11$}}']]},
                    {'type': 'answer', 'rows': [{'label': '값', 'answer': '$5$', 'accept': ['$7$']},
                                               {'label': '설명', 'answer': '$2$', 'check': 'off'},
                                               {'label': '식', 'answer': '이전 식', 'check': 'text'}]}]}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        source = self.teacher.read_text(encoding='utf-8')
        edited = (source.replace('8, 10, 12, 14,', '8, 10, 14, 14,').replace('<mn>12</mn>', '<mn>14</mn>', 1)
                  .replace('{{|$11$}}', '{{|$12$}}').replace('<mn>11</mn>', '<mn>12</mn>')
                  .replace('data-src="$5$"><math><mn>5</mn>', 'data-src="$6.0$"><math><mn>6.0</mn>')
                  .replace('data-src="이전 식">이전 식', 'data-src="$y=2x$">새 식'))
        self.teacher.write_text(edited, encoding='utf-8')
        self.cli('derive', self.teacher)
        table_path = self.folder / '검사/정답표.json'
        derived_table = table_path.read_bytes()
        table = json.loads(derived_table)
        self.assertEqual([(a['slot'], a['right'], a['wrong']) for a in table['slots'][:2]],
                         [('q2.1', '12', '13'), ('q2.2', '6.0', '8')])
        def slots(path):
            return {a['data-slot']: a for _, a in Tags(path.read_text(encoding='utf-8')).tags
                    if a.get('data-slot', '').startswith('q2.')}
        student_slots, teacher_slots = slots(self.student), slots(self.teacher)
        self.assertEqual(student_slots['q2.2']['data-input-mode'], 'decimal')
        self.assertEqual(student_slots['q2.3']['data-check-kind'], 'open')
        self.assertEqual(student_slots['q2.4']['data-check-kind'], 'text')
        self.assertIn(check_hash(table['salt'], 'number', '12@분'), student_slots['q2.1']['data-check-hash'])
        self.assertNotIn(check_hash(table['salt'], 'number', '11'), student_slots['q2.1']['data-check-hash'])
        blocks = data['items'][0]['blocks']
        blocks[0]['text'] = blocks[0]['text'].replace('12, 14', '14, 14')
        blocks[1]['rows'][0][1] = '{{|$12$}}'
        blocks[2]['rows'][0]['answer'] = '$6.0$'
        blocks[2]['rows'][2]['answer'] = '$y=2x$'
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('build', self.folder)
        self.assertEqual(slots(self.student), student_slots)
        self.assertEqual(slots(self.teacher), teacher_slots)
        self.assertEqual(table_path.read_bytes(), derived_table)

    def test_derive_rejects_changed_blank_count_or_slot_name_before_writing(self):
        self.interactive_fixture()
        self.cli('build', self.folder)
        original = self.teacher.read_text(encoding='utf-8')
        table = self.folder / '검사/정답표.json'
        before = [path.read_bytes() for path in (self.student, self.record, table)]
        for edited, detail in [(original.replace('{{㉠|$5$;;$+5$}}', '{{㉠|$5$}}와 {{|$6$}}'), '빈칸'),
                               (original.replace('data-slot="q1.1"', 'data-slot="q9.1"'), 'q9.1'),
                               (original.replace('data-slot="q1.1"', 'data-slot="q1.99"'), 'q1.99')]:
            with self.subTest(detail=detail):
                self.teacher.write_text(edited, encoding='utf-8')
                output = self.cli('derive', self.teacher, status=1)
                self.assertIn('q1', output)
                self.assertIn(detail, output)
                self.assertEqual(self.teacher.read_text(encoding='utf-8'), edited)
                self.assertEqual([path.read_bytes() for path in (self.student, self.record, table)], before)

    def test_derive_policy_transitions_match_fresh_student_build(self):
        def state():
            tags = Tags(self.student.read_text(encoding='utf-8')).tags
            self.assertFalse(any('data-check' in attrs or 'data-src' in attrs for _, attrs in tags))
            published = any(attrs.get('id') == 'sheet-reveal' for _, attrs in tags)
            return [attrs for _, attrs in tags if attrs.get('data-slot')], self.public_data() if published else None
        for start, end in [('교사만', '없음'), ('바로', '없음'), ('없음', '시도 후')]:
            with self.subTest(start=start, end=end):
                self.interactive_fixture(start)
                self.cli('build', self.folder)
                edited = self.teacher.read_text(encoding='utf-8').replace(f'data-reveal="{start}"', f'data-reveal="{end}"', 1)
                self.teacher.write_text(edited, encoding='utf-8')
                self.cli('derive', self.teacher)
                after = state()
                if end == '없음':
                    self.assertIsNone(after[1])
                    self.assertFalse(any({'data-check-hash', 'data-check-unit'} & a.keys() for a in after[0]))
                else:
                    self.assertGreater(sum('data-check-hash' in a for a in after[0]), 5)
                    self.assertIn('모범 풀이', after[1]['items']['q1']['solution_html'])
                self.interactive_fixture(end)
                self.cli('build', self.folder)
                self.assertEqual(state(), after)

    def test_lesson_and_practice_heads(self):
        for kind, cols, head in [('수업', 2, '짝의 이름'), ('연습', 1, '수학 연습지')]:
            with self.subTest(kind=kind):
                self.fixture(kind, cols)
                self.cli('build', self.folder)
                teacher = self.teacher.read_text(encoding='utf-8')
                self.assertIn(f'class="ws-head ws-head--{kind}"', teacher)
                self.assertIn(f'data-columns="{cols}"', teacher)
                self.assertIn(head, teacher)
