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
  items: { q1: { solution_html: '<p>풀이 표식: 분수 식을 약분했어요.</p>', rubric: ['분자와 분모에 같은 수를 썼나요?'], video: { yt: 'M7lc1UVf-VE', start: 5 } } },
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
      const named = { Enter: 13, Escape: 27 };
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
    const filled = `${slot('q1.3')} textarea`;
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
          assert.equal(await status('compare'), false);
          await tab.click(action('compare',q1));
          assert.equal(await tab.js(`document.querySelector('${q1} .ws-compare input').checkVisibility()`), true);
          if (policy === '시도 후') {
            assert.equal(await status('reveal-solution'), true);
            await tab.js(`sheet.answer('q1.2','서로 평행'); sheet.answer('q1.3','1')`);
            assert.equal(await status('reveal-solution'), false, '펜 칸은 채우지 않아도 됨');
          }
          await tab.click(action('reveal-solution',q1));
          assert.match(await tab.js(`document.querySelector('${q1} .ws-solution:not(.ws-compare)').textContent`), /풀이 표식/);
          assert.equal(await revealedCount(), 4);
          if (policy === '시도 후') {
            await setInput(tab, `${slot('q1.2')} input`, '');
            assert.equal(await status('reveal-solution'), true);
            assert.equal(await tab.js(`document.querySelector('${q1} .ws-solution:not(.ws-compare)').hidden`), true);
            assert.equal(await revealedCount(), 3);
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
      assert.deepEqual(await valuesOf(tab),filled);
      assert.equal(await tab.js(`document.querySelector(${quote(name)}).value`),filled['head.이름']);
    });
    await usingTab(studentInteractive, async tab => {
      assert.equal(await tab.js('sheet.view()'),'solve','좁은 화면은 항상 풀기 보기');
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
      await usingTab(pathToFileURL(file).href, async copy => {
        await copy.js(`localStorage.setItem('pbj-sheet:interactive-fixture:student',${quote(quote({'head.이름':'다른 자동 저장 값'}))})`);
      });
      await usingTab(pathToFileURL(file).href, async copy => {
        assert.deepEqual(await valuesOf(copy),filled);
        assert.equal(await copy.js(`document.querySelector(${quote(name)}).value`),filled['head.이름']);
        await copy.js('window.confirm = () => false'); await copy.click(action('clear-answers'));
        assert.deepEqual(await valuesOf(copy),filled);
        await copy.js('window.confirm = () => true'); await copy.click(action('clear-answers'));
        assert.deepEqual(await valuesOf(copy),{});
        assert.equal(await copy.js(`localStorage.getItem('pbj-sheet:interactive-fixture:student')`),null);
      });
      await tab.js('sheet.clearAnswers()');
    },metrics(390,844,true));
  });
  await test('교사 편집·되돌리기·복제 후 재번호·학생용 재판별', () => usingTab(interactive, async tab => {
    await tab.key('e');
    const editField = async (selector,text) => { await tab.mouseClick(selector,2); await paste(tab,text); await tab.key('Enter'); };
    const newText = '앞칸 {{|예시}} 뒤칸 {{㉠|$\\frac{3}{4}$;;0.75|26}}';
    await editField(qText('q1'),newText);
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
      assert.equal(await copy.js(`sheet.answer('q1.2','6/8')`),true);
      assert.equal(await copy.js(`sheet.answer('q1.2','1/2')`),false);
      assert.equal(await copy.js(`sheet.answer('q1.4','2')`),true);
      assert.equal(await copy.js(`sheet.answer('q1-copy-1.2','0.75')`),true);
      await copy.js('sheet.clearAnswers()');
    });
  }));
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
      await tab.js(`sheet.answer('head.이름','가상 학생')`); await tab.click(action('check',item('q1')));
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
        answers:{'q1.1':'옛 확인 답','q1.5':{strokes:[[[.2,.3],[.8,.7]]]}},verdicts:{'q1.1':false},score:{correct:0,checked:1}},
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
        for (const width of [360,390,1024]) {
          await tab.send('Emulation.setDeviceMetricsOverride',metrics(width,width===1024?768:844,width!==1024));
          await tab.js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))',true);
          const report = await tab.report();
          if (report.overflow_x) {
            const outside = await tab.js(`[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.width && (r.right>innerWidth+1 || r.left< -1)}).slice(0,12).map(el=>({tag:el.tagName,cls:el.className?.baseVal ?? el.className,id:el.id,rect:[el.getBoundingClientRect().left,el.getBoundingClientRect().right]}))`);
            assert.fail(`${theme} ${width}: 넘침 ${report.overflow_x}px ${quote(outside)}`);
          }
          assert.equal(await tab.js(`(() => {const cell=document.querySelector('.ws-head-name'), r=cell.getBoundingClientRect();return [...cell.querySelectorAll('.ws-name-slot')].every(el=>{const b=el.getBoundingClientRect();return b.left>=r.left && b.right<=r.right;})})()`),true);
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
          assert.deepEqual((await valuesOf(copy))['q3.1'],[1]);
          assert(await copy.js(`document.querySelector('${slot('q3.1')} canvas').getContext('2d').getImageData(0,0,1000,200).data.some(v=>v!==0)`));
        });
      });
      await usingTab(url,async copy=>{
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
