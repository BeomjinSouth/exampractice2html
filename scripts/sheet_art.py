"""활동지에 쓸 그림을 생성하거나 가져와 기록한다."""
from collections import deque
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import struct
import subprocess
import tempfile
import zlib

from sheet_common import ROOT, atomic_write, json_bytes

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None


KINDS = ('장면', '스티커', '선화')
ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,119}\Z')
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
IMAGE_ERRORS = (OSError, ValueError) + ((Image.DecompressionBombError,) if Image is not None else ())


class ArtError(Exception):
    def __init__(self, message, hint, code=1):
        super().__init__(message)
        self.hint = hint
        self.code = code


class MissingPillowError(ValueError):
    pass


def safe_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or PureWindowsPath(value + '.png').is_reserved():
        raise ArtError('그림 id가 올바르지 않습니다.', 'id는 영문 소문자·숫자로 시작하고 영문 소문자·숫자·_·-만 쓰세요.')
    return value


def read_index(path):
    if path.is_symlink():
        raise ArtError('그림 기록에 바로 가기를 쓸 수 없습니다.', '그림/_에셋.json을 일반 파일로 확인하세요.')
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or any(not isinstance(item, dict) for item in data.values()):
            raise ValueError('기록 형식이 올바르지 않습니다')
        return data
    except (ValueError, UnicodeError) as error:
        raise ArtError(f'그림 기록을 읽을 수 없습니다: {path}', '기존 기록을 확인하고 올바른 JSON 객체로 고치세요.') from error


def png_size(data):
    """Pillow가 없을 때 PNG 청크와 크기를 확인한다."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError('PNG가 아닙니다')
    pos = len(PNG_SIGNATURE)
    size = None
    seen_data = False
    while pos + 12 <= len(data):
        length = struct.unpack_from('>I', data, pos)[0]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError('PNG 청크가 잘렸습니다')
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from('>I', data, pos + 8 + length)[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != crc:
            raise ValueError('PNG 검사값이 올바르지 않습니다')
        if size is None:
            if kind != b'IHDR' or length != 13:
                raise ValueError('PNG 머리말이 올바르지 않습니다')
            size = struct.unpack_from('>II', chunk)
            if not all(size):
                raise ValueError('그림 크기가 올바르지 않습니다')
        if kind == b'IDAT':
            seen_data = True
        if kind == b'IEND':
            if length or not seen_data or end != len(data):
                raise ValueError('PNG 끝이 올바르지 않습니다')
            return list(size)
        pos = end
    raise ValueError('PNG 끝을 찾을 수 없습니다')


def remove_background(image):
    """가장자리에서 이어지는 모서리 배경색만 투명하게 만든다."""
    width, height = image.size
    pixels = image.load()
    corners = [pixels[0, 0], pixels[width - 1, 0], pixels[0, height - 1], pixels[width - 1, height - 1]]
    color = tuple(sum(pixel[k] for pixel in corners) / 4 for k in range(3))
    seen = bytearray(width * height)
    queue = deque()

    def add(x, y):
        index = y * width + x
        if seen[index]:
            return
        seen[index] = 1
        if sum(abs(pixels[x, y][k] - color[k]) for k in range(3)) < 60:
            queue.append(index)

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(height):
        add(0, y)
        add(width - 1, y)
    while queue:
        index = queue.popleft()
        x, y = index % width, index // width
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = red, green, blue, 0
        if x:
            add(x - 1, y)
        if x + 1 < width:
            add(x + 1, y)
        if y:
            add(x, y - 1)
        if y + 1 < height:
            add(x, y + 1)


def process_image(data, kind):
    """PNG로 통일하고 필요하면 축소와 스티커 배경 처리를 한다."""
    if Image is None:
        if data.startswith(b'\xff\xd8'):
            raise MissingPillowError('Pillow가 없어 JPG를 PNG로 바꿀 수 없습니다')
        return data, png_size(data), ['Pillow가 없어 투명 배경 처리와 크기 조정을 하지 못했습니다.']
    with Image.open(BytesIO(data)) as original:
        if original.format not in ('PNG', 'JPEG'):
            raise ValueError('PNG 또는 JPG 그림이 아닙니다')
        original.verify()
    with Image.open(BytesIO(data)) as original:
        has_alpha = 'A' in original.getbands() or 'transparency' in original.info
        image = ImageOps.exif_transpose(original).convert('RGBA' if kind == '스티커' or has_alpha else 'RGB')
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    warnings = []
    if kind == '스티커':
        alpha = image.getchannel('A')
        if not sum(alpha.histogram()[:10]):
            remove_background(image)
            alpha = image.getchannel('A')
        if alpha.getbbox() is None:
            raise ValueError('배경을 제외한 그림이 없습니다')
        transparent = sum(alpha.histogram()[:10]) / (image.width * image.height)
        if not transparent:
            raise ValueError('스티커 배경을 투명하게 만들지 못했습니다')
        if transparent < 0.03:
            warnings.append('투명한 부분이 3% 미만입니다. 배경을 눈으로 확인하세요.')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue(), list(image.size), warnings


def last_line(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    lines = str(value or '').strip().splitlines()
    return lines[-1] if lines else ''


class Renderer:
    def render(self, prompt, references, kind):
        executable = shutil.which('codex.cmd') or shutil.which('codex')
        if not executable:
            raise ArtError('codex 명령을 찾을 수 없습니다.', 'Codex CLI를 설치하고 로그인한 뒤 다시 실행하세요.', 2)
        canvas = ('Request a transparent background (background=transparent) if supported. '
                  if kind == '스티커' else
                  'Request a landscape canvas if supported. ' if kind == '장면' else '')
        attached = ('Use the attached images as visual references. ' if references else '')
        message = ('[SHEET-RENDER]\nYou are a renderer. Do not read or write files or run shell commands. '
                   'Call the built-in image generation tool exactly once with the prompt between the markers. '
                   + canvas + attached + 'Reply with exactly DONE or FAILED: <reason>.\n<<<PROMPT\n'
                   + prompt + '\nPROMPT>>>')
        command = [executable, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                   '-C', tempfile.gettempdir(), '-s', 'read-only', '--json']
        for reference in references:
            command.extend(['-i', str(reference.resolve())])
        # 지시문은 표준 입력으로 넘긴다. Windows의 codex.cmd는 인수 속 줄바꿈에서 글을 잘라
        # 첫 줄("[SHEET-RENDER]")만 전달하기 때문이다(pbj-html-deck의 deck_art.py와 같은 방식).
        command.append('-')
        try:
            result = subprocess.run(command, input=message, capture_output=True, encoding='utf-8',
                                    errors='replace', timeout=300,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            detail = last_line(error.stderr)
            raise ArtError('그림 생성이 300초를 넘겼습니다.' + (f' {detail}' if detail else ''),
                           'Codex 연결 상태를 확인하고 다시 실행하세요.', 2) from error
        except OSError as error:
            raise ArtError(f'Codex를 실행하지 못했습니다: {error}', 'Codex CLI 설치와 실행 권한을 확인하세요.', 2) from error
        thread_id, messages = None, []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'thread.started':
                thread_id = event.get('thread_id')
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'agent_message':
                messages.append(str(item.get('text', '')))
        detail = last_line(result.stderr) or (last_line(messages[-1]) if messages else '')
        if result.returncode:
            raise ArtError('Codex 그림 생성에 실패했습니다.' + (f' {detail}' if detail else ''),
                           '오류와 Codex 로그인·연결 상태를 확인하고 다시 실행하세요.', 2)
        images = []
        if isinstance(thread_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', thread_id):
            home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
            images = list((home / 'generated_images' / thread_id).glob('*.png'))
        if not images:
            raise ArtError('Codex가 생성한 PNG를 찾지 못했습니다.' + (f' {detail}' if detail else ''),
                           'Codex의 이미지 도구와 연결 상태를 확인하고 다시 실행하세요.', 2)
        return max(images, key=lambda path: path.stat().st_mtime_ns)


def backup_path(directory, ident):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    candidate = directory / f'{ident}-{stamp}.png'
    number = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = directory / f'{ident}-{stamp}-{number}.png'
        number += 1
    return candidate


def cmd_art(args):
    try:
        return _cmd_art(args)
    except ArtError as error:
        print(f'오류: {error}')
        print(f'다음: {error.hint}')
        return error.code
    except OSError as error:
        print(f'도구 오류: 그림 파일을 읽거나 쓸 수 없습니다. ({error})')
        print('다음: 그림 폴더와 파일 권한을 확인하고 다시 실행하세요.')
        return 2


def _cmd_art(args):
    folder = args.folder.resolve()
    if not folder.is_dir():
        raise ArtError(f'활동지 폴더가 없습니다: {folder}', 'new로 활동지 폴더를 만들거나 기존 폴더를 지정하세요.')
    directory = folder / '그림'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ArtError('그림 폴더가 일반 폴더가 아닙니다.', '활동지 폴더 안의 그림 폴더를 확인하세요.')
    index_path = directory / '_에셋.json'
    if args.list:
        if args.id is not None or args.kind is not None or args.prompt is not None or args.ref or args.import_file or args.replace:
            raise ArtError('--list에는 다른 그림 옵션을 함께 쓸 수 없습니다.', 'art <폴더> --list로 다시 실행하세요.')
        index = read_index(index_path)
        if not index:
            print('기록된 그림이 없습니다.')
        for ident, entry in index.items():
            print(f'{ident} · {entry.get("kind", "?")} · 그림/{ident}.png · {entry.get("size", "?")} · {entry.get("created_at", "?")}')
            if entry.get('prompt'):
                print(f'  설명: {entry["prompt"]}')
            print(f'  원본: {entry.get("source", "?")}')
        print('다음: 구성.json에 필요한 그림의 src·width_mm·alt를 넣으세요.')
        return 0
    ident = safe_id(args.id)
    if args.kind is not None and args.kind not in KINDS:
        raise ArtError('그림 종류가 올바르지 않습니다.', '--kind에 장면, 스티커, 선화 중 하나를 쓰세요.')
    importing = args.import_file is not None
    if importing and args.ref:
        raise ArtError('그림을 가져올 때는 --ref를 쓸 수 없습니다.', '--ref를 빼고 다시 실행하세요.')
    if not importing and (not args.kind or not args.prompt or not args.prompt.strip()):
        raise ArtError('그림 종류와 설명이 필요합니다.', '--kind와 --prompt를 지정하고 다시 실행하세요.')
    kind = args.kind or '가져옴'
    source = args.import_file.resolve() if importing else None
    if importing and (not source.is_file() or source.suffix.lower() not in ('.png', '.jpg', '.jpeg')):
        raise ArtError(f'가져올 PNG 또는 JPG 파일이 없습니다: {source}', '있는 PNG 또는 JPG 파일을 지정하세요.')
    for reference in args.ref:
        if not reference.is_file():
            raise ArtError(f'참고 그림이 없습니다: {reference}', '있는 참고 그림을 지정하거나 --ref를 빼세요.')
    target = directory / f'{ident}.png'
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ArtError(f'그림 경로가 일반 파일이 아닙니다: {target}', '기존 경로를 확인하고 다른 id를 쓰세요.')
    index = read_index(index_path)
    if (target.exists() or ident in index) and not args.replace:
        raise ArtError(f'이미 있는 그림 id입니다: {ident}', '다른 id를 쓰거나 --replace로 이전 그림을 보관하세요.')
    if not importing:
        template = (ROOT / 'assets' / 'prompts' / f'{kind}.txt').read_text(encoding='utf-8')
        source = Renderer().render(template.replace('{prompt}', args.prompt.strip()), args.ref, kind)
    try:
        png, size, warnings = process_image(source.read_bytes(), kind)
    except IMAGE_ERRORS as error:
        code = 1 if importing and not isinstance(error, MissingPillowError) else 2
        raise ArtError(f'그림을 읽거나 PNG로 처리하지 못했습니다: {error}',
                       '올바른 PNG·JPG인지 확인하고 필요하면 Pillow를 설치하세요.', code) from error
    directory.mkdir(parents=True, exist_ok=True)
    archived = None
    if target.exists():
        if not args.replace:
            raise ArtError(f'이미 있는 그림입니다: {target}', '다른 id를 쓰거나 --replace를 지정하세요.')
        previous = directory / '_이전'
        if previous.is_symlink() or (previous.exists() and not previous.is_dir()):
            raise ArtError('이전 그림 보관 폴더가 일반 폴더가 아닙니다.', '그림/_이전 폴더를 확인하세요.')
        previous.mkdir(exist_ok=True)
        archived = backup_path(previous, ident)
        target.rename(archived)
    try:
        with target.open('xb') as output:
            output.write(png)
    except FileExistsError as error:
        raise ArtError(f'저장할 그림이 이미 있습니다: {target}', '그림 폴더를 확인하고 다른 id를 쓰세요.') from error
    index[ident] = {'id': ident, 'kind': kind, 'prompt': args.prompt or '',
                    'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                    'source': str(source), 'size': size}
    atomic_write(index_path, json_bytes(index))
    print(f'저장: {target} ({size[0]}×{size[1]}px)')
    if archived:
        print(f'이전 그림: {archived}')
    for warning in warnings:
        print(f'경고: {warning}')
    print('다음: 그림을 확인하고 구성.json에 src·width_mm·alt를 넣으세요.')
    return 0
