import base64
import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
TRIALS = Path(os.environ.get('SHEET_TEST_TRIALS', ROOT / 'tests' / '_out' / '검사-시험'))
sys.path.insert(0, str(ROOT / 'scripts'))
import sheet_build
import sheet_check


def fixture_html():
    source = (ROOT / 'tests/engine/fixture-assessment.html').read_text(encoding='utf-8')
    css = (ROOT / 'assets/engine/sheet.css').read_text(encoding='utf-8')
    script = (ROOT / 'assets/engine/sheet.js').read_text(encoding='utf-8')
    if re.search(r'</script', script, re.I):
        raise AssertionError('엔진을 script 요소에 그대로 내장할 수 없습니다.')
    source, css_count = re.subn(r'<link id="sheet-css"[^>]*>', lambda _: '<style id="sheet-css">' + css + '</style>', source)
    source, js_count = re.subn(r'<script id="sheet-js"[^>]*></script>', lambda _: '<script id="sheet-js">' + script + '</script>', source)
    if (css_count, js_count) != (1, 1):
        raise AssertionError('엔진 자산 위치를 찾지 못했습니다.')
    return source


def generate_fixture(folder):
    folder.mkdir(parents=True, exist_ok=True)
    teacher = folder / '도형과 좌표 (교사용).html'
    student = folder / '도형과 좌표.html'
    teacher.write_text(fixture_html(), encoding='utf-8')
    with sheet_check.sheet_browser() as browser:
        protocol = browser.cdp
        protocol.call('Page.enable')
        protocol.call('Runtime.enable')
        protocol.call('Page.navigate', {'url': teacher.resolve().as_uri()})
        protocol.wait_event('Page.loadEventFired')
        result = protocol.evaluate("(async()=>{await window.sheet.ready;return window.sheet.exportStudent()})()", True)
    student.write_text(result, encoding='utf-8')
    return teacher, student


class CheckIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_root = TRIALS / ('check-' + uuid4().hex[:10])
        cls.run_root.mkdir(parents=True, exist_ok=True)
        cls.old_temp = os.environ.get('SHEET_TEST_TMP')
        cls.old_profile = os.environ.get('SHEET_TEST_PROFILE')
        # 브라우저 프로필은 실행마다 새로 만들면 수십 MB씩 쌓이므로 모든 실행이 한 폴더를 다시 쓴다.
        shared = TRIALS / '_브라우저-프로필'
        os.environ['SHEET_TEST_TMP'] = str(shared)
        os.environ['SHEET_TEST_PROFILE'] = str(shared / '현재')
        cls.reference = cls.run_root / '기준'
        teacher, student = generate_fixture(cls.reference)
        cls.teacher_text = teacher.read_text(encoding='utf-8')
        cls.student_text = student.read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        if cls.old_temp is None:
            os.environ.pop('SHEET_TEST_TMP', None)
        else:
            os.environ['SHEET_TEST_TMP'] = cls.old_temp
        if cls.old_profile is None:
            os.environ.pop('SHEET_TEST_PROFILE', None)
        else:
            os.environ['SHEET_TEST_PROFILE'] = cls.old_profile

    def make_case(self, name, teacher=None, student=None):
        folder = self.run_root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / '도형과 좌표 (교사용).html').write_text(teacher or self.teacher_text, encoding='utf-8')
        (folder / '도형과 좌표.html').write_text(student or self.student_text, encoding='utf-8')
        return folder

    def inspect(self, folder, expected):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = sheet_check.cmd_check(folder)
        self.assertEqual(result, expected, output.getvalue())
        report = json.loads((folder / '검사/report.json').read_text(encoding='utf-8'))
        return {issue['code'] for issue in report['problems']}

    def test_clean_fixture_and_archive_old_images(self):
        folder = self.make_case('정상')
        old = folder / '검사/교사-99.png'
        old.parent.mkdir(exist_ok=True)
        old.write_bytes(b'old page kept')
        codes = self.inspect(folder, 0)
        self.assertNotIn('student-private-markup', codes)
        self.assertTrue((folder / '검사/교사-01.png').is_file())
        self.assertTrue((folder / '검사/모아보기-흑백.png').is_file())
        self.assertEqual((folder / '검사/_이전/교사-99.png').read_bytes(), b'old page kept')
        self.assertFalse(old.exists())

    def test_filled_phone_detects_real_clipped_flow_and_clears_answers(self):
        folder = self.run_root / '채움-흐름'
        folder.mkdir(parents=True, exist_ok=True)
        data = {'meta': {'title': '채움 검사', 'kind': '수업', 'interactive': True, 'reveal': '바로', 'sync': False},
                'items': [{'id': 'q1', 'blocks': [{'type': 'flow',
                           'nodes': ['입력', '{{|$12345678901234567890$}}', '결과']}]}]}
        renderer = sheet_build.Renderer(data, folder, [])
        teacher = sheet_build.document_html(renderer, {}, '')
        table = sheet_build.answer_table_from_html(teacher)
        student = sheet_build.strip_teacher(teacher).replace(
            '<style id="sheet-user"></style>',
            '<style id="sheet-user">#sheet-flow .ws-flow-node {width:40px;overflow:hidden;white-space:nowrap}</style>')
        path = folder / '학생.html'
        path.write_text(student, encoding='utf-8')
        out = folder / '검사'
        out.mkdir()
        with sheet_check.sheet_browser() as browser:
            for domain in ('Page.enable', 'Runtime.enable'):
                browser.cdp.call(domain)
            sheet_check._navigate(browser, path, False)
            problems = []
            info, _, _ = sheet_check._interactive_check(browser.cdp, out, table, problems)
            self.assertEqual(browser.cdp.evaluate('sheet.values()'), {})
        self.assertGreater(info['filled_screen']['checked_items'], 0)
        self.assertGreater(info['filled_screen']['revealed_items'], 0)
        self.assertTrue(info['filled_screen']['clipped'])
        self.assertIn('filled-screen-clipped', [problem['code'] for problem in problems])

    def test_snapshot_tick_labels_are_at_least_six_points_in_pdf(self):
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            self.skipTest('PyMuPDF 없음')
        renderer = sheet_build.Renderer({'meta': {'title': '그래프', 'interactive': False}}, self.run_root, [])
        block = {'fn': 'a*x**2', 'params': [{'name': 'a', 'value': 1}], 'x': [-4, 4], 'y': [-9, 9],
                 'unit_mm': 5, 'print': [{'a': x} for x in (.5, -.5, 2, -2)]}
        section = '<section class="ws-item" data-id="snapshots">' + renderer.explore(block) + '</section>'
        small = renderer.grid({'mode': 'coord', 'x': [-3, 3], 'y': [-3, 3], 'unit_mm': 5})
        small = small.replace('<svg ', '<svg style="width:12mm;height:auto" ', 1)
        section += '<section class="ws-item" data-id="small-graph">' + small + '</section>'
        source = self.student_text.replace('<div id="sheet-src">', '<div id="sheet-src">' + section, 1)
        folder = self.make_case('그래프-PDF', student=source)
        with sheet_check.sheet_browser() as browser:
            for domain in ('Page.enable', 'Runtime.enable'):
                browser.cdp.call(domain)
            report, _, _, pdf, _ = sheet_check._navigate(browser, folder / '도형과 좌표.html', False)
        problems = []
        measurements = sheet_check._audit_graph_labels(pdf, report['graph_regions'], '학생용', problems)
        snapshots = [m for m in measurements if m['item'] == 'snapshots']
        other = [m for m in measurements if m['item'] == 'small-graph']
        self.assertEqual(len(snapshots), 4)
        self.assertTrue(all(m['labels'] > 0 and m['min_pt'] >= 6 for m in snapshots), snapshots)
        self.assertEqual(len(other), 1)
        self.assertLess(other[0]['min_pt'], 6)
        self.assertEqual([p['code'] for p in problems], ['graph-label-small'])

    def test_browser_failure_invalidates_old_report(self):
        folder = self.make_case('브라우저실패')
        previous = folder / '검사/report.json'
        previous.parent.mkdir(exist_ok=True)
        previous.write_text('이전 결과', encoding='utf-8')
        with patch.dict(os.environ, {'SHEET_BROWSER': str(folder / '없는-브라우저.exe')}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sheet_check.cmd_check(folder), 2)
        self.assertFalse(previous.exists())
        self.assertEqual((folder / '검사/_이전/report.json').read_text(encoding='utf-8'), '이전 결과')

    def test_answer_in_student_source(self):
        answer = '두 대각선은 서로 수직으로 만나기 때문입니다.'
        broken = self.student_text.replace('</body>', '<p>' + answer + '</p></body>')
        codes = self.inspect(self.make_case('정답유출', student=broken), 1)
        self.assertIn('answer-leak', codes)

    def test_external_url(self):
        broken = self.student_text.replace('</body>', '<img src="https://example.invalid/missing.png"></body>')
        codes = self.inspect(self.make_case('외부주소', student=broken), 1)
        self.assertIn('external-url', codes)

    def test_oversized_item(self):
        broken = self.teacher_text.replace('<div id="sheet-src">',
            '<div id="sheet-src"><section class="ws-item" data-id="giant" style="height:310mm">큰 문항</section>')
        codes = self.inspect(self.make_case('큰문항', teacher=broken), 1)
        self.assertIn('too-tall', codes)
        self.assertIn('page-overflow', codes)

    def test_long_answer_in_small_box(self):
        old = '<div class="ws-key" data-src="직각">직각</div>'
        answer = '매우긴예시답안' * 25
        new = '<div class="ws-key" style="white-space:nowrap">' + answer + '</div>'
        self.assertIn(old, self.teacher_text)
        broken = self.teacher_text.replace(old, new, 1)
        codes = self.inspect(self.make_case('긴답안', teacher=broken), 1)
        self.assertIn('handwriting-overflow', codes)


class SourceAuditTest(unittest.TestCase):
    def test_markers_only_in_markup_not_inline_engine(self):
        parser = sheet_check._SourceAudit()
        parser.feed('<style>.ws-key{color:red}</style><script>"data-src=";"ws-key"</script><p>정상</p>')
        self.assertFalse(parser.markers)
        parser.feed('<span class="ws-key-choice" data-src="x">x</span>')
        self.assertEqual(parser.markers, {'ws-key-choice', 'data-src'})

    def test_external_css_url_and_local_data_svg(self):
        parser = sheet_check._SourceAudit()
        parser.feed('<img src="data:image/svg+xml,%3Csvg xmlns=http://www.w3.org/2000/svg%3E"><style>x{background:url(https://example.invalid/x)}</style>')
        self.assertEqual(parser.remotes, ['CSS url()'])

    def test_answer_table_leak_private_attribute_and_video_exceptions(self):
        problems = []
        student = ('<figure class="ws-video" data-yt="abcdefghijk"><span class="ws-video-url">'
                   'https://youtu.be/abcdefghijk</span></figure>'
                   '<script id="sheet-js">const host="youtube-nocookie.com";</script>'
                   '<script id="sheet-reveal">비밀정답</script>'
                   '<p data-check="{}">공개문구 비밀정답</p>')
        sheet_check._audit_source('', student, ['공개문구', '비밀정답'], problems, '공개문구')
        codes = [issue['code'] for issue in problems]
        self.assertIn('student-private-markup', codes)
        self.assertIn('answer-leak', codes)
        self.assertNotIn('external-url', codes)
        self.assertEqual(sum(code == 'answer-leak' for code in codes), 1)

    def test_reveal_and_script_are_not_searched_for_answers(self):
        problems = []
        sheet_check._audit_source('', '<script id="sheet-reveal">비밀정답</script>'
                                  '<script id="sheet-js">비밀정답</script><p>공개문구</p>',
                                  ['비밀정답', '공개문구'], problems, '공개문구')
        self.assertFalse(problems)


class FakeInteractiveProtocol:
    def __init__(self, reveal='시도 후', overflow=0, leaks=0, items=(), lose=None, clipped=()):
        self.reveal, self.overflow, self.leaks = reveal, overflow, leaks
        self.clipped = list(clipped)
        self.items, self.lose = list(items), lose
        self.values = {'q1.1': '정답', 'q2.1': '검사 서술 첫째 줄\n검사 서술 둘째 줄',
                       'q2.2': {'strokes': [[[0.15, 0.25], [0.5, 0.75], [0.85, 0.35]]]}, 'head.이름': '검사'}
        self.calls, self.expressions, self.events = [], [], []

    def wait_event(self, method, timeout=None):
        return {}

    def call(self, name, params=None):
        self.calls.append((name, params))
        if name == 'Page.getLayoutMetrics':
            return {'cssContentSize': {'height': 1000}}
        if name in ('Page.captureScreenshot', 'Page.printToPDF'):
            return {'data': base64.b64encode(b'test image or pdf').decode('ascii')}
        return {}

    def evaluate(self, expression, await_promise=False):
        self.expressions.append(expression)
        if 'available:' in expression:
            return {'available': True, 'reveal': self.reveal, 'salt': 'a' * 32}
        if 'filledScreenInspection' in expression:
            return {'view': 'solve', 'overflow_x': self.overflow, 'clipped': [], 'checked_items': 1, 'revealed_items': 1}
        if 'ws-item[data-id]' in expression:
            return self.items
        if 'Promise.race' in expression:
            return 'ready'
        if expression == 'sheet.slots()':
            return [{'slot': 'q1.1', 'auto': True}]
        if '.map(row =>' in expression:
            return [{'slot': 'q1.1', 'right': True, 'wrong': False}]
        if 'for (const row of' in expression:
            return {'kinds': {'open_box': 1, 'open_blank': 0, 'draw': 1, 'name': 1}, 'values': dict(self.values)}
        if 'values: sheet.values()' in expression:
            values = {k: v for k, v in self.values.items() if k != self.lose}
            return {'view': 'a4', 'print_leaks': self.leaks, 'values': values,
                    'clipped': self.clipped if 'scrollWidth' in expression and 'scrollHeight' in expression else None}
        if "sheet.view('solve')" in expression:
            return {'view': 'solve', 'overflow_x': self.overflow}
        return None


class CheckBoundaryTest(unittest.TestCase):
    def test_filled_phone_reports_overflow_and_clipping(self):
        out = TRIALS / ('filled-screen-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        table = {'salt': 'a' * 32, 'slots': [{'slot': 'q1.1', 'kind': 'text', 'right': '정답', 'wrong': '오답'}]}
        protocol = FakeInteractiveProtocol()
        original = protocol.evaluate
        def evaluate(expression, await_promise=False):
            if 'filledScreenInspection' in expression:
                protocol.expressions.append(expression)
                return {'view': 'solve', 'overflow_x': 3, 'clipped': ['q1.1'], 'checked_items': 1, 'revealed_items': 1}
            return original(expression, await_promise)
        protocol.evaluate = evaluate
        problems = []
        info, files, _ = sheet_check._interactive_check(protocol, out, table, problems)
        self.assertEqual(info['filled_screen']['overflow_x'], 3)
        self.assertEqual(info['filled_screen']['clipped'], ['q1.1'])
        self.assertIn('filled-screen-overflow', [p['code'] for p in problems])
        self.assertIn('filled-screen-clipped', [p['code'] for p in problems])
        self.assertTrue(any('reveal-answer' in e and 'data-sheet-action="check"' in e for e in protocol.expressions))
        self.assertTrue(any('sheet.clearAnswers()' in e for e in protocol.expressions))

    def test_pdf_graph_labels_use_actual_point_size(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest('PyMuPDF 없음')
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((20, 30), '8', fontsize=5.5)
        page.insert_text((20, 60), '9', fontsize=7)
        page.insert_text((200, 60), '10', fontsize=4)
        pdf = document.tobytes()
        document.close()
        problems = []
        regions = [{'page': 1, 'item': 'q1', 'bbox': [0, 0, 100, 100]}]
        measured = sheet_check._audit_graph_labels(pdf, regions, '학생용', problems)
        self.assertAlmostEqual(measured[0]['min_pt'], 5.5, places=1)
        self.assertEqual(measured[0]['labels'], 2)
        self.assertEqual([p['code'] for p in problems], ['graph-label-small'])

    def test_screen_overflow_and_filled_print_are_recorded(self):
        out = TRIALS / ('screen-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        table = {'salt': 'a' * 32, 'slots': [{'slot': 'q1.1', 'kind': 'text', 'right': '정답', 'wrong': '오답'}]}
        protocol, problems = FakeInteractiveProtocol(overflow=2, leaks=1), []
        info, files, pdf = sheet_check._interactive_check(protocol, out, table, problems)
        self.assertEqual(info['grading'], 'passed')
        self.assertEqual((info['auto_slots'], info['answer_slots']), (1, 1))
        self.assertEqual([p.name for p in files], ['화면-폰.png', '화면-태블릿.png', '화면-폰-채움.png'])
        self.assertTrue(all(p.is_file() for p in files))
        self.assertEqual(pdf, b'test image or pdf')
        self.assertEqual([p['code'] for p in problems].count('screen-overflow'), 2)
        self.assertIn('print-leak', [p['code'] for p in problems])
        self.assertTrue(any("sheet.clearAnswers()" in e for e in protocol.expressions))

    def test_missing_screen_overflow_metric_is_not_a_pass(self):
        out = TRIALS / ('screen-metric-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        protocol, problems = FakeInteractiveProtocol(overflow=None), []
        sheet_check._interactive_check(protocol, out, None, problems)
        self.assertEqual([p['code'] for p in problems], ['interactive-report-invalid'] * 2)

    def test_no_reveal_skips_grading_but_still_prints_answers(self):
        out = TRIALS / ('no-reveal-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        table = {'salt': 'a' * 32, 'slots': [{'slot': 'q1.1', 'kind': 'text', 'right': '정답', 'wrong': '오답'}]}
        protocol, problems = FakeInteractiveProtocol(reveal='없음', overflow=1), []
        info, files, pdf = sheet_check._interactive_check(protocol, out, table, problems)
        self.assertEqual(info['grading'], 'skipped-reveal-none')
        self.assertFalse(any('.map(row =>' in e for e in protocol.expressions))
        self.assertTrue(any('for (const row of' in e for e in protocol.expressions))
        self.assertEqual(len(files), 3)
        self.assertIsNotNone(pdf)
        self.assertFalse(problems)

    def test_missing_and_invalid_answer_tables_are_errors(self):
        out = TRIALS / ('answer-key-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        problems = []
        self.assertIsNone(sheet_check._load_answer_table(out, problems))
        self.assertEqual(problems[0]['code'], 'answer-table-missing')
        self.assertIn('build', problems[0]['fix'])
        (out / '정답표.json').write_text('{"slots": []}', encoding='utf-8')
        problems = []
        self.assertIsNone(sheet_check._load_answer_table(out, problems))
        self.assertEqual(problems[0]['code'], 'answer-table-invalid')

    def test_grading_mismatch_names_slots_and_checks_exact_booleans(self):
        table = {'slots': [{'slot': 'q1.1'}, {'slot': 'q2.1'}]}
        slots = [{'slot': 'q1.1', 'auto': True}, {'slot': 'q3.1', 'auto': True},
                 {'slot': 'head.이름', 'auto': False}]
        results = [{'slot': 'q1.1', 'right': True, 'wrong': True},
                   {'slot': 'q2.1', 'right': None, 'wrong': False}]
        problems = []
        sheet_check._audit_answers(table, slots, results, problems)
        mismatches = [p for p in problems if p['code'] == 'answer-slot-mismatch']
        self.assertTrue(any('q2.1' in p['msg'] for p in mismatches))
        self.assertTrue(any('q3.1' in p['msg'] for p in mismatches))
        failed = [p['item'] for p in problems if p['code'] == 'answer-check-failed']
        self.assertEqual(failed, ['q1.1', 'q2.1'])

    def test_filled_print_enters_essays_drawings_names_and_reloads(self):
        out = TRIALS / ('filled-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        table = {'salt': 'a' * 32, 'slots': [{'slot': 'q1.1', 'kind': 'text', 'right': '정답', 'wrong': '오답'}]}
        protocol, problems = FakeInteractiveProtocol(), []
        info, _, _ = sheet_check._interactive_check(protocol, out, table, problems)
        self.assertEqual(info['filled_inputs'], {'auto': 1, 'open_box': 1, 'open_blank': 0, 'draw': 1, 'name': 1})
        self.assertTrue(info['filled_restored'])
        self.assertIn(('Page.reload', {'ignoreCache': False}), protocol.calls)
        fill = next(e for e in protocol.expressions if 'for (const row of' in e)
        self.assertIn("'검사 서술 첫째 줄\\n검사 서술 둘째 줄'", fill)
        self.assertFalse(problems)
        protocol, problems = FakeInteractiveProtocol(lose='q2.2'), []
        info, _, _ = sheet_check._interactive_check(protocol, out, table, problems)
        self.assertFalse(info['filled_restored'])
        lost = [p for p in problems if p['code'] == 'filled-restore-failed']
        self.assertTrue(lost and 'q2.2' in lost[0]['msg'])

    def test_filled_print_reports_clipped_blank_and_name(self):
        out = TRIALS / ('clipped-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        table = {'salt': 'a' * 32, 'slots': [{'slot': 'q1.1', 'kind': 'text', 'right': '정답', 'wrong': '오답'}]}
        protocol = FakeInteractiveProtocol(clipped=['head.이름', 'q1.1'])
        problems = []
        info, _, _ = sheet_check._interactive_check(protocol, out, table, problems)
        self.assertEqual(info['filled_print_clipped'], ['head.이름', 'q1.1'])
        self.assertEqual([(i['level'], i['code']) for i in problems], [('error', 'filled-print-clipped')])
        self.assertIn('head.이름, q1.1', problems[0]['msg'])

    def test_later_item_showing_an_earlier_equation_warns(self):
        table = {'slots': [{'slot': 'q3.1', 'kind': 'text', 'right': 'y=2x'},
                           {'slot': 'q2.1', 'kind': 'text', 'right': '정비례'},
                           {'slot': 'q1.1', 'kind': 'number', 'right': '4'}]}
        items = [{'id': 'q1', 'text': '표 4 6 8'}, {'id': 'q2', 'text': '정비례'}, {'id': 'q3', 'text': '관계식'},
                 {'id': 'q4', 'text': '정비례 관계를 생각합니다. 4'}, {'id': 'q5', 'text': '관계식 2 x = y 를 그래프로'}]
        problems = []
        sheet_check._audit_later_answers(table, items, problems)
        self.assertEqual([(p['code'], p['item']) for p in problems], [('later-answer-shown', 'q5')])
        problems = []
        quiet = items[:4] + [{'id': 'q5', 'text': '앞에서 구한 관계식의 그래프'}]
        sheet_check._audit_later_answers(table, quiet, problems)
        sheet_check._audit_later_answers(None, items, problems)
        sheet_check._audit_later_answers(table, None, problems)
        self.assertEqual(problems, [])

    def test_filled_text_counts_both_lines_in_pdf(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest('PyMuPDF 없음')

        def pdf(lines):
            document = pymupdf.open()
            page = document.new_page()
            for i, line in enumerate(lines):
                page.insert_text((72, 72 + 20 * i), line, fontname='korea')
            data = document.tobytes()
            document.close()
            return data
        both = list(sheet_check._FILL_LINES)
        info, problems = {'filled_inputs': {'open_box': 2}}, []
        sheet_check._audit_filled_text(info, pdf(both + both), problems)
        self.assertEqual((info['filled_text'], problems), ({'open_box': 2, 'found': 2}, []))
        info, problems = {'filled_inputs': {'open_box': 2}}, []
        sheet_check._audit_filled_text(info, pdf(both + both[:1]), problems)
        self.assertEqual(info['filled_text']['found'], 1)
        self.assertEqual([p['code'] for p in problems], ['filled-print-missing'])

    def test_interactive_report_must_include_print_leak_count(self):
        problems = []
        sheet_check._audit_report({'interactive': True, 'view': 'a4'}, '학생용', problems)
        self.assertIn('interactive-report-invalid', [p['code'] for p in problems])

    def test_print_leaks_are_reported_for_both_roles(self):
        for role in ('교사용', '학생용'):
            with self.subTest(role=role):
                problems = []
                sheet_check._audit_report({'view': 'a4', 'print_leaks': 2}, role, problems)
                self.assertEqual([p['code'] for p in problems], ['print-leak'])
                self.assertIn(role, problems[0]['msg'])
        problems = []
        sheet_check._audit_report({'view': 'solve', 'print_leaks': 2}, '학생용', problems)
        self.assertFalse(problems)

    def test_thresholds_and_drawing_exemption(self):
        report = {
            'pages': [{'n': 1, 'teacher': False, 'slots': [{'fill': .95, 'base_fill': .65, 'overflow_mm': .5}]},
                      {'n': 2, 'teacher': False, 'slots': [{'fill': .9, 'base_fill': .5, 'overflow_mm': .501}]}],
            'items': [{'id': 'q1', 'page': 1}], 'issues': [], 'overflow': [],
            'fonts': {}, 'images': {}, 'math': {},
            'boxes': [{'item': 'q1', 'index': 0, 'kind': 'text', 'height_mm': 6.9, 'width_mm': 20, 'key_text': ''},
                      {'item': 'q1', 'index': 1, 'kind': 'draw', 'height_mm': 10, 'width_mm': 10, 'key_text': '그림 설명' * 50}]}
        problems = []
        sheet_check._audit_report(report, '교사용', problems)
        sheet_check._audit_fill(report, problems)
        codes = [issue['code'] for issue in problems]
        self.assertIn('page-overflow', codes)
        self.assertIn('box-too-short', codes)
        self.assertNotIn('handwriting-overflow', codes)
        self.assertNotIn('page-fill', codes)
        self.assertNotIn('base-fill', codes)

    def test_single_page_with_thin_content_warns(self):
        # 한 쪽짜리에서 늘이기 전 채움이 51%면 답칸만 반 쪽씩 늘어나므로 경고한다(실제 Codex 시험에서 나온 경우).
        report = {'pages': [{'n': 1, 'teacher': False, 'slots': [{'fill': .985, 'base_fill': .51, 'overflow_mm': 0}]}]}
        problems = []
        sheet_check._audit_fill(report, problems)
        self.assertEqual([issue['code'] for issue in problems], ['base-fill'])

    def test_missing_pymupdf_archives_old_outputs_and_uses_regex(self):
        out = TRIALS / ('fallback-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        for name in ('학생-01.png', '모아보기.png', '모아보기-흑백.png'):
            (out / name).write_bytes(b'prior')
        real_import = builtins.__import__

        def without_pymupdf(name, *args, **kwargs):
            if name == 'pymupdf':
                raise ImportError('시험: pymupdf 없음')
            return real_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=without_pymupdf):
            self.assertEqual(sheet_check._pdf_pages(b'/Type /Pages /Type /Page /Type /Page'), 2)
            problems = []
            self.assertEqual(sheet_check._render_pages(out, [], problems), [])
            self.assertEqual(sheet_check._contact_sheets(out, [], problems), [])
        self.assertEqual(problems[0]['code'], 'pymupdf-missing')
        for name in ('학생-01.png', '모아보기.png', '모아보기-흑백.png'):
            self.assertEqual((out / '_이전' / name).read_bytes(), b'prior')
            self.assertFalse((out / name).exists())

    def test_missing_pillow_archives_stale_contact_sheets(self):
        out = TRIALS / ('no-pillow-' + uuid4().hex[:10])
        out.mkdir(parents=True, exist_ok=True)
        for name in ('모아보기.png', '모아보기-흑백.png'):
            (out / name).write_bytes(b'prior')
        real_import = builtins.__import__

        def without_pillow(name, *args, **kwargs):
            if name == 'PIL':
                raise ImportError('시험: Pillow 없음')
            return real_import(name, *args, **kwargs)

        problems = []
        with patch('builtins.__import__', side_effect=without_pillow):
            self.assertEqual(sheet_check._contact_sheets(out, [('학생', 1, out / '학생-01.png')], problems), [])
        self.assertEqual(problems[0]['code'], 'pillow-missing')
        self.assertTrue((out / '_이전/모아보기-흑백.png').is_file())


class DeliveryGateTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        import time
        self.temp = tempfile.TemporaryDirectory(dir=TRIALS if TRIALS.exists() else None)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.out = self.folder / '검사'
        self.out.mkdir()
        self.teacher = self.folder / '활동 (교사용).html'
        self.student = self.folder / '활동.html'
        self.digest = sheet_build.engine_sha256()
        self.write_html(self.teacher)
        self.write_html(self.student, interactive=True, sync=True)
        for path in (self.teacher, self.student):
            path.with_suffix('.pdf').write_bytes(b'%PDF')
        (self.folder / '구성.json').write_text('{}', encoding='utf-8')
        self.build = {'source_sha256': sheet_check._sha(self.folder / '구성.json'), 'engine_sha256': self.digest,
                      'teacher': {'file': self.teacher.name}, 'student': {'file': self.student.name}}
        self.write_build()
        self.report = {'student': {'interactive': True, 'pages': [{'n': 1, 'teacher': False}]},
                       'teacher': {'pages': [{'n': 1, 'teacher': False}, {'n': 2, 'teacher': True}]},
                       'interactive_check': {'screens': [{'width': 390, 'height': 844}, {'width': 1024, 'height': 768}],
                           'grading': 'passed', 'answer_slots': 3, 'filled_inputs': {'auto': 3, 'open_box': 1, 'name': 1},
                           'filled_restored': True}, 'problems': []}
        self.report_path = self.out / 'report.json'
        self.report_path.write_text(json.dumps(self.report, ensure_ascii=False), encoding='utf-8')
        self.review = self.out / '검토.md'
        self.review.write_text('판정: 통과\n검수: 자체\n학생 1쪽\n교사용 1쪽\n교사용 2쪽\n'
                               '## 내용 검산\n확인\n## 화면 확인\n확인\n', encoding='utf-8')
        for name in ('학생-01.png', '교사-01.png', '교사-02.png'):
            (self.out / name).write_bytes(b'page')
        base = time.time_ns() - 10_000_000_000
        for path, delta in ((self.teacher, 1), (self.student, 1), (self.report_path, 2), (self.review, 3)):
            os.utime(path, ns=(base + delta * 1_000_000_000,) * 2)

    def write_html(self, path, digest=None, interactive=False, sync=False):
        import html
        meta = html.escape(json.dumps({'engine_sha256': digest or self.digest}), quote=True)
        root = ' data-interactive="1"' if interactive else ''
        sync_meta = '<meta name="sheet-sync" content="PRIVATE-CONFIG">' if sync else ''
        answer = '<span data-check-hash="abc"></span><script id="sheet-reveal">encoded</script>' if interactive else ''
        path.write_text(f'<html{root}><head><meta name="sheet-build" content="{meta}">{sync_meta}</head><body>{answer}</body></html>', encoding='utf-8')

    def write_build(self):
        (self.out / 'build.json').write_text(json.dumps(self.build), encoding='utf-8')

    def status(self):
        with patch.object(sheet_check, '_plan_status', return_value=(True, 'plan 오류 0개')), contextlib.redirect_stdout(io.StringIO()) as output:
            result = sheet_check.cmd_status(self.folder)
        return result, output.getvalue()

    def test_fingerprint_and_delivery_draft(self):
        result, output = self.status()
        self.assertEqual(result, 0, output)
        for expected in ('전달 문안 초안', '학생 1쪽, 교사 2쪽', '390×844', '1024×768', '3곳 통과',
                         '채움 인쇄 검사', '실제 인쇄', '실제 기기', '다른 브라우저', '난독화', '개인정보'):
            self.assertIn(expected, output)
        self.assertNotIn('PRIVATE-CONFIG', output)
        self.assertTrue(output.rstrip().endswith('이 활동지 스킬이 도움이 됐다면 GitHub에 ⭐ 하나 부탁드려요 → https://github.com/BeomjinSouth/exampractice2html'))
        self.build['engine_sha256'] = '0' * 64
        self.write_build()
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('설치된 스킬과 다른 엔진으로 조립됐습니다', output)
        self.assertNotIn('전달 문안 초안', output)

    def test_delivery_disclosure_follows_policy_and_actual_markup(self):
        for policy, reveal, hashes, expected in (
                ('시도 후', True, True, '정답과 풀이가 난독화'),
                ('바로', True, True, '정답과 풀이가 난독화'),
                ('교사만', False, True, '정답 확인용 해시'),
                ('없음', False, False, '학생용에 정답이 들어 있지 않습니다'),
                ('없음', True, True, '정답과 풀이가 난독화')):
            with self.subTest(policy=policy, reveal=reveal, hashes=hashes):
                tag = '<script id="sheet-reveal">encoded</script>' if reveal else ''
                check = '<span data-check-hash="abc"></span>' if hashes else ''
                self.student.write_text(f'<html data-interactive="1" data-reveal="{policy}"><head></head><body>{check}{tag}</body></html>', encoding='utf-8')
                meta = sheet_check._delivery_meta(self.student)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    sheet_check._delivery_draft(self.student, self.teacher, self.report, meta)
                self.assertIn(expected, output.getvalue())
                self.assertIn('마지막 줄', output.getvalue())
                if not reveal:
                    self.assertNotIn('난독화되어', output.getvalue())

    def test_html_engine_mismatch_and_missing_page_block(self):
        self.write_html(self.teacher, digest='f' * 64)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('설치된 스킬과 다른 엔진으로 조립됐습니다', output)
        self.write_html(self.teacher)
        (self.out / '교사-02.png').unlink()
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('쪽 그림이 없어 눈으로 검토할 수 없습니다', output)
        self.assertIn('python -m pip install pymupdf pillow', output)

    def test_fingerprint_changes_with_engine_and_assembly_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = ('assets/engine/sheet.js', 'assets/templates/구성.json', 'assets/kit/blocks.json',
                     'assets/kit/terms.json', 'assets/kit/expressions.json',
                     'scripts/sheet_build.py', 'scripts/sheet_markup.py', 'scripts/sheet_answer.py',
                     'scripts/sheet_plan.py', 'scripts/sheet_common.py', 'scripts/sheet_sync.py')
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding='utf-8')
            with patch.object(sheet_build, 'ROOT', root):
                initial = sheet_build.engine_sha256()
                self.assertEqual(initial, sheet_build.engine_sha256())
                (root / 'assets/engine/sheet.js').write_text('수정된 엔진', encoding='utf-8')
                changed_engine = sheet_build.engine_sha256()
                self.assertNotEqual(initial, changed_engine)
                (root / 'scripts/sheet_build.py').write_text('수정된 조립기', encoding='utf-8')
                changed_build = sheet_build.engine_sha256()
                self.assertNotEqual(changed_engine, changed_build)
                # plan이 읽는 학년 용어·교과서 표현 자료가 바뀌어도 예전 검사 결과를 믿지 않는다.
                (root / 'assets/kit/expressions.json').write_text('수정된 표현', encoding='utf-8')
                self.assertNotEqual(changed_build, sheet_build.engine_sha256())

    def test_doctor_does_not_print_configuration(self):
        import sheet
        secret = self.folder / 'sync.json'
        secret.write_text('PRIVATE-CONFIG', encoding='utf-8')
        with patch.dict(os.environ, {'SHEET_SYNC_CONFIG': str(secret)}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sheet.cmd_doctor(), 0)
        self.assertIn('학생 기록 설정 파일 | 있음', output.getvalue())
        self.assertNotIn('PRIVATE-CONFIG', output.getvalue())
        self.assertNotIn(str(secret), output.getvalue())


if __name__ == '__main__':
    unittest.main()
