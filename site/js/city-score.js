/* The last week at one station: what each tool said, against what happened.

   The scorecard page ranks the tools across every station. This is the same
   record for the station in front of you, drawn rather than tabulated: one line
   per tool for the daily high and another for the daily low, both on the same
   temperature axis because that is the axis they are both in, and the
   observation in black so the thing being predicted is never one line among
   equals.

   Highs and lows are the same colour per tool and told apart by the dash, not
   by a second palette: a reader is comparing tools to the observation, not
   highs to lows, so the colour is spent on the comparison that matters.

   A tool that has no value for a day leaves a gap rather than a straight line
   through it. Each archive lane started on a different date, so the gaps are
   real and stating them is the point. */
window.WXCityScore = (() => {
  const { el, txt, h, $ } = WXC;
  const SERIES = [
    { k: 'nws', name: 'National Weather Service', col: 'var(--nws)' },
    { k: 'nbm', name: 'Blend of Models', col: 'var(--nbm)' },
    { k: 'lamp', name: 'Aviation guidance (LAMP)', col: 'var(--lamp)' },
    { k: 'mav', name: 'GFS MOS', col: 'var(--mav)' },
    { k: 'fx', name: 'ForecastEx implied', col: 'var(--accent)' },
  ];
  const DAYS = 7;
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const dlab = s => MON[+String(s).slice(5, 7) - 1] + ' ' + (+String(s).slice(8, 10));
  let tip = null;

  async function draw(station) {
    const svg = $('#cityScore'); if (!svg) return;
    const r = await WXD.get('scorecard.json', 1440);
    const st = r.data && r.data.stations && r.data.stations[station];
    const cap = $('#cityScoreCap'), key = $('#cityScoreKey');
    svg.innerHTML = ''; if (key) key.innerHTML = ''; if (cap) cap.textContent = '';
    if (!st || !(st.days || []).length) {
      svg.appendChild(txt('No scored days for this station yet.', { x: 20, y: 30, class: 'axl' }));
      return;
    }
    // the archive lists newest first; a chart reads left to right
    const days = st.days.slice(0, DAYS).slice().reverse();

    const W = 960, H = 380, L = 52, R = 830, T = 20, B = 316;
    const vals = [];
    days.forEach(d => {
      if (d.obsHigh != null) vals.push(d.obsHigh);
      if (d.obsLow != null) vals.push(d.obsLow);
      SERIES.forEach(s => {
        const f = d[s.k]; if (!f) return;
        if (f.high != null) vals.push(f.high);
        if (f.low != null) vals.push(f.low);
      });
    });
    if (!vals.length) { svg.appendChild(txt('No values yet.', { x: 20, y: 30, class: 'axl' })); return; }
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.1);
    lo -= pad; hi += pad;
    const x = i => L + (days.length === 1 ? (R - L) / 2 : (i / (days.length - 1)) * (R - L));
    const y = v => B - ((v - lo) / (hi - lo)) * (B - T);

    const step = (hi - lo) > 40 ? 10 : (hi - lo) > 20 ? 5 : 2;
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(v + '°', { x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }
    days.forEach((d, i) => svg.appendChild(txt(dlab(d.date), { x: x(i), y: B + 16, 'text-anchor': 'middle', class: 'ax' })));

    /* A dot per tool per day, not a line.

       A line between Monday and Tuesday draws a value for Monday night that no
       tool ever issued, and where a tool has no forecast archived the line either
       breaks or leaps a gap it did not measure. These are separate daily
       forecasts, so they are drawn as separate marks.

       Within a day the tools are spread by a few pixels so a day where they all
       agree still shows every tool rather than one dot hiding four. A filled dot
       is the daily high and a hollow one the daily low: the colour is spent on
       which tool, because that is the comparison the panel is for.

       The observation is black and larger, drawn last, because it is the thing
       being predicted rather than one forecast among equals. */
    const wBand = (R - L) / Math.max(days.length - 1, 1);
    const spread = Math.min(7, wBand / (SERIES.length + 2));
    const dot = (cx, cy, col, filled, rad) => svg.appendChild(el('circle', {
      cx, cy, r: rad, fill: filled ? col : 'var(--panel)', stroke: col,
      'stroke-width': filled ? 0.8 : 1.8, 'pointer-events': 'none' }));

    days.forEach((d, i) => {
      SERIES.forEach((sr, j) => {
        const f = d[sr.k]; if (!f) return;
        const cx = x(i) + (j - (SERIES.length - 1) / 2) * spread;
        if (f.high != null) dot(cx, y(f.high), sr.col, true, 3.4);
        if (f.low != null) dot(cx, y(f.low), sr.col, false, 3.4);
      });
    });
    days.forEach((d, i) => {
      if (d.obsHigh != null) dot(x(i), y(d.obsHigh), 'var(--ink)', true, 5);
      if (d.obsLow != null) dot(x(i), y(d.obsLow), 'var(--ink)', false, 5);
    });

    // one hover band per day, carrying that day in full
    days.forEach((d, i) => {
      const band = el('rect', { x: x(i) - wBand / 2, y: T, width: wBand, height: B - T, fill: 'transparent' });
      const rows = [['Observed high / low',
                     (d.obsHigh == null ? '—' : d.obsHigh.toFixed(1) + '°') + ' / '
                     + (d.obsLow == null ? '—' : d.obsLow.toFixed(1) + '°')]];
      SERIES.forEach(s => {
        const f = d[s.k]; if (!f) return;
        const eh = f.errHigh == null ? '' : ' (' + (f.errHigh > 0 ? '+' : '') + f.errHigh.toFixed(1) + ')';
        const el2 = f.errLow == null ? '' : ' (' + (f.errLow > 0 ? '+' : '') + f.errLow.toFixed(1) + ')';
        const lt = s.k === 'fx' ? '' : (f.lead != null ? ' · ' + leadText(f.lead) : '');
        rows.push(['<span class="sw" style="background:' + s.col + '"></span>' + s.name + lt,
                   (f.high == null ? '—' : f.high.toFixed(0) + '°' + eh) + ' / '
                   + (f.low == null ? '—' : f.low.toFixed(0) + '°' + el2)]);
      });
      const missing = SERIES.filter(s => !d[s.k]).map(s => s.name);
      band.addEventListener('mousemove', e => tip.show(e, tip.rows(dlab(d.date), rows,
        'high / low, each tool’s error against the observation, and how far before the day its cycle was issued'
        + (missing.length ? ' · not archived yet on this day: ' + missing.join(', ') : ''))));
      band.addEventListener('mouseleave', () => tip.hide());
      svg.appendChild(band);
    });

    if (key) {
      key.innerHTML = SERIES.map(s => '<span><i style="border-color:' + s.col + '"></i>' + s.name + '</span>').join('')
        + '<span><i style="border-color:var(--ink);border-width:3px"></i><b>Observed</b></span>'
        + '<span>a filled dot is the daily high, a hollow one the daily low</span>';
    }
    if (cap) {
      cap.textContent = 'The last ' + days.length + ' scored day' + (days.length === 1 ? '' : 's')
        + ' at this station. A missing dot is a day that tool was not archived for, which is why some tools start '
        + 'later than others; the record grows by a day every day. Tools are nudged apart within each day so an '
        + 'agreed forecast still shows every one of them. The ForecastEx line is the strike where the Yes '
        + 'price crosses 50 cents, read from the last quote before local midnight.';
    }
  }

  /* The scored days for this station, as a table.

     It used to sit on the daily-temperatures landing page showing one station —
     whichever sorted first, which meant every reader saw Atlanta whether or not
     they cared about it. It belongs with the station it describes.

     Two things carry meaning beyond the numbers. Every temperature is tinted on
     the same ramp the national map shades with, so a cold morning and a hot
     afternoon are the same colours here as there and a column can be read down
     without reading each figure. And each tool keeps its own colour from the
     chart above, carried on the header and a rule down the left of its columns,
     so a reader tracking one tool can find it without counting across.

     Errors stay plain text. Tinting them too would put three colour scales in
     one table and the eye would have nothing to hold on to. */
  const TRAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];
  const hx = c => [1, 3, 5].map(k => parseInt(c.slice(k, k + 2), 16));

  function tempColor(v, lo, hi) {
    if (v == null || hi <= lo) return '';
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * (TRAMP.length - 1);
    const i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= TRAMP.length - 1) return TRAMP[Math.min(i, TRAMP.length - 1)];
    const A = hx(TRAMP[i]), B = hx(TRAMP[i + 1]);
    return 'rgb(' + A.map((a, k) => Math.round(a + (B[k] - a) * f)).join(',') + ')';
  }

  const f1 = v => (v == null ? '—' : Math.round(v) + '°');
  const med = a => { const b = a.slice().sort((x, y) => x - y); return b.length ? b[Math.floor(b.length / 2)] : null; };
  /* What each tool's number is anchored to.

     Every tool here is read at one moment: six in the evening, the station's own
     time, the day before. What differs between them is how stale each one's
     standing run is at that moment — hourly guidance is half an hour old, a
     four-times-daily model can be six hours old — and that is a real difference
     in what each product offers rather than an artefact of the scoring. So each
     tool carries its own lead in the header. */
  const leadOf = (days, k) => med(days.map(d => (d[k] || {}).lead).filter(v => v != null));
  const leadText = v => (v == null ? '' : (Math.round(v * 10) / 10) + 'h to midnight');
  const sgn = v => (v == null ? '' : (v > 0 ? '+' : '') + v.toFixed(1));

  async function drawTable(station) {
    const host = $('#cityDays'); if (!host) return;
    host.innerHTML = '';
    const r = await WXD.get('scorecard.json', 1440);
    const st = r.data && r.data.stations && r.data.stations[station];
    if (!st || !(st.days || []).length) {
      host.appendChild(h('p', { class: 'cap', text: 'No scored days for this station yet.' }));
      return;
    }
    const days = st.days;
    // one temperature scale for the whole table, so a cell's colour means the
    // same thing in every column
    const all = [];
    days.forEach(d => {
      [d.obsHigh, d.obsLow].forEach(v => { if (v != null) all.push(v); });
      SERIES.forEach(sr => { const x = d[sr.k]; if (x) { if (x.high != null) all.push(x.high); if (x.low != null) all.push(x.low); } });
    });
    const lo = Math.min(...all), hi = Math.max(...all);

    const t = h('table', { class: 'daytab' });
    const hr1 = h('tr', {}, [h('th', { text: '' }), h('th', { class: 'grp obs', colspan: '2', text: 'Observed' })]);
    const hr2 = h('tr', {}, [h('th', { text: 'Day' }), h('th', { class: 'num obs', text: 'High' }), h('th', { class: 'num obs', text: 'Low' })]);
    SERIES.forEach(sr => {
      const th = h('th', { class: 'grp', colspan: '4' });
      th.style.borderTopColor = sr.col;
      th.appendChild(h('span', { class: 'sw', style: 'background:' + sr.col }));
      th.appendChild(document.createTextNode(sr.name));
      const lt = sr.k === 'fx' ? 'last quote before the day'
                               : leadText(leadOf(days, sr.k));
      if (lt) th.appendChild(h('span', { class: 'lead', text: lt }));
      hr1.appendChild(th);
      ['High', 'err', 'Low', 'err'].forEach((lab, i) => {
        const c = h('th', { class: 'num' + (i === 0 ? ' gs' : '') + (/err/.test(lab) ? ' err' : ''), text: lab });
        if (i === 0) c.style.borderLeftColor = sr.col;
        hr2.appendChild(c);
      });
    });
    t.appendChild(hr1); t.appendChild(hr2);

    days.forEach(d => {
      const tr = h('tr', {}, [h('td', { text: dlab(d.date) })]);
      [d.obsHigh, d.obsLow].forEach(v => {
        const td = h('td', { class: 'num obs', text: f1(v) });
        const c = tempColor(v, lo, hi); if (c) { td.style.background = c; td.style.color = '#14202b'; }
        tr.appendChild(td);
      });
      SERIES.forEach(sr => {
        const x = d[sr.k] || {};
        [['high', 'errHigh'], ['low', 'errLow']].forEach(([vk, ek], i) => {
          const td = h('td', { class: 'num' + (i === 0 ? ' gs' : ''), text: f1(x[vk]) });
          if (i === 0) td.style.borderLeftColor = sr.col;
          const c = tempColor(x[vk], lo, hi); if (c) { td.style.background = c; td.style.color = '#14202b'; }
          tr.appendChild(td);
          tr.appendChild(h('td', { class: 'num err', text: sgn(x[ek]) }));
        });
      });
      t.appendChild(tr);
    });
    host.appendChild(h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, [t]));
    host.appendChild(h('p', { class: 'cap',
      text: 'The ' + days.length + ' scored day' + (days.length === 1 ? '' : 's') + ' at this station, in '
            + (st.unit || '°F') + '. Every tool is read at ONE moment: six in the evening, this station\u2019s own '
            + 'time, the day before. What differs is how stale each one\u2019s standing run was by then \u2014 hourly '
            + 'guidance half an hour, a four-times-daily model several \u2014 and the hours under each name are that '
            + 'run\u2019s distance from midnight. That is a real difference between the products rather than an '
            + 'artefact of the scoring. The market column is the last quote before the same moment. '
            + 'Every temperature is tinted on the same scale the national map uses, so the coldest reading in the '
            + 'table is the palest and the warmest the deepest. err is the forecast minus what was observed, so a '
            + 'positive number is a forecast that ran warm. A dash is a day that tool was not archived for.' }));
  }

  function init() { tip = WXC.tooltip(); }
  return { init, draw, drawTable };
})();
