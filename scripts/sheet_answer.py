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
         '점', '일', '년', '분', '초', '시', '도', '칸', '줄', '대', 'm', 'g', 'l', '°', '%')


def norm(value):
    text = unicodedata.normalize('NFKC', str(value)).translate(str.maketrans('−‒–—―⁄', '-----/'))
    return re.sub(r'\s+', '', text).lower().rstrip('.。')


def text_candidates(value):
    text = norm(value)
    left, equal, right = text.partition('=')
    if equal and left and right and '=' not in right and not re.search(r'[<>≤≥≠]', text):
        return list(dict.fromkeys((text, f'{right}={left}')))
    return [text] if text else []


def numeric_details(value):
    text = re.sub(r'^[a-z]=', '', norm(value), count=1)
    suffix = ''
    for unit in sorted(UNITS, key=len, reverse=True):
        if text.endswith(unit) and len(text) > len(unit):
            text = text[:-len(unit)]
            suffix = '°' if unit == '도' else unit
            break
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    if text.startswith('+'):
        text = text[1:]
    text = text.replace('pi', 'π').replace('파이', 'π')
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
        return [str(value) + pi, suffix, form]
    except (ValueError, ZeroDivisionError):
        return None


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
                or (unit == '점' and re.match(r'점\s*에서\s*(?:만나|만난|만날|만났)', text))):
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


def radical_parts(value):
    text = norm(value).replace('루트', '√')
    match = re.fullmatch(r'(-?)([0-9]{0,128})√(?:([0-9]{1,128})|\(([0-9]{1,128})\))', text)
    if not match:
        return None
    sign, coefficient, radicand, grouped = match.groups()
    radicand = radicand or grouped
    square = int(coefficient or '1') ** 2 * int(radicand)
    # Keep the sign when comparing squares so √12 and −√12 never match.
    return [f'{sign}{coefficient}√{radicand}', f'square:{-square if sign else square}']


def writing_forms(kind, values, unit=''):
    if kind == 'number':
        return number_forms(values, unit)
    if kind == 'radical':
        return [form for value in values for form in radical_parts(value)]
    return [norm(value) for value in values]


def writing_result(kind, value, matches, form='', strict=False, check_unit=False):
    if value is None or value == '':
        return None
    if kind == 'text':
        return None if not str(value).strip() else True if any(map(matches, text_candidates(value))) else 'review'
    if kind == 'radical':
        parts = radical_parts(value)
        return False if not parts else True if matches(parts[0]) else 'review' if matches(parts[1]) else False
    if kind == 'number':
        parts = numeric_details(value)
        if not parts:
            return False
        number, unit, written = parts
        if not matches(f'{number}@{unit}' if check_unit and unit else number):
            return False
        if form == 'fraction':
            if strict:
                return written == 'reduced'
            if written == 'unreduced':
                return 'review'
        return True
    return None


def input_mode(values):
    texts = [norm(value) for value in values]
    if not all(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', text) for text in texts):
        return ''
    return 'decimal' if any('.' in text for text in texts) else 'numeric'


def num(value):
    parts = number_parts(value)
    return parts[0] if parts is not None else None


def plain(source):
    """Turn author markup into the plain-text convention in answer-cases.json."""
    text = str(source)
    escapes = []

    def protect(match):
        escapes.append(match.group(1))
        return f'\ue000{len(escapes)-1}\ue001'

    text = re.sub(r'\\([$\{\}\\])', protect, text)
    text = text.replace('$', '').replace('**', '').replace('~', '')
    text = re.sub(r'\\(?:,| )', '', text)
    commands = {'times': '×', 'div': '÷', 'le': '≤', 'ge': '≥', 'ne': '≠', 'pi': 'π',
                'angle': '∠', 'circ': '°', 'pm': '±', 'cdot': '⋅', 'approx': '≈',
                'triangle': '△', 'square': '□', 'perp': '⊥', 'parallel': '⫽',
                'equiv': '≡', 'sim': '∽', 'therefore': '∴', 'because': '∵', 'cdots': '⋯',
                'left': '', 'right': '', 'quad': ' ', 'sin': 'sin ', 'cos': 'cos ', 'tan': 'tan '}
    # TeX command names consume the whitespace immediately following the name.
    text = re.sub(r'\\([A-Za-z]+)\s*', lambda m: commands.get(m.group(1), '\\' + m.group(1)), text)
    text = re.sub(r'\^(?:\{°\}|°)', '°', text)
    fraction = re.compile(r'\\(?:d?frac)\{([^{}]*)\}\{([^{}]*)\}')
    while fraction.search(text):
        text = fraction.sub(r'\1/\2', text)
    group = re.compile(r'\\(sqrt|text|mathit|mathrm|overline|overarc|overgroup)\{([^{}]*)\}')
    while group.search(text):
        text = group.sub(lambda m: ('√' + (m[2] if re.fullmatch(r'[0-9]+', m[2]) else f'({m[2]})')
                                    if m[1] == 'sqrt' else m[2]), text)
    text = re.sub(r'\\sqrt\[([^\[\]]*)\]\{([^{}]*)\}', r'√[\1](\2)', text)
    text = re.sub(r'\\[A-Za-z]+', '', text).replace('{', '').replace('}', '')
    return re.sub(r'\ue000([0-9]+)\ue001', lambda m: escapes[int(m.group(1))], text)


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
