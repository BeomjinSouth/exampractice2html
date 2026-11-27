from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import sheet
import sheet_art


def png(width=12, height=8, color=(30, 50, 80)):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    blank = b'\0' + b'\xff\xff\xff' * width
    middle = b'\0' + b'\xff\xff\xff' * (width // 4) + bytes(color) * (width - 2 * (width // 4)) + b'\xff\xff\xff' * (width // 4)
    raw = blank * (height // 4) + middle * (height - 2 * (height // 4)) + blank * (height // 4)
    return (sheet_art.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class ArtTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / '활동지'
        self.cli('new', self.folder, '--title', '그림 시험')
        self.directory = self.folder / '그림'
        self.index_path = self.directory / '_에셋.json'
        self.home = self.root / 'codex-home'
        self.generated = self.home / 'generated_images' / 'thread-test' / 'source.png'
        self.generated.parent.mkdir(parents=True)
        self.generated.write_bytes(png())
        self.ref = self.root / '참고.png'
        self.ref.write_bytes(png())

    def cli(self, *arguments, status=0):
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = sheet.main([str(argument) for argument in arguments])
        self.assertEqual(code, status, out.getvalue())
        self.assertIn('다음:', out.getvalue())
        return out.getvalue()

    def index(self):
        return json.loads(self.index_path.read_text(encoding='utf-8'))

    def fake_render(self, *arguments, result=None, status=0):
        if result is None:
            stdout = json.dumps({'type': 'thread.started', 'thread_id': 'thread-test'}) + '\n'
            result = subprocess.CompletedProcess(['codex'], 0, stdout=stdout, stderr='')
        with patch.dict(os.environ, {'CODEX_HOME': str(self.home)}), \
             patch('sheet_art.shutil.which', return_value='fake-codex'), \
             patch('sheet_art.subprocess.run', return_value=result) as runner:
            output = self.cli('art', self.folder, *arguments, status=status)
        return output, runner

    def test_generated_image_saved_recorded_and_references_attached(self):
        output, runner = self.fake_render('scene', '--kind', '장면', '--prompt', '자전거 대여소',
                                          '--ref', self.ref, '--ref', self.generated)
        picture = self.directory / 'scene.png'
        self.assertTrue(picture.read_bytes().startswith(sheet_art.PNG_SIGNATURE))
        record = self.index()['scene']
        self.assertEqual(record['id'], 'scene')
        self.assertEqual(record['kind'], '장면')
        self.assertEqual(record['prompt'], '자전거 대여소')
        self.assertEqual(record['size'], [12, 8])
        self.assertEqual(record['source'], str(self.generated.resolve()))
        self.assertTrue(record['created_at'])
        self.assertIn('12×8px', output)
        command = runner.call_args.args[0]
        self.assertIn('--json', command)
        self.assertEqual(command[command.index('-s') + 1], 'read-only')
        self.assertEqual(command.count('-i'), 2)
        self.assertIn(str(self.ref.resolve()), command)
        # 지시문은 인수가 아니라 표준 입력으로 간다(codex.cmd가 인수 속 줄바꿈에서 글을 자르기 때문).
        self.assertEqual(command[-1], '-')
        message = runner.call_args.kwargs['input']
        self.assertIn('자전거 대여소', message)
        self.assertIn('글자, 숫자, 수식', message)
        self.assertNotIn('stdin', runner.call_args.kwargs)
        self.assertEqual(runner.call_args.kwargs['timeout'], 300)

    def test_sticker_is_transparent_and_long_edge_is_reduced(self):
        if sheet_art.Image is None:
            self.skipTest('Pillow가 없는 환경입니다.')
        self.generated.write_bytes(png(1700, 850))
        self.fake_render('cat', '--kind', '스티커', '--prompt', '연필을 든 고양이')
        with sheet_art.Image.open(self.directory / 'cat.png') as image:
            self.assertEqual(image.size, (1600, 800))
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertGreater(image.getpixel((800, 400))[3], 0)
        self.assertEqual(self.index()['cat']['size'], [1600, 800])

    def test_import_png_list_and_duplicate_protection(self):
        self.cli('art', self.folder, 'asset-1', '--import', self.ref)
        record = self.index()['asset-1']
        self.assertEqual(record['kind'], '가져옴')
        self.assertEqual(record['prompt'], '')
        self.assertEqual(record['source'], str(self.ref.resolve()))
        self.assertEqual(record['size'], [12, 8])
        result = self.cli('art', self.folder, '--list')
        self.assertIn('asset-1', result)
        self.assertIn('가져옴', result)
        before = (self.directory / 'asset-1.png').read_bytes(), self.index_path.read_bytes()
        self.cli('art', self.folder, 'asset-1', '--import', self.ref, status=1)
        self.assertEqual(before, ((self.directory / 'asset-1.png').read_bytes(), self.index_path.read_bytes()))

    def test_replace_moves_old_image_and_updates_record(self):
        self.cli('art', self.folder, 'asset', '--import', self.ref)
        old = (self.directory / 'asset.png').read_bytes()
        self.generated.write_bytes(png(color=(120, 20, 20)))
        self.fake_render('asset', '--kind', '선화', '--prompt', '다른 그림', '--replace')
        archived = list((self.directory / '_이전').glob('asset-*.png'))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), old)
        self.assertNotEqual((self.directory / 'asset.png').read_bytes(), old)
        self.assertEqual(self.index()['asset']['kind'], '선화')
        self.assertEqual(self.index()['asset']['prompt'], '다른 그림')
        self.fake_render('asset', '--kind', '장면', '--prompt', '세 번째', '--replace')
        self.assertEqual(len(list((self.directory / '_이전').glob('asset-*.png'))), 2)

    def test_replace_failure_does_not_move_or_change_existing_image(self):
        self.cli('art', self.folder, 'asset', '--import', self.ref)
        before = (self.directory / 'asset.png').read_bytes(), self.index_path.read_bytes()
        failure = subprocess.CompletedProcess(['codex'], 1, stdout='', stderr='첫 줄\n마지막 실패 줄\n')
        output, _ = self.fake_render('asset', '--kind', '장면', '--prompt', '실패할 그림', '--replace',
                                     result=failure, status=2)
        self.assertIn('마지막 실패 줄', output)
        self.assertEqual(before, ((self.directory / 'asset.png').read_bytes(), self.index_path.read_bytes()))
        self.assertFalse((self.directory / '_이전').exists())

    def test_import_jpeg_converts_to_png(self):
        if sheet_art.Image is None:
            self.skipTest('Pillow가 없는 환경입니다.')
        jpg = self.root / '원본.jpg'
        sheet_art.Image.new('RGB', (24, 12), 'blue').save(jpg, format='JPEG')
        self.cli('art', self.folder, 'photo', '--import', jpg, '--kind', '장면')
        with sheet_art.Image.open(self.directory / 'photo.png') as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (24, 12))
        self.assertEqual(self.index()['photo']['kind'], '장면')

    def test_import_preserves_existing_transparency(self):
        if sheet_art.Image is None:
            self.skipTest('Pillow가 없는 환경입니다.')
        source = self.root / '투명.png'
        image = sheet_art.Image.new('RGBA', (12, 8), (10, 20, 30, 0))
        image.putpixel((5, 4), (255, 0, 0, 255))
        image.save(source)
        self.cli('art', self.folder, 'transparent', '--import', source)
        with sheet_art.Image.open(self.directory / 'transparent.png') as saved:
            self.assertEqual(saved.getpixel((0, 0))[3], 0)
            self.assertEqual(saved.getpixel((5, 4))[3], 255)

    def test_without_pillow_warns_and_keeps_png(self):
        with patch.object(sheet_art, 'Image', None), patch.object(sheet_art, 'ImageOps', None):
            output = self.cli('art', self.folder, 'raw', '--import', self.ref, '--kind', '스티커')
        self.assertIn('경고:', output)
        self.assertEqual((self.directory / 'raw.png').read_bytes(), self.ref.read_bytes())
        self.assertEqual(self.index()['raw']['size'], [12, 8])

    def test_without_pillow_jpg_fails_without_creating_a_png(self):
        jpg = self.root / '원본.jpg'
        jpg.write_bytes(b'\xff\xd8\xff\xd9')
        with patch.object(sheet_art, 'Image', None), patch.object(sheet_art, 'ImageOps', None):
            output = self.cli('art', self.folder, 'photo', '--import', jpg, status=2)
        self.assertIn('Pillow', output)
        self.assertFalse((self.directory / 'photo.png').exists())
        self.assertFalse(self.index_path.exists())

    def test_invalid_ids_and_other_invalid_inputs(self):
        for ident in ('../outside', '대문자', '_에셋', 'con', 'x.png', 'A'):
            with self.subTest(id=ident):
                self.cli('art', self.folder, ident, '--import', self.ref, status=1)
        self.cli('art', self.root / '없는폴더', 'x', '--import', self.ref, status=1)
        self.cli('art', self.folder, 'x', '--kind', '기타', '--prompt', '그림', status=1)
        self.cli('art', self.folder, 'x', '--kind', '장면', status=1)
        self.cli('art', self.folder, 'x', '--kind', '장면', '--prompt', '그림', '--ref', self.root / '없음.png', status=1)
        self.cli('art', self.folder, 'x', '--import', self.ref, '--ref', self.ref, status=1)
        self.cli('art', self.folder, 'x', '--list', status=1)
        self.assertFalse(self.index_path.exists())

    def test_invalid_image_and_malformed_index_are_preserved(self):
        bad = self.root / 'bad.png'
        bad.write_bytes(b'not an image')
        self.cli('art', self.folder, 'bad', '--import', bad, status=1)
        self.assertFalse((self.directory / 'bad.png').exists())
        self.index_path.write_text('{not json', encoding='utf-8')
        self.cli('art', self.folder, 'good', '--import', self.ref, status=1)
        self.assertEqual(self.index_path.read_text(encoding='utf-8'), '{not json')
        self.assertFalse((self.directory / 'good.png').exists())

    def test_duplicate_is_rejected_before_calling_codex(self):
        self.cli('art', self.folder, 'asset', '--import', self.ref)
        with patch('sheet_art.Renderer.render') as renderer:
            self.cli('art', self.folder, 'asset', '--kind', '장면', '--prompt', '풍경', status=1)
        renderer.assert_not_called()

    def test_bad_png_without_pillow_is_an_input_error(self):
        bad = self.root / '깨진.png'
        bad.write_bytes(b'broken')
        with patch.object(sheet_art, 'Image', None), patch.object(sheet_art, 'ImageOps', None):
            self.cli('art', self.folder, 'bad', '--import', bad, status=1)
        self.assertFalse((self.directory / 'bad.png').exists())

    def test_oversized_image_reports_an_input_error(self):
        if sheet_art.Image is None:
            self.skipTest('Pillow가 없는 환경입니다.')
        with patch.object(sheet_art.Image, 'MAX_IMAGE_PIXELS', 1):
            self.cli('art', self.folder, 'large', '--import', self.ref, status=1)
        self.assertFalse((self.directory / 'large.png').exists())

    def test_missing_codex_timeout_and_missing_generated_image(self):
        with patch('sheet_art.shutil.which', return_value=None):
            self.cli('art', self.folder, 'x', '--kind', '장면', '--prompt', '풍경', status=2)
        with patch('sheet_art.shutil.which', return_value='fake-codex'), \
             patch('sheet_art.subprocess.run', side_effect=subprocess.TimeoutExpired('codex', 300, stderr=b'network timeout')):
            output = self.cli('art', self.folder, 'x', '--kind', '장면', '--prompt', '풍경', status=2)
        self.assertIn('network timeout', output)
        no_image = subprocess.CompletedProcess(['codex'], 0, stdout='', stderr='이미지 도구 오류')
        output, _ = self.fake_render('x', '--kind', '장면', '--prompt', '풍경', result=no_image, status=2)
        self.assertIn('이미지 도구 오류', output)
        self.assertFalse(self.index_path.exists())

    def test_prompt_templates_include_print_and_no_text_rules(self):
        for kind in ('장면', '스티커', '선화'):
            with self.subTest(kind=kind):
                template = (ROOT / 'assets' / 'prompts' / (kind + '.txt')).read_text(encoding='utf-8')
                for term in ('{prompt}', '중학생', '흑백', '글자', '숫자', '수식', '정답', '실존 인물', '상표', '캐릭터'):
                    self.assertIn(term, template)
        self.assertIn('투명', (ROOT / 'assets' / 'prompts' / '스티커.txt').read_text(encoding='utf-8'))
        self.assertIn('가로형', (ROOT / 'assets' / 'prompts' / '장면.txt').read_text(encoding='utf-8'))
        self.assertIn('색이나 회색 음영 없이', (ROOT / 'assets' / 'prompts' / '선화.txt').read_text(encoding='utf-8'))

    def test_all_sheet_kinds_allow_two_columns(self):
        for kind in ('수업', '연습', '평가'):
            with self.subTest(kind=kind):
                folder = self.root / ('2단-' + kind)
                self.cli('new', folder, '--title', '제목', '--kind', kind, '--columns', '2')
                data = json.loads((folder / '구성.json').read_text(encoding='utf-8'))
                self.assertEqual((data['meta']['kind'], data['meta']['columns']), (kind, 2))
        output = self.cli('new', self.root / '제목없음', '--title', ' ', status=1)
        self.assertNotIn('수업형에서만', output)


if __name__ == '__main__':
    unittest.main()
