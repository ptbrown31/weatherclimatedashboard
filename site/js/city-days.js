/* The last few days at one station, run together.

   The chart above this one shows a single contract day in detail. This shows
   three of them end to end, which answers a different question: not "what is
   today doing" but "how has this station been running, and were the forecasts
   with it or against it".

   Two things are drawn and they are not the same kind of thing.

     The observations are the hourly METAR trace, the same record settlement
     reads, drawn continuously across the days with a mark at each report.

     The forecasts are levels, not traces: each source's high and low for that
     day as it stood at ONE moment — six in the evening, the station's own time,
     the day before. That is the same anchor the skill table uses, and it is what
     makes the days comparable to each other. A forecast drawn at whatever hour
     each source last happened to publish would be a different lead every day.

   So a level line spans the day it is a forecast for, and its distance from the
   trace under it is that day's error, readable without arithmetic. */
window.WXCityDays = (() => {
  const { el, txt, h, $ } = WXC;
  const DAYS = 3;
  const COL = { nws: 'var(--nws)', nbm: 'var(--nbm)', lamp: 'var(--lamp)', mav: 'var(--mav)' };
  const NAME = { nws: 'National Weather Service', nbm: 'Blend of Models',
                 lamp: 'Aviation guidance (LAMP)', mav: 'GFS MOS' };
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  let tip = null;

  const dayLabel = iso => MON[+iso.slice(5, 7) - 1] + ' ' + (+iso.slice(8, 10));

  async function draw(station) {
    const host = $('#cityDaysWrap'); if (!host) return;
    const svg = $('#cityDays'); if (!svg) return;
    svg.innerHTML = '';
    const [sres, ores, cres] = await Promise.all([
      WXD.get('summary.json'), WXD.get('obs/' + station + '.json', 10), WXD.get('scorecard.json', 1440)]);
    const city = ((sres.data || {}).cities || []).find(c => c.station === station);
    const obs = ((ores.data || {}).rows || []).filter(r => r && r.t && r.tempF != null);
    const st = ((cres.data || {}).stations || {})[station];
    const cap = $('#cityDaysCap'); if (cap) cap.textContent = '';
    if (!city || !obs.length) {
      svg.appendChild(txt('No observations for this station yet.', { x: 20, y: 30, class: 'axl' }));
      return;
    }
    const tz = city.tz;
    // the local day each observation belongs to, through the zone rather than by
    // adding an offset, so a daylight-saving change does not shift a day
    const dayOf = ms => new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms));
    const rows = obs.map(r => ({ t: Date.parse(r.t), v: r.tempF })).filter(r => isFinite(r.t)).sort((a, b) => a.t - b.t);
    const today = dayOf(rows[rows.length - 1].t);
    const wanted = [];
    for (let i = DAYS - 1; i >= 0; i--) {
      const d = new Date(Date.parse(today + 'T12:00:00Z') - i * 86400000);
      wanted.push(new Intl.DateTimeFormat('en-CA', { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d));
    }
    const keep = rows.filter(r => wanted.indexOf(dayOf(r.t)) >= 0);
    if (keep.length < 4) {
      svg.appendChild(txt('Not enough observations yet to run days together.', { x: 20, y: 30, class: 'axl' }));
      return;
    }

    const W = 960, H = 330, L = 52, R = 928, T = 18, B = 268;
    const t0 = keep[0].t, t1 = keep[keep.length - 1].t;
    const byDay = {};
    (st && st.days || []).forEach(d => { byDay[d.date] = d; });

    const vals = keep.map(r => r.v);
    wanted.forEach(dd => {
      const d = byDay[dd]; if (!d) return;
      [d.obsHigh, d.obsLow].forEach(v => { if (v != null) vals.push(v); });
      Object.keys(COL).forEach(k => {
        const f = d[k]; if (!f) return;
        if (f.high != null) vals.push(f.high);
        if (f.low != null) vals.push(f.low);
      });
    });
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.12); lo -= pad; hi += pad;
    const x = t => L + (t - t0) / Math.max(t1 - t0, 1) * (R - L);
    const y = v => B - (v - lo) / (hi - lo) * (B - T);

    const step = (hi - lo) > 40 ? 10 : 5;
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(v + '°', { x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }

    // one band per local day, so a level line can be read against the day it belongs to
    wanted.forEach(dd => {
      const inDay = keep.filter(r => dayOf(r.t) === dd);
      if (!inDay.length) return;
      const a = x(inDay[0].t), b = x(inDay[inDay.length - 1].t);
      svg.appendChild(el('line', { x1: a, x2: a, y1: T, y2: B, class: 'grid' }));
      svg.appendChild(txt(dayLabel(dd), { x: (a + b) / 2, y: B + 16, 'text-anchor': 'middle', class: 'ax' }));

      const d = byDay[dd];
      if (!d) return;
      Object.keys(COL).forEach(k => {
        const f = d[k]; if (!f) return;
        [['high', false], ['low', true]].forEach(([side, dash]) => {
          const v = f[side]; if (v == null) return;
          const ln = el('line', Object.assign({ x1: a, x2: b, y1: y(v), y2: y(v), stroke: COL[k],
                                                'stroke-width': 1.6, 'pointer-events': 'stroke' },
                                              dash ? { 'stroke-dasharray': '5 4' } : {}));
          const err = side === 'high' ? f.errHigh : f.errLow;
          const ob = side === 'high' ? d.obsHigh : d.obsLow;
          ln.onmousemove = e => tip.show(e, tip.rows(NAME[k] + ' — ' + dayLabel(dd), [
            [side === 'high' ? 'Forecast high' : 'Forecast low', WXC.deg(v)],
            ['Observed', ob == null ? '—' : WXC.deg(ob)],
            ['Error', err == null ? '—' : (err > 0 ? '+' : '') + err.toFixed(1)],
            ['Standing at', f.lead != null ? f.lead + ' h to midnight' : null],
          ], 'as this source stood at six the evening before'));
          ln.onmouseleave = () => tip.hide();
          svg.appendChild(ln);
        });
      });
      if (d.obsHigh != null) svg.appendChild(el('circle', { cx: (a + b) / 2, cy: y(d.obsHigh), r: 3.4, fill: 'var(--obs)', 'pointer-events': 'none' }));
      if (d.obsLow != null) svg.appendChild(el('circle', { cx: (a + b) / 2, cy: y(d.obsLow), r: 3.4, fill: 'var(--panel)', stroke: 'var(--obs)', 'stroke-width': 1.6, 'pointer-events': 'none' }));
    });

    // the observations, continuous across the days, with each report marked
    svg.appendChild(el('path', { d: keep.map((r, i) => (i ? 'L' : 'M') + x(r.t).toFixed(1) + ',' + y(r.v).toFixed(1)).join(''),
                                 fill: 'none', stroke: 'var(--obs)', 'stroke-width': 1.8, 'pointer-events': 'none' }));
    const gap = (R - L) / Math.max(keep.length - 1, 1);
    if (gap >= 4.5) keep.forEach(r => svg.appendChild(el('circle',
      { class: 'rdot', cx: x(r.t).toFixed(1), cy: y(r.v).toFixed(1), r: Math.min(2.4, gap / 4),
        fill: 'var(--obs)', 'pointer-events': 'none' })));

    const key = $('#cityDaysKey');
    if (key) {
      key.innerHTML = '<span><i style="border-color:var(--obs)"></i>Observed (METAR)</span>'
        + Object.keys(COL).map(k => '<span><i style="border-color:' + COL[k] + '"></i>' + NAME[k] + '</span>').join('')
        + '<span>solid is the forecast high, dashed the low</span>';
    }
    if (cap) {
      cap.textContent = 'The last ' + wanted.length + ' days at this station, run together. The trace is the hourly '
        + 'METAR record, the same one settlement reads. The level lines are each source’s high and low for that '
        + 'day as it stood at six in the evening the day before — one moment for every source and every day, so '
        + 'the days can be compared with each other. The distance from a level line to the trace under it is that '
        + 'day’s error. Hover a level for the number.';
    }
  }

  function init() { tip = WXC.tooltip(); }
  return { init, draw };
})();
