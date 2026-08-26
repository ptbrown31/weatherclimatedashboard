/* The scorecard: where the forecasts disagreed, and where the day landed.

   The figure is one row per station on a shared axis of degrees from that
   station's consensus median (the median of the forecasts that exist for it).
   A shaded band spans the coldest to the warmest forecast, a dot marks each
   source, and for a day already observed a diamond marks what actually
   happened — ringed when it fell outside the band entirely, which is the case
   worth looking at.

   Four views. The two scored views (yesterday's highs and lows) rank the
   stations by how far the consensus finished from the observation, most wrong
   first. The two forward views have no observation yet and rank by spread,
   widest disagreement first.

   Data in: scorecard.json for the scored days (including the exchange's
   implied median for the day, which the pipeline reads from the last quote
   before local midnight), and summary.json plus the market summary through
   WXM for the forward views. Only the Fahrenheit stations appear: the axis is
   in degrees, and a degree Celsius is not a degree Fahrenheit.

   Below the figure, the accumulated skill tables: mean absolute error, bias
   and the share of days within one and two degrees, per source and station. */
window.WXScore = (() => {
  const { h, $, el, txt } = WXC;
  const NAME = { nws: 'NWS', nbm: 'NBM', mav: 'GFS MOS', lamp: 'LAMP' };
  const ORDER = ['nws', 'nbm', 'mav', 'lamp'];
  // the figure's five series; `fx` is the exchange's implied median, not a forecast product
  const SERIES = [
    { k: 'nws', name: 'National Weather Service', col: 'var(--nws)' },
    { k: 'nbm', name: 'Blend of Models', col: 'var(--nbm)' },
    { k: 'lamp', name: 'Aviation guidance (LAMP)', col: 'var(--lamp)' },
    { k: 'mav', name: 'GFS MOS', col: 'var(--mav)' },
    { k: 'fx', name: 'ForecastEx implied', col: 'var(--accent)' },
  ];
  const VIEWS = [
    { key: 'yhigh', when: 'past', side: 'high', label: 'Yesterday’s highs' },
    { key: 'ylow', when: 'past', side: 'low', label: 'Yesterday’s lows' },
    { key: 'thigh', when: 'next', side: 'high', label: 'Tomorrow’s highs' },
    { key: 'tlow', when: 'next', side: 'low', label: 'Tomorrow’s lows' },
  ];
  const MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

  // the view is addressable, because the daily letter links one of these four
  // directly and a link that lands on the wrong panel is worse than no link
  let S = null, SUM = null, cur = null, tip = null;
  let view = (VIEWS.find(v => v.key === WXC.param('view')) || VIEWS[0]).key;

  const fmt = v => (v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1));
  const pct = v => (v == null ? '—' : Math.round(v * 100) + '%');
  const signed = v => { if (v == null) return '—'; const r = Math.round(v * 10) / 10; return (r > 0 ? '+' : '') + fmt(r); };
  const degs = v => (v == null ? '—' : fmt(v) + '°');
  const off = v => (v == null ? '—' : (v > 0 ? '+' : '') + (Math.round(v * 10) / 10) + '°');
  function dayLabel(iso) {
    if (!iso) return '';
    const d = new Date(iso + 'T00:00:00Z');
    return isNaN(+d) ? iso : DOW[d.getUTCDay()] + ' ' + MON[d.getUTCMonth()].slice(0, 3) + ' ' + d.getUTCDate() + ', ' + d.getUTCFullYear();
  }
  // bias with its lean in words; the rounded value decides, so "+0.0" never reads warm
  function biasWord(v) {
    if (v == null) return '—';
    const r = Math.round(v * 10) / 10;
    return signed(v) + '°' + (r > 0 ? ' · runs warm' : r < 0 ? ' · runs cool' : '');
  }
  const median = xs => {
    const a = xs.slice().sort((p, q) => p - q), n = a.length;
    return n ? (n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2) : null;
  };
  const cycleText = c => {
    if (!c) return null;
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(c) || /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(c);
    return m ? MON[+m[2] - 1].slice(0, 3) + ' ' + (+m[3]) + ' ' + m[4] + ':' + m[5] + 'Z' : c;
  };

  // ---------------------------------------------------------------- the rows
  // One row per station: every source's value for the day, the median of them,
  // the band they span, and (for a scored day) what was observed.
  function rows(v) {
    const out = [];
    if (v.when === 'past') {
      if (!S || !S.stations) return { rows: out, day: null };
      // the day to draw is the newest one that has been scored anywhere; a
      // station whose record does not reach it is left out rather than mixed in
      let day = null;
      Object.values(S.stations).forEach(st => { const d = st.days && st.days[0]; if (d && (!day || d.date > day)) day = d.date; });
      Object.entries(S.stations).forEach(([sid, st]) => {
        const d = (st.days || []).find(x => x.date === day);
        if (!d) return;
        const vals = {}, meta = {};
        SERIES.forEach(s => {
          const src = d[s.k];
          if (src && src[v.side] != null) { vals[s.k] = src[v.side]; meta[s.k] = src; }
        });
        const actual = v.side === 'high' ? d.obsHigh : d.obsLow;
        out.push(build(sid, st.city, vals, meta, actual, { n: d.n, date: d.date }));
      });
      return { rows: out, day };
    }
    if (!SUM || !SUM.cities) return { rows: out, day: null };
    const suffix = v.side === 'high' ? 'HighTomorrow' : 'LowTomorrow';
    let day = null;
    SUM.cities.forEach(c => { if (c.unit === 'F' && c.markers && (!day || c.markers.tomorrow > day)) day = c.markers.tomorrow; });
    SUM.cities.filter(c => c.unit === 'F').forEach(c => {
      const vals = {}, meta = {};
      ORDER.forEach(k => { const x = c[k + suffix]; if (x != null) { vals[k] = x; meta[k] = { cycle: c[k + 'Cycle'] }; } });
      const m = WXM.on() ? WXM.implied(c) : null;
      const im = m && (v.side === 'high' ? m.impliedHigh : m.impliedLow);
      if (im != null) { vals.fx = im; meta.fx = { asof: m.asof, state: m.state }; }
      else if (m) meta.fx = { state: m.state };
      if (Object.keys(vals).length) out.push(build(c.station, c.city, vals, meta, null, { markers: c.markers }));
    });
    return { rows: out, day };
  }

  function build(sid, city, vals, meta, actual, extra) {
    const present = SERIES.filter(s => vals[s.k] != null);
    const nums = present.map(s => vals[s.k]);
    const consensus = median(nums);
    const lo = Math.min(...nums), hi = Math.max(...nums);
    const outside = actual != null && (actual < lo || actual > hi);
    return {
      sid, city, vals, meta, actual, consensus, lo, hi,
      spread: Math.round((hi - lo) * 10) / 10,
      err: actual == null ? null : Math.round((consensus - actual) * 10) / 10,
      missing: SERIES.filter(s => vals[s.k] == null).map(s => s.k),
      outside, escape: outside ? Math.round((actual < lo ? lo - actual : actual - hi) * 10) / 10 : null,
      extra: extra || {},
    };
  }

  // ---------------------------------------------------------------- tooltips
  function dotTip(r, s, v) {
    const m = r.meta[s.k] || {};
    const rowsOut = [
      ['Forecast ' + v.side, degs(r.vals[s.k])],
      ['From the consensus', off(r.vals[s.k] - r.consensus)],
      ['Consensus median', degs(r.consensus)],
    ];
    if (r.actual != null) rowsOut.push(['Error against the day', signed(r.vals[s.k] - r.actual) + '°']);
    if (m.cycle) rowsOut.push(['Cycle', cycleText(m.cycle)]);
    if (m.lead != null) rowsOut.push(['Lead', m.lead + ' h to local midnight']);
    if (s.k === 'fx' && m.asof) rowsOut.push(['Quotes as of', String(m.asof).replace('T', ' ').replace('Z', 'Z')]);
    const foot = s.k === 'fx'
      ? 'the strike where the Yes price crosses 50¢, from the exchange’s published bids'
      : (S && S.sources && S.sources[s.k]) || null;
    return tip.rows(r.city + ' (' + r.sid + ') — ' + s.name, rowsOut, foot);
  }
  function actualTip(r, v) {
    const rowsOut = [
      ['Observed ' + v.side, degs(r.actual)],
      ['From the consensus', off(r.actual - r.consensus)],
      ['Consensus median', degs(r.consensus)],
      ['Forecast range', degs(r.lo) + ' to ' + degs(r.hi)],
      ['METARs scored', r.extra.n],
    ];
    if (r.outside) rowsOut.push(['Outside the range by', r.escape + '° ' + (r.actual < r.lo ? 'colder than any forecast' : 'warmer than any forecast')]);
    return tip.rows(r.city + ' (' + r.sid + ') — what happened', rowsOut,
      r.outside ? 'every forecast missed on the same side' : 'inside the forecast range');
  }
  function rowTip(r, v) {
    const rowsOut = SERIES.filter(s => r.vals[s.k] != null).map(s =>
      ['<span class="sw" style="background:' + s.col + '"></span>' + s.name, degs(r.vals[s.k]) + '  (' + off(r.vals[s.k] - r.consensus) + ')']);
    rowsOut.push(['Consensus median', degs(r.consensus)]);
    rowsOut.push(['Spread', r.spread + '°']);
    if (r.actual != null) {
      rowsOut.push(['Observed', degs(r.actual)]);
      rowsOut.push(['Consensus error', signed(r.err) + '°' + (r.err > 0 ? ' · forecast too warm' : r.err < 0 ? ' · forecast too cold' : '')]);
    }
    r.missing.forEach(k => {
      const s = SERIES.find(x => x.k === k), m = r.meta[k] || {};
      const why = k !== 'fx' ? 'no forecast for this day'
        : v.when === 'past' ? 'not in the quote archive for this day'
        : m.state === 'unlisted' ? 'no market for this station'
        : m.state === 'unavailable' ? 'quotes unavailable'
        : 'not listed yet';
      rowsOut.push([s.name, why]);
    });
    return tip.rows(r.city + ' (' + r.sid + ')', rowsOut, v.when === 'past' ? 'click to pin · error = consensus minus observed' : 'click to pin');
  }

  // ---------------------------------------------------------------- the figure
  function drawFigure() {
    const v = VIEWS.find(x => x.key === view);
    const built = rows(v);
    const R = built.rows.slice();
    const host = $('#divergence'); host.innerHTML = '';
    const cap = $('#divCap');
    if (!R.length) {
      host.appendChild(h('p', { class: 'cap', text: v.when === 'past' ? 'No scored day yet.' : 'No forecasts for tomorrow yet.' }));
      cap.textContent = ''; $('#divTitle').textContent = ''; return;
    }
    R.sort(v.when === 'past'
      ? (a, b) => Math.abs(b.err) - Math.abs(a.err) || b.spread - a.spread
      : (a, b) => b.spread - a.spread || a.city.localeCompare(b.city));

    const W = 960, ROW = 30, L = 214, Rt = 792, T = 74;
    const H = T + R.length * ROW + 54;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, id: 'divsvg' });
    // a symmetric axis: the widest excursion on either side, rounded out to an even degree
    let dom = 2;
    R.forEach(r => {
      SERIES.forEach(s => { if (r.vals[s.k] != null) dom = Math.max(dom, Math.abs(r.vals[s.k] - r.consensus)); });
      if (r.actual != null) dom = Math.max(dom, Math.abs(r.actual - r.consensus));
    });
    dom = Math.ceil(dom / 2) * 2;
    const mid = (L + Rt) / 2, x = t => mid + (t / dom) * (Rt - L) / 2;

    // gridlines, zero line, axis
    for (let t = -dom; t <= dom; t += 2) {
      svg.appendChild(el('line', { x1: x(t), x2: x(t), y1: T - 12, y2: T + R.length * ROW, class: 'grid' }));
      svg.appendChild(txt((t > 0 ? '+' : '') + t + '°', { x: x(t), y: T + R.length * ROW + 20, 'text-anchor': 'middle', class: 'ax' }));
    }
    svg.appendChild(el('line', { x1: mid, x2: mid, y1: T - 26, y2: T + R.length * ROW, stroke: 'var(--rule)', 'stroke-width': 1.2 }));
    svg.appendChild(txt('consensus median', { x: mid, y: T - 32, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(txt('Degrees from the station’s consensus median (°F)', { x: mid, y: T + R.length * ROW + 42, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(txt(v.when === 'past' ? 'Consensus error' : 'Spread', { x: 900, y: T - 32, 'text-anchor': 'end', class: 'axl', 'font-weight': 700 }));

    R.forEach((r, i) => {
      const y = T + i * ROW + ROW / 2;
      const g = el('g');
      // the row's own hover target sits under everything, so a dot always wins
      const hit = el('rect', { x: 8, y: y - ROW / 2, width: W - 16, height: ROW, fill: 'transparent' });
      bind(hit, () => rowTip(r, v), true);
      g.appendChild(hit);
      if (i % 2) g.appendChild(el('rect', { x: 8, y: y - ROW / 2, width: W - 16, height: ROW, fill: 'var(--shade)', opacity: .5, 'pointer-events': 'none' }));
      // the band the forecasts span
      g.appendChild(el('rect', { x: x(r.lo - r.consensus), y: y - 8, width: Math.max(x(r.hi - r.consensus) - x(r.lo - r.consensus), 1.5), height: 16,
        fill: 'var(--rule)', 'fill-opacity': .34, 'pointer-events': 'none' }));
      // the station, red when the day escaped every forecast
      g.appendChild(bind(txt(r.city, { x: 196, y: y + 4, 'text-anchor': 'end', class: 'ax', 'font-size': 12.5,
        fill: r.outside ? 'var(--warm)' : 'var(--ink)', 'font-weight': r.outside ? 700 : 400 }), () => rowTip(r, v), true));
      // the dots, stacked vertically only where they would overlap
      const placed = [];
      SERIES.filter(s => r.vals[s.k] != null)
        .map(s => ({ s, px: x(r.vals[s.k] - r.consensus) }))
        .sort((a, b) => a.px - b.px)
        .forEach(d => {
          let lvl = 0;
          while (placed.some(p => p.lvl === lvl && Math.abs(p.px - d.px) < 13)) lvl++;
          placed.push({ px: d.px, lvl });
          const dy = [0, -9, 9, -18, 18][Math.min(lvl, 4)];
          const c = el('circle', { cx: d.px, cy: y + dy, r: 5, fill: d.s.col, stroke: 'var(--panel)', 'stroke-width': 1 });
          g.appendChild(bind(c, () => dotTip(r, d.s, v), true));
        });
      // what actually happened
      if (r.actual != null) {
        const ax = x(r.actual - r.consensus), s = r.outside ? 8 : 6.5;
        const dpath = 'M' + ax + ' ' + (y - s) + 'L' + (ax + s) + ' ' + y + 'L' + ax + ' ' + (y + s) + 'L' + (ax - s) + ' ' + y + 'Z';
        if (r.outside) g.appendChild(el('path', { d: dpath, fill: 'none', stroke: 'var(--warm)', 'stroke-width': 4, 'pointer-events': 'none' }));
        g.appendChild(bind(el('path', { d: dpath, fill: 'var(--obs)', stroke: 'var(--panel)', 'stroke-width': 1 }), () => actualTip(r, v), true));
      }
      // the ranking column
      const val = v.when === 'past' ? signed(r.err) + '°' : r.spread + '°';
      g.appendChild(bind(txt(val, { x: 900, y: y + 4, 'text-anchor': 'end', class: 'ax', 'font-weight': 700, 'font-size': 12.5,
        fill: v.when === 'past' && Math.abs(r.err) >= 3 ? 'var(--warm)' : 'var(--muted)' }), () => rowTip(r, v), true));
      svg.appendChild(g);
    });
    host.appendChild(svg);

    // title, legend and the notes the figure needs to be read honestly
    const label = dayLabel(built.day);
    $('#divTitle').textContent = (v.when === 'past' ? 'What happened, ' : 'Where the forecasts disagree, ') + label +
      ' — ' + (v.side === 'high' ? 'daily highs' : 'daily lows');
    const key = $('#divKey'); key.innerHTML = '';
    SERIES.forEach(s => key.appendChild(h('span', {}, [h('i', { style: 'background:' + s.col + ';border-color:' + s.col }), document.createTextNode(s.name)])));
    if (v.when === 'past') {
      key.appendChild(h('span', {}, [h('i', { style: 'background:var(--obs);border-color:var(--obs)' }), document.createTextNode('What happened')]));
      key.appendChild(h('span', {}, [h('i', { style: 'background:var(--obs);border-color:var(--warm);border-width:3px' }), document.createTextNode('Outside every forecast')]));
    }
    const gone = {};
    R.forEach(r => r.missing.forEach(k => { gone[k] = (gone[k] || 0) + 1; }));
    const esc = R.filter(r => r.outside);
    const notes = [];
    notes.push(v.when === 'past'
      ? 'Rows are ranked by how far the consensus median finished from the observation, widest first; the column on the right is that error, signed, with a positive value meaning the forecasts were too warm.'
      : 'Rows are ranked by spread, the coldest forecast to the warmest, widest first. Nothing has been observed yet.');
    Object.entries(gone).forEach(([k, n]) => {
      const s = SERIES.find(x => x.k === k);
      notes.push(s.name + ' is absent for ' + n + ' of ' + R.length + ' stations' +
        (k === 'lamp' ? ', whose horizon is 25 hours and rarely reaches a following day'
         : k === 'fx' ? (v.when === 'past' ? ', for days before the quote archive began' : ', where the exchange has not listed that day yet')
         : '') + '.');
    });
    if (v.when === 'past' && esc.length) {
      const cold = esc.filter(r => r.actual < r.lo), warm = esc.filter(r => r.actual > r.hi);
      notes.push(esc.length + ' station' + (esc.length > 1 ? 's' : '') + ' finished outside every forecast: ' +
        esc.map(r => r.city + ' ' + r.escape + '° ' + (r.actual < r.lo ? 'colder' : 'warmer')).join(', ') + '.' +
        (cold.length && !warm.length ? ' All of them were cold.' : warm.length && !cold.length ? ' All of them were warm.' : ''));
    }
    cap.textContent = notes.join(' ');
  }

  // hover, leave, and a pinning click
  function bind(node, html, pin) {
    node.addEventListener('mousemove', e => { e.stopPropagation(); tip.show(e, html()); });
    node.addEventListener('mouseleave', () => tip.hide());
    if (pin) { node.addEventListener('click', e => { e.stopPropagation(); tip.pin(e, html()); }); node.setAttribute('data-tip-pin', '1'); }
    return node;
  }

  // ---------------------------------------------------------- the standings
  // How each tool has done over the last few scored days, ranked by mean
  // absolute error on the daily high. The exchange's implied median is ranked
  // alongside the forecasts here, because the question the table answers is
  // which of them has been closest, and that is arithmetic on published numbers.
  const WINDOW = 7;
  // The standings are a matched sample: only station-days where every tool has
  // a value count, and each side is matched on its own.
  //
  // Without that the table compares tools over different days. Each archive
  // lane started on a different date — the NWS and Blend lanes from the
  // beginning, GFS MOS nine days later, LAMP ten, the exchange twelve — so
  // pooling every error each tool happens to have scored the NWS over twelve
  // days and LAMP over three and then ranked them against each other. Nothing
  // was wrong with any single number; they were answers to different questions.
  function standings() {
    if (!S || !S.stations) return { rows: [], days: 0, from: null, to: null, dropped: 0 };
    const acc = {}, seen = {};
    SERIES.forEach(x => { acc[x.k] = { high: [], low: [] }; });
    let dropped = 0;
    Object.values(S.stations).forEach(st => {
      (st.days || []).slice(0, WINDOW).forEach(d => {
        const okHigh = SERIES.every(x => (d[x.k] || {}).errHigh != null);
        const okLow = SERIES.every(x => (d[x.k] || {}).errLow != null);
        if (!okHigh && !okLow) { dropped++; return; }
        seen[d.date] = 1;
        SERIES.forEach(x => {
          const f = d[x.k]; if (!f) return;
          if (okHigh && f.errHigh != null) acc[x.k].high.push(f.errHigh);
          if (okLow && f.errLow != null) acc[x.k].low.push(f.errLow);
        });
      });
    });
    const stat = e => (e.length ? { n: e.length, mae: e.reduce((a, v) => a + Math.abs(v), 0) / e.length,
                                    bias: e.reduce((a, v) => a + v, 0) / e.length,
                                    within1: e.filter(v => Math.abs(v) <= 1).length / e.length,
                                    within2: e.filter(v => Math.abs(v) <= 2).length / e.length } : null);
    const rowsOut = SERIES.map(x => ({ s: x, high: stat(acc[x.k].high), low: stat(acc[x.k].low) }))
      .filter(r => r.high || r.low)
      .sort((a, b) => (a.high ? a.high.mae : 99) - (b.high ? b.high.mae : 99));
    const dates = Object.keys(seen).sort();
    return { rows: rowsOut, days: dates.length, from: dates[0], to: dates[dates.length - 1], dropped: dropped };
  }
  function drawStandings() {
    const host = $('#standings'); if (!host) return;
    host.innerHTML = '';
    const st = standings();
    if (!st.rows.length) { host.appendChild(h('p', { class: 'cap', text: 'No scored days yet.' })); return; }
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: '' }), h('th', { text: 'Tool' }), h('th', { class: 'num', text: 'MAE, highs' }),
      h('th', { class: 'num', text: 'bias' }), h('th', { class: 'num', text: '≤2°' }), h('th', { class: 'num', text: 'n' }),
      h('th', { class: 'num', text: 'MAE, lows' })]));
    st.rows.forEach((r, i) => {
      const tr = h('tr', {}, [
        h('td', { class: 'num', text: r.high ? String(i + 1) : '—' }),
        h('td', {}, [h('span', { class: 'sw', style: 'background:' + r.s.col }), document.createTextNode(r.s.name)]),
        h('td', { class: 'num', text: r.high ? degs(r.high.mae) : '—' }),
        h('td', { class: 'num', text: r.high ? biasWord(r.high.bias).split(' ·')[0] : '—' }),
        h('td', { class: 'num', text: r.high ? pct(r.high.within2) : '—' }),
        h('td', { class: 'num', text: r.high ? String(r.high.n) : '—' }),
        h('td', { class: 'num', text: r.low ? degs(r.low.mae) : '—' })]);
      tr.dataset.key = r.s.k;
      t.appendChild(tr);
    });
    bindTips(t, (k) => {
      const r = st.rows.find(x => x.s.k === k); if (!r) return null;
      return tip.rows(r.s.name + ' — the last ' + st.days + ' scored days',
        (r.high ? statRows(r.high) : []).concat(r.low ? statRows(r.low, 'daily low ') : []),
        r.s.k === 'fx' ? 'the exchange’s implied median, scored the same way as a forecast' : (S.sources || {})[r.s.k]);
    });
    host.appendChild(h('div', { class: 'card', style: 'padding:0' }, [t]));
    const n = st.rows[0] && st.rows[0].high ? st.rows[0].high.n : 0;
    host.appendChild(h('p', { class: 'cap', text: 'Matched sample: only station-days where every tool has a value are '
      + 'counted, so n is the same for all of them — ' + n + ' station-days across ' + st.days + ' day'
      + (st.days === 1 ? '' : 's') + '. Each archive lane started on a different date, so pooling every error a tool '
      + 'happens to have would score some tools over far more days than others and then rank them against each other. '
      + 'The window grows by a day every day.' }));
    const lead = st.rows[0];
    // the sources are not archived equally deeply, so the ranking is not
    // like-for-like and the caption has to say so rather than let n speak alone
    const ns = st.rows.filter(r => r.high).map(r => r.high.n);
    const uneven = ns.length > 1 && Math.max(...ns) >= 2 * Math.min(...ns);
    host.appendChild(h('p', { class: 'cap', text: 'Ranked by mean absolute error on the daily high over the ' + st.days +
      ' scored days from ' + st.from + ' to ' + st.to + ', pooled across every station. ' +
      (lead && lead.high ? lead.s.name + ' is closest at ' + degs(lead.high.mae) + '. ' : '') +
      'Error is forecast minus observed, so a positive bias runs warm. ' +
      (uneven ? 'The sources are not scored on the same days: the archive holds fewer cycles for some of them, so n differs by source and the ranking is not like-for-like. ' +
        st.rows.filter(r => r.high).map(r => r.s.name + ' ' + r.high.n).join(', ') + '.' : '') }));
  }

  // ------------------------------------------------------- the skill tables
  const statRows = (st, prefix = '') => (st ? [
    [prefix + 'n', st.n], [prefix + 'MAE', degs(st.mae)], [prefix + 'bias', biasWord(st.bias)],
    [prefix + 'within 1°', pct(st.within1)], [prefix + 'within 2°', pct(st.within2)],
  ] : []);
  function cell(stat) {
    if (!stat) return [h('td', { class: 'num', text: '—' }), h('td', { class: 'num', text: '—' }), h('td', { class: 'num', text: '—' }), h('td', { class: 'num', text: '—' })];
    return [h('td', { class: 'num', text: String(stat.n) }), h('td', { class: 'num', text: fmt(stat.mae) }),
            h('td', { class: 'num', text: signed(stat.bias) }), h('td', { class: 'num', text: pct(stat.within2) })];
  }
  function table(title, rowsIn) {
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: title }), ...ORDER.flatMap(s => [h('th', { class: 'num', text: NAME[s] + ' n' }), h('th', { class: 'num', text: 'MAE' }), h('th', { class: 'num', text: 'bias' }), h('th', { class: 'num', text: '≤2°' })])]));
    rowsIn.forEach(([label, bySource, onclick, key]) => {
      const tr = h('tr', {}, [h('td', { text: label }), ...ORDER.flatMap(s => cell(bySource[s]))]);
      if (onclick) { tr.style.cursor = 'pointer'; tr.onclick = onclick; }
      if (key) tr.dataset.key = key;
      t.appendChild(tr);
    });
    return t;
  }
  // one delegated listener per table: the cell's column says which source it belongs to
  function bindTips(t, tipFor) {
    t.setAttribute('data-tip-pin', '1');
    t.addEventListener('mousemove', e => {
      const td = e.target.closest && e.target.closest('td');
      if (!td || !td.parentNode.dataset.key) return tip.hide();
      const i = [...td.parentNode.children].indexOf(td);
      if (i < 1) return tip.hide();
      const html = tipFor(td.parentNode.dataset.key, ORDER[Math.floor((i - 1) / 4)]);
      if (html) tip.show(e, html); else tip.hide();
    });
    t.addEventListener('mouseleave', () => tip.hide());
  }

  function drawOverall() {
    const host = $('#overall'); host.innerHTML = '';
    const hi = {}, lo = {};
    ORDER.forEach(s => { const o = S.overall[s] || {}; hi[s] = o.high; lo[s] = o.low; });
    const t = table('All stations', [['Daily high', hi, null, 'high'], ['Daily low', lo, null, 'low']]);
    bindTips(t, (side, src) => {
      const st = (S.overall[src] || {})[side];
      if (!st) return null;
      return tip.rows(NAME[src] + ' — daily ' + side + ', all stations', statRows(st), (S.sources || {})[src]);
    });
    host.appendChild(t);
  }
  function drawStations() {
    const host = $('#stations'); host.innerHTML = '';
    const rowsIn = Object.entries(S.stations).sort((a, b) => a[1].city.localeCompare(b[1].city)).map(([sid, st]) => {
      const hi = {}; ORDER.forEach(s => { hi[s] = (st.summary[s] || {}).high; });
      return [st.city + ' (' + sid + ')', hi, () => { cur = sid; drawDays(); location.hash = sid; }, sid];
    });
    const t = table('Daily high, by station', rowsIn);
    bindTips(t, (sid, src) => {
      const st = S.stations[sid], sm = st && (st.summary[src] || {});
      if (!st || !sm.high) return null;
      return tip.rows(st.city + ' (' + sid + ') — ' + NAME[src] + ' daily high',
        statRows(sm.high).concat(statRows(sm.low, 'daily low ')), 'click → the station’s scored days');
    });
    host.appendChild(t);
  }
  function drawDays() {
    const host = $('#days'); host.innerHTML = '';
    const st = S.stations[cur]; if (!st) return;
    host.appendChild(h('div', { class: 'secttl', text: st.city.toUpperCase() + ' · the last ' + st.days.length + ' scored days (' + st.unit + ')' }));
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: 'Day' }), h('th', { class: 'num', text: 'Observed high' }), h('th', { class: 'num', text: 'low' }),
      ...ORDER.flatMap(s => [h('th', { class: 'num', text: NAME[s] + ' high' }), h('th', { class: 'num', text: 'err' }), h('th', { class: 'num', text: 'low' }), h('th', { class: 'num', text: 'err' })])]));
    st.days.forEach((d, i) => {
      const tr = h('tr', {}, [h('td', { text: d.date }), h('td', { class: 'num', text: fmt(d.obsHigh) }), h('td', { class: 'num', text: fmt(d.obsLow) }),
        ...ORDER.flatMap(s => { const f = d[s] || {}; return [h('td', { class: 'num', text: fmt(f.high) }), h('td', { class: 'num', text: signed(f.errHigh) }), h('td', { class: 'num', text: fmt(f.low) }), h('td', { class: 'num', text: signed(f.errLow) })]; })]);
      tr.dataset.key = String(i);
      t.appendChild(tr);
    });
    bindTips(t, (idx, src) => {
      const d = st.days[+idx]; if (!d) return null;
      const f = d[src];
      if (!f) return tip.rows(st.city + ' — ' + d.date, [['Observed high', degs(d.obsHigh)], ['Observed low', degs(d.obsLow)], ['METARs scored', d.n]],
        'no ' + NAME[src] + ' forecast archived for this day');
      return tip.rows(st.city + ' — ' + d.date + ' — ' + NAME[src], [
        ['Forecast high / observed / error', degs(f.high) + ' / ' + degs(d.obsHigh) + ' / ' + signed(f.errHigh)],
        ['Forecast low / observed / error', degs(f.low) + ' / ' + degs(d.obsLow) + ' / ' + signed(f.errLow)],
        ['Cycle', cycleText(f.cycle)], ['Lead', f.lead != null ? f.lead + ' h to local midnight' : null], ['METARs scored', d.n],
      ], (S.sources || {})[src]);
    });
    host.appendChild(t);
    host.appendChild(h('p', { class: 'cap', text: 'Forecasts are the cycle each source issued before local midnight; lead is hours from issuance to midnight. Error is forecast minus observed. Hover a cell for the cycle behind it.' }));
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('scorecard.json', 1440);
    const sres = await WXD.get('summary.json');
    await WXM.loadSummary();
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440));
    S = r.data; SUM = sres.data;
    const btns = {};
    VIEWS.forEach(v => {
      const b = h('button', { class: 'vbtn' + (v.key === view ? ' on' : ''), text: v.label });
      b.onclick = () => {
        view = v.key;
        Object.values(btns).forEach(x => x.classList.remove('on'));
        b.classList.add('on');
        // keep the address bar on the panel being read, so a copied link reopens it
        const u = new URL(location.href); u.searchParams.set('view', v.key);
        history.replaceState(null, '', u);
        drawFigure();
      };
      btns[v.key] = b;
      $('#divControls').appendChild(b);
    });
    if (!S || !S.stations) {
      $('#overall').textContent = 'No scorecard available yet.';
      $('#divergence').textContent = '';
      // with no scored day yet only the forward views have anything to draw
      if (SUM) {
        if (view !== 'thigh' && view !== 'tlow') view = 'thigh';
        Object.entries(btns).forEach(([k, b]) => b.classList.toggle('on', k === view));
        drawFigure();
      }
      return;
    }
    $('#since').textContent = 'Scored from ' + S.firstDay + ' (the day the archive started). ' + S.method + '.';
    drawFigure(); drawStandings(); drawOverall(); drawStations();
    cur = (location.hash || '').slice(1);
    if (!S.stations[cur]) cur = Object.keys(S.stations)[0];
    drawDays();
  }
  return { init };
})();
