/* The world GDP contract's page: Patrick Brown's article with its figures drawn live from the
   contract's model, the contract itself with the market's ladder, and every assumption on a control.

   The page computes, on the reader's machine, what the contract's model computes: elnino-model.js
   re-runs the published analysis on its own 50,000 simulated paths, so every number here is either
   the article's text (as written) or that model's output at the settings on the controls. The two
   views it reports are never blended into one number and never called a price: a reader's own price
   sits between them according to how much weight they give each.

   The settings live in the address (#irf=PWT_panel&share=0.5), so a scenario can be shared.

   The model's files sit beside the snapshots (snapshots/elnino/). The El Niño refresh writes them
   once a month; the pipeline never does. Once the exchange lists the contract, config.js names its
   product (gdp_contract in config/site.json) and the ladder draws the exchange's prices through the
   market layer, with the two views' marks kept on each bar. */
window.WXGdp = (() => {
  const { el, txt, h, $ } = WXC;
  const BASE = (window.WX && window.WX.elninoBase) || WXD.base() + '/snapshots/elnino/';
  const NULL = 'var(--gdp-null)', PAPER = 'var(--gdp-paper)', INK = 'var(--ink)';
  let M = null, A = null, D = null, S = null, R = null, tip = null;
  let BOOK = null;                   // the listed contract's ladder and quotes, or null while it is not listed
  const MONTH = 30 * 24 * 60;        // the model's files are rewritten monthly, in minutes for the status strip
  const figs = [];                   // {n, svg, state, draw}
  let scanCache = null;              // the end-year scan for the current result

  // ---------------------------------------------------------------- formatting
  const minus = s => String(s).replace(/^-/, '−');
  const f1 = v => minus((Math.round(v * 10) / 10).toFixed(1));
  const f2 = v => minus(v.toFixed(2));
  const pct = p => Math.round(p * 100) + '%';
  const pct1 = p => (Math.round(p * 1000) / 10).toFixed(1) + '%';
  const signed1 = v => (v >= 0 ? '+' : '−') + Math.abs(Math.round(v * 10) / 10).toFixed(1);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const dateOf = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('en-US', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }); };
  const asofText = iso => (iso ? WXC.clockFull(Date.parse(iso), Intl.DateTimeFormat().resolvedOptions().timeZone) : 'time not given');
  const cents = d => (d == null ? null : Math.round(d * 1000) / 10);

  // ---------------------------------------------------------------- settings
  const KEYS = ['irf', 'uncertainty', 'share', 'baseline', 'trendStart', 'missShare', 'spread', 'netting', 't26',
                't26Value', 't27', 't27Value', 'future', 'scope', 'endYear', 'strike'];
  const NUMERIC = new Set(['share', 'trendStart', 'missShare', 'spread', 't26Value', 't27Value', 'endYear', 'strike']);
  const same = (a, b) => (typeof a === 'number' && typeof b === 'number') ? Math.abs(a - b) < 1e-9 : a === b;
  // settings that change nothing in the current state (a set value no one is using) do not count as changes
  const live = (k, s) => !((k === 't26Value' && s.t26 !== 'value') || (k === 't27Value' && s.t27 !== 'value')
    || ((k === 'missShare' || k === 'netting') && s.baseline === 'frozen')
    || (k === 'trendStart' && (s.baseline !== 'frozen' || !M.meta.trend)));
  const changed = s => KEYS.filter(k => live(k, s) && !same(s[k], D[k]));
  const atArticle = s => changed(s).length === 0;

  function readHash() {
    const s = Object.assign({}, D);
    const q = new URLSearchParams(location.hash.replace(/^#/, ''));
    KEYS.forEach(k => {
      if (!q.has(k)) return;
      const v = q.get(k);
      if (k === 'netting') s[k] = v !== '0';
      else if (NUMERIC.has(k)) { const x = parseFloat(v); if (isFinite(x)) s[k] = x; }
      else s[k] = v;
    });
    if (!M.irfByKey[s.irf]) s.irf = D.irf;
    if (s.uncertainty === 'nw' && s.irf !== 'PWT_ts') s.uncertainty = 'boot';
    if (M.meta.trend && !M.meta.trend.starts[String(s.trendStart)]) s.trendStart = D.trendStart;
    return s;
  }
  function writeHash() {
    const q = new URLSearchParams();
    changed(S).forEach(k => q.set(k, k === 'netting' ? (S[k] ? '1' : '0') : String(S[k])));
    const hs = q.toString();
    history.replaceState(null, '', hs ? '#' + hs : location.pathname + location.search);
  }

  // ---------------------------------------------------------------- the run
  let pending = false, timer = null, busy = false;
  function schedule(delay) {
    clearTimeout(timer);
    timer = setTimeout(() => { if (!pending) { pending = true; requestAnimationFrame(compute); } }, delay || 0);
  }
  async function compute() {
    pending = false;
    if (busy) { schedule(30); return; }
    busy = true;
    try {
      // a setting changed while a file loads can need another file, so load until the current settings are ready
      while (!M.ready(S)) { status('Loading what that setting needs…', true); await M.ensure(S); }
      const t0 = performance.now();
      R = M.run(S);
      scanCache = null;
      drawAll();
      status('Re-run in ' + Math.round(performance.now() - t0) + ' ms on ' + M.n.toLocaleString() + ' paths.');
    } catch (e) {
      status('The model could not run: ' + e.message, true);
      throw e;
    } finally { busy = false; }
    writeHash();
    syncControls();
  }
  function set(k, v, delay) {
    S[k] = v;
    if (k === 'irf' && S.uncertainty === 'nw' && v !== 'PWT_ts') S.uncertainty = 'boot';
    // a value control that appears starts from today's estimate
    if (k === 't27' && v === 'value' && S.t27Value == null) S.t27Value = Math.round(M.meta.temps.T27.mean * 100) / 100;
    if (k === 't26' && v === 'value' && S.t26Value == null) S.t26Value = Math.round(M.meta.temps.T26.mean * 100) / 100;
    schedule(delay);
    syncControls();
  }
  function reset() { S = Object.assign({}, D); schedule(0); syncControls(); }

  // the end-year scan (Figure 6) costs more than the rest together, so it is made once per run, when asked
  function scan() {
    if (!scanCache) scanCache = M.meta.endYears.map(E => R.youden(E));
    return scanCache;
  }
  // the rule that picks the end year: highest J to two decimals, ties to the earlier year
  function bestEnd(sc) {
    const best = Math.max(...sc.map(y => Math.round(y.J * 100)));
    return sc.find(y => Math.round(y.J * 100) === best).E;
  }

  // ---------------------------------------------------------------- shared drawing
  function clear(svg) { [...svg.childNodes].forEach(c => { if (c.nodeName !== 'title') svg.removeChild(c); }); }
  function frame(o) {
    const x = v => o.L + (v - o.x0) / (o.x1 - o.x0) * (o.W - o.L - o.R);
    const y = v => o.T + (1 - (v - o.y0) / (o.y1 - o.y0)) * (o.H - o.T - o.B);
    return Object.assign({ x, y }, o);
  }
  const path = pts => pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('');
  const area = (xs, lo, hi) => path(xs.map((x, i) => [x, hi[i]]).concat(xs.map((x, i) => [x, lo[i]]).reverse())) + 'Z';
  function yGrid(svg, F, ticks, fmt, x0, x1) {
    ticks.forEach(v => {
      svg.appendChild(el('line', { x1: x0 == null ? F.L : x0, x2: x1 == null ? F.W - F.R : x1, y1: F.y(v), y2: F.y(v), class: 'grid' }));
      svg.appendChild(txt(fmt(v), { x: (x0 == null ? F.L : x0) - 6, y: F.y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    });
  }
  function xTicks(svg, F, ticks, fmt) {
    ticks.forEach(v => svg.appendChild(txt(fmt(v), { x: F.x(v), y: F.H - F.B + 16, 'text-anchor': 'middle', class: 'ax' })));
  }
  function ticksFor(lo, hi, n) {
    const raw = (hi - lo) / (n || 5), p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw) || 10 * p;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v / step) * step);
    return out;
  }
  // names at the right end of lines, nudged apart so two close lines do not print on each other
  function endLabels(svg, items, x, gap) {
    items.sort((a, b) => a.y - b.y);
    let last = -1e9;
    items.forEach(it => {
      let y = it.y;
      if (y - last < (gap || 26)) y = last + (gap || 26);
      last = y;
      (it.lines || [it.text]).forEach((s, i) => svg.appendChild(txt(s, { x, y: y + i * 13 - ((it.lines || [1]).length - 1) * 6.5 + 4, class: 'lbl', fill: it.col, 'font-size': i ? 11 : 12, 'font-weight': i ? 400 : 700 })));
    });
  }
  function hoverBand(svg, F, xs, rowsAt) {
    const guide = el('line', { y1: F.T, y2: F.H - F.B, stroke: 'var(--rule)', 'stroke-dasharray': '3 3', 'pointer-events': 'none', opacity: 0 });
    svg.appendChild(guide);
    const r = el('rect', { x: F.L, y: F.T, width: F.W - F.L - F.R, height: F.H - F.T - F.B, fill: 'transparent' });
    r.addEventListener('mousemove', e => {
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      let best = xs[0];
      xs.forEach(v => { if (Math.abs(F.x(v) - p.x) < Math.abs(F.x(best) - p.x)) best = v; });
      guide.setAttribute('x1', F.x(best)); guide.setAttribute('x2', F.x(best)); guide.setAttribute('opacity', 1);
      const o = rowsAt(best);
      tip.show(e, tip.rows(o.title, o.rows, o.foot));
    });
    r.addEventListener('mouseleave', () => { guide.setAttribute('opacity', 0); tip.hide(); });
    svg.appendChild(r);
  }
  function hover(node, make) {
    node.addEventListener('mousemove', e => tip.show(e, make()));
    node.addEventListener('mouseleave', () => tip.hide());
  }
  const sw = c => '<span class="sw" style="background:' + c + '"></span>';

  // index of GDP per person, 2026 = 100, on a view's mean path or a quantile of it
  function cumBase(t) {
    const m = M.meta;
    if (S.baseline === 'frozen') return M.trendMeta(S).mu * (t - m.base);
    let s = 0;
    for (let y = m.base + 1; y <= t; y++) s += m.imf.growth[String(y)];
    return s;
  }
  const idx = (t, dev) => 100 * Math.exp((cumBase(t) + dev) / 100);
  // the trend baseline's window, from the chosen first year through the year before the pandemic
  const trendLabel = () => { const c = M.trendMeta(S).calib || M.meta.frozen.calib; return c[0] + '–' + c[1] + ' average'; };
  const strikeLevel = (x, E) => 100 * Math.exp(x * (E - M.meta.base) / 100);

  // ---------------------------------------------------------------- the method notes
  function tex(node, src, display) {
    if (window.katex) { try { window.katex.render(src, node, { displayMode: !!display, throwOnError: false }); return node; } catch (e) { /* falls through */ } }
    node.textContent = src;
    return node;
  }
  function mathText(node, s) {
    String(s).split(/\$([^$]+)\$/).forEach((part, i) => {
      if (!part) return;
      if (i % 2 === 0) node.appendChild(document.createTextNode(part));
      else tex(node.appendChild(h('span', { class: 'mi' })), part, false);
    });
    return node;
  }
  function codeLinks(list) {
    const repo = M.meta.code.repo + '/blob/main/';
    const p = h('div', { class: 'rule code' }, [h('b', { text: 'Code' }), ' ']);
    list.forEach(([label, file], i) => {
      if (i) p.appendChild(document.createTextNode(' · '));
      p.appendChild(h('a', { href: repo + file, text: label }));
    });
    return p;
  }
  function methodNote(container, spec) {
    container.innerHTML = '';
    const open = container.dataset.open === '1';
    const btn = h('button', { class: 'vbtn accnote-btn', type: 'button', 'aria-expanded': open ? 'true' : 'false',
                             text: (open ? 'Hide' : 'Show') + ' details of calculation' });
    const box = h('div', { class: 'accnote' });
    box.hidden = !open;
    btn.onclick = () => {
      const now = box.hidden;
      box.hidden = !now;
      container.dataset.open = now ? '1' : '0';
      btn.setAttribute('aria-expanded', now ? 'true' : 'false');
      btn.textContent = (now ? 'Hide' : 'Show') + ' details of calculation';
    };
    container.appendChild(btn);
    if (spec.title) box.appendChild(h('div', { class: 'nt', text: spec.title }));
    (spec.body || []).forEach(item => {
      if (item && item.tex) { tex(box.appendChild(h('div', { class: 'eq' })), item.tex, true); return; }
      if (item && item.table) { box.appendChild(item.table); return; }
      mathText(box.appendChild(h('div', { class: 'rule' })), item);
    });
    if (spec.code) box.appendChild(codeLinks(spec.code));
    if (spec.n) box.appendChild(h('div', { class: 'rule n', text: spec.n }));
    container.appendChild(box);
  }

  // ---------------------------------------------------------------- the contract figure (Figure 4)
  function drawMain(svg) {
    clear(svg);
    const m = M.meta, E = S.endYear, k = E - m.base;
    const yrs = []; for (let t = m.base; t <= E; t++) yrs.push(t);
    const at = (arr, t) => (t === m.base ? 0 : arr[R.tYears.indexOf(t)]);
    const mean0 = R.levelMean(0), mean1 = R.levelMean(1);
    const lo0 = R.levelBand(0, 0.1), hi0 = R.levelBand(0, 0.9), lo1 = R.levelBand(1, 0.1), hi1 = R.levelBand(1, 0.9);
    const I = (arr) => yrs.map(t => idx(t, at(arr, t)));
    const m0 = I(mean0), m1 = I(mean1), a0 = I(lo0), b0 = I(hi0), a1 = I(lo1), b1 = I(hi1);
    const book = listedBook();
    const ladder = book ? book.rows.map(r => r.strike) : ladderStrikes();
    const lev = ladder.map(x => strikeLevel(x, E));
    const ymin = Math.min(...a1, ...lev, 100) - 0.6, ymax = Math.max(...b0, ...lev, 100) + 0.6;
    const F = frame({ W: 960, H: 470, L: 50, R: 490, T: 40, B: 40, x0: m.base - 0.15, x1: E + 0.15, y0: ymin, y1: ymax });
    const defs = el('defs');
    const pat = el('pattern', { id: 'gdpHatch', width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' });
    pat.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: 'var(--gdp-pend)', 'stroke-width': 2 }));
    defs.appendChild(pat); svg.appendChild(defs);
    svg.appendChild(txt('World GDP per person (' + m.base + ' = 100)', { x: F.L, y: 16, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, F, ticksFor(ymin, ymax, 6), v => String(v));
    xTicks(svg, F, yrs, v => String(v));
    const X = yrs.map(F.x);
    svg.appendChild(el('path', { d: area(X, a0.map(F.y), b0.map(F.y)), fill: NULL, 'fill-opacity': 0.13, 'pointer-events': 'none' }));
    svg.appendChild(el('path', { d: area(X, a1.map(F.y), b1.map(F.y)), fill: PAPER, 'fill-opacity': 0.15, 'pointer-events': 'none' }));
    const cutPath = yrs.map(t => [F.x(t), F.y(100 * Math.exp(S.strike * (t - m.base) / 100))]);
    svg.appendChild(el('path', { d: path(cutPath), fill: 'none', stroke: INK, 'stroke-width': 1.4, 'stroke-dasharray': '5 4', 'pointer-events': 'none' }));
    [[m0, NULL], [m1, PAPER]].forEach(([v, c]) => {
      svg.appendChild(el('path', { d: path(yrs.map((t, i) => [F.x(t), F.y(v[i])])), fill: 'none', stroke: c, 'stroke-width': 2.2, 'pointer-events': 'none' }));
      yrs.forEach((t, i) => svg.appendChild(el('circle', { cx: F.x(t), cy: F.y(v[i]), r: 3.6, fill: c, stroke: 'var(--panel)', 'stroke-width': 1.2, 'pointer-events': 'none' })));
    });
    const g0 = R.growthMean(0, E), g1 = R.growthMean(1, E);
    const lab1 = S.share === 1 ? 'The paper’s estimate' : 'The paper’s estimate × ' + pct(S.share);
    endLabels(svg, [
      { y: F.y(m0[m0.length - 1]), col: NULL, lines: ['No temperature effect', signed1(g0) + '% a year'] },
      { y: F.y(100 * Math.exp(S.strike * k / 100)), col: INK, lines: ['Cutoff', '+' + S.strike.toFixed(1) + '% a year'] },
      { y: F.y(m1[m1.length - 1]), col: PAPER, lines: [lab1, signed1(g1) + '% a year'] },
    ], F.x(E) + 12, 30);
    hoverBand(svg, F, yrs, t => {
      const i = yrs.indexOf(t);
      return { title: t === m.base ? m.base + ', the starting level' : String(t),
        rows: [[sw(NULL) + 'No temperature effect', f1(m0[i]) + (t > m.base ? ' (' + f1(a0[i]) + '–' + f1(b0[i]) + ')' : '')],
               [sw(PAPER) + lab1, f1(m1[i]) + (t > m.base ? ' (' + f1(a1[i]) + '–' + f1(b1[i]) + ')' : '')],
               ['Cutoff path', f1(100 * Math.exp(S.strike * (t - m.base) / 100))]],
        foot: m.base + ' = 100; averages of ' + M.n.toLocaleString() + ' paths, 80% ranges in brackets' };
    });

    // the ladder, on the same axis: each strike sits at the level growth of exactly that much reaches by E
    const RL = 664, RR = 944;
    const px = p => RL + (RR - RL) * p / 100;
    // Once the exchange lists the contract, its strikes and prices replace the proposal. They are the
    // listed contract's prices, for its own period, so they are drawn only while the page's end year
    // is that period's; at another end year the listed strikes are drawn hatched, with no price.
    const priced = !!book && E === D.endYear;
    svg.appendChild(txt('The market’s ladder', { x: RL, y: 16, class: 'axl', 'font-weight': 700 }));
    svg.appendChild(txt(!book ? (ladder.length > 1 ? 'Proposed strikes' : 'Proposed strike') + ', to list ' + m.ladder.lists
      : priced ? 'ForecastEx prices, ' + asofText(book.asof)
      : 'Prices show with the listed end year, ' + D.endYear, { x: RL, y: 29, class: 'ax' }));
    [0, 25, 50, 75, 100].forEach(p => {
      svg.appendChild(el('line', { x1: px(p), x2: px(p), y1: F.T, y2: F.H - F.B, class: 'grid' }));
      svg.appendChild(txt(String(p), { x: px(p), y: F.H - F.B + 16, 'text-anchor': 'middle', class: 'ax' }));
    });
    svg.appendChild(txt(priced ? 'Yes green, No red, ¢ · marks, chance of Yes, %' : 'Chance of Yes under each view, %',
                        { x: (RL + RR) / 2, y: F.H - F.B + 32, 'text-anchor': 'middle', class: 'axl' }));
    // behind the bars, each view's chance of Yes at every strike on the axis, as Fig. 1b's legend has it, with a
    // readout of both at the level under the pointer
    const strikeAt = lv => 100 * Math.log(lv / 100) / k;
    const sx = [], xa = strikeAt(F.y0), xb = strikeAt(F.y1);
    for (let i = 0; i <= 120; i++) sx.push(xa + (xb - xa) * i / 120);
    const who1 = S.share === 1 ? 'the paper is right' : pct(S.share) + ' of the paper';
    const guide = el('line', { x1: px(0), x2: px(100), stroke: 'var(--rule)', 'stroke-dasharray': '3 3', 'pointer-events': 'none', opacity: 0 });
    const zone = el('rect', { x: px(0), y: F.T, width: px(100) - px(0), height: F.H - F.T - F.B, fill: 'transparent' });
    zone.addEventListener('mousemove', e => {
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const yv = pt.matrixTransform(svg.getScreenCTM().inverse()).y;
      const x = strikeAt(F.y0 + (1 - (yv - F.T) / (F.H - F.T - F.B)) * (F.y1 - F.y0));
      guide.setAttribute('y1', yv); guide.setAttribute('y2', yv); guide.setAttribute('opacity', 1);
      tip.show(e, tip.rows('More than ' + x.toFixed(2) + '% a year, ' + m.base + '–' + E,
        [[sw(NULL) + 'Chance of Yes, no temperature effect', pct1(R.pYes(0, E, x))], [sw(PAPER) + 'Chance of Yes, ' + who1, pct1(R.pYes(1, E, x))]],
        'Each view’s share of ' + M.n.toLocaleString() + ' simulated paths.'));
    });
    zone.addEventListener('mouseleave', () => { guide.setAttribute('opacity', 0); tip.hide(); });
    svg.appendChild(zone);
    [[0, NULL], [1, PAPER]].forEach(([v, c]) => svg.appendChild(el('path', {
      d: path(sx.map(x => [px(100 * R.pYes(v, E, x)), F.y(strikeLevel(x, E))])), fill: 'none', stroke: c, 'stroke-width': 2, 'pointer-events': 'none', class: 'gdp-pcurve' })));
    svg.appendChild(guide);
    const strikes = ladder.slice();
    if (!strikes.some(x => Math.abs(x - S.strike) < 1e-9)) strikes.push(S.strike);
    strikes.forEach(x => {
      const yy = F.y(strikeLevel(x, E));
      const mine = Math.abs(x - S.strike) < 1e-9, extra = !ladder.some(v => Math.abs(v - x) < 1e-9);
      const p0 = R.pYes(0, E, x), p1 = R.pYes(1, E, x);
      const title = 'More than ' + x.toFixed(1) + '% a year, ' + m.base + '–' + E;
      const chances = [[sw(NULL) + 'Chance of Yes, no temperature effect', pct1(p0)],
                       [sw(PAPER) + 'Chance of Yes, ' + (S.share === 1 ? 'the paper is right' : pct(S.share) + ' of the paper'), pct1(p1)]];
      const row = priced && !extra ? book.rows.find(r => Math.abs(r.strike - x) < 1e-9) : null;
      const v = row && WXM.realMid(row) ? Math.round(row.mid * 100) : null;
      if (v != null) {
        // the exchange's price, Yes green and No red, as the site's other ladders draw it
        const url = WXM.contractUrl(book.productConid, row.conidYes);
        const gw = px(v) - px(0);
        const html = tip.rows(title, WXM.bookRows(cents(row.bid), row.ask == null ? null : cents(1 - row.ask), false).concat(chances),
          'ForecastEx quote, ' + asofText(book.asof) + '. The chances are each view’s share of ' + M.n.toLocaleString() + ' simulated paths.');
        [[px(0), gw, 'var(--yes)'], [px(0) + gw, px(100) - px(0) - gw, 'var(--no)']].forEach(([x0, w, c]) => {
          const bar = el('rect', { x: x0, y: yy - 7, width: Math.max(w, 1), height: 14, fill: c, stroke: 'var(--panel)', 'stroke-width': 0.6 });
          hover(bar, () => html);
          if (url) WXM.linkTo(bar, url, 'Open more than ' + x.toFixed(1) + '% a year on IBKR');
          svg.appendChild(bar);
        });
        if (mine) svg.appendChild(el('rect', { x: px(0), y: yy - 7, width: px(100) - px(0), height: 14, fill: 'none', stroke: INK, 'stroke-width': 1.6, 'pointer-events': 'none' }));
        // a price label a mark would cover is left out; the box still gives the book
        const free = (a, b) => ![p0, p1].some(p => px(100 * p) >= a - 4 && px(100 * p) <= b + 4);
        const wide = s => 5.6 * s.length + 2;
        const yl = v + '¢', nl = (100 - v) + '¢';
        if (gw >= 26 && free(px(0) + 3, px(0) + 3 + wide(yl))) svg.appendChild(txt(yl, { x: px(0) + 3, y: yy + 3.5, class: 'ladtxt', 'pointer-events': 'none' }));
        if (px(100) - px(0) - gw >= 26 && free(px(100) - 3 - wide(nl), px(100) - 3)) svg.appendChild(txt(nl, { x: px(100) - 3, y: yy + 3.5, class: 'ladtxt', 'text-anchor': 'end', 'pointer-events': 'none' }));
      } else {
        const bar = el('rect', { x: px(0), y: yy - 7, width: px(100) - px(0), height: 14, fill: 'url(#gdpHatch)',
                                 stroke: mine ? INK : 'var(--gdp-pend)', 'stroke-width': mine ? 1.6 : 0.8, rx: 2 });
        svg.appendChild(bar);
        const status = !book ? 'not listed yet; lists ' + m.ladder.lists
          : extra ? 'not a listed strike'
          : !priced ? 'listed for ' + m.base + '–' + D.endYear + ', so its price shows at that end year'
          : row && WXM.emptyBook(row) ? 'listed; both sides bid the minimum, so no price' : 'listed; no standing bids';
        hover(bar, () => tip.rows(title, [['Status', status]].concat(chances), 'Each view’s share of ' + M.n.toLocaleString() + ' simulated paths.'));
      }
      svg.appendChild(txt('> ' + x.toFixed(1) + '%' + (extra ? ' (yours)' : ''), { x: RL - 6, y: yy + 4, 'text-anchor': 'end', class: 'ax', 'font-weight': mine ? 700 : 400, fill: mine ? INK : null }));
      [[p0, NULL, -1], [p1, PAPER, 1]].forEach(([p, c, side]) => {
        // on a priced bar the mark sits on green or red, so a panel-colored edge keeps it apart
        if (v != null) svg.appendChild(el('line', { x1: px(100 * p), x2: px(100 * p), y1: yy - 11, y2: yy + 11, stroke: 'var(--panel)', 'stroke-width': 5, 'pointer-events': 'none' }));
        svg.appendChild(el('line', { x1: px(100 * p), x2: px(100 * p), y1: yy - 10, y2: yy + 10, stroke: c, 'stroke-width': 3, 'pointer-events': 'none', class: 'gdp-mark' }));
        svg.appendChild(txt(String(Math.round(100 * p)), { x: Math.min(Math.max(px(100 * p), px(0) + 8), px(100) - 8), y: side < 0 ? yy - 13 : yy + 21, 'text-anchor': 'middle', class: 'lbl', fill: c, 'font-size': 10.5, 'font-weight': 700 }));
      });
    });
    const card = svg.parentNode;
    if (card) WXM.tradeNote(card, card);
  }

  function ladderStrikes() { return M.meta.ladder.strikes.slice(); }
  /* The listed contract's numeric strikes, or null while it is not listed. Its strikes are read as
     average growth in percent a year, the terms the proposal uses; a listing written on any other
     scale has to be mapped here before it is drawn. */
  function listedBook() {
    if (!BOOK) return null;
    const rows = BOOK.rows.filter(r => r.numeric && isFinite(r.strike));
    return rows.length ? Object.assign({}, BOOK, { rows }) : null;
  }
  // a range of NOAA's 2027 value as the terms state it
  const condition = name => ((M.meta.published.bins.find(b => b.bin === name) || {}).condition || name).replace(' to ', '–').replace(/ C\b/, ' °C');

  function mainKey() {
    const k = $('#gdpMainKey');
    k.innerHTML = '';
    const item = (c, label, dash) => h('span', { html: (dash ? '<svg width="22" height="8"><line x1="1" y1="4" x2="21" y2="4" stroke="' + c + '" stroke-width="2" stroke-dasharray="5 4"/></svg> '
      : '<span class="sw" style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + c + ';margin-right:5px;vertical-align:-1px"></span>') + esc(label) });
    k.appendChild(item(NULL, 'No temperature effect: the IMF’s projection less its average past miss'));
    k.appendChild(item(PAPER, 'The paper’s estimate applied to every temperature shock since 2015'));
    k.appendChild(item(INK, 'Growth at exactly the strike', true));
    k.appendChild(h('span', { text: 'Shading spans the 80% ranges. Beside the ladder, each view’s curve gives its chance of Yes at every strike, and the marks give it at the ladder’s own strikes.' }));
  }

  // ---------------------------------------------------------------- readout and question
  function drawReadout() {
    const m = M.meta, E = S.endYear;
    const p0 = R.pYes(0, E, S.strike), p1 = R.pYes(1, E, S.strike), y = R.youden(E);
    const g = v => R.growthMean(v, E), q = (v, p) => R.growthQuantile(v, E, p);
    const who = S.share === 1 ? 'If the paper is right' : 'If ' + pct(S.share) + ' of the paper’s effect is real';
    $('#gdpRead').innerHTML =
      '<div class="gdp-tile null"><div class="tl">If temperature shocks have no effect</div><div class="tv">' + pct(p0) + '</div>'
      + '<div class="ts">chance of Yes. Average growth ' + f1(g(0)) + '% a year, 80% range ' + f1(q(0, 0.1)) + ' to ' + f1(q(0, 0.9)) + '%.</div></div>'
      + '<div class="gdp-tile paper"><div class="tl">' + esc(who) + '</div><div class="tv">' + pct(p1) + '</div>'
      + '<div class="ts">chance of Yes. Average growth ' + f1(g(1)) + '% a year, 80% range ' + f1(q(1, 0.1)) + ' to ' + f1(q(1, 0.9)) + '%.</div></div>'
      + '<div class="gdp-tile cut"><div class="tl">The cutoff that best separates the views</div><div class="tv">' + f2(y.growth) + '%</div>'
      + '<div class="ts">a year. At it the contract resolves in line with the true view ' + pct((y.sensitivity + 1 - y.falsePositive) / 2) + ' of the time on average (Youden’s J ' + y.J.toFixed(2) + ').</div></div>';
    const art = atArticle(S);
    $('#gdpQuestion').innerHTML = 'Will world GDP per capita grow by more than ' + S.strike.toFixed(1) + '% a year, on average, from '
      + m.base + ' to ' + E + '?<span class="gdp-qnote">' + (art ? 'The article’s contract, drawn at the article’s settings.'
      : 'At your settings. The article’s contract is more than ' + D.strike.toFixed(1) + '% a year from ' + m.base + ' to ' + D.endYear + '.') + '</span>';
  }

  // ---------------------------------------------------------------- Figure 1: temperature
  function drawTemp(svg) {
    clear(svg);
    const m = M.meta, T = m.noaa.T, T0 = m.noaa.first;
    const t26 = S.t26 === 'value' ? S.t26Value : m.temps.T26.mean, s26 = S.t26 === 'value' ? 0 : m.temps.T26.sd;
    const t27 = S.t27 === 'value' ? S.t27Value : m.temps.T27.mean, s27 = S.t27 === 'value' ? 0 : m.temps.T27.sd;
    const years = []; for (let t = 1950; t <= 2025; t++) years.push(t);
    const val = t => T[t - T0];
    const F = frame({ W: 960, H: 420, L: 58, R: 200, T: 22, B: 36, x0: 1949, x1: 2031, y0: -0.35, y1: 1.8 });
    svg.appendChild(txt('°C above the 1901–2000 average', { x: F.L, y: 13, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, F, [-0.25, 0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75], v => minus(v.toFixed(2)));
    xTicks(svg, F, [1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020], String);
    svg.appendChild(el('path', { d: path(years.map(t => [F.x(t), F.y(val(t))])), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.7 }));
    svg.appendChild(el('path', { d: path([[F.x(2025), F.y(val(2025))], [F.x(2026), F.y(t26)], [F.x(2027), F.y(t27)]]), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.2, 'stroke-dasharray': '2 3' }));
    m.temps.strongElNinoYears.forEach(t => {
      const c = el('circle', { cx: F.x(t), cy: F.y(val(t)), r: 5.2, fill: PAPER, stroke: 'var(--panel)', 'stroke-width': 1.3 });
      hover(c, () => tip.rows(String(t), [['Global temperature', minus(val(t).toFixed(2)) + ' °C'], ['Winter ONI', '1.5 or more']], 'a year that began during a strong El Niño'));
      svg.appendChild(c);
    });
    [[2026, t26, s26, 'var(--muted)'], [2027, t27, s27, PAPER]].forEach(([t, v, sd, c]) => {
      if (sd > 0) svg.appendChild(el('line', { x1: F.x(t), x2: F.x(t), y1: F.y(v - 1.645 * sd), y2: F.y(v + 1.645 * sd), stroke: c, 'stroke-width': 1.6 }));
      const dot = el('circle', { cx: F.x(t), cy: F.y(v), r: 5, fill: sd > 0 ? 'var(--panel)' : c, stroke: c, 'stroke-width': 2 });
      hover(dot, () => tip.rows(t + (sd > 0 ? ', estimate' : ', your value'), [['Global temperature', minus(v.toFixed(2)) + ' °C'],
        sd > 0 ? ['90% range', minus((v - 1.645 * sd).toFixed(2)) + ' to ' + (v + 1.645 * sd).toFixed(2) + ' °C'] : null,
        ['Method', t === 2026 ? m.temps.T26.method : m.temps.T27.method]], 'updated monthly until NOAA publishes the annual value'));
      svg.appendChild(dot);
    });
    // the three ranges of NOAA's 2027 value
    // the three ranges stay in view wherever the 2027 value is set, each at least a sliver tall
    const cen = m.temps.bins.find(b => b.lower != null && b.upper != null) || { lower: t27 - 0.04, upper: t27 + 0.04 };
    const bx = F.x(2028.6), bw = 22;
    const lo = Math.min(t27 - 1.645 * Math.max(s27, 0.06), cen.lower - 0.08) - 0.03, hi = Math.max(t27 + 1.645 * Math.max(s27, 0.06), cen.upper + 0.08) + 0.03;
    const cols = { cool: 'var(--cool)', central: 'var(--muted)', warm: 'var(--warm)' };
    m.temps.bins.forEach(b => {
      const a = Math.max(b.lower == null ? lo : b.lower, lo), z = Math.min(b.upper == null ? hi : b.upper, hi);
      const on = S.t27 === 'bin:' + b.name;
      svg.appendChild(el('rect', { x: bx, y: F.y(z), width: bw, height: F.y(a) - F.y(z), fill: cols[b.name], 'fill-opacity': on ? 0.55 : 0.22, stroke: on ? INK : 'none' }));
      const label = b.name[0].toUpperCase() + b.name.slice(1) + ': ' + condition(b.name);
      svg.appendChild(txt(label, { x: bx + bw + 8, y: (F.y(a) + F.y(z)) / 2 + 4, class: 'ax', fill: on ? INK : null, 'font-weight': on ? 700 : 400 }));
    });
    svg.appendChild(txt('NOAA’s 2027 value', { x: bx, y: F.y(hi) - 8, class: 'axl', 'font-weight': 700 }));
  }

  // ---------------------------------------------------------------- Figure 2: the paper's estimate
  function drawIrf(svg) {
    clear(svg);
    const e = M.irfByKey[S.irf], b = M.irfBand(S);
    const H = b.theta.map((_, i) => i);
    const lo = Math.min(...b.q05, ...b.theta.map(v => v * S.share), 0) - 1.5, hi = Math.max(...b.q95, 0) + 2;
    const F = frame({ W: 960, H: 400, L: 58, R: 30, T: 22, B: 44, x0: -0.3, x1: 10.3, y0: lo, y1: hi });
    svg.appendChild(txt('Change in world GDP per person (%) after a 1 °C global temperature shock', { x: F.L, y: 13, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, F, ticksFor(lo, hi, 6), v => minus(String(v)));
    xTicks(svg, F, H, String);
    svg.appendChild(txt('Years after the shock', { x: (F.L + F.W - F.R) / 2, y: F.H - 6, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(el('line', { x1: F.L, x2: F.W - F.R, y1: F.y(0), y2: F.y(0), stroke: 'var(--rule)', 'stroke-width': 1.2 }));
    const X = H.map(F.x);
    if (S.uncertainty !== 'point') svg.appendChild(el('path', { d: area(X, b.q05.map(F.y), b.q95.map(F.y)), fill: PAPER, 'fill-opacity': 0.16 }));
    svg.appendChild(el('path', { d: path(H.map(i => [F.x(i), F.y(b.theta[i])])), fill: 'none', stroke: PAPER, 'stroke-width': 2.2 }));
    H.forEach(i => svg.appendChild(el('circle', { cx: F.x(i), cy: F.y(b.theta[i]), r: 3.8, fill: PAPER, stroke: 'var(--panel)', 'stroke-width': 1.2 })));
    if (S.share !== 1) svg.appendChild(el('path', { d: path(H.map(i => [F.x(i), F.y(S.share * b.theta[i])])), fill: 'none', stroke: PAPER, 'stroke-width': 1.6, 'stroke-dasharray': '5 4' }));
    let tr = 0; b.theta.forEach((v, i) => { if (v < b.theta[tr]) tr = i; });
    svg.appendChild(txt(minus(Math.round(b.theta[tr])) + '% after ' + tr + ' years', { x: F.x(tr), y: F.y(b.theta[tr]) + 22, 'text-anchor': 'middle', class: 'lbl', fill: INK, 'font-weight': 700, 'font-size': 12.5 }));
    svg.appendChild(txt(e.label, { x: F.W - F.R, y: F.T + 14, 'text-anchor': 'end', class: 'ax', fill: INK }));
    hoverBand(svg, F, H, i => ({ title: 'Year ' + i + ' after a 1 °C shock',
      rows: [['Central estimate', f1(b.theta[i]) + '%'], S.uncertainty !== 'point' ? ['90% range', f1(b.q05[i]) + ' to ' + f1(b.q95[i]) + '%'] : null,
             S.share !== 1 ? ['× your share (' + pct(S.share) + ')', f1(S.share * b.theta[i]) + '%'] : null],
      foot: e.label }));
  }

  // ---------------------------------------------------------------- Figure 3: background growth
  function drawGrowth(svg) {
    clear(svg);
    const m = M.meta, E = S.endYear, hist = m.wdi.growth;
    const hy = Object.keys(hist).map(Number);
    const proj = [], expect = [];
    for (let t = m.base + 1; t <= E; t++) {
      proj.push([t, m.imf.growth[String(t)]]);
      expect.push([t, S.baseline === 'frozen' ? M.trendMeta(S).mu : m.imf.growth[String(t)] + S.missShare * m.imf.meanErrorByLead[t - m.anchor - 1]]);
    }
    const g0 = R.growthMean(0, E), g1 = R.growthMean(1, E), q10 = R.growthQuantile(0, E, 0.1), q90 = R.growthQuantile(0, E, 0.9);
    const F = frame({ W: 960, H: 420, L: 58, R: 230, T: 22, B: 36, x0: 1990, x1: E + 1, y0: -5.4, y1: 6.2 });
    svg.appendChild(txt('Growth in world GDP per person (%)', { x: F.L, y: 13, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, F, [-4, -2, 0, 2, 4, 6], v => minus(String(v)));
    xTicks(svg, F, ticksFor(1990, E, 8).filter(v => v >= 1990), String);
    const bw = (F.x(1) - F.x(0)) * 0.72;
    hy.forEach(t => {
      const v = hist[String(t)];
      const r = el('rect', { x: F.x(t) - bw / 2, y: Math.min(F.y(v), F.y(0)), width: bw, height: Math.abs(F.y(v) - F.y(0)), fill: 'var(--rule)' });
      hover(r, () => tip.rows(String(t), [['World GDP per person', signed1(v) + '%']], 'World Bank WDI, 100 × log growth'));
      svg.appendChild(r);
    });
    svg.appendChild(el('rect', { x: F.x(m.base + 0.5), y: F.y(q90), width: F.x(E + 0.5) - F.x(m.base + 0.5), height: F.y(q10) - F.y(q90), fill: NULL, 'fill-opacity': 0.13 }));
    svg.appendChild(el('line', { x1: F.L, x2: F.x(E + 0.6), y1: F.y(0), y2: F.y(0), stroke: 'var(--rule)' }));
    svg.appendChild(el('path', { d: path(proj.map(p => [F.x(p[0]), F.y(p[1])])), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.6 }));
    svg.appendChild(el('path', { d: path(expect.map(p => [F.x(p[0]), F.y(p[1])])), fill: 'none', stroke: NULL, 'stroke-width': 2.2 }));
    expect.forEach(p => svg.appendChild(el('circle', { cx: F.x(p[0]), cy: F.y(p[1]), r: 3.6, fill: NULL, stroke: 'var(--panel)', 'stroke-width': 1.1 })));
    svg.appendChild(el('line', { x1: F.x(m.base + 0.5), x2: F.x(E + 0.5), y1: F.y(g1), y2: F.y(g1), stroke: PAPER, 'stroke-width': 2, 'stroke-dasharray': '6 3' }));
    const pm = proj.reduce((s, p) => s + p[1], 0) / proj.length;
    endLabels(svg, [
      { y: F.y(pm) - 10, col: 'var(--muted)', lines: ['IMF projection', f1(pm) + '% a year'] },
      { y: F.y(g0), col: NULL, lines: ['No temperature effect', f1(g0) + '% a year; 80% range', f1(q10) + ' to ' + f1(q90) + '% (shaded)'] },
      { y: F.y(g1), col: PAPER, lines: [S.share === 1 ? 'Paper’s estimate' : 'Paper × ' + pct(S.share), f1(g1) + '% a year'] },
    ], F.x(E + 0.9), 40);
    hoverBand(svg, F, proj.map(p => p[0]), t => {
      const i = t - m.base - 1;
      return { title: String(t), rows: [['IMF projection', f2(proj[i][1]) + '%'],
        [sw(NULL) + (S.baseline === 'frozen' ? trendLabel() : 'Less the IMF’s average miss' + (S.missShare !== 1 ? ' × ' + pct(S.missShare) : '')), f2(expect[i][1]) + '%']],
        foot: 'growth per person, 100 × log points' };
    });
  }

  // ---------------------------------------------------------------- Figure 5: the years after the El Niño
  function drawAfter(svg) {
    clear(svg);
    const m = M.meta, fam = m.families[M.irfByKey[S.irf].family], E = S.endYear;
    const sh = t => fam.shocks[String(t)];
    const an = m.temps.analogYears.filter(y => sh(y + 3) != null);
    const paths = an.map(y => [0, 1, 2, 3].map(j => sh(y + j)));
    const avg = [0, 1, 2, 3].map(j => paths.reduce((s, p) => s + p[j], 0) / paths.length);
    const sim = [2027, 2028, 2029, 2030].map(t => R.shockMean[t - R.Y0]);
    // left panel
    const F = frame({ W: 450, H: 400, L: 58, R: 14, T: 40, B: 44, x0: -0.2, x1: 3.2, y0: -0.45, y1: 0.65 });
    svg.appendChild(txt('Strong El Niños are usually followed by cooler years', { x: 10, y: 14, class: 'axl', 'font-weight': 700 }));
    svg.appendChild(txt('Global temperature shock (°C)', { x: 10, y: 29, class: 'ax' }));
    yGrid(svg, F, [-0.4, -0.2, 0, 0.2, 0.4, 0.6], v => minus(v.toFixed(1)));
    ['El Niño year', '+1', '+2', '+3'].forEach((s, j) => svg.appendChild(txt(s, { x: F.x(j), y: F.H - F.B + 16, 'text-anchor': 'middle', class: 'ax' })));
    paths.forEach((p, i) => {
      const ln = el('path', { d: path(p.map((v, j) => [F.x(j), F.y(v)])), fill: 'none', stroke: 'var(--rule)', 'stroke-width': 1.4 });
      hover(ln, () => tip.rows('After the ' + an[i] + ' El Niño', p.map((v, j) => [String(an[i] + j), minus(v.toFixed(2)) + ' °C']), 'the paper’s shock measure, NOAA temperature'));
      svg.appendChild(ln);
    });
    svg.appendChild(el('path', { d: path(avg.map((v, j) => [F.x(j), F.y(v)])), fill: 'none', stroke: NULL, 'stroke-width': 2.4 }));
    svg.appendChild(el('path', { d: path(sim.map((v, j) => [F.x(j), F.y(v)])), fill: 'none', stroke: PAPER, 'stroke-width': 2.2, 'stroke-dasharray': S.future === 'simulate' ? '5 3' : '1 0' }));
    [[avg, NULL], [sim, PAPER]].forEach(([v, c]) => v.forEach((y, j) => svg.appendChild(el('circle', { cx: F.x(j), cy: F.y(y), r: 3.6, fill: c, stroke: 'var(--panel)', 'stroke-width': 1.1 }))));
    svg.appendChild(txt('Gray: each of the ' + an.length + ' past strong El Niños', { x: F.L + 4, y: F.T + 6, class: 'ax' }));
    svg.appendChild(txt('Blue: their average', { x: F.L + 4, y: F.T + 20, class: 'ax', fill: NULL }));
    svg.appendChild(txt(S.future === 'simulate' ? 'Orange: 2027 and the simulated years after it' : 'Orange: 2027, then no further shocks', { x: F.L + 4, y: F.T + 34, class: 'ax', fill: PAPER }));
    // right panel
    const yrs = []; for (let t = m.base; t <= 2035; t++) yrs.push(t);
    const w = [0].concat(R.effectMean(true)), wo = [0].concat(R.effectMean(false));
    const lo = Math.min(...w, ...wo) - 0.6, hi = Math.max(...w, ...wo, 0) + 0.8;
    const G = frame({ W: 960, H: 400, L: 548, R: 20, T: 40, B: 44, x0: m.base - 0.2, x1: 2035.2, y0: lo, y1: hi });
    svg.appendChild(txt('The paper’s prediction, with and without the years after 2027', { x: 500, y: 14, class: 'axl', 'font-weight': 700 }));
    svg.appendChild(txt('GDP per person against the no-effect path, % from ' + m.base, { x: 500, y: 29, class: 'ax' }));
    svg.appendChild(el('rect', { x: G.x(m.base), y: G.T, width: G.x(E) - G.x(m.base), height: G.H - G.T - G.B, fill: 'var(--shade)' }));
    svg.appendChild(txt('Measured ' + m.base + '–' + E, { x: G.x(m.base) + 4, y: G.T + 12, class: 'ax' }));
    yGrid(svg, G, ticksFor(lo, hi, 5), v => minus(String(v)), G.L, G.W - G.R);
    xTicks(svg, G, yrs.filter(t => t % 2 === 0), String);
    svg.appendChild(el('path', { d: path(yrs.map((t, i) => [G.x(t), G.y(wo[i])])), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.6, 'stroke-dasharray': '4 3' }));
    svg.appendChild(el('path', { d: path(yrs.map((t, i) => [G.x(t), G.y(w[i])])), fill: 'none', stroke: PAPER, 'stroke-width': 2.2 }));
    const ie = E - m.base;
    [[PAPER, '', 'Plus the simulated years after the El Niño'], ['var(--muted)', '4 3', 'Shocks through 2027 only']].forEach(([c, dash, s2], i) => {
      const yy = G.T + 30 + i * 16, x0 = G.W - G.R - 210;
      svg.appendChild(el('line', { x1: x0, x2: x0 + 22, y1: yy - 4, y2: yy - 4, stroke: c, 'stroke-width': 2, 'stroke-dasharray': dash || null }));
      svg.appendChild(txt(s2, { x: x0 + 28, y: yy, class: 'ax', fill: c === PAPER ? PAPER : null }));
    });
    hoverBand(svg, G, yrs, t => {
      const i = t - m.base;
      return { title: String(t), rows: [[sw(PAPER) + 'With the simulated years', f2(w[i]) + '%'], ['Shocks through 2027 only', f2(wo[i]) + '%']],
        foot: 'mean over paths; the paper’s full effect, before the netting and the share' };
    });
    const gap = w[ie] - wo[ie];
    svg.appendChild(txt(Math.abs(gap) < 0.1 ? 'Later years barely change the ' + m.base + '–' + E + ' gap'
      : 'Later years change the ' + m.base + '–' + E + ' gap by ' + signed1(gap) + ' points', { x: G.L + 6, y: G.H - G.B - 8, class: 'lbl', fill: INK }));
  }

  // ---------------------------------------------------------------- Figure 6: the end year
  function drawEndYear(svg) {
    clear(svg);
    const sc = scan(), best = bestEnd(sc), E = S.endYear;
    const xs = sc.map(y => y.E);
    const F = frame({ W: 960, H: 400, L: 58, R: 230, T: 22, B: 44, x0: xs[0] - 0.4, x1: xs[xs.length - 1] + 0.4, y0: 35, y1: 100 });
    svg.appendChild(txt('Chance the contract resolves in line with the true view (%)', { x: F.L, y: 13, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, F, [40, 50, 60, 70, 80, 90, 100], String);
    xTicks(svg, F, xs, String);
    svg.appendChild(txt('Last year of the growth period', { x: (F.L + F.W - F.R) / 2, y: F.H - 6, 'text-anchor': 'middle', class: 'axl' }));
    const lines = [[sc.map(y => 100 * y.sensitivity), PAPER, 1.8, ['If the paper is right:', 'chance of No']],
                   [sc.map(y => 100 * (1 - y.falsePositive)), NULL, 1.8, ['If temperature shocks have', 'no effect: chance of Yes']],
                   [sc.map(y => 50 * (y.sensitivity + 1 - y.falsePositive)), INK, 2.6, ['Average of the two']]];
    lines.forEach(([v, c, w]) => {
      svg.appendChild(el('path', { d: path(xs.map((x, i) => [F.x(x), F.y(v[i])])), fill: 'none', stroke: c, 'stroke-width': w }));
      xs.forEach((x, i) => svg.appendChild(el('circle', { cx: F.x(x), cy: F.y(v[i]), r: 3.4, fill: c, stroke: 'var(--panel)', 'stroke-width': 1 })));
    });
    const bi = xs.indexOf(best);
    svg.appendChild(el('circle', { cx: F.x(best), cy: F.y(lines[2][0][bi]), r: 7, fill: INK, stroke: 'var(--panel)', 'stroke-width': 1.6 }));
    svg.appendChild(txt('Best: ' + best + ' (' + Math.round(lines[2][0][bi]) + '% average)', { x: F.x(best), y: F.y(lines[2][0][bi]) - 14, 'text-anchor': 'middle', class: 'lbl', fill: INK, 'font-weight': 700 }));
    if (E !== best) svg.appendChild(el('line', { x1: F.x(E), x2: F.x(E), y1: F.T, y2: F.H - F.B, stroke: 'var(--warn)', 'stroke-dasharray': '3 3' }));
    svg.appendChild(el('line', { x1: F.L, x2: F.W - F.R, y1: F.y(50), y2: F.y(50), stroke: 'var(--rule)' }));
    svg.appendChild(txt('50% = a coin flip', { x: F.W - F.R, y: F.y(50) - 5, 'text-anchor': 'end', class: 'ax' }));
    endLabels(svg, lines.map(([v, c, , name]) => ({ y: F.y(v[v.length - 1]), col: c, lines: name })), F.W - F.R + 10, 30);
    hoverBand(svg, F, xs, x => {
      const y = sc[xs.indexOf(x)];
      return { title: 'Growth from ' + M.meta.base + ' to ' + x, rows: [['Best cutoff', f2(y.growth) + '% a year'],
        [sw(PAPER) + 'Paper right: chance of No', pct1(y.sensitivity)], [sw(NULL) + 'No effect: chance of Yes', pct1(1 - y.falsePositive)],
        ['Youden’s J', y.J.toFixed(3)]], foot: 'each end year at its own best cutoff' };
    });
  }

  // ---------------------------------------------------------------- Figure 7: the two predictions at the cutoff
  function kde(vals, lo, hi, nb) {
    const n = vals.length, bins = new Float64Array(nb), w = (hi - lo) / nb;
    let s = 0, ss = 0;
    for (let i = 0; i < n; i++) { s += vals[i]; ss += vals[i] * vals[i]; const b = Math.floor((vals[i] - lo) / w); if (b >= 0 && b < nb) bins[b]++; }
    const sd = Math.sqrt(ss / n - (s / n) * (s / n)), bw = 0.18 * sd / w;        // scipy's gaussian_kde, bw_method=0.18
    const K = Math.ceil(4 * bw), ker = [];
    for (let j = -K; j <= K; j++) ker.push(Math.exp(-0.5 * (j / bw) * (j / bw)));
    const ks = ker.reduce((a, b) => a + b, 0);
    const out = new Float64Array(nb);
    for (let i = 0; i < nb; i++) { let v = 0; for (let j = -K; j <= K; j++) { const b = i + j; if (b >= 0 && b < nb) v += bins[b] * ker[j + K]; } out[i] = v / ks / n / w; }
    return out;
  }
  function drawDist(svg) {
    clear(svg);
    const m = M.meta, E = S.endYear, k = E - m.base, mu = R.mu(E);
    const g0 = Float64Array.from(R.m(0, E), v => mu + v / k), g1 = Float64Array.from(R.m(1, E), v => mu + v / k);
    const lo = Math.floor(2 * R.growthQuantile(1, E, 0.002)) / 2, hi = Math.ceil(2 * R.growthQuantile(0, E, 0.998)) / 2;
    const nb = 600, d0 = kde(g0, lo, hi, nb), d1 = kde(g1, lo, hi, nb);
    const xs = Array.from({ length: nb }, (_, i) => lo + (i + 0.5) * (hi - lo) / nb);
    const top = Math.max(...d0, ...d1) * 1.32;
    const F = frame({ W: 960, H: 400, L: 30, R: 30, T: 22, B: 44, x0: lo, x1: hi, y0: 0, y1: top });
    svg.appendChild(txt('Simulated average growth, ' + m.base + ' to ' + E, { x: F.L, y: 13, class: 'axl', 'font-weight': 700 }));
    xTicks(svg, F, ticksFor(lo, hi, 8), v => minus(String(v)));
    svg.appendChild(txt('Average yearly growth in world GDP per person, ' + m.base + ' to ' + E + ' (%)', { x: (F.L + F.W - F.R) / 2, y: F.H - 6, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(el('line', { x1: F.L, x2: F.W - F.R, y1: F.y(0), y2: F.y(0), stroke: 'var(--rule)' }));
    const c = S.strike;
    const fillPart = (d, col, left) => {
      const pts = xs.map((x, i) => [x, d[i]]).filter(p => (left ? p[0] <= c : p[0] > c));
      if (!pts.length) return;
      const dd = path([[F.x(pts[0][0]), F.y(0)]].concat(pts.map(p => [F.x(p[0]), F.y(p[1])]), [[F.x(pts[pts.length - 1][0]), F.y(0)]])) + 'Z';
      svg.appendChild(el('path', { d: dd, fill: col, 'fill-opacity': 0.2 }));
    };
    fillPart(d1, PAPER, true); fillPart(d0, NULL, false);
    [[d0, NULL], [d1, PAPER]].forEach(([d, col]) => svg.appendChild(el('path', { d: path(xs.map((x, i) => [F.x(x), F.y(d[i])])), fill: 'none', stroke: col, 'stroke-width': 2.2 })));
    svg.appendChild(el('line', { x1: F.x(c), x2: F.x(c), y1: F.T, y2: F.y(0), stroke: INK, 'stroke-width': 1.4, 'stroke-dasharray': '5 4' }));
    svg.appendChild(txt('Cutoff: ' + c.toFixed(1) + '% a year', { x: F.x(c) - 6, y: F.T + 10, 'text-anchor': 'end', class: 'lbl', fill: INK }));
    const p1 = 1 - R.pYes(1, E, c), p0 = R.pYes(0, E, c);
    const i1 = d1.indexOf(Math.max(...d1)), i0 = d0.indexOf(Math.max(...d0));
    svg.appendChild(txt((S.share === 1 ? 'If the paper is right: ' : 'At ' + pct(S.share) + ' of the paper: ') + pct(p1), { x: F.x(xs[i1]) - 14, y: F.y(d1[i1]) - 22, 'text-anchor': 'end', class: 'lbl', fill: PAPER, 'font-weight': 700 }));
    svg.appendChild(txt('of outcomes at or below the cutoff', { x: F.x(xs[i1]) - 14, y: F.y(d1[i1]) - 8, 'text-anchor': 'end', class: 'lbl', fill: PAPER }));
    svg.appendChild(txt('No temperature effect: ' + pct(p0), { x: F.x(xs[i0]) + 14, y: F.y(d0[i0]) - 22, class: 'lbl', fill: NULL, 'font-weight': 700 }));
    svg.appendChild(txt('of outcomes above it', { x: F.x(xs[i0]) + 14, y: F.y(d0[i0]) - 8, class: 'lbl', fill: NULL }));
  }

  // ---------------------------------------------------------------- Figure 8: timeline
  function drawTimeline(svg) {
    clear(svg);
    const m = M.meta, E = S.endYear, settle = E + 1, d = (y, mo) => y + (mo - 1) / 12;
    const F = frame({ W: 960, H: 300, L: 290, R: 20, T: 70, B: 30, x0: d(2026, 7), x1: d(settle + 1, 3), y0: 0, y1: 4 });
    const rows = [['Open for comment; estimates updated monthly', d(2026, 10), d(2027, 1.5), 'var(--gdp-paper)'],
                  ['Conditional contracts trade', d(2027, 1.5), d(2028, 1.5), 'var(--accent)'],
                  ['Growth period measured', d(m.base + 1, 1), d(E + 1, 1), NULL],
                  ['World Bank publishes its ' + E + ' estimate', d(E + 1, 7), d(settle, 10), 'var(--muted)']];
    const rowY = i => F.T + 12 + i * 52;
    for (let y = 2027; y <= settle + 1; y++) {
      svg.appendChild(el('line', { x1: F.x(y), x2: F.x(y), y1: F.T - 6, y2: F.H - F.B, class: 'grid' }));
      svg.appendChild(txt(String(y), { x: F.x(y), y: F.H - F.B + 16, 'text-anchor': 'middle', class: 'ax' }));
    }
    rows.forEach(([lab, a, b, c], i) => {
      svg.appendChild(txt(lab, { x: F.L - 10, y: rowY(i) + 5, 'text-anchor': 'end', class: 'ax', fill: INK }));
      svg.appendChild(el('rect', { x: F.x(a), y: rowY(i) - 11, width: F.x(b) - F.x(a), height: 22, fill: c, 'fill-opacity': 0.85, rx: 3 }));
    });
    svg.appendChild(el('rect', { x: F.x(d(2028, 1.5)), y: rowY(1) - 11, width: F.x(d(settle, 10)) - F.x(d(2028, 1.5)), height: 22, fill: 'var(--accent)', 'fill-opacity': 0.3, rx: 3 }));
    svg.appendChild(txt('the converted contract trades until it resolves', { x: F.x(d(2028, 3)), y: rowY(1) + 4, class: 'ax', fill: INK }));
    [[d(2027, 1.5), 'Mid-January 2027: NOAA’s 2026 value; contracts list', 14],
     [d(2028, 1.5), 'Mid-January 2028: NOAA’s 2027 value; the matching contract converts', 34],
     [d(settle, 10), 'October 1, ' + settle + ': contracts resolve', 54]].forEach(([x, s, y]) => {
      svg.appendChild(el('line', { x1: F.x(x), x2: F.x(x), y1: y + 4, y2: F.H - F.B, stroke: INK, 'stroke-dasharray': '3 2' }));
      svg.appendChild(txt(s, { x: F.x(x) + (x > d(settle - 1, 1) ? -5 : 5), y, 'text-anchor': x > d(settle - 1, 1) ? 'end' : 'start', class: 'ax', fill: INK, 'font-weight': 700 }));
    });
  }

  // ---------------------------------------------------------------- method notes, figure by figure
  function notes() {
    const m = M.meta, ck = m.checks, E = S.endYear, n = M.n.toLocaleString();
    const ref = 'run of ' + dateOf(m.refreshedUtc);
    const e = M.irfByKey[S.irf];
    const N = {
      main: {
        title: 'How the two views, the cutoff and the ladder are computed',
        body: [
          'Each line is the average of ' + n + ' simulated paths of world GDP per person, as a level with ' + m.base + ' = 100; the shading spans the 10th to 90th percentile of the paths in each year. A path is one possible ' + m.base + '–2035 for the world economy and for global temperature, built from the record of each, as the other figures’ notes describe.',
          'With no temperature effect, GDP per person follows the IMF’s projection plus the path’s own forecast miss:',
          { tex: '\\ln y^{0}_{t} = \\ln y_{' + m.base + '} + \\sum_{s=' + (m.base + 1) + '}^{t} g^{\\mathrm{IMF}}_{s} + D_{t}' },
          'where $g^{\\mathrm{IMF}}_s$ is the IMF’s October 2026 projection of world growth per person, put in World Bank units, and $D_t$ is the miss from ' + m.base + ' to $t$, drawn from the IMF’s own record of misses (' + (A.schema === 2 ? labelOf('growth', 'Extended Data Fig. 4') : 'Figure 3') + '’s note).',
          'If the paper is right, the same path also carries the paper’s effect of every temperature shock since 2015, and of the simulated shocks after 2027:',
          { tex: '\\ln y^{1}_{t} = \\ln y^{0}_{t} + \\lambda \\left[ \\sum_{\\tau} u_{\\tau} \\left( \\theta_{t-\\tau} - \\theta_{' + m.base + '-\\tau} \\right) - N_{t} \\right]' },
          '$u_\\tau$ is the part of year $\\tau$’s shock that the two shocks before it do not predict (the paper’s projection controls for them), $\\theta_h$ is the paper’s estimated response after $h$ years, held at its year-10 value after that, and the path draws its own $\\theta$ from the estimate’s bootstrap. $N_t$ removes the part of the IMF’s past misses that the paper’s estimates attribute to past temperature shocks, so a temperature effect is not counted twice. $\\lambda$ is the share of the paper’s effect, 1 at the article’s settings.',
          'The contract resolves Yes when average growth beats the strike $X$:',
          { tex: '\\bar g = \\frac{100}{T-' + m.base + '} \\ln \\frac{y_{T}}{y_{' + m.base + '}} > X' },
          'Each view’s chance of Yes is the share of the ' + n + ' paths for which that holds. The cutoff that best separates the views maximises Youden’s $J(c) = P_{1}(\\bar g \\le c) - P_{0}(\\bar g \\le c)$, the chance of No if the paper is right plus the chance of Yes if it is not, less one; it is the median of the candidate cutoffs whose J is within 0.0025 of the best, which keeps Monte Carlo noise out of it.',
          'Until the contract lists, ' + (m.ladder.strikes.length > 1 ? 'each proposed strike' : 'the proposed strike') + ' on the ladder sits at the level that growth of exactly that much reaches by ' + E + ', hatched, with two marks for the two views’ chances of Yes. Behind the ladder, a curve for each view gives its chance of Yes at every strike on the same axis. Where the market’s price settles between the two marks, once it trades, is a running public measure of how much credence its participants give the paper. Once the exchange lists the contract, its prices replace the hatching and each bar opens the contract on IBKR.',
          'This page re-runs the model in your browser on the same paths as the published analysis. The IMF misses, the bootstrap draw each path uses, the 2026–27 temperatures and the simulated shocks after 2027 are read from that run, not drawn again, and only what a control names is changed. At the article’s settings it reproduces the run within 0.002 points of GDP on every path; the repository’s test checks this on twelve designs.',
        ],
        table: variantsTable(),
        code: [['simulate()', 'bk_elnino/analysis.py'], ['the cutoff', 'bk_elnino/wager.py'], ['this page’s model', 'site_tab/js/elnino-model.js'], ['the check against the run', 'tests/test_site_model.py']],
        n: n + ' simulated paths, the ' + ref + '. ' + (listedBook() ? 'The ladder carries the exchange’s listed strikes.'
          : m.ladder.strikes.length > 1 ? 'The proposed strikes are ' + m.ladder.strikes.map(x => x.toFixed(1) + '%').join(', ') + ', and the exchange’s own replace them when it lists.'
          : 'The proposed strike is more than ' + m.ladder.strikes[0].toFixed(1) + '% a year, the article’s contract, and the exchange’s own strikes replace it when it lists.'),
      },
      1: {
        title: 'Global temperature and the 2026–27 estimates',
        body: [
          'NOAA’s global annual mean surface temperature (NOAAGlobalTemp, from Climate at a Glance) against its 1901–2000 average; the contract’s conditions use the same series. Orange dots mark years whose December–February Oceanic Niño Index was 1.5 °C or more, an El Niño peaking in the winter that opens the year.',
          'Until NOAA publishes a year’s annual value, the page carries an estimate with its uncertainty. 2026: ' + m.temps.T26.method + ', ' + m.temps.T26.mean.toFixed(2) + ' ± ' + m.temps.T26.sd.toFixed(2) + ' °C: the months NOAA has published, plus the rest of the year from the change between those months and the rest in past El Niño onset years. 2027: ' + m.temps.T27.method + ', ' + m.temps.T27.mean.toFixed(2) + ' ± ' + m.temps.T27.sd.toFixed(2) + ' °C: the temperature shock predicted from the winter Relative Oceanic Niño Index in 2027 and the two winters before it, added to what the paper’s filter expects for 2027. Bars are 90% ranges.',
          'The three ranges split the 2027 forecast halfway between its 20th, 50th and 80th percentiles, so each holds about a third of it today; within a range, a path’s 2027 value is drawn from the forecast cut to that range.',
        ],
        code: [['nowcast and ENSO forecast', 'bk_elnino/temperature.py'], ['the ranges', 'bk_elnino/analysis.py']],
        n: 'NOAA annual values through 2025; estimates as of the ' + ref + '.',
      },
      2: {
        title: 'The paper’s estimate and its uncertainty',
        body: [
          'The paper estimates, by local projection, how world real GDP per person moves in the $h$ years after a global temperature shock (its equation 2):',
          { tex: 'y_{t+h} - y_{t-1} = \\alpha_h + \\theta_h\\, s_t + \\sum_{j=1}^{2} \\beta_{h,j}\\, s_{t-j} + \\gamma_h\\, \\text{controls}_t + \\varepsilon_{t+h}' },
          'with $y$ the log of world GDP per person (Penn World Table, 1960–2019) and $s_t$ the shock, the year’s temperature less what the temperatures of two and three years earlier predict (its equation 1, as the authors’ data apply it):',
          { tex: 's_t = T_t - \\left( a + b_1 T_{t-2} + b_2 T_{t-3} \\right)' },
          'This page re-estimates the paper’s numbers from the authors’ replication files, and the time-series responses match the authors’ own output to ' + ck.irfReplicationMaxAbsDiff.gdp_b.toExponential(1) + ' points. The paper’s filter is fitted on Berkeley Earth temperatures; the contract measures shocks on NOAA’s series, which the paper reports tracks it closely (here, correlation ' + ck.noaaShockVsAuthors.corr.toFixed(3) + ' with the authors’ shocks over ' + ck.noaaShockVsAuthors.first + '–' + ck.noaaShockVsAuthors.last + '). The filter on NOAA, fitted on the paper’s window, is ' + ck.hamiltonPrimary + '.',
          'The band is the 5th to 95th percentile of 4,000 draws, the authors’ wild bootstrap for the time series, re-centred on the estimate, and for the country panels normal draws with the paper’s band-implied standard errors and the time series’ correlation across horizons (an approximation). The figure shows ' + e.label + '. ' + e.note,
        ],
        code: [['local projections', 'bk_elnino/lp.py'], ['the estimates', 'bk_elnino/irf.py'], ['the country panel', 'bk_elnino/panel.py'], ['the filter', 'bk_elnino/hamilton.py']],
        n: 'Penn World Table world GDP per person, 1960–2019; 4,000 bootstrap draws per estimate.',
      },
      3: {
        title: 'The no-effect path and its spread',
        body: [
          'The gray bars are growth in world GDP per person, from the World Bank series the contract settles on (WDI NY.GDP.PCAP.PP.KD), as 100 × the change in its log. The IMF projection is the October 2026 World Economic Outlook’s world real GDP growth less its population growth, plus the average gap between World Bank and IMF-implied growth over 1991–2025 (' + f2(ck.wdiGap) + ' points), so it is in the settlement series’ units.',
          'The IMF’s October forecasts of 1990–2020 (' + ck.imfErrors.n_forecasts + ' forecasts) missed world growth by ' + f1(ck.imfErrors.sum_over_statistic_mean) + ' points on average over years 2–6 ahead, and fell short in ' + ck.imfErrors.n_below_forecast + ' of them. The no-effect path keeps that average miss, times the share on the control (1 at the article’s settings). Each path’s own miss is one historical forecast’s whole miss path, pulled part of the way to the average, plus noise with the same covariance across years:',
          { tex: 'e_{\\ell} = \\bar e_{\\ell} + \\kappa \\left( e^{(j)}_{\\ell} - \\bar e_{\\ell} \\right) + \\kappa\\,\\tfrac{1}{2}\\,\\eta_{\\ell}, \\qquad \\kappa = 1/\\sqrt{1.25}, \\qquad D_t = \\sum_{\\ell \\le t-2025} e_{\\ell}' },
          'so recessions and slowdowns keep their persistence across years, and the mixture keeps the record’s mean and covariance. Past six years ahead, each year is drawn on its own from the six-year-ahead misses. The spread control scales each path’s distance from the average miss.',
          'The trend option replaces the IMF projection with average growth from a first year through 2019, before the pandemic, and draws its spread from those years, resampled, with the average itself redrawn by bootstrap. The first year runs from 1991 to 2005 on the control and is 2000 at the article’s settings. It is now ' + trendLabel().replace(' average', '') + '.',
        ],
        code: [['IMF baseline and misses', 'bk_elnino/imf_baseline.py'], ['the trend option', 'bk_elnino/baseline.py']],
        n: m.imfSources.baseline + '. ' + m.imfSources.errors.replace(/ forecasts: /, ' forecasts of ') + '.',
      },
      5: {
        title: 'Temperatures after 2027',
        body: [
          'Under the paper’s estimates every later shock moves GDP too, so the years after the El Niño are simulated rather than ignored. If a path’s 2027 winter is a strong El Niño (Relative Oceanic Niño Index of 1.5 or more), its later winters replay the winters that followed one of the ' + m.temps.analogYears.length + ' past strong El Niños (' + m.temps.analogYears.join(', ') + '); otherwise they follow an AR(2) fitted to the winter index since 1951.',
          'A year’s shock is the ENSO regression’s prediction from the winter index in that year and the two before, plus a residual resampled from all years 1953–2025, volcanic years included, so eruptions occur at their historical rate. The shocks are then re-measured with the paper’s filter, fitted through 2022, so a lasting warmer level registers as positive shocks, as it does for 2023–27.',
          'The left panel shows the shocks in the El Niño year and the three after, by the selected estimate’s filter. The right panel shows the paper’s mean effect on GDP per person from ' + m.base + ', with and without the simulated years, before the netting of the IMF’s past misses and the share control.',
        ],
        code: [['simulated shocks', 'bk_elnino/future_temps.py'], ['re-measuring', 'bk_elnino/analysis.py']],
        n: n + ' simulated paths.',
      },
      6: {
        title: 'The end year that best separates the two views',
        body: [
          'For each end year the page finds that year’s own best cutoff (as in the contract figure’s note) and plots the chance of No if the paper is right, the chance of Yes if temperature shocks have no effect, and their average. The rule picks the end year with the highest Youden’s J, to two decimals, ties to the earlier year; the end year is fixed when the contracts list.',
          'The rule uses only the paper’s published estimates, the IMF projection and its record of misses, and temperature forecasts, all known before listing. It uses no GDP data after 2025. A longer period lets more of the paper’s predicted damage accumulate, but adds a year of uncertain background growth each year, and after the early 2030s the 2023–24 shocks wear off in the paper’s estimates.',
          'The article’s numbers for this choice were computed with 2027 drawn from today’s forecast rather than from the central range this page opens on, so they differ in the second decimal, with J of ' + scanJ(m.endYear) + ' for ' + m.endYear + ' and ' + scanJ(m.endYear - 1) + ' for ' + (m.endYear - 1) + ' there. Choose “Today’s forecast” under NOAA’s 2027 value to draw them here.',
        ],
        code: [['the scan and the rule', 'bk_elnino/analysis.py']],
        n: n + ' simulated paths per end year, 2028–2035.',
      },
      7: {
        title: 'The two predictions at the cutoff',
        body: [
          'Each curve is a smoothed histogram of average growth from ' + m.base + ' to ' + E + ' over the ' + n + ' paths under one view (Gaussian kernel, bandwidth 0.18 times the sample’s standard deviation). The shaded parts are the paths that resolve in line with each view, at or below the cutoff if the paper is right and above it if temperature shocks have no effect.',
          'The curves overlap because growth would vary for reasons that have nothing to do with temperature; the cutoff sits where the two kinds of error together are smallest.',
        ],
        code: [['the article’s version', 'docs/article_v2/make_figures.py']],
        n: n + ' simulated paths.',
      },
      8: {
        title: 'Dates in the terms',
        body: ['The terms are fixed when NOAA publishes its 2026 annual value, in mid-January 2027, and the conditional contracts list right after. When NOAA publishes 2027’s value, in mid-January 2028, positions in the matching contract convert into a regular contract at the same cost and the other two are refunded. All resolve on October 1 of the year after the end year, about three months after the World Bank’s first estimate for that year, or on the first business day of a later month if that estimate is late.'],
        n: 'End year ' + E + '.',
      },
    };
    if (A.schema !== 2) return N;
    // the Letter's figures: Fig. 1 adds its levels panel, Fig. 3 its ranges panel, and three figures are new
    const P = m.published;
    N.main = Object.assign({}, N.main, { body: ['Panel a shows world GDP per person in 2021 international dollars, the World Bank’s series from 2010, with each view’s median and 80% range from the published run. NOAA’s temperature sits above it on the same years, with the record, the ' + m.base + ' estimate and the ' + (m.base + 1) + ' forecast with their 90% ranges, and the simulated years’ median and 80% range.'
      + (shockStrip() ? ' Between them is each year’s global temperature shock, NOAA’s temperature less what the paper’s filter predicts from the two and three years before, with the authors’ published shocks and the ' + m.base + ' and ' + (m.base + 1) + ' estimates’ 90% ranges.' : '')
      + ' Panel a stays at the article’s settings. Panel b and the readout follow the controls.'].concat(N.main.body) });
    N.fig3 = Object.assign({}, N[7], { body: N[7].body.concat(['Panel b gives, for each range of NOAA’s ' + (m.base + 1) + ' value, each view’s chance of Yes at that range’s own strike, the strike’s Youden’s J and the range’s chance today, all from the published run.']) });
    N.sens = {
      title: 'The sensitivity rows',
      body: ['Each row changes one setting of the published design and gives each view’s chance of Yes at ' + P.sensitivity.strike.toFixed(1) + '% a year, the row’s own best threshold and its Youden’s J, from the published run. ' + tableLabel('sensitivity', 'Extended Data Table 2') + ' lists the same numbers. The cool and warm rows restrict NOAA’s ' + (m.base + 1) + ' value to that range, and the trend rows replace the IMF baseline with average growth from the stated year through 2019.',
             'A row with a setting on the controls draws the whole page at that setting when clicked. The page’s model reproduces those rows, and the repository’s test checks each one. Measuring growth from ' + (m.base + 1) + ' is not on the controls, so that row is drawn from the run only.'],
      code: [['the rows', 'bk_elnino/analysis.py'], ['the check against the page', 'tests/test_site_model.py']],
      n: n + ' simulated paths per row, the ' + ref + '.',
    };
    N.starts = {
      title: 'The trend baseline’s first year',
      body: ['For each first year from ' + P.trendStartScan.rows[0].start + ' to ' + P.trendStartScan.rows[P.trendStartScan.rows.length - 1].start + ', the trend baseline extends average growth from that year through ' + P.trendStartScan.window_end + ' and draws its spread from the same years, so every window holds the 2009 recession. Panel a gives the best threshold with its Youden’s J against the IMF baseline’s, and panel b each view’s chance of Yes at ' + P.trendStartScan.strike.toFixed(1) + '% a year.',
             'Choose the trend under Background growth and move its first year to draw the whole page that way. Windows that start in 2010 or later hold no recession and make the trend look far more certain than growth has been. The control runs from ' + P.trendStartScan.rows[0].start + ' to ' + P.trendStartScan.rows[P.trendStartScan.rows.length - 1].start + '.'],
      code: [['the scan', 'run_analysis.py'], ['the trend baseline', 'bk_elnino/baseline.py']],
      n: n + ' simulated paths per first year, the ' + ref + '.',
    };
    N.known27 = {
      title: 'A contract written once ' + (m.base + 1) + ' is known',
      body: ['Each point is the best threshold and its Youden’s J for a contract written after NOAA publishes its ' + (m.base + 1) + ' value, with ' + m.base + ' at today’s estimate (' + P.t27Grid.T2026.toFixed(2) + ' °C), from the published run’s threshold grid. The shading marks the cool and warm ranges of the conditional contracts.'],
      code: [['the grid', 'run_analysis.py']],
      n: n + ' simulated paths per point, the ' + ref + '.',
    };
    return N;
  }

  // the published run's Youden's J for an end year (2027 drawn from today's forecast)
  const scanJ = E => { const r = M.meta.published.scan.find(x => x.end_year === E); return r ? r.youden_j.toFixed(3) : '—'; };

  function variantsTable() {
    const rows = M.meta.published.variants;
    const t = h('table', { class: 'gdp-tbl' });
    t.appendChild(h('thead', {}, [h('tr', {}, ['Design (published run)', 'Cutoff, % a year', 'Paper right: No', 'No effect: Yes', 'J'].map(s => h('th', { text: s })))]));
    const tb = h('tbody');
    rows.forEach(v => tb.appendChild(h('tr', {}, [v.label, f2(v.threshold_growth_pp), pct(v.sensitivity), pct(1 - v.false_positive), v.youden_j.toFixed(2)].map(s => h('td', { text: s })))));
    t.appendChild(tb);
    return h('div', { class: 'gdp-tblwrap' }, [h('div', { class: 'rule', text: 'The same contract under other designs, from the published run (each at its own best cutoff; most can be set on the controls):' }), t]);
  }

  // ---------------------------------------------------------------- the Letter's figures drawn from the published run
  // These six show the published run's numbers (model.json's published block), so they stay at the
  // article's settings whatever the controls say; each figure's line under it says which panels move.
  const signed2 = v => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);
  const money = v => '$' + Math.round(v * 1000).toLocaleString('en-US');
  const letterIn = (svg, s, y) => svg.appendChild(txt(s, { x: 4, y, 'font-size': 15, 'font-weight': 800, fill: INK }));

  // Fig. 1a: world GDP per person and global temperature as levels, the record and each view, and since 6 Nov 2026
  // the global temperature shocks between them on the same years
  const LEVELS_H = () => (shockStrip() ? 704 : 560);
  function drawLevels(svg) {
    clear(svg);
    const top = shockStrip() ? 144 : 0;
    const m = M.meta, P = m.published, E = D.endYear, ft = P.futureTemperatures, t26 = m.temps.T26, t27 = m.temps.T27;
    const first = 2010, obs = [], fut = [];
    for (let t = first; t <= m.anchor; t++) obs.push(t);
    for (let t = m.base; t <= E; t++) fut.push(t);
    const temp = t => m.noaa.T[t - m.noaa.first], sim = fut.filter(t => t > m.base + 1);
    const tv = obs.map(temp).concat(sim.map(t => ft[String(t)].q90), sim.map(t => ft[String(t)].q10), [t27.mean + 1.645 * t27.sd]);
    const W = 960, L = 74, Rm = 210;
    const T = frame({ W, H: 172, L, R: Rm, T: 30, B: 6, x0: first - 0.5, x1: E + 0.5, y0: Math.floor(Math.min(...tv) * 5) / 5, y1: Math.ceil(Math.max(...tv) * 5) / 5 });
    svg.appendChild(txt('Global temperature (NOAA), °C above the 1901–2000 average', { x: L, y: 16, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, T, ticksFor(T.y0, T.y1, 3), v => minus(v.toFixed(1)));
    svg.appendChild(el('path', { d: area(sim.map(T.x), sim.map(t => T.y(ft[String(t)].q10)), sim.map(t => T.y(ft[String(t)].q90))), fill: 'var(--rule)', 'fill-opacity': 0.45 }));
    const med = [[m.anchor, temp(m.anchor)], [m.base, t26.mean], [m.base + 1, t27.mean]].concat(sim.map(t => [t, ft[String(t)].q50]));
    svg.appendChild(el('path', { d: path(med.map(([t, v]) => [T.x(t), T.y(v)])), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.6, 'stroke-dasharray': '4 3' }));
    svg.appendChild(el('path', { d: path(obs.map(t => [T.x(t), T.y(temp(t))])), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.9 }));
    obs.forEach(t => {
      const c = el('circle', { cx: T.x(t), cy: T.y(temp(t)), r: 3.1, fill: 'var(--muted)' });
      hover(c, () => tip.rows(String(t), [['NOAA', minus(temp(t).toFixed(2)) + ' °C']], 'annual global land and ocean temperature'));
      svg.appendChild(c);
    });
    [[m.base, t26, 'estimate'], [m.base + 1, t27, 'forecast']].forEach(([t, e, what]) => {
      svg.appendChild(el('line', { x1: T.x(t), x2: T.x(t), y1: T.y(e.mean - 1.645 * e.sd), y2: T.y(e.mean + 1.645 * e.sd), stroke: 'var(--muted)', 'stroke-width': 1.4 }));
      const c = el('circle', { cx: T.x(t), cy: T.y(e.mean), r: 4.2, fill: 'var(--panel)', stroke: 'var(--muted)', 'stroke-width': 1.8 });
      hover(c, () => tip.rows(t + ', ' + what, [['Mean', e.mean.toFixed(2) + ' °C'], ['90% range', (e.mean - 1.645 * e.sd).toFixed(2) + ' to ' + (e.mean + 1.645 * e.sd).toFixed(2) + ' °C']], 'until NOAA publishes the annual value'));
      svg.appendChild(c);
    });
    svg.appendChild(txt((m.base + 1) + ' forecast', { x: T.x(m.base + 1) - 9, y: T.y(t27.mean + 1.645 * t27.sd) + 4, 'text-anchor': 'end', class: 'ax' }));
    svg.appendChild(txt('simulated, 80% range', { x: T.x(E) + 10, y: T.y(ft[String(E)].q50) + 4, class: 'ax' }));
    const q = (v, t, i) => P.levels[String(t)][v][i] / 1000, lev = t => m.wdi.levels[String(t)] / 1000;
    const yv = obs.map(lev).concat(fut.map(t => q('h0', t, 2)), fut.map(t => q('h1', t, 0)));
    if (top) {
      const ev = Object.values((P.shocks || {}).events || {});
      const sv = obs.map(t => m.families.PWT.shocks[String(t)]).concat(ev.map(e => e.shock + 1.645 * e.sd), ev.map(e => e.shock - 1.645 * e.sd));
      const Sf = frame({ W, H: 322, L, R: Rm, T: 204, B: 6, x0: first - 0.5, x1: E + 0.5, y0: Math.floor((Math.min(...sv) - 0.04) * 10) / 10, y1: Math.ceil((Math.max(...sv) + 0.04) * 10) / 10 });
      svg.appendChild(txt('Global temperature shock (°C), NOAA measured with the paper’s filter', { x: L, y: 190, class: 'axl', 'font-weight': 700 }));
      yGrid(svg, Sf, [-0.2, 0, 0.2, 0.4].filter(v => v >= Sf.y0 && v <= Sf.y1), v => minus(v.toFixed(1)));
      const { sh, e27 } = shockBars(svg, Sf, first);
      svg.appendChild(txt('2024, ' + signed2(sh['2024']) + ' °C', { x: Sf.x(2024) - 9, y: Sf.y(sh['2024']) + 4, 'text-anchor': 'end', class: 'ax', fill: INK }));
      if (e27) svg.appendChild(txt((m.base + 1) + ' estimate, ' + signed2(e27.shock) + ' °C', { x: Sf.x(m.base + 1) + 12, y: Sf.y(e27.shock) + 4, class: 'ax', fill: INK }));
      svg.appendChild(txt('Open circles, the authors’ published shocks', { x: L + 6, y: Sf.T + 10, class: 'ax' }));
    }
    const G = frame({ W, H: 560 + top, L, R: Rm, T: 212 + top, B: 34, x0: first - 0.5, x1: E + 0.5, y0: Math.floor(Math.min(...yv)) - 0.5, y1: Math.ceil(Math.max(...yv)) + 0.5 });
    svg.appendChild(txt('World GDP per person (World Bank), thousand 2021 international dollars', { x: L, y: 198 + top, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, G, ticksFor(G.y0, G.y1, 5), v => String(v));
    xTicks(svg, G, [first, 2015, 2020, m.anchor, E].filter((v, i, a) => a.indexOf(v) === i), String);
    const fx = [m.anchor].concat(fut);
    const at = (v, i) => fx.map(t => (t === m.anchor ? lev(t) : q(v, t, i)));
    [['h0', NULL, 0.13], ['h1', PAPER, 0.15]].forEach(([v, c, op]) => svg.appendChild(el('path', { d: area(fx.map(G.x), at(v, 0).map(G.y), at(v, 2).map(G.y)), fill: c, 'fill-opacity': op })));
    [['h0', NULL], ['h1', PAPER]].forEach(([v, c]) => svg.appendChild(el('path', { d: path(fx.map((t, i) => [G.x(t), G.y(at(v, 1)[i])])), fill: 'none', stroke: c, 'stroke-width': 2.4 })));
    svg.appendChild(el('path', { d: path(obs.map(t => [G.x(t), G.y(lev(t))])), fill: 'none', stroke: INK, 'stroke-width': 2 }));
    obs.forEach(t => svg.appendChild(el('circle', { cx: G.x(t), cy: G.y(lev(t)), r: 3.2, fill: INK })));
    endLabels(svg, [{ y: G.y(q('h0', E, 1)), col: NULL, lines: ['No temperature effect', money(q('h0', E, 1)) + ' in ' + E] },
                    { y: G.y(q('h1', E, 1)), col: PAPER, lines: ['The paper’s estimate', money(q('h1', E, 1)) + ' in ' + E] }], G.x(E) + 12, 34);
    hoverBand(svg, G, obs.concat(fut), t => (t <= m.anchor
      ? { title: String(t), rows: [['World Bank', money(lev(t))]], foot: 'WDI NY.GDP.PCAP.PP.KD, world' }
      : { title: String(t), rows: [[sw(NULL) + 'No temperature effect', money(q('h0', t, 1)) + ' (' + money(q('h0', t, 0)) + '–' + money(q('h0', t, 2)) + ')'],
                                   [sw(PAPER) + 'The paper’s estimate', money(q('h1', t, 1)) + ' (' + money(q('h1', t, 0)) + '–' + money(q('h1', t, 2)) + ')']],
          foot: 'medians of ' + M.n.toLocaleString() + ' paths at the article’s settings, 80% ranges in brackets' }));
  }

  // the shocks from a first year: each year's bar (2023 on darker), the authors' published shocks as open circles
  // and the 2026-27 estimates with their 90% ranges
  function shockBars(svg, F, first) {
    const m = M.meta, sh = m.families.PWT.shocks, P = m.published.shocks || { authors: {}, events: {} };
    const yrs = Object.keys(sh).map(Number).filter(t => t >= first && t <= m.anchor);
    const bw = (F.x(1) - F.x(0)) * 0.74;
    yrs.forEach(t => {
      const v = sh[String(t)], a = P.authors[String(t)];
      const r = el('rect', { x: F.x(t) - bw / 2, y: Math.min(F.y(v), F.y(0)), width: bw, height: Math.abs(F.y(v) - F.y(0)), fill: t >= 2023 ? INK : 'var(--rule)', 'fill-opacity': t >= 2023 ? 0.72 : 1 });
      hover(r, () => tip.rows(String(t), [['Shock, NOAA', signed2(v) + ' °C'], a != null ? ['The authors’ published shock', signed2(a) + ' °C'] : null], 'the year’s temperature less what the two and three years before it predict'));
      svg.appendChild(r);
    });
    Object.keys(P.authors).filter(t => +t >= first).forEach(t => svg.appendChild(el('circle', { cx: F.x(+t), cy: F.y(P.authors[t]), r: 2.6, fill: 'var(--panel)', stroke: INK, 'stroke-width': 1, 'pointer-events': 'none' })));
    Object.keys(P.events).forEach(t => {
      const e = P.events[t], x = F.x(+t);
      const r = el('rect', { x: x - bw / 2, y: Math.min(F.y(e.shock), F.y(0)), width: bw, height: Math.max(1, Math.abs(F.y(e.shock) - F.y(0))), fill: 'var(--shade)', stroke: 'var(--muted)', 'stroke-width': 0.9 });
      hover(r, () => tip.rows(t + ', estimate', [['Shock', signed2(e.shock) + ' °C'], ['90% range', signed2(e.shock - 1.645 * e.sd) + ' to ' + signed2(e.shock + 1.645 * e.sd) + ' °C']], 'until NOAA publishes the annual value'));
      svg.appendChild(r);
      svg.appendChild(el('line', { x1: x, x2: x, y1: F.y(e.shock - 1.645 * e.sd), y2: F.y(e.shock + 1.645 * e.sd), stroke: 'var(--muted)', 'stroke-width': 1.3, 'pointer-events': 'none' }));
    });
    svg.appendChild(el('line', { x1: F.L, x2: F.W - F.R, y1: F.y(0), y2: F.y(0), stroke: 'var(--rule)' }));
    return { sh, e27: P.events[String(m.base + 1)] };
  }

  // the first Fig. 2a: each year's global temperature shock since 1953 (a letter.json from before 6 Nov 2026)
  function drawShocks(svg) {
    clear(svg);
    const m = M.meta, sh = m.families.PWT.shocks;
    const yrs = Object.keys(sh).map(Number).filter(t => t <= m.anchor);
    const F = frame({ W: 960, H: 330, L: 58, R: 150, T: 30, B: 34, x0: yrs[0] - 1, x1: m.base + 2, y0: -0.36, y1: 0.5 });
    svg.appendChild(txt('Global temperature shock (°C), NOAA measured with the paper’s filter', { x: F.L, y: 14, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, F, [-0.2, 0, 0.2, 0.4], v => minus(v.toFixed(1)));
    xTicks(svg, F, [1960, 1970, 1980, 1990, 2000, 2010, 2020], String);
    const { e27 } = shockBars(svg, F, yrs[0]);
    if (e27) svg.appendChild(txt((m.base + 1) + ' estimate, ' + signed2(e27.shock) + ' °C', { x: F.x(m.base + 1) + 6, y: F.y(e27.shock + 1.645 * e27.sd) - 6, 'text-anchor': 'end', class: 'lbl', fill: INK }));
    svg.appendChild(txt('2024, ' + signed2(sh['2024']) + ' °C', { x: F.x(2024) - 10, y: F.y(sh['2024']) + 4, 'text-anchor': 'end', class: 'lbl', fill: INK }));
    svg.appendChild(txt('Open circles, the authors’ published shocks', { x: F.L + 6, y: F.T + 6, class: 'ax' }));
  }

  // Fig. 3b: each range of NOAA's 2027 value, each view's chance of Yes at the range's own strike
  function drawBins(svg) {
    clear(svg);
    const bins = M.meta.published.bins;
    const F = frame({ W: 960, H: 250, L: 250, R: 210, T: 26, B: 40, x0: 0, x1: 100, y0: 0, y1: 1 });
    svg.appendChild(txt('Chance of Yes at the range’s strike (%)', { x: F.L, y: 14, class: 'axl', 'font-weight': 700 }));
    [0, 20, 40, 60, 80, 100].forEach(p => {
      svg.appendChild(el('line', { x1: F.x(p), x2: F.x(p), y1: F.T, y2: F.H - F.B, class: 'grid' }));
      svg.appendChild(txt(String(p), { x: F.x(p), y: F.H - F.B + 16, 'text-anchor': 'middle', class: 'ax' }));
    });
    bins.forEach((b, i) => {
      const yy = F.T + (i + 0.5) * (F.H - F.T - F.B) / bins.length;
      const p0 = 100 * b.p_yes_no_effect_at_strike, p1 = 100 * b.p_yes_paper_at_strike;
      svg.appendChild(txt(b.bin[0].toUpperCase() + b.bin.slice(1) + ', ' + condition(b.bin), { x: F.L - 14, y: yy + 4, 'text-anchor': 'end', class: 'ax', fill: INK }));
      svg.appendChild(el('line', { x1: F.x(p1), x2: F.x(p0), y1: yy, y2: yy, stroke: 'var(--rule)', 'stroke-width': 3 }));
      [[p1, PAPER, -1], [p0, NULL, 1]].forEach(([p, c, side]) => {
        svg.appendChild(el('circle', { cx: F.x(p), cy: yy, r: 6, fill: c, stroke: 'var(--panel)', 'stroke-width': 1.4 }));
        svg.appendChild(txt(Math.round(p) + '%', { x: F.x(p) + side * 12, y: yy + 4, 'text-anchor': side < 0 ? 'end' : 'start', class: 'lbl', fill: c, 'font-weight': 700 }));
      });
      svg.appendChild(txt('strike ' + b.strike.toFixed(1) + '%, J ' + b.youden_j.toFixed(2) + ',', { x: F.W - F.R + 14, y: yy - 3, class: 'ax' }));
      svg.appendChild(txt(pct(b.prob_now) + ' likely today', { x: F.W - F.R + 14, y: yy + 11, class: 'ax' }));
    });
  }

  // Fig. 4: the sensitivity rows; a row with settings of its own draws the whole page at them when clicked
  function applySettings(set) {
    S = Object.assign({}, D, set);
    schedule(0);
    syncControls();
    const c = $('#contract');
    if (c) c.scrollIntoView({ behavior: 'smooth' });
  }
  function sensRows() {
    const out = [];
    let g = null;
    M.meta.published.sensitivity.rows.forEach(r => {
      if (r.group !== g && r.group !== 'Published') out.push({ head: r.group });
      g = r.group;
      out.push(r);
    });
    return out;
  }
  const sensHeight = () => 52 + 22 * sensRows().length + 44;
  function drawSens(svg) {
    clear(svg);
    const P = M.meta.published.sensitivity, rows = sensRows(), RH = 22, top = 52, H = sensHeight();
    svg.setAttribute('viewBox', '0 0 960 ' + H);
    const ax0 = 400, ax1 = 720, bx0 = 760, bx1 = 940;
    const thr = P.rows.map(r => r.threshold_growth_pp);
    const tlo = Math.floor(Math.min(...thr) * 10) / 10 - 0.1, thi = Math.ceil(Math.max(...thr) * 10) / 10 + 0.1;
    const xa = p => ax0 + (ax1 - ax0) * p, xb = v => bx0 + (bx1 - bx0) * (v - tlo) / (thi - tlo);
    const yb = H - 44;
    letterIn(svg, 'a', 16); svg.appendChild(txt('b', { x: bx0 - 18, y: 16, 'font-size': 15, 'font-weight': 800, fill: INK }));
    svg.appendChild(txt('Paper’s estimate', { x: ax0 + 4, y: top - 10, class: 'lbl', fill: PAPER, 'font-weight': 700 }));
    svg.appendChild(txt('No temperature effect', { x: ax1, y: top - 10, 'text-anchor': 'end', class: 'lbl', fill: NULL, 'font-weight': 700 }));
    [0, 0.2, 0.4, 0.6, 0.8, 1].forEach(p => {
      svg.appendChild(el('line', { x1: xa(p), x2: xa(p), y1: top - 4, y2: yb, class: 'grid' }));
      svg.appendChild(txt(String(Math.round(100 * p)), { x: xa(p), y: yb + 16, 'text-anchor': 'middle', class: 'ax' }));
    });
    ticksFor(tlo, thi, 3).forEach(v => {
      svg.appendChild(el('line', { x1: xb(v), x2: xb(v), y1: top - 4, y2: yb, class: 'grid' }));
      svg.appendChild(txt(v.toFixed(1), { x: xb(v), y: yb + 16, 'text-anchor': 'middle', class: 'ax' }));
    });
    svg.appendChild(txt('Chance of Yes at ' + P.strike.toFixed(1) + '% a year (%)', { x: (ax0 + ax1) / 2, y: yb + 34, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(txt('Best threshold (% a year)', { x: (bx0 + bx1) / 2, y: yb + 34, 'text-anchor': 'middle', class: 'axl' }));
    rows.forEach((r, i) => {
      const yy = top + (i + 0.5) * RH;
      if (r.head) { svg.appendChild(txt(r.head, { x: ax0 - 12, y: yy + 4, 'text-anchor': 'end', class: 'axl', 'font-weight': 700 })); return; }
      if (r.group === 'Published') svg.appendChild(el('rect', { x: 0, y: yy - RH / 2, width: 960, height: RH, fill: 'var(--shade)' }));
      svg.appendChild(txt(r.label, { x: ax0 - 12, y: yy + 4, 'text-anchor': 'end', class: 'ax', fill: INK }));
      const p0 = r.p_yes_no_effect_at_strike, p1 = r.p_yes_paper_at_strike;
      svg.appendChild(el('line', { x1: xa(p1), x2: xa(p0), y1: yy, y2: yy, stroke: 'var(--rule)', 'stroke-width': 2.6 }));
      [[p1, PAPER], [p0, NULL]].forEach(([p, c]) => svg.appendChild(el('circle', { cx: xa(p), cy: yy, r: 4.6, fill: c, stroke: 'var(--panel)', 'stroke-width': 1.2 })));
      svg.appendChild(el('rect', { x: xb(r.threshold_growth_pp) - 4.5, y: yy - 4.5, width: 9, height: 9, fill: INK, transform: 'rotate(45 ' + xb(r.threshold_growth_pp) + ' ' + yy + ')' }));
      svg.appendChild(txt('J ' + r.youden_j.toFixed(2), { x: xb(r.threshold_growth_pp) + 10, y: yy + 4, class: 'ax' }));
      const hit = el('rect', { x: 0, y: yy - RH / 2, width: 960, height: RH, fill: 'transparent', style: r.settings ? 'cursor:pointer' : null });
      hover(hit, () => tip.rows(r.label, [[sw(NULL) + 'Chance of Yes, no temperature effect', pct1(p0)], [sw(PAPER) + 'Chance of Yes, the paper is right', pct1(p1)],
        ['Best threshold', f2(r.threshold_growth_pp) + '% a year'], ['Youden’s J', r.youden_j.toFixed(2)]],
        r.settings ? 'published run; click to draw the page at this row’s settings' : 'published run; the controls do not offer this row'));
      if (r.settings) hit.addEventListener('click', () => { tip.hide(); applySettings(r.settings); });
      svg.appendChild(hit);
    });
  }

  // Extended Data Fig. 5: the trend baseline by the first year of its average
  function drawStartYears(svg) {
    clear(svg);
    const P = M.meta.published, rows = P.trendStartScan.rows, xs = rows.map(r => r.start);
    const imf = P.contract.threshold_growth_equivalent_pp;
    const opt = { W: 960, L: 70, R: 210, x0: xs[0] - 0.5, x1: xs[xs.length - 1] + 0.5 };
    const A1 = frame(Object.assign({ H: 250, T: 30, B: 8, y0: 0.9, y1: 2.0 }, opt));
    const B1 = frame(Object.assign({ H: 520, T: 290, B: 40, y0: 0, y1: 100 }, opt));
    letterIn(svg, 'a', 16); letterIn(svg, 'b', 276);
    svg.appendChild(txt('Best threshold (% a year)', { x: opt.L, y: 16, class: 'axl', 'font-weight': 700 }));
    svg.appendChild(txt('Chance of Yes at ' + P.trendStartScan.strike.toFixed(1) + '% a year (%)', { x: opt.L, y: 276, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, A1, [1.0, 1.2, 1.4, 1.6, 1.8, 2.0], v => v.toFixed(1));
    yGrid(svg, B1, [0, 20, 40, 60, 80, 100], String);
    xTicks(svg, B1, xs.filter(x => x % 2 === 1), String);
    svg.appendChild(txt('First year of the average (the window ends in ' + P.trendStartScan.window_end + ')', { x: (opt.L + 960 - opt.R) / 2, y: 514, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(el('line', { x1: opt.L, x2: 960 - opt.R, y1: A1.y(imf), y2: A1.y(imf), stroke: NULL, 'stroke-width': 1.6, 'stroke-dasharray': '6 4' }));
    svg.appendChild(txt('IMF baseline, ' + imf.toFixed(2) + '%', { x: A1.x(xs[0]), y: A1.y(imf) - 7, class: 'lbl', fill: NULL }));
    svg.appendChild(el('path', { d: path(rows.map(r => [A1.x(r.start), A1.y(r.threshold_growth_pp)])), fill: 'none', stroke: INK, 'stroke-width': 2.2 }));
    rows.forEach((r, i) => {
      svg.appendChild(el('circle', { cx: A1.x(r.start), cy: A1.y(r.threshold_growth_pp), r: 3.8, fill: INK }));
      if (i % 2 === 0) svg.appendChild(txt('J ' + r.youden_j.toFixed(2), { x: A1.x(r.start), y: A1.y(r.threshold_growth_pp) - 10, 'text-anchor': 'middle', class: 'ax' }));
    });
    [['p_yes_no_effect_at_strike', NULL, 'No temperature effect'], ['p_yes_paper_at_strike', PAPER, 'The paper’s estimate']].forEach(([k, c, name]) => {
      svg.appendChild(el('path', { d: path(rows.map(r => [B1.x(r.start), B1.y(100 * r[k])])), fill: 'none', stroke: c, 'stroke-width': 2.2 }));
      rows.forEach(r => svg.appendChild(el('circle', { cx: B1.x(r.start), cy: B1.y(100 * r[k]), r: 3.6, fill: c })));
      svg.appendChild(txt(name, { x: B1.x(xs[xs.length - 1]) + 14, y: B1.y(100 * rows[rows.length - 1][k]) + 4, class: 'lbl', fill: c, 'font-weight': 700 }));
    });
    if (S.baseline === 'frozen' && S.trendStart != null) {
      svg.appendChild(el('line', { x1: A1.x(S.trendStart), x2: A1.x(S.trendStart), y1: A1.T, y2: B1.H - B1.B, stroke: 'var(--warn)', 'stroke-dasharray': '3 3' }));
    }
    hoverBand(svg, B1, xs, x => {
      const r = rows[xs.indexOf(x)];
      return { title: 'Average from ' + x + ' through ' + P.trendStartScan.window_end, rows: [['Average growth', f2(r.drift_pp) + '% a year'], ['Best threshold', f2(r.threshold_growth_pp) + '% a year'],
        [sw(NULL) + 'Chance of Yes, no effect', pct1(r.p_yes_no_effect_at_strike)], [sw(PAPER) + 'Chance of Yes, paper right', pct1(r.p_yes_paper_at_strike)], ['Youden’s J', r.youden_j.toFixed(2)]],
        foot: 'published run, 2027 drawn from today’s forecast' };
    });
  }

  // Extended Data Fig. 8: the best threshold and its separation if NOAA's 2027 value were known
  function drawKnown27(svg) {
    clear(svg);
    const m = M.meta, G = m.published.t27Grid, rows = G.rows, xs = rows.map(r => r.T2027);
    const thr = rows.map(r => r.threshold_growth_pp), J = rows.map(r => r.youden_j);
    const opt = { W: 960, L: 70, R: 60, x0: xs[0] - 0.02, x1: xs[xs.length - 1] + 0.02 };
    const A1 = frame(Object.assign({ H: 250, T: 30, B: 8, y0: Math.floor(Math.min(...thr) * 20) / 20, y1: Math.ceil(Math.max(...thr) * 20) / 20 }, opt));
    const B1 = frame(Object.assign({ H: 500, T: 290, B: 40, y0: Math.floor(Math.min(...J) * 20) / 20, y1: Math.ceil(Math.max(...J) * 20) / 20 }, opt));
    const cen = m.temps.bins.find(b => b.lower != null && b.upper != null);
    [A1, B1].forEach(F => {
      if (cen) {
        svg.appendChild(el('rect', { x: F.L, y: F.T, width: F.x(cen.lower) - F.L, height: F.H - F.T - F.B, fill: 'var(--cool)', 'fill-opacity': 0.12 }));
        svg.appendChild(el('rect', { x: F.x(cen.upper), y: F.T, width: F.W - F.R - F.x(cen.upper), height: F.H - F.T - F.B, fill: 'var(--warm)', 'fill-opacity': 0.12 }));
      }
    });
    letterIn(svg, 'a', 16); letterIn(svg, 'b', 276);
    svg.appendChild(txt('Best threshold (% a year)', { x: opt.L, y: 16, class: 'axl', 'font-weight': 700 }));
    svg.appendChild(txt('Youden’s J', { x: opt.L, y: 276, class: 'axl', 'font-weight': 700 }));
    yGrid(svg, A1, ticksFor(A1.y0, A1.y1, 4), v => v.toFixed(1));
    yGrid(svg, B1, ticksFor(B1.y0, B1.y1, 4), v => v.toFixed(1));
    xTicks(svg, B1, ticksFor(xs[0], xs[xs.length - 1], 6), v => v.toFixed(1));
    svg.appendChild(txt('NOAA’s ' + (m.base + 1) + ' value (°C above 1901–2000), with ' + m.base + ' at ' + G.T2026.toFixed(2) + ' °C', { x: (opt.L + 960 - opt.R) / 2, y: 494, 'text-anchor': 'middle', class: 'axl' }));
    if (cen) {
      svg.appendChild(txt('Cool range', { x: A1.L + 8, y: A1.H - A1.B - 10, class: 'ax' }));
      svg.appendChild(txt('Warm range', { x: A1.W - A1.R - 8, y: A1.T + 14, 'text-anchor': 'end', class: 'ax' }));
    }
    [[A1, thr], [B1, J]].forEach(([F, v]) => {
      svg.appendChild(el('path', { d: path(xs.map((x, i) => [F.x(x), F.y(v[i])])), fill: 'none', stroke: INK, 'stroke-width': 2.2 }));
      xs.forEach((x, i) => svg.appendChild(el('circle', { cx: F.x(x), cy: F.y(v[i]), r: 3.4, fill: INK })));
    });
    hoverBand(svg, B1, xs, x => {
      const r = rows[xs.indexOf(x)];
      return { title: 'NOAA’s ' + (m.base + 1) + ' value ' + x.toFixed(2) + ' °C', rows: [['Best threshold', f2(r.threshold_growth_pp) + '% a year'], ['Youden’s J', r.youden_j.toFixed(3)]], foot: 'published run’s threshold grid' };
    });
  }

  // ---------------------------------------------------------------- figure registry
  const FIG = { 1: [drawTemp, 420], 2: [drawIrf, 400], 3: [drawGrowth, 420], 5: [drawAfter, 400], 6: [drawEndYear, 400], 7: [drawDist, 400], 8: [drawTimeline, 300] };
  // the Letter's figures by what they draw (letter.json names it for each figure; a letter.json from before 6 Nov 2026
  // goes by the figure's id): each panel's drawing, its height and whether it follows the controls
  const LDRAW = {
    response: [[drawIrf, 400, true]],
    shocksResponse: [[drawShocks, 330, false], [drawIrf, 400, true]],
    distributions: [[drawDist, 400, true], [drawBins, 250, false]],
    sensitivity: [[drawSens, 0, false]],
    timeline: [[drawTimeline, 300, true]],
    temperature: [[drawTemp, 420, true]],
    growth: [[drawGrowth, 420, true]],
    trendStarts: [[drawStartYears, 520, false]],
    afterElNino: [[drawAfter, 400, true]],
    endYear: [[drawEndYear, 400, true]],
    known2027: [[drawKnown27, 500, false]],
  };
  const LKEY = { fig1: 'main', fig2: 'shocksResponse', fig3: 'distributions', fig4: 'sensitivity', ed2: 'timeline', ed3: 'temperature',
                 ed4: 'growth', ed5: 'trendStarts', ed6: 'afterElNino', ed7: 'endYear', ed8: 'known2027' };
  // and the method note each one opens
  const LNOTE = { main: 'main', response: 2, shocksResponse: 2, distributions: 'fig3', sensitivity: 'sens', timeline: 8, temperature: 1,
                  growth: 3, trendStarts: 'starts', afterElNino: 5, endYear: 6, known2027: 'known27' };
  function drawOf(id) {
    const f = A.figures[id];
    return f ? ('draw' in f ? f.draw : LKEY[id]) : undefined;
  }
  const figWith = key => Object.keys(A.figures || {}).find(id => drawOf(id) === key);
  const labelOf = (key, old) => (figWith(key) ? A.figures[figWith(key)].label : old);
  const tableLabel = (role, old) => { const id = Object.keys(A.tables || {}).find(k => A.tables[k].role === role); return id ? A.tables[id].label : old; };
  // the shocks are Fig. 1a's middle strip, unless the letter.json still draws them in a figure of their own
  const shockStrip = () => A.schema === 2 && !figWith('shocksResponse');

  function figState(f) {
    const live = f.panels.map(p => p.live), letters = (want) => f.panels.map((p, i) => (p.live === want ? 'ab'[i] : null)).filter(Boolean).join(' and ');
    const mine = !atArticle(S) && live.some(Boolean);
    f.state.className = 'gdp-figstate' + (mine ? ' mine' : '');
    f.state.innerHTML = '';
    let s;
    if (!live.some(Boolean)) s = 'Drawn from the published run, at the article’s settings.';
    else if (live.every(Boolean)) s = atArticle(S) ? 'Drawn live at the article’s settings.' : 'Drawn at your settings (' + changed(S).length + ' changed).';
    else s = 'Panel ' + letters(false) + ' is drawn from the published run. Panel ' + letters(true) + (atArticle(S) ? ' is drawn live at the article’s settings.' : ' is drawn at your settings (' + changed(S).length + ' changed).');
    f.state.appendChild(document.createTextNode(s));
    if (mine) f.state.appendChild(h('button', { type: 'button', text: 'Reset', onclick: reset }));
  }

  function drawAll() {
    drawReadout();
    drawMain($('#gdpMain'));
    const lv = $('#gdpLevels');
    if (lv) { try { drawLevels(lv); } catch (err) { console.error('figure 1a', err); } }
    const N = notes();
    const main = N.main;
    methodNote($('#gdpMainMethod'), Object.assign({}, main, { body: main.body.concat([{ table: main.table }]) }));
    figs.forEach(f => {
      f.panels.forEach(p => { try { p.draw(p.svg); } catch (err) { console.error('figure ' + f.key, err); } });
      figState(f);
      if (N[f.key]) methodNote(f.note, N[f.key]);
    });
    floatBar();
  }

  // ---------------------------------------------------------------- the controls
  const CTL = [];
  function control(group, spec) { CTL.push(Object.assign({ group }, spec)); }
  function buildControls() {
    const m = M.meta;
    const strikeOf = name => (M.meta.published.bins.find(b => b.bin === name) || {}).strike;
    control('The paper’s estimate', { key: 'irf', type: 'select', label: 'Which estimate', options: m.irfs.map(e => [e.key, e.label]) });
    control('The paper’s estimate', { key: 'share', type: 'range', label: 'Share of the paper’s effect that is real', min: 0, max: 1.5, step: 0.05, fmt: pct });
    control('The paper’s estimate', { key: 'uncertainty', type: 'select', label: 'The paper’s statistical uncertainty',
      options: [['boot', 'As estimated (bootstrap)'], ['wide', 'Widened by half'], ['nw', 'Newey–West widths (time series only)'], ['point', 'None: the central estimate only']] });
    control('Background growth', { key: 'baseline', type: 'select', label: 'Growth with no temperature effect',
      options: [['imf', 'IMF October 2026 projection'], ['frozen', m.trend ? 'Average growth to ' + m.trend.windowEnd + ', extended' : '2000–2019 average growth, extended']] });
    if (m.trend) {
      const st = Object.keys(m.trend.starts).map(Number);
      control('Background growth', { key: 'trendStart', type: 'range', label: 'First year of the average', min: st[0], max: st[st.length - 1], step: 1, fmt: String, show: s => s.baseline === 'frozen' });
    }
    control('Background growth', { key: 'missShare', type: 'range', label: 'Share of the IMF’s average past miss kept', min: 0, max: 1.5, step: 0.05, fmt: pct, off: s => s.baseline === 'frozen' });
    control('Background growth', { key: 'spread', type: 'range', label: 'Spread of background growth', min: 0.5, max: 1.5, step: 0.05, fmt: pct });
    control('Background growth', { key: 'netting', type: 'check', label: 'Net the paper’s part out of the IMF’s past misses', off: s => s.baseline === 'frozen' });
    control('Temperatures', { key: 't27', type: 'select', label: 'NOAA’s 2027 value', options: [
      ['bin:central', 'Central range, ' + condition('central') + ' (the ' + (strikeOf('central') || 1.1).toFixed(1) + '% contract)'],
      ['bin:cool', 'Cool range, ' + condition('cool')], ['bin:warm', 'Warm range, ' + condition('warm')],
      ['forecast', 'Today’s forecast, ' + m.temps.T27.mean.toFixed(2) + ' ± ' + m.temps.T27.sd.toFixed(2) + ' °C'], ['value', 'A value I set']] });
    control('Temperatures', { key: 't27Value', type: 'range', label: 'NOAA’s 2027 value', min: 1.3, max: 1.75, step: 0.01, fmt: v => v.toFixed(2) + ' °C', show: s => s.t27 === 'value', mark: m.temps.T27.mean });
    control('Temperatures', { key: 't26', type: 'select', label: 'NOAA’s 2026 value', options: [['forecast', 'Today’s estimate, ' + m.temps.T26.mean.toFixed(2) + ' ± ' + m.temps.T26.sd.toFixed(2) + ' °C'], ['value', 'A value I set']] });
    control('Temperatures', { key: 't26Value', type: 'range', label: 'NOAA’s 2026 value', min: 1.1, max: 1.35, step: 0.01, fmt: v => v.toFixed(2) + ' °C', show: s => s.t26 === 'value', mark: m.temps.T26.mean });
    control('Temperatures', { key: 'future', type: 'select', label: 'Years after 2027', options: [['simulate', 'Simulated from past strong El Niños'], ['none', 'No further shocks']] });
    control('Temperatures', { key: 'scope', type: 'select', label: 'Shocks counted', options: [['full', 'Every shock since 2015'], ['recent', '2023–27 only'], ['incremental', '2026–27 only']] });
    control('The contract', { key: 'endYear', type: 'range', label: 'Last year of the growth period', min: m.endYears[0], max: m.endYears[m.endYears.length - 1], step: 1, fmt: String });
    control('The contract', { key: 'strike', type: 'range', label: 'Strike: average growth of more than', min: 0, max: 2.5, step: 0.1, fmt: v => v.toFixed(1) + '% a year' });

    const panel = $('#gdpPanel');
    panel.innerHTML = '';
    const groups = {};
    CTL.forEach(c => {
      if (!groups[c.group]) { groups[c.group] = h('div', { class: 'gdp-group' }, [h('div', { class: 'gdp-gh', text: c.group })]); panel.appendChild(groups[c.group]); }
      const box = h('div', { class: 'gdp-ctl' });
      c.box = box;
      if (c.type === 'select') {
        const sel = h('select', { 'aria-label': c.label });
        c.options.forEach(([v, l]) => sel.appendChild(h('option', { value: v, text: l })));
        sel.onchange = () => set(c.key, sel.value, 0);
        c.input = sel;
        box.appendChild(h('div', { class: 'cl' }, [h('span', { text: c.label })]));
        box.appendChild(sel);
      } else if (c.type === 'range') {
        const inp = h('input', { type: 'range', min: c.min, max: c.max, step: c.step, 'aria-label': c.label });
        const val = h('b');
        inp.oninput = () => { const v = parseFloat(inp.value); val.textContent = c.fmt(v); set(c.key, v, 90); };
        c.input = inp; c.val = val;
        const mk = c.mark != null ? c.mark : D[c.key];
        const wrap = h('div', { class: 'gdp-range' }, [inp]);
        if (mk != null) wrap.appendChild(h('span', { class: 'mk', title: 'the article’s setting', style: 'left:' + (100 * (mk - c.min) / (c.max - c.min)) + '%' }));
        box.appendChild(h('div', { class: 'cl' }, [h('span', { text: c.label }), val]));
        box.appendChild(wrap);
      } else if (c.type === 'check') {
        const inp = h('input', { type: 'checkbox' });
        inp.onchange = () => set(c.key, inp.checked, 0);
        c.input = inp;
        box.appendChild(h('label', { class: 'ck' }, [inp, h('span', { text: c.label })]));
      }
      groups[c.group].appendChild(box);
    });
    const bar = $('#gdpPanelBar');
    bar.innerHTML = '';
    bar.appendChild(h('button', { type: 'button', text: 'Reset to the article’s settings', onclick: reset }));
    bar.appendChild(h('button', { type: 'button', text: 'Use the best cutoff', title: 'set the strike to the cutoff that best separates the views at these settings, to 0.1', onclick: () => { set('strike', Math.round(R.youden(S.endYear).growth * 10) / 10, 0); } }));
    bar.appendChild(h('button', { type: 'button', text: 'Copy a link to these settings', onclick: () => { try { navigator.clipboard.writeText(location.href); status('Link copied.'); } catch (e) { status('Copy the address bar to share these settings.'); } } }));
    bar.appendChild(h('span', { id: 'gdpRunStatus' }));
  }
  function syncControls() {
    CTL.forEach(c => {
      const v = S[c.key];
      if (c.type === 'select') c.input.value = v;
      else if (c.type === 'range') { const x = v == null ? (c.mark != null ? c.mark : c.min) : v; if (document.activeElement !== c.input) c.input.value = x; c.val.textContent = c.fmt(x); }
      else if (c.type === 'check') c.input.checked = !!v;
      c.box.hidden = c.show ? !c.show(S) : false;
      c.box.classList.toggle('off', !!(c.off && c.off(S)));
      if (c.input) c.input.disabled = !!(c.off && c.off(S));
    });
    const nw = CTL.find(c => c.key === 'uncertainty');
    if (nw) [...nw.input.options].forEach(o => { if (o.value === 'nw') o.disabled = S.irf !== 'PWT_ts'; });
  }
  function status(s, warn) { const e = $('#gdpRunStatus'); if (e) { e.textContent = s; e.className = warn ? 'gdp-busy' : ''; } }

  function floatBar() {
    const f = $('#gdpFloat');
    const panel = $('#gdpPanel').getBoundingClientRect();
    const show = !atArticle(S) && (panel.bottom < 0 || panel.top > window.innerHeight);
    f.hidden = !show;
    if (!show) return;
    f.innerHTML = '';
    f.appendChild(h('span', { class: 'gdp-mine', text: 'Figures drawn at your settings (' + changed(S).length + ' changed)' }));
    f.appendChild(h('button', { type: 'button', text: 'Change', onclick: () => $('#assumptions').scrollIntoView({ behavior: 'smooth' }) }));
    f.appendChild(h('button', { type: 'button', text: 'Reset', onclick: reset }));
  }

  // ---------------------------------------------------------------- the article
  function runsTo(node, runs, caption) {
    runs.forEach(r => {
      let t = r.t;
      let n;
      if (r.href) n = h('a', { href: r.href });
      else n = document.createDocumentFragment();
      // "Figure N" in the text points at the figure
      // the live figure is drawn here, not plotted in Python: the caption keeps everything else
      if (caption) t = t.replace(/, plotted in Python\./g, '.').replace(/\s*Plotted in Python\./g, '');
      if (!r.href && !caption) {
        String(t).split(/(Figure \d+)/).forEach(part => {
          const mm = /^Figure (\d+)$/.exec(part);
          if (mm) n.appendChild(h('a', { href: '#fig-' + mm[1], class: 'figref', text: part }));
          else if (part) n.appendChild(document.createTextNode(part));
        });
      } else n.textContent = t;
      let w = n;
      if (r.i) w = h('i', {}, [w]);
      if (r.b) w = h('b', {}, [w]);
      node.appendChild(w);
    });
    return node;
  }
  function renderArticle() {
    const art = $('#gdpArticle');
    art.innerHTML = '';
    let list = null;
    A.blocks.forEach(b => {
      if (b.type !== 'p' || !b.list) list = null;
      if (b.type === 'title') { $('#gdpTitle').textContent = b.text; return; }
      if (b.type === 'subtitle') { $('#gdpSub').textContent = b.text; return; }
      if (b.type === 'h1' || b.type === 'h2' || b.type === 'h3') {
        art.appendChild(h({ h1: 'h2', h2: 'h3', h3: 'h4' }[b.type], { id: b.id, text: b.text.replace(/:$/, '') }));
        return;
      }
      if (b.type === 'table') {
        const t = h('table', { class: 'gdp-tbl' });
        b.rows.forEach((row, i) => {
          const tr = h('tr');
          row.forEach(cell => tr.appendChild(runsTo(h(i === 0 ? 'th' : 'td'), cell)));
          t.appendChild(i === 0 ? h('thead', {}, [tr]) : tr);
        });
        art.appendChild(h('div', { class: 'gdp-tblwrap' }, [t]));
        return;
      }
      if (b.type === 'figure') {
        const fig = h('figure', { class: 'gdp-fig', id: 'fig-' + b.n });
        if (b.n === 4) {
          fig.appendChild(h('div', { class: 'gdp-pointer', html: 'Figure 4 is the contract’s figure <a href="#contract">at the top of this page</a>, with the market’s ladder beside it.' }));
          fig.appendChild(runsTo(h('figcaption'), b.caption, true));
          art.appendChild(fig);
          return;
        }
        if (!FIG[b.n]) { fig.appendChild(runsTo(h('figcaption'), b.caption, true)); art.appendChild(fig); return; }
        const svg = el('svg', { viewBox: '0 0 960 ' + FIG[b.n][1], role: 'img', 'aria-label': 'Figure ' + b.n });
        fig.appendChild(h('div', { class: 'card' }, [svg]));
        fig.appendChild(runsTo(h('figcaption'), b.caption, true));
        const state = h('div', { class: 'gdp-figstate' }), note = h('div');
        fig.appendChild(state); fig.appendChild(note);
        figs.push({ key: b.n, panels: [{ svg, draw: FIG[b.n][0], live: true }], state, note });
        art.appendChild(fig);
        return;
      }
      // paragraphs
      const allBI = b.runs.every(r => !r.t.trim() || (r.b && r.i));
      if (b.list) {
        if (!list || list.tagName.toLowerCase() !== b.list) { list = h(b.list); art.appendChild(list); }
        list.appendChild(runsTo(h('li'), b.runs));
        return;
      }
      const p = h('p', { class: b.indent ? (allBI ? 'callout' : 'ind') : '' });
      art.appendChild(runsTo(p, b.runs));
    });
  }

  // ---------------------------------------------------------------- the Letter (letter.json, schema 2)
  // References to figures, tables and sections link to them; the main figures sit where the text first cites them.
  const XREF = /(Extended Data Fig\.|Extended Data Table|Supplementary Table|Supplementary Section|Figs?\.|Table)\s(\d+)/;
  const XKIND = { 'Fig.': 'fig', 'Figs.': 'fig', 'Extended Data Fig.': 'ed', 'Extended Data Table': 'edt', 'Supplementary Table': 'st', 'Supplementary Section': 'ss', Table: 'tab' };
  // a legend's own label ("Fig. 2 |") is not a link to the figure it sits under
  function textWithLinks(node, t, self) {
    const re = new RegExp('(https?://[^\\s]+[^\\s.,;:)])|' + XREF.source, 'g');
    let last = 0, mm;
    while ((mm = re.exec(t))) {
      if (mm.index > last) node.appendChild(document.createTextNode(t.slice(last, mm.index)));
      const id = mm[1] ? null : XKIND[mm[2]] + mm[3];
      if (mm[1]) node.appendChild(h('a', { href: mm[1], text: mm[1] }));
      else if (id === self) node.appendChild(document.createTextNode(mm[0]));
      else node.appendChild(h('a', { href: '#' + id, class: 'figref', text: mm[0] }));
      last = re.lastIndex;
    }
    if (last < t.length) node.appendChild(document.createTextNode(t.slice(last)));
  }
  function runs2(node, runs, self) {
    (runs || []).forEach(r => {
      if (r.math != null) { node.appendChild(tex(h('span', { class: 'mi' }), r.math, false)); return; }
      let w;
      if (r.cite) {
        w = h('sup', { class: 'cite' });
        String(r.t).split(/(\d+)/).forEach(part => {
          if (/^\d+$/.test(part)) w.appendChild(h('a', { href: '#ref-' + part, text: part }));
          else if (part) w.appendChild(document.createTextNode(part));
        });
      } else if (r.href) w = h('a', { href: r.href, text: r.t });
      else { w = document.createDocumentFragment(); textWithLinks(w, r.t, self); }
      if (r.sup) w = h('sup', {}, [w]);
      if (r.sub) w = h('sub', {}, [w]);
      if (r.i) w = h('i', {}, [w]);
      if (r.b) w = h('b', {}, [w]);
      node.appendChild(w);
    });
    return node;
  }
  // the main figures a paragraph cites, in order ("Fig. 4", "Figs. 2–4", "Fig. 3a")
  function firstCited(runs) {
    const out = [], re = /Figs?\.\s(\d+)[a-z]?(?:\s?[–-]\s?(\d+))?/g;
    const t = (runs || []).map(r => r.t || '').join('');
    let mm;
    while ((mm = re.exec(t))) for (let k = +mm[1]; k <= +(mm[2] || mm[1]); k++) out.push('fig' + k);
    return out;
  }
  function figureEl(id) {
    const f = A.figures[id], key = drawOf(id), spec = LDRAW[key];
    const fig = h('figure', { class: 'gdp-fig', id });
    if (spec) {
      const card = h('div', { class: 'card' });
      const panels = spec.map(([draw, H, live], i) => {
        if (spec.length > 1) card.appendChild(h('div', { class: 'gdp-pl', text: 'ab'[i] }));
        const svg = el('svg', { viewBox: '0 0 960 ' + (H || sensHeight()), role: 'img', 'aria-label': f.label + (spec.length > 1 ? 'ab'[i] : '') });
        card.appendChild(svg);
        return { svg, draw, live };
      });
      fig.appendChild(card);
      fig.appendChild(runs2(h('figcaption'), f.legend, id));
      const state = h('div', { class: 'gdp-figstate' }), note = h('div');
      fig.appendChild(state); fig.appendChild(note);
      figs.push({ key: LNOTE[key], panels, state, note });
    } else {
      if (f.image) fig.appendChild(h('div', { class: 'card gdp-img' }, [h('img', { src: BASE + f.image.file, alt: f.label, loading: 'lazy', width: f.image.w, height: f.image.h })]));
      fig.appendChild(runs2(h('figcaption'), f.legend, id));
    }
    return fig;
  }
  function tableRows(rows) {
    const t = h('table', { class: 'gdp-tbl' });
    rows.forEach((row, i) => {
      const tr = h('tr');
      row.forEach(cell => tr.appendChild(runs2(h(i === 0 ? 'th' : 'td'), cell)));
      t.appendChild(i === 0 ? h('thead', {}, [tr]) : tr);
    });
    return h('div', { class: 'gdp-tblwrap' }, [t]);
  }
  function tableEl(id) {
    const t = A.tables[id];
    return h('div', { class: 'gdp-ltbl', id }, [runs2(h('p', { class: 'gdp-tblcap' }), t.legend, id), tableRows(t.rows)]);
  }
  // an indented paragraph is a quotation, or the contract question when it is bold and italic throughout
  function paraEl(b) {
    if (!b.indent) return h('p');
    return h('p', { class: b.runs.every(r => !(r.t || '').trim() || (r.b && r.i)) ? 'callout' : 'ind' });
  }
  function blocksTo(node, blocks, after) {
    (blocks || []).forEach((b, j) => {
      if (b.type === 'h3') {
        const hd = h('h3', { id: b.id, text: b.text });
        hd.appendChild(h('a', { class: 'anchor', href: '#' + b.id, 'aria-label': 'link to ' + b.text, text: '#' }));
        node.appendChild(hd);
      } else if (b.type === 'p') node.appendChild(runs2(paraEl(b), b.runs));
      else if (b.type === 'eq') node.appendChild(tex(h('div', { class: 'gdp-eq' }), b.tex + (b.n ? '\\tag{' + b.n + '}' : ''), true));
      else if (b.type === 'figure' && A.figures[b.id]) { if (drawOf(b.id) !== 'main') node.appendChild(figureEl(b.id)); }
      else if (b.type === 'table') node.appendChild(b.id ? tableEl(b.id) : tableRows(b.rows));
      else if (b.type === 'list') { const l = h(b.ordered ? 'ol' : 'ul'); b.items.forEach(it => l.appendChild(runs2(h('li'), it))); node.appendChild(l); }
      if (after) after(node, j);
    });
    return node;
  }
  function renderLetter() {
    const m = M.meta;
    const sec = id => A.sections.find(s => s.id === id);
    $('#gdpTitle').textContent = A.title;
    if (A.subtitle) $('#gdpSub').textContent = A.subtitle;
    const by = $('#gdpByline');
    by.innerHTML = '';
    if (A.byline) {
      runs2(by, A.byline);
      by.appendChild(h('span', { html: ' · Draft for comment · <a href="#reproduce">Data and code</a> · <a href="' + esc(m.code.paper) + '">The paper</a>' }));
      by.after(runs2(h('p', { class: 'gdp-aff' }), A.affiliations));
    } else by.hidden = true;
    if (A.summary) $('#contract').before(runs2(h('p', { class: 'gdp-summary' }), A.summary));
    // Fig. 1 is the contract's figure: its levels panel above the ladder, and its legend under them
    const card = figWith('main') || 'fig1';
    const mainCard = $('#gdpMain').parentNode;
    const levels = el('svg', { id: 'gdpLevels', viewBox: '0 0 960 ' + LEVELS_H(), role: 'img', 'aria-label': 'Fig. 1a' });
    const f1wrap = h('div', { id: card }, [h('div', { class: 'gdp-pl', text: 'a' }), h('div', { class: 'card' }, [levels]),
      h('div', { class: 'gdp-figstate', text: 'Panel a is drawn from the published run, at the article’s settings. Panel b, the readout and the ladder follow the controls.' }),
      h('div', { class: 'gdp-pl', text: 'b' })]);
    mainCard.before(f1wrap);
    if (A.figures[card]) $('#gdpMainMethod').before(runs2(h('p', { class: 'cap gdp-cap1' }), A.figures[card].legend, card));
    // the main text, each main figure after the paragraph that first cites it. The Letter's main text follows the
    // contract card. An introduction in the article's form starts above the card, and the card follows the
    // contract question and the list of its two outcomes.
    const art = $('#gdpArticle');
    art.innerHTML = '';
    // a figure the draft puts in the text is drawn there; the others follow the paragraph that cites them
    const inText = A.main.concat(...A.sections.filter(s => s.id !== 'figures').map(s => s.blocks)).filter(b => b.type === 'figure').map(b => b.id);
    const placed = new Set([card].concat(inText));
    const place = (node, ids) => ids.forEach(id => { if (!placed.has(id) && A.figures[id]) { placed.add(id); node.appendChild(figureEl(id)); } });
    const cited = b => b.type === 'list' ? b.items.flatMap(firstCited) : b.type === 'p' ? firstCited(b.runs) : [];
    let into = art, split = -1;
    if (!A.summary) {
      const qi = A.main.findIndex(b => b.type === 'p' && /^Will world GDP per (capita|person) grow/.test((b.runs || []).map(r => r.t || '').join('')));
      split = qi < 0 ? A.main.length - 1 : qi;
      for (let k = qi + 1; qi >= 0 && k <= qi + 2 && k < A.main.length; k++) if (A.main[k].type === 'list') { split = k; break; }
      into = h('article', { class: 'gdp-article', id: 'gdpIntro' });
      $('#contract').before(into);
      $('#proposal').hidden = true;
    }
    A.main.forEach((b, k) => {
      blocksTo(into, [b]);
      place(into, cited(b));
      if (k === split) into = art;
    });
    if (A.summary) place(art, Object.keys(A.figures).filter(id => /^fig\d+$/.test(id)));
    // then the detail sections (the Letter's Methods), the Extended Data, the references, the end notes and the
    // Supplementary Information
    const anchor = $('#reproduce');
    const part = (id, title, fill) => {
      anchor.before(h('div', { class: 'secttl', id, text: title }));
      const body = h('div', { class: 'gdp-article' });
      fill(body);
      anchor.before(body);
      return body;
    };
    const ends = ['acknowledgements', 'author-contributions', 'competing-interests', 'additional-information'].map(sec).filter(Boolean);
    const known = new Set(['figures', 'extended-data', 'references', 'supplementary-information'].concat(ends.map(s => s.id)));
    let lastBody = art;
    A.sections.filter(s => !known.has(s.id)).forEach(s => {
      // a main figure the text above has not shown follows the last paragraph here that cites it
      const last = {};
      s.blocks.forEach((x, j) => { if (x.type === 'p') firstCited(x.runs).forEach(id => { if (!placed.has(id)) last[id] = j; }); });
      lastBody = part(s.id, s.title.toUpperCase(), b => blocksTo(b, s.blocks, (node, j) => place(node, Object.keys(last).filter(id => last[id] === j))));
    });
    place(lastBody, Object.keys(A.figures).filter(id => /^fig\d+$/.test(id)));
    if (sec('extended-data')) part('extended-data', 'EXTENDED DATA', b => blocksTo(b, sec('extended-data').blocks));
    if (sec('references')) part('references', 'REFERENCES', b => {
      const refs = sec('references').refs || [];
      const ol = h('ol', { class: 'gdp-refs', start: refs.length ? refs[0].n : 1 });
      refs.forEach(r => ol.appendChild(runs2(h('li', { id: 'ref-' + r.n }), r.runs)));
      b.appendChild(ol);
    });
    if (ends.length) part('notes', 'NOTES', b => ends.forEach(s => { b.appendChild(h('h3', { text: s.title })); blocksTo(b, s.blocks); }));
    if (sec('supplementary-information')) part('supplementary', 'SUPPLEMENTARY INFORMATION', b => {
      const n = sec('supplementary-information').blocks.filter(x => x.type === 'table').length;
      const d = h('details', { class: 'gdp-si' }, [h('summary', { text: 'Show the Supplementary Information (' + (n === 1 ? 'a table' : n === 2 ? 'two tables' : n + ' tables') + ' and the computational details)' })]);
      blocksTo(d, sec('supplementary-information').blocks);
      b.appendChild(d);
    });
  }

  // ---------------------------------------------------------------- data and code
  function renderRepro() {
    const m = M.meta, repo = m.code.repo;
    const files = [['Temperature shocks (the paper’s filter)', 'bk_elnino/hamilton.py, bk_elnino/temperature.py'],
                   ['The paper’s estimates, re-estimated from its replication files', 'bk_elnino/lp.py, bk_elnino/irf.py, bk_elnino/panel.py'],
                   ['The IMF baseline and its record of misses', 'bk_elnino/imf_baseline.py'],
                   ['Temperatures after 2027', 'bk_elnino/future_temps.py'],
                   ['The simulation, the contract statistic and the cutoff', 'bk_elnino/analysis.py, bk_elnino/wager.py'],
                   ['The whole run', 'run_analysis.py'],
                   ['This page’s data and text', 'build_site_data.py, build_site_article.py'],
                   ['This page’s model, and its check against the run', 'site_tab/js/elnino-model.js, tests/test_site_model.py']];
    const link = f => f.split(', ').map(x => '<a href="' + repo + '/blob/main/' + x + '"><code>' + esc(x) + '</code></a>').join(', ');
    const srcRows = m.sources.map(s => '<tr><td>' + esc(s.what) + '</td><td><a href="' + esc(s.url) + '">source</a></td><td>' + esc(dateOf(s.retrieved)) + '</td><td>' + esc(s.terms) + '</td></tr>').join('')
      + '<tr><td>IMF World Economic Outlook, October 2026: world real GDP growth and population (DataMapper)</td><td><a href="https://www.imf.org/external/datamapper/NGDP_RPCH@WEO/WEOWORLD">source</a></td><td>' + esc(m.imfSources.baseline.replace(/.*retrieved /, '').replace(/\)$/, '')) + '</td><td>IMF terms of use</td></tr>'
      + '<tr><td>IMF Historical WEO Forecasts Database (October vintages 1990–2020)</td><td><a href="https://data.imf.org/-/media/iData/External-Storage/Documents/D2AC2BB4C12C4A8CB2B6DAF1159263EB/en/WEOhistorical.xlsx">source</a></td><td>30 October 2026</td><td>IMF terms of use</td></tr>'
      + '<tr><td>Bilal and Känzig (2026) replication package: Penn World Table and Barro–Ursúa world GDP, the authors’ shocks and Stata output</td><td><a href="' + esc(m.code.replication) + '">source</a></td><td></td><td>the authors’</td></tr>'
      + '<tr><td>The authors’ temperature shock series</td><td><a href="' + esc(m.code.shocks) + '">source</a></td><td></td><td>the authors’</td></tr>';
    $('#gdpRepro').innerHTML =
      '<p>Everything on this page comes from code anyone can run. The repository is <a href="' + esc(repo) + '">' + esc(repo.replace('https://', '')) + '</a>; the paper is <a href="' + esc(m.code.paper) + '">Bilal and Känzig (2026), <i>Quarterly Journal of Economics</i></a>.</p>'
      + '<pre>git clone ' + esc(repo) + '\ncd ' + esc(repo.split('/').pop()) + '\npython -I run_analysis.py --refresh     # download the data, re-estimate, simulate (a few minutes)\npython -I build_site_data.py           # the files this page loads\npython -I -m pytest tests -q            # includes the check of this page’s model against the run</pre>'
      + '<div class="gdp-tblwrap"><table class="gdp-tbl gdp-files"><thead><tr><th>What</th><th>Where</th></tr></thead><tbody>' + files.map(([a, b]) => '<tr><td>' + esc(a) + '</td><td>' + link(b) + '</td></tr>').join('') + '</tbody></table></div>'
      + '<p>This page loads <a href="' + BASE + 'model.json"><code>model.json</code></a> (every fixed number the model uses), <a href="' + BASE + esc(m.paths.file) + '"><code>' + esc(m.paths.file) + '</code></a> (the ' + M.n.toLocaleString() + ' paths’ random inputs, ' + (M.n * m.paths.fields.reduce((a, f) => a + f.count, 0) * 2 / 1e6).toFixed(1) + ' MB) and one file of bootstrap draws per estimate, written by <code>build_site_data.py</code> from the run of ' + esc(dateOf(m.refreshedUtc)) + '.</p>'
      + '<div class="gdp-tblwrap"><table class="gdp-tbl"><thead><tr><th>Data</th><th>Link</th><th>Retrieved</th><th>Terms</th></tr></thead><tbody>' + srcRows + '</tbody></table></div>';
  }

  // ---------------------------------------------------------------- start
  async function init() {
    tip = WXC.tooltip();
    const product = ((window.WX && WX.gdpContract) || {}).product;
    try {
      // the Letter's text when it is published, the cover article's until then
      const article = () => fetch(BASE + 'article.json').then(r => { if (!r.ok) throw new Error('article ' + r.status); return r.json(); });
      const [art, model, book] = await Promise.all([
        fetch(BASE + 'letter.json').then(r => (r.ok ? r.json() : null)).catch(() => null).then(l => (l && l.schema === 2 ? l : article())),
        ElNinoModel.load(BASE),
        product ? WXM.productBook(product) : null,
      ]);
      A = art; M = model; BOOK = book;
    } catch (e) {
      $('#pageStatus').appendChild(WXC.statusEl([{ source: 'none' }], MONTH));
      throw e;
    }
    // the status strip dates the run the model's files come from, which the El Niño refresh replaces monthly
    const ran = Date.parse(M.meta.refreshedUtc), age = (Date.now() - ran) / 6e4;
    $('#pageStatus').appendChild(WXC.statusEl([{ data: M.meta, source: 'live', asof: ran, ageMin: age, stale: !(age <= 2 * MONTH) }], MONTH));
    D = Object.assign({}, M.meta.defaults);
    S = readHash();
    $('#gdpByline').innerHTML = 'Patrick T. Brown · Draft for comment · <a href="#reproduce">Data and code</a> · <a href="' + esc(M.meta.code.paper) + '">The paper</a>';
    $('#gdpListing').textContent = listedBook()
      ? 'Listed on ForecastEx as ' + BOOK.name + '. The bars carry the exchange’s prices for the ' + M.meta.base + '–' + D.endYear
        + ' contract, Yes green and No red. The two marks on each bar are the views’ chances of Yes from the model on this page, not prices.'
      : 'Not listed yet. The contract is proposed to list ' + M.meta.ladder.lists + ', after NOAA publishes its 2026 annual value. Until then the ladder shows the proposed ' + (M.meta.ladder.strikes.length > 1 ? 'strikes' : 'strike') + ', hatched, with each view’s chance of Yes; once it lists, the bars carry the exchange’s prices and each opens the contract on IBKR.';
    if (A.schema === 2) renderLetter(); else renderArticle();
    renderRepro();
    buildControls();
    mainKey();
    syncControls();
    await M.ensure(S);
    const t0 = performance.now();
    R = M.run(S);
    drawAll();
    status('Run in ' + Math.round(performance.now() - t0) + ' ms on ' + M.n.toLocaleString() + ' paths.');
    window.addEventListener('scroll', () => requestAnimationFrame(floatBar), { passive: true });
    // a link to a figure or a section moves the page; only an address with settings changes them
    window.addEventListener('hashchange', () => { if (!location.hash.includes('=')) return; S = readHash(); schedule(0); syncControls(); });
    // a link into the closed Supplementary Information opens it
    document.addEventListener('click', e => {
      const a = e.target.closest && e.target.closest('a[href^="#"]');
      const t = a && document.getElementById(a.getAttribute('href').slice(1));
      const d = t && t.closest('details');
      if (d) d.open = true;
    });
    if (location.hash.length > 1) { const id = location.hash.slice(1); const t = document.getElementById(id); if (t) t.scrollIntoView(); }
  }

  return { init };
})();
