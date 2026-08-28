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
      // a scored day is measured against what happened; a day still ahead has
      // nothing to measure against, so it is measured against the forecasts'
      // own middle
      centre: actual != null ? actual : consensus,
      centreName: actual != null ? 'observed' : 'consensus median',
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
      ['From the ' + r.centreName, off(r.vals[s.k] - r.centre)],
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
      ['<span class="sw" style="background:' + s.col + '"></span>' + s.name, degs(r.vals[s.k]) + '  (' + off(r.vals[s.k] - r.centre) + ')']);
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
    const host = $('#divergence'); if (!host) return;
    const v = VIEWS.find(x => x.key === view);
    const built = rows(v);
    const R = built.rows.slice();
    host.innerHTML = '';
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
      SERIES.forEach(s => { if (r.vals[s.k] != null) dom = Math.max(dom, Math.abs(r.vals[s.k] - r.centre)); });
      if (r.actual != null) dom = Math.max(dom, Math.abs(r.actual - r.centre));
      dom = Math.max(dom, Math.abs(r.consensus - r.centre));
    });
    dom = Math.ceil(dom / 2) * 2;
    const mid = (L + Rt) / 2, x = t => mid + (t / dom) * (Rt - L) / 2;

    // gridlines, zero line, axis
    for (let t = -dom; t <= dom; t += 2) {
      svg.appendChild(el('line', { x1: x(t), x2: x(t), y1: T - 12, y2: T + R.length * ROW, class: 'grid' }));
      svg.appendChild(txt((t > 0 ? '+' : '') + t + '°', { x: x(t), y: T + R.length * ROW + 20, 'text-anchor': 'middle', class: 'ax' }));
    }
    svg.appendChild(el('line', { x1: mid, x2: mid, y1: T - 26, y2: T + R.length * ROW, stroke: 'var(--rule)', 'stroke-width': 1.2 }));
    const cname = v.when === 'past' ? 'observed' : 'consensus median';
    svg.appendChild(txt(cname, { x: mid, y: T - 32, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(txt('Degrees from the station’s ' + cname + ' (°F)', { x: mid, y: T + R.length * ROW + 42, 'text-anchor': 'middle', class: 'axl' }));
    svg.appendChild(txt(v.when === 'past' ? 'Consensus error' : 'Spread', { x: 900, y: T - 32, 'text-anchor': 'end', class: 'axl', 'font-weight': 700 }));

    let labelledEscape = false;
    R.forEach((r, i) => {
      const y = T + i * ROW + ROW / 2;
      const g = el('g');
      // the row's own hover target sits under everything, so a dot always wins
      const hit = el('rect', { x: 8, y: y - ROW / 2, width: W - 16, height: ROW, fill: 'transparent' });
      bind(hit, () => rowTip(r, v), true);
      g.appendChild(hit);
      if (i % 2) g.appendChild(el('rect', { x: 8, y: y - ROW / 2, width: W - 16, height: ROW, fill: 'var(--shade)', opacity: .5, 'pointer-events': 'none' }));
      // the band the forecasts span
      g.appendChild(el('rect', { x: x(r.lo - r.centre), y: y - 8, width: Math.max(x(r.hi - r.centre) - x(r.lo - r.centre), 1.5), height: 16,
        fill: 'var(--rule)', 'fill-opacity': .34, 'pointer-events': 'none' }));
      // the station, red when the day escaped every forecast
      g.appendChild(bind(txt(r.city, { x: 196, y: y + 4, 'text-anchor': 'end', class: 'ax', 'font-size': 12.5,
        fill: r.outside ? 'var(--warm)' : 'var(--ink)', 'font-weight': r.outside ? 700 : 400 }), () => rowTip(r, v), true));
      // the dots, stacked vertically only where they would overlap
      const placed = [];
      SERIES.filter(s => r.vals[s.k] != null)
        .map(s => ({ s, px: x(r.vals[s.k] - r.centre) }))
        .sort((a, b) => a.px - b.px)
        .forEach(d => {
          let lvl = 0;
          while (placed.some(p => p.lvl === lvl && Math.abs(p.px - d.px) < 13)) lvl++;
          placed.push({ px: d.px, lvl });
          const dy = [0, -9, 9, -18, 18][Math.min(lvl, 4)];
          const c = el('circle', { cx: d.px, cy: y + dy, r: 5, fill: d.s.col, stroke: 'var(--panel)', 'stroke-width': 1 });
          g.appendChild(bind(c, () => dotTip(r, d.s, v), true));
          /* The first row names every source on its own dot.

             The names were swatches under the figure, which meant carrying a
             colour in mind down to the chart. Only the top row is labelled;
             the same colour then means the same source all the way down. */
          if (i === 0) {
            g.appendChild(txt(d.s.name, { x: d.px, y: y + dy - 9, 'text-anchor': 'middle', 'font-size': 9,
                                          'font-weight': 700, fill: d.s.col, 'pointer-events': 'none' }));
          }
        });
      // what the station recorded
      if (r.actual != null) {
        const ax = x(r.actual - r.centre), s = r.outside ? 8 : 6.5;
        if (i === 0) g.appendChild(txt('observed', { x: ax, y: y - s - 5, 'text-anchor': 'middle', 'font-size': 9,
                                       'font-weight': 700, fill: 'var(--obs)', 'pointer-events': 'none' }));
        if (r.outside && !labelledEscape) {
          labelledEscape = true;
          g.appendChild(txt('outside every forecast', { x: ax, y: y + s + 11, 'text-anchor': 'middle', 'font-size': 9,
                            'font-weight': 700, fill: 'var(--warm)', 'pointer-events': 'none' }));
        }
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
    $('#divTitle').textContent = (v.side === 'high' ? 'Daily highs, ' : 'Daily lows, ') + label +
      (v.when === 'past' ? ', against what the stations recorded' : ', where the forecasts disagree');
    // every mark names itself on the top row of the figure
    const key = $('#divKey'); if (key) key.innerHTML = '';
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
  /* The standings, as ranked bars.

     This was a table of five columns and it read as a table: the reader had to
     compare numbers down a column to see who was ahead. The daily letter draws
     the same figures as horizontal bars ranked best first, which answers "who
     is closest" before anything is read, and this is that figure. The other
     columns did not vanish — bias, the share within two degrees, the sample
     size — they moved into the box a bar hands over on hover. */
  function drawStandings() {
    const host = $('#standings'); if (!host) return;
    host.innerHTML = '';
    const st = standings();
    const rows = st.rows.filter(r => r.high);
    if (!rows.length) { host.appendChild(h('p', { class: 'cap', text: 'No scored days yet.' })); return; }

    const W = 960, rowH = 34, T = 16, B = 44, L = 232, R = 906;
    const H = T + rows.length * rowH + B;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts', id: 'standChart' });
    const max = Math.max(...rows.map(r => r.high.mae));
    // a round upper bound, so the ticks land on readable numbers
    const step = max > 6 ? 2 : max > 3 ? 1 : 0.5;
    const top = Math.ceil(max / step) * step + step * 0.5;
    const x = v => L + (v / top) * (R - L);

    for (let v = 0; v <= top + 1e-9; v += step) {
      svg.appendChild(el('line', { x1: x(v), x2: x(v), y1: T, y2: T + rows.length * rowH, class: 'grid' }));
      svg.appendChild(txt(fmt(v), { x: x(v), y: T + rows.length * rowH + 16, 'text-anchor': 'middle', class: 'ax' }));
    }
    svg.appendChild(txt('Mean absolute error on the daily high, °F', { x: (L + R) / 2, y: H - 8,
                                                                      'text-anchor': 'middle', class: 'ax' }));
    /* Competition ranking on the printed numbers, so a tie reads as a tie.
       Ranking by sort position said "second" about a dead heat. */
    const shown = r => Math.round(r.high.mae * 100) / 100;
    rows.forEach((r, i) => {
      const y0 = T + i * rowH;
      const rank = 1 + rows.filter(o => shown(o) < shown(r)).length;
      const bh = 19, by = y0 + (rowH - bh) / 2;
      const isFx = r.s.k === 'fx';
      svg.appendChild(txt(rank + '.', { x: 26, y: by + 14, class: 'ax', 'font-weight': 700,
                                        fill: isFx ? 'var(--accent)' : 'var(--muted)' }));
      svg.appendChild(txt(r.s.name, { x: 46, y: by + 14, 'font-size': 12,
                                      'font-weight': isFx ? 700 : 400,
                                      fill: isFx ? 'var(--accent)' : 'var(--ink)' }));
      const bar = el('rect', { x: L, y: by, width: Math.max(x(r.high.mae) - L, 1), height: bh, rx: 2,
                               fill: isFx ? 'var(--accent)' : r.s.col, opacity: isFx ? 0.95 : 0.62 });
      bar.dataset.key = r.s.k;
      svg.appendChild(bar);
      svg.appendChild(txt(degs(r.high.mae), { x: x(r.high.mae) + 6, y: by + 14, 'font-size': 11,
                                              'font-weight': 600, fill: 'var(--ink)', 'pointer-events': 'none' }));
    });
    // one listener for the figure, keyed by the bar under the pointer
    svg.addEventListener('mousemove', e => {
      const k = e.target && e.target.dataset && e.target.dataset.key;
      if (!k) return tip.hide();
      const r = rows.find(x2 => x2.s.k === k); if (!r) return tip.hide();
      tip.show(e, tip.rows(r.s.name + ' — the last ' + st.days + ' scored days',
        statRows(r.high).concat(r.low ? statRows(r.low, 'daily low ') : []),
        r.s.k === 'fx' ? 'the exchange’s implied median, scored the same way as a forecast' : (S.sources || {})[r.s.k]));
    });
    svg.addEventListener('mouseleave', () => tip.hide());
    host.appendChild(h('div', { class: 'card' }, [svg]));

    const n = rows[0].high.n;
    const lead = rows[0];
    const ns = rows.map(r => r.high.n);
    const uneven = ns.length > 1 && Math.max(...ns) >= 2 * Math.min(...ns);
    host.appendChild(h('p', { class: 'cap', text: 'Ranked by mean absolute error on the daily high over the ' + st.days
      + ' scored day' + (st.days === 1 ? '' : 's') + ' from ' + st.from + ' to ' + st.to + ', pooled across every station. '
      + lead.s.name + ' is closest at ' + degs(lead.high.mae) + '. Matched sample: only station-days where every tool has '
      + 'a value are counted, so n is the same for all of them — ' + n + ' station-days. Each archive lane started on a '
      + 'different date, so pooling every error a tool happens to have would score some tools over far more days than '
      + 'others and then rank them against each other. Hover a bar for its bias, its share within two degrees, and the '
      + 'same figures on the daily low. The window grows by a day every day.'
      + (uneven ? ' The sources are not scored on the same days: ' + rows.map(r => r.s.name + ' ' + r.high.n).join(', ') + '.' : '') }));
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
      return [st.city + ' (' + sid + ')', hi, () => {
        if ($('#days')) { cur = sid; drawDays(); location.hash = sid; }
        else location.href = 'city.html?station=' + encodeURIComponent(sid);
      }, sid];
    });
    const t = table('Daily high, by station', rowsIn);
    bindTips(t, (sid, src) => {
      const st = S.stations[sid], sm = st && (st.summary[src] || {});
      if (!st || !sm.high) return null;
      return tip.rows(st.city + ' (' + sid + ') — ' + NAME[src] + ' daily high',
        statRows(sm.high).concat(statRows(sm.low, 'daily low ')), ($('#days') ? 'click → this station’s scored days' : 'click → this station’s page'));
    });
    host.appendChild(t);
  }
  function drawDays() {
    const host = $('#days'); if (!host) return;
    host.innerHTML = '';
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
    // on the daily temperatures page the map owns the status strip, so the
    // scorecard writes into its own when there is one and stays quiet otherwise
    const st = $('#scoreStatus') || $('#pageStatus');
    if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440)); }
    S = r.data; SUM = sres.data;
    const btns = {};
    // the accuracy page carries the standings alone; the view buttons and the
    // divergence figure belong to the daily temperatures page
    if ($('#divControls')) VIEWS.forEach(v => {
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
      const ov = $('#overall'); if (ov) ov.textContent = 'No scorecard available yet.';
      const sd = $('#standings'); if (sd) sd.textContent = 'No scorecard available yet.';
      const dv = $('#divergence'); if (dv) dv.textContent = '';
      // with no scored day yet only the forward views have anything to draw
      if (SUM) {
        if (view !== 'thigh' && view !== 'tlow') view = 'thigh';
        Object.entries(btns).forEach(([k, b]) => b.classList.toggle('on', k === view));
        drawFigure();
      }
      return;
    }
    const since = $('#since');
    if (since) since.textContent = 'Scored from ' + S.firstDay + ' (the day the archive started). ' + S.method + '.';
    // the divergence figure and the standings sit on different pages now, and
    // the skill tables were dropped; each draws only where it has a host
    drawFigure(); drawStandings();
    if ($('#overall')) drawOverall();
    if ($('#stations')) drawStations();
    cur = (location.hash || '').slice(1);
    if (!S.stations[cur]) cur = Object.keys(S.stations)[0];
    drawDays();
  }
  return { init };
})();
