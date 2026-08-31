/* The advanced panels on the city page: the physical ingredients of the day,
   observed against forecast.

   A temperature forecast is the end of a causal chain that runs through
   moisture, cloud and wind, and a reader deciding whether today's high will
   verify can watch those directly: if the blend's 82° needs unbroken sun and
   a late front, low cloud at noon is evidence before the thermometer has it.
   Each panel is one upstream variable, the tools' hourly lines under the
   station's own METAR readings, in the same colours and line styles the main
   chart uses for the same tools, over the same window the main chart shows,
   noon the day before to the end of the contract day.

   Two columns, temperature at the top of both, so each variable is read
   against the thing it drives without the eye travelling. Cloud and moisture
   on the left, wind on the right.

   Two days. Today runs against the newest standing cycles, with the cycles
   that stood at six the evening before drawn dashed over the hours before
   midnight, the same way the main chart carries as-issued lines. Yesterday
   runs entirely against those anchored cycles, the scorecard's anchor, so
   the postmortem judges the same forecast the standings judged.

   Data in: advanced/{SID}.json (the tools, from the archived bulletins and
   the NWS hourly product) and the obs snapshot the page already carries.
   Everything drawn is a published number; nothing here is a forecast of the
   site's own. */
window.WXAdv = (function () {
  'use strict';
  const { el, txt, h, $ } = WXC;

  // colour, weight and dash per tool, exactly as the main chart draws them
  const TOOLS = [
    { k: 'nws', name: 'Weather Service', col: 'var(--nws)', w: 2.4, dash: null, op: 0.95 },
    { k: 'nbm', name: 'Blend of Models', col: 'var(--nbm)', w: 2, dash: '5 4', op: 0.9 },
    { k: 'lamp', name: 'LAMP', col: 'var(--lamp)', w: 1.6, dash: '1 3', op: 0.9 },
    { k: 'mav', name: 'GFS MOS', col: 'var(--mav)', w: 2, dash: null, op: 0.9 },
  ];
  // METAR cover codes on the same axis as the bulletins' bands: the midpoint
  // of each code's okta range. VV is an obscured sky, which is total cover.
  const OBS_PCT = { CAVOK: 0, CLR: 0, SKC: 0, FEW: 19, SCT: 44, BKN: 75, OVC: 100, VV: 100 };

  let adv = null, ob = null, sid = null, tz = null, unit = 'F';
  let day = 'today', host = null, tip = null;

  /* Geometry. Two columns inside one 960-wide figure, kept near the height
     the single column had, so halving the width is what makes the vertical
     variation legible rather than the panels shrinking with it. */
  const G = { W: 960, C: [{ x0: 46, x1: 468 }, { x0: 546, x1: 952 }], panelH: 132, gap: 34, barbRow: 21 };

  const P2 = s => Date.parse(s);
  const reading = () => (day === 'today' ? adv.current : adv.yesterday);
  const context = () => (day === 'today' ? adv.yesterday : null);

  /* The same window the main chart shows: noon the day before through the end
     of the contract day. The postmortem keeps the shape, shifted back a day,
     so the two views are read the same way. */
  function span() {
    const mk = adv.markers, DAY = 86400000;
    return day === 'today' ? [P2(mk.winStart), P2(mk.dayEnd)]
                           : [P2(mk.winStart) - DAY, P2(mk.dayStart)];
  }
  const dayStart = () => (day === 'today' ? P2(adv.markers.dayStart) : P2(adv.markers.ydayStart));

  const stampMs = st => {
    const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)/.exec(st || '');
    return m ? Date.parse(m[1] + '-' + m[2] + '-' + m[3] + 'T' + m[4] + ':' + m[5] + ':00Z') : NaN;
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
    let vals = rowsIn(ob.rows, s, obsGet).map(q => q.v);
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
    // the hours before the contract day, from the cycles that stood at six the
    // evening before: dashed and dim, the way the main chart draws as-issued
    const ctx = context();
    if (ctx) TOOLS.forEach(t => {
      if (!ctx[t.k]) return;
      const pts = rowsIn(ctx[t.k].rows, s, get).filter(q => q.t <= dayStart());
      if (pts.length < 2) return;
      svg.appendChild(el('path', { d: path(pts, opts && opts.step), fill: 'none', stroke: t.col,
                                   'stroke-width': 1.2, 'stroke-dasharray': '2 3', opacity: 0.7 }));
    });
    TOOLS.forEach(t => {
      const src = reading()[t.k];
      const pts = src ? rowsIn(src.rows, s, get) : [];
      if (pts.length < 2) return;
      // a categorical tool draws in steps, so it never claims a smooth
      // evolution its bulletin does not publish
      const a = { d: path(pts, opts && opts.step), fill: 'none', stroke: t.col,
                  'stroke-width': t.w, opacity: t.op };
      if (t.dash) a['stroke-dasharray'] = t.dash;
      svg.appendChild(el('path', a));
    });
    const O = rowsIn(ob.rows, s, obsGet);
    if (!(opts && opts.dotsOnly) && O.length > 1) {
      svg.appendChild(el('path', { d: path(O, false), fill: 'none', stroke: 'var(--obs)', 'stroke-width': 2 }));
    }
    O.forEach(q => svg.appendChild(el('circle', { cx: X(q.t), cy: Y(q.v), r: 1.9, fill: 'var(--obs)' })));
  }

  /* One wind barb, the same convention the station map teaches: the staff
     points toward where the wind comes from, half barb five knots, full ten,
     pennant fifty. Small, because a row of them is a time series. */
  function barb(svg, x, y, wdir, wspd, col) {
    if (wspd == null) return;
    if (wspd < 3 || wdir == null) {
      svg.appendChild(el('circle', { cx: x, cy: y, r: 2.6, fill: 'none', stroke: col, 'stroke-width': 1 }));
      return;
    }
    const L = 13, a = (wdir - 90) * Math.PI / 180;
    const ux = Math.cos(a), uy = Math.sin(a);
    const px = -uy, py = ux;
    const g = el('g', { stroke: col, 'stroke-width': 1.1, fill: col });
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
    const rows = [{ name: 'OBS', col: 'var(--obs)', pts: rowsIn(ob.rows, s, wget) }]
      .concat(TOOLS.map(t => {
        const src = reading()[t.k];
        return { name: { nws: 'NWS', nbm: 'NBM', lamp: 'LAMP', mav: 'MOS' }[t.k], col: t.col,
                 pts: src ? rowsIn(src.rows, s, wget) : [] };
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
      for (let t = Math.ceil(s[0] / HR3) * HR3; t <= s[1]; t += HR3) {
        let best = null;
        row.pts.forEach(q => { if (best == null || Math.abs(q.t - t) < Math.abs(best.t - t)) best = q; });
        if (!best || Math.abs(best.t - t) > 5400000) continue;
        barb(svg, col.x0 + (t - s[0]) / (s[1] - s[0]) * (col.x1 - col.x0), y, best.v.wdir, best.v.wspd, row.col);
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
          + (r.wspd != null ? (r.wdir != null ? Math.round(r.wdir) + '° at ' : '') + Math.round(r.wspd) + ' kt' : '—'));
      const pack = rows => { const q = near(rows); return q && { temp: q.r.tempF, dew: q.r.dewF, sky: q.r.sky != null ? q.r.sky : OBS_PCT[q.r.cover], cover: q.r.cover, wdir: q.r.wdir, wspd: q.r.wspd }; };
      const lines = [['Observed (METAR)', fmtRow(pack(ob.rows))]]
        .concat(TOOLS.map(t => { const src = reading()[t.k]; return [t.name, fmtRow(src && pack(src.rows))]; }));
      const when = new Date(at).toLocaleTimeString('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' });
      tip.show(ev, tip.rows(when + ' · temp · dew · sky · wind', lines,
                            day === 'today' ? 'tools at their newest standing cycle'
                                            : 'tools as they stood at six the evening before'));
    });
    svg.addEventListener('mouseleave', () => { rule.setAttribute('visibility', 'hidden'); tip.hide(); });
  }

  function draw() {
    if (!host || !adv || !ob) return;
    host.innerHTML = '';
    const s = span();
    const [tLo, tHi] = range(r => r.tempF, r => r.tempF, s, 10);
    const [dLo, dHi] = range(r => r.dewF, r => r.dewF, s, 8);
    const [, spdHi] = range(r => r.wspd, r => r.wspd, s, 12, 0);
    const colH = 14 + 3 * (G.panelH + G.gap);
    const H = colH + 20;
    const svg = el('svg', { viewBox: '0 0 ' + G.W + ' ' + H, class: 'ts' });

    // who is which colour and dash, once, along the top
    let lx = G.W - 8;
    [{ name: 'Observed (METAR)', col: 'var(--obs)', dash: null, w: 2 }].concat(TOOLS.slice().reverse()).forEach(t => {
      svg.appendChild(txt(t.name, { x: lx, y: 10, 'text-anchor': 'end', 'font-size': 10, fill: t.col }));
      lx -= t.name.length * 5.6 + 32;
      const a = { x1: lx + 4, y1: 6.5, x2: lx + 26, y2: 6.5, stroke: t.col, 'stroke-width': t.w };
      if (t.dash) a['stroke-dasharray'] = t.dash;
      svg.appendChild(el('line', a));
    });

    const deg = v => WXC.deg(v);
    const c1 = G.C[0], c2 = G.C[1];
    let y = 32;
    seriesPanel(svg, c1, y, s, 'Temperature', r => r.tempF, r => r.tempF, tLo, tHi, deg);
    seriesPanel(svg, c2, y, s, 'Temperature', r => r.tempF, r => r.tempF, tLo, tHi, deg);
    y += G.panelH + G.gap;
    seriesPanel(svg, c1, y, s, 'Sky cover', r => r.sky, r => OBS_PCT[r.cover], 0, 100, v => v + '%',
                { step: true, dotsOnly: true });
    seriesPanel(svg, c2, y, s, 'Wind speed', r => r.wspd, r => r.wspd, 0, spdHi, v => v + ' kt');
    y += G.panelH + G.gap;
    seriesPanel(svg, c1, y, s, 'Dewpoint', r => r.dewF, r => r.dewF, dLo, dHi, deg);
    const bh = barbPanel(svg, c2, y, s);
    hourLabels(svg, c1, y + G.panelH + 14, s);
    hourLabels(svg, c2, Math.max(y + bh + 14, y + G.panelH + 14), s);
    hover(svg, s, y + G.panelH);
    host.appendChild(svg);

    const src = reading();
    const cyc = TOOLS.map(t => src[t.k] ? t.name + ' ' + clockOf(src[t.k].cycle) : null).filter(Boolean).join(', ');
    $('#advCap').textContent =
      (day === 'today'
        ? 'Each tool’s newest standing cycle under the station’s own METAR reports, over the same window as the '
          + 'chart above. The dim dashed lines before the day boundary are the cycles that stood at six the evening '
          + 'before, so the hours already scored keep their forecast. '
        : 'The postmortem reads each tool as it stood at six the evening before, the same moment the standings judge, '
          + 'under what the station then recorded. ')
      + 'Temperature, dewpoint and wind compare directly; sky cover is percent where a tool publishes percent, the '
      + 'band midpoint of its published code (FEW 19%, SCT 44%, BKN 75%, OVC 100%) where it publishes a code, and '
      + 'the Weather Service’s own sky wording mapped to the same bands. Issued ' + cyc + ', station time.';
  }

  async function load() {
    const [a, o] = await Promise.all([
      WXD.get('advanced/' + sid + '.json').catch(() => null),
      WXD.get('obs/' + sid + '.json').catch(() => null),
    ]);
    adv = a && a.data; ob = o && o.data;
    unit = (adv && adv.unit) || 'F'; tz = (adv && adv.tz) || tz;
    const sect = $('#advSection');
    if (!adv || !ob || !ob.rows) { if (sect) sect.hidden = true; return false; }
    draw();
    return true;
  }

  function init() {
    tip = WXC.tooltip();
    host = $('#advPanels');
    const btn = $('#advBtn'), sect = $('#advSection');
    if (!btn || !host) return;
    const xh = $('#advExpand'), card = $('#advCard');
    if (xh && card && !xh.childElementCount) xh.appendChild(WXC.expander(card, 'Expand'));
    btn.onclick = async () => {
      if (!sid) return;
      const on = btn.classList.toggle('on');
      sect.hidden = !on;
      if (on && !adv) {
        const ok = await load();
        if (!ok) { btn.classList.remove('on'); btn.disabled = true; btn.title = 'No bulletin elements archived for this station.'; }
      }
      if (on && adv) sect.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };
    ['advToday', 'advYday'].forEach(id => {
      const b = $('#' + id); if (!b) return;
      b.onclick = () => {
        day = id === 'advToday' ? 'today' : 'yesterday';
        $('#advToday').classList.toggle('on', day === 'today');
        $('#advYday').classList.toggle('on', day === 'yesterday');
        draw();
      };
    });
  }

  // the page tells this module which station it is on; a change resets the data
  function station(s, zone) {
    if (s === sid) return;
    sid = s; tz = zone || tz; adv = ob = null;
    const btn = $('#advBtn'), sect = $('#advSection');
    if (btn && btn.classList.contains('on')) { btn.classList.remove('on'); }
    if (btn) { btn.disabled = false; btn.title = ''; }
    if (sect) sect.hidden = true;
  }

  return { init, station };
})();
