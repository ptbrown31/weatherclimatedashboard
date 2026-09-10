/* Shared ground for the five accuracy figures.

   The page draws five figures from six files the record builder ships,
   every one under the conventions in docs/accuracy.md. This module owns
   what the figures share so they agree with each other without meaning to:
   the naming and order of the systems, one color per system, the loading
   of the files and the status strip built from their meta, the method note
   under each figure, and the small geometry the SVG figures draw with.

   Each figure module (acc-lead, acc-dyn, acc-cal, acc-map, acc-grid) exports
   draw(D), where D is the bundle init() assembles. A figure module owns its
   own tabs, its own tooltips and its own method note; it borrows the helpers
   here so a band, a line or a lead axis looks the same on every figure. */
window.WXAcc = (() => {
  const { el, txt, h, $ } = WXC;

  // ------------------------------------------------------------- naming
  // docs/accuracy.md section 2. Every export uses these ids; the reader sees
  // the names. The eleven panel tools are NDFD through JMA, in this order.
  const NAME = {
    FX: 'ForecastEx',
    NDFD: 'National Weather Service', NBM: 'National Blend of Models', LAMP: 'Aviation Forecast',
    ECMWF: 'European Model', GFS: 'American Model', MOSMIX: 'German Statistical Model',
    ICON: 'German Model', GEM: 'Canadian Model', UKMO: 'UK Model', MF: 'French Model', JMA: 'Japanese Model',
    ECMWF_IFS: 'European Ensemble Mean', GFS_MOS: 'GFS MOS', NAM_MOS: 'NAM MOS', NBS_MOS: 'Blend MOS',
    HRRR: 'HRRR', HRRR_OM: 'HRRR (Open-Meteo)',
  };
  // a shorter name for a legend or a column head, where the full one wraps
  const SHORT = {
    FX: 'ForecastEx', NDFD: 'NWS', NBM: 'Blend', LAMP: 'Aviation', ECMWF: 'European', GFS: 'American',
    MOSMIX: 'German Stat.', ICON: 'German', GEM: 'Canadian', UKMO: 'UK', MF: 'French', JMA: 'Japanese',
    ECMWF_IFS: 'Euro. Ens.', GFS_MOS: 'GFS MOS', NAM_MOS: 'NAM MOS', NBS_MOS: 'Blend MOS',
    HRRR: 'HRRR', HRRR_OM: 'HRRR (OM)',
  };
  const PANEL = ['NDFD', 'NBM', 'LAMP', 'ECMWF', 'GFS', 'MOSMIX', 'ICON', 'GEM', 'UKMO', 'MF', 'JMA'];
  const CORE5 = ['NDFD', 'NBM', 'LAMP', 'ECMWF', 'GFS'];
  const EXTRA = ['ECMWF_IFS', 'GFS_MOS', 'NAM_MOS', 'NBS_MOS', 'HRRR', 'HRRR_OM'];
  const ORDER = ['FX'].concat(PANEL, EXTRA);
  // which cohort each id belongs to, for a figure that filters by cohort
  const COHORT = { matched11: ['FX'].concat(PANEL), core5: ['FX'].concat(CORE5), own: ORDER };

  // ------------------------------------------------------------- palette
  // The exchange keeps the site accent so it is the line a reader finds
  // first. The eleven panel tools take a muted ramp (--t1 to --t11 in
  // site.css), one hue each in panel order, so they read as a family behind
  // the market rather than as eleven competing colors. The extra sources
  // share one gray: they are drawn on their own span and never against each
  // other, so telling them apart by color is not something the page asks.
  function color(id) {
    if (id === 'FX') return 'var(--accent)';
    const i = PANEL.indexOf(id);
    if (i >= 0) return 'var(--t' + (i + 1) + ')';
    return 'var(--t-extra)';
  }
  // the market line is heavier than a tool line, for the same reason
  const width = id => (id === 'FX' ? 2.6 : 1.5);
  const name = id => NAME[id] || id;
  const short = id => SHORT[id] || id;
  const swatch = id => '<span class="sw" style="background:' + color(id) + '"></span>' + name(id);

  // ------------------------------------------------------------- formats
  const dash = '—';
  const f1 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 10) / 10).toFixed(1));
  const f2 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 100) / 100).toFixed(2));
  const f3 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 1000) / 1000).toFixed(3));
  const deg1 = v => (v == null || isNaN(v) ? dash : f1(v) + '°');
  const signed1 = v => (v == null || isNaN(v) ? dash : (v > 0 ? '+' : v < 0 ? '−' : '') + f1(Math.abs(v)));
  // a share in [0, 1] as whole percent; pct1 keeps one decimal
  const pct = v => (v == null || isNaN(v) ? dash : Math.round(v * 100) + '%');
  const pct1 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 1000) / 10).toFixed(1) + '%');
  const int = n => (n == null || isNaN(n) ? dash : Math.round(n).toLocaleString('en-US'));
  const hours = v => (v == null || isNaN(v) ? dash : Math.round(v) + ' h');
  // an interval as text, for a tooltip row
  const iv = (lo, hi, f) => (lo == null || hi == null ? dash : (f || f1)(lo) + ' to ' + (f || f1)(hi));

  // ------------------------------------------------------------- files
  // Six files under snapshots/accuracy/, daily from the builder, so the
  // cadence is a day and stale means two. A file counts only when it carries
  // the meta the contract requires; the old lead curve sits at the same path
  // until this page is live and must not be read as the new one.
  const FILES = { lead: 'lead-curve', dyn: 'dynamics', cal: 'calibration', map: 'map', grid: 'grid',
                  avail: 'availability' };
  const CADENCE = 1440;
  const valid = d => !!(d && d.meta && d.meta.schema != null && d.meta.asof && d.meta.conventions);
  async function load(name, loose) {
    const r = await WXD.get('accuracy/' + name + '.json', CADENCE);
    const ok = r.data && (loose ? !!r.data.meta : valid(r.data));
    return { data: ok ? r.data : null, r };
  }
  // a trace file for one target date; its meta is {date, built} only
  const trace = date => load('trace/' + date, true);

  // ------------------------------------------------------------- status
  const plur = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  function ago(mins) {
    if (mins == null || isNaN(mins)) return '';
    if (mins < 1) return 'just now';
    if (mins < 90) return plur(Math.round(mins), 'minute') + ' ago';
    if (mins < 48 * 60) return plur(Math.round(mins / 60), 'hour') + ' ago';
    return plur(Math.round(mins / (60 * 24)), 'day') + ' ago';
  }
  const isoShort = s => (s ? String(s).replace('T', ' ').replace(/:\d\d(\.\d+)?Z$/, 'Z') : '');
  // "Window 2026-06-12 to 2026-09-09, built 2026-09-10 10:52Z" for a caption
  function windowAndBuilt(meta) {
    if (!meta) return '';
    const w = meta.window || {};
    const parts = [];
    if (w.from && w.to) parts.push('Window ' + w.from + ' to ' + w.to);
    if (meta.asof) parts.push('last resolved day ' + meta.asof);
    if (meta.built) parts.push('built ' + isoShort(meta.built));
    return parts.join(', ');
  }
  // the newest meta among the loaded files, by build time
  function newestMeta(D) {
    let best = null;
    Object.keys(FILES).forEach(k => {
      const m = D[k] && D[k].meta;
      if (m && (!best || String(m.built || '') > String(best.built || ''))) best = m;
    });
    return best;
  }
  /* The status strip. WXC.statusEl reads a top-level asof and these files
     keep theirs under meta, so the strip is built here in the same shape:
     the dot, the as-of time in the reader's zone, the age, the cadence. */
  function statusEl(D) {
    const meta = newestMeta(D);
    const n = Object.keys(FILES).filter(k => D[k]).length;
    if (!meta) return h('span', { class: 'status none', text: 'No data available. The accuracy record has not been published yet.' });
    const built = Date.parse(meta.built || '');
    const mins = isNaN(built) ? null : (Date.now() - built) / 6e4;
    const cached = Object.keys(FILES).some(k => D.results[k] && D.results[k].source === 'cache');
    const stale = mins == null || mins > 2 * CADENCE;
    const when = isNaN(built) ? 'unknown time' : WXC.clockFull(built, Intl.DateTimeFormat().resolvedOptions().timeZone);
    const w = meta.window || {};
    let text = 'Data as of ' + when + (mins == null ? '' : ' (' + ago(mins) + ')')
      + (w.from && w.to ? ' · window ' + w.from + ' to ' + w.to : '')
      + (stale ? '; updates are normally daily and the record is behind' : ' · updates daily');
    if (cached) text = 'Showing the last data this browser saved (as of ' + when + '); the live fetch failed.';
    if (n < Object.keys(FILES).length) text += ' · ' + n + ' of ' + Object.keys(FILES).length + ' files published';
    return h('span', { class: 'status ' + (cached ? 'cache' : stale ? 'stale' : 'live'), text });
  }

  // ------------------------------------------------------------- controls
  /* A group of tab buttons inside a .bar.

     options are strings or {key, label, title}. onChange(key) runs on a
     click that changes the selection, never on the initial state. opts:
     {initial, label}; the label is set in front of the group in muted type.
     Returns {get, set(key, silent)}; several groups can share one bar, each
     in its own span, so a figure can carry highs/lows beside a cohort. */
  function tabs(barEl, options, onChange, opts) {
    opts = opts || {};
    const items = options.map(o => (typeof o === 'string' ? { key: o, label: o } : o));
    let cur = opts.initial != null ? opts.initial : items[0].key;
    const group = h('span', { class: 'tabgroup' });
    if (opts.label) group.appendChild(h('span', { class: 'tl', text: opts.label }));
    const btns = {};
    items.forEach(it => {
      const b = h('button', { class: 'vbtn' + (it.key === cur ? ' on' : ''), text: it.label, title: it.title || null });
      b.onclick = () => { if (it.key !== cur) set(it.key); };
      btns[it.key] = b; group.appendChild(b);
    });
    function set(key, silent) {
      if (!btns[key]) return;
      cur = key;
      Object.keys(btns).forEach(k => btns[k].classList.toggle('on', k === key));
      if (!silent && onChange) onChange(key);
    }
    barEl.appendChild(group);
    return { get: () => cur, set };
  }
  // highs default, lows a tab, on every figure
  const metricTabs = (barEl, onChange, initial) =>
    tabs(barEl, [{ key: 'high', label: 'Highs' }, { key: 'low', label: 'Lows' }], onChange, { initial: initial || 'high' });

  // ------------------------------------------------------------- key
  // the legend under a figure: one entry per id, a colored rule and the name
  function key(container, ids, opts) {
    opts = opts || {};
    container.innerHTML = '';
    ids.forEach(id => {
      const e = h('span', { 'data-id': id });
      e.appendChild(h('i', { style: 'border-color:' + color(id) + (id === 'FX' ? ';border-top-width:3px' : '') }));
      e.appendChild(document.createTextNode(opts.short ? short(id) : name(id)));
      container.appendChild(e);
    });
    if (opts.note) container.appendChild(h('span', { class: 'kn', text: opts.note }));
  }

  // ------------------------------------------------------------- method note
  /* The note under a figure: what was estimated and how it was sampled.

     {title, equation, rules, n}. The equation is typeset as it was written,
     in a monospace block; the rules are short lines, one thought each; n
     is the sample line, a number of city-days or a sentence of its own. */
  /* Typeset an equation, or fall back to its source.

     KaTeX is vendored beside the page rather than fetched, so it is there
     whenever the page is; if it ever is not, the LaTeX source is still the
     statement of the estimator and is shown as written rather than nothing. */
  function tex(el, src, display) {
    if (window.katex) {
      try {
        window.katex.render(src, el, { displayMode: !!display, throwOnError: false });
        return el;
      } catch (e) { /* an unparsable source falls through to itself */ }
    }
    el.textContent = src;
    el.classList.add('texraw');
    return el;
  }
  // prose carrying inline math between single dollars, as the notes are written
  function mathText(el, s) {
    String(s).split(/\$([^$]+)\$/).forEach((part, i) => {
      if (!part) return;
      if (i % 2 === 0) { el.appendChild(document.createTextNode(part)); return; }
      tex(el.appendChild(h('span', { class: 'mi' })), part, false);
    });
    return el;
  }

  /* The method note under a figure: a title, then the body, then the sampling
     rules, then the sample line. A body item is either a line of prose, which
     may carry inline math, or {tex} for a display equation. The equations are
     the page's statement of what it measured, so they are set as mathematics
     rather than printed as code. */
  function methodNote(container, spec) {
    if (!container) return null;
    container.innerHTML = '';
    const box = h('div', { class: 'accnote' });
    if (spec.title) box.appendChild(h('div', { class: 'nt', text: spec.title }));
    (spec.body || []).forEach(item => {
      if (item && item.tex) { tex(box.appendChild(h('div', { class: 'eq' })), item.tex, true); return; }
      mathText(box.appendChild(h('div', { class: 'rule' })), item);
    });
    (spec.rules || []).forEach(r => mathText(box.appendChild(h('div', { class: 'rule' })), r));
    if (spec.n != null) {
      box.appendChild(h('div', { class: 'rule n',
        text: typeof spec.n === 'number' ? 'Sample ' + int(spec.n) + ' city-days.' : String(spec.n) }));
    }
    container.appendChild(box);
    return box;
  }

  // ------------------------------------------------------------- tooltip
  let tip = null;
  const tooltip = () => (tip = tip || WXC.tooltip());
  // one hover binding: make() returns the tooltip html
  function hover(node, make) {
    node.addEventListener('mousemove', e => tooltip().show(e, make(e)));
    node.addEventListener('mouseleave', () => tooltip().hide());
    node.style.cursor = 'default';
  }

  // ------------------------------------------------------------- geometry
  // Every SVG figure is drawn in a 960-wide viewBox and scaled by CSS.
  const W = 960;
  // the plot frame for a figure of height H; margins may be overridden
  function frame(H, m) {
    m = m || {};
    return { W, H, L: m.L != null ? m.L : 62, R: m.R != null ? m.R : 920,
             T: m.T != null ? m.T : 22, B: m.B != null ? m.B : H - 58 };
  }
  function clear(svg, H) {
    svg.innerHTML = '';
    if (H != null) svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  }
  // a linear scale from data [d0, d1] to pixels [p0, p1], with invert
  function scale(d0, d1, p0, p1) {
    const s = (p1 - p0) / ((d1 - d0) || 1);
    const f = v => p0 + (v - d0) * s;
    f.invert = p => d0 + (p - p0) / s;
    return f;
  }
  // Lead runs down to the right, so the day ends at the right edge and a
  // curve is read the way the day is lived; hmax sits at L and hmin at R.
  const leadScale = (g, hmax, hmin) => scale(hmax, hmin == null ? 0 : hmin, g.L, g.R);
  function ticks(lo, hi, step) {
    const out = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
    return out;
  }
  // a round step so about `target` ticks land on readable numbers
  function niceStep(range, target) {
    const raw = range / (target || 6);
    const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const m = raw / p;
    return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
  }
  // the bottom axis: tick labels under the frame, a title under those
  function xAxis(svg, g, x, tks, fmt, label, opts) {
    opts = opts || {};
    tks.forEach(v => {
      if (opts.grid) svg.appendChild(el('line', { x1: x(v), x2: x(v), y1: g.T, y2: g.B, class: 'grid' }));
      svg.appendChild(txt((fmt || String)(v), { x: x(v), y: g.B + 17, 'text-anchor': 'middle', class: 'ax' }));
    });
    if (label) svg.appendChild(txt(label, { x: (g.L + g.R) / 2, y: g.B + 38, 'text-anchor': 'middle', class: 'ax' }));
  }
  // the left axis: grid lines across the frame, labels at the left, a
  // title rotated up the margin
  function yAxis(svg, g, y, tks, fmt, label, opts) {
    opts = opts || {};
    tks.forEach(v => {
      if (opts.grid !== false) svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt((fmt || String)(v), { x: g.L - 8, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    });
    if (label) svg.appendChild(txt(label, { x: 17, y: (g.T + g.B) / 2, 'text-anchor': 'middle',
                                            transform: 'rotate(-90 17 ' + (g.T + g.B) / 2 + ')', class: 'ax' }));
  }
  // the lead axis every 6 h, with the day boundary named where it is in range
  function leadAxis(svg, g, x, hmax, hmin, label) {
    hmin = hmin == null ? 0 : hmin;
    xAxis(svg, g, x, ticks(hmin, hmax, 6), v => v + 'h', label || 'Hours before the end of the target day');
    if (hmax >= 24 && hmin <= 24) {
      svg.appendChild(el('line', { x1: x(24), x2: x(24), y1: g.T, y2: g.B, class: 'grid', 'stroke-dasharray': '4 4' }));
      svg.appendChild(txt('the target day begins', { x: x(24) - 6, y: g.T + 12, 'text-anchor': 'end', class: 'ax' }));
    }
  }

  const fin = v => v != null && isFinite(v);
  /* A line through pixel points, broken wherever a value is missing.

     A bin under 30 city-days is null in the files and is not drawn at all,
     so the line lifts its pen there rather than bridging the gap with a
     segment that would claim a value nobody computed. */
  function lineSeries(svg, xs, ys, attrs) {
    let d = '', pen = false, n = 0;
    for (let i = 0; i < xs.length; i++) {
      if (!fin(xs[i]) || !fin(ys[i])) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + xs[i].toFixed(1) + ',' + ys[i].toFixed(1);
      pen = true; n++;
    }
    if (!n) return null;
    const a = Object.assign({ d, fill: 'none', 'stroke-width': 2, 'stroke-linejoin': 'round',
                              'stroke-linecap': 'round', 'pointer-events': 'none' }, attrs || {});
    const p = el('path', a);
    svg.appendChild(p);
    return p;
  }
  // a dot at each defined point
  function dots(svg, xs, ys, attrs) {
    const g = el('g', { 'pointer-events': 'none' });
    for (let i = 0; i < xs.length; i++) {
      if (!fin(xs[i]) || !fin(ys[i])) continue;
      g.appendChild(el('circle', Object.assign({ cx: xs[i], cy: ys[i], r: 2.4 }, attrs || {})));
    }
    svg.appendChild(g);
    return g;
  }
  /* A bootstrap band between two pixel series, one closed polygon per run
     of defined points, so a gap in the interval is a gap in the shading. */
  function band(svg, xs, lo, hi, fill, attrs) {
    let d = '', runs = 0;
    let i = 0;
    while (i < xs.length) {
      while (i < xs.length && !(fin(xs[i]) && fin(lo[i]) && fin(hi[i]))) i++;
      const s = i;
      while (i < xs.length && fin(xs[i]) && fin(lo[i]) && fin(hi[i])) i++;
      if (i - s < 2) continue;
      let top = '', bot = '';
      for (let k = s; k < i; k++) top += (k === s ? 'M' : 'L') + xs[k].toFixed(1) + ',' + hi[k].toFixed(1);
      for (let k = i - 1; k >= s; k--) bot += 'L' + xs[k].toFixed(1) + ',' + lo[k].toFixed(1);
      d += top + bot + 'Z';
      runs++;
    }
    if (!runs) return null;
    const p = el('path', Object.assign({ d, fill: fill || 'var(--muted)', 'fill-opacity': 0.16, stroke: 'none',
                                         'pointer-events': 'none' }, attrs || {}));
    svg.appendChild(p);
    return p;
  }
  // a series label at a point, in the series color, kept clear of the frame
  function label(svg, x, y, text, col, attrs) {
    return svg.appendChild(txt(text, Object.assign({ x, y, 'font-size': 10.5, 'font-weight': 700, fill: col,
                                                     class: 'lbl' }, attrs || {})));
  }

  // ------------------------------------------------------------- empty states
  // a figure with nothing to draw says so in its own frame, small
  function notYet(host, msg) {
    if (typeof host === 'string') host = $(host);
    if (!host) return;
    if (host.tagName && host.tagName.toLowerCase() === 'svg') {
      clear(host, 60);
      host.appendChild(txt(msg, { x: 20, y: 34, class: 'axl' }));
    } else {
      host.innerHTML = '';
      host.appendChild(h('p', { class: 'cap', text: msg }));
    }
  }
  const NOT_PUBLISHED = 'This figure has not been published yet. The record is built daily on the machine that holds the capture.';

  // ------------------------------------------------------------- init
  // the bundle the figures draw from; kept so a tab change can redraw
  let D = null;
  const MODULES = [['WXAccLead', 'lead'], ['WXAccMap', 'map'], ['WXAccGrid', 'grid'], ['WXAccCal', 'cal'], ['WXAccDyn', 'dyn']];
  // anything written as mathematics in the page's own markup, set once the
  // typesetter is loaded; the element's text is the source, so a page that
  // loses the typesetter still reads
  function typeset(root) {
    (root || document).querySelectorAll('[data-tex]').forEach(el => {
      const src = el.getAttribute('data-tex');
      el.removeAttribute('data-tex');
      tex(el, src, true);
    });
  }

  async function init() {
    tooltip();
    typeset(document);
    const keys = Object.keys(FILES);
    const got = await Promise.all(keys.map(k => load(FILES[k])));
    D = { results: {} };
    keys.forEach((k, i) => { D[k] = got[i].data; D.results[k] = got[i].r; });
    D.meta = newestMeta(D);
    const st = $('#pageStatus');
    if (st) { st.innerHTML = ''; st.appendChild(statusEl(D)); }
    MODULES.forEach(([g, k]) => {
      const mod = window[g];
      if (!mod || typeof mod.draw !== 'function') return;
      try { mod.draw(D); } catch (e) { console.error('accuracy figure ' + k + ' failed', e); }
    });
    return D;
  }

  return {
    init, load, trace, valid, data: () => D, FILES, CADENCE,
    NAME, SHORT, PANEL, CORE5, EXTRA, ORDER, COHORT, color, width, name, short, swatch,
    f1, f2, f3, deg1, signed1, pct, pct1, int, hours, iv, dash,
    windowAndBuilt, newestMeta, statusEl, isoShort,
    tabs, metricTabs, key, methodNote, tex, mathText, typeset, tooltip, hover,
    W, frame, clear, scale, leadScale, ticks, niceStep, xAxis, yAxis, leadAxis, lineSeries, dots, band, label,
    notYet, NOT_PUBLISHED,
  };
})();
