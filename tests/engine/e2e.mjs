import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.env.SHEET_TEST_TMP || path.join(here, '_out');
await fs.mkdir(out, { recursive: true });
const work = await fs.mkdtemp(path.join(process.env.SHEET_TEST_TMP || out, 'run-'));
const profile = path.join(work, 'profile');
const shots = path.join(work, 'screens');
const quote = JSON.stringify;
const run = promisify(execFile);
const chrome = process.env.SHEET_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function eventually(fn, description) {
  const end = Date.now() + 10000;
  while (Date.now() < end) {
    const result = await fn();
    if (result) return result;
    await delay(50);
  }
  throw new Error(`시간 초과: ${description}`);
}

let processError, child, exited, exit, socket, serial = 0;
const pending = new Map(), exceptions = [], remotes = [];
function command(method, params = {}, sessionId) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP 응답 시간 초과: ${method}`)); }, 20000);
    pending.set(id, { resolve, reject, timer, method });
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });
}
async function start() {
  const portFile = path.join(profile, 'DevToolsActivePort');
  const previous = await fs.readFile(portFile, 'utf8').catch(() => '');
  child = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--window-size=1200,900', '--no-first-run', '--disable-background-networking', '--disable-breakpad', 'about:blank'],
  { windowsHide: true, stdio: 'ignore', env: { ...process.env, TEMP: work, TMP: work, TMPDIR: work } });
  child.on('error', error => { processError = error; });
  exit = new Promise(resolve => child.once('exit', () => { exited = true; resolve(); }));
  const port = await eventually(async () => {
    if (processError) throw processError;
    if (exited) throw new Error(`Chrome이 일찍 종료됐습니다 (${child.exitCode ?? child.signalCode}).`);
    const file = await fs.readFile(portFile, 'utf8').catch(() => '');
    return file !== previous && Number(file.split('\n')[0]);
  }, 'Chrome 디버깅 포트');
  const { webSocketDebuggerUrl } = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(5000) }).then(r => r.json());
  socket = new WebSocket(webSocketDebuggerUrl);
  await once(socket, 'open', { signal: AbortSignal.timeout(5000) });
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    const call = pending.get(message.id);
    if (call) {
      pending.delete(message.id);
      clearTimeout(call.timer);
      if (message.error) call.reject(new Error(`${call.method}: ${message.error.message}`));
      else call.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') {
      const detail = message.params.exceptionDetails;
      exceptions.push(detail.exception?.description || detail.text);
    } else if (message.method === 'Network.requestWillBeSent' && /^https?:/i.test(message.params.request.url)) {
      remotes.push(message.params.request.url);
    }
  });
}
async function stop() {
  let treeKillError;
  try {
    if (!child?.pid || exited) return;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ id: ++serial, method: 'Browser.close' }));
      socket.close();
      await Promise.race([exit, once(AbortSignal.timeout(2500), 'abort')]);
    }
    if (!exited && process.platform === 'win32') {
      const taskkill = path.join(process.env.SystemRoot || 'C:/Windows', 'System32', 'taskkill.exe');
      try { await run(taskkill, ['/PID', String(child.pid), '/T', '/F'], { timeout: 3000, windowsHide: true }); }
      catch (error) { treeKillError = error; }
    }
    if (!exited) {
      // taskkill's return isn't confirmation that Node's own Chrome process exited.
      child.kill('SIGKILL');
      await Promise.race([exit, once(AbortSignal.timeout(3000), 'abort')]);
    }
    assert(exited, `Chrome이 종료되지 않았습니다 (PID ${child.pid}, ${processError?.message ?? treeKillError?.message ?? '종료 이벤트 없음'}).`);
  } finally {
    socket?.close();
    for (const call of pending.values()) clearTimeout(call.timer);
  }
}
async function saveFile(name, html) {
  const file = path.join(work, `${name}.html`);
  await fs.writeFile(file, html);
  return pathToFileURL(file).href;
}
const salt = '00112233445566778899aabbccddeeff';
const sha = text => createHash('sha256').update(text).digest('hex');
const hash = (salt, kind, value) => sha(`${salt}|${kind}|${value}`);
const examples = [
  ['q1.1', 'number', '1/2', '0.50', '1/3'], ['q1.2', 'text', '서로평행', '서로 평행。', '서로 수직'],
  ['q1.3', 'number', '1', '1', '2'], ['q2.1', 'choice', '1,3', [3,1], [2]],
  ['q2.2', 'ox', 'o', '○', '×'], ['q2.3', 'match', 'l1:r2,l2:r1', { l2:'r1', l1:'r2' }, { l1:'r1', l2:'r2' }],
  ['q2.4', 'order', 't2,t3,t1', ['t2','t3','t1'], ['t1','t2','t3']],
  ['q3.1', 'points', '-3/2;1', [-1.49,1.01], [0,1]], ['q3.2', 'points', '-1,1;1,2', [[1,2],[-1,1]], [[1,2],[0,1]]],
  ['q3.3', 'line', '1,-1,-1', [[0,1],[-2,-1]], [[0,1],[-1,-1]]], ['q3.4', 'bars', '2,3', [2,3], [3,2]],
];
const revealPayload = {
  slots: Object.fromEntries([...examples.map(([slot,,canonical]) => [slot, { answer_html: canonical }]),
    ['q1.4', { answer_html: '분자와 분모를 같은 수로 나눴어요.' }]]),
  items: { q1: { solution_html: '<p><b>예시 답안</b> 풀이 표식: 분수 식을 약분했어요.</p><ol><li>분모를 확인해요.</li></ol>', rubric: ['분자와 분모에 같은 수를 썼나요?', '$2$배를 확인했나요?'], video: { yt: 'M7lc1UVf-VE', start: 5 } } },
};
function fixtureData(html, policy) {
  const attr = value => quote(value).replace(/[&"<>]/g, c => ({'&':'&amp;','"':'&quot;','<':'&lt;','>':'&gt;'})[c]);
  for (const [slot, kind, canonical, right, wrong] of examples) {
    html = html.replace(`data-slot="${slot}"`, () => `data-slot="${slot}" data-check="${attr({mode:'auto',accept:[],right,wrong})}"` +
      (policy === '없음' ? '' : ` data-check-hash="${hash(salt, kind, canonical)}"`));
  }
  if (policy === '교사만' || policy === '없음') return html.replace(/^<script[^>]*id="sheet-reveal".*\n/m, '');
  const key = createHash('sha256').update(`${salt}|reveal`).digest();
  const raw = Buffer.from(quote(revealPayload), 'utf8');
  raw.forEach((v, i) => { raw[i] = v ^ key[i % key.length]; });
  return html.replace('id="sheet-reveal"></script>', `id="sheet-reveal">${raw.toString('base64')}</script>`);
}
async function fixture(name, policy = '시도 후') {
  const [html, css, ...parts] = await Promise.all([
    fs.readFile(path.join(here, `fixture-${name}.html`), 'utf8'),
    fs.readFile(path.join(here, '../../assets/engine/sheet.css'), 'utf8'),
    ...['sheet.js','solve.js','sync.js'].map(file => fs.readFile(path.join(here, '../../assets/engine', file), 'utf8')),
  ]);
  const script = parts.join('\n');
  assert.doesNotMatch(script, /<\/script/i, '스크립트는 HTML에 그대로 내장되어야 합니다.');
  assert.match(html, /<link id="sheet-css"[^>]*>/);
  assert.match(html, /<script id="sheet-js"[^>]*><\/script>/);
  const filled = name === 'interactive' ? fixtureData(html.replace('data-reveal="시도 후"', `data-reveal="${policy}"`), policy) : html;
  return saveFile(name === 'interactive' ? `${name}-${policy}` : name, filled.replace(/<link id="sheet-css"[^>]*>/, () => `<style id="sheet-css">${css}</style>`)
    .replace(/<script id="sheet-js"[^>]*><\/script>/, () => `<script id="sheet-js">${script}</script>`));
}
const metrics = (width, height, mobile = false) => ({ width, height, deviceScaleFactor: 1, mobile });
async function usingTab(url, test, viewport) {
  const { targetId } = await command('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await command('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params) => command(method, params, sessionId);
  const js = async (expression, awaitPromise = false) => {
    const { result, exceptionDetails } = await send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true });
    if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
    return result.value;
  };
  try {
    for (const domain of ['Page', 'Runtime', 'Network', 'DOM']) await send(`${domain}.enable`);
    if (viewport) await send('Emulation.setDeviceMetricsOverride', viewport);
    const nav = await send('Page.navigate', { url });
    assert(!nav.errorText, nav.errorText);
    await eventually(() => js(`location.href === ${quote(url)} && !!window.sheet`).catch(() => false), url);
    await js('sheet.ready.then(() => true)', true);
    const select = s => `document.querySelector(${quote(s)})`;
    async function mouseClick(s, count = 1) {
      const [x, y] = await js(`(() => {const el=${select(s)}; el.scrollIntoView({block:'center'}); const r=el.getBoundingClientRect(); return [r.left+r.width/2,r.top+r.height/2]})()`);
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      for (let clickCount = 1; clickCount <= count; clickCount++) {
        for (const type of ['mousePressed', 'mouseReleased']) {
          await send('Input.dispatchMouseEvent', { type, button: 'left', x, y, clickCount });
        }
      }
    }
    async function key(key, modifiers = 0) {
      const named = { Enter: 13, Escape: 27, ArrowUp: 38, ArrowDown: 40 };
      const code = named[key] ? key : `Key${key.toUpperCase()}`;
      const event = { key, code, windowsVirtualKeyCode: named[key] || key.toUpperCase().charCodeAt(0), modifiers };
      await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...event });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', ...event });
    }
    return await test({ js, send, mouseClick, key, click: s => js(`${select(s)}.click()`), report: () => js('sheet.report()') });
  } finally { await command('Target.closeTarget', { targetId }); }
}

// PyMuPDF로 PDF의 물리 크기와 내용이 비어 있지 않은지 읽고, 검수용으로 모든 쪽을 150dpi로 남긴다.
const inspectPdf = `
import json, pathlib, sys, pymupdf
with pymupdf.open(sys.argv[1]) as doc:
    out = []
    for i, page in enumerate(doc, 1):
        out.append({'width': page.rect.width, 'height': page.rect.height, 'text': page.get_text()})
        if sys.argv[2]:
            for colorspace, suffix in ((pymupdf.csRGB, ''), (pymupdf.csGRAY, '-gray')):
                page.get_pixmap(dpi=150, colorspace=colorspace).save(pathlib.Path(sys.argv[2]) / f'{sys.argv[3]}-{i:02d}{suffix}.png')
    print(json.dumps(out, ensure_ascii=False))
`;
async function pdf(tab, name, expected, capture = false) {
  assert.equal(await tab.js('document.querySelectorAll(".ws-page").length'), expected);
  const { data } = await tab.send('Page.printToPDF', { preferCSSPageSize: true, printBackground: true, displayHeaderFooter: false });
  const pdfPath = path.join(work, `${name}.pdf`);
  await fs.writeFile(pdfPath, Buffer.from(data, 'base64'));
  const { stdout } = await run(process.env.PYTHON || 'python', ['-X', 'utf8', '-c', inspectPdf, pdfPath, capture ? shots : '', name]);
  const pages = JSON.parse(stdout);
  assert.equal(pages.length, expected, `${name}: PDF 쪽 수`);
  for (const page of pages) {
    assert(Math.abs(page.width * 25.4 / 72 - 210) < 0.35, `${name}: PDF 폭 ${page.width}pt`);
    assert(Math.abs(page.height * 25.4 / 72 - 297) < 0.35, `${name}: PDF 높이 ${page.height}pt`);
    assert(page.text.trim(), `${name}: 빈 쪽`);
    assert.doesNotMatch(page.text, /정답 보기|학생용으로 저장|되돌리기/);
  }
  return pages.map(p => p.text).join('\n');
}
const fillTarget = slot => assert(slot.fill >= 0.95 && slot.fill <= 0.995 && slot.fill > slot.base_fill, JSON.stringify(slot));
const goodLayout = (report, pages) => {
  assert.deepEqual(report.issues, []);
  assert.deepEqual(report.overflow, []);
  assert.equal(report.pages.length, pages);
  assert(report.items.every(item => !item.too_tall));
  assert(report.pages.every(page => page.slots.every(slot => slot.overflow_mm === 0)));
  // 견본에는 글꼴을 내장하지 않는다. 수업형은 명조를 쓰지 않으므로 확인 대상에서 빠진다.
  assert.deepEqual(report.fonts, report.kind === '수업'
    ? { 'Sheet Sans': false }
    : { 'Sheet Sans': false, 'Sheet Serif': false });
  assert.equal(report.images.broken, 0);
  assert.equal(report.math.errors, 0);
};
let completed = 0, failed = 0;
async function test(name, operation) {
  try { await operation(); completed++; console.log(`통과 · ${name}`); }
  catch (error) { failed++; console.error(`실패 · ${name}\n${error.stack}`); }
}
const action = (name, parent = '') => `${parent ? `${parent} ` : ''}[data-sheet-action="${name}"]`;
const item = id => `.ws-item[data-id="${id}"]`;
const qText = id => `${item(id)} .ws-q-text`;
const paste = (tab, value) => tab.send('Input.insertText', { text: value });
const draft = tab => tab.js('document.querySelector(".ws-edit-input")?.value');

try {
  await start();
  await fs.mkdir(shots, { recursive: true });
  const [assessment, practice, lesson] = await Promise.all(['assessment', 'practice', 'lesson'].map(fixture));
  await test('평가형 배치·높이·번호·A4 PDF와 정답 토글', () => usingTab(assessment, async tab => {
    const report = await tab.report();
    goodLayout(report, 3);
    assert.deepEqual(report.pages.map(p => p.slots[0].items), [['head', 'q1', 'q2'], ['band', 'q3'], ['t-memo', 't-q1']]);
    assert.deepEqual(report.pages.map(p => p.teacher), [false, false, true]);
    assert.equal(report.pages[2].slots[0].fill, report.pages[2].slots[0].base_fill);
    for (const page of report.pages.slice(0, 2)) fillTarget(page.slots[0]);
    const [short, grow, draw] = report.boxes;
    assert.equal(await tab.js('parseFloat(document.querySelector(".ws-ans").style.height)'), 10.18);
    assert(short.height_mm >= 10.18, `두 줄 라벨이 있는 행의 실측 높이: ${short.height_mm}`);
    assert(Math.abs(report.boxes[3].height_mm - 10.18) < .15, '단계 기록표의 2줄 답칸');
    assert(Math.abs(short.width_mm - 162.9) < .5);
    assert(Math.abs(draw.height_mm - 57) < .15);
    assert.equal(draw.lines, null);
    assert.equal(short.key_text, '직각');
    assert(grow.height_mm > 44.02);
    assert(report.math.total >= 12 && report.images.total >= 3);
    assert.deepEqual(await tab.js('[...document.querySelectorAll(".ws-qn,.ws-mark")].map(x=>x.textContent)'), ['1.', '㉠', '2.', '3.', '㉡']);
    assert.deepEqual(await tab.js(`(() => { const r=document.querySelector('.ws-page').getBoundingClientRect(), f=25.4/96;
      return [Math.round(r.width*f),Math.round(r.height*f),Math.round(document.querySelector('.ws-head').getBoundingClientRect().width*f)]; })()`), [210, 297, 188]);
    const teacherText = await pdf(tab, 'assessment', 3, true);
    assert.match(teacherText, /교사용 메모|교사용\s*메모/);
    await tab.js('window.keptItem = document.querySelector(\'.ws-item[data-id="q1"]\'); sheet.setAnswers(false)');
    assert.equal(await tab.js('keptItem === document.querySelector(\'.ws-item[data-id="q1"]\')'), true);
    assert.equal(await tab.js('document.querySelector(".ws-blank-mark").checkVisibility()'), true);
    goodLayout(await tab.report(), 2);
    const hidden = await pdf(tab, 'assessment-hidden', 2);
    assert.doesNotMatch(hidden, /교사용 메모|수직으로 만나기|교사실 보관용/);
    await tab.key('a');
    goodLayout(await tab.report(), 3);
  }));
  await test('연습형 학생용과 수업형 두 단 A4 PDF', async () => {
    await usingTab(practice, async tab => {
      const report = await tab.report();
      goodLayout(report, 1); fillTarget(report.pages[0].slots[0]);
      assert.equal(report.images.total, 3);
      assert.deepEqual(await tab.js('[...document.querySelectorAll("#sheet-toolbar button")].map(x=>x.textContent)'), ['인쇄']);
      await tab.key('e'); await tab.key('a');
      assert.equal(await tab.js('document.documentElement.hasAttribute("data-sheet-edit") || document.documentElement.hasAttribute("data-answers")'), false);
      await pdf(tab, 'practice', 1, true);
    });
    await usingTab(lesson, async tab => {
      const report = await tab.report();
      goodLayout(report, 1);
      assert.deepEqual(report.pages[0].slots.map(slot => slot.items), [['head', 'left'], ['right']]);
      assert.equal(await tab.js('document.querySelector(".ws-top > .ws-item").dataset.id'), 'head');
      for (const slot of report.pages[0].slots) fillTarget(slot);
      const [b0,b1] = await tab.js('[...document.querySelectorAll(".ws-slot:first-child .ws-ans")].map(x=>Number(x.dataset.extraMm))');
      assert(Math.abs(b0/b1 - 1.68145) < .002, '기본 높이 비율로 두 답칸에 배분');
      await pdf(tab, 'lesson', 1, true);
    });
  });
  await test('줄 간격 재시도·큰 문항·글자 넘침 구분', () => usingTab(practice, async tab => {
    const synthetic = await tab.js(`(() => {const d=new DOMParser().parseFromString(sheet.serialize(),'text/html');
      d.querySelector('#sheet-src').innerHTML='<section class="ws-item" data-id="first" data-break="1" style="height:140mm"><p class="ws-text">첫 문항</p></section>'
        + '<section class="ws-item" data-id="second" style="height:140mm"><p class="ws-text">둘째 문항</p></section>'
        + '<section class="ws-item" data-id="giant" style="height:310mm"><table class="ws-data"><tr><td style="white-space:nowrap">'+'가'.repeat(150)+'</td></tr></table></section>'
        + '<section class="ws-item" data-id="after"><p class="ws-text">셋째 문항</p></section>';return '<!doctype html>'+d.documentElement.outerHTML;})()`);
    await usingTab(await saveFile('overflow', synthetic), async stress => {
      const report = await stress.report();
      assert.deepEqual(report.pages.map(p => p.slots[0].items), [['first', 'second'], ['giant'], ['after']]);
      assert(Math.abs(report.pages[0].slots[0].fill - .992) < .001, '4.7mm로 좁혀 첫 쪽에 두 문항을 둠');
      assert(Math.abs(report.pages[1].slots[0].overflow_mm - 23) < .1);
      assert(report.issues.some(i => i.code === 'too-tall' && i.page === 2 && i.item === 'giant'));
      assert(report.overflow.some(i => i.item === 'giant'));
      assert.equal(await stress.js('document.querySelector(".ws-over").textContent.includes("넘침")'), true);
      await pdf(stress, 'overflow', 3);
    });
  }));
  await test('편집·문의 이동·저장 후 새 탭에서 재열기', () => usingTab(assessment, async tab => {
    await tab.key('e');
    await tab.mouseClick(qText('q1'), 2);
    assert.match(await draft(tab), /두 대각선/);
    const modified = '**각의 종류**와 그 까닭을 쓰시오.';
    await paste(tab, modified);
    await tab.key('Enter');
    assert.equal(await tab.js(`document.querySelector(${quote(qText('q1') + ' strong')}).textContent`), '각의 종류');
    await tab.click(action('more-lines', `${item('q1')} .ws-ans[data-lines="2"]`));
    assert.equal((await tab.report()).boxes[0].lines, 3);
    await tab.click(action('up', item('q2')));
    assert.deepEqual((await tab.report()).pages[0].slots[0].items, ['head', 'q2', 'q1']);
    await tab.key('z', 2);
    assert.deepEqual((await tab.report()).pages[0].slots[0].items, ['head', 'q1', 'q2']);
    await tab.click(action('up', item('q2')));
    await tab.click(action('body-more'));
    await tab.click(action('question-more'));
    const saved = await tab.js('sheet.serialize()');
    assert.match(saved, /data-saved-at="\d{4}-/);
    const clean = await tab.js(`(() => { const d=new DOMParser().parseFromString(sheet.serialize(),'text/html');
      return !d.querySelector('#sheet-pages,.ws-page,.ws-tools,[contenteditable],[data-extra-mm],[data-sheet-edit],.ws-ans[style]'); })()`);
    assert(clean);
    const report = await tab.report();
    await usingTab(await saveFile('edited', saved), async copy => {
      assert.deepEqual(await copy.report(), report);
      assert.equal(await copy.js(`document.querySelector(${quote(qText('q1'))}).dataset.src`), modified);
      await copy.js('window.probe = {picks:0,writes:[],closes:0}; window.showSaveFilePicker = async o => { probe.picks++; probe.name=o.suggestedName; return {createWritable:async()=>({write:async text=>probe.writes.push(text),close:async()=>{probe.closes++}})}; };');
      await copy.key('s', 2); await eventually(() => copy.js('probe.closes === 1'), '첫 저장');
      await copy.click(action('save')); await eventually(() => copy.js('probe.closes === 2'), '두 번째 저장');
      assert.deepEqual(await copy.js('[probe.picks,probe.writes.length,probe.name]'), [1, 2, '도형과 좌표 (교사용).html']);
    });
  }));
  await test('학생용 원문에 정답·교사 전용 요소가 남지 않음', () => usingTab(assessment, async tab => {
    const student = await tab.js('sheet.exportStudent()');
    assert.doesNotMatch(student, /두 대각선은 서로 수직|교사실 보관용 도형 자료|예상 오개념<\/b>/);
    // 엔진 스크립트에 있는 공통 클래스 이름이 아니라, 실행 전 원고를 검사한다.
    const clean = await tab.js(`!new DOMParser().parseFromString(sheet.exportStudent(), 'text/html').querySelector('.ws-key,.ws-teacher-only,[data-src],.ws-key-choice,#sheet-teacher')`);
    assert(clean, '학생용 원고에서 정답 표시가 물리적으로 지워짐');
    assert.match(student, /<html[^>]*data-role="student"/);
    assert.doesNotMatch(student, /<html[^>]*data-answers=/);
    await usingTab(await saveFile('student', student), async copy => {
      goodLayout(await copy.report(), 2);
      assert.equal(await copy.js('!!document.querySelector(".ws-key,.ws-teacher-only,[data-src],.ws-key-choice,#sheet-teacher")'), false);
      assert.equal(await copy.js('document.querySelectorAll("#sheet-toolbar button").length'), 1);
      assert.equal(await copy.js('document.querySelector(\'.ws-item[data-id="q3"] .ws-blank-mark\').textContent'), '㉡');
      await pdf(copy, 'student', 2);
    });
  }));
  await test('JS 인라인 표기·잘못된 수식 취소·빈칸 번호 유지', () => usingTab(assessment, async tab => {
    const cases = JSON.parse(await fs.readFile(path.join(here, 'markup-cases.json'), 'utf8'));
    await tab.key('e');
    // 이미 기호가 있는 자리 뒤에 새 기호를 넣고, 검사할 때는 data-mark 원본으로 정규화한다.
    const target = qText('q1');
    for (const { src, html } of cases) {
      await tab.mouseClick(target, 2);
      await paste(tab, src);
      await tab.key('Enter');
      const compare = await tab.js(`(() => {
        const want=document.createElement('span'); want.innerHTML=${quote(html)};
        const got=document.querySelector(${quote(target)}).cloneNode(true);
        for (const blank of got.querySelectorAll('[data-mark]')) blank.querySelector('.ws-blank-mark').textContent=blank.dataset.mark;
        return {got:got.innerHTML,want:want.innerHTML}; })()`);
      assert.equal(compare.got, compare.want, src);
    }
    const before = await tab.js(`document.querySelector(${quote(target)}).innerHTML`);
    await tab.mouseClick(target, 2);
    await paste(tab, '$\\frac{1}$');
    await tab.key('Enter');
    assert.equal(await tab.js('document.querySelector(".ws-edit-input").getAttribute("aria-invalid")'), 'true');
    await tab.key('Escape');
    assert.equal(await draft(tab), undefined);
    assert.equal(await tab.js(`document.querySelector(${quote(target)}).innerHTML`), before);
    assert.equal((await tab.report()).math.errors, 0);
  }));
  await test('교사용 편집 막대·글꼴 내장·내려받기 대체', () => usingTab(assessment, async tab => {
    await tab.key('e');
    const before = await tab.report();
    await tab.mouseClick(action('taller', `${item('q1')} [data-kind="draw"]`));
    assert(Math.abs((await tab.report()).boxes[2].height_mm - before.boxes[2].height_mm - 5) < .03);
    await tab.key('z', 2);
    assert.deepEqual(await tab.report(), before);
    await tab.click(action('duplicate', item('q2')));
    const duplicate = (await tab.report()).items.find(row => row.id.startsWith('q2-copy-'));
    assert(duplicate);
    await tab.click(action('delete', item(duplicate.id)));
    assert.deepEqual(await tab.report(), before);

    await tab.js(`{ const menu=document.querySelector('select[aria-label="글꼴"]'); menu.value='file'; menu.dispatchEvent(new Event('change')); }`);
    const { root: dom } = await tab.send('DOM.getDocument');
    const { nodeId } = await tab.send('DOM.querySelector', { nodeId: dom.nodeId, selector: '#sheet-toolbar input[type=file]' });
    await tab.send('DOM.setFileInputFiles', { nodeId, files: [process.env.SHEET_TEST_FONT || 'C:/Windows/Fonts/malgun.ttf'] });
    await eventually(() => tab.js(`document.fonts.check('10pt "Sheet Custom 1"', '가') && getComputedStyle(document.querySelector('.ws-head')).fontFamily.startsWith('"Sheet Custom 1"')`), '제목에 추가 글꼴 적용');
    assert(await tab.js('new DOMParser().parseFromString(sheet.serialize(),"text/html").querySelector("#sheet-fonts").textContent.includes("data:font/ttf;base64,")'));
    await tab.click(action('undo'));
    assert.deepEqual(await tab.report(), before);

    await command('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: work });
    await tab.js('window.showSaveFilePicker = undefined');
    await tab.key('s', 2);
    const saved = await eventually(() => fs.readFile(path.join(work, '도형과 좌표 (교사용).html'), 'utf8').catch(() => false), '교사용 HTML 내려받기');
    assert.match(saved, /data-role="teacher"/);
    await tab.click(action('export-student'));
    const student = await eventually(() => fs.readFile(path.join(work, '도형과 좌표.html'), 'utf8').catch(() => false), '학생용 HTML 내려받기');
    assert.match(student, /data-role="student"/);
    assert.doesNotMatch(student, /교사실 보관용 도형 자료/);

    await tab.send('Emulation.setDeviceMetricsOverride', { width: 375, height: 812, deviceScaleFactor: 1, mobile: true });
    assert.deepEqual(await tab.js(`(() => {const main=document.querySelector('#sheet-pages'); return [document.documentElement.scrollWidth === innerWidth, main.scrollWidth > main.clientWidth, Math.round(main.querySelector('.ws-page').offsetWidth * 25.4/96)];})()`), [true, true, 210]);
  }));
  const interactive = await fixture('interactive');
  let studentInteractive;
  await test('2판 A4 ↔ 학생 미리보기, 원래 배치와 정답 상태 복원', async () => usingTab(interactive, async tab => {
    studentInteractive = await saveFile('interactive-student', await tab.js('sheet.exportStudent()'));
    const before = await tab.report();
    assert.equal(before.view, 'a4');
    assert.equal(before.interactive, true);
    assert.equal(before.slots_total, 16);
    await tab.click(action('preview-student'));
    assert.equal(await tab.js('sheet.view()'), 'solve');
    assert.equal(await tab.js('document.querySelectorAll("#sheet-flow > .ws-item").length'), 5);
    assert.equal(await tab.js('document.documentElement.dataset.answers'), 'off');
    await tab.js('sheet.setAnswers(true)');
    assert.equal(await tab.js('document.documentElement.dataset.answers'), 'off');
    await tab.click(action('return-teacher'));
    assert.equal(await tab.js('document.documentElement.dataset.answers'), 'on');
    const after = await tab.report();
    assert.deepEqual(after.items, before.items);
    assert.deepEqual(after.pages, before.pages);
  }));
  await test('JS 정규화·해시·난독화 기준 자료 전부 및 Node SHA-256 대조', () => usingTab(interactive, async tab => {
    const cases = JSON.parse(await fs.readFile(path.join(here, 'answer-cases.json'), 'utf8'));
    for (const name of ['norm','num','number_parts','text_candidates','plain','points','line']) {
      for (const [input, expected] of cases[name]) {
        assert.deepEqual(await tab.js(`__sheetCore.codec.${name}(${quote(input)})`), expected, `${name}: ${quote(input)}`);
      }
    }
    assert.deepEqual(await tab.js(`(${quote(cases.units)}).map(u=>__sheetCore.codec.num('18'+u))`), cases.units.map(() => '18'));
    for (const name of ['inferred_unit','number_forms','input_mode']) {
      for (const [args, expected] of cases[name]) {
        assert.deepEqual(await tab.js(`__sheetCore.codec.${name}(...${quote(args)})`), expected, `${name}: ${quote(args)}`);
      }
    }
    for (const spec of cases.writing_results) {
      const result = await tab.js(`(() => {
        const {kind, answer, unit, form, strict, cases} = ${quote(spec)};
        const codec = __sheetCore.codec;
        const forms = codec.writing_forms(kind, [codec.plain(answer)], unit);
        return cases.map(([value]) => codec.writing_result(kind, value, v => forms.includes(v), form, strict, !!unit));
      })()`);
      assert.deepEqual(result, spec.cases.map(([, expected]) => expected), quote(spec));
    }
    for (const [s, kind, value, expected] of cases.hash) {
      assert.equal(await tab.js(`__sheetCore.codec.hash(${[s,kind,value].map(quote).join(',')})`), expected);
      assert.equal(expected, hash(s,kind,value));
    }
    for (const text of ['', 'abc', '한글과 수식 π', 'A'.repeat(55), 'A'.repeat(56), 'A'.repeat(64), '😀'.repeat(300)]) {
      assert.equal(await tab.js(`__sheetCore.codec.sha256(${quote(text)})`), sha(text));
    }
    for (const [s, payload, encoded] of cases.reveal) {
      assert.deepEqual(await tab.js(`__sheetCore.codec.decodeReveal(${quote(s)}, ${quote(encoded)})`), payload);
      assert.equal(await tab.js(`__sheetCore.codec.encodeReveal(${quote(s)}, ${quote(payload)})`), encoded);
    }
  }));
  const slot = name => `[data-slot="${name}"]`;
  const results = (tab, col) => tab.js(`${quote(examples)}.map(row => sheet.answer(row[0], row[${col}]))`);
  const setInput = (tab, selector, value) => tab.js(`{ const input=document.querySelector(${quote(selector)}); input.value=${quote(value)}; input.dispatchEvent(new Event('input',{bubbles:true})); }`);
  const valuesOf = tab => tab.js('sheet.values()');
  await test('자리별 판별 결과·점수·입력·그리기·짝·순서 상태', () => usingTab(studentInteractive, async tab => {
    assert.deepEqual(await tab.js('sheet.slots().filter(s=>s.auto).map(s=>[s.slot,s.kind])'), examples.map(([s,k])=>[s,k]));
    assert.deepEqual(await results(tab, 3), examples.map(() => true));
    assert.deepEqual(await tab.js('sheet.score()'), { checked:11,correct:11,total:11 });
    assert.deepEqual(await results(tab, 4), examples.map(() => false));
    assert.deepEqual(await tab.js('sheet.score()'), { checked:11,correct:0,total:11 });
    assert.equal(await tab.js(`sheet.answer('q3.4',[2,null])`), null);
    assert.equal(await tab.js(`sheet.answer('q2.3',{l1:'r2'})`), null);
    assert.equal(await tab.js(`sheet.answer('q3.3',[[1,1],[1,1]])`), null);
    assert.equal(await tab.js(`sheet.answer('q1.4','분자와 분모에 같은 수를 사용했어요.')`), null);
    await tab.js('sheet.clearAnswers()');
    const filled = `${slot('q1.3')} input`;
    await tab.mouseClick(filled); await paste(tab, '1');
    assert.equal((await valuesOf(tab))['q1.3'], '1');
    await tab.click(action('check', item('q1')));
    assert.equal(await tab.js(`document.querySelector(${quote(slot('q1.3') + ' .ws-verdict')}).textContent`), '✓');
    await setInput(tab, filled, '2');
    assert.equal(await tab.js(`document.querySelector(${quote(slot('q1.3') + ' .ws-verdict')}).hidden`), true);
    await tab.mouseClick(`${slot('q2.1')} [data-n="1"]`);
    await tab.mouseClick(`${slot('q2.1')} [data-n="3"]`);
    await tab.mouseClick(`${slot('q2.2')} .ws-ox-o`);
    await tab.mouseClick(`${slot('q2.3')} [data-token="l1"]`);
    await tab.mouseClick(`${slot('q2.3')} [data-token="r2"]`);
    await tab.mouseClick(`${slot('q2.4')} [data-token="t1"] ${action('order-down')}`);
    await tab.mouseClick(`${slot('q4.2')} li:first-child`);
    await tab.mouseClick(`${slot('q3.1')} svg`);
    await tab.mouseClick(`${slot('q3.4')} .ws-bar-hit[data-i="0"]`);
    const penSelector = `${slot('q4.1')} canvas`;
    const [x,y] = await tab.js(`(() => { const el=document.querySelector(${quote(penSelector)}); el.scrollIntoView({block:'center'}); const r=el.getBoundingClientRect(); return [r.left+20,r.top+20]; })()`);
    await tab.send('Input.dispatchMouseEvent', { type:'mousePressed', x,y, button:'left', clickCount:1 });
    await tab.send('Input.dispatchMouseEvent', { type:'mouseMoved', x:x+50,y:y+20, buttons:1 });
    await tab.send('Input.dispatchMouseEvent', { type:'mouseReleased', x:x+70,y:y+40, button:'left', clickCount:1 });
    const got = await valuesOf(tab);
    assert.deepEqual(got['q2.1'], [1,3]); assert.equal(got['q2.2'], 'O');
    assert.deepEqual(got['q2.3'], { l1:'r2' }); assert.deepEqual(got['q2.4'], ['t2','t1','t3']);
    assert.deepEqual(got['q3.1'], [0]); assert.deepEqual(got['q3.4'], [2,null]);
    assert.deepEqual(got['q4.2'], [true,false]); assert(got['q4.1'].strokes[0].length >= 2);
    await tab.click(action('eraser',slot('q4.1')));
    await tab.send('Input.dispatchMouseEvent', { type:'mousePressed', x,y, button:'left', clickCount:1 });
    await tab.send('Input.dispatchMouseEvent', { type:'mouseReleased', x,y, button:'left', clickCount:1 });
    assert.deepEqual((await valuesOf(tab))['q4.1'], { strokes:[] });
    await tab.click(action('pen', slot('q4.1')));
    // Replay the failing event order: Chrome lost capture with the button still held.
    await tab.js(`{
      const canvas = document.querySelector(${quote(penSelector)});
      const r = canvas.getBoundingClientRect();
      const event = (type, left, top, buttons) => new PointerEvent(type, {
        pointerId: 91, pointerType: 'mouse', clientX: r.left + left, clientY: r.top + top, buttons, bubbles: true
      });
      canvas.dispatchEvent(event('pointerdown', 20, 20, 1));
      canvas.dispatchEvent(event('lostpointercapture', 70, 40, 1));
      document.body.dispatchEvent(event('pointermove', 70, 40, 1));
      document.body.dispatchEvent(event('pointerup', 90, 60, 0));
    }`);
    const completedStroke = (await valuesOf(tab))['q4.1'].strokes[0];
    assert.equal(completedStroke.length, 3);
    assert(completedStroke[2][0] > completedStroke[1][0]);
    await tab.click(action('pen', slot('q3.1')));
    await tab.mouseClick(`${slot('q3.1')} canvas`);
    assert.deepEqual((await valuesOf(tab))['q3.1'], [0], '펜 필기가 판별용 점을 바꾸지 않음');
    assert(await tab.js(`document.querySelector('${slot('q3.1')} canvas').getContext('2d').getImageData(0,0,1000,200).data.some(v=>v!==0)`));
    await tab.click(action('pen-plot', slot('q3.1')));
    await tab.mouseClick(`${slot('q3.1')} svg`);
    assert.deepEqual((await valuesOf(tab))['q3.1'], [], '점 찍기로 돌아가 같은 점을 지움');
    await tab.js('sheet.clearAnswers()');
  }));
  await test('펜 인쇄는 밑그림 위에 SVG 획만 남김', () => usingTab(studentInteractive, async tab => {
    const standalone = [slot('q1.5'), slot('q4.1')], grid = slot('q3.2');
    const targets = [...standalone, grid];
    const layers = () => tab.js(`${quote(targets)}.map(s => {
      const el=document.querySelector(s), canvas=el.querySelector('canvas.ws-pen'), ink=el.querySelector('svg.ws-pen-ink');
      const base=el.querySelector('.ws-draw-frame, svg:not(.ws-pen-ink), img') || el;
      const geometry=n=>{const {left,top,width,height}=n.getBoundingClientRect();return [left,top,width,height];};
      return { canvas:canvas.checkVisibility(), ink:ink?.checkVisibility(), strokes:ink?.querySelectorAll('polyline').length,
        position:ink && geometry(ink), base:geometry(base), baseVisible:base.checkVisibility() };
    })`);
    try {
      assert((await layers()).every(r => r.canvas && !r.ink));
      await tab.js(`sheet.view('a4')`, true);
      await tab.send('Emulation.setEmulatedMedia', {media:'print'});
      const blank = await layers();
      assert(blank.every(r => !r.canvas && r.ink && r.strokes === 0 && r.baseVisible), quote(blank));
      await tab.send('Emulation.setEmulatedMedia', {media:''});
      await tab.js(`sheet.view('solve')`, true);
      const strokes = [[[.1,.2],[.8,.6]], [[.4,.4]]];
      await tab.js(`sheet.answer('q1.5',{strokes:${quote(strokes)}}); sheet.answer('q4.1',{strokes:${quote(strokes)}});
        sheet.answer('q3.2',[[1,2]])`);
      await tab.click(action('pen', grid));
      await tab.mouseClick(`${grid} canvas.ws-pen`);
      await tab.js(`sheet.view('a4')`, true);
      await tab.send('Emulation.setEmulatedMedia', {media:'print'});
      assert.deepEqual((await layers()).map(r => [r.canvas,r.ink,r.strokes,r.baseVisible]),
        [[false,true,2,true],[false,true,2,true],[false,true,1,true]]);
      for (const r of (await layers()).slice(1)) assert.deepEqual(r.position, r.base, '잉크와 밑그림의 위치·크기');
      assert.equal(await tab.js(`document.querySelectorAll('${grid} .ws-user-point').length`), 1);
      assert.equal(await tab.js(`!!new DOMParser().parseFromString(sheet.serialize(),'text/html').querySelector('.ws-pen-ink')`), false);
      await tab.js('sheet.clearAnswers()');
      assert.deepEqual((await layers()).map(r => r.strokes), [0,0,0]);
    } finally { await tab.js('sheet.clearAnswers()'); await tab.send('Emulation.setEmulatedMedia', {media:''}); await tab.js(`sheet.view('solve')`, true); }
  }));
  const penState = (tab, id = 'q1.5') => tab.js(`sheet.values()[${quote(id)}]?.strokes ?? []`);
  async function penGesture(tab, selector, [x0, y0], moves = [], during) {
    const [left, top] = await tab.js(`(() => {
      const c = document.querySelector(${quote(selector)});
      (c.closest('.ws-pen-viewport') || c).scrollIntoView({block:'center'});
      const r = c.getBoundingClientRect(); return [r.left, r.top];
    })()`);
    const send = (type, x, y, extra) => tab.send('Input.dispatchMouseEvent', {type, x:left+x, y:top+y, ...extra});
    await send('mousePressed', x0, y0, {button:'left', clickCount:1});
    for (const [x,y] of moves) await send('mouseMoved', x, y, {buttons:1});
    if (during) await during();
    await send('mouseReleased', ...(moves.at(-1) || [x0,y0]), {button:'left', clickCount:1});
  }
  await test('폰에서 크게 펼쳐 그린 45° 선과 컴퍼스 원이 인쇄에서도 같은 모양', async () => {
    const html = await fs.readFile(fileURLToPath(studentInteractive), 'utf8');
    const url = await saveFile('construction-student', html.replace('data-height-mm="25"', 'data-height-mm="85"'));
    await usingTab(url, async tab => {
      const draw = slot('q1.5'), canvas = `${draw} canvas.ws-pen`, btn = name => action(name, draw);
      const select = s => `document.querySelector(${quote(s)})`;
      const marks = (left, right) => tab.js(`(() => {
        const c = ${select(canvas)}, {width} = c.getBoundingClientRect(), dpr = c.width/width;
        const x = Math.round(${left}*dpr), w = Math.round(${right}*dpr)-x, h = c.height;
        const data = c.getContext('2d').getImageData(x,0,w,h).data;
        let minX=w, maxX=-1, minY=h, maxY=-1;
        for (let row=0; row<h; row++) for (let col=0; col<w; col++) if (data[(row*w+col)*4+3] > 127) {
          minX=Math.min(col,minX); maxX=Math.max(col,maxX); minY=Math.min(row,minY); maxY=Math.max(row,maxY);
        }
        return maxX < 0 ? null : [maxX-minX+1, maxY-minY+1];
      })()`);
      const size = () => tab.js(`(() => {const r=${select(canvas)}.getBoundingClientRect(); return [r.width,r.height];})()`);
      const pressed = () => tab.js(`${select(btn('pen-zoom'))}.getAttribute('aria-pressed')`);
      try {
        const small = await size();
        assert.equal(await pressed(), 'false');
        await tab.mouseClick(btn('pen-zoom'));
        assert.equal(await pressed(), 'true');
        const large = await size();
        assert(large[0] >= small[0] * 1.9, quote({small,large}));
        assert(Math.abs(large[0]/large[1] / (small[0]/small[1]) - 1) <= .015);
        assert.equal((await tab.report()).overflow_x, 0);
        await tab.click(btn('pen-line'));
        assert.equal(await tab.js(`${select(btn('pen-line'))}.getAttribute('aria-pressed')`), 'true');
        await penGesture(tab, canvas, [17,24], [[37,56],[67,74]], async () => {
          assert.deepEqual(await penState(tab), [], '놓기 전에는 저장하지 않음');
          assert(await marks(15,72), '끄는 동안 선 미리보기가 보임');
        });
        assert.equal((await penState(tab))[0].length, 2, '중간 지점을 거치지 않고 두 끝점을 이음');
        await tab.click(btn('pen-compass'));
        await penGesture(tab, canvas, [130,65]);
        assert.equal(await tab.js(`${select(`${draw} [aria-live="polite"]`)}.textContent`), '먼저 끌어서 컴퍼스를 벌리세요');
        assert.equal((await penState(tab)).length, 1);
        await penGesture(tab, canvas, [122,65], [[149,65]], async () => {
          assert.equal((await penState(tab)).length, 1);
          assert(await marks(95,153), '컴퍼스 폭을 재는 동안 원 미리보기');
        });
        assert.equal(await marks(95,153), null, '폭만 잰 뒤 원은 남지 않음');
        await penGesture(tab, canvas, [190,65]);
        assert((await penState(tab))[1].length >= 72);
        const [w,h] = await marks(155,225);
        assert(Math.abs(w/h-1) <= .02, `폰 캔버스 원의 지름비 ${w/h}`);
        assert(Math.abs(w/2 - 55) <= 2, '다른 점에서 잰 반지름을 그대로 사용');
        await tab.js("sheet.view('a4')", true);
        await tab.send('Emulation.setEmulatedMedia', {media:'print'});
        const [angle, aspect, visibleTools] = await tab.js(`(() => {
          const [line,circle] = document.querySelectorAll(${quote(`${draw} svg.ws-pen-ink polyline`)});
          const [a,b] = [line.points.getItem(0), line.points.getItem(1)].map(p =>
            new DOMPoint(p.x,p.y).matrixTransform(line.getScreenCTM()));
          const c = circle.getBoundingClientRect();
          return [Math.atan2(b.y-a.y,b.x-a.x)*180/Math.PI,c.width/c.height,${select(btn('pen-line'))}.checkVisibility()];
        })()`);
        assert(Math.abs(angle-45) <= 1, `A4 선의 각 ${angle}°`);
        assert(Math.abs(aspect-1) <= .02, `A4 원의 지름비 ${aspect}`);
        assert.equal(visibleTools, false);
      } finally {
        await tab.send('Emulation.setEmulatedMedia', {media:''});
        await tab.js('sheet.clearAnswers()');
      }
    }, metrics(390,844));
  });
  await test('폰의 좁은 펜 그림 확대와 정답·화면·인쇄 획의 자리 일치', async () => {
    const html = await fs.readFile(fileURLToPath(interactive), 'utf8');
    const image = svg => `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
    const base = image('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 82"><circle cx="42" cy="41" r="30" fill="none" stroke="black"/></svg>');
    const answer = image('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 82"><path d="M10 10L70 70" stroke="red"/></svg>');
    const figure = `<div class="ws-split" data-ratio="1:1"><div class="ws-split-col"><figure class="ws-fig" data-pen="1" data-slot="q4.1" data-check-kind="draw"><img src="${base}" style="width:82mm" alt="그림"><img class="ws-key ws-fig-answer" src="${answer}" style="width:82mm" alt="" aria-hidden="true"></figure></div><div class="ws-split-col">설명</div></div>`;
    const source = html.replace(/<figure class="ws-fig" data-pen="1" data-slot="q4\.1"[\s\S]*?<\/figure>/, figure);
    assert.notEqual(source, html);
    const teacher = await saveFile('narrow-pen-teacher', source);
    const student = await usingTab(teacher, tab => tab.js('sheet.exportStudent()'));
    const select = s => `document.querySelector(${quote(s)})`;
    const draw = slot('q4.1'), canvas = `${draw} canvas.ws-pen`, btn = name => action(name, draw);
    const geometry = (tab, selector) => tab.js(`(() => {const r=${select(selector)}.getBoundingClientRect();return [r.left,r.top,r.width,r.height];})()`);
    const sameBox = (a, b, message) => assert(a.every((value, i) => Math.abs(value-b[i]) < .1), `${message}: ${quote({a,b})}`);
    await usingTab(await saveFile('narrow-pen-student', student), async tab => {
      try {
        await tab.js('sheet.clearAnswers()');
        const small = await geometry(tab, `${draw} > img`);
        sameBox(await geometry(tab, canvas), small, '보통 크기의 그림과 캔버스');
        await tab.mouseClick(btn('pen-zoom'));
        const large = await geometry(tab, `${draw} .ws-pen-stage > img`);
        const viewportWidth = await tab.js(`${select(`${draw} .ws-pen-viewport`)}.clientWidth`);
        assert(large[2] >= small[2] * 1.9 && large[2] <= small[2] * 2.5, quote({small,large}));
        assert(Math.abs(large[2] / viewportWidth - 2) < .02, quote({large,viewportWidth}));
        assert(Math.abs((large[2]/large[3]) / (small[2]/small[3]) - 1) <= .015);
        sameBox(await geometry(tab, canvas), large, '확대한 그림과 캔버스');
        assert.equal((await tab.report()).overflow_x, 0);
        const [scrollX, scrollY] = await tab.js(`(() => {const box=${select(`${draw} .ws-pen-viewport`)};
          box.scrollLeft=100; box.scrollTop=70; return [box.scrollLeft,box.scrollTop];})()`);
        assert(scrollX > 0 && scrollY > 0, '확대한 그림 안에서 가로·세로 이동');
        sameBox(await geometry(tab, canvas), await geometry(tab, `${draw} .ws-pen-stage > img`), '이동한 그림과 캔버스');
        await tab.click(btn('pen-line'));
        await penGesture(tab, canvas, [scrollX+40,scrollY+50], [[scrollX+140,scrollY+150]]);
        const stroke = await penState(tab, 'q4.1');
        assert.equal(stroke.length, 1);
        const expected = [[(scrollX+40)/large[2], (scrollY+50)/large[3]], [(scrollX+140)/large[2], (scrollY+150)/large[3]]];
        for (const [i, point] of stroke[0].entries()) for (const [j, value] of point.entries()) {
          assert(Math.abs(value-expected[i][j]) < .001, '스크롤한 그림에서 그은 선의 상대 좌표');
        }
        await tab.click(btn('pen-zoom'));
        sameBox(await geometry(tab, canvas), await geometry(tab, `${draw} > img`), '축소한 그림과 캔버스');
        assert.deepEqual(await penState(tab, 'q4.1'), stroke);
        await tab.click(btn('pen-zoom'));
        await tab.js("sheet.view('a4')", true);
        await tab.send('Emulation.setEmulatedMedia', {media:'print'});
        const paper = await geometry(tab, `${draw} > img`);
        sameBox(await geometry(tab, `${draw} svg.ws-pen-ink`), paper, '인쇄 그림과 펜 SVG');
        const points = await tab.js(`(() => {const line=${select(`${draw} svg.ws-pen-ink polyline`)};
          return [...line.points].map(p=>{const absolute=new DOMPoint(p.x,p.y).matrixTransform(line.getScreenCTM());
            return [p.x,p.y,absolute.x,absolute.y];});})()`);
        for (const [i, point] of points.entries()) {
          for (const [j, value] of point.slice(0,2).entries()) {
            assert(Math.abs(value-stroke[0][i][j]) < .00001, '확대해 그린 선의 인쇄 좌표');
            assert(Math.abs(point[j+2]-(paper[j]+value*paper[j+2])) < .1, '인쇄 그림에서 선이 놓인 자리');
          }
        }
        assert.deepEqual(await penState(tab, 'q4.1'), stroke);
      } finally { await tab.send('Emulation.setEmulatedMedia', {media:''}); await tab.js('sheet.clearAnswers()'); }
    }, metrics(390,844));
    await usingTab(teacher, async tab => {
      await tab.click(action('preview-student'));
      // 미리보기는 정답을 숨긴다. 배치만 확인하기 위해 이 탭에서 잠시 표시한다.
      await tab.js("document.documentElement.dataset.answers='on'");
      sameBox(await geometry(tab, `${draw} .ws-fig-answer`), await geometry(tab, `${draw} > img:not(.ws-fig-answer)`), '보통 크기의 정답층');
      await tab.click(btn('pen-zoom'));
      sameBox(await geometry(tab, `${draw} .ws-fig-answer`), await geometry(tab, `${draw} .ws-pen-stage > img:not(.ws-fig-answer)`), '확대한 정답층');
      sameBox(await geometry(tab, canvas), await geometry(tab, `${draw} .ws-fig-answer`), '확대한 정답층과 캔버스');
      assert.equal((await tab.report()).overflow_x, 0);
      await tab.js(`{const box=${select(`${draw} .ws-pen-viewport`)}; box.scrollLeft=100; box.scrollTop=70;}`);
      sameBox(await geometry(tab, `${draw} .ws-fig-answer`), await geometry(tab, `${draw} .ws-pen-stage > img:not(.ws-fig-answer)`), '이동한 정답층');
      sameBox(await geometry(tab, canvas), await geometry(tab, `${draw} .ws-fig-answer`), '이동한 정답층과 캔버스');
      assert.equal(await tab.js(`new DOMParser().parseFromString(sheet.serialize(),'text/html').querySelectorAll(${quote(`#sheet-src ${draw} > .ws-fig-answer`)}).length`), 1);
      assert.equal(await tab.js(`!!new DOMParser().parseFromString(sheet.exportStudent(),'text/html').querySelector('#sheet-src .ws-fig-answer')`), false);
      await tab.js("sheet.view('a4')", true);
      await tab.send('Emulation.setEmulatedMedia', {media:'print'});
      sameBox(await geometry(tab, `${draw} .ws-fig-answer`), await geometry(tab, `${draw} > img:not(.ws-fig-answer)`), '인쇄 정답층');
    }, metrics(390,844));
  });
  await test('아주 작은 그림과 SVG 속성·인라인 최대 폭도 스크롤 틀 기준으로 확대', async () => {
    const html = await fs.readFile(fileURLToPath(studentInteractive), 'utf8');
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 82"><circle cx="42" cy="41" r="30"/></svg>';
    const src = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
    const cases = [
      ['small', `<img src="${src}" style="width:18mm" alt="작은 그림">`],
      ['max-width', `<img src="${src}" style="width:82mm;max-width:60mm" alt="그림">`],
      ['svg', '<svg width="82mm" height="80.05mm" style="max-width:60mm" viewBox="0 0 84 82"><circle cx="42" cy="41" r="30"/></svg>'],
    ];
    for (const [name, picture] of cases) {
      const figure = `<div class="ws-split" data-ratio="1:1"><div class="ws-split-col"><figure class="ws-fig" data-pen="1" data-slot="q4.1" data-check-kind="draw">${picture}</figure></div><div class="ws-split-col">설명</div></div>`;
      const source = html.replace(/<figure class="ws-fig" data-pen="1" data-slot="q4\.1"[\s\S]*?<\/figure>/, figure);
      assert.notEqual(source, html);
      await usingTab(await saveFile(`narrow-${name}`, source), async tab => {
        const draw = slot('q4.1'), canvas = `${draw} canvas.ws-pen`;
        const size = selector => tab.js(`(() => {const r=document.querySelector(${quote(selector)}).getBoundingClientRect();return [r.width,r.height];})()`);
        await tab.js('sheet.clearAnswers()');
        const normal = await size(canvas);
        await tab.click(action('pen-zoom', draw));
        const large = await size(canvas);
        const viewport = await tab.js(`document.querySelector(${quote(`${draw} .ws-pen-viewport`)}).clientWidth`);
        assert(large[0] > normal[0] * 1.3 && Math.abs(large[0]/viewport-2) < .02, quote({name,normal,large,viewport}));
        assert(Math.abs((large[0]/large[1]) / (normal[0]/normal[1]) - 1) <= .015, quote({name,normal,large}));
        assert.equal((await tab.report()).overflow_x, 0, name);
        const saved = await tab.js(`(() => {const d=new DOMParser().parseFromString(sheet.serialize(),'text/html');
          const figure=d.querySelector(${quote(`#sheet-src ${draw}`)}); return [!!figure.querySelector('.ws-pen-viewport'),figure.querySelector('img,svg').getAttribute('style')];})()`);
        assert.equal(saved[0], false);
        assert.match(saved[1], name === 'small' ? /18mm/ : /60mm/);
        await tab.click(action('pen-zoom', draw));
        const restored = await size(canvas);
        assert(Math.abs(restored[0]-normal[0]) < .1 && Math.abs(restored[1]-normal[1]) < .1, quote({name,normal,restored}));
      }, metrics(390,844));
    }
  });
  await test('펜 확대 경고의 1.3배 경계', async () => {
    const script = `
import sys
sys.path.insert(0, sys.argv[1])
from sheet_check import _audit_pen_zoom
normal = {name: {'width_px': 100, 'height_px': 50} for name in ('q.1', 'q.2', 'q.3')}
large = {name: {'width_px': width, 'height_px': 100} for name, width in zip(normal, (129, 130, 130.01))}
problems = []
_audit_pen_zoom(normal, large, problems)
assert len(problems) == 2 and all(p['code'] == 'pen-zoom-ineffective' for p in problems)
assert 'q.1' in problems[0]['msg'] and '100.00×50.00' in problems[0]['msg'] and '129.00×100.00' in problems[0]['msg']
assert 'q.2' in problems[1]['msg']
`;
    await run(process.env.PYTHON || 'python', ['-B', '-X', 'utf8', '-c', script, path.join(here, '../../scripts')]);
  });
  await test('경계에 잘려 보이는 원도 한 번에 지우고 되돌리며 다시 열 수 있음', async () => {
    const draw = slot('q4.1'), canvas = `${draw} canvas`, btn = name => action(name, draw);
    const phone = metrics(390,844);
    const saved = await usingTab(studentInteractive, async tab => {
      try {
        await tab.click(btn('pen-compass'));
        await penGesture(tab, canvas, [40,60], [[110,60]]);
        await penGesture(tab, canvas, [138,60]);
        const stroke = await penState(tab, 'q4.1');
        assert.equal(stroke.length, 1);
        assert(stroke[0].some(([,y]) => y < 0) && stroke[0].some(([,y]) => y > 1));
        await tab.click(btn('eraser'));
        await penGesture(tab, canvas, [208,60]);
        assert.deepEqual(await penState(tab, 'q4.1'), []);
        await tab.click(btn('pen-undo'));
        assert.deepEqual(await penState(tab, 'q4.1'), stroke);
        const html = await tab.js('__sheetCore.serialize(true,true)');
        return { html, stroke };
      } catch (error) { await tab.js('sheet.clearAnswers()'); throw error; }
    }, phone);
    const restored = async tab => {
      const status = `${draw} [aria-live="polite"]`;
      const notice = () => tab.js(`document.querySelector(${quote(status)}).textContent`);
      assert.deepEqual(await penState(tab, 'q4.1'), saved.stroke);
      await eventually(async () => (await notice()).includes('컴퍼스는 다시 벌려 주세요'), '그림을 복원한 상태 줄');
      assert.match(await notice(), /그림을 다시 불러왔어요/);
      assert(await tab.js(`document.querySelector(${quote(btn('pen-undo'))}).disabled`));
      await tab.click(btn('pen-compass'));
      await penGesture(tab, canvas, [138,60]);
      assert.match(await notice(), /먼저 끌어서/);
      assert.deepEqual(await penState(tab, 'q4.1'), saved.stroke);
      await tab.js("sheet.view('a4')", true);
      await tab.send('Emulation.setEmulatedMedia', {media:'print'});
      const ink = `${draw} svg.ws-pen-ink`;
      assert.deepEqual(await tab.js(`(() => {const el=document.querySelector(${quote(ink)});
        return [el.checkVisibility(),el.children.length,getComputedStyle(el).overflow];})()`), [true,1,'hidden']);
      await tab.send('Emulation.setEmulatedMedia', {media:''});
      await tab.js("sheet.view('solve')", true);
      assert.doesNotMatch(await notice(), /다시 불러왔어요/);
    };
    await usingTab(studentInteractive, async tab => {
      try { await tab.click(action('resume-answers')); await restored(tab); }
      finally { await tab.js('sheet.clearAnswers()'); }
    }, phone);
    await usingTab(await saveFile('restored-circle', saved.html), restored, phone);
  });
  await test('펜 되돌리기는 자리별로 지우개와 모두 지우기도 복원', () => usingTab(studentInteractive, async tab => {
    const draw = slot('q1.5'), canvas = `${draw} canvas`, btn = name => action(name, draw);
    const disabled = () => tab.js(`document.querySelector(${quote(btn('pen-undo'))}).disabled`);
    try {
      assert.equal(await disabled(), true);
      await penGesture(tab, canvas, [20,13], [[55,13]]);
      const freehand = await penState(tab);
      await tab.click(btn('pen-line'));
      await penGesture(tab, canvas, [90,21], [[124,21]]);
      const both = await penState(tab);
      await tab.click(btn('eraser'));
      await penGesture(tab, canvas, [23,13]);
      assert.deepEqual(await penState(tab), both.slice(1));
      await tab.click(btn('pen-clear'));
      assert.deepEqual(await penState(tab), []);
      for (const expected of [both.slice(1), both, freehand, []]) {
        await tab.click(btn('pen-undo'));
        assert.deepEqual(await penState(tab), expected);
      }
      assert.equal(await disabled(), true);
      assert.equal(await tab.js(`document.querySelector(${quote(action('pen-undo', slot('q4.1')))}).disabled`), true);
    } finally { await tab.js('sheet.clearAnswers()'); }
  }, metrics(390,844)));
  await test('화면 이동으로 그리기 틀 위를 밀면 잉크 없이 스크롤', () => usingTab(studentInteractive, async tab => {
    const draw = slot('q1.5'), canvas = `${draw} canvas.ws-pen`;
    try {
      await tab.send('Emulation.setTouchEmulationEnabled', {enabled:true});
      await tab.click(action('pen-pan', draw));
      const [x,y,before,passThrough] = await tab.js(`(() => {
        const c = document.querySelector(${quote(canvas)}); c.scrollIntoView({block:'center'});
        const r=c.getBoundingClientRect(), x=r.left+24, y=r.top+r.height/2;
        return [x,y,scrollY,document.elementFromPoint(x,y)!==c];
      })()`);
      assert(passThrough);
      for (const [type, dy] of [['touchStart',0],['touchMove',-25],['touchMove',-85],['touchMove',-150]]) {
        await tab.send('Input.dispatchTouchEvent', {type, touchPoints:[{x,y:y+dy}]});
      }
      await eventually(() => tab.js(`scrollY > ${before}+30`), '그리기 틀에서 시작한 화면 이동');
      await tab.send('Input.dispatchTouchEvent', {type:'touchEnd', touchPoints:[]});
      assert.deepEqual(await penState(tab), []);
      const plot = slot('q3.1');
      await tab.click(action('pen-pan', plot));
      await tab.js(`{ const svg=document.querySelector(${quote(`${plot} svg[data-map]`)}), r=svg.getBoundingClientRect();
        svg.dispatchEvent(new MouseEvent('click',{bubbles:true, clientX:r.left+r.width/2, clientY:r.top+r.height/2})); }`);
      assert.equal((await valuesOf(tab))['q3.1'], undefined, '화면 이동 중 점도 찍지 않음');
      await tab.click(action('pen', draw));
      assert.equal(await tab.js(`document.querySelector(${quote(action('pen-pan', draw))}).getAttribute('aria-pressed')`), 'false');
      assert.equal(await tab.js(`getComputedStyle(document.querySelector(${quote(canvas)})).pointerEvents`), 'auto');
    } finally { await tab.js('sheet.clearAnswers()'); }
  }, metrics(390,844)));
  await test('크게 펼친 모눈은 두 방향으로 그림 안에서만 밀고 저장 시 밑그림을 보존', () => usingTab(studentInteractive, async tab => {
    const grid = slot('q3.2'), viewport = `${grid} .ws-pen-viewport`;
    try {
      await tab.send('Emulation.setTouchEmulationEnabled', {enabled:true});
      await tab.click(action('pen-zoom', grid));
      await tab.click(action('pen-pan', grid));
      const [left, top, pageScroll] = await tab.js(`(() => {
        const box = document.querySelector(${quote(viewport)}); box.scrollIntoView({block:'center'});
        const r = box.getBoundingClientRect(); return [r.left,r.top,scrollY];
      })()`);
      for (const [type, dx, dy] of [['touchStart',0,0],['touchMove',-48,-48],['touchMove',-100,-120]]) {
        await tab.send('Input.dispatchTouchEvent', {type, touchPoints:[{x:left+180+dx,y:top+230+dy}]});
      }
      await tab.send('Input.dispatchTouchEvent', {type:'touchEnd', touchPoints:[]});
      const [dx, dy, after] = await tab.js(`(() => {const box=document.querySelector(${quote(viewport)});
        return [box.scrollLeft,box.scrollTop,scrollY];})()`);
      assert(dx > 60 && dy > 60, quote({dx,dy}));
      assert.equal(after, pageScroll, '크게 펼친 그림 밖의 페이지는 움직이지 않음');
      assert.equal((await tab.report()).overflow_x, 0);
      assert.equal((await valuesOf(tab))['q3.2'], undefined);
      const content = await tab.js(`new DOMParser().parseFromString(sheet.serialize(),'text/html')
        .querySelector(${quote(`#sheet-src ${grid} > svg[data-map]`)})?.getAttribute('viewBox')`);
      assert.equal(content, '0 0 57 57', '크게 보기로 저장해도 원래 SVG를 보존');
    } finally { await tab.js('sheet.clearAnswers()'); }
  }, metrics(390,844)));
  await test('늘어난 그리기 칸과 2단·좌우 나눔에서 A4 틀 비율 유지', async () => {
    for (const columns of [1,2]) {
      const folder = path.join(work, `draw-columns-${columns}`), title = `그리기 틀 ${columns}단`;
      const box = {type:'answer', rows:[{label:'작도', kind:'draw', height_mm:85, grow:true,
        answer:{width_mm:12, svg:'<svg viewBox="0 0 30 20" aria-label="예시 그림"><path d="M2 2H28L15 18Z"/></svg>'}}]};
      const split = {type:'split', ratio:'1:2', left:[box], right:[{type:'text',text:'다른 칸'}]};
      const source = {version:1, meta:{title, kind:'수업', columns, sync:false}, items:[
        {id:'draw', blocks:[{type:'question',text:'도형을 그려 봅시다.'}, columns === 1 ? split : box]},
      ]};
      await fs.mkdir(folder);
      await fs.writeFile(path.join(folder, '구성.json'), quote(source));
      await run(process.env.PYTHON || 'python', ['-B','-X','utf8',path.join(here,'../../scripts/sheet.py'),'build',folder],
        {env:{...process.env,SHEET_TEST_SALT:salt,SHEET_FONT_DIR:work}});
      const s = slot('draw.1');
      await usingTab(pathToFileURL(path.join(folder, `${title}.html`)).href, async tab => {
        await tab.js("sheet.view('a4')", true);
        const size = which => tab.js(`(() => {
          const box=document.querySelector(${quote(s)}), layer=box.querySelector(${quote(which)});
          const r=layer.getBoundingClientRect(), t=box.getBoundingClientRect();
          return [r.width/r.height,r.height,t.bottom-r.bottom];
        })()`);
        const paper = await size('canvas.ws-pen');
        assert(Math.abs(paper[1]*25.4/96 - 85) <= .4, quote(paper));
        assert(paper[2] > 10, '늘어난 높이는 틀 아래 여백');
        await tab.send('Emulation.setEmulatedMedia', {media:'print'});
        const print = await size('svg.ws-pen-ink');
        assert(Math.abs(print[0]/paper[0] - 1) <= .015, quote({paper, print}));
        await tab.send('Emulation.setEmulatedMedia', {media:''});
        for (const [width,height] of [[390,844],[1024,768]]) {
          await tab.send('Emulation.setDeviceMetricsOverride', metrics(width,height));
          await tab.js("sheet.view('solve')", true);
          const solve = await size('canvas.ws-pen');
          assert(Math.abs(solve[0]/print[0]-1) <= .015, quote({columns,width,print,solve}));
          if (width === 390) {
            const [rows,gap,after] = await tab.js(`(() => {
              const box=document.querySelector(${quote(s)}), frame=box.querySelector('.ws-draw-frame');
              const tools=box.querySelector('.ws-pen-tools'), t=tools.getBoundingClientRect();
              return [new Set([...tools.children].map(b=>b.offsetTop)).size,
                t.top-frame.getBoundingClientRect().bottom,box.getBoundingClientRect().bottom-t.bottom];
            })()`);
            assert(rows <= 2 && gap >= 0 && after < 5, quote({rows,gap,after}));
            assert.equal((await tab.report()).overflow_x, 0);
          }
        }
      });
      await usingTab(pathToFileURL(path.join(folder, `${title} (교사용).html`)).href, async tab => {
        const answer = () => tab.js(`document.querySelector(${quote(`${s} .ws-key img`)}).checkVisibility()`);
        assert(await answer());
        await tab.click(action('edit'));
        await tab.click(action('taller', s));
        assert.equal(await tab.js(`document.querySelector(${quote(s)}).dataset.heightMm`), '90');
        await tab.click(action('preview-student'));
        assert.equal(await answer(), false);
        await tab.js("sheet.view('a4')", true);
        assert(Math.abs(await tab.js(`document.querySelector(${quote(`${s} canvas`)}).getBoundingClientRect().height*25.4/96`) - 90) <= .4);
        await tab.click(action('return-teacher'));
        assert(await answer());
        assert.equal(await tab.js(`document.querySelector(${quote(action('pen', s))}).checkVisibility()`), false);
      });
    }
  });
  await test('펜을 붙인 그림·모눈·수직선의 비율은 화면과 인쇄에서 그대로', () => usingTab(studentInteractive, async tab => {
    const ids = ['q4.1','q3.2','q3.1'];
    const ratios = selector => tab.js(`${quote(ids)}.map(id => {
      const el=document.querySelector('[data-slot="'+id+'"]'), r=el.querySelector(${quote(selector)}).getBoundingClientRect();
      return r.width/r.height;
    })`);
    const phone = await ratios('canvas.ws-pen');
    await tab.send('Emulation.setDeviceMetricsOverride', metrics(1024,768));
    await tab.js("sheet.view('solve')",true);
    const tablet = await ratios('canvas.ws-pen');
    await tab.js("sheet.view('a4')",true);
    await tab.send('Emulation.setEmulatedMedia', {media:'print'});
    const print = await ratios('svg.ws-pen-ink');
    for (const [i,expected] of [72/32,1,120/16].entries()) {
      for (const measured of [phone[i],tablet[i],print[i]]) assert(Math.abs(measured/expected-1) <= .015, quote({i,measured,expected}));
    }
  }, metrics(390,844)));
  await test('겸용 칸의 저장 실패 뒤에도 본답·그림·시각을 함께 복원', async () => {
    const key = 'pbj-sheet:interactive-fixture:student', plot = slot('q3.1');
    const old = { 'q3.1':[0] }, oldPen = { strokes: [[[.2,.5],[.4,.6]]] };
    await usingTab(studentInteractive, tab => tab.js(`{
      sheet.clearAnswers();
      localStorage.setItem(${quote(key)}, ${quote(quote(old))});
      localStorage.setItem(${quote(key+':pen')}, ${quote(quote({'q3.1':oldPen}))});
      localStorage.setItem(${quote(key+':saved-at')}, String(+new Date(2024,10,27,9,10)));
    }`));
    const picture = tab => tab.js(`(() => {
      const d=new DOMParser().parseFromString(__sheetCore.serialize(true,true),'text/html');
      return JSON.parse(d.querySelector(${quote(plot)}).dataset.penValue);
    })()`);
    await usingTab(studentInteractive, async tab => {
      await tab.click(action('resume-answers'));
      assert.deepEqual(await valuesOf(tab), old);
      assert.deepEqual(await picture(tab), oldPen);
      await tab.js(`{
        const write = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          // 본답만 든 작은 쓰기는 허용하고, 겸용 펜을 포함한 쓰기는 거부한다.
          if (value.includes('"strokes"')) throw new DOMException('가득 참','QuotaExceededError');
          return write.call(this,key,value);
        };
      }`);
      await tab.click(action('pen', plot));
      await tab.mouseClick(`${plot} canvas`);
      await tab.js(`sheet.answer('q3.1',[1])`);
      assert.deepEqual(await valuesOf(tab), {'q3.1':[1]});
      assert.equal((await picture(tab)).strokes.length, 2);
      assert.match(await tab.js('document.querySelector(".ws-save-status").textContent'), /저장되지 않았어요/);
    });
    await usingTab(studentInteractive, async tab => {
      try {
        assert.match(await tab.js('document.querySelector(".ws-restore-dialog").textContent'), /11월 27일 09:10/);
        await tab.click(action('resume-answers'));
        assert.deepEqual(await valuesOf(tab), old);
        assert.deepEqual(await picture(tab), oldPen);
        await tab.click(action('pen', plot));
        await tab.mouseClick(`${plot} canvas`);
        await tab.js(`sheet.answer('q3.1',[1])`);
      } catch (error) { await tab.js('sheet.clearAnswers()'); throw error; }
    });
    await usingTab(studentInteractive, async tab => {
      try {
        await tab.click(action('resume-answers'));
        assert.deepEqual(await valuesOf(tab), {'q3.1':[1]});
        assert.equal((await picture(tab)).strokes.length, 2);
      } finally { await tab.js('sheet.clearAnswers()'); }
    });
  });
  await test('저장 용량이 차면 경고하고 다음 저장에 성공하면 시각 갱신', () => usingTab(studentInteractive, async tab => {
    const key = 'pbj-sheet:interactive-fixture:student', status = 'document.querySelector(".ws-save-status")';
    const saved = () => tab.js(`JSON.parse(localStorage.getItem(${quote(key)}))`);
    const input = value => setInput(tab, `${slot('q1.1')} input`, value);
    try {
      await tab.js(`{ const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          if (key === window.failKey) throw new DOMException('가득 참','QuotaExceededError');
          return original.call(this, key, value);
        };
        window.testTime=3000; Date.now=()=>window.testTime; }`);
      await input('1');
      const first = await saved();
      assert.equal(first.savedAt, 3000);
      await tab.js(`window.failKey=${quote(key)}; window.testTime=4000;`);
      await input('2');
      assert.equal((await valuesOf(tab))['q1.1'], '2');
      assert.deepEqual(await saved(), first);
      assert.equal(await tab.js(`${status}.getAttribute('aria-live')`), 'polite');
      assert.match(await tab.js(`${status}.textContent`), /이 기기에 저장되지 않았어요.*답을 넣어 저장/);
      await tab.js("sheet.view('a4')",true);
      assert.equal(await tab.js(`${status}.checkVisibility()`), true);
      await tab.send('Emulation.setEmulatedMedia', {media:'print'});
      assert.equal(await tab.js(`${status}.checkVisibility()`), false);
      await tab.send('Emulation.setEmulatedMedia', {media:''});
      await tab.js("sheet.view('solve')",true);
      await tab.js('window.failKey=null; window.testTime=6000;');
      await input('3');
      assert.equal((await saved()).savedAt, 6000);
      assert.equal(await tab.js(`${status}.textContent`), '');
    } finally { await tab.send('Emulation.setEmulatedMedia', {media:''}); await tab.js('sheet.clearAnswers()'); }
  }));
  await test('공개 정책 네 가지·힌트 단계·서술형 예시 답안과 점검표', async () => {
    for (const policy of ['시도 후','바로','교사만','없음']) {
      await usingTab(policy === '시도 후' ? interactive : await fixture('interactive',policy), async tab => {
        await tab.click(action('preview-student'));
        const q1 = item('q1');
        const status = a => tab.js(`document.querySelector(${quote(action(a, q1))})?.disabled`);
        const permitted = policy === '시도 후' || policy === '바로';
        assert.equal(await status('reveal-answer'), permitted ? policy !== '바로' : undefined);
        assert.equal(await status('reveal-solution'), permitted ? policy !== '바로' : undefined);
        assert.equal(await status('check'), policy === '없음' ? undefined : false);
        assert.equal(await tab.js(`sheet.answer('q1.1','1/2')`), policy === '없음' ? null : true);
        if (policy === '시도 후') {
          // Blank confirmation must not count, including for the record event.
          await tab.js(`sheet.clearAnswers(); window.checks = 0; document.addEventListener('sheet:check', () => checks++)`);
          await setInput(tab, `${slot('q1.1')} input`, '  ');
          await tab.click(action('check',q1));
          assert.equal(await status('reveal-answer'), true);
          assert.equal(await status('reveal-solution'), true);
          assert.match(await tab.js(`document.querySelector('${q1} .ws-item-score').textContent`), /먼저 답을 써 보세요/);
          assert.equal(await tab.js('checks'), 0);
          await tab.js(`sheet.answer('q1.1','1/2')`);
          assert.equal(await status('reveal-answer'), true, 'API 판별은 확인 단추를 누른 시도가 아님');
        }
        if (policy !== '없음') await tab.click(action('check',q1));
        if (permitted) {
          assert.equal(await status('reveal-answer'), false);
          assert.equal(await status('compare'), true);
          const revealedCount = () => tab.js(`document.querySelectorAll('${q1} .ws-reveal:not([hidden])').length`);
          await tab.click(action('reveal-answer',q1));
          assert.equal(await revealedCount(), policy === '시도 후' ? 1 : 3);
          assert.equal(await status('reveal-solution'), policy === '시도 후');
          await setInput(tab, `${slot('q1.4')} textarea`, '!?');
          assert.equal(await status('compare'), true);
          await setInput(tab, `${slot('q1.4')} textarea`, '원점');
          assert.equal(await status('compare'), true, '서술만 쓰면 나머지 칸의 답을 짐작할 수 있음');
          if (policy === '시도 후') assert.equal(await status('reveal-solution'), true);
          await tab.js(`sheet.answer('q1.2','서로 수직'); sheet.answer('q1.3','2')`);
          assert.equal(await status('compare'), false, '정답 여부가 아니라 모든 응답 칸을 시도했는지 봄');
          await tab.click(action('compare',q1));
          assert.equal(await tab.js(`document.querySelector('${q1} .ws-compare input').checkVisibility()`), true);
          assert.equal(await tab.js(`document.querySelector('${q1} .ws-compare math')?.textContent`), '2');
          await setInput(tab, `${slot('q1.3')} input`, '');
          assert.equal(await status('compare'), true);
          assert.equal(await tab.js(`document.querySelector('${q1} .ws-compare').hidden`), true);
          if (policy === '시도 후') assert.equal(await status('reveal-solution'), true);
          await tab.js(`sheet.answer('q1.3','1')`);
          if (policy === '시도 후') {
            await tab.js(`sheet.answer('q1.2','서로 평행')`);
            assert.equal(await status('reveal-solution'), false, '펜 칸은 채우지 않아도 됨');
          }
          await tab.click(action('reveal-solution',q1));
          assert.match(await tab.js(`document.querySelector('${q1} .ws-solution:not(.ws-compare)').textContent`), /풀이 표식/);
          assert.equal(await revealedCount(), 4);
          if (policy === '시도 후') {
            await setInput(tab, `${slot('q1.2')} input`, '');
            assert.equal(await status('reveal-solution'), true);
            assert.equal(await tab.js(`document.querySelector('${q1} .ws-solution:not(.ws-compare)').hidden`), true);
            assert.equal(await revealedCount(), 2, '지운 칸 정답과 서술 예시를 함께 숨김');
          }
        } else assert.equal(await tab.js('!!document.querySelector("#sheet-reveal,.ws-reveal,.ws-solution")'), false);
        const shownHints = () => tab.js(`document.querySelectorAll('${q1} .ws-hint:not([hidden])').length`);
        assert.equal(await shownHints(), 0);
        await tab.click(action('hint',q1)); assert.equal(await shownHints(), 1);
        await tab.click(action('hint',q1)); assert.equal(await shownHints(), 2);
        await tab.js('sheet.clearAnswers()'); assert.equal(await shownHints(), 0);
      });
    }
  });
  await test('자동 판별 자리가 없으면 학생 도구 막대에 점수를 표시하지 않음', async () => {
    const source = await fs.readFile(fileURLToPath(studentInteractive), 'utf8');
    const withoutAuto = source.replaceAll(/ data-check-hash="[^"]+"/g, '')
      .replaceAll(/data-check-kind="(?:number|text|choice|ox|order|match|points|line|bars)"/g, 'data-check-kind="open"');
    for (const [name, html] of [['reveal-none', source.replace('data-reveal="시도 후"','data-reveal="없음"')],
      ['all-open', withoutAuto]]) {
      await usingTab(await saveFile(`no-score-${name}`, html), async tab => {
        assert.deepEqual(await tab.js('sheet.score()'), {checked:0,correct:0,total:0});
        assert.doesNotMatch(await tab.js('document.querySelector("#sheet-toolbar").innerText'), /맞힌 수|자동 판별/);
        assert.equal(await tab.js('!!document.querySelector(".ws-score")?.checkVisibility()'), false);
      });
    }
  });
  await test('폰 자료표 안내는 표가 넘칠 때만 보이고 주어진 값 칸의 폭을 보장', () => usingTab(studentInteractive, async tab => {
    const input = `${slot('q1.2')} input`;
    const geometry = () => tab.js(`(() => {
      const table = document.querySelector('#sheet-flow .ws-split .ws-data');
      const cell = table.tBodies[0].rows[0].cells[0];
      return { overflow:table.scrollWidth-table.clientWidth, cue:getComputedStyle(table,'::after').content,
        givenWidth:cell.getBoundingClientRect().width }; })()`);
    const expectCue = async (shown) => {
      const r = await geometry();
      assert.equal(r.overflow > 1, shown, quote(r));
      assert.equal(r.cue.includes('좌우'), shown, quote(r));
      assert.equal((await tab.report()).overflow_x, 0);
    };
    await expectCue(false);
    await tab.js(`document.querySelector('#sheet-flow .ws-split .ws-data tbody td').innerHTML = '<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>';`);
    assert((await geometry()).givenWidth >= 32, '주어진 분수가 독립해서 읽힐 열 폭');
    await setInput(tab, input, '1'.repeat(46));
    await expectCue(true);
    await tab.send('Emulation.setDeviceMetricsOverride', metrics(680,844,true));
    await eventually(async () => !(await geometry()).cue.includes('좌우'), '폭 확대 후 안내 숨김');
    await expectCue(false);
    await tab.send('Emulation.setDeviceMetricsOverride', metrics(390,844,true));
    await eventually(async () => (await geometry()).cue.includes('좌우'), '폭 축소 후 안내 표시');
    await expectCue(true);
    await setInput(tab, input, '');
    await expectCue(false);
    await tab.js('sheet.clearAnswers()');
  }, metrics(390,844,true)));
  await test('폰 입력 표는 폭을 채우고 넘치는 표만 스크롤', async () => {
    const folder = path.join(work, 'responsive-input-tables'), title = '표 입력 폭 시험';
    const source = {version:1, meta:{title, kind:'수업', sync:false}, items:[
      {id:'compact', blocks:[{type:'table', header:['사건','경우의 수','확률'],
        rows:[['합 $6$', '{{|$5$}}', '{{|$\\frac{5}{36}$}}']]}]},
      {id:'wide', blocks:[{type:'table', header:['눈', ...[1,2,3,4,5,6].map(String)],
        rows:Array.from({length:6}, (_,i) => [String(i+1), ...Array.from({length:6}, (_,j) => `{{|$${i+j+2}$}}`)])}]},
    ]};
    await fs.mkdir(folder);
    await fs.writeFile(path.join(folder, '구성.json'), quote(source));
    await run(process.env.PYTHON || 'python', ['-B','-X','utf8',path.join(here,'../../scripts/sheet.py'),'build',folder],
      {env:{...process.env,SHEET_TEST_SALT:salt,SHEET_FONT_DIR:work}});
    await usingTab(pathToFileURL(path.join(folder, `${title}.html`)).href, async tab => {
      const geometry = id => tab.js(`(() => {
        const table=document.querySelector(${quote(`#sheet-flow ${item(id)} table.ws-data`)}), parent=table.parentElement;
        const style=getComputedStyle(parent), rect=table.getBoundingClientRect();
        return {width:rect.width, available:parent.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),
          inputs:[...table.querySelectorAll('.ws-in')].map(el=>el.getBoundingClientRect().width),
          client:table.clientWidth, scroll:table.scrollWidth, overflow:table.classList.contains('ws-table-overflow'),
          display:getComputedStyle(table).display, cue:getComputedStyle(table,'::after').content};
      })()`);
      const compact = await geometry('compact');
      assert.equal(compact.inputs.length, 2);
      assert(compact.inputs.every(width => width >= 70), quote(compact));
      assert(Math.abs(compact.width-compact.available) <= 2, quote(compact));
      assert.equal(compact.overflow, false, quote(compact));
      assert.equal(compact.display, 'table');
      assert(!compact.cue.includes('좌우'));
      // 뷰포트는 그대로 두고 부모 카드만 줄였다 늘려도 다시 측정한다.
      await tab.js(`document.querySelector('#sheet-flow ${item('compact')}').style.width='150px'`);
      await eventually(async () => (await geometry('compact')).overflow, '부모 카드 축소 후 넘침 표시');
      await tab.js(`{ const table=document.querySelector('#sheet-flow ${item('compact')} table');
        window.tableTransitions=0; window.tableObserver=new MutationObserver(() => tableTransitions++);
        tableObserver.observe(table,{attributes:true,attributeFilter:['class']}); }`);
      await tab.js(`new Promise(resolve=>{let frames=0;const next=()=>++frames===12?resolve():requestAnimationFrame(next);requestAnimationFrame(next);})`, true);
      assert.equal(await tab.js('tableTransitions'), 0, '넘침 상태가 프레임마다 뒤집히지 않음');
      assert((await geometry('compact')).overflow);
      const unstable = await tab.js(`(async()=>{
        const card=document.querySelector('#sheet-flow ${item('compact')}');
        const frames=n=>new Promise(resolve=>{const next=()=>--n?requestAnimationFrame(next):resolve();requestAnimationFrame(next);});
        const unstable=[], states=new Set(), table=card.querySelector('table');
        for(let width=170;width<=225;width++) {
          card.style.width=width+'px'; await frames(4); const before=tableTransitions; await frames(5);
          states.add(table.classList.contains('ws-table-overflow'));
          if(tableTransitions!==before) unstable.push(width);
        }
        return {unstable,states:[...states].sort()};
      })()`, true);
      assert.deepEqual(unstable, {unstable:[],states:[false,true]}, '경계 폭에서 표 상태가 반복 전환되지 않음');
      await tab.js(`tableObserver.disconnect(); document.querySelector('#sheet-flow ${item('compact')}').style.width=''`);
      await eventually(async () => !(await geometry('compact')).overflow, '부모 카드 확대 후 넘침 해제');
      const wide = await geometry('wide');
      assert.equal(wide.inputs.length, 36);
      await eventually(async () => (await geometry('wide')).overflow, '넓은 입력 표 넘침 표시');
      const overflow = await geometry('wide');
      assert(overflow.scroll > overflow.client+1 && overflow.cue.includes('좌우'), quote(overflow));
      assert.equal((await tab.report()).overflow_x, 0);
      assert(await tab.js(`(() => {const t=document.querySelector('#sheet-flow ${item('wide')} table');t.scrollLeft=t.scrollWidth;return t.scrollLeft>0;})()`));
      await tab.send('Emulation.setDeviceMetricsOverride', metrics(1024,768));
      await eventually(async () => !(await geometry('wide')).overflow, '화면 확대 후 넘침 해제');
      const expanded = await geometry('wide');
      assert.equal(expanded.display, 'table', quote(expanded));
      assert(!expanded.cue.includes('좌우'));
      await tab.js("sheet.view('a4')", true);
      assert.equal((await tab.report()).print_leaks, 0);
      assert.equal(await tab.js(`getComputedStyle(document.querySelector('#sheet-pages ${item('wide')} table')).display`), 'table');
      await tab.js('sheet.clearAnswers()');
    }, metrics(390,844,true));
  });
  const contractFolder = path.join(work, 'contract-worksheet');
  const contractTitle = '판별과 인쇄 시험';
  const contractStudent = pathToFileURL(path.join(contractFolder, `${contractTitle}.html`)).href;
  const explore = { type:'explore', fn:'a*x', x:[-2,2], y:[-4,4], unit_mm:5, table:[-1,0,1],
    params:[{name:'a', min:-2, max:2, step:0.5, value:0.5, except:[-2,0,0.5+0.5]}] };
  await test('조립본 단위 해시·등식 대칭·확인 필요 판별', async () => {
    const source = { version:1, meta:{title:contractTitle, kind:'수업', theme:'산뜻', sync:false}, items:[
      {id:'check', blocks:[{type:'answer', rows:[{label:'시간', answer:'$2$분'}, {label:'등식', answer:'$y=2x$', check:'text'}]}]},
      {id:'units', blocks:[{type:'answer', rows:[{label:'각도', answer:'$30^\\circ$'}, {label:'길이', answer:'$5$'},
        {label:'다른 정답', answer:'$1$시간', accept:['$60$분', '$1$']},
        {label:'단위 있는 대안', answer:'$2$', accept:['$3$분']}]}]},
      {id:'explore', break:true, blocks:[{...explore, print:[{a:2},{a:-1}]}, { ...explore, unit_mm:3, params: [{ ...explore.params[0], label:'$a$의 값' }] }]},
      {id:'screen-check', break:true, blocks:[{type:'split', left:[{type:'checklist', title:'화면전용 점검', items:['하나']}],
        right:[{type:'checklist', items:['둘'], print:false}]}]},
      {id:'paper-check', blocks:[{type:'checklist', title:'인쇄허용 점검', items:['확인했어요.'], print:true},
        {type:'order', items:['처음','중간','끝']}]}
    ]};
    await fs.mkdir(contractFolder);
    await fs.writeFile(path.join(contractFolder, '구성.json'), quote(source));
    await run(process.env.PYTHON || 'python', ['-B', '-X', 'utf8', path.join(here, '../../scripts/sheet.py'), 'build', contractFolder],
      { env:{...process.env, SHEET_TEST_SALT:salt, SHEET_FONT_DIR:work} });
    await usingTab(contractStudent, async tab => {
      const answer = (id, value) => tab.js(`sheet.answer(${quote(id)}, ${quote(value)})`);
      for (const [id, value, expected] of [
        ['check.1','2',true], ['check.1','2분',true], ['check.1','2시간',false], ['check.1','3분',false],
        ['check.2','2x=y',true], ['units.1','30도',true], ['units.1','30°',true], ['units.1','30분',false],
        ['units.2','5cm',true], ['units.3','60분',true], ['units.3','60시간',false], ['units.3','1',true],
        ['units.4','2',true], ['units.4','3분',true], ['units.4','3시간',false]
      ]) assert.equal(await answer(id, value), expected, `${id}: ${value}`);
      assert.deepEqual(await tab.js(`(() => { const el=document.querySelector('${slot('check.1')}'); return [el.dataset.checkUnit,el.dataset.checkHash.split(',')]; })()`),
        ['1', [hash(salt,'number','2'), hash(salt,'number','2@분')]]);
      await tab.js('sheet.clearAnswers()');
      await setInput(tab, `${slot('check.1')} textarea`, '2분');
      await setInput(tab, `${slot('check.2')} textarea`, 'y=3x');
      await tab.click(action('check', item('check')));
      assert.deepEqual(await tab.js(`Object.values(__sheetCore.verdicts()).slice(0,2)`), [true,'review']);
      assert.match(await tab.js(`document.querySelector('${slot('check.2')} .ws-verdict.ws-review').textContent`), /예상한 답과 달라요.*선생님께 확인받아요/);
      assert.equal(await tab.js(`document.querySelector('${item('check')} .ws-item-score').textContent`), '1/2 맞음 · 확인 필요 1');
      assert.deepEqual(await tab.js('sheet.score()'), {checked:2, correct:1, total:7});
      await setInput(tab, `${slot('check.2')} textarea`, '');
      assert.equal(await tab.js(`__sheetCore.verdicts()['check.2']`), null);
      assert.equal(await tab.js(`document.querySelector('${slot('check.2')} .ws-verdict').checkVisibility()`), false);
      await tab.js('sheet.clearAnswers()');
    });
  });
  await test('히스토그램 연속 경계·키보드·전체 입력 후 공개·표 단위', async () => {
    const folder = path.join(work, 'histogram-worksheet'), title = '도수 그래프 시험';
    const source = {version:1, meta:{title, kind:'수업', sync:false}, items:[
      {id:'table', blocks:[{type:'table', header:['계급','도수 (일)','소수'], rows:[['15 이상 20 미만','{{|$2$}}','{{|$2.5$}}']]},
        {type:'question', number:false, text:'답은 {{|$11$|24}}일, 길이는 {{|$5$}}cm이다.'},
        {type:'question', number:false, text:'{{|$-1$}}를 더한다.'}]},
      {id:'plot', blocks:[{type:'chart', kind:'histogram', bins:[15,20,25,30,35,40], values:[2,4,6,5,3], y_max:6, y_step:1,
        plot:{answer:[2,4,6,5,3],snap:1}}]},
      {id:'ox', blocks:[{type:'ox',items:[{text:'참이다.',answer:'O'}]}]},
      {id:'flow', blocks:[{type:'flow', nodes:['봉지 수 $x$', '{{㉠|$2$}}를 곱한다', '구슬 수 $y$']}]},
      {id:'open', blocks:[{type:'answer', rows:[{label:'설명',answer:'설명을 씁니다.'}]}]}
    ], teacher:{solutions:[{item:'plot',answer:'전체 도수를 표시했다.'}]}};
    await fs.mkdir(folder);
    await fs.writeFile(path.join(folder, '구성.json'), quote(source));
    await run(process.env.PYTHON || 'python', ['-B','-X','utf8',path.join(here,'../../scripts/sheet.py'),'build',folder],
      {env:{...process.env,SHEET_TEST_SALT:salt,SHEET_FONT_DIR:work}});
    await usingTab(pathToFileURL(path.join(folder, `${title}.html`)).href, async tab => {
      const graph = item('plot'), btn = name => action(name,graph);
      const state = () => tab.js(`${quote(['reveal-answer','reveal-solution'].map(btn))}.map(s=>document.querySelector(s).disabled)`);
      const inputs = await tab.js(`[...document.querySelectorAll('${item('table')} input')].map(el=>[el.ariaLabel, el.getAttribute('inputmode')])`);
      assert.deepEqual(inputs[0], ['15 이상 20 미만, 도수 (일)', 'numeric']);
      assert.equal(inputs[1][1], 'decimal'); assert.equal(inputs[4][1], null);
      for (const [id, value, expected] of [['table.3','11',true],['table.3','11일',true],['table.3','11권',false],
        ['table.1','2권',false],['table.1','2일',true],['table.4','5cm',true],['table.4','5mm',false]]) {
        assert.equal(await tab.js(`sheet.answer(${quote(id)}, ${quote(value)})`),expected, `${id}: ${value}`);
      }
      assert.match(await tab.js('document.querySelector(".ws-score").textContent'), /서술 1칸은 직접 확인/);
      const hit = i => `.ws-bar-hit[data-i="${i}"]`;
      const press = async (i, ...keys) => {
        assert.equal(await tab.js(`{ const hit = document.querySelector(${quote(hit(i))}); hit.focus(); document.activeElement === hit; }`),true);
        for (const key of keys) await tab.key(key);
      };
      await press(0,'ArrowUp','ArrowUp','ArrowUp','ArrowDown');
      assert.equal(await tab.js(`document.querySelector(${quote(hit(0))}).getAttribute('aria-label')`), '15 이상 20 미만, 높이 2');
      await tab.click(btn('check'));
      assert.deepEqual(await state(),[true,true]);
      assert.match(await tab.js(`document.querySelector('${graph} .ws-item-score').textContent`),/높이를 모두/);
      for (let i = 1; i < 5; i++) await tab.mouseClick(hit(i));
      assert.deepEqual(await state(),[false,false]);
      await tab.click(btn('reveal-answer')); await tab.click(btn('reveal-solution'));
      assert.equal(await tab.js(`document.querySelector('${graph} .ws-reveal').checkVisibility()`), true);
      const gap = await tab.js(`(() => { const bars=[...document.querySelectorAll('.ws-user-bar')].map(el=>el.getBoundingClientRect());
        return Math.max(...bars.slice(1).map((el,i)=>Math.abs(el.left-bars[i].right))); })()`);
      assert(gap < .001, `막대 경계 차이 ${gap}px`);
      assert.equal(await tab.js(`[...document.querySelectorAll('.ws-ox-pick > [role=button]')].every(el=>{const r=el.getBoundingClientRect();return r.width>=44&&r.height>=44;})`),true);
      assert.equal((await tab.report()).overflow_x, 0);
      const lines = width => tab.js(`(() => {
        const node = document.querySelector('.ws-flow-node:nth-child(3)');
        node.style.width = ${quote(width)};
        const suffix = node.querySelector('.ws-blank').nextSibling;
        const r = document.createRange(); r.setStart(suffix,0); r.setEnd(suffix,1);
        const first = r.getBoundingClientRect();
        r.setStart(node.lastChild,1); r.setEnd(node.lastChild,4);
        return Math.round(r.getBoundingClientRect().top-first.top);
      })()`);
      assert(await lines('65px') > 0, '좁으면 조사를 빈칸에 붙이고 다음 낱말만 넘긴다');
      await lines('');
      await tab.js(`sheet.view('a4')`,true);
      assert.equal(await lines(''),0, 'A4 흐름 상자가 본래 너비로 한 줄을 유지한다');
      assert.equal(await tab.js(`document.querySelector('${graph} svg .ws-chart-guides').checkVisibility()`),true);
      assert.equal(await tab.js(`document.querySelector('${graph} .ws-explore-note').checkVisibility()`),false);
      await tab.js('sheet.clearAnswers()');
    },metrics(390,844,true));
  });
  await test('채운 휴대폰 표의 정답과 학번이 잘리지 않음', async () => {
    const folder = path.join(work, 'filled-mobile-table');
    const source = JSON.parse(await fs.readFile(path.join(here, '../../examples/대여소-요금-비교/구성.json'), 'utf8'));
    source.meta.sync = false;
    await fs.mkdir(folder);
    await fs.writeFile(path.join(folder, '구성.json'), quote(source));
    await run(process.env.PYTHON || 'python', ['-B','-X','utf8',path.join(here,'../../scripts/sheet.py'),'build',folder],
      {env:{...process.env,SHEET_TEST_SALT:salt,SHEET_FONT_DIR:work}});
    await usingTab(pathToFileURL(path.join(folder, `${source.meta.title}.html`)).href, async tab => {
      const answers = ['5000','7000','9000','11000','4000','7000','10000','13000'];
      await tab.js(`${quote(answers)}.forEach((value,i)=>sheet.answer('q1.'+(i+1),value)); sheet.answer('head.학번','20315')`);
      await tab.click(action('check',item('q1')));
      await tab.click(action('reveal-answer',item('q1')));
      for (const width of [390,360,320]) {
        await tab.send('Emulation.setDeviceMetricsOverride',metrics(width,844,true));
        await tab.js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))',true);
        const result = await tab.js(`(() => {
          const main=document.querySelector('#sheet-flow');
          const clipped=[...main.querySelectorAll('td,th,.ws-flow-node,.ws-in')]
            .filter(el=>el.getClientRects().length && el.scrollWidth>el.clientWidth+1)
            .map(el=>el.dataset.slot || el.closest('[data-slot]')?.dataset.slot || el.textContent.trim());
          const cells=['q1.4','q1.7','q1.8'].map(id=>{
            const input=main.querySelector('[data-slot="'+id+'"] .ws-in');
            const reveal=input.parentElement.parentElement.querySelector('.ws-reveal');
            const r=reveal.getBoundingClientRect(), content=reveal.querySelector('math').getBoundingClientRect();
            return {value:input.value, answer:reveal.textContent.trim(), visible:reveal.checkVisibility(),
              fits:content.left>=r.left-1 && content.right<=r.right+1};
          });
          return {clipped,cells,identity:main.querySelector('[data-slot="head.학번"] input').value};
        })()`);
        assert.equal((await tab.report()).overflow_x,0,`${width}px 문서 가로 넘침`);
        assert.deepEqual(result.clipped,[],`${width}px 채운 표와 학번 잘림`);
        assert.equal(result.identity,'20315');
        assert.deepEqual(result.cells.map(cell=>[cell.value,cell.answer,cell.visible,cell.fits]),
          ['11000','10000','13000'].map(value=>[value,value,true,true]),`${width}px 정답 표시`);
      }
      await tab.js('sheet.clearAnswers()');
    },metrics(390,844,true));
  });
  await test('긴 표 답은 입력 때부터 표 안에서 밀어 모두 읽음', async () => {
    const folder = path.join(work, 'long-mobile-table');
    const source = JSON.parse(await fs.readFile(path.join(here, '../../examples/대여소-요금-비교/구성.json'), 'utf8'));
    const longAnswer = '12345678901234567890';
    source.meta.sync = false;
    source.items[0].blocks.find(block=>block.type === 'table').rows[0][4] = `{{|$${longAnswer}$}}`;
    await fs.mkdir(folder);
    await fs.writeFile(path.join(folder, '구성.json'), quote(source));
    await run(process.env.PYTHON || 'python', ['-B','-X','utf8',path.join(here,'../../scripts/sheet.py'),'build',folder],
      {env:{...process.env,SHEET_TEST_SALT:salt,SHEET_FONT_DIR:work}});
    await usingTab(pathToFileURL(path.join(folder, `${source.meta.title}.html`)).href, async tab => {
      await setInput(tab, '#sheet-flow [data-slot="q1.4"] input', longAnswer);
      const geometry = () => tab.js(`(() => {
        const input=document.querySelector('#sheet-flow [data-slot="q1.4"] .ws-in');
        const table=input.closest('table'), cell=input.closest('td');
        const reveal=cell.querySelector('.ws-reveal:not([hidden])');
        const math=reveal?.querySelector('math');
        return {input:[input.clientWidth,input.scrollWidth], table:[table.clientWidth,table.scrollWidth],
          cell:[cell.clientWidth,cell.scrollWidth], cue:getComputedStyle(table,'::after').content,
          reveal:reveal && [reveal.clientWidth,reveal.scrollWidth], math:math && [math.getBoundingClientRect().width,reveal.getBoundingClientRect().width]};
      })()`);
      let size = await geometry();
      assert(size.input[1] <= size.input[0]+1, `입력할 때 잘림: ${quote(size)}`);
      assert(size.table[1] > size.table[0], `긴 답은 표 안 가로 스크롤: ${quote(size)}`);
      assert.match(size.cue,/좌우/);
      assert.equal((await tab.report()).overflow_x,0);
      // 짧은 오답을 썼더라도 긴 정답 전체가 들어갈 폭이 필요하다.
      await setInput(tab, '#sheet-flow [data-slot="q1.4"] input', '1');
      await tab.click(action('check',item('q1'))); await tab.click(action('reveal-answer',item('q1')));
      size = await geometry();
      assert(size.reveal[1] <= size.reveal[0]+1 && size.math[0] <= size.math[1]+1 && size.cell[1] <= size.cell[0]+1,
        `짧은 오답 뒤 긴 정답 잘림: ${quote(size)}`);
      await setInput(tab, '#sheet-flow [data-slot="q1.4"] input', longAnswer);
      size = await geometry();
      assert(size.input[1] <= size.input[0]+1 && size.cell[1] <= size.cell[0]+1, `공개 뒤 입력·표 칸 잘림: ${quote(size)}`);
      assert(size.reveal[1] <= size.reveal[0]+1 && size.math[0] <= size.math[1]+1, `정답 표시 잘림: ${quote(size)}`);
      assert.equal(await tab.js(`(() => {
        const input=document.querySelector('#sheet-flow [data-slot="q1.4"] .ws-in'), table=input.closest('table');
        table.scrollLeft=table.scrollWidth;
        const a=input.getBoundingClientRect(), b=table.getBoundingClientRect();
        return table.scrollLeft>0 && a.left>=b.left-1 && a.right<=b.right+1;
      })()`),true,'표를 밀면 긴 입력 전체를 볼 수 있음');
      await tab.js(`sheet.view('a4')`,true);
      assert.equal((await tab.report()).print_leaks,0);
      assert.equal(await tab.js(`!!new DOMParser().parseFromString(sheet.serialize(),'text/html').querySelector('.ws-input-sizer,.ws-input-table')`),false);
      await tab.js('sheet.clearAnswers()');
    },metrics(390,844,true));
  });
  await test('탐구 제외값·인쇄 스냅숏·점검표와 순서 안내', () => usingTab(contractStudent, async tab => {
    const exploreItem = item('explore'), first = `${exploreItem} .ws-explore:first-child`;
    const visible = selector => tab.js(`document.querySelector(${quote(selector)}).checkVisibility()`);
    const slider = `${first} .ws-explore-ctrl input`;
    assert.equal(await tab.js(`document.querySelectorAll('${slider}').length`), 1);
    assert.equal(await visible(`${first} .ws-explore-snapshots`), false);
    assert.equal(await visible(`${first} .ws-explore-table`), true);
    assert.deepEqual(await tab.js(`[...document.querySelectorAll('${exploreItem} .ws-explore-ctrl label')].map(x=>[x.querySelector('math mi').textContent,x.querySelector('input').ariaLabel])`),
      [['a','a의 값'], ['a','a의 값']]);
    assert.deepEqual(await tab.js(`[...document.querySelectorAll('${first} .ws-explore-table th math mi')].map(x=>x.textContent)`), ['x','y']);
    assert.equal(await visible(`${item('screen-check')} .ws-checklist`), true);
    assert.equal(await visible('.ws-order .ws-print-only'), false);
    for (const [input, expected, text] of [['0','-0.5','−0.5'], ['0','0.5','0.5'], ['1','1.5','1.5'],
      ['-1.5','-1.5','−1.5'], ['-2','-1.5','−1.5']]) {
      await setInput(tab, slider, input);
      assert.equal(await tab.js(`document.querySelector('${slider}').value`), expected);
      assert.equal(await tab.js(`document.querySelector('${first} [data-y-for="1"]').textContent`), text);
    }
    await tab.js(`sheet.view('a4')`, true);
    assert.equal(await visible(`${first} .ws-explore-snapshots`), true);
    assert.equal(await visible(`${first} .ws-explore-table`), false);
    assert.equal(await visible(`${first} .ws-explore-ctrl`), false);
    assert.equal(await visible(`${item('paper-check')} .ws-checklist`), true);
    assert.equal(await visible('.ws-order .ws-print-only'), true);
    assert.equal(await tab.js(`document.querySelector('#sheet-pages ${item('screen-check')}') === null`), true);
    assert.deepEqual(await tab.js(`[...document.querySelectorAll('${first} .ws-explore-snapshots figcaption')].map(x=>x.textContent)`), ['a=2','a=−1']);
    assert.equal(await tab.js(`document.querySelector('${first} .ws-print-only [data-map], ${first} .ws-print-only [data-y-for]') === null`), true);
    assert.deepEqual(await tab.js(`[...document.querySelectorAll('${first} .ws-explore-snapshots figcaption math, ${first} .ws-explore-print-table th math')].map(x=>x.textContent)`),
      ['a=2','a=−1','x','y','a=2','y','a=−1']);
    assert.deepEqual(await tab.js(`[...document.querySelectorAll('${first} .ws-explore-print-table tr')].map(row=>[...row.cells].map(c=>c.textContent))`),
      [['x','−1','0','1'], ['y (a=2)','−2','0','2'], ['y (a=−1)','1','0','−1']]);
    const report = await tab.report();
    assert.deepEqual(report.issues, []);
    const printed = await pdf(tab, 'snapshot-worksheet', report.pages.length, true);
    const equations = printed.normalize('NFKC').replace(/\s/g, '');
    assert.match(equations, /a=2/); assert.match(equations, /a=−1/);
    assert.match(printed, /인쇄허용\s*점검/); assert.doesNotMatch(printed, /화면전용\s*점검/);
    assert.match(printed, /안에\s*순서대로\s*1,\s*2,\s*3,/);
    assert.match(printed, /화면에서는\s*값을\s*바꾸며\s*살펴볼\s*수\s*있습니다/);
    assert.equal(await tab.js(`document.querySelector('${first} .ws-explore-note') === null`), true);
  }));
  await test('해상도·인쇄 배치·열 복원 및 실제 Chrome PNG', () => usingTab(studentInteractive, async tab => {
    assert.equal(await tab.js('sheet.view()'), 'solve');
    const col = '.ws-split .ws-data col:first-child';
    const width = () => tab.js(`document.querySelector(${quote(col)}).style.width`);
    assert.match(await width(), /%$/);
    await tab.js(`sheet.view('a4')`, true);
    assert.equal(await width(), '69mm');
    const a4 = await tab.report();
    assert.deepEqual(a4.issues, []);
    const inlineFields = [slot('q1.1'), slot('head.이름')];
    const fieldWidths = () => tab.js(`${quote(inlineFields)}.map(sel => document.querySelector(sel).getBoundingClientRect().width)`);
    const before = await fieldWidths();
    await tab.js(`sheet.view('solve')`, true);
    const longAnswer = '길게 입력한 답도 빠짐없이 보여요';
    const longName = '인쇄시험용으로길게쓴이름';
    const fill = [...examples.map(([s,,,v]) => [s,v]), ['q1.1',longAnswer], ['q1.2','오답 예시'], ['head.이름',longName],
      ['q1.4','분자와 분모를 같은 수로 나누면 값이 같아요.\n두 수에 똑같이 적용했어요.'],
      ['q1.5',{strokes:[[[.12,.72],[.25,.35],[.38,.67],[.52,.22]]]}],
      ['q4.1',{strokes:[[[.16,.25],[.38,.41],[.65,.65],[.79,.79]]]}], ['q4.2',[true,false]]];
    await tab.js(`${quote(fill)}.forEach(([s,v])=>sheet.answer(s,v))`);
    await tab.click(action('hint',item('q1')));
    const fullShot = async name => {
      const { cssContentSize:{width,height} } = await tab.send('Page.getLayoutMetrics');
      const { data } = await tab.send('Page.captureScreenshot', { format:'png', captureBeyondViewport:true,
        clip:{ x:0,y:0,width:Math.ceil(width),height:Math.ceil(height),scale:1 } });
      await fs.writeFile(path.join(shots, `${name}.png`), Buffer.from(data,'base64'));
    };
    for (const [w,h,mobile] of [[390,844,true],[1024,768,false]]) {
      await tab.send('Emulation.setDeviceMetricsOverride', metrics(w,h,mobile));
      await tab.js('window.scrollTo(0, 0); new Promise(requestAnimationFrame)',true);
      assert.equal((await tab.report()).overflow_x, 0, `${w}px`);
      assert(await tab.js('Array.from(document.querySelectorAll("#sheet-flow .ws-in")).every(el=>parseFloat(getComputedStyle(el).fontSize) >= 16)'));
      assert.equal(await tab.js('document.querySelectorAll("#sheet-flow .ws-split")[0].getBoundingClientRect().width > 0'), true);
      await fullShot(`solve-${w}`);
    }
    await tab.js(`window.dispatchEvent(new Event('beforeprint'))`);
    assert.equal(await tab.js('sheet.view()'), 'a4', 'beforeprint는 동기');
    const report = await tab.report();
    assert.equal(report.print_leaks, 0);
    const placement = layout => layout.pages.map(page => page.slots.map(s => s.items));
    assert.deepEqual(placement(report), placement(a4));
    assert.deepEqual(report.issues, []);
    assert.equal(await width(), '69mm');
    assert((await fieldWidths()).every((w,i) => w > before[i]), '채운 한 줄 칸은 원래 칸보다 늘어남');
    assert(await tab.js(`${quote(inlineFields)}.every(sel => {
      const ink = document.querySelector(sel + ' .ws-ink');
      return ink.scrollWidth <= ink.clientWidth + 1 && ink.scrollHeight <= ink.clientHeight + 1;
    })`), '채운 답의 실제 영역이 너비·높이를 넘지 않음');
    assert.equal(await tab.js(`getComputedStyle(document.querySelector('${slot('q1.1')} .ws-blank-mark')).display`), 'none');
    await fullShot('a4');
    const text = await pdf(tab,'interactive-filled',a4.pages.length,true);
    const flat = value => value.replace(/\s/g, '');
    assert(flat(text).includes(flat(longAnswer))); assert(flat(text).includes(longName));
    assert.match(text, /두 수에 똑같이 적용했어요/);
    assert.doesNotMatch(text, /다시 생각해 봐요|공약수를 찾아|0.5를 10분의 5로/);
    await tab.js(`window.dispatchEvent(new Event('afterprint'))`);
    assert.equal(await tab.js('sheet.view()'), 'solve');
    await tab.js('window.print = () => { window.printed = sheet.view(); }');
    await tab.click(action('print'));
    await eventually(() => tab.js(`window.printed === 'a4' && sheet.view() === 'solve'`), '인쇄 단추 보기 복원');
    await tab.js('sheet.clearAnswers()');
  }));
  await test('쪽 머리 이름 띠는 풀기 화면에서만 숨기고 A4와 PDF에 남김', async () => {
    const html = await fs.readFile(fileURLToPath(studentInteractive), 'utf8');
    const band = '<table class="ws-band"><tr><td>쪽 머리 이름 띠</td></tr></table>';
    const source = html.replace(/<section class="ws-item" data-id="q3"[^>]*>/, match =>
      `<section class="ws-item" data-id="band" data-break="1">${band}</section>${match}`);
    assert.notEqual(source, html);
    await usingTab(await saveFile('interactive-band', source), async tab => {
      const shown = selector => tab.js(`!!document.querySelector(${quote(selector)})?.checkVisibility()`);
      assert.equal(await shown('#sheet-flow .ws-band'), false);
      assert.equal(await shown(`#sheet-flow ${item('band')}`), false, '빈 문항 상자도 남기지 않음');
      assert(await shown('#sheet-flow .ws-head .ws-name-slot input'));
      await tab.js("sheet.view('a4')", true);
      assert(await shown('#sheet-pages .ws-band'));
      assert.match(await pdf(tab, 'nameband', (await tab.report()).pages.length), /쪽\s*머리\s*이름\s*띠/);
      await tab.js("sheet.view('solve')", true);
      assert.equal(await shown('#sheet-flow .ws-band'), false);
      assert(await tab.js(`!!new DOMParser().parseFromString(sheet.serialize(),'text/html').querySelector('#sheet-src .ws-band')`));
    }, metrics(390,844,true));
  });
  await test('두 단은 넓을 때 나란히 좁을 때 문서 순서대로', async () => usingTab(lesson, async tab => {
    const two = await tab.js(`sheet.exportStudent().replace('data-columns="2"','data-columns="2" data-interactive="1"')`);
    await usingTab(await saveFile('interactive-columns',two), async student => {
      const positions = () => student.js(`[...document.querySelectorAll('.ws-flow-col')].map(x=>[x.offsetLeft,x.offsetTop])`);
      await student.send('Emulation.setDeviceMetricsOverride', metrics(1024,768));
      let [a,b] = await positions(); assert(a[0] < b[0] && a[1] === b[1]);
      await student.send('Emulation.setDeviceMetricsOverride', metrics(390,844,true));
      [a,b] = await positions(); assert(a[0] === b[0] && a[1] < b[1]);
      assert.equal((await student.report()).overflow_x,0);
    });
  }));
  await test('자동 저장·답 파일 우선 복원·직렬화 정리·지우기', async () => {
    const name = `${slot('head.이름')} input`;
    const filled = { 'head.이름':'로컬 시험', 'q1.1':'1/2', 'q1.4':'설명은 다섯 자 이상입니다.',
      'q2.1':[1,3], 'q2.3':{l1:'r2',l2:'r1'}, 'q2.4':['t2','t3','t1'], 'q3.4':[2,3], 'q4.1':{strokes:[[[.1,.2],[.8,.7]]]} };
    await usingTab(studentInteractive, async tab => {
      await tab.js('sheet.clearAnswers()'); await setInput(tab,name,filled['head.이름']);
      await tab.js(`Object.entries(${quote(filled)}).slice(1).forEach(([s,v])=>sheet.answer(s,v))`);
      await tab.js(`sheet.view('a4')`,true);
    });
    await usingTab(studentInteractive, async tab => {
      assert.equal(await tab.js('sheet.view()'),'a4','넓은 화면에서 마지막 보기');
      assert.deepEqual(await valuesOf(tab),{});
      assert.match(await tab.js('document.querySelector(".ws-answer-prompt [role=status]").textContent'), /마지막 저장 \d{2}:\d{2}/);
      assert.equal(await tab.js(`document.querySelector('main').innerText.includes('로컬 시험')`), false);
      await tab.click(action('resume-answers'));
      assert.deepEqual(await valuesOf(tab),filled);
      assert.equal(await tab.js(`document.querySelector(${quote(name)}).value`),filled['head.이름']);
    });
    await usingTab(studentInteractive, async tab => {
      assert.equal(await tab.js('sheet.view()'),'solve','좁은 화면은 항상 풀기 보기');
      await tab.click(action('resume-answers'));
      await tab.click(action('check',item('q1')));
      await tab.click(action('hint',item('q1')));
      await tab.click(action('reveal-answer',item('q1')));
      await tab.click(action('reveal-solution',item('q1')));
      const slider = '.ws-explore-ctrl input';
      const previous = await tab.js(`document.querySelector('.ws-explore .ws-plot path').getAttribute('d')`);
      await setInput(tab,slider,'-2');
      assert.notEqual(await tab.js(`document.querySelector('.ws-explore .ws-plot path').getAttribute('d')`),previous);
      assert.equal(await tab.js(`document.querySelector('.ws-explore [data-y-for="2"]').textContent`),'−4');
      const clean = await tab.js(`(() => {
        const d=new DOMParser().parseFromString(sheet.serialize(),'text/html');
        return { runtime: !!d.querySelector('#sheet-flow,#sheet-pages,[data-view],[data-sheet-runtime],.ws-in,.ws-ink,.ws-pen,.ws-pen-tools,.ws-picked,.ws-match-lines,.ws-plot [data-sheet-runtime],.ws-check-bar,.ws-ok,.ws-no,.ws-verdict,.ws-reveal,.ws-solution,.ws-explore-ctrl,iframe,.ws-hint[hidden],[data-value],[data-check],[data-src],.ws-key'),
          order:[...d.querySelectorAll('.ws-order-list li')].map(x=>x.dataset.token), path:d.querySelector('.ws-explore .ws-plot path').getAttribute('d'),
          y:d.querySelector('.ws-explore [data-y-for="2"]').textContent, col:d.querySelector('.ws-split .ws-data col').style.width };
      })()`);
      assert.deepEqual(clean,{runtime:false,order:['t1','t2','t3'],path:'M8 48L48 8',y:'2',col:'69mm'});
      await command('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:work});
      await tab.click(action('save-answers'));
      const file = path.join(work,'그래프와 수 (내 답).html');
      await eventually(()=>fs.readFile(file,'utf8').catch(()=>false),'답 HTML 내려받기');
      const fileUrl = pathToFileURL(file).href;
      // Chrome's local files can share storage in this profile. Start with a clean device.
      await tab.js('sheet.clearAnswers()');
      const device = { 'head.이름':'기기에서 고친 이름', 'q1.1':'3/4', 'q1.3':'2' };
      await usingTab(fileUrl, async copy => {
        assert.deepEqual(await valuesOf(copy),filled, '기기 답이 없으면 파일 답을 바로 표시');
        assert.equal(await copy.js('!!document.querySelector(".ws-answer-prompt:not([hidden])")'), false);
        await copy.js(`localStorage.setItem('pbj-sheet:interactive-fixture:student',${quote(quote(device))})`);
      });
      await usingTab(fileUrl, async copy => {
        assert.deepEqual(await valuesOf(copy),{});
        await copy.click(action('resume-answers'));
        assert.deepEqual(await valuesOf(copy),device, '파일의 이전 값이나 기기에서 지운 답을 가져오지 않음');
        await setInput(copy, `${slot('q1.1')} input`, '4/5');
      });
      await usingTab(fileUrl, async copy => {
        await copy.click(action('resume-answers'));
        assert.deepEqual(await valuesOf(copy),{ ...device, 'q1.1':'4/5' });
      });
      await usingTab(fileUrl, async copy => {
        assert.deepEqual(await valuesOf(copy),{});
        await copy.click(action('open-file-answers'));
        assert.deepEqual(await valuesOf(copy),filled, '기기 답으로 파일의 빈칸을 채우지 않음');
        assert.equal(await copy.js(`document.querySelector(${quote(name)}).value`),filled['head.이름']);
        await copy.js(`window.confirm = () => { throw Error('브라우저 확인창을 열면 안 됩니다'); }; localStorage.setItem('sample-sync:queue','보존')`);
        await copy.click(action('clear-answers'));
        assert.deepEqual(await valuesOf(copy),filled);
        await copy.click(action('cancel-clear-answers'));
        assert.deepEqual(await valuesOf(copy),filled);
        await copy.click(action('clear-answers'));
        await copy.click(action('confirm-clear-answers'));
        assert.deepEqual(await valuesOf(copy),{});
        assert.equal(await copy.js(`localStorage.getItem('pbj-sheet:interactive-fixture:student')`),null);
        assert.equal(await copy.js(`localStorage.getItem('sample-sync:queue')`),'보존');
      });
      await tab.js(`localStorage.setItem('sample-sync:queue','보존'); sheet.answer('head.이름','다음 차례 시험')`);
    },metrics(390,844,true));
    await usingTab(studentInteractive, async tab => {
      assert.deepEqual(await valuesOf(tab),{});
      await tab.click(action('start-fresh'));
      await tab.click(action('confirm-start-fresh'));
      assert.deepEqual(await valuesOf(tab),{});
      assert.equal(await tab.js(`document.querySelector(${quote(name)}).value`), '');
      assert.equal(await tab.js(`localStorage.getItem('pbj-sheet:interactive-fixture:student:saved-at')`), null);
      assert.equal(await tab.js(`localStorage.getItem('sample-sync:queue')`),'보존');
    });
  });
  await test('교사 편집·되돌리기·복제 후 재번호·학생용 재판별', () => usingTab(interactive, async tab => {
    await tab.key('e');
    const editField = async (selector,text) => { await tab.mouseClick(selector,2); await paste(tab,text); await tab.key('Enter'); };
    const newText = '앞칸 {{|예시}} 뒤칸 {{㉠|$\\frac{3}{4}$;;0.75|26}} $\\begin{cases}x+y=3 \\\\ x-y=1\\end{cases}$';
    await editField(qText('q1'),newText);
    assert.equal(await tab.js(`document.querySelectorAll('${qText('q1')} math mtr').length`), 2);
    const hashes = (selector) => tab.js(`document.querySelector(${quote(selector)}).dataset.checkHash`);
    assert.equal(await hashes(slot('q1.2')),hash(salt,'number','3/4'));
    assert.equal(await tab.js(`document.querySelector('${qText('q1')} .ws-blank[data-slot="q1.2"] .ws-key').textContent`),'34');
    await editField(`${slot('q1.4')} .ws-key`,'$2$분');
    const unitAttribute = () => tab.js(`document.querySelector(${quote(slot('q1.4'))}).getAttribute('data-check-unit')`);
    assert.equal(await unitAttribute(), '1');
    assert.equal(await hashes(slot('q1.4')),[hash(salt,'number','2'),hash(salt,'number','2@분')].join(','));
    await tab.key('z',2);
    assert.equal(await hashes(slot('q1.4')),hash(salt,'number','1'));
    assert.equal(await unitAttribute(), null);
    await editField(`${slot('q1.4')} .ws-key`,'$2$분');
    await tab.click(action('duplicate',item('q1')));
    const serialized = await tab.js(`(() => {
      const d=new DOMParser().parseFromString(sheet.serialize(),'text/html');
      const a=__sheetCore.codec.decodeReveal(${quote(salt)},d.querySelector('#sheet-reveal').textContent);
      const slots=[...d.querySelectorAll('#sheet-src [data-slot]')].map(el=>el.dataset.slot);
      return { slots, answers:a.slots, items:Object.keys(a.items), check:JSON.parse(d.querySelector('[data-slot="q1.2"]').dataset.check) };
    })()`);
    assert.equal(new Set(serialized.slots).size,serialized.slots.length);
    assert(serialized.slots.includes('q1-copy-1.2'));
    assert.deepEqual(serialized.check,{mode:'auto',accept:['0.75'],right:'3/4',wrong:'7/4'});
    assert.match(serialized.answers['q1.2'].answer_html,/<mfrac>/);
    assert.deepEqual(serialized.answers['q1-copy-1.2'],serialized.answers['q1.2']);
    assert.deepEqual(serialized.items,['q1','q1-copy-1']);
    const exported = await tab.js('sheet.exportStudent()');
    assert.doesNotMatch(exported,/앞칸 \{\{|data-check="\{/);
    await usingTab(await saveFile('edited-interactive-student',exported),async copy=>{
      assert.equal(await copy.js(`sheet.answer('q1.2','3/4')`),true);
      await copy.js(`sheet.answer('q1.2','6/8')`);
      assert.equal(await copy.js(`__sheetCore.verdicts()['q1.2']`),'review');
      assert.equal(await copy.js(`sheet.answer('q1.2','1/2')`),false);
      assert.equal(await copy.js(`sheet.answer('q1.4','2')`),true);
      assert.equal(await copy.js(`sheet.answer('q1-copy-1.2','0.75')`),true);
      await copy.js('sheet.clearAnswers()');
    });
  }));
  await test('교사용 풀이와 채점 요소 편집 → 저장 → 학생용 풀이 열기', async () => {
    const saved = await usingTab(interactive, async tab => {
      await tab.key('e');
      const edit = async (sel,text) => { await tab.mouseClick(sel,2); await paste(tab,text); await tab.key('Enter'); };
      await edit('.ws-t-ans [data-src]', '새 예시 답안 표식');
      await edit('.ws-t-steps li','풀이 새 문장: $\\frac{1}{2}$로 나타내요.');
      await edit('.ws-t-rubric [data-src]', '되돌릴 문장');
      await tab.key('z',2);
      assert.match(await tab.js('document.querySelector(".ws-t-rubric").textContent'), /분자와 분모/);
      await edit('.ws-t-rubric [data-src]', '$3$배를 확인했나요?');
      assert.equal(await tab.js('document.querySelector(".ws-t-steps math mfrac").textContent'),'12');
      return tab.js('sheet.serialize()');
    });
    const html = await usingTab(await saveFile('edited-solution-teacher', saved), tab => tab.js('sheet.exportStudent()'));
    assert.doesNotMatch(html,/풀이 새 문장|3\$배를 확인|교사만 보는 문장|다음 시간에 확인/);
    await usingTab(await saveFile('edited-solution-student', html), async tab => {
      await tab.js(`sheet.clearAnswers(); sheet.answer('q1.1','0.5'); sheet.answer('q1.2','서로 평행'); sheet.answer('q1.3','1'); sheet.answer('q1.4','설명')`);
      await tab.click(action('check',item('q1')));
      await tab.click(action('reveal-solution',item('q1')));
      const box = `${item('q1')} .ws-solution:not(.ws-compare)`;
      assert.match(await tab.js(`document.querySelector('${box}').textContent`),/새 예시 답안 표식.*풀이 새 문장/s);
      assert.equal(await tab.js(`document.querySelector('${box} mfrac')?.textContent`),'12');
      assert.equal(await tab.js(`document.querySelector('${box} .ws-compare-list math')?.textContent`),'3');
      assert(await tab.js(`!!document.querySelector('${box} ${action('play-video')}')`), '풀이 영상 유지');
      assert.doesNotMatch(await tab.js(`document.querySelector('${box}').textContent`), /분모를 확인|되돌릴 문장|교사만 보는 문장/);
      await tab.js('sheet.clearAnswers()');
    });
  });
  async function buildEditorCase(name, blocks, solutions = []) {
    const folder = path.join(work, name), title = `편집 시험 ${name}`;
    const cli = (command, target) => run(process.env.PYTHON || 'python',
      ['-B', '-X', 'utf8', path.join(here, '../../scripts/sheet.py'), command, target],
      {env:{...process.env, SHEET_TEST_SALT:salt, SHEET_FONT_DIR:work}});
    await fs.mkdir(folder);
    await fs.writeFile(path.join(folder, '구성.json'), quote({ version:1, meta:{title, reveal:'바로', sync:false},
      items:blocks, teacher:{memo:['메모는 처음에 둡니다.'], solutions} }));
    await cli('build', folder);
    const teacher = path.join(folder, `${title} (교사용).html`), student = path.join(folder, `${title}.html`);
    return { teacher, student, derive:() => cli('derive', teacher), open:p => pathToFileURL(p).href };
  }
  const mathWriting = () => buildEditorCase('수식-답-판별', [
    {id:'symbols', blocks:[{type:'question', text:'길이 {{|$4\\pi$}} cm와 근호 {{|$2\\sqrt{3}$}}를 써 봅시다.'}]},
    {id:'strict', blocks:[{type:'situation', lines:['기약분수로 써 봅시다.']},
      {type:'question', text:'비는 {{|$\\dfrac{3}{5}$}}입니다.'}]},
    {id:'general', blocks:[{type:'question', text:'비는 {{|$\\frac{3}{5}$}}입니다.'},
      {type:'answer', rows:[{label:'짧은 답', lines:2, answer:'$4$'}, {label:'설명', lines:3, answer:'두 줄 답', check:'off'}]}]},
    {id:'unchecked', blocks:[{type:'question', text:'식은 {{|$x+x+x+x+x+x+x+x$}}입니다.'}]}
  ]);
  let mathWritingFiles;
  await test('폰에서 π와 √를 입력 위치에 넣고 A4 보기와 인쇄에서는 숨김', async () => {
    const files = mathWritingFiles = await mathWriting();
    const student = await fs.readFile(files.student, 'utf8');
    assert.doesNotMatch(student, /data-check=|4π|2√3/);
    await usingTab(files.open(files.student), async tab => {
      const insert = async (id, symbol, before, caret, after) => {
        const field = `${slot(id)} input`;
        await setInput(tab, field, before);
        await tab.js(`document.querySelector(${quote(field)}).setSelectionRange(${caret}, ${caret})`);
        await tab.mouseClick(`${slot(id)} + .ws-symbols [data-symbol="${symbol}"]`);
        assert.equal((await valuesOf(tab))[id], after);
      };
      await insert('symbols.1', 'π', '4 cm', 1, '4π cm');
      await insert('symbols.2', '√', '23', 1, '2√3');
      await tab.click(action('check', item('symbols')));
      assert.deepEqual(await tab.js(`Object.values(__sheetCore.verdicts()).slice(0,2)`), [true,true]);
      const visible = () => tab.js(`[...document.querySelectorAll('.ws-symbols button')].filter(el => el.getClientRects().length).length`);
      assert.equal(await visible(), 2);
      await tab.send('Emulation.setEmulatedMedia', {media:'print'});
      assert.equal(await visible(), 0);
      await tab.send('Emulation.setEmulatedMedia', {media:'screen'});
      await tab.js(`sheet.view('a4')`);
      assert.equal(await visible(), 0);
      await tab.js(`sheet.view('solve'); sheet.clearAnswers()`);
    }, metrics(390,844,true));
  });
  await test('Enter는 한 줄에서만 문항을 확인하고 한글 조합 중에는 무시', async () => {
    const files = mathWritingFiles ??= await mathWriting();
    await usingTab(files.open(files.student), async tab => {
      const field = `${slot('symbols.1')} input`, short = `${slot('general.2')} input`;
      await tab.js(`sheet.clearAnswers(); window.checkedItems = []; document.addEventListener('sheet:check', e => checkedItems.push(e.detail.item));`);
      await setInput(tab, field, '4pi');
      await tab.mouseClick(field);
      await tab.js(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, isComposing:true}))`);
      assert.deepEqual(await tab.js('checkedItems'), []);
      await tab.key('Enter');
      assert.equal(await tab.js(`__sheetCore.verdicts()['symbols.1']`), true);
      await setInput(tab, short, '4');
      await tab.mouseClick(short); await tab.key('Enter');
      assert.equal(await tab.js(`__sheetCore.verdicts()['general.2']`), true);
      const long = `${slot('general.3')} textarea`;
      await tab.mouseClick(long); await paste(tab, '첫 줄');
      const enter = {key:'Enter', code:'Enter', windowsVirtualKeyCode:13};
      await tab.send('Input.dispatchKeyEvent', {type:'keyDown', text:'\r', ...enter});
      await tab.send('Input.dispatchKeyEvent', {type:'keyUp', ...enter});
      await paste(tab, '둘째 줄');
      assert.equal((await valuesOf(tab))['general.3'], '첫 줄\n둘째 줄');
      await tab.mouseClick(`${slot('unchecked.1')} input`); await tab.key('Enter');
      assert.deepEqual(await tab.js('checkedItems'), ['symbols','general']);
      await tab.js('sheet.clearAnswers()');
    });
  });
  await test('같은 값의 분수도 문항의 기약분수 요구에 따라 판별', async () => {
    const files = mathWritingFiles ??= await mathWriting();
    await usingTab(files.open(files.student), async tab => {
      await tab.js('sheet.clearAnswers()');
      for (const [id, value, expected] of [
        ['strict','6/10',false], ['strict','0.6',false], ['strict','3/5',true],
        ['general','6/10','review'], ['general','0.60',true]
      ]) {
        await setInput(tab, `${slot(id + '.1')} input`, value);
        await tab.click(action('check', item(id)));
        assert.equal(await tab.js(`__sheetCore.verdicts()[${quote(id + '.1')}]`), expected);
        if (expected === 'review') {
          const result = `${item(id)} .ws-verdict.ws-review`;
          assert.match(await tab.js(`document.querySelector(${quote(result)}).textContent`), /기약분수로 나타냈는지/);
        }
      }
      await setInput(tab, `${slot('symbols.2')} input`, '√12');
      await tab.click(action('check', item('symbols')));
      assert.equal(await tab.js(`__sheetCore.verdicts()['symbols.2']`), 'review');
      await tab.js('sheet.clearAnswers()');
    });
  });
  async function editField(tab, selector, value) {
    await tab.mouseClick(selector, 2);
    assert.notEqual(await draft(tab), undefined, `편집 입력: ${selector}`);
    await paste(tab, value); await tab.key('Enter');
    assert.equal(await draft(tab), undefined, '편집 내용 확정');
  }
  await test('교사 표 칸 편집과 조립의 전체 빈칸 판정이 같음', async () => {
    const cases = [
      ['{{|$\\frac{5}{36}$}}', true], ['{{|$\\frac{1}{x^{2}}$}}', true],
      ['{{㉠|$\\sqrt{3}$}}', true], ['{{㉠}}', true], ['{{|3|20}}', true],
      ['  {{|$\\overline{AB}$}}  ', true], ['{{|3}} cm', false],
      ['{{|3}}{{|4}}', false], ['약 {{|3}}', false],
    ];
    const files = await buildEditorCase('표-빈칸-판정', [{id:'q1', blocks:[
      {type:'table', header:['값'], rows:cases.map(() => ['{{|3}}'])},
    ]}]);
    await usingTab(files.open(files.teacher), async tab => {
      const selectors = cases.map((_, i) => `#sheet-pages ${item('q1')} table.ws-data tbody tr:nth-child(${i+1}) td`);
      const flags = () => tab.js(`${quote(selectors)}.map(s=>document.querySelector(s).classList.contains('ws-fill'))`);
      assert.deepEqual(await flags(), cases.map(() => true));
      await tab.click(action('edit'));
      for (const [i, [source, expected]] of cases.entries()) {
        await editField(tab, selectors[i], '임시');
        assert.equal(await tab.js(`document.querySelector(${quote(selectors[i])}).classList.contains('ws-fill')`), false);
        await editField(tab, selectors[i], source);
        assert.equal(await tab.js(`document.querySelector(${quote(selectors[i])}).classList.contains('ws-fill')`), expected, source);
      }
      assert.deepEqual(await flags(), cases.map(([, expected])=>expected));
    });
  });
  await test('표 머리·행 라벨·표 자체 단위를 고쳐도 저장과 derive 판별이 같음', async () => {
    const files = await buildEditorCase('표-단위', [{id:'q1', blocks:[
      {type:'table', header:['반','최솟값','최댓값'], rows:[['1반','{{|$45$}}','{{|$90$}}'], ['2반','{{|$50$}}','{{|$95$}}']]},
      {type:'table', unit:'점', header:['반','최솟값'], rows:[['1반','{{|$45$}}']]},
      {type:'answer', rows:[{label:'길이', answer:'$4$', group:'length'}, {answer:'$5$', group:'length'}]},
    ]}]);
    const check = (tab, id, values) => tab.js(`${quote(values)}.map(value=>sheet.answer(${quote(id)},value))`);
    await usingTab(files.open(files.student), async tab => {
      assert.deepEqual(await check(tab,'q1.5',['45','45점','45명','45cm','45분']),[true,true,false,false,false]);
      await tab.js('sheet.clearAnswers()');
    });
    const edited = await usingTab(files.open(files.teacher), async tab => {
      await tab.click(action('edit'));
      const table = `${item('q1')} table.ws-data:first-child`;
      await editField(tab, `${table} thead th:nth-child(2)`, '최솟값 (점)');
      assert.deepEqual(await check(tab,'q1.1',['45','45점','45명']),[true,true,false]);
      assert.deepEqual(await check(tab,'q1.3',['50점','50명']),[true,false]);
      await editField(tab, `${table} tbody tr:first-child td:first-child`, '1반 (명)');
      assert.deepEqual(await check(tab,'q1.2',['90명','90점']),[true,false]);
      await tab.click(action('undo'));
      assert.deepEqual(await check(tab,'q1.2',['90명','90점']),[true,true]);
      await editField(tab, `${table} tbody tr:first-child td:first-child`, '1반 (명)');
      await tab.click(action('table-unit', item('q1')));
      await paste(tab, '점수'); await tab.key('Enter');
      assert.equal(await tab.js('document.querySelector(".ws-edit-input").getAttribute("aria-invalid")'), 'true');
      await tab.key('Escape');
      await tab.click(action('table-unit', item('q1')));
      await paste(tab, '분'); await tab.key('Enter');
      await editField(tab, '.ws-lab-text', '길이 (cm)');
      await tab.js('sheet.clearAnswers()');
      return tab.js('[sheet.serialize(), sheet.exportStudent()]');
    });
    await fs.writeFile(files.teacher, edited[0]);
    await fs.writeFile(files.student, edited[1]);
    for (const derived of [false, true]) {
      if (derived) await files.derive();
      await usingTab(files.open(files.student), async tab => {
        for (const [id, inputs, expected] of [
          ['q1.1',['45','45점','45명','45cm','45분'],[true,true,false,false,false]],
          ['q1.2',['90명','90분'],[true,false]], ['q1.3',['50점','50분'],[true,false]],
          ['q1.4',['95분','95명'],[true,false]], ['q1.5',['45점','45분'],[true,false]],
          ['q1.6',['4cm','4분'],[true,false]], ['q1.7',['5cm','5분'],[true,false]],
        ]) assert.deepEqual(await check(tab,id,inputs),expected,`${derived}: ${id}`);
        await tab.js('sheet.clearAnswers()');
      });
    }
  });
  const editorQuestions = ['q1','q2','q3','q4'].map((id, i) => ({id, blocks:[{type:'question', text:`발문 ${i+1}` }]}));
  const editorSolutions = editorQuestions.map(({id}, i) => ({item:id, answer:`예시 ${i+1}`, steps:[`풀이 ${i+1}`], rubric:[`기준 ${i+1}`]}));
  const solution = id => `.ws-item[data-solution-for="${id}"]`;
  const solutionOrder = tab => tab.js(`[...document.querySelectorAll('#sheet-pages .ws-item[data-solution-for]')]
    .map(n=>[n.dataset.solutionFor,n.querySelector('.ws-t-q').textContent])`);
  await test('문항을 옮기면 교사용 풀이의 순서와 번호가 저장 후에도 같음', async () => {
    const files = await buildEditorCase('재배열', editorQuestions, editorSolutions);
    const expected = [['q1','1번'],['q2','2번'],['q4','3번'],['q3','4번']];
    const saved = await usingTab(files.open(files.teacher), async tab => {
      await tab.click(action('edit'));
      await tab.click(action('up', item('q4')));
      assert.equal(await tab.js(`document.querySelector(${quote(`${item('q4')} .ws-qn`)}).textContent`), '3.');
      assert.deepEqual(await solutionOrder(tab), expected);
      return tab.js('sheet.serialize()');
    });
    await fs.writeFile(files.teacher, saved);
    await usingTab(files.open(files.teacher), async tab => assert.deepEqual(await solutionOrder(tab), expected));
    await files.derive();
    await usingTab(files.open(files.teacher), async tab => assert.deepEqual(await solutionOrder(tab), expected));
    await usingTab(files.open(files.student), async tab => {
      await tab.click(action('reveal-solution', item('q4')));
      assert.match(await tab.js(`document.querySelector(${quote(`${item('q4')} .ws-solution`)}).textContent`), /예시 4.*풀이 4/s);
    });
  });
  await test('복제 문항의 풀이를 고쳐 저장하면 브라우저와 derive에서 모두 열림', async () => {
    const files = await buildEditorCase('복제', editorQuestions.slice(0,2), editorSolutions.slice(0,2));
    const edited = await usingTab(files.open(files.teacher), async tab => {
      await tab.click(action('edit'));
      await tab.click(action('duplicate', item('q1')));
      assert.deepEqual(await solutionOrder(tab), [['q1','1번'],['q1-copy-1','2번'],['q2','3번']]);
      await tab.click(action('undo'));
      assert.deepEqual(await solutionOrder(tab), [['q1','1번'],['q2','2번']]);
      await tab.click(action('duplicate', item('q1')));
      await editField(tab, `${solution('q1-copy-2')} .ws-t-steps li`, '복제 문항만의 풀이');
      return tab.js('[sheet.serialize(), sheet.exportStudent()]');
    });
    await fs.writeFile(files.teacher, edited[0]);
    await fs.writeFile(files.student, edited[1]);
    let browser;
    for (const derived of [false, true]) {
      if (derived) await files.derive();
      const texts = await usingTab(files.open(files.student), async tab => {
        const found = [];
        for (const id of ['q1','q1-copy-2','q2']) {
          await tab.click(action('reveal-solution', item(id)));
          found.push(await tab.js(`document.querySelector(${quote(`${item(id)} .ws-solution`)}).textContent`));
        }
        assert.match(found[0], /풀이 1/);
        assert.match(found[1], /복제 문항만의 풀이.*기준 1/s);
        assert.match(found[2], /풀이 2/);
        return found;
      });
      if (derived) assert.deepEqual(texts, browser);
      else browser = texts;
    }
  });
  await test('MathML 미지원 안내는 교사·학생의 두 화면에만 표시', async () => {
    const visible = tab => tab.js('!!document.querySelector(".ws-math-notice")?.checkVisibility()');
    const warn = html => html.replace('</head>', '<style>math, mfrac, msup, math * { display:inline !important; }</style></head>');
    const student = await fs.readFile(fileURLToPath(studentInteractive), 'utf8');
    const teacher = await fs.readFile(fileURLToPath(interactive), 'utf8');
    for (const [name, html, firstView] of [['student', student, 'solve'],
      ['static-student', student.replace(' data-interactive="1"',''), 'a4'], ['teacher', teacher, 'a4']]) {
      await usingTab(await saveFile(`mathml-supported-${name}`, html), async tab => assert.equal(await visible(tab), false));
      await usingTab(await saveFile(`mathml-broken-${name}`, warn(html)), async tab => {
        assert.equal(await tab.js('sheet.view()'), firstView);
        assert.equal(await visible(tab), true, name);
        assert.match(await tab.js('document.querySelector(".ws-math-notice").textContent'), /수식.*최신 브라우저.*PDF/);
        await tab.js(`sheet.view('a4')`, true);
        assert.equal(await visible(tab), true, `${name} A4 화면`);
        assert.equal((await tab.report()).print_leaks, 0);
        await tab.send('Emulation.setEmulatedMedia', {media:'print'});
        assert.equal(await visible(tab), false, `${name} 인쇄`);
        assert.equal(await tab.js(`!!new DOMParser().parseFromString(sheet.serialize(),'text/html').querySelector('.ws-math-notice')`), false);
      }, metrics(390,844,true));
    }
  });
  await test('1판에는 2판 장치가 없고, 영상은 누를 때만 임베드', async () => {
    const staticHtml = await usingTab(interactive, tab => tab.js(`sheet.exportStudent().replace(' data-interactive="1"','')`));
    await usingTab(await saveFile('static-v2-blocks',staticHtml), async tab => {
      assert.equal((await tab.report()).interactive,false);
      assert.deepEqual(await tab.js('sheet.slots()'),[]);
      assert.equal(await tab.js('!!document.querySelector("#sheet-flow,.ws-in,.ws-pen,.ws-check-bar,.ws-explore-ctrl,iframe,[data-view]")'),false);
      assert.equal(await tab.js('document.querySelector(".ws-heading").checkVisibility()'),true);
    });
    await usingTab(studentInteractive,async tab=>{
      await tab.js(`sheet.view('solve')`,true);
      assert.equal(await tab.js('document.querySelectorAll("iframe").length'),0);
      assert.match(await tab.js('document.querySelector("video").getAttribute("src")'),/^data:video\/mp4/);
      await tab.send('Network.setBlockedURLs',{urls:['*youtube-nocookie.com*']});
      await tab.click(action('play-video', '.ws-video[data-yt]'));
      assert.equal(await tab.js('document.querySelector(".ws-video iframe").src'), 'https://www.youtube-nocookie.com/embed/M7lc1UVf-VE?autoplay=1&start=30');
      assert.equal(await tab.js(`new DOMParser().parseFromString(sheet.serialize(),'text/html').querySelector('iframe')===null`),true);
    });
  });
  const syncConfig = {v:1,url:'https://records.invalid',key:'legacy-anon-key',table:'sheet_records',sheet:'fixture-sheet',title:'그래프와 수'};
  const mockScript = `<script>
    window.syncMock = { calls: [], fail: false, loginFail: false, rows: [] };
    window.confirm = () => true;
    window.fetch = async (url, options = {}) => {
      const call = { url: String(url), method: options.method || 'GET', headers: options.headers || {},
        body: options.body ? JSON.parse(options.body) : null, keepalive: options.keepalive || false };
      syncMock.calls.push(call);
      if (syncMock.fail) throw new Error('offline');
      if (call.url.includes('/auth/')) return { ok: !syncMock.loginFail, status: syncMock.loginFail ? 400 : 200,
        json: async () => ({ access_token: 'teacher-token' }) };
      return { ok: true, status: 200, json: async () => syncMock.rows };
    };
  <\/script>`;
  const withSync = html => html.replace('</head>', `<meta name="sheet-sync" content='${quote(syncConfig)}'></head>`)
    .replace('<script id="sheet-js">', `${mockScript}<script id="sheet-js">`);
  const teacherSource = await usingTab(interactive, tab => tab.js('sheet.serialize()'));
  const studentSource = await usingTab(interactive, tab => tab.js('sheet.exportStudent()'));
  const syncTeacher = await saveFile('sync-teacher', withSync(teacherSource));
  const syncStudent = await saveFile('sync-student', withSync(studentSource));
  await test('공용 기기의 복원 선택·날짜·재확인·제출 잠금', async () => {
    const old = { 'head.이름':'앞 학생', 'q1.1':'0.5' };
    const key = 'pbj-sheet:interactive-fixture:student';
    const url = await saveFile('sync-restore', withSync(studentSource).replace('<script id="sheet-js">',
      '<script>delete HTMLElement.prototype.inert;<\/script><script id="sheet-js">'));
    await usingTab(url, async tab => {
      await tab.js(`sheet.clearAnswers(); localStorage.clear(); syncMock.fail = true;
        Date.now = () => +new Date(2024,10,27,9,10);
        sheet.answer('head.이름','앞 학생'); sheet.answer('q1.1','0.5')`);
      await tab.click(action('check',item('q1')));
      await eventually(() => tab.js('Object.keys(localStorage).some(k=>k.endsWith(":queue"))'), '앞 학생 전송 대기');
    });
    await usingTab(url, async tab => {
      assert.deepEqual(await valuesOf(tab),{});
      const prompt = '.ws-answer-prompt:not([hidden])';
      assert.equal(await tab.js(`document.querySelector('${prompt} button').dataset.sheetAction`), 'start-fresh');
      assert.equal(await tab.js('document.activeElement.dataset.sheetAction'), 'start-fresh');
      assert.match(await tab.js(`document.querySelector('${prompt}').textContent`), /11월 27일 09:10/);
      assert.match(await tab.js(`document.querySelector('${action('resume-answers')}').textContent`), /이 기기에서 내가 쓰던 답/);
      for (const name of ['sync-submit','sync-retry','save-answers','clear-answers']) {
        assert.equal(await tab.js(`document.querySelector('${action(name)}').disabled`), true, name);
        await tab.click(action(name));
      }
      assert.equal(await tab.js('syncMock.calls.length'), 0);
      assert.equal(await tab.js(`sheet.answer('q1.1','2')`), null);
      assert.equal(await tab.js(`(() => { const v=document.querySelector('main video'); v.focus(); return document.activeElement === v; })()`), false);
      await tab.click(action('start-fresh'));
      assert.deepEqual(await tab.js(`JSON.parse(localStorage.getItem(${quote(key)})).answers`),old);
      assert.equal(await tab.js('document.activeElement.dataset.sheetAction'), 'cancel-start-fresh');
      await tab.click(action('cancel-start-fresh'));
      assert.equal(await tab.js('document.activeElement.dataset.sheetAction'), 'start-fresh');
      await tab.click(action('start-fresh')); await tab.click(action('confirm-start-fresh'));
      assert.deepEqual(await valuesOf(tab),{});
      assert.equal(await tab.js(`localStorage.getItem(${quote(key)})`),null);
      assert.equal(await tab.js('document.querySelector(\'.ws-in\').disabled'), false);
      assert.equal(await tab.js('document.querySelector("main video").controls'), true);
      await tab.click(action('sync-retry'));
      await eventually(() => tab.js('syncMock.calls.length === 1'), '앞 학생 기록 재전송');
      assert.deepEqual(await tab.js('syncMock.calls[0].body.student'),{'이름':'앞 학생'});
      assert.deepEqual(await tab.js('syncMock.calls[0].body.answers'),{'q1.1':'0.5'});
    }, metrics(390,844,true));
  });
  await test('교사 편집 판을 학생 자동 저장과 기록에서 분리', async () => {
    const legacy = 'interactive-';
    const savedBuild = html => JSON.parse(/<meta name="sheet-build" content="([^"]*)"/.exec(html)[1].replaceAll('&quot;', '"'));
    const [teacherHtml, studentHtml, answerOnly] = await usingTab(syncTeacher, async tab => {
      const edit = async (sel,text) => { await tab.mouseClick(sel,2); await paste(tab,text); await tab.key('Enter'); };
      const saved = () => tab.js('sheet.serialize()');
      const before = savedBuild(await saved());
      await tab.click(action('preview-student'));
      await tab.js(`sheet.answer('head.이름','미리보기 시험'); sheet.answer('q2.4',['t2','t3','t1']); sheet.clearAnswers()`);
      await tab.click(action('return-teacher'));
      assert.deepEqual(savedBuild(await saved()), before, '학생 화면을 미리 보거나 입력해도 같은 판');
      await tab.key('e');
      await edit('.ws-t-list li','교사만 보는 새 메모');
      assert.deepEqual(savedBuild(await saved()), before);
      await edit(`${slot('q1.3')} .ws-key`,'$2$');
      const answer = savedBuild(await saved());
      await tab.key('z',2);
      assert.deepEqual(savedBuild(await saved()), before);
      await edit(qText('q1'),'0.75를 분수로 쓰세요. {{㉠|$\\frac{3}{4}$|26}}');
      const after = await saved();
      const exported = await tab.js('sheet.exportStudent()');
      assert.deepEqual(savedBuild(after), savedBuild(exported));
      assert.deepEqual(savedBuild(await saved()), savedBuild(after), '그대로 다시 저장해도 같은 판');
      return [after, exported, answer];
    });
    const edition = savedBuild(teacherHtml).edition;
    assert(edition && edition !== answerOnly.edition && answerOnly.edition, '정답만 또는 발문·빈칸을 고쳐도 각각 새 판');
    assert.equal(savedBuild(teacherHtml).source_sha256, 'interactive-fixture');
    const oldState = { 'head.이름':'구판 학생', 'q1.1':'1/2' };
    const url = await saveFile('new-edition-student',studentHtml);
    await usingTab(url, async tab => {
      await tab.js(`localStorage.clear(); localStorage.setItem('pbj-sheet:interactive-fixture:student',${quote(quote(oldState))})`);
    });
    const posted = await usingTab(url, async tab => {
      assert.deepEqual(await valuesOf(tab),{});
      assert.equal(await tab.js(`!!document.querySelector('${action('resume-answers')}')`),false);
      await tab.js(`sheet.answer('head.이름','신판 학생'); sheet.answer('q1.1','3/4')`);
      await tab.click(action('check',item('q1')));
      await eventually(() => tab.js('syncMock.calls.length === 1'), '신판 확인 기록');
      return tab.js('syncMock.calls[0].body');
    });
    assert.equal(posted.build, edition);
    await usingTab(await saveFile('new-edition-teacher',teacherHtml), async tab => {
      assert.equal(savedBuild(await tab.js('sheet.serialize()')).edition,edition);
      const current = { ...posted, created_at:'2026-01-02T09:00:00Z' };
      const old = { ...current, build: legacy, event:'submit', student:{'이름':'구판 학생'} };
      await tab.js(`syncMock.rows=${quote([current,old])}`);
      await tab.click(action('student-records'));
      await setInput(tab,'.ws-records input[type=email]','teacher@example.invalid');
      await setInput(tab,'.ws-records input[type=password]','test-password');
      await tab.js('document.querySelector(".ws-records-login").requestSubmit()');
      await eventually(() => tab.js('document.querySelectorAll(".ws-records-table tbody tr").length === 2'), '새 판 학생 표');
      const states = await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].map(tr=>[tr.cells[0].textContent,tr.cells[4].textContent])`);
      assert.deepEqual(Object.fromEntries(states),{'신판 학생':'미제출','구판 학생':'다른 판에서 제출'});
    });
  });
  await test('학생 기록 전송·이름·제출·대기열·온라인 재전송', () => usingTab(syncStudent, async tab => {
    await tab.js('sheet.clearAnswers(); localStorage.clear()');
    await tab.js(`sheet.view('a4')`,true); await tab.click(action('sync-submit'));
    await eventually(() => tab.js(`sheet.view() === 'solve'`), '이름 안내를 위한 풀이 화면');
    await tab.click(action('check', item('q1')));
    assert.equal(await tab.js('syncMock.calls.length'), 0);
    assert.equal(await tab.js('document.querySelector(".ws-sync-warning").hidden'), false);
    await setInput(tab, `${slot('head.이름')} input`, '가상 학생');
    await tab.js(`sheet.answer('q1.1','0.5'); sheet.answer('q1.2','오답'); sheet.answer('q2.1',[1,3])`);
    await tab.click(action('check', item('q1')));
    await eventually(() => tab.js('syncMock.calls.length === 1'), '확인 기록 전송');
    const sent = await tab.js('syncMock.calls[0]');
    assert.equal(sent.url, 'https://records.invalid/rest/v1/sheet_records');
    assert.equal(sent.method, 'POST'); assert.equal(sent.keepalive, true);
    assert.deepEqual(sent.headers, {apikey:'legacy-anon-key',Authorization:'Bearer legacy-anon-key','Content-Type':'application/json',Prefer:'return=minimal'});
    assert.deepEqual(Object.keys(sent.body), ['sheet_id','sheet_title','build','client_id','student','event','item','score','answers','verdicts']);
    assert.equal(sent.body.sheet_id,'fixture-sheet'); assert.equal(sent.body.build,'interactive-');
    assert.match(sent.body.client_id,/^[0-9a-f]{32}$/); assert.deepEqual(sent.body.student,{'이름':'가상 학생'});
    assert.equal(sent.body.event,'check'); assert.equal(sent.body.item,'q1');
    assert(Object.keys(sent.body.answers).every(key => key.startsWith('q1.')));
    assert(Object.keys(sent.body.verdicts).every(key => key.startsWith('q1.')));
    assert.equal(sent.body.answers['q1.1'],'0.5'); assert.equal(sent.body.verdicts['q1.1'],true);
    assert.equal(sent.body.verdicts['q1.2'],'review'); assert.deepEqual(sent.body.score,{checked:3,correct:2,total:11});
    assert.equal(await tab.js('document.querySelector(".ws-sync-notice p").textContent'),
      "이름과 학번·반·번호 등 본인 정보와 답을 선생님께 보냅니다. 이름을 쓰고 '확인'을 누르면 그 문항의 답·판별 결과와 활동지 전체 맞힌 수가, '선생님께 제출'을 누르면 모든 답·판별 결과와 맞힌 수가 전송됩니다. 짝 칸에 쓴 정보는 보내지 않습니다.");
    await tab.js('syncMock.fail = true'); await tab.click(action('sync-submit'));
    await eventually(() => tab.js(`document.querySelector(${quote(action('sync-retry'))}).textContent.includes('보내지 못함')`), '실패 상태');
    const queued = await tab.js(`JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.endsWith(':queue'))))`);
    assert.equal(queued.length,1); assert.equal(queued[0].event,'submit'); assert.equal(queued[0].item,null);
    assert.deepEqual(queued[0].answers['q2.1'],[1,3]); assert.equal(queued[0].verdicts['q2.1'],true);
    assert(!Object.keys(queued[0].answers).some(key => key.startsWith('head.')));
    await tab.js('syncMock.fail = false; window.dispatchEvent(new Event("online"))');
    await eventually(() => tab.js(`document.querySelector(${quote(action('sync-retry'))}).textContent.includes('보냄')`), '온라인 재전송');
    assert.equal(await tab.js(`Object.keys(localStorage).some(k=>k.endsWith(':queue'))`),false);
    assert.equal(await tab.js('syncMock.calls.length'),3);
    await tab.js(`sheet.view('a4')`,true); assert.equal((await tab.report()).print_leaks,0);
    assert.equal(await tab.js('document.querySelector(".ws-sync-notice").checkVisibility()'),false);
    await tab.js('sheet.clearAnswers()');
  }));
  await test('nosync·교사 미리보기·설정 없음은 학생 기록 전송 안 함', async () => {
    for (const suffix of ['?nosync','#nosync']) await usingTab(syncStudent + suffix, async tab => {
      await tab.js(`sheet.clearAnswers(); sheet.answer('head.이름','가상 학생')`); await tab.click(action('check',item('q1')));
      assert.equal(await tab.js('syncMock.calls.length'),0); assert.equal(await tab.js(`!!document.querySelector(${quote(action('sync-submit'))})`),false);
    });
    await usingTab(syncTeacher, async tab => {
      await tab.click(action('preview-student')); await tab.js(`sheet.answer('head.이름','가상 학생')`);
      await tab.click(action('check',item('q1'))); assert.equal(await tab.js('syncMock.calls.length'),0);
      assert.equal(await tab.js(`!!document.querySelector(${quote(action('sync-submit'))})`),false);
    });
    await usingTab(studentInteractive, async tab => assert.equal(await tab.js(`!!document.querySelector(${quote(action('sync-submit'))})`),false));
  });
  await test('새 공개 키·최대 50건 대기열·수동 재시도', async () => {
    const url = await saveFile('sync-publishable',withSync(studentSource).replace('legacy-anon-key','sb_publishable_fixture'));
    await usingTab(url, async tab => {
      await tab.js(`sheet.clearAnswers(); localStorage.clear(); sheet.answer('head.이름','가상 학생'); syncMock.fail=true`);
      await tab.js(`for(let i=0;i<56;i++) document.dispatchEvent(new CustomEvent('sheet:check',{detail:{item:'q1'}}))`);
      await eventually(() => tab.js(`document.querySelector(${quote(action('sync-retry'))}).textContent.includes('보내지 못함')`), '대기열 저장');
      assert.equal(await tab.js(`JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.endsWith(':queue')))).length`),50);
      assert.equal(await tab.js('syncMock.calls[0].headers.apikey'),'sb_publishable_fixture');
      assert.equal(await tab.js('Object.hasOwn(syncMock.calls[0].headers,"Authorization")'),false);
      await tab.js('syncMock.fail=false'); await tab.click(action('sync-retry'));
      await eventually(() => tab.js(`document.querySelector(${quote(action('sync-retry'))}).textContent.includes('보냄')`), '대기열 수동 재시도');
      assert.equal(await tab.js('syncMock.calls.length'),51);
      assert.equal(await tab.js(`Object.keys(localStorage).some(k=>k.endsWith(':queue'))`),false);
    });
  });
  await test('이전 대기열의 확인 기록도 해당 문항만 재전송', async () => {
    const url = await saveFile('sync-old-queue',withSync(studentSource).replace('fixture-sheet','old-queue-sheet'));
    const queueKey = `pbj-sheet-sync:${encodeURIComponent('https://records.invalid|sheet_records|old-queue-sheet')}:queue`;
    const pending = [
      {event:'check',item:'q1',student:{'이름':'본인','짝-이름':'짝 이름','짝-학번':'짝 학번'},answers:{'q1.1':'확인 답','q2.1':'다른 문항','head.짝-이름':'짝 이름'},verdicts:{'q1.1':true,'q2.1':false,'head.짝-이름':true},score:{correct:1,checked:2}},
      {event:'submit',item:null,student:{'이름':'본인','짝-이름':'짝 이름','짝-학번':'짝 학번'},answers:{'q1.1':'제출 답','q2.1':'다른 문항','head.짝-이름':'짝 이름'},verdicts:{'q1.1':true,'q2.1':false,'head.짝-이름':true},score:{correct:1,checked:2}},
    ];
    await usingTab(url, tab => tab.js(`localStorage.clear(); localStorage.setItem(${quote(queueKey)},${quote(quote(pending))})`));
    await usingTab(url, async tab => {
      await tab.click(action('sync-retry'));
      await eventually(() => tab.js('syncMock.calls.length===2'), '이전 대기열 재전송');
      const sent = await tab.js('syncMock.calls.map(call=>call.body)');
      assert.deepEqual(sent[0].answers,{'q1.1':'확인 답'}); assert.deepEqual(sent[0].verdicts,{'q1.1':true});
      assert.deepEqual(sent[0].score,{correct:1,checked:2});
      assert.deepEqual(sent[1].answers,{'q1.1':'제출 답','q2.1':'다른 문항'});
      assert.deepEqual(sent[1].verdicts,{'q1.1':true,'q2.1':false});
      assert(sent.every(row => JSON.stringify(row.student) === JSON.stringify({'이름':'본인'})));
      assert.doesNotMatch(JSON.stringify(sent),/짝 이름|짝 학번|짝-이름/);
    });
  });
  await test('짝 칸은 확인과 제출의 신원·답에서 제외', async () => {
    const names = ['학번','짝-학번','짝-이름','기타'].map(name =>
      `<span class="ws-name-slot" data-slot="head.${name}" data-check-kind="name">&nbsp;</span>`).join('');
    const source = studentSource.replace('</table>', `<tr><td colspan="2">${names}</td></tr></table>`);
    await usingTab(await saveFile('sync-pair',withSync(source)), async tab => {
      await tab.js(`sheet.clearAnswers(); localStorage.clear();
        sheet.answer('head.이름','본인'); sheet.answer('head.학번','10101');
        sheet.answer('head.짝-이름','짝 이름'); sheet.answer('head.짝-학번','짝 학번');
        sheet.answer('head.기타','식별 외 정보'); sheet.answer('q1.1','0.5')`);
      await tab.click(action('check',item('q1')));
      await eventually(() => tab.js('syncMock.calls.length===1'), '짝 활동 확인 기록');
      await tab.click(action('sync-submit'));
      await eventually(() => tab.js('syncMock.calls.length===2'), '짝 활동 제출 기록');
      const sent = await tab.js('syncMock.calls.map(call=>call.body)');
      assert(sent.every(row => JSON.stringify(row.student) === JSON.stringify({'이름':'본인','학번':'10101'})));
      assert(sent.every(row => Object.keys(row.answers).every(key => !key.startsWith('head.'))));
      assert.doesNotMatch(JSON.stringify(sent),/짝 이름|짝 학번|짝-이름|식별 외 정보/);
      await tab.js('sheet.clearAnswers()');
    });
  });
  await test('정답 공개 없음이어도 제출', async () => {
    const source = await usingTab(await fixture('interactive','없음'), tab => tab.js('sheet.exportStudent()'));
    await usingTab(await saveFile('sync-no-reveal',withSync(source)),async tab=>{
      await tab.js(`sheet.clearAnswers(); sheet.answer('head.이름','가상 학생')`);
      await tab.click(action('sync-submit'));
      await eventually(()=>tab.js('syncMock.calls.length===1'),'공개 없음 제출');
      assert.equal(await tab.js('syncMock.calls[0].body.event'),'submit');
      assert.deepEqual(await tab.js('syncMock.calls[0].body.score'),{checked:0,correct:0,total:0});
    });
  });
  await test('교사 로그인·기록 표·정답 비교·CSV·로그아웃', () => usingTab(syncTeacher, async tab => {
    const rows = [
      {client_id:'one',created_at:'2026-01-02T04:00:00Z',student:{'이름':'가상 학생','반':'1','번':'2'},event:'check',score:{correct:1,checked:2,total:11},answers:{'q1.1':'0.5','q1.2':'오답'},verdicts:{'q1.1':true,'q1.2':false}},
      {client_id:'two',created_at:'2026-01-02T03:00:00Z',student:{'이름':'다른 학생'},event:'submit',score:{correct:1,checked:1,total:11},answers:{'q1.1':'1/2'},verdicts:{'q1.1':true}},
      {client_id:'one',created_at:'2026-01-02T02:00:00Z',student:{'이름':'가상 학생','반':'1','번':'2'},event:'submit',score:{correct:1,checked:2,total:11},answers:{'q1.1':'0.5','q1.2':'오답'},verdicts:{'q1.1':true,'q1.2':false}},
    ].map(row => ({build:'interactive-', ...row}));
    await tab.js(`syncMock.rows = ${quote(rows)}`); await tab.click(action('student-records'));
    await setInput(tab,'.ws-records input[type=email]','teacher@example.invalid');
    await setInput(tab,'.ws-records input[type=password]','test-password');
    await tab.js('syncMock.loginFail = true; document.querySelector(".ws-records-login").requestSubmit()');
    await eventually(() => tab.js('document.querySelector(".ws-records-message").textContent.includes("로그인하지")'), '로그인 실패');
    await tab.js('syncMock.loginFail = false; document.querySelector(".ws-records-login").requestSubmit()');
    await eventually(() => tab.js('document.querySelectorAll(".ws-records-table tbody tr").length === 2'), '학생 표');
    const calls = await tab.js('syncMock.calls');
    assert.equal(calls[1].url,'https://records.invalid/auth/v1/token?grant_type=password');
    assert.deepEqual(calls[1].body,{email:'teacher@example.invalid',password:'test-password'});
    assert.equal(calls[2].url,'https://records.invalid/rest/v1/sheet_records?sheet_id=eq.fixture-sheet&order=created_at.desc&limit=5000');
    assert.equal(calls[2].headers.Authorization,'Bearer teacher-token');
    assert.equal(await tab.js('document.querySelector(".ws-records input[type=password]").value'),'');
    assert.equal(await tab.js('Object.values(sessionStorage).includes("teacher-token")'),true);
    assert.equal(await tab.js('Object.values(localStorage).includes("teacher-token")'),false);
    assert.equal(await tab.js('document.querySelector(".ws-records-login").checkVisibility()'),false);
    assert.match(await tab.js('document.querySelector(".ws-records-summary").textContent'),/학생\(이름 기준\)2제출2기기2/);
    assert.match(await tab.js('document.querySelector(".ws-records-rates").textContent'),/1\.1 · 100% \(2\/2\)/);
    assert.match(await tab.js('document.querySelector(".ws-records-table tbody tr").textContent'),/가상 학생.*제출.*1\/2.*✓.*✗/);
    await tab.click('.ws-records-table tbody tr');
    assert.match(await tab.js('document.querySelector(".ws-records-detail").textContent'),/가상 학생의 답.*0\.5.*12/s);
    for (const width of [390,1024]) {
      await tab.send('Emulation.setDeviceMetricsOverride',metrics(width,width===390?844:768,width===390));
      await tab.js('new Promise(resolve => requestAnimationFrame(resolve))',true);
      const {data} = await tab.send('Page.captureScreenshot',{format:'png'});
      await fs.writeFile(path.join(shots,`records-${width}.png`),Buffer.from(data,'base64'));
    }
    await command('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:work});
    await tab.click(action('records-csv'));
    const csv = await eventually(() => fs.readFile(path.join(work,'학생 기록.csv')).catch(() => false),'CSV 내려받기');
    assert.deepEqual([...csv.subarray(0,3)],[0xef,0xbb,0xbf]); assert.match(csv.toString('utf8'),/가상 학생/);
    await tab.js('syncMock.rows=[]'); await tab.click(action('records-refresh'));
    await eventually(() => tab.js('document.querySelector(".ws-records-message").textContent.includes("sheet_teachers")'), '빈 결과 안내');
    await tab.js(`syncMock.rows=${quote(rows)}`); await tab.click(action('records-refresh'));
    await eventually(() => tab.js('document.querySelectorAll(".ws-records-table tbody tr").length === 2'), '기록 다시 표시');
    await tab.click('.ws-records-table tbody tr');
    await tab.click(action('records-logout')); assert.equal(await tab.js('Object.values(sessionStorage).includes("teacher-token")'),false);
    assert.equal(await tab.js('document.querySelectorAll(".ws-records-table tbody tr").length'),0);
    assert.equal(await tab.js('document.querySelector(".ws-records-detail").textContent'),'');
  }));
  await test('공용 기기 학생별 집계·제출본·검토·예전 기록 병합', () => usingTab(syncTeacher, async tab => {
    const a = {'이름':'가상 학생'.normalize('NFD'),'반':' 1 ','번':'2'};
    const aLater = {'성명':' 가상 \t 학생 ','반':'1','학번':' 2 '};
    const b = {'이름':'다른 학생','반':'1','번':'3'};
    const rows = [
      {client_id:'shared',created_at:'2026-01-02T11:00:00Z',student:aLater,event:'check',score:{correct:0,checked:1},answers:{'q1.1':'제출 뒤 수정'},verdicts:{'q1.1':false}},
      {client_id:'shared',created_at:'2026-01-02T10:00:00Z',student:b,event:'check',score:{correct:2,checked:3},answers:{'q2.1':[1,3]},verdicts:{'q2.1':true}},
      {client_id:'shared',created_at:'2026-01-02T08:00:00Z',student:a,event:'submit',score:{correct:0,checked:1},answers:{'q1.1':'이전 제출본'},verdicts:{'q1.1':false}},
      {client_id:'shared',created_at:'2026-01-02T07:00:00Z',student:b,event:'check',score:{correct:1,checked:3},answers:{'q1.1':'기존 답','q1.2':'이전 두번째','q1.3':'옛 답','q2.1':[2]},verdicts:{'q1.1':false,'q1.2':true,'q1.3':true,'q2.1':false}},
      {client_id:'shared',created_at:'2026-01-02T09:30:00Z',student:b,event:'check',score:{correct:1,checked:2},answers:{'q1.3':''},verdicts:{'q1.3':null}},
      {client_id:'shared',created_at:'2026-01-02T09:00:00Z',student:a,event:'submit',score:{correct:1,checked:2},answers:{'q1.1':'최종 제출본','q1.2':'검토 답','q1.3':'검토만','q2.1':[2]},verdicts:{'q1.1':true,'q1.2':'review','q1.3':'review','q2.1':false,'q2.2':'unknown'}},
    ].map(row => ({build:'interactive-', ...row}));
    await tab.js(`syncMock.rows=${quote(rows.slice(1))}`); await tab.click(action('student-records'));
    await setInput(tab,'.ws-records input[type=email]','teacher@example.invalid');
    await setInput(tab,'.ws-records input[type=password]','test-password');
    await tab.js('document.querySelector(".ws-records-login").requestSubmit()');
    await eventually(() => tab.js('document.querySelectorAll(".ws-records-table tbody tr").length > 0'), '학생별 집계');
    const tableRows = () => tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].map(tr=>[...tr.cells].map(td=>td.textContent.normalize('NFC')))`);
    const beforeEdit = await tableRows();
    assert.equal(beforeEdit.length,2);
    assert.equal(beforeEdit.find(row=>row[0].trim()==='가상 학생')[4],'제출');
    assert.equal(beforeEdit.find(row=>row[0].trim()==='다른 학생')[4],'미제출');
    assert.match(await tab.js('document.querySelector(".ws-records-summary").textContent'),/학생\(이름 기준\)2제출1기기1/);
    await tab.js(`syncMock.rows=${quote(rows)}`); await tab.click(action('records-refresh'));
    await eventually(() => tab.js('document.querySelector(".ws-records-table tbody").textContent.includes("제출 후 수정")'), '제출 뒤 확인');
    const displayed = await tableRows();
    assert.equal(displayed.length,2);
    const submitted = displayed.find(row => row[0].trim() === '가상 학생');
    const draft = displayed.find(row => row[0].trim() === '다른 학생');
    assert(submitted && draft);
    assert.equal(submitted[4],'제출 후 수정'); assert.equal(submitted[5],'1/2');
    assert.equal(submitted[7],'✓'); assert.equal(submitted[8],'?'); assert.equal(submitted[9],'?'); assert.equal(submitted[10],'✗'); assert.equal(submitted[11],'');
    assert.equal(draft[4],'미제출'); assert.equal(draft[7],'✗'); assert.equal(draft[8],'✓'); assert.equal(draft[9],''); assert.equal(draft[10],'✓');
    assert.equal(submitted[3],await tab.js(`new Date('2026-01-02T09:00:00Z').toLocaleString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})`));
    assert.match(await tab.js('document.querySelector(".ws-records-summary").textContent'),/학생\(이름 기준\)2제출1기기1/);
    assert.match(await tab.js('document.querySelector(".ws-records-content").textContent'),/같은 이름·반·번호는 한 학생으로 묶습니다/);
    const rates = await tab.js('document.querySelector(".ws-records-rates").textContent');
    assert.match(rates,/1\.1 · 50% \(1\/2\)/); assert.match(rates,/1\.2 · 100% \(1\/1\).*확인 필요 1/);
    assert.match(rates,/1\.3 · — \(0\/0\).*확인 필요 1/);
    await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].find(tr=>tr.cells[0].textContent.normalize('NFC').trim()==='가상 학생').click()`);
    const detail = await tab.js('document.querySelector(".ws-records-detail").textContent');
    assert.match(detail,/제출 뒤 바뀐 답이 있습니다.*마지막 기록 시각/); assert.match(detail,/최종 제출본/);
    const lastClock = await tab.js(`new Date('2026-01-02T11:00:00Z').toLocaleString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})`);
    assert(detail.includes(`마지막 기록 시각: ${lastClock}`));
    assert.doesNotMatch(detail,/제출 뒤 수정|이전 제출본/);
    await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].find(tr=>tr.cells[0].textContent.trim()==='다른 학생').click()`);
    assert.match(await tab.js('document.querySelector(".ws-records-detail").textContent'),/기존 답.*이전 두번째.*\[1,3\]/s);
    await tab.js(`window.captureCSV=URL.createObjectURL; URL.createObjectURL=blob=>{window.savedCSV=blob;return captureCSV(blob)}`);
    await tab.click(action('records-csv'));
    const csv = (await tab.js('savedCSV.text()',true)).normalize('NFC');
    const csvA = csv.split('\r\n').find(line=>line.includes('가상 학생'));
    const csvB = csv.split('\r\n').find(line=>line.includes('다른 학생'));
    assert(csvA && csvB); assert.match(csvA,/제출 후 수정.*\?.*최종 제출본/); assert.doesNotMatch(csvA,/제출 뒤 수정/);
    assert.match(csvB,/미제출.*기존 답.*이전 두번째/);
    await tab.js(`syncMock.rows.push({...${quote(rows[1])},client_id:'another-device',created_at:'2026-01-02T12:00:00Z'})`);
    await tab.click(action('records-refresh'));
    await eventually(() => tab.js('document.querySelector(".ws-records-summary").textContent.includes("기기2")'), '서로 다른 기기 수');
    assert.equal((await tableRows()).length,2);
    const resubmissions = [
      {client_id:'shared',created_at:'2026-01-02T11:00:00.000003Z',student:a,event:'submit',score:{correct:1,checked:1},answers:{'q1.1':'최종 재제출본'},verdicts:{'q1.1':true}},
      {client_id:'shared',created_at:'2026-01-02T11:00:00.000001Z',student:a,event:'submit',score:{correct:0,checked:1},answers:{'q1.1':'이전 재제출본'},verdicts:{'q1.1':false}},
      {client_id:'shared',created_at:'2026-01-02T11:00:00.000002Z',student:a,event:'check',score:{correct:0,checked:1},answers:{'q1.1':'재제출 사이 수정'},verdicts:{'q1.1':false}},
    ].map(row => ({build:'interactive-', ...row}));
    await tab.js(`syncMock.rows.push(...${quote(resubmissions)})`); await tab.click(action('records-refresh'));
    await eventually(async () => (await tableRows()).find(row=>row[0].trim()==='가상 학생')?.[4]==='제출', '재제출 상태');
    await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].find(tr=>tr.cells[0].textContent.normalize('NFC').trim()==='가상 학생').click()`);
    const resubmittedDetail = await tab.js('document.querySelector(".ws-records-detail").textContent');
    assert.match(resubmittedDetail,/최종 재제출본/); assert.doesNotMatch(resubmittedDetail,/제출 뒤 바뀐 답|재제출 사이 수정/);
    await tab.js(`syncMock.rows.push({...${quote(rows[1])},student:{'이름':'가상 학생','반':'2','번':'2'}})`);
    await tab.click(action('records-refresh'));
    await eventually(() => tab.js('document.querySelector(".ws-records-summary").textContent.includes("학생(이름 기준)3")'), '동명이인 분리');
    assert.equal((await tableRows()).length,3);
  }));
  await test('판별 집계·다른 판 원본·그림과 CSV 판 구분', () => usingTab(syncTeacher, async tab => {
    const a = {'이름':'가상 학생','반':'1','번':'2'}, b = {'이름':'이전 학생'}, c = {'이름':'판 미상 학생'}, d = {'이름':'진행 학생'}, e = {'이름':'제출 학생','반':'1','번':'4'};
    const rows = [
      {client_id:'five',build:'old123456789',created_at:'2026-01-02T13:00:00Z',student:e,event:'check',item:'q8',
        answers:{'q8.1':[[1,2],[3,4]]},verdicts:{'q8.1':false},score:{correct:0,checked:1}},
      {client_id:'five',build:'interactive-',created_at:'2026-01-02T04:00:00Z',student:{'이름':'제출 학생','학급':'1','번호':'4'},event:'submit',
        answers:{'q1.1':'제출 유지'},verdicts:{'q1.1':true},score:{correct:1,checked:1}},
      {client_id:'one',build:'old123456789',created_at:'2026-01-02T12:00:00Z',student:a,event:'check',item:'q1',
        answers:{'q1.1':'옛 확인 답','q1.5':{strokes:[[[1.2,.5],[.5,1.2],[-.2,.5],[.5,-.2],[1.2,.5]]]}},verdicts:{'q1.1':false},score:{correct:0,checked:1}},
      {client_id:'one',build:'interactive-',created_at:'2026-01-02T11:00:00Z',student:a,event:'check',item:'q1',
        answers:{'q1.1':'현재 수정'},verdicts:{'q1.1':false},score:{correct:0,checked:1}},
      {client_id:'one',build:'interactive-',created_at:'2026-01-02T10:00:00Z',student:a,event:'submit',
        answers:{'q1.1':'0.5','q1.5':{strokes:[[[.1,.2],[.8,.7]]]},'q3.1':[-1.5,1],
          'q3.2':[[-1,1],[1,2]],'q3.3':[[0,1],[-2,-1]],'q4.1':{strokes:[[[.1,.2],[.8,.7]],[[.2,.3]]]}},

        verdicts:{'q1.1':true},score:{correct:1,checked:1}},
      {client_id:'one',build:'old123456789',created_at:'2026-01-02T09:00:00Z',student:a,event:'submit',
        answers:{'q1.1':'옛 제출 답','q3.2':[[-1,1],[1,2]],'q3.3':[[-1,-1],[1,1]]},verdicts:{'q1.1':false},score:{correct:0,checked:1}},
      {client_id:'two',build:'old123456789',created_at:'2026-01-02T08:00:00Z',student:b,event:'submit',
        answers:{'q1.1':'이전 판 답'},verdicts:{'q1.1':true},score:{correct:1,checked:1}},
      {client_id:'three',created_at:'2026-01-02T07:00:00Z',student:c,event:'check',
        answers:{'q1.1':'판 없는 답'},verdicts:{'q1.1':true},score:{correct:1,checked:1}},
      {client_id:'four',build:'old123456789',created_at:'2026-01-02T06:00:00Z',student:d,event:'submit',
        answers:{'q1.1':'이전 제출'},verdicts:{'q1.1':true},score:{correct:1,checked:1}},
      {client_id:'four',build:'interactive-',created_at:'2026-01-02T05:00:00Z',student:d,event:'check',
        answers:{'q1.1':'현재 진행'},verdicts:{'q1.1':false},score:{correct:0,checked:1}},
    ];
    await tab.js(`syncMock.rows=${quote(rows)}`); await tab.click(action('student-records'));
    await setInput(tab,'.ws-records input[type=email]','teacher@example.invalid');
    await setInput(tab,'.ws-records input[type=password]','test-password');
    await tab.js('document.querySelector(".ws-records-login").requestSubmit()');
    await eventually(() => tab.js('document.querySelectorAll(".ws-records-table tbody tr").length===5'), '판이 섞인 학생 표');
    const tableRows = await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].map(tr=>[...tr.cells].map(td=>td.textContent))`);
    const current = tableRows.find(row=>row[0]==='가상 학생'), older = tableRows.find(row=>row[0]==='이전 학생');
    const unknown = tableRows.find(row=>row[0]==='판 미상 학생'), draft = tableRows.find(row=>row[0]==='진행 학생');
    assert.equal(current[4],'제출 후 수정'); assert.equal(current[5],'1/1'); assert.equal(current[7],'✓');
    assert.match(current[6],/다른 판 old123/);
    const oldClock = await tab.js(`new Date('2026-01-02T12:00:00Z').toLocaleString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})`);
    assert(current[6].includes(oldClock));
    assert.equal(older[4],'다른 판에서 제출'); assert.equal(older[5],'—'); assert.equal(older[7],'');
    assert.equal(unknown[4],'다른 판 기록만 있음'); assert.equal(unknown[5],'—'); assert.match(unknown[6],/판 미상/);
    assert.equal(draft[4],'미제출'); assert.equal(draft[5],'0/1'); assert.equal(draft[7],'✗');
    assert.equal(tableRows.find(row=>row[0]==='제출 학생')[4],'제출', '다른 판의 확인은 현재 판을 수정하지 않음');
    assert.match(await tab.js('document.querySelector(".ws-records-summary").textContent'),/학생\(이름 기준\)5제출2기기5/);
    assert.match(await tab.js('document.querySelector(".ws-records-rates").textContent'),/1\.1 · 67% \(2\/3\)/);
    await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].find(tr=>tr.cells[0].textContent==='가상 학생').click()`);
    assert.match(await tab.js('document.querySelector(".ws-records-detail").textContent'),/제출 뒤 바뀐 답.*옛 확인 답.*옛 제출 답/s);
    const pictures = await tab.js(`(() => {
      const table=document.querySelector('.ws-records-detail-table');
      return [...table.querySelectorAll('tr')].slice(1).filter(row=>row.cells[1]?.querySelector('svg')).map(row=>({
        slot:row.cells[0].textContent, viewBox:row.cells[1].querySelector('svg').getAttribute('viewBox'),
        circles:[...row.cells[1].querySelectorAll('svg > circle')].map(c=>[+c.getAttribute('cx'),+c.getAttribute('cy')]),
        lines:[...row.cells[1].querySelectorAll('svg > line')].map(l=>['x1','y1','x2','y2'].map(a=>+l.getAttribute(a))),
        polyline:!!row.cells[1].querySelector('svg > polyline'),
      })); })()`);
    const bySlot = slotName => pictures.find(p => p.slot === slotName);
    assert(bySlot('q1.5')?.polyline); assert(bySlot('q4.1')?.polyline); assert.equal(bySlot('q4.1').circles.length,1);
    const drawingBox = bySlot('q1.5').viewBox.split(' ').map(Number);
    const drawingRatio = await tab.js(`(() => {const r=document.querySelector('${slot('q1.5')}').getBoundingClientRect();return r.width/r.height})()`);
    assert(Math.abs(drawingBox[2] / drawingBox[3] - drawingRatio) < .001);
    assert.deepEqual(bySlot('3.1').circles,[[22.5,6],[85,6]]);
    assert.deepEqual(bySlot('3.2').circles,[[18,18],[38,8]]);
    assert.deepEqual(bySlot('3.3').circles,[[28,18],[8,38]]);
    const [x1,y1,x2,y2] = bySlot('3.3').lines[0];
    assert.deepEqual([[x1,y1],[x2,y2]].sort((a,b)=>a[0]-b[0]),[[8,38],[38,8]]);
    for (const width of [390,1024]) {
      await tab.send('Emulation.setDeviceMetricsOverride',metrics(width,width===390?844:768,width===390));
      await tab.js(`new Promise(resolve => requestAnimationFrame(resolve))`,true);
      const pictureSize = await tab.js(`(() => {
        const row=[...document.querySelector('.ws-records-detail-table').rows].find(r=>r.cells[0].textContent==='q1.5');
        const svg=row.cells[1].querySelector('svg'), rect=svg.getBoundingClientRect(), cell=row.cells[1].getBoundingClientRect();
        svg.scrollIntoView({block:'center'});
        return {width:rect.width,height:rect.height,cell:cell.width,viewBox:svg.getAttribute('viewBox')}; })()`);
      const [, , w, h] = pictureSize.viewBox.split(' ').map(Number);
      assert(pictureSize.width <= pictureSize.cell && Math.abs((pictureSize.width-2)/(pictureSize.height-2)-w/h)<.05, `${width}px 그림 비율`);
      const {data} = await tab.send('Page.captureScreenshot',{format:'png'});
      await fs.writeFile(path.join(shots,`records-drawing-${width}.png`),Buffer.from(data,'base64'));
    }
    const otherDetails = await tab.js(`[...document.querySelectorAll('.ws-records-detail h3')].map(h=>({label:h.textContent,text:h.nextElementSibling.textContent,svg:h.nextElementSibling.querySelectorAll('svg').length,columns:h.nextElementSibling.rows[0].cells.length}))`);
    assert.equal(otherDetails.length,2); assert(otherDetails.every(row => row.label.includes('다른 판 old123') && row.columns === 2));
    assert(otherDetails.some(row=>row.text.includes('옛 확인 답') && row.svg===1));
    assert(otherDetails.some(row=>row.text.includes('옛 제출 답') && row.svg===2));
    assert(otherDetails.every(row=>!row.text.includes('교사용 정답')));
    await tab.js(`window.captureCSV=URL.createObjectURL; URL.createObjectURL=blob=>{window.savedCSV=blob;return captureCSV(blob)}`);
    await tab.click(action('records-csv'));
    const csv = await tab.js('savedCSV.text()',true), lines = csv.split('\r\n');
    assert.match(lines[0],/"판"/); assert.equal(lines.length,10);
    assert(lines.some(line=>line.includes('interactive-') && line.includes('제출 후 수정') && line.includes('0.5')));
    assert(lines.some(line=>line.includes('old123456789') && line.includes('옛 제출 답')));
    assert(lines.some(line=>line.includes('판 미상') && line.includes('판 없는 답')));
    for (const line of lines.filter(line=>line.includes('old123456789') || line.includes('판 미상 학생'))) {
      assert.doesNotMatch(line,/✓|✗|"1","1"/);
    }
    await tab.js(`[...document.querySelectorAll('.ws-records-table tbody tr')].find(tr=>tr.cells[0].textContent==='제출 학생').click()`);
    const missingSlot = await tab.js(`(() => {const h=document.querySelector('.ws-records-detail h3'), table=h.nextElementSibling;
      return {svg:table.querySelectorAll('svg').length,text:table.textContent};})()`);
    assert.equal(missingSlot.svg,1); assert.match(missingSlot.text,/직선 여부는 알 수 없습니다.*\[\[1,2\],\[3,4\]\]/);
  }));
  await test('교사용 판 정보가 없으면 같은 판으로 판정하지 않음', async () => {
    const source = withSync(teacherSource).replace('interactive-fixture','');
    await usingTab(await saveFile('sync-no-build',source), async tab => {
      await tab.js(`syncMock.rows=[{client_id:'one',build:'',created_at:'2026-01-02T08:00:00Z',student:{'이름':'판 미상'},event:'submit',answers:{'q1.1':'기록 답'},verdicts:{'q1.1':true},score:{correct:1,checked:1}}]`);
      await tab.click(action('student-records'));
      await setInput(tab,'.ws-records input[type=email]','teacher@example.invalid');
      await setInput(tab,'.ws-records input[type=password]','test-password');
      await tab.js('document.querySelector(".ws-records-login").requestSubmit()');
      await eventually(() => tab.js('document.querySelectorAll(".ws-records-table tbody tr").length===1'), '판 정보 없는 기록');
      const cells = await tab.js(`[...document.querySelector('.ws-records-table tbody tr').cells].map(cell=>cell.textContent)`);
      assert.equal(cells[4],'다른 판에서 제출'); assert.equal(cells[5],'—'); assert.equal(cells[7],'');
      await tab.click('.ws-records-table tbody tr');
      assert.match(await tab.js('document.querySelector(".ws-records-detail").textContent'),/판 미상.*기록 답/s);
      assert.doesNotMatch(await tab.js('document.querySelector(".ws-records-detail").textContent'),/교사용 정답/);
    });
  });
  await test('휴대폰 머리표·OX와 음수 막대·펜 복원', async () => {
    let html = studentSource.replace('data-kind="수업"','data-kind="연습"');
    const head = `<table class="ws-head ws-head--연습"><tr><td class="ws-head-title"><div class="ws-head-1">기록 시험</div></td></tr><tr><td class="ws-head-name">1학년 (<span class="ws-name-slot" data-slot="head.반" data-check-kind="name">&nbsp;&nbsp;&nbsp;</span>)반 (<span class="ws-name-slot" data-slot="head.번" data-check-kind="name">&nbsp;&nbsp;&nbsp;&nbsp;</span>)번 이름 : (<span class="ws-name-slot" data-slot="head.이름" data-check-kind="name">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>)</td></tr></table>`;
    html = html.replace(/<table class="ws-head ws-head--수업">[\s\S]*?<\/table>/, head).replace('data-ymap="0 4 34 6"','data-ymap="-4 4 34 6"');
    const freshCss = await fs.readFile(path.join(here,'../../assets/engine/theme-fresh.css'),'utf8');
    for (const theme of ['정본','산뜻']) {
      const themed = theme === '산뜻' ? html.replace('data-theme="정본"','data-theme="산뜻"').replace('</head>',`<style id="sheet-theme">${freshCss}</style></head>`) : html;
      const url = await saveFile(`mobile-${theme}`,themed);
      await usingTab(url, async tab => {
        await tab.js('sheet.clearAnswers()'); await tab.js(`sheet.view('solve')`,true);
        await tab.js(`sheet.answer('head.반','12'); sheet.answer('head.번','123')`);
        for (const width of [360,390,1024]) {
          await tab.send('Emulation.setDeviceMetricsOverride',metrics(width,width===1024?768:844,width!==1024));
          await tab.js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))',true);
          const report = await tab.report();
          if (report.overflow_x) {
            const outside = await tab.js(`[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.width && (r.right>innerWidth+1 || r.left< -1)}).slice(0,12).map(el=>({tag:el.tagName,cls:el.className?.baseVal ?? el.className,id:el.id,rect:[el.getBoundingClientRect().left,el.getBoundingClientRect().right]}))`);
            assert.fail(`${theme} ${width}: 넘침 ${report.overflow_x}px ${quote(outside)}`);
          }
          assert.equal(await tab.js(`(() => {const cell=document.querySelector('.ws-head-name'), r=cell.getBoundingClientRect();return [...cell.querySelectorAll('.ws-name-slot')].every(el=>{const b=el.getBoundingClientRect();return b.left>=r.left && b.right<=r.right;})})()`),true);
          if (width < 720) assert.equal(await tab.js(`['head.반','head.번'].every(id=>{const input=document.querySelector('#sheet-flow [data-slot="'+id+'"] input'); return input.scrollWidth<=input.clientWidth+1;})`),true,`${theme} ${width} 반·번 입력`);
          const ox = await tab.js(`(() => {const cells=[...document.querySelectorAll('.ws-ox-n,.ws-ox-o,.ws-ox-x')];
            const single=cells.every(el=>{const r=document.createRange();r.selectNodeContents(el);return [...r.getClientRects()].filter(x=>x.width&&x.height).length===1});
            const picks=[...document.querySelectorAll('.ws-ox-o,.ws-ox-x')].map(el=>el.getBoundingClientRect().top);
            return single && Math.abs(picks[0]-picks[1])<2;})()`);
          assert(ox,`${theme} ${width} OX 한 줄`);
          if (width !== 360) {
            await tab.js('window.scrollTo(0,0); new Promise(requestAnimationFrame)',true);
            const {cssContentSize:s} = await tab.send('Page.getLayoutMetrics');
            const {data} = await tab.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:Math.ceil(s.width),height:Math.ceil(s.height),scale:1}});
            await fs.writeFile(path.join(shots,`sync-mobile-${theme}-${width}.png`),Buffer.from(data,'base64'));
          }
        }
        await tab.js(`sheet.answer('q3.4',[2,-2])`);
        assert.deepEqual(await tab.js(`Array.from(document.querySelectorAll('${slot('q3.4')} .ws-user-bar')).map(e=>[+e.getAttribute('y'),+e.getAttribute('height')])`),[[13,7],[20,7]]);
        await setInput(tab,'.ws-explore-ctrl input','-2');
        assert.equal(await tab.js(`document.querySelector('.ws-explore [data-y-for="2"]').textContent`),'−4');
        await tab.js(`sheet.answer('q3.1',[1])`); await tab.click(action('pen',slot('q3.1')));
        await tab.mouseClick(`${slot('q3.1')} canvas`);
        assert.deepEqual((await valuesOf(tab))['q3.1'],[1]);
        const saved = await tab.js('window.__sheetCore.serialize(true,true)');
        assert.match(saved,/data-pen-value=/);
        await usingTab(await saveFile(`pen-copy-${theme}`,saved),async copy=>{
          assert.deepEqual(await valuesOf(copy), {});
          await copy.click(action('open-file-answers'));
          assert.deepEqual((await valuesOf(copy))['q3.1'],[1]);
          assert(await copy.js(`document.querySelector('${slot('q3.1')} canvas').getContext('2d').getImageData(0,0,1000,200).data.some(v=>v!==0)`));
        });
      });
      await usingTab(url,async copy=>{
        assert.deepEqual(await valuesOf(copy), {});
        await copy.click(action('resume-answers'));
        assert.deepEqual((await valuesOf(copy))['q3.1'],[1]);
        assert(await copy.js(`document.querySelector('${slot('q3.1')} canvas').getContext('2d').getImageData(0,0,1000,200).data.some(v=>v!==0)`));
        await copy.js('sheet.clearAnswers()');
      });
    }
  });
  await test('콘솔 예외와 외부 요청 없음', async () => { assert.deepEqual(exceptions, []); assert.deepEqual(remotes.filter(u=>u !== 'https://www.youtube-nocookie.com/embed/M7lc1UVf-VE?autoplay=1&start=30'), []); });
  console.log(`화면 · ${shots}`);
} finally {
  await stop();
}
console.log(`합계 · ${completed} 통과, ${failed} 실패`);
if (failed) process.exitCode = 1;
