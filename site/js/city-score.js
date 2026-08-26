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

    // one path per tool per side, broken wherever the tool has no value
    const line = (get, col, dash, wide) => {
      let d = '', pen = false;
      days.forEach((day, i) => {
        const v = get(day);
        if (v == null) { pen = false; return; }
        d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
        pen = true;
      });
      if (!d) return;
      svg.appendChild(el('path', Object.assign({ d, fill: 'none', stroke: col, 'stroke-width': wide ? 3.2 : 1.6,
                                                 'stroke-linejoin': 'round', 'pointer-events': 'none' },
                                               dash ? { 'stroke-dasharray': dash } : {})));
    };
    SERIES.forEach(s => {
      line(d => (d[s.k] || {}).high, s.col, null);
      line(d => (d[s.k] || {}).low, s.col, '4 3');
    });
    // the observation last and heavier, in black: it is the thing being
    // predicted, not one forecast among the others, and at these line weights a
    // dark navy tool is otherwise easy to mistake for it
    line(d => d.obsHigh, 'var(--ink)', null, true);
    line(d => d.obsLow, 'var(--ink)', '5 4', true);

    // one hover band per day, carrying that day in full
    days.forEach((d, i) => {
      const wBand = (R - L) / Math.max(days.length - 1, 1);
      const band = el('rect', { x: x(i) - wBand / 2, y: T, width: wBand, height: B - T, fill: 'transparent' });
      const rows = [['Observed high / low',
                     (d.obsHigh == null ? '—' : d.obsHigh.toFixed(1) + '°') + ' / '
                     + (d.obsLow == null ? '—' : d.obsLow.toFixed(1) + '°')]];
      SERIES.forEach(s => {
        const f = d[s.k]; if (!f) return;
        const eh = f.errHigh == null ? '' : ' (' + (f.errHigh > 0 ? '+' : '') + f.errHigh.toFixed(1) + ')';
        const el2 = f.errLow == null ? '' : ' (' + (f.errLow > 0 ? '+' : '') + f.errLow.toFixed(1) + ')';
        rows.push(['<span class="sw" style="background:' + s.col + '"></span>' + s.name,
                   (f.high == null ? '—' : f.high.toFixed(0) + '°' + eh) + ' / '
                   + (f.low == null ? '—' : f.low.toFixed(0) + '°' + el2)]);
      });
      const missing = SERIES.filter(s => !d[s.k]).map(s => s.name);
      band.addEventListener('mousemove', e => tip.show(e, tip.rows(dlab(d.date), rows,
        'high / low, and each tool’s error against the observation'
        + (missing.length ? ' · not archived yet on this day: ' + missing.join(', ') : ''))));
      band.addEventListener('mouseleave', () => tip.hide());
      svg.appendChild(band);
    });

    if (key) {
      key.innerHTML = SERIES.map(s => '<span><i style="border-color:' + s.col + '"></i>' + s.name + '</span>').join('')
        + '<span><i style="border-color:var(--ink);border-width:3px"></i><b>Observed</b></span>'
        + '<span>solid is the daily high, dashed the daily low</span>';
    }
    if (cap) {
      cap.textContent = 'The last ' + days.length + ' scored day' + (days.length === 1 ? '' : 's')
        + ' at this station. A gap in a line is a day that tool was not archived for, which is why the lines start '
        + 'at different dates; the record grows by a day every day. The ForecastEx line is the strike where the Yes '
        + 'price crosses 50 cents, read from the last quote before local midnight.';
    }
  }

  function init(station) { tip = WXC.tooltip(); return draw(station); }
  return { init, draw };
})();
