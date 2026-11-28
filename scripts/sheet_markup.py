"""Render the inline notation shared with assets/engine/sheet.js."""
import html
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass

# Set only while rendering a student's item; plan and teacher notes stay pure.
blank_hook = ContextVar('sheet_blank_hook', default=None)
from typing import Optional


class MarkupError(ValueError):
    pass


SYMBOLS = dict(zip(
    'times div pm le ge ne approx cdot angle triangle square perp parallel equiv sim pi circ therefore because cdots'.split(),
    '×÷±≤≥≠≈⋅∠△□⊥⫽≡∽π°∴∵⋯'))
SUPERS = str.maketrans('0123456789+-=()nix', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱˣ')
SUBS = str.maketrans('0123456789+-=()aeiox', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒₓ')
DECIMAL = re.compile(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)')
LETTERS = re.compile(r'[A-Za-z]+')
NEWLINE = re.compile(r'[\r\n\v\f\x85  ]')
MARKS = '㉠㉡㉢㉣㉤㉥㉦㉧㉨㉩㉪㉫㉬㉭㉮㉯㉰㉱㉲㉳㉴㉵㉶㉷㉸㉹㉺㉻'
MATH_SPACES = {',': '0.1667em', ' ': '0.1667em', ':': '0.2222em', ';': '0.2778em', '!': '0em'}


def join_parts(parts):
    return ''.join(p[0] for p in parts), ''.join(p[1] for p in parts)


def wrap_row(parts):
    rendered, plain = join_parts(parts)
    return (rendered if len(parts) == 1 else f'<mrow>{rendered}</mrow>', plain)


def fraction_term(text, *, denominator=False):
    """Group a term if it has an operator outside an already grouped atom."""
    text = text.strip()
    # A denominator product must not look like a factor after the fraction.
    if denominator and re.match(r'[+−-]?[0-9.]+\s*[π√]', text):
        return f'({text})'
    depth = 0
    for i, char in enumerate(text):
        if char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        elif depth == 0 and (char in '/×÷⋅·*=<>≤≥≠±,∽≈≡' or (i and char in '+−-')):
            if char == ',' and re.fullmatch(r'[+−-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?', text):
                continue
            return f'({text})'
    return text


def is_math_space(part):
    return part[0].startswith('<mspace ')


def fraction_operand(text, parts, *, denominator):
    grouped = fraction_term(text, denominator=denominator)
    # A product numerator reads the same either way (2x/3), so only a denominator needs it.
    if grouped != text.strip() or not denominator:
        return grouped
    factors = [part for part in parts if not is_math_space(part)]
    if factors and factors[0][0].startswith('<mo form="prefix"') and factors[0][1] in ('+', '−'):
        factors = factors[1:]
    # 1/(2x) must not read as (1/2)x.
    return f'({grouped})' if len(factors) > 1 else grouped


class MathParser:
    def __init__(self, source, *, answer=False, label=False):
        self.source, self.pos, self.answer = source, 0, answer
        self.structured_plain = answer or label

    @property
    def next(self):
        return self.source[self.pos:self.pos + 1]

    def fail(self, message, at=None):
        index = self.pos if at is None else at
        raise MarkupError(f'수식 ${self.source}$ · {index + 1}번째 글자: {message}')

    def spaces(self):
        while self.next:
            if self.next.isspace():
                self.pos += 1
            elif match := re.match(r'\\(?:left(?=\s*[(|])|right(?=\s*[)|]))', self.source[self.pos:]):
                self.pos += match.end()
            else:
                break

    def group(self, opening='{', close='}', *, denominator=None):
        self.spaces()
        if self.next != opening:
            self.fail(f'{opening}가 필요합니다')
        self.pos += 1
        parts = self.sequence(close)
        if self.next != close:
            self.fail(f'닫는 {close}가 없습니다')
        if not parts:
            self.fail(f'{opening}{close} 안에 값이 없습니다')
        self.pos += 1
        rendered, plain = wrap_row(parts)
        if self.structured_plain and denominator is not None:
            plain = fraction_operand(plain, parts, denominator=denominator)
        return rendered, plain

    @staticmethod
    def prefix_sign(sign):
        return f'<mo form="prefix" lspace="0em" rspace="0em">{sign}</mo>', sign

    def argument(self):
        self.spaces()
        if not self.next:
            self.fail('첨자에 값이 필요합니다')
        if self.next == '{':
            return self.group()
        if self.next in '0123456789':
            digit = self.next
            self.pos += 1
            return f'<mn>{digit}</mn>', digit
        part = self.atom()
        return self.prefix_sign(part[1]) if part[0] in ('<mo>+</mo>', '<mo>−</mo>') else part

    def command(self, start):
        if self.next in MATH_SPACES:
            width = MATH_SPACES[self.next]
            self.pos += 1
            return f'<mspace width="{width}"></mspace>', ''
        match = LETTERS.match(self.source, self.pos)
        if not match:
            self.fail('명령 이름이 필요합니다', start)
        name = match[0]
        self.pos = match.end()
        if name in SYMBOLS:
            symbol = SYMBOLS[name]
            if name in ('triangle', 'square'):
                return f'<mi mathvariant="normal">{symbol}</mi>', symbol
            tag = 'mi' if name == 'pi' else 'mo'
            return f'<{tag}>{symbol}</{tag}>', symbol
        if name in ('sin', 'cos', 'tan'):
            return f'<mo form="prefix" lspace="0em" rspace="0.1667em">{name}</mo>', name + ' '
        if name == 'quad':
            return '<mspace width="1em"></mspace>', '' if self.structured_plain else ' '
        if name == 'begin':
            self.spaces()
            if not self.source.startswith('{cases}', self.pos):
                self.fail('지원하는 환경은 cases입니다', start)
            self.pos += len('{cases}')
            rows, texts = [], []
            while True:
                left = self.sequence(case_row=True)
                if self.next == '&':
                    self.pos += 1
                    right = self.sequence(case_row=True)
                    if not right:
                        self.fail('& 뒤에 식이 필요합니다')
                    left.extend(right)
                if not left:
                    self.fail('cases의 각 줄에 식을 쓰세요')
                markup, text = join_parts(left)
                rows.append(f'<mtr><mtd>{markup}</mtd></mtr>')
                texts.append(text)
                if self.source.startswith(r'\end{cases}', self.pos):
                    self.pos += len(r'\end{cases}')
                    break
                if not self.source.startswith(r'\\', self.pos):
                    self.fail('cases의 줄 구분이나 닫는 명령을 확인하세요')
                self.pos += 2
            return ('<mrow><mo stretchy="true">{</mo><mtable columnalign="left">'
                    + ''.join(rows) + '</mtable></mrow>', '{' + '; '.join(texts))
        if name in ('frac', 'dfrac'):
            a, b = self.group(denominator=False), self.group(denominator=True)
            plain = f'{a[1]}/{b[1]}' if self.structured_plain else f'({a[1]})/({b[1]})'
            return f'<mfrac>{a[0]}{b[0]}</mfrac>', plain
        if name == 'sqrt':
            self.spaces()
            index = self.group('[', ']') if self.next == '[' else None
            rendered, plain = self.group()
            if index:
                return f'<mroot>{rendered}{index[0]}</mroot>', f'√[{index[1]}]({plain})'
            if self.answer:
                plain = plain.strip()
                simple = re.fullmatch(r'[0-9]+', plain)
            else:
                simple = len(plain) == 1
            return f'<msqrt>{rendered}</msqrt>', '√' + (plain if simple else f'({plain})')
        if name == 'mathrm':
            rendered, plain = self.group()
            return f'<mstyle mathvariant="normal">{rendered}</mstyle>', plain
        if name in ('overline', 'overarc', 'overgroup'):
            rendered, plain = self.group()
            if name == 'overline':
                return f'<mrow class="seg">{rendered}</mrow>', plain if self.answer else plain + '̅'
            return f'<mover>{rendered}<mo stretchy="true">⏜</mo></mover>', plain if self.answer else plain + '⌒'
        if name in ('text', 'mathit'):
            self.spaces()
            if self.next != '{':
                self.fail(f'\\{name} 뒤에 {{글자}}가 필요합니다')
            begin = self.pos + 1
            end = self.source.find('}', begin)
            value = self.source[begin:end]
            if end < 0 or '{' in value:
                self.fail(f'\\{name}의 중괄호를 확인하세요')
            self.pos = end + 1
            if name == 'mathit':
                if not re.fullmatch('[A-Za-z]', value):
                    self.fail('\\mathit에는 영문 한 글자를 쓰세요', begin)
                return f'<mi>{value}</mi>', value
            return f'<mtext>{html.escape(value)}</mtext>', value
        self.fail(f'모르는 명령 \\{name}입니다', start)

    def atom(self):
        self.spaces()
        c = self.next
        if not c:
            self.fail('값이 필요합니다')
        start = self.pos
        self.pos += 1
        if c == '\\':
            return self.command(start)
        if c == '~':
            return '<mspace width="0.3333em"></mspace>', ''
        if c == '{':
            self.pos = start
            return self.group()
        if c in '(|':
            close = ')' if c == '(' else '|'
            parts = self.sequence(close)
            if self.next != close:
                self.fail(f'닫는 {close}가 없습니다')
            self.pos += 1
            rendered, plain = wrap_row(parts)
            attr = ' stretchy="false"' if c == '(' else ''
            return f'<mrow><mo{attr}>{c}</mo>{rendered}<mo{attr}>{close}</mo></mrow>', c + plain + close
        if LETTERS.fullmatch(c):
            attr = ' mathvariant="normal"' if c.isupper() else ''
            return f'<mi{attr}>{c}</mi>', c
        if c in '0123456789.':
            match = re.match(r'[0-9]+(?:\{,\}[0-9]+)+(?:\.[0-9]*)?', self.source[start:])
            if match:
                self.pos = start + match.end()
                number = match[0].replace('{,}', ',')
                return f'<mn>{number}</mn>', number
            match = DECIMAL.match(self.source, start)
            if match:
                self.pos = match.end()
                return f'<mn>{match[0]}</mn>', match[0]
            return '<mo>.</mo>', '.'
        if c in '+-=<>,!':
            value = '−' if c == '-' else c
            return f'<mo>{html.escape(value)}</mo>', value
        self.fail(f'지원하지 않는 글자 {c!r}입니다', start)

    def sequence(self, close=None, *, case_row=False):
        result = []
        while True:
            self.spaces()
            c = self.next
            if case_row and (c == '&' or self.source.startswith((r'\\', r'\end{cases}'), self.pos)):
                return result
            if not c or c == close:
                return result
            if c in '})]':
                self.fail('짝이 맞지 않는 닫는 괄호입니다')
            if c in '^_':
                self.fail('첨자 앞에 값이 필요합니다')
            base = self.atom()
            prior = next((i for i in range(len(result) - 1, -1, -1) if not is_math_space(result[i])), None)
            if base[1] in ('+', '−') and base[0].startswith('<mo>'):
                previous = result[prior] if prior is not None else None
                if not previous or (previous[0].startswith(('<mo>', '<mo ')) and previous[1] not in ('!', '°')):
                    base = self.prefix_sign(base[1])
            if base[0] == '<mo>°</mo>' and result and re.fullmatch(r'<mn>[^<]+</mn>', result[-1][0]):
                result[-1] = result[-1][0][:-5] + '°</mn>', result[-1][1] + '°'
                continue
            scripts = {}
            while True:
                self.spaces()
                op = self.next
                if op not in ('^', '_'):
                    break
                self.pos += 1
                if op in scripts:
                    self.fail('같은 첨자가 중복되었습니다')
                scripts[op] = self.argument()
            if scripts:
                tag = 'm' + ('sub' if '_' in scripts else '') + ('sup' if '^' in scripts else '')
                rendered, plain = base
                if self.structured_plain and rendered.startswith('<mfrac>'):
                    plain = fraction_term(plain)
                for op, translation in (('_', SUBS), ('^', SUPERS)):
                    if op in scripts:
                        markup, text = scripts[op]
                        rendered += markup
                        if self.answer:
                            if op == '^' and text == '°':
                                plain += text
                            else:
                                part = fraction_term(text)
                                plain += op + part
                        else:
                            plain += text.translate(translation) if all(ord(c) in translation for c in text) else f'{op}({text})'
                base = f'<{tag}>{rendered}</{tag}>', plain
            if self.structured_plain and prior is not None and not is_math_space(base):
                # Adjacent factors must not merge with a denominator or radicand.
                html_before, before = result[prior]
                html_now, now = base
                if (html_before.startswith('<mfrac>') and re.match(r'[0-9√]', now)
                        or html_before.startswith('<msqrt>') and re.match(r'[0-9]', now)):
                    result[prior] = html_before, f'({before})'
                elif html_now.startswith('<mfrac>') and re.search(r'[0-9π)]$', before):
                    base = html_now, fraction_term(now)
            result.append(base)

    def parse(self):
        parts = self.sequence()
        if not parts:
            self.fail('빈 수식입니다', 0)
        rendered, plain = join_parts(parts)
        return f'<math>{rendered}</math>', plain


def math_to_mathml(source):
    return MathParser(source).parse()[0]


@dataclass(frozen=True)
class Blank:
    mark: str
    answer: str
    alternatives: tuple = ()
    source: str = ''
    width_mm: Optional[float] = None


@dataclass(frozen=True)
class Markup:
    html: str
    plain: str
    outside_math: str
    blanks: tuple
    public_source: str
    blank_spans: tuple = ()


def join_inline_parts(parts):
    """Keep inline math or a blank with its directly attached word."""
    rendered, word = [], []
    bold = False

    def set_bold(active):
        nonlocal bold
        if active != bold:
            rendered.append('<strong>' if active else '</strong>')
            bold = active

    def emit(part):
        set_bold(part[3])
        rendered.append(part[0])

    def flush():
        has_blank = any(part[2] == 'blank' for part in word)
        grouped = len(word) > 1 and (has_blank or any(part[2] == 'math' for part in word))
        if grouped:
            set_bold(False)
            cls = 'ws-blank-word' if has_blank else 'ws-math-word'
            rendered.append(f'<span class="{cls}"><span>')
        for part in word:
            emit(part)
        if grouped:
            set_bold(False)
            rendered.append('</span></span>')
        word.clear()

    for part in parts:
        if part[2] == 'boundary':
            flush()
            emit(part)
        else:
            if part[2] == 'blank':
                flush()
            word.append(part)
    flush()
    set_bold(False)
    return ''.join(rendered), ''.join(part[1] for part in parts)


def inspect_markup(source, *, interactive=False, table_blank_mm=None, answer=False, label=False):
    """Parse once; label plain text uses readable math and shows only blank marks."""
    if NEWLINE.search(source):
        raise MarkupError('한 필드에는 줄을 바꾸지 마세요.')
    pos = 0
    blanks = []
    blank_spans = []
    outside_math = []

    def at(token):
        return source.startswith(token, pos)

    def fail(token, start):
        raise MarkupError(f'{start + 1}번째 글자: 닫는 {token}가 없습니다.')

    def sequence(stops=(), in_blank=False, bold=False):
        nonlocal pos
        parts = []
        while pos < len(source) and not any(map(at, stops)):
            start = pos
            c = source[pos]
            pos += 1
            next_char = source[pos:pos + 1]
            if c == '\\' and next_char in ('$', '\\', '{'):
                pos += 1
                outside_math.append(next_char)
                parts.append((html.escape(next_char), next_char, 'text', bold))
            elif c == '$':
                begin = pos
                while pos < len(source) and not at('$'):
                    pos += 2 if at('\\$') or at('\\\\') else 1
                if not at('$'):
                    fail('$', start)
                parts.append((*MathParser(source[begin:pos], answer=answer, label=label).parse(), 'math', bold))
                pos += 1
                outside_math.append(' ')
            elif c == '*' and at('*'):
                pos += 1
                parts.extend(sequence(('**', *stops), in_blank, bold=True))
                if not at('**'):
                    fail('**', start)
                pos += 2
            elif c == '{' and at('{') and not in_blank:
                pos += 1
                begin = pos
                while pos < len(source) and not at('|') and not at('}}'):
                    pos += 1
                mark = source[begin:pos].strip()
                rendered = plain = ''
                answers, lengths = [], []
                width = None
                if at('|'):
                    pos += 1
                    while True:
                        answer_start = pos
                        answer_html, answer_text = join_inline_parts(sequence(('|', '}}', ';;'), True))
                        answers.append(source[answer_start:pos])
                        lengths.append(len(answer_text.strip()))
                        if len(answers) == 1:
                            rendered, plain = answer_html, answer_text
                        if not at(';;'):
                            break
                        pos += 2
                    if at('|'):
                        pos += 1
                        end = source.find('}}', pos)
                        if end < 0:
                            fail('}}', start)
                        raw = source[pos:end].strip()
                        number = float(raw) if DECIMAL.fullmatch(raw) else 0
                        if not (number > 0 and math.isfinite(number)):
                            raise MarkupError('빈칸 너비를 양수(mm)로 쓰세요.')
                        width = number
                        pos = end
                if not at('}}'):
                    fail('}}', start)
                if not mark and not plain.strip():
                    raise MarkupError('빈칸에 기호나 정답을 쓰세요.')
                pos += 2
                if answers and any(not answer.strip() for answer in answers):
                    raise MarkupError('빈칸의 대안 정답을 비워 둘 수 없습니다.')
                if width is None and lengths:
                    # A marked blank can be two handwriting cells wide; unmarked lines keep the 18 mm minimum.
                    width = max(9 if mark else 18, max(lengths) * 4.5)
                blanks.append(Blank(mark, plain.strip(), tuple(answers[1:]), answers[0] if answers else '', width))
                blank_spans.append((start, pos, mark))
                width = table_blank_mm if table_blank_mm is not None else width
                mark_attr = f' data-mark="{html.escape(mark)}"' if mark else ''
                style = '' if width is None else f' style="--ws-blank-min:{js_number(width)}mm"'
                mark_node = f'<span class="ws-blank-mark">{html.escape(mark)}</span>' if mark else ''
                key = f'<span class="ws-key">{rendered}</span>' if plain.strip() else ''
                hook = blank_hook.get() if interactive else None
                slot = hook(answers, rendered, source[pos:]) if hook and answers else ''
                kind = 'blank' if re.match(r'[가-힣]', source[pos:]) else 'boundary'
                blank_text = (mark or ' ') if label else (plain or mark)
                parts.append((f'<span class="ws-blank"{mark_attr}{style}{slot}>{mark_node}{key}</span>', blank_text, kind, bold))
            else:
                outside_math.append(c)
                parts.append((html.escape(c), c, 'boundary' if c.isspace() else 'text', bold))
        return parts

    try:
        rendered, plain = join_inline_parts(sequence())
    except RecursionError as error:
        raise MarkupError('수식이나 강조의 중첩을 줄이세요.') from error
    public_parts, last = [], 0
    for start, end, mark in blank_spans:
        public_parts.extend((source[last:start], mark or ' '))
        last = end
    public_parts.append(source[last:])
    return Markup(rendered, plain, ''.join(outside_math), tuple(blanks), ''.join(public_parts), tuple(blank_spans))


def whole_blank(source):
    """Whether the cell contains exactly one blank and surrounding whitespace."""
    spans = inspect_markup(source).blank_spans
    if len(spans) != 1:
        return False
    start, end, _ = spans[0]
    return not source[:start].strip() and not source[end:].strip()


def js_number(value):
    """Keep CSS decimal formatting aligned with JS Number's string form."""
    from decimal import Decimal
    raw = repr(value)
    if 1e-6 <= value < 1e21:
        fixed = format(Decimal(raw), 'f')
        return fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed
    return re.sub(r'e([+-])0+(\d)', r'e\1\2', raw)


def render_markup(source, *, table_blank_mm=None):
    return inspect_markup(source, interactive=True, table_blank_mm=table_blank_mm).html
