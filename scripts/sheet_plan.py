"""Validate a worksheet manuscript without Chrome or optional dependencies."""
import ast
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from fractions import Fraction
import json
from html.parser import HTMLParser
from itertools import product
import math
import operator
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from sheet_common import (KINDS, LABEL_MM, PICTURE_CELL_PADDING_MM, PLACEHOLDER, RASTER_MIMES, ROOT, SOURCE,
                          SPLIT_RATIOS, box_five, box_tick_values, content_width_mm, figure_width_mm, image_path,
                          situation_cell_mm, split_widths_mm, youtube_id)
from sheet_answer import UNITS, norm, normalized_unit, num, plain


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
    code: str = ''


@lru_cache(maxsize=1)
def load_terms():
    return json.loads((ROOT / 'assets/kit/terms.json').read_text(encoding='utf-8'))


def grade_level(value):
    if not isinstance(value, str) or '고' in value or '초' in value:
        return None
    match = re.search(r'중\s*(?:학교\s*)?([123])(?!\d)(?:\s*학년)?|(?<!\d)([123])\s*학년', value)
    return int(match.group(1) or match.group(2)) if match else None


class StudentText(HTMLParser):
    """렌더된 빈칸의 정답은 빼고 학생에게 보이는 수식·글만 모은다."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            self.hidden += 1
        elif 'ws-key' in dict(attrs).get('class', '').split():
            self.hidden = 1

    def handle_endtag(self, tag):
        if self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@lru_cache(maxsize=1)
def load_registry():
    return json.loads((ROOT / 'assets/kit/blocks.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def load_expressions():
    return json.loads((ROOT / 'assets/kit/expressions.json').read_text(encoding='utf-8'))['expressions']


def is_number(value):
    return type(value) in (int, float) and abs(value) <= 1e100


def exact_number(source):
    decimal = Decimal(source)
    if not decimal.is_finite() or abs(decimal.as_tuple().exponent) > 4800:
        raise ValueError('검산할 수의 자릿수가 너무 많습니다.')
    return Fraction(decimal)


def on_step(value, low, step):
    """슬라이더 눈금은 min을 기준으로 하며 소수 연산 오차만 허용한다."""
    index = (Fraction(str(value)) - Fraction(str(low))) / Fraction(str(step))
    return abs(index - round(index)) <= Fraction(1, 100_000_000)


def slider_samples(low, high, step, excluded):
    low, high, step = (Fraction(str(v)) for v in (low, high, step))
    last = (high - low) // step
    omitted = set()
    for value in excluded:
        index = (Fraction(str(value)) - low) / step
        if index.denominator == 1 and 0 <= index <= last:
            omitted.add(int(index))
    count = last + 1 - len(omitted)
    if not count:
        return []
    ranks = range(count) if count <= 61 else (i * (count - 1) // 60 for i in range(61))
    skipped = iter(sorted(omitted))
    next_skipped, offset = next(skipped, last + 1), 0
    values = []
    for rank in ranks:
        while rank + offset >= next_skipped:
            offset += 1
            next_skipped = next(skipped, last + 1)
        values.append(float(low + (rank + offset) * step))
    return values


def slider_scopes(ranges, snapshots):
    defaults = {name: values[3] for name, values in ranges.items()}
    yield defaults
    for snapshot in snapshots:
        yield {**defaults, **snapshot}
    ticks = [slider_samples(low, high, step, excluded)
             for low, high, step, _, excluded in ranges.values()]
    for name, values in zip(ranges, ticks):
        for value in values:
            yield {**defaults, name: value}
    # Keep the check bounded with two or three sliders: combine a small, even grid.
    joint = [values if len(values) <= 9 else [values[i * (len(values) - 1) // 8] for i in range(9)]
             for values in ticks]
    if len(joint) > 1:
        for values in product(*joint):
            yield dict(zip(ranges, values))


BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.Pow: operator.pow}
UNOPS = {ast.USub: operator.neg}


# Match solve.js parseExpression, including ASCII digits and ECMAScript whitespace.
EXPLORE_NUMBER = re.compile(r'\d+(?:\.\d*)?|\.\d+', re.ASCII)
EXPLORE_TOKEN = re.compile(r'\*\*|[a-z()+*/-]')
JS_SPACE = '\t\n\v\f\r                  　﻿'


def explore_tree(source, names):
    def fail():
        raise ValueError('탐구 식은 소수·정수, 괄호, 선언한 한 글자 이름과 + - * / **로 쓰세요.')

    if len(source) > 512:
        fail()
    pos = 0

    def token():
        nonlocal pos
        while pos < len(source) and source[pos] in JS_SPACE:
            pos += 1
        match = EXPLORE_NUMBER.match(source, pos) or EXPLORE_TOKEN.match(source, pos)
        if match:
            pos = match.end()
            return match[0]
        if pos != len(source):
            fail()
        return ''

    current = token()

    def take():
        nonlocal current
        result, current = current, token()
        return result

    operators = {'+': (1, ast.Add), '-': (1, ast.Sub), '*': (2, ast.Mult),
                 '/': (2, ast.Div), '**': (4, ast.Pow)}

    def expression(minimum=0):
        item = take()
        if item == '(':
            result = expression()
            if take() != ')':
                fail()
        elif item == '-':
            result = ast.UnaryOp(op=ast.USub(), operand=expression(3))
        elif EXPLORE_NUMBER.fullmatch(item):
            result = ast.Constant(float(item))
        elif item in names:
            result = ast.Name(id=item, ctx=ast.Load())
        else:
            fail()
        while current in operators and operators[current][0] > minimum:
            symbol = take()
            precedence, op = operators[symbol]
            # Powers associate to the right; prefix minus binds less tightly than a power.
            rhs = expression(precedence - (symbol == '**'))
            result = ast.BinOp(left=result, op=op(), right=rhs)
        return result

    try:
        tree = expression()
    except RecursionError:
        fail()
    if current:
        fail()
    return tree


def function_tree(source, params=()):
    # Explore's plan and renderer both pass declared parameters; grid calls do not.
    if params:
        return explore_tree(source, {'x', *params})
    return arithmetic_tree(source)


def arithmetic_tree(source, params=(), *, exact=False):
    names = {'x', *params}
    if len(source) > 240 or not re.fullmatch(r'[0-9a-z.()+*/ \t-]+', source):
        raise ValueError('x, 수, 괄호와 + - * / **만 사용할 수 있습니다.')
    source = source.strip()
    try:
        tree = ast.parse(source, mode='eval').body
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ValueError('함수 식의 괄호와 연산 기호를 확인하세요.') from error

    def check(node, depth=0):
        if depth > 24:
            raise ValueError('함수 식의 중첩을 줄이세요.')
        if isinstance(node, ast.Constant) and is_number(node.value):
            if exact:
                node.value = exact_number(ast.get_source_segment(source, node))
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


def function_value(tree, x, params=None, *, exact=False):
    values = {'x': x, **(params or {})}
    number = (lambda value: value if isinstance(value, Fraction) else Fraction(str(value))) if exact else float

    def calculate(node):
        if isinstance(node, ast.Constant):
            return number(node.value)
        if isinstance(node, ast.Name):
            return number(values[node.id])
        if isinstance(node, ast.UnaryOp):
            return UNOPS[type(node.op)](calculate(node.operand))
        left, right = calculate(node.left), calculate(node.right)
        if exact and isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right.numerator) > 1000:
                raise ValueError('검산의 거듭제곱 지수는 -1000~1000의 정수여야 합니다.')
            if max(left.numerator.bit_length(), left.denominator.bit_length()) * abs(right.numerator) > 16384:
                raise ValueError('검산할 수가 너무 큽니다.')
            right = right.numerator
        result = BINOPS[type(node.op)](left, right)
        if exact:
            if max(result.numerator.bit_length(), result.denominator.bit_length()) > 16384:
                raise ValueError('검산할 수가 너무 큽니다.')
        elif type(result) is not float or not math.isfinite(result):
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


def svg_tag(element):
    return element.tag.rsplit('}', 1)[-1]


def svg_alt(root):
    label = root.get('aria-label', '').strip()
    if label:
        return label
    return next((' '.join(''.join(node.itertext()).split()) for node in root
                 if svg_tag(node) == 'title' and ''.join(node.itertext()).strip()), '')


def svg_viewbox(root):
    raw = root.get('viewBox', '').strip()
    if not raw:
        return None
    try:
        box = tuple(map(float, re.split(r'[\s,]+', raw)))
    except ValueError:
        return None
    return box if len(box) == 4 and all(map(math.isfinite, box)) and min(box[2:]) > 0 else None


SVG_LENGTH = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(px|pt|pc|mm|cm|in|q|em|rem|%)?', re.I)
SVG_PX = {'px': 1, 'pt': 96 / 72, 'pc': 16, 'mm': 96 / 25.4, 'cm': 96 / 2.54,
          'in': 96, 'q': 96 / 101.6, 'rem': 16}


def svg_property(node, name):
    # Inline declarations override presentation attributes; the last declaration of equal priority wins.
    value, important = node.get(name, ''), False
    for declaration in node.get('style', '').split(';'):
        key, _, style_value = declaration.partition(':')
        if key.strip().lower() != name:
            continue
        style_value, n = re.subn(r'\s*!\s*important\s*$', '', style_value, flags=re.I)
        if style_value.strip() and (n or not important):
            value, important = style_value, bool(n)
    return value.strip()


def svg_length(value, parent=None, rem=16):
    match = SVG_LENGTH.fullmatch(value)
    if not match:
        return None
    amount, unit = float(match[1]), (match[2] or 'px').lower()
    if unit in ('em', '%'):
        if parent is None:
            return None
        amount *= parent if unit == 'em' else parent / 100
    else:
        amount *= rem if unit == 'rem' else SVG_PX[unit]
    return amount if math.isfinite(amount) and amount >= 0 else None


def svg_mm_per_unit(root, width_mm):
    """Estimate horizontal and vertical print scale of an auto-height SVG image."""
    box = svg_viewbox(root)
    width = svg_length(svg_property(root, 'width'))
    height = svg_length(svg_property(root, 'height'))
    if not box:
        # Without a viewBox, changing the image viewport does not scale SVG user units.
        return 25.4 / 96, 25.4 / 96
    sx = width_mm / box[2]
    if not width or not height:
        return sx, sx
    sy = (width_mm / width) * (height / box[3])
    align = root.get('preserveAspectRatio', '').split()
    if 'none' in align:
        return sx, sy
    scale = max(sx, sy) if 'slice' in align else min(sx, sy)
    return scale, scale


SVG_TRANSFORM = re.compile(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)')
SVG_TRANSFORM_NUMBER = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
SVG_TRANSFORM_ARGS = re.compile(rf'{SVG_TRANSFORM_NUMBER}(?:(?:\s*,\s*|\s+){SVG_TRANSFORM_NUMBER})*')
SVG_IDENTITY = (1, 0, 0, 1)
FIGURE_SCALE_TOLERANCE = .02


def svg_compose_linear(outer, inner):
    a, b, c, d = outer
    e, f, g, h = inner
    return a*e + c*f, b*e + d*f, a*g + c*h, b*g + d*h


def svg_linear_transform(value):
    # Translation (including a transform's center) cannot change a glyph's size.
    result, end = SVG_IDENTITY, 0
    for match in SVG_TRANSFORM.finditer(value):
        name, args = match[1], match[2].strip()
        if value[end:match.start()].strip(' ,\t\r\n') or not SVG_TRANSFORM_ARGS.fullmatch(args):
            return SVG_IDENTITY
        end = match.end()
        values = tuple(map(float, re.split(r'[\s,]+', args)))
        if not all(map(math.isfinite, values)):
            return SVG_IDENTITY
        n = len(values)
        if name == 'matrix' and n == 6:
            transform = values[:4]
        elif name == 'scale' and n in (1, 2):
            transform = (values[0], 0, 0, values[-1])
        elif name == 'translate' and n in (1, 2):
            continue
        elif name == 'rotate' and n in (1, 3):
            angle = math.radians(values[0] % 360)
            c, s = math.cos(angle), math.sin(angle)
            transform = (c, s, -s, c)
        elif name in ('skewX', 'skewY') and n == 1:
            slope = math.tan(math.radians(values[0] % 180))
            transform = (1, 0, slope, 1) if name == 'skewX' else (1, slope, 0, 1)
        else:
            return SVG_IDENTITY
        result = svg_compose_linear(result, transform)
    return result if not value[end:].strip() else SVG_IDENTITY


def svg_smallest_label(root, width_mm, *, viewport=None):
    sx, sy = svg_mm_per_unit(root if viewport is None else viewport, width_mm)
    smallest = None
    root_size = svg_length(svg_property(root, 'font-size'), 16)
    stack = [(root, 16, False, (sx, 0, 0, sy))]
    while stack:
        node, inherited, in_text, matrix = stack.pop()
        tag = svg_tag(node)
        size = svg_length(svg_property(node, 'font-size'), inherited, 16 if root_size is None else root_size)
        size = inherited if size is None else size
        matrix = svg_compose_linear(matrix, svg_linear_transform(svg_property(node, 'transform')))
        in_text = in_text or tag == 'text'
        if in_text and ((node.text or '').strip() or any((child.tail or '').strip() for child in node)):
            height = size * math.hypot(matrix[2], matrix[3])
            if math.isfinite(height):
                smallest = height if smallest is None else min(smallest, height)
        stack.extend((child, size, in_text, matrix) for child in node)
    return None if smallest is None else smallest * 72 / 25.4


def figure_scale_differs(scale, base):
    delta, limit = abs(scale - base), base * FIGURE_SCALE_TOLERANCE
    return delta > limit and not math.isclose(delta, limit)


def answer_svg_source(answer):
    return answer.get('svg') if isinstance(answer, dict) else answer


# Keep a decimal point inside a sentence even when math and prose touch.
SENTENCE_BREAK = re.compile(r'[!?。！？\n]|\.(?!\d)')
QUADRATIC_MATH = re.compile(r'<msup><mi>x</mi><mn>2</mn></msup>')
# A correction applies only to the occurrence it directly negates, not the whole sentence.
EXPRESSION_CORRECTION = re.compile(
    r'\s*(?:(?:하면|면|해서는|해선|하여서는)\s*안\s*[되된돼됩]|'
    r'(?:하|열리|리)?지\s*(?:않|말|마)|'
    r'(?:할|될|릴)\s*수(?:는)?\s*없|'
    r'(?:하는\s*(?:것은|것이)|한다는\s*말은)\s*(?:(?:옳|맞|올바르)지\s*않|아니)|'
    r'[’”\'\"]?\s*(?:(?:라는|이라는)\s*(?:말|표현)(?:은|을)|라고)?\s*(?:쓰지|사용하지)\s*(?:않|말|마))')
INLINE_PARTS = re.compile(r'\\[\\${]|\$((?:\\[\\$]|[^$])*)\$')
UPRIGHT_MATH = re.compile(r'\\(?:text|mathrm)\s*\{[^{}]*\}')
UNIT_BASES = '|'.join([*(unit for unit in UNITS if re.fullmatch('[a-z]{2,}', unit)), 'mL'])
UNIT_EXPONENT = r'(?:[²³]|\^\s*(?:[23]|\{\s*[23]\s*\})|[23])'
MATH_NUMBER = r'(?:\d+(?:\{,\}\d+)*(?:\.\d*)?|\.\d+)'
NUMBER_IN_FORMULA = rf'(?<![A-Za-z0-9_.^]){MATH_NUMBER}'
BARE_MATH_UNIT = re.compile(
    rf'{NUMBER_IN_FORMULA}\s*(?:m{UNIT_EXPONENT}|(?:{UNIT_BASES})(?:{UNIT_EXPONENT})?)(?![A-Za-z0-9_^])',
    re.ASCII)
BARE_METRE = re.compile(rf'{NUMBER_IN_FORMULA}\s+m(?!\s*[A-Za-z0-9_^²³])', re.ASCII)
LENGTH_CONTEXT = re.compile(r'길이|거리|높이|너비|반지름|지름|둘레|깊이|두께')
LENGTH_UNIT = r'(?:mm|cm|km|m|㎜|㎝|㎞|밀리미터|센티미터|킬로미터|미터)'
LENGTH_UNIT_AFTER_MATH = re.compile(rf'(?:\*\*)?\s*{LENGTH_UNIT}(?![A-Za-z0-9_^²³])')
LENGTH_NUMBER = re.compile(
    rf'-?{MATH_NUMBER}(?P<unit>(?:\s|\\[,;:! ])*'
    rf'(?:{LENGTH_UNIT}|\\(?:text|mathrm)\s*\{{\s*{LENGTH_UNIT}\s*\}}))?')
# Expression checks see rendered text, including numbers with grouping commas.
RENDERED_NUMBER = r'(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)'
LENGTH_VALUE = re.compile(
    rf'\s*(?:[+−-]?√?(?:\({RENDERED_NUMBER}\)/\({RENDERED_NUMBER}\)|{RENDERED_NUMBER})|[A-Za-zΑ-Ωα-ω])'
    rf'(?=(?:\s+|(?<=\d)){LENGTH_UNIT}(?![A-Za-z0-9_^²³])|[+−×÷/=<>≤≥≠^²³-]|'
    r'\s*(?:이(?:고|다|며|면|므로|거나|상|하|\s)|인|일|임|보다|으?로|[,.](?!\d)|[;:)]|$))')
NON_LENGTH_OBJECT_BEFORE = re.compile(r'(?:행렬|집합)\s*(?:\*\*)?\s*$')
NON_LENGTH_OBJECT_AFTER = re.compile(r'\s*(?:인\s*)?(?:행렬|집합)')
BARE_SEGMENT_NAME = r'[A-Z]{2}'
SEGMENT_NAME = rf'(?:{BARE_SEGMENT_NAME}|\\overline\s*\{{\s*{BARE_SEGMENT_NAME}\s*\}})'
SEGMENT_COMPARISON = re.compile(
    rf'(?<![A-Za-z0-9_^\\])({SEGMENT_NAME})\s*(?:[=<>≤≥≠]|\\(?:leq?|geq?|ne)(?![a-z]))\s*'
    rf'(?=({SEGMENT_NAME})(?![A-Za-z0-9_^]))')
GEOMETRY_PREFIX = re.compile(r'(?:\\(?:angle|triangle|square|overarc)[\s{]*|[∠△□]\s*)$')
GRAPH_TARGET = r'상자\s*그림|히스토그램|도수분포\s*다각형|(?:막대\s*|꺾은선\s*)?그래프'
DRAW_ACTION = re.compile(
    r'(?<![가-힣])(?:그[어려](?:서)?(?![가-힣]|\s+(?:놓[은았]|둔|두었))|그으(?:시오|세요)|그읍시다|'
    r'그리(?:시오|세요|자|고\s+나서)|그립시다|그린\s*(?:후|뒤|다음)(?![가-힣])|'
    r'작도\s*(?:하시오|하세요|하자|하고|하여|해|한\s*(?:후|뒤|다음)(?![가-힣]))|'
    r'(?P<mark>표시\s*(?:하시오|하세요|하자|하고|하여|해))|'
    rf'(?:(?:{GRAPH_TARGET})(?:으)?로|'
    r'(?P<numberline>수직선\s*(?:위\s*)?에))\s+나타내(?:시오|세요|자|고|어?\s*보))'
    r'|[을를]\s+그리고(?![가-힣])')
PAPER_LOCATION = re.compile(r'(?:종이(?:\s*\(인쇄본\))?|인쇄본|공책)(?:\s*위)?\s*에(?:서)?')
MARK_LOCATION = re.compile(r'좌표|선분|직선|교점|외심|내심|중점|그래프|수직선|모눈|상자그림|히스토그램|도수분포다각형|'
                           r'(?<![가-힣])(?:점|각|원|변)(?=[을를에]|\s+[A-Z]|$)')
CATEGORY_HEADER_UNIT = re.compile(r'\s*(?:모둠|학급|반|구분|항목|이름|번호|종류)\s*\(\s*단위\s*:\s*[^()]+\)\s*')


def math_contexts(value):
    # Mask formulas before finding sentence ends; punctuation in TeX is not prose.
    prose = INLINE_PARTS.sub(lambda part: '@' * len(part[0]), value)
    ends = iter(SENTENCE_BREAK.finditer(prose))
    start, end = 0, next(ends, None)
    for part in INLINE_PARTS.finditer(value):
        if part[1] is None:
            continue
        while end and end.start() < part.start():
            start, end = end.end(), next(ends, None)
        yield part[1], prose[start:part.start()], prose[part.end():end.start() if end else len(prose)]


def numeric_segment_equality(formula, after, length_context):
    left, equal, right = (part.strip() for part in formula.partition('='))
    if not equal:
        return False
    if not re.fullmatch(BARE_SEGMENT_NAME, left):
        left, right = right, left
    if not re.fullmatch(BARE_SEGMENT_NAME, left):
        return False
    number = LENGTH_NUMBER.fullmatch(right)
    return number is not None and (length_context or number['unit'] is not None or
                                   LENGTH_UNIT_AFTER_MATH.match(after) is not None)


def visible_text(parsed):
    parser = StudentText()
    parser.feed(QUADRATIC_MATH.sub('x²', parsed.html))
    return ''.join(parser.parts)


def item_blocks(blocks, path):
    for i, block in enumerate(blocks if isinstance(blocks, list) else []):
        if not isinstance(block, dict):
            continue
        where = f'{path}[{i}]'
        yield block, where
        if block.get('type') == 'split':
            for side in ('left', 'right'):
                yield from item_blocks(block.get(side), f'{where}.{side}')


BOX_ROW_HEADERS = ('구분', '모둠', '학급', '반', '항목', '이름', '종류')
BOX_FIVE_HEADERS = ('최솟값', '제1사분위수', '중앙값', '제3사분위수', '최댓값')
BOX_RAW_HEADER = re.compile(r'(?:원자료|자료(?: 값)?|(?:\S+ )*점수)(?:\s*\(([^()]+)\))?')
BOX_CELL_NUMBER = re.compile(r'[+\-−]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)')


def box_table_rows(blocks):
    for block, path in blocks:
        header, rows = block.get('header'), block.get('rows')
        if (block.get('type') != 'table' or not isinstance(header, list) or not header or
                header[0] not in BOX_ROW_HEADERS or not isinstance(rows, list)):
            continue
        raw = BOX_RAW_HEADER.fullmatch(header[1]) if len(header) == 2 and isinstance(header[1], str) else None
        if raw and any(word in header[1] for word in ('평균', '중앙값', '합계', '총점', '대표')):
            continue
        if raw is None and tuple(header[1:]) != BOX_FIVE_HEADERS:
            continue
        names = [row[0] for row in rows if isinstance(row, list) and row and isinstance(row[0], str)]
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != len(header) or names.count(row[0]) != 1:
                continue
            if not all(isinstance(cell, str) for cell in row):
                continue
            try:
                cells = [inspect_markup(cell).plain.strip() for cell in row[1:]]
            except MarkupError:
                continue
            values = re.split(r'\s*[,，]\s*', cells[0]) if raw else cells
            if (raw and len(values) < 2) or not all(len(v) <= 90 and BOX_CELL_NUMBER.fullmatch(v) for v in values):
                continue
            yield row[0], raw is not None, raw[1] if raw else None, [
                Fraction(v.replace('−', '-')) for v in values], f'{path}.rows[{i}]'


def screen_drawing_tools(blocks):
    for block, _ in blocks:
        kind = block.get('type')
        if (block.get('pen') is True and not block.get('teacher_only') and
                (kind in ('figure', 'grid', 'numberline') or kind == 'chart' and block.get('kind') == 'box')):
            yield 'pen'
        if kind == 'answer' and isinstance(block.get('rows'), list) and any(
                isinstance(row, dict) and row.get('kind') == 'draw' for row in block['rows']):
            yield 'pen'
        plot = block.get('plot')
        if isinstance(plot, dict):
            if kind == 'grid' and plot.get('mode') in ('points', 'line'):
                yield plot['mode']
            elif kind == 'numberline':
                yield 'points'
            elif kind == 'chart' and block.get('kind') in ('bar', 'histogram'):
                yield 'bars'


class Validator:
    def __init__(self, folder, decimals=None):
        self.decimals = {} if decimals is None else decimals
        self.folder = folder
        self.issues = []
        self.blank_index = 0
        self.points = []
        self.ids = set()
        self.numbered = {}
        self.limits = {}
        self.local_marks = set()
        self.set_text_context({})

    def set_text_context(self, meta):
        """Set the grade and unit context before checking manuscript or edited text."""
        self.grade = grade_level(meta.get('grade'))
        self.unit_context = '\n'.join(meta[key] for key in ('unit', 'title') if isinstance(meta.get(key), str))

    def error(self, path, message, hint, code=''):
        self.issues.append(Issue('error', path, message, hint, code))

    def warn(self, path, message, hint, code=''):
        self.issues.append(Issue('warning', path, message, hint, code))

    def check_terms(self, text, path):
        if self.grade is None:
            return
        sentences = SENTENCE_BREAK.split(text)
        levels = load_terms()['levels']
        for entry in load_terms()['terms']:
            level = entry['level']
            if level not in levels or levels.index(level) < self.grade:
                continue
            term = entry['term']
            prefixes = entry.get('skip_if_preceded_by', [])
            prefixes = (prefixes,) if isinstance(prefixes, str) else tuple(prefixes)
            suffix = entry.get('skip_if_followed_by')
            context = entry.get('context')
            unit_context = entry.get('unit_context')
            if unit_context and re.search(unit_context, self.unit_context):
                context = None
            if any(not sentence[:match.start()].endswith(prefixes) and
                   not (suffix and re.match(suffix, sentence[match.end():]))
                   for sentence in sentences if not context or re.search(context, sentence)
                   for match in re.finditer(re.escape(term), sentence)):
                hint = '일상어가 아니라면 이 학년에서 배운 말로 바꾸세요.'
                if entry.get('note'):
                    hint += ' ' + entry['note']
                self.warn(path, f'‘{term}’ 용어는 {level}에서 처음 나옵니다.', hint, 'term-beyond-grade')

    def check_expressions(self, text, path):
        sentences = SENTENCE_BREAK.split(text)
        for entry in load_expressions():
            unit = entry.get('unit')
            guidance = entry
            if unit and not re.search(unit, self.unit_context):
                guidance = entry.get('otherwise')
                if guidance is None:
                    continue
            context = entry.get('context')
            pattern = entry.get('pattern') or re.escape(entry['avoid'])
            needs_length = entry.get('followed_by_length')
            match = next((match for sentence in sentences
                          if not context or any(word in sentence for word in context)
                          for match in re.finditer(pattern, sentence)
                          if (not needs_length or LENGTH_VALUE.match(sentence, match.end()))
                          and not EXPRESSION_CORRECTION.match(sentence[match.end():])), None)
            if match:
                self.warn(path, f'교과서 표현을 확인하세요: ‘{entry.get("avoid", match[0])}’.',
                          f'{guidance["reason"]} 문맥에 맞게 ‘{guidance["preferred"]}’ 표현으로 이으세요.',
                          'textbook-expression')

    def check_math_notation(self, value, path):
        bad_unit = bad_segment = False
        for formula, before, after in math_contexts(value):
            math_source = UPRIGHT_MATH.sub('@', formula)
            length_context = bool(LENGTH_CONTEXT.search(before) or LENGTH_CONTEXT.search(after))
            if BARE_MATH_UNIT.search(math_source) or (length_context and BARE_METRE.search(math_source)):
                bad_unit = True
            if NON_LENGTH_OBJECT_BEFORE.search(before) or NON_LENGTH_OBJECT_AFTER.match(after):
                continue
            if numeric_segment_equality(formula, after, length_context) or any(
                    not (match[1].startswith('\\') and match[2].startswith('\\')) and
                    not GEOMETRY_PREFIX.search(math_source[:match.start()])
                    for match in SEGMENT_COMPARISON.finditer(math_source)):
                bad_segment = True
        if bad_unit:
            self.warn(path, '수식 안의 단위가 변수처럼 기울어 보입니다.',
                      r'$12$ cm 또는 $12\text{ cm}$처럼 단위를 정체로 쓰세요.', 'math-unit-upright')
        if bad_segment:
            self.warn(path, '선분 길이 표기에 윗줄이 빠졌습니다.',
                      r'$\overline{OA}=\overline{OB}$, $\overline{AB}=6$ cm처럼 선분 이름 위에 윗줄을 쓰세요.',
                      'segment-length-overline')

    def check_screen_drawing(self, blocks, path):
        located = list(item_blocks(blocks, path))
        tools = set(screen_drawing_tools(located))
        if 'pen' in tools:
            return
        numberline_plot = any(b.get('type') == 'numberline' and isinstance(b.get('plot'), dict) for b, _ in located)
        for block, where in located:
            source = block.get('text')
            if block.get('type') not in ('question', 'ask') or not isinstance(source, str):
                continue
            try:
                text = visible_text(inspect_markup(source))
            except MarkupError:
                continue
            for sentence in SENTENCE_BREAK.split(text):
                for action in DRAW_ACTION.finditer(sentence):
                    before = re.split(r'[,，]', sentence[:action.start()])[-1]
                    if PAPER_LOCATION.search(before):
                        continue
                    marking = action.group('mark') is not None
                    if marking and not MARK_LOCATION.search(before):
                        continue
                    clause = before + action[0]
                    if ('bars' in tools and any(word in clause for word in ('막대', '히스토그램')) or
                        'line' in tools and re.search(r'(?<!수)직선|일차함수의 그래프|정비례 관계의 그래프', clause) or
                        'points' in tools and marking and re.search(r'점|좌표', before) or
                        numberline_plot and action.group('numberline') and not re.search(GRAPH_TARGET, before)):
                        continue
                    self.warn(where + '.text', '화면 풀이의 작도·그리기 지시에 화면에서 그릴 곳이 없습니다.',
                              '같은 문항에 펜(pen: true)이나 그리기 칸을 넣거나, 종이(인쇄본)에 그리라고 쓰세요. '
                              '밑그림에 겹칠 정답은 그림의 answer에 같은 viewBox의 SVG로, 그리기 칸의 정답은 행의 answer에 그림 객체로 넣으세요. '
                              '상자그림에는 chart의 kind: "box"를 쓰세요.', 'interactive-drawing-missing')
                    break
                else:
                    continue
                break

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

    def text(self, value, path, *, empty=False, private=False, local=False, field=None, term_visible=None,
             expression_visible=None):
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
        self.check_math_notation(value, path)
        try:
            parsed = inspect_markup(value)
        except MarkupError as error:
            self.error(path, str(error), '$…$, **…**, {{…}}의 짝과 수식 명령을 확인하세요.')
            return None
        if not empty and not parsed.plain.strip():
            self.error(path, '표기 안에 실제 내용이 없습니다.', '화면에 보일 글자나 수식을 쓰세요.')
        check_terms = term_visible if term_visible is not None else not private
        check_expressions = expression_visible if expression_visible is not None else check_terms
        if check_terms or check_expressions:
            text = visible_text(parsed) if parsed.blanks and not private else parsed.plain
            if check_terms:
                self.check_terms(text, path)
            if check_expressions:
                self.check_expressions(text, path)
        limit = self.limits.get(field)
        if limit is not None and len(parsed.plain) > limit:
            self.warn(path, f'글자 수 권장 한도({limit}자)를 넘었습니다.', '문장을 줄이거나 여러 문단으로 나누세요.', 'text-length')
        if '□' in parsed.outside_math:
            self.warn(path, '점검 줄 기호(□)가 있습니다.', '학생이 해야 할 행동을 문장으로 쓰세요.', 'checklist-mark')
        if re.search(r'(?<![A-Za-z0-9])[A-Z]+(?![A-Za-z0-9])', parsed.outside_math):
            self.warn(path, '수식 밖에 영문 대문자 낱말이 있습니다.', '점은 $A$, 평면은 $\\mathit{A}$처럼 쓰세요.', 'latin-outside-math')
        for blank in parsed.blanks:
            if not blank.answer:
                self.error(path, '빈칸에 교사용 정답이 없습니다.', '예: {{㉠|정답}}처럼 정답을 써 넣으세요.')
            if num(plain(blank.source)) is not None:
                for alternate in blank.alternatives:
                    if num(plain(alternate)) is None:
                        self.error(path, '수로 판별할 수 없는 빈칸 대안입니다.', '수나 분수로 쓴 대안을 사용하세요.')
            if ambiguous_answer(blank.answer) and not blank.alternatives:
                self.warn(path, '학생 입력과 정답 표기가 다를 수 있습니다.', '다른 표현을 허용하려면 ;; 뒤에 대안을 추가하세요.', 'answer-notation')
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

    def figure(self, value, path, *, outer=False, caption_required=False, private=False, field='', placement='body'):
        keys = ('src', 'svg', 'width_mm', 'alt', 'caption', 'teacher_only') + (('type', 'pen', 'answer') if outer else ())
        if not self.object(value, path, keys):
            return
        width_ok = self.positive(value.get('width_mm'), path + '.width_mm')
        self.bools(value, path, 'teacher_only', 'pen')
        private = private or value.get('teacher_only') is True
        if ('src' in value) == ('svg' in value):
            self.error(path, '그림의 출처가 하나여야 합니다.', 'src 또는 svg 중 하나만 쓰세요.')
        root, local_svg = None, False
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
                    root = ET.fromstring(checked_svg(raw))
                else:
                    file = image_path(self.folder, raw)
                    ext = file.suffix.lower()
                    local_svg = ext == '.svg'
                    if ext not in RASTER_MIMES and not local_svg:
                        raise ValueError('PNG, JPEG, WebP, GIF, BMP 또는 SVG 파일을 쓰세요.')
                    if not file.is_file():
                        raise ValueError('그림 파일이 없습니다. 그림/ 아래의 파일 이름을 확인하세요.')
                    if local_svg:
                        root = ET.fromstring(checked_svg(file.read_text(encoding='utf-8-sig')))
            except (ValueError, OSError, RuntimeError) as error:
                message = str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else '그림 파일을 읽지 못했습니다.'
                self.error(where, message, '활동지 폴더 안의 읽을 수 있는 그림을 지정하세요.')
        if 'alt' in value or ('src' in value and not local_svg):
            parsed = self.text(value.get('alt'), path + '.alt', private=True, term_visible=not private, field=field + 'alt')
            if parsed and parsed.blanks:
                self.error(path + '.alt', '대체 설명에는 빈칸을 그릴 수 없습니다.', '정답이 없는 일반 설명으로 바꾸세요.')
        elif root is not None and not private and placement != 'decoration' and not svg_alt(root):
            self.warn(path + '.alt', '학생용 SVG에 대체 설명이 없습니다.',
                      'alt, SVG의 aria-label 또는 <title>에 정답이 없는 설명을 쓰세요.', 'figure-alt-missing')
        if 'caption' in value or caption_required:
            self.text(value.get('caption'), path + '.caption', private=private, field=field + 'caption')
        overlay = self.figure_answer(value, root, path) if outer and 'answer' in value else None
        if root is None or not width_ok:
            return
        available = self.content_mm
        if placement == 'pair':
            available = available / 2 - PICTURE_CELL_PADDING_MM
        elif placement == 'situation':
            available = situation_cell_mm(available, value['width_mm']) - PICTURE_CELL_PADDING_MM
        elif placement == 'draw':
            available -= LABEL_MM.get(self.kind if self.kind in KINDS else '수업') + PICTURE_CELL_PADDING_MM
        elif placement == 'decoration':
            available = min(30, available * .3)
        width = figure_width_mm(value, available)
        where = path + ('.svg' if 'svg' in value else '.src')
        scale = svg_mm_per_unit(root, width)
        self.figure_labels(root, width, where)
        if value.get('pen') is True or overlay is not None or placement == 'draw':
            self.figure_aspects[where] = scale
        if overlay is not None:
            self.figure_labels(overlay, width, path + '.answer', viewport=root)
            self.figure_aspects[path + '.answer'] = svg_mm_per_unit(overlay, width)
        if (outer or placement == 'draw') and all(math.isfinite(v) and v > 0 for v in scale):
            self.figure_scales.append((private, path, where, scale))

    def figure_answer(self, value, base, path):
        answer = value['answer']
        where = path + '.answer'
        if 'src' in value:
            self.error(where, '파일 그림에는 정답층을 얹을 수 없습니다.', '밑그림을 svg 문자열로 쓰세요.')
            return None
        if isinstance(answer, dict) and not self.object(answer, where, ('svg',)):
            return None
        source = answer_svg_source(answer)
        try:
            if not isinstance(source, str) or not source.strip() or PLACEHOLDER in source:
                raise ValueError('정답층을 SVG 문자열 또는 {"svg": "…"}로 쓰세요.')
            root = ET.fromstring(checked_svg(source))
        except ValueError as error:
            self.error(where, str(error), '밑그림과 같은 viewBox를 쓰는 SVG로 바꾸세요.')
            return None
        box = svg_viewbox(root)
        if base is None or box is None or svg_viewbox(base) != box:
            self.error(where, '밑그림과 정답층의 viewBox가 다르거나 없습니다.', '두 SVG에 같은 viewBox를 쓰세요.')
            return None
        return root

    def figure_labels(self, root, width_mm, path, *, viewport=None):
        size = svg_smallest_label(root, width_mm, viewport=viewport)
        if size is not None and size < 6:
            self.warn(path, f'이 폭에서 SVG 글자 크기가 최소 {size:.2f}pt입니다.',
                      '그림을 넓히거나 SVG의 font-size를 조정해 인쇄 크기를 6pt 이상으로 맞추세요.', 'figure-label-small')

    def check_figure_scales(self):
        public = [i for i, (private, _, _, _) in enumerate(self.figure_scales) if not private]
        for i, (private, path, where, scale) in enumerate(self.figure_scales):
            if not private or not public:
                continue
            # Use the nearest student figure in this item; prefer the preceding one on a tie.
            ref = min(public, key=lambda j: (abs(j - i), j))
            _, base_path, base_where, base = self.figure_scales[ref]
            self.figure_aspects.update({base_where: base, where: scale})
            if any(figure_scale_differs(answer, student) for answer, student in zip(scale, base)):
                self.warn(path, f'학생 그림({base_path})의 가로·세로 축척은 {base[0]:.4g}·{base[1]:.4g}mm/단위, '
                          f'정답 그림은 {scale[0]:.4g}·{scale[1]:.4g}mm/단위입니다.',
                          '같은 축척으로 맞추거나 figure.answer에 정답층을 넣으세요.', 'answer-scale-mismatch')
        for where, (sx, sy) in self.figure_aspects.items():
            if all(math.isfinite(v) and v > 0 for v in (sx, sy)) and not math.isclose(sx, sy):
                self.warn(where, f'SVG의 가로·세로 축척이 {sx:.4g}·{sy:.4g}mm/단위로 달라 그림이 찌그러질 수 있습니다.',
                          'width와 height의 비율을 viewBox와 맞추거나 preserveAspectRatio="none"을 빼세요.',
                          'figure-aspect-distorted')

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
                    self.warn(where + '.grow', '짧은 답 칸이 쪽 채움으로 커집니다.', 'grow를 빼고 lines로 정하세요.', 'short-answer-grow')
                if check == 'number' and num(value) is None:
                    self.error(where + '.answer', '수로 판별할 수 없는 정답입니다.', '숫자나 분수로 쓰세요.')
                if check in ('number', 'auto') and num(value) is not None and isinstance(row.get('accept', []), list):
                    for j, alternate in enumerate(row.get('accept', [])):
                        if isinstance(alternate, str) and num(plain(alternate)) is None:
                            self.error(f'{where}.accept[{j}]', '수로 판별할 수 없는 대안입니다.', '같은 종류의 수나 분수를 쓰세요.')
                if check != 'off' and ambiguous_answer(value):
                    self.warn(where + '.answer', '학생 입력과 정답 표기가 다를 수 있습니다.', '다른 표현을 허용하려면 accept 추가를 검토하세요.', 'answer-notation')
            kind = row.get('kind', 'text')
            if kind not in ('text', 'draw'):
                self.error(where + '.kind', '답 칸 종류를 알 수 없습니다.', 'text 또는 draw를 쓰세요.')
            answer = row.get('answer')
            if kind == 'draw' or 'height_mm' in row:
                self.positive(row.get('height_mm'), where + '.height_mm')
            if kind == 'draw' and isinstance(answer, dict):
                self.figure(answer, where + '.answer', private=True, placement='draw')
            else:
                self.text(answer, where + '.answer', private=True, expression_visible=True, field='rows[].answer')

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
                                   private=True, term_visible=not entry.get('teacher', False), field='draw[].names[]') or []
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

    def frequencies(self, values, path, relative):
        if not isinstance(values, list):
            return
        for i, value in enumerate(values):
            if not is_number(value):
                continue
            where = f'{path}[{i}]'
            if value < 0:
                self.error(where, '도수는 음수일 수 없습니다.', '0 이상의 값을 쓰세요.')
            if not relative and not float(value).is_integer():
                self.warn(where, '도수가 정수가 아닙니다.', '자료의 개수인지 확인하고 필요하면 정수로 고치세요.', 'frequency-not-integer')

    def box_chart(self, block, path):
        allowed = {'type', 'kind', 'groups', 'x', 'x_step', 'x_label', 'width_mm', 'teacher', 'pen', 'guides'}
        for key in set(block) - allowed:
            if key in load_registry()['chart']['fields']:
                self.error(f'{path}.{key}', '상자그림에는 이 필드를 쓸 수 없습니다.',
                           '상자그림에는 groups와 공통 가로축을 쓰고, 학생이 그릴 때는 pen을 켜세요.')
        self.bools(block, path, 'teacher', 'pen', 'guides')
        if 'width_mm' in block and self.positive(block['width_mm'], path + '.width_mm') and block['width_mm'] < 35:
            self.error(path + '.width_mm', '상자그림의 폭이 너무 좁습니다.', '35mm 이상으로 쓰세요.')
        bounds = self.bounds(block.get('x'), path + '.x')
        step = block.get('x_step')
        if self.positive(step, path + '.x_step') and bounds:
            try:
                box_tick_values(bounds, step)
            except ValueError as error:
                self.error(path + '.x_step', str(error), '축 범위에 맞춰 눈금 간격을 바꾸세요.')
        if 'x_label' in block:
            label = self.text(block['x_label'], path + '.x_label', field='x_label')
            if label and label.blanks:
                self.error(path + '.x_label', '축 단위에는 빈칸을 넣을 수 없습니다.', '단위를 일반 글자로 쓰세요.')
        for i, group in enumerate(self.list(block.get('groups'), path + '.groups', 1, 4) or []):
            where = f'{path}.groups[{i}]'
            if not self.object(group, where, ('label', 'data', 'five')):
                continue
            label = self.text(group.get('label'), where + '.label', field='groups[].label')
            if label and label.blanks:
                self.error(where + '.label', '모둠 이름에는 빈칸을 넣을 수 없습니다.', '짧은 이름을 쓰세요.')
            if ('data' in group) == ('five' in group):
                self.error(where, '자료와 다섯 값 중 하나만 필요합니다.', 'data 또는 five 중 하나를 쓰세요.')
                continue
            key = 'five' if 'five' in group else 'data'
            values = self.list(group[key], where + '.' + key, 5 if key == 'five' else 4, 5 if key == 'five' else None)
            if values is None:
                continue
            if not all(is_number(v) for v in values):
                for j, value in enumerate(values):
                    if not is_number(value):
                        self.error(f'{where}.{key}[{j}]', '유한한 수가 필요합니다.', '자료 값을 숫자로 쓰세요.')
                continue
            if key == 'five' and any(a > b for a, b in zip(values, values[1:])):
                self.error(where + '.five', '다섯 값의 순서가 맞지 않습니다.', '최솟값부터 최댓값까지 작은 값부터 쓰세요. 같은 값도 됩니다.')
                continue
            low, _, _, _, high = box_five(group)
            if bounds and not bounds[0] <= low <= high <= bounds[1]:
                self.error(where + '.' + key, '자료가 가로축 범위를 벗어났습니다.', 'x가 최솟값부터 최댓값까지 덮도록 넓히세요.')

    def check_box_tables(self, blocks):
        candidates = {}
        for block, path in blocks:
            if block.get('type') != 'chart' or block.get('kind') != 'box' or not isinstance(block.get('groups'), list):
                continue
            groups = [g for g in block['groups'] if isinstance(g, dict) and isinstance(g.get('label'), str)]
            unit = block.get('x_label')
            unit = re.fullmatch(r'\(([^()]+)\)', unit) if isinstance(unit, str) else None
            for group in groups:
                key = 'data' if 'data' in group else 'five'
                values = group.get(key)
                if ('data' in group and 'five' in group or not isinstance(values, list) or
                        not all(map(is_number, values)) or (len(values) < 4 if key == 'data' else len(values) != 5)):
                    continue
                # Keep decimal midpoints exact when checking written values against the chart.
                values = [Fraction(str(v)) for v in values]
                if key == 'data':
                    candidates.setdefault((group['label'], True), []).append((path, unit[1] if unit else None, sorted(values)))
                candidates.setdefault((group['label'], False), []).append((path, None, box_five({key: values})))
        if not candidates:
            return
        for name, raw, unit, values, path in box_table_rows(blocks):
            matches = [c for c in candidates.get((name, raw), []) if not (unit and c[1] and unit != c[1])]
            local = [c for c in matches if c[0].split('.', 1)[0] == path.split('.', 1)[0]]
            matches = local or matches
            if len(matches) != 1:
                continue
            chart, _, expected = matches[0]
            if raw and sorted(values) != expected:
                self.warn(path + '[1]', f'{name}의 자료 목록이 상자그림({chart})의 자료와 다릅니다.',
                          '같은 값이 몇 번 나오는지도 표와 groups[].data에서 맞추세요.', 'box-table-data-mismatch')
            elif not raw:
                for i, (value, target) in enumerate(zip(values, expected), 1):
                    if value != target:
                        self.warn(f'{path}[{i}]', f'표와 상자그림({chart})에서 {name}의 {BOX_FIVE_HEADERS[i-1]} 값이 다릅니다.',
                                  '표의 값(빈칸이면 정답)과 상자그림의 자료에서 구한 다섯 값을 맞추세요.',
                                  'box-table-five-mismatch')

    def chart(self, block, path):
        kind = block.get('kind')
        if kind == 'box':
            self.box_chart(block, path)
            return
        if kind not in ('bar', 'line', 'histogram', 'polygon', 'scatter'):
            self.error(path + '.kind', '그래프 종류를 알 수 없습니다.', 'bar, line, histogram, polygon, scatter, box 중 하나를 쓰세요.')
            return
        for key in ('groups', 'pen', 'guides'):
            if key in block:
                self.error(f'{path}.{key}', '이 필드는 상자그림 전용입니다.', 'kind가 box인 블록에서만 쓰세요.')
        self.bools(block, path, 'teacher')
        for name in ('width_mm', 'height_mm', 'y_step', 'x_step'):
            if name in block:
                self.positive(block[name], path + '.' + name)
        if 'y_max' in block and not is_number(block['y_max']):
            self.error(path + '.y_max', '유한한 수가 필요합니다.', '세로축 최댓값을 숫자로 쓰세요.')
        elif kind in ('histogram', 'polygon') and 'y_max' in block and block['y_max'] <= 0:
            self.error(path + '.y_max', '도수 그래프의 세로축 최댓값은 양수여야 합니다.',
                       '0보다 큰 y_max를 쓰세요.', 'frequency-y-max')
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
                else:
                    widths = [b - a for a, b in zip(bins, bins[1:])]
                    if any(not math.isclose(width, widths[0], rel_tol=1e-9, abs_tol=1e-9) for width in widths[1:]):
                        self.warn(path + '.bins', '계급의 폭이 서로 다릅니다.', '같은 계급 폭을 쓰거나 그래프의 뜻을 확인하세요.', 'unequal-bin-width')
                label = block.get('y_label', '')
                relative = isinstance(label, str) and ('상대도수' in label or '비율' in label)
                self.frequencies(values, path + '.values', relative)
                plot = block.get('plot')
                if isinstance(plot, dict):
                    self.frequencies(plot.get('answer'), path + '.plot.answer', relative)
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
                              '엔진 수정 전에는 음수가 아닌 자료를 사용하거나 화면을 별도로 확인하세요.', 'negative-bar')
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
                self.figure(block['figure'], path + '.figure', placement='situation')
        elif kind == 'nameband':
            text('label')
        elif kind in ('question', 'ask', 'text'):
            if kind == 'ask':
                text('tag')
            parsed = text('text', local=kind in ('question', 'ask'))
            if kind in ('question', 'ask') and parsed and re.match(r'^\s*[0-9]+[.)](?![0-9])', parsed.plain):
                self.warn(path + '.text', '발문 첫머리가 문항 번호처럼 보입니다.', '직접 쓴 번호를 빼고 발문만 남기세요.', 'manual-number')
            if kind == 'question':
                self.bools(block, path, 'number')
                if block.get('number', True) is True:
                    self.numbered.setdefault(item_id, path + '.text')
            if kind == 'ask':
                self.lines(block, path, required=True)
                self.bools(block, path, 'grow')
                text('answer', private=True, expression_visible=True)
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
            if 'unit' in block and not normalized_unit(block['unit']):
                self.error(path + '.unit', '판별에 쓸 수 있는 단위가 아닙니다.',
                           '점, 명, cm처럼 숫자 없이 단위 하나만 쓰거나 unit을 빼세요.', 'table-unit')
            header = self.texts(block.get('header'), path + '.header', field='header[]')
            for i, label in enumerate(header or []):
                if isinstance(label, str) and CATEGORY_HEADER_UNIT.fullmatch(label):
                    self.warn(f'{path}.header[{i}]', '범주를 나타내는 표 머리에 단위가 붙었습니다.',
                              '단위는 양을 나타내는 머리(예: 시간(분))나 표 위 안내에 쓰세요.', 'table-category-unit')
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
                self.figure(figure, f'{path}.items[{i}]', caption_required=True, field='items[].', placement='pair')
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
                self.figure(block['image'], path + '.image', placement='decoration')
                if isinstance(block['image'], dict) and is_number(block['image'].get('width_mm')) and block['image']['width_mm'] > 30:
                    self.error(path + '.image.width_mm', '제목 그림이 너무 넓습니다.', '30mm 이하로 쓰세요.')
        elif kind == 'callout':
            if block.get('kind') not in ('개념', '생각', '주의', '정리', '예시'):
                self.error(path + '.kind', '상자 종류가 맞지 않습니다.', '개념·생각·주의·정리·예시 중 하나를 쓰세요.')
            text('title', False)
            self.texts(block.get('lines'), path + '.lines', field='lines[]')
            if 'image' in block:
                self.figure(block['image'], path + '.image', placement='decoration')
        elif kind == 'split':
            if inside_split or self.columns == 2:
                self.error(path, '2단 안에 split을 겹칠 수 없습니다.', '한 겹의 단만 쓰세요.')
            ratio = block.get('ratio', '1:1')
            if ratio not in SPLIT_RATIOS:
                self.error(path + '.ratio', '지원하지 않는 비율입니다.', '1:1, 2:1, 1:2, 3:2, 2:3 중 하나를 쓰세요.')
                ratio = '1:1'
            original_width = self.content_mm
            widths = split_widths_mm(original_width, ratio) if not inside_split else (original_width, original_width)
            for side, width in zip(('left', 'right'), widths):
                self.content_mm = width
                for i, child in enumerate(self.list(block.get(side), path + '.' + side) or []):
                    self.block(child, f'{path}.{side}[{i}]', item_id, pending_marks, True)
            self.content_mm = original_width
        elif kind == 'ox':
            for i, entry in enumerate(self.list(block.get('items'), path + '.items', 1, 10) or []):
                where = f'{path}.items[{i}]'
                if self.object(entry, where, ('text', 'answer')):
                    self.text(entry.get('text'), where + '.text', field='items[].text',
                              expression_visible=entry.get('answer') != 'X')
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
            defaults_valid = True
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
                    parsed = self.text(param['label'], where + '.label', private=True, term_visible=True)
                    if parsed and parsed.blanks:
                        self.error(where + '.label', '슬라이더 이름에는 빈칸을 넣을 수 없습니다.', '$a$의 값처럼 글과 수식으로 쓰세요.')
                low, high, step, value = (param.get(k) for k in ('min', 'max', 'step', 'value'))
                bounded = is_number(low) and is_number(high) and low < high
                valid_step = is_number(step) and step > 0
                valid_value = is_number(value) and bounded and low <= value <= high
                if not (bounded and valid_step and valid_value):
                    self.error(where, '매개변수 범위가 맞지 않습니다.', 'min < max, 양수 step, 범위 안의 value를 쓰세요.')
                excluded = set()
                for j, v in enumerate(self.list(param.get('except', []), where + '.except', 0) or []):
                    if not is_number(v) or (bounded and not low <= v <= high):
                        self.error(f'{where}.except[{j}]', '건너뛸 값이 범위에 맞지 않습니다.', 'min~max 사이의 수를 쓰세요.')
                    else:
                        excluded.add(v)
                if is_number(value) and value in excluded:
                    self.error(where + '.value', '처음 값은 건너뛸 값과 같을 수 없습니다.', 'except에 없는 값을 쓰세요.')
                    valid_value = False
                if bounded and valid_step and valid_value and not on_step(value, low, step):
                    self.error(where + '.value', '처음 값이 슬라이더 눈금에 없습니다.',
                               'min + k×step인 값을 쓰세요.', 'explore-step')
                    valid_value = False
                defaults_valid = defaults_valid and valid_value
                if valid_name and bounded and valid_step:
                    ranges[name] = (low, high, step, value, excluded)
            snapshots = []
            if 'print' in block:
                for i, snapshot in enumerate(self.list(block['print'], path + '.print', 1, 4) or []):
                    where = f'{path}.print[{i}]'
                    if not self.object(snapshot, where, names):
                        continue
                    valid_snapshot = bool(snapshot) and all(name in ranges for name in snapshot)
                    if not snapshot:
                        self.error(where, '인쇄할 값이 필요합니다.', '매개변수 이름과 값을 한 쌍 이상 쓰세요.')
                    for name, value in snapshot.items():
                        if name in ranges:
                            low, high, step, _, excluded = ranges[name]
                            if not is_number(value) or not low <= value <= high or value in excluded:
                                self.error(f'{where}.{name}', '인쇄할 값이 허용 범위에 없습니다.', 'min~max 사이에서 except에 없는 수를 쓰세요.')
                                valid_snapshot = False
                            elif not on_step(value, low, step):
                                self.error(f'{where}.{name}', '인쇄할 값이 슬라이더 눈금에 없습니다.',
                                           'min + k×step인 값을 쓰세요.', 'explore-step')
                                valid_snapshot = False
                    if valid_snapshot:
                        snapshots.append(snapshot)
            tree = None
            try:
                if not isinstance(block.get('fn'), str):
                    raise ValueError('함수 식이 필요합니다.')
                tree = explore_tree(block['fn'], {'x', *names})
            except ValueError as error:
                self.error(path + '.fn', str(error), 'x와 선언한 매개변수로 식을 쓰세요.', 'explore-expression')
            self.grid({'mode': 'coord', 'x': block.get('x'), 'y': block.get('y'), 'unit_mm': block.get('unit_mm')}, path)
            if 'table' in block:
                xs = []
                x_bounds = block.get('x')
                x_valid = (isinstance(x_bounds, list) and len(x_bounds) == 2 and
                           all(type(v) is int and abs(v) <= 10000 for v in x_bounds) and
                           0 < x_bounds[1] - x_bounds[0] <= 200)
                for i, x in enumerate(self.list(block['table'], path + '.table', 0) or []):
                    if not is_number(x):
                        self.error(f'{path}.table[{i}]', '수만 넣을 수 있습니다.', '표에 넣을 x값을 쓰세요.')
                    elif x_valid and not x_bounds[0] <= x <= x_bounds[1]:
                        self.warn(f'{path}.table[{i}]', f'표의 x={x:g}가 그래프 가로 범위를 벗어납니다.',
                                  'x 범위 안의 표 값을 쓰거나 가로축 범위를 넓히세요.', 'explore-table-x-out-of-range')
                    else:
                        xs.append(x)
                bounds = block.get('y')
                if (xs and tree is not None and len(ranges) == len(params) and params and defaults_valid and
                        isinstance(bounds, list) and len(bounds) == 2 and
                        all(is_number(v) for v in bounds) and bounds[0] < bounds[1]):
                    outside = None
                    for scope in slider_scopes(ranges, snapshots):
                        for x in xs:
                            try:
                                y = function_value(tree, x, scope)
                            except (ValueError, ArithmeticError):
                                continue
                            if y < bounds[0] - 1e-9 or y > bounds[1] + 1e-9:
                                outside = (x, scope, y)
                                break
                        if outside:
                            break
                    if outside:
                        x, scope, y = outside
                        values = ', '.join(f'{key}={value:g}' for key, value in scope.items())
                        self.warn(path + '.table', f'표의 값이 그래프 세로 범위를 벗어납니다: x={x:g}, {values}일 때 y={y:g}.',
                                  '슬라이더를 움직이거나 인쇄할 때 표의 점이 보이도록 y 범위나 표를 조정하세요.',
                                  'explore-table-out-of-range')
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

    def exact_input(self, value):
        return exact_number(self.decimals.get(id(value), str(value)))

    def verify(self, checks, path):
        for i, check in enumerate(self.list(checks, path) or []):
            where = f'{path}[{i}]'
            if not self.object(check, where, ('given', 'holds', 'tolerance')):
                continue
            tolerance = check.get('tolerance', 0)
            try:
                if not is_number(tolerance) or (tolerance := self.exact_input(tolerance)) < 0:
                    raise ValueError
            except (ValueError, ArithmeticError):
                self.error(where + '.tolerance', '허용 오차는 0 이상의 수여야 합니다.',
                           '절대 차이 한도를 JSON 숫자로 쓰세요.', 'verify-expression')
                tolerance = None
            given = check.get('given')
            valid_given = isinstance(given, dict)
            if not valid_given:
                self.error(where + '.given', '대입할 문자와 값의 객체가 필요합니다.', '예: {"x": 2}로 쓰세요.', 'verify-expression')
                given = {}
            exact_given = {}
            for name, value in given.items():
                try:
                    if not isinstance(name, str) or not re.fullmatch(r'[a-z]', name) or not is_number(value):
                        raise ValueError
                    exact_given[name] = self.exact_input(value)
                except (ValueError, ArithmeticError):
                    self.error(f'{where}.given.{name}', '문자 이름 또는 대입값이 맞지 않습니다.',
                               '영문 소문자 한 글자와 유한한 수를 쓰세요.', 'verify-expression')
                    valid_given = False
            for j, equation in enumerate(self.list(check.get('holds'), where + '.holds') or []):
                slot = f'{where}.holds[{j}]'
                try:
                    if not isinstance(equation, str) or equation.count('=') != 1:
                        raise ValueError('등호가 한 개 있는 식을 쓰세요.')
                    left, right = (arithmetic_tree(part, given, exact=True) for part in equation.split('='))
                    used = {node.id for tree in (left, right) for node in ast.walk(tree) if isinstance(node, ast.Name)}
                    if not used <= given.keys():
                        raise ValueError('식에 쓴 모든 문자의 대입값이 필요합니다.')
                    if not valid_given or tolerance is None:
                        continue
                    lhs, rhs = (function_value(tree, 0, exact_given, exact=True) for tree in (left, right))
                    if abs(lhs - rhs) > tolerance:
                        self.error(slot, f'대입한 양변이 다릅니다: {lhs} ≠ {rhs}.',
                                   '정답의 문자별 값과 원래 조건을 함께 확인하세요.', 'verify-failed')
                except (ValueError, TypeError, ArithmeticError, KeyError, RecursionError) as error:
                    self.error(slot, f'대입 검산 식을 계산할 수 없습니다: {error}',
                               '곱셈은 *, 거듭제곱은 **로 쓰고 0으로 나누지 마세요.', 'verify-expression')

    def teacher(self, teacher):
        self.limits = {}
        solutions = set()
        if self.object(teacher, 'teacher', ('memo', 'solutions')):
            self.texts(teacher.get('memo', []), 'teacher.memo', minimum=0, private=True)
            entries = self.list(teacher.get('solutions', []), 'teacher.solutions', 0) or []
            for i, entry in enumerate(entries):
                path = f'teacher.solutions[{i}]'
                groups = ('steps', 'rubric', 'alternatives', 'misconceptions')
                if not self.object(entry, path, ('item', 'answer', 'video', 'verify', *groups)):
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
                              '정답만 두지 말고 steps에 풀이 과정을 쓰세요(필요하면 rubric·misconceptions도).', 'solution-steps-missing')
                self.text(entry.get('answer'), path + '.answer', private=True, term_visible=self.public_solution,
                          expression_visible=True)
                for key in groups:
                    if key in entry:
                        self.texts(entry[key], f'{path}.{key}', minimum=0, private=True,
                                   term_visible=self.public_solution and key in ('steps', 'rubric'),
                                   expression_visible=True)
                if 'verify' in entry:
                    self.verify(entry['verify'], path + '.verify')
                if 'video' in entry:
                    self.video(entry['video'], path + '.video', solution=True)
        for item, path in self.numbered.items():
            if item not in solutions:
                self.warn(path, '번호 있는 발문의 교사용 풀이가 없습니다.', 'teacher.solutions에 이 item의 풀이를 쓰세요.', 'solution-missing')

    def assessment(self, data):
        if self.kind != '평가':
            return
        teacher = data.get('teacher', {})
        entries = teacher.get('solutions', []) if isinstance(teacher, dict) else []
        solutions = ({s['item']: s for s in entries if isinstance(s, dict) and isinstance(s.get('item'), str)}
                     if isinstance(entries, list) else {})

        def scored_rows(blocks, path):
            found = []
            if not isinstance(blocks, list):
                return found
            for i, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                where = f'{path}[{i}]'
                if block.get('type') == 'split':
                    for side in ('left', 'right'):
                        found.extend(scored_rows(block.get(side), where + '.' + side))
                elif block.get('type') == 'answer' and isinstance(block.get('rows'), list):
                    previous = None
                    for j, row in enumerate(block['rows']):
                        if not isinstance(row, dict):
                            previous = None
                            continue
                        group = row.get('group')
                        same = bool(group) and group == previous
                        previous = group
                        if (not same and row.get('kind', 'text') == 'text' and
                                is_number(row.get('points')) and row['points'] > 0):
                            found.append((f'{where}.rows[{j}].points', row['points']))
            return found

        for i, item in enumerate(data.get('items', []) if isinstance(data.get('items'), list) else []):
            if not isinstance(item, dict):
                continue
            rows = scored_rows(item.get('blocks'), f'items[{i}].blocks')
            if not rows:
                continue
            key = item.get('id')
            solution = solutions.get(key, {}) if isinstance(key, str) else {}
            rubric = solution.get('rubric')
            criteria = [v for v in rubric if isinstance(v, str) and v.strip()] if isinstance(rubric, list) else []
            if not criteria:
                self.warn(rows[0][0], '배점 있는 서술 칸에 교사용 채점 기준이 없습니다.',
                          '해당 item의 teacher.solutions에 rubric을 쓰세요.', 'rubric-missing')
            elif len(rows) == 1 and rows[0][1] > 1 and len(criteria) > 1:
                self.warn(rows[0][0], '한 배점 칸에 여러 채점 요소가 묶였습니다.',
                          '채점 요소마다 실제 답 칸과 라벨·배점을 대응시키세요.', 'rubric-coverage')

    def check(self, data):
        if not self.object(data, '$', ('version', 'meta', 'items', 'teacher')):
            return self.issues
        self.integer(data.get('version'), 'version', 1, 1)
        meta = data.get('meta')
        self.kind = meta.get('kind', '수업') if isinstance(meta, dict) else '수업'
        self.columns = meta.get('columns', 1) if isinstance(meta, dict) else 1
        self.content_mm = content_width_mm(self.columns)
        settings = meta if isinstance(meta, dict) else {}
        self.set_text_context(settings)
        self.interactive = settings.get('interactive', self.kind in ('수업', '연습')) is True
        self.public_solution = self.interactive and settings.get('reveal', '시도 후') in ('시도 후', '바로')
        if isinstance(meta, dict):
            title = self.text(meta.get('title'), 'meta.title', private=True, term_visible=True)
            if title and title.blanks:
                self.error('meta.title', '제목에는 빈칸 표기를 넣을 수 없습니다.', '빈칸은 상황이나 발문에 넣으세요.')
            for key in ('term', 'grade', 'subject', 'header_title', 'unit', 'goal'):
                if key in meta and (key != 'header_title' or meta[key] is not None):
                    parsed = self.text(meta[key], 'meta.' + key, empty=key in ('term', 'grade', 'subject', 'unit'),
                                       private=True, term_visible=key != 'goal')
                    if parsed and parsed.blanks:
                        self.error('meta.' + key, '머리표 정보에는 빈칸 표기를 넣을 수 없습니다.', '빈칸은 상황이나 발문에 넣으세요.')
            for i, name in enumerate(self.texts(meta['name_fields'], 'meta.name_fields', minimum=1, maximum=3,
                                                private=True, term_visible=True) or []
                                     if 'name_fields' in meta else []):
                if isinstance(name, str) and '{{' in name:
                    self.error(f'meta.name_fields[{i}]', '이름 항목에는 빈칸 표기를 넣을 수 없습니다.', '라벨만 쓰세요. 쓰기 공간은 자동으로 붙습니다.')
        sheet_blocks = []
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
            self.figure_scales = []
            self.figure_aspects = {}
            blocks = self.list(item.get('blocks'), path + '.blocks') or []
            sheet_blocks.extend(item_blocks(blocks, path + '.blocks'))
            for j, block in enumerate(blocks):
                self.block(block, f'{path}.blocks[{j}]', item_id, marks)
            self.check_figure_scales()
            if self.interactive:
                self.check_screen_drawing(blocks, path + '.blocks')
            for where, mark in marks:
                if mark not in self.local_marks:
                    self.warn(where, '같은 item의 상황·발문에 이 빈칸 기호가 없습니다.',
                              'mark를 같은 item의 빈칸과 맞추거나 필요 없으면 지우세요.', 'blank-mark-unused')
        self.check_box_tables(sheet_blocks)
        self.meta(meta)
        self.teacher(data.get('teacher', {}))
        self.assessment(data)
        return self.issues


def validate(data, folder: Path, *, decimals=None):
    return Validator(folder, decimals).check(data)


def load_plan(folder: Path):
    """Return (parsed manuscript, exact input bytes, validation issues)."""
    raw = (folder / SOURCE).read_bytes()
    decimals = {}

    def remember_decimal(literal):
        # Keep normal JSON types for assembly, but don't round verify's source decimals.
        value = float(literal)
        decimals[id(value)] = literal
        return value

    try:
        data = json.loads(raw.decode('utf-8-sig'), parse_float=remember_decimal)
    except json.JSONDecodeError as error:
        return None, raw, [Issue('error', '$', f'JSON 형식 오류({error.lineno}행 {error.colno}열).',
                                 '쉼표, 큰따옴표, 괄호를 확인하세요.')]
    except UnicodeError:
        return None, raw, [Issue('error', '$', 'UTF-8로 읽을 수 없습니다.', '구성.json을 UTF-8로 저장하세요.')]
    except (ValueError, RecursionError):
        return None, raw, [Issue('error', '$', 'JSON의 깊이 또는 숫자가 너무 큽니다.', '중첩이나 숫자 자릿수를 줄이세요.')]
    return data, raw, validate(data, folder, decimals=decimals)


def print_issues(issues):
    for issue in issues:
        label = '오류' if issue.level == 'error' else '경고'
        code = f' {issue.code}' if issue.code else ''
        print(f'{label}{code} {issue.path}: {issue.message} 수정: {issue.hint}')


def cmd_plan(folder: Path) -> int:
    _, _, issues = load_plan(folder)
    print_issues(issues)
    errors = sum(issue.level == 'error' for issue in issues)
    print(f'구성 검증: 오류 {errors}개, 경고 {len(issues) - errors}개')
    print('다음: 구성.json을 고친 뒤 plan을 다시 실행하세요.' if errors else '다음: build로 HTML을 조립하세요.')
    return int(bool(errors))
