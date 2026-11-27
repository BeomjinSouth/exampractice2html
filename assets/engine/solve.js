;(() => {
  'use strict';
  const core = window.__sheetCore;
  if (!core) return;
  const root = document.documentElement, teacher = root.dataset.role === 'teacher';
  const all = (node, selector) => [...node.querySelectorAll(selector)];
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const runtime = node => { node.setAttribute('data-sheet-runtime', ''); return node; };
  const svgNode = (tag, attrs) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  const json = (text, fallback) => { try { return JSON.parse(text) ?? fallback; } catch { return fallback; } };
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  const clone = value => structuredClone(value);
  const textValue = v => typeof v === 'string' || Number.isFinite(v);
  const units = 'cm2 km2 cm3 마리 개월 시간 그루 송이 cm mm km kg ml m2 m3 원 개 명 권 장 쪽 번 회 배 살 세 점 일 년 분 초 시 도 칸 줄 대 m g l ° %'.split(' ');
  const norm = s => String(s).normalize('NFKC').replace(/[−‒–—―]/g, '-').replace(/⁄/g, '/')
    .replace(/[\p{White_Space}\x1c-\x1f]/gu, '').toLowerCase().replace(/[.。]+$/u, '');
  const abs = n => n < 0n ? -n : n;
  function gcd(a, b) {
    a = abs(a); b = abs(b);
    while (b) [a, b] = [b, a % b];
    return a;
  }
  function fraction(p, q) {
    if (!q) return null;
    const d = gcd(p, q);
    return q === d ? String(p / d) : `${p / d}/${q / d}`;
  }
  function num(s) {
    if (!textValue(s)) return null;
    let t = norm(s).replace(/^[a-z]=/, '');
    const unit = units.find(u => t.length > u.length && t.endsWith(u));
    if (unit) t = t.slice(0, -unit.length);
    if (t.startsWith('(') && t.endsWith(')')) t = t.slice(1, -1);
    t = t.replace(/^\+/, '');
    if (/^-?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?$/.test(t)) t = t.replaceAll(',', '');
    if (/^-?[0-9]+$/.test(t)) return String(BigInt(t));
    if (/^-?[0-9]+\.[0-9]+$/.test(t)) return fraction(BigInt(t.replace('.', '')), 10n ** BigInt(t.split('.')[1].length));
    if (/^-?[0-9]+\/[0-9]+$/.test(t)) return fraction(...t.split('/').map(BigInt));
    return null;
  }
  const symbols = { times: '×', div: '÷', le: '≤', ge: '≥', ne: '≠', pi: 'π', circ: '°', angle: '∠',
    triangle: '△', square: '□', perp: '⊥', parallel: '⫽', equiv: '≡', sim: '∽', pm: '±', approx: '≈',
    cdot: '⋅', therefore: '∴', because: '∵', cdots: '⋯' };
  function plain(source) {
    let i = 0;
    function group() {
      if (source[i] !== '{') return null;
      i++;
      return read(true);
    }
    function read(nested = false) {
      let out = '';
      while (i < source.length) {
        if (source.startsWith('**', i)) { i += 2; continue; }
        const degree = /^\^(?:\{\\circ\s*\}|\\circ(?![a-zA-Z])\s*)/.exec(source.slice(i));
        if (degree) { i += degree[0].length; out += '°'; continue; }
        const c = source[i++];
        if (c === '}' && nested) break;
        if (c === '{') { out += read(true); continue; }
        if (c === '$' || c === '}' || c === '~') continue;
        if (c !== '\\') { out += c; continue; }
        const command = /^[a-zA-Z]+\s*/.exec(source.slice(i));
        if (!command) {
          const literal = source[i++];
          if (literal && '$}{\\'.includes(literal)) out += literal;
          continue;
        }
        i += command[0].length;
        const name = command[0].trimEnd();
        if (name === 'frac' || name === 'dfrac') {
          const a = group();
          while (source[i] && /\s/u.test(source[i])) i++;
          const b = group();
          out += (a ?? '') + (b == null ? '' : '/' + b);
        } else if (name === 'sqrt') out += '√' + (group() ?? '');
        else if (Object.hasOwn(symbols, name)) out += symbols[name];
      }
      return out;
    }
    return read();
  }
  const snapValue = (v, snap) => Number((Math.round(v / snap) * snap).toFixed(6));
  const numericPart = (v, snap) => typeof v === 'number' && snap ? num(snapValue(v, snap)) : num(v);
  function points(value, snap) {
    if (!Array.isArray(value) || !value.length) return null;
    const normalized = value.map(point => {
      if (!Array.isArray(point)) return numericPart(point, snap);
      const coords = point.map(v => numericPart(v, snap));
      return coords.length === 2 && coords.every(v => v != null) ? coords.join(',') : null;
    });
    return normalized.every(v => v != null) ? normalized.sort().join(';') : null;
  }
  function line(value, snap) {
    if (!Array.isArray(value) || value.length !== 2 || !value.every(p => Array.isArray(p) && p.length === 2)) return null;
    const parts = value.flat().map(v => numericPart(v, snap));
    if (parts.some(p => p == null)) return null;
    const rationals = parts.map(s => { const [p, q = 1n] = s.split('/').map(BigInt); return [p, q]; });
    const d = rationals.reduce((n, [, q]) => n * q / gcd(n, q), 1n);
    const [x1, y1, x2, y2] = rationals.map(([p, q]) => p * (d / q));
    const a = y2 - y1, b = x1 - x2, c = a * x1 + b * y1;
    if (!a && !b) return null;
    const div = gcd(gcd(d * a, d * b), c) * ((a || b) < 0n ? -1n : 1n);
    return [d * a, d * b, c].map(n => n / div).join(',');
  }

  // FIPS 180-4 SHA-256. Byte output is reused as the reveal keystream.
  const K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
  ];
  const utf8 = new TextEncoder(), decoder = new TextDecoder('utf-8', { fatal: true });
  const rot = (v, s) => (v >>> s) | (v << (32 - s));
  function sha256(text) {
    const bytes = utf8.encode(text), padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
    padded.set(bytes); padded[bytes.length] = 128;
    const data = new DataView(padded.buffer);
    data.setBigUint64(padded.length - 8, BigInt(bytes.length) * 8n);
    const state = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
    const w = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i++) w[i] = data.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        w[i] = w[i - 16] + (rot(a, 7) ^ rot(a, 18) ^ (a >>> 3)) + w[i - 7] + (rot(b, 17) ^ rot(b, 19) ^ (b >>> 10));
      }
      let [a,b,c,d,e,f,g,h] = state;
      for (let i = 0; i < 64; i++) {
        const t1 = h + (rot(e, 6) ^ rot(e, 11) ^ rot(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        const t2 = (rot(a, 2) ^ rot(a, 13) ^ rot(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        [a,b,c,d,e,f,g,h] = [(t1 + t2) | 0,a,b,c,(d + t1) | 0,e,f,g];
      }
      [a,b,c,d,e,f,g,h].forEach((v, i) => { state[i] += v; });
    }
    const out = new Uint8Array(32), dv = new DataView(out.buffer);
    state.forEach((v, i) => dv.setUint32(i * 4, v));
    return out;
  }
  const hex = text => Array.from(sha256(text), b => b.toString(16).padStart(2, '0')).join('');
  const hash = (salt, kind, value) => hex(`${salt}|${kind}|${value}`);
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  function base64(bytes) {
    const chunks = [];
    for (let i = 0; i < bytes.length; i += 3) {
      const a = bytes[i], b = bytes[i + 1], c = bytes[i + 2];
      chunks.push(alphabet[a >> 2] + alphabet[((a & 3) << 4) | (b >> 4)] +
        (b == null ? '=' : alphabet[((b & 15) << 2) | (c >> 6)]) + (c == null ? '=' : alphabet[c & 63]));
    }
    return chunks.join('');
  }
  function unbase64(text) {
    text = text.replace(/\s/g, '');
    if (text.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) throw new Error('정답 자료의 형식이 올바르지 않습니다.');
    const out = new Uint8Array(text.length / 4 * 3 - (text.endsWith('==') ? 2 : Number(text.endsWith('='))));
    for (let i = 0, at = 0; i < text.length; i += 4) {
      const a = alphabet.indexOf(text[i]), b = alphabet.indexOf(text[i + 1]);
      const c = alphabet.indexOf(text[i + 2]), d = alphabet.indexOf(text[i + 3]);
      out[at++] = (a << 2) | (b >> 4);
      if (c >= 0) out[at++] = (b << 4) | (c >> 2);
      if (d >= 0) out[at++] = (c << 6) | d;
    }
    return out;
  }
  const xor = (salt, bytes) => {
    const key = sha256(`${salt}|reveal`);
    return bytes.map((byte, i) => byte ^ key[i & 31]);
  };
  const encodeReveal = (salt, data) => base64(xor(salt, utf8.encode(JSON.stringify(data))));
  const decodeReveal = (salt, text) => JSON.parse(decoder.decode(xor(salt, unbase64(text))));
  // 계약 시험용 내부 표면. window.sheet의 학생 API와 분리한다.
  core.codec = Object.freeze({ norm, num, plain, line, points, sha256: hex, hash, encodeReveal, decodeReveal });

  const salt = () => document.querySelector('meta[name="sheet-salt"]')?.content || '';
  const policy = root.dataset.reveal || '시도 후';
  const canReveal = policy === '시도 후' || policy === '바로';
  const automatic = new Set(['number','text','choice','ox','order','match','points','line','bars']);
  const slotsIn = item => all(item, '[data-slot]').filter(el => !el.closest('.ws-key, .ws-teacher-only'));
  const recordByNode = new WeakMap(), panels = new WeakMap(), itemReveal = new WeakMap(), slotReveal = new WeakMap();
  const originalAttrs = new WeakMap(), exploreOriginal = new WeakMap(), mediaBound = new WeakSet();
  const records = new Map(), dash = new Set();
  let stored = {}, storedPens = {}, initialReveal = {}, storageKey, mode = 'a4', preview = false, previousAnswers;
  let scoreLabel, viewButton, previewButton, returnButton, printing, mainNode;
  let delayedPaint;
  const store = (op, ...args) => { try { return window.localStorage[op](...args); } catch { return undefined; } };
  const usable = () => !teacher || preview;
  const solveOnly = (tag, cls, text) => runtime(make(tag, `${cls} ws-solve-only`, text));
  const uiButton = (label, action, fn, options) => core.button(label, action, event => { if (usable()) fn(event); }, options);
  function saveValues() {
    if (!storageKey) return;
    store('setItem', storageKey, JSON.stringify(values()));
    const pens = Object.fromEntries([...records.values()].filter(r => r.penValue !== undefined).map(r => [r.slot, r.penValue]));
    if (Object.keys(pens).length) store('setItem', `${storageKey}:pen`, JSON.stringify(pens));
    else store('removeItem', `${storageKey}:pen`);
  }
  function attr(el, name, value) {
    let saved = originalAttrs.get(el);
    if (!saved) originalAttrs.set(el, saved = new Map());
    if (!saved.has(name)) saved.set(name, el.getAttribute(name));
    if (value == null) el.removeAttribute(name); else el.setAttribute(name, value);
  }
  function matchTokens(el, side) { return all(el, `.ws-match-${side} > li[data-token]`).map(li => li.dataset.token); }
  const barHits = el => all(el, '.ws-bar-hit[data-i]');
  const snapOf = el => Number.isFinite(+el.dataset.snap) && +el.dataset.snap > 0 ? +el.dataset.snap : 1;
  const maxPoints = el => Math.max(1, Number(el.dataset.max) || (el.dataset.checkKind === 'line' ? 2 : Infinity));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function normalize(kind, value, el) {
    const snap = el ? snapOf(el) : undefined;
    switch (kind) {
      case 'number': return num(value);
      case 'text': return textValue(value) ? norm(value) || null : null;
      case 'choice': return Array.isArray(value) && value.length && value.every(Number.isSafeInteger) ? [...new Set(value)].sort((a,b) => a-b).join(',') : null;
      case 'ox': return /^[o○]$/.test(norm(value)) ? 'o' : /^[x×]$/.test(norm(value)) ? 'x' : null;
      case 'order': return Array.isArray(value) && value.length ? value.join(',') : null;
      case 'match': {
        const left = matchTokens(el, 'l');
        return left.length && left.every(k => typeof value?.[k] === 'string') ? left.map(k => `${k}:${value[k]}`).join(',') : null;
      }
      case 'points': return points(value, snap);
      case 'line': return line(value, snap);
      case 'bars': {
        const parts = Array.isArray(value) ? value.map(v => numericPart(v, snap)) : [];
        return parts.length && parts.every(v => v != null) ? parts.join(',') : null;
      }
      default: return null;
    }
  }
  function check(record) {
    if (!record.auto) return null;
    const value = record.value ?? record.control?.initial;
    const normalized = normalize(record.kind, value, record.el);
    const incomplete = value == null || value === '' || (Array.isArray(value) && !value.length) ||
      (normalized == null && ['match','line','bars'].includes(record.kind));
    if (incomplete) return null;
    return normalized != null && record.hashes.includes(hash(salt(), record.kind, normalized));
  }
  function sanitizedValue(record, value) {
    const { el, kind } = record;
    if (el.matches('.ws-blank,.ws-name-slot,.ws-ans:not([data-kind="draw"])')) {
      if (!textValue(value)) return undefined;
      return el.matches('.ws-ans') ? String(value).replace(/\r\n?/g, '\n') : String(value).replace(/[\r\n]/g, '');
    }
    if (kind === 'ox') return value === '' ? '' : normalize(kind, value, el)?.toUpperCase();
    if (kind === 'draw') {
      const strokes = value?.strokes;
      if (!Array.isArray(strokes) || !strokes.every(path => Array.isArray(path) && path.length && path.every(p =>
        Array.isArray(p) && p.length === 2 && p.every(n => Number.isFinite(n) && n >= 0 && n <= 1)))) return undefined;
      return { strokes: clone(strokes) };
    }
    if (kind === 'match') {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
      const right = matchTokens(el, 'r');
      return Object.fromEntries(matchTokens(el, 'l').filter(k => right.includes(value[k])).map(k => [k, value[k]]));
    }
    if (!Array.isArray(value)) return undefined;
    if (kind === 'choice') {
      const allowed = all(el, ':scope > li[data-n]').map(li => +li.dataset.n);
      return [...new Set(value.filter(n => allowed.includes(n)))].slice(0, el.dataset.multi === '1' ? allowed.length : 1);
    }
    if (kind === 'self') return all(el, 'ul > li').map((_, i) => value[i] === true);
    if (kind === 'order') {
      const tokens = all(el, '.ws-order-list > li[data-token]').map(li => li.dataset.token);
      return value.length === tokens.length && new Set(value).size === tokens.length && value.every(v => tokens.includes(v)) ? [...value] : undefined;
    }
    if (kind === 'bars') {
      const snap = snapOf(el), count = barHits(el).length;
      if (value.length !== count || !value.every(v => v == null || Number.isFinite(v))) return undefined;
      return value.map(v => v == null ? null : snapValue(v, snap));
    }
    if (kind === 'points' || kind === 'line') {
      const nl = el.matches('.ws-nl'), snap = snapOf(el);
      const valid = v => nl ? Number.isFinite(v) : Array.isArray(v) && v.length === 2 && v.every(Number.isFinite);
      return value.length <= maxPoints(el) && value.every(valid) ? value.map(v => nl ? snapValue(v, snap) : v.map(n => snapValue(n, snap))) : undefined;
    }
    return undefined;
  }
  function indexSlots() {
    records.clear();
    for (const item of core.items()) {
      if (item.matches('.ws-teacher-only')) continue;
      if (!itemReveal.has(item)) itemReveal.set(item, Object.hasOwn(initialReveal.items ?? {}, item.dataset.id) ? initialReveal.items[item.dataset.id] : undefined);
      let i = 0;
      for (const el of slotsIn(item)) {
        const old = el.dataset.slot, kind = el.dataset.checkKind || 'open';
        let row = recordByNode.get(el);
        if (!row) {
          row = { el, item, kind, result: null };
          const embedded = json(el.dataset.value, undefined);
          row.value = sanitizedValue(row, embedded ?? (Object.hasOwn(stored, old) ? stored[old] : undefined));
          if (el.matches('figure[data-pen="1"]') && ['points', 'line'].includes(kind)) {
            const ink = json(el.dataset.penValue, undefined) ?? storedPens[old];
            row.penValue = sanitizedValue({ el, kind: 'draw' }, ink);
          }
          recordByNode.set(el, row);
        }
        if (!slotReveal.has(el)) slotReveal.set(el, Object.hasOwn(initialReveal.slots ?? {}, old) ? initialReveal.slots[old] : undefined);
        if (item.dataset.id === 'head' && old.startsWith('head.')) row.slot = old;
        else row.slot = el.dataset.slot = `${item.dataset.id}.${++i}`;
        row.kind = kind;
        row.hashes = (el.dataset.checkHash || '').split(',').filter(Boolean);
        row.auto = policy !== '없음' && automatic.has(kind) && row.hashes.length > 0;
        if (!row.auto) row.result = null;
        records.set(row.slot, row);
      }
    }
  }
  const values = () => Object.fromEntries([...records.values()].filter(r => r.value !== undefined).map(r => [r.slot, clone(r.value)]));
  const slots = () => [...records.values()].map(({ slot, kind, item, auto }) => ({ slot, kind, item: item.dataset.id, auto }));
  function score() {
    const rows = [...records.values()].filter(r => r.auto);
    return { checked: rows.filter(r => r.result != null).length, correct: rows.filter(r => r.result === true).length, total: rows.length };
  }
  function setValue(row, value, evaluate = false) {
    const next = sanitizedValue(row, value);
    if (next === undefined) return null;
    if (!same(next, row.value)) { row.value = next; row.result = null; saveValues(); }
    if (evaluate) row.result = check(row);
    if (usable()) {
      row.control?.paint(row.value);
      updateRow(row);
      updatePanel(row.item);
      updateScore();
    }
    return row.result;
  }
  const answer = (slot, value) => records.has(slot) ? setValue(records.get(slot), value, true) : null;
  function clearAnswers() {
    stored = {}; storedPens = {};
    for (const row of records.values()) {
      row.value = undefined; row.penValue = undefined; row.result = null;
      delete row.el.dataset.value; delete row.el.dataset.penValue;
      row.control?.reset?.();
      row.control?.paint(undefined); updateRow(row);
    }
    for (const item of core.items()) {
      const panel = panels.get(item);
      if (panel) {
        Object.assign(panel, { attempted: false, answers: false, solution: false, comparing: false, hintCount: 0 });
        if (panel.solutionBox) { stopVideo(panel.solutionBox); panel.solutionBox.remove(); panel.solutionBox = undefined; }
        if (panel.compareBox) { panel.compareBox.remove(); panel.compareBox = undefined; }
        updatePanel(item);
      }
    }
    if (storageKey) { store('removeItem', storageKey); store('removeItem', `${storageKey}:pen`); }
    updateScore();
  }

  // Input devices update the same state as sheet.answer(), but never grade on input.
  function toggle(el, cls, on) {
    const classes = new Set(el.classList);
    if (on) classes.add(cls); else classes.delete(cls);
    attr(el, 'class', [...classes].join(' ') || null);
  }
  function activate(el, fn, role = 'button') {
    attr(el, 'tabindex', '0'); attr(el, 'role', role);
    el.addEventListener('click', event => { if (usable()) fn(event); });
    el.addEventListener('keydown', event => {
      if (!usable() || event.target !== el || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      if (!event.repeat) fn(event);
    });
  }
  function picked(el, on, aria = 'aria-pressed') {
    toggle(el, 'ws-picked', on);
    attr(el, aria, String(on));
  }
  function field(row) {
    const { el, kind } = row, multiline = el.matches('.ws-ans');
    const input = runtime(make(multiline ? 'textarea' : 'input', 'ws-in'));
    const ink = runtime(make('span', 'ws-ink ws-print-only'));
    ink.setAttribute('aria-hidden', 'true');
    if (!multiline) input.type = 'text';
    const label = el.closest('tr')?.querySelector('th.ws-lab, th.ws-step-n')?.textContent.trim();
    input.setAttribute('aria-label', kind === 'name' ? row.slot.split('.').at(-1) : label ||
      (multiline ? '답 쓰기' : `빈칸 ${el.querySelector('.ws-blank-mark')?.textContent || row.slot}`));
    input.autocomplete = 'off';
    if (kind === 'number') input.spellcheck = false;
    if (el.matches('.ws-blank') && el.style.minWidth) {
      const style = make('span');
      style.setAttribute('style', el.getAttribute('style'));
      style.style.setProperty('--ws-blank-min', el.style.minWidth);
      attr(el, 'style', style.getAttribute('style'));
    }
    el.append(input, ink);
    input.addEventListener('input', () => setValue(row, input.value));
    return { paint(value) {
      const text = value ?? '';
      if (input.value !== text) input.value = text;
      if (multiline && el.style.height) input.style.setProperty('--ws-input-height', el.style.height);
      ink.textContent = text;
      toggle(el, 'ws-filled', text.length > 0);
    } };
  }
  function choices(row) {
    const { el } = row, multi = el.dataset.multi === '1';
    const options = all(el, ':scope > li[data-n]');
    for (const li of options) activate(li, () => {
      const n = +li.dataset.n, previous = row.value ?? [];
      setValue(row, previous.includes(n) ? previous.filter(v => v !== n) : multi ? [...previous, n].sort((a,b) => a-b) : [n]);
    });
    return { paint(value) { for (const li of options) picked(li, !!value?.includes(+li.dataset.n)); } };
  }
  function oxChoice(row) {
    const buttons = ['O','X'].map(v => [v, row.el.querySelector(`.ws-ox-${v.toLowerCase()}`)]).filter(([, el]) => el);
    for (const [v, el] of buttons) activate(el, () => setValue(row, row.value === v ? '' : v));
    return { paint(value) { for (const [v, el] of buttons) picked(el, v === value); } };
  }
  function checklist(row) {
    const options = all(row.el, 'ul > li');
    options.forEach((el, i) => activate(el, () => {
      const values = options.map((_, j) => !!row.value?.[j]);
      values[i] = !values[i]; setValue(row, values);
    }, 'checkbox'));
    return { paint(value) { options.forEach((el, i) => picked(el, !!value?.[i], 'aria-checked')); } };
  }
  function match(row) {
    const { el } = row, cols = el.querySelector('.ws-match-cols');
    const left = all(el, '.ws-match-l > li[data-token]'), right = all(el, '.ws-match-r > li[data-token]');
    if (!cols) return;
    const svg = runtime(svgNode('svg', { class: 'ws-match-lines', 'aria-hidden': 'true' }));
    cols.append(svg);
    let pending;
    function paint(value = {}) {
      const box = cols.getBoundingClientRect();
      svg.replaceChildren();
      if (box.width && box.height) svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
      for (const a of left) {
        const b = right.find(b => b.dataset.token === value[a.dataset.token]);
        picked(a, !!b || pending === a);
        if (!b || !box.width) continue;
        const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
        svg.append(svgNode('line', { x1: x.right - box.left, x2: y.left - box.left,
          y1: x.top + x.height / 2 - box.top, y2: y.top + y.height / 2 - box.top }));
      }
      for (const b of right) picked(b, Object.values(value).includes(b.dataset.token));
    }
    for (const a of left) activate(a, () => {
      const pairs = { ...row.value };
      if (Object.hasOwn(pairs, a.dataset.token)) {
        delete pairs[a.dataset.token]; pending = undefined; setValue(row, pairs);
      } else { pending = pending === a ? undefined : a; paint(pairs); }
    });
    for (const b of right) activate(b, () => {
      const pairs = { ...row.value }, token = b.dataset.token;
      const previous = left.find(a => pairs[a.dataset.token] === token);
      if (previous) delete pairs[previous.dataset.token];
      if (pending) pairs[pending.dataset.token] = token;
      pending = undefined; setValue(row, pairs);
    });
    return { paint, reset() { pending = undefined; }, resize: true };
  }
  function order(row) {
    const list = row.el.querySelector('.ws-order-list');
    if (!list) return;
    const original = all(list, ':scope > li[data-token]'), initial = original.map(el => el.dataset.token);
    let drag;
    const controls = new Map();
    const shift = (token, index, button) => {
      const current = [...(row.value ?? initial)], from = current.indexOf(token);
      const to = clamp(index, 0, current.length - 1);
      if (from === to || from < 0) return;
      current.splice(from, 1); current.splice(to, 0, token); setValue(row, current);
      if (button) (button.disabled ? controls.get(token).find(b => !b.disabled) : button)?.focus({ preventScroll: true });
    };
    for (const li of original) {
      const token = li.dataset.token, box = li.querySelector('.ws-order-box');
      const actions = solveOnly('span', 'ws-order-tools');
      const label = li.querySelector('[data-src]')?.textContent || li.querySelector('.ws-order-box + span')?.textContent || token;
      const buttons = [-1, 1].map(dir => {
        const button = uiButton(dir < 0 ? '↑' : '↓', dir < 0 ? 'order-up' : 'order-down', () =>
          shift(token, (row.value ?? initial).indexOf(token) + dir, button), { title: `${label} ${dir < 0 ? '위로' : '아래로'} 이동` });
        return button;
      });
      actions.append(...buttons); li.append(actions); controls.set(token, buttons);
      if (box) box.append(runtime(make('span', 'ws-order-rank')));
      const handle = solveOnly('span', 'ws-order-grip', '⠿');
      handle.setAttribute('aria-hidden', 'true'); li.prepend(handle);
      handle.addEventListener('pointerdown', event => {
        if (!usable() || event.button !== 0 || drag) return;
        drag = { id: event.pointerId, y: event.clientY };
        if (event.isTrusted) handle.setPointerCapture(event.pointerId);
      });
      for (const type of ['pointerup','pointercancel','lostpointercapture']) handle.addEventListener(type, event => {
        if (drag?.id !== event.pointerId) return;
        const moved = Math.abs(event.clientY - drag.y) > 4;
        drag = undefined;
        if (type !== 'pointerup' || !moved || mode !== 'solve') return;
        const peer = document.elementFromPoint(event.clientX, event.clientY)?.closest('li[data-token]');
        if (peer?.parentElement === list) shift(token, (row.value ?? initial).indexOf(peer.dataset.token));
      });
    }
    function arrange(ordering) {
      const sorted = ordering.map(t => original[initial.indexOf(t)]);
      sorted.forEach((li, i) => { if (list.children[i] !== li) list.insertBefore(li, list.children[i] || null); });
    }
    return { initial, arrange, paint(value) {
      const current = value ?? initial;
      arrange(mode === 'solve' ? current : initial);
      for (const li of original) {
        const at = current.indexOf(li.dataset.token), rank = li.querySelector('.ws-order-rank');
        if (rank) rank.textContent = value || mode === 'solve' ? String(at + 1) : '';
        const [up, down] = controls.get(li.dataset.token);
        up.disabled = at <= 0; down.disabled = at >= initial.length - 1;
      }
    } };
  }

  function svgPoint(svg, event) {
    const matrix = svg.getScreenCTM();
    return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : null;
  }
  function readMap(svg, key, size) {
    const vals = (svg?.dataset[key] || '').trim().split(/\s+/).map(Number);
    return vals.length === size && vals.every(Number.isFinite) ? vals : null;
  }
  function gridMap(svg) {
    const parts = readMap(svg, 'map', 7);
    if (!parts) return null;
    const [x0,x1,y0,y1,left,top,unit] = parts;
    return unit > 0 && x1 > x0 && y1 > y0 ? {
      x0,x1,y0,y1,left,top,unit,
      x: x => left + (x - x0) * unit, y: y => top + (y1 - y) * unit,
    } : null;
  }
  // Clip a line (or a sampled function segment) to the plotting rectangle, not to the labels.
  function clipSegment([x1, y1], [x2, y2], map, extend = false) {
    const dx = x2 - x1, dy = y2 - y1;
    if (!dx && !dy) return null;
    let lo = extend ? -Infinity : 0, hi = extend ? Infinity : 1;
    for (const [v, d, min, max] of [[x1, dx, map.x0, map.x1], [y1, dy, map.y0, map.y1]]) {
      if (!d) { if (v < min || v > max) return null; continue; }
      const a = (min - v) / d, b = (max - v) / d;
      lo = Math.max(lo, Math.min(a,b)); hi = Math.min(hi, Math.max(a,b));
    }
    return lo > hi ? null : [lo, hi].map(t => [map.x(x1 + dx*t), map.y(y1 + dy*t)]);
  }
  function plotting(row) {
    const { el, kind } = row, svg = el.querySelector('svg[data-map]'), plot = svg?.querySelector('.ws-plot');
    const nl = el.matches('.ws-nl'), snap = snapOf(el);
    const snapped = (value, low, high) => snapValue(clamp(snapValue(value, snap), Math.ceil(low/snap)*snap, Math.floor(high/snap)*snap), snap);
    const nlMap = nl && readMap(svg, 'map', 5), map = !nl && gridMap(svg);
    if (!plot || !(nl ? nlMap && nlMap[4] > 0 : map)) return;
    const put = point => {
      const values = [...(row.value ?? [])], index = values.findIndex(v => same(v, point));
      if (index >= 0) values.splice(index, 1);
      else if (values.length < maxPoints(el)) values.push(point);
      else return;
      setValue(row, values);
    };
    svg.addEventListener('click', event => {
      if (!usable()) return;
      const p = svgPoint(svg, event);
      if (!p) return;
      if (nl) {
        const [a,b,left,,unit] = nlMap;
        put(snapped(a + (p.x - left) / unit, a, b));
      } else put([snapped(map.x0 + (p.x-map.left)/map.unit, map.x0, map.x1),
        snapped(map.y1 - (p.y-map.top)/map.unit, map.y0, map.y1)]);
    });
    return { paint(value = []) {
      for (const node of all(plot, '[data-sheet-runtime]')) node.remove();
      if (kind === 'line' && value.length === 2) {
        const segment = clipSegment(value[0], value[1], map, true);
        if (segment) {
          const [[x1,y1],[x2,y2]] = segment;
          plot.append(runtime(svgNode('line', { class: 'ws-user-line', x1,y1,x2,y2 })));
        }
      }
      for (const point of value) {
        const [a,,left,y,unit] = nlMap || [];
        const [cx,cy] = nl ? [left + (point - a) * unit, y] : [map.x(point[0]), map.y(point[1])];
        plot.append(runtime(svgNode('circle', { class: 'ws-user-point', cx,cy,r: 1.15 })));
      }
    } };
  }
  function bars(row) {
    const el = row.el, svg = el.querySelector('svg[data-ymap]'), plot = svg?.querySelector('.ws-plot');
    const map = readMap(svg, 'ymap', 4), hits = barHits(el), snap = snapOf(el);
    if (!plot || !map || map[1] <= map[0] || map[2] <= map[3]) return;
    const [y0,y1,bottom,top] = map;
    const toSvg = value => bottom - (clamp(value, y0, y1) - y0) / (y1 - y0) * (bottom - top);
    for (const hit of hits) hit.addEventListener('click', event => {
      if (!usable()) return;
      const p = svgPoint(svg, event);
      if (!p) return;
      const v = snapValue(clamp(y0 + (bottom - p.y) / (bottom - top) * (y1 - y0), y0, y1), snap);
      const current = [...(row.value ?? hits.map(() => null))], i = +hit.dataset.i;
      current[i] = v === current[i] ? null : v;
      setValue(row, current);
    });
    return { paint(value = []) {
      for (const node of all(plot, '[data-sheet-runtime]')) node.remove();
      for (const hit of hits) {
        const v = value[+hit.dataset.i];
        if (v == null) continue;
        const width = Number(hit.getAttribute('width')), pad = Math.min(.6, width * .05);
        const baseline = toSvg(0), valueY = toSvg(v);
        const y = Math.min(baseline, valueY);
        const mark = runtime(svgNode('rect', { class: 'ws-user-bar', x: Number(hit.getAttribute('x')) + pad,
          y, width: width - 2 * pad, height: Math.abs(baseline - valueY) }));
        mark.append(svgNode('title', {})); mark.firstChild.textContent = String(v);
        plot.append(mark);
      }
    } };
  }

  // The CSS-sized canvas does not take space; normalized points survive reflow and printing.
  function pen(row, alongsidePlot = false) {
    const el = row.el, figure = el.matches('figure');
    // Keep plot values unchanged; the accompanying pen is stored separately.
    const drawing = () => alongsidePlot ? row.penValue : row.value;
    const setDrawing = strokes => {
      if (alongsidePlot) { row.penValue = { strokes }; saveValues(); paint(row.penValue); }
      else setValue(row, { strokes });
    };
    const anchor = figure ? el.querySelector('svg:not(.ws-match-lines), img') : el;
    if (!anchor) return;
    const canvas = runtime(make('canvas', 'ws-pen'));
    canvas.setAttribute('aria-label', '그리기 칸');
    el.append(canvas);
    const tools = solveOnly('div', 'ws-pen-tools');
    let selected = alongsidePlot ? 'plot' : 'ink', gesture;
    const modes = [];
    function tool(which) {
      selected = which;
      canvas.classList.toggle('ws-pen-pass-through', which === 'plot');
      for (const [key, button] of modes) button.setAttribute('aria-pressed', String(key === which));
    }
    if (alongsidePlot) modes.push(['plot', uiButton('점 찍기', 'pen-plot', () => tool('plot'))]);
    modes.push(['ink', uiButton('펜', 'pen', () => tool('ink'))], ['erase', uiButton('지우개', 'eraser', () => tool('erase'))]);
    tool(selected);
    tools.append(...modes.map(([,button]) => button), uiButton('모두 지우기', 'pen-clear', () => setDrawing([])));
    el.append(tools);
    function paint(value) {
      if (figure) {
        const f = el.getBoundingClientRect(), r = anchor.getBoundingClientRect();
        if (!r.width || !r.height) return;
        Object.assign(canvas.style, { left: `${r.left-f.left}px`, top: `${r.top-f.top}px`, width: `${r.width}px`, height: `${r.height}px` });
      }
      const { width, height } = canvas.getBoundingClientRect();
      if (!width || !height) return;
      const ratio = Math.min(3, Math.max(2, devicePixelRatio || 1));
      const w = Math.round(width * ratio), h = Math.round(height * ratio);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0,0,w,h); ctx.lineWidth = 1.8 * ratio; ctx.lineCap = ctx.lineJoin = 'round'; ctx.strokeStyle = '#161616';
      for (const path of value?.strokes ?? []) {
        const [[x,y], ...rest] = path;
        ctx.beginPath(); ctx.moveTo(x*w,y*h);
        if (!rest.length) ctx.lineTo(x*w+.01,y*h);
        for (const [a,b] of rest) ctx.lineTo(a*w,b*h);
        ctx.stroke();
      }
    }
    const point = event => {
      const r = canvas.getBoundingClientRect();
      return [clamp((event.clientX-r.left)/r.width,0,1), clamp((event.clientY-r.top)/r.height,0,1)].map(v => Number(v.toFixed(5)));
    };
    function eraseAt(strokes, points) {
      const { width:w, height:h } = canvas.getBoundingClientRect();
      const [p,q] = points.map(([x,y]) => [x*w, y*h]);
      const distance = (a,b,c) => {
        const dx = c[0]-b[0], dy = c[1]-b[1];
        const t = clamp(((a[0]-b[0])*dx + (a[1]-b[1])*dy)/(dx*dx+dy*dy || 1),0,1);
        return Math.hypot(a[0]-b[0]-dx*t,a[1]-b[1]-dy*t);
      };
      const count = Math.max(1, Math.ceil(Math.hypot(q[0]-p[0], q[1]-p[1])/6));
      const samples = Array.from({length:count+1}, (_,i) => [p[0]+(q[0]-p[0])*i/count,p[1]+(q[1]-p[1])*i/count]);
      return strokes.filter(path => !path.some(([x,y],i) => {
        const [a,b] = path[i-1] || path[i];
        return samples.some(sample => distance(sample,[a*w,b*h],[x*w,y*h]) < 9);
      }));
    }
    canvas.addEventListener('pointerdown', event => {
      if (!usable() || event.button !== 0 || gesture || selected === 'plot') return;
      event.preventDefault();
      const p = point(event), erasing = selected === 'erase';
      let strokes = clone(drawing()?.strokes ?? []);
      if (erasing) strokes = eraseAt(strokes,[p,p]); else strokes.push([p]);
      gesture = { id:event.pointerId, strokes, erasing, last:p };
      if (event.isTrusted) canvas.setPointerCapture(event.pointerId);
      paint(gesture);
    });
    function move(event) {
      if (gesture?.id !== event.pointerId) return;
      const samples = event.getCoalescedEvents?.();
      for (const sample of samples?.length ? samples : [event]) {
        const p = point(sample);
        if (same(p, gesture.last)) continue;
        if (gesture.erasing) gesture.strokes = eraseAt(gesture.strokes,[gesture.last,p]);
        else gesture.strokes.at(-1).push(p);
        gesture.last = p;
      }
      paint(gesture);
    }
    canvas.addEventListener('pointermove', move);
    for (const type of ['pointerup','pointercancel','lostpointercapture']) canvas.addEventListener(type, event => {
      if (gesture?.id !== event.pointerId) return;
      if (type === 'pointerup') move(event);
      const { strokes } = gesture; gesture = undefined; setDrawing(strokes);
    });
    return { paint: value => paint(alongsidePlot && usable() ? row.penValue : value), resize: true,
      reset() { gesture = undefined; tool(alongsidePlot ? 'plot' : 'ink'); } };
  }
  function installControl(row) {
    if (row.control) return;
    const { el, kind } = row;
    if (kind === 'draw') row.control = pen(row);
    else if (el.matches('.ws-blank,.ws-name-slot,.ws-ans')) row.control = field(row);
    else {
      const controls = { choice: choices, ox: oxChoice, match, order, points: plotting, line: plotting, bars, self: checklist };
      if (Object.hasOwn(controls, kind)) row.control = controls[kind](row);
      if (row.control && ['line','points'].includes(kind) && el.matches('figure[data-pen="1"]')) {
        const plot = row.control, sketch = pen(row, true);
        if (sketch) row.control = { resize: true, reset: sketch.reset,
          paint(value) { plot.paint(value); sketch.paint(); } };
      }
    }
    if (row.control) {
      toggle(el, 'ws-answer-live', true);
      if (row.control.resize) resizeObserver.observe(el);
    }
  }

  function auxiliary(row, node) {
    const el = row.el;
    if (el.matches('.ws-ox-row')) (el.querySelector('.ws-ox-text') || el.lastElementChild)?.append(node);
    else if (el.matches('.ws-blank,.ws-name-slot,.ws-choices')) el.after(node);
    else el.append(node);
  }
  function updateRow(row) {
    const active = usable(), ok = active && row.result === true, no = active && row.result === false;
    toggle(row.el, 'ws-ok', ok); toggle(row.el, 'ws-no', no);
    if ((ok || no) && !row.verdict) {
      row.verdict = solveOnly(row.el.matches('.ws-blank,.ws-name-slot') ? 'span' : 'div', 'ws-verdict');
      row.verdict.setAttribute('role', 'status');
      auxiliary(row, row.verdict);
    }
    if (row.verdict) {
      row.verdict.hidden = !(ok || no);
      row.verdict.classList.toggle('ws-ok', ok);
      row.verdict.classList.toggle('ws-no', no);
      const message = ok ? '✓' : no ? '다시 생각해 봐요' : '';
      if (row.verdict.textContent !== message) row.verdict.textContent = message;
      if (ok) row.verdict.setAttribute('aria-label', '맞았어요');
      else row.verdict.removeAttribute('aria-label');
    }
  }
  function putHtml(el, html) {
    // This is teacher-authored MathML/markup from the same worksheet, never a student value.
    el.innerHTML = html;
  }
  function setReveal(row, on) {
    const html = slotReveal.get(row.el)?.answer_html;
    if (on && html && !row.reveal) {
      row.reveal = solveOnly(row.el.matches('.ws-blank,.ws-name-slot') ? 'span' : 'div', 'ws-reveal');
      putHtml(row.reveal, html); auxiliary(row, row.reveal);
    }
    if (row.reveal) row.reveal.hidden = !(on && html);
  }
  function appendRubric(box, rubric) {
    if (!Array.isArray(rubric) || !rubric.length) return;
    const list = make('ul', 'ws-compare-list');
    for (const text of rubric) {
      const entry = make('li'), check = make('input'), label = make('label');
      check.type = 'checkbox'; label.append(check, document.createTextNode(String(text))); entry.append(label); list.append(entry);
    }
    box.append(list);
  }
  function bindVideo(box, data, title = '영상') {
    if (!/^[\w-]{11}$/.test(data?.yt ?? '')) return;
    const play = uiButton('영상 재생', 'play-video', () => {
      if (mode !== 'solve' || box.querySelector('iframe')) return;
      const iframe = runtime(make('iframe', 'ws-solve-only'));
      iframe.title = title;
      iframe.allow = 'autoplay; encrypted-media; picture-in-picture';
      iframe.allowFullscreen = true;
      const start = Math.max(0, Math.floor(Number(data.start) || 0));
      // Split the domain literal so the inline engine itself has no external resource URL.
      iframe.src = `https:${'//'}www.youtube-nocookie.com/embed/${data.yt}?autoplay=1&start=${start}`;
      play.hidden = true;
      box.append(iframe);
    });
    play.classList.add('ws-video-play'); box.append(runtime(play));
  }
  function stopVideo(scope) {
    for (const iframe of all(scope, 'iframe[data-sheet-runtime]')) {
      const play = iframe.parentElement.querySelector('.ws-video-play');
      if (play) play.hidden = false;
      iframe.remove();
    }
    for (const video of all(scope, 'video')) video.pause();
  }
  function ensurePanel(item, rows) {
    const old = panels.get(item), reveal = canReveal ? itemReveal.get(item) : undefined;
    if (old && rows.length === old.rows.length && rows.every((r, i) => r === old.rows[i] && r.kind === old.kinds[i] && r.auto === old.auto[i])) return;
    old?.bar?.remove(); old?.solutionBox?.remove(); old?.compareBox?.remove();
    const hints = all(item, '.ws-hint');
    const hasAnswers = canReveal && rows.some(r => r.kind !== 'open' && slotReveal.get(r.el)?.answer_html);
    const hasCompare = canReveal && rows.some(r => r.kind === 'open' && slotReveal.get(r.el)?.answer_html);
    const hasSolution = canReveal && !!(reveal?.solution_html || reveal?.video || reveal?.rubric?.length);
    const hasAuto = rows.some(r => r.auto);
    const panel = { rows, kinds: rows.map(r => r.kind), auto: rows.map(r => r.auto), hints,
      attempted: old?.attempted ?? false, hintCount: old?.hintCount ?? 0 };
    panels.set(item, panel);
    if (!(hints.length || hasAnswers || hasCompare || hasSolution || hasAuto)) return;
    const bar = panel.bar = solveOnly('div', 'ws-check-bar');
    bar.setAttribute('role', 'group'); bar.setAttribute('aria-label', '문항 풀이 도구');
    if (hasAuto || (policy === '시도 후' && (hasAnswers || hasSolution))) {
      bar.append(uiButton('확인', 'check', () => {
        panel.attempted = true;
        for (const row of panel.rows) { row.result = check(row); updateRow(row); }
        updatePanel(item); updateScore();
        document.dispatchEvent(new CustomEvent('sheet:check', { detail: { item: item.dataset.id } }));
      }));
    }
    const toggleButton = (label, action, property) => uiButton(label, action, () => {
      panel[property] = !panel[property]; updatePanel(item);
    }, { pressed: false });
    if (hasAnswers) bar.append(panel.answerButton = toggleButton('정답 보기', 'reveal-answer', 'answers'));
    if (hasSolution) bar.append(panel.solutionButton = toggleButton('풀이 보기', 'reveal-solution', 'solution'));
    if (hasCompare) bar.append(panel.compareButton = toggleButton('예시 답안과 비교', 'compare', 'comparing'));
    if (hints.length) bar.append(panel.hintButton = uiButton('힌트 1', 'hint', () => {
      panel.hintCount++; updatePanel(item);
    }));
    if (hasAuto) {
      panel.result = make('span', 'ws-item-score');
      panel.result.setAttribute('role', 'status'); bar.append(panel.result);
    }
    item.append(bar);
  }
  function updatePanel(item) {
    const panel = panels.get(item);
    if (!panel) return;
    const active = usable(), unlocked = active && (policy === '바로' || panel.attempted);
    const setButton = (button, enabled, pressed) => {
      if (!button) return;
      button.disabled = !enabled;
      button.setAttribute('aria-pressed', String(enabled && !!pressed));
    };
    setButton(panel.answerButton, unlocked, panel.answers);
    setButton(panel.solutionButton, unlocked, panel.solution);
    const open = panel.rows.filter(r => r.kind === 'open' && slotReveal.get(r.el)?.answer_html);
    const eligible = open.filter(r => Array.from(String(r.value ?? '').trim()).length >= 5);
    setButton(panel.compareButton, active && !!eligible.length, panel.comparing);
    for (const row of panel.rows) setReveal(row, row.kind === 'open'
      ? !!(active && panel.compareButton && panel.comparing && eligible.includes(row)) : !!(unlocked && panel.answers));
    const compare = !!(active && panel.compareButton && panel.comparing && eligible.length);
    if (compare && itemReveal.get(item)?.rubric?.length && !panel.compareBox) {
      panel.compareBox = solveOnly('div', 'ws-solution ws-compare');
      appendRubric(panel.compareBox, itemReveal.get(item).rubric); item.append(panel.compareBox);
    }
    if (panel.compareBox) panel.compareBox.hidden = !compare;
    const show = !!(unlocked && panel.solutionButton && panel.solution);
    if (show && !panel.solutionBox) {
      const reveal = itemReveal.get(item), box = panel.solutionBox = solveOnly('div', 'ws-solution');
      if (reveal.solution_html) putHtml(box, reveal.solution_html);
      appendRubric(box, reveal.rubric);
      if (reveal.video) {
        const frame = make('div', 'ws-video-frame'); bindVideo(frame, reveal.video, '풀이 영상'); box.append(frame);
      }
      item.append(box);
    }
    if (panel.solutionBox) {
      if (!show && !panel.solutionBox.hidden) stopVideo(panel.solutionBox);
      panel.solutionBox.hidden = !show;
    }
    panel.hints.forEach((hint, i) => attr(hint, 'hidden', active && i < panel.hintCount ? null : ''));
    if (panel.hintButton) {
      panel.hintButton.textContent = `힌트 ${Math.min(panel.hints.length, panel.hintCount + 1)}`;
      panel.hintButton.disabled = panel.hintCount >= panel.hints.length;
    }
    if (panel.result) {
      const auto = panel.rows.filter(row => row.auto);
      const label = auto.some(row => row.result != null) || panel.attempted ? `${auto.filter(row => row.result === true).length}/${auto.length} 맞음` : '';
      if (panel.result.textContent !== label) panel.result.textContent = label;
    }
  }
  function updateScore() {
    if (!scoreLabel) return;
    const { correct, total } = score(), label = `맞힌 수 ${correct} / 자동 판별 ${total}`;
    if (scoreLabel.textContent !== label) scoreLabel.textContent = label;
  }

  // Arithmetic-only recursive descent. Exponentiation is right-associative; unary minus binds less tightly.
  function parseExpression(source, names) {
    let pos = 0;
    const fail = () => { throw new Error('탐구 식을 읽을 수 없습니다.'); };
    if (source.length > 512) fail();
    const consume = re => {
      const match = re.exec(source.slice(pos));
      if (!match) return null;
      pos += match[0].length; return match[1];
    };
    function atom() {
      if (consume(/^\s*(\()/)) {
        const result = sum(); if (!consume(/^\s*(\))/)) fail(); return result;
      }
      const numeral = consume(/^\s*(\d+(?:\.\d*)?|\.\d+)/);
      if (numeral != null) return () => Number(numeral);
      const name = consume(/^\s*([a-z])/);
      if (name && names.has(name)) return scope => scope[name];
      return fail();
    }
    function unary() {
      if (consume(/^\s*(-)/)) { const rhs = unary(); return scope => -rhs(scope); }
      const lhs = atom();
      if (!consume(/^\s*(\*\*)/)) return lhs;
      const rhs = unary(); return scope => lhs(scope) ** rhs(scope);
    }
    function combine(parse, operators, operations) {
      let result = parse();
      for (let op; (op = consume(operators));) {
        const lhs = result, rhs = parse(), fn = operations[op];
        result = scope => fn(lhs(scope), rhs(scope));
      }
      return result;
    }
    const product = () => combine(unary, /^\s*(\*(?!\*)|\/)/, { '*': (a,b) => a*b, '/': (a,b) => a/b });
    const sum = () => combine(product, /^\s*([+-])/, { '+': (a,b) => a+b, '-': (a,b) => a-b });
    const run = sum();
    if (source.slice(pos).trim()) fail();
    return run;
  }
  const nfmt = n => String(Number(n.toPrecision(12))).replace(/-/g, '−');
  function explore(block) {
    if (exploreOriginal.has(block)) return;
    const params = json(block.dataset.params, []), cells = all(block, '[data-y-for]');
    const svg = block.querySelector('svg[data-map]'), path = svg?.querySelector('.ws-plot > path'), map = gridMap(svg);
    if (!Array.isArray(params) || !params.length || !map || !path) return;
    const names = new Set(['x']);
    for (const p of params) {
      if (!/^[a-z]$/.test(p.name) || names.has(p.name) || ![p.min,p.max,p.step,p.value].every(Number.isFinite) || p.min >= p.max || p.step <= 0) return;
      names.add(p.name);
    }
    let fn;
    try { fn = parseExpression(block.dataset.fn || '', names); } catch { return; }
    const scope = Object.create(null), tool = solveOnly('div', 'ws-explore-ctrl');
    const pathD = path.getAttribute('d'), initial = cells.map(el => el.innerHTML);
    const restoredHtml = new Map(cells.map((el, i) => [el, initial[i]]));
    const reset = () => {
      if (pathD == null) path.removeAttribute('d'); else path.setAttribute('d', pathD);
      cells.forEach((cell, i) => { if (cell.innerHTML !== initial[i]) cell.innerHTML = initial[i]; });
    };
    function render() {
      if (mode !== 'solve') { reset(); return; }
      const compute = x => { scope.x = x; return fn(scope); };
      let last, lastEnd;
      const d = [], length = 320;
      for (let i = 0; i <= length; i++) {
        const x = map.x0 + (map.x1-map.x0)*i/length, y = compute(x);
        const finite = Number.isFinite(y) && Math.abs(y) < 1e12;
        const line = finite && last && Math.abs(y - last[1]) <= 2 * (map.y1-map.y0) && clipSegment(last,[x,y],map);
        if (line) {
          const [a,b] = line.map(point => point.map(v => Number(v.toFixed(5))).join(' '));
          if (lastEnd !== a) d.push(`M${a}`);
          d.push(`L${b}`); lastEnd = b;
        } else lastEnd = undefined;
        last = finite ? [x,y] : undefined;
      }
      path.setAttribute('d', d.join(' '));
      for (const cell of cells) {
        const value = compute(Number(cell.dataset.yFor));
        cell.textContent = Number.isFinite(value) ? nfmt(value) : '—';
      }
    }
    for (const p of params) {
      const label = make('label'), input = make('input'), out = make('output');
      input.type = 'range'; input.min = p.min; input.max = p.max; input.step = p.step; input.value = p.value;
      scope[p.name] = input.valueAsNumber;
      const name = p.label || p.name;
      input.setAttribute('aria-label', name);
      out.value = nfmt(scope[p.name]);
      input.addEventListener('input', () => { scope[p.name] = input.valueAsNumber; out.value = nfmt(scope[p.name]); render(); });
      label.append(make('span', '', name), input, out); tool.append(label);
    }
    const prompt = block.querySelector('.ws-explore-prompt');
    if (prompt) prompt.after(tool); else block.prepend(tool);
    exploreOriginal.set(block, { render, path, pathD, restoredHtml });
  }

  function convertColumns(item, flow, available) {
    for (const table of all(item, 'table')) {
      const cols = all(table, ':scope > colgroup > col');
      const widths = cols.map(el => {
        const saved = originalAttrs.get(el);
        if (saved?.has('style')) {
          const probe = make('col'); probe.setAttribute('style', saved.get('style') || '');
          return probe.style.width;
        }
        return el.style.width;
      });
      const fixed = widths.map(w => /^\d+(?:\.\d+)?mm$/.test(w) ? parseFloat(w) : 0);
      const sum = fixed.reduce((a,b) => a+b,0), flexible = fixed.some(w => !w);
      const denominator = flexible ? Math.max(sum, available) : sum;
      cols.forEach((el,i) => {
        if (!fixed[i] || !denominator) return;
        const save = originalAttrs.get(el);
        if (!flow) {
          if (save?.has('style')) {
            const original = save.get('style');
            if (original == null) el.removeAttribute('style'); else el.setAttribute('style', original);
          }
        } else {
          const style = make('col'); style.setAttribute('style', save?.get('style') ?? el.getAttribute('style') ?? '');
          style.style.width = `${fixed[i] / denominator * 100}%`;
          attr(el, 'style', style.getAttribute('style'));
        }
      });
    }
  }
  function adjustColumns(item) {
    const wide = root.dataset.columns !== '2' || item.dataset.span === 'all';
    const area = wide ? 188 : 93.8;
    convertColumns(item, mode === 'solve', area);
    for (const split of all(item, '.ws-split')) {
      // An unspecified <col> is the remaining width of its original A4 split column.
      const a = +split.style.getPropertyValue('--split-l') || 1, b = +split.style.getPropertyValue('--split-r') || 1;
      [...split.children].filter(el => el.matches('.ws-split-col')).forEach((col,i) => convertColumns(col, mode === 'solve', (area-5)*(i ? b : a)/(a+b)));
    }
  }
  function renderExisting(items) {
    for (const item of items) {
      adjustColumns(item);
      for (const row of slotsIn(item).map(el => recordByNode.get(el))) {
        if (row?.kind === 'order') row.control?.paint(usable() ? row.value : undefined);
      }
      for (const block of all(item, '.ws-explore')) exploreOriginal.get(block)?.render();
    }
  }
  function layoutFlow(main, items) {
    mainNode = main;
    renderExisting(items);
    if (mode !== 'solve') return false;
    main.id = 'sheet-flow';
    if (root.dataset.columns === '2') {
      const columns = make('div', 'ws-flow-cols');
      // Each partition retains document order; the breakpoint only changes its presentation.
      const ordinary = items.filter(item => item.dataset.span !== 'all');
      const explicit = ordinary.findIndex((item, i) => i > 0 && item.dataset.break === '1');
      const boundary = explicit > 0 ? explicit : Math.ceil(ordinary.length / 2);
      for (const partition of [ordinary.slice(0,boundary), ordinary.slice(boundary)]) {
        if (!partition.length) continue;
        const col = make('div', 'ws-flow-col'); col.append(...partition); columns.append(col);
      }
      main.append(...items.filter(item => item.dataset.span === 'all'), columns);
    } else main.append(...items);
    return true;
  }
  function afterLayout() {
    if (!storageKey) return; // first A4 font/layout pass runs before extension initialization
    indexSlots();
    const rowsByItem = Map.groupBy(records.values(), row => row.item);
    for (const item of core.items()) {
      const rows = rowsByItem.get(item) ?? [];
      if (usable()) ensurePanel(item, rows);
      for (const row of rows) {
        if (usable()) installControl(row);
        row.control?.paint(usable() ? row.value : undefined);
        updateRow(row);
      }
      for (const el of all(item, '[role][tabindex]')) {
        if (originalAttrs.get(el)?.has('tabindex')) el.tabIndex = usable() ? 0 : -1;
      }
      updatePanel(item);
      if (!usable()) continue;
      for (const block of all(item, '.ws-explore')) { explore(block); exploreOriginal.get(block)?.render(); }
      for (const video of all(item, '.ws-video[data-yt]')) {
        const frame = video.querySelector('.ws-video-frame');
        if (!frame || mediaBound.has(frame)) continue;
        bindVideo(frame, video.dataset, video.querySelector('figcaption')?.textContent.trim()); mediaBound.add(frame);
      }
    }
    updateScore();
  }
  function schedulePaint() {
    if (delayedPaint) return;
    delayedPaint = requestAnimationFrame(() => {
      delayedPaint = null;
      if (usable()) for (const row of records.values()) if (row.control?.resize) row.control.paint(row.value);
    });
  }
  const resizeObserver = new ResizeObserver(schedulePaint);
  function syncToolbar() {
    if (viewButton) {
      viewButton.textContent = mode === 'solve' ? 'A4 보기' : '풀기 보기';
      viewButton.setAttribute('aria-pressed', String(mode === 'a4'));
    }
    if (previewButton) previewButton.hidden = preview;
    if (returnButton) returnButton.hidden = !preview;
  }
  function changeView(next) {
    if (mode === next) return;
    if (next === 'a4' && mainNode) stopVideo(mainNode);
    mode = root.dataset.view = next;
    syncToolbar(); core.layout();
  }
  function teacherPreview(on) {
    if (!teacher || on === preview) return;
    if (on) {
      core.stopEditing(); previousAnswers = root.getAttribute('data-answers'); root.dataset.answers = 'off';
    } else if (previousAnswers == null) delete root.dataset.answers;
    else root.dataset.answers = previousAnswers;
    preview = on;
    root.toggleAttribute('data-sheet-preview', on);
    core.syncAnswerButton(); syncToolbar();
  }
  function wideScreen() { return window.matchMedia('(min-width: 900px)').matches; }
  function view(next) {
    if (next === undefined) return mode;
    if (next !== 'a4' && next !== 'solve') return Promise.reject(new TypeError('보기는 a4 또는 solve입니다.'));
    return window.sheet.ready.then(async () => {
      if (!core.commitDraft()) return mode;
      if (next === 'solve') teacherPreview(true);
      changeView(next);
      if (!teacher && printing == null && wideScreen()) store('setItem', `${storageKey}:view`, next);
      await core.refreshFontLayout();
      return mode;
    });
  }
  function restorePrint() {
    if (printing == null) return;
    const before = printing; printing = undefined;
    changeView(before);
  }
  function beforePrintButton() { printing ??= mode; return restorePrint; }
  function init() {
    const build = json(document.querySelector('meta[name="sheet-build"]')?.content, {});
    storageKey = `pbj-sheet:${build.source_sha256 || salt() || location.pathname}:${teacher ? 'teacher' : 'student'}`;
    stored = json(store('getItem', storageKey), {});
    storedPens = json(store('getItem', `${storageKey}:pen`), {});
    const content = document.getElementById('sheet-reveal')?.textContent.trim();
    if (canReveal && content) {
      try { initialReveal = decodeReveal(salt(), content) || {}; } catch { /* input and hashing are still available */ }
    }
    scoreLabel = solveOnly('span', 'ws-score'); scoreLabel.setAttribute('role', 'status');
    if (teacher) {
      previewButton = core.button('학생 화면 미리보기', 'preview-student', () => {
        if (!core.commitDraft()) return;
        teacherPreview(true); changeView('solve'); void core.refreshFontLayout();
      });
      returnButton = core.button('교사 화면으로', 'return-teacher', () => {
        teacherPreview(false);
        if (mode !== 'a4') changeView('a4'); else core.layout();
        void core.refreshFontLayout();
      }, { });
      core.addToolbar(previewButton, returnButton);
    } else {
      viewButton = core.button('A4 보기', 'view', () => void view(mode === 'solve' ? 'a4' : 'solve'));
      const download = core.button('답을 넣어 저장', 'save-answers', () =>
        core.download(core.serialize(true, true), core.fileName(true).replace(/\.html$/, ' (내 답).html')));
      core.addToolbar(viewButton, download, core.button('내 답 지우기', 'clear-answers', () => {
        if (window.confirm('입력한 답을 모두 지울까요?')) clearAnswers();
      }));
    }
    core.addToolbar(scoreLabel);
    window.addEventListener('beforeprint', () => { printing ??= mode; changeView('a4'); });
    window.addEventListener('afterprint', restorePrint);
    const initial = !teacher && !(wideScreen() && store('getItem', `${storageKey}:view`) === 'a4') ? 'solve' : 'a4';
    syncToolbar();
    if (initial !== mode) changeView(initial); else core.layout();
  }
  function report() {
    const visible = el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
    return { view: mode, interactive: true, slots_total: records.size,
      print_leaks: mode === 'a4' ? all(document, '.ws-solve-only, .ws-verdict, .ws-check-bar, .ws-reveal, .ws-solution').filter(visible).length : 0 };
  }

  function changedAnswer(el, source, html, options, blank) {
    const answer = plain(source).trim(), accept = options.accept ?? [];
    const mode = options.mode || 'auto';
    const number = num(answer);
    let kind = 'open';
    if (answer && mode !== 'off') {
      if (mode !== 'text' && number != null) kind = 'number';
      else if (mode === 'text' || (mode === 'auto' && (blank ? Array.from(answer).length <= 12 : accept.length))) kind = 'text';
    }
    const canonical = value => normalize(kind, value, el);
    const valid = new Set([answer, ...accept.map(plain)].map(canonical).filter(v => v != null));
    const right = canonical(answer);
    const attrs = { 'data-check-kind': kind, 'data-check-hash': null, 'data-check': null };
    if (right != null && salt()) {
      let wrong;
      if (kind === 'number') {
        const [p, q = 1n] = right.split('/').map(BigInt);
        let next = p;
        do { next += q; wrong = fraction(next, q); } while (valid.has(canonical(wrong)));
      } else {
        const letters = Array.from(answer); letters.pop();
        wrong = `${letters.join('')}가`;
        while (valid.has(canonical(wrong))) wrong += '가';
      }
      attrs['data-check'] = JSON.stringify({ mode, accept, right: answer, wrong });
      if (policy !== '없음') attrs['data-check-hash'] = [...valid].map(v => hash(salt(),kind,v)).join(',');
    } else if (mode !== 'auto' || accept.length) {
      // Keep an explicit setting (notably off) so another edit does not turn grading back on.
      attrs['data-check'] = JSON.stringify({ mode, accept });
    }
    return { attrs, reveal: html && answer ? { answer_html: html } : undefined };
  }
  function applyAttrs(el, attrs) {
    for (const [name,value] of Object.entries(attrs)) {
      if (value == null) el.removeAttribute(name); else el.setAttribute(name, value);
    }
  }
  function editMarkup(target, source, html, blanks) {
    const item = target.closest('.ws-item');
    if (!item || !core.items().includes(item)) return null;
    const oldSrc = target.dataset.src, oldChildren = [...target.childNodes];
    const staged = make('template'); staged.innerHTML = html;
    const newChildren = [...staged.content.childNodes];
    const oldBlanks = all(target, '.ws-blank[data-slot]'), newBlanks = all(staged.content, '.ws-blank');
    const writing = target.closest('.ws-ans[data-slot]');
    const key = target.closest('.ws-key');
    const isAnswer = writing?.matches(':not([data-kind="draw"])') && !!key;
    if (!key) {
      for (const [i,el] of newBlanks.entries()) {
        el.dataset.slot = ''; // assigned after the edited nodes are inserted
        const { answer, accept, html } = blanks[i];
        const result = changedAnswer(el, answer, html, { accept }, true);
        applyAttrs(el, result.attrs); slotReveal.set(el, result.reveal);
      }
    }
    const oldAttrs = isAnswer ? Object.fromEntries(['data-check-kind','data-check-hash','data-check'].map(name => [name,writing.getAttribute(name)])) : null;
    const oldReveal = isAnswer ? slotReveal.get(writing) : undefined;
    const updated = isAnswer ? changedAnswer(writing, source, html, json(writing.dataset.check, {}), false) : null;
    function write(text, children, result) {
      target.dataset.src = text; target.replaceChildren(...children);
      if (isAnswer) {
        applyAttrs(writing, result.attrs); slotReveal.set(writing, result.reveal);
        const row = recordByNode.get(writing);
        if (row) {
          row.result = null; row.reveal?.remove(); row.reveal = undefined;
        }
      }
      const panel = panels.get(item);
      if (panel) {
        panel.bar?.remove(); panel.solutionBox?.remove(); panel.compareBox?.remove(); panels.delete(item);
      }
    }
    const fill = target.matches('td:not(.ws-ans)') && (target.classList.contains('ws-fill') || oldBlanks.length || newBlanks.length);
    const hadFill = target.classList.contains('ws-fill');
    const nextFill = newChildren.filter(n => n.nodeType !== Node.TEXT_NODE || n.textContent.trim()).length === 1 &&
      newChildren.some(n => n.nodeType === Node.ELEMENT_NODE && n.matches('.ws-blank'));
    return {
      apply() { write(source, newChildren, updated); if (fill) target.classList.toggle('ws-fill', nextFill); },
      revert() { write(oldSrc, oldChildren, { attrs: oldAttrs, reveal: oldReveal }); if (fill) target.classList.toggle('ws-fill', hadFill); },
    };
  }
  function copiedItem(source, copy) {
    const original = new Map(slotsIn(source).map(el => [el.dataset.slot, el]));
    for (const el of slotsIn(copy)) slotReveal.set(el, slotReveal.get(original.get(el.dataset.slot)));
    itemReveal.set(copy, itemReveal.get(source));
  }
  function cleanItem(item, copy, keepValues) {
    const nodes = [item, ...all(item, '*')], copies = [copy, ...all(copy, '*')];
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i], el = copies[i];
      const attrs = originalAttrs.get(node);
      if (attrs) applyAttrs(el, Object.fromEntries(attrs));
      if (node.hasAttribute('data-slot')) {
        const value = recordByNode.get(node)?.value;
        if (keepValues && value !== undefined) el.dataset.value = JSON.stringify(value);
        else el.removeAttribute('data-value');
        const penValue = recordByNode.get(node)?.penValue;
        if (keepValues && penValue !== undefined) el.dataset.penValue = JSON.stringify(penValue);
        else el.removeAttribute('data-pen-value');
        const originalOrder = recordByNode.get(node)?.control?.initial;
        const list = el.querySelector('.ws-order-list');
        if (originalOrder && list) {
          const children = new Map(all(list, ':scope > li[data-token]').map(li => [li.dataset.token, li]));
          list.append(...originalOrder.map(token => children.get(token)).filter(Boolean));
        }
      }
    }
    // Explore owns only the live path and the computed y cells; preserve the author-supplied start.
    for (let i = 0; i < nodes.length; i++) {
      const data = exploreOriginal.get(nodes[i]);
      if (!data) continue;
      const el = copies[i], path = el.querySelector('.ws-plot > path');
      if (path) applyAttrs(path, { d: data.pathD });
      const yCells = all(el, '[data-y-for]');
      [...data.restoredHtml.values()].forEach((html, j) => { if (yCells[j]) yCells[j].innerHTML = html; });
    }
  }
  function cleanDocument(copy) {
    copy.removeAttribute('data-view'); copy.removeAttribute('data-sheet-preview');
    if (preview && teacher) applyAttrs(copy, { 'data-answers': previousAnswers });
    let script = copy.querySelector('#sheet-reveal');
    if (!canReveal) script?.remove();
    else if (script || [...records.values()].some(row => slotReveal.get(row.el))) {
      const slots = Object.fromEntries([...records.values()].flatMap(row => {
        const value = slotReveal.get(row.el); return value ? [[row.slot, value]] : [];
      }));
      const items = Object.fromEntries(core.items().flatMap(item => {
        const value = itemReveal.get(item); return value ? [[item.dataset.id, value]] : [];
      }));
      if (!script) {
        script = make('script'); script.id = 'sheet-reveal'; script.type = 'application/octet-stream';
        copy.querySelector('#sheet-js')?.before(script);
      }
      script.textContent = encodeReveal(salt(), { slots, items });
    }
    if (policy === '없음') for (const el of all(copy, '[data-check-hash]')) el.removeAttribute('data-check-hash');
  }

  core.attach({ init, layoutFlow, afterLayout, studentPreview: () => preview, beforePrintButton, report,
    editMarkup, copiedItem, cleanItem, cleanDocument });
  core.verdicts = () => Object.fromEntries([...records.values()].filter(r => r.auto).map(r => [r.slot, r.result]));
  Object.assign(window.sheet, { view, slots, answer, values, score, clearAnswers });
})();
