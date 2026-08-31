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
  // the grid's own state: which scored day, and which end of it
  let gridDate = WXC.param('day') || null;
  let gridSide = (WXC.param('side') === 'low') ? 'low' : 'high';

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
  /* The scorecard grid.

     One row per station, one column per system, each cell carrying the forecast
     and its error against what the station recorded. The colour is the error, so
     a column that ran warm reads as a red stripe down the grid and a station
     nobody caught reads as a red row. The margins are means of absolute error,
     the right column per station and the bottom row per system.

     This is the figure the daily letter publishes, drawn from the site's own
     archive so a reader can move the day rather than wait for the next letter.

     The market is drawn beside the forecasts but left out of both margins. It
     is a price, not a forecast product, and averaging it into a forecast skill
     figure would answer a different question from the one the margins ask. */
  const SHORT_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const shortDay = iso => DOW[new Date(iso + 'T12:00:00Z').getUTCDay()].slice(0, 3) + ' '
    + SHORT_MON[+iso.slice(5, 7) - 1] + ' ' + (+iso.slice(8, 10));

  const GRID = ORDER.concat(['fx']);
  const GRID_NAME = { nws: 'National\nWeather\nService', nbm: 'Blend of\nModels', lamp: 'Aviation\nguidance\n(LAMP)',
                      mav: 'GFS\nMOS', fx: 'ForecastEx\nimplied' };
  const ERR_CAP = 6;                     // the colour saturates here, in degrees

  function errFill(e) {
    if (e == null) return 'var(--shade)';
    const t = Math.max(-1, Math.min(1, e / ERR_CAP)), a = Math.abs(t);
    const c = t >= 0 ? [178, 24, 43] : [33, 102, 172];
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (0.10 + 0.72 * a).toFixed(3) + ')';
  }

  /* The scored days this station set has, newest first. */
  function scoredDays() {
    const seen = {};
    Object.values(S.stations || {}).forEach(st => (st.days || []).forEach(d => { seen[d.date] = 1; }));
    return Object.keys(seen).sort().reverse().slice(0, 7);
  }

  function gridRows(date, side) {
    const hi = side === 'high';
    const out = [];
    Object.entries(S.stations || {}).forEach(([sid, st]) => {
      const d = (st.days || []).find(q => q.date === date);
      if (!d) return;
      const obs = hi ? d.obsHigh : d.obsLow;
      if (obs == null) return;
      const vals = {}, errs = [];
      GRID.forEach(k => {
        const f = d[k];
        if (!f) return;
        const v = hi ? f.high : f.low;
        const e = hi ? f.errHigh : f.errLow;
        if (v == null) return;
        vals[k] = { v, e: e != null ? e : v - obs };
        if (k !== 'fx' && vals[k].e != null) errs.push(Math.abs(vals[k].e));
      });
      out.push({ sid, city: st.city || sid, unit: st.unit || '°F', obs, vals,
                 mae: errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : null });
    });
    // worst forecast first, which is the order the letter uses
    out.sort((a, b) => (b.mae == null ? -1 : b.mae) - (a.mae == null ? -1 : a.mae));
    return out;
  }

  function drawFigure() {
    const host = $('#divergence'); if (!host) return;
    host.innerHTML = '';
    const days = scoredDays();
    if (!days.length) { host.appendChild(h('p', { class: 'cap', text: 'No scored days yet.' })); return; }
    if (!gridDate || days.indexOf(gridDate) < 0) gridDate = days[0];
    const R = gridRows(gridDate, gridSide);
    const cap = $('#divCap');
    if (!R.length) {
      host.appendChild(h('p', { class: 'cap', text: 'Nothing scored on this day yet.' }));
      if (cap) cap.textContent = '';
      return;
    }

    const CW = 78, MW = 62, LW = 150, RH = 30, HH = 52, W = 960;
    const cols = GRID.length;
    const gw = LW + cols * CW + MW + MW;
    const H = HH + (R.length + 1) * RH + 26;
    const svg = el('svg', { viewBox: '0 0 ' + Math.max(W, gw) + ' ' + H, class: 'ts', id: 'divsvg' });
    const xOf = j => LW + j * CW;
    const xObs = LW + cols * CW, xMae = xObs + MW;

    // headings
    GRID.forEach((k, j) => {
      const lines = GRID_NAME[k].split('\n');
      lines.forEach((ln, li) => svg.appendChild(txt(ln, { x: xOf(j) + CW / 2, y: HH - 34 + li * 10.5,
        'text-anchor': 'middle', 'font-size': 9.5, 'font-weight': 700,
        fill: k === 'fx' ? 'var(--accent)' : 'var(--ink)' })));
    });
    svg.appendChild(txt('OBSERVED', { x: xObs + MW / 2, y: HH - 12, 'text-anchor': 'middle', 'font-size': 9,
                                      'font-weight': 700, fill: 'var(--ink)' }));
    svg.appendChild(txt('ERROR', { x: xMae + MW / 2, y: HH - 22, 'text-anchor': 'middle', 'font-size': 9,
                                   'font-weight': 700, fill: 'var(--warn)' }));
    svg.appendChild(txt('this station', { x: xMae + MW / 2, y: HH - 12, 'text-anchor': 'middle', 'font-size': 8,
                                          fill: 'var(--muted)' }));

    R.forEach((r, i) => {
      const yy = HH + i * RH;
      svg.appendChild(txt(r.city, { x: LW - 8, y: yy + RH / 2 + 4, 'text-anchor': 'end', 'font-size': 11.5,
                                    fill: 'var(--ink)' }));
      GRID.forEach((k, j) => {
        const cell = r.vals[k];
        const g = el('rect', { x: xOf(j) + 1, y: yy + 1, width: CW - 2, height: RH - 2, rx: 2,
                               fill: cell ? errFill(cell.e) : 'var(--shade)' });
        svg.appendChild(g);
        if (!cell) {
          svg.appendChild(txt('—', { x: xOf(j) + CW / 2, y: yy + RH / 2 + 4, 'text-anchor': 'middle',
                                     class: 'ax', 'pointer-events': 'none' }));
          return;
        }
        const dark = Math.abs(cell.e) >= 3.5;
        svg.appendChild(txt(Math.round(cell.v) + '°', { x: xOf(j) + CW / 2 - 1, y: yy + RH / 2 + 4,
          'text-anchor': 'end', 'font-size': 12, 'font-weight': 700,
          fill: dark ? '#FFFFFF' : 'var(--ink)', 'pointer-events': 'none' }));
        svg.appendChild(txt((cell.e > 0 ? '+' : cell.e < 0 ? '−' : '') + Math.abs(cell.e).toFixed(1),
          { x: xOf(j) + CW / 2 + 4, y: yy + RH / 2 + 4, 'font-size': 9.5,
            fill: dark ? 'rgba(255,255,255,.9)' : 'var(--muted)', 'pointer-events': 'none' }));
        const hit = el('rect', { x: xOf(j) + 1, y: yy + 1, width: CW - 2, height: RH - 2, fill: 'transparent' });
        bind2(hit, () => tip.rows(r.city + ' ' + (gridSide === 'high' ? 'high' : 'low') + ', ' + dayLabel(gridDate),
          [['<span class="sw" style="background:' + (SERIES.find(x => x.k === k) || {}).col + '"></span>'
            + (SERIES.find(x => x.k === k) || {}).name, Math.round(cell.v) + r.unit],
           ['Observed', Math.round(r.obs) + r.unit],
           ['Error', (cell.e > 0 ? '+' : '') + cell.e.toFixed(1) + ' (' + (cell.e > 0 ? 'too warm' : 'too cold') + ')']],
          k === 'fx' ? 'the exchange’s implied median, left out of the margins'
                     : 'as this source stood at six the evening before'));
        svg.appendChild(hit);
      });
      svg.appendChild(el('rect', { x: xObs + 1, y: yy + 1, width: MW - 2, height: RH - 2, rx: 2, fill: 'var(--shade)' }));
      svg.appendChild(txt(Math.round(r.obs) + '°', { x: xObs + MW / 2, y: yy + RH / 2 + 4, 'text-anchor': 'middle',
                          'font-size': 12, 'font-weight': 700, fill: 'var(--ink)', 'pointer-events': 'none' }));
      svg.appendChild(el('rect', { x: xMae + 1, y: yy + 1, width: MW - 2, height: RH - 2, rx: 2, fill: 'var(--warn-soft)' }));
      svg.appendChild(txt(r.mae == null ? '—' : r.mae.toFixed(1), { x: xMae + MW / 2, y: yy + RH / 2 + 4,
                          'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': 700, fill: 'var(--warn)',
                          'pointer-events': 'none' }));
    });

    // the bottom margin: each system's mean absolute error across the stations
    const yb = HH + R.length * RH;
    svg.appendChild(txt('mean absolute error', { x: LW - 8, y: yb + RH / 2 + 4, 'text-anchor': 'end',
                        'font-size': 10, 'font-weight': 700, fill: 'var(--navy)' }));
    const sysMae = {};
    GRID.forEach(k => {
      const e = R.map(r => r.vals[k]).filter(Boolean).map(c => Math.abs(c.e));
      sysMae[k] = e.length ? e.reduce((a, b) => a + b, 0) / e.length : null;
      svg.appendChild(el('rect', { x: xOf(GRID.indexOf(k)) + 1, y: yb + 1, width: CW - 2, height: RH - 2, rx: 2,
                                   fill: 'var(--warn-soft)' }));
      svg.appendChild(txt(sysMae[k] == null ? '—' : sysMae[k].toFixed(2),
        { x: xOf(GRID.indexOf(k)) + CW / 2, y: yb + RH / 2 + 4, 'text-anchor': 'middle', 'font-size': 11.5,
          'font-weight': 700, fill: 'var(--warn)', 'pointer-events': 'none' }));
    });
    const four = ORDER.map(k => sysMae[k]).filter(v => v != null);
    svg.appendChild(el('rect', { x: xMae + 1, y: yb + 1, width: MW - 2, height: RH - 2, rx: 2, fill: 'var(--chip)' }));
    svg.appendChild(txt(four.length ? (four.reduce((a, b) => a + b, 0) / four.length).toFixed(2) : '—',
      { x: xMae + MW / 2, y: yb + RH / 2 + 4, 'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': 700,
        fill: 'var(--navy)', 'pointer-events': 'none' }));

    // the colour scale, on the figure
    const rx = LW, ry = H - 12;
    for (let i = 0; i < 40; i++) {
      svg.appendChild(el('rect', { x: rx + i * 3, y: ry - 8, width: 3.4, height: 8,
                                   fill: errFill(-ERR_CAP + (2 * ERR_CAP) * i / 39) }));
    }
    svg.appendChild(txt('−' + ERR_CAP + '° too cold', { x: rx - 6, y: ry - 1, 'text-anchor': 'end', class: 'ax' }));
    svg.appendChild(txt('too warm +' + ERR_CAP + '°', { x: rx + 126, y: ry - 1, class: 'ax' }));

    host.appendChild(svg);
    const t = $('#divTitle');
    if (t) t.textContent = 'Forecast standing at six the evening before, for the '
      + (gridSide === 'high' ? 'high' : 'low') + ' on ' + dayLabel(gridDate);
    if (cap) {
      cap.textContent = 'Every value in ' + (R[0].unit || '°F') + '. Each cell carries the forecast and, beside it, '
        + 'its error against what the station recorded, red where the forecast ran warm and blue where it ran cold. '
        + 'The right column is that station’s mean absolute error across the four forecast systems and the bottom '
        + 'row is each system’s across the stations. The exchange’s implied median is drawn beside them and left out '
        + 'of both margins, because it is a price rather than a forecast product. Stations run worst-forecast first. '
        + R.length + ' stations scored on this day.';
    }
  }

  // one hover binding for a cell
  function bind2(node, make) {
    node.addEventListener('mousemove', e => tip.show(e, make()));
    node.addEventListener('mouseleave', () => tip.hide());
    node.style.cursor = 'default';
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
      + lead.s.name + ' is closest at ' + degs(lead.high.mae) + '. The sample is matched, so only station-days where '
      + 'every tool has a value are counted and n is the same for all of them, ' + n + ' station-days. Each archive lane '
      + 'started on a different date, so pooling every error a tool happens to have would score some tools over far more '
      + 'days than others and then rank them against each other. Hover a bar for its bias, its share within two degrees, '
      + 'and the same figures on the daily low. The window grows by a day every day.'
      + (uneven ? ' The sources are not scored on the same days. ' + rows.map(r => r.s.name + ' ' + r.high.n).join(', ') + '.' : '') }));
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
        else location.href = WXC.cityHref({ city: st.city, station: sid });
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
    /* The grid's controls: which end of the day, and which of the last seven
       scored days. Both go in the address bar so a link reopens the same
       panel, which the daily letter relies on. */
    const ctl = $('#divControls');
    if (ctl && S && S.stations) {
      const stamp = () => {
        const u = new URL(location.href);
        u.searchParams.set('side', gridSide);
        if (gridDate) u.searchParams.set('day', gridDate);
        history.replaceState(null, '', u);
      };
      const sideBtns = {};
      [['high', 'Highs'], ['low', 'Lows']].forEach(([k, lab]) => {
        const b = h('button', { class: 'vbtn' + (k === gridSide ? ' on' : ''), text: lab });
        b.onclick = () => {
          gridSide = k;
          Object.values(sideBtns).forEach(x => x.classList.remove('on'));
          b.classList.add('on');
          stamp(); drawFigure();
        };
        sideBtns[k] = b; ctl.appendChild(b);
      });
      const days = scoredDays();
      if (!gridDate || days.indexOf(gridDate) < 0) gridDate = days[0];
      const dayBtns = {};
      days.forEach(dte => {
        const b = h('button', { class: 'vbtn' + (dte === gridDate ? ' on' : ''), text: shortDay(dte) });
        b.onclick = () => {
          gridDate = dte;
          Object.values(dayBtns).forEach(x => x.classList.remove('on'));
          b.classList.add('on');
          stamp(); drawFigure();
        };
        dayBtns[dte] = b; ctl.appendChild(b);
      });
    }
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
