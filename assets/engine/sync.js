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
    notice.append(make('p', '', '이 활동지는 이름과 답을 선생님께 보냅니다.'));
    const warning = make('p', 'ws-sync-warning', '이름을 쓰면 선생님께 기록이 전송돼요');
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
            }, body: JSON.stringify(queue[0]), keepalive: true });
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
      const student = Object.fromEntries(Object.entries(values).filter(([slot]) => slot.startsWith('head.'))
        .map(([slot, value]) => [slot.slice(5), value]));
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
      queue.push(row);
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
    content.append(actions, summary, rates, tableWrap, detail);
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
    function grouped() {
      const map = new Map();
      for (const row of records) {
        if (!row.client_id) continue;
        if (!map.has(row.client_id)) map.set(row.client_id, { latest: row, submitted: false });
        if (row.event === 'submit') map.get(row.client_id).submitted = true;
      }
      return [...map.values()];
    }
    function identity(row) {
      const s = row.student || {};
      return [s['이름'] ?? s['성명'] ?? '', s['반'] ?? '', s['번'] ?? s['학번'] ?? ''].map(String);
    }
    function render() {
      const cols = columns(), students = grouped();
      summary.replaceChildren(); rates.replaceChildren(); tableWrap.replaceChildren(); detail.replaceChildren();
      for (const [label, value] of [['학생 수', students.length], ['제출 수', students.filter(s => s.submitted).length]]) {
        const box = make('div', 'ws-records-stat'); box.append(make('span', '', label), make('strong', '', String(value))); summary.append(box);
      }
      rates.append(make('h2', '', '자리별 정답률'));
      const list = make('div', 'ws-records-rate-list');
      for (const col of cols) {
        const judged = students.map(s => s.latest.verdicts?.[col.slot]).filter(v => v === true || v === false);
        const correct = judged.filter(Boolean).length;
        list.append(make('span', '', `${col.label} · ${judged.length ? Math.round(100 * correct / judged.length) + '%' : '—'} (${correct}/${judged.length})`));
      }
      if (!cols.length) list.append(make('span', '', '자동 판별 자리가 없습니다.'));
      rates.append(list);
      const table = make('table', 'ws-records-table'), thead = make('thead'), tr = make('tr');
      for (const label of ['이름', '반', '번/학번', '마지막 기록', '제출', '점수', ...cols.map(c => c.label)]) {
        const th = make('th', '', label); th.scope = 'col'; tr.append(th);
      }
      thead.append(tr); table.append(thead);
      const body = make('tbody');
      for (const student of students) {
        const row = student.latest, score = row.score || {}, line = make('tr');
        line.tabIndex = 0; line.setAttribute('role', 'button'); line.setAttribute('aria-label', `${identity(row)[0] || '이름 없음'}의 답 보기`);
        const result = cols.map(c => row.verdicts?.[c.slot] === true ? '✓' : row.verdicts?.[c.slot] === false ? '✗' : '');
        for (const value of [...identity(row), clock(row.created_at), student.submitted ? '제출' : '미제출',
          `${score.correct ?? 0}/${score.checked ?? 0}`, ...result]) line.append(make('td', '', String(value)));
        const open = () => showDetail(row, cols);
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
    function showDetail(row, cols) {
      detail.replaceChildren(make('h2', '', `${identity(row)[0] || '이름 없음'}의 답`));
      const table = make('table', 'ws-records-detail-table'), head = make('tr');
      for (const label of ['자리', '학생 답', '교사용 정답']) head.append(make('th', '', label));
      table.append(head);
      const answers = row.answers && typeof row.answers === 'object' ? row.answers : {};
      const slots = [...new Set([...window.sheet.slots().filter(s => s.kind !== 'name').map(s => s.slot), ...Object.keys(answers)])];
      for (const slot of slots) {
        const col = cols.find(c => c.slot === slot);
        const el = col?.el || [...document.querySelectorAll('[data-slot]')].find(node => node.dataset.slot === slot);
        const check = parse(el?.dataset.check, {}), line = make('tr');
        const result = make('td');
        const key = el?.querySelector('.ws-key');
        if (key && key.textContent.trim()) {
          const rendered = key.cloneNode(true); rendered.classList.remove('ws-key'); result.append(rendered);
        } else if (Object.hasOwn(check, 'right')) {
          if (typeof check.right === 'string') {
            try { result.innerHTML = core.renderMarkup(check.right); } catch { result.textContent = check.right; }
          } else result.textContent = display(check.right);
        } else result.textContent = '—';
        line.append(make('td', '', col?.label || slot), make('td', '', display(answers[slot])), result); table.append(line);
      }
      detail.append(table);
    }
    function downloadCSV() {
      const cols = columns(), students = grouped();
      const rows = [['이름', '반', '번/학번', '마지막 기록', '제출', '맞음', '판별', ...cols.map(c => c.label), '답 전체']];
      for (const { latest: row, submitted } of students) {
        const score = row.score || {};
        rows.push([...identity(row), clock(row.created_at), submitted ? '제출' : '미제출', score.correct ?? 0, score.checked ?? 0,
          ...cols.map(c => row.verdicts?.[c.slot] === true ? '✓' : row.verdicts?.[c.slot] === false ? '✗' : ''), JSON.stringify(row.answers || {})]);
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
