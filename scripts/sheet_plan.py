"""Validate a worksheet manuscript without Chrome or optional dependencies."""
import ast
from dataclasses import dataclass
from functools import lru_cache
from fractions import Fraction
import json
import math
import operator
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from sheet_common import KINDS, PLACEHOLDER, RASTER_MIMES, ROOT, SOURCE, image_path, youtube_id
from sheet_answer import UNITS, norm, num, plain


def ambiguous_answer(value):
    text = norm(value)
    return '=' in text or (num(text) is not None and any(text.endswith(unit) and len(text) > len(unit) for unit in UNITS))
from sheet_markup import MARKS, MarkupError, inspect_markup


@dataclass(frozen=True)
class Issue:
    level: str
    path: str
    message: str
    hint: str


@lru_cache(maxsize=1)
def load_registry():
    return json.loads((ROOT / 'assets/kit/blocks.json').read_text(encoding='utf-8'))


def is_number(value):
    return type(value) in (int, float) and abs(value) <= 1e100


BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.Pow: operator.pow}
UNOPS = {ast.USub: operator.neg}


def function_tree(source, params=()):
    names = {'x', *params}
    if len(source) > 240 or not re.fullmatch(r'[0-9a-z.()+*/ \t-]+', source):
        raise ValueError('x, 수, 괄호와 + - * / **만 사용할 수 있습니다.')
    try:
        tree = ast.parse(source.strip(), mode='eval').body
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ValueError('함수 식의 괄호와 연산 기호를 확인하세요.') from error

    def check(node, depth=0):
        if depth > 24:
            raise ValueError('함수 식의 중첩을 줄이세요.')
        if isinstance(node, ast.Constant) and is_number(node.value):
            return
        if isinstance(node, ast.Name) and node.id in names:
            return
        if isinstance(node, ast.UnaryOp) and type(node.op) in UNOPS:
            check(node.operand, depth + 1)
            return
        if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            check(node.left, depth + 1)
            check(node.right, depth + 1)
            return
        raise ValueError('x, 수, 괄호와 + - * / **만 사용할 수 있습니다.')

    check(tree)
    return tree


def function_value(tree, x, params=None):
    values = {'x': x, **(params or {})}
    def calculate(node):
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return float(values[node.id])
        if isinstance(node, ast.UnaryOp):
            return UNOPS[type(node.op)](calculate(node.operand))
        result = BINOPS[type(node.op)](calculate(node.left), calculate(node.right))
        if type(result) is not float or not math.isfinite(result):
            raise ValueError('유한한 실수가 아닙니다.')
        return result

    return calculate(tree)


def line_grid_points(tree, x_bounds, y_bounds, snap):
    """Find two exactly snappable points without scanning a potentially tiny snap grid."""
    def evaluate(node, x):
        if isinstance(node, ast.Constant):
            return Fraction(str(node.value))
        if isinstance(node, ast.Name):
            return Fraction(x)
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand, x)
            return value if isinstance(node.op, ast.UAdd) else -value
        left, right = evaluate(node.left, x), evaluate(node.right, x)
        if isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right.numerator) > 1000:
                raise ValueError('정확한 직선 좌표를 계산할 수 없습니다.')
            return left ** right.numerator
        return BINOPS[type(node.op)](left, right)

    base = x_bounds[0]
    y0 = evaluate(tree, base)
    slope = evaluate(tree, base + 1) - y0
    intercept = y0 - slope * Fraction(base)
    spacing = Fraction(str(snap))
    beta = intercept / spacing
    denominator = math.lcm(slope.denominator, beta.denominator)
    a, b = int(slope * denominator), int(beta * denominator)
    divisor = math.gcd(a, denominator)
    if b % divisor:
        raise ValueError('직선 위에 snap에 맞는 점이 없습니다.')
    period = denominator // divisor
    first_residue = ((-b // divisor) * pow(a // divisor, -1, period)) % period if period > 1 else 0
    low = math.ceil(Fraction(str(x_bounds[0])) / spacing)
    high = math.floor(Fraction(str(x_bounds[1])) / spacing)
    if slope:
        ylow = (Fraction(str(y_bounds[0])) / spacing - beta) / slope
        yhigh = (Fraction(str(y_bounds[1])) / spacing - beta) / slope
        low = max(low, math.ceil(min(ylow, yhigh)))
        high = min(high, math.floor(max(ylow, yhigh)))
    elif not y_bounds[0] <= float(intercept) <= y_bounds[1]:
        raise ValueError('직선이 세로 범위 밖에 있습니다.')
    k = first_residue + (-((first_residue - low) // period)) * period
    if k + period > high:
        raise ValueError('직선 위에 snap에 맞는 서로 다른 두 점이 범위 안에 없습니다.')
    last = k + ((high - k) // period) * period
    result = []
    for index in (k, last):
        x = spacing * index
        y = slope * x + intercept
        result.append([float(x), float(y)])
    return result


EXTERNAL = re.compile(r'(?i)\b[a-z][a-z0-9+.-]*://|\b(?:www\.|(?:mailto|file|data|javascript):)|(?<!\S)//[a-z0-9]')
SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'
SVG_TAGS = set('svg g defs title desc path rect circle ellipse line polyline polygon text tspan use symbol '
               'clipPath mask pattern marker linearGradient radialGradient stop'.split())
SVG_LOCAL = re.compile(r'#[A-Za-z0-9_.:-]+')
SVG_URL = re.compile(r'url\(\s*[\'\"]?(#[A-Za-z0-9_.:-]+)[\'\"]?\s*\)', re.I)


def checked_svg(source):
    """Only self-contained SVG geometry/text; rendered as a data image, not as HTML."""
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)|<\?(?!xml\s)', source, re.I):
        raise ValueError('SVG의 문서형·엔터티·외부 스타일 참조를 없애세요.')
    try:
        root = ET.fromstring(source)
    except (ET.ParseError, UnicodeError) as error:
        raise ValueError('SVG의 태그와 속성 따옴표를 확인하세요.') from error
    if root.tag not in ('svg', f'{{{SVG_NS}}}svg'):
        raise ValueError('SVG는 <svg>로 시작해야 합니다.')
    for element in root.iter():
        if element.tag.removeprefix(f'{{{SVG_NS}}}') not in SVG_TAGS:
            raise ValueError('SVG에는 도형·글자·내부 정의만 넣으세요. 외부 그림이나 스크립트는 쓸 수 없습니다.')
        for attr, value in element.attrib.items():
            name = attr.rsplit('}', 1)[-1].lower()
            if (name.startswith('on') or EXTERNAL.search(value) or
                    (name in ('href', 'src') and not SVG_LOCAL.fullmatch(value)) or
                    (name == 'style' and re.search(r'[@\\]|/\*', value)) or
                    re.search(r'url\s*\(', SVG_URL.sub('', value), re.I)):
                raise ValueError('SVG의 외부 URL, 외부 파일 및 실행 속성을 지우세요.')
    # Keep the author's element names and namespace declarations as written.
    if root.tag == 'svg':
        source = re.sub(r'<svg(?=[\s/>])', f'<svg xmlns="{SVG_NS}"', source, count=1)
    return source


class Validator:
    def __init__(self, folder):
        self.folder = folder
        self.issues = []
        self.blank_index = 0
        self.points = []
        self.ids = set()
        self.numbered = {}
        self.limits = {}
        self.local_marks = set()

    def error(self, path, message, hint):
        self.issues.append(Issue('error', path, message, hint))

    def warn(self, path, message, hint):
        self.issues.append(Issue('warning', path, message, hint))

    def object(self, value, path, allowed):
        if not isinstance(value, dict):
            self.error(path, '객체가 필요합니다.', '{…} 안에 이름과 값을 쓰세요.')
            return False
        for key in value.keys() - set(allowed):
            self.error(f'{path}.{key}' if path != '$' else key, '알 수 없는 필드입니다.', '계약과 blocks의 필드 이름을 확인하세요.')
        return True

    def list(self, value, path, minimum=1, maximum=None):
        if isinstance(value, list) and len(value) >= minimum and (maximum is None or len(value) <= maximum):
            return value
        size = f'{minimum}~{maximum}개' if maximum is not None else f'{minimum}개 이상'
        self.error(path, '목록의 형식 또는 길이가 맞지 않습니다.', f'항목 {size}를 배열 […]에 넣으세요.')
        return None

    def integer(self, value, path, lo, hi):
        if type(value) is int and lo <= value <= hi:
            return True
        self.error(path, '정수 범위가 맞지 않습니다.', f'{lo}~{hi} 사이의 정수를 쓰세요.')
        return False

    def positive(self, value, path):
        if is_number(value) and value > 0:
            return True
        self.error(path, '양수가 필요합니다.', '0보다 큰 수를 쓰세요.')
        return False

    def bools(self, value, path, *names):
        for key in names:
            if key in value and type(value[key]) is not bool:
                self.error(f'{path}.{key}', '참·거짓 값이 필요합니다.', 'true 또는 false를 쓰세요.')

    def text(self, value, path, *, empty=False, private=False, local=False, field=None):
        if not isinstance(value, str) or (not empty and not value.strip()):
            self.error(path, '글자 내용이 필요합니다.', '비어 있지 않은 문자열을 쓰세요.')
            return None
        if PLACEHOLDER in value:
            self.error(path, '틀의 자리표시가 남았습니다.', '〔작성〕을 지우고 실제 내용을 쓰세요.')
        if any(ord(c) < 32 and c not in '\t\n\r\v\f' or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            self.error(path, '그대로 저장할 수 없는 문자가 들어 있습니다.', '제어 문자와 잘못된 유니코드 문자를 지우세요.')
        if '__' in value:
            self.error(path, '밑줄 문자로 빈칸을 만들었습니다.', '예: {{㉠|정답}} 또는 {{|정답|30}}로 바꾸세요.')
        if EXTERNAL.search(value):
            self.error(path, '외부 URL을 쓸 수 없습니다.', '외부 주소를 빼고 그림은 로컬 파일로 넣으세요.')
        try:
            parsed = inspect_markup(value)
        except MarkupError as error:
            self.error(path, str(error), '$…$, **…**, {{…}}의 짝과 수식 명령을 확인하세요.')
            return None
        if not empty and not parsed.plain.strip():
            self.error(path, '표기 안에 실제 내용이 없습니다.', '화면에 보일 글자나 수식을 쓰세요.')
        limit = self.limits.get(field)
        if limit is not None and len(parsed.plain) > limit:
            self.warn(path, f'글자 수 권장 한도({limit}자)를 넘었습니다.', '문장을 줄이거나 여러 문단으로 나누세요.')
        if '□' in parsed.outside_math:
            self.warn(path, '점검 줄 기호(□)가 있습니다.', '학생이 해야 할 행동을 문장으로 쓰세요.')
        if re.search(r'(?<![A-Za-z0-9])[A-Z]+(?![A-Za-z0-9])', parsed.outside_math):
            self.warn(path, '수식 밖에 영문 대문자 낱말이 있습니다.', '점은 $A$, 평면은 $\\mathit{A}$처럼 쓰세요.')
        for blank in parsed.blanks:
            if not blank.answer:
                self.error(path, '빈칸에 교사용 정답이 없습니다.', '예: {{㉠|정답}}처럼 정답을 써 넣으세요.')
            if num(plain(blank.source)) is not None:
                for alternate in blank.alternatives:
                    if num(plain(alternate)) is None:
                        self.error(path, '수로 판별할 수 없는 빈칸 대안입니다.', '수나 분수로 쓴 대안을 사용하세요.')
            if ambiguous_answer(blank.answer) and not blank.alternatives:
                self.warn(path, '학생 입력과 정답 표기가 다를 수 있습니다.', '다른 표현을 허용하려면 ;; 뒤에 대안을 추가하세요.')
            if blank.mark and not private:
                expected = MARKS[self.blank_index:self.blank_index + 1]
                if blank.mark != expected:
                    self.error(path, '빈칸 기호가 문서 순서와 맞지 않습니다.',
                               f'다음 기호는 {expected}입니다.' if expected else '기호 없이 {{|정답}}을 쓰세요.')
                self.blank_index += 1
                if local:
                    self.local_marks.add(blank.mark)
        return parsed

    def texts(self, value, path, *, minimum=1, maximum=None, **kwargs):
        values = self.list(value, path, minimum, maximum)
        if values is not None:
            for i, cell in enumerate(values):
                self.text(cell, f'{path}[{i}]', **kwargs)
        return values

    def matrix(self, value, path, columns, rows=None, **kwargs):
        values = self.list(value, path, rows or 1, rows)
        if values is not None:
            for i, row in enumerate(values):
                self.texts(row, f'{path}[{i}]', minimum=columns or 1, maximum=columns, **kwargs)

    def figure(self, value, path, *, outer=False, caption_required=False, private=False, field=''):
        keys = ('src', 'svg', 'width_mm', 'alt', 'caption', 'teacher_only') + (('type', 'pen') if outer else ())
        if not self.object(value, path, keys):
            return
        self.positive(value.get('width_mm'), path + '.width_mm')
        self.bools(value, path, 'teacher_only', 'pen')
        private = private or value.get('teacher_only') is True
        if ('src' in value) == ('svg' in value):
            self.error(path, '그림의 출처가 하나여야 합니다.', 'src 또는 svg 중 하나만 쓰세요.')
        for key in ('src', 'svg'):
            if key not in value:
                continue
            raw = value[key]
            where = f'{path}.{key}'
            if not isinstance(raw, str) or not raw.strip() or PLACEHOLDER in raw:
                self.error(where, '실제 그림 출처가 필요합니다.', '자리표시를 바꾸고 로컬 파일 또는 SVG를 넣으세요.')
                continue
            try:
                if key == 'svg':
                    checked_svg(raw)
                else:
                    file = image_path(self.folder, raw)
                    ext = file.suffix.lower()
                    if ext not in RASTER_MIMES and ext != '.svg':
                        raise ValueError('PNG, JPEG, WebP, GIF, BMP 또는 SVG 파일을 쓰세요.')
                    if not file.is_file():
                        raise ValueError('그림 파일이 없습니다. 그림/ 아래의 파일 이름을 확인하세요.')
                    if ext == '.svg':
                        checked_svg(file.read_text(encoding='utf-8-sig'))
            except (ValueError, OSError, RuntimeError) as error:
                message = str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else '그림 파일을 읽지 못했습니다.'
                self.error(where, message, '활동지 폴더 안의 읽을 수 있는 그림을 지정하세요.')
        if 'alt' in value or 'src' in value:
            parsed = self.text(value.get('alt'), path + '.alt', private=True, field=field + 'alt')
            if parsed and parsed.blanks:
                self.error(path + '.alt', '대체 설명에는 빈칸을 그릴 수 없습니다.', '정답이 없는 일반 설명으로 바꾸세요.')
        if 'caption' in value or caption_required:
            self.text(value.get('caption'), path + '.caption', private=private, field=field + 'caption')

    def lines(self, obj, path, required=False):
        if required or 'lines' in obj:
            self.integer(obj.get('lines'), path + '.lines', 2, 30)

    def answer_rows(self, block, path, pending_marks):
        rows = self.list(block.get('rows'), path + '.rows') or []
        allowed = {key.split('.', 1)[1] for key in load_registry()['answer']['fields'] if key.startswith('rows[].')}
        previous = None
        for i, row in enumerate(rows):
            where = f'{path}.rows[{i}]'
            if not self.object(row, where, allowed):
                previous = None
                continue
            group = row.get('group')
            if 'group' in row and (not isinstance(group, str) or not group.strip()):
                self.error(where + '.group', '병합 이름이 필요합니다.', '같은 문자열을 연속된 행에 쓰세요.')
            same = bool(group) and group == previous
            previous = group
            if not same or 'label' in row:
                self.text(row.get('label'), where + '.label', field='rows[].label', private=same)
            if 'points' in row:
                points = row['points']
                if not is_number(points) or points < 0:
                    self.error(where + '.points', '배점은 0 이상의 수여야 합니다.', '라벨 칸의 배점을 숫자로 쓰세요.')
                elif not same:
                    self.points.append(points)
            if 'guide' in row:
                self.text(row['guide'], where + '.guide', field='rows[].guide', private=same)
            if 'mark' in row:
                mark = row['mark']
                if not isinstance(mark, str) or len(mark) != 1 or mark not in MARKS:
                    self.error(where + '.mark', '빈칸 기호가 아닙니다.', '㉠과 같은 기호 한 개를 쓰세요.')
                else:
                    pending_marks.append((where + '.mark', mark))
            self.lines(row, where)
            self.bools(row, where, 'grow')
            check = row.get('check', 'auto')
            if check not in ('auto', 'number', 'text', 'off'):
                self.error(where + '.check', '판별 종류를 알 수 없습니다.', 'auto, number, text, off 중 하나를 쓰세요.')
            if 'accept' in row:
                self.texts(row['accept'], where + '.accept', private=True)
            if isinstance(row.get('answer'), str):
                source = row['answer']
                value = plain(source)
                if (row.get('grow') is True and row.get('kind', 'text') == 'text' and
                        source.splitlines() == [source] and 0 < len(value.strip()) <= 20):
                    self.warn(where + '.grow', '짧은 답 칸이 쪽 채움으로 커집니다.', 'grow를 빼고 lines로 정하세요.')
                if check == 'number' and num(value) is None:
                    self.error(where + '.answer', '수로 판별할 수 없는 정답입니다.', '숫자나 분수로 쓰세요.')
                if check in ('number', 'auto') and num(value) is not None and isinstance(row.get('accept', []), list):
                    for j, alternate in enumerate(row.get('accept', [])):
                        if isinstance(alternate, str) and num(plain(alternate)) is None:
                            self.error(f'{where}.accept[{j}]', '수로 판별할 수 없는 대안입니다.', '같은 종류의 수나 분수를 쓰세요.')
                if check != 'off' and ambiguous_answer(value):
                    self.warn(where + '.answer', '학생 입력과 정답 표기가 다를 수 있습니다.', '다른 표현을 허용하려면 accept 추가를 검토하세요.')
            kind = row.get('kind', 'text')
            if kind not in ('text', 'draw'):
                self.error(where + '.kind', '답 칸 종류를 알 수 없습니다.', 'text 또는 draw를 쓰세요.')
            answer = row.get('answer')
            if kind == 'draw' or 'height_mm' in row:
                self.positive(row.get('height_mm'), where + '.height_mm')
            if kind == 'draw' and isinstance(answer, dict):
                self.figure(answer, where + '.answer', private=True)
            else:
                self.text(answer, where + '.answer', private=True, field='rows[].answer')

    def grid(self, block, path):
        mode = block.get('mode')
        self.positive(block.get('unit_mm'), path + '.unit_mm')
        self.bools(block, path, 'labels', 'pen')
        if mode == 'square':
            for key in ('cols', 'rows'):
                self.integer(block.get(key), f'{path}.{key}', 1, 200)
        elif mode == 'coord':
            for key in ('x', 'y'):
                bounds = self.list(block.get(key), f'{path}.{key}', 2, 2)
                if bounds and (not all(type(v) is int and abs(v) <= 10000 for v in bounds) or
                               not 0 < bounds[1] - bounds[0] <= 200):
                    self.error(f'{path}.{key}', '축 범위가 맞지 않습니다.', '작은 정수부터 큰 정수 순으로 쓰세요(최대 200칸).')
        else:
            self.error(path + '.mode', '모눈 종류를 알 수 없습니다.', 'square 또는 coord를 쓰세요.')
        draws = self.list(block.get('draw', []), path + '.draw', 0) or []
        if mode == 'square' and draws:
            self.error(path + '.draw', '좌표 자료는 좌표평면에만 그릴 수 있습니다.', 'mode를 coord로 바꾸고 x, y 범위를 쓰세요.')
        trees = {}
        for i, entry in enumerate(draws):
            where = f'{path}.draw[{i}]'
            if not self.object(entry, where, ('fn', 'points', 'on', 'names', 'teacher')):
                continue
            self.bools(entry, where, 'teacher')
            if ('fn' in entry) == ('points' in entry):
                self.error(where, '그릴 대상이 하나여야 합니다.', 'fn 또는 points 중 하나만 쓰세요.')
            if 'fn' in entry:
                try:
                    if not isinstance(entry['fn'], str):
                        raise ValueError('함수 식은 문자열로 써야 합니다.')
                    trees[i] = function_tree(entry['fn'])
                except ValueError as error:
                    self.error(where + '.fn', str(error), '예: "2*x+1"처럼 식을 쓰세요.')
                if 'on' in entry or 'names' in entry:
                    self.error(where, 'on과 names는 점에만 씁니다.', 'points가 있는 항목으로 옮기세요.')
        for i, entry in enumerate(draws):
            if not isinstance(entry, dict) or 'points' not in entry:
                continue
            where = f'{path}.draw[{i}]'
            on = entry.get('on')
            tree = trees.get(on) if type(on) is int else None
            if 'on' in entry and tree is None:
                self.error(where + '.on', '검산할 함수 식이 없습니다.', 'draw 배열에서 fn을 쓴 위치(0부터)를 넣으세요.')
            points = self.list(entry['points'], where + '.points')
            if 'names' in entry:
                n = len(points) if points else 1
                names = self.texts(entry['names'], where + '.names', minimum=n, maximum=n,
                                   private=True, field='draw[].names[]') or []
                for j, name in enumerate(names):
                    if isinstance(name, str) and '{{' in name:
                        self.error(f'{where}.names[{j}]', '점 이름에는 빈칸을 그릴 수 없습니다.', '빈칸은 발문이나 표로 옮기세요.')
            for j, pair in enumerate(points or []):
                point_path = f'{where}.points[{j}]'
                if not (isinstance(pair, list) and len(pair) == 2 and all(is_number(v) for v in pair)):
                    self.error(point_path, '좌표는 수 두 개여야 합니다.', '예: [1, 3]처럼 쓰세요.')
                elif tree is not None:
                    try:
                        matches = abs(function_value(tree, pair[0]) - pair[1]) <= 1e-9
                    except (ValueError, ArithmeticError):
                        matches = False
                    if not matches:
                        self.error(point_path, '점이 지정한 그래프 위에 있지 않습니다.', 'x를 식에 대입해 y값과 on을 확인하세요.')

    def video(self, value, path, solution=False):
        allowed = ('url', 'start') if solution else ('type', 'url', 'src', 'title', 'start', 'caption')
        if not self.object(value, path, allowed):
            return
        if solution or 'url' in value:
            if not youtube_id(value.get('url')):
                self.error(path + '.url', 'YouTube 영상 주소가 아닙니다.', '유효한 YouTube 영상 주소를 쓰세요.')
        if not solution:
            if ('url' in value) == ('src' in value):
                self.error(path, '영상 출처가 하나여야 합니다.', 'url 또는 src 중 하나를 쓰세요.')
            if 'src' in value:
                try:
                    file = image_path(self.folder, value['src'])
                    if file.suffix.lower() != '.mp4' or not file.is_file() or file.stat().st_size > 20 * 1024 * 1024:
                        raise ValueError('20MB 이하의 로컬 mp4 파일이 필요합니다.')
                except (TypeError, ValueError, OSError, RuntimeError) as error:
                    self.error(path + '.src', str(error), '활동지 폴더 안의 mp4를 확인하세요.')
            self.text(value.get('title'), path + '.title')
            if 'caption' in value:
                self.text(value['caption'], path + '.caption')
        if 'start' in value:
            self.integer(value['start'], path + '.start', 0, 86400)

    def bounds(self, value, path):
        values = self.list(value, path, 2, 2)
        if values and (not all(is_number(v) for v in values) or values[0] >= values[1]):
            self.error(path, '범위가 맞지 않습니다.', '작은 수와 큰 수를 차례로 쓰세요.')
            return None
        return values

    def plot(self, plot, path, bounds, mode, count=None):
        if not self.object(plot, path, ('mode', 'answer', 'snap')):
            return
        snap = plot.get('snap', 1)
        if not self.positive(snap, path + '.snap'):
            return
        bounds = [extent if isinstance(extent, (list, tuple)) and len(extent) == 2 and
                  all(is_number(v) for v in extent) and extent[0] < extent[1] else None for extent in bounds]
        answer = plot.get('answer')
        if mode == 'grid':
            mode = plot.get('mode')
            if mode not in ('points', 'line'):
                self.error(path + '.mode', '점 또는 직선 모드가 필요합니다.', 'points 또는 line을 쓰세요.')
                return
        if mode == 'line' and isinstance(answer, str):
            try:
                tree = function_tree(answer)
                # Evaluate at three consecutive points, including the requested domain.
                a = bounds[0][0] if bounds and bounds[0] else 0
                vals = [function_value(tree, a + i) for i in range(4)]
                if any(abs(vals[i] - 2 * vals[i+1] + vals[i+2]) > 1e-9 * max(1, *map(abs, vals)) for i in (0, 1)):
                    raise ValueError('일차식이 아닙니다.')
                if abs(vals[1] - vals[0]) < 1e-12:
                    raise ValueError('일차식이 아닙니다.')
                if len(bounds) == 2 and all(bounds):
                    line_grid_points(tree, bounds[0], bounds[1], snap)
            except (ValueError, ArithmeticError, OverflowError) as error:
                self.error(path + '.answer', str(error), '일차식 또는 서로 다른 두 점을 쓰세요.')
            return
        values = self.list(answer, path + '.answer', 2 if mode == 'line' else 1, 2 if mode == 'line' else count)
        if values is None:
            return
        if count is not None and len(values) != count:
            self.error(path + '.answer', '자료와 정답 길이가 다릅니다.', '자료마다 정답을 하나씩 쓰세요.')
        if mode == 'line' and len(values) == 2 and values[0] == values[1]:
            self.error(path + '.answer', '같은 두 점으로 직선을 정할 수 없습니다.', '서로 다른 두 점을 쓰세요.')
        for i, value in enumerate(values):
            coords = value if mode in ('points', 'line') and len(bounds) == 2 else [value]
            if not isinstance(coords, list) or len(coords) != len(bounds) or not all(is_number(v) for v in coords):
                self.error(f'{path}.answer[{i}]', '정답 점 또는 값의 형식이 맞지 않습니다.', '범위에 맞는 수를 쓰세요.')
                continue
            for coordinate, extent in zip(coords, bounds):
                if extent and not extent[0] <= coordinate <= extent[1]:
                    self.error(f'{path}.answer[{i}]', '정답이 범위를 벗어났습니다.', '축 범위 안의 값을 쓰세요.')
                quotient = coordinate / snap
                if not math.isfinite(quotient) or abs(quotient - round(quotient)) > 1e-8:
                    self.error(f'{path}.answer[{i}]', '정답이 snap의 배수가 아닙니다.', 'snap에 맞는 값을 쓰세요.')

    def chart(self, block, path):
        kind = block.get('kind')
        if kind not in ('bar', 'line', 'histogram', 'polygon', 'scatter'):
            self.error(path + '.kind', '그래프 종류를 알 수 없습니다.', 'bar, line, histogram, polygon, scatter 중 하나를 쓰세요.')
            return
        self.bools(block, path, 'teacher')
        for name in ('width_mm', 'height_mm', 'y_step', 'x_step'):
            if name in block:
                self.positive(block[name], path + '.' + name)
        if 'y_max' in block and not is_number(block['y_max']):
            self.error(path + '.y_max', '유한한 수가 필요합니다.', '세로축 최댓값을 숫자로 쓰세요.')
        for name in ('x_label', 'y_label'):
            if name in block:
                self.text(block[name], path + '.' + name, field=name)
        if kind == 'scatter':
            x = self.bounds(block.get('x'), path + '.x')
            y = self.bounds(block.get('y'), path + '.y')
            values = self.list(block.get('points'), path + '.points') or []
            for i, point in enumerate(values):
                if not isinstance(point, list) or len(point) != 2 or not all(is_number(v) for v in point):
                    self.error(f'{path}.points[{i}]', '좌표 형식이 맞지 않습니다.', '[x,y]로 쓰세요.')
                elif x and y and not (x[0] <= point[0] <= x[1] and y[0] <= point[1] <= y[1]):
                    self.error(f'{path}.points[{i}]', '점이 축 범위를 벗어났습니다.', '축 범위 안의 좌표를 쓰세요.')
        else:
            values = self.list(block.get('values'), path + '.values') or []
            for i, value in enumerate(values):
                if not is_number(value):
                    self.error(f'{path}.values[{i}]', '유한한 수가 필요합니다.', '자료 값을 확인하세요.')
                elif is_number(block.get('y_max')) and value > block['y_max']:
                    self.error(f'{path}.values[{i}]', '자료가 세로축 범위를 벗어났습니다.', 'y_max를 자료 이상으로 쓰세요.')
            if kind in ('bar', 'line'):
                labels = self.texts(block.get('labels'), path + '.labels', field='labels[]') or []
                if len(labels) != len(values):
                    self.error(path + '.labels', '항목과 값의 길이가 다릅니다.', '자료마다 항목과 값을 하나씩 쓰세요.')
            else:
                bins = self.list(block.get('bins'), path + '.bins', 2) or []
                if len(bins) != len(values) + 1 or not all(is_number(v) for v in bins) or any(a >= b for a, b in zip(bins, bins[1:])):
                    self.error(path + '.bins', '계급 경계가 맞지 않습니다.', '증가하는 경계 n+1개와 값 n개를 쓰세요.')
        if 'plot' in block:
            if kind not in ('bar', 'histogram'):
                self.error(path + '.plot', '이 그래프에는 막대를 그릴 수 없습니다.', 'bar 또는 histogram을 쓰세요.')
            else:
                answers = block['plot'].get('answer', []) if isinstance(block['plot'], dict) else []
                numbers = [v for v in values + (answers if isinstance(answers, list) else []) if is_number(v)]
                ymin = min([0, *numbers])
                ymax = block.get('y_max', max([1, *numbers]))
                if is_number(ymax) and ymin >= ymax:
                    ymin = ymax - 1
                if ymin < 0:
                    self.warn(path + '.plot', '음수 막대는 현재 화면 엔진의 막대 기준선과 일치하지 않을 수 있습니다.',
                              '엔진 수정 전에는 음수가 아닌 자료를 사용하거나 화면을 별도로 확인하세요.')
                self.plot(block['plot'], path + '.plot', [(ymin, ymax)], 'bars', len(values))

    def block(self, block, path, item_id, pending_marks, inside_split=False):
        registry = load_registry()
        kind = block.get('type') if isinstance(block, dict) else None
        if not isinstance(kind, str) or kind not in registry:
            self.error(path + '.type', '등록되지 않은 블록입니다.', 'blocks 명령으로 종류를 확인하세요. 머리표는 meta에서 자동 생성됩니다.')
            return
        entry = registry[kind]
        self.limits = entry['limits']
        allowed = {'type'} | {key for key in entry['fields'] if '.' not in key}
        self.object(block, path, allowed)

        def text(key, required=True, **kwargs):
            if key in block or required:
                return self.text(block.get(key), f'{path}.{key}', field=key, **kwargs)
            return None

        if kind in ('notice', 'situation'):
            text('label' if kind == 'notice' else 'title', kind == 'notice', local=kind == 'situation')
            self.texts(block.get('lines'), path + '.lines', field='lines[]', local=kind == 'situation')
            text('note', False, local=True)
            if 'figure' in block:
                self.figure(block['figure'], path + '.figure')
        elif kind == 'nameband':
            text('label')
        elif kind in ('question', 'ask', 'text'):
            if kind == 'ask':
                text('tag')
            parsed = text('text', local=kind in ('question', 'ask'))
            if kind in ('question', 'ask') and parsed and re.match(r'^\s*[0-9]+[.)](?![0-9])', parsed.plain):
                self.warn(path + '.text', '발문 첫머리가 문항 번호처럼 보입니다.', '직접 쓴 번호를 빼고 발문만 남기세요.')
            if kind == 'question':
                self.bools(block, path, 'number')
                if block.get('number', True) is True:
                    self.numbered.setdefault(item_id, path + '.text')
            if kind == 'ask':
                self.lines(block, path, required=True)
                self.bools(block, path, 'grow')
                text('answer', private=True)
        elif kind == 'answer':
            self.answer_rows(block, path, pending_marks)
        elif kind == 'steps':
            cols = self.texts(block.get('columns'), path + '.columns', field='columns[]')
            count = block.get('rows')
            valid = self.integer(count, path + '.rows', 1, 20)
            self.lines(block, path)
            self.matrix(block.get('answers'), path + '.answers', len(cols) if cols else None,
                        count if valid else None, field='answers[][]', private=True)
        elif kind == 'choices':
            opts = self.texts(block.get('options'), path + '.options', minimum=2, maximum=20, field='options[]')
            limit = len(opts) if opts else 20
            answer = block.get('answer')
            if isinstance(answer, list):
                answers = self.list(answer, path + '.answer') or []
                seen = set()
                for i, choice in enumerate(answers):
                    slot = f'{path}.answer[{i}]'
                    if self.integer(choice, slot, 1, limit):
                        if choice in seen:
                            self.error(slot, '같은 정답 번호가 겹칩니다.', '각 정답 번호를 한 번만 쓰세요.')
                        seen.add(choice)
            else:
                self.integer(answer, path + '.answer', 1, limit)
            if 'cols' in block:
                self.integer(block['cols'], path + '.cols', 1, limit)
        elif kind == 'table':
            header = self.texts(block.get('header'), path + '.header', field='header[]')
            self.matrix(block.get('rows'), path + '.rows', len(header) if header else None, empty=True, field='rows[][]')
            if 'widths_mm' in block:
                n = len(header) if header else None
                widths = self.list(block['widths_mm'], path + '.widths_mm', n or 1, n)
                for i, width in enumerate(widths or []):
                    self.positive(width, f'{path}.widths_mm[{i}]')
        elif kind == 'figure':
            self.figure(block, path, outer=True)
        elif kind == 'figures':
            figures = self.list(block.get('items'), path + '.items', 2, 2) or []
            for i, figure in enumerate(figures):
                self.figure(figure, f'{path}.items[{i}]', caption_required=True, field='items[].')
        elif kind == 'grid':
            self.grid(block, path)
            if 'plot' in block:
                if block.get('mode') != 'coord':
                    self.error(path + '.plot', '좌표평면에서만 점을 찍을 수 있습니다.', 'mode를 coord로 쓰세요.')
                else:
                    self.plot(block['plot'], path + '.plot', [block.get('x'), block.get('y')], 'grid')
        elif kind == 'heading':
            text('tag')
            text('text')
            if 'icon' in block and block['icon'] not in ('pencil', 'bulb', 'search', 'check', 'video', 'chart', 'puzzle', 'star', 'warn', 'book', 'calc', 'flag'):
                self.error(path + '.icon', '지원하지 않는 아이콘입니다.', '계약의 아이콘 이름을 쓰세요.')
            if 'image' in block:
                self.figure(block['image'], path + '.image')
                if isinstance(block['image'], dict) and is_number(block['image'].get('width_mm')) and block['image']['width_mm'] > 30:
                    self.error(path + '.image.width_mm', '제목 그림이 너무 넓습니다.', '30mm 이하로 쓰세요.')
        elif kind == 'callout':
            if block.get('kind') not in ('개념', '생각', '주의', '정리', '예시'):
                self.error(path + '.kind', '상자 종류가 맞지 않습니다.', '개념·생각·주의·정리·예시 중 하나를 쓰세요.')
            text('title', False)
            self.texts(block.get('lines'), path + '.lines', field='lines[]')
            if 'image' in block:
                self.figure(block['image'], path + '.image')
        elif kind == 'split':
            if inside_split or self.columns == 2:
                self.error(path, '2단 안에 split을 겹칠 수 없습니다.', '한 겹의 단만 쓰세요.')
            if block.get('ratio', '1:1') not in ('1:1', '2:1', '1:2', '3:2', '2:3'):
                self.error(path + '.ratio', '지원하지 않는 비율입니다.', '1:1, 2:1, 1:2, 3:2, 2:3 중 하나를 쓰세요.')
            for side in ('left', 'right'):
                for i, child in enumerate(self.list(block.get(side), path + '.' + side) or []):
                    self.block(child, f'{path}.{side}[{i}]', item_id, pending_marks, True)
        elif kind == 'ox':
            for i, entry in enumerate(self.list(block.get('items'), path + '.items', 1, 10) or []):
                where = f'{path}.items[{i}]'
                if self.object(entry, where, ('text', 'answer')):
                    self.text(entry.get('text'), where + '.text', field='items[].text')
                    if entry.get('answer') not in ('O', 'X'):
                        self.error(where + '.answer', '정답은 O 또는 X여야 합니다.', '대문자 O 또는 X를 쓰세요.')
        elif kind in ('match', 'order'):
            text('prompt', False)
            if kind == 'match':
                left = self.texts(block.get('left'), path + '.left', minimum=2, maximum=8, field='left[]') or []
                self.texts(block.get('right'), path + '.right', minimum=len(left) or 2, maximum=len(left) or 8, field='right[]')
            else:
                self.texts(block.get('items'), path + '.items', minimum=2, maximum=8, field='items[]')
        elif kind == 'flow':
            nodes = self.texts(block.get('nodes'), path + '.nodes', minimum=2, maximum=8, field='nodes[]') or []
            if 'arrows' in block:
                n = max(0, len(nodes) - 1)
                self.texts(block['arrows'], path + '.arrows', minimum=n, maximum=n, empty=True, field='arrows[]')
        elif kind == 'numberline':
            bounds = self.bounds(block.get('range'), path + '.range')
            step = block.get('step')
            if self.positive(step, path + '.step') and bounds and (bounds[1] - bounds[0]) / step > 200:
                self.error(path + '.step', '눈금이 너무 많습니다.', '200칸 이내로 조정하세요.')
            self.bools(block, path, 'labels', 'pen')
            if 'width_mm' in block:
                self.positive(block['width_mm'], path + '.width_mm')
            for i, point in enumerate(self.list(block.get('points', []), path + '.points', 0) or []):
                where = f'{path}.points[{i}]'
                if self.object(point, where, ('x', 'name')):
                    x = point.get('x')
                    if not is_number(x) or (bounds and not bounds[0] <= x <= bounds[1]):
                        self.error(where + '.x', '점이 범위 안에 있지 않습니다.', '수직선 범위 안의 수를 쓰세요.')
                    if 'name' in point:
                        self.text(point['name'], where + '.name', field='points[].name')
            if 'plot' in block:
                self.plot(block['plot'], path + '.plot', [bounds], 'points')
        elif kind == 'chart':
            self.chart(block, path)
        elif kind == 'explore':
            text('prompt', False)
            params = self.list(block.get('params'), path + '.params', 1, 3) or []
            names, ranges = set(), {}
            for i, param in enumerate(params):
                where = f'{path}.params[{i}]'
                if not self.object(param, where, ('name', 'label', 'min', 'max', 'step', 'value', 'except')):
                    continue
                name = param.get('name')
                valid_name = isinstance(name, str) and re.fullmatch('[a-wyz]', name) and name not in names
                if valid_name:
                    names.add(name)
                else:
                    self.error(where + '.name', '매개변수 이름이 맞지 않습니다.', 'x를 제외한 서로 다른 영문 소문자 한 글자를 쓰세요.')
                if 'label' in param:
                    parsed = self.text(param['label'], where + '.label', private=True)
                    if parsed and parsed.blanks:
                        self.error(where + '.label', '슬라이더 이름에는 빈칸을 넣을 수 없습니다.', '$a$의 값처럼 글과 수식으로 쓰세요.')
                low, high, step, value = (param.get(k) for k in ('min', 'max', 'step', 'value'))
                bounded = is_number(low) and is_number(high) and low < high
                if not (bounded and is_number(step) and step > 0 and is_number(value) and low <= value <= high):
                    self.error(where, '매개변수 범위가 맞지 않습니다.', 'min < max, 양수 step, 범위 안의 value를 쓰세요.')
                excluded = set()
                for j, v in enumerate(self.list(param.get('except', []), where + '.except', 0) or []):
                    if not is_number(v) or (bounded and not low <= v <= high):
                        self.error(f'{where}.except[{j}]', '건너뛸 값이 범위에 맞지 않습니다.', 'min~max 사이의 수를 쓰세요.')
                    else:
                        excluded.add(v)
                if is_number(value) and value in excluded:
                    self.error(where + '.value', '처음 값은 건너뛸 값과 같을 수 없습니다.', 'except에 없는 값을 쓰세요.')
                if valid_name and bounded:
                    ranges[name] = (low, high, excluded)
            if 'print' in block:
                for i, snapshot in enumerate(self.list(block['print'], path + '.print', 1, 4) or []):
                    where = f'{path}.print[{i}]'
                    if not self.object(snapshot, where, names):
                        continue
                    if not snapshot:
                        self.error(where, '인쇄할 값이 필요합니다.', '매개변수 이름과 값을 한 쌍 이상 쓰세요.')
                    for name, value in snapshot.items():
                        if name in ranges:
                            low, high, excluded = ranges[name]
                            if not is_number(value) or not low <= value <= high or value in excluded:
                                self.error(f'{where}.{name}', '인쇄할 값이 허용 범위에 없습니다.', 'min~max 사이에서 except에 없는 수를 쓰세요.')
            try:
                if not isinstance(block.get('fn'), str):
                    raise ValueError('함수 식이 필요합니다.')
                function_tree(block['fn'], names)
            except ValueError as error:
                self.error(path + '.fn', str(error), 'x와 선언한 매개변수로 식을 쓰세요.')
            self.grid({'mode': 'coord', 'x': block.get('x'), 'y': block.get('y'), 'unit_mm': block.get('unit_mm')}, path)
            if 'table' in block:
                for i, x in enumerate(self.list(block['table'], path + '.table', 0) or []):
                    if not is_number(x):
                        self.error(f'{path}.table[{i}]', '수만 넣을 수 있습니다.', '표에 넣을 x값을 쓰세요.')
        elif kind == 'video':
            self.video(block, path)
        elif kind == 'checklist':
            self.bools(block, path, 'print')
            if self.kind == '평가':
                self.error(path, '평가에는 자기 점검표를 쓸 수 없습니다.', '점검표를 빼거나 활동지 종류를 바꾸세요.')
            text('title', False)
            self.texts(block.get('items'), path + '.items', field='items[]')

    def meta(self, meta):
        if not self.object(meta, 'meta', ('title', 'kind', 'columns', 'subject', 'grade', 'term', 'unit',
                                          'header_title', 'name_fields', 'pair', 'total_points', 'goal',
                                          'interactive', 'reveal', 'theme', 'accent', 'sync', 'sheet_id')):
            return
        kind = meta.get('kind', '수업')
        if kind not in KINDS:
            self.error('meta.kind', '활동지 유형을 알 수 없습니다.', '수업, 연습, 평가 중 하나를 쓰세요.')
        columns = meta.get('columns', 1)
        self.integer(columns, 'meta.columns', 1, 2)
        self.bools(meta, 'meta', 'pair', 'interactive', 'sync')
        if 'sheet_id' in meta and (not isinstance(meta['sheet_id'], str) or
                                   not re.fullmatch(r'[a-z0-9-]{1,32}', meta['sheet_id'])):
            self.error('meta.sheet_id', '활동지 id 형식이 맞지 않습니다.', '소문자·숫자·붙임표를 1~32자로 쓰세요.')
        for key, allowed in (('reveal', ('시도 후', '바로', '교사만', '없음')),
                             ('theme', ('정본', '산뜻')), ('accent', ('하늘', '민트', '주황', '보라'))):
            if key in meta and meta[key] not in allowed:
                self.error('meta.' + key, '지원하지 않는 설정입니다.', '허용된 값을 사용하세요.')
        total = meta.get('total_points')
        if total is not None:
            if not is_number(total) or total < 0:
                self.error('meta.total_points', '총점은 0 이상의 수여야 합니다.', '숫자를 쓰거나 null로 두세요.')
            elif abs(math.fsum(self.points) - total) > 1e-9:
                self.error('meta.total_points', f'라벨 배점의 합({math.fsum(self.points):g}점)과 총점이 다릅니다.',
                           '연속 병합한 라벨은 첫 행 배점만 더해 총점을 맞추세요.')

    def teacher(self, teacher):
        self.limits = {}
        solutions = set()
        if self.object(teacher, 'teacher', ('memo', 'solutions')):
            self.texts(teacher.get('memo', []), 'teacher.memo', minimum=0, private=True)
            entries = self.list(teacher.get('solutions', []), 'teacher.solutions', 0) or []
            for i, entry in enumerate(entries):
                path = f'teacher.solutions[{i}]'
                groups = ('steps', 'rubric', 'alternatives', 'misconceptions')
                if not self.object(entry, path, ('item', 'answer', 'video', *groups)):
                    continue
                item = entry.get('item')
                if not isinstance(item, str) or item not in self.ids:
                    self.error(path + '.item', '이 id의 item이 없습니다.', 'items에 실제로 있는 id를 쓰세요.')
                elif item in solutions:
                    self.error(path + '.item', '같은 item의 풀이가 두 개입니다.', '하나의 풀이 객체로 합치세요.')
                else:
                    solutions.add(item)
                if not entry.get('steps'):
                    self.warn(path + '.steps', '교사용 풀이에 풀이 과정이 없습니다.',
                              '정답만 두지 말고 steps에 풀이 과정을 쓰세요(필요하면 rubric·misconceptions도).')
                self.text(entry.get('answer'), path + '.answer', private=True)
                for key in groups:
                    if key in entry:
                        self.texts(entry[key], f'{path}.{key}', minimum=0, private=True)
                if 'video' in entry:
                    self.video(entry['video'], path + '.video', solution=True)
        for item, path in self.numbered.items():
            if item not in solutions:
                self.warn(path, '번호 있는 발문의 교사용 풀이가 없습니다.', 'teacher.solutions에 이 item의 풀이를 쓰세요.')

    def check(self, data):
        if not self.object(data, '$', ('version', 'meta', 'items', 'teacher')):
            return self.issues
        self.integer(data.get('version'), 'version', 1, 1)
        meta = data.get('meta')
        self.kind = meta.get('kind', '수업') if isinstance(meta, dict) else '수업'
        self.columns = meta.get('columns', 1) if isinstance(meta, dict) else 1
        if isinstance(meta, dict):
            title = self.text(meta.get('title'), 'meta.title', private=True)
            if title and title.blanks:
                self.error('meta.title', '제목에는 빈칸 표기를 넣을 수 없습니다.', '빈칸은 상황이나 발문에 넣으세요.')
            for key in ('term', 'grade', 'subject', 'header_title', 'unit', 'goal'):
                if key in meta and (key != 'header_title' or meta[key] is not None):
                    parsed = self.text(meta[key], 'meta.' + key, empty=key in ('term', 'grade', 'subject', 'unit'), private=True)
                    if parsed and parsed.blanks:
                        self.error('meta.' + key, '머리표 정보에는 빈칸 표기를 넣을 수 없습니다.', '빈칸은 상황이나 발문에 넣으세요.')
            for i, name in enumerate(self.texts(meta['name_fields'], 'meta.name_fields', minimum=1, maximum=3, private=True) or []
                                     if 'name_fields' in meta else []):
                if isinstance(name, str) and '{{' in name:
                    self.error(f'meta.name_fields[{i}]', '이름 항목에는 빈칸 표기를 넣을 수 없습니다.', '라벨만 쓰세요. 쓰기 공간은 자동으로 붙습니다.')
        for i, item in enumerate(self.list(data.get('items'), 'items') or []):
            path = f'items[{i}]'
            if not self.object(item, path, ('id', 'break', 'blocks', 'hints')):
                continue
            item_id = item.get('id')
            if (not isinstance(item_id, str) or not re.fullmatch(r'[A-Za-z0-9-]+', item_id) or
                    item_id == 'head'):
                self.error(path + '.id', '사용할 수 없는 id입니다.', 'head가 아닌 영문·숫자·붙임표 id를 쓰세요.')
                item_id = None
            elif item_id in self.ids:
                self.error(path + '.id', '이미 있는 id입니다.', '문서 전체에서 유일한 id로 바꾸세요.')
            else:
                self.ids.add(item_id)
            self.bools(item, path, 'break')
            if 'hints' in item:
                self.texts(item['hints'], path + '.hints', minimum=1, maximum=3)
            self.local_marks = set()
            marks = []
            for j, block in enumerate(self.list(item.get('blocks'), path + '.blocks') or []):
                self.block(block, f'{path}.blocks[{j}]', item_id, marks)
            for where, mark in marks:
                if mark not in self.local_marks:
                    self.warn(where, '같은 item의 상황·발문에 이 빈칸 기호가 없습니다.',
                              'mark를 같은 item의 빈칸과 맞추거나 필요 없으면 지우세요.')
        self.meta(meta)
        self.teacher(data.get('teacher', {}))
        return self.issues


def validate(data, folder: Path):
    return Validator(folder).check(data)


def load_plan(folder: Path):
    """Return (parsed manuscript, exact input bytes, validation issues)."""
    raw = (folder / SOURCE).read_bytes()
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except json.JSONDecodeError as error:
        return None, raw, [Issue('error', '$', f'JSON 형식 오류({error.lineno}행 {error.colno}열).',
                                 '쉼표, 큰따옴표, 괄호를 확인하세요.')]
    except UnicodeError:
        return None, raw, [Issue('error', '$', 'UTF-8로 읽을 수 없습니다.', '구성.json을 UTF-8로 저장하세요.')]
    except (ValueError, RecursionError):
        return None, raw, [Issue('error', '$', 'JSON의 깊이 또는 숫자가 너무 큽니다.', '중첩이나 숫자 자릿수를 줄이세요.')]
    return data, raw, validate(data, folder)


def print_issues(issues):
    for issue in issues:
        label = '오류' if issue.level == 'error' else '경고'
        print(f'{label} {issue.path}: {issue.message} 수정: {issue.hint}')


def cmd_plan(folder: Path) -> int:
    _, _, issues = load_plan(folder)
    print_issues(issues)
    errors = sum(issue.level == 'error' for issue in issues)
    print(f'구성 검증: 오류 {errors}개, 경고 {len(issues) - errors}개')
    print('다음: 구성.json을 고친 뒤 plan을 다시 실행하세요.' if errors else '다음: build로 HTML을 조립하세요.')
    return int(bool(errors))
