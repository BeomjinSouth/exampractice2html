import contextlib
import html
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import types
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
TRIALS = Path(os.environ.get('SHEET_TEST_TRIALS', ROOT / 'tests' / '_out' / '검사-시험'))
sys.path.insert(0, str(ROOT / 'scripts'))
import sheet_build
import sheet_check


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.folder = TRIALS / ('status-' + uuid4().hex[:10])
        self.out = self.folder / '검사'
        self.out.mkdir(parents=True, exist_ok=True)
        if not os.environ.get('SHEET_KEEP_TRIALS'):
            self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.source = self.folder / '구성.json'
        self.teacher = self.folder / '활동 (교사용).html'
        self.student = self.folder / '활동.html'
        self.source.write_text('{}', encoding='utf-8')
        self.engine = sheet_build.engine_sha256()
        metadata = html.escape(json.dumps({'engine_sha256': self.engine}), quote=True)
        self.teacher.write_text(f'<html data-role="teacher"><head><meta name="sheet-build" content="{metadata}"></head></html>', encoding='utf-8')
        self.student.write_text(f'<html data-role="student"><head><meta name="sheet-build" content="{metadata}"></head></html>', encoding='utf-8')
        for path in (self.teacher, self.student):
            path.with_suffix('.pdf').write_bytes(b'%PDF')
        self.build = self.out / 'build.json'
        self.build.write_text(json.dumps({'source_sha256': sheet_check._sha(self.source), 'engine_sha256': self.engine,
            'teacher': {'file': self.teacher.name, 'sha256': sheet_check._sha(self.teacher)},
            'student': {'file': self.student.name, 'sha256': sheet_check._sha(self.student)}}), encoding='utf-8')
        self.report = self.out / 'report.json'
        self.data = {'teacher': {'pages': [{'n': 1, 'teacher': False}, {'n': 2, 'teacher': True}]},
                     'student': {'pages': [{'n': 1, 'teacher': False}]}, 'problems': []}
        for role, prefix in (('student', '학생'), ('teacher', '교사')):
            for page in self.data[role]['pages']:
                (self.out / f'{prefix}-{page["n"]:02d}.png').write_bytes(b'page')
        self.write_report()
        self.review = self.out / '검토.md'
        self.review.write_text('판정: 통과\n검수: 자체\n1쪽 확인\n교사용 1쪽 확인\n교사용 2쪽 확인\n## 내용 검산\n정답과 풀이를 확인함.\n', encoding='utf-8')
        self.base = time.time_ns() - 100_000_000_000
        self.stamp(self.teacher, 1)
        self.stamp(self.student, 1)
        self.stamp(self.report, 2)
        self.stamp(self.review, 3)
        self.fake_plan = types.ModuleType('sheet_plan')
        self.fake_plan.load_plan = lambda folder: ({}, b'{}', [])

    def stamp(self, path, seconds):
        at = self.base + seconds * 1_000_000_000
        os.utime(path, ns=(at, at))

    def write_report(self):
        self.report.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def status(self, module=True):
        fake = self.fake_plan if module else None
        with patch.dict(sys.modules, {'sheet_plan': fake}), contextlib.redirect_stdout(io.StringIO()) as output:
            result = sheet_check.cmd_status(self.folder)
        return result, output.getvalue()

    def test_all_gates_and_delivery(self):
        result, output = self.status()
        self.assertEqual(result, 0, output)
        self.assertIn('✓ 5 전달', output)
        for path in (self.student, self.teacher, self.student.with_suffix('.pdf'), self.teacher.with_suffix('.pdf')):
            self.assertIn(str(path), output)

    def build_education_fixture(self, **settings):
        data = {'version': 1, 'meta': {'title': '활동', 'kind': '연습', 'sync': False, **settings},
                'items': [{'id': 'q1', 'blocks': [{'type': 'question', 'text': '관계식을 구하시오.'}]}]}
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        with patch.dict(os.environ, {'SHEET_FONT_DIR': str(self.folder)}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sheet_build.cmd_build(self.folder), 0, out.getvalue())
        for path in (self.teacher, self.student):
            self.stamp(path, 1)
        if settings.get('interactive', True):
            with self.review.open('a', encoding='utf-8') as review:
                review.write('## 화면 확인\n폰·태블릿 확인.\n')
            self.stamp(self.review, 3)

    def test_education_downgrade_needs_exact_records_for_each_change(self):
        self.build_education_fixture(reveal='교사만', interactive=False)
        first = self.out / '처음-설정.json'
        first.write_text(json.dumps({
            'version': 1, 'meta': {'kind': '연습', 'interactive': True, 'reveal': '시도 후'},
            'items': 2, 'auto_slots': 3, 'written_boxes': 2, 'pen_figures': 1, 'draw_boxes': 1,
            'forms': {'q1': {'fraction_form': '기약분수', 'radical_form': '유리화'}},
        }, ensure_ascii=False), encoding='utf-8')
        result, output = self.status()
        self.assertEqual(result, 1, output)
        self.assertIn('✗ 4 검토', output)
        self.assertIn('교육 설정 변경 절이 없습니다', output)
        review = self.review.read_text(encoding='utf-8') + '## 교육 설정 변경\n'
        changes = ('공개 정책: 시도 후 → 교사만', '화면 풀이: 켬 → 끔', '문항 수: 2 → 1',
                   '자동 판별 자리 수: 3 → 0', '서술 답 칸 수: 2 → 0', '펜 그림 수: 1 → 0',
                   '그리기 칸 수: 1 → 0', 'q1 fraction_form: 기약분수 → 미지정', 'q1 radical_form: 유리화 → 미지정')
        body = ''.join(f'- {line} — 비교 활동 조정\n' for line in changes[1:])
        line = f'- {changes[0]} — 사용자 요청\n'
        for malformed in (f'- {changes[0]} — (까닭)\n', '- 공개 정책: 바로 → 교사만 — 사용자 요청\n',
                          '## 다른 절\n' + line, '```md\n' + line + '```\n'):
            with self.subTest(malformed=malformed):
                self.review.write_text(review + body + malformed, encoding='utf-8')
                self.stamp(self.review, 3)
                result, output = self.status()
                self.assertEqual(result, 1, output)
                self.assertIn('✗ 4 검토', output)
                self.assertIn('공개 정책', output)
        self.review.write_text(review + body + line, encoding='utf-8')
        self.stamp(self.review, 3)
        result, output = self.status()
        self.assertEqual(result, 0, output)
        draft = output.split('전달 문안 초안', 1)[1]
        for change in changes:
            self.assertIn(change + ' — ', draft)
        self.assertIn(line.strip(), draft)

    def test_equal_rank_policy_change_does_not_require_review_section(self):
        self.build_education_fixture(reveal='시도 후')
        self.build_education_fixture(reveal='바로')
        result, output = self.status()
        self.assertEqual(result, 0, output)
        self.assertIn('공개 정책 `바로`', output)
        self.assertNotIn('교육 설정 변경', output)

    def test_corrupt_first_settings_does_not_count_as_legacy(self):
        self.build_education_fixture()
        (self.out / '처음-설정.json').write_text('{}', encoding='utf-8')
        result, output = self.status()
        self.assertEqual(result, 1, output)
        self.assertIn('✗ 4 검토', output)
        self.assertIn('현재 교육 설정을 비교할 수 없습니다', output)
        self.assertNotIn('비교를 건너뜁니다', output)

    def test_old_worksheet_without_first_settings_reports_skip(self):
        result, output = self.status()
        self.assertEqual(result, 0, output)
        self.assertIn('처음-설정.json이 없어 교육 설정 비교를 건너뜁니다', output)

    def test_delivery_names_policy_and_default_before_student_contents(self):
        self.build_education_fixture(reveal='교사만')
        result, output = self.status()
        self.assertEqual(result, 0, output)
        self.assertIn('공개 정책 `교사만`: 학생은 맞음·틀림만 보고 정답과 풀이는 보지 못합니다.', output)
        self.assertIn('`연습` 기본 공개 정책은 `시도 후`', output)
        self.assertLess(output.index('공개 정책 `교사만`'), output.index('학생용에 정답 공개 자료'))

    def test_foreign_code_blocks_gate_three_even_with_an_old_successful_report(self):
        source = self.student.read_text(encoding='utf-8')
        self.student.write_text(source.replace('</html>', '<script>/* 추가 코드 */</script></html>'), encoding='utf-8')
        self.stamp(self.student, 1)
        result, output = self.status()
        self.assertEqual(result, 1, output)
        self.assertIn('✗ 3 검사 · foreign-code', output)
        self.assertNotIn('전달 문안 초안', output)

    def test_fallback_fonts_do_not_block_delivery_but_add_caution(self):
        build = json.loads(self.build.read_text(encoding='utf-8'))
        for embedded in (False, True):
            with self.subTest(embedded=embedded):
                build['fonts'] = {'embedded': embedded, 'bytes': 100 if embedded else 0}
                self.build.write_text(json.dumps(build), encoding='utf-8')
                result, output = self.status()
                self.assertEqual(result, 0, output)
                self.assertIn('✓ 5 전달', output)
                self.assertEqual('기기마다 줄바꿈과 쪽 나눔이 달라질 수 있습니다' in output, not embedded)
        del build['fonts']
        self.build.write_text(json.dumps(build), encoding='utf-8')
        self.data['student']['fonts'] = {'Sheet Sans': False}
        self.write_report()
        self.stamp(self.report, 2)
        result, output = self.status()
        self.assertEqual(result, 0, output)
        self.assertIn('기기마다 줄바꿈과 쪽 나눔이 달라질 수 있습니다', output)

    def test_interactive_review_requires_screen_section(self):
        self.data['student']['interactive'] = True
        self.write_report()
        self.stamp(self.report, 2)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('화면 확인 절이 없습니다', output)
        with self.review.open('a', encoding='utf-8') as review:
            review.write('## 화면 확인\n폰·태블릿과 채움 인쇄를 확인함.\n')
        self.stamp(self.review, 3)
        result, output = self.status()
        self.assertEqual(result, 0, output)

    def test_student_markup_requires_screen_review_even_if_old_report_omits_flag(self):
        self.student.write_text('<html data-role="student" data-interactive="1"></html>', encoding='utf-8')
        self.stamp(self.student, 1)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('화면 확인 절이 없습니다', output)

    def test_stale_report_and_report_errors_fail(self):
        self.stamp(self.report, 1)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('HTML 변경 뒤', output)
        self.data['problems'] = [{'level': 'error'}]
        self.write_report()
        self.stamp(self.report, 2)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('오류 1개', output)

    def test_review_needs_each_page_and_freshness(self):
        self.review.write_text('판정: 통과\n검수: 독립\n교사용 1쪽\n## 내용 검산\n', encoding='utf-8')
        self.stamp(self.review, 1)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('검사 뒤에 검토하지 않았습니다', output)
        self.assertIn('학생 1쪽 검토가 없습니다', output)
        self.assertIn('교사용 2쪽 검토가 없습니다', output)

    def test_edited_teacher_is_original_even_when_source_changes(self):
        self.source.write_text('{"changed":true}', encoding='utf-8')
        edited = self.teacher.read_text(encoding='utf-8').replace('data-role="teacher"',
                                                                  'data-role="teacher" data-saved-at="2026-01-01"')
        self.teacher.write_text(edited.replace('</html>', '수정</html>'), encoding='utf-8')
        self.stamp(self.teacher, 1)
        self.fake_plan.load_plan = lambda folder: ({}, b'', [types.SimpleNamespace(level='error')])
        result, output = self.status()
        self.assertEqual(result, 0, output)
        self.assertIn('편집본이 원본', output)

    def test_edited_text_warnings_replace_old_counts_and_respect_solution_policy(self):
        build = json.loads(self.build.read_text(encoding='utf-8'))
        build.update(grade='중1', unit='삼각형의 작도', plan_warnings={'text-length': 10, 'term-beyond-grade': 0})
        self.build.write_text(json.dumps(build), encoding='utf-8')
        source = self.teacher.read_text(encoding='utf-8').replace('data-role="teacher"',
                    'data-role="teacher" data-saved-at="2026-01-01" data-interactive="1" data-reveal="교사만"')
        source = source.replace('</html>', '''<body><div id="sheet-src"><section class="ws-item" data-id="q1">
            <p data-src="삼각형의 내심을 구하시오. 분모를 이항하여 $3 cm$를 구하시오.">발문</p>
            <p data-src="결과는 {{|삼각형의 내심}}입니다.">빈칸</p>
            <div class="ws-key" data-src="삼각형의 내심">비공개 정답 그림</div></section></div>
            <div id="sheet-teacher" class="ws-teacher-only"><section class="ws-item" data-solution-for="q1">
            <p class="ws-t-ans"><span data-src="삼각형의 내심">풀이</span></p>
            <p class="ws-t-mis"><span data-src="삼각형의 내심">비공개 예상 오개념</span></p>
            </section></div></body></html>''')
        for policy, count in [('교사만', 1), ('시도 후', 2)]:
            self.teacher.write_text(source.replace('data-reveal="교사만"', f'data-reveal="{policy}"'), encoding='utf-8')
            self.stamp(self.teacher, 1)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sheet_check.cmd_status(self.folder), 1)
            result = output.getvalue()
            self.assertIn(f'term-beyond-grade {count}건', result)
            self.assertIn('textbook-expression 1건', result)
            self.assertIn('math-unit-upright 1건', result)
            self.assertNotIn('text-length', result)
            self.assertIn('편집본 미검사', result)
            self.assertIn('원고 경고 절이 없습니다', result)
        with self.review.open('a', encoding='utf-8') as review:
            review.write('## 원고 경고\n' + ''.join(f'- {code}: 그대로 둠. 비교 예시\n' for code in
                         ('term-beyond-grade', 'textbook-expression', 'math-unit-upright')))
        self.stamp(self.review, 3)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sheet_check.cmd_status(self.folder), 0)
        self.assertEqual(output.getvalue().count('편집본 미검사'), 2)

    def test_edited_text_checks_use_unit_context(self):
        # 단원 조건이 붙은 표현 점검(작도 단원의 ‘호’)이 편집본에도 적용되는지 본다.
        build = json.loads(self.build.read_text(encoding='utf-8'))
        source = self.teacher.read_text(encoding='utf-8').replace('data-role="teacher"',
                    'data-role="teacher" data-saved-at="2026-01-01"')
        source = source.replace('</html>', '''<body><div id="sheet-src"><section class="ws-item" data-id="q1">
            <p data-src="두 호의 교점을 점 $A$라고 하시오.">발문</p></section></div></body></html>''')
        self.teacher.write_text(source, encoding='utf-8')
        for unit, expected in [('작도와 합동', True), ('원과 부채꼴', False)]:
            with self.subTest(unit=unit):
                build.update(grade='중1', unit=unit, plan_warnings={})
                self.build.write_text(json.dumps(build), encoding='utf-8')
                self.stamp(self.teacher, 1)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    sheet_check.cmd_status(self.folder)
                if expected:
                    self.assertIn('textbook-expression 1건', output.getvalue())
                else:
                    self.assertNotIn('textbook-expression', output.getvalue())

    def test_edited_text_checks_use_document_title_and_edited_header_unit(self):
        start = '<html data-role="teacher" data-saved-at="2026-01-01"><head><title>'
        body = '</title></head><body><div id="sheet-src">{head}<section class="ws-item" data-id="q1">' \
               '<p data-src="두 호의 교점을 점 $A$라고 하시오.">발문</p></section></div></body></html>'
        for title, unit, edited_unit in [('크기가 같은 각의 작도', None, None),
                                         ('기본 도형', '원과 부채꼴', '작도와 합동')]:
            with self.subTest(title=title, unit=edited_unit):
                head = (f'<section class="ws-item" data-id="head"><table class="ws-head"><tbody><tr>'
                        f'<td data-src="단원 : {edited_unit}">단원 : {edited_unit}</td>'
                        '</tr></tbody></table></section>') if edited_unit else ''
                build = json.loads(self.build.read_text(encoding='utf-8'))
                build.update(grade='중1', plan_warnings={})
                if unit is not None:
                    build['unit'] = unit
                else:
                    build.pop('unit', None)
                self.build.write_text(json.dumps(build), encoding='utf-8')
                self.teacher.write_text(start + title + body.format(head=head), encoding='utf-8')
                self.stamp(self.teacher, 1)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    sheet_check.cmd_status(self.folder)
                self.assertIn('textbook-expression 1건', output.getvalue())

    def test_warning_review_rejects_placeholders_and_accepts_punctuation(self):
        from sheet_plan import Issue
        self.fake_plan.load_plan = lambda folder: ({}, b'', [Issue('warning', '$', '', '', 'test-warning')])
        original = self.review.read_text(encoding='utf-8') + '## 원고 경고\n'
        for decision, expected in [('고침 — .', 1), ('그대로 둠 — …', 1), ('고침: (까닭)', 1), ('고침 — 가!', 1),
                                   ('그대로 둠. 근거', 0), ('고침: 실제 근거', 0), ('고침-근거', 0),
                                   ('고침 – 근거', 0), ('고침 — 근거', 0)]:
            with self.subTest(decision=decision):
                self.review.write_text(original + f'- `test-warning`: {decision}\n', encoding='utf-8')
                self.stamp(self.review, 3)
                result, output = self.status()
                self.assertEqual(result, expected, output)

    def test_unchecked_html_is_named_in_gate_and_delivery_but_not_delivered(self):
        extra = '삼각형의 작도 (태블릿 작도판).html'
        for name in (extra, '활동 (내 답).html', '활동 (내 답) (1).html'):
            (self.folder / name).write_text('<html></html>', encoding='utf-8')
        (self.out / '비교.html').write_text('<html></html>', encoding='utf-8')
        # 조립 도구가 남긴 예전 판 보관 폴더는 스킬 밖 파일로 세지 않는다.
        (self.folder / '_이전').mkdir()
        (self.folder / '_이전' / '활동-001.html').write_text('<html></html>', encoding='utf-8')
        result, output = self.status()
        self.assertEqual(result, 0, output)
        note = f'검사하지 않은 HTML: {extra} — 정답 유출·인쇄·호환 검사를 거치지 않았습니다'
        self.assertEqual(output.count(note), 2)
        self.assertNotIn('내 답', output)
        self.assertNotIn('비교.html', output)
        self.assertNotIn('활동-001.html', output)
        self.assertNotIn(f'전달: {self.folder / extra}', output)

    def test_plan_errors_and_unavailable(self):
        self.fake_plan.load_plan = lambda folder: ({}, b'', [types.SimpleNamespace(level='error')])
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('plan 오류 1개', output)
        result, output = self.status(module=False)
        self.assertEqual(result, 0, output)
        self.assertIn('plan 확인 불가', output)

    def test_missing_source_makes_plan_unavailable_not_failed(self):
        folder = self.folder / '구성-없음'
        folder.mkdir()
        ok, note = sheet_check._plan_status(folder)
        self.assertTrue(ok)
        self.assertIn('확인 불가', note)

    def test_real_plan_rejects_malformed_json(self):
        self.source.write_text('{', encoding='utf-8')
        ok, note = sheet_check._plan_status(self.folder)
        self.assertFalse(ok, note)
        self.assertIn('plan 오류 1개', note)

    def test_saved_marker_in_script_does_not_count(self):
        self.teacher.write_text('<html><script>let s="data-saved-at";</script></html>', encoding='utf-8')
        self.source.write_text('{"changed":true}', encoding='utf-8')
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertNotIn('편집본이 원본', output)

    def test_malformed_page_report_cannot_pass_review(self):
        self.data['teacher']['pages'] = [{}]
        self.write_report()
        self.stamp(self.report, 2)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('쪽 번호나 구분이 올바르지 않습니다', output)

    def test_report_hash_detects_changed_html_even_with_old_mtime(self):
        self.data['html_sha256'] = {'teacher': sheet_check._sha(self.teacher), 'student': 'old-hash'}
        self.write_report()
        self.stamp(self.report, 2)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('HTML 변경 뒤', output)

    def test_missing_pdf_blocks_delivery(self):
        # 별도의 시험 폴더에서 PDF를 만들지 않고 경로만 바꿔 누락을 재현한다.
        other = self.folder / '다른이름.html'
        other.write_text(self.student.read_text(encoding='utf-8'), encoding='utf-8')
        build = json.loads(self.build.read_text(encoding='utf-8'))
        build['student'] = {'file': other.name, 'sha256': sheet_check._sha(other)}
        self.build.write_text(json.dumps(build), encoding='utf-8')
        self.stamp(other, 1)
        result, output = self.status()
        self.assertEqual(result, 1)
        self.assertIn('전달할 PDF가 없습니다', output)


if __name__ == '__main__':
    unittest.main()
