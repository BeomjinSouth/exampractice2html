import base64
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import sheet_build
import sheet_check
import sheet_sync
from sheet_plan import validate


def jwt(role):
    payload = base64.urlsafe_b64encode(json.dumps({'role': role}).encode()).decode().rstrip('=')
    return 'eyJhbGciOiJIUzI1NiJ9.' + payload + '.signature'


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'sync.json'
        self.env = {**os.environ, 'SHEET_SYNC_CONFIG': str(self.config), 'SHEET_FONT_DIR': str(self.root)}
        self.key = 'sb_publishable_public_key_123456'

    def cli(self, *args, status=0):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT / 'scripts/sheet.py'),
                                 *map(str, args)], env=self.env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, status, (result.stdout + result.stderr)[:1000])
        return result.stdout

    def test_save_show_off_and_reject_keys(self):
        self.cli('sync', 'config', '--url', 'https://project.supabase.co/', '--key', self.key)
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8')),
                         {'url': 'https://project.supabase.co', 'key': self.key})
        output = self.cli('sync', 'show')
        self.assertIn(self.key[:10], output)
        self.assertNotIn(self.key, output)
        for key in ('sb_secret_dont-save', jwt('service_role'), jwt('authenticated'), 'garbage'):
            self.cli('sync', 'config', '--url', 'https://custom.example', '--key', key, status=1)
            self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['key'], self.key)
        for url in ('http://project.supabase.co', 'https://evil.example@project.supabase.co',
                    'https://example.test/path', 'https://example.test/?a=1'):
            self.cli('sync', 'config', '--url', url, '--key', self.key, status=1)
        self.cli('sync', 'config', '--url', 'https://custom.example', '--key', jwt('anon'))
        self.cli('sync', 'off')
        self.assertFalse(self.config.exists())

    def test_schema_output(self):
        sql = self.cli('sync', 'schema')
        self.assertIn('create table if not exists public.sheet_records', sql)
        self.assertIn('enable row level security', sql)
        self.assertIn('drop policy if exists sheet_records_insert', sql)
        self.assertIn('grant select on public.sheet_records to authenticated', sql)

    def test_post_and_http_errors_without_network(self):
        class Response:
            def __init__(self, status):
                self.status = status
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        config = {'url': 'https://example.invalid', 'key': self.key}
        for response, expected in ((Response(201), 0), (Response(204), 0), (HTTPError('https://example.invalid', 401, '', {}, None), 1),
                                   (HTTPError('https://example.invalid', 403, '', {}, None), 1),
                                   (HTTPError('https://example.invalid', 404, '', {}, None), 1),
                                   (URLError('offline'), 2)):
            with self.subTest(expected=expected, response=response):
                side = response if isinstance(response, Exception) else None
                with patch('sheet_sync.urlopen', return_value=response, side_effect=side) as mocked:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(sheet_sync.test_connection(config), expected)
                    request = mocked.call_args.args[0]
                    self.assertEqual((request.method, request.full_url), ('POST', config['url'] + '/rest/v1/sheet_records'))
                    self.assertEqual(json.loads(request.data)['sheet_id'], '_test')
                    self.assertEqual(json.loads(request.data)['event'], 'check')
                    self.assertEqual(request.get_header('Prefer'), 'return=minimal')

    def test_new_id_and_validation(self):
        folder = self.root / 'worksheet'
        self.cli('new', folder, '--title', '활동지')
        data = json.loads((folder / '구성.json').read_text(encoding='utf-8'))
        self.assertRegex(data['meta']['sheet_id'], r'^[a-z0-9-]{1,32}$')
        simple = {'version': 1, 'meta': {'title': '제목', 'sync': True, 'sheet_id': data['meta']['sheet_id']},
                  'items': [{'id': 'q1', 'blocks': [{'type': 'text', 'text': '글'}]}]}
        self.assertFalse([x for x in validate(simple, self.root) if x.level == 'error'])
        simple['meta'].update(sync='true', sheet_id='BAD_형식')
        self.assertTrue({'meta.sync', 'meta.sheet_id'} <= {x.path for x in validate(simple, self.root) if x.level == 'error'})

    def test_meta_insert_filter_and_disabled_modes(self):
        data = {'version': 1, 'meta': {'title': '따옴표 "와 &', 'kind': '수업', 'sheet_id': 'stable-123'},
                'items': [{'id': 'q1', 'blocks': [{'type': 'text', 'text': '글'}]}]}
        self.cli('sync', 'config', '--url', 'https://custom.example', '--key', self.key)
        with patch.dict(os.environ, self.env):
            renderer = sheet_build.Renderer(data, self.root, [])
            teacher = sheet_build.document_html(renderer, {'tool': 'test'}, '')
            student = sheet_build.strip_teacher(teacher)
        from html.parser import HTMLParser
        class Meta(HTMLParser):
            def __init__(self, content):
                super().__init__()
                self.metas = []
                self.feed(content)
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'meta' and attrs.get('name') == 'sheet-sync':
                    self.metas.append(json.loads(attrs['content']))
        for text in (teacher, student):
            meta = Meta(text).metas
            self.assertEqual(len(meta), 1)
            self.assertEqual(meta[0], {'v': 1, 'url': 'https://custom.example', 'key': self.key,
                                       'table': 'sheet_records', 'sheet': 'stable-123', 'title': data['meta']['title']})
        problems = []
        sheet_check._audit_source(teacher, student, [], problems)
        self.assertNotIn('external-url', [x['code'] for x in problems])
        for setting in ({'sync': False}, {'interactive': False}):
            with self.subTest(setting=setting), patch.dict(os.environ, self.env):
                renderer = sheet_build.Renderer({**data, 'meta': {**data['meta'], **setting}}, self.root, [])
                self.assertEqual(Meta(sheet_build.document_html(renderer, {}, '')).metas, [])
        with patch.dict(os.environ, {**self.env, 'SHEET_SYNC_CONFIG': str(self.root / 'absent.json')}):
            self.assertEqual(Meta(sheet_build.document_html(sheet_build.Renderer(data, self.root, []), {}, '')).metas, [])
        with patch.dict(os.environ, self.env):
            renderer = sheet_build.Renderer({**data, 'meta': {'title': '제목', 'kind': '수업'}}, self.root, [])
            meta = Meta(sheet_build.document_html(renderer, {}, '')).metas[0]
            self.assertRegex(meta['sheet'], r'^sheet-[a-f0-9]{24}$')
            self.assertTrue(any('제목 해시' in warning for warning in renderer.warnings))

    def test_cli_build_uses_config_in_both_outputs(self):
        folder = self.root / 'built'
        folder.mkdir()
        data = {'version': 1, 'meta': {'title': '작은 활동지', 'kind': '수업', 'sheet_id': 'stable-1'},
                'items': [{'id': 'q1', 'blocks': [{'type': 'text', 'text': '확인'}]}]}
        (folder / '구성.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.cli('sync', 'config', '--url', 'https://custom.example', '--key', self.key)
        self.cli('build', folder)
        for name in ('작은 활동지.html', '작은 활동지 (교사용).html'):
            text = (folder / name).read_text(encoding='utf-8')
            self.assertIn('<meta name="sheet-sync"', text)
            self.assertIn('stable-1', text)
            self.assertIn(self.key, text)

    def test_engine_script_order_when_sync_exists(self):
        engine = self.root / 'assets' / 'engine'
        engine.mkdir(parents=True)
        for name, content in (('sheet.css', '/*css*/'), ('sheet.js', '/*core*/'),
                              ('solve.js', '/*solve*/'), ('sync.js', '/*sync*/')):
            (engine / name).write_text(content, encoding='utf-8')
        data = {'meta': {'title': '제목', 'kind': '수업', 'theme': '정본', 'sync': False},
                'items': [{'id': 'q1', 'blocks': [{'type': 'text', 'text': '글'}]}]}
        with patch('sheet_build.ROOT', self.root):
            document = sheet_build.document_html(sheet_build.Renderer(data, self.root, []), {}, '')
        self.assertLess(document.index('/*core*/'), document.index('/*solve*/'))
        self.assertLess(document.index('/*solve*/'), document.index('/*sync*/'))

    def test_student_navigation_disables_sync(self):
        class Protocol:
            def __init__(self):
                self.events = []
                self.url = None
            def call(self, command, params=None):
                if command == 'Page.navigate':
                    self.url = params['url']
                    return {}
                return {'data': base64.b64encode(b'pdf').decode()}
            def wait_event(self, event):
                pass
            def evaluate(self, source, *args):
                if 'Promise.race' in source:
                    return 'ready'
                if 'report:' in source:
                    return {'report': {'role': 'student', 'pages': [{}]}, 'count': 1}
                return None
        protocol = Protocol()
        sheet_check._navigate(type('Browser', (), {'cdp': protocol})(), self.root / '학생.html', False)
        self.assertTrue(protocol.url.endswith('?nosync'))
        self.assertTrue(protocol.url.startswith('file:'))

    def test_grid_labels_clipped_line_and_minus(self):
        data = {'meta': {'title': '좌표', 'kind': '수업', 'interactive': False}}
        renderer = sheet_build.Renderer(data, self.root, [])
        grid = renderer.grid({'mode': 'coord', 'x': [-3, 3], 'y': [-6, 6], 'unit_mm': 5,
                              'plot': {'mode': 'line', 'answer': [[-2, -4], [2, 4]]}})
        self.assertNotRegex(grid, r'>−1</text>')
        self.assertIn('>−2</text>', grid)
        path = re.search(r'<g class="ws-key"[^>]*><path d="M([^ ]+) ([^L]+)L([^ ]+) ([^"]+)', grid)
        self.assertIsNotNone(path)
        self.assertTrue(all(0 <= float(v) <= limit for v, limit in zip(path.groups(), (30, 60, 30, 60))))
        chart = renderer.chart({'kind': 'histogram', 'bins': [-2, -1, 0], 'values': [1, 2]})
        self.assertIn('>−2</text>', chart)
        explore = renderer.explore({'fn': 'a*x', 'params': [{'name': 'a', 'value': 2}],
                                    'x': [-3, 3], 'y': [-6, 6], 'unit_mm': 5, 'table': [-2, -1, 0]})
        self.assertIn('data-x="-2">−2</td>', explore)
        self.assertIn('data-y-for="-2">−4</td>', explore)


if __name__ == '__main__':
    unittest.main()
