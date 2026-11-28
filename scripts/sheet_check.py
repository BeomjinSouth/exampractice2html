"""활동지의 인쇄 결과와 전달 관문을 검사한다."""

import base64
from collections import Counter
import contextlib
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata

import cdp


class CheckError(Exception):
    pass


class _KeptProfile:
    """CDP 원본의 자동 영구 삭제 대신 브라우저 프로필을 남긴다."""
    def __init__(self, prefix):
        fixed = os.environ.get("SHEET_TEST_PROFILE")
        if fixed:
            profile = Path(fixed)
            profile.mkdir(parents=True, exist_ok=True)
            port = profile / "DevToolsActivePort"
            if port.exists():
                previous = profile.parent / "_이전"
                previous.mkdir(exist_ok=True)
                i = 0
                while (previous / f"DevToolsActivePort-{i}").exists():
                    i += 1
                port.rename(previous / f"DevToolsActivePort-{i}")
            self.name = str(profile)
        else:
            root = os.environ.get("SHEET_TEST_TMP")
            if root:
                Path(root).mkdir(parents=True, exist_ok=True)
            self.name = tempfile.mkdtemp(prefix=prefix, dir=root)

    def cleanup(self):
        pass


@contextlib.contextmanager
def sheet_browser():
    """원본 CDP를 그대로 쓰되 브라우저 지정과 프로필 보존을 적용한다."""
    original_find = cdp.find_browser
    original_temp = cdp.tempfile.TemporaryDirectory
    configured = os.environ.get("SHEET_BROWSER")

    def find():
        if configured:
            found = shutil.which(configured) or (configured if Path(configured).is_file() else None)
            if not found:
                raise cdp.BrowserError("SHEET_BROWSER의 실행 파일을 찾을 수 없습니다: " + configured)
            return found
        old = os.environ.pop("DECK_BROWSER", None)
        try:
            try:
                return original_find()
            except cdp.BrowserError as exc:
                raise cdp.BrowserError(str(exc).replace("DECK_BROWSER", "SHEET_BROWSER")) from exc
        finally:
            if old is not None:
                os.environ["DECK_BROWSER"] = old

    browser = cdp.Browser()
    try:
        cdp.find_browser = find
        # 평소에는 cdp.py가 만든 임시 프로필을 cdp.py가 스스로 치운다(검사마다 약 100MB가 쌓이지 않게).
        # 시험에서 프로필을 다시 쓰거나 남겨 볼 때만 SHEET_TEST_PROFILE·SHEET_TEST_TMP로 보존한다.
        if os.environ.get("SHEET_TEST_PROFILE") or os.environ.get("SHEET_TEST_TMP"):
            cdp.tempfile.TemporaryDirectory = _KeptProfile
        browser.__enter__()
    finally:
        cdp.find_browser = original_find
        cdp.tempfile.TemporaryDirectory = original_temp
    try:
        yield browser
    finally:
        browser.__exit__(None, None, None)


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_file(folder, name):
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise CheckError("build.json에 폴더 밖이나 잘못된 HTML 경로가 있습니다.")
    return folder / name


def _find_files(folder):
    build_path = folder / "검사" / "build.json"
    build = None
    if build_path.is_file():
        try:
            build = _json(build_path)
            if not isinstance(build, dict):
                raise ValueError("객체가 아닙니다")
            teacher = _local_file(folder, build["teacher"]["file"])
            student = _local_file(folder, build["student"]["file"])
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise CheckError("build.json의 HTML 기록을 읽을 수 없습니다: " + str(exc)) from exc
    else:
        matches = sorted(folder.glob("* (교사용).html"))
        if len(matches) != 1:
            raise CheckError("교사용 HTML을 하나 찾을 수 없습니다 · build.json과 파일 이름을 확인하세요.")
        teacher = matches[0]
        student = folder / (teacher.name[:-len(" (교사용).html")] + ".html")
    if teacher == student or not teacher.is_file() or not student.is_file():
        raise CheckError("교사용과 학생용 HTML이 모두 필요합니다 · 조립하거나 학생용을 다시 만드세요.")
    return teacher, student, build


_URL = re.compile(r"url\(\s*[\"\x27]?\s*https?://", re.I)
_REMOTE = re.compile(r"^\s*https?://", re.I)
_MARKERS = {"ws-key", "ws-teacher-only", "ws-key-choice", "ws-fig-answer"}
_BOX_GEOMETRY = {"path": ("d",), "text": ("x", "y")}


class _SourceAudit(HTMLParser):
    """스크립트·스타일의 공통 클래스명은 실제 학생용 요소가 아니다."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.markers = set()
        self.remotes = []
        self.in_style = False
        self.in_box = False
        self.box_geometry = Counter()
        self.images = Counter()
        self.overlays = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        self.markers.update(_MARKERS.intersection(classes))
        if tag == "img" and (src := attrs.get("src")):
            self.images[src] += 1
            if "ws-fig-answer" in classes:
                self.overlays.add(src)
        if tag == "svg":
            self.in_box = attrs.get("data-chart") == "box"
        if self.in_box:
            for name in _BOX_GEOMETRY.get(tag, ()):
                if attrs.get(name):
                    self.box_geometry[tag, name, re.sub(r"\s+", "", attrs[name])] += 1
        for private in ("data-src", "data-check"):
            if private in attrs:
                self.markers.add(private)
        if attrs.get("id") == "sheet-teacher":
            self.markers.add("sheet-teacher")
        for name, value in attrs.items():
            value = value or ""
            if name in ("src", "href", "xlink:href") and _REMOTE.search(value):
                self.remotes.append(name)
            if name == "style" and _URL.search(value):
                self.remotes.append("style url()")
        if tag == "style":
            self.in_style = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        elif tag == "svg":
            self.in_box = False

    def handle_data(self, data):
        if self.in_style and _URL.search(data):
            self.remotes.append("CSS url()")


def _problem(problems, level, code, page, item, msg, fix):
    problems.append({"level": level, "code": code, "page": page, "item": item, "msg": msg, "fix": fix})


def _page_for(report, item):
    return next((entry.get("page") for entry in report.get("items", []) if entry.get("id") == item), None)


def _audit_report(report, role, problems):
    fixes = {
        "too-tall": "문항 묶음을 나누거나 내용을 줄이세요.",
        "page-overflow": "문항을 나누거나 답 칸과 간격을 조절하세요.",
        "text-overflow": "글자 길이를 줄이거나 칸을 넓히세요.",
        "image-broken": "그림을 내장하고 파일이 열리는지 확인하세요.",
        "math-error": "수식 표기를 고쳐 다시 조립하세요.",
    }
    seen = set()

    def add(code, page, item, msg):
        key = (code, page, item)
        if key not in seen:
            seen.add(key)
            _problem(problems, "error", code, page, item, role + ": " + msg,
                     fixes.get(code, "해당 문항과 엔진 보고를 확인하세요."))

    leaks = report.get("print_leaks")
    if report.get("interactive") and (report.get("view") != "a4" or type(leaks) is not int or leaks < 0):
        add("interactive-report-invalid", None, None, "A4 보기 또는 인쇄 유출 수치를 확인할 수 없습니다.")
    elif report.get("view") == "a4" and isinstance(leaks, (int, float)) and leaks > 0:
        add("print-leak", None, None, f"인쇄 보기에 화면 전용 요소 {leaks}개가 보입니다.")
    for issue in report.get("issues", []):
        if issue.get("level") == "error":
            add(issue.get("code", "engine-error"), issue.get("page"), issue.get("item"), issue.get("msg", "엔진 오류"))
    for page in report.get("pages", []):
        for slot in page.get("slots", []):
            if slot.get("overflow_mm", 0) > 0.5:
                add("page-overflow", page.get("n"), (slot.get("items") or [None])[-1], "쪽이 넘칩니다.")
    for item in report.get("items", []):
        if item.get("too_tall"):
            add("too-tall", item.get("page"), item.get("id"), "문항이 빈 쪽에도 들어가지 않습니다.")
    for entry in report.get("overflow", []):
        add("text-overflow", _page_for(report, entry.get("item")), entry.get("item"), entry.get("what", "글자 넘침"))
    for field, code, msg in (("images", "image-broken", "깨진 그림이 있습니다."),
                              ("math", "math-error", "수식 렌더링 오류가 있습니다.")):
        count = report.get(field, {}).get("broken" if field == "images" else "errors", 0)
        if count and not any(key[0] == code for key in seen):
            add(code, None, None, msg)
    for box in report.get("boxes", []):
        height = box.get("height_mm", 0)
        width = box.get("width_mm", 0)
        page = _page_for(report, box.get("item"))
        item = box.get("item")
        index = box.get("index", 0) + 1
        if height < 7:
            _problem(problems, "error", "box-too-short", page, item,
                     f"{role}: {index}번 답 칸의 높이가 {height:.1f}mm입니다.", "답 칸을 7mm 이상으로 늘리세요.")
        key = box.get("key_text") or ""
        if role == "교사용" and box.get("kind") == "text" and key.strip():
            required = math.ceil(len(key) * 4.5 / (width - 3.6)) * 8 + 2 if width > 3.6 else float("inf")
            # 손글씨 한 줄짜리 답은 2줄 칸(10.2mm)에 맞는 정상 상태라 경고하지 않는다(정본 7.6).
            if required > height or (required >= height * 0.85 and required > 10):
                level = "error" if required > height else "warning"
                code = "handwriting-overflow" if level == "error" else "handwriting-tight"
                amount = f"{required:.1f}mm" if math.isfinite(required) else "측정 불가"
                _problem(problems, level, code, page, item,
                         f"{index}번 답 칸의 예상 필기 높이 {amount}, 실제 {height:.1f}mm.",
                         "답 칸을 늘리거나 학생이 쓸 답안 분량을 줄이세요.")
    for family, applied in report.get("fonts", {}).items():
        if not applied:
            _problem(problems, "warning", "font-not-applied", None, None,
                     f"{role}: 내장 글꼴 {family}이 적용되지 않았습니다.", "글꼴 내장과 역할별 적용을 확인하세요.")


def _audit_fill(report, problems):
    pages = [page for page in report.get("pages", []) if not page.get("teacher")]
    for index, page in enumerate(pages):
        for column, slot in enumerate(page.get("slots", []), 1):
            fill, base = slot.get("fill", 0), slot.get("base_fill", 0)
            last = index == len(pages) - 1
            if (not last and (fill < 0.95 or fill > 0.995)) or (last and fill < 0.90):
                _problem(problems, "warning", "page-fill", page.get("n"), None,
                         f"학생용 {column}번 칸 채움이 {fill * 100:.1f}%입니다.", "늘이기 설정이나 쪽 구성을 확인하세요.")
            # 늘이기 전 채움이 낮으면 남는 높이가 모두 답칸으로 가서 한 문장 답에 반 쪽짜리 칸이 생긴다.
            # 여러 쪽의 마지막 쪽만 50%까지 허용하고, 한 쪽짜리·중간 쪽은 65%를 기준으로 본다.
            limit = 0.5 if (last and len(pages) > 1) else 0.65
            if base < limit:
                _problem(problems, "warning", "base-fill", page.get("n"), None,
                         f"{page.get('n')}쪽 {column}번 칸의 늘이기 전 채움이 {base * 100:.1f}%입니다. "
                         "남는 높이가 모두 답칸으로 가서 칸이 지나치게 커집니다.",
                         "학습 목표에 맞는 활동(예: 적용 문제, 수직선·표로 확인하기)을 보강해 한 쪽 분량을 맞추거나, "
                         "여러 쪽이면 문항 배치(break)를 다시 정하세요. 쪽을 채우려고 안내 글을 늘리지는 마세요.")


def _audit_source(teacher_text, student_text, keys, problems, teacher_public=""):
    teacher, student = _SourceAudit(), _SourceAudit()
    for role, text, parser in (("교사용", teacher_text, teacher), ("학생용", student_text, student)):
        parser.feed(text)
        if role == "학생용":
            for marker in sorted(parser.markers):
                _problem(problems, "error", "student-private-markup", None, None,
                         f"학생용 원문에 {marker} 표시가 남았습니다.", "교사용에서 학생용을 다시 내보내세요.")
        if parser.remotes:
            _problem(problems, "error", "external-url", None, None,
                     f"{role}에 외부 URL이 있습니다 ({', '.join(sorted(set(parser.remotes)))}).",
                     "외부 자원을 파일에 내장하거나 로컬 자원으로 바꾸세요.")
    public_source = _SourceAudit()
    if teacher.overlays or teacher.box_geometry:
        from sheet_build import strip_teacher
        try:
            public_source.feed(strip_teacher(teacher_text))
        except ValueError:
            pass  # 교사용 공개 원문을 구할 수 없으면 어떤 좌표도 비교에서 빼지 않는다.
    if any(student.images[src] > public_source.images[src] for src in teacher.overlays):
        _problem(problems, "error", "answer-leak", None, None, "교사용 SVG 정답층이 학생용 원문에 있습니다.",
                 "교사용에서 학생용을 다시 내보내세요.")
    # 공개 눈금의 좌표가 우연히 '11.5' 같은 정답을 포함할 수 있다.
    # 양쪽 공개 원문에 실제로 있는 동일 좌표만 뺀다. 새 좌표와 글자·대체 설명은 그대로 검사한다.
    shared_geometry = student.box_geometry & public_source.box_geometry
    body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", student_text, flags=re.S | re.I)
    compact = re.sub(r"\s+", "", html.unescape(body))
    public = re.sub(r"\s+", "", teacher_public or "")
    for key in sorted(set(keys)):
        clean = re.sub(r"\s+", "", key)
        if len(clean) < 4:
            continue
        geometry_hits = sum(value.count(clean) * count for (_, _, value), count in shared_geometry.items())
        if compact.count(clean) > public.count(clean) + geometry_hits:
            _problem(problems, "error", "answer-leak", None, None,
                     f"교사용 정답 문자열({clean[:20]})이 학생용 원문에 있습니다.",
                     "학생용을 다시 만들고 정답 유출 위치를 확인하세요.")
            break


def _load_answer_table(out, problems):
    path = out / "정답표.json"
    if not path.is_file():
        _problem(problems, "error", "answer-table-missing", None, None,
                 "화면 풀이 학생용의 정답표.json이 없습니다.", "build를 다시 실행해 정답표를 만드세요.")
        return None
    try:
        table = _json(path)
        if not isinstance(table, dict) or not isinstance(table.get("salt"), str) or not re.fullmatch(r"[0-9a-fA-F]{32}", table["salt"]):
            raise ValueError("salt")
        rows = table.get("slots")
        if not isinstance(rows, list):
            raise ValueError("slots")
        names = set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("slot"), str) or not row["slot"]
                    or not isinstance(row.get("kind"), str) or not row["kind"]
                    or "right" not in row or "wrong" not in row or row["slot"] in names):
                raise ValueError("자리 형식 또는 중복")
            names.add(row["slot"])
        return table
    except (OSError, UnicodeError, ValueError, TypeError):
        _problem(problems, "error", "answer-table-invalid", None, None,
                 "정답표.json의 형식이 올바르지 않습니다.", "build를 다시 실행해 정답표를 만드세요.")
        return None


def _audit_answers(table, slots, results, problems):
    expected = {row["slot"] for row in table["slots"]}
    auto = [row["slot"] for row in slots if row.get("auto")]
    actual = set(auto)
    if len(auto) != len(actual):
        duplicates = sorted(slot for slot in actual if auto.count(slot) > 1)
        _problem(problems, "error", "answer-slot-mismatch", None, None,
                 "학생용 자동 판별 자리가 중복됩니다: " + ", ".join(duplicates),
                 "자리 이름을 확인하고 다시 조립하세요.")
    for slot in sorted(expected - actual):
        _problem(problems, "error", "answer-slot-mismatch", None, slot,
                 f"정답표의 {slot} 자리가 학생용 자동 판별에 없습니다.", "다시 조립해 자리와 정답표를 맞추세요.")
    for slot in sorted(actual - expected):
        _problem(problems, "error", "answer-slot-mismatch", None, slot,
                 f"학생용 자동 판별 자리 {slot}이 정답표에 없습니다.", "다시 조립해 자리와 정답표를 맞추세요.")
    by_slot = {row.get("slot"): row for row in results}
    for slot in sorted(expected):
        result = by_slot.get(slot, {})
        if result.get("right") is not True or result.get("wrong") is not False:
            _problem(problems, "error", "answer-check-failed", None, slot,
                     f"{slot} 자리에서 정답 또는 오답이 올바르게 판별되지 않았습니다.",
                     "정답·오답 자료와 학생용 판별 해시를 확인하고 다시 조립하세요.")


def _flat(text):
    """비교용: NFKC, 빼기 기호 통일, 보이지 않는 서식 문자·공백 제거, 소문자."""
    text = unicodedata.normalize("NFKC", str(text)).translate(str.maketrans("−‒–—―⁄", "-----/"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", "", text).lower()


def _audit_later_answers(table, items, problems):
    """앞 문항에서 구하게 한 식이 뒤 문항의 학생용 글(상자·발문·표·힌트)에 그대로 보이면 경고한다.

    `answer-leak`은 지워진 정답의 잔여만 본다. 원래 보이는 글이 앞 답을 알려 주는 경우는 여기서 본다.
    낱말 답은 뒤 문항에서 용어로 계속 쓰이므로 등호가 하나인 식만 대상으로 한다."""
    if table is None or not isinstance(items, list):
        return
    entries = [entry for entry in items if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
    order = [entry["id"] for entry in entries]
    texts = {entry["id"]: _flat(entry.get("text", "")) for entry in entries}
    for row in table["slots"]:
        right = row.get("right")
        if row.get("kind") != "text" or not isinstance(right, str):
            continue
        target = _flat(right)
        if target.count("=") != 1 or len(target) < 3 or re.search(r"[<>≤≥≠]", target):
            continue
        left, rhs = target.split("=")
        variants = {target, f"{rhs}={left}"}
        item = str(row.get("slot", "")).split(".")[0]
        if item not in order:
            continue
        for later in order[order.index(item) + 1:]:
            if any(variant in texts[later] for variant in variants):
                _problem(problems, "warning", "later-answer-shown", None, later,
                         f"{row['slot']} 자리의 답 ‘{right}’이 뒤 문항 {later}의 학생용 글에 보입니다.",
                         "뒤 문항·개념 상자·힌트에서 그 식을 빼고 ‘앞에서 구한 관계식’처럼 가리키세요.")
                break


def _reload_keeping_answers(protocol):
    """자동 저장된 답을 지우지 않고 같은 주소를 다시 연다."""
    protocol.events = [event for event in getattr(protocol, "events", []) if event.get("method") != "Page.loadEventFired"]
    protocol.call("Page.reload", {"ignoreCache": False})
    protocol.wait_event("Page.loadEventFired")
    ready = protocol.evaluate(_READY_JS, True)
    if ready != "ready":
        raise CheckError(f"답을 넣은 뒤 다시 연 활동지의 엔진을 준비하지 못했습니다 ({ready})")
    protocol.evaluate('document.querySelector(\'[data-sheet-action="resume-answers"]\')?.click()')


def _pdf_text(data):
    """PDF의 글자(공백 제거). PyMuPDF가 없으면 None."""
    try:
        import pymupdf
    except ImportError:
        return None
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return re.sub(r"\s+", "", "".join(page.get_text() for page in document))


def _audit_graph_labels(pdf, regions, role, problems):
    """Measure numeric labels inside graph SVG regions on the printed PDF pages."""
    if not regions:
        return []
    try:
        import pymupdf
    except ImportError:
        _problem(problems, 'warning', 'graph-label-unmeasured', None, None,
                 f'{role}: PyMuPDF가 없어 인쇄 그래프의 눈금 글자를 측정하지 못했습니다.',
                 'PyMuPDF를 설치하고 다시 검사하세요.')
        return 'skipped-no-pymupdf'
    measured = []
    with pymupdf.open(stream=pdf, filetype='pdf') as document:
        for region in regions:
            page = region['page']
            rect = pymupdf.Rect(region['bbox'])
            sizes = []
            if 1 <= page <= len(document):
                for block in document[page - 1].get_text('dict')['blocks']:
                    for line in block.get('lines', []):
                        for span in line.get('spans', []):
                            bounds = pymupdf.Rect(span['bbox'])
                            center = ((bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2)
                            if re.fullmatch(r'\s*[−+\-]?\d+(?:\.\d+)?\s*', span['text']) and rect.contains(center):
                                sizes.append(span['size'])
            item = region.get('item')
            result = {'page': page, 'item': item, 'labels': len(sizes),
                      'min_pt': round(min(sizes), 3) if sizes else None}
            measured.append(result)
            if not sizes:
                _problem(problems, 'warning', 'graph-label-unmeasured', page, item,
                         f'{role}: 인쇄 그래프의 숫자 눈금을 PDF에서 찾지 못했습니다.',
                         '그래프와 PDF 글자를 직접 확인하세요.')
            elif min(sizes) < 6:
                _problem(problems, 'warning', 'graph-label-small', page, item,
                         f'{role}: 인쇄 그래프 눈금이 최소 {min(sizes):.2f}pt입니다.',
                         '그래프 크기와 눈금 글자를 6pt 이상으로 조정하세요.')
    return measured


_PEN_ASPECT_JS = """(() => Object.fromEntries([...document.querySelectorAll(%s)]
  .filter(layer => layer.getClientRects().length)
  .map(layer => {
    const r = layer.getBoundingClientRect();
    return [layer.closest('[data-slot]')?.dataset.slot, r.height ? r.width / r.height : 0];
  }).filter(([slot]) => slot)))()"""


def _pen_aspects(protocol, *, print_media=False):
    selector = '#sheet-pages svg.ws-pen-ink[data-sheet-runtime]' if print_media else '#sheet-flow canvas.ws-pen'
    return protocol.evaluate(_PEN_ASPECT_JS % json.dumps(selector)) or {}


def _print_pen_aspects(protocol):
    protocol.call('Emulation.setEmulatedMedia', {'media': 'print'})
    try:
        protocol.evaluate("sheet.view('a4')", True)
        return _pen_aspects(protocol, print_media=True)
    finally:
        protocol.call('Emulation.setEmulatedMedia', {'media': ''})


def _audit_pen_aspects(screen, printed, name, problems):
    for slot in sorted(screen.keys() | printed.keys()):
        a, b = screen.get(slot), printed.get(slot)
        if a and b and abs(a - b) <= b * .015 + 1e-12:
            continue
        detail = f'화면 {a:.3f}, A4 {b:.3f}' if a and b else '한쪽 펜 층을 재지 못함'
        _problem(problems, 'error', 'pen-aspect-mismatch', None, slot.rsplit('.', 1)[0],
                 f'{name}: {slot} 펜 층의 가로세로 비율이 맞지 않습니다 ({detail}).',
                 '화면과 인쇄에서 펜 층의 그리기 틀 비율을 같게 유지하세요.')


_PEN_REGIONS_JS = """(() => {
  const pages = [...document.querySelectorAll('#sheet-pages .ws-page')];
  return [...document.querySelectorAll('#sheet-pages :is(figure[data-pen="1"], .ws-ans[data-kind="draw"][data-slot])')]
    .map(el => {
      const anchor = el.matches('figure') ? el.querySelector('img, svg:not(.ws-pen-ink)') : el.querySelector('.ws-draw-frame') || el;
      if (!anchor) return null;
      const r = anchor.getBoundingClientRect(), paper = el.closest('.ws-page'), p = paper.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      const inset = el === anchor ? 1 : 0; // Ignore the draw cell's border.
      return {page: pages.indexOf(paper) + 1, item: el.closest('.ws-item')?.dataset.id,
        slot: el.dataset.slot, bbox: [(r.left-p.left)*.75+inset, (r.top-p.top)*.75+inset,
          (r.right-p.left)*.75-inset, (r.bottom-p.top)*.75-inset]};
    }).filter(Boolean);
})()"""


def _print_pdf(protocol):
    return base64.b64decode(protocol.call('Page.printToPDF', {
        'preferCSSPageSize': True, 'printBackground': True, 'displayHeaderFooter': False
    })['data'], validate=True)


def _audit_pen_print(protocol, pdf, problems, *, drawn=None):
    """Compare the same A4 state with and without the absolute-positioned pen layers."""
    regions = protocol.evaluate(_PEN_REGIONS_JS) or []
    if not regions:
        return []
    try:
        import pymupdf
    except ImportError:
        _problem(problems, 'warning', 'pen-print-unmeasured', None, None,
                 'PyMuPDF가 없어 펜 자리의 인쇄를 측정하지 못했습니다.', 'PyMuPDF를 설치하고 다시 검사하세요.')
        return 'skipped-no-pymupdf'
    protocol.evaluate("""(() => {
      const style = document.createElement('style');
      style.id = 'sheet-check-hide-pen';
      style.textContent = '@media print {html[data-sheet] #sheet-pages :is(canvas.ws-pen, .ws-pen-ink) {display:none!important}}';
      document.head.append(style);
    })()""")
    try:
        bare_pdf = _print_pdf(protocol)
    finally:
        protocol.evaluate("document.getElementById('sheet-check-hide-pen')?.remove()")
    mode = '빈 인쇄' if drawn is None else '채움 인쇄'
    measured = []
    with pymupdf.open(stream=pdf, filetype='pdf') as actual, \
            pymupdf.open(stream=bare_pdf, filetype='pdf') as bare:
        if len(actual) != len(bare):
            _problem(problems, 'error', 'pen-print-unmeasured', None, None,
                     f'{mode}: 펜 층을 숨기면 PDF 쪽 수가 달라집니다.', '펜 층이 쪽 배치를 바꾸지 않는지 확인하세요.')
            return measured
        for region in regions:
            page, item, slot = region['page'], region.get('item'), region.get('slot')
            if not 1 <= page <= min(len(actual), len(bare)):
                _problem(problems, 'error', 'pen-print-unmeasured', page, item,
                         f'{mode}: {slot} 펜 자리의 비교 쪽이 없습니다.', '인쇄 쪽 나눔을 확인하세요.')
                continue
            clip = pymupdf.Rect(region['bbox'])
            shown, hidden = (doc[page - 1].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY, clip=clip).samples
                             for doc in (actual, bare))
            if not hidden or len(shown) != len(hidden):
                _problem(problems, 'error', 'pen-print-unmeasured', page, item,
                         f'{mode}: {slot} 펜 자리 영역이 맞지 않습니다.', 'A4 인쇄 영역을 확인하세요.')
                continue
            # Count loss and gain separately: a dark stroke must not mask a lost underlay in the total.
            baseline = lost = added = 0
            for visible, reference in zip(shown, hidden):
                if reference <= 225:
                    baseline += 1
                    lost += visible - reference >= 24
                added += visible <= 225 and reference - visible >= 24
            measured.append({**region, 'underlay_pixels': baseline, 'lost_pixels': lost, 'added_pixels': added})
            if lost >= max(24, baseline * .15):
                _problem(problems, 'error', 'pen-covers-content', page, item,
                         f'{mode}: {slot} 펜 층이 밑그림 잉크를 가립니다 ({lost}/{baseline}픽셀).',
                         '펜 층을 숨긴 결과와 학생 PDF를 비교하고, 인쇄 펜 층이 밑그림을 가리지 않게 하세요.')
            if drawn is not None and slot in drawn and added < 16:
                _problem(problems, 'error', 'pen-print-missing', page, item,
                         f'{mode}: {slot}에 넣은 펜 획이 PDF에 보이지 않습니다.', '펜 획의 저장과 인쇄를 확인하세요.')
    return measured


def _audit_filled_text(info, pdf, problems):
    """서술 칸에 넣은 두 줄 답이 채움 인쇄 PDF에 두 줄 모두 남았는지 센다."""
    boxes = (info.get("filled_inputs") or {}).get("open_box", 0)
    text = _pdf_text(pdf)
    if text is None:
        info["filled_text"] = "skipped-no-pymupdf"
        return
    found = min(text.count(_flat(line)) for line in _FILL_LINES)
    info["filled_text"] = {"open_box": boxes, "found": found}
    if found < boxes:
        _problem(problems, "error", "filled-print-missing", None, None,
                 f"서술 칸 {boxes}곳에 넣은 두 줄 답 가운데 {found}곳만 채움 인쇄에 보입니다.",
                 "A4 보기에서 서술 칸의 입력이 잘리거나 숨지 않는지 확인하세요.")


_READY_JS = """(async()=>{
      if(!window.sheet) return 'missing';
      try { return await Promise.race([
        Promise.resolve(window.sheet.ready).then(()=> 'ready'),
        new Promise(resolve=>setTimeout(()=>resolve('timeout'),20000))
      ]); } catch(e) { return 'rejected:' + String(e); }
    })()"""

# 채움 인쇄: 정답표의 자동 판별 자리 + 서술 칸(여러 줄)·빈칸·그리기·이름 칸까지 넣는다.
_FILL_JS = """(async()=>{
  for (const row of %s) sheet.answer(row.slot, row.right);
  const kinds = {open_box: 0, open_blank: 0, draw: 0, name: 0};
  const nodes = [...document.querySelectorAll('[data-slot]')];
  for (const s of sheet.slots()) {
    if (s.auto) continue;
    const el = nodes.find(n => n.dataset.slot === s.slot);
    let value;
    if (s.kind === 'open') {
      if (el && el.matches('.ws-ans')) { value = '%s\\n%s'; kinds.open_box++; }
      else { value = '검사'; kinds.open_blank++; }
    } else if (s.kind === 'draw') { value = {strokes: [[[0.15, 0.25], [0.5, 0.75], [0.85, 0.35]]]}; kinds.draw++; }
    else if (s.kind === 'name') { value = '검사'; kinds.name++; }
    else continue;
    sheet.answer(s.slot, value);
  }
  // A plot with a pen stores the pen separately; draw through its normal pointer path.
  const extraPens = [];
  for (const el of nodes.filter(n => n.matches('figure[data-pen="1"]:not([data-check-kind="draw"])'))) {
    const canvas = el.querySelector('canvas.ws-pen');
    const pen = el.querySelector('[data-sheet-action="pen"]');
    if (!canvas || !pen) continue;
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    pen.click();
    const fire = (type, x, y) => canvas.dispatchEvent(new PointerEvent(type, {bubbles:true, button:0,
      pointerId:1, clientX:r.left+x*r.width, clientY:r.top+y*r.height}));
    fire('pointerdown', .15, .25); fire('pointermove', .5, .75); fire('pointerup', .85, .35);
    extraPens.push(el.dataset.slot);
  }
  kinds.draw += extraPens.length;
  return {kinds, values: sheet.values(), extraPens};
})()"""
_FILL_LINES = ("검사 서술 첫째 줄", "검사 서술 둘째 줄")
_FILLED_SCREEN_JS = """(async function filledScreenInspection(){
  await sheet.view('solve');
  const items = [...document.querySelectorAll('#sheet-flow .ws-item')];
  let checked_items = 0, revealed_items = 0, solution_items = 0, compare_items = 0;
  for (const item of items) {
    const check = item.querySelector('[data-sheet-action="check"]');
    if (check && !check.disabled) { check.click(); checked_items++; }
    const open = action => {
      const button = item.querySelector(`[data-sheet-action="${action}"]`);
      if (!button || button.disabled) return 0;
      if (button.getAttribute('aria-pressed') !== 'true') button.click();
      return Number(button.getAttribute('aria-pressed') === 'true');
    };
    revealed_items += open('reveal-answer');
    solution_items += open('reveal-solution');
    compare_items += open('compare');
  }
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const clipped = [...document.querySelectorAll('#sheet-flow :is(td, th, .ws-flow-node, .ws-in, .ws-solution, .ws-reveal)')]
    .filter(el => el.getClientRects().length && el.clientWidth && (el.scrollWidth > el.clientWidth + 1 ||
      (el.matches('.ws-solution, .ws-reveal') && el.scrollHeight > el.clientHeight + 1)))
    .map(el => el.closest('[data-slot]')?.dataset.slot || el.closest('.ws-item')?.dataset.id || el.tagName.toLowerCase());
  return { view: sheet.view(), overflow_x: sheet.report().overflow_x,
    clipped: [...new Set(clipped)].sort(), checked_items, revealed_items, solution_items, compare_items };
})()"""
_RESTORED_JS = """(async()=>{
  await sheet.view('a4');
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const clipped = [...document.querySelectorAll('#sheet-pages .ws-ink, #sheet-pages .ws-name-slot.ws-filled')]
    .filter(el => el.getClientRects().length &&
      (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1))
    .map(el => el.closest('[data-slot]')?.dataset.slot).filter(Boolean);
  return { ...sheet.report(), values: sheet.values(), clipped: [...new Set(clipped)].sort() };
})()"""
# 문항별 학생에게 보이는 글(정답·교사 전용·화면 단추 제외, 힌트 포함)
_ITEM_TEXT_JS = """(() => [...document.querySelectorAll('.ws-item[data-id]')].map(item => {
  const copy = item.cloneNode(true);
  copy.querySelectorAll('.ws-key, .ws-teacher-only, [data-sheet-runtime], script, style').forEach(x => x.remove());
  return {id: item.dataset.id, text: copy.textContent};
}))()"""


def _navigate(browser, path, teacher):
    protocol = browser.cdp
    protocol.events = [event for event in protocol.events if event.get("method") != "Page.loadEventFired"]
    url = path.resolve().as_uri() + ("" if teacher else "?nosync")
    result = protocol.call("Page.navigate", {"url": url})
    if result.get("errorText"):
        raise CheckError("HTML을 열지 못했습니다: " + result["errorText"])
    protocol.wait_event("Page.loadEventFired")
    ready = protocol.evaluate(_READY_JS, True)
    if ready != "ready":
        raise CheckError(f"활동지 엔진을 준비하지 못했습니다: {path.name} ({ready})")
    protocol.evaluate("""(async()=>{
      if (typeof sheet.clearAnswers === 'function') sheet.clearAnswers();
      if (typeof sheet.view === 'function') await sheet.view('a4');
      if (document.documentElement.dataset.role === 'teacher') sheet.setAnswers(true);
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    })()""", True)
    result = protocol.evaluate("""(() => ({
      report: Object.assign(window.sheet.report(), {
        interactive: document.documentElement.hasAttribute('data-interactive')
      }),
      count: document.querySelectorAll('.ws-page').length,
      keys: [...document.querySelectorAll('.ws-key')].map(x=>x.textContent.trim()).filter(Boolean),
      graph_regions: [...document.querySelectorAll('#sheet-pages .ws-grid > svg, #sheet-pages .ws-nl > svg, #sheet-pages .ws-chart > svg')]
        .filter(svg => svg.getClientRects().length && [...svg.querySelectorAll('text')]
          .some(text => /^[−+\\-]?\\d+(?:\\.\\d+)?$/.test(text.textContent.trim()))).map(svg => {
        const paper = svg.closest('.ws-page'), pages = [...document.querySelectorAll('.ws-page')];
        const r = svg.getBoundingClientRect(), p = paper.getBoundingClientRect();
        return {page: pages.indexOf(paper) + 1, item: svg.closest('.ws-item')?.dataset.id || '',
          bbox: [(r.left-p.left)*.75, (r.top-p.top)*.75, (r.right-p.left)*.75, (r.bottom-p.top)*.75]};
      }),
      // 정답·교사 전용 요소를 뺀 '학생에게도 보이는 글'(정답 낱말이 문제 본문에 원래 있는 경우를 가리기 위해)
      public: (() => { const c = document.body.cloneNode(true);
        c.querySelectorAll('.ws-key, .ws-teacher-only, .ws-tpage, script, style').forEach(x => x.remove());
        return c.textContent; })()
    }))()""")
    expected = "teacher" if teacher else "student"
    if not isinstance(result, dict) or not isinstance(result.get("report"), dict):
        raise CheckError(f"엔진 보고를 읽을 수 없습니다: {path.name}")
    report = result["report"]
    if report.get("role") != expected or not isinstance(report.get("pages"), list) or not result.get("count"):
        raise CheckError(f"활동지 역할이나 쪽 수가 올바르지 않습니다: {path.name}")
    pdf = _print_pdf(protocol)
    report['graph_regions'] = result.get('graph_regions', [])
    return report, result["count"], result.get("keys", []), pdf, result.get("public", "")


def _interactive_check(protocol, out, table, problems):
    """빈 화면 두 크기, 정답 판별, 답을 채운 인쇄를 차례로 검사한다."""
    info = {"screens": [], "grading": "not-run", "auto_slots": None, "answer_slots": None, "grading_tested": 0}
    files, filled_pdf = [], None
    capabilities = protocol.evaluate("""(() => ({
      available: ['view','slots','answer','values','clearAnswers'].every(k => typeof sheet[k] === 'function'),
      reveal: document.documentElement.dataset.reveal || '시도 후',
      salt: document.querySelector('meta[name="sheet-salt"]')?.content || ''
    }))()""")
    if not isinstance(capabilities, dict) or not capabilities.get("available"):
        _problem(problems, "error", "interactive-api-missing", None, None,
                 "학생용 화면 풀이 API가 없습니다.", "build를 다시 실행하고 엔진을 확인하세요.")
        return info, files, filled_pdf
    info["reveal"] = capabilities["reveal"]
    if table is not None and table["salt"] != capabilities["salt"]:
        _problem(problems, "error", "answer-table-invalid", None, None,
                 "정답표와 학생용 HTML의 salt가 다릅니다.", "build를 다시 실행해 같은 정답표를 만드세요.")
        table = None
    _audit_later_answers(table, protocol.evaluate(_ITEM_TEXT_JS), problems)
    try:
        printed_aspects = _print_pen_aspects(protocol)
        for name, width, height in (("화면-폰.png", 390, 844), ("화면-태블릿.png", 1024, 768)):
            protocol.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                                  "deviceScaleFactor": 1, "mobile": False})
            report = protocol.evaluate("""(async()=>{
              await sheet.view('solve');
              await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
              return sheet.report();
            })()""", True)
            if not isinstance(report, dict):
                raise CheckError("화면 풀이 엔진 보고를 읽을 수 없습니다.")
            overflow = report.get("overflow_x")
            if report.get("view") != "solve":
                _problem(problems, "error", "screen-not-solve", None, None,
                         f"{name}: 풀기 보기가 아닙니다.", "화면 풀이 엔진의 보기 전환을 확인하세요.")
            if type(overflow) not in (int, float) or not math.isfinite(overflow) or overflow < 0:
                _problem(problems, "error", "interactive-report-invalid", None, None,
                         f"{name}: 가로 넘침 수치를 확인할 수 없습니다.", "화면 풀이 엔진의 보고를 확인하세요.")
            elif overflow > 1:
                _problem(problems, "error", "screen-overflow", None, None,
                         f"{name}: 가로로 {overflow}px 넘칩니다.", "해당 폭의 표·그림·문항 배치를 고치세요.")
            _audit_pen_aspects(_pen_aspects(protocol), printed_aspects, name, problems)
            size = protocol.call("Page.getLayoutMetrics").get("cssContentSize", {})
            full_height = max(height, math.ceil(size.get("height", height)))
            data = protocol.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                "clip": {"x": 0, "y": 0, "width": width, "height": full_height, "scale": 1}})["data"]
            path = out / name
            if path.exists():
                _archive(path, out)
            path.write_bytes(base64.b64decode(data, validate=True))
            files.append(path)
            info["screens"].append({"file": str(path), "width": width, "height": height,
                                    "image_height": full_height, "view": report.get("view"), "overflow_x": overflow})
        if table is not None:
            rows = table["slots"]
            info["answer_slots"] = len(rows)
            if capabilities["reveal"] == "없음":
                info["grading"] = "skipped-reveal-none"
            else:
                slots = protocol.evaluate("sheet.slots()")
                if not isinstance(slots, list) or any(not isinstance(s, dict) or not isinstance(s.get('slot'), str) for s in slots):
                    raise CheckError("학생용 자리 목록을 읽을 수 없습니다.")
                info["auto_slots"] = sum(bool(slot.get("auto")) for slot in slots)
                data = json.dumps(rows, ensure_ascii=True)
                results = protocol.evaluate("""(() => %s.map(row => {
                  let right, wrong;
                  try { right = sheet.answer(row.slot, row.right); } catch(e) { right = null; }
                  try { wrong = sheet.answer(row.slot, row.wrong); } catch(e) { wrong = null; }
                  return {slot: row.slot, right, wrong};
                }))()""" % data)
                if not isinstance(results, list):
                    raise CheckError("자리별 판별 결과를 읽을 수 없습니다.")
                info['grading_tested'] = len(results)
                before = len(problems)
                _audit_answers(table, slots, results, problems)
                info["grading"] = "failed" if len(problems) > before else "passed"
            data = json.dumps(rows, ensure_ascii=True)
            entered = protocol.evaluate(_FILL_JS % (data, *_FILL_LINES), True)
            if not isinstance(entered, dict) or not isinstance(entered.get("values"), dict):
                raise CheckError("채움 인쇄에 쓸 입력을 넣지 못했습니다.")
            info['filled_inputs'] = {'from_answer_table': sum(row['slot'] in entered['values'] for row in rows),
                                     **(entered.get('kinds') or {})}
            protocol.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844,
                                                                  'deviceScaleFactor': 1, 'mobile': False})
            screen = protocol.evaluate(_FILLED_SCREEN_JS, True)
            if (not isinstance(screen, dict) or screen.get('view') != 'solve'
                    or type(screen.get('overflow_x')) not in (int, float)
                    or not math.isfinite(screen['overflow_x']) or screen['overflow_x'] < 0
                    or not isinstance(screen.get('clipped'), list)
                    or any(not isinstance(slot, str) for slot in screen['clipped'])):
                raise CheckError('답을 채운 폰 화면의 넘침을 확인할 수 없습니다.')
            info['filled_screen'] = screen
            filled_aspects = _pen_aspects(protocol)
            if screen['overflow_x'] > 1:
                _problem(problems, 'error', 'filled-screen-overflow', None, None,
                         f"답과 정답을 보이는 폰 화면이 가로로 {screen['overflow_x']}px 넘칩니다.",
                         '정답 표시와 문항 배치를 확인하세요.')
            if screen['clipped']:
                _problem(problems, 'warning', 'filled-screen-clipped', None, None,
                         '답을 채운 폰 화면에서 잘릴 수 있는 곳: ' + ', '.join(screen['clipped']),
                         '해당 입력 칸·표·흐름 상자·정답과 풀이 패널의 실제 표시를 확인하세요.')
            size = protocol.call('Page.getLayoutMetrics').get('cssContentSize', {})
            height = max(844, math.ceil(size.get('height', 844)))
            data = protocol.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True,
                'clip': {'x': 0, 'y': 0, 'width': 390, 'height': height, 'scale': 1}})['data']
            path = out / '화면-폰-채움.png'
            if path.exists():
                _archive(path, out)
            path.write_bytes(base64.b64decode(data, validate=True))
            files.append(path)
            # 자동 저장 → 다시 열기 → 인쇄 순서로, 학생이 쓴 답이 실제로 남아 인쇄되는지 본다.
            protocol.call('Emulation.clearDeviceMetricsOverride')
            _reload_keeping_answers(protocol)
            filled = protocol.evaluate(_RESTORED_JS, True)
            if not isinstance(filled, dict) or filled.get("view") != "a4":
                raise CheckError("채움 인쇄 전에 A4 보기로 전환하지 못했습니다.")
            restored = filled.get("values") if isinstance(filled.get("values"), dict) else {}
            canon = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
            lost = sorted(slot for slot, value in entered["values"].items()
                          if canon(value) != canon(restored.get(slot)))
            info["filled_restored"] = not lost
            if lost:
                _problem(problems, "error", "filled-restore-failed", None, None,
                         f"다시 열었을 때 입력이 사라지거나 바뀐 자리: {', '.join(lost[:8])}"
                         + (f" 외 {len(lost) - 8}곳" if len(lost) > 8 else ""),
                         "자동 저장과 복원(엔진의 저장 형식)을 확인하세요.")
            info["filled_print_leaks"] = filled.get("print_leaks")
            leaks = info["filled_print_leaks"]
            if type(leaks) is not int or leaks < 0:
                _problem(problems, "error", "interactive-report-invalid", None, None,
                         "학생용 채움 인쇄의 화면 전용 요소 수를 확인할 수 없습니다.", "화면 풀이 엔진의 보고를 확인하세요.")
            elif leaks > 0:
                _problem(problems, "error", "print-leak", None, None,
                         f"학생용 채움 인쇄에 화면 전용 요소 {leaks}개가 보입니다.",
                         "A4 보기의 화면 전용 요소를 숨기세요.")
            clipped = info["filled_print_clipped"] = filled.get("clipped")
            if not isinstance(clipped, list) or any(not isinstance(slot, str) for slot in clipped):
                raise CheckError("채움 인쇄의 답 잘림을 확인할 수 없습니다.")
            if clipped:
                _problem(problems, "error", "filled-print-clipped", None, None,
                         "A4 보기에서 채운 답이 잘리는 자리: " + ", ".join(clipped),
                         "빈칸·이름 칸의 너비와 서술 칸의 높이를 확인하세요.")
            _audit_pen_aspects(filled_aspects, _print_pen_aspects(protocol), '화면-폰-채움.png', problems)
            filled_pdf = _print_pdf(protocol)
            drawn = {slot for slot, value in entered['values'].items()
                     if isinstance(value, dict) and value.get('strokes')}
            drawn.update(entered.get('extraPens', []))
            info['filled_print_pen'] = _audit_pen_print(protocol, filled_pdf, problems, drawn=drawn)
    finally:
        protocol.evaluate("""(async()=>{
          if (typeof sheet.clearAnswers === 'function') sheet.clearAnswers();
          if (typeof sheet.view === 'function') await sheet.view('a4');
        })()""", True)
        protocol.call("Emulation.clearDeviceMetricsOverride")
    return info, files, filled_pdf


def _pdf_pages(data):
    try:
        import pymupdf
    except ImportError:
        count = len(re.findall(rb"/Type\s*/Page\b", data))
        if not count:
            raise CheckError("PyMuPDF 없이 PDF 쪽 수를 확인하지 못했습니다 · PyMuPDF를 설치하세요.")
        return count
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return len(document)


def _archive(path, out):
    archive = out / "_이전"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / path.name
    i = 1
    while target.exists():
        target = archive / f"{path.stem}-{i:03d}{path.suffix}"
        i += 1
    path.rename(target)


def _archive_old_images(out, active):
    for previous in out.iterdir():
        if previous.is_file() and re.fullmatch(r"(?:학생|교사|학생-채움)-\d+\.png", previous.name) and previous.name not in active:
            _archive(previous, out)


def _archive_contact_sheets(out):
    for name in ("모아보기.png", "모아보기-흑백.png"):
        previous = out / name
        if previous.is_file():
            _archive(previous, out)


def _render_pages(out, pdfs, problems):
    try:
        import pymupdf
    except ImportError:
        _archive_old_images(out, set())
        _problem(problems, "warning", "pymupdf-missing", None, None,
                 "PyMuPDF가 없어 쪽 그림을 만들지 못했습니다.", "PyMuPDF를 설치한 뒤 검사를 다시 실행하세요.")
        return []
    images = []
    for role, data in pdfs:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            for i, page in enumerate(document, 1):
                name = f"{role}-{i:02d}.png"
                if (out / name).exists():
                    _archive(out / name, out)
                page.get_pixmap(dpi=150, alpha=False).save(str(out / name))
                images.append((role, i, out / name))
    active = {path.name for _, _, path in images}
    _archive_old_images(out, active)
    return images


def _contact_sheets(out, images, problems):
    if not images:
        _archive_contact_sheets(out)
        return []
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError:
        _archive_contact_sheets(out)
        _problem(problems, "warning", "pillow-missing", None, None,
                 "Pillow가 없어 모아보기를 만들지 못했습니다.", "Pillow를 설치한 뒤 검사를 다시 실행하세요.")
        return []
    font = None
    for name in ("C:/Windows/Fonts/malgun.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
        try:
            font = ImageFont.truetype(name, 20)
            break
        except OSError:
            continue
    has_korean_font = font is not None
    font = font or ImageFont.load_default()
    width, height, columns, gap, label = 350, 495, min(3, len(images)), 16, 32
    rows = math.ceil(len(images) / columns)
    sheet = Image.new("RGB", (gap + columns * (width + gap), gap + rows * (height + label + gap)), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    for n, (role, page, path) in enumerate(images):
        x, y = gap + (n % columns) * (width + gap), gap + (n // columns) * (height + label + gap)
        caption = f"{role} {page}쪽" if has_korean_font else f"{'Student' if role == '학생' else 'Teacher'} {page}"
        try:
            draw.text((x, y), caption, fill="#222222", font=font)
        except UnicodeEncodeError:
            draw.text((x, y), f"{'Student' if role == '학생' else 'Teacher'} {page}", fill="#222222", font=font)
        with Image.open(path) as source:
            thumb = ImageOps.contain(source.convert("RGB"), (width, height))
            sheet.paste(thumb, (x + (width - thumb.width) // 2, y + label))
    color, gray = out / "모아보기.png", out / "모아보기-흑백.png"
    _archive_contact_sheets(out)
    sheet.save(color)
    ImageOps.grayscale(sheet).save(gray)
    return [color, gray]


def cmd_check(folder: Path) -> int:
    folder = Path(folder).resolve()
    try:
        teacher, student, build = _find_files(folder)
        teacher_text = teacher.read_text(encoding="utf-8-sig")
        student_text = student.read_text(encoding="utf-8-sig")
        out = folder / "검사"
        out.mkdir(parents=True, exist_ok=True)
        # 중간 실패가 지난 검사 결과를 유효한 것처럼 남기지 않게 보존 후 비운다.
        if (out / "report.json").exists():
            _archive(out / "report.json", out)
        for name in ("화면-폰.png", "화면-태블릿.png", "화면-폰-채움.png"):
            if (out / name).exists():
                _archive(out / name, out)
        problems = []
        if build is None:
            _problem(problems, "warning", "build-missing", None, None, "build.json이 없습니다.", "브라우저 편집본인지 확인하고 검사 결과를 검토하세요.")
        else:
            for role, path, key in (("교사용", teacher, "teacher"), ("학생용", student, "student")):
                if build.get(key, {}).get("sha256") != _sha(path):
                    _problem(problems, "warning", "build-hash-changed", None, None,
                             f"{role} HTML이 조립 기록과 다릅니다.", "브라우저 편집본인지 확인하고 최신 학생용을 검사하세요.")
        with sheet_browser() as browser:
            protocol = browser.cdp
            for domain in ("Page.enable", "Runtime.enable", "Network.enable"):
                protocol.call(domain)
            protocol.call("Network.emulateNetworkConditions", {"offline": True, "latency": 0,
                                                                  "downloadThroughput": -1, "uploadThroughput": -1})
            teacher_report, teacher_count, keys, teacher_pdf, teacher_public = _navigate(browser, teacher, True)
            student_report, student_count, _, student_pdf, _ = _navigate(browser, student, False)
            student_report['print_pen'] = _audit_pen_print(protocol, student_pdf, problems)
            interactive_info, screen_files, filled_pdf, table = None, [], None, None
            if student_report.get("interactive"):
                table = _load_answer_table(out, problems)
                interactive_info, screen_files, filled_pdf = _interactive_check(protocol, out, table, problems)
        keys += [box.get("key_text") or "" for box in teacher_report.get("boxes", [])]
        if table is not None:
            keys += [row["right"] for row in table["slots"] if isinstance(row["right"], str)]
        _audit_source(teacher_text, student_text, keys, problems, teacher_public)
        _audit_report(teacher_report, "교사용", problems)
        _audit_report(student_report, "학생용", problems)
        for role, engine, pdf in (('교사용', teacher_report, teacher_pdf), ('학생용', student_report, student_pdf)):
            engine['print_graph_labels'] = _audit_graph_labels(pdf, engine.pop('graph_regions', []), role, problems)
        _audit_fill(student_report, problems)
        outputs = [str(path) for path in screen_files]
        pdfs = [("학생", student, student_pdf, student_count), ("교사", teacher, teacher_pdf, teacher_count)]
        for role, source, data, count in pdfs:
            actual = _pdf_pages(data)
            if actual != count:
                _problem(problems, "error", "pdf-page-count", None, None,
                         f"{role} PDF {actual}쪽과 HTML {count}쪽이 다릅니다.", "인쇄 쪽 나눔과 용지 크기를 확인하세요.")
            pdf_path = source.with_suffix(".pdf")
            if pdf_path.exists():
                _archive(pdf_path, out)
            pdf_path.write_bytes(data)
            outputs.append(str(pdf_path))
        image_pdfs = [(r, d) for r, _, d, _ in pdfs]
        if filled_pdf is not None:
            actual = _pdf_pages(filled_pdf)
            if actual != student_count:
                _problem(problems, "error", "pdf-page-count", None, None,
                         f"학생 채움 PDF {actual}쪽과 HTML {student_count}쪽이 다릅니다.", "채움 인쇄 쪽 나눔을 확인하세요.")
            image_pdfs.append(("학생-채움", filled_pdf))
            _audit_filled_text(interactive_info, filled_pdf, problems)
        images = _render_pages(out, image_pdfs, problems)
        outputs.extend(str(path) for _, _, path in images)
        sheets = _contact_sheets(out, [image for image in images if image[0] != "학생-채움"], problems)
        outputs.extend(str(path) for path in sheets)
        report_path = out / "report.json"
        outputs.append(str(report_path))
        report = {"tool": "pbj-html-sheet", "version": 1,
                  "checked_at": datetime.now(timezone.utc).isoformat(),
                  "teacher": teacher_report, "student": student_report,
                  "interactive_check": interactive_info,
                  "problems": problems, "files": outputs,
                  "html_sha256": {"teacher": _sha(teacher), "student": _sha(student)}}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for role, engine in (("학생", student_report), ("교사", teacher_report)):
            for page in engine["pages"]:
                fills = ", ".join(f"{slot.get('fill', 0) * 100:.1f}%" for slot in page.get("slots", []))
                print(f"{role} {page.get('n')}쪽 · 채움 {fills}")
        for issue in problems:
            location = f"{issue['page']}쪽" if issue["page"] else "문서"
            if issue["item"]:
                location += " " + str(issue["item"])
            level = "오류" if issue["level"] == "error" else "경고"
            print(f"{level} · {location} · {issue['code']} · {issue['msg']} · {issue['fix']}")
        errors = sum(issue["level"] == "error" for issue in problems)
        warns = sum(issue["level"] == "warning" for issue in problems)
        print(f"오류 {errors} · 경고 {warns}")
        for path in sheets or [entry[2] for entry in images]:
            print(f"열어 볼 그림: {path}")
        print("다음 할 일: " + ("오류를 고치고 다시 검사하세요." if errors else "쪽 그림을 열어 확인하고 검토.md를 작성하세요."))
        return 1 if errors else 0
    except (CheckError, cdp.BrowserError, OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError, ArithmeticError) as exc:
        print(f"도구 실패: {exc} · 입력 파일과 실행 환경을 확인하세요.")
        return 2


class _DeliveryMeta(HTMLParser):
    """조립 지문과 공개 정책·실제 요소를 읽되 연동 설정 값은 보관하지 않는다."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.build = None
        self.sync = False
        self.interactive = False
        self.policy = ''
        self.has_reveal = False
        self.has_hash = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'html':
            self.interactive = 'data-interactive' in attrs
            self.policy = attrs.get('data-reveal', '시도 후')
        if tag == 'script' and attrs.get('id') == 'sheet-reveal':
            self.has_reveal = True
        if attrs.get('data-check-hash'):
            self.has_hash = True
        if tag == 'meta' and attrs.get('name') == 'sheet-build':
            try:
                self.build = json.loads(attrs.get('content', ''))
            except (ValueError, TypeError):
                self.build = None
        elif tag == 'meta' and attrs.get('name') == 'sheet-sync':
            self.sync = True


def _delivery_meta(path):
    parser = _DeliveryMeta()
    with path.open(encoding='utf-8-sig') as source:
        while True:
            chunk = source.read(8192)
            if not chunk:
                break
            parser.feed(chunk)
    parser.close()
    return parser


def _engine_matches(build, teacher, student):
    from sheet_build import engine_sha256
    installed = engine_sha256()
    return (isinstance(build, dict) and build.get('engine_sha256') == installed and
            all(isinstance(meta.build, dict) and meta.build.get('engine_sha256') == installed
                for meta in (teacher, student)))


def _page_images_missing(out, report):
    return [out / f'{prefix}-{page["n"]:02d}.png'
            for role, prefix in (('student', '학생'), ('teacher', '교사'))
            for page in report[role]['pages']
            if not (out / f'{prefix}-{page["n"]:02d}.png').is_file()]


def _has_review_reason(reason):
    letters = ''.join(c for c in unicodedata.normalize('NFKC', reason) if c.isalpha())
    return len(letters) >= 2 and letters.casefold() not in {
        '까닭', '이유', '사유', '작성', '미정', '생략', 'todo', 'tbd', 'xx', 'xxx',
    }


def _review_errors(text, report, plan_warnings=()):
    lines = text.splitlines()
    errors = []
    if not lines or lines[0].strip() != "판정: 통과":
        errors.append("첫 줄에 판정: 통과가 없습니다")
    if not re.search(r"(?m)^\s*검수:\s*(?:자체|독립)\s*$", text):
        errors.append("검수: 자체 또는 검수: 독립 줄이 없습니다")
    if not re.search(r"(?m)^##\s+내용 검산\s*$", text):
        errors.append("내용 검산 절이 없습니다")
    if report.get("student", {}).get("interactive") and not re.search(r"(?m)^##\s+화면 확인\s*$", text):
        errors.append("화면 확인 절이 없습니다")
    if plan_warnings:
        heading = re.search(r'(?m)^##[ \t]+원고 경고[ \t]*$', text)
        if heading is None:
            errors.append('원고 경고 절이 없습니다')
        else:
            section = re.split(r'(?m)^#{1,2}[ \t]+', text[heading.end():], maxsplit=1)[0]
            for code in plan_warnings:
                decisions = re.findall(rf'(?m)^[ \t]*[-*][ \t]+(?:`{re.escape(code)}`|{re.escape(code)})[ \t]*:[ \t]*'
                                       r'(?:고침|그대로[ \t]+둠)[ \t]*[—–:.-][ \t]*([^\r\n]*)$', section)
                if not any(_has_review_reason(reason) for reason in decisions):
                    errors.append(f'원고 경고 {code}의 판단과 까닭이 없습니다')
    student_text = re.sub(r"교사용\s+\d+\s*쪽", "", text)
    for page in report.get("student", {}).get("pages", []):
        n = page.get("n")
        if isinstance(n, int) and not re.search(rf"(?<!\d){n}\s*쪽", student_text):
            errors.append(f"학생 {n}쪽 검토가 없습니다")
    for page in report.get("teacher", {}).get("pages", []):
        n = page.get("n")
        if isinstance(n, int) and not re.search(rf"교사용\s+{n}\s*쪽(?!\d)", text):
            errors.append(f"교사용 {n}쪽 검토가 없습니다")
    return errors


def _plan_status(folder, *, warnings=None):
    if not (folder / "구성.json").is_file():
        return True, "구성.json이 없어 plan 확인 불가"
    try:
        from sheet_plan import load_plan
    except ImportError:
        return True, "plan 확인 불가 (검증 도구 없음)"
    try:
        _, _, issues = load_plan(folder)
        from sheet_build import plan_warning_counts
        counts = plan_warning_counts(issues)
        if warnings is not None:
            warnings.update(counts)
        errors = sum(issue.level == 'error' for issue in issues)
        return errors == 0, f'plan 오류 {errors}개'
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return True, f"plan 확인 불가 ({exc})"


_EDITED_STRUCTURE_NOTE = '편집본 미검사: 원고가 필요한 구조 검사(구성 필드·배점·검산식 등)는 하지 않았습니다.'


def _edited_plan_status(teacher, build, warnings):
    from sheet_build import _Document, plan_warning_counts, student_policy
    from sheet_markup import MarkupError, inspect_markup
    from sheet_plan import Validator, grade_level, visible_text

    doc = _Document(teacher.read_text(encoding='utf-8-sig'))
    public = doc.lookup('id', 'sheet-src')
    root = next((el for el in doc.root.elements() if el.tag == 'html'), None)
    if public is None or root is None:
        raise ValueError('활동지 본문을 찾을 수 없습니다.')
    grade, unit = build.get('grade'), build.get('unit')
    if 'grade' not in build or 'unit' not in build:
        source = teacher.parent / '구성.json'
        try:
            if build.get('source_sha256') == _sha(source):
                original = _json(source).get('meta', {})
                grade, unit = original.get('grade'), original.get('unit')
        except (OSError, ValueError, AttributeError):
            pass
        head = next((el for el in public.elements() if el.attrs.get('data-id') == 'head'), None)
        if head is not None and grade is None:
            grade = next((el.text() for el in head.walk(public=True) if el.has('ws-head-name') or el.has('ws-head-1')
                          if grade_level(el.text()) is not None), None)
    validator = Validator(teacher.parent)
    # 단원 조건이 있는 표현 점검(작도 단원의 ‘호’ 등)도 원고 검증과 같은 문맥으로 본다.
    title = next((el.text() for el in doc.root.elements() if el.tag == 'title'), None)
    validator.set_text_context({'grade': grade, 'unit': unit, 'title': title})
    validator.kind = root.attrs.get('data-kind', '수업')

    def check_field(el, path):
        source = el.attrs['data-src']
        try:
            parsed = inspect_markup(source)
        except MarkupError as error:
            validator.warn(path, f'편집본의 원본 표기를 읽을 수 없습니다: {error}',
                           '글자 요소의 data-src와 수식·빈칸 표기를 확인하세요.', 'edited-text-markup')
            return
        text = visible_text(parsed)
        validator.check_terms(text, path)
        validator.check_expressions(text, path)
        validator.check_math_notation(parsed.public_source, path)

    for el in public.walk(public=True):
        if 'data-src' not in el.attrs:
            continue
        item = el.closest(lambda node: node.has('ws-item'))
        check_field(el, item.attrs.get('data-id', '본문') if item else '본문')
    _, published = student_policy('data-interactive' in root.attrs, root.attrs.get('data-reveal', '시도 후'))
    private = doc.lookup('id', 'sheet-teacher')
    if published and private is not None:
        public_ids = {el.attrs.get('data-id') for el in public.elements() if el.has('ws-item')}
        for section in private.elements():
            item = section.attrs.get('data-solution-for')
            if item is not None and item not in public_ids:
                continue
            for field in section.elements():
                if not any(field.has(cls) for cls in ('ws-t-ans', 'ws-t-steps', 'ws-t-rubric')):
                    continue
                for el in field.walk(public=True):
                    if 'data-src' in el.attrs:
                        check_field(el, f'{item or "문항"} 풀이')
    warnings.update(plan_warning_counts(validator.issues))
    if grade is None and 'grade' not in build:
        return ' · 학년을 확인하지 못해 학년 용어 검사는 건너뜀'
    return ''


def _unchecked_html(folder, build):
    known = {build[key].get('file', '').casefold() for key in ('student', 'teacher')
             if isinstance(build.get(key), dict) and isinstance(build[key].get('file'), str)}
    names = []
    for directory, subdirs, files in os.walk(folder):
        # 검사 산출물과 조립·검사·그림 도구가 남긴 예전 판(_이전)은 스킬 밖 파일이 아니다.
        subdirs[:] = [name for name in subdirs if name not in ('검사', '_이전')]
        parent = Path(directory).relative_to(folder)
        for name in files:
            if (Path(name).suffix.lower() != '.html' or
                (parent == Path('.') and name.casefold() in known) or
                    re.search(r' \(내 답\)(?: \([0-9]+\))?\.html$', name, re.I)):
                continue
            names.append(str(parent / name))
    return [f'검사하지 않은 HTML: {name} — 정답 유출·인쇄·호환 검사를 거치지 않았습니다'
            for name in sorted(names, key=str.casefold)]


def _plan_warning_note(warnings):
    return '원고 경고: ' + (', '.join(f'{code} {count}건' for code, count in warnings.items()) or '0건')


def _delivery_draft(student, teacher, report, metadata, plan_warnings=None, *, edited_note='', unchecked=()):
    print('\n전달 문안 초안 (경로·확인 범위는 다듬어도 되지만 맨 끝 ⭐ 줄은 답의 마지막 줄에 글자 그대로 붙이세요.)')
    print(f'학생용 HTML: {student}')
    print(f'학생용 PDF: {student.with_suffix(".pdf")}')
    print(f'교사용 HTML: {teacher}')
    print(f'교사용 PDF: {teacher.with_suffix(".pdf")}')
    print(f'확인한 것: 학생 {len(report["student"]["pages"])}쪽, 교사 {len(report["teacher"]["pages"])}쪽의 검사와 검토.')
    if plan_warnings is not None:
        print(_plan_warning_note(plan_warnings))
    if edited_note:
        print(edited_note)
    for note in unchecked:
        print(note)
    info = report.get('interactive_check')
    if metadata.interactive or report['student'].get('interactive'):
        info = info if isinstance(info, dict) else {}
        screens = info.get('screens')
        sizes = [f'{s["width"]}×{s["height"]}' for s in screens or []
                 if isinstance(s, dict) and type(s.get('width')) is int and type(s.get('height')) is int]
        print('화면 크기 검사: ' + (', '.join(sizes) if sizes else '기록 없음'))
        grading = info.get('grading')
        count = info.get('grading_tested', info.get('answer_slots'))
        if grading == 'passed' and type(count) is int:
            print(f'자동 판별 자체 시험: {count}곳 통과.')
        else:
            reason = '입력 전용(reveal: 없음)으로 생략' if grading == 'skipped-reveal-none' else '기록 없음 또는 미완료'
            print(f'자동 판별 자체 시험: {reason}.')
        filled = info.get('filled_inputs')
        if isinstance(filled, dict) and info.get('filled_restored') is True:
            # Legacy reports called this field auto even when grading was skipped.
            filled = dict(filled, from_answer_table=filled.get('from_answer_table', filled.get('auto')))
            counts = ', '.join(f'{label} {filled[key]}곳' for key, label in
                               (('from_answer_table', '정답표 값으로 채운 자리'), ('open_box', '서술 칸'),
                                ('open_blank', '빈칸'), ('draw', '그리기'), ('name', '이름')) if type(filled.get(key)) is int)
            print(f'채움 인쇄 검사: {counts or "입력 수 기록 없음"}; 다시 열기 복원 확인.')
            if info.get('filled_text') == 'skipped-no-pymupdf':
                print('채운 서술의 PDF 글자 확인은 생략했습니다.')
        else:
            print('채움 인쇄 검사: 기록 없음 또는 미완료.')
    else:
        print('화면 크기·자동 판별·채움 인쇄 검사: 화면 풀이가 꺼져 있어 해당 없음.')
    print('확인하지 못한 것: 실제 인쇄, 실제 기기, 다른 브라우저.')
    if metadata.interactive or report['student'].get('interactive') or metadata.has_reveal or metadata.has_hash:
        if metadata.has_reveal:
            print('학생용에 정답과 풀이가 난독화되어 들어 있어 소스에서 찾을 수 있습니다.')
        elif metadata.has_hash:
            print('학생용에 정답 확인용 해시만 있어 화면에는 정답이 나오지 않지만, 짧은 답은 대입해 맞혀 볼 수 있습니다.')
        elif metadata.policy == '없음':
            print('학생용에 정답이 들어 있지 않습니다.')
        else:
            print('학생용에 정답 공개 자료나 정답 확인용 해시가 없습니다.')
    if metadata.sync:
        print('학생의 이름과 답이 교사의 Supabase에 저장됩니다. 학교 개인정보 규정을 확인하고 학생에게 안내해 주세요.')
    print('이 활동지 스킬이 도움이 됐다면 GitHub에 ⭐ 하나 부탁드려요 → https://github.com/BeomjinSouth/exampractice2html')


def cmd_status(folder: Path) -> int:
    folder = Path(folder).resolve()
    out = folder / "검사"
    source = folder / "구성.json"
    try:
        teacher, student, build = _find_files(folder)
        files_ok, file_note = True, "두 HTML 확인"
    except (CheckError, OSError) as exc:
        teacher = student = build = None
        files_ok, file_note = False, str(exc)

    teacher_meta = student_meta = None
    fingerprint_ok = False
    if files_ok:
        try:
            teacher_meta, student_meta = _delivery_meta(teacher), _delivery_meta(student)
            fingerprint_ok = _engine_matches(build, teacher_meta, student_meta)
        except (OSError, UnicodeError, ValueError, ImportError):
            fingerprint_ok = False

    edited = False
    if files_ok and build:
        try:
            # 원문 전체에서 검색하지 않는다. 스크립트에도 data-saved-at 문자열이 있다.
            with teacher.open(encoding="utf-8-sig") as f:
                prefix = f.read(4096)
            root = re.search(r"<html\b[^>]*>", prefix, re.I)
            saved = bool(root and re.search(r"\bdata-saved-at\s*=", root.group(), re.I))
            recorded_hash = build.get("teacher", {}).get("sha256")
            edited = saved and isinstance(recorded_hash, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", recorded_hash)) and recorded_hash.lower() != _sha(teacher)
        except (OSError, UnicodeError):
            pass
    plan_warnings, edited_note = {}, ''
    if edited:
        plan_ok, plan_note = True, '편집본이 원본'
        edited_note = _EDITED_STRUCTURE_NOTE
        try:
            extra = _edited_plan_status(teacher, build, plan_warnings)
            plan_note += ' · ' + _plan_warning_note(plan_warnings)
            edited_note += extra
        except (OSError, ValueError, ImportError) as exc:
            edited_note += f' · 편집본 문장 검사 확인 불가 ({exc})'
        plan_note += ' · ' + edited_note
    else:
        plan_ok, plan_note = _plan_status(folder, warnings=plan_warnings)
        if plan_warnings or plan_note.startswith('plan 오류'):
            plan_note += ' · ' + _plan_warning_note(plan_warnings)
    unchecked = _unchecked_html(folder, build or {})
    build_ok = False
    if files_ok:
        if edited:
            build_ok, file_note = True, "편집본이 원본 · 두 HTML 확인"
        elif not build:
            file_note = "build.json이 없습니다"
        elif not source.is_file():
            file_note = "구성.json이 없습니다"
        else:
            try:
                build_ok = build.get("source_sha256") == _sha(source)
                if not build_ok:
                    file_note = "구성.json 해시가 조립 기록과 다릅니다"
            except OSError:
                file_note = "구성.json 해시를 읽을 수 없습니다"
        if not fingerprint_ok:
            build_ok = False
            file_note = "설치된 스킬과 다른 엔진으로 조립됐습니다. build로 다시 조립하세요."

    report_path = out / "report.json"
    report, check_ok, check_note = None, False, "검사 결과가 없습니다"
    if files_ok and report_path.is_file():
        try:
            report = _json(report_path)
            entries = report["problems"]
            if not isinstance(entries, list) or not all(isinstance(x, dict) and x.get("level") in ("error", "warning") for x in entries):
                raise ValueError("판정 목록이 올바르지 않습니다")
            for role in ("teacher", "student"):
                pages = report[role]["pages"]
                if not isinstance(pages, list) or not pages:
                    raise ValueError("쪽 보고가 없습니다")
                if any(not isinstance(page, dict) or type(page.get("n")) is not int or
                       page["n"] != index or type(page.get("teacher")) is not bool
                       for index, page in enumerate(pages, 1)):
                    raise ValueError("쪽 번호나 구분이 올바르지 않습니다")
            fresh = report_path.stat().st_mtime_ns > max(teacher.stat().st_mtime_ns, student.stat().st_mtime_ns)
            hashes = report.get("html_sha256")
            if hashes:
                fresh = fresh and hashes.get("teacher") == _sha(teacher) and hashes.get("student") == _sha(student)
            errors = sum(x.get("level") == "error" for x in entries)
            check_ok = fresh and not errors
            check_note = f"오류 {errors}개" if fresh else "HTML 변경 뒤 검사를 다시 해야 합니다"
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            report = None
            check_note = f"검사 결과를 읽을 수 없습니다 ({exc})"
    elif not files_ok:
        check_note = "검사할 HTML이 없습니다"

    review_path = out / "검토.md"
    review_ok, review_note = False, "검토.md가 없습니다"
    if review_path.is_file() and report is not None:
        try:
            with student.open(encoding="utf-8-sig") as source_file:
                root = re.search(r"<html\b[^>]*>", source_file.read(4096), re.I)
            interactive = bool(root and re.search(r"(?<![\w:-])data-interactive(?=[\s=/>])", root.group(), re.I))
            student_review = dict(report["student"], interactive=interactive or bool(report["student"].get("interactive")))
            review_report = dict(report, student=student_review)
            errors = _review_errors(review_path.read_text(encoding='utf-8-sig'), review_report, plan_warnings)
            if review_path.stat().st_mtime_ns <= report_path.stat().st_mtime_ns:
                errors.insert(0, "검사 뒤에 검토하지 않았습니다")
            review_ok = not errors
            review_note = "; ".join(errors) if errors else "쪽별 검토와 내용 검산 확인"
        except (OSError, UnicodeError) as exc:
            review_note = f"검토.md를 읽을 수 없습니다 ({exc})"
    elif report is None:
        review_note = "먼저 검사 결과가 필요합니다"
    if report is not None and _page_images_missing(out, report):
        review_ok = False
        image_note = "쪽 그림이 없어 눈으로 검토할 수 없습니다. python -m pip install pymupdf pillow 후 check를 다시 실행하세요."
        review_note = image_note if review_note == "쪽별 검토와 내용 검산 확인" else review_note + "; " + image_note

    ready = plan_ok and build_ok and check_ok and review_ok
    pdfs = [student.with_suffix(".pdf"), teacher.with_suffix(".pdf")] if files_ok else []
    if files_ok and not all(path.is_file() for path in pdfs):
        ready = False
        delivery_note = "전달할 PDF가 없습니다 · check를 다시 실행하세요"
    else:
        delivery_note = "전달 가능" if ready else "앞 관문을 먼저 통과하세요"
    if unchecked:
        delivery_note += ' · ' + ' · '.join(unchecked)
    gates = [("구성", plan_ok, plan_note), ("조립", build_ok, file_note),
             ("검사", check_ok, check_note), ("검토", review_ok, review_note),
             ("전달", ready, delivery_note)]
    for index, (label, ok, note) in enumerate(gates, 1):
        print(f"{'✓' if ok else '✗'} {index} {label} · {note}")
    if ready:
        for path in (student, teacher, *pdfs):
            print(f"전달: {path}")
        print("다음 할 일: 전달할 파일을 확인하세요.")
        _delivery_draft(student, teacher, report, student_meta,
                        plan_warnings if '원고 경고:' in plan_note else None,
                        edited_note=edited_note, unchecked=unchecked)
    else:
        action = next(label for label, ok, _ in gates if not ok)
        print(f"다음 할 일: {action} 관문을 확인하세요.")
    return 0 if ready else 1
