"""Assemble offline worksheet HTML and remove teacher data for distribution."""
import base64
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from html import escape
from html.parser import HTMLParser
from io import BytesIO
from itertools import groupby
import json
import math
import os
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from sheet_common import (BUILD_FILE, CHECK_DIR, LABEL_MM, PICTURE_CELL_PADDING_MM, RASTER_MIMES, ROOT,
                          TEACHER_SUFFIX, TOOL, VERSION, atomic_write, box_five, box_tick_values, content_width_mm,
                          figure_width_mm, html_paths, image_path, json_bytes, sha256, situation_cell_mm, split_widths_mm,
                          FOREIGN_CODE_FIX, engine_script, find_usable_python, foreign_code_issues, python_sheet_command)
from sheet_markup import MARKS, SYMBOLS, blank_hook, inspect_markup, render_markup, whole_blank
from sheet_plan import (SVG_URL, answer_svg_source, checked_svg, function_tree, function_value, line_grid_points,
                        load_plan, print_issues, svg_alt, svg_property, svg_tag)
from sheet_common import youtube_id
from sheet_answer import (canonical, check_hash, fraction_requirement, inferred_unit, input_mode, make_salt,
                          normalized_unit, numeric_details, plain, radical_parts, reveal, text_candidates, writing_forms)

KEY_COLOR = '#c00000'  # --ws-key-color; data images cannot inherit the document's CSS.
TEACHER_ONLY = 'ws-teacher-only'
KINDS_LABEL = {'평가': '수행평가', '연습': '연습지'}


def student_policy(interactive, reveal_policy):
    """Return whether to keep grading hashes and the public answer/solution bundle."""
    return (interactive and reveal_policy in ('시도 후', '바로', '교사만'),
            interactive and reveal_policy in ('시도 후', '바로'))


def _visible_label(source):
    """Use the rendered student text, without blank answers, for a solution title."""
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = Text()
    parser.feed(render_markup(inspect_markup(source).public_source))
    return ' '.join(''.join(parser.parts).split())


def _solution_label(item):
    def blocks_in_order(blocks):
        for block in blocks:
            if block.get('teacher_only'):
                continue
            yield block
            if block['type'] == 'split':
                yield from blocks_in_order(block['left'])
                yield from blocks_in_order(block['right'])

    blocks = list(blocks_in_order(item['blocks']))
    for block in blocks:
        if block['type'] == 'heading':
            heading = ' · '.join(filter(None, (_visible_label(block.get('tag', '')), _visible_label(block.get('text', '')))))
            if heading:
                return heading
    for block in blocks:
        if block['type'] == 'question':
            text = _visible_label(block['text'])
            if text:
                return text[:30] + ('…' if len(text) > 30 else '')
    return item['id'] + ' 풀이'


def _attrs_text(attrs):
    return ''.join(f' {key}="{escape(value)}"' if value is not None else f' {key}'
                   for key, value in attrs.items())


def _text_slot_attrs(slot, source, salt, grading, *, mode='auto', accept=(), unit='', blank=False, rendered=None):
    # Rendering and grading use the same parser; fail closed if either path drifts.
    parsed = [inspect_markup(value, answer=True) for value in (source, *accept)]
    if rendered is not None and parsed[0].html != rendered:
        raise ValueError(f'{slot}: answer-render-mismatch: 화면 수식과 판별 자료가 맞지 않습니다.')
    answer, *alternatives = (value.plain.strip().replace('−', '-') for value in parsed)
    answers = [answer, *alternatives]
    numeric = numeric_details(answer)
    if mode == 'number' and numeric is None:
        raise ValueError(f'{slot}: 정답을 수로 읽을 수 없습니다.')
    kind = 'open'
    if answer and mode != 'off':
        if mode != 'text' and numeric is not None:
            kind = 'number'
        elif mode == 'auto' and all(map(radical_parts, answers)):
            kind = 'radical'
        elif mode == 'text' or (len(answer) <= 12 if blank else accept):
            kind = 'text'
    shape = [symbol for symbol in ('π', '√') if any(symbol in value for value in answers)]
    if any((parts := numeric_details(value)) and parts[0].endswith('π') for value in answers) and 'π' not in shape:
        shape.insert(0, 'π')
    if (kind == 'number' and any((parts := numeric_details(value)) and parts[2] in ('reduced', 'unreduced')
                                 for value in answers) or kind == 'radical' and any('/' in value for value in answers)):
        shape.append('fraction')
    # Set even the empty shape: deriving an edited sheet replaces old writing metadata.
    attrs = {'data-check-kind': kind, 'data-writing-shape': ' '.join(shape)}
    descriptor = {'mode': mode, 'accept': list(accept)}
    if kind != 'open':
        forms = writing_forms(kind, answers, unit)
        if not forms:
            raise ValueError(f'{slot}: 허용 답을 정답과 같은 형식으로 읽을 수 없습니다.')
        if kind == 'number':
            from fractions import Fraction
            suffix = 'π' if numeric[0].endswith('π') else ''
            wrong = Fraction(numeric[0].removesuffix('π')) + 1
            while str(wrong) + suffix in forms:
                wrong += 1
            wrong = str(wrong) + suffix
            keyboard = input_mode(answers)
            if keyboard:
                attrs['data-input-mode'] = keyboard
        elif kind == 'radical':
            wrong = '0'
        else:
            wrong = answer + '오답'
            while any(candidate in forms for candidate in text_candidates(wrong)):
                wrong += '오답'
        if grading:
            if kind in ('number', 'radical') and any('@' in form for form in forms):
                attrs['data-check-unit'] = '1'
            attrs['data-check-hash'] = ','.join(dict.fromkeys(check_hash(salt, kind, form) for form in forms))
        descriptor.update(right=answer, wrong=wrong)
    if kind != 'open' or mode != 'auto' or accept:
        attrs['data-check'] = json.dumps(descriptor, ensure_ascii=False, separators=(',', ':'))
    return attrs


def mm(value):
    return f'{value:.12g}'


def spaced_tick_labels(ticks, size, *, vertical=False):
    """Choose from (position, text) ticks without changing the axis geometry."""
    if len(ticks) < 2:
        return ticks
    # In print, a digit is about .56 em. Leave room for font fallback and a small gap.
    widths = [size * (1 if vertical else sum(.72 if c in '−+' else .35 if c == '.' else .62 for c in text))
              for _, text in ticks]

    def fits(a, b):
        return abs(ticks[b][0] - ticks[a][0]) >= (widths[a] + widths[b]) / 2 + .4

    end = len(ticks) - 1
    keep_end = fits(0, end)
    step = ticks[1][0] - ticks[0][0]
    if keep_end and all(math.isclose(b[0] - a[0], step) for a, b in zip(ticks, ticks[1:])):
        # A regular numbered interval makes it easier to infer the skipped tick values.
        for stride in range(1, end + 1):
            regular = list(range(0, end, stride))
            if not fits(regular[-1], end):
                regular.pop()
            regular.append(end)
            if all(fits(a, b) for a, b in zip(regular, regular[1:])):
                return [ticks[i] for i in regular]
    chosen = [0]
    for i in range(1, end):
        if fits(chosen[-1], i) and (not keep_end or fits(i, end)):
            chosen.append(i)
    if keep_end:
        chosen.append(end)
    return [ticks[i] for i in chosen]


def editable(tag, value, cls='', attrs='', *, table_blank_mm=None):
    if cls:
        attrs = f' class="{cls}"' + attrs
    # Blanks inside teacher answers are not student input positions.
    token = blank_hook.set(None) if set(cls.split()) & {'ws-key', 'ws-teacher-only'} else None
    try:
        rendered = render_markup(value, table_blank_mm=table_blank_mm)
    finally:
        if token is not None:
            blank_hook.reset(token)
    return f'<{tag}{attrs} data-src="{escape(value)}">{rendered}</{tag}>'


def private_editable(tag, value, cls='', attrs=''):
    token = blank_hook.set(None)
    try:
        return editable(tag, value, cls, attrs)
    finally:
        blank_hook.reset(token)


def cols(*widths):
    return '<colgroup>' + ''.join(f'<col style="width:{mm(w)}mm">' for w in widths) + '</colgroup>'


def table(cls, widths, body, head='', attrs=''):
    return f'<table class="{cls}"{attrs}>{cols(*widths)}{head}<tbody>{body}</tbody></table>'


def solve_only(block):
    if block['type'] == 'checklist':
        return not block.get('print', False)
    return block['type'] == 'split' and all(solve_only(b) for side in ('left', 'right') for b in block[side])


def solution_public(answer, steps, rubric, video=None):
    markup = '<p><b>예시 답안</b> ' + render_markup(answer) + '</p>'
    if steps:
        markup += '<ol>' + ''.join('<li>' + render_markup(step) + '</li>' for step in steps) + '</ol>'
    info = {'solution_html': markup}
    if rubric:
        info['rubric'] = rubric
    if video:
        info['video'] = video
    return info


def section(id_, body, extra='', *, screen_only=False):
    cls = 'ws-item ws-solve-only' if screen_only else 'ws-item'
    return f'<section class="{cls}" data-id="{id_}"{extra}>\n{body}\n</section>'


def tr(content):
    return f'<tr>{content}</tr>'


def answer_cell(answer, *, lines=3, grow=False, height=None, kind_explicit=False, slot=''):
    if height is None:
        kind = ' data-kind="text"' if kind_explicit else ''
        attrs = f'{kind} data-lines="{lines}"'
    else:
        attrs = f' data-kind="draw" data-height-mm="{mm(height)}"'
    if grow:
        attrs += ' data-grow="1"'
    return f'<td class="ws-ans"{attrs}{slot}>{answer}</td>'


_ITALIC_LATIN = {c: chr(0x1D434 + i)
                 for i, c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')}
_ITALIC_LATIN['h'] = 'ℎ'


def figure_svg(source):
    # Keep author SVG in an isolated image. MathML uses the corresponding Unicode
    # math italic glyphs, not the synthetic slant of an arbitrary SVG text font.
    source = checked_svg(source)
    root = ET.fromstring(source)
    changed = False
    for text in root.iter():
        if text.tag.rsplit('}', 1)[-1] != 'text':
            continue
        label = ''.join(text.itertext()).strip()
        italic = re.fullmatch(r'[a-z][′₀-₉]*', label)
        upright = (re.fullmatch(r'[A-Z][′₀-₉]*', label)
                   or re.fullmatch(r'[-−+]?\d+(?:[.,]\d+)?(?:\s*[\w°%²³]+)?', label)
                   or label in ('°', '%'))
        if not (italic or upright):
            upright = label in ('cm', 'mm', 'km', 'kg', 'ml', 'cm²', 'cm³', 'm²', 'm³')
        if not (italic or upright):
            continue
        for node in text.iter():
            for attr in ('font-family', 'font-style', 'font'):
                node.attrib.pop(attr, None)
            style = node.get('style', '').rstrip(';')
            node.set('style', style + ';font-family:Cambria Math, STIX Two Math, Latin Modern Math, serif!important;font-style:normal!important')
        if italic:
            for node in text.iter():
                for target, attr in ((node, 'text'), *((child, 'tail') for child in node)):
                    part = getattr(target, attr)
                    if part and label[0] in part:
                        setattr(target, attr, part.replace(label[0], _ITALIC_LATIN[label[0]], 1))
                        break
                else:
                    continue
                break
        changed = True
    if not changed:
        return source
    # Keep the SVG default namespace and xlink references when ElementTree serializes.
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    return ET.tostring(root, encoding='unicode')


def overlay_svg(source, viewport):
    """Match the base image viewport and recolor paint, not geometry or masks."""
    root = ET.fromstring(figure_svg(source))
    sizing = ('width', 'height', 'preserveAspectRatio')
    for attr in sizing:
        root.attrib.pop(attr, None)
        value = svg_property(viewport, attr) if attr != 'preserveAspectRatio' else viewport.get(attr)
        if value:
            root.set(attr, value)
    paints = {'color', 'fill', 'stroke', 'stop-color'}
    unchanged = {'none', 'transparent', 'inherit', 'unset', 'revert', 'revert-layer'}

    rgb = ','.join(str(int(KEY_COLOR[i:i+2], 16)) for i in (1, 3, 5))

    def paint(value):
        color = value.strip()
        if color.lower() in unchanged or SVG_URL.match(color):
            return value
        functional = re.fullmatch(r'(?:rgba?|hsla?)\((.*)\)', color, re.I | re.S)
        if functional:
            # Find the alpha separator outside nested CSS functions such as calc().
            depth, commas = 0, []
            for match in re.finditer(r'[(),/]', functional[1]):
                token = match[0]
                if token == '(':
                    depth += 1
                elif token == ')':
                    depth -= 1
                elif depth == 0:
                    if token == '/':
                        return f'rgba({rgb},{functional[1][match.end():].strip()})'
                    commas.append(match.end())
            if len(commas) == 3:
                return f'rgba({rgb},{functional[1][commas[-1]:].strip()})'
        if re.fullmatch(r'#[0-9a-f]{4}', color, re.I):
            return KEY_COLOR + color[-1] * 2
        if re.fullmatch(r'#[0-9a-f]{8}', color, re.I):
            return KEY_COLOR + color[-2:]
        return KEY_COLOR

    stack = [root]
    while stack:
        node = stack.pop()
        if svg_tag(node) in ('clipPath', 'mask'):
            continue
        for attr in node.attrib.keys() & paints:
            node.set(attr, paint(node.get(attr)))
        if 'style' in node.attrib:
            declarations = []
            for declaration in node.get('style').split(';'):
                key, _, value = declaration.partition(':')
                prop = key.strip().lower()
                if node is root and prop in ('width', 'height'):
                    continue
                if prop in paints and value.strip():
                    value, important = re.subn(r'\s*!\s*important\s*$', '', value, flags=re.I)
                    declaration = f'{key}:{paint(value)}' + ('!important' if important else '')
                if declaration.strip():
                    declarations.append(declaration)
            node.set('style', ';'.join(declarations))
        stack.extend(node)
    if not svg_property(root, 'fill'):
        root.set('fill', KEY_COLOR)  # SVG's default black fill is red on the answer layer.
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    return ET.tostring(root, encoding='unicode')


def data_image(mime, data):
    return f'data:{mime};base64,{base64.b64encode(data).decode("ascii")}'


def _fraction_prompt_texts(blocks):
    for block in blocks:
        kind = block['type']
        if kind == 'split':
            yield from _fraction_prompt_texts(block['left'])
            yield from _fraction_prompt_texts(block['right'])
        texts = (block['text'],) if kind in ('question', 'ask') else (
            (*block['lines'], block.get('title', ''), block.get('note', '')) if kind == 'situation' else ())
        for text in texts:
            yield plain(inspect_markup(text).public_source)


class Renderer:
    def __init__(self, data, folder, warnings):
        self.data = data
        self.folder = folder
        self.meta = data['meta']
        self.kind = self.meta.get('kind', '수업')
        self.columns = self.meta.get('columns', 1)
        self.content_mm = content_width_mm(self.columns)
        self.label_mm = LABEL_MM[self.kind]
        self.title = self.meta['title']
        self.number = 0
        self.first_number = {}
        self.warnings = warnings
        self.grid_index = 0
        self.interactive = self.meta.get('interactive', self.kind in ('수업', '연습'))
        self.reveal_policy = self.meta.get('reveal', '시도 후')
        self.grading, self.published = student_policy(self.interactive, self.reveal_policy)
        self.theme = self.meta.get('theme', '산뜻' if self.kind == '수업' else '정본')
        self.accent = self.meta.get('accent', '하늘')
        self.salt = make_salt() if self.interactive else None
        self.reveal_data = {'slots': {}, 'items': {}}
        self.item_id = None
        self.slot_index = 0

    def slot(self, kind, right=None, *, accept=(), mode='auto', answer_html='', snap=None, count=None, unit='', blank=False):
        if not self.interactive:
            return ''
        self.slot_index += 1
        slot = f'{self.item_id}.{self.slot_index}'
        attrs = {'data-slot': slot, 'data-check-kind': kind}
        if answer_html and self.published:
            self.reveal_data['slots'][slot] = {'answer_html': answer_html}
        if kind == 'writing':
            attrs.update(_text_slot_attrs(slot, right, self.salt, self.grading, mode=mode, accept=accept,
                                         unit=unit, blank=blank, rendered=answer_html))
            return _attrs_text(attrs)
        if kind in ('draw', 'self', 'name'):
            return _attrs_text(attrs)
        normalized = canonical(kind, right, snap)
        if normalized is None:
            raise ValueError(f'{slot}: 정답을 {kind} 형식으로 정규화할 수 없습니다.')
        if self.grading:
            attrs['data-check-hash'] = check_hash(self.salt, kind, normalized)
        if kind == 'choice':
            wrong = [i for i in range(1, (count or max(right)) + 1) if i not in right][:1]
            if not wrong:
                wrong = right[:-1] or [0]
        elif kind == 'ox':
            wrong = 'X' if normalized == 'o' else 'O'
        elif kind == 'order':
            wrong = list(right)
            wrong[0], wrong[1] = wrong[1], wrong[0]
        elif kind == 'match':
            wrong = dict(right)
            keys = list(wrong)
            wrong[keys[0]], wrong[keys[1]] = wrong[keys[1]], wrong[keys[0]]
        elif kind == 'points':
            wrong = [list(p) if isinstance(p, list) else p for p in right]
            if isinstance(wrong[0], list):
                wrong[0][0] += snap or 1
            else:
                wrong[0] += snap or 1
        elif kind == 'line':
            wrong = [list(point) for point in right]
            wrong[1][1] += snap or 1
            if canonical(kind, wrong, snap) in (normalized, None):
                wrong[1] = list(right[1])
                wrong[1][0] += snap or 1
        elif kind == 'bars':
            wrong = list(right)
            wrong[0] += snap or 1
        else:
            raise ValueError(f'{slot}: 알 수 없는 판별 종류입니다.')
        if canonical(kind, wrong, snap) in (normalized, None):
            raise ValueError(f'{slot}: 다른 오답을 만들 수 없습니다.')
        descriptor = {'mode': mode, 'accept': list(accept), 'right': right, 'wrong': wrong, 'answer_html': answer_html}
        attrs['data-check'] = json.dumps(descriptor, ensure_ascii=False, separators=(',', ':'))
        return _attrs_text(attrs)

    def blank_slot(self, answers, rendered, tail, column='', row='', unit=''):
        return self.slot('writing', answers[0], accept=answers[1:], answer_html=rendered,
                         unit=inferred_unit(tail, column, row, unit), blank=True)

    def answer_slot(self, answer, *, mode='auto', accept=(), draw=False, unit=''):
        if draw:
            return self.slot('draw')
        token = blank_hook.set(None)
        try:
            markup = render_markup(answer)
        finally:
            blank_hook.reset(token)
        return self.slot('writing', answer, mode=mode, accept=accept, answer_html=markup, unit=unit)

    def name_line(self, with_slots=False):
        fields = self.meta.get('name_fields', ['반', '번', '이름'])
        grade = render_markup(self.meta.get('grade', '1학년'))
        def space(name, length):
            value = '&nbsp;' * length
            if with_slots and self.interactive:
                value = f'<span class="ws-name-slot" data-slot="head.{escape(name)}" data-check-kind="name">{value}</span>'
            return '(' + value + ')'
        entries = [f'{space(name, 3)}반' if name == '반' else f'{space(name, 4)}번' if name == '번' else
                   f'{render_markup(name)} : {space(name, 17 if name == "이름" else 8)}' for name in fields]
        return ' '.join(filter(None, [grade, *entries]))

    def head(self):
        unit = self.meta.get('unit') or self.title
        title = self.meta.get('header_title')
        name = f'<td class="ws-head-name">{self.name_line(with_slots=True)}</td>'
        widths = (188,)
        if self.kind == '수업':
            widths = (30, 64, 32, 62)
            labels = self.meta.get('name_fields', ['반', '번', '이름'])
            # The common class/number pair shares the school-number cell.
            names = ['학번', '이름'] if labels == ['반', '번', '이름'] else labels
            count = len(names)
            body = tr(f'<td colspan="4">{editable("div", title or self.title, "ws-head-1")}</td>')
            unit_tag = editable('td', f'단원 : {unit}', attrs=f' colspan="{4 - count}"')
            def name_cell(label, pair=False):
                prefix = '짝의 ' if pair else ''
                if not self.interactive:
                    return f'<td>{prefix}{render_markup(label)} :</td>'
                slot_name = ('짝-' if pair else '') + label
                space = f'<span class="ws-name-slot" data-slot="head.{escape(slot_name)}" data-check-kind="name">&nbsp;&nbsp;&nbsp;&nbsp;</span>'
                return f'<td>{prefix}{render_markup(label)} : ({space})</td>'
            body += tr(unit_tag + ''.join(name_cell(label) for label in names))
            if self.meta.get('pair'):
                body += tr(f'<td colspan="{4 - count}">짝 활동</td>' + ''.join(name_cell(label, True) for label in names))
        else:
            parts = [self.meta.get('term', ''), self.meta.get('grade', '1학년'), self.meta.get('subject', '수학'), KINDS_LABEL[self.kind]]
            # Put the year before the grade when the term is written as "2026학년도 2학기".
            term = re.fullmatch(r'(\d{4}학년도)\s+([12]학기)', parts[0])
            if term:
                parts[:2] = [term[1], parts[1], term[2]]
            title = title or ' '.join(filter(None, parts))
            subtitle = ('평가영역 : ' if self.kind == '평가' else '단원 : ') + unit
            title_cells = (f'<td class="ws-head-title">{editable("div", title, "ws-head-1")}'
                           f'{editable("div", subtitle, "ws-head-2")}</td>')
            if self.kind == '평가':
                widths = (122.2, 9.2, 23.2, 10.2, 23.2)
                title_cells += ('<td rowspan="2" class="ws-head-k">점수</td><td rowspan="2"></td>'
                                '<td rowspan="2" class="ws-head-k">학생<br>확인</td><td rowspan="2"></td>')
            body = tr(title_cells) + tr(name)
        return section('head', table('ws-head ws-head--' + self.kind, widths, body), ' data-span="all"')

    def image(self, picture, max_width, *, math_labels=False):
        width = figure_width_mm(picture, max_width)
        source = picture.get('svg')
        if source is None:
            file = image_path(self.folder, picture['src'])
            ext = file.suffix.lower()
            if ext == '.svg':
                source = file.read_text(encoding='utf-8-sig')
        if source is not None:
            mime = 'image/svg+xml'
            svg = (figure_svg if math_labels else checked_svg)(source)
            data = svg.encode('utf-8')
            fallback = svg_alt(ET.fromstring(svg)) if 'alt' not in picture else ''
        else:
            data, mime = self.raster(file, width, RASTER_MIMES[ext])
            fallback = ''
        alt = inspect_markup(picture['alt']).plain if 'alt' in picture else fallback
        return f'<img src="{data_image(mime, data)}" style="width:{mm(width)}mm" alt="{escape(alt)}">'

    def raster(self, file, width, mime):
        data = file.read_bytes()
        try:
            from PIL import Image, ImageOps
        except ImportError:
            self.warnings.append('Pillow가 없어 그림을 줄이지 않고 내장했습니다.')
            return data, mime
        try:
            with Image.open(BytesIO(data), formats=['PNG', 'JPEG', 'WEBP', 'GIF', 'BMP']) as original:
                image = ImageOps.exif_transpose(original)
                px = max(1, math.floor(width * 300 / 25.4))
                if image.width > px:
                    resampling = getattr(Image, 'Resampling', Image)
                    image.thumbnail((px, max(1, round(image.height * px / image.width))), resampling.LANCZOS)
                result = BytesIO()
                if original.format == 'JPEG':
                    image.convert('RGB').save(result, 'JPEG', optimize=True, quality=88)
                    return result.getvalue(), 'image/jpeg'
                image.save(result, 'PNG', optimize=True)
                return result.getvalue(), 'image/png'
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise ValueError(f'{file.name}: 그림 파일을 열 수 없습니다. 정상적인 PNG나 JPEG로 다시 저장하세요.') from error

    def picture(self, figure):
        cls = 'ws-fig' + (' ' + TEACHER_ONLY if figure.get('teacher_only') else '')
        pen = ' data-pen="1"' if self.interactive and figure.get('pen') and not figure.get('teacher_only') else ''
        slot = self.slot('draw') if pen else ''
        make_caption = private_editable if figure.get('teacher_only') else editable
        caption = make_caption('figcaption', figure['caption']) if 'caption' in figure else ''
        base = self.image(figure, self.content_mm, math_labels=True)
        overlay = ''
        if 'answer' in figure:
            viewport = ET.fromstring(checked_svg(figure['svg']))
            svg = overlay_svg(answer_svg_source(figure['answer']), viewport)
            src = data_image('image/svg+xml', svg.encode('utf-8'))
            width = figure_width_mm(figure, self.content_mm)
            overlay = (f'<img class="ws-key ws-fig-answer" src="{src}" style="width:{mm(width)}mm" '
                       'alt="" aria-hidden="true">')
        return f'<figure class="{cls}"{pen}{slot}>{base}{overlay}{caption}</figure>'

    def two_pictures(self, block):
        width = self.content_mm / 2
        figures = block['items']
        images = ''.join(f'<td>{self.wrap_private(figure, self.image(figure, width - PICTURE_CELL_PADDING_MM, math_labels=True))}</td>'
                         for figure in figures)
        caps = ''.join(f'<td class="ws-cap">{self.wrap_private(figure, private_editable("span", figure["caption"]))}</td>'
                       if figure.get('teacher_only') else editable('td', figure['caption'], 'ws-cap') for figure in figures)
        cls = 'ws-figs' + (' ' + TEACHER_ONLY if all(figure.get('teacher_only') for figure in figures) else '')
        return table(cls, (width, width), tr(images) + tr(caps))

    @staticmethod
    def wrap_private(picture, body):
        return f'<span class="{TEACHER_ONLY}">{body}</span>' if picture.get('teacher_only') else body

    def situation(self, block):
        body = editable('p', block['title'], 'ws-sit-title') if 'title' in block else ''
        body += ''.join(editable('p', line) for line in block['lines'])
        if 'note' in block:
            body += editable('p', block['note'], 'ws-sit-note')
        text_cell = f'<td class="ws-sit-text">{body}</td>'
        fig = block.get('figure')
        if not fig:
            return table('ws-box ws-sit', (self.content_mm,), tr(text_cell))
        pic_mm = situation_cell_mm(self.content_mm, fig['width_mm'])
        make_caption = private_editable if fig.get('teacher_only') else editable
        caption = make_caption('p', fig['caption'], 'ws-cap') if 'caption' in fig else ''
        picture = self.wrap_private(fig, self.image(fig, pic_mm - PICTURE_CELL_PADDING_MM, math_labels=True) + caption)
        return table('ws-box ws-sit ws-sit--fig', (self.content_mm - pic_mm, pic_mm),
                     tr(text_cell + f'<td class="ws-sit-fig">{picture}</td>'))

    def label(self, row, span):
        body = f'<span class="ws-mark">{row["mark"]}</span> ' if 'mark' in row else ''
        body += editable('span', row['label'], 'ws-lab-text')
        if 'points' in row:
            body += f'<span class="ws-pts">({mm(row["points"])}점)</span>'
        if 'guide' in row:
            body += editable('span', row['guide'], 'ws-guide')
        attrs = f' rowspan="{span}"' if span > 1 else ''
        return f'<th class="ws-lab"{attrs}>{body}</th>'

    def answer_table(self, block):
        rows = []
        groups = groupby(enumerate(block['rows']), key=lambda pair: pair[1].get('group') or pair[0])
        for _, pairs in groups:
            group = [row for _, row in pairs]
            for i, row in enumerate(group):
                label = self.label(row, len(group)) if i == 0 else ''
                answer, kind = row['answer'], row.get('kind', 'text')
                if isinstance(answer, dict):
                    image = self.image(answer, self.content_mm - self.label_mm - PICTURE_CELL_PADDING_MM)
                    caption = private_editable('p', answer['caption']) if 'caption' in answer else ''
                    key = f'<div class="ws-key">{image}{caption}</div>'
                else:
                    key = editable('div', answer, 'ws-key')
                height = row['height_mm'] if kind == 'draw' else None
                slot = self.answer_slot(answer, mode=row.get('check', 'auto'), accept=row.get('accept', ()),
                                        draw=kind == 'draw', unit=inferred_unit('', row=inspect_markup(group[0]['label']).plain))
                td = answer_cell(key, lines=row.get('lines', 3), grow=row.get('grow', False), height=height, kind_explicit=True, slot=slot)
                rows.append(f'<tr class="ws-row">{label}{td}</tr>')
        return table('ws-ans-table', (self.label_mm, self.content_mm - self.label_mm), ''.join(rows))

    def grid(self, block, *, live_map=True, fit=None):
        coord = block['mode'] == 'coord'
        xmin, xmax = block['x'] if coord else (0, block['cols'])
        ymin, ymax = block['y'] if coord else (0, block['rows'])
        unit = block['unit_mm']
        width, height = (xmax - xmin) * unit, (ymax - ymin) * unit
        self.grid_index += 1
        clip = f'ws-grid-clip-{self.grid_index}'

        def px(x):
            return (x - xmin) * unit

        def py(y):
            return (ymax - y) * unit

        def label(x, y, text, attrs=''):
            # 눈금 숫자의 음수 부호는 하이픈(-)이 아니라 수학 부호(−, U+2212)로 쓴다.
            text = text.replace('-', '−')
            return f'<text x="{mm(x)}" y="{mm(y)}" font-size="2.8"{attrs}>{escape(text)}</text>'

        plot = block.get('plot')
        slot = ''
        key_plot = ''
        if plot:
            snap = plot.get('snap', 1)
            plot_kind = plot['mode']
            right = plot['answer']
            if plot_kind == 'line' and isinstance(right, str):
                tree = function_tree(right)
                right = line_grid_points(tree, (xmin, xmax), (ymin, ymax), snap)
            slot = self.slot(plot_kind, right, snap=snap, answer_html=escape(str(plot['answer'])))
            if self.interactive:
                slot += f' data-snap="{mm(snap)}" data-max="{2 if plot_kind == "line" else len(right)}"'
            if plot_kind == 'points':
                key_plot = ''.join(f'<circle cx="{mm(px(x))}" cy="{mm(py(y))}" r="0.8"/>' for x, y in right)
            else:
                (x1, y1), (x2, y2) = right
                # 두 점을 잇는 무한 직선과 좌표 범위의 교점을 계산한다.
                lo, hi = -math.inf, math.inf
                for start, delta, lower, upper in ((x1, x2-x1, xmin, xmax), (y1, y2-y1, ymin, ymax)):
                    if delta:
                        ends = sorted(((lower-start)/delta, (upper-start)/delta))
                        lo, hi = max(lo, ends[0]), min(hi, ends[1])
                if lo <= hi and math.isfinite(lo) and math.isfinite(hi):
                    ax, ay = x1 + lo*(x2-x1), y1 + lo*(y2-y1)
                    bx, by = x1 + hi*(x2-x1), y1 + hi*(y2-y1)
                    key_plot = (f'<path d="M{mm(px(ax))} {mm(py(ay))}L{mm(px(bx))} {mm(py(by))}" '
                                'fill="none" stroke="currentColor" stroke-width="0.4"/>')
        elif self.interactive and block.get('pen'):
            slot = self.slot('draw')
        if self.interactive and block.get('pen'):
            slot += ' data-pen="1"'
        w, h = mm(width), mm(height)
        edges = ''.join(f'M{mm(px(x))} 0V{h}' for x in range(xmin, xmax + 1))
        edges += ''.join(f'M0 {mm(py(y))}H{w}' for y in range(ymin, ymax + 1))
        body = f'<path d="{edges}" fill="none" stroke="#999" stroke-width="0.1"/>'
        pad_left = pad_top = pad_right = 0
        if coord:
            x_axis = ymin <= 0 <= ymax
            y_axis = xmin <= 0 <= xmax
            labels = block.get('labels', True)
            ticks = []
            axes = ''
            if x_axis:
                oy = py(0)
                axes += f'M0 {mm(oy)}H{w}'
                for x in range(xmin, xmax + 1):
                    xx = px(x)
                    axes += f'M{mm(xx)} {mm(max(0, oy - .6))}V{mm(min(height, oy + .6))}'
                    if (x and labels and unit >= 3 and (unit >= 6 or x % 2 == 0)
                            and not (y_axis and x < 0 and abs(x * unit) < 5)):
                        align = 'start' if x == xmin else 'end' if x == xmax else 'middle'
                        ticks.append(label(xx, oy + 3.3 if oy + 3.5 < height else oy - 1.1, str(x),
                                           f' text-anchor="{align}"'))
                # Reserve a label lane beyond the final tick, without changing the grid scale.
                pad_right = 4
                ticks.append(label(width + 1.1, max(2.8, min(height - .8, oy + .9)), 'x', ' font-style="italic"'))
            if y_axis:
                ox = px(0)
                axes += f'M{mm(ox)} 0V{h}'
                for y in range(ymin, ymax + 1):
                    yy = py(y)
                    axes += f'M{mm(max(0, ox - .6))} {mm(yy)}H{mm(min(width, ox + .6))}'
                    if y and labels and unit >= 3 and (unit >= 6 or y % 2 == 0):
                        right = ox < 6
                        ticks.append(label(ox + 1 if right else ox - 1.1, min(height - .2, max(2.8, yy + .9)), str(y),
                                           ' text-anchor="start"' if right else ' text-anchor="end"'))
                pad_top = 4.5
                ticks.append(label(ox, -1.0, 'y', ' text-anchor="middle" font-style="italic"'))
            if x_axis and y_axis:
                # At the left edge, O belongs outside the grid, opposite the y tick labels.
                origin_x = px(0) - .9
                pad_left = 3.7 if xmin == 0 else 0
                ticks.append(label(origin_x, py(0) + 3.3 if py(0) + 3.5 < height else py(0) - 1.1,
                                   'O', ' text-anchor="end"'))
            elif y_axis and xmin == 0:
                pad_left = 1.5  # Keep the centered y label inside the viewBox.
            body += f'<path d="{axes}" fill="none" stroke="black" stroke-width="0.3"/>' + ''.join(ticks)
            body += f'<defs><clipPath id="{clip}"><rect width="{w}" height="{h}"/></clipPath></defs>'
            for entry in block.get('draw', []):
                graph = ''
                if 'fn' in entry:
                    tree = function_tree(entry['fn'])
                    count = max(240, min(1200, (xmax - xmin) * 24))
                    segments = []
                    previous = None
                    for i in range(count + 1):
                        x = xmin + i / count * (xmax - xmin)
                        try:
                            y = function_value(tree, x)
                        except (ValueError, ArithmeticError):
                            previous = None
                            continue
                        # Break at jumps and do not send huge out-of-view coordinates to SVG.
                        if not ymin - 2 * (ymax - ymin) <= y <= ymax + 2 * (ymax - ymin):
                            previous = None
                            continue
                        command = 'L' if previous is not None and abs(previous - y) < (ymax - ymin) else 'M'
                        segments.append(f'{command}{mm(px(x))} {mm(py(y))}')
                        previous = y
                    graph = f'<path d="{"".join(segments)}" fill="none" stroke="currentColor" stroke-width="0.4"/>'
                else:
                    for i, (x, y) in enumerate(entry['points']):
                        if not (xmin <= x <= xmax and ymin <= y <= ymax):
                            continue
                        xx, yy = px(x), py(y)
                        graph += f'<circle cx="{mm(xx)}" cy="{mm(yy)}" r="0.65" fill="currentColor"/>'
                        if 'names' in entry:
                            name = entry['names'][i]
                            graph += label(xx + 1.1, max(3, yy - 1), inspect_markup(name).plain,
                                           ' font-style="italic"' if r'\mathit{' in name else '')
                cls = ' class="ws-key"' if entry.get('teacher') else ''
                body += f'<g{cls} clip-path="url(#{clip})">{graph}</g>'
        if key_plot:
            body += f'<g class="ws-key" clip-path="url(#{clip})">{key_plot}</g>'
        if plot:
            body += '<g class="ws-plot"></g>'
        mapping = f' data-map="{mm(xmin)} {mm(xmax)} {mm(ymin)} {mm(ymax)} 0 0 {mm(unit)}"' if coord and live_map else ''
        svg_width, svg_height = width + pad_left + pad_right, height + pad_top
        scale = min(1, fit[0] / svg_width, fit[1] / svg_height) if fit else 1
        if fit and coord:
            # The viewBox shrinks the geometry, not the physical size of axis labels.
            font_size = max(2.8, (6.2 * 25.4 / 72) / scale)
            body = body.replace('font-size="2.8"', f'font-size="{mm(font_size)}"')
        alt = '좌표평면' if coord else f'가로 {xmax}칸, 세로 {ymax}칸 모눈'
        return (f'<figure class="ws-fig ws-grid"{slot}><svg width="{mm(svg_width * scale)}mm" height="{mm(svg_height * scale)}mm" '
                f'viewBox="{mm(-pad_left)} {mm(-pad_top)} {mm(svg_width)} {mm(svg_height)}"{mapping} '
                f'role="img" aria-label="{alt}">{body}</svg></figure>')

    @staticmethod
    def private_markup(value):
        token = blank_hook.set(None)
        try:
            return render_markup(value)
        finally:
            blank_hook.reset(token)

    def shuffle_indices(self, count, item_id, label):
        order = sorted(range(count), key=lambda i: hashlib.sha256(f'{item_id}|{label}|{i}'.encode()).digest())
        if order == list(range(count)):
            order = order[1:] + order[:1]
        return order

    def ox(self, block):
        rows = []
        for i, entry in enumerate(block['items'], 1):
            attrs = self.slot('ox', entry['answer'], answer_html=escape(entry['answer']))
            text = editable('td', entry['text'], 'ws-ox-text')
            rows.append(f'<tr class="ws-ox-row"{attrs}><td class="ws-ox-n">({i})</td>{text}'
                        f'<td class="ws-ox-pick"><span class="ws-ox-o">O</span><span class="ws-ox-x">X</span>'
                        f'<span class="ws-key">{entry["answer"]}</span></td></tr>')
        return table('ws-ox', (10, self.content_mm - 30, 20), ''.join(rows))

    def match(self, block, item_id):
        left, right = block['left'], block['right']
        order = self.shuffle_indices(len(right), item_id, 'match')
        answer = {f'l{i+1}': f'r{order.index(i)+1}' for i in range(len(left))}
        pairs = ''.join(f'<li>{self.private_markup(a)} ↔ {self.private_markup(b)}</li>' for a, b in zip(left, right))
        answer_html = f'<ol>{pairs}</ol>'
        slot = self.slot('match', answer, answer_html=answer_html)
        prompt = editable('p', block['prompt'], 'ws-match-prompt') if 'prompt' in block else ''
        lhs = ''.join(f'<li data-token="l{i+1}">{editable("span", text)}</li>' for i, text in enumerate(left))
        rhs = ''.join(f'<li data-token="r{i+1}">{editable("span", right[j])}</li>' for i, j in enumerate(order))
        key = f'<ol class="ws-key ws-match-key">{pairs}</ol>'
        return (f'<div class="ws-match"{slot}>{prompt}<div class="ws-match-cols"><ol class="ws-match-l">{lhs}</ol>'
                f'<div class="ws-match-gap"></div><ol class="ws-match-r">{rhs}</ol></div>{key}</div>')

    def order(self, block, item_id):
        values = block['items']
        permutation = self.shuffle_indices(len(values), item_id, 'order')
        right = [f't{permutation.index(i)+1}' for i in range(len(values))]
        slot = self.slot('order', right, answer_html=' → '.join(self.private_markup(v) for v in values))
        prompt = editable('p', block['prompt'], 'ws-order-prompt') if 'prompt' in block else ''
        items = ''.join(f'<li data-token="t{i+1}"><span class="ws-order-box"></span>{editable("span", values[j])}'
                        f'<span class="ws-key">{j+1}</span></li>' for i, j in enumerate(permutation))
        instruction = '써 봅시다.' if self.kind == '수업' else '쓰시오.'
        note = f'<p class="ws-print-only">□ 안에 순서대로 1, 2, 3, …을 {instruction}</p>'
        return f'<div class="ws-order"{slot}>{prompt}{note}<ol class="ws-order-list">{items}</ol></div>'

    def numberline(self, block):
        a, b = block['range']
        width = min(block.get('width_mm', 150), self.content_mm)
        left, y = 5, 8
        unit = (width - 10) / (b - a)
        def x(v):
            return left + (v - a) * unit
        plot = block.get('plot')
        attrs = ''
        if plot:
            answer, snap = plot['answer'], plot.get('snap', 1)
            attrs = self.slot('points', answer, snap=snap, answer_html=escape(', '.join(map(str, answer))))
            if self.interactive:
                attrs += f' data-snap="{mm(snap)}" data-max="{len(answer)}"'
        elif self.interactive and block.get('pen'):
            attrs = self.slot('draw')
        if self.interactive and block.get('pen'):
            attrs += ' data-pen="1"'
        body = f'<path d="M{mm(left)} {y}H{mm(width-left)}" fill="none" stroke="currentColor" stroke-width="0.3"/>'
        n = math.floor((b-a) / block['step'] + 1e-9)
        for i in range(n+1):
            value = a + i * block['step']
            xx = x(value)
            body += f'<path d="M{mm(xx)} {y-1}V{y+1}" stroke="currentColor" stroke-width="0.25"/>'
            if block.get('labels', True):
                body += f'<text x="{mm(xx)}" y="13" text-anchor="middle" font-size="2.7">{escape(mm(value).replace("-", "−"))}</text>'
        for point in block.get('points', []):
            xx = x(point['x'])
            body += f'<circle cx="{mm(xx)}" cy="{y}" r="0.7"/>'
            if point.get('name'):
                body += f'<text x="{mm(xx)}" y="5" text-anchor="middle" font-size="2.7">{escape(inspect_markup(point["name"]).plain)}</text>'
        if plot:
            body += '<g class="ws-key">' + ''.join(f'<circle cx="{mm(x(v))}" cy="{y}" r="0.8"/>' for v in answer) + '</g><g class="ws-plot"></g>'
        return (f'<figure class="ws-fig ws-nl"{attrs}><svg width="{mm(width)}mm" height="16mm" viewBox="0 0 {mm(width)} 16" '
                f'data-map="{mm(a)} {mm(b)} {mm(left)} {y} {mm(unit)}" role="img" aria-label="수직선">{body}</svg></figure>')

    def box_chart(self, block):
        groups = block['groups']
        width = min(block.get('width_mm', 130), self.content_mm)
        lo, hi = block['x']
        span = hi - lo
        ticks = [(float(value), mm(float(value)).replace('-', '−')) for value in box_tick_values(block['x'], block['x_step'])]
        names = [inspect_markup(group['label']).plain for group in groups]
        print_size = 3.4  # viewBox units are mm in print (about 9.64pt).
        target_phone_size = max(print_size, 6.6 * width / 170)
        # The unit gets its own line so the end of the axis can still carry a number.
        label_width = max(sum(.6 if c.isascii() else 1 for c in name) for name in names)
        end_chars = max(len(ticks[0][1]), len(ticks[-1][1]))
        left = min(width * .32, max(4 + label_width * target_phone_size, 2 + end_chars * .3 * target_phone_size))
        right = width - min(width * .19, 2 + end_chars * .3 * target_phone_size)
        tick_gap = (ticks[1][0] - ticks[0][0]) / span * (right - left)
        max_label = max(len(text) for _, text in ticks)
        phone_size = max(print_size, min(target_phone_size, (tick_gap - .35) / (.59 * max_label)))
        row_height, top = 14, 1
        bottom = top + row_height * len(groups)
        unit = inspect_markup(block.get('x_label', '')).plain
        height = bottom + (15.5 if unit else 9.5)

        def x(value):
            return left + (value - lo) / span * (right - left)

        def label(at, y, text, anchor):
            return (f'<text x="{mm(at)}" y="{mm(y)}" text-anchor="{anchor}" '
                    f'font-size="{print_size}">{escape(text)}</text>')

        lines = [f'M{mm(left)} {bottom}H{mm(right)}']
        guides = []
        axis_ticks = [(x(value), text) for value, text in ticks]
        for at, _ in axis_ticks:
            xx = mm(at)
            lines.append(f'M{xx} {mm(bottom-.9)}v1.8')
            guides.append(f'M{xx} {top}V{bottom}')
        labels = [label(at, bottom + 6.3, text, 'middle')
                  for at, text in spaced_tick_labels(axis_ticks, phone_size)]
        if unit:
            labels.append(label(width - 1, bottom + 13, unit, 'end'))
        marks, summary = [], []
        for i, (group, name) in enumerate(zip(groups, names)):
            y = top + row_height * (i + .5)
            labels.append(label(left - 2.5, y + 1, name, 'end'))
            five = box_five(group)
            low, q1, mid, q3, high = map(lambda v: mm(x(v)), five)
            upper, lower = mm(y - 3.8), mm(y + 3.8)
            path = (f'M{low} {mm(y)}H{q1}M{q3} {mm(y)}H{high}'
                    f'M{low} {mm(y-2)}v4M{high} {mm(y-2)}v4'
                    f'M{q1} {upper}H{q3}V{lower}H{q1}Z' if q1 != q3 else
                    f'M{low} {mm(y)}H{high}M{low} {mm(y-2)}v4M{high} {mm(y-2)}v4')
            marks.append(f'<path d="{path}M{mid} {upper}V{lower}"/>')
            if not block.get('teacher'):
                summary.append(f'{name}: ' + ', '.join(mm(value).replace('-', '−') for value in five))
        attrs = self.slot('draw') + ' data-pen="1"' if self.interactive and block.get('pen') else ''
        key = ' class="ws-key"' if block.get('teacher') else ''
        frame = f'<path d="{"".join(lines)}" fill="none" stroke="currentColor" stroke-width=".3"/>'
        grid = f'<path class="ws-chart-guides" d="{"".join(guides)}" fill="none"/>' if block.get('guides') else ''
        body = f'{grid}{frame}{"".join(labels)}<g{key} fill="none" stroke="currentColor" stroke-width=".45">{"".join(marks)}</g>'
        alt = '상자그림을 그리는 가로축: ' + ', '.join(names)
        if summary:
            alt = '상자그림: 최솟값, 제1사분위수, 중앙값, 제3사분위수, 최댓값. ' + '; '.join(summary)
        if unit:
            alt += f' {unit}'
        return (f'<figure class="ws-fig ws-chart"{attrs}><svg width="{mm(width)}mm" height="{mm(height)}mm" '
                f'viewBox="0 0 {mm(width)} {mm(height)}" data-chart="box" style="--ws-box-phone-font:{mm(phone_size)}px" '
                f'role="img" aria-label="{escape(alt)}">{body}</svg></figure>')

    def chart(self, block):
        kind = block['kind']
        if kind == 'box':
            return self.box_chart(block)
        width, height = min(block.get('width_mm', 130), self.content_mm), block.get('height_mm', 65)
        left, right, top, bottom = 14, width - 4, 4, height - 12
        labels, values = block.get('labels', []), block.get('values', [])
        if kind == 'scatter':
            x0, x1 = block['x']
            y0, y1 = block['y']
        else:
            plot_values = block.get('plot', {}).get('answer', [])
            y0 = min([0, *values, *plot_values])
            y1 = block.get('y_max', max([1, *values, *plot_values]))
            if kind in ('histogram', 'polygon'):
                y0 = 0
                if y1 <= 0:
                    y1 = max([1, *values, *plot_values])
            elif y0 >= y1:
                y0 = y1 - 1
            bins = block.get('bins', [])
            if kind == 'polygon':
                bins = [2*bins[0]-bins[1], *bins, 2*bins[-1]-bins[-2]]
            x0, x1 = (bins[0], bins[-1]) if bins else (0, max(len(values), 1))
        def sx(v):
            return left + (v - x0) / (x1 - x0) * (right - left)
        def sy(v):
            return bottom - (v - y0) / (y1 - y0) * (bottom - top)
        plot = block.get('plot')
        attrs = ''
        if plot:
            snap, answer = plot.get('snap', 1), plot['answer']
            attrs = self.slot('bars', answer, snap=snap, answer_html=escape(', '.join(map(str, answer))))
            if self.interactive:
                attrs += f' data-snap="{mm(snap)}"'
        axis = f'<path d="M{left} {top}V{mm(bottom)}H{mm(right)}" fill="none" stroke="currentColor" stroke-width="0.3"/>'
        if y0 < 0 < y1:
            axis += f'<path d="M{mm(left)} {mm(sy(0))}H{mm(right)}" fill="none" stroke="currentColor" stroke-width="0.2"/>'
        step = block.get('y_step', (y1-y0) / 5)
        n = min(200, math.floor((y1-y0) / step + 1e-9))
        guides = []
        blank_histogram = kind == 'histogram' and (plot or block.get('teacher'))
        if blank_histogram:
            guides.extend(f'M{mm(sx(v))} {top}V{mm(bottom)}' for v in bins[1:])
        font_size = 2.5
        y_values = [y0 + i * step for i in range(n+1)]
        y_ticks = [(sy(v), mm(v).replace('-', '−')) for v in y_values]
        if blank_histogram:
            guides.extend(f'M{left} {mm(y)}H{mm(right)}' for y, _ in y_ticks[1:])
        y_labels = spaced_tick_labels(y_ticks, font_size, vertical=True)
        extra_ticks = []
        if len(y_labels) < len(y_ticks) and not blank_histogram:
            extra_ticks.extend(f'M{mm(left-.65)} {mm(y)}h.65' for y, _ in y_ticks)
        for y, text in y_labels:
            axis += f'<text x="{left-1}" y="{mm(y+1)}" text-anchor="end" font-size="{font_size}">{escape(text)}</text>'
        if kind in ('bar', 'line'):
            x_labels = [(sx(i+.5), inspect_markup(label).plain) for i, label in enumerate(labels)]
        else:
            if kind == 'scatter':
                x_step = block.get('x_step', (x1-x0) / 5)
                bins = [x0 + i * x_step for i in range(min(200, math.floor((x1-x0) / x_step + 1e-9)) + 1)]
            x_ticks = [(sx(v), mm(v).replace('-', '−')) for v in bins]
            x_labels = spaced_tick_labels(x_ticks, font_size)
            if kind == 'polygon' or (len(x_labels) < len(x_ticks) and not blank_histogram):
                extra_ticks.extend(f'M{mm(x)} {mm(bottom-.6)}v1.2' for x, _ in x_ticks)
        if extra_ticks:
            axis += f'<path d="{"".join(extra_ticks)}" fill="none" stroke="currentColor" stroke-width="0.25"/>'
        for x, text in x_labels:
            axis += f'<text x="{mm(x)}" y="{mm(bottom+4)}" text-anchor="middle" font-size="{font_size}">{escape(text)}</text>'
        if block.get('x_label'):
            axis += f'<text x="{mm(right)}" y="{mm(height-1)}" text-anchor="end" font-size="2.7">{escape(inspect_markup(block["x_label"]).plain)}</text>'
        if block.get('y_label'):
            axis += f'<text x="1" y="3" font-size="2.7">{escape(inspect_markup(block["y_label"]).plain)}</text>'
        marks = ''
        if kind in ('bar', 'histogram'):
            series = plot['answer'] if plot else values
            for i, value in enumerate(series):
                start, end = (block['bins'][i:i+2] if kind == 'histogram' else (i, i+1))
                x1p, x2p = sx(start), sx(end)
                gap = (x2p-x1p) * (.15 if kind == 'bar' else 0)
                zero = sy(min(y1, max(y0, 0)))
                marks += (f'<rect x="{mm(x1p+gap)}" y="{mm(min(zero, sy(value)))}" width="{mm(x2p-x1p-2*gap)}" '
                          f'height="{mm(abs(zero-sy(value)))}" fill="none" stroke="currentColor" stroke-width="0.3"/>')
        else:
            pts = block['points'] if kind == 'scatter' else [[i+.5, value] for i, value in enumerate(values)]
            if kind == 'polygon':
                pts = [[(a+b)/2, value] for a, b, value in zip(bins, bins[1:], [0, *values, 0])]
            if kind != 'scatter':
                marks += '<polyline points="' + ' '.join(f'{mm(sx(x))},{mm(sy(y))}' for x, y in pts) + '" fill="none" stroke="currentColor" stroke-width="0.4"/>'
            marks += ''.join(f'<circle cx="{mm(sx(x))}" cy="{mm(sy(y))}" r="0.65"/>' for x, y in pts)
        if block.get('teacher') or plot:
            marks = f'<g class="ws-key">{marks}</g>'
        hits = ''
        if plot:
            edges = [(block['bins'][i], block['bins'][i+1]) if kind == 'histogram' else (i, i+1)
                     for i in range(len(values))]
            rectangles = []
            for i, (start, end) in enumerate(edges):
                label = (f'{mm(start)} 이상 {mm(end)} 미만' if kind == 'histogram' else inspect_markup(labels[i]).plain)
                x, edge = mm(sx(start)), mm(sx(end))
                rectangles.append(f'<rect class="ws-bar-hit" data-i="{i}" data-label="{escape(label)}" data-edge="{edge}" '
                                  f'x="{x}" y="{mm(top)}" width="{mm(float(edge)-float(x))}" height="{mm(bottom-top)}" fill="transparent"/>')
            hits = '<g class="ws-bars-hit">' + ''.join(rectangles) + '</g><g class="ws-plot"></g>'
        grid = f'<path class="ws-chart-guides" d="{"".join(guides)}" fill="none"/>' if guides else ''
        return (f'<figure class="ws-fig ws-chart"{attrs}><svg width="{mm(width)}mm" height="{mm(height)}mm" '
                f'viewBox="0 0 {mm(width)} {mm(height)}" data-chart="{kind}" data-ymap="{mm(y0)} {mm(y1)} {mm(bottom)} {mm(top)}" '
                f'role="img" aria-label="통계 그래프">{grid}{axis}{marks}{hits}</svg></figure>')

    def explore_figure(self, block, tree, params, caption=None, fit=None):
        xmin, xmax = block['x']
        ymin, ymax = block['y']
        unit = block['unit_mm']
        live = caption is None
        figure = self.grid({'mode': 'coord', 'x': block['x'], 'y': block['y'], 'unit_mm': unit}, live_map=live, fit=fit)
        expression = block['fn'].replace('**', '^').replace('*', '×').replace('-', '−')
        title = _visible_label(block.get('prompt', ''))
        values = ', '.join(f'{name}={mm(value).replace("-", "−")}' for name, value in params.items())
        label = ', '.join(filter(None, (title, f'y={expression} 그래프', values)))
        figure = figure.replace('aria-label="좌표평면"', f'aria-label="{escape(label)}"', 1)
        path = []
        active = False
        for i in range(241):
            x = xmin + (xmax-xmin)*i/240
            try:
                y = function_value(tree, x, params)
                if not ymin - 2*(ymax-ymin) <= y <= ymax + 2*(ymax-ymin):
                    active = False
                    continue
            except (ArithmeticError, ValueError, OverflowError):
                active = False
                continue
            path.append(f'{"L" if active else "M"}{mm((x-xmin)*unit)} {mm((ymax-y)*unit)}')
            active = True
        group = 'class="ws-plot"' if live else f'clip-path="url(#ws-grid-clip-{self.grid_index})"'
        graph = f'<g {group}><path d="{"".join(path)}" fill="none" stroke="currentColor" stroke-width="0.4"/></g>'
        figure = figure.replace('</svg>', graph + '</svg>')
        return figure if live else figure.replace('</figure>', f'<figcaption>{render_markup(caption)}</figcaption></figure>')

    @staticmethod
    def explore_table(xs, tree, rows, *, live):
        if not xs:
            return ''

        def cell(value, attr=''):
            return f'<td{attr}>{escape(value.replace("-", "−"))}</td>'

        header = '<th><math><mi>x</mi></math></th>' + ''.join(cell(mm(x), f' data-x="{mm(x)}"' if live else '') for x in xs)
        body = []
        for label, params in rows:
            cells = [f'<th>{render_markup(label)}</th>']
            for x in xs:
                try:
                    value = mm(function_value(tree, x, params) or 0)
                except (ArithmeticError, ValueError):
                    value = '—'
                cells.append(cell(value, f' data-y-for="{mm(x)}"' if live else ''))
            body.append(tr(''.join(cells)))
        cls = 'ws-explore-table' if live else 'ws-explore-print-table'
        return f'<table class="ws-data {cls}"><tbody>{tr(header)}{"".join(body)}</tbody></table>'

    def explore(self, block):
        params = {p['name']: p['value'] for p in block['params']}
        tree = function_tree(block['fn'], params)
        xs = block.get('table', [])
        figure = self.explore_figure(block, tree, params)
        values = self.explore_table(xs, tree, [('$y$', params)], live=True)
        if block.get('print'):
            snapshots, rows = [], []
            count = len(block['print'])
            graph_width = (block['x'][1] - block['x'][0]) * block['unit_mm'] + 4
            graph_height = (block['y'][1] - block['y'][0]) * block['unit_mm'] + 4.5
            # Use as many columns as can retain a usable graph, including narrow split columns.
            columns = 1
            for candidate in range(2, min(count, 4) + 1):
                available = (self.content_mm - (candidate - 1) * 1.5) / candidate
                scale = min(1, available / graph_width, 70 / graph_height)
                if graph_width * scale >= 28 and graph_height * scale >= 30:
                    columns = candidate
            fit = ((self.content_mm - (columns - 1) * 1.5) / columns, 70)
            for overrides in block['print']:
                scope = {**params, **overrides}
                label = ', '.join(f'${name}={mm(value)}$' for name, value in scope.items())
                snapshots.append(self.explore_figure(block, tree, scope, label, fit))
                rows.append((f'$y$ ({label})', scope))
            content = (f'<div class="ws-solve-only">{figure}{values}</div><div class="ws-print-only">'
                       f'<div class="ws-explore-snapshots" style="--ws-snapshot-cols:{columns}">{"".join(snapshots)}</div>'
                       f'{self.explore_table(xs, tree, rows, live=False)}</div>')
        else:
            note = '<p class="ws-print-only ws-explore-note">화면에서는 값을 바꾸며 살펴볼 수 있습니다.</p>'
            content = figure + values + note
        attrs = f' data-fn="{escape(block["fn"])}" data-params="{escape(json.dumps(block["params"], ensure_ascii=False, separators=(",", ":")))}" data-table="{escape(json.dumps(xs, separators=(",", ":")))}"'
        prompt = editable('p', block['prompt'], 'ws-explore-prompt') if 'prompt' in block else ''
        return f'<div class="ws-explore"{attrs}>{prompt}{content}</div>'

    def video(self, block):
        title = editable('span', block['title'], 'ws-video-title')
        caption = editable('span', block['caption']) if block.get('caption') else ''
        if 'url' in block:
            ident = youtube_id(block['url'])
            url = f'https://youtu.be/{ident}'
            qr = ''
            try:
                import segno
                output = BytesIO()
                segno.make(url).save(output, kind='svg', xmldecl=False)
                qr = f'<div class="ws-video-qr ws-print-only">{output.getvalue().decode("utf-8")}</div>'
            except ImportError:
                try:
                    import qrcode
                    import qrcode.image.svg
                    output = BytesIO()
                    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(output)
                    svg = output.getvalue().decode('utf-8')
                    qr = f'<div class="ws-video-qr ws-print-only">{svg[svg.find("<svg"):]}</div>'
                except ImportError:
                    pass
            return (f'<figure class="ws-video" data-yt="{ident}" data-start="{block.get("start", 0)}">'
                    f'<div class="ws-video-frame ws-solve-only"></div><figcaption>{title} '
                    f'<span class="ws-video-url">{url}</span> {caption}</figcaption>{qr}</figure>')
        source = data_image('video/mp4', image_path(self.folder, block['src']).read_bytes())
        return (f'<figure class="ws-video" data-local="1"><video class="ws-solve-only" controls preload="metadata" src="{source}"></video>'
                f'<figcaption>{title} {caption}</figcaption></figure>')

    def block(self, block, item_id):
        kind = block['type']
        if kind == 'notice':
            label = editable('th', block['label'], 'ws-lab')
            lines = ''.join(editable('p', line) for line in block['lines'])
            return table('ws-box ws-notice', (self.label_mm, self.content_mm - self.label_mm), tr(f'{label}<td>{lines}</td>'))
        if kind == 'nameband':
            return table('ws-band', (60, self.content_mm - 60), tr(editable('td', block['label']) + f'<td class="ws-head-name">{self.name_line()}</td>'))
        if kind == 'situation':
            return self.situation(block)
        if kind == 'question':
            number = ''
            if block.get('number', True):
                self.number += 1
                self.first_number.setdefault(item_id, self.number)
                number = f'<span class="ws-qn">{self.number}.</span> '
            return f'<p class="ws-q">{number}{editable("span", block["text"], "ws-q-text")}</p>'
        if kind == 'answer':
            return self.answer_table(block)
        if kind == 'steps':
            count = len(block['columns'])
            width = (self.content_mm - 10) / count
            head = '<thead>' + tr('<th></th>' + ''.join(editable('th', text) for text in block['columns'])) + '</thead>'
            body = ''.join(tr(f'<th class="ws-step-n">{chr(0x2460 + i)}</th>' + ''.join(
                answer_cell(editable('div', ans, 'ws-key'), lines=block.get('lines', 2), kind_explicit=self.interactive,
                            slot=self.answer_slot(ans, unit=inferred_unit('', inspect_markup(column).plain)))
                for ans, column in zip(row, block['columns'])))
                for i, row in enumerate(block['answers']))
            return table('ws-steps', (10, *([width] * count)), body, head)
        if kind == 'choices':
            numbers = block['answer'] if isinstance(block['answer'], list) else [block['answer']]
            count = block.get('cols', min(5, len(block['options'])))
            slot = self.slot('choice', sorted(numbers), count=len(block['options']),
                             answer_html=', '.join(str(n) for n in sorted(numbers)))
            multi = ' data-multi="1"' if isinstance(block['answer'], list) else ''
            options = []
            for i, text in enumerate(block['options'], 1):
                cls = ' class="ws-key-choice"' if i in numbers else ''
                number = f' data-n="{i}"' if self.interactive else ''
                options.append(f'<li{cls}{number}><span class="ws-cn">{chr(0x245F + i)}</span> {editable("span", text)}</li>')
            return f'<ol class="ws-choices" style="--cols:{count}"{slot}{multi if self.interactive else ""}>{"".join(options)}</ol>'
        if kind == 'table':
            header = block['header']
            widths = block.get('widths_mm', [self.content_mm / len(header)] * len(header))
            if self.content_mm != 188 and sum(widths) > self.content_mm + 1e-9:
                widths = [width * self.content_mm / sum(widths) for width in widths]
            widest = max((blank.width_mm for row in (header, *block['rows']) for text in row
                          for blank in inspect_markup(text).blanks if blank.width_mm is not None), default=None)
            unit = normalized_unit(block.get('unit'))
            hook = blank_hook.get()

            def cell(tag, text, column='', row_label=''):
                # 칸 전체가 빈칸 하나면 밑줄을 겹쳐 긋지 않는다(ws-fill).
                whole = tag == 'td' and whole_blank(text)
                token = blank_hook.set(lambda a, markup, tail: hook(a, markup, tail, column, row_label, unit)) if hook else None
                try:
                    return editable(tag, text, 'ws-fill' if whole else '', table_blank_mm=widest)
                finally:
                    if token is not None:
                        blank_hook.reset(token)

            head = '<thead>' + tr(''.join(cell('th', text) for text in header)) + '</thead>'
            columns = [node.text(without_blanks=True) for node in _Document(head).root.walk() if node.tag == 'th']
            rows = []
            for row in block['rows']:
                first = inspect_markup(row[0])
                label = '' if first.blanks else _Document(first.html).root.text()
                rows.append(tr(''.join(cell('td', text, column, label if i else '')
                                       for i, (text, column) in enumerate(zip(row, columns)))))
            attrs = _attrs_text({'data-table-unit': unit}) if unit else ''
            return table('ws-data', widths, ''.join(rows), head, attrs)
        if kind == 'figure':
            return self.picture(block)
        if kind == 'figures':
            return self.two_pictures(block)
        if kind == 'grid':
            return self.grid(block)
        if kind == 'ask':
            title = editable('span', block['tag'], 'ws-ask-tag') + ' ' + editable('span', block['text'])
            cell = answer_cell(editable('div', block['answer'], 'ws-key'), lines=block['lines'], grow=block.get('grow', False),
                               kind_explicit=self.interactive, slot=self.answer_slot(block['answer']))
            return table('ws-ask', (self.content_mm,), tr(f'<th>{title}</th>') + tr(cell))
        if kind == 'heading':
            icon = f' data-icon="{escape(block["icon"])}"' if block.get('icon') else ''
            tag = editable('span', block['tag'], 'ws-heading-tag')
            text = editable('span', block['text'], 'ws-heading-text')
            image = self.wrap_private(block['image'], self.image(block['image'], 30).replace('<img ', '<img class="ws-deco" ', 1)) if block.get('image') else ''
            return f'<div class="ws-heading"{icon}>{tag}{text}{image}</div>'
        if kind == 'callout':
            icons = {'개념': 'bulb', '생각': 'search', '주의': 'warn', '정리': 'check', '예시': 'book'}
            title = editable('p', block['title'], 'ws-callout-title') if block.get('title') else ''
            lines = ''.join(editable('p', line) for line in block['lines'])
            image = self.wrap_private(block['image'], self.image(block['image'], self.content_mm).replace('<img ', '<img class="ws-deco" ', 1)) if block.get('image') else ''
            return f'<div class="ws-callout" data-kind="{escape(block["kind"])}" data-icon="{icons[block["kind"]]}">{title}{lines}{image}</div>'
        if kind == 'split':
            ratio = block.get('ratio', '1:1')
            left, right = map(int, ratio.split(':'))
            original = self.content_mm
            left_width, right_width = split_widths_mm(original, ratio)
            try:
                self.content_mm = left_width
                lhs = '\n'.join(self.block(child, item_id) for child in block['left'])
                self.content_mm = right_width
                rhs = '\n'.join(self.block(child, item_id) for child in block['right'])
            finally:
                self.content_mm = original
            cls = 'ws-split ws-solve-only' if solve_only(block) else 'ws-split'
            return (f'<div class="{cls}" data-ratio="{ratio}" style="--split-l:{left};--split-r:{right}">'
                    f'<div class="ws-split-col">{lhs}</div><div class="ws-split-col">{rhs}</div></div>')
        if kind == 'ox':
            return self.ox(block)
        if kind == 'match':
            return self.match(block, item_id)
        if kind == 'order':
            return self.order(block, item_id)
        if kind == 'flow':
            nodes = []
            for i, value in enumerate(block['nodes']):
                if i:
                    arrows = block.get('arrows', [])
                    label = editable('span', arrows[i-1]) if arrows else ''
                    nodes.append(f'<div class="ws-flow-arrow">{label}</div>')
                nodes.append(editable('div', value, 'ws-flow-node'))
            return '<div class="ws-flow">' + ''.join(nodes) + '</div>'
        if kind == 'numberline':
            return self.numberline(block)
        if kind == 'chart':
            return self.chart(block)
        if kind == 'explore':
            return self.explore(block)
        if kind == 'video':
            return self.video(block)
        if kind == 'checklist':
            slot = self.slot('self')
            title = editable('p', block['title'], 'ws-checklist-title') if block.get('title') else ''
            entries = ''.join(f'<li><span class="ws-check-box"></span>{editable("span", value)}</li>' for value in block['items'])
            cls = 'ws-checklist ws-solve-only' if solve_only(block) else 'ws-checklist'
            return f'<div class="{cls}"{slot}>{title}<ul>{entries}</ul></div>'
        return editable('p', block['text'], 'ws-text')

    def render_items(self):
        items = [self.head()]
        for item in self.data['items']:
            id_ = item['id']
            self.item_id, self.slot_index = id_, 0
            token = blank_hook.set(self.blank_slot if self.interactive else None)
            try:
                parts = '\n'.join(self.block(block, id_) for block in item['blocks'])
                if self.interactive and item.get('hints'):
                    parts += '<div class="ws-hints ws-solve-only">' + ''.join(editable('p', hint, 'ws-hint') for hint in item['hints']) + '</div>'
            finally:
                blank_hook.reset(token)
            attrs = ' data-break="1"' if item.get('break') else ''
            if self.interactive:
                explicit = item.get('fraction_form')
                form = explicit or fraction_requirement(_fraction_prompt_texts(item['blocks']))
                attrs += _attrs_text({'data-fraction-form': form})
                if explicit:
                    attrs += ' data-fraction-form-explicit="1"'
            items.append(section(id_, parts, attrs, screen_only=all(solve_only(b) for b in item['blocks'])))
        return '\n'.join(items)

    def render_teacher(self):
        teacher = self.data.get('teacher', {})
        memo = teacher.get('memo', [])
        items = []
        solution_ids = set(self.first_number) | {item['id'] for item in self.data['items']}
        reserved = solution_ids.copy()

        def private_id(preferred):
            candidate = preferred
            while candidate in reserved:
                candidate = 't-' + candidate
            reserved.add(candidate)
            return candidate

        if memo:
            lines = ''.join(editable('li', p) for p in memo)
            items.append(section(private_id('t-memo'), f'<h2 class="ws-t-h">교사용 메모</h2><ul class="ws-t-list">{lines}</ul>'))
        students = {item['id']: item for item in self.data['items']}
        for solution in teacher.get('solutions', []):
            item = solution['item']
            video = solution.get('video')
            video = {'yt': youtube_id(video['url']), 'start': video.get('start', 0)} if video else None
            if self.published:
                self.reveal_data['items'][item] = solution_public(
                    solution['answer'], solution.get('steps', []), solution.get('rubric', []), video)
            label = str(self.first_number[item]) + '번' if item in self.first_number else _solution_label(students[item])
            parts = [f'<h3 class="ws-t-q">{escape(label)}</h3>',
                     f'<p class="ws-t-ans"><b>예시 답안</b> {editable("span", solution["answer"])}</p>']
            if solution.get('steps'):
                steps = ''.join(editable('li', step) for step in solution['steps'])
                parts.append(f'<ol class="ws-t-steps">{steps}</ol>')
            for key, cls, heading in (('rubric', 'ws-t-rubric', '채점 요소'), ('alternatives', 'ws-t-alt', '다른 타당한 답'),
                                      ('misconceptions', 'ws-t-mis', '예상 오개념')):
                for text in solution.get(key, []):
                    parts.append(f'<p class="{cls}"><b>{heading}</b> {editable("span", text)}</p>')
            attrs = {'data-solution-for': item}
            if video:
                attrs['data-solution-video'] = json.dumps(video, separators=(',', ':'))
            items.append(section(private_id('t-' + item), '\n'.join(parts), _attrs_text(attrs)))
        return '\n'.join(items)


def font_chars(data):
    # KS X 1001's Hangul rows: B0A1 to C8FE, 25 * 94 = 2,350 glyphs.
    chars = {bytes((row, col)).decode('euc_kr') for row in range(0xB0, 0xC9) for col in range(0xA1, 0xFF)}
    chars.update(chr(i) for i in range(32, 127))
    chars.update(chr(i) for i in range(0x2460, 0x2474))
    chars.update(MARKS + '□○△·…°′″−√⌒⏜̅‘’“”–—₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹')
    chars.update(SYMBOLS.values())
    chars.update(_ITALIC_LATIN.values())
    chars.add('↔')
    # Includes generated interface and header labels, even on a tiny manuscript.
    chars.update('수행평가영역단원학년도학기점수학생확인교사용메모예시답안채점요소다른타당한답예상오개념이름학번짝활동좌표평면모눈'
                 '확인 정답 보기 풀이 보기 힌트 맞음 다시 생각해 봐요 답을 넣어 저장 내 답 지우기 학생 화면 미리보기 교사 화면으로 '
                 'A4로 보기 풀기 화면 화면에서는 값을 바꾸며 살펴볼 수 있습니다 예시 답안과 비교 맞힌 수 동영상 재생 지우개 모두 지우기'
                 '확인 필요 예상한 답과 달라요 같은 뜻이면 선생님께 확인받아요 먼저 답을 써 보세요 안에 순서대로 쓰시오 써 봅시다'
                 '이 기기에 전에 쓴 답이 있어요 마지막 저장 이 기기에서 내가 쓰던 답 열기 파일에 담긴 답 열기 빈 활동지로 시작 '
                 '이 기기에 저장된 이름과 답을 지울까요 지우고 시작 취소 표를 좌우로 밀어 볼 수 있어요'
                 '각 계급에서 직사각형의 윗변이 올 높이를 눌러요 높이 미입력 각 막대의 높이를 모두 정해 보세요 점을 모두 찍어 보세요'
                 '서로 다른 두 점을 찍어 보세요 짝을 모두 이어 보세요 순서를 정해 보세요 서술 칸은 직접 확인')

    def add_text(entry):
        if isinstance(entry, str):
            chars.update(c for c in entry if c.isprintable())
        elif isinstance(entry, list):
            for value in entry:
                add_text(value)
        elif isinstance(entry, dict):
            for value in entry.values():
                add_text(value)

    add_text(data)
    return ''.join(sorted(chars))


def font_styles(data, warnings):
    directory = Path(os.environ.get('SHEET_FONT_DIR') or (Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'))
    # 수업형은 모든 글을 고딕으로 쓰므로 명조를 넣지 않는다(파일이 약 0.7MB 가벼워진다).
    kind = (data.get('meta') or {}).get('kind') or '수업'
    faces = ('Sans',) if kind == '수업' else ('Sans', 'Serif')
    sources = [(directory / f'Noto{face}KR-VF.ttf', f'Sheet {face}') for face in faces]
    if not all(path.is_file() for path, _ in sources):
        warnings.append('Noto Sans KR·Noto Serif KR 가변 글꼴을 찾을 수 없어 대체 글꼴을 사용합니다. SHEET_FONT_DIR를 확인하세요.')
        return '', 0
    try:
        import fontTools
        from fontTools import subset
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont
    except ImportError:
        doctor = python_sheet_command('python', 'doctor')
        message = ('fontTools를 불러올 수 없어 글꼴을 내장하지 않았습니다. 대체 글꼴을 사용합니다. '
                   f'파이썬 환경을 확인하려면 {doctor}를 실행하세요.')
        alternate = find_usable_python()
        if alternate:
            command = python_sheet_command(alternate, 'build "<활동지 폴더>"')
            message += f' 필요한 모듈을 갖춘 다른 파이썬으로 다시 조립할 수 있습니다: {command}'
        warnings.append(message)
        return '', 0
    base = os.environ.get('LOCALAPPDATA') or (Path.home() / '.cache')
    cache = Path(base) / TOOL / 'fonts'
    chars = font_chars(data)
    char_hash = sha256(chars.encode('utf-8'))
    styles = []
    total = 0
    try:
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            warnings.append('글꼴 캐시에 쓸 수 없어 이번 파일에만 내장합니다.')
        for path, family in sources:
            # Cache invalidates on source font, subset characters, weight or converter changes.
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            for weight in (400, 700):
                fingerprint = sha256(f'v1:{fontTools.__version__}:{digest.hexdigest()}:{char_hash}:{weight}'.encode())
                cached = cache / f'{family.replace(" ", "-")}-{weight}-{fingerprint}.woff'
                try:
                    binary = cached.read_bytes()
                except OSError:
                    binary = b''
                if not binary.startswith(b'wOFF') or len(binary) < 44 or int.from_bytes(binary[8:12], 'big') != len(binary):
                    with TTFont(path, recalcTimestamp=False) as font:
                        location = {axis.axisTag: weight if axis.axisTag == 'wght' else axis.defaultValue
                                    for axis in font['fvar'].axes}
                        instantiateVariableFont(font, location, inplace=True)
                        config = subset.Options()
                        config.name_IDs = sorted(set(config.name_IDs) | {13, 14})
                        cutter = subset.Subsetter(options=config)
                        cutter.populate(text=chars)
                        cutter.subset(font)
                        font.flavor = 'woff'
                        with BytesIO() as stream:
                            font.save(stream)
                            binary = stream.getvalue()
                    try:
                        atomic_write(cached, binary)
                    except OSError:
                        warnings.append('글꼴 캐시에 쓸 수 없어 이번 파일에만 내장합니다.')
                total += len(binary)
                # Noto's broad fallback metrics overstate the bounds of the worksheet's
                # 9.5 pt question line; normalize the inline metrics to its fixed leading.
                styles.append(f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:normal;'
                              'ascent-override:98%;descent-override:22%;line-gap-override:0%;'
                              f'src:url({data_image("font/woff", binary)}) format("woff");}}')
    except Exception as error:
        # The optional converter exposes different exceptions for table/codec failures.
        warnings.append(f'글꼴 내장 실패({type(error).__name__}): 대체 글꼴을 사용합니다.')
        return '', 0
    return '\n'.join(styles), total


VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())
PRIVATE_CLASSES = frozenset(('ws-key', 'ws-teacher-only'))


class StudentFilter(HTMLParser):
    """Remove actual DOM nodes/attributes, never regex-match answer text or engine code."""
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0, *(m.end() for m in re.finditer('\n', source))]
        self.stack = []
        self.cut_start = None
        self.patches = []
        self.role = None
        self.sheet = False
        self.has_src = False
        self.title_start = None
        self.has_runtime = False
        self.grading = self.published = False

    def absolute_position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def opening(self, tag, attrs, single):
        values = dict(attrs)
        classes = (values.get('class') or '').split()
        start = self.absolute_position()
        maybe_end = start + len(self.get_starttag_text())
        if tag == 'html':
            self.role, self.sheet = values.get('data-role'), values.get('data-sheet') == '1'
            self.grading, self.published = student_policy('data-interactive' in values, values.get('data-reveal', '시도 후'))
        drop = bool(PRIVATE_CLASSES.intersection(classes) or values.get('id') == 'sheet-teacher'
                    or (values.get('id') == 'sheet-reveal' and not self.published))
        if values.get('id') == 'sheet-src' and not drop and self.cut_start is None:
            self.has_src = True
        if values.get('id') == 'sheet-pages' or 'data-sheet-runtime' in values:
            self.has_runtime = True
        discard = self.cut_start is None and drop
        if not single and tag not in VOID:
            self.stack.append((tag, discard))
        if self.cut_start is not None:
            return
        if discard:
            if single or tag in VOID:
                self.patches.append((start, maybe_end, ''))
            else:
                self.cut_start = start
            return
        if tag == 'title':
            self.title_start = maybe_end
        forbidden = {'data-src', 'data-check'}
        if not self.grading:
            forbidden |= {'data-check-hash', 'data-check-unit'}
        if tag == 'html':
            forbidden |= {'data-answers', 'data-sheet-edit', 'data-sheet-preparing'}
        if tag == 'html' or forbidden.intersection(values) or 'ws-key-choice' in classes:
            clean = []
            for key, value in attrs:
                if key in forbidden:
                    continue
                if tag == 'html' and key == 'data-role':
                    value = 'student'
                if key == 'class' and 'ws-key-choice' in classes:
                    value = ' '.join(c for c in classes if c != 'ws-key-choice')
                    if not value:
                        continue
                clean.append(f' {key}="{escape(value)}"' if value is not None else f' {key}')
            # Generated and browser-saved editable nodes are HTML; untouched foreign tags stay byte-identical.
            self.patches.append((start, maybe_end, '<' + tag + ''.join(clean) + ('/>' if single else '>')))

    def handle_starttag(self, tag, attrs):
        self.opening(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self.opening(tag, attrs, True)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise ValueError('저장된 활동지의 태그 구조가 맞지 않습니다. 브라우저의 저장 버튼으로 다시 저장하세요.')
        _, discard = self.stack.pop()
        offset = self.absolute_position()
        if discard:
            end = self.source.index('>', offset) + 1
            self.patches.append((self.cut_start, end, ''))
            self.cut_start = None
        elif tag == 'title' and self.title_start is not None:
            original = self.source[self.title_start:offset]
            clean = re.sub(r'\s*\(교사용\)\s*$', '', original)
            if clean != original:
                self.patches.append((self.title_start, offset, clean))

    def student(self):
        self.feed(self.source)
        self.close()
        if self.stack or not (self.sheet and self.role == 'teacher' and self.has_src) or self.has_runtime:
            raise ValueError('저장된 교사용 활동지가 아닙니다. 브라우저의 저장 버튼으로 교사용 HTML을 저장하세요.')
        output, last = [], 0
        for start, end, value in self.patches:
            output.extend((self.source[last:start], value))
            last = end
        output.append(self.source[last:])
        return ''.join(output)


def strip_teacher(source):
    return StudentFilter(source).student()


@dataclass
class _Element:
    tag: str
    attrs: dict
    start: int
    body: int
    end: int
    children: list = field(default_factory=list)
    parent: '_Element' = field(default=None, repr=False, compare=False)

    def has(self, cls):
        return cls in (self.attrs.get('class') or '').split()

    def walk(self, *, public=False):
        for child in self.elements():
            if public and PRIVATE_CLASSES.intersection((child.attrs.get('class') or '').split()):
                continue
            yield child
            yield from child.walk(public=public)

    def elements(self):
        return [child for child in self.children if isinstance(child, _Element)]

    def closest(self, predicate):
        el = self
        while el is not None and not predicate(el):
            el = el.parent
        return el

    def text(self, *, without_blanks=False):
        if without_blanks and self.has('ws-blank'):
            return ''
        return ''.join(child.text(without_blanks=without_blanks) if isinstance(child, _Element) else child
                       for child in self.children)


class _Document(HTMLParser):
    """Read the explicit, balanced DOM written by build or the browser editor."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0, *(m.end() for m in re.finditer('\n', source))]
        self.root = _Element('', {}, 0, 0, len(source))
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def _open(self, tag, attrs, single):
        start = self.position()
        end = start + len(self.get_starttag_text())
        el = _Element(tag, dict(attrs), start, end, end, parent=self.stack[-1])
        self.stack[-1].children.append(el)
        if not single and tag not in VOID:
            self.stack.append(el)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, True)

    def handle_endtag(self, tag):
        if len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop().end = self.position()

    def handle_data(self, data):
        children = self.stack[-1].children
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            children.append(data)

    def lookup(self, attr, value):
        return next((el for el in self.root.walk() if el.attrs.get(attr) == value), None)

    def inner(self, el):
        return self.source[el.body:el.end]

    def metadata(self, name):
        el = self.lookup('name', name)
        return el.attrs.get('content') if el is not None else None


def _reveal_tag(salt, payload):
    return f'<script type="application/octet-stream" id="sheet-reveal">{reveal(salt, payload)}</script>'


def _read_reveal(doc, salt):
    script = doc.lookup('id', 'sheet-reveal')
    if script is None:
        return {'slots': {}, 'items': {}}
    try:
        raw = base64.b64decode(doc.inner(script).strip(), validate=True)
        key = hashlib.sha256((salt + '|reveal').encode()).digest()
        payload = json.loads(bytes(byte ^ key[i % len(key)] for i, byte in enumerate(raw)))
        if all(isinstance(payload[name], dict) for name in ('slots', 'items')):
            return payload
    except (TypeError, ValueError, KeyError):
        pass
    raise ValueError('교사용 공개 자료의 형식을 읽을 수 없습니다.')


def _table_unit(el, tail=''):
    cell = el.closest(lambda node: node.tag in ('td', 'th'))
    if cell is None:
        return inferred_unit(tail)
    row = cell.parent
    table = row.closest(lambda node: node.tag == 'table')
    if table is None:
        return inferred_unit(tail)
    cells = [node for node in row.elements() if node.tag in ('td', 'th')]
    index = cells.index(cell)
    column = row_label = ''
    if table.has('ws-data') or table.has('ws-steps'):
        # Header blanks use the table default, not other cells of the header.
        head = next((node for node in table.elements() if node.tag == 'thead'), None)
        if head and head.elements() and row.parent is not head:
            headings = head.elements()[0].elements()
            if index < len(headings):
                column = headings[index].text(without_blanks=True)
        if table.has('ws-data') and row.parent is not head and index and not any(node.has('ws-blank') for node in cells[0].walk()):
            row_label = cells[0].text()
    elif table.has('ws-ans-table'):
        rows = row.parent.elements()
        row_index = rows.index(row)
        for distance, candidate in enumerate(reversed(rows[:row_index + 1])):
            lab = next((node for node in candidate.elements() if node.has('ws-lab')), None)
            if lab is not None:
                if distance < int(lab.attrs.get('rowspan', '1')):
                    label = next((node for node in lab.walk() if node.has('ws-lab-text')), lab)
                    row_label = label.text(without_blanks=True)
                break
    return inferred_unit(tail, column, row_label, table.attrs.get('data-table-unit') if table.has('ws-data') else '')


_TEXT_ATTRS = ('data-check-kind', 'data-check-hash', 'data-check-unit', 'data-check', 'data-input-mode', 'data-writing-shape')


def _fraction_field(el):
    return 'data-src' in el.attrs and (el.has('ws-q-text') or el.has('ws-sit-title') or el.has('ws-sit-note')
        or el.parent.has('ws-sit-text')
        or (not el.has('ws-ask-tag') and el.parent.tag == 'th' and el.closest(lambda node: node.has('ws-ask'))))
_NON_GRADING = frozenset(('open', 'draw', 'self', 'name'))


def _refresh_grading(doc, salt, grading):
    """Read editable sources, keeping the DOM positions and their saved slot names."""
    public = doc.lookup('id', 'sheet-src')
    patches, displays, seen, paired = [], {}, set(), set()

    def update(el, values, clear):
        attrs = {key: value for key, value in el.attrs.items() if key not in clear}
        attrs.update(values)
        if attrs != el.attrs:
            patches.append((el.start, el.body, '<' + el.tag + _attrs_text(attrs) + '>'))

    for section in public.elements():
        if not section.has('ws-item') or section.has('ws-teacher-only'):
            continue
        item = section.attrs.get('data-id') or '(이름 없는 문항)'
        elements = list(section.walk(public=True))
        if item != 'head':
            form = section.attrs.get('data-fraction-form')
            if section.attrs.get('data-fraction-form-explicit') == '1':
                if form not in ('기약분수', '분수', '자유'):
                    raise ValueError(f'{item}: fraction-form: 문항의 분수 요구 꼴이 올바르지 않습니다.')
            else:
                fields = [el for el in elements if _fraction_field(el)]
                form = fraction_requirement(plain(inspect_markup(el.attrs['data-src']).public_source) for el in fields)
                if not fields and section.attrs.get('data-reduced-fractions') == '1':
                    form = '기약분수'
            update(section, {'data-fraction-form': form}, ('data-reduced-fractions',))
        index = 0
        for el in elements:
            if 'data-slot' not in el.attrs and not (el.has('ws-blank') or el.has('ws-ans')):
                continue
            name = el.attrs.get('data-slot') or '(이름 없음)'
            if item == 'head' and el.attrs.get('data-check-kind') == 'name':
                valid = name.startswith('head.') and bool(name.removeprefix('head.'))
            else:
                index += 1
                valid = name == f'{item}.{index}'
            if not valid or name in seen:
                label = '빈칸' if el.has('ws-blank') else '답 칸' if el.has('ws-ans') else '판별 자리'
                raise ValueError(f'{item} 문항 {label} {name}: 자리 이름이 맞지 않거나 중복됩니다.')
            seen.add(name)
        for el in elements:
            if 'data-src' in el.attrs:
                dom_blanks = [node for node in el.walk(public=True) if node.has('ws-blank')
                              and node.parent.closest(lambda node: 'data-src' in node.attrs) is el]
                source_blanks = []

                def collect(answers, rendered, tail):
                    source_blanks.append((answers, rendered, tail))
                    return ''

                token = blank_hook.set(collect)
                try:
                    parsed = inspect_markup(el.attrs['data-src'], interactive=True)
                except ValueError as error:
                    raise ValueError(f'{item} 문항 {el.tag} 글자 칸: {error}') from error
                finally:
                    blank_hook.reset(token)
                if len(parsed.blanks) != len(dom_blanks) or len(parsed.blanks) != len(source_blanks):
                    slots = ', '.join(node.attrs.get('data-slot') or '(이름 없음)' for node in dom_blanks)
                    raise ValueError(f'{item} 문항 {el.tag} 빈칸{f" ({slots})" if slots else ""}: 원본 표기와 본문의 빈칸 수 또는 정답이 맞지 않습니다.')
                for node, blank, (answers, rendered, tail) in zip(dom_blanks, parsed.blanks, source_blanks):
                    if (node.attrs.get('data-mark') or '') != blank.mark:
                        raise ValueError(f'{item} 문항 {node.attrs.get("data-slot") or "빈칸"}: 빈칸 기호가 원본 표기와 다릅니다.')
                    name = node.attrs['data-slot']
                    paired.add(node.start)
                    update(node, _text_slot_attrs(name, answers[0], salt, grading, accept=answers[1:],
                                                  unit=_table_unit(el, tail), blank=True, rendered=rendered), _TEXT_ATTRS)
                    displays[name] = {'answer_html': rendered}
            if el.has('ws-blank'):
                if el.start not in paired:
                    raise ValueError(f'{item} 문항 {el.attrs.get("data-slot") or "빈칸"}: 빈칸의 원본 표기(data-src)를 찾을 수 없습니다.')
                continue
            if 'data-slot' not in el.attrs and not el.has('ws-ans'):
                continue
            name, kind = el.attrs['data-slot'], el.attrs.get('data-check-kind')
            try:
                if el.has('ws-ans') and el.attrs.get('data-kind') != 'draw':
                    key = next((node for node in el.elements() if node.has('ws-key')), None)
                    if key is None or 'data-src' not in key.attrs:
                        raise ValueError('답 칸의 원본 표기(data-src)를 찾을 수 없습니다.')
                    source = key.attrs['data-src']
                    saved = json.loads(el.attrs.get('data-check') or '{}')
                    mode, accept = saved.get('mode', 'auto'), saved.get('accept', [])
                    if mode not in ('auto', 'off', 'number', 'text') or not isinstance(accept, list) or not all(isinstance(v, str) for v in accept):
                        raise ValueError('답 칸의 판별 방식이나 허용 답이 올바르지 않습니다.')
                    if not plain(source).strip():
                        raise ValueError('답 칸의 정답이 비어 있습니다.')
                    rendered = render_markup(source)
                    update(el, _text_slot_attrs(name, source, salt, grading, mode=mode, accept=accept,
                                                unit=_table_unit(el), rendered=rendered), _TEXT_ATTRS)
                    displays[name] = {'answer_html': rendered}
                elif kind not in _NON_GRADING:
                    saved = json.loads(el.attrs['data-check'])
                    snap = float(el.attrs['data-snap']) if 'data-snap' in el.attrs else None
                    right, wrong = (canonical(kind, saved[key], snap) for key in ('right', 'wrong'))
                    if right is None or wrong in (None, right):
                        raise ValueError('정답과 시험용 오답을 읽을 수 없습니다.')
                    values = {'data-check-hash': check_hash(salt, kind, right)} if grading else {}
                    update(el, values, ('data-check-hash', 'data-check-unit'))
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise ValueError(f'{item} 문항 {name}: 판별 자료를 다시 만들 수 없습니다. {error}') from error
    if not patches:
        return doc, displays
    parts, last = [], 0
    for start, end, value in sorted(patches):
        parts.extend((doc.source[last:start], value))
        last = end
    parts.append(doc.source[last:])
    return _Document(''.join(parts)), displays


def _rebuild_reveal(doc, previous, displays):
    public = doc.lookup('id', 'sheet-src')
    slots = {}
    for el in public.walk(public=True):
        slot = el.attrs.get('data-slot')
        if slot in displays:
            slots[slot] = displays[slot]
        elif slot and el.attrs.get('data-check-kind') not in _NON_GRADING:
            # Non-text controls can have display forms not in their DOM (e.g. a line's source equation).
            saved = json.loads(el.attrs.get('data-check') or '{}')
            if saved.get('answer_html'):
                slots[slot] = {'answer_html': saved['answer_html']}
            elif slot in previous['slots']:
                slots[slot] = previous['slots'][slot]
    items = {}
    teacher = doc.lookup('id', 'sheet-teacher')
    if teacher is None:
        return {'slots': slots, 'items': items}
    ids = {el.attrs['data-id'] for el in public.walk() if el.has('ws-item') and 'data-id' in el.attrs}
    sections = [el for el in teacher.children if isinstance(el, _Element) and el.has('ws-item')]
    # Older files have only a collision-safe private ID; resolve it in the assembler's original order.
    reserved = ids | {el.attrs.get('data-id') for el in sections if any(c.has('ws-t-list') for c in el.walk())}
    legacy = {}
    for item in previous['items']:
        candidate = 't-' + item
        while candidate in reserved:
            candidate = 't-' + candidate
        reserved.add(candidate)
        legacy[candidate] = item
    for section in sections:
        item = section.attrs.get('data-solution-for') or legacy.get(section.attrs.get('data-id'))
        if item not in ids:
            continue
        fields = [el for el in section.children if isinstance(el, _Element)]
        answer = next((child.attrs['data-src'] for el in fields if el.has('ws-t-ans')
                       for child in el.walk() if 'data-src' in child.attrs), None)
        if answer is None:
            continue
        steps = [child.attrs['data-src'] for el in fields if el.has('ws-t-steps')
                 for child in el.walk() if child.tag == 'li' and 'data-src' in child.attrs]
        rubric = [child.attrs['data-src'] for el in fields if el.has('ws-t-rubric')
                  for child in el.walk() if (child.attrs.get('data-src') or '').strip()]
        video = section.attrs.get('data-solution-video')
        video = json.loads(video) if video else previous['items'].get(item, {}).get('video')
        items[item] = solution_public(answer, steps, rubric, video)
    return {'slots': slots, 'items': items}


def _edition_body(doc, *, with_hashes=False):
    # Compare rendered content, not HTML quoting/attribute order or inter-item formatting.
    def shape(el):
        attrs = {k: v for k, v in el.attrs.items() if k != 'data-check-hash' or with_hashes}
        return [el.tag, attrs, [shape(child) if isinstance(child, _Element) else child for child in el.children]]

    root = next(el for el in doc.root.children if isinstance(el, _Element) and el.tag == 'html')
    public = doc.lookup('id', 'sheet-src')
    user_style = doc.lookup('id', 'sheet-user')
    return [doc.inner(user_style) if user_style else '',
            {k: v for k, v in root.attrs.items() if k in ('data-kind', 'data-columns', 'data-interactive', 'data-reveal', 'data-theme', 'data-accent')},
            [shape(el) for el in public.children if isinstance(el, _Element)]]


def _edition(source, student_doc, public):
    # Hash the unsalted grading inputs, not the randomly salted verifier or the test's wrong examples.
    answers = []
    for el in source.lookup('id', 'sheet-src').walk(public=True):
        if el.attrs.get('data-check-hash') and el.attrs.get('data-check'):
            row = json.loads(el.attrs['data-check'])
            answers.append((el.attrs.get('data-slot'), row.get('right'), row.get('accept', [])))
    value = json.dumps([_edition_body(student_doc), answers, public], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return 'e-' + sha256(value.encode('utf-8'))[:32]


def _replace_metadata(doc, metadata):
    el = doc.lookup('name', 'sheet-build')
    content = escape(json.dumps(metadata, ensure_ascii=False, separators=(',', ':')))
    tag = f'<meta name="sheet-build" content="{content}">'
    if el:
        return doc.source[:el.start] + tag + doc.source[el.end:]
    head = next((el for el in doc.root.walk() if el.tag == 'head'), None)
    if head is None:
        raise ValueError('조립 기록을 넣을 head 요소가 없습니다.')
    return doc.source[:head.end] + tag + doc.source[head.end:]


def plan_warning_counts(issues):
    return dict(sorted(Counter(issue.code or 'uncoded' for issue in issues if issue.level == 'warning').items()))


def document_html(renderer, build_meta, fonts):
    css = (ROOT / 'assets/engine/sheet.css').read_text(encoding='utf-8')
    js = engine_script(ROOT)
    sync_script = ROOT / 'assets/engine/sync.js'
    sync_tag = ''
    if renderer.interactive and renderer.meta.get('sync', True):
        from sheet_sync import load_config
        config = load_config()
        if config:
            sheet_id = renderer.meta.get('sheet_id')
            if not sheet_id:
                sheet_id = 'sheet-' + sha256(renderer.title.encode('utf-8'))[:24]
                renderer.warnings.append('meta.sheet_id가 없어 제목 해시를 사용합니다. 제목을 바꾸면 기록이 분리됩니다.')
            descriptor_sync = {'v': 1, **config, 'table': 'sheet_records',
                               'sheet': sheet_id, 'title': renderer.title}
            sync_tag = '<meta name="sheet-sync" content="' + escape(json.dumps(
                descriptor_sync, ensure_ascii=False, separators=(',', ':')), quote=True) + '">\n'
            if not sync_script.is_file():
                renderer.warnings.append('학생 기록 엔진 sync.js가 없어 기록을 전송하거나 조회할 수 없습니다.')
    theme = ''
    if renderer.theme == '산뜻':
        fresh = ROOT / 'assets/engine/theme-fresh.css'
        if fresh.is_file():
            theme = f'<style id="sheet-theme">{fresh.read_text(encoding="utf-8")}</style>\n'
        else:
            renderer.warnings.append('산뜻 테마가 아직 준비되지 않아 기본 스타일을 사용합니다.')
    descriptor = escape(json.dumps(build_meta, ensure_ascii=False, separators=(',', ':')))
    title = escape(renderer.title + TEACHER_SUFFIX)
    items = renderer.render_items()
    private = renderer.render_teacher()
    interactive = f' data-interactive="1" data-reveal="{escape(renderer.reveal_policy)}"' if renderer.interactive else ''
    salt = f'<meta name="sheet-salt" content="{escape(renderer.salt)}">\n' if renderer.interactive else ''
    reveal_tag = _reveal_tag(renderer.salt, renderer.reveal_data) + '\n' if renderer.published else ''
    return (f'<!doctype html>\n<html lang="ko" data-sheet="1" data-role="teacher" data-kind="{renderer.kind}" '
            f'data-columns="{renderer.columns}" data-answers="on"{interactive} data-theme="{escape(renderer.theme)}" '
            f'data-accent="{escape(renderer.accent)}">\n<head>\n'
            '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{title}</title>\n<meta name="sheet-build" content="{descriptor}">\n{salt}{sync_tag}'
            f'<style id="sheet-fonts">{fonts}</style>\n<style id="sheet-css">{css}</style>\n{theme}'
            '<style id="sheet-user"></style>\n</head>\n<body>\n'
            f'<div id="sheet-src">\n{items}\n</div>\n'
            f'<div id="sheet-teacher" class="{TEACHER_ONLY}">\n{private}\n</div>\n'
            f'{reveal_tag}<script id="sheet-js">{js}</script>\n</body>\n</html>\n')


class AnswerTable(HTMLParser):
    """Recover the test answers from the current teacher DOM, including saved edits."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.salt = None
        self.slots = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'sheet-salt':
            self.salt = attrs.get('content')
        if 'data-check' in attrs:
            slot, kind = attrs.get('data-slot'), attrs.get('data-check-kind')
            if kind in ('open', 'draw', 'self', 'name'):
                return
            if not slot or not kind or slot in self.seen:
                raise ValueError('교사용 정답 자리의 이름이 없거나 중복됩니다.')
            try:
                check = json.loads(attrs['data-check'])
                right, wrong = check['right'], check['wrong']
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f'{slot}: 교사용 판별 자료가 잘못되었습니다.') from error
            self.seen.add(slot)
            self.slots.append({'slot': slot, 'kind': kind, 'right': right, 'wrong': wrong})


def answer_table_from_html(source):
    parser = AnswerTable()
    parser.feed(source)
    parser.close()
    if parser.slots and parser.salt is None:
        raise ValueError('정답표를 만들 salt가 없습니다.')
    return {'salt': parser.salt, 'slots': parser.slots} if parser.salt is not None else None


def update_answer_table(folder, answers):
    out = folder / CHECK_DIR
    path = out / '정답표.json'
    if answers is not None:
        out.mkdir(exist_ok=True)
        atomic_write(path, json_bytes(answers))
    elif path.exists():
        archive = out / '_이전'
        archive.mkdir(exist_ok=True)
        target = archive / path.name
        index = 1
        while target.exists():
            target = archive / f'{path.stem}-{index:03d}{path.suffix}'
            index += 1
        path.rename(target)


def manifest_path(folder):
    return folder / CHECK_DIR / BUILD_FILE


def load_manifest(folder):
    try:
        record = json.loads(manifest_path(folder).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or record.get('tool') != TOOL or record.get('version') != VERSION:
        return {}
    return record


def engine_sha256():
    """조립에 영향을 주는 설치본의 경로와 파일 내용을 함께 지문으로 만든다."""
    paths = [path for directory in ('assets/engine', 'assets/templates')
             for path in (ROOT / directory).rglob('*') if path.is_file()]
    # plan 규칙과 그 자료(학년 용어·교과서 표현)가 바뀌어도 예전 검사를 그대로 믿지 않게 함께 넣는다.
    paths += [ROOT / name for name in ('assets/kit/blocks.json', 'assets/kit/terms.json',
              'assets/kit/expressions.json', 'scripts/sheet_build.py',
              'scripts/sheet_markup.py', 'scripts/sheet_answer.py', 'scripts/sheet_plan.py',
              'scripts/sheet_common.py', 'scripts/sheet_sync.py')]
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix()):
        name = path.relative_to(ROOT).as_posix().encode('utf-8')
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big'))
        digest.update(name)
        digest.update(len(content).to_bytes(8, 'big'))
        digest.update(content)
    return digest.hexdigest()


def file_record(path, data):
    return {'file': path.name, 'sha256': sha256(data)}


def protected(teacher, folder):
    if not teacher.is_file():
        return False
    with teacher.open('rb') as stream:
        # Before the fonts: the flag on the root HTML element is a few hundred bytes in.
        root = re.search(rb'<html\b[^>]*>', stream.read(8192), re.I)
        if not root or not re.search(rb'\sdata-saved-at(?:\s*=|[\s>])', root[0], re.I):
            return False
        stream.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    previous = load_manifest(folder).get('teacher')
    return not isinstance(previous, dict) or previous != {'file': teacher.name, 'sha256': digest.hexdigest()}


def cmd_build(folder: Path, force=False) -> int:
    data, source, issues = load_plan(folder)
    print_issues(issues)
    if any(issue.level == 'error' for issue in issues):
        print('다음: 구성.json을 고쳐 plan을 다시 실행하세요.')
        return 1
    teacher, student = html_paths(folder, data['meta']['title'])
    if not force and protected(teacher, folder):
        print(f'오류: 브라우저에서 편집하고 저장한 교사용 파일을 보호했습니다: {teacher}')
        print('다음: 편집을 보존하려면 derive를, 덮어쓰려면 원본을 백업한 뒤 build --force를 실행하세요.')
        return 1
    metadata = {'tool': TOOL, 'version': VERSION, 'built_at': datetime.now(timezone.utc).isoformat(),
                'source_sha256': sha256(source), 'engine_sha256': engine_sha256(),
                'grade': data['meta'].get('grade'), 'unit': data['meta'].get('unit')}
    warnings = []
    font_css, font_bytes = font_styles(data, warnings)
    renderer = Renderer(data, folder, warnings)
    try:
        teacher_text = document_html(renderer, metadata, font_css)
        teacher_doc = _Document(teacher_text)
        student_doc = _Document(strip_teacher(teacher_text))
        metadata['edition'] = _edition(teacher_doc, student_doc, renderer.reveal_data if renderer.published else None)
        teacher_data = _replace_metadata(teacher_doc, metadata).encode('utf-8')
        student_data = _replace_metadata(student_doc, metadata).encode('utf-8')
        answers = answer_table_from_html(teacher_text)
    except ValueError as error:
        print(f'조립 오류: {error}')
        print('다음: 원고와 그림을 확인한 뒤 build를 다시 실행하세요.')
        return 1
    if max(len(teacher_data), len(student_data)) > 4_000_000:
        warnings.append('HTML이 목표 크기 4 MB를 넘었습니다. 그림 크기와 개수를 확인하세요.')
    warnings = list(dict.fromkeys(warnings))
    script = next(el for el in teacher_doc.root.walk() if el.tag == 'script' and el.attrs.get('id') == 'sheet-js')
    script_hash = sha256(teacher_doc.inner(script).encode('utf-8'))
    record = {**metadata, 'teacher': file_record(teacher, teacher_data), 'student': file_record(student, student_data),
              'sheet_js_sha256': script_hash, 'fonts': {'embedded': bool(font_bytes), 'bytes': font_bytes},
              'plan_warnings': plan_warning_counts(issues),
              'warnings': [f'{i.path}: {i.message}' for i in issues if i.level == 'warning'] + warnings}
    record_path = manifest_path(folder)
    record_path.parent.mkdir(exist_ok=True)
    atomic_write(teacher, teacher_data)
    atomic_write(student, student_data)
    update_answer_table(folder, answers)
    atomic_write(record_path, json_bytes(record))
    for message in warnings:
        print(f'경고: {message}')
    print(f'교사용: {teacher} ({len(teacher_data):,}바이트)')
    print(f'학생용: {student} ({len(student_data):,}바이트)')
    print(f'조립 기록: {record_path} · WOFF {font_bytes:,}바이트')
    print('다음: check로 브라우저 검사와 인쇄 확인을 하세요.')
    return 0


def _derive_documents(source, previous_student):
    # Validate the saved teacher DOM before interpreting any of its metadata.
    strip_teacher(source)
    doc = _Document(source)
    root = next(el for el in doc.root.walk() if el.tag == 'html')
    interactive = 'data-interactive' in root.attrs
    grading, published = student_policy(interactive, root.attrs.get('data-reveal', '시도 후'))
    salt = doc.metadata('sheet-salt')
    if interactive and not salt:
        raise ValueError('판별 자료를 만들 salt가 없습니다.')
    if interactive:
        doc, displays = _refresh_grading(doc, salt, grading)
        source = doc.source
    public = None
    old = doc.lookup('id', 'sheet-reveal')
    if published:
        public = _rebuild_reveal(doc, _read_reveal(doc, salt), displays)
        if old is not None:
            source = source[:old.body] + reveal(salt, public) + source[old.end:]
        else:
            script = doc.lookup('id', 'sheet-js')
            if script is None:
                raise ValueError('활동지 엔진 요소를 찾을 수 없습니다.')
            source = source[:script.start] + _reveal_tag(salt, public) + '\n' + source[script.start:]
    elif old is not None:
        source = source[:old.start] + source[source.index('>', old.end) + 1:]
    if source != doc.source:
        doc = _Document(source)
    student_doc = _Document(strip_teacher(source))
    metadata = json.loads(doc.metadata('sheet-build') or '{}')
    if not isinstance(metadata, dict):
        raise ValueError('조립 메타데이터가 올바르지 않습니다.')
    edition = _edition(doc, student_doc, public)
    if previous_student is not None:
        prior = _Document(previous_student)
        # Browser-generated editions and legacy source IDs remain valid for unchanged content.
        try:
            prior_meta = json.loads(prior.metadata('sheet-build') or '{}')
            same = (prior.lookup('id', 'sheet-src') is not None and
                    _edition_body(prior, with_hashes=True) == _edition_body(student_doc, with_hashes=True) and
                    (not published or _read_reveal(prior, prior.metadata('sheet-salt')) == public))
            if same and isinstance(prior_meta, dict):
                if isinstance(prior_meta.get('edition'), str) and prior_meta['edition']:
                    edition = prior_meta['edition']
                elif metadata.get('source_sha256') and prior_meta.get('source_sha256') == metadata['source_sha256']:
                    edition = None  # Keep the legacy edition fallback for this unchanged pair.
        except (ValueError, StopIteration):
            pass  # An invalid previous student copy is replaced, never used as authority.
    if edition:
        metadata['edition'] = edition
    else:
        metadata.pop('edition', None)
    return _replace_metadata(doc, metadata), _replace_metadata(student_doc, metadata), edition


def cmd_derive(teacher: Path) -> int:
    suffix = TEACHER_SUFFIX + '.html'
    if not teacher.name.endswith(suffix) or not teacher.name.removesuffix(suffix):
        print('오류: 교사용 파일 이름은 <제목> (교사용).html이어야 합니다.')
        print('다음: 교사용 HTML을 선택해 derive를 다시 실행하세요.')
        return 1
    folder = teacher.parent
    student = folder / (teacher.name.removesuffix(suffix) + '.html')
    source = teacher.read_text(encoding='utf-8-sig')
    record = load_manifest(folder)
    teacher_entry = record.get('teacher')
    recorded = isinstance(teacher_entry, dict) and teacher_entry.get('file') == teacher.name
    script_hash = record.get('sheet_js_sha256') if recorded else None
    problems = foreign_code_issues(source, script_hash)
    if problems:
        for problem in problems:
            print(f'오류 · foreign-code · 교사용 HTML: {problem}')
        print(f'다음: {FOREIGN_CODE_FIX}')
        return 1
    try:
        try:
            previous_student = student.read_text(encoding='utf-8-sig')
        except (FileNotFoundError, UnicodeError):
            previous_student = None
        refreshed, derived, edition = _derive_documents(source, previous_student)
        payload = derived.encode('utf-8')
        answers = answer_table_from_html(refreshed)
    except ValueError as error:
        print(f'오류: {error}')
        print('다음: 저장한 교사용 HTML에서 derive를 실행하세요.')
        return 1
    if refreshed != source:
        atomic_write(teacher, refreshed.encode('utf-8'))
    atomic_write(student, payload)
    update_answer_table(folder, answers)
    if recorded:
        # Keep the original teacher and script hashes so a later build still protects the edit.
        record['student'] = file_record(student, payload)
        if edition is not None:
            record['edition'] = edition
        else:
            record.pop('edition', None)
        atomic_write(manifest_path(folder), json_bytes(record))
    else:
        print('경고: 일치하는 build.json이 없어 조립 기록은 갱신하지 않았습니다.')
    print(f'학생용 다시 생성: {student} ({len(payload):,}바이트)')
    print('다음: check로 새 학생용과 교사용을 검사하세요.')
    return 0
