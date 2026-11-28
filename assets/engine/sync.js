;(() => {
  'use strict';
  const core = window.__sheetCore;
  const root = document.documentElement;
  let config;
  try { config = JSON.parse(document.querySelector('meta[name="sheet-sync"]')?.content || 'null'); } catch { return; }
  if (!core || config?.v !== 1 || !config.url || !config.key || !config.sheet || !config.title ||
      !/^[a-z_][a-z0-9_]*$/.test(config.table || '')) return;
  const base = config.url.replace(/\/+$/, ''), endpoint = `${base}/rest/v1/${config.table}`;
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  const runtime = el => { el.dataset.sheetRuntime = ''; return el; };
  const read = (storage, key, fallback = null) => {
    try { return storage.getItem(key) ?? fallback; } catch { return fallback; }
  };
  const write = (storage, key, value) => {
    try { if (value == null) storage.removeItem(key); else storage.setItem(key, value); return true; } catch { return false; }
  };
  const parse = (value, fallback) => { try { return JSON.parse(value) ?? fallback; } catch { return fallback; } };
  const local = () => { try { return window.localStorage; } catch { return null; } };
  const session = () => { try { return window.sessionStorage; } catch { return null; } };
  const apiHeaders = token => ({ apikey: config.key, ...(token ? { Authorization: `Bearer ${token}` } : {}) });
  // New publishable keys are not JWTs and must not be used as Bearer tokens.
  const anonToken = config.key.startsWith('sb_publishable_') ? null : config.key;
  const storageScope = encodeURIComponent(`${base}|${config.table}|${config.sheet}`);
  const clock = value => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  };

  function studentSync() {
    const disabled = new URLSearchParams(location.search).has('nosync') ||
      location.hash.slice(1).split(/[&?]/).some(part => part.split('=')[0] === 'nosync');
    if (disabled) return;
    const prefix = `pbj-sheet-sync:${storageScope}`;
    let client = read(local(), `${prefix}:client`);
    if (!client) {
      const bytes = new Uint8Array(16);
      if (window.crypto?.getRandomValues) window.crypto.getRandomValues(bytes);
      else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
      client = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
      write(local(), `${prefix}:client`, client);
    }
    const queueKey = `${prefix}:queue`;
    const identityFields = new Set(['학년', '학번', '이름', '성명', '반', '번', '학급', '번호']);
    function scoped(row) {
      const result = { ...row };
      if (row.student && typeof row.student === 'object') {
        result.student = Object.fromEntries(Object.entries(row.student).filter(([name]) => identityFields.has(name)));
      }
      for (const field of ['answers', 'verdicts']) {
        if (!row[field] || typeof row[field] !== 'object') continue;
        result[field] = Object.fromEntries(Object.entries(row[field]).filter(([slot]) =>
          !slot.startsWith('head.') && (row.event !== 'check' ||
            (typeof row.item === 'string' && slot.startsWith(`${row.item}.`)))));
      }
      return result;
    }
    const initial = parse(read(local(), queueKey, '[]'), []);
    const queue = Array.isArray(initial) ? initial.slice(-50) : [];
    const status = core.button('', 'sync-retry', () => void flush());
    status.classList.add('ws-sync-status'); status.hidden = true;
    status.setAttribute('aria-live', 'polite');
    const submit = core.button('선생님께 제출', 'sync-submit', () => {
      if (window.confirm('이름과 답을 선생님께 제출할까요?')) send('submit', null);
    });
    core.addToolbar(submit, status);
    const notice = runtime(make('div', 'ws-sync-notice ws-solve-only'));
    notice.append(make('p', '', "이름과 학번·반·번호 등 본인 정보와 답을 선생님께 보냅니다. 이름을 쓰고 '확인'을 누르면 그 문항의 답·판별 결과와 활동지 전체 맞힌 수가, '선생님께 제출'을 누르면 모든 답·판별 결과와 맞힌 수가 전송됩니다. 짝 칸에 쓴 정보는 보내지 않습니다."));
    const warning = make('p', 'ws-sync-warning', '이름을 먼저 써야 확인·제출할 때 기록을 보낼 수 있어요');
    warning.hidden = true; warning.setAttribute('role', 'status'); notice.append(warning);
    document.querySelector('#sheet-flow, #sheet-pages')?.before(notice);
    document.addEventListener('input', event => {
      if (event.target.closest?.('[data-slot="head.이름"], [data-slot="head.성명"]') && event.target.value.trim()) warning.hidden = true;
    });
    const persist = () => write(local(), queueKey, queue.length ? JSON.stringify(queue) : null);
    function markFailed() {
      status.textContent = '보내지 못함 — 다시 시도'; status.hidden = false;
      status.title = '누르면 대기 중인 기록을 다시 보냅니다.';
    }
    let sending;
    function flush() {
      if (sending) return sending;
      sending = (async () => {
        while (queue.length) {
          try {
            const response = await fetch(endpoint, { method: 'POST', headers: {
              ...apiHeaders(anonToken), 'Content-Type': 'application/json', Prefer: 'return=minimal',
            }, body: JSON.stringify(scoped(queue[0])), keepalive: true });
            if (!response.ok) throw new Error('전송 실패');
            queue.shift(); persist();
          } catch { markFailed(); return; }
        }
        const now = new Date();
        status.textContent = `기록: 보냄 ✓ ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
        status.title = ''; status.hidden = false;
      })().finally(() => { sending = undefined; });
      return sending;
    }
    function send(event, item) {
      const values = window.sheet.values();
      const student = Object.fromEntries(Object.entries(values).filter(([slot]) =>
        slot.startsWith('head.') && identityFields.has(slot.slice(5))).map(([slot, value]) => [slot.slice(5), value]));
      if (!String(student['이름'] ?? student['성명'] ?? '').trim()) {
        warning.hidden = false;
        if (window.sheet.view() !== 'solve') void window.sheet.view('solve');
        return;
      }
      warning.hidden = true;
      const build = parse(document.querySelector('meta[name="sheet-build"]')?.content, {}).source_sha256 || '';
      const row = {
        sheet_id: config.sheet, sheet_title: config.title, build: String(build).slice(0, 12), client_id: client,
        student, event, item, score: window.sheet.score(),
        answers: Object.fromEntries(Object.entries(values).filter(([slot]) => !slot.startsWith('head.'))),
        verdicts: core.verdicts(),
      };
      queue.push(scoped(row));
      // Do not evict the in-flight head: its successful response removes that exact row.
      if (queue.length > 50) queue.splice(sending ? 1 : 0, queue.length - 50);
      persist(); void flush();
    }
    document.addEventListener('sheet:check', event => send('check', event.detail.item));
    window.addEventListener('online', () => { if (queue.length) void flush(); });
    if (queue.length) markFailed();
  }

  function teacherPanel() {
    const tokenKey = `pbj-sheet-sync:${storageScope}:token`;
    let token = read(session(), tokenKey, ''), records = [], generation = 0;
    const panel = runtime(make('section', 'ws-records'));
    panel.hidden = true; panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', 'ws-records-title');
    const shell = make('div', 'ws-records-shell'), header = make('header', 'ws-records-header');
    const heading = make('div'), eyebrow = make('p', 'ws-records-eyebrow', config.title);
    const title = make('h1', '', '학생 기록'); title.id = 'ws-records-title'; heading.append(eyebrow, title);
    const close = core.button('닫기', 'records-close', () => { panel.hidden = true; opener.focus(); });
    header.append(heading, close);
    const message = make('p', 'ws-records-message'); message.setAttribute('role', 'status');
    const login = make('form', 'ws-records-login');
    const loginTitle = make('h2', '', '교사 로그인');
    const emailLabel = make('label', '', '이메일'), email = make('input');
    email.type = 'email'; email.autocomplete = 'username'; email.required = true; emailLabel.append(email);
    const passwordLabel = make('label', '', '비밀번호'), password = make('input');
    password.type = 'password'; password.autocomplete = 'current-password'; password.required = true; passwordLabel.append(password);
    const loginButton = make('button', 'ws-records-primary', '로그인'); loginButton.type = 'submit';
    login.append(loginTitle, emailLabel, passwordLabel, loginButton);
    const content = make('div', 'ws-records-content');
    const actions = make('div', 'ws-records-actions');
    const refresh = core.button('새로 고침', 'records-refresh', () => void load());
    const csv = core.button('CSV 내려받기', 'records-csv', downloadCSV);
    const logout = core.button('로그아웃', 'records-logout', () => {
      generation++; token = ''; records = []; write(session(), tokenKey, null);
      render(); password.value = ''; message.textContent = ''; showLogin();
    });
    actions.append(refresh, csv, logout);
    const summary = make('div', 'ws-records-summary'), rates = make('div', 'ws-records-rates');
    const tableWrap = make('div', 'ws-records-table-wrap'), detail = make('section', 'ws-records-detail');
    const groupingNote = make('small', '', '같은 이름·반·번호는 한 학생으로 묶습니다');
    content.append(actions, summary, groupingNote, rates, tableWrap, detail);
    shell.append(header, message, login, content); panel.append(shell); document.body.append(panel);
    const opener = core.button('학생 기록', 'student-records', () => {
      panel.hidden = false; message.textContent = '';
      if (token) { showContent(); void load(); } else { showLogin(); email.focus(); }
    });
    core.addToolbar(opener);
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); close.click(); }
    });
    function showLogin() { login.hidden = false; content.hidden = true; }
    function showContent() { login.hidden = true; content.hidden = false; }
    login.addEventListener('submit', async event => {
      event.preventDefault(); message.textContent = ''; loginButton.disabled = true;
      const run = ++generation;
      try {
        const response = await fetch(`${base}/auth/v1/token?grant_type=password`, { method: 'POST',
          headers: { ...apiHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email.value.trim(), password: password.value }) });
        if (run !== generation) return;
        if (!response.ok) { message.textContent = '로그인하지 못했습니다. 이메일과 비밀번호를 확인해 주세요.'; return; }
        const result = await response.json();
        if (run !== generation) return;
        if (!result.access_token) { message.textContent = '로그인하지 못했습니다. 이메일과 비밀번호를 확인해 주세요.'; return; }
        token = result.access_token; write(session(), tokenKey, token); password.value = '';
        showContent(); await load();
      } catch { if (run === generation) message.textContent = '연결하지 못했습니다. 인터넷 연결을 확인해 주세요.'; }
      finally { loginButton.disabled = false; }
    });
    async function load() {
      if (!token) return;
      const run = ++generation;
      refresh.disabled = true; message.textContent = '기록을 불러오는 중입니다.';
      try {
        const query = `?sheet_id=eq.${encodeURIComponent(config.sheet)}&order=created_at.desc&limit=5000`;
        const response = await fetch(endpoint + query, { headers: apiHeaders(token) });
        if (run !== generation) return;
        if (!response.ok) {
          message.textContent = response.status === 401 || response.status === 403
            ? '기록을 볼 권한이 없습니다. 다시 로그인하고 교사 이메일이 sheet_teachers에 등록됐는지 확인해 주세요.'
            : '기록을 불러오지 못했습니다. 연결과 설정을 확인해 주세요.';
          return;
        }
        const result = await response.json();
        if (run !== generation) return;
        if (!Array.isArray(result)) throw new Error('잘못된 응답');
        records = result; render();
        message.textContent = records.length ? '' : '기록이 없습니다. 교사 이메일이 sheet_teachers에 등록됐는지 확인해 주세요.';
      } catch { if (run === generation) message.textContent = '연결하지 못했습니다. 인터넷 연결을 확인해 주세요.'; }
      finally { refresh.disabled = false; }
    }
    function columns() {
      return window.sheet.slots().filter(slot => slot.auto).map(slot => {
        const el = [...document.querySelectorAll('[data-slot]')].find(node => node.dataset.slot === slot.slot);
        const q = el?.closest('.ws-item')?.querySelector('.ws-qn')?.textContent.trim().replace(/\.$/, '');
        return { ...slot, label: `${q || slot.item}.${slot.slot.split('.').at(-1)}`, el };
      });
    }
    function identity(row) {
      const s = row.student || {};
      return [s['이름'] ?? s['성명'] ?? '', s['반'] ?? s['학급'] ?? '', s['번'] ?? s['번호'] ?? s['학번'] ?? ''].map(String);
    }
    function grouped() {
      const map = new Map();
      const build = String(parse(document.querySelector('meta[name="sheet-build"]')?.content, {}).source_sha256 || '').slice(0, 12);
      // Postgres timestamps can carry more precision than Date's milliseconds.
      const fraction = row => (/\.(\d+)/.exec(row.created_at || '')?.[1] || '').padEnd(6, '0').slice(3);
      const ordered = records.map((row, index) => ({ row, index })).sort((a, b) =>
        (Date.parse(a.row.created_at) - Date.parse(b.row.created_at)) || fraction(a.row).localeCompare(fraction(b.row)) ||
        ((Number(a.row.id) || 0) - (Number(b.row.id) || 0)) || b.index - a.index);
      for (const [order, { row }] of ordered.entries()) {
        if (!row.client_id) continue;
        const key = JSON.stringify(identity(row).map(value => value.normalize('NFC').replace(/[\s\p{White_Space}]+/gu, ' ').trim()));
        if (!map.has(key)) map.set(key, { current: null, others: [] });
        const student = map.get(key);
        student.lastRecord = row; student.order = order;
        // Missing build metadata is not evidence that the versions match.
        if (!build || row.build !== build) { student.others.push(row); continue; }
        const current = student.current ||= { submitted: false, modified: false, answers: Object.create(null), verdicts: Object.create(null) };
        current.lastRecord = row;
        if (row.event === 'submit') {
          current.submitted = true; current.modified = false; current.submission = row;
        } else if (row.event === 'check') {
          Object.assign(current.answers, row.answers || {}); Object.assign(current.verdicts, row.verdicts || {});
          if (current.submitted) current.modified = true;
        }
      }
      return [...map.values()].sort((a, b) => b.order - a.order).map(student => {
        const current = student.current;
        if (current) current.latest = current.submitted ? current.submission :
          { ...current.lastRecord, answers: current.answers, verdicts: current.verdicts };
        student.others.reverse();
        return student;
      });
    }
    const state = student => student.current ? student.current.submitted ?
      student.current.modified ? '제출 후 수정' : '제출' : '미제출' :
      student.others.some(row => row.event === 'submit') ? '다른 판에서 제출' : '다른 판 기록만 있음';
    const otherLabel = row => `다른 판 ${typeof row.build === 'string' && row.build ? row.build.slice(0, 6) : '판 미상'} · ${clock(row.created_at) || '시각 미상'}`;
    const verdict = value => value === true ? '✓' : value === false ? '✗' : value === 'review' ? '?' : '';
    function render() {
      const cols = columns(), students = grouped();
      summary.replaceChildren(); rates.replaceChildren(); tableWrap.replaceChildren(); detail.replaceChildren();
      const devices = new Set(records.map(row => row.client_id).filter(Boolean)).size;
      for (const [label, value] of [['학생(이름 기준)', students.length], ['제출', students.filter(s => s.current?.submitted).length], ['기기', devices]]) {
        const box = make('div', 'ws-records-stat'); box.append(make('span', '', label), make('strong', '', String(value))); summary.append(box);
      }
      rates.append(make('h2', '', '자리별 정답률'));
      const list = make('div', 'ws-records-rate-list');
      for (const col of cols) {
        const values = students.map(s => s.current?.latest.verdicts?.[col.slot]);
        const judged = values.filter(v => v === true || v === false), review = values.filter(v => v === 'review').length;
        const correct = judged.filter(v => v === true).length;
        list.append(make('span', '', `${col.label} · ${judged.length ? Math.round(100 * correct / judged.length) + '%' : '—'} (${correct}/${judged.length}) · 확인 필요 ${review}`));
      }
      if (!cols.length) list.append(make('span', '', '자동 판별 자리가 없습니다.'));
      rates.append(list);
      const table = make('table', 'ws-records-table'), thead = make('thead'), tr = make('tr');
      for (const label of ['이름', '반', '번/학번', '마지막 기록', '제출', '점수', '다른 판 기록', ...cols.map(c => c.label)]) {
        const th = make('th', '', label); th.scope = 'col'; tr.append(th);
      }
      thead.append(tr); table.append(thead);
      const body = make('tbody');
      for (const student of students) {
        const row = student.current?.latest || student.lastRecord, score = row.score || {}, line = make('tr');
        line.tabIndex = 0; line.setAttribute('role', 'button'); line.setAttribute('aria-label', `${identity(row)[0] || '이름 없음'}의 답 보기`);
        const result = cols.map(c => student.current ? verdict(row.verdicts?.[c.slot]) : '');
        for (const value of [...identity(row), clock(row.created_at), state(student),
          student.current ? `${score.correct ?? 0}/${score.checked ?? 0}` : '—']) line.append(make('td', '', String(value)));
        const otherCell = make('td', '', student.others.map(otherLabel).join(' / '));
        otherCell.style.cssText = 'min-width:9em;max-width:18em;overflow-wrap:anywhere'; line.append(otherCell);
        for (const value of result) line.append(make('td', '', value));
        const open = () => showDetail(student, cols);
        line.addEventListener('click', open);
        line.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
        body.append(line);
      }
      table.append(body); tableWrap.append(table);
    }
    function display(value) {
      if (value == null || value === '') return '—';
      return typeof value === 'string' ? value : JSON.stringify(value);
    }
    const svgNode = (tag, attrs = {}) => {
      const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
      return el;
    };
    function answerPicture(value, el, sameBuild) {
      let kind = el?.dataset.checkKind;
      const strokes = value && !Array.isArray(value) && Array.isArray(value.strokes) ? value.strokes : null;
      const point = p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite);
      if (strokes) {
        if (!strokes.length || !strokes.every(path => Array.isArray(path) && path.length &&
            path.every(p => point(p) && p.every(n => n >= 0 && n <= 1)))) return null;
        let width = 1, height = 1;
        if (sameBuild) {
          const anchor = el?.matches('figure') ? el.querySelector('svg, img') : el;
          const bounds = anchor?.getBoundingClientRect();
          if (bounds?.width > 0 && bounds?.height > 0) { width = bounds.width; height = bounds.height; }
        }
        const paths = strokes.map(path => path.map(([x, y]) => [x * width, y * height]));
        const svg = fittedPicture(paths.flat(), sameBuild ? [0, 0, width, height] : null, '학생이 그린 답');
        const weight = Math.max(width, height) * .008;
        for (const path of paths) {
          if (path.length === 1) svg.append(svgNode('circle', { cx: path[0][0], cy: path[0][1], r: weight / 2, fill: '#164b86' }));
          else svg.append(svgNode('polyline', { points: path.map(p => p.join(',')).join(' '), fill: 'none',
            stroke: '#164b86', 'stroke-width': weight, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
        }
        return svg;
      }
      if (!Array.isArray(value) || !value.length) return null;
      // Two-dimensional coordinates are identifiable even if this slot no longer exists,
      // but without its old metadata we cannot tell points from a line.
      if (!sameBuild && !['points', 'line'].includes(kind) && value.every(point)) kind = 'points';
      if (!['points', 'line'].includes(kind)) return null;
      const numberline = value.every(Number.isFinite);
      if ((!numberline && !value.every(point)) || (sameBuild && el?.matches('.ws-nl') !== numberline)) return null;
      if (kind === 'line' && (numberline || value.length !== 2)) return null;
      const points = numberline ? value.map(x => [x, 0]) : value;
      const source = sameBuild ? el?.querySelector('svg[data-map]') : null;
      const map = source?.dataset.map?.trim().split(/\s+/).map(Number);
      let svg, projected, weight;
      if (map?.every(Number.isFinite) && numberline && map.length === 5 && map[4] > 0) {
        svg = plotBackground(source); projected = points.map(([x]) => [map[2] + (x - map[0]) * map[4], map[3]]); weight = 1.15;
      } else if (map?.every(Number.isFinite) && !numberline && map.length === 7 && map[6] > 0 && map[1] > map[0] && map[3] > map[2]) {
        svg = plotBackground(source); projected = points.map(([x, y]) => [map[4] + (x - map[0]) * map[6], map[5] + (map[3] - y) * map[6]]); weight = 1.15;
        if (kind === 'line') {
          const [a, b] = points, dx = b[0] - a[0], dy = b[1] - a[1];
          let lo = -Infinity, hi = Infinity;
          for (const [v, d, min, max] of [[a[0], dx, map[0], map[1]], [a[1], dy, map[2], map[3]]]) {
            if (!d) { if (v < min || v > max) lo = Infinity; continue; }
            const t = [(min - v) / d, (max - v) / d]; lo = Math.max(lo, Math.min(...t)); hi = Math.min(hi, Math.max(...t));
          }
          if ((dx || dy) && lo <= hi) {
            const ends = [lo, hi].map(t => [map[4] + (a[0] + dx * t - map[0]) * map[6], map[5] + (map[3] - a[1] - dy * t) * map[6]]);
            svg.append(svgNode('line', { x1: ends[0][0], y1: ends[0][1], x2: ends[1][0], y2: ends[1][1], stroke: '#164b86', 'stroke-width': .6 }));
          }
        }
      } else {
        projected = points.map(([x, y]) => [x, -y]);
        svg = fittedPicture(projected, null, kind === 'line' ? '학생이 그린 직선' : '학생이 찍은 점');
        const box = svg.viewBox.baseVal; weight = Math.max(box.width, box.height) * .018;
        if (kind === 'line') svg.append(svgNode('line', { x1: projected[0][0], y1: projected[0][1], x2: projected[1][0], y2: projected[1][1], stroke: '#164b86', 'stroke-width': weight / 2 }));
      }
      for (const [cx, cy] of projected) svg.append(svgNode('circle', { cx, cy, r: weight, fill: '#164b86' }));
      return svg;
    }
    function fittedPicture(points, box, label) {
      if (!box) {
        const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
        const left = Math.min(...xs), top = Math.min(...ys), width = Math.max(...xs) - left, height = Math.max(...ys) - top;
        const pad = Math.max(width, height, .1) * .1;
        box = [left - pad, top - pad, width + pad * 2, height + pad * 2];
      }
      const svg = svgNode('svg', { viewBox: box.join(' '), role: 'img', 'aria-label': label });
      svg.style.cssText = 'display:block;width:200px;max-width:100%;height:auto;border:1px solid #888;background:white';
      return svg;
    }
    function plotBackground(source) {
      const svg = source.cloneNode(true);
      for (const node of svg.querySelectorAll('.ws-key, .ws-plot, [data-sheet-runtime]')) node.remove();
      svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '학생이 표시한 답');
      svg.style.cssText = 'display:block;width:200px;max-width:100%;height:auto;background:white';
      return svg;
    }
    function showDetail(student, cols) {
      const row = student.current?.latest || student.lastRecord;
      detail.replaceChildren(make('h2', '', `${identity(row)[0] || '이름 없음'}의 답`));
      if (student.current) {
        if (student.current.modified) detail.append(make('p', '', `제출 뒤 바뀐 답이 있습니다(마지막 기록 시각: ${clock(student.current.lastRecord.created_at)})`));
        answerTable(row, cols, true);
      }
      for (const other of student.others) {
        detail.append(make('h3', '', `${otherLabel(other)} · ${other.event === 'submit' ? '제출' : '문항 확인'}`));
        answerTable(other, cols, false);
      }
    }
    function answerTable(row, cols, sameBuild) {
      const table = make('table', 'ws-records-detail-table'), head = make('tr');
      for (const label of sameBuild ? ['자리', '학생 답', '교사용 정답'] : ['자리', '학생 답']) head.append(make('th', '', label));
      table.append(head);
      const answers = row.answers && typeof row.answers === 'object' ? row.answers : {};
      const slots = sameBuild ? [...new Set([...window.sheet.slots().filter(s => s.kind !== 'name').map(s => s.slot), ...Object.keys(answers)])] : Object.keys(answers);
      for (const slot of slots) {
        const col = cols.find(c => c.slot === slot);
        const el = col?.el || [...document.querySelectorAll('[data-slot]')].find(node => node.dataset.slot === slot);
        const line = make('tr'), answer = make('td');
        const picture = answerPicture(answers[slot], el, sameBuild);
        if (picture) {
          answer.append(picture);
          if (!sameBuild && Array.isArray(answers[slot])) {
            const note = ['points', 'line'].includes(el?.dataset.checkKind) ?
              '현재 자리 종류를 기준으로 그린 그림입니다.' : '저장된 좌표를 점으로 표시했습니다. 직선 여부는 알 수 없습니다.';
            answer.append(make('small', '', note));
            const raw = make('details'); raw.append(make('summary', '', '저장된 답 보기'), make('code', '', display(answers[slot]))); answer.append(raw);
          }
        } else if (Array.isArray(answers[slot]?.strokes) && !answers[slot].strokes.length) answer.textContent = '—';
        else answer.textContent = display(answers[slot]);
        line.append(make('td', '', sameBuild ? col?.label || slot : slot), answer);
        if (sameBuild) {
          const check = parse(el?.dataset.check, {}), result = make('td'), key = el?.querySelector('.ws-key');
          if (key && key.textContent.trim()) {
            const rendered = key.cloneNode(true); rendered.classList.remove('ws-key'); result.append(rendered);
          } else if (Object.hasOwn(check, 'right')) {
            if (typeof check.right === 'string') {
              try { result.innerHTML = core.renderMarkup(check.right); } catch { result.textContent = check.right; }
            } else result.textContent = display(check.right);
          } else result.textContent = '—';
          line.append(result);
        }
        table.append(line);
      }
      detail.append(table);
    }
    function downloadCSV() {
      const cols = columns(), students = grouped();
      const rows = [['이름', '반', '번/학번', '판', '마지막 기록', '제출', '맞음', '판별', ...cols.map(c => c.label), '답 전체']];
      for (const student of students) {
        if (student.current) {
          const row = student.current.latest, score = row.score || {};
          rows.push([...identity(row), row.build, clock(row.created_at), state(student), score.correct ?? 0, score.checked ?? 0,
            ...cols.map(c => verdict(row.verdicts?.[c.slot])), JSON.stringify(row.answers || {})]);
        }
        for (const row of student.others) rows.push([...identity(row), row.build || '판 미상', clock(row.created_at),
          row.event === 'submit' ? '다른 판에서 제출' : '다른 판 확인', '', '', ...cols.map(() => ''), JSON.stringify(row.answers || {})]);
      }
      const cell = value => {
        let text = String(value ?? '');
        if (/^[\s\u0000-\u001f]*[=+\-@]/.test(text)) text = "'" + text;
        return '"' + text.replaceAll('"', '""') + '"';
      };
      const blob = new Blob(['﻿' + rows.map(row => row.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob), link = make('a');
      link.href = url; link.download = '학생 기록.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
    showLogin();
  }
  window.sheet.ready.then(() => {
    if (root.dataset.role === 'teacher') teacherPanel();
    else if (root.dataset.role === 'student') studentSync();
  }).catch(() => {});
})();
