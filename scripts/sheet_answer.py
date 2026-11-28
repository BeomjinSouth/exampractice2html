"""Answer normalization shared with the worksheet's browser engine."""
import base64
from decimal import Decimal
from fractions import Fraction
from functools import reduce
import hashlib
import json
import math
import os
import re
import secrets
import unicodedata

# The units are part of the public answer-cases contract, not a runtime test dependency.
UNITS = ('cm2', 'km2', 'cm3', '마리', '개월', '시간', '그루', '송이', 'cm', 'mm', 'km', 'kg',
         'ml', 'm2', 'm3', '원', '개', '명', '권', '장', '쪽', '번', '회', '배', '살', '세',
         '점', '일', '년', '분', '초', '시', '도', '칸', '줄', '대', 'm', 'g', 'l', '°', '%',
         '가지', '쌍', '켤레', '자루', '벌', '조각', '묶음', '상자', '봉지', '바퀴', '층', '곳', '군데', 'dl')
_UNIT_SUFFIX = re.compile('(' + '|'.join(map(re.escape, sorted(UNITS, key=len, reverse=True))) + r'|[가-힣]{1,3})$')
FRACTION_FORMS = ('기약분수', '분수', '자유')
# Match a request, not a mention of the term. Keep negative clauses local to their own request.
_FRACTION_REQUEST = re.compile(r'(기약분수|분수)\s*로|약분\s*하')
_FORM_OPTIONAL = re.compile(r'지\s*(?:않|말)|(?:이|가)?\s*아니어도|안\s*(?:해도|하여도|써도|나타내도)|필요\s*없')


def fraction_requirement(texts):
    forms = set()
    for text in texts:
        for clause in re.split(r'[.!?。;\n]', text):
            for match in _FRACTION_REQUEST.finditer(clause):
                between = clause[match.end():]
                following = _FRACTION_REQUEST.search(between)
                if not _FORM_OPTIONAL.search(between[:following.start()] if following else between):
                    forms.add(match[1] or '기약분수')
    return next((form for form in FRACTION_FORMS if form in forms), '자유')


def _with_unit(value, parse):
    if value is None:
        return None
    text = re.sub(r'^[a-z]=', '', norm(value), count=1).removeprefix('+')
    if parse is _number:
        text = text.replace('파이', 'π')
    if parts := parse(text):
        return [*parts, '']
    unit = _UNIT_SUFFIX.search(text)
    if unit and unit[0] not in ('파이', '루트') and (parts := parse(text[:unit.start()])):
        return [*parts, '°' if unit[0] == '도' else unit[0]]
    return None


def norm(value):
    text = unicodedata.normalize('NFKC', str(value)).translate(str.maketrans('−‒–—―⁄', '-----/'))
    return re.sub(r'\s+', '', text).lower().rstrip('.。')


def text_candidates(value):
    text = norm(value)
    left, equal, right = text.partition('=')
    if equal and left and right and '=' not in right and not re.search(r'[<>≤≥≠]', text):
        return list(dict.fromkeys((text, f'{right}={left}')))
    return [text] if text else []


def _number(text):
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1].removeprefix('+')
    text = text.replace('pi', 'π')
    pi = ''
    if text.endswith('π'):
        pi, text = 'π', text[:-1]
        if text in ('', '-'):
            text += '1'
        else:
            text = re.sub(r'[×⋅·*]$', '', text)
    elif match := re.fullmatch(r'(-?)([0-9]*)π/([0-9]+)', text):
        pi, text = 'π', f'{match[1]}{match[2] or "1"}/{match[3]}'
    if re.fullmatch(r'-?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?', text):
        text = text.replace(',', '')
    if not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+|/[0-9]+)?', text):
        return None
    try:
        value = Fraction(text)
        if '/' in text:
            form = 'reduced' if int(text.split('/')[1]) == value.denominator else 'unreduced'
        else:
            form = 'decimal' if '.' in text else 'integer'
        return [str(value) + pi, form]
    except (ValueError, ZeroDivisionError):
        return None


def numeric_details(value):
    parts = _with_unit(value, _number)
    return [parts[0], parts[2], parts[1]] if parts else None


def number_parts(value):
    parts = numeric_details(value)
    return parts[:2] if parts else None


def normalized_unit(value):
    unit = norm(value) if isinstance(value, str) else ''
    return '°' if unit == '도' else unit if unit in UNITS else ''


def inferred_unit(tail, column='', row='', table=''):
    # 열 머리 → 행 라벨 → 표 전체 → 빈칸 뒤 순으로 본다.
    for heading in (column, row):
        match = re.search(r'\(([^()]*)\)\s*$', unicodedata.normalize('NFKC', heading))
        if match and (unit := normalized_unit(match[1])):
            return unit
    if unit := normalized_unit(table):
        return unit
    particles = r'(?:으로|로|이다|입니다|이고|이었다|이고요|이며|이므로|이므로서|인|은|는|이|가|을|를|과|와|의|에|에서|까지|부터|씩|만|도)?'
    text = unicodedata.normalize('NFKC', tail).lower().lstrip(' \t')
    for unit in sorted(UNITS, key=len, reverse=True):
        if (unit == '도'
                or (unit == '일' and re.match(r'일\s*(?:때|경우|수(?:\s|$)|지|까|것(?:\s|$))', text))
                or (unit == '분' and re.match(r'분\s*의', text))
                or (unit == '점' and re.match(r'점\s*에서\s*(?:만나|만난|만날|만났)', text))
                or (unit == '가지' and re.match(r'가지\s*(?:말|않|못)', text))):
            continue
        if re.match(re.escape(unit) + particles + r'(?![\w])', text):
            return unit
    return ''


def number_forms(values, unit=''):
    parts = [number_parts(value) for value in values]
    if not all(parts):
        return []
    inferred = '' if any(u for _, u in parts) else unit
    return list(dict.fromkeys([n for n, _ in parts] + [f'{n}@{u or inferred}' for n, u in parts if u or inferred]))


_ROOT = r'([0-9]{0,128})√(?:([0-9]{1,128})|\(([0-9]{1,128})\))'
_ROOT_NUMERATOR = re.compile(r'(-?)' + _ROOT + r'(?:/([0-9]{1,128}))?')
_ROOT_DENOMINATOR = re.compile(r'(-?)([0-9]{1,128})/(?:' + _ROOT + r'|\(' + _ROOT + r'\))')


def _radical(text):
    text = text.replace('루트', '√')
    if match := _ROOT_NUMERATOR.fullmatch(text):
        sign, coefficient, bare, grouped, denominator = match.groups()
        root = bare or grouped
        top, bottom = int(coefficient or '1') ** 2 * int(root), int(denominator or '1') ** 2
        shape = f'{sign}{coefficient}√{root}' + (f'/{denominator}' if denominator else '')
    elif match := _ROOT_DENOMINATOR.fullmatch(text):
        sign, numerator, c1, r1, g1, c2, r2, g2 = match.groups()
        coefficient, root = c1 or c2 or '', r1 or g1 or r2 or g2
        top, bottom = int(numerator) ** 2, int(coefficient or '1') ** 2 * int(root)
        shape = f'{sign}{numerator}/{coefficient}√{root}'
    else:
        return None
    if not bottom:
        return None
    # Signed exact squares compare even irrational values without rounding.
    return [shape, f'square:{Fraction(-top if sign else top, bottom)}']


def radical_parts(value):
    return _with_unit(value, _radical)


def writing_forms(kind, values, unit=''):
    if kind == 'number':
        forms = number_forms(values, unit)
        return forms + [f'written:{norm(value)}' for value in values] if forms else []
    if kind == 'radical':
        parts = [radical_parts(value) for value in values]
        if not all(parts):
            return []
        inferred = '' if any(u for _, _, u in parts) else unit
        return list(dict.fromkeys(v for shape, square, u in parts for key in (shape, square)
                                  for v in ((key, f'{key}@{u or inferred}') if u or inferred else (key,))))
    return [norm(value) for value in values]


def writing_result(kind, value, matches, form='', check_unit=False):
    """Return [verdict, notice] without changing the public sheet.answer verdict API."""
    if value is None or not str(value).strip():
        return [None, '']
    if kind == 'text':
        return [True if any(map(matches, text_candidates(value))) else 'review', '']
    if kind not in ('radical', 'number'):
        return [None, '']
    parts = radical_parts(value) if kind == 'radical' else numeric_details(value)
    if not parts:
        return [False, '']
    key, alternate, unit = parts if kind == 'radical' else (parts[0], parts[2], parts[1])

    def match_unit(candidate):
        return matches(f'{candidate}@{unit}' if check_unit and unit else candidate)

    exact_shape = match_unit(key)
    if not exact_shape and (kind == 'number' or not match_unit(alternate)):
        return [False, '']
    if unit and not check_unit:
        return ['review', 'unit']
    if kind == 'radical':
        return [True, ''] if exact_shape else ['review', 'radical']
    if matches(f'written:{norm(value)}'):
        return [True, '']
    if form == '기약분수' and alternate != 'reduced':
        return [False, 'reduced']
    if form == '분수' and alternate != 'reduced' and alternate != 'unreduced':
        return ['review', 'fraction']
    return [True, 'reduce' if alternate == 'unreduced' else '']


def input_mode(values):
    texts = [norm(value) for value in values]
    if not all(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', text) for text in texts):
        return ''
    return 'decimal' if any('.' in text for text in texts) else 'numeric'


def num(value):
    parts = number_parts(value)
    return parts[0] if parts is not None else None


def plain(source):
    """Use the same math parse that produces the rendered answer."""
    from sheet_markup import MarkupError, inspect_markup
    text = str(source)
    try:
        return inspect_markup(text, answer=True).plain.replace('−', '-')
    except MarkupError:
        # Plan also inspects invalid fields to give a location; don't invent a value.
        return text


def snapped(value, snap=None):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('유한한 수가 필요합니다.')
    if snap is not None:
        if not math.isfinite(float(snap)) or snap <= 0:
            raise ValueError('snap은 양수여야 합니다.')
        quotient = value / snap
        if not math.isfinite(quotient):
            raise ValueError('snap에 비해 수가 너무 큽니다.')
        lower = math.floor(quotient)
        value = float(f'{(lower + (quotient - lower >= 0.5)) * snap:.6f}')
    if value == 0:
        return '0'
    if 1e-6 <= abs(value) < 1e21:
        text = format(Decimal(str(value)), 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
    else:
        text = str(value)
    return num(text)


def points(value, snap=None):
    try:
        result = []
        for point in value:
            values = point if isinstance(point, (list, tuple)) else [point]
            if len(values) not in (1, 2):
                return None
            row = [snapped(v, snap) for v in values]
            if any(v is None for v in row):
                return None
            result.append(','.join(row))
        return ';'.join(sorted(result)) if result else None
    except (TypeError, ValueError, OverflowError):
        return None


def line(value, snap=None):
    try:
        if len(value) != 2 or any(len(p) != 2 for p in value):
            return None
        (x1, y1), (x2, y2) = [[Fraction(snapped(v, snap)) for v in point] for point in value]
        a, b = y2 - y1, x1 - x2
        if not (a or b):
            return None
        c = a * x1 + b * y1
        multiple = math.lcm(a.denominator, b.denominator, c.denominator)
        numbers = [int(v * multiple) for v in (a, b, c)]
        gcd = reduce(math.gcd, numbers)
        numbers = [v // gcd for v in numbers]
        if next(v for v in numbers[:2] if v) < 0:
            numbers = [-v for v in numbers]
        return ','.join(map(str, numbers))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def bars(value, snap=None):
    try:
        if not value or any(v is None for v in value):
            return None
        result = [snapped(v, snap) for v in value]
        return ','.join(result) if all(v is not None for v in result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def canonical(kind, value, snap=None, left=None):
    if kind == 'number':
        return num(value)
    if kind == 'text':
        return norm(value)
    if kind == 'choice':
        try:
            return ','.join(str(v) for v in sorted(set(value))) if value else None
        except TypeError:
            return None
    if kind == 'ox':
        return {'o': 'o', '○': 'o', 'x': 'x', '×': 'x'}.get(norm(value))
    if kind == 'order':
        return ','.join(value) if isinstance(value, list) and value else None
    if kind == 'match':
        if not isinstance(value, dict):
            return None
        keys = left if left is not None else list(value)
        return ','.join(f'{key}:{value[key]}' for key in keys) if keys and all(value.get(k) for k in keys) else None
    if kind == 'points':
        return points(value, snap)
    if kind == 'line':
        return line(value, snap)
    if kind == 'bars':
        return bars(value, snap)
    return None


def check_hash(salt, kind, normalized):
    return hashlib.sha256(f'{salt}|{kind}|{normalized}'.encode('utf-8')).hexdigest()


def reveal(salt, payload):
    key = hashlib.sha256((salt + '|reveal').encode('utf-8')).digest()
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return base64.b64encode(bytes(byte ^ key[i % len(key)] for i, byte in enumerate(raw))).decode('ascii')


def make_salt():
    return os.environ['SHEET_TEST_SALT'] if 'SHEET_TEST_SALT' in os.environ else secrets.token_hex(16)
