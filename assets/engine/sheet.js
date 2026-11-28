(() => {
  'use strict';

  // pbj-html-deck의 MathParser와 같은 문법·MathML. 조작 값(\val)은 활동지에 없다.
  const symbols = {
    times: '×', div: '÷', pm: '±', le: '≤', ge: '≥', ne: '≠', approx: '≈', cdot: '⋅',
    angle: '∠', triangle: '△', square: '□', perp: '⊥', parallel: '⫽', equiv: '≡',
    sim: '∽', pi: 'π', circ: '°', therefore: '∴', because: '∵', cdots: '⋯',
  };
  const escapeHTML = text => text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;' })[c]);
  const translate = (from, to) => Object.fromEntries([...from].map((c, i) => [c, to[i]]));
  const supers = translate('0123456789+-=()nix', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱˣ');
  const subs = translate('0123456789+-=()aeiox', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒₓ');
  const joinParts = parts => [parts.map(p => p[0]).join(''), parts.map(p => p[1]).join('')];
  function wrapRow(parts) {
    const [markup, plain] = joinParts(parts);
    return [parts.length === 1 ? markup : `<mrow>${markup}</mrow>`, plain];
  }
  function fractionTerm(text, denominator = false) {
    text = text.trim();
    if (denominator && /^[+−-]?[0-9.]+\s*[π√]/u.test(text)) return `(${text})`;
    let depth = 0;
    for (const [i, c] of Array.from(text).entries()) {
      if ('(['.includes(c)) depth++;
      else if (')]'.includes(c)) depth--;
      else if (depth === 0 && ('/×÷⋅·*=<>≤≥≠±,∽≈≡'.includes(c) || (i && '+−-'.includes(c)))) {
        if (c === ',' && /^[+−-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?$/.test(text)) continue;
        return `(${text})`;
      }
    }
    return text;
  }
  class MathParser {
    constructor(source, answer = false) { this.source = Array.from(source); this.pos = 0; this.answer = answer; }
    get next() { return this.source[this.pos] ?? ''; }
    fail(message, at = this.pos) {
      throw new Error(`수식 $${this.source.join('')}$ · ${at + 1}번째 글자: ${message}`);
    }
    spaces() {
      while (this.next) {
        if (/[\p{White_Space}\x1c-\x1f]/u.test(this.next)) this.pos++;
        else if (this.next === '\\') {
          const match = /^\\(?:left(?=\s*[(|])|right(?=\s*[)|]))/.exec(this.source.slice(this.pos).join(''));
          if (!match) break;
          this.pos += match[0].length;
        } else break;
      }
    }
    group(open = '{', close = '}') {
      this.spaces();
      if (this.next !== open) this.fail(`${open}가 필요합니다`);
      this.pos++;
      const result = this.sequence(close);
      if (this.next !== close) this.fail(`닫는 ${close}가 없습니다`);
      if (!result.length) this.fail(`${open}${close} 안에 값이 없습니다`);
      this.pos++;
      return wrapRow(result);
    }
    static prefixSign([, sign]) { return [`<mo form="prefix" lspace="0em" rspace="0em">${sign}</mo>`, sign]; }
    argument() {
      this.spaces();
      if (!this.next) this.fail('첨자에 값이 필요합니다');
      if (this.next === '{') return this.group();
      if (/[0-9]/.test(this.next)) {
        const digit = this.source[this.pos++];
        return [`<mn>${digit}</mn>`, digit];
      }
      const part = this.atom();
      return ['<mo>+</mo>', '<mo>−</mo>'].includes(part[0]) ? MathParser.prefixSign(part) : part;
    }
    atom() {
      this.spaces();
      if (!this.next) this.fail('값이 필요합니다');
      const start = this.pos;
      const c = this.source[this.pos++];
      if (c === '\\') {
        if (this.next === ',' || this.next === ' ') {
          this.pos++;
          return ['<mspace width="0.1667em"></mspace>', ''];
        }
        const begin = this.pos;
        while (/[A-Za-z]/.test(this.next)) this.pos++;
        const name = this.source.slice(begin, this.pos).join('');
        if (!name) this.fail('명령 이름이 필요합니다', start);
        if (Object.hasOwn(symbols, name)) {
          const symbol = symbols[name];
          if (name === 'triangle' || name === 'square') return [`<mi mathvariant="normal">${symbol}</mi>`, symbol];
          if (name === 'pi') return [`<mi>${symbol}</mi>`, symbol];
          return [`<mo>${symbol}</mo>`, symbol];
        }
        if (['sin', 'cos', 'tan'].includes(name)) {
          return [`<mo form="prefix" lspace="0em" rspace="0.1667em">${name}</mo>`, name + ' '];
        }
        if (name === 'quad') return ['<mspace width="1em"></mspace>', ' '];
        if (name === 'begin') {
          this.spaces();
          if (!this.at('{cases}')) this.fail('지원하는 환경은 cases입니다', start);
          this.pos += '{cases}'.length;
          const rows = [], texts = [];
          while (true) {
            const parts = this.sequence(undefined, true);
            if (this.next === '&') {
              this.pos++;
              const right = this.sequence(undefined, true);
              if (!right.length) this.fail('& 뒤에 식이 필요합니다');
              parts.push(...right);
            }
            if (!parts.length) this.fail('cases의 각 줄에 식을 쓰세요');
            const [markup, text] = joinParts(parts);
            rows.push(`<mtr><mtd>${markup}</mtd></mtr>`); texts.push(text);
            if (this.at('\\end{cases}')) { this.pos += '\\end{cases}'.length; break; }
            if (!this.at('\\\\')) this.fail('cases의 줄 구분이나 닫는 명령을 확인하세요');
            this.pos += 2;
          }
          return [`<mrow><mo stretchy="true">{</mo><mtable columnalign="left">${rows.join('')}</mtable></mrow>`, '{' + texts.join('; ')];
        }
        if (name === 'frac' || name === 'dfrac') {
          const a = this.group(), b = this.group();
          const plain = this.answer ? `${fractionTerm(a[1])}/${fractionTerm(b[1], true)}` : `(${a[1]})/(${b[1]})`;
          return [`<mfrac>${a[0]}${b[0]}</mfrac>`, plain];
        }
        if (name === 'sqrt') {
          this.spaces();
          const index = this.next === '[' ? this.group('[', ']') : null;
          const value = this.group();
          if (index) return [`<mroot>${value[0]}${index[0]}</mroot>`, `√[${index[1]}](${value[1]})`];
          const body = this.answer ? value[1].trim() : value[1];
          const simple = this.answer ? /^[0-9]+$/.test(body) : Array.from(body).length === 1;
          return [`<msqrt>${value[0]}</msqrt>`, `√${simple ? body : `(${body})`}`];
        }
        if (name === 'mathrm') {
          const [markup, plain] = this.group();
          return [`<mstyle mathvariant="normal">${markup}</mstyle>`, plain];
        }
        if (['overline', 'overarc', 'overgroup'].includes(name)) {
          const [markup, plain] = this.group();
          return name === 'overline' ? [`<mrow class="seg">${markup}</mrow>`, this.answer ? plain : plain + '̅']
            : [`<mover>${markup}<mo stretchy="true">⏜</mo></mover>`, this.answer ? plain : plain + '⌒'];
        }
        if (name === 'text' || name === 'mathit') {
          this.spaces();
          if (this.next !== '{') this.fail(`\\${name} 뒤에 {글자}가 필요합니다`);
          const start = this.pos + 1;
          const end = this.source.indexOf('}', start);
          if (end < 0 || this.source.slice(start, end).includes('{')) this.fail(`\\${name}의 중괄호를 확인하세요`);
          const value = this.source.slice(start, end).join('');
          this.pos = end + 1;
          if (name === 'mathit') {
            if (!/^[A-Za-z]$/.test(value)) this.fail('\\mathit에는 영문 한 글자를 쓰세요', start);
            return [`<mi>${value}</mi>`, value];
          }
          return [`<mtext>${escapeHTML(value)}</mtext>`, value];
        }
        this.fail(`모르는 명령 \\${name}입니다`, start);
      }
      if (c === '{') { this.pos--; return this.group(); }
      if (c === '(' || c === '|') {
        const close = c === '(' ? ')' : '|';
        const parts = this.sequence(close);
        if (this.next !== close) this.fail(`닫는 ${close}가 없습니다`);
        this.pos++;
        const [markup, plain] = wrapRow(parts);
        const attr = c === '(' ? ' stretchy="false"' : '';
        return [`<mrow><mo${attr}>${c}</mo>${markup}<mo${attr}>${close}</mo></mrow>`, c + plain + close];
      }
      if (/[A-Za-z]/.test(c)) return [`<mi${/[A-Z]/.test(c) ? ' mathvariant="normal"' : ''}>${c}</mi>`, c];
      if (/[0-9.]/.test(c)) {
        const rest = this.source.slice(start).join('');
        const value = rest.match(/^[0-9]+(?:\{,\}[0-9]+)+(?:\.[0-9]*)?/)?.[0]
          || rest.match(/^(?:\d+(?:\.\d*)?|\.\d+)/)?.[0];
        if (!value) return ['<mo>.</mo>', '.'];
        this.pos = start + value.length;
        const number = value.replace(/\{,\}/g, ',');
        return [`<mn>${number}</mn>`, number];
      }
      if ('+-=<>,!'.includes(c)) {
        const value = c === '-' ? '−' : c;
        return [`<mo>${escapeHTML(value)}</mo>`, value];
      }
      this.fail(`지원하지 않는 글자 ${JSON.stringify(c)}입니다`, start);
    }
    at(token) { return Array.from(token).every((c, i) => this.source[this.pos + i] === c); }
    sequence(closing, caseRow = false) {
      const result = [];
      while (true) {
        this.spaces();
        if (caseRow && (this.next === '&' || this.at('\\\\') || this.at('\\end{cases}'))) return result;
        if (!this.next || this.next === closing) return result;
        if ('})]'.includes(this.next)) this.fail('짝이 맞지 않는 닫는 괄호입니다');
        if ('^_'.includes(this.next)) this.fail('첨자 앞에 값이 필요합니다');
        let base = this.atom();
        if (['+', '−'].includes(base[1]) && base[0].startsWith('<mo>')) {
          const previous = result.findLast(part => !part[0].startsWith('<mspace '));
          if (!previous || (/^<mo[ >]/.test(previous[0]) && !['!', '°'].includes(previous[1]))) base = MathParser.prefixSign(base);
        }
        const previous = result.at(-1);
        if (base[0] === '<mo>°</mo>' && previous && /^<mn>[^<]+<\/mn>$/.test(previous[0])) {
          previous[0] = previous[0].slice(0, -5) + '°</mn>';
          previous[1] += '°';
          continue;
        }
        let sup, sub;
        while (true) {
          this.spaces();
          if (!this.next || !'^_'.includes(this.next)) break;
          const op = this.source[this.pos++];
          if (op === '^' ? sup : sub) this.fail('같은 첨자가 중복되었습니다');
          if (op === '^') sup = this.argument();
          else sub = this.argument();
        }
        if (sup || sub) {
          const markup = sup && sub ? `<msubsup>${base[0]}${sub[0]}${sup[0]}</msubsup>`
            : sup ? `<msup>${base[0]}${sup[0]}</msup>` : `<msub>${base[0]}${sub[0]}</msub>`;
          let plain = base[1];
          for (const [arg, table, prefix] of [[sub, subs, '_'], [sup, supers, '^']]) {
            if (!arg) continue;
            if (this.answer) plain += prefix === '^' && arg[1] === '°' ? '°' : prefix + fractionTerm(arg[1]);
            else plain += Array.from(arg[1]).every(c => Object.hasOwn(table, c))
              ? Array.from(arg[1], c => table[c]).join('') : `${prefix}(${arg[1]})`;
          }
          base = [markup, plain];
        }
        if (this.answer && result.length) {
          const [htmlBefore, before] = result.at(-1), [htmlNow, now] = base;
          if ((htmlBefore.startsWith('<mfrac>') && /^[0-9√]/u.test(now)) ||
              (htmlBefore.startsWith('<msqrt>') && /^[0-9]/.test(now))) {
            result[result.length - 1] = [htmlBefore, `(${before})`];
          } else if (htmlNow.startsWith('<mfrac>') && /[0-9π)]$/u.test(before)) {
            base = [htmlNow, `(${now})`];
          }
        }
        result.push(base);
      }
    }
    parse() {
      const parts = this.sequence();
      if (!parts.length) this.fail('빈 수식입니다', 0);
      const [markup, plain] = joinParts(parts);
      return [`<math>${markup}</math>`, plain];
    }
  }

  function joinInlineParts(parts) {
    const rendered = [], word = [];
    let bold = false;
    function setBold(active) {
      if (active === bold) return;
      rendered.push(active ? '<strong>' : '</strong>');
      bold = active;
    }
    function emit([markup, , , strong]) { setBold(strong); rendered.push(markup); }
    function flush() {
      const hasBlank = word.some(part => part[2] === 'blank');
      const grouped = word.length > 1 && (hasBlank || word.some(part => part[2] === 'math'));
      if (grouped) { setBold(false); rendered.push(`<span class="${hasBlank ? 'ws-blank-word' : 'ws-math-word'}"><span>`); }
      for (const part of word) emit(part);
      if (grouped) { setBold(false); rendered.push('</span></span>'); }
      word.length = 0;
    }
    for (const part of parts) {
      if (part[2] === 'boundary') { flush(); emit(part); }
      else { if (part[2] === 'blank') flush(); word.push(part); }
    }
    flush();
    setBold(false);
    return [rendered.join(''), parts.map(part => part[1]).join('')];
  }

  function parseMarkup(source, onBlank, answer = false) {
    if (/[\r\n\p{Zl}\p{Zp}]/u.test(source)) throw new Error('한 필드에는 줄을 바꾸지 마세요.');
    let pos = 0;
    const at = token => source.startsWith(token, pos);
    const fail = (token, start) => { throw new Error(`${start + 1}번째 글자: 닫는 ${token}가 없습니다.`); };
    function sequence(stops = [], inBlank = false, bold = false) {
      const parts = [];
      while (pos < source.length && !stops.some(at)) {
        const start = pos, c = source[pos++];
        const next = source[pos];
        if (c === '\\' && next && '$\\{'.includes(next)) {
          pos++; parts.push([escapeHTML(next), next, 'text', bold]);
        } else if (c === '$') {
          const begin = pos;
          while (pos < source.length && !at('$')) {
            if (at('\\$') || at('\\\\')) pos++;
            pos++;
          }
          if (!at('$')) fail('$', start);
          parts.push([...new MathParser(source.slice(begin, pos++), answer).parse(), 'math', bold]);
        } else if (c === '*' && at('*')) {
          pos++;
          for (const part of sequence(['**', ...stops], inBlank, true)) parts.push(part);
          if (!at('**')) fail('**', start);
          pos += 2;
        } else if (c === '{' && at('{') && !inBlank) {
          pos++;
          const begin = pos;
          while (pos < source.length && !at('|') && !at('}}')) pos++;
          const mark = source.slice(begin, pos).trim();
          let html = '', text = '', width, answer = '';
          const accept = [], lengths = [];
          if (at('|')) {
            const answerStart = ++pos;
            [html, text] = joinInlineParts(sequence(['|', '}}', ';;'], true));
            answer = source.slice(answerStart, pos);
            lengths.push(Array.from(text.trim()).length);
            while (at(';;')) {
              const begin = pos += 2;
              const [, alternative] = joinInlineParts(sequence(['|', '}}', ';;'], true));
              lengths.push(Array.from(alternative.trim()).length);
              accept.push(source.slice(begin, pos));
            }
            if ([answer, ...accept].some(v => !v.trim())) throw new Error('빈칸의 대안 정답을 비워 둘 수 없습니다.');
            if (at('|')) {
              const end = source.indexOf('}}', ++pos);
              if (end < 0) fail('}}', start);
              const raw = source.slice(pos, end).trim();
              if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw) || !(Number(raw) > 0 && Number.isFinite(Number(raw)))) {
                throw new Error('빈칸 너비를 양수(mm)로 쓰세요.');
              }
              width = Number(raw);
              pos = end;
            }
          }
          if (!at('}}')) fail('}}', start);
          if (!mark && !text.trim()) throw new Error('빈칸에 기호나 정답을 쓰세요.');
          pos += 2;
          if (width == null && lengths.length) width = Math.max(mark ? 9 : 18, Math.max(...lengths) * 4.5);
          const markAttr = mark ? ` data-mark="${escapeHTML(mark)}"` : '';
          const widthAttr = width == null ? '' : ` style="--ws-blank-min:${width}mm"`;
          const markNode = mark ? `<span class="ws-blank-mark">${escapeHTML(mark)}</span>` : '';
          const key = text.trim() ? `<span class="ws-key">${html}</span>` : '';
          const tail = source.slice(pos);
          onBlank?.({ answer, accept, html, tail });
          parts.push([`<span class="ws-blank"${markAttr}${widthAttr}>${markNode}${key}</span>`, text || mark, /^[가-힣]/.test(tail) ? 'blank' : 'boundary', bold]);
        } else {
          parts.push([escapeHTML(c), c, /[\p{White_Space}\x1c-\x1f]/u.test(c) ? 'boundary' : 'text', bold]);
        }
      }
      return parts;
    }
    return joinInlineParts(sequence());
  }
  const renderMarkup = (source, onBlank) => parseMarkup(source, onBlank)[0];
  const answerMarkup = source => parseMarkup(source, undefined, true);

  const root = document.documentElement;
  const teacher = root.dataset.role === 'teacher';
  const columns = root.dataset.columns === '2' ? 2 : 1;
  const pxPerMm = 96 / 25.4;
  const epsilon = 1 / 32; // Chrome의 서브픽셀 반올림 여유
  const growFill = 0.985;
  const marks = Array.from('㉠㉡㉢㉣㉤㉥㉦㉧㉨㉩㉪㉫㉬㉭㉮㉯㉰㉱㉲㉳㉴㉵㉶㉷㉸㉹㉺㉻');
  const all = (node, selector) => Array.from(node.querySelectorAll(selector));
  const byId = id => document.getElementById(id);
  const rect = node => node.getBoundingClientRect();
  const mm = px => Number((px / pxPerMm).toFixed(3));
  const fraction = n => Number(n.toFixed(5));
  const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
  const styleNames = ['--sheet-body-scale', '--sheet-question-scale', '--sheet-head-font', '--sheet-question-font', '--sheet-body-font'];
  const privateContent = '.ws-key, .ws-teacher-only';
  const answersOn = () => teacher && root.dataset.answers === 'on';
  const boxKind = box => box.dataset.kind === 'draw' ? 'draw' : 'text';
  const boxLines = box => clamp(Number.parseInt(box.dataset.lines, 10) || 3, 2, 30);
  const drawHeight = box => Number(box.dataset.heightMm) > 0 && Number.isFinite(Number(box.dataset.heightMm)) ? Number(box.dataset.heightMm) : 30;
  const autoGrow = box => box.dataset.grow === '1';
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  let src, teacherSrc, main, userStyle, fontStyle, toolbar, notice, answerButton, editButton, undoButton;
  let studentItems = [], teacherItems = [], pages = [], edit = false, draft, saveHandle, saving = false;
  let fontRun = 0, activeFont = false, copySerial = 0, solve;
  let originalEditionContent, lastEditionContent, lastEdition;
  const undoStack = [];
  const originalMarks = new WeakMap();
  let boxBases = new WeakMap();
  const drawWidths = new WeakMap();
  const drawRatio = box => (drawWidths.get(box) || box.clientWidth || 1) / (drawHeight(box) * pxPerMm);

  function scale(name) { return Number.parseFloat(getComputedStyle(root).getPropertyValue(name)) || 1; }
  function newPage(isTeacher, withTop = false) {
    const wrap = make('div', 'ws-paper');
    const badge = make('span', 'ws-badge');
    const page = make('section', isTeacher ? 'ws-page ws-tpage' : 'ws-page');
    const inside = make('div', 'ws-page-in');
    const count = isTeacher ? 1 : columns;
    const top = withTop ? make('div', 'ws-top') : null;
    const place = count === 2 ? make('div', 'ws-cols') : inside;
    const slots = Array.from({ length: count }, () => make('div', 'ws-slot'));
    if (top) inside.append(top);
    if (place !== inside) inside.append(place);
    place.append(...slots);
    page.append(inside);
    wrap.append(badge, page);
    main.append(wrap);
    const info = { page, badge, top, inside, slots, isTeacher, base: [] };
    pages.push(info);
    return info;
  }
  function usedHeight(slot) {
    return slot?.lastElementChild ? rect(slot.lastElementChild).bottom - rect(slot).top : 0;
  }
  function fits(slot) { return usedHeight(slot) <= rect(slot).height + epsilon; }
  function placeItems(items, isTeacher) {
    if (!items.length) return;
    const wide = !isTeacher && columns === 2;
    let current = newPage(isTeacher, wide), index = 0;
    const advance = () => {
      if (++index >= current.slots.length) { current = newPage(isTeacher); index = 0; }
      return current.slots[index];
    };
    // 전폭 머리말은 원본의 선두에 모여 있으며 첫 쪽에서만 사용한다.
    let start = 0;
    if (wide) {
      while (items[start]?.dataset.span === 'all') current.top.append(items[start++]);
      if (!fits(current.top)) {
        current.top.classList.add('ws-tight');
        if (!fits(current.top)) current.top.lastElementChild?.classList.add('too-tall');
      }
    }
    for (const item of items.slice(start)) {
      let slot = current.slots[index];
      if (item.dataset.break === '1' && slot.childElementCount) slot = advance();
      const occupied = slot.childElementCount > 0;
      slot.append(item);
      if (!fits(slot) && occupied) {
        const wasTight = slot.classList.contains('ws-tight');
        slot.classList.add('ws-tight');
        if (fits(slot)) continue;
        item.remove();
        if (!wasTight) slot.classList.remove('ws-tight');
        slot = advance();
        slot.append(item);
      }
      // 전폭 머리말 때문에 좁아진 첫 쪽에서만 못 들어갈 때는 다음 쪽에서 시도한다.
      if (!fits(slot) && current.top?.childElementCount && rect(item).height <= 277 * pxPerMm + epsilon) {
        item.remove();
        current = newPage(isTeacher);
        index = 0;
        slot = current.slots[index];
        slot.append(item);
      }
      if (!fits(slot)) item.classList.add('too-tall');
    }
  }

  function numberItems() {
    let question = 0, blank = 0;
    const order = new Map(), numbers = new Map();
    for (const [index, item] of studentItems.entries()) {
      const id = item.dataset.id;
      order.set(id, index);
      for (const el of all(item, '.ws-qn')) {
        if (el.closest(privateContent)) continue;
        el.textContent = `${++question}.`;
        if (!numbers.has(id)) numbers.set(id, question);
      }
      const local = new Map();
      // 정답 안의 글자나 교사 전용 그림은 학생의 빈칸 번호를 소비하지 않는다.
      for (const el of all(item, '.ws-blank[data-mark]')) {
        if (el.closest(privateContent)) continue;
        const display = marks[blank++] ?? `(${blank})`;
        const symbol = el.querySelector(':scope > .ws-blank-mark');
        if (symbol) symbol.textContent = display;
        if (!local.has(el.dataset.mark)) local.set(el.dataset.mark, display);
      }
      for (const el of all(item, '.ws-mark')) {
        if (!originalMarks.has(el)) originalMarks.set(el, el.dataset.mark ?? el.textContent.trim());
        const value = originalMarks.get(el);
        el.textContent = local.get(value) ?? value;
      }
    }
    orderSolutions(order, numbers);
  }
  function solutionLabel(item) {
    const visibleText = node => {
      if (!node) return '';
      const copy = node.cloneNode(true);
      for (const hidden of all(copy, `${privateContent}, [data-sheet-runtime]`)) hidden.remove();
      return copy.textContent.replace(/\s+/g, ' ').trim();
    };
    for (const heading of all(item, '.ws-heading').filter(node => !node.closest(privateContent))) {
      const title = [visibleText(heading.querySelector('.ws-heading-tag')), visibleText(heading.querySelector('.ws-heading-text'))]
        .filter(Boolean).join(' · ');
      if (title) return title;
    }
    for (const question of all(item, '.ws-q-text').filter(node => !node.closest(privateContent))) {
      const text = Array.from(visibleText(question));
      if (text.length) return text.slice(0, 30).join('') + (text.length > 30 ? '…' : '');
    }
    return `${item.dataset.id} 풀이`;
  }
  function orderSolutions(order, numbers) {
    // 메모와 연결되지 않은 교사 쪽은 제자리에 두고, 연결된 풀이만 문항 순서에 맞춘다.
    const linked = section => order.has(section.dataset.solutionFor);
    const solutions = teacherItems.filter(linked).sort((a, b) => order.get(a.dataset.solutionFor) - order.get(b.dataset.solutionFor));
    let index = 0;
    for (const [i, section] of teacherItems.entries()) {
      if (!linked(section)) continue;
      const current = teacherItems[i] = solutions[index++];
      const id = current.dataset.solutionFor, label = current.querySelector('.ws-t-q');
      if (label) label.textContent = numbers.has(id) ? `${numbers.get(id)}번` : solutionLabel(studentItems[order.get(id)]);
    }
  }
  function prepareBoxes(items) {
    const bodyScale = scale('--sheet-body-scale');
    boxBases = new WeakMap();
    for (const item of items) {
      item.classList.remove('too-tall');
      for (const box of all(item, '.ws-ans')) {
        const base = boxKind(box) === 'draw' ? drawHeight(box) : (5.64 * boxLines(box) - 1.1) * bodyScale;
        boxBases.set(box, base);
        box.style.height = root.dataset.view === 'solve' && boxKind(box) === 'draw' && box.hasAttribute('data-slot')
          ? '' : `${base}mm`;
        delete box.dataset.extraMm;
      }
    }
  }
  function growSlot(slot, used) {
    const target = rect(slot).height * growFill;
    const remaining = target - used;
    if (remaining <= epsilon) return;
    const boxes = all(slot, '.ws-ans[data-grow="1"]').filter(box => box.getClientRects().length);
    const sum = boxes.reduce((total, box) => total + boxBases.get(box), 0);
    if (!sum) return;
    const apply = ratio => {
      for (const box of boxes) {
        const base = boxBases.get(box);
        const extra = Number((remaining / pxPerMm * (base / sum) * ratio).toFixed(4));
        box.style.height = `${base + extra}mm`;
        if (extra) box.dataset.extraMm = String(extra);
        else delete box.dataset.extraMm;
      }
      return usedHeight(slot);
    };
    let high = 1, low = 0, result = apply(high);
    // 같은 행의 두 칸이나 긴 라벨 때문에 height 합과 실제 증가량이 다를 수 있다.
    while (result < target - .25 && high < 16) { low = high; high *= 2; result = apply(high); }
    if (result <= target + epsilon) return;
    for (let step = 0; step < 14; step++) {
      const mid = (low + high) / 2;
      if (apply(mid) <= target + epsilon) low = mid;
      else high = mid;
    }
    apply(low);
  }
  function slotUsage(info, i, base = false) {
    const slot = info.slots[i];
    const max = rect(slot).height;
    const used = base ? (info.base[i] ?? 0) : usedHeight(slot);
    const topOver = i === 0 && info.top ? usedHeight(info.top) - rect(info.top).height : 0;
    return { fill: max > epsilon ? fraction(used / max) : 0, overflow: Math.max(0, used - max, topOver) };
  }
  function layout() {
    if (!main) return;
    for (const node of all(main, '.ws-tools')) node.remove();
    numberItems();
    src.append(...studentItems);
    teacherSrc?.append(...teacherItems);
    main.replaceChildren();
    pages = [];
    prepareBoxes([...studentItems, ...teacherItems]);
    const visible = studentItems.filter(item => answersOn() || !item.matches('.ws-teacher-only'));
    if (solve?.layoutFlow(main, visible)) { solve.afterLayout(); return; }
    main.id = 'sheet-pages';
    placeItems(visible.filter(item => !item.matches('.ws-solve-only')), false);
    if (answersOn()) placeItems(teacherItems, true);
    // Reuse the A4 content width in solve view, even after the columns stack or a row grows.
    for (const box of all(main, '.ws-ans[data-kind="draw"][data-slot]')) {
      const css = getComputedStyle(box);
      const width = box.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
      if (width > 0) drawWidths.set(box, width);
    }
    for (const [i, info] of pages.entries()) {
      info.slots.forEach((slot, j) => {
        const used = usedHeight(slot);
        info.base[j] = used;
        if (!info.isTeacher) growSlot(slot, used);
      });
      const usage = info.slots.map((_, j) => slotUsage(info, j));
      const fill = Math.max(...usage.map(s => s.fill));
      const tooTall = usage.some(s => s.overflow > epsilon) || !!info.page.querySelector('.too-tall');
      info.badge.classList.toggle('ws-over', tooTall);
      info.badge.textContent = `${i + 1}쪽 · ${Math.round(fill * 100)}%${tooTall ? ' · 넘침' : ''}`;
      info.page.setAttribute('aria-label', `${info.isTeacher ? '교사용 ' : ''}${i + 1}쪽`);
    }
    if (edit) addItemTools();
    solve?.afterLayout();
  }

  function hasTextOverflow(el) {
    if (!el.getClientRects().length) return false;
    const bounds = rect(el), css = getComputedStyle(el);
    const inset = side => Number.parseFloat(css[`padding${side}`]) + Number.parseFloat(css[`border${side}Width`]);
    const left = bounds.left + inset('Left'), right = bounds.right - inset('Right');
    const range = document.createRange();
    const text = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.textContent.trim() && !node.parentElement.closest('.ws-tools, math, svg') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
    // 가로는 칸 안쪽을, 세로는 글꼴의 행 경계 돌출을 감안해 칸 바깥을 잰다.
    const outside = r => (r.width || r.height) && (r.left < left - 1.5 || r.right > right + 1.5 || r.top < bounds.top - 2 || r.bottom > bounds.bottom + 2);
    for (let node; (node = text.nextNode());) {
      range.selectNodeContents(node);
      if (Array.from(range.getClientRects()).some(outside)) return true;
    }
    return all(el, 'math, .ws-blank').some(node => outside(rect(node)));
  }
  function fontUsed(family) {
    const style = getComputedStyle(root);
    return ['--sheet-head-font', '--sheet-question-font', '--sheet-body-font'].some(role =>
      style.getPropertyValue(role).split(',')[0].trim().replace(/^['"]|['"]$/g, '') === family);
  }
  function fontApplied(family) {
    const matching = Array.from(document.fonts).filter(face => face.family.replace(/^['"]|['"]$/g, '') === family);
    // check()는 선언이 없는 이름에도 true이므로, 실제 로드된 내장 face를 확인한다.
    if (!matching.some(face => face.status === 'loaded') || !document.fonts.check(`10pt "${family}"`, '가')) return false;
    return !!main && all(main, '.ws-page, .ws-head, .ws-band, .ws-t-h, .ws-t-q, .ws-q').some(el =>
      el.getClientRects().length && getComputedStyle(el).fontFamily.split(',')[0].trim().replace(/^['"]|['"]$/g, '') === family);
  }
  function report() {
    const result = {
      role: teacher ? 'teacher' : 'student', kind: root.dataset.kind || '수업', columns,
      pages: [], items: [], boxes: [], overflow: [],
      // 이 활동지가 실제로 쓰는 내장 글꼴만 확인한다(수업형은 명조를 쓰지 않는다).
      fonts: Object.fromEntries(['Sheet Sans', 'Sheet Serif'].filter(fontUsed).map(family => [family, fontApplied(family)])),
      images: { total: 0, broken: 0 }, math: { total: 0, errors: 0 }, issues: [],
    };
    const issue = (code, page, item, msg) => result.issues.push({ level: 'error', code, page, item, msg });
    const isVisible = node => node.getClientRects().length > 0;
    for (const [i, info] of pages.entries()) {
      const n = i + 1;
      const slots = info.slots.map((slot, j) => {
        const nodes = [...(j === 0 && info.top ? info.top.children : []), ...slot.children];
        for (const item of nodes) {
          const id = item.dataset.id || '';
          const tooTall = item.classList.contains('too-tall');
          result.items.push({ id, page: n, slot: j, height_mm: mm(rect(item).height), too_tall: tooTall });
          if (tooTall) issue('too-tall', n, id, '문항 묶음이 빈 칸에도 들어가지 않습니다.');
          all(item, '.ws-ans').forEach((box, index) => {
            if (!isVisible(box)) return;
            const size = rect(box), kind = boxKind(box);
            const keyText = all(box, '.ws-key').filter(key => !key.parentElement.closest('.ws-key')).map(key => key.textContent.trim()).filter(Boolean).join(' ');
            result.boxes.push({ item: id, index, kind, lines: kind === 'draw' ? null : boxLines(box), grow: autoGrow(box),
              height_mm: mm(size.height), width_mm: mm(size.width), key_text: keyText });
          });
          for (const el of all(item, 'td, th, .ws-q, .ws-text, .ws-choices li, figcaption, .ws-t-list li, .ws-t-steps li, .ws-t-ans, .ws-t-rubric, .ws-t-alt, .ws-t-mis')) {
            if (!hasTextOverflow(el)) continue;
            const cls = Array.from(el.classList).find(c => c.startsWith('ws-')) || el.localName;
            const what = `${cls} 글자가 칸을 넘침`;
            result.overflow.push({ item: id, what });
            issue('text-overflow', n, id, what);
          }
          const images = all(item, 'img, svg:not(svg svg)').filter(isVisible);
          result.images.total += images.length;
          for (const img of images) {
            if (img.localName === 'img' && (!img.complete || !img.naturalWidth)) {
              result.images.broken++;
              issue('image-broken', n, id, '불러오지 못한 그림이 있습니다.');
            }
          }
          result.math.total += all(item, 'math').filter(isVisible).length;
          const errors = all(item, 'math merror').filter(isVisible).length;
          result.math.errors += errors;
          if (errors) issue('math-error', n, id, '렌더링 오류가 있는 수식이 있습니다.');
        }
        const { fill, overflow } = slotUsage(info, j);
        if (overflow > .5 * pxPerMm) issue('page-overflow', n, nodes.at(-1)?.dataset.id || '', '쪽에 들어가지 않는 내용이 있습니다.');
        return { fill, base_fill: slotUsage(info, j, true).fill, overflow_mm: mm(overflow), items: nodes.map(item => item.dataset.id || '') };
      });
      result.pages.push({ n, teacher: info.isTeacher, slots });
    }
    return Object.assign(result, {
      view: 'a4', interactive: false, slots_total: 0, print_leaks: 0,
      overflow_x: Math.max(0, root.scrollWidth - root.clientWidth),
    }, solve?.report());
  }

  function cleanItem(item, restoreMark, keepValues = false) {
    const copy = item.cloneNode(true);
    solve?.cleanItem(item, copy, keepValues);
    for (const el of all(copy, '.ws-tools')) el.remove();
    copy.classList.remove('too-tall');
    for (const el of all(copy, '.ws-ans')) {
      el.style.removeProperty('height');
      if (!el.getAttribute('style')) el.removeAttribute('style');
      delete el.dataset.extraMm;
    }
    if (restoreMark) {
      const originals = all(item, '.ws-mark');
      all(copy, '.ws-mark').forEach((el, i) => {
        if (originalMarks.has(originals[i])) el.textContent = originalMarks.get(originals[i]);
      });
    }
    return copy;
  }
  function stripTeacher(node) {
    for (const el of all(node, privateContent)) el.remove();
    for (const el of all(node, '.ws-key-choice')) {
      el.classList.remove('ws-key-choice');
      if (!el.getAttribute('class')) el.removeAttribute('class');
    }
    for (const el of all(node, '[data-src], [data-check]')) {
      el.removeAttribute('data-src');
      el.removeAttribute('data-check');
    }
  }
  function editionContent() {
    const items = studentItems.filter(item => !item.matches('.ws-teacher-only')).map(item => {
      const copy = cleanItem(item, true);
      for (const node of all(copy, '[data-sheet-runtime], .ws-teacher-only')) node.remove();
      return copy.outerHTML;
    });
    return JSON.stringify([items, solve?.revealData()]);
  }
  function buildMeta() {
    let build = {};
    try { build = JSON.parse(document.querySelector('meta[name="sheet-build"]')?.content || '{}') || {}; }
    catch { /* Older files may not carry build metadata. */ }
    if (originalEditionContent !== undefined) {
      const content = editionContent();
      if (content !== originalEditionContent) {
        if (content !== lastEditionContent) {
          lastEditionContent = content;
          const bytes = crypto.getRandomValues(new Uint8Array(16));
          lastEdition = 'e-' + Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
        }
        return { ...build, edition: lastEdition };
      }
    }
    return build;
  }
  function serialize(asStudent = false, keepValues = false) {
    const copy = root.cloneNode(true);
    copy.removeAttribute('data-sheet-edit');
    copy.removeAttribute('data-sheet-preparing');
    copy.dataset.savedAt = new Date().toISOString();
    copy.querySelector('#sheet-src')?.replaceChildren(...studentItems.map(item => cleanItem(item, !asStudent, keepValues)));
    solve?.cleanDocument(copy);
    copy.querySelector('#sheet-teacher')?.replaceChildren(...teacherItems.map(item => cleanItem(item, true)));
    if (teacher) {
      const build = buildMeta();
      if (build.edition) {
        let meta = copy.querySelector('meta[name="sheet-build"]');
        if (!meta) { meta = make('meta'); meta.name = 'sheet-build'; copy.querySelector('head').append(meta); }
        meta.content = JSON.stringify(build);
      }
    }
    for (const el of all(copy, '[data-sheet-runtime]')) el.remove();
    for (const el of all(copy, '[contenteditable]')) el.removeAttribute('contenteditable');
    if (asStudent || !teacher) {
      stripTeacher(copy);
      copy.querySelector('#sheet-teacher')?.remove();
      copy.dataset.role = 'student';
      delete copy.dataset.answers;
      const title = copy.querySelector('title');
      if (title) title.textContent = title.textContent.replace(/\s*\(교사용\)\s*$/, '');
    }
    return '<!doctype html>\n' + copy.outerHTML;
  }
  function setAnswers(on) {
    if (!teacher) return;
    const state = on ? 'on' : 'off';
    if (root.dataset.answers === state) return;
    if (solve?.studentPreview()) return;
    cancelDraft();
    root.dataset.answers = state;
    answerButton?.setAttribute('aria-pressed', String(answersOn()));
    layout();
  }

  function announce(message = '') { if (notice) notice.textContent = message; }
  function button(label, action, fn, { title, pressed, disabled } = {}) {
    const el = make('button', '', label);
    el.type = 'button';
    el.dataset.sheetAction = action;
    if (title) { el.title = title; el.setAttribute('aria-label', title); }
    if (pressed != null) el.setAttribute('aria-pressed', String(pressed));
    el.disabled = Boolean(disabled);
    el.addEventListener('click', fn);
    return el;
  }
  function updateUndo() { if (undoButton) undoButton.disabled = !undoStack.length; }
  function change(apply, revert) {
    cancelDraft();
    apply();
    undoStack.push(revert);
    if (undoStack.length > 100) undoStack.shift();
    announce();
    updateUndo();
    layout();
  }
  function undo() {
    cancelDraft();
    const revert = undoStack.pop();
    if (!revert) return;
    revert();
    updateUndo();
    layout();
    announce();
  }
  function changeAttribute(node, attr, value) {
    const old = node.getAttribute(attr);
    const write = v => v == null ? node.removeAttribute(attr) : node.setAttribute(attr, v);
    if (value !== old) change(() => write(value), () => write(old));
  }
  function deleteItem(list, item, index) {
    const linked = list === studentItems
      ? teacherItems.map((section, i) => ({ section, i })).filter(({ section }) => section.dataset.solutionFor === item.dataset.id)
      : [];
    change(() => {
      list.splice(index, 1); item.remove();
      for (const { section, i } of [...linked].reverse()) { teacherItems.splice(i, 1); section.remove(); }
    }, () => {
      list.splice(index, 0, item);
      for (const { section, i } of linked) teacherItems.splice(i, 0, section);
    });
  }
  function setEdit(on) {
    cancelDraft();
    edit = on;
    root.toggleAttribute('data-sheet-edit', on);
    editButton.setAttribute('aria-pressed', String(on));
    for (const node of all(main, '.ws-tools')) node.remove();
    if (on) addItemTools();
  }
  function addItemTools() {
    for (const list of [studentItems, teacherItems]) {
      const firstColumn = list === studentItems && columns === 2;
      for (const [i, item] of list.entries()) {
        if (!main.contains(item) || !item.getClientRects().length) continue;
        const head = item.dataset.id === 'head';
        const span = node => firstColumn && node?.dataset.span === 'all';
        const canMove = j => !head && !!list[j] && list[j].dataset.id !== 'head' && span(item) === span(list[j]);
        const focusTool = (action, editAction) => {
          const hasFocus = document.activeElement?.dataset.sheetAction === action;
          editAction();
          if (hasFocus) {
            const next = item.querySelector(`[data-sheet-action="${action}"]:not(:disabled)`);
            (next?.getClientRects().length ? next : editButton).focus({ preventScroll: true });
          }
        };
        const move = j => {
          const swap = () => { [list[i], list[j]] = [list[j], list[i]]; };
          change(swap, swap);
        };
        if (!head) {
          const tools = make('div', 'ws-tools ws-item-tools');
          tools.setAttribute('role', 'group');
          tools.setAttribute('aria-label', '문항 편집');
          const linked = list === teacherItems && studentItems.some(node => node.dataset.id === item.dataset.solutionFor);
          tools.append(
            button('↑', 'up', () => focusTool('up', () => move(i - 1)), { title: '위로 이동', disabled: linked || !canMove(i - 1) }),
            button('↓', 'down', () => focusTool('down', () => move(i + 1)), { title: '아래로 이동', disabled: linked || !canMove(i + 1) }),
            button(firstColumn ? '새 단에서' : '새 쪽에서', 'break', () => focusTool('break', () => changeAttribute(item, 'data-break', item.dataset.break === '1' ? null : '1')), { pressed: item.dataset.break === '1', disabled: span(item) }),
            button('복제', 'duplicate', () => focusTool('duplicate', () => duplicateItem(list, item, i)), { disabled: linked }),
            button('삭제', 'delete', () => focusTool('delete', () => deleteItem(list, item, i))),
          );
          if (list === studentItems && solve) {
            const tables = all(item, 'table.ws-data').filter(table => table.querySelector('th[data-src], td[data-src]') &&
              table.querySelector('.ws-blank[data-slot]') && !table.closest(privateContent));
            if (tables.length) {
              // 단위 단추를 붙인 막대가 길어져도 표의 첫 줄을 가리지 않는다.
              Object.assign(tools.style, { marginBlockStart: '0', top: 'auto', bottom: '100%', transform: 'none' });
            }
            for (const [index, table] of tables.entries()) {
              const label = tables.length > 1 ? `표 ${index + 1} 단위` : '표 단위';
              tools.append(button(label, 'table-unit', () => startDraft(table, label)));
            }
          }
          item.append(tools);
        }
        for (const box of all(item, '.ws-ans').filter(node => node.getClientRects().length)) {
          const tools = make('div', 'ws-tools ws-box-tools');
          tools.setAttribute('role', 'group');
          tools.setAttribute('aria-label', '답칸 편집');
          const doBox = (action, attr, value) => {
            const hadFocus = box.contains(document.activeElement);
            changeAttribute(box, attr, value);
            if (hadFocus) (box.querySelector(`[data-sheet-action="${action}"]:not(:disabled)`) || editButton).focus({ preventScroll: true });
          };
          if (boxKind(box) === 'draw') {
            const height = drawHeight(box);
            for (const step of [-5, 5]) {
              const action = step < 0 ? 'shorter' : 'taller';
              tools.append(button(step < 0 ? '높이 −5mm' : '높이 +5mm', action,
                () => doBox(action, 'data-height-mm', String(Math.max(5, height + step))), { disabled: step < 0 && height <= 5 }));
            }
          } else {
            const lines = boxLines(box);
            for (const step of [-1, 1]) {
              const action = step < 0 ? 'fewer-lines' : 'more-lines';
              tools.append(button(step < 0 ? '줄 −' : '줄 +', action, () => doBox(action, 'data-lines', String(lines + step)),
                { disabled: lines + step < 2 || lines + step > 30 }));
            }
          }
          tools.append(button('늘이기', 'grow', () => doBox('grow', 'data-grow', autoGrow(box) ? null : '1'),
            { pressed: autoGrow(box), disabled: list === teacherItems || span(item) }));
          box.append(tools);
        }
      }
    }
  }
  function cloneItem(item, id) {
    const copy = cleanItem(item, true);
    copy.dataset.id = id;
    const ids = new Map();
    for (const el of [copy, ...all(copy, '[id]')]) {
      if (!el.id) continue;
      let mapped = `${id}-${el.id}`;
      while (byId(mapped) || [...ids.values()].includes(mapped)) mapped += '-copy';
      ids.set(el.id, mapped);
      el.id = mapped;
    }
    for (const el of [copy, ...all(copy, '*')]) {
      for (const attr of Array.from(el.attributes)) {
        const value = attr.value.replace(/url\(\s*(['"]?)#([^\s)'"]+)\1\s*\)/g, (whole, q, old) => ids.has(old) ? `url(${q}#${ids.get(old)}${q})` : whole);
        if (value !== attr.value) attr.value = value;
        else if (attr.localName === 'href' && ids.has(value.slice(1)) && value.startsWith('#')) attr.value = `#${ids.get(value.slice(1))}`;
      }
    }
    return copy;
  }
  function duplicateItem(list, item, index) {
    const existing = new Set([...studentItems, ...teacherItems].map(node => node.dataset.id));
    const base = item.dataset.id || 'item';
    let id;
    do { id = `${base}-copy-${++copySerial}`; } while (existing.has(id));
    const copy = cloneItem(item, id);
    const solutionIndex = list === studentItems ? teacherItems.findIndex(node => node.dataset.solutionFor === base) : -1;
    let solution;
    if (solutionIndex !== -1) {
      let privateId = `t-${id}`;
      while (existing.has(privateId)) privateId = `t-${privateId}`;
      solution = cloneItem(teacherItems[solutionIndex], privateId);
      solution.dataset.solutionFor = id;
    }
    solve?.copiedItem(item, copy);
    change(() => {
      list.splice(index + 1, 0, copy);
      if (solution) teacherItems.splice(solutionIndex + 1, 0, solution);
    }, () => {
      list.splice(list.indexOf(copy), 1); copy.remove();
      if (solution) { teacherItems.splice(teacherItems.indexOf(solution), 1); solution.remove(); }
    });
  }

  function cancelDraft() {
    if (!draft) return;
    draft.input.remove();
    draft = null;
  }
  function positionDraft() {
    if (!draft) return;
    const anchor = rect(draft.target);
    const width = Math.min(Math.max(anchor.width, 300), Math.max(120, window.innerWidth - 16));
    const left = clamp(anchor.left, 8, Math.max(8, window.innerWidth - width - 8));
    const top = clamp(anchor.top, Math.max(8, rect(toolbar).bottom), Math.max(8, window.innerHeight - 40));
    Object.assign(draft.input.style, { width: `${width}px`, left: `${left}px`, top: `${top}px` });
  }
  function startDraft(target, unitLabel) {
    if (draft?.target === target) return;
    cancelDraft();
    announce();
    // 긴 발문도 한눈에 보이게 여러 줄로 감기는 입력칸을 쓴다. 줄바꿈은 넣지 않는다(Enter는 확정).
    const input = make('textarea', 'ws-edit-input');
    input.rows = 1;
    Object.assign(input.style, { resize: 'none', overflow: 'hidden', whiteSpace: 'pre-wrap', wordBreak: 'keep-all' });
    const fit = () => { input.style.height = 'auto'; input.style.height = `${input.scrollHeight + 2}px`; };
    input.addEventListener('input', () => {
      if (/[\r\n]/.test(input.value)) input.value = input.value.replace(/\s*[\r\n]+\s*/g, ' ');
      fit();
    });
    input.dataset.sheetRuntime = '';
    input.setAttribute('aria-label', unitLabel || '원본 표기');
    input.setAttribute('aria-describedby', 'sheet-status');
    input.value = (unitLabel ? target.dataset.tableUnit : target.dataset.src) ?? '';
    if (unitLabel) input.placeholder = '예: 점 (없으면 비워 두세요)';
    input.spellcheck = false;
    draft = { target, input, unitLabel, composing: false };
    document.body.append(input);
    positionDraft();
    fit();
    input.focus({ preventScroll: true });
    input.select();
    input.addEventListener('compositionstart', () => { if (draft?.input === input) draft.composing = true; });
    input.addEventListener('compositionend', () => { if (draft?.input === input) draft.composing = false; });
    input.addEventListener('input', () => { input.removeAttribute('aria-invalid'); announce(); });
    input.addEventListener('keydown', event => {
      if (event.isComposing || event.keyCode === 229 || draft?.composing) return;
      if (event.key === 'Escape') { event.preventDefault(); cancelDraft(); announce(); }
      if (event.key === 'Enter') { event.preventDefault(); commitDraft(); }
    });
  }
  function commitDraft() {
    if (!draft) return true;
    const { target, input, unitLabel } = draft;
    const source = input.value;
    try {
      if (unitLabel) {
        const edits = solve.editTableUnit(target, source);
        if (edits) change(edits.apply, edits.revert); else cancelDraft();
        return true;
      }
      const oldSrc = target.dataset.src, oldHTML = target.innerHTML;
      if (source === oldSrc) { cancelDraft(); return true; }
      const blanks = [];
      const markup = renderMarkup(source, blank => blanks.push(blank));
      const edits = solve?.editMarkup(target, source, markup, blanks);
      const write = (text, html) => { target.dataset.src = text; target.innerHTML = html; };
      if (edits) change(edits.apply, edits.revert);
      else change(() => write(source, markup), () => write(oldSrc, oldHTML));
      return true;
    } catch (error) {
      input.setAttribute('aria-invalid', 'true');
      announce(error.message);
      input.focus({ preventScroll: true });
      return false;
    }
  }
  function editStyle(write) {
    const old = userStyle.textContent;
    const values = new Map();
    for (const rule of userStyle.sheet.cssRules) {
      if (rule.selectorText !== ':root') continue;
      for (const name of styleNames) {
        const value = rule.style.getPropertyValue(name);
        if (value) values.set(name, value.trim());
      }
    }
    write(values);
    const next = values.size ? `:root {\n${[...values].map(([k, v]) => `  ${k}: ${v};`).join('\n')}\n}\n` : '';
    if (next === old) return;
    change(() => { userStyle.textContent = next; }, () => { userStyle.textContent = old; });
    refreshFontLayout();
  }
  function waitForStyleFonts() {
    // display:none인 원본이나 숨긴 교사 쪽에서도 처음 쓸 글꼴을 미리 요청한다.
    const sample = [...studentItems, ...teacherItems].map(item => item.textContent).join('');
    const style = getComputedStyle(root);
    return Promise.allSettled(styleNames.slice(2).flatMap(role => [400, 700].map(weight =>
      document.fonts.load(`${weight} 10pt ${style.getPropertyValue(role)}`, sample || '활동지'))));
  }
  async function refreshFontLayout() {
    const run = ++fontRun;
    await waitForStyleFonts();
    await document.fonts.ready;
    if (run === fontRun) layout();
  }
  async function addFont(file, role) {
    if (activeFont) return;
    const ext = file.name.split('.').pop().toLowerCase();
    const format = { woff: 'woff', woff2: 'woff2', ttf: 'truetype', otf: 'opentype' }[ext];
    if (!format) { announce('WOFF, WOFF2, TTF, OTF 글꼴을 고르세요.'); return; }
    activeFont = true;
    try {
      let i = 1;
      while (fontStyle.textContent.includes(`"Sheet Custom ${i}"`)) i++;
      const family = `Sheet Custom ${i}`;
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const url = `url("data:font/${ext};base64,${data}") format("${format}")`;
      await new FontFace(family, url).load(); // 적용하기 전에 파일 형식을 검사한다.
      const previousFont = fontStyle.textContent, previousStyle = userStyle.textContent;
      const fallback = role === '--sheet-body-font' && root.dataset.kind !== '수업' ? 'serif' : 'sans';
      const next = `${previousStyle}\n:root { ${role}: "${family}", var(--sheet-${fallback}); }\n`;
      change(() => {
        fontStyle.textContent = `${previousFont}\n@font-face { font-family: "${family}"; src: ${url}; }\n`;
        userStyle.textContent = next;
      }, () => { fontStyle.textContent = previousFont; userStyle.textContent = previousStyle; });
      await refreshFontLayout();
    } catch {
      announce('글꼴을 불러오지 못했습니다. 다른 글꼴 파일을 고르세요.');
    } finally { activeFont = false; }
  }
  function fileName(student) {
    const base = document.title.replace(/\s*\(교사용\)\s*$/, '').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').trim().replace(/[. ]+$/, '') || '활동지';
    const safe = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(base) ? `_${base}` : base;
    return safe.slice(0, 180) + (student ? '' : ' (교사용)') + '.html';
  }
  function download(html, name) {
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
    const a = make('a');
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }
  async function save(student = false) {
    if (saving || !commitDraft()) return;
    const name = fileName(student);
    if (student) { download(serialize(true), name); announce('학생용 파일을 내려받았습니다.'); return; }
    saving = true;
    try {
      if (!saveHandle && typeof window.showSaveFilePicker === 'function') {
        try {
          saveHandle = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: 'HTML', accept: { 'text/html': ['.html'] } }] });
        } catch (error) {
          if (error.name === 'AbortError') return;
          if (!['SecurityError', 'NotAllowedError', 'NotSupportedError'].includes(error.name)) throw error;
        }
      }
      const html = serialize();
      if (saveHandle) {
        try {
          const stream = await saveHandle.createWritable();
          try { await stream.write(html); await stream.close(); }
          catch (error) { if (stream.abort) await stream.abort().catch(() => {}); throw error; }
          announce('저장했습니다.');
          return;
        } catch (error) {
          saveHandle = undefined;
          if (error.name === 'AbortError') return;
        }
      }
      download(html, name);
      announce('HTML 파일을 내려받았습니다.');
    } catch {
      saveHandle = undefined;
      announce('저장하지 못했습니다. 수정 내용은 화면에 남아 있습니다.');
    } finally { saving = false; }
  }
  async function print() {
    if (!commitDraft()) return;
    const restore = solve?.beforePrintButton();
    try {
      if (solve) await window.sheet.view('a4');
      window.print();
    } finally { restore?.(); }
  }
  function select(label, options) {
    const el = make('select');
    el.setAttribute('aria-label', label);
    for (const [value, text] of options) el.add(new Option(text, value));
    return el;
  }
  function createToolbar() {
    toolbar = make('div');
    toolbar.id = 'sheet-toolbar';
    toolbar.dataset.sheetRuntime = '';
    toolbar.setAttribute('role', 'region');
    toolbar.setAttribute('aria-label', '활동지 도구');
    const printButton = button('인쇄', 'print', print);
    if (teacher) {
      editButton = button('편집', 'edit', () => setEdit(!edit), { pressed: false });
      editButton.setAttribute('aria-keyshortcuts', 'E');
      answerButton = button('정답 보기', 'answers', () => setAnswers(!answersOn()), { pressed: answersOn() });
      answerButton.setAttribute('aria-keyshortcuts', 'A');
      const saveButton = button('저장', 'save', () => save());
      saveButton.setAttribute('aria-keyshortcuts', 'Control+s');
      toolbar.append(editButton, answerButton, printButton, saveButton, button('학생용으로 저장', 'export-student', () => save(true)));
      for (const [role, label] of [['body', '본문'], ['question', '발문']]) {
        const group = make('div', 'ws-controls');
        const name = `--sheet-${role}-scale`;
        group.append(make('span', '', label));
        for (const step of [-5, 5]) group.append(button(step < 0 ? '−' : '+', `${role}-${step < 0 ? 'less' : 'more'}`, () => {
          const next = clamp(Math.round(scale(name) * 100) + step, 50, 200) / 100;
          editStyle(values => values.set(name, String(next)));
        }, { title: `${label} ${step < 0 ? '작게' : '크게'}` }));
        toolbar.append(group);
      }
      const group = make('div', 'ws-controls');
      const fontMenu = select('글꼴', [['', '글꼴'], ['default', '기본'], ['sans', '모두 고딕'], ['file', '파일 추가']]);
      const roles = select('추가 글꼴 역할', [['--sheet-head-font', '제목'], ['--sheet-question-font', '발문'], ['--sheet-body-font', '본문']]);
      const input = make('input');
      input.type = 'file'; input.accept = '.woff,.woff2,.ttf,.otf'; input.hidden = roles.hidden = true;
      const choose = button('파일 고르기', 'font-file', () => input.click());
      choose.hidden = true;
      input.addEventListener('change', () => { const file = input.files?.[0]; if (file) void addFont(file, roles.value); input.value = ''; });
      fontMenu.addEventListener('change', () => {
        const mode = fontMenu.value;
        roles.hidden = choose.hidden = mode !== 'file';
        if (mode === 'file' || !mode) return;
        editStyle(values => styleNames.slice(2).forEach(name => mode === 'sans' ? values.set(name, 'var(--sheet-sans)') : values.delete(name)));
        fontMenu.value = '';
      });
      group.append(fontMenu, roles, choose, input);
      undoButton = button('되돌리기', 'undo', undo, { disabled: true });
      undoButton.setAttribute('aria-keyshortcuts', 'Control+z');
      notice = make('span', 'ws-status'); notice.id = 'sheet-status'; notice.setAttribute('role', 'status');
      toolbar.append(group, undoButton, notice);
    } else toolbar.append(printButton);
    document.body.prepend(toolbar);
  }
  function bindEditor() {
    main.addEventListener('dblclick', event => {
      const target = edit && event.target.closest('[data-src]');
      if (target) { event.preventDefault(); startDraft(target); }
    });
    window.addEventListener('resize', positionDraft);
    window.addEventListener('scroll', positionDraft, { passive: true });
    document.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || draft?.composing || event.altKey) return;
      const key = /^Key[A-Z]$/.test(event.code) ? event.code.slice(-1).toLowerCase() : event.key.toLowerCase();
      const command = event.ctrlKey || event.metaKey;
      if (command && !event.shiftKey && key === 's') { event.preventDefault(); void save(); return; }
      if (command && !event.shiftKey && key === 'p') { event.preventDefault(); print(); return; }
      if (event.target.closest?.('input, textarea, select, [contenteditable]')) return;
      if (command && !event.shiftKey && key === 'z') { event.preventDefault(); undo(); }
      else if (!command && !event.shiftKey && !event.repeat && key === 'e') { event.preventDefault(); setEdit(!edit); }
      else if (!command && !event.shiftKey && !event.repeat && key === 'a') { event.preventDefault(); setAnswers(!answersOn()); }
    });
  }
  function mathLayoutWorks() {
    const probe = make('div');
    probe.style.cssText = 'position:fixed;visibility:hidden;pointer-events:none;inset:0 auto auto 0;font-size:32px';
    probe.innerHTML = '<math><mfrac><mn>1</mn><mn>2</mn></mfrac><msup><mi>x</mi><mn>2</mn></msup></math>';
    document.body.append(probe);
    const [numerator, denominator, base, exponent] = all(probe, 'mn, mi').map(rect);
    probe.remove();
    return numerator.width > 0 && numerator.top < denominator.top && exponent.width > 0 && exponent.top < base.top;
  }
  async function init() {
    if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    src = byId('sheet-src');
    if (!src) throw new Error('활동지 원본(#sheet-src)이 없습니다.');
    teacherSrc = byId('sheet-teacher');
    if (!teacher) { delete root.dataset.answers; stripTeacher(src); teacherSrc?.remove(); teacherSrc = null; }
    studentItems = Array.from(src.children).filter(node => node.matches('.ws-item'));
    teacherItems = teacherSrc ? Array.from(teacherSrc.children).filter(node => node.matches('.ws-item')) : [];
    const ensureStyle = id => byId(id) || document.head.appendChild(Object.assign(make('style'), { id }));
    userStyle = ensureStyle('sheet-user');
    fontStyle = ensureStyle('sheet-fonts');
    const images = all(src, 'img').concat(teacherSrc ? all(teacherSrc, 'img') : []);
    for (const img of images) img.loading = 'eager';
    await Promise.all([waitForStyleFonts(), Promise.allSettled(images.map(img => img.decode()))]);
    await document.fonts.ready;
    main = make('main');
    main.id = 'sheet-pages';
    main.dataset.sheetRuntime = '';
    root.setAttribute('data-sheet-preparing', '');
    src.after(main);
    layout();
    await document.fonts.ready;
    layout();
    root.removeAttribute('data-sheet-preparing');
    createToolbar();
    if (!mathLayoutWorks()) {
      const warning = make('p', 'ws-math-notice', '이 브라우저에서는 수식이 제대로 보이지 않아요. 최신 브라우저로 열거나 함께 받은 PDF를 보세요.');
      warning.dataset.sheetRuntime = '';
      warning.setAttribute('role', 'status');
      main.before(warning);
    }
    if (teacher) bindEditor();
    solve?.init();
    if (teacher) originalEditionContent = editionContent();
  }

  window.sheet = {
    ready: init(), report, serialize: () => serialize(), exportStudent: () => serialize(true), setAnswers,
    view: mode => mode === undefined ? 'a4' : Promise.resolve('a4'), slots: () => [],
    answer: () => null, values: () => ({}), score: () => ({ checked: 0, correct: 0, total: 0 }), clearAnswers() {},
  };
  // 내부 연결점. 이어 붙인 solve.js가 동기로 한 번 설치한다. 공개 API는 window.sheet만 사용한다.
  // layout은 DOM을 옮긴 뒤 동기 측정한다. 편집·저장 훅은 원본 대신 수정할 사본을 받는다.
  if (root.dataset.interactive === '1') window.__sheetCore = {
    attach(extension) { solve = extension; root.dataset.view = 'a4'; },
    items: () => studentItems, teacherItems: () => teacherItems, buildMeta,
    layout, refreshFontLayout, renderMarkup, answerMarkup, serialize, download, fileName, drawRatio,
    addToolbar(...nodes) { toolbar.append(...nodes); }, button, commitDraft,
    stopEditing() { if (edit) setEdit(false); },
    syncAnswerButton() { answerButton?.setAttribute('aria-pressed', String(answersOn())); },
  };
})();
