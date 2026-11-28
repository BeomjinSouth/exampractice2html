"""학생 기기(학교 태블릿)의 오래된 브라우저에서 엔진이 멈추지 않는지 문법 수준에서 본다.

기준은 Safari 15.4·Chrome/Edge 98·삼성 인터넷 18이다(references/상호작용-계약.md 11절).
그보다 새 내장 함수나 문법을 쓰면 화면 풀이 전체가 실행되지 않으므로 금지 목록으로 막는다.
CSS는 모양만 조금 달라지는 선택자(`:has()`)는 허용하고, 규칙 묶음 전체가 무시되는 문법만 막는다.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'assets' / 'engine'

# (찾을 정규식, 설명). 설명에는 처음 지원한 Safari·Chrome 판을 적는다.
TOO_NEW = [
    (r'\b(?:Map|Object)\.groupBy\b', 'Map/Object.groupBy (Safari 17.4, Chrome 117)'),
    (r'\.(?:toSorted|toReversed|toSpliced)\(', '배열 복사 메서드 (Safari 16, Chrome 110)'),
    (r'\.with\(\s*-?\d', 'Array.prototype.with (Safari 16, Chrome 110)'),
    (r'\bPromise\.withResolvers\b', 'Promise.withResolvers (Safari 17.4, Chrome 119)'),
    (r'\bArray\.fromAsync\b', 'Array.fromAsync (Safari 16.4, Chrome 121)'),
    (r'\.(?:union|intersection|symmetricDifference|isSubsetOf|isSupersetOf|isDisjointFrom)\(',
     'Set 메서드 (Safari 17, Chrome 122)'),
    (r'\.(?:isWellFormed|toWellFormed)\(', '문자열 isWellFormed (Safari 16.4, Chrome 111)'),
    (r'\(\?<[=!]', '정규식 뒤보기 (Safari 16.4) — 구문 오류로 스크립트 전체가 멈춤'),
    (r'\bstatic\s*\{', '클래스 정적 블록 (Safari 16.4)'),
    (r'\bIterator\.from\b|\.(?:drop|take)\(\d', '이터레이터 도우미 (Safari 18.4, Chrome 122)'),
]

# 이런 CSS 문법은 옛 브라우저가 규칙이나 묶음 전체를 버린다. 폰에서 2단이 쌓이지 않는 식으로 기능이 깨진다.
CSS_TOO_NEW = [
    (r'@media[^{]*\([^)]*[<>]', '미디어 쿼리 범위 문법 `(width < 720px)` (Safari 16.4, Chrome 104) — `max-width`를 쓴다'),
    (r'@container', '컨테이너 쿼리 (Safari 16, Chrome 105)'),
    (r'@layer', '캐스케이드 층 (Safari 15.4, Chrome 99)'),
    (r'color-mix\(', 'color-mix() (Safari 16.2, Chrome 111)'),
    (r'\d(?:dvh|svh|lvh|dvw|svw|lvw)', '동적 뷰포트 단위 (Safari 15.4, Chrome 108)'),
    (r'(?m)^\s*&', 'CSS 중첩 (Safari 16.5, Chrome 112)'),
]


class EngineCompatTest(unittest.TestCase):
    def test_engine_scripts_avoid_too_new_apis(self):
        scripts = sorted(ENGINE.glob('*.js'))
        self.assertTrue(scripts)
        for script in scripts:
            text = script.read_text(encoding='utf-8')
            for pattern, why in TOO_NEW:
                for match in re.finditer(pattern, text):
                    line = text.count('\n', 0, match.start()) + 1
                    self.fail(f'{script.name}:{line}: {why}를 쓰면 오래된 학교 태블릿에서 화면 풀이가 멈춥니다.')

    def test_engine_styles_avoid_rules_old_browsers_drop(self):
        sheets = sorted(ENGINE.glob('*.css'))
        self.assertTrue(sheets)
        for sheet in sheets:
            text = sheet.read_text(encoding='utf-8')
            for pattern, why in CSS_TOO_NEW:
                for match in re.finditer(pattern, text):
                    line = text.count(chr(10), 0, match.start()) + 1
                    self.fail(f'{sheet.name}:{line}: {why}를 쓰면 오래된 학교 태블릿이 그 규칙을 버립니다.')

    def test_css_rule_catches_media_range_syntax(self):
        pattern = CSS_TOO_NEW[0][0]
        self.assertRegex('@media screen and (width < 720px) {', pattern)
        self.assertRegex('@media (400px <= width <= 700px) {', pattern)
        self.assertNotRegex('@media screen and (max-width: 719.98px) {', pattern)
        self.assertNotRegex('@media print { .a > .b { color: red } }', pattern)


if __name__ == '__main__':
    unittest.main()
