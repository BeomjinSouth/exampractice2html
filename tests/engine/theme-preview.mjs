// 실행: node tests/engine/theme-preview.mjs (e2e.mjs와 같은 Chrome 프로필: 동시에 실행하지 않는다.)
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const here = path.dirname(fileURLToPath(import.meta.url));
const engine = path.resolve(here, '../../assets/engine');
const out = path.join(here, '_out/theme'), profile = path.join(here, '_out/profile');
const work = path.join(process.env.SHEET_TEST_TMP || path.join(here, '_out'), 'theme-work');
const run = promisify(execFile), read = file => fs.readFile(file, 'utf8');
const [css, theme, core, solve] = await Promise.all(['sheet.css','theme-fresh.css','sheet.js','solve.js'].map(f => read(path.join(engine,f))));
const widthHeight = (width,height,mobile = false) => ({width,height,mobile,deviceScaleFactor:1});
const hash = value => createHash('sha256').update(value);
const quote = JSON.stringify, active = new Map(), exceptions = [];
let chrome, closed, socket, browserContextId, seq = 0;

async function until(probe, what) {
  for (const end = Date.now()+15000; Date.now()<end; await delay(60)) {
    const value = await probe(); if (value) return value;
  }
  throw Error(`${what}: 시간 초과`);
}
async function start() {
  const flag = path.join(profile, 'DevToolsActivePort'), old = await read(flag).catch(()=>'');
  chrome = spawn(process.env.SHEET_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    ['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--window-size=1200,900',
      '--no-first-run','--hide-scrollbars','--disable-background-networking','--disable-breakpad','about:blank'],
    {stdio:'ignore',windowsHide:true,env:{...process.env,TEMP:work,TMP:work,TMPDIR:work}});
  let startupError;
  closed = new Promise(resolve => { chrome.once('exit',resolve); chrome.once('error',e=>{startupError=e;resolve();}); });
  const port = await until(async()=>{
    if (startupError || chrome.exitCode !== null) throw startupError || Error('Chrome 조기 종료: e2e와 프로필 사용이 겹치는지 확인하세요.');
    const now = await read(flag).catch(()=>''); return now!==old && Number(now.split('\n')[0]);
  },'Chrome 시작');
  const info = await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json());
  socket = new WebSocket(info.webSocketDebuggerUrl); await once(socket,'open',{signal:AbortSignal.timeout(5000)});
  socket.onmessage = ({data}) => {
    const m = JSON.parse(data), pending = active.get(m.id);
    if (pending) { active.delete(m.id); clearTimeout(pending.timer); if (m.error) pending.reject(Error(`${pending.method}: ${m.error.message}`)); else pending.resolve(m.result); }
    else if (m.method==='Runtime.exceptionThrown') exceptions.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  };
  // 파일 URL의 자동 저장(답·마지막 보기)이 e2e의 견본과 섞이지 않게 격리한다.
  ({browserContextId} = await cmd('Target.createBrowserContext',{disposeOnDetach:true}));
}
function cmd(method,params={},sessionId) {
  const id = ++seq;
  return new Promise((resolve,reject)=>{
    const timer = setTimeout(()=>{active.delete(id);reject(Error(`CDP 시간 초과: ${method}`));},25000);
    active.set(id,{resolve,reject,timer,method}); socket.send(quote({id,method,params,sessionId}));
  });
}
async function stop() {
  if (socket?.readyState===WebSocket.OPEN) socket.send(quote({id:++seq,method:'Browser.close'}));
  if (chrome?.pid && chrome.exitCode===null && !(await Promise.race([closed.then(()=>true),delay(2500,false,{ref:false})]))) {
    if (process.platform==='win32') await run('taskkill',['/PID',String(chrome.pid),'/T','/F']).catch(()=>chrome.kill());
    else chrome.kill();
  }
  socket?.close();
}
async function tab(html,name,fn) {
  const file = path.join(work,`${name}.html`); await fs.writeFile(file,html);
  const url = pathToFileURL(file).href;
  const {targetId} = await cmd('Target.createTarget',{url:'about:blank',browserContextId});
  try {
    const {sessionId} = await cmd('Target.attachToTarget',{targetId,flatten:true});
    const send = (method,params)=>cmd(method,params,sessionId);
    const js = async expression => {
      const {result,exceptionDetails} = await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
      if (exceptionDetails) throw Error(exceptionDetails.exception?.description || exceptionDetails.text);
      return result.value;
    };
    await send('Runtime.enable'); await send('Page.enable'); await send('Emulation.setDeviceMetricsOverride',widthHeight(1200,900));
    const nav = await send('Page.navigate',{url}); assert(!nav.errorText,nav.errorText);
    await until(()=>js(`location.href===${quote(url)} && !!window.sheet`).catch(()=>false),name);
    await js('sheet.ready.then(()=>true)'); return await fn({js,send});
  } finally { await cmd('Target.closeTarget',{targetId}); }
}

// 견본에는 실제 빌드의 해시·풀이 자료가 없다. 교사용 원본의 복사본에만 시험 값을 붙인다.
const answers = {'q1.1':'1/2','q1.2':'서로평행','q1.3':'1','q2.1':'1,3','q2.2':'o','q2.3':'l1:r2,l2:r1',
  'q2.4':'t2,t3,t1','q3.1':'-3/2;1','q3.2':'-1,1;1,2','q3.3':'1,-1,-1','q3.4':'2,3'};
function addChecks(html) {
  const salt = html.match(/name="sheet-salt" content="([^"]+)"/)[1];
  const payload = {slots:Object.fromEntries(Object.entries(answers).map(([k,v])=>[k,{answer_html:v}])),
    items:{q1:{solution_html:'<p>0.5는 10분의 5입니다. 분자와 분모를 각각 5로 나누면 2분의 1이 됩니다.</p>'}}};
  const key = hash(`${salt}|reveal`).digest(), data = Buffer.from(quote(payload)); data.forEach((v,i)=>data[i]=v^key[i%key.length]);
  return html.replace(/data-slot="([^"]+)" data-check-kind="([^"]+)"/g,(m,s,k)=>s in answers ? `${m} data-check-hash="${hash(`${salt}|${k}|${answers[s]}`).digest('hex')}"` : m)
    .replace('id="sheet-reveal"></script>',()=>`id="sheet-reveal">${data.toString('base64')}</script>`);
}
async function fixture(name,accent) {
  let html = await read(path.join(here,`fixture-${name}.html`));
  if (name==='interactive') html = addChecks(html);
  else html = html.replace('<html ','<html data-interactive="1" '); // 1판 견본에도 풀기 배치를 적용한다.
  html = html.replace(/ data-theme="[^"]*"/,'').replace('<html ',()=>`<html data-theme="${accent?'산뜻':'정본'}" data-accent="${accent||'하늘'}" `)
    .replace(/<link id="sheet-css"[^>]*>/,()=>`<style id="sheet-css">${css}</style>${accent?`<style id="sheet-theme">${theme}</style>`:''}`)
    .replace(/<script id="sheet-js"[^>]*><\/script>/,()=>`<script id="sheet-js">${core}\n${solve}</script>`);
  return html.includes('data-role="teacher"') ? tab(html,`${name}-source`,({js})=>js('sheet.exportStudent()')) : html;
}
const settle = 'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))';
async function png(send,file,clip) {
  const {data} = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{...clip,scale:1}});
  await fs.writeFile(path.join(out,`${file}.png`),Buffer.from(data,'base64'));
}
const raster = `import sys,pathlib,pymupdf as p
file=pathlib.Path(sys.argv[1]); doc=p.open(file)
assert len(doc)==int(sys.argv[2]), (file,len(doc),sys.argv[2])
for i,page in enumerate(doc,1):
 assert abs(page.rect.width*25.4/72-210)<.35 and abs(page.rect.height*25.4/72-297)<.35
 page.get_pixmap(dpi=120).save(file.with_name(f'{file.stem}-print-{i:02d}.png'))
 if i==1 and sys.argv[3]=='yes': page.get_pixmap(dpi=120,colorspace=p.csGRAY).save(file.with_name(f'{file.stem}-gray.png'))`;
async function capture({send,js},name,accent,baseline,canRaster) {
  await js('sheet.clearAnswers(); sheet.view("a4")'); await js(settle);
  const report = await js('sheet.report()'), n = report.pages.length;
  assert.equal(report.print_leaks,0); assert.equal(report.overflow_x,0); assert.deepEqual(report.overflow,[]);
  if (!accent) return n;
  const prefix = `${name}-${accent}`;
  assert.equal(n,baseline,`${prefix}: 정본 대비 쪽 수`);
  const rects = await js(`[...document.querySelectorAll('.ws-page')].filter(e=>e.checkVisibility()).map(e=>{const r=e.getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height}})`);
  assert.equal(rects.length,n);
  for (const [i,r] of rects.entries()) await png(send,`${prefix}-a4-${String(i+1).padStart(2,'0')}`,r);
  const gray = name==='interactive' && accent==='하늘';
  const {data} = await send('Page.printToPDF',{preferCSSPageSize:true,printBackground:true,displayHeaderFooter:false});
  const file = path.join(out,`${prefix}.pdf`); await fs.writeFile(file,Buffer.from(data,'base64'));
  if (canRaster) await run(process.env.PYTHON||'python',['-I','-c',raster,file,String(n),gray?'yes':'no']);
  else if (gray) {
    await send('Emulation.setEmulatedVisionDeficiency',{type:'achromatopsia'}); await png(send,`${prefix}-gray`,rects[0]);
    await send('Emulation.setEmulatedVisionDeficiency',{type:'none'});
  }
  await js('sheet.view("solve")');
  if (name==='interactive') {
    await js(`sheet.answer('q1.1','1/2');sheet.answer('q1.2','수직');sheet.answer('q1.3','1');
      sheet.answer('q2.1',[1,3]);sheet.answer('q2.2','O');sheet.answer('q2.3',{l1:'r2',l2:'r1'});
      sheet.answer('q3.1',[-1.5,1]);sheet.answer('q3.2',[[-1,1],[1,2]]);sheet.answer('q3.3',[[0,1],[-2,-1]]);
      sheet.answer('q3.4',[2,3]);sheet.answer('q4.2',[true,false]);
      for (const a of ['check','hint','reveal-answer','reveal-solution']) document.querySelector('[data-id="q1"] [data-sheet-action="'+a+'"]').click();`);
  }
  const sizes = [];
  for (const [w,h] of [[390,844],[1024,768]]) {
    await send('Emulation.setDeviceMetricsOverride',widthHeight(w,h,w===390)); await js('window.scrollTo(0,0)'); await js(settle);
    const r = await js('sheet.report()'); assert.equal(r.view,'solve'); assert.equal(r.overflow_x,0,`${prefix} ${w}px`);
    assert(await js(`(() => {const rows=[...document.querySelectorAll('#sheet-flow .ws-head tr')];
      return rows.slice(1).every((row,i)=>rows[i].cells[0].getBoundingClientRect().bottom<=row.cells[0].getBoundingClientRect().top+1);})()`), `${prefix}: 머리표 행 겹침`);
    const fonts = await js(`[...document.querySelectorAll('#sheet-flow .ws-in')].map(e=>parseFloat(getComputedStyle(e).fontSize))`);
    if (fonts.length) assert(Math.min(...fonts)>=16);
    const {cssContentSize:s} = await send('Page.getLayoutMetrics');
    await png(send,`${prefix}-solve-${w}`,{x:0,y:0,width:w,height:Math.ceil(s.height)});
    sizes.push(`${w}px overflow_x=${r.overflow_x}`);
  }
  await js('sheet.view("a4")'); assert.equal(await js('sheet.report().print_leaks'),0,'판별·힌트·풀이를 연 뒤 A4 유출');
  console.log(`${prefix} · 정본 ${baseline} → 산뜻 ${n}쪽 · A4 print_leaks=${report.print_leaks} · ${sizes.join(' / ')}`);
}

await Promise.all([out,work].map(dir=>fs.mkdir(dir,{recursive:true})));
const canRaster = await run(process.env.PYTHON||'python',['-I','-c','import pymupdf']).then(()=>true,()=>false);
if (!canRaster) console.warn('PyMuPDF 없음: PDF와 화면 PNG만 저장하고 회색조는 Chrome에서 캡처합니다.');
try {
  await start();
  for (const [name,accents] of [['interactive',['하늘','보라']],['lesson',['민트']],['practice',['주황']]]) {
    const base = await tab(await fixture(name),`${name}-base`,t=>capture(t,name));
    for (const accent of accents) await tab(await fixture(name,accent),`${name}-theme`,t=>capture(t,name,accent,base,canRaster));
  }
  assert.deepEqual(exceptions,[]); console.log(`캡처 · ${out}`);
} finally { await stop(); }
