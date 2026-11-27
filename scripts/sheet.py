"""A4 활동지 원고와 HTML을 관리하는 명령줄 도구."""
import argparse
import importlib
import json
from pathlib import Path
import sys
from uuid import uuid4

from sheet_common import KINDS, ROOT, SOURCE, json_bytes


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = '위치 인수'
        self._optionals.title = '선택 인수'
        self.add_argument('-h', '--help', action='help', help='사용법을 봅니다.')

    def format_usage(self):
        return super().format_usage().replace('usage:', '사용법:', 1)

    def format_help(self):
        return super().format_help().replace('usage:', '사용법:', 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, '오류: 명령이나 인수가 올바르지 않습니다.\n다음: --help로 사용법을 확인하세요.\n')


def cmd_new(folder, title, kind, columns):
    if not title.strip():
        print('오류: 제목을 입력하세요.')
        print('다음: 공백이 아닌 제목을 넣고 new를 다시 실행하세요.')
        return 1
    data = json.loads((ROOT / 'assets/templates' / SOURCE).read_text(encoding='utf-8'))
    data['meta'].update(title=title, kind=kind, columns=columns, sheet_id='sheet-' + uuid4().hex[:24])
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f'오류: 이미 있는 경로입니다: {folder}')
        print('다음: 다른 폴더를 지정하거나 기존 구성.json을 사용하세요.')
        return 1
    (folder / '그림').mkdir()
    (folder / SOURCE).write_bytes(json_bytes(data))
    print(f'생성: {folder / SOURCE}, {folder / "그림"}')
    print('다음: 구성.json의 〔작성〕 자리를 채우고 plan을 실행하세요.')
    return 0


def cmd_blocks(kind=None):
    from sheet_plan import load_registry

    registry = load_registry()
    if kind is None:
        for name, block in registry.items():
            print(f'{name}: {block["description"]}')
        print('다음: blocks <종류>로 필드와 JSON 예시를 확인하세요.')
        return 0
    if kind not in registry:
        print(f'오류: 등록되지 않은 블록입니다: {kind}')
        print('다음: blocks로 블록 목록을 확인하세요.')
        return 1
    block = registry[kind]
    print(f'{kind}: {block["description"]}')
    print('필수: ' + ', '.join(block['required']))
    for name, description in block['fields'].items():
        limit = block['limits'].get(name, block['limits'].get(name + '[]'))
        print(f'  {name}: {description}' + (f' (권장 {limit}자 이하)' if limit is not None else ''))
    if block['limits']:
        print('글자 한도: ' + ', '.join(f'{field} {limit}자' for field, limit in block['limits'].items()))
    print('JSON 예시:')
    print(json_bytes(block['example']).decode('utf-8'), end='')
    print('다음: 예시를 items[].blocks에 붙이고 원고에 맞게 고치세요.')
    return 0


def main(argv=None) -> int:
    parser = Parser(prog='sheet.py', description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True, title='명령')
    new = subs.add_parser('new', help='원고 틀을 만듭니다.')
    new.add_argument('folder', type=Path, metavar='폴더')
    new.add_argument('--title', required=True, metavar='제목')
    new.add_argument('--kind', choices=KINDS, default='수업')
    new.add_argument('--columns', type=int, choices=(1, 2), default=1)
    sync = subs.add_parser('sync', help='학생 기록 연동을 설정합니다.')
    sync_commands = sync.add_subparsers(dest='sync_command', required=True, title='연동 명령')
    config = sync_commands.add_parser('config', help='프로젝트 주소와 공개 키를 저장합니다.')
    config.add_argument('--url', required=True, help='Supabase 프로젝트 HTTPS 주소')
    config.add_argument('--key', required=True, help='publishable 또는 anon 키')
    for name, help_text in [('show', '설정을 확인합니다.'), ('test', '시험 기록을 전송합니다.'),
                            ('schema', '데이터베이스 SQL을 출력합니다.'), ('off', '연동 설정을 끕니다.')]:
        sync_commands.add_parser(name, help=help_text)
    blocks = subs.add_parser('blocks', help='블록 등록부를 봅니다.')
    blocks.add_argument('kind', nargs='?', metavar='종류')
    art = subs.add_parser('art', help='활동지 그림을 만들거나 가져옵니다.')
    art.add_argument('folder', type=Path, metavar='폴더')
    art.add_argument('id', nargs='?', metavar='id')
    art.add_argument('--kind', metavar='장면|스티커|선화', help='그림의 종류를 지정합니다.')
    art.add_argument('--prompt', metavar='설명', help='만들 그림을 설명합니다.')
    art.add_argument('--ref', type=Path, action='append', default=[], metavar='그림', help='참고 그림을 첨부합니다. 여러 번 지정할 수 있습니다.')
    art.add_argument('--import', dest='import_file', type=Path, metavar='파일', help='PNG 또는 JPG 그림을 가져옵니다.')
    art.add_argument('--replace', action='store_true', help='기존 그림을 이전 폴더로 옮기고 새로 저장합니다.')
    art.add_argument('--list', action='store_true', help='기록한 그림 목록을 봅니다.')
    for command, description in [('plan', '구성을 검증'), ('build', 'HTML을 조립'),
                                 ('check', '브라우저에서 검사'), ('status', '관문 상태를 확인')]:
        sub = subs.add_parser(command, help=description + '합니다.')
        sub.add_argument('folder', type=Path, metavar='폴더')
        if command == 'build':
            sub.add_argument('--force', action='store_true', help='저장된 교사용을 구성.json으로 덮어씁니다.')
    derive = subs.add_parser('derive', help='교사용에서 학생용을 만듭니다.')
    derive.add_argument('teacher', type=Path, metavar='교사용.html')
    args = parser.parse_args(argv)
    try:
        if args.command == 'new':
            return cmd_new(args.folder, args.title, args.kind, args.columns)
        if args.command == 'sync':
            from sheet_sync import cmd_sync
            return cmd_sync(args)
        if args.command == 'blocks':
            return cmd_blocks(args.kind)
        if args.command == 'art':
            from sheet_art import cmd_art
            return cmd_art(args)
        if args.command == 'plan':
            from sheet_plan import cmd_plan
            return cmd_plan(args.folder)
        if args.command in ('build', 'derive'):
            from sheet_build import cmd_build, cmd_derive
            return cmd_build(args.folder, args.force) if args.command == 'build' else cmd_derive(args.teacher)
        try:
            checks = importlib.import_module('sheet_check')
        except ModuleNotFoundError as error:
            if error.name != 'sheet_check':
                raise
            print('검사 모듈이 없습니다.')
            print('다음: scripts/sheet_check.py가 설치되었는지 확인하세요.')
            return 2
        return getattr(checks, 'cmd_' + args.command)(args.folder)
    except (OSError, UnicodeError, ImportError, ValueError) as error:
        if isinstance(error, OSError):
            print(f'도구 오류: 파일을 읽거나 쓸 수 없습니다. ({error.filename or type(error).__name__})')
        else:
            print(f'도구 오류: 입력 파일 또는 설치 상태를 확인할 수 없습니다. ({type(error).__name__})')
        print('다음: 파일 경로·인코딩·의존성을 확인하고 다시 실행하세요.')
        return 2


if __name__ == '__main__':
    sys.exit(main())
