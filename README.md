# pbj-html-sheet — 화면에서 풀고 A4로 인쇄하는 수학 활동지

Codex·Claude Code에게 "활동지 만들어 줘"라고 말하면 **HTML 파일 하나짜리 학생 활동지**를 만들어 주는 스킬입니다.

- **학생용 파일 하나로 두 가지**: 휴대폰·태블릿·PC에서 직접 입력하고 `확인`으로 정답을 확인하는 **풀기 화면**과, 그대로 인쇄하는 **A4 활동지**.
- **풀기 화면**: 빈칸·답 칸 입력, 선택지·O·X·짝짓기·순서 맞추기, 수직선·좌표평면 점 찍기, 막대 그래프 높이 정하기, 펜 그리기, 단계 힌트, 시도 뒤 정답·풀이 보기, 슬라이더 탐구, 영상. 입력은 자동 저장되고 `답을 넣어 저장`으로 제출용 파일을 받습니다.
- **교사용 파일**: 정답·예시 답안·풀이·메모가 들어 있고, 브라우저에서 글자를 두 번 눌러 바로 고친 뒤 `Ctrl+S`로 저장합니다. `학생 화면 미리보기`로 학생처럼 풀어 볼 수 있습니다.
- **검사까지 자동**: 실제 브라우저로 A4 PDF·쪽 그림·휴대폰/태블릿 화면을 뽑아 넘침·정답 유출·판별 오류를 확인합니다.

> 견본: [`examples/전체-블록-견본`](examples/전체-블록-견본) (모든 블록을 쓴 중1 정비례 활동지), [`examples/대여소-요금-비교`](examples/대여소-요금-비교) (인쇄형 1쪽). HTML 파일을 내려받아 Chrome·Edge로 열어 보세요.

## 설치

### 가장 쉬운 방법

Codex(또는 Claude Code)에게 이렇게 말하세요.

> https://github.com/BeomjinSouth/exampractice2html 이 저장소를 codex 전역 스킬로 설치해 줘.

설치가 끝나면 Codex를 새로 열고 "중1 정비례 활동지 하나 만들어 줘"처럼 요청하면 됩니다.

### 에이전트가 따라 할 설치 절차

1. 스킬 폴더에 저장소를 `pbj-html-sheet`라는 이름으로 내려받는다.
   - Codex: `~/.codex/skills/pbj-html-sheet` (Windows: `%USERPROFILE%\.codex\skills\pbj-html-sheet`)
   - Claude Code: `~/.claude/skills/pbj-html-sheet` (Windows: `%USERPROFILE%\.claude\skills\pbj-html-sheet`)

   ```bash
   git clone https://github.com/BeomjinSouth/exampractice2html ~/.codex/skills/pbj-html-sheet
   ```

   Windows PowerShell:

   ```powershell
   git clone https://github.com/BeomjinSouth/exampractice2html "$env:USERPROFILE\.codex\skills\pbj-html-sheet"
   ```

   같은 이름의 폴더가 이미 있으면 덮어쓰지 말고 사용자에게 알린다(업데이트는 그 폴더에서 `git pull`).
2. 필요한 것을 확인한다: Python 3.9 이상, Chrome 또는 Edge. 있으면 좋은 Python 모듈(없어도 동작하지만 글꼴 내장·쪽 그림이 빠진다):

   ```bash
   python -m pip install fonttools pillow pymupdf
   ```

3. 확인: `python -X utf8 ~/.codex/skills/pbj-html-sheet/scripts/sheet.py blocks`가 블록 목록을 출력하면 설치된 것이다.
4. 사용자에게 Codex(또는 Claude Code)를 새로 열어야 스킬이 보인다고 알린다.

글꼴은 컴퓨터에 설치된 Noto Sans KR·Noto Serif KR을 잘라 파일 안에 넣습니다(없으면 맑은 고딕·바탕으로 대신). 새로 내려받지 않습니다.

## 쓰는 법

- "중학교 1학년 정비례 수업 활동지 만들어 줘. 학생들이 태블릿으로 풀고 정답 확인도 하게."
- "일차방정식 연습지 2쪽, 인쇄용으로."
- "3번 표에 문제 두 개 더 넣고 2번 답칸 줄여 줘." (이미 만든 활동지 고치기)

활동지 폴더에 `<제목>.html`(학생 배부용), `<제목> (교사용).html`, 두 PDF가 생깁니다. 학생에게는 `<제목>.html`만 주면 됩니다.

## 알아 둘 점

- 학생용 파일의 정답은 해시와 난독화로만 들어 있습니다. 소스 보기로 바로 보이지는 않지만 보안 장치는 아닙니다(개발자 도구를 쓰면 찾을 수 있습니다). 점수가 걸린 평가는 화면 풀이를 끈 채(평가 종류의 기본값) 쓰세요.
- 내장 글꼴은 SIL Open Font License 1.1입니다([NOTICE.md](NOTICE.md)).

## ⭐ 도움이 되셨다면

이 스킬로 활동지를 만들어 수업에 쓰셨다면 저장소에 **스타(⭐)** 하나 눌러 주세요. 계속 다듬을 힘이 됩니다!
👉 https://github.com/BeomjinSouth/exampractice2html
