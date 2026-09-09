/* The last week at one station: what each tool said, against what happened.

   The scorecard page ranks the tools across every station. This is the same
   record for the station in front of you, drawn rather than tabulated: one line
   per tool for the daily high and another for the daily low, both on the same
   temperature axis because that is the axis they are both in, and the
   observation in black so the thing being predicted is never one line among
   equals.

   Highs and lows are the same color per tool and told apart by the dash, not
   by a second palette: a reader is comparing tools to the observation, not
   highs to lows, so the color is spent on the comparison that matters.

   A tool that has no value for a day leaves a gap rather than a straight line
   through it. Each archive lane started on a different date, so the gaps are
   real and stating them is the point. */
window.WXCityScore = (() => {
  const { el, txt, h, $ } = WXC;
  const SHOW_BAND = false;   // a dot answers for itself now
  const SERIES = [
    { k: 'nws', name: 'National Weather Service', col: 'var(--nws)' },
    { k: 'nbm', name: 'Blend of Models', col: 'var(--nbm)' },
    { k: 'lamp', name: 'Aviation guidance (LAMP)', col: 'var(--lamp)' },
    { k: 'mav', name: 'GFS MOS', col: 'var(--mav)' },
    { k: 'fx', name: 'ForecastEx implied', col: 'var(--accent)' },
  ];
  const DAYS = 7;
  const TABLE_DAYS = 14;
  /* The error columns get their own scale, and it is divergent.

     A forecast that ran three degrees warm and one that ran three degrees cold
     are opposite mistakes, and on the temperature ramp they were near enough
     the same color. Warm errors run red, cold errors blue, and zero is the
     paper — so the sign is visible before the number is read. The scale is
     fixed at five degrees rather than fitted to the table, so a column's
     color means the same thing on a calm week as on a wild one. */
  const ERR_MAX = 5;
  function errColor(v) {
    if (v == null || !isFinite(v)) return null;
    const t = Math.max(-1, Math.min(1, v / ERR_MAX));
    const a = Math.abs(t);
    // pale at zero, saturated at five degrees; the same warm/cool pair the
    // rest of the site uses for a signed temperature difference
    const c = t >= 0 ? [178, 24, 43] : [33, 102, 172];
    const w = 0.10 + 0.62 * a;
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + w.toFixed(3) + ')';
  }
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
    // NCEI's daily normal for this station, drawn on the panel and therefore
    // part of what the axis has to hold
    const nmz = await WXD.get('normals/' + station + '.json', 1440).catch(() => null);
    // keyed by month-day; the panel's own last day is the one to read
    const nmdays = nmz && nmz.data && nmz.data.days;
    const lastDay = days.length ? days[days.length - 1].date : null;
    const nmd = (nmdays && lastDay) ? nmdays[lastDay.slice(5)] : null;
    const vals = [];
    if (nmd) { [nmd.tmax, nmd.tmin].forEach(v => { if (v != null) vals.push(v); }); }
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
    /* One temperature scale for both panels of this figure.

       The panel below opens out the last three of these days hour by hour, and
       a reader compares heights across the two. Published here because this
       panel is drawn first and holds the wider set of values, the forecasts as
       well as the observations. */
    window.WXCityScale = { lo, hi, days: days.map(d => d.date) };
    const x = i => L + (days.length === 1 ? (R - L) / 2 : (i / (days.length - 1)) * (R - L));
    const y = v => B - ((v - lo) / (hi - lo)) * (B - T);

    const step = (hi - lo) > 40 ? 10 : (hi - lo) > 20 ? 5 : 2;
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(v + '°', { x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }
    days.forEach((d, i) => svg.appendChild(txt(dlab(d.date), { x: x(i), y: B + 16, 'text-anchor': 'middle', class: 'ax' })));

    /* The three days the panel below opens out.

       Shaded here and joined to that panel's edges, so the second figure reads
       as these three days at an hour's resolution rather than as a separate
       chart of its own. */
    const sub = days.slice(-3);
    if (sub.length) {
      const half = days.length > 1 ? (R - L) / (days.length - 1) / 2 : (R - L) / 2;
      const a = Math.max(L - 2, x(days.length - sub.length) - half);
      const b = Math.min(R + 2, x(days.length - 1) + half);
      svg.insertBefore(el('rect', { x: a, y: T, width: b - a, height: B - T, fill: 'var(--accent)',
                                    opacity: 0.07, 'pointer-events': 'none' }), svg.firstChild);
      [a, b].forEach(px => svg.appendChild(el('line', { x1: px, x2: px, y1: T, y2: H - 2, stroke: 'var(--accent)',
                                                        'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.55,
                                                        'pointer-events': 'none' })));
      svg.appendChild(txt('opened out below', { x: (a + b) / 2, y: H - 6, 'text-anchor': 'middle',
                                                'font-size': 9.5, fill: 'var(--accent)' }));
    }

    /* A dot per tool per day, not a line.

       A line between Monday and Tuesday draws a value for Monday night that no
       tool ever issued, and where a tool has no forecast archived the line either
       breaks or leaps a gap it did not measure. These are separate daily
       forecasts, so they are drawn as separate marks.

       Within a day the tools are spread by a few pixels so a day where they all
       agree still shows every tool rather than one dot hiding four. A filled dot
       is the daily high and a hollow one the daily low: the color is spent on
       which tool, because that is the comparison the panel is for.

       The observation is black and larger, drawn last, because it is the thing
       being predicted rather than one forecast among equals. */
    const wBand = (R - L) / Math.max(days.length - 1, 1);
    const spread = Math.min(7, wBand / (SERIES.length + 2));
    const dot = (cx, cy, col, filled, rad) => svg.appendChild(el('circle', {
      cx, cy, r: rad, fill: filled ? col : 'var(--panel)', stroke: col,
      'stroke-width': filled ? 0.8 : 1.8, 'pointer-events': 'none' }));

    /* The normal high and low, the same pair the chart above draws.

       A week of dots says how the tools did against each other; the normals say
       what the week itself was, which is the question a reader brings to a run
       of hot days. NCEI's daily normal for the station, flat across the panel. */
    if (nmd && (nmd.tmax != null || nmd.tmin != null)) {
      [['normal high', nmd.tmax, 'var(--warm)'], ['normal low', nmd.tmin, 'var(--cool)']].forEach(([nm, v, col]) => {
        if (v == null) return;
        svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), stroke: col, 'stroke-width': 1,
                                     'stroke-dasharray': '5 4', opacity: .8, 'pointer-events': 'none' }));
        svg.appendChild(txt(nm + ' ' + v.toFixed(0) + '\u00b0', { x: R - 3, y: y(v) - 3, 'text-anchor': 'end',
                                                                  class: 'axl', fill: col }));
      });
    }

    /* Every mark answers for itself.

       One box per day listed all five tools whichever dot the pointer was on,
       so the reader had to find the row for the mark they were pointing at. A
       dot now carries its own value, its error, and the moment its forecast was
       issued, which for the market is the moment its quote was read. */
    // the station's own clock, which is the one every cycle time is quoted in
    const tz = st.tz || (window.WX_CITY && WX_CITY.tz) || undefined;
    const stamp = t => { const ms = Date.parse(t || ''); return isFinite(ms) ? WXC.clockFull(ms, tz) + ' · ' + WXC.dateShort(ms, tz) : null; };
    const hit = (cx, cy, html) => {
      const h2 = el('circle', { cx, cy, r: 7, fill: 'transparent' });
      h2.addEventListener('mousemove', e => tip.show(e, html()));
      h2.addEventListener('mouseleave', () => tip.hide());
      svg.appendChild(h2);
    };
    days.forEach((d, i) => {
      SERIES.forEach((sr, j) => {
        const f = d[sr.k]; if (!f) return;
        const cx = x(i) + (j - (SERIES.length - 1) / 2) * spread;
        [['high', f.high, f.errHigh, true], ['low', f.low, f.errLow, false]].forEach(([side, v, err, filled]) => {
          if (v == null) return;
          dot(cx, y(v), sr.col, filled, 3.4);
          const when = sr.k === 'fx'
            ? ['Quote read at', stamp(f.asof)]
            : ['Cycle issued', stamp(side === 'high' ? (f.cycleHigh || f.cycle) : (f.cycleLow || f.cycle))];
          hit(cx, y(v), () => tip.rows(
            '<span class="sw" style="background:' + sr.col + '"></span>' + sr.name + ' · ' + side + ' · ' + dlab(d.date),
            [[side === 'high' ? 'Forecast high' : 'Forecast low', v.toFixed(1) + '°'],
             ['Observed', (side === 'high' ? d.obsHigh : d.obsLow) == null ? '—'
               : (side === 'high' ? d.obsHigh : d.obsLow).toFixed(1) + '°'],
             ['Error', err == null ? '—' : (err > 0 ? '+' : '') + err.toFixed(1) + '°'],
             when,
             sr.k === 'fx' ? null : ['Lead on the day', f.lead != null ? leadText(f.lead) : null]]));
        });
      });
    });
    days.forEach((d, i) => {
      [['high', d.obsHigh, true], ['low', d.obsLow, false]].forEach(([side, v, filled]) => {
        if (v == null) return;
        dot(x(i), y(v), 'var(--ink)', filled, 5);
        hit(x(i), y(v), () => tip.rows('Observed ' + side + ' · ' + dlab(d.date),
          [['Value', v.toFixed(1) + '°']], 'the METAR record the contract settles on'));
      });
    });

    // the shaded columns still mark which days the panel below opens out
    days.forEach((d, i) => {
      if (!SHOW_BAND) return;
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

    // the plot labels its own series, so a key under it says the same thing twice
  }

  /* The scored days for this station, as a table.

     It used to sit on the daily-temperatures landing page showing one station —
     whichever sorted first, which meant every reader saw Atlanta whether or not
     they cared about it. It belongs with the station it describes.

     Two things carry meaning beyond the numbers. Every temperature is tinted on
     the same ramp the national map shades with, so a cold morning and a hot
     afternoon are the same colors here as there and a column can be read down
     without reading each figure. And each tool keeps its own color from the
     chart above, carried on the header and a rule down the left of its columns,
     so a reader tracking one tool can find it without counting across.

     Errors stay plain text. Tinting them too would put three color scales in
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
    const host = $('#scoredDays'); if (!host) return;
    host.innerHTML = '';
    const r = await WXD.get('scorecard.json', 1440);
    const st = r.data && r.data.stations && r.data.stations[station];
    if (!st || !(st.days || []).length) {
      host.appendChild(h('p', { class: 'cap', text: 'No scored days for this station yet.' }));
      return;
    }
    /* The last fortnight, not the whole record.

       The table grows by a row a day and had no end; two weeks is as far back
       as a reader asking "how has this station been running" is looking, and
       it keeps the table on one screen. The chart above covers the last three
       complete days; this covers the two weeks around them. */
    const days = st.days.slice(0, TABLE_DAYS);
    // one temperature scale for the whole table, so a cell's color means the
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
          const etd = h('td', { class: 'num err', text: sgn(x[ek]) });
          const ec = errColor(x[ek]);
          if (ec) { etd.style.background = ec; etd.style.color = 'var(--ink)'; }
          tr.appendChild(etd);
        });
      });
      t.appendChild(tr);
    });

    /* The mean of the column, under it.

       A reader comparing tools down fourteen rows was doing the averaging by
       eye. The error columns are the ones that settle it, so those carry the
       mean error and the mean absolute error under it, and the temperature
       columns carry the plain mean of what was forecast. Days a tool was not
       archived for are left out of its own mean rather than counted as zero. */
    const mean = v => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);
    const col = pick => mean(days.map(pick).filter(v => v != null && !isNaN(v)));
    const mr = h('tr', { class: 'meanrow' }, [h('td', { text: 'Mean' })]);
    [d => d.obsHigh, d => d.obsLow].forEach(pick => mr.appendChild(h('td', { class: 'num obs', text: f1(col(pick)) })));
    SERIES.forEach(sr => {
      [['high', 'errHigh'], ['low', 'errLow']].forEach(([vk, ek], i) => {
        const td = h('td', { class: 'num' + (i === 0 ? ' gs' : ''), text: f1(col(d => (d[sr.k] || {})[vk])) });
        if (i === 0) td.style.borderLeftColor = sr.col;
        mr.appendChild(td);
        const me = col(d => (d[sr.k] || {})[ek]);
        const mae = col(d => { const e = (d[sr.k] || {})[ek]; return e == null ? null : Math.abs(e); });
        mr.appendChild(h('td', { class: 'num err', text: me == null ? '\u2014' : sgn(me) + ' (' + mae.toFixed(1) + ')' }));
      });
    });
    t.appendChild(mr);
    host.appendChild(h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, [t]));
    host.appendChild(h('p', { class: 'cap',
      text: 'The mean row carries each column\u2019s average, and under err the mean error with the mean absolute '
            + 'error beside it in brackets.' }));
  }

  function init() { tip = WXC.tooltip(); }
  return { init, draw, drawTable };
})();
