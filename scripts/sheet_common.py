"""Shared names and paths for the worksheet CLI."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
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


def safe_title(title: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title).strip().rstrip('.')
    stem = stem.rstrip() or '_'
    return '_' + stem if PureWindowsPath(stem + '.html').is_reserved() else stem


def html_paths(folder: Path, title: str):
    stem = safe_title(title)
    return folder / (stem + TEACHER_SUFFIX + '.html'), folder / (stem + '.html')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
