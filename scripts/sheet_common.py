"""Shared names and paths for the worksheet CLI."""
from fractions import Fraction
import hashlib
from html.parser import HTMLParser
import json
import os
from statistics import median
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit


def youtube_id(url):
    """Accept only genuine YouTube hosts and an eleven-character video ID."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return None
        host = (parsed.hostname or '').lower()
        parts = [part for part in parsed.path.split('/') if part]
        if host in ('youtu.be', 'www.youtu.be'):
            ident = parts[0] if len(parts) == 1 else ''
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'):
            ident = (parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else
                     parts[1] if len(parts) == 2 and parts[0] in ('embed', 'shorts', 'live') else '')
        else:
            return None
        return ident if re.fullmatch(r'[A-Za-z0-9_-]{11}', ident) else None
    except ValueError:
        return None

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '구성.json'
CHECK_DIR = '검사'
BUILD_FILE = 'build.json'
TEACHER_SUFFIX = ' (교사용)'
TOOL = 'pbj-html-sheet'
VERSION = 1
KINDS = ('수업', '연습', '평가')
PLACEHOLDER = '〔작성〕'
RASTER_MIMES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'}
LABEL_MM = {'평가': 25.1, '연습': 46.1, '수업': 30}
PICTURE_CELL_PADDING_MM = 3.6
SPLIT_RATIOS = ('1:1', '2:1', '1:2', '3:2', '2:3')
BOX_MAX_TICKS = 25


def content_width_mm(columns):
    return 90.8 if columns == 2 else 188  # (190 − 6) / 2 − 1.2


def split_widths_mm(content_mm, ratio):
    left, right = map(int, ratio.split(':'))
    return tuple((content_mm - 5) * part / (left + right) for part in (left, right))


def situation_cell_mm(content_mm, image_mm):
    return round(min(.36 * content_mm, max(.31 * content_mm, image_mm + PICTURE_CELL_PADDING_MM)), 1)


def figure_width_mm(picture, available_mm):
    return min(picture['width_mm'], available_mm)


def box_five(group):
    """중앙값을 제외한 두 절반에서 사분위수를 구한다(짝수는 반씩)."""
    if 'five' in group:
        return group['five']
    values = sorted(group['data'])
    half = len(values) // 2
    return [values[0], median(values[:half]), median(values), median(values[-half:]), values[-1]]


def box_tick_values(bounds, step):
    # Decimal input ends on its intended tick (e.g. 0.3 with step 0.1).
    low, high, spacing = map(lambda v: Fraction(str(v)), (*bounds, step))
    count = (high - low) // spacing + 1
    if not 2 <= count <= BOX_MAX_TICKS:
        raise ValueError(f'가로 눈금은 2~{BOX_MAX_TICKS}개여야 합니다.')
    return [low + i * spacing for i in range(count)]


def safe_title(title: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title).strip().rstrip('.')
    stem = stem.rstrip() or '_'
    return '_' + stem if PureWindowsPath(stem + '.html').is_reserved() else stem


def html_paths(folder: Path, title: str):
    stem = safe_title(title)
    return folder / (stem + TEACHER_SUFFIX + '.html'), folder / (stem + '.html')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


PYTHON_MODULES = (('fonttools', 'fontTools'), ('pillow', 'PIL'), ('pymupdf', 'pymupdf'))
_PYTHON_TIMEOUT = 5


def _python_candidates():
    windows = sys.platform == 'win32'
    launcher = shutil.which('py.exe') if windows else None
    if launcher:
        try:
            listing = subprocess.run([launcher, '-0p'], cwd=ROOT, capture_output=True, timeout=_PYTHON_TIMEOUT)
            if listing.returncode == 0:
                try:
                    text = listing.stdout.decode('utf-8')
                except UnicodeDecodeError:
                    text = listing.stdout.decode('mbcs', errors='replace')
                for line in text.splitlines():
                    match = re.fullmatch(r'\s*-\S+\s+(?:\*\s+)?(.+?\.exe)\s*', line, re.I)
                    if match:
                        yield Path(match[1])
        except (OSError, subprocess.TimeoutExpired):
            pass
    names = ('python.exe', 'python3.exe') if windows else ('python3',)
    for entry in os.get_exec_path():
        directory = Path(entry.strip('"') if windows else entry)
        if not directory.is_absolute() or (windows and directory.name.lower() == 'windowsapps'):
            continue  # Do not run a current-folder executable or a Microsoft Store shortcut.
        for name in names:
            yield directory / name
    if windows:
        for env, pattern in (('APPDATA', 'uv/python/*/python.exe'),
                             ('LOCALAPPDATA', 'Programs/Python/*/python.exe')):
            root = os.environ.get(env)
            if root:
                yield from sorted(Path(root).glob(pattern))
    else:
        yield Path('/opt/homebrew/bin/python3')
        yield Path('/usr/local/bin/python3')
        yield from sorted(Path.home().glob('.local/share/uv/python/*/bin/python3'))


def find_usable_python():
    """Find the first other local Python that can import all optional modules."""
    seen = {Path(sys.executable).resolve()}
    imports = ', '.join(module for _, module in PYTHON_MODULES)
    probe = f'import sys; assert sys.version_info >= (3, 9); import {imports}'
    for path in _python_candidates():
        try:
            if not path.is_absolute() or not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            # Ignore PYTHONPATH and avoid the worksheet folder; keep the user site available.
            result = subprocess.run([str(path), '-E', '-B', '-c', probe], cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=_PYTHON_TIMEOUT)
            if result.returncode == 0:
                return path
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            continue
    return None


def python_sheet_command(executable, command):
    executable = sys.executable if str(executable) == 'python' else str(executable)
    script = str(ROOT / 'scripts/sheet.py')
    if sys.platform == 'win32':
        # PowerShell expands $ and backticks even inside double-quoted paths.
        executable, script = (part.replace('`', '``').replace('$', '`$') for part in (executable, script))
        return f'& "{executable}" -X utf8 "{script}" {command}'
    return f'"{executable}" -X utf8 "{script}" {command}'


def engine_script(root=ROOT):
    directory = root / 'assets/engine'
    parts = [(directory / 'sheet.js').read_text(encoding='utf-8')]
    for name in ('solve.js', 'sync.js'):
        path = directory / name
        if path.is_file():
            parts.append(path.read_text(encoding='utf-8'))
    return '\n'.join(parts)


FOREIGN_CODE_FIX = '조립된 HTML을 손으로 고치지 말고 구성.json을 고쳐 다시 조립하세요.'
_URL_ATTRS = frozenset('href xlink:href src action formaction data poster background cite'.split())
_URL_LEADING = ''.join(map(chr, range(33)))
_REVEAL_DATA = re.compile(r'[A-Za-z0-9+/]+={0,2}')
_STYLE_IDS = frozenset(('sheet-css', 'sheet-fonts', 'sheet-theme', 'sheet-user'))
_CSS_ESCAPE = r'\\(?:[0-9a-fA-F]{1,6}(?:\r\n|[ \t\r\n\f])?|[^\r\n\f0-9a-fA-F])'
_CSS_IDENT = rf'(?:[-_a-zA-Z0-9]|[^\x00-\x7f]|{_CSS_ESCAPE})+'
_CSS_TOKEN = re.compile(rf'''/\*[\s\S]*?(?:\*/|\Z)|(?P<string>"(?:\\[\s\S]|[^"\\])*"?|'(?:\\[\s\S]|[^'\\])*'?)
                            |@(?P<at>{_CSS_IDENT})|(?P<ident>{_CSS_IDENT})|(?P<paren>[()])''', re.X)
_CSS_URL = re.compile(r'''\s*\(\s*(?:"((?:\\[\s\S]|[^"\\])*)"|'((?:\\[\s\S]|[^'\\])*)'|((?:\\[\s\S]|[^\\)])*))\s*(?:\)|\Z)''')
_CSS_UNESCAPE = re.compile(r'\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|(\r\n|[\r\n\f])|([\s\S]))')


def _css_unescape(value):
    def replace(match):
        if match[1]:
            codepoint = int(match[1], 16)
            return chr(codepoint) if 0 < codepoint <= 0x10ffff else '\ufffd'
        return '' if match[2] else match[3]
    return _CSS_UNESCAPE.sub(replace, value)


def _external_css_resource(value):
    value = _css_unescape(value).strip(_URL_LEADING)
    return bool(value and not value.lower().startswith(('data:', '#')))


def _style_resource_issues(css):
    """Inspect CSS tokens without mistaking comments or ordinary strings for resources."""
    problems, skip_until, functions, last_ident = [], 0, [], None
    image_sets = ('image-set', '-webkit-image-set')
    for token in _CSS_TOKEN.finditer(css):
        if token.start() < skip_until:
            continue
        if token['at'] and _css_unescape(token['at']).lower() == 'import':
            problems.append('<style>에 @import가 있습니다.')
        if token['ident']:
            name = _css_unescape(token['ident']).lower()
            if functions and functions[-1] in image_sets and name == 'var' and re.match(r'\s*\(', css[token.end():]):
                problems.append('<style>의 image-set()에 외부 주소일 수 있는 변수가 있습니다.')
            if name == 'url':
                url = _CSS_URL.match(css, token.end())
                if url:
                    skip_until = url.end()
                    value = next(part for part in url.groups() if part is not None)
                    if _external_css_resource(value):
                        problems.append('<style>의 url()이 외부 자원을 가리킵니다.')
                    last_ident = None
                    continue
            last_ident = (name, token.end())
        elif token['paren']:
            if token['paren'] == '(':
                name = last_ident[0] if last_ident and not css[last_ident[1]:token.start()].strip() else ''
                functions.append(name)
            elif functions:
                functions.pop()
            last_ident = None
        else:
            if token['string'] and functions and functions[-1] in image_sets:
                text = token['string']
                value = text[1:-1] if len(text) > 1 and text[-1] == text[0] else text[1:]
                if _external_css_resource(value):
                    problems.append('<style>의 image-set()이 외부 자원을 가리킵니다.')
            last_ident = None
    return problems


class ForeignCodeAudit(HTMLParser):
    """Inspect tags and script bodies without opening the worksheet in a browser."""
    def __init__(self):
        super().__init__()
        self.problems = []
        self.engine_hash = None
        self.seen = set()
        self.seen_styles = set()
        self.script = None
        self.body = []
        self.style_body = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in ('iframe', 'object', 'embed', 'base'):
            self.problems.append(f'엔진이 만들지 않은 <{tag}>가 있습니다.')
        if tag == 'meta' and any(name == 'http-equiv' and (value or '').strip().lower() == 'refresh'
                                 for name, value in attrs):
            self.problems.append('주소를 이동시키는 <meta http-equiv="refresh">가 있습니다.')
        if tag == 'style':
            ids = [value for name, value in attrs if name == 'id']
            ident = ids[0] if len(ids) == 1 else None
            if ident not in _STYLE_IDS or ident in self.seen_styles:
                label = f' ({ident})' if ident and re.fullmatch(r'[\w.-]{1,80}', ident) else ''
                self.problems.append(f'허용되지 않거나 중복된 <style>{label}가 있습니다.')
            if ident in _STYLE_IDS:
                self.seen_styles.add(ident)
            self.style_body = []
        for name, value in attrs:
            if tag == 'link' and name == 'href' and value and value.strip() and not value.lstrip().startswith('#'):
                self.problems.append('HTML 밖의 자원을 가리키는 <link>가 있습니다.')
            if name.startswith('on') and value is not None:
                self.problems.append(f'<{tag}>에 동작을 실행하는 {name} 속성이 있습니다.')
            if (name in _URL_ATTRS and value and
                    re.sub(r'[\t\r\n]', '', value).lstrip(_URL_LEADING).lower().startswith('javascript:')):
                self.problems.append(f'<{tag}>의 {name}에 javascript: 주소가 있습니다.')
        if tag != 'script':
            return
        ident = values.get('id') or ''
        if ((ident == 'sheet-js' and attrs == [('id', 'sheet-js')]) or
                (ident == 'sheet-reveal' and len(attrs) == 2 and
                 values == {'id': 'sheet-reveal', 'type': 'application/octet-stream'})) and ident not in self.seen:
            self.script, self.body = ident, []
            self.seen.add(ident)
        else:
            label = f' ({ident})' if re.fullmatch(r'[\w.-]{1,80}', ident) else ''
            self.problems.append(f'엔진이 만들지 않은 스크립트{label}가 있습니다.')

    def handle_startendtag(self, tag, attrs):
        if tag == 'script':
            self.problems.append('닫는 태그가 없는 스크립트가 있습니다.')
        else:
            self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.script:
            self.body.append(data)
        elif self.style_body is not None:
            self.style_body.append(data)

    def handle_endtag(self, tag):
        if tag == 'style' and self.style_body is not None:
            self.problems.extend(_style_resource_issues(''.join(self.style_body)))
            self.style_body = None
        if tag != 'script' or not self.script:
            return
        body = ''.join(self.body)
        if self.script == 'sheet-js':
            self.engine_hash = sha256(body.encode('utf-8'))
        elif not _REVEAL_DATA.fullmatch(body):
            self.problems.append('sheet-reveal에 올바른 정답 자료 형식이 아닌 내용이 있습니다.')
        self.script, self.body = None, []

    def issues(self, recorded_hash=None):
        problems = list(self.problems)
        if self.script:
            problems.append(f'{self.script} 스크립트가 닫히지 않았습니다.')
        if self.style_body is not None:
            problems.append('<style>이 닫히지 않았습니다.')
        if self.engine_hash is not None:
            # 예전 조립 기록에는 스크립트 해시가 없다. 그때는 내용 비교를 건너뛰고 덧붙인 요소만 본다.
            # 지금 설치본과 비교하면 스킬을 업데이트한 뒤 예전 활동지의 브라우저 편집본을 derive할 수 없다.
            if recorded_hash is not None and self.engine_hash != recorded_hash:
                problems.append('sheet-js 스크립트 내용이 조립 때와 다릅니다.')
        elif recorded_hash is not None and 'sheet-js' not in self.seen:
            problems.append('조립 때 넣은 sheet-js 스크립트가 없습니다.')
        return list(dict.fromkeys(problems))


def foreign_code_issues(source, recorded_hash=None):
    parser = ForeignCodeAudit()
    parser.feed(source)
    parser.close()
    return parser.issues(recorded_hash)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def atomic_write(path: Path, data: bytes):
    with NamedTemporaryFile(dir=path.parent, prefix='.sheet-', delete=False) as stream:
        stage = Path(stream.name)
        stream.write(data)
    try:
        stage.replace(path)
    finally:
        stage.unlink(missing_ok=True)


def image_path(folder: Path, src: str) -> Path:
    windows = PureWindowsPath(src)
    if ':' in src or windows.anchor or not src or any(ord(c) < 32 for c in src):
        raise ValueError('활동지 폴더 안의 상대 경로를 쓰세요. 예: 그림/도형.png')
    root = folder.resolve()
    path = (root / Path(*windows.parts)).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('활동지 폴더 밖으로 나갈 수 없습니다. 그림/ 아래에 파일을 넣으세요.')
    return path
