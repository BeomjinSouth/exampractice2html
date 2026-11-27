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


def join_parts(parts):
    return ''.join(p[0] for p in parts), ''.join(p[1] for p in parts)


def wrap_row(parts):
    rendered, plain = join_parts(parts)
    return (rendered if len(parts) == 1 else f'<mrow>{rendered}</mrow>', plain)


class MathParser:
    def __init__(self, source):
        self.source, self.pos = source, 0

    @property
    def next(self):
        return self.source[self.pos:self.pos + 1]

    def fail(self, message, at=None):
        index = self.pos if at is None else at
        raise MarkupError(f'수식 ${self.source}$ · {index + 1}번째 글자: {message}')

    def spaces(self):
        while self.next.isspace():
            self.pos += 1

    def group(self, opening='{', close='}'):
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
        return wrap_row(parts)

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
        if name == 'quad':
            return '<mspace width="1em"></mspace>', ' '
        if name == 'frac':
            a, b = self.group(), self.group()
            return f'<mfrac>{a[0]}{b[0]}</mfrac>', f'({a[1]})/({b[1]})'
        if name == 'sqrt':
            self.spaces()
            index = self.group('[', ']') if self.next == '[' else None
            rendered, plain = self.group()
            if index:
                return f'<mroot>{rendered}{index[0]}</mroot>', f'√[{index[1]}]({plain})'
            return f'<msqrt>{rendered}</msqrt>', '√' + (plain if len(plain) == 1 else f'({plain})')
        if name in ('overline', 'overarc'):
            rendered, plain = self.group()
            if name == 'overline':
                return f'<mrow class="seg">{rendered}</mrow>', plain + '̅'
            return f'<mover>{rendered}<mo stretchy="true">⏜</mo></mover>', plain + '⌒'
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
            match = DECIMAL.match(self.source, start)
            if match:
                self.pos = match.end()
                return f'<mn>{match[0]}</mn>', match[0]
            return '<mo>.</mo>', '.'
        if c in '+-=<>,!':
            value = '−' if c == '-' else c
            return f'<mo>{html.escape(value)}</mo>', value
        self.fail(f'지원하지 않는 글자 {c!r}입니다', start)

    def sequence(self, close=None):
        result = []
        while True:
            self.spaces()
            c = self.next
            if not c or c == close:
                return result
            if c in '})]':
                self.fail('짝이 맞지 않는 닫는 괄호입니다')
            if c in '^_':
                self.fail('첨자 앞에 값이 필요합니다')
            base = self.atom()
            if base[1] in ('+', '−') and base[0].startswith('<mo>'):
                previous = next((p for p in reversed(result) if not p[0].startswith('<mspace ')), None)
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
                for op, translation in (('_', SUBS), ('^', SUPERS)):
                    if op in scripts:
                        markup, text = scripts[op]
                        rendered += markup
                        plain += text.translate(translation) if all(ord(c) in translation for c in text) else f'{op}({text})'
                base = f'<{tag}>{rendered}</{tag}>', plain
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


@dataclass(frozen=True)
class Markup:
    html: str
    plain: str
    outside_math: str
    blanks: tuple


def inspect_markup(source, *, interactive=False):
    """Parse once; return rendered markup and the facts plan needs to check."""
    if NEWLINE.search(source):
        raise MarkupError('한 필드에는 줄을 바꾸지 마세요.')
    pos = 0
    blanks = []
    outside_math = []

    def at(token):
        return source.startswith(token, pos)

    def fail(token, start):
        raise MarkupError(f'{start + 1}번째 글자: 닫는 {token}가 없습니다.')

    def sequence(stops=(), in_blank=False):
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
                parts.append((html.escape(next_char), next_char))
            elif c == '$':
                begin = pos
                while pos < len(source) and not at('$'):
                    pos += 2 if at('\\$') or at('\\\\') else 1
                if not at('$'):
                    fail('$', start)
                parts.append(MathParser(source[begin:pos]).parse())
                pos += 1
                outside_math.append(' ')
            elif c == '*' and at('*'):
                pos += 1
                rendered, plain = sequence(('**', *stops), in_blank)
                if not at('**'):
                    fail('**', start)
                pos += 2
                parts.append((f'<strong>{rendered}</strong>', plain))
            elif c == '{' and at('{') and not in_blank:
                pos += 1
                begin = pos
                while pos < len(source) and not at('|') and not at('}}'):
                    pos += 1
                mark = source[begin:pos].strip()
                rendered = plain = ''
                answers = []
                width: Optional[str] = None
                if at('|'):
                    pos += 1
                    answer_start = pos
                    rendered, plain = sequence(('|', '}}'), True)
                    answers = source[answer_start:pos].split(';;')
                    rendered = rendered.split(';;', 1)[0]
                    plain = plain.split(';;', 1)[0]
                    if at('|'):
                        pos += 1
                        end = source.find('}}', pos)
                        if end < 0:
                            fail('}}', start)
                        raw = source[pos:end].strip()
                        number = float(raw) if DECIMAL.fullmatch(raw) else 0
                        if not (number > 0 and math.isfinite(number)):
                            raise MarkupError('빈칸 너비를 양수(mm)로 쓰세요.')
                        width = js_number(number)
                        pos = end
                if not at('}}'):
                    fail('}}', start)
                if not mark and not plain.strip():
                    raise MarkupError('빈칸에 기호나 정답을 쓰세요.')
                pos += 2
                if answers and any(not answer.strip() for answer in answers):
                    raise MarkupError('빈칸의 대안 정답을 비워 둘 수 없습니다.')
                blanks.append(Blank(mark, plain.strip(), tuple(answers[1:]), answers[0] if answers else ''))
                if width is None and not mark:
                    width = js_number(max(18, len(plain.strip()) * 4.5))
                mark_attr = f' data-mark="{html.escape(mark)}"' if mark else ''
                style = '' if width is None else f' style="min-width:{width}mm"'
                mark_node = f'<span class="ws-blank-mark">{html.escape(mark)}</span>' if mark else ''
                key = f'<span class="ws-key">{rendered}</span>' if plain.strip() else ''
                hook = blank_hook.get() if interactive else None
                slot = hook(answers, rendered) if hook and answers else ''
                parts.append((f'<span class="ws-blank"{mark_attr}{style}{slot}>{mark_node}{key}</span>', plain or mark))
            else:
                outside_math.append(c)
                parts.append((html.escape(c), c))
        return join_parts(parts)

    try:
        rendered, plain = sequence()
    except RecursionError as error:
        raise MarkupError('수식이나 강조의 중첩을 줄이세요.') from error
    return Markup(rendered, plain, ''.join(outside_math), tuple(blanks))


def js_number(value):
    """Keep CSS decimal formatting aligned with JS Number's string form."""
    from decimal import Decimal
    raw = repr(value)
    if 1e-6 <= value < 1e21:
        fixed = format(Decimal(raw), 'f')
        return fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed
    return re.sub(r'e([+-])0+(\d)', r'e\1\2', raw)


def render_markup(source):
    return inspect_markup(source, interactive=True).html
