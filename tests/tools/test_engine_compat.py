"""학생 기기(학교 태블릿)의 오래된 브라우저에서 엔진이 멈추지 않는지 문법 수준에서 본다.

기준은 Safari 15.4·Chrome/Edge 98·삼성 인터넷 18이다(references/상호작용-계약.md 11절).
그보다 새 내장 함수나 문법을 쓰면 화면 풀이 전체가 실행되지 않으므로 금지 목록으로 막는다.
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


if __name__ == '__main__':
    unittest.main()
