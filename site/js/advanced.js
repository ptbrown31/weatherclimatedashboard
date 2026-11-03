/* The advanced panels on the city page: the physical ingredients of the day,
   observed against forecast.

   A temperature forecast is the end of a causal chain that runs through
   moisture, cloud and wind, and a reader deciding whether today's high will
   verify can watch those directly: if the blend's 82° needs unbroken sun and
   a late front, low cloud at noon is evidence before the thermometer has it.
   Each panel is one upstream variable, the tools' hourly lines under the
   station's own METAR readings, in the same colors and line styles the main
   chart uses for the same tools, over the same window the main chart shows,
   noon the day before to the end of the contract day.

   Two columns, temperature at the top of both, so each variable is read
   against the thing it drives without the eye travelling. Cloud and moisture
   on the left, wind on the right.

   Two days. Today runs against each tool's newest run, solid, with the run
   that stood at six the evening before yesterday drawn dotted under it, the
   way the main chart draws a run a newer one superseded. Yesterday runs
   entirely against those anchored runs, the scorecard's anchor, so the
   postmortem judges the same forecast the standings judged, and they are
   dotted there too.

   Data in: advanced/{SID}.json (the tools, from the archived bulletins, the
   NWS hourly product and the HRRR at the station's grid point) and the obs
   snapshot the page already carries.
   Everything drawn is a published number; nothing here is a forecast of the
   site's own. */
window.WXAdv = (function () {
  'use strict';
  const { el, txt, h, $ } = WXC;

  /* Color and weight per tool, exactly as the main chart draws them. Every
     tool's newest run is solid and a run a newer one superseded is dotted
     (EARLIER), the one convention both figures use (owner, 2026-10-16), so
     the tools are told apart by color alone. */
  const TOOLS = [
    { k: 'nws', name: 'Weather Service', short: 'NWS', col: 'var(--nws)', w: 2.4, op: 0.95 },
    { k: 'nbm', name: 'Blend of Models', short: 'NBM', col: 'var(--nbm)', w: 2, op: 0.9 },
    { k: 'lamp', name: 'LAMP', short: 'LAMP', col: 'var(--lamp)', w: 1.8, op: 0.9 },
    { k: 'mav', name: 'GFS MOS', short: 'MOS', col: 'var(--mav)', w: 1.8, op: 0.85 },
    // the HRRR at the station's grid point (owner, 2026-10-16), absent off the grid
    { k: 'hrrr', name: 'HRRR', short: 'HRRR', col: 'var(--hrrr)', w: 1.8, op: 0.9 },
  ];
  const EARLIER = '2 3';
  // METAR cover codes on the same axis as the bulletins' bands: the midpoint
  // of each code's okta range. VV is an obscured sky, which is total cover.
  const OBS_PCT = { CAVOK: 0, CLR: 0, SKC: 0, FEW: 19, SCT: 44, BKN: 75, OVC: 100, VV: 100 };

  let adv = null, ob = null, obRows = null, sid = null, tz = null, unit = 'F';
  let day = 'today', host = null, tip = null;

  /* Geometry. One column whose viewBox is 610 wide with the plot from 52 to
     610, the exact frame the main chart's temperature panel uses, and whose
     card is sized to the same share of the page, so the time axis here sits
     directly under the one above it at the same scale. */
  const G = { W: 610, C: [{ x0: 52, x1: 610 }], panelH: 118, gap: 34, barbRow: 21 };

  const P2 = s => Date.parse(s);
  // tomorrow reads the same newest cycles as today, which is what the main
  // chart's day-ahead view draws; only the postmortem switches to the
  // anchored ones
  const reading = () => (day === 'yesterday' ? adv.yesterday : adv.current);
  const context = () => (day === 'today' ? adv.yesterday : null);

  /* The same window the main chart shows: noon the day before through the end
     of the contract day. The day-ahead and postmortem views keep the shape,
     shifted a day either way, so the three are read the same way. */
  function span() {
    const mk = adv.markers, lead = P2(mk.dayStart) - P2(mk.winStart);
    if (day === 'tomorrow') return [P2(mk.dayEnd) - lead, P2(mk.dayEnd) + 86400000];
    if (day === 'yesterday') return [P2(mk.ydayStart) - lead, P2(mk.dayStart)];
    return [P2(mk.winStart), P2(mk.dayEnd)];
  }
  const dayStart = () => (day === 'tomorrow' ? P2(adv.markers.dayEnd)
                          : day === 'yesterday' ? P2(adv.markers.ydayStart)
                          : P2(adv.markers.dayStart));

  const stampMs = st => {
    const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)/.exec(st || '');
    return m ? Date.parse(m[1] + '-' + m[2] + '-' + m[3] + 'T' + m[4] + ':' + m[5] + ':00Z') : Date.parse(st || '');
  };
  /* When a run was issued, as the main chart names it: the hour, a or p, and
     T or Y for the day on screen or the day before it, else the date. */
  const runTag = st => {
    const ms = stampMs(st);
    if (isNaN(ms)) return '';
    const key = t => new Date(t).toLocaleDateString('en-CA', { timeZone: tz });
    const hh = WXC.hourOf(ms, tz), on = key(ms), d0 = dayStart();
    const d = on === key(d0) ? 'T' : (on === key(d0 - 43200000) ? 'Y'
      : new Date(ms).toLocaleDateString('en-US', { timeZone: tz, month: 'short', day: 'numeric' }));
    return (hh % 12 || 12) + (hh < 12 ? 'a' : 'p') + ' ' + d;
  };
  const clockOf = st => {
    const ms = stampMs(st);
    return isNaN(ms) ? st : new Date(ms).toLocaleTimeString('en-US',
      { timeZone: tz, hour: 'numeric', minute: '2-digit' }).toLowerCase().replace(' ', '');
  };

  function rowsIn(rows, s, map) {
    return (rows || []).map(r => ({ t: Date.parse(r.t), v: map(r), r }))
      .filter(q => q.v != null && q.t >= s[0] - 1800000 && q.t <= s[1] + 1800000);
  }

  /* An axis range from the values actually drawn, snug: a small pad, whole
     degrees, a floor on the spread so a flat day is not a magnified wiggle. */
  function range(get, obsGet, s, minSpan, floor) {
    let vals = rowsIn(obRows, s, obsGet).map(q => q.v);
    [reading(), context()].forEach(rd => rd && TOOLS.forEach(t => {
      if (rd[t.k]) vals = vals.concat(rowsIn(rd[t.k].rows, s, get).map(q => q.v));
    }));
    if (!vals.length) return [0, minSpan];
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max(1, (hi - lo) * 0.07);
    lo = Math.floor(lo - pad); hi = Math.ceil(hi + pad);
    if (hi - lo < minSpan) { const m = (hi + lo - minSpan) / 2; lo = Math.floor(m); hi = lo + minSpan; }
    if (floor != null && lo < floor) { lo = floor; hi = Math.max(hi, lo + minSpan); }
    return [lo, hi];
  }

  function grid(svg, col, y0, s, lo, hi, fmt, label) {
    svg.appendChild(txt(label.toUpperCase(), { x: col.x0, y: y0 - 5, class: 'axl',
                                               'font-size': 10, 'letter-spacing': '0.06em' }));
    [lo, (lo + hi) / 2, hi].forEach(v => {
      const y = y0 + G.panelH - (v - lo) / (hi - lo) * G.panelH;
      svg.appendChild(el('line', { x1: col.x0, y1: y, x2: col.x1, y2: y, stroke: 'var(--rule)', 'stroke-width': 0.5 }));
      svg.appendChild(txt(fmt(v), { x: col.x0 - 4, y: y + 3, 'text-anchor': 'end', class: 'axl', 'font-size': 9 }));
    });
    ticks(s).forEach(t => {
      const x = col.x0 + (t - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0);
      svg.appendChild(el('line', { x1: x, y1: y0, x2: x, y2: y0 + G.panelH, stroke: 'var(--rule)', 'stroke-width': 0.5 }));
    });
    // the contract day's own midnight, the boundary every number settles on
    const xd = col.x0 + (dayStart() - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0);
    svg.appendChild(el('line', { x1: xd, y1: y0, x2: xd, y2: y0 + G.panelH, stroke: 'var(--muted)', 'stroke-width': 1 }));
  }

  function seriesPanel(svg, col, y0, s, label, get, obsGet, lo, hi, fmt, opts) {
    grid(svg, col, y0, s, lo, hi, fmt, label);
    const X = t => col.x0 + (t - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0);
    const Y = v => y0 + G.panelH - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * G.panelH;
    const path = (pts, step) => pts.map((q, i) => {
      if (!i) return 'M' + X(q.t).toFixed(1) + ' ' + Y(q.v).toFixed(1);
      return step ? ('L' + X(q.t).toFixed(1) + ' ' + Y(pts[i - 1].v).toFixed(1) + 'L' + X(q.t).toFixed(1) + ' ' + Y(q.v).toFixed(1))
                  : ('L' + X(q.t).toFixed(1) + ' ' + Y(q.v).toFixed(1));
    }).join('');
    /* The runs that stood at six the evening before yesterday, dotted and dim
       under the newest ones, across the whole window, the way the main chart
       draws a run a newer one superseded. Clipped at midnight these left every
       tool a hole from midnight until its newest run begins, which read as the
       lines being broken while the observed line above sailed through. */
    const ctx = context();
    if (ctx) TOOLS.forEach(t => {
      if (!ctx[t.k]) return;
      const pts = rowsIn(ctx[t.k].rows, s, get);
      if (pts.length < 2) return;
      svg.appendChild(el('path', { d: path(pts, false), fill: 'none', stroke: t.col,
                                   'stroke-width': 1.2, 'stroke-dasharray': EARLIER, opacity: 0.7 }));
      pts.forEach(q => svg.appendChild(el('circle', { cx: X(q.t), cy: Y(q.v), r: 1.2,
                                                      fill: t.col, opacity: 0.6 })));
    });
    TOOLS.forEach(t => {
      const src = reading()[t.k];
      const pts = src ? rowsIn(src.rows, s, get) : [];
      if (pts.length < 2) return;
      const a = { d: path(pts, false), fill: 'none', stroke: t.col,
                  'stroke-width': t.w, opacity: t.op };
      // the postmortem's runs are the anchored ones, which newer runs superseded
      if (day === 'yesterday') a['stroke-dasharray'] = EARLIER;
      svg.appendChild(el('path', a));
      // a dot at every reading, so each line shows its own cadence: hourly
      // tools every hour, the 3-hourly ones every third
      pts.forEach(q => svg.appendChild(el('circle', { cx: X(q.t), cy: Y(q.v), r: 1.6,
                                                      fill: t.col, opacity: t.op })));
    });
    const O = rowsIn(obRows, s, obsGet);
    if (O.length > 1) {
      svg.appendChild(el('path', { d: path(O, false), fill: 'none', stroke: 'var(--obs)', 'stroke-width': 2 }));
    }
    O.forEach(q => svg.appendChild(el('circle', { cx: X(q.t), cy: Y(q.v), r: 1.9, fill: 'var(--obs)' })));
  }

  /* One wind barb, the same convention the station map teaches: the staff
     points toward where the wind comes from, half barb five knots, full ten,
     pennant fifty. Small, because a row of them is a time series. */
  function barb(svg, x, y, wdir, wspd, col, op) {
    if (wspd == null) return;
    if (wspd < 3 || wdir == null) {
      svg.appendChild(el('circle', { cx: x, cy: y, r: 2.6, fill: 'none', stroke: col,
                                     'stroke-width': 1, opacity: op || 1 }));
      return;
    }
    const L = 13, a = (wdir - 90) * Math.PI / 180;
    const ux = Math.cos(a), uy = Math.sin(a);
    const px = -uy, py = ux;
    const g = el('g', { stroke: col, 'stroke-width': 1.1, fill: col, opacity: op || 1 });
    g.appendChild(el('line', { x1: x, y1: y, x2: x + ux * L, y2: y + uy * L }));
    let left = Math.round(wspd / 5) * 5, at = 1.0;
    const stepBack = 3.2 / L;
    while (left >= 50) {
      const bx = x + ux * L * at, by = y + uy * L * at;
      const cx2 = x + ux * L * (at - stepBack), cy2 = y + uy * L * (at - stepBack);
      g.appendChild(el('path', { d: `M${bx} ${by}L${bx + px * 5.5} ${by + py * 5.5}L${cx2} ${cy2}Z` }));
      left -= 50; at -= stepBack * 1.6;
    }
    while (left >= 10) {
      const bx = x + ux * L * at, by = y + uy * L * at;
      g.appendChild(el('line', { x1: bx, y1: by, x2: bx + px * 6, y2: by + py * 6 }));
      left -= 10; at -= stepBack;
    }
    if (left >= 5) {
      const bx = x + ux * L * at, by = y + uy * L * at;
      g.appendChild(el('line', { x1: bx, y1: by, x2: bx + px * 3.2, y2: by + py * 3.2 }));
    }
    svg.appendChild(g);
  }

  function barbPanel(svg, col, y0, s) {
    const wget = r => (r.wspd != null ? { wdir: r.wdir, wspd: r.wspd } : null);
    const ctx = context();
    const rows = [{ name: 'OBS', col: 'var(--obs)', pts: rowsIn(obRows, s, wget), ctxPts: [] }]
      .concat(TOOLS.map(t => {
        const src = reading()[t.k];
        return { name: t.short, col: t.col,
                 pts: src ? rowsIn(src.rows, s, wget) : [],
                 // a mark the newest cycle has not reached takes the anchored
                 // cycle's barb, dimmed, so the row is whole like the lines
                 ctxPts: ctx && ctx[t.k] ? rowsIn(ctx[t.k].rows, s, wget) : [] };
      }));
    svg.appendChild(txt('WIND, FROM', { x: col.x0, y: y0 - 5, class: 'axl',
                                        'font-size': 10, 'letter-spacing': '0.06em' }));
    const xd = col.x0 + (dayStart() - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0);
    const bh = rows.length * G.barbRow + 8;
    svg.appendChild(el('line', { x1: xd, y1: y0, x2: xd, y2: y0 + bh, stroke: 'var(--muted)', 'stroke-width': 1 }));
    rows.forEach((row, i) => {
      const y = y0 + 12 + i * G.barbRow;
      svg.appendChild(txt(row.name, { x: col.x0 - 4, y: y + 3, 'text-anchor': 'end', class: 'axl',
                                      'font-size': 9, fill: row.col }));
      // one barb every three hours over the 36-hour window; the nearest
      // reading to each mark, so an hourly tool and a 3-hourly one land on
      // the same grid
      const HR3 = 3 * 3600000;
      const nearest = (pts, t) => {
        let best = null;
        pts.forEach(q => { if (best == null || Math.abs(q.t - t) < Math.abs(best.t - t)) best = q; });
        return best && Math.abs(best.t - t) <= 5400000 ? best : null;
      };
      for (let t = Math.ceil(s[0] / HR3) * HR3; t <= s[1]; t += HR3) {
        const x = col.x0 + (t - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0);
        const cur = nearest(row.pts, t);
        if (cur) { barb(svg, x, y, cur.v.wdir, cur.v.wspd, row.col); continue; }
        const held = nearest(row.ctxPts, t);
        if (held) barb(svg, x, y, held.v.wdir, held.v.wspd, row.col, 0.55);
      }
    });
    return bh;
  }

  /* Six-hourly marks anchored on the day boundary, which is the station's
     local midnight, so the grid reads noon, 6p, midnight, 6a rather than
     wherever the UTC six-hour grid happens to fall in this zone. */
  function ticks(s) {
    const HR = 6 * 3600000, d0 = dayStart(), out = [];
    for (let t = d0 - Math.floor((d0 - s[0]) / HR) * HR; t <= s[1]; t += HR) {
      if (t >= s[0]) out.push(t);
    }
    return out;
  }

  function hourLabels(svg, col, y, s) {
    ticks(s).forEach(t => {
      const x = col.x0 + (t - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0);
      const lab = new Date(t).toLocaleTimeString('en-US', { timeZone: tz, hour: 'numeric' })
        .toLowerCase().replace(' am', 'a').replace(' pm', 'p').replace('12a', 'mdnt').replace('12p', 'noon');
      svg.appendChild(txt(lab, { x, y, 'text-anchor': 'middle', class: 'axl', 'font-size': 9 }));
    });
  }

  function hover(svg, s, H) {
    const rule = el('line', { y1: 14, y2: H, stroke: 'var(--muted)', 'stroke-width': 0.8, visibility: 'hidden' });
    svg.appendChild(rule);
    svg.addEventListener('mousemove', ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const x = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
      const col = G.C.find(cc => x >= cc.x0 && x <= cc.x1);
      if (!col) { rule.setAttribute('visibility', 'hidden'); tip.hide(); return; }
      rule.setAttribute('x1', x); rule.setAttribute('x2', x); rule.setAttribute('visibility', 'visible');
      const at = s[0] + (x - col.x0) / (col.x1 - col.x0) * (s[1] - s[0]);
      const near = rows => {
        let best = null;
        rowsIn(rows, s, r => r).forEach(q => { if (best == null || Math.abs(q.t - at) < Math.abs(best.t - at)) best = q; });
        return best && Math.abs(best.t - at) <= 5400000 ? best : null;
      };
      const fmtRow = r => (r == null ? '—'
        : (r.temp != null ? WXC.deg(r.temp) : '—') + ' · '
          + (r.dew != null ? WXC.deg(r.dew) : '—') + ' · '
          + (r.sky != null ? r.sky + '%' + (r.cover ? ' ' + r.cover : '') : '—') + ' · '
          + (r.wspd != null ? (r.wdir != null ? WXC.compass(r.wdir) + ' at ' : '') + Math.round(r.wspd * 1.15078) + ' mph' : '—'));
      const pack = rows => { const q = near(rows); return q && { temp: q.r.tempF, dew: q.r.dewF, sky: q.r.sky != null ? q.r.sky : OBS_PCT[q.r.cover], cover: q.r.cover, wdir: q.r.wdir, wspd: q.r.wspd }; };
      /* Tool by tool, each run with a reading at this hour, the newest first,
         named and drawn the way the main chart's crosshair lists them. */
      const lines = [[WXC.lineSwatch('var(--obs)', false) + 'Observed (METAR)', fmtRow(pack(obRows))]];
      const ctx = context();
      TOOLS.forEach(t => {
        const runs = [[reading()[t.k], day === 'yesterday'], [ctx && ctx[t.k], true]]
          .filter(([src]) => src).map(([src, early]) => [src, early, pack(src.rows)]).filter(r => r[2]);
        runs.forEach(([src, early, q], i) => lines.push([WXC.lineSwatch(t.col, early) + t.short + ' ' + runTag(src.cycle),
                                                         fmtRow(q), i ? null : 'gs']));
      });
      const when = new Date(at).toLocaleTimeString('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' });
      tip.show(ev, tip.rows(when + ' · temp · dew · sky · wind', lines,
                            day === 'today' ? 'each tool\u2019s newest run first; dotted, the run that stood at six the evening before yesterday'
                            : day === 'tomorrow' ? 'each tool\u2019s newest run'
                            : 'dotted, the runs that stood at six the evening before'));
    });
    svg.addEventListener('mouseleave', () => { rule.setAttribute('visibility', 'hidden'); tip.hide(); });
  }

  // published wind is knots; the panel reads in miles per hour
  const MPH = 1.15078;

  function draw() {
    if (!host || !adv || !ob) return;
    host.innerHTML = '';
    const s = span();
    const [tLo, tHi] = range(r => r.tempF, r => r.tempF, s, 10);
    const [dLo, dHi] = range(r => r.dewF, r => r.dewF, s, 8);
    const mph = r => (r.wspd != null ? r.wspd * MPH : null);
    const [, spdHi] = range(mph, mph, s, 14, 0);
    const col = G.C[0];
    let H = 32 + 4 * (G.panelH + G.gap) + (1 + TOOLS.length) * G.barbRow + 8 + 26;
    const svg = el('svg', { viewBox: '0 0 ' + G.W + ' ' + H, class: 'ts' });

    // who is which color, once, along the top, and on today's view what the dotted lines are
    let lx = G.W - 2;
    // a tool the snapshot does not carry, such as the HRRR off its grid, has no key
    const has = t => [reading(), context()].some(rd => rd && rd[t.k]);
    const key = [{ name: 'Observed (METAR)', col: 'var(--obs)', w: 2 }]
      .concat(TOOLS.filter(has).map(t => Object.assign({}, t, { dash: day === 'yesterday' ? EARLIER : null })))
      .concat(day === 'today' ? [{ name: 'Earlier runs', col: 'var(--muted)', w: 1.4, dash: EARLIER }] : []);
    key.reverse().forEach(t => {
      svg.appendChild(txt(t.name, { x: lx, y: 10, 'text-anchor': 'end', 'font-size': 9.5, fill: t.col }));
      lx -= t.name.length * 5.3 + 30;
      const a = { x1: lx + 4, y1: 6.5, x2: lx + 24, y2: 6.5, stroke: t.col, 'stroke-width': t.w };
      if (t.dash) a['stroke-dasharray'] = t.dash;
      svg.appendChild(el('line', a));
    });

    const deg = v => WXC.deg(v);
    let y = 32;
    seriesPanel(svg, col, y, s, 'Temperature', r => r.tempF, r => r.tempF, tLo, tHi, deg);
    y += G.panelH + G.gap;
    seriesPanel(svg, col, y, s, 'Sky cover', r => r.sky, r => OBS_PCT[r.cover], 0, 100, v => v + '%');
    y += G.panelH + G.gap;
    seriesPanel(svg, col, y, s, 'Dewpoint', r => r.dewF, r => r.dewF, dLo, dHi, deg);
    y += G.panelH + G.gap;
    seriesPanel(svg, col, y, s, 'Wind speed', mph, mph, 0, spdHi, v => Math.round(v) + ' mph');
    y += G.panelH + G.gap;
    const bh = barbPanel(svg, col, y, s);
    hourLabels(svg, col, y + bh + 14, s);
    hover(svg, s, y + bh);
    host.appendChild(svg);
    fitFull();

    const src = reading();
    const cyc = TOOLS.map(t => src[t.k] ? t.name + ' ' + clockOf(src[t.k].cycle) : null).filter(Boolean).join(', ');
    $('#advCap').textContent = '';
  }

  async function load() {
    const [a, o] = await Promise.all([
      WXD.get('advanced/' + sid + '.json').catch(() => null),
      WXD.get('obs/' + sid + '.json').catch(() => null),
    ]);
    adv = a && a.data; ob = o && o.data;
    // every report in the window: the chart rows, and the reports that carry
    // no temperature, which still carry the sky and the wind these panels draw
    obRows = ob && ob.rows ? ob.rows.concat(ob.rowsNoTemp || [])
      .sort((p, q) => (p.t < q.t ? -1 : p.t > q.t ? 1 : 0)) : null;
    unit = (adv && adv.unit) || 'F'; tz = (adv && adv.tz) || tz;
    const sect = $('#advSection');
    if (!adv || !ob || !ob.rows) { if (sect) sect.hidden = true; return false; }
    draw();
    return true;
  }

  /* Expanded, the column is fitted the way the station map is: to the
     window, floored at the width it already had in the page, and the card
     scrolls when the floor wins. Expanding must never make a thing smaller,
     which on a short window a bare fit-to-height does. */
  let restW = 0;
  function fitFull() {
    const card = $('#advCard'), svgEl = host && host.querySelector('svg');
    if (!card || !svgEl) return;
    if (!card.classList.contains('full')) {
      restW = host.clientWidth || restW;
      svgEl.style.width = svgEl.style.height = '';
      return;
    }
    const vb = svgEl.viewBox.baseVal;
    const availW = card.clientWidth - 24, availH = window.innerHeight - 46;
    const w = Math.min(availW, Math.max(availH * vb.width / vb.height, restW));
    svgEl.style.width = Math.round(w) + 'px';
    svgEl.style.height = Math.round(w * vb.height / vb.width) + 'px';
  }

  function init() {
    tip = WXC.tooltip();
    host = $('#advPanels');
    if (!host) return;
    const xh = $('#advExpand'), card = $('#advCard');
    if (xh && card && !xh.childElementCount) xh.appendChild(WXC.expander(card, 'Expand'));
    // the expander raises a resize on the way both in and out
    window.addEventListener('resize', fitFull);
    const DAYS = { advToday: 'today', advTmw: 'tomorrow', advYday: 'yesterday' };
    Object.entries(DAYS).forEach(([id, d]) => {
      const b = $('#' + id); if (!b) return;
      b.onclick = () => setDay(d);
    });
    /* The chart's own day buttons carry these panels with them, so switching
       the page to the day-ahead board switches everything on it. Listeners,
       not onclick, so the chart keeps its own handler. */
    [['dayToday', 'today'], ['dayTomorrow', 'tomorrow'], ['dayBoth', 'today']].forEach(([id, d]) => {
      const b = $('#' + id); if (!b) return;
      b.addEventListener('click', () => setDay(d));
    });
  }

  function setDay(d) {
    day = d;
    Object.entries({ advToday: 'today', advTmw: 'tomorrow', advYday: 'yesterday' })
      .forEach(([id, dd]) => { const b = $('#' + id); if (b) b.classList.toggle('on', dd === day); });
    draw();
    // the reasoning beside the panels follows the day they are showing: the
    // postmortem gets the discussion that stood at the moment it scores
    if (window.WXDiscussion && sid) {
      WXDiscussion.draw(sid, day === 'yesterday' && adv ? Date.parse(adv.anchor) : null)
        .catch(() => {});
    }
  }

  /* The page tells this module which station it is on. The panels are part
     of the page now, not a mode: they load and draw on their own, and the
     section stays hidden only where the archive has no bulletins to draw,
     which is every station abroad. */
  function station(s, zone) {
    if (s === sid) return;
    sid = s; tz = zone || tz; adv = ob = obRows = null;
    const sect = $('#advSection');
    load().then(ok => {
      if (sect) sect.hidden = !ok;
      // the resting width can only be read once the section is on the page;
      // during draw it was hidden and measured as nothing
      if (ok) requestAnimationFrame(fitFull);
    }).catch(() => { if (sect) sect.hidden = true; });
  }

  return { init, station };
})();
