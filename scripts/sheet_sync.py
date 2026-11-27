"""Supabase 학생 기록 연동 설정. 공개 가능한 키만 HTML에 넣는다."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from sheet_common import ROOT, atomic_write, json_bytes


def config_path():
    override = os.environ.get('SHEET_SYNC_CONFIG')
    if override:
        return Path(override).expanduser()
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path.home() / '.config'
    return base / 'pbj-html-sheet' / 'sync.json'


def validate_config(url, key):
    if not isinstance(url, str) or not isinstance(key, str):
        raise ValueError('URL과 공개 키를 입력하세요.')
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        port = parts.port
    except ValueError as error:
        raise ValueError('HTTPS 프로젝트 URL을 확인하세요.') from error
    if (parts.scheme != 'https' or not host or '.' not in host or parts.username is not None or
            parts.password is not None or port not in (None, 443) or parts.path not in ('', '/') or
            parts.query or parts.fragment or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', p)
                                                  for p in host.split('.'))):
        raise ValueError('경로가 없는 HTTPS 프로젝트 URL을 쓰세요. 예: https://project.supabase.co')
    if key.startswith('sb_secret_'):
        raise ValueError('비밀 키는 사용할 수 없습니다. publishable 또는 anon 키를 쓰세요.')
    valid = bool(re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]+', key))
    if not valid:
        segments = key.split('.')
        if len(segments) == 3 and all(re.fullmatch(r'[A-Za-z0-9_-]+', s) for s in segments):
            try:
                payload = json.loads(base64.urlsafe_b64decode(segments[1] + '=' * (-len(segments[1]) % 4)))
                role = payload.get('role') if isinstance(payload, dict) else None
            except (ValueError, UnicodeError, binascii.Error):
                role = None
            if role == 'service_role':
                raise ValueError('service_role 키는 사용할 수 없습니다. publishable 또는 anon 키를 쓰세요.')
            valid = role == 'anon'
    if not valid:
        raise ValueError('publishable 또는 anon 키만 사용할 수 있습니다.')
    return {'url': url.rstrip('/'), 'key': key}


def load_config():
    path = config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('설정 객체가 아닙니다.')
        return validate_config(data.get('url'), data.get('key'))
    except (ValueError, UnicodeError) as error:
        raise ValueError('학생 기록 연동 설정이 올바르지 않습니다. sync config로 다시 설정하세요.') from error


def test_connection(config):
    payload = {'sheet_id': '_test', 'sheet_title': '연결 시험', 'build': 'test',
               'client_id': 'test', 'student': {}, 'event': 'check', 'item': 'test',
               'score': {}, 'answers': {}, 'verdicts': {}}
    key = config['key']
    request = Request(config['url'] + '/rest/v1/sheet_records', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                               'Prefer': 'return=minimal'}, method='POST')
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    except (URLError, OSError, TimeoutError):
        print('연결 실패: 네트워크 또는 프로젝트 주소를 확인하세요.')
        print('다음: 연결 상태를 확인한 뒤 sync test를 다시 실행하세요.')
        return 2
    if status in (201, 204):
        print('연결 통과: 시험 기록 1행을 저장했습니다 (sheet_id=_test).')
        print('다음: 필요하면 Supabase에서 시험 기록을 정리하세요.')
        return 0
    reasons = {401: '공개 키가 올바른지 확인하세요.', 403: '테이블 권한과 RLS 정책을 확인하세요.',
               404: '프로젝트 주소, Data API 노출 및 schema.sql 실행 여부를 확인하세요.'}
    print(f'연결 오류 HTTP {status}: {reasons.get(status, "SQL과 API 설정을 확인하세요.")}')
    print('다음: Supabase 설정을 확인하고 sync test를 다시 실행하세요.')
    return 1


def cmd_sync(args):
    command = args.sync_command
    if command == 'schema':
        print((ROOT / 'assets/supabase/schema.sql').read_text(encoding='utf-8'), end='')
        return 0
    path = config_path()
    if command == 'config':
        try:
            config = validate_config(args.url, args.key)
        except ValueError as error:
            print(f'오류: {error}')
            print('다음: 프로젝트 URL과 공개 키를 확인하세요.')
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json_bytes(config))
        print('학생 기록 연동 설정을 저장했습니다.')
        print('다음: sync show로 설정을 확인하세요.')
        return 0
    if command == 'off':
        path.unlink(missing_ok=True)
        print('학생 기록 연동 설정을 껐습니다. 이미 만든 HTML은 바뀌지 않습니다.')
        print('다음: 연동 없이 만들려면 활동지를 다시 조립하세요.')
        return 0
    config = load_config()
    if config is None:
        print('학생 기록 연동 설정이 없습니다.')
        print('다음: sync config로 공개 키를 설정하세요.')
        return 1 if command == 'test' else 0
    if command == 'show':
        print('URL:', config['url'])
        print('키:', config['key'][:10] + '…')
        print('다음: sync test로 연결을 확인할 수 있습니다.')
        return 0
    return test_connection(config)
