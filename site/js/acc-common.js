/* Shared ground for the accuracy figures.

   The page draws its figures from four files the record builder ships,
   every one under the conventions in docs/accuracy.md. This module owns
   what the figures share so they agree with each other without meaning to:
   the naming and order of the systems, one color per system, the loading
   of the files and the status strip built from their meta, the method note
   under each figure, and the small geometry the SVG figures draw with.

   Each figure module (acc-lead, acc-prob, acc-map, acc-grid) exports draw(D),
   where D is the bundle init() assembles. A figure module owns its own tabs,
   its own tooltips and its own method note; it borrows the helpers here so a
   band, a line or a lead axis looks the same on every figure, and every chart
   against lead is drawn by leadChart. */
window.WXAcc = (() => {
  const { el, txt, h, $ } = WXC;

  // ------------------------------------------------------------- series
  /* Every series the files score, in the paper's order with the market first, read from meta.series
     at init (docs/accuracy.md section 2). A series is one forecast system scored against one standard.
     Five systems are scored twice, as in the paper: the three station MOS products on the daytime
     maximum they issue, against the climate report, and on their hourly rows, against the METAR
     settle; and the National Weather Service forecast and the National Blend of Models against URMA
     as well as against their own standards. */
  let ORDER = [];
  let GROUP_LIST = [];
  const INFO = {};
  const MARKET_NAME = 'ForecastEx prediction market';
  // the names the paper's Table 3 gives the series of a system scored twice (its 'issued max' written out)
  const TABLE_NAME = {
    NBS_MOS: 'NBM station MOS, issued maximum', NBS_MOS_ROW: 'NBM station MOS, hourly rows',
    GFS_MOS: 'GFS MOS, issued maximum', GFS_MOS_ROW: 'GFS MOS, hourly rows',
    NAM_MOS: 'NAM MOS, issued maximum', NAM_MOS_ROW: 'NAM MOS, hourly rows',
    NBM: 'National Blend of Models, METAR', NBM_URMA: 'National Blend of Models, URMA',
    NDFD: 'National Weather Service, climate report', NDFD_URMA: 'National Weather Service, URMA',
  };
  function setSeries(meta) {
    ORDER = [];
    ((meta && meta.series) || []).forEach(s => { INFO[s.id] = s; ORDER.push(s.id); });
    GROUP_LIST = (meta && meta.groups) || [];
  }
  /* The groups of the paper's Table 1 holding the ids asked for, in its order: [{key, title, ids}].
     Anything the groups do not name is gathered last, so nothing a file carries drops from view. */
  function systemGroups(ids) {
    const want = new Set(ids || []);
    const seen = new Set();
    const out = [];
    GROUP_LIST.forEach(g => {
      const got = (g.series || []).filter(id => want.has(id) && !seen.has(id));
      got.forEach(id => seen.add(id));
      if (got.length) out.push({ key: g.title, title: g.title, ids: got });
    });
    const rest = (ids || []).filter(id => id !== 'FX' && !seen.has(id));
    if (rest.length) out.push({ key: 'other', title: 'Other series', ids: rest });
    return out;
  }

  // ------------------------------------------------------------- palette
  /* The market keeps the site accent so it is the line a reader finds first. Every other series takes
     its group's hue, as in the paper's figures and the brief at the top of the page: station MOS
     green, the Blend teal, the National Weather Service orange, the AI model purple and the raw models
     tan. LAMP, the station product whose target is closest to the market's, is drawn heavier, and a
     system's second series is dashed. */
  const GROUP_TOKEN = {
    'Station MOS': '--g-mos', 'Blend': '--g-blend', 'Human-edited': '--g-human',
    'Artificial intelligence': '--g-ai', 'Raw numerical weather prediction': '--g-raw',
  };
  function color(id) {
    if (id === 'FX') return 'var(--accent)';
    const g = INFO[id] && INFO[id].group;
    return 'var(' + (GROUP_TOKEN[g] || '--t-extra') + ')';
  }
  const width = id => (id === 'FX' ? 2.6 : id === 'LAMP' ? 2.3 : 1.3);
  const dashOf = id => (INFO[id] && INFO[id].second ? '5 3' : null);
  const name = id => (id === 'FX' ? MARKET_NAME : TABLE_NAME[id] || (INFO[id] && INFO[id].name) || id);
  // the compact names a hover table uses, where the full ones would not fit the box
  const SHORT_NAME = {
    NBS_MOS: 'NBM MOS, issued max', NBS_MOS_ROW: 'NBM MOS, hourly rows', GFS_MOS: 'GFS MOS, issued max',
    GFS_MOS_ROW: 'GFS MOS, hourly rows', NAM_MOS: 'NAM MOS, issued max', NAM_MOS_ROW: 'NAM MOS, hourly rows',
    NBM: 'Blend, METAR', NBM_URMA: 'Blend, URMA', NDFD: 'NWS, climate report', NDFD_URMA: 'NWS, URMA',
    AIFS: 'AIFS ens. mean', ECMWF_IFS: 'IFS ens. mean', ECMWF: 'ECMWF IFS', GEFS: 'GEFS median',
    ICON_ENS: 'ICON-EPS median', GEM_ENS: 'GEPS median', MOSMIX: 'MOSMIX', MF: 'ARPEGE', UKMO: 'Met Office UM',
  };
  const short = id => (id === 'FX' ? 'ForecastEx' : SHORT_NAME[id] || name(id));
  // the paper's qualifier: the daily value a series is scored on, whether it is corrected, and its standard
  const qual = id => (id === 'FX' ? 'central value, as quoted, vs. METARs' : (INFO[id] && INFO[id].qual) || '');
  const swatch = id => '<span class="sw" style="background:' + color(id) + '"></span>' + name(id);
  const swatchShort = id => '<span class="sw" style="background:' + color(id) + '"></span>' + short(id);

  // ------------------------------------------------------------- formats
  const dash = '—';
  const f1 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 10) / 10).toFixed(1));
  const f2 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 100) / 100).toFixed(2));
  const f3 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 1000) / 1000).toFixed(3));
  const deg1 = v => (v == null || isNaN(v) ? dash : f1(v) + '°');
  // a share in [0, 1] as whole percent; pct1 keeps one decimal
  const pct = v => (v == null || isNaN(v) ? dash : Math.round(v * 100) + '%');
  const pct1 = v => (v == null || isNaN(v) ? dash : (Math.round(v * 1000) / 10).toFixed(1) + '%');
  const int = n => (n == null || isNaN(n) ? dash : Math.round(n).toLocaleString('en-US'));
  // an interval as text, for a tooltip row
  const iv = (lo, hi, f) => (lo == null || hi == null ? dash : (f || f1)(lo) + ' to ' + (f || f1)(hi));

  // ------------------------------------------------------------- files
  // Four files under snapshots/accuracy/, daily from the builder, so the
  // cadence is a day and stale means two. A file counts only when it carries
  // the meta the contract requires (schema, asof, conventions).
  const FILES = { lead: 'lead-curve', cal: 'calibration', map: 'map', grid: 'grid' };
  const CADENCE = 1440;
  /* The page reads schema 2, the paper's methods. A file of the earlier schema is one the builder is
     about to replace, so the figures say they are being rebuilt rather than misreading it. */
  const SCHEMA = 'accuracy-figures/2';
  const valid = d => !!(d && d.meta && d.meta.schema === SCHEMA && d.meta.asof && d.meta.conventions);
  const older = d => !!(d && d.meta && d.meta.schema && d.meta.schema !== SCHEMA);
  async function load(name) {
    const r = await WXD.get('accuracy/' + name + '.json', CADENCE);
    return { data: r.data && valid(r.data) ? r.data : null, older: older(r.data), r };
  }

  // ------------------------------------------------------------- status
  const plur = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  function ago(mins) {
    if (mins == null || isNaN(mins)) return '';
    if (mins < 1) return 'just now';
    if (mins < 90) return plur(Math.round(mins), 'minute') + ' ago';
    if (mins < 48 * 60) return plur(Math.round(mins / 60), 'hour') + ' ago';
    return plur(Math.round(mins / (60 * 24)), 'day') + ' ago';
  }
  // the close of a method note's sample line, the newest day scored and when
  // the files were built
  function windowAndBuilt(meta) {
    if (!meta) return '';
    const parts = [];
    if (meta.asof) parts.push('Last day scored ' + mdyY(meta.asof));
    if (meta.built) {
      const b = String(meta.built);
      parts.push('built ' + mdyY(b.slice(0, 10)) + ' ' + b.slice(11, 16) + ' UTC');
    }
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
     the dot, the as-of time in the reader's zone with the zone named, the
     age, the cadence. */
  function statusEl(D) {
    const meta = newestMeta(D);
    const n = Object.keys(FILES).filter(k => D[k]).length;
    if (!meta) {
      return h('span', { class: 'status none', text: D.rebuilding
        ? 'The figures are being rebuilt on the methods of the working paper and return with the next daily update.'
        : 'No data available. The accuracy record has not been published yet.' });
    }
    const built = Date.parse(meta.built || '');
    const mins = isNaN(built) ? null : (Date.now() - built) / 6e4;
    const cached = Object.keys(FILES).some(k => D.results[k] && D.results[k].source === 'cache');
    const stale = mins == null || mins > 2 * CADENCE;
    const when = isNaN(built) ? 'unknown time' : WXC.clockZone(built, Intl.DateTimeFormat().resolvedOptions().timeZone);
    // the days the ForecastEx record is scored on, which is the span the
    // working paper quotes, rather than the wider window the builder reads
    const fx = (INFO.FX || {}).scored || {};
    const w = fx.start && fx.end ? { from: fx.start, to: fx.end } : (meta.window || {});
    let text = 'Data as of ' + when + (mins == null ? '' : ' (' + ago(mins) + ')')
      + (w.from && w.to ? ' · target days ' + mdyY(w.from) + ' to ' + mdyY(w.to) : '')
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
     in its own span, so a figure can carry highs/lows beside another group. */
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

  // ------------------------------------------------------------- record spans
  /* How far back each record goes.

     The series on this page do not share a span, so every figure says which days it used. The
     builder ships each series' scored span under meta.series, whole and per metric: the days it was
     actually scored on, its bias-correction warm-up left out. */
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  // '2026-02-11' as 'Feb 11', and with the year where a caption needs it
  function mdy(iso) {
    if (!iso) return '';
    const p = String(iso).split('-');
    return p.length < 3 ? String(iso) : MON[+p[1] - 1] + ' ' + (+p[2]);
  }
  const mdyY = iso => (iso ? mdy(iso) + ' ' + String(iso).slice(0, 4) : '');
  /* The span of one series: {start, end, days}, on one metric's record when a figure shows one. */
  function span(meta, id, metric) {
    const sy = INFO[id];
    if (!sy) return null;
    const s = (metric && sy.byMetric && sy.byMetric[metric]) || sy.scored;
    return s && s.start ? s : null;
  }
  // 'since Feb 11' for a legend entry or a column head
  function since(meta, id, metric) {
    const s = span(meta, id, metric);
    return s ? 'since ' + mdy(s.start) : '';
  }
  /* One sentence naming the span of every system a figure drew, systems
     that share a start date named together, earliest first. This is the
     line that goes in a method note under a figure scored on each system's
     own days. */
  function spanLine(meta, ids, metric, opts) {
    opts = opts || {};
    const byStart = new Map();
    (ids || []).forEach(id => {
      const s = span(meta, id, metric);
      if (!s) return;
      if (!byStart.has(s.start)) byStart.set(s.start, []);
      byStart.get(s.start).push(opts.short ? short(id) : name(id));
    });
    if (!byStart.size) return '';
    const keys = Array.from(byStart.keys()).sort();
    const parts = keys.map(k => {
      const ns = byStart.get(k);
      const who = ns.length > 3 ? ns.slice(0, 2).join(', ') + ' and ' + (ns.length - 2) + ' more' : ns.join(', ');
      return mdyY(k) + ' for ' + who;
    });
    return (opts.lead || 'Records run from') + ' ' + parts.join('; ') + '.';
  }
  /* The sentence saying which days a figure drew, for either set of days,
     on one metric's record when the figure shows one. */
  function cohortSpanLine(meta, cohort, metric) {
    if (cohort === 'all') {
      const fx = span(meta, 'FX', metric);
      return 'The ForecastEx prediction market\u2019s record runs'
        + (fx && fx.start ? ' from ' + mdyY(fx.start) + ' to ' + mdyY(fx.end || meta.asof) : ' over the whole window')
        + '. Each series is scored on the part of it that its own record covers, and each comparison between the market and a series on the days the two share.';
    }
    const c = meta && meta.cohorts && meta.cohorts.balanced;
    const from = c && c['from_' + (metric || 'high')];
    if (!from) return '';
    return 'Scored from ' + mdyY(from) + ' to ' + mdyY(meta.asof)
      + ' on the balanced sample, the location-days on which every series holds a value at every lead from 1 to 30 hours before the end of the day.';
  }

  // ------------------------------------------------------------- key
  // the legend under a figure: one entry per id, a colored rule and the name
  function key(container, ids, opts) {
    opts = opts || {};
    container.innerHTML = '';
    ids.forEach(id => {
      const e = h('span', { 'data-id': id });
      e.appendChild(h('i', { style: 'border-color:' + color(id) + (id === 'FX' ? ';border-top-width:3px' : '')
                                    + (dashOf(id) && opts.dashes ? ';border-top-style:dashed' : '') }));
      e.appendChild(document.createTextNode(opts.short ? short(id) : name(id)));
      if (opts.since) {
        const sn = opts.since(id);
        if (sn) e.appendChild(h('span', { class: 'ks', text: sn }));
      } else if (opts.meta) {
        const sn = since(opts.meta, id, opts.metric);
        if (sn) e.appendChild(h('span', { class: 'ks', text: sn }));
      }
      container.appendChild(e);
    });
    if (opts.note) container.appendChild(h('span', { class: 'kn', text: opts.note }));
  }

  // ------------------------------------------------------------- method note
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
     rules, the span line, then the sample line. A body item is either a line of prose, which
     may carry inline math, or {tex} for a display equation. The equations are
     the page's statement of what it measured, so they are set as mathematics
     rather than printed as code. */
  function methodNote(container, spec) {
    if (!container) return null;
    container.innerHTML = '';
    /* The note is for a reader who wants the arithmetic, so it waits behind
       a button. Whether it is open is kept on the container, so a tab change
       that redraws the note leaves it as the reader left it. */
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
      mathText(box.appendChild(h('div', { class: 'rule' })), item);
    });
    (spec.rules || []).forEach(r => mathText(box.appendChild(h('div', { class: 'rule' })), r));
    if (spec.span) box.appendChild(h('div', { class: 'rule span', text: String(spec.span) }));
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
  // (dayLine false for a lead that is not counted from the end of the day)
  function leadAxis(svg, g, x, hmax, hmin, label, dayLine) {
    hmin = hmin == null ? 0 : hmin;
    xAxis(svg, g, x, ticks(hmin, hmax, 6), v => v + 'h', label || 'Hours before the end of the target day');
    if (dayLine !== false && hmax >= 24 && hmin <= 24) {
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
  /* Every chart against lead on the page, drawn one way.

     Lead runs down to the right, so the day ends at the right edge and a
     curve is read the way the day is lived. A series marked smooth (the
     ForecastEx prediction market, whose prices move between hours) is drawn
     through the bin centers with its band; any other series is a step, one
     flat tread per hourly bin, because a forecast's value changes only when a
     new forecast arrives.
     Bins above 30 h are hatched because the sample there is partial.

     spec = { H, hs, series: [{ id, v, lo?, hi?, smooth?, dash?, opacity?, width? }],
              floor (least top), fmt, label, secondary?: { v, label } (a share on the right),
              dayLine?, hatch?, steps? (false draws plain lines),
              strips?: { n: [per h], beats: [per h {k, of}], ofLabel? },
              tip: (i, h) => html, name? (label the market's line), empty? }
     Returns { g, x, y }, or null when nothing was drawable. */
  function leadChart(svg, spec) {
    const hs = spec.hs || [];
    const H = spec.H || 380;
    if (!hs.length || !(spec.series || []).some(s => (s.v || []).some(fin))) {
      notYet(svg, spec.empty || 'This view is not in the published record.');
      return null;
    }
    const hmax = Math.max.apply(null, hs);
    const hmin = Math.min.apply(null, hs);
    const strips = spec.strips || null;
    const sec = spec.secondary && (spec.secondary.v || []).some(fin) ? spec.secondary : null;
    // a second axis on the right takes room from the frame for its labels
    const g = frame(H, Object.assign({ L: 70, T: 24, B: strips ? H - 118 : H - 58 }, sec ? { R: 884 } : {}));
    const x = scale(hmax + 0.5, hmin - 0.5, g.L, g.R);
    let top = 0;
    spec.series.forEach(s => (s.v || []).concat(s.hi || []).forEach(v => { if (fin(v) && v > top) top = v; }));
    top = Math.max(spec.floor || 0, top * 1.08);
    const step = niceStep(top, 5);
    const y = scale(0, top, g.B, g.T);
    clear(svg, H);
    const hid = 'hatch-' + (svg.id || 'lead');
    const defs = el('defs');
    const pat = el('pattern', { id: hid, patternUnits: 'userSpaceOnUse', width: 7, height: 7, patternTransform: 'rotate(45)' });
    pat.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 7, stroke: 'var(--rule)', 'stroke-width': 1.1, 'stroke-opacity': 0.55 }));
    defs.appendChild(pat);
    svg.appendChild(defs);
    yAxis(svg, g, y, ticks(0, top, step), spec.fmt, spec.label);
    if (spec.hatch !== false && hmax > 30) {
      svg.appendChild(el('rect', { x: x(hmax + 0.5), y: g.T, width: x(30.5) - x(hmax + 0.5), height: g.B - g.T,
                                   fill: 'url(#' + hid + ')', stroke: 'none', 'pointer-events': 'none' }));
      svg.appendChild(txt('partial sample above 30 h', { x: (x(hmax + 0.5) + x(30.5)) / 2, y: g.T + 12, 'text-anchor': 'middle', class: 'ax' }));
    }
    leadAxis(svg, g, x, hmax, hmin, spec.xLabel || 'Hours before the end of the target day', spec.dayLine);
    const px = (arr, f) => (arr || []).map(v => (fin(v) ? f(v) : null));
    const stepLineAt = (vals, attrs) => {
      const xs = [], ys = [];
      hs.forEach((hh, i) => {
        if (fin(vals[i])) { xs.push(x(hh + 0.5), x(hh - 0.5)); ys.push(y(vals[i]), y(vals[i])); }
        else { xs.push(null, null); ys.push(null, null); }
      });
      return lineSeries(svg, xs, ys, attrs);
    };
    const cx = hs.map(hh => x(hh));
    const smooth = spec.series.filter(s => s.smooth);
    smooth.forEach(s => { if (s.lo && s.hi && !fin(s.opacity)) band(svg, cx, px(s.lo, y), px(s.hi, y), color(s.id)); });
    /* An alternative is drawn as one step per bin where the bin is a clock
       hour, because its value holds until the next forecast is issued. Where
       the axis is not clock time (lead counted back from the extreme, which
       falls at a different hour on every city-day) a step claims a flatness
       the data does not have, so those series are drawn as plain lines. */
    spec.series.filter(s => !s.smooth).forEach(s => {
      const attrs = Object.assign({ stroke: color(s.id), 'stroke-width': s.width || width(s.id) },
                                  s.dash ? { 'stroke-dasharray': s.dash } : {},
                                  fin(s.opacity) ? { 'stroke-opacity': s.opacity } : {});
      if (spec.steps === false) lineSeries(svg, cx, px(s.v || [], y), attrs);
      else stepLineAt(s.v || [], attrs);
    });
    smooth.forEach(s => lineSeries(svg, cx, px(s.v, y), Object.assign({ stroke: color(s.id), 'stroke-width': s.width || width(s.id) },
                                                                    s.dash ? { 'stroke-dasharray': s.dash } : {},
                                                                    fin(s.opacity) ? { 'stroke-opacity': s.opacity } : {})));
    /* The second axis: a share from 0 to 100 percent on the right, drawn as a
       dashed ink line so it is never taken for a system. It carries context for
       the error curves (how much of the sample the observations had already
       decided, or how much of it reaches this far), not a score. */
    if (sec) {
      const ys = scale(0, 1, g.B, g.T);
      svg.appendChild(el('line', { x1: g.R, x2: g.R, y1: g.T, y2: g.B, stroke: 'var(--rule)', 'stroke-width': 1, 'pointer-events': 'none' }));
      [0, 0.25, 0.5, 0.75, 1].forEach(v => svg.appendChild(txt(Math.round(v * 100) + '%', { x: g.R + 6, y: ys(v) + 3.5, class: 'ax' })));
      if (sec.label) {
        const cy = (g.T + g.B) / 2;
        svg.appendChild(txt(sec.label, { x: W - 10, y: cy, 'text-anchor': 'middle', class: 'ax',
                                         transform: 'rotate(90 ' + (W - 10) + ' ' + cy + ')' }));
      }
      lineSeries(svg, cx, (sec.v || []).map(v => (fin(v) ? ys(v) : null)),
                 { stroke: 'var(--ink)', 'stroke-width': 1.4, 'stroke-dasharray': '5 4', class: 'acc-sec' });
    }
    if (spec.name && smooth.length) {
      const s = smooth[0], i0 = (s.v || []).findIndex(fin);
      if (i0 >= 0) label(svg, x(hs[i0]) + 4, y(s.v[i0]) + 14, short(s.id), color(s.id));
    }
    let bottom = g.B;
    if (strips) {
      const beats = strips.beats || [], nPer = strips.n || [];
      const of = beats.reduce((m, b) => (b && fin(b.of) ? Math.max(m, b.of) : m), 0);
      const ofVaries = beats.some(b => b && fin(b.of) && b.of > 0 && b.of !== of);
      const s1 = g.B + 48, s2 = s1 + 24, sh = 20;
      bottom = s2 + sh;
      svg.appendChild(txt('city-days', { x: g.L - 8, y: s1 + 13.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9.5 }));
      if (ofVaries) {
        svg.appendChild(txt('beats, of', { x: g.L - 8, y: s2 + 9, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
        svg.appendChild(txt(strips.ofLabel || 'scored', { x: g.L - 8, y: s2 + 18.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
      } else {
        svg.appendChild(txt('beats, of ' + of, { x: g.L - 8, y: s2 + 13.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9.5 }));
      }
      hs.forEach((hh, i) => {
        const x0 = x(hh + 0.5), w = x(hh - 0.5) - x0;
        const n = nPer[i], b = beats[i];
        svg.appendChild(el('rect', { x: x0, y: s1, width: w, height: sh, fill: 'var(--shade)', stroke: 'var(--panel)', 'stroke-width': 1, 'pointer-events': 'none' }));
        // a four-digit count is wider than a bin, so it prints in thousands; the hover has it exactly
        if (fin(n)) svg.appendChild(txt(n >= 1000 ? (Math.round(n / 100) / 10).toFixed(1) + 'k' : String(n), { x: x0 + w / 2, y: s1 + 13.5, 'text-anchor': 'middle', 'font-size': 8.5,
                                                     fill: n < 30 ? 'var(--muted)' : 'var(--ink)', 'pointer-events': 'none' }));
        // the beats cell darkens with the share of systems the market came in under
        const share = b && fin(b.k) && b.of ? b.k / b.of : null;
        svg.appendChild(el('rect', { x: x0, y: s2, width: w, height: sh, fill: share == null ? 'var(--shade)' : 'var(--accent)',
                                     'fill-opacity': share == null ? 1 : 0.08 + 0.42 * share, stroke: 'var(--panel)', 'stroke-width': 1, 'pointer-events': 'none' }));
        if (share != null) svg.appendChild(txt(String(b.k), { x: x0 + w / 2, y: s2 + 13.5, 'text-anchor': 'middle', 'font-size': 8.5,
                                                              fill: 'var(--ink)', 'pointer-events': 'none' }));
      });
    }
    // hover: one column per bin over the frame and any strips
    const guide = el('rect', { x: 0, y: g.T, width: 0, height: bottom - g.T, fill: 'var(--ink)', 'fill-opacity': 0.06,
                               stroke: 'none', 'pointer-events': 'none', visibility: 'hidden' });
    svg.appendChild(guide);
    hs.forEach((hh, i) => {
      const x0 = x(hh + 0.5), w = x(hh - 0.5) - x0;
      const r = el('rect', { x: x0, y: g.T, width: w, height: bottom - g.T, fill: 'transparent', stroke: 'none' });
      r.addEventListener('mouseenter', () => { guide.setAttribute('x', x0); guide.setAttribute('width', w); guide.setAttribute('visibility', 'visible'); });
      r.addEventListener('mouseleave', () => guide.setAttribute('visibility', 'hidden'));
      if (spec.tip) hover(r, () => spec.tip(i, hh));
      svg.appendChild(r);
    });
    return { g, x, y };
  }

  /* A standings table for one hour of a lead chart: every system ordered by
     its score there, best first, with how far each sits from ForecastEx as a
     share of ForecastEx's own score. rows = [{id, v, n}]; higherBetter flips
     the order and the sign for a score where more is better. */
  function rankTip(title, sub, rows, col, fmtv, opts) {
    opts = opts || {};
    const hb = !!opts.higherBetter;
    const fx = (rows.find(r => r.id === 'FX') || {}).v;
    const rel = v => (fin(v) && fin(fx) && fx > 0 ? 100 * (v - fx) / fx : null);
    const relTxt = v => (v == null ? dash : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(0) + '%');
    const key = r => (fin(r.v) ? (hb ? -r.v : r.v) : Infinity);
    const sorted = rows.slice().sort((a, b) => key(a) - key(b));
    let body = '';
    sorted.forEach((r, k) => {
      body += '<tr' + (r.id === 'FX' ? ' class="tfx"' : '') + '><td>' + (k + 1) + '</td><td>'
        + swatch(r.id).replace(name(r.id), short(r.id)) + '</td><td>' + fmtv(r.v) + '</td><td>'
        + (r.id === 'FX' || opts.noRel ? dash : relTxt(rel(r.v))) + '</td><td>' + int(r.n) + '</td></tr>';
    });
    return '<b>' + title + '</b><div class="tsub">' + sub + '</div>'
      + '<table class="l3"><tr><th>#</th><th>System</th><th>' + col + '</th><th>vs ForecastEx</th><th>n</th></tr>'
      + body + '</table>' + (opts.foot ? '<div class="tf">' + opts.foot + '</div>' : '');
  }
  const leadTitle = hh => (hh === 0 ? 'The hour the day ends' : hh + ' hour' + (hh === 1 ? '' : 's') + ' before the day ends');

  const NOT_PUBLISHED = 'This figure has not been published yet.';
  const REBUILDING = 'This figure is being rebuilt on the methods of the working paper and returns with the next daily update.';
  const unpublished = D => (D && D.rebuilding ? REBUILDING : NOT_PUBLISHED);

  // ------------------------------------------------------------- init
  const MODULES = [['WXAccLead', 'lead'], ['WXAccProb', 'prob'], ['WXAccMap', 'map'], ['WXAccGrid', 'grid']];
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

  /* The record column of the forecast-systems table at the foot of the page: each row names its
     series by id, and its cell gives the span that series is scored over. */
  function fillRecords(D) {
    const meta = D.meta;
    const fmt = sp => mdyY(sp.start) + (fin(sp.days) ? ', ' + int(sp.days) + ' days' : '');
    document.querySelectorAll('table.acc-sources td.rec').forEach(td => {
      const own = span(meta, td.getAttribute('data-id'));
      if (own && own.start) td.textContent = 'From ' + fmt(own);
    });
  }

  async function init() {
    tooltip();
    typeset(document);
    const keys = Object.keys(FILES);
    const got = await Promise.all(keys.map(k => load(FILES[k])));
    // the bundle every figure draws from
    const D = { results: {} };
    keys.forEach((k, i) => { D[k] = got[i].data; D.results[k] = got[i].r; });
    D.rebuilding = got.some(g => g.older);
    D.meta = newestMeta(D);
    setSeries(D.meta);
    const st = $('#pageStatus');
    if (st) { st.innerHTML = ''; st.appendChild(statusEl(D)); }
    fillRecords(D);
    MODULES.forEach(([g, k]) => {
      const mod = window[g];
      if (!mod || typeof mod.draw !== 'function') return;
      try { mod.draw(D); } catch (e) { console.error('accuracy figure ' + k + ' failed', e); }
    });
    return D;
  }

  return {
    init, ORDER: () => ORDER, INFO, systemGroups, color, width, dashOf, name, short, qual, swatch, swatchShort,
    f1, f2, f3, deg1, pct, pct1, int, iv, dash, windowAndBuilt,
    tabs, metricTabs, key, methodNote, tooltip, hover,
    mdy, mdyY, span, since, spanLine, cohortSpanLine,
    W, clear, scale, leadChart, rankTip, leadTitle, lineSeries, dots,
    notYet, NOT_PUBLISHED, unpublished, setSeries,
  };
})();
