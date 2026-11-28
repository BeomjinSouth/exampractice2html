"""문서의 작성 예시와 파이썬 경로 안내를 실제 검증기와 대조한다.

포함: SKILL·내용 설계·구성 쓰기·구조와 계약·상호작용 계약의 구체적인 빈칸,
구성 쓰기의 전체 원고와 표·코드 블록 JSON, 구조와 계약의 좌표평면 JSON.
제외: 정답 없는 {{㉠}}, {{…|값}} 같은 자리표시, 말줄임표로 블록을 대신한
split·구조 설명, 검사 보고 JSON, 이미지 객체의 자리표시와 '넣지 않는다' 예.
그림 경로 예시는 임시 PNG를 준비해 경로 검사를 생략하지 않는다.
"""
import base64
import contextlib
import io
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sheet_plan import validate


# 정답이 들어간 기호·무기호 빈칸, 너비·단위·수식·대안 예시를 문서에서 읽는다.
# 표의 \|는 |로, JSON 예시의 이중 역슬래시는 단일 역슬래시로 바꾼다.
BLANK_DOCS = ('SKILL.md', 'references/내용-설계.md', 'references/구성-쓰기.md',
              'references/구조와-계약.md', 'references/상호작용-계약.md')
# 제외: 배부 원고에서 오류인 정답 없는 빈칸과 형식을 설명하는 자리표시.
EXCLUDED_BLANKS = {'{{㉠}}', '{{기호|가;;나|너비}}'}

# 말줄임표가 실제 블록을 대신하는 개략도라 JSON 원고가 아니다.
SCHEMATIC_SPLIT = '{"type":"split","ratio":"3:2","left":[…],"right":[…]}'
INLINE_BLOCK_KINDS = {
    'notice', 'nameband', 'situation', 'question', 'answer', 'steps', 'choices',
    'table', 'figure', 'figures', 'grid', 'ask', 'text', 'heading', 'callout', 'ox',
}
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC'
)


def manuscript(block):
    return {'version': 1, 'meta': {'title': '문서 예시 시험', 'kind': '수업', 'sync': False},
            'items': [{'id': 'q1', 'blocks': [block]}]}


class DocumentExamplesTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        image_dir = self.folder / '그림'
        image_dir.mkdir()
        for name in ('좌표.png', '가.png', '나.png'):
            (image_dir / name).write_bytes(PNG)

    def assert_valid(self, data):
        errors = [issue for issue in validate(data, self.folder) if issue.level == 'error']
        self.assertEqual(errors, [], errors)

    def test_documented_blank_examples_pass_plan(self):
        for path in BLANK_DOCS:
            document = (ROOT / path).read_text(encoding='utf-8')
            examples = re.findall(r'`([^`\n]*\{\{[^`\n]*\}\}[^`\n]*)`', document)
            # JSON 블록은 별도 시험에서 검증한다. 말줄임표는 개략도다.
            examples = [example for example in examples if not example.startswith('{"')
                        and '…' not in example and example not in EXCLUDED_BLANKS]
            self.assertTrue(examples, path)
            for example in examples:
                with self.subTest(document=path, example=example):
                    source = example.replace(r'\|', '|').replace('\\\\', '\\')
                    self.assert_valid(manuscript({'type': 'text', 'text': source}))

    def test_skill_reference_links_resolve_to_headings(self):
        source = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        links = re.findall(r'\]\((references/[^)#]+)(?:#([^)]+))?\)', source)
        self.assertTrue(links)
        for target, anchor in links:
            with self.subTest(target=target, anchor=anchor):
                content = (ROOT / target).read_text(encoding='utf-8')
                headings = re.findall(r'^#{1,6}\s+(.+?)\s*$', content, re.M)
                slugs = [re.sub(r'\s', '-', re.sub(r'[^\w\s-]', '', h.lower())) for h in headings]
                if anchor:
                    self.assertIn(anchor, slugs)

    def test_authoring_inline_block_examples_pass_plan(self):
        document = (ROOT / 'references/구성-쓰기.md').read_text(encoding='utf-8')
        examples = re.findall(r'`(\{"type"\s*:\s*"[^"]+"[^`\n]*\})`', document)
        self.assertIn(SCHEMATIC_SPLIT, examples)
        blocks = [json.loads(example) for example in examples if example != SCHEMATIC_SPLIT]
        self.assertTrue(INLINE_BLOCK_KINDS <= {block['type'] for block in blocks})
        for block in blocks:
            with self.subTest(kind=block['type']):
                self.assert_valid(manuscript(block))

    def test_fenced_manuscript_and_block_examples_pass_plan(self):
        document = (ROOT / 'references/구성-쓰기.md').read_text(encoding='utf-8')
        examples = [json.loads(example) for example in re.findall(r'```json\n(.*?)\n```', document, re.S)]
        self.assertEqual(len(examples), 7)
        for example in examples:
            label = example.get('type', '전체 원고')
            with self.subTest(example=label, kind=example.get('kind')):
                self.assert_valid(manuscript(example) if 'type' in example else example)
        self.assertEqual(sum(example.get('kind') == 'box' for example in examples), 4)

        contract = (ROOT / 'references/구조와-계약.md').read_text(encoding='utf-8')
        blocks = [json.loads(example) for example in re.findall(r'```json\n(.*?)\n```', contract, re.S)]
        grid = next(example for example in blocks if example.get('type') == 'grid')
        self.assert_valid(manuscript(grid))


class PythonPathDisplayTest(unittest.TestCase):
    def test_doctor_shows_current_and_selected_alternate_paths(self):
        import sheet
        from sheet_common import python_sheet_command

        with TemporaryDirectory() as folder:
            alternate = Path(folder) / '대체 환경' / 'python.exe'
            with (patch.object(sheet.importlib, 'import_module', side_effect=ImportError),
                  patch.object(sheet, 'find_usable_python', return_value=alternate),
                  contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertEqual(sheet.cmd_doctor(), 0)
            self.assertIn(sys.executable, output.getvalue())
            self.assertIn(str(alternate), output.getvalue())
            self.assertIn(sys.executable, python_sheet_command('python', 'doctor'))
            self.assertIn(str(alternate), python_sheet_command(alternate, 'build "활동지"'))


if __name__ == '__main__':
    unittest.main()
