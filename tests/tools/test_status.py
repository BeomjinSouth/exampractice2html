import contextlib
import html
import io
import json
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
TRIALS = Path(os.environ.get('SHEET_TEST_TRIALS', ROOT.parent / '시험' / '검사-시험'))
sys.path.insert(0, str(ROOT / 'scripts'))
import sheet_build
import sheet_check


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.folder = TRIALS / ('status-' + uuid4().hex[:10])
        self.out = self.folder / '검사'
        self.out.mkdir(parents=True, exist_ok=True)
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
