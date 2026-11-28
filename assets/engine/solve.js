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
  function text_candidates(value) {
    const text = norm(value), [left, right, ...rest] = text.split('=');
    if (left && right && !rest.length && !/[<>≤≥≠]/u.test(text)) return [...new Set([text, `${right}=${left}`])];
    return text ? [text] : [];
  }
  function numericDetails(s) {
    if (!textValue(s)) return null;
    let t = norm(s).replace(/^[a-z]=/, '');
    const unit = units.find(u => t.length > u.length && t.endsWith(u)) ?? '';
    if (unit) t = t.slice(0, -unit.length);
    if (t.startsWith('(') && t.endsWith(')')) t = t.slice(1, -1);
    t = t.replace(/^\+/, '').replace(/pi|파이/g, 'π');
    let pi = '';
    if (t.endsWith('π')) {
      pi = 'π'; t = t.slice(0, -1);
      t = t === '' || t === '-' ? t + '1' : t.replace(/[×⋅·*]$/, '');
    } else {
      const match = /^(-?)([0-9]*)π\/([0-9]+)$/.exec(t);
      if (match) { pi = 'π'; t = `${match[1]}${match[2] || '1'}/${match[3]}`; }
    }
    if (/^-?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?$/.test(t)) t = t.replaceAll(',', '');
    let n, form = 'integer';
    if (/^-?[0-9]+$/.test(t)) n = String(BigInt(t));
    else if (/^-?[0-9]+\.[0-9]+$/.test(t)) {
      n = fraction(BigInt(t.replace('.', '')), 10n ** BigInt(t.split('.')[1].length));
      form = 'decimal';
    } else if (/^-?[0-9]+\/[0-9]+$/.test(t)) {
      const [p, q] = t.split('/').map(BigInt);
      n = fraction(p, q);
      form = gcd(p, q) === 1n ? 'reduced' : 'unreduced';
    }
    return n == null ? null : [n + pi, unit === '도' ? '°' : unit, form];
  }
  function number_parts(s) { return numericDetails(s)?.slice(0, 2) ?? null; }
  const num = s => number_parts(s)?.[0] ?? null;
  function normalizedUnit(value) {
    const unit = norm(value);
    return unit === '도' ? '°' : units.includes(unit) ? unit : '';
  }
  function inferred_unit(tail, column = '', row = '', table = '') {
    // 열 머리 → 행 라벨 → 표 전체 → 빈칸 뒤 순으로 본다.
    for (const heading of [column, row]) {
      const unit = normalizedUnit(/\(([^()]*)\)\s*$/.exec(heading.normalize('NFKC'))?.[1] ?? '');
      if (unit) return unit;
    }
    const fallback = normalizedUnit(table);
    if (fallback) return fallback;
    const text = tail.normalize('NFKC').toLowerCase().replace(/^[ \t]+/, '');
    const particles = '(?:으로|로|이다|입니다|이고|이었다|이고요|이며|이므로|이므로서|인|은|는|이|가|을|를|과|와|의|에|에서|까지|부터|씩|만|도)?';
    for (const unit of [...units].sort((a, b) => b.length - a.length)) {
      if (unit === '도' || (unit === '일' && /^일\s*(?:때|경우|수(?:\s|$)|지|까|것(?:\s|$))/u.test(text)) ||
          (unit === '분' && /^분\s*의/u.test(text)) || (unit === '점' && /^점\s*에서\s*(?:만나|만난|만날|만났)/u.test(text))) continue;
      if (new RegExp(`^${unit}${particles}(?![\\p{L}\\p{N}_])`, 'u').test(text)) return unit;
    }
    return '';
  }
  function number_forms(values, unit = '') {
    const parts = values.map(number_parts);
    if (parts.some(p => !p)) return [];
    const inferred = parts.some(([, u]) => u) ? '' : unit;
    return [...new Set([...parts.map(([n]) => n), ...parts.filter(([, u]) => u || inferred).map(([n, u]) => `${n}@${u || inferred}`)])];
  }
  function radicalParts(value) {
    const match = /^(-?)([0-9]{0,128})√(?:([0-9]{1,128})|\(([0-9]{1,128})\))$/.exec(norm(value).replaceAll('루트', '√'));
    if (!match) return null;
    const [, sign, coefficient, radicand, grouped] = match, body = radicand || grouped;
    const square = BigInt(coefficient || '1') ** 2n * BigInt(body);
    return [`${sign}${coefficient}√${body}`, `square:${sign ? -square : square}`];
  }
  function writing_forms(kind, values, unit = '') {
    if (kind === 'number') return number_forms(values, unit);
    if (kind === 'radical') return values.flatMap(radicalParts);
    return values.map(norm);
  }
  function writing_result(kind, value, matches, form = '', strict = false, checkUnit = false) {
    if (value == null || value === '') return null;
    if (kind === 'text') return !String(value).trim() ? null : text_candidates(value).some(matches) || 'review';
    if (kind === 'radical') {
      const parts = radicalParts(value);
      return !!parts && (matches(parts[0]) || (matches(parts[1]) ? 'review' : false));
    }
    if (kind === 'number') {
      const parts = numericDetails(value);
      if (!parts) return false;
      const [n, unit, written] = parts;
      if (!matches(checkUnit && unit ? `${n}@${unit}` : n)) return false;
      if (form === 'fraction') {
        if (strict) return written === 'reduced';
        if (written === 'unreduced') return 'review';
      }
      return true;
    }
    return null;
  }
  function input_mode(values) {
    const texts = values.map(norm);
    if (!texts.every(t => /^[0-9]+(?:\.[0-9]+)?$/.test(t))) return '';
    return texts.some(t => t.includes('.')) ? 'decimal' : 'numeric';
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
        } else if (name === 'sqrt') {
          const index = /^\[([^\[\]]*)\]\s*/.exec(source.slice(i));
          if (index) i += index[0].length;
          const body = group() ?? '';
          out += index ? `√[${plain(index[1])}](${body})` : /^[0-9]+$/.test(body) ? `√${body}` : `√(${body})`;
        } else if (['sin', 'cos', 'tan'].includes(name)) out += name + ' ';
        else if (name === 'quad') out += ' ';
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
  core.codec = Object.freeze({ norm, num, number_parts, inferred_unit, number_forms, input_mode, text_candidates,
    plain, line, points, writing_forms, writing_result, sha256: hex, hash, encodeReveal, decodeReveal });

  const salt = () => document.querySelector('meta[name="sheet-salt"]')?.content || '';
  const policy = root.dataset.reveal || '시도 후';
  // Same student exposure rules as sheet_build.student_policy.
  const canReveal = policy === '시도 후' || policy === '바로';
  const canGrade = canReveal || policy === '교사만';
  const automatic = new Set(['number','radical','text','choice','ox','order','match','points','line','bars']);
  const slotsIn = item => all(item, '[data-slot]').filter(el => !el.closest('.ws-key, .ws-teacher-only'));
  const recordByNode = new WeakMap(), panels = new WeakMap(), itemReveal = new WeakMap(), slotReveal = new WeakMap();
  const originalAttrs = new WeakMap(), exploreOriginal = new WeakMap(), mediaBound = new WeakSet();
  const records = new Map(), dash = new Set();
  let initialReveal = {}, storageKey, mode = 'a4', preview = false, previousAnswers;
  let scoreLabel, viewButton, previewButton, returnButton, printing, mainNode;
  let delayedPaint, pendingRestore, restoreDialog, saveButton, clearButton, saveStatus;
  let oldStorageKeys = [];
  let lockedMedia = [];
  const store = (op, ...args) => {
    try {
      const result = window.localStorage[op](...args);
      return op === 'getItem' ? result : true;
    } catch { return undefined; }
  };
  function showSaveFailure(failed) {
    const message = failed ? '이 기기에 저장되지 않았어요. ‘답을 넣어 저장’으로 파일에 저장하세요.' : '';
    if (saveStatus && saveStatus.textContent !== message) saveStatus.textContent = message;
  }
  const usable = () => (!teacher || preview) && !pendingRestore;
  const solveOnly = (tag, cls, text) => runtime(make(tag, `${cls} ws-solve-only`, text));
  const uiButton = (label, action, fn, options) => core.button(label, action, event => { if (usable()) fn(event); }, options);
  function saveValues() {
    if (!storageKey || pendingRestore) return;
    const pens = Object.fromEntries([...records.values()].filter(r => r.penValue !== undefined).map(r => [r.slot, r.penValue]));
    // A failed setItem leaves the last complete snapshot intact.
    const saved = store('setItem', storageKey, JSON.stringify({ v: 1, answers: values(), pens, savedAt: Date.now() }));
    if (saved) oldStorageKeys = oldStorageKeys.filter(key => !store('removeItem', key));
    showSaveFailure(!saved);
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
  function hasResponse({ kind, value }) {
    if (value == null) return false;
    if (kind === 'draw') return !!value.strokes.length;
    if (kind === 'match') return Object.keys(value).length > 0;
    if (kind === 'self') return value.some(Boolean);
    if (Array.isArray(value)) return value.some(v => v != null);
    return String(value).trim() !== '';
  }
  const multiple = new Set(['bars', 'points', 'line', 'match', 'order']);
  function completeResponse(row) {
    if (!hasResponse(row)) return false;
    if (['match','line','bars'].includes(row.kind)) return normalize(row.kind, row.value, row.el) != null;
    if (row.kind === 'points') return row.value.length >= maxPoints(row.el);
    return true;
  }
  function check(record) {
    if (!record.auto) return null;
    const { kind, el } = record, value = record.value ?? record.control?.initial;
    const matches = normalized => record.hashes.includes(hash(salt(), kind, normalized));
    if (value == null || value === '' || (Array.isArray(value) && !value.length)) return null;
    if (['text', 'number', 'radical'].includes(kind)) {
      const form = writingShape(el).includes('fraction') ? 'fraction' : '';
      return writing_result(kind, value, matches, form, record.item.dataset.reducedFractions === '1',
        el.dataset.checkUnit === '1');
    }
    const normalized = normalize(kind, value, el);
    if (normalized == null && ['match','line','bars'].includes(kind)) return null;
    return normalized != null && matches(normalized);
  }
  // A radius measured across a long, thin drawing can span several frame heights.
  const penReach = 64;
  const validPenStrokes = strokes => Array.isArray(strokes) && strokes.every(path => Array.isArray(path) && path.length &&
    path.every(p => Array.isArray(p) && p.length === 2 && p.every(n => Number.isFinite(n) && n >= -penReach && n <= 1 + penReach)));
  function sanitizedValue(record, value) {
    const { el, kind } = record;
    if (el.matches('.ws-blank,.ws-name-slot,.ws-ans:not([data-kind="draw"])')) {
      if (!textValue(value)) return undefined;
      return el.matches('.ws-ans') ? String(value).replace(/\r\n?/g, '\n') : String(value).replace(/[\r\n]/g, '');
    }
    if (kind === 'ox') return value === '' ? '' : normalize(kind, value, el)?.toUpperCase();
    if (kind === 'draw') {
      const strokes = value?.strokes;
      if (!validPenStrokes(strokes)) return undefined;
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
  function readDeviceStorage() {
    const stored = json(store('getItem', storageKey), {});
    const penKey = `${storageKey}:pen`, timeKey = `${storageKey}:saved-at`;
    const oldPens = store('getItem', penKey), oldTime = store('getItem', timeKey);
    oldStorageKeys = [oldPens != null && penKey, oldTime != null && timeKey].filter(Boolean);
    // Never supplement a new snapshot with stale values from the previous format.
    return stored.v === 1 ? stored : { answers: stored, pens: json(oldPens, {}), savedAt: Number(oldTime) };
  }
  function readSnapshot(device) {
    const snapshot = new Map();
    for (const row of records.values()) {
      const { el, kind, slot } = row;
      const value = sanitizedValue(row, device ? device.answers?.[slot] : json(el.dataset.value, undefined));
      const penValue = el.matches('figure[data-pen="1"]') && ['points', 'line'].includes(kind)
        ? sanitizedValue({ el, kind: 'draw' }, device ? device.pens?.[slot] : json(el.dataset.penValue, undefined)) : undefined;
      if (value !== undefined || penValue !== undefined) snapshot.set(row, { value, penValue });
    }
    return snapshot;
  }
  function restoreSnapshot(snapshot) {
    for (const [row, state] of snapshot) {
      Object.assign(row, state);
      const drawing = row.kind === 'draw' ? state.value : state.penValue;
      row.restoredDrawing = !!drawing?.strokes.length;
    }
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
          recordByNode.set(el, row);
        }
        if (!slotReveal.has(el)) slotReveal.set(el, Object.hasOwn(initialReveal.slots ?? {}, old) ? initialReveal.slots[old] : undefined);
        if (item.dataset.id === 'head' && old.startsWith('head.')) row.slot = old;
        else row.slot = el.dataset.slot = `${item.dataset.id}.${++i}`;
        row.kind = kind;
        row.hashes = (el.dataset.checkHash || '').split(',').filter(Boolean);
        row.auto = canGrade && automatic.has(kind) && row.hashes.length > 0;
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
    if (pendingRestore) return null;
    const next = sanitizedValue(row, value);
    if (next === undefined) return null;
    return applyValue(row, same(next, row.value) ? row.value : next, evaluate);
  }
  function applyValue(row, next, evaluate = false) {
    if (next !== row.value) { row.value = next; row.result = null; saveValues(); }
    if (evaluate) row.result = check(row);
    if (usable()) {
      row.control?.paint(row.value);
      updateRow(row);
      const panel = panels.get(row.item);
      if (panel) panel.emptyAttempt = false;
      updatePanel(row.item);
      updateScore();
    }
    // Keep sheet.answer() boolean/null for callers that test whether the answer matched.
    return row.result === 'review' ? false : row.result;
  }
  const answer = (slot, value) => records.has(slot) ? setValue(records.get(slot), value, true) : null;
  function clearAnswers() {
    const wasPending = !!pendingRestore;
    closeRestorePrompt();
    for (const row of records.values()) {
      row.value = undefined; row.penValue = undefined; row.result = null;
      delete row.el.dataset.value; delete row.el.dataset.penValue;
      row.control?.reset?.();
      row.control?.paint(undefined); updateRow(row);
    }
    for (const item of core.items()) {
      const panel = panels.get(item);
      if (panel) {
        Object.assign(panel, { attempted: false, emptyAttempt: false, answers: false, solution: false, comparing: false, hintCount: 0 });
        if (panel.solutionBox) { stopVideo(panel.solutionBox); panel.solutionBox.remove(); panel.solutionBox = undefined; }
        if (panel.compareBox) { panel.compareBox.remove(); panel.compareBox = undefined; }
        updatePanel(item);
      }
    }
    if (storageKey) {
      const removed = ['', ':pen', ':saved-at'].map(suffix => store('removeItem', storageKey + suffix));
      if (removed.every(Boolean)) showSaveFailure(false);
    }
    if (wasPending) core.layout();
    updateScore();
  }
  function closeRestorePrompt() {
    if (!pendingRestore) return;
    pendingRestore = undefined;
    restoreDialog.close();
    restoreDialog.remove();
    for (const video of lockedMedia) video.controls = true;
    lockedMedia = [];
    syncToolbar();
    document.dispatchEvent(new Event('sheet:answers-ready'));
  }
  function savedClock(when) {
    const date = new Date(when), today = new Date();
    if (!(when > 0 && Number.isFinite(+date))) return '';
    const day = date.toDateString() === today.toDateString() ? '' :
      `${date.getFullYear() !== today.getFullYear() ? `${date.getFullYear()}년 ` : ''}${date.getMonth() + 1}월 ${date.getDate()}일 `;
    return `마지막 저장 ${day}${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  }
  function showRestorePrompt(when) {
    const dialog = restoreDialog = runtime(make('dialog', 'ws-restore-dialog'));
    const prompt = make('div', 'ws-answer-prompt');
    const message = make('span', '', '이 기기에 전에 쓴 답이 있어요.');
    message.id = 'ws-restore-message'; message.setAttribute('role', 'status');
    const time = savedClock(when);
    dialog.setAttribute('aria-labelledby', message.id);
    function use(snapshot) {
      restoreSnapshot(snapshot);
      closeRestorePrompt(); core.layout(); viewButton.focus();
    }
    const resume = core.button('이 기기에서 내가 쓰던 답 열기', 'resume-answers', () => use(pendingRestore.device));
    const openFile = pendingRestore.file.size && core.button('파일에 담긴 답 열기', 'open-file-answers', () => use(pendingRestore.file));
    const start = core.button('빈 활동지로 시작', 'start-fresh', () => render(true));
    const cancel = core.button('취소', 'cancel-start-fresh', () => { render(false); start.focus(); });
    const confirm = core.button('지우고 시작', 'confirm-start-fresh', () => { clearAnswers(); viewButton.focus(); });
    let confirming = false;
    function render(ask) {
      confirming = ask;
      if (ask) {
        message.textContent = '이 기기에 저장된 이름과 답을 지울까요?';
        prompt.replaceChildren(message, cancel, confirm);
        cancel.focus();
      } else {
        message.textContent = '이 기기에 전에 쓴 답이 있어요.';
        if (time) message.append(make('small', '', time));
        prompt.replaceChildren(message, ...(openFile ? [openFile, resume, start] : [start, resume]));
      }
    }
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (confirming) cancel.click(); });
    render(false);
    dialog.append(prompt); document.body.append(dialog);
    // Chrome's native media controls can take focus even under a modal dialog.
    lockedMedia = core.items().flatMap(item => all(item, 'video[controls]'));
    for (const video of lockedMedia) attr(video, 'controls', null);
    dialog.showModal(); (openFile || start).focus();
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
  function tableHeadings(el, forGrading = false) {
    const cell = el.closest('td,th'), tr = cell?.parentElement, table = cell?.closest('table');
    const read = node => {
      if (!node) return '';
      const copy = node.cloneNode(true);
      for (const ignored of all(copy, '.ws-blank, .ws-tools, [data-sheet-runtime]')) ignored.remove();
      return copy.textContent.trim();
    };
    if (!tr || !table) return { row: '', column: '' };
    if (forGrading && table.matches('.ws-ans-table')) {
      let row = tr, distance = 0;
      while (row) {
        const lab = row.querySelector(':scope > th.ws-lab');
        if (lab) return { row: distance < lab.rowSpan ? read(lab.querySelector('.ws-lab-text') ?? lab) : '', column: '' };
        row = row.previousElementSibling;
        distance++;
      }
      return { row: '', column: '' };
    }
    if (forGrading && !table.matches('.ws-data, .ws-steps')) return { row: '', column: '' };
    const heads = table.tHead?.rows[0];
    const first = tr.cells[0];
    const rowHead = tr.querySelector(':scope > th.ws-lab, :scope > th.ws-step-n');
    const inHead = tr.parentElement === table.tHead;
    const row = forGrading && inHead ? '' : read(rowHead?.querySelector('.ws-lab-text') ?? rowHead ??
      (table.matches('.ws-data') && first !== cell && !first.querySelector('.ws-blank') ? first : null));
    const column = forGrading && inHead ? '' : read(heads?.cells[cell.cellIndex]);
    return { row, column, unit: table.matches('.ws-data') ? table.dataset.tableUnit : undefined };
  }
  const writingShape = el => (el.dataset.writingShape ?? '').split(' ');
  function field(row) {
    const { el, kind } = row;
    const answerBox = el.matches('.ws-ans');
    const multiline = answerBox && !(el.dataset.lines === '2' && el.dataset.grow !== '1' &&
      ['number', 'radical', 'text'].includes(kind));
    const input = runtime(make(multiline ? 'textarea' : 'input', 'ws-in'));
    const ink = runtime(make('span', 'ws-ink ws-print-only'));
    const fillTable = el.matches('.ws-blank') && el.parentElement?.matches('td.ws-fill') && el.closest('table.ws-data');
    const sizer = fillTable ? runtime(make('span', 'ws-input-sizer')) : null;
    ink.setAttribute('aria-hidden', 'true');
    if (sizer) {
      sizer.setAttribute('aria-hidden', 'true');
      toggle(fillTable, 'ws-input-table', true);
      resizeObserver.observe(fillTable);
    }
    if (!multiline) input.type = 'text';
    const headings = tableHeadings(el);
    const label = [headings.row, headings.column].filter(Boolean).join(', ');
    input.setAttribute('aria-label', kind === 'name' ? row.slot.split('.').at(-1) : label ||
      (answerBox ? '답 쓰기' : `빈칸 ${el.querySelector('.ws-blank-mark')?.textContent || row.slot}`));
    input.autocomplete = 'off';
    if (kind === 'number' || kind === 'radical') input.spellcheck = false;
    if (['numeric', 'decimal'].includes(el.dataset.inputMode)) input.inputMode = el.dataset.inputMode;
    if (el.matches('.ws-name-slot') && !el.querySelector(':scope > .ws-name-space')) {
      const placeholder = make('span', 'ws-name-space');
      placeholder.append(...el.childNodes);
      el.append(placeholder);
    }
    if (el.matches('.ws-blank') && el.style.minWidth) {
      const style = make('span');
      style.setAttribute('style', el.getAttribute('style'));
      style.style.setProperty('--ws-blank-min', el.style.minWidth);
      attr(el, 'style', style.getAttribute('style'));
    }
    el.append(input, ink);
    if (sizer) el.append(sizer);
    input.addEventListener('input', () => setValue(row, input.value));
    if (!multiline && kind !== 'name') input.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229 || !usable() || mode !== 'solve') return;
      const button = row.item.querySelector('.ws-check-bar [data-sheet-action="check"]');
      if (!button || button.disabled) return;
      event.preventDefault();
      button.click();
    });
    let symbols;
    function syncSymbols() {
      symbols?.remove();
      symbols = undefined;
      for (const [symbol, label] of [['π', '파이'], ['√', '루트']]) {
        if (!writingShape(el).includes(symbol)) continue;
        if (!symbols) {
          symbols = solveOnly('span', 'ws-symbols');
          if (answerBox) el.append(symbols); else el.after(symbols);
        }
        const button = make('button', '', symbol);
        button.type = 'button';
        button.dataset.symbol = symbol;
        button.setAttribute('aria-label', `${label} 넣기`);
        button.addEventListener('mousedown', event => event.preventDefault());
        button.addEventListener('click', () => {
          if (!usable() || mode !== 'solve') return;
          const start = input.selectionStart ?? input.value.length, end = input.selectionEnd ?? start;
          input.focus();
          input.setRangeText(symbol, start, end, 'end');
          setValue(row, input.value);
        });
        symbols.append(button);
      }
    }
    syncSymbols();
    return { syncSymbols, paint(value) {
      const text = value ?? '';
      if (input.value !== text) input.value = text;
      if (multiline && el.style.height) input.style.setProperty('--ws-input-height', el.style.height);
      ink.textContent = text;
      if (sizer) sizer.textContent = text;
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
      if (!usable() || row.penPanning) return;
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
    const [y0,y1,bottom,top] = map, histogram = svg.dataset.chart === 'histogram';
    const min = snapValue(Math.ceil(y0 / snap - 1e-9) * snap, snap);
    const max = snapValue(Math.floor(y1 / snap + 1e-9) * snap, snap);
    const toSvg = value => bottom - (clamp(value, y0, y1) - y0) / (y1 - y0) * (bottom - top);
    attr(svg, 'role', 'group');
    if (histogram) el.prepend(solveOnly('div', 'ws-explore-note', '각 계급에서 직사각형의 윗변이 올 높이를 눌러요.'));
    for (const hit of hits) {
      const i = +hit.dataset.i;
      attr(hit, 'tabindex', '0'); attr(hit, 'role', 'slider');
      attr(hit, 'aria-valuemin', min); attr(hit, 'aria-valuemax', max);
      const change = (v, toggleOff = false) => {
        const current = [...(row.value ?? hits.map(() => null))];
        current[i] = toggleOff && v === current[i] ? null : v;
        setValue(row, current);
      };
      hit.addEventListener('click', event => {
        if (!usable()) return;
        const p = svgPoint(svg, event);
        if (p) change(clamp(snapValue(y0 + (bottom - p.y) / (bottom - top) * (y1 - y0), snap), min, max), true);
      });
      hit.addEventListener('keydown', event => {
        if (!usable() || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        const next = (row.value?.[i] ?? clamp(0, min, max)) + (event.key === 'ArrowUp' ? snap : -snap);
        change(clamp(snapValue(next, snap), min, max));
      });
    }
    return { paint(value = []) {
      for (const node of all(plot, '[data-sheet-runtime]')) node.remove();
      for (const hit of hits) {
        const v = value[+hit.dataset.i];
        attr(hit, 'aria-label', `${hit.dataset.label ?? `막대 ${+hit.dataset.i + 1}`}, ${v == null ? '높이 미입력' : `높이 ${v}`}`);
        attr(hit, 'aria-valuenow', v ?? clamp(0, min, max));
        attr(hit, 'aria-valuetext', v == null ? '미입력' : String(v));
        if (v == null) continue;
        const x = Number(hit.getAttribute('x'));
        const width = histogram && hit.hasAttribute('data-edge') ? Number(hit.dataset.edge) - x : Number(hit.getAttribute('width'));
        const pad = histogram ? 0 : Math.min(.6, width * .05);
        const baseline = toSvg(0), valueY = toSvg(v);
        const y = Math.min(baseline, valueY);
        const mark = runtime(svgNode('rect', { class: 'ws-user-bar', x: x + pad,
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
    const setDrawing = value => {
      if (alongsidePlot) { row.penValue = value; saveValues(); paint(value); }
      else applyValue(row, value);
    };
    const history = [];
    function commit(strokes) {
      history.push(drawing());
      setDrawing({ strokes });
    }
    const anchor = figure ? el.querySelector('svg:not(.ws-match-lines), img') : runtime(make('div', 'ws-draw-frame'));
    if (!anchor) return;
    if (!figure) {
      el.append(anchor);
      const table = el.closest('.ws-ans-table');
      if (table) toggle(table, 'ws-draw-table', true);
    }
    const canvas = runtime(make('canvas', 'ws-pen'));
    canvas.setAttribute('aria-label', '그리기 칸');
    const ink = runtime(svgNode('svg', { class: 'ws-pen-ink', 'aria-hidden': 'true',
      viewBox: '0 0 1 1', preserveAspectRatio: 'none' }));
    (figure ? el : anchor).append(canvas, ink);
    const tools = solveOnly('div', 'ws-pen-tools');
    tools.setAttribute('role', 'group');
    tools.setAttribute('aria-label', '그리기 도구');
    const status = solveOnly('div', 'ws-pen-status');
    status.setAttribute('aria-live', 'polite');
    const initialTool = alongsidePlot ? 'plot' : 'ink';
    let selected, gesture, radius = 0, restoreFrame;
    let enlarged = false, panGesture;
    const viewport = runtime(make('div', 'ws-pen-viewport'));
    const stage = runtime(make('div', 'ws-pen-stage'));
    viewport.append(stage);
    viewport.tabIndex = 0;
    viewport.setAttribute('role', 'region');
    viewport.setAttribute('aria-label', '크게 보는 그림');
    const zoomButton = uiButton('크게', 'pen-zoom', () => {
      if (mode !== 'solve' || !narrowScreen.matches) return;
      enlarged = !enlarged;
      zoomButton.setAttribute('aria-pressed', String(enlarged));
      paint(drawing());
    }, { pressed: false });
    zoomButton.classList.add('ws-pen-zoom');
    const modes = [];
    function clearStatus() {
      if (restoreFrame) {
        cancelAnimationFrame(restoreFrame);
        restoreFrame = undefined;
        row.restoredDrawing = false;
      }
      status.textContent = '';
    }
    function tool(which) {
      stopTracking();
      panGesture = undefined;
      selected = which;
      row.penPanning = which === 'pan';
      viewport.classList.toggle('ws-pen-pan', row.penPanning);
      canvas.classList.toggle('ws-pen-pass-through', which === 'plot' || row.penPanning);
      for (const [key, button] of modes) button.setAttribute('aria-pressed', String(key === which));
      clearStatus();
      paintCanvas(drawing());
    }
    function syncView() {
      const zoom = enlarged && mode === 'solve' && narrowScreen.matches && usable();
      if (zoom === !!viewport.parentElement) return;
      stopTracking();
      panGesture = undefined;
      if (zoom) {
        anchor.before(viewport);
        stage.append(anchor);
        if (figure) stage.append(canvas);
        viewport.scrollLeft = viewport.scrollTop = 0;
      } else {
        viewport.replaceWith(anchor);
        if (figure) ink.before(canvas);
      }
    }
    viewport.addEventListener('pointerdown', event => {
      if (!usable() || selected !== 'pan' || event.button !== 0 || panGesture || !stage.contains(event.target)) return;
      event.preventDefault();
      panGesture = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
      if (event.isTrusted) viewport.setPointerCapture(event.pointerId);
    });
    viewport.addEventListener('pointermove', event => {
      if (panGesture?.id !== event.pointerId) return;
      viewport.scrollLeft = panGesture.left + panGesture.x - event.clientX;
      viewport.scrollTop = panGesture.top + panGesture.y - event.clientY;
    });
    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) viewport.addEventListener(type, event => {
      if (panGesture?.id === event.pointerId) panGesture = undefined;
    });
    function addMode(key, label, action) {
      const button = uiButton(label, action, () => tool(key), { pressed: key === initialTool });
      tools.append(button);
      modes.push([key, button]);
    }
    if (alongsidePlot) addMode('plot', '점 찍기', 'pen-plot');
    addMode('ink', '펜', 'pen');
    addMode('line', '곧은 선', 'pen-line');
    if (!alongsidePlot) addMode('compass', '컴퍼스', 'pen-compass');
    addMode('erase', '지우개', 'eraser');
    const undo = uiButton('되돌리기', 'pen-undo', () => {
      stopTracking();
      if (history.length) setDrawing(history.pop());
    }, { disabled: true });
    tools.append(undo, uiButton('모두 지우기', 'pen-clear', () => {
      stopTracking();
      if (drawing()?.strokes.length) commit([]);
    }));
    addMode('pan', '화면 이동', 'pen-pan');
    tools.prepend(zoomButton);
    el.append(tools, status);
    let printedStrokes;
    function paint(value) {
      syncView();
      // Screen and print both follow the underlay; the enlarged canvas lives inside the scrolling stage.
      if (figure) {
        const parent = viewport.parentElement ? stage : el;
        const f = parent.getBoundingClientRect(), r = anchor.getBoundingClientRect();
        if (r.width && r.height) {
          const box = { left: `${r.left-f.left-parent.clientLeft}px`, top: `${r.top-f.top-parent.clientTop}px`,
            width: `${r.width}px`, height: `${r.height}px` };
          Object.assign(canvas.style, box);
          if (parent === el) Object.assign(ink.style, box);
        }
      } else anchor.style.setProperty('--ws-draw-ratio', String(core.drawRatio(el)));
      if (row.restoredDrawing && mode === 'solve' && usable() && !restoreFrame) {
        restoreFrame = requestAnimationFrame(() => {
          restoreFrame = undefined;
          if (!row.restoredDrawing || mode !== 'solve' || !usable()) return;
          row.restoredDrawing = false;
          status.textContent = alongsidePlot ? '그림을 다시 불러왔어요.' : '그림을 다시 불러왔어요. 컴퍼스는 다시 벌려 주세요.';
        });
      }
      const strokes = value?.strokes;
      if (strokes !== printedStrokes) {
        printedStrokes = strokes;
        ink.replaceChildren(...(strokes ?? []).map(path => svgNode('polyline', {
          // A tap is a zero-length stroke with a round cap.
          points: (path.length === 1 ? [path[0], path[0]] : path).join(' '),
        })));
      }
      undo.disabled = !history.length;
      paintCanvas(gesture || value, gesture);
    }
    function paintCanvas(value, previewGesture) {
      const { width, height } = canvas.getBoundingClientRect();
      if (!width || !height) return;
      const ratio = Math.min(3, Math.max(2, devicePixelRatio || 1));
      const w = Math.round(width * ratio), h = Math.round(height * ratio);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0,0,w,h); ctx.lineWidth = 1.8 * ratio; ctx.lineCap = ctx.lineJoin = 'round'; ctx.strokeStyle = '#161616';
      const trace = path => {
        const [[x,y], ...rest] = path;
        ctx.beginPath(); ctx.moveTo(x*w,y*h);
        if (!rest.length) ctx.lineTo(x*w+.01,y*h);
        for (const [a,b] of rest) ctx.lineTo(a*w,b*h);
        ctx.stroke();
      };
      for (const path of value?.strokes ?? []) trace(path);
      if (previewGesture?.which === 'line') trace([previewGesture.start, previewGesture.last]);
      if (previewGesture?.which === 'compass' && previewGesture.dragged) {
        const [x,y] = previewGesture.start;
        const r = opening(previewGesture.start, previewGesture.last, width, height);
        ctx.save();
        ctx.setLineDash([4*ratio, 4*ratio]); ctx.beginPath();
        ctx.ellipse(x*w, y*h, r*w/width, r*h/height, 0, 0, 2*Math.PI);
        ctx.stroke(); ctx.restore();
      }
    }
    const rounded = n => Number(n.toFixed(5));
    const point = (event, r) => [(event.clientX-r.left)/r.width, (event.clientY-r.top)/r.height]
      .map(v => rounded(clamp(v, 0, 1)));
    const distancePx = (a, b, w, h) => Math.hypot((a[0]-b[0])*w, (a[1]-b[1])*h);
    const opening = (a, b, w, h) => Math.min(distancePx(a, b, w, h), penReach * Math.min(w, h));
    const unitBox = { x0: 0, x1: 1, y0: 0, y1: 1, x: v => v, y: v => v };
    const inFrame = p => p.every(n => n >= 0 && n <= 1);
    function circlePath([x,y], aspect) {
      // Even if the frame cuts the circle into visible arcs, store just one closed stroke.
      if (radius > Math.hypot(Math.max(x, 1-x), Math.max(y, 1-y) / aspect)) return null;
      return Array.from({ length: 73 }, (_, i) => {
        const theta = i * Math.PI / 36;
        return [rounded(x + radius * Math.cos(theta)), rounded(y + radius * aspect * Math.sin(theta))];
      });
    }
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
      return strokes.filter(path => !path.some((b,i) => {
        const a = path[i-1] || b;
        const segment = inFrame(a) && inFrame(b) ? [a,b] : clipSegment(a,b,unitBox);
        if (!segment) return false;
        const [[x1,y1],[x2,y2]] = segment;
        return samples.some(sample => distance(sample,[x1*w,y1*h],[x2*w,y2*h]) < 9);
      }));
    }
    canvas.addEventListener('pointerdown', event => {
      if (!usable() || event.button !== 0 || gesture || selected === 'plot' || selected === 'pan') return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const p = point(event, rect), before = drawing()?.strokes ?? [];
      const strokes = selected === 'erase' ? eraseAt(before,[p,p]) : selected === 'ink' ? [...before, [p]] : before;
      const listeners = new AbortController();
      clearStatus();
      gesture = { id:event.pointerId, which:selected, strokes, start:p, last:p, listeners };
      // Chrome can lose capture while the pointer is still down. Keep tracking this
      // pointer through release, even if the next event targets another element.
      const options = { capture: true, signal: listeners.signal };
      window.addEventListener('pointermove', move, options);
      window.addEventListener('pointerup', finish, options);
      window.addEventListener('pointercancel', finish, options);
      window.addEventListener('blur', finish, { signal: listeners.signal });
      if (event.isTrusted) canvas.setPointerCapture(event.pointerId);
      paintCanvas(gesture, gesture);
    });
    function move(event) {
      if (gesture?.id !== event.pointerId) return;
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const samples = event.getCoalescedEvents?.();
      for (const sample of samples?.length ? samples : [event]) {
        const p = point(sample, rect);
        if (same(p, gesture.last)) continue;
        if (gesture.which === 'erase') gesture.strokes = eraseAt(gesture.strokes,[gesture.last,p]);
        else if (gesture.which === 'ink') gesture.strokes.at(-1).push(p);
        else if (distancePx(gesture.start, p, rect.width, rect.height) >= 5) gesture.dragged = true;
        gesture.last = p;
      }
      paintCanvas(gesture, gesture);
    }
    function stopTracking() {
      gesture?.listeners.abort();
      gesture = undefined;
    }
    function finish(event) {
      if (!gesture || (event.type !== 'blur' && gesture.id !== event.pointerId)) return;
      if (event.type === 'pointerup') move(event);
      const { strokes, start, last, which, dragged } = gesture;
      stopTracking();
      const { width, height } = canvas.getBoundingClientRect();
      if (event.type === 'pointerup' && usable() && width && height) {
        if (which === 'ink' || (which === 'erase' && strokes.length < (drawing()?.strokes.length ?? 0))) {
          commit(strokes); return;
        }
        if (which === 'line' && !same(start, last)) { commit([...strokes, [start, last]]); return; }
        if (which === 'compass') {
          if (dragged) {
            const distance = opening(start, last, width, height);
            if (distance >= 5) radius = distance / width;
          } else if (!radius) status.textContent = '먼저 끌어서 컴퍼스를 벌리세요';
          else {
            const path = circlePath(start, width / height);
            if (path) { commit([...strokes, path]); return; }
          }
        }
      }
      paintCanvas(drawing());
    }
    tool(initialTool);
    return { paint: value => paint(alongsidePlot && usable() ? row.penValue : value), resize: true, syncView,
      reset() { history.length = 0; radius = 0; row.restoredDrawing = false; tool(initialTool); } };
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
        if (sketch) row.control = { resize: true, reset: sketch.reset, syncView: sketch.syncView,
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
    const review = active && row.result === 'review';
    toggle(row.el, 'ws-ok', ok); toggle(row.el, 'ws-no', no); toggle(row.el, 'ws-review', review);
    if ((ok || no || review) && !row.verdict) {
      row.verdict = solveOnly(row.el.matches('.ws-blank,.ws-name-slot') ? 'span' : 'div', 'ws-verdict');
      row.verdict.setAttribute('role', 'status');
      auxiliary(row, row.verdict);
    }
    if (row.verdict) {
      row.verdict.hidden = !(ok || no || review);
      row.verdict.classList.toggle('ws-ok', ok);
      row.verdict.classList.toggle('ws-no', no);
      row.verdict.classList.toggle('ws-review', review);
      let message = ok ? '✓' : no ? '다시 생각해 봐요' : '';
      if (review) {
        if (row.kind === 'number') message = '기약분수로 나타냈는지 확인해 보세요.';
        else if (row.kind === 'radical') message = '값은 같아요. 근호가 있는 꼴을 확인해 보세요.';
        else message = '예상한 답과 달라요. 같은 뜻이면 선생님께 확인받아요.';
      }
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
      const content = make('span');
      try { putHtml(content, core.renderMarkup(String(text))); }
      catch { content.textContent = text; }
      check.type = 'checkbox'; label.append(check, content); entry.append(label); list.append(entry);
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
    const hasCheck = hasAuto || (policy === '시도 후' && (hasAnswers || hasSolution));
    if (hasCheck) {
      bar.append(uiButton('확인', 'check', () => {
        panel.emptyAttempt = policy === '시도 후' && !panel.rows.some(hasResponse);
        if (panel.emptyAttempt) { updatePanel(item); return; }
        panel.attempted = true;
        for (const row of panel.rows) {
          row.result = policy === '시도 후' && !hasResponse(row) ? null : check(row);
          updateRow(row);
        }
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
    if (hasAuto || (hasCheck && policy === '시도 후') || ((hasAnswers || hasSolution) && rows.some(r => multiple.has(r.kind)))) {
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
    const missing = panel.rows.find(r => multiple.has(r.kind) && !completeResponse(r));
    const answerRows = panel.rows.filter(r => r.kind !== 'open' && slotReveal.get(r.el)?.answer_html &&
      (multiple.has(r.kind) ? completeResponse(r) : policy === '바로' || hasResponse(r)));
    const answerReady = unlocked && answerRows.length > 0;
    const solutionReady = unlocked && !missing && (policy === '바로' || panel.rows.filter(r => r.kind !== 'draw').every(completeResponse));
    setButton(panel.answerButton, answerReady, panel.answers);
    setButton(panel.solutionButton, solutionReady, panel.solution);
    const compareReady = active && !!panel.compareButton && panel.rows.every(row => {
      if (row.kind === 'open') return /[\p{L}\p{N}]/u.test(row.value ?? '');
      return ['draw', 'self', 'name'].includes(row.kind) || completeResponse(row);
    });
    setButton(panel.compareButton, compareReady, panel.comparing);
    const compare = compareReady && !!panel.comparing;
    for (const row of panel.rows) setReveal(row, row.kind === 'open'
      ? compare : !!(answerReady && panel.answers && answerRows.includes(row)));
    if (compare && itemReveal.get(item)?.rubric?.length && !panel.compareBox) {
      panel.compareBox = solveOnly('div', 'ws-solution ws-compare');
      appendRubric(panel.compareBox, itemReveal.get(item).rubric); item.append(panel.compareBox);
    }
    if (panel.compareBox) panel.compareBox.hidden = !compare;
    const show = !!(solutionReady && panel.solutionButton && panel.solution);
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
      const review = auto.filter(row => row.result === 'review').length;
      const remaining = { bars: '각 막대의 높이를 모두 정해 보세요.', points: '점을 모두 찍어 보세요.',
        line: '서로 다른 두 점을 찍어 보세요.', match: '짝을 모두 이어 보세요.', order: '순서를 정해 보세요.' };
      const score = auto.length && (auto.some(row => row.result != null) || panel.attempted)
        ? `${auto.filter(row => row.result === true).length}/${auto.length} 맞음${review ? ` · 확인 필요 ${review}` : ''}` : '';
      const guidance = active && missing && (panel.answerButton || panel.solutionButton) &&
        (unlocked || panel.emptyAttempt || hasResponse(missing)) ? remaining[missing.kind] : '';
      const label = [panel.emptyAttempt ? guidance || '먼저 답을 써 보세요.' : score,
        panel.emptyAttempt ? '' : guidance].filter(Boolean).join(' · ');
      if (panel.result.textContent !== label) panel.result.textContent = label;
    }
    updateTableOverflow(item);
  }
  function updateScore() {
    if (!scoreLabel) return;
    const { correct, total } = score();
    const open = [...records.values()].filter(row => row.kind === 'open').length;
    const label = total ? `맞힌 수 ${correct} / 자동 판별 ${total}${open ? ` · 서술 ${open}칸은 직접 확인` : ''}` : '';
    if (scoreLabel.textContent !== label) scoreLabel.textContent = label;
    scoreLabel.hidden = !total;
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
      const excluded = new Set(Array.isArray(p.except) ? p.except : []);
      const seek = direction => {
        while (excluded.has(input.valueAsNumber)) {
          const before = input.value;
          if (direction > 0) input.stepUp(); else input.stepDown();
          if (input.value === before) return false;
        }
        return true;
      };
      if (!seek(1)) {
        input.value = p.value;
        if (!seek(-1)) input.disabled = true;
      }
      scope[p.name] = input.disabled ? p.value : input.valueAsNumber;
      const name = make('span');
      try { name.innerHTML = core.renderMarkup(p.label || `$${p.name}$의 값`); }
      catch { name.textContent = p.label || p.name; }
      input.setAttribute('aria-label', name.textContent);
      out.value = nfmt(scope[p.name]);
      input.addEventListener('input', () => {
        if (input.disabled) return;
        const previous = scope[p.name];
        if (!seek(input.valueAsNumber >= previous ? 1 : -1)) input.value = previous;
        scope[p.name] = input.valueAsNumber; out.value = nfmt(scope[p.name]); render();
      });
      label.append(name, input, out); tool.append(label);
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
        // Put the underlay back before measuring the A4 page.
        row?.control?.syncView?.();
      }
      for (const block of all(item, '.ws-explore')) exploreOriginal.get(block)?.render();
    }
  }
  function layoutFlow(main, items) {
    mainNode = main;
    renderExisting(items);
    if (mode !== 'solve') return false;
    main.id = 'sheet-flow';
    // Standalone page bands do not leave a blank card. Mixed items keep their other blocks.
    const flowItems = list => list.filter(item => {
      const blocks = [...item.children].filter(block => !block.hasAttribute('data-sheet-runtime'));
      return !blocks.length || blocks.some(block => !block.matches('.ws-band'));
    });
    if (root.dataset.columns === '2') {
      const columns = make('div', 'ws-flow-cols');
      // Each partition retains document order; the breakpoint only changes its presentation.
      const ordinary = items.filter(item => item.dataset.span !== 'all');
      const explicit = ordinary.findIndex((item, i) => i > 0 && item.dataset.break === '1');
      const boundary = explicit > 0 ? explicit : Math.ceil(ordinary.length / 2);
      for (const partition of [ordinary.slice(0,boundary), ordinary.slice(boundary)]) {
        const visible = flowItems(partition);
        if (!visible.length) continue;
        const col = make('div', 'ws-flow-col'); col.append(...visible); columns.append(col);
      }
      main.append(...flowItems(items.filter(item => item.dataset.span === 'all')), columns);
    } else main.append(...flowItems(items));
    return true;
  }
  function afterLayout() {
    if (!storageKey) return; // first A4 font/layout pass runs before extension initialization
    indexSlots();
    // Map.groupBy는 Safari 17.4·Chrome 117부터라 학교 태블릿에서 화면 풀이 전체가 멈출 수 있다.
    const rowsByItem = new Map();
    for (const row of records.values()) {
      const rows = rowsByItem.get(row.item);
      if (rows) rows.push(row); else rowsByItem.set(row.item, [row]);
    }
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
  const narrowScreen = window.matchMedia('screen and (max-width: 719.98px)');
  function updateTableOverflow(scope) {
    const narrow = mode === 'solve' && narrowScreen.matches;
    for (const table of all(scope, 'table.ws-input-table')) {
      const overflow = narrow && table.clientWidth > 0 && table.scrollWidth > table.clientWidth + 1;
      if (table.classList.contains('ws-table-overflow') !== overflow) toggle(table, 'ws-table-overflow', overflow);
    }
  }
  function schedulePaint() {
    if (delayedPaint) return;
    delayedPaint = requestAnimationFrame(() => {
      delayedPaint = null;
      if (usable()) for (const row of records.values()) if (row.control?.resize) row.control.paint(row.value);
      if (mainNode) updateTableOverflow(mainNode);
    });
  }
  const resizeObserver = new ResizeObserver(schedulePaint);
  function syncToolbar() {
    if (viewButton) {
      viewButton.textContent = mode === 'solve' ? 'A4 보기' : '풀기 보기';
      viewButton.setAttribute('aria-pressed', String(mode === 'a4'));
    }
    if (saveButton) saveButton.disabled = clearButton.disabled = !!pendingRestore;
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
    const build = core.buildMeta();
    storageKey = `pbj-sheet:${build.edition || build.source_sha256 || salt() || location.pathname}:${teacher ? 'teacher' : 'student'}`;
    const content = document.getElementById('sheet-reveal')?.textContent.trim();
    if (canReveal && content) {
      try { initialReveal = decodeReveal(salt(), content) || {}; } catch { /* input and hashing are still available */ }
    }
    if (teacher) bindTeacherSolutions();
    indexSlots();
    const saved = readDeviceStorage(), device = readSnapshot(saved), file = readSnapshot();
    const when = saved.savedAt;
    const hasDevice = [...device].some(([row, { value, penValue }]) =>
      hasResponse({ kind: row.kind, value }) || !!penValue?.strokes.length);
    // An explicitly emptied device snapshot can still differ from a filled answer file.
    if (!teacher && (hasDevice || (file.size && device.size && when > 0))) pendingRestore = { device, file };
    else restoreSnapshot(file.size ? file : device);
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
      saveButton = uiButton('답을 넣어 저장', 'save-answers', () =>
        core.download(core.serialize(true, true), core.fileName(true).replace(/\.html$/, ' (내 답).html')));
      const prompt = runtime(make('div', 'ws-answer-prompt'));
      prompt.hidden = true;
      clearButton = uiButton('내 답 지우기', 'clear-answers', () => { prompt.hidden = false; cancel.focus(); });
      const hide = () => { prompt.hidden = true; clearButton.focus(); };
      const cancel = uiButton('취소', 'cancel-clear-answers', hide);
      prompt.append(make('span', '', '이름과 입력한 답을 모두 지울까요?'),
        uiButton('지우기', 'confirm-clear-answers', () => { clearAnswers(); hide(); }), cancel);
      core.addToolbar(viewButton, saveButton, clearButton, prompt);
    }
    core.addToolbar(scoreLabel);
    if (!teacher) {
      saveStatus = runtime(make('span', 'ws-save-status'));
      saveStatus.setAttribute('aria-live', 'polite');
      core.addToolbar(saveStatus);
    }
    narrowScreen.addEventListener('change', schedulePaint);
    window.addEventListener('beforeprint', () => {
      printing ??= mode;
      // An inline answer entered in A4 view may have wrapped since its last layout.
      if (mode === 'a4') core.layout();
      else changeView('a4');
    });
    window.addEventListener('afterprint', restorePrint);
    const initial = !teacher && !(wideScreen() && store('getItem', `${storageKey}:view`) === 'a4') ? 'solve' : 'a4';
    syncToolbar();
    if (initial !== mode) changeView(initial); else core.layout();
    if (pendingRestore) showRestorePrompt(when);
  }
  function report() {
    const visible = el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
    return { view: mode, interactive: true, slots_total: records.size,
      print_leaks: mode === 'a4' ? all(document, '.ws-solve-only, .ws-verdict, .ws-check-bar, .ws-reveal, .ws-solution').filter(visible).length : 0 };
  }

  function changedAnswer(el, source, html, options, blank) {
    const answer = plain(source).trim(), accept = options.accept ?? [];
    const mode = options.mode || 'auto';
    const numeric = numericDetails(answer), answers = [answer, ...accept.map(value => plain(value).trim())];
    let kind = 'open';
    if (answer && mode !== 'off') {
      if (mode !== 'text' && numeric) kind = 'number';
      else if (mode === 'auto' && answers.every(radicalParts)) kind = 'radical';
      else if (mode === 'text' || (mode === 'auto' && (blank ? Array.from(answer).length <= 12 : accept.length))) kind = 'text';
    }
    const shape = [];
    if (answers.some(value => value.includes('π') || num(value)?.endsWith('π'))) shape.push('π');
    if (answers.some(value => value.includes('√') || radicalParts(value))) shape.push('√');
    const attrs = { 'data-check-kind': kind, 'data-check-hash': null, 'data-check-unit': null, 'data-check': null,
      'data-input-mode': kind === 'number' ? input_mode(answers) || null : null };
    const { column, row, unit } = options.headings;
    const forms = kind === 'open' ? [] : writing_forms(kind, answers, inferred_unit(options.tail ?? '', column, row, unit));
    if (forms.length && salt()) {
      let right = answer, wrong;
      if (kind === 'number') {
        const [number, , written] = numeric;
        const pi = number.endsWith('π') ? 'π' : '', coefficient = pi ? number.slice(0, -1) : number;
        if (written === 'reduced' || written === 'unreduced') shape.push('fraction');
        if (written === 'unreduced') right = coefficient + (coefficient.includes('/') ? '' : '/1') + pi;
        const [p, q = 1n] = coefficient.split('/').map(BigInt);
        let next = p;
        do { next += q; wrong = fraction(next, q) + pi; } while (forms.includes(wrong));
      } else {
        wrong = answer + '오답';
        while (text_candidates(wrong).some(v => forms.includes(v))) wrong += '오답';
      }
      attrs['data-check'] = JSON.stringify({ mode, accept, right, wrong });
      if (canGrade) {
        if (kind === 'number' && forms.some(form => form.includes('@'))) attrs['data-check-unit'] = '1';
        attrs['data-check-hash'] = [...new Set(forms)].map(v => hash(salt(),kind,v)).join(',');
      }
    } else if (mode !== 'auto' || accept.length) {
      // Keep an explicit setting (notably off) so another edit does not turn grading back on.
      attrs['data-check'] = JSON.stringify({ mode, accept });
    }
    attrs['data-writing-shape'] = shape.join(' ');
    return { attrs, reveal: html && answer ? { answer_html: html } : undefined };
  }
  function applyAttrs(el, attrs) {
    for (const [name,value] of Object.entries(attrs)) {
      if (value == null) el.removeAttribute(name); else el.setAttribute(name, value);
    }
  }
  function bindTeacherSolutions() {
    const sections = core.teacherItems();
    if (!canReveal || sections.some(section => section.hasAttribute('data-solution-for'))) return;
    // Match the assembler's reserved IDs, including collisions with student item IDs.
    const reserved = new Set(core.items().map(item => item.dataset.id));
    const memo = sections.find(section => section.querySelector('.ws-t-list'));
    if (memo) reserved.add(memo.dataset.id);
    for (const id of Object.keys(initialReveal.items ?? {})) {
      let privateId = `t-${id}`;
      while (reserved.has(privateId)) privateId = `t-${privateId}`;
      reserved.add(privateId);
      const section = sections.find(section => section.dataset.id === privateId && section.querySelector('.ws-t-ans'));
      if (section) section.dataset.solutionFor = id;
    }
  }
  function editTeacherSolution(section, target, source, html) {
    if (!canReveal || !target.closest('.ws-t-ans, .ws-t-steps, .ws-t-rubric')) return null;
    const item = core.items().find(item => item.dataset.id === section.dataset.solutionFor);
    if (!item) return null;
    const original = itemReveal.get(item), oldSrc = target.dataset.src, oldHtml = target.innerHTML;
    const write = (text, markup, reveal) => {
      target.dataset.src = text; putHtml(target, markup);
      itemReveal.set(item, reveal);
      const panel = panels.get(item);
      if (panel) {
        panel.bar?.remove(); panel.solutionBox?.remove(); panel.compareBox?.remove(); panels.delete(item);
      }
    };
    return {
      apply() {
        target.dataset.src = source; putHtml(target, html);
        const publicParts = all(section, ':scope > .ws-t-ans, :scope > .ws-t-steps').map(el => {
          const copy = el.cloneNode(true);
          copy.removeAttribute('class');
          for (const field of all(copy, '[data-src]')) field.removeAttribute('data-src');
          return copy.outerHTML;
        });
        const rubric = all(section, ':scope > .ws-t-rubric > [data-src]').map(el => el.dataset.src).filter(text => text.trim());
        write(source, html, { ...original, solution_html: publicParts.join(''), rubric });
      },
      revert() { write(oldSrc, oldHtml, original); },
    };
  }
  function resetPanel(item) {
    const panel = panels.get(item);
    if (!panel) return;
    panel.bar?.remove(); panel.solutionBox?.remove(); panel.compareBox?.remove(); panels.delete(item);
  }
  function writeAnswer(el, result) {
    const oldShape = el.dataset.writingShape;
    applyAttrs(el, result.attrs); slotReveal.set(el, result.reveal);
    const row = recordByNode.get(el);
    if (!row) return;
    row.result = null; row.reveal?.remove(); row.reveal = undefined;
    const input = el.querySelector('.ws-in');
    if (input) applyAttrs(input, { inputmode: result.attrs['data-input-mode'] });
    if (oldShape !== el.dataset.writingShape) row.control?.syncSymbols?.();
  }
  function tableAnswerUpdates(table, editedField) {
    // 원본 표기에서 정답·대안·빈칸 바로 뒤의 글을 읽는다. 이미 붙은 해시로는 단위를 되돌릴 수 없다.
    const answers = [];
    for (const field of all(table, '[data-src]')) {
      if (field === editedField || field.closest('table') !== table || field.closest('.ws-teacher-only')) continue;
      const writing = field.closest('.ws-ans[data-slot][data-check-kind="number"]');
      if (writing && field.matches('.ws-key')) {
        answers.push([writing, field.dataset.src, field.innerHTML, json(writing.dataset.check, {}), false]);
        continue;
      }
      if (field.closest('.ws-key')) continue;
      const nodes = all(field, '.ws-blank[data-slot]');
      if (!nodes.some(el => el.dataset.checkKind === 'number')) continue;
      const blanks = [];
      core.renderMarkup(field.dataset.src, blank => blanks.push(blank));
      if (nodes.length !== blanks.length) continue;
      nodes.forEach((el, i) => {
        const { answer, accept, html, tail } = blanks[i];
        if (el.dataset.checkKind === 'number') answers.push([el, answer, html, { accept, tail }, true]);
      });
    }
    return () => {
      for (const [el, source, html, options, blank] of answers) {
        const result = changedAnswer(el, source, html, { ...options, headings: tableHeadings(el, true) }, blank);
        if (result.attrs['data-check-hash'] !== el.getAttribute('data-check-hash') ||
            result.attrs['data-check-unit'] !== el.getAttribute('data-check-unit')) writeAnswer(el, result);
      }
    };
  }
  function editTableUnit(table, value) {
    const previous = table.getAttribute('data-table-unit');
    const unit = normalizedUnit(value) || null;
    if (value.trim() && !unit) throw new Error('점, 명, cm처럼 단위 하나만 쓰세요. 단위를 없애려면 비워 두세요.');
    if (unit === previous) return null;
    const refresh = tableAnswerUpdates(table);
    const write = next => {
      applyAttrs(table, { 'data-table-unit': next });
      refresh();
      resetPanel(table.closest('.ws-item'));
    };
    return { apply: () => write(unit), revert: () => write(previous) };
  }
  function editMarkup(target, source, html, blanks) {
    const item = target.closest('.ws-item');
    if (!item) return null;
    if (core.teacherItems().includes(item)) return editTeacherSolution(item, target, source, html);
    if (!core.items().includes(item)) return null;
    const oldSrc = target.dataset.src, oldChildren = [...target.childNodes];
    const staged = make('template'); staged.innerHTML = html;
    const newChildren = [...staged.content.childNodes];
    const oldBlanks = all(target, '.ws-blank[data-slot]'), newBlanks = all(staged.content, '.ws-blank');
    const writing = target.closest('.ws-ans[data-slot]');
    const key = target.closest('.ws-key');
    const isAnswer = writing?.matches(':not([data-kind="draw"])') && !!key;
    const table = target.closest('table');
    const refresh = !key && table?.matches('.ws-data, .ws-steps, .ws-ans-table') ? tableAnswerUpdates(table, target) : null;
    const oldAttrs = isAnswer ? Object.fromEntries(['data-check-kind','data-check-hash','data-check-unit','data-check','data-input-mode','data-writing-shape'].map(name => [name,writing.getAttribute(name)])) : null;
    const oldReveal = isAnswer ? slotReveal.get(writing) : undefined;
    const updated = isAnswer ? changedAnswer(writing, source, html, { ...json(writing.dataset.check, {}), headings: tableHeadings(writing, true) }, false) : null;
    const promptFields = '.ws-q-text, .ws-sit-text [data-src], .ws-ask th [data-src]:not(.ws-ask-tag)';
    function write(text, children, result) {
      target.dataset.src = text; target.replaceChildren(...children);
      if (isAnswer) writeAnswer(writing, result);
      if (target.matches(promptFields)) {
        const strict = all(item, promptFields).some(field => {
          const copy = field.cloneNode(true);
          for (const node of all(copy, '.ws-blank, [data-sheet-runtime]')) node.remove();
          return copy.textContent.includes('기약분수');
        });
        if (strict) item.dataset.reducedFractions = '1';
        else delete item.dataset.reducedFractions;
        for (const row of records.values()) {
          if (row.item === item && writingShape(row.el).includes('fraction')) row.result = null;
        }
      }
      refresh?.();
      resetPanel(item);
    }
    const fill = target.matches('td:not(.ws-ans)') && (target.classList.contains('ws-fill') || oldBlanks.length || newBlanks.length);
    const hadFill = target.classList.contains('ws-fill');
    const nextFill = newChildren.filter(n => n.nodeType !== Node.TEXT_NODE || n.textContent.trim()).length === 1 &&
      newChildren.some(n => n.nodeType === Node.ELEMENT_NODE && n.matches('.ws-blank'));
    return {
      apply() {
        write(source, newChildren, updated);
        if (!key) for (const [i, el] of newBlanks.entries()) {
          el.dataset.slot = ''; // assigned in layout order after the edit
          const { answer, accept, html, tail } = blanks[i];
          writeAnswer(el, changedAnswer(el, answer, html, { accept, tail, headings: tableHeadings(el, true) }, true));
        }
        if (fill) target.classList.toggle('ws-fill', nextFill);
      },
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
    for (const space of all(copy, '.ws-name-space')) space.replaceWith(...space.childNodes);
    // Keep the authored underlay when the enlarged view's runtime wrapper is stripped.
    for (const viewport of all(copy, '.ws-pen-viewport')) viewport.replaceWith(...viewport.firstElementChild.childNodes);
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
  function revealData() {
    if (!canReveal) return undefined;
    const slots = Object.fromEntries([...records.values()].flatMap(row => {
      const value = slotReveal.get(row.el); return value ? [[row.slot, value]] : [];
    }));
    const items = Object.fromEntries(core.items().flatMap(item => {
      const value = itemReveal.get(item); return value ? [[item.dataset.id, value]] : [];
    }));
    return { slots, items };
  }
  function cleanDocument(copy) {
    copy.removeAttribute('data-view'); copy.removeAttribute('data-sheet-preview');
    if (preview && teacher) applyAttrs(copy, { 'data-answers': previousAnswers });
    let script = copy.querySelector('#sheet-reveal');
    const reveal = revealData();
    if (!reveal) script?.remove();
    else if (script || Object.keys(reveal.slots).length || Object.keys(reveal.items).length) {
      if (!script) {
        script = make('script'); script.id = 'sheet-reveal'; script.type = 'application/octet-stream';
        copy.querySelector('#sheet-js')?.before(script);
      }
      script.textContent = encodeReveal(salt(), reveal);
    }
    if (!canGrade) for (const el of all(copy, '[data-check-hash], [data-check-unit]')) {
      el.removeAttribute('data-check-hash');
      el.removeAttribute('data-check-unit');
    }
  }

  core.attach({ init, layoutFlow, afterLayout, studentPreview: () => preview, beforePrintButton, report,
    editMarkup, editTableUnit, copiedItem, cleanItem, cleanDocument, revealData });
  core.restorePending = () => !!pendingRestore;
  core.validPenStrokes = validPenStrokes;
  core.verdicts = () => Object.fromEntries([...records.values()].filter(r => r.auto).map(r => [r.slot, r.result]));
  Object.assign(window.sheet, { view, slots, answer, values, score, clearAnswers });
})();
