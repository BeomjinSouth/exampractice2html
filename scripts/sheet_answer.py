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
_POWER_UNITS = {f'{unit[:-1]}^{unit[-1]}': unit for unit in UNITS if unit.endswith(('2', '3'))}
_UNIT_PATTERN = '|'.join(map(re.escape, sorted((*UNITS, *_POWER_UNITS), key=len, reverse=True)))
_KNOWN_UNIT_SUFFIX = re.compile(f'(?:{_UNIT_PATTERN})$')
_UNIT_SUFFIX = re.compile(rf'(?:{_UNIT_PATTERN}|[가-힣]{{1,3}})$')
FRACTION_FORMS = ('기약분수', '분수', '자유')
RADICAL_FORMS = ('유리화', '자유')
_FRACTION_REQUEST = re.compile(r'(기약분수|분수)\s*(?:로|(?:가|이)\s*되도록)|약분\s*(?=하|해|안)')
_RADICAL_REQUEST = re.compile(r'분모\s*(?:를\s*유리화(?=\s*(?:하|해|안|$))|의\s*유리화(?=\s*를\s*하))')
_FORM_OPTIONAL = re.compile(r'지\s*(?:않|말)|아니어도|안\s*(?:해도|하여도|써도|나타내도)|필요\s*(?:가\s*)?없|'
                            r'(?:해|하여|써|나타내)도\s*(?:된|됩|좋|괜찮|무방)')
_FORM_CHOICE = re.compile(r'또는|혹은|거나')
_CLAUSE_JOIN = re.compile(r'(?:나타내|표현하|정리하|구하|유리화하|쓰|적)(?:고|며)(?=\s)')


def _required_forms(texts, pattern):
    # A choice requires only the form common to all alternatives; negation is local.
    fraction = pattern is _FRACTION_REQUEST
    for text in texts:
        for clause in re.split(r'[.!?。;,\n]', _CLAUSE_JOIN.sub(r'\g<0>\n', text)):
            arms = _FORM_CHOICE.split(clause)
            options = []
            for arm in arms:
                requests = list(pattern.finditer(arm))
                forms = set()
                for i, match in enumerate(requests):
                    after = arm[match.end():requests[i + 1].start() if i + 1 < len(requests) else None]
                    if not _FORM_OPTIONAL.search(after):
                        forms.add((match[1] or '기약분수') if fraction else '유리화')
                if not requests and fraction and len(arms) > 1:
                    # The first option can omit '로 쓰시오': 기약분수 또는 분수로 쓰시오.
                    if noun := re.search(r'(기약분수|분수)\s*$', arm):
                        forms.add(noun[1])
                options.append(forms)
            if len(options) == 1:
                yield from options[0]
            elif all(options):
                yield ('기약분수' if all('기약분수' in forms for forms in options) else '분수') if fraction else '유리화'


def fraction_requirement(texts):
    forms = set(_required_forms(texts, _FRACTION_REQUEST))
    return next((form for form in FRACTION_FORMS if form in forms), '자유')


def radical_requirement(texts):
    return next(_required_forms(texts, _RADICAL_REQUEST), '자유')


def _ungroup(text):
    if not (text.startswith('(') and text.endswith(')')):
        return text
    depth = 0
    for i, char in enumerate(text):
        depth += (char == '(') - (char == ')')
        if depth == 0:
            return text[1:-1].removeprefix('+') if i == len(text) - 1 else text
    return text


def _with_unit(value, parse, authored=False):
    if value is None:
        return None
    text = re.sub(r'^[a-z]=', '', norm(value), count=1).removeprefix('+')
    bare = _ungroup(text)
    if parts := parse(bare):
        return [*parts, '', bare]
    unit = (_KNOWN_UNIT_SUFFIX if authored else _UNIT_SUFFIX).search(text)
    if unit and unit[0] not in ('파이', '루트'):
        bare = _ungroup(text[:unit.start()])
        if parts := parse(bare):
            return [*parts, normalized_unit(unit[0]) or unit[0], bare]
    return None


def norm(value):
    # Preserve the exponent before compatibility normalization can flatten it (2³ ≠ 23).
    source = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾]+', r'^\g<0>', str(value))
    text = unicodedata.normalize('NFKC', source).translate(str.maketrans('−‒–—―⁄', '-----/'))
    return re.sub(r'\s+', '', text).lower().rstrip('.。')


def text_candidates(value):
    text = norm(value)
    left, equal, right = text.partition('=')
    if equal and left and right and '=' not in right and not re.search(r'[<>≤≥≠]', text):
        return list(dict.fromkeys((text, f'{right}={left}')))
    return [text] if text else []


def _number(text):
    text = text.replace('파이', 'π').replace('pi', 'π')
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


def numeric_details(value, *, authored=False):
    parts = _with_unit(value, _number, authored)
    return [parts[0], parts[2], parts[1]] if parts else None


def qualified_number(value):
    # These short answer labels are meaningful text, not units. Allow the bare value separately.
    text = norm(value)
    match = re.fullmatch(r'약(.+)|(.+?)(?:이상|이하|초과|미만|번째|쯤|정도|가량|약)', text)
    parts = numeric_details(match[1] or match[2], authored=True) if match else None
    return parts[0] if parts else None


def number_parts(value):
    parts = numeric_details(value)
    return parts[:2] if parts else None


def normalized_unit(value):
    unit = norm(value) if isinstance(value, str) else ''
    return '°' if unit == '도' else unit if unit in UNITS else _POWER_UNITS.get(unit, '')


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
    parts = [numeric_details(value, authored=True) for value in values]
    if not all(parts):
        return []
    inferred = '' if any(u for _, u, _ in parts) else unit
    return list(dict.fromkeys([n for n, _, _ in parts] + [f'{n}@{u or inferred}' for n, u, _ in parts if u or inferred]))


_ROOT = r'([0-9]{0,128})√(?:([0-9]{1,128})|\(([0-9]{1,128})\))'
_ROOT_NUMERATOR = re.compile(r'(-?)' + _ROOT + r'(?:/([0-9]{1,128}))?')
_ROOT_DENOMINATOR = re.compile(r'(-?)([0-9]{1,128})/(?:' + _ROOT + r'|\(' + _ROOT + r'\))')
_ROOT_COEFFICIENT = re.compile(r'\(([+-]?)([0-9]{1,128})/([0-9]{1,128})\)√')


def _radical(text):
    text = text.replace('루트', '√')
    if coefficient := _ROOT_COEFFICIENT.match(text):
        sign, numerator, denominator = coefficient.groups()
        text = ('-' if sign == '-' else '') + (numerator if numerator != '1' else '') + '√' + text[coefficient.end():] + '/' + denominator
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


def radical_parts(value, *, authored=False):
    parts = _with_unit(value, _radical, authored)
    return parts[:3] if parts else None


_QUALIFIED = '\0qualified:'


def writing_forms(kind, values, unit='', *, auto=False):
    if kind == 'number':
        forms = number_forms(values, unit)
        return forms + [f'written:{_with_unit(value, _number, True)[3]}' for value in values] if forms else []
    if kind == 'radical':
        parts = [radical_parts(value, authored=True) for value in values]
        if not all(parts):
            return []
        inferred = '' if any(u for _, _, u in parts) else unit
        return list(dict.fromkeys(v for shape, square, u in parts for key in (shape, square)
                                  for v in ((key, f'{key}@{u or inferred}') if u or inferred else (key,))))
    forms = [norm(value) for value in values]
    if auto and values and qualified_number(values[0]) is not None:
        forms += [_QUALIFIED, *(f'{_QUALIFIED}{number}' for value in values
                                if (number := qualified_number(value)) is not None)]
    return forms


def writing_result(kind, value, matches, form='', check_unit=False):
    """Return [verdict, notice] without changing the public sheet.answer verdict API."""
    if value is None or not str(value).strip():
        return [None, '']
    if kind == 'text':
        if any(map(matches, text_candidates(value))):
            return [True, '']
        if matches(_QUALIFIED):
            parts = number_parts(value)
            return [bool(parts and not parts[1] and matches(_QUALIFIED + parts[0])), '']
        return ['review', '']
    if kind not in ('radical', 'number'):
        return [None, '']
    radical = kind == 'radical'
    parts = _with_unit(value, _radical if radical else _number)
    if not parts:
        return [False, '']
    key, alternate, unit, written = parts
    if not (matches(key) or radical and matches(alternate)):
        return [False, '']
    if unit:
        if not check_unit:
            return ['review', 'unit']
        if not (matches(f'{key}@{unit}') or radical and matches(f'{alternate}@{unit}')):
            return [False, 'wrong-unit']
    if radical:
        if re.search(r'/[0-9]*√', key):
            return [False, 'rationalize'] if form == '유리화' else [True, 'rationalize-tip']
        return [True, ''] if matches(f'{key}@{unit}' if unit else key) else ['review', 'radical']
    if matches(f'written:{written}'):
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
