/* The advanced panels on the city page: the physical ingredients of the day,
   observed against forecast.

   A temperature forecast is the end of a causal chain that runs through
   moisture, cloud and wind, and a reader deciding whether today's high will
   verify can watch those directly: if the blend's 82° needs unbroken sun and
   a late front, low cloud at noon is evidence before the thermometer has it.
   Each panel is one upstream variable, the tools' hourly lines under the
   station's own METAR readings, so "is the day following the script" is read
   per variable rather than inferred from the temperature alone.

   Two days. Today runs against the newest standing cycles. Yesterday runs
   against the cycles that stood at six the evening before, the scorecard's
   anchor, so the postmortem judges the same forecast the standings judged.

   Data in: advanced/{SID}.json (the tools, from the archived bulletins) and
   the obs snapshot the page already carries (METAR rows with dewpoint, wind
   and cover). Everything drawn is a published number; nothing here is a
   forecast of the site's own. */
window.WXAdv = (function () {
  'use strict';
  const { el, txt, h, $ } = WXC;

  const TOOLS = [
    { k: 'nbm', name: 'Blend of Models', col: 'var(--nbm)', w: 2.2 },   // the script the article starts from
    { k: 'lamp', name: 'Aviation guidance (LAMP)', col: 'var(--lamp)', w: 1.4 },
    { k: 'mav', name: 'GFS MOS', col: 'var(--mav)', w: 1.4 },
  ];
  // METAR cover codes on the same axis as the bulletins' bands: the midpoint
  // of each code's okta range. VV is an obscured sky, which is total cover.
  const OBS_PCT = { CAVOK: 0, CLR: 0, SKC: 0, FEW: 19, SCT: 44, BKN: 75, OVC: 100, VV: 100 };

  let adv = null, ob = null, sid = null, tz = null, unit = 'F';
  let day = 'today', host = null, tip = null;

  const P = { W: 960, L: 46, R: 908, panelH: 96, gap: 26, barbRow: 20 };

  const dayKey = () => (day === 'today' ? adv.markers.day : adv.markers.yesterday);
  const reading = () => (day === 'today' ? adv.current : adv.yesterday);

  // local midnight to midnight of the shown day, as epoch ms
  function span() {
    const d = dayKey();
    // the offset trick every page here uses: read the clock of the zone
    const probe = new Date(d + 'T12:00:00Z');
    const zoned = new Date(probe.toLocaleString('en-US', { timeZone: tz }));
    const off = probe - zoned;
    const t0 = Date.parse(d + 'T00:00:00Z') + off;
    return [t0, t0 + 86400000];
  }

  const X = (t, s) => P.L + (t - s[0]) / (s[1] - s[0]) * (P.R - P.L);

  function rowsIn(rows, s, map) {
    return (rows || []).map(r => ({ t: Date.parse(r.t), v: map(r), r }))
      .filter(q => q.v != null && q.t >= s[0] - 1800000 && q.t <= s[1] + 1800000);
  }

  function axis(svg, y0, s, label, lo, hi, fmt) {
    svg.appendChild(txt(label.toUpperCase(), { x: P.L + 4, y: y0 - 4, class: 'axl',
                                               'font-size': 10, 'letter-spacing': '0.06em' }));
    [lo, (lo + hi) / 2, hi].forEach(v => {
      const y = y0 + P.panelH - (v - lo) / (hi - lo) * P.panelH;
      svg.appendChild(el('line', { x1: P.L, y1: y, x2: P.R, y2: y, stroke: 'var(--rule)', 'stroke-width': 0.5 }));
      svg.appendChild(txt(fmt(v), { x: P.R + 4, y: y + 3, class: 'axl', 'font-size': 9 }));
    });
    for (let hh = 0; hh <= 24; hh += 6) {
      const x = P.L + hh / 24 * (P.R - P.L);
      svg.appendChild(el('line', { x1: x, y1: y0, x2: x, y2: y0 + P.panelH, stroke: 'var(--rule)', 'stroke-width': 0.5 }));
    }
  }

  function seriesPanel(svg, y0, s, label, get, obsGet, lo, hi, fmt, step) {
    axis(svg, y0, s, label, lo, hi, fmt);
    const Y = v => y0 + P.panelH - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * P.panelH;
    TOOLS.forEach(t => {
      const src = reading()[t.k];
      const pts = src ? rowsIn(src.rows, s, get) : [];
      if (pts.length < 2) return;
      // a categorical tool draws in steps, so it never claims a smooth
      // evolution its bulletin does not publish
      const d = pts.map((q, i) => {
        if (!i) return 'M' + X(q.t, s) + ' ' + Y(q.v);
        return step ? ('L' + X(q.t, s) + ' ' + Y(pts[i - 1].v) + 'L' + X(q.t, s) + ' ' + Y(q.v))
                    : ('L' + X(q.t, s) + ' ' + Y(q.v));
      }).join('');
      svg.appendChild(el('path', { d, fill: 'none', stroke: t.col, 'stroke-width': t.w,
                                   opacity: t.k === 'nbm' ? 1 : 0.85 }));
    });
    rowsIn(ob.rows, s, obsGet).forEach(q => {
      svg.appendChild(el('circle', { cx: X(q.t, s), cy: Y(q.v), r: 2.4, fill: 'var(--obs)' }));
    });
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
    const tipx = x + ux * L, tipy = y + uy * L;
    const g = el('g', { stroke: col, 'stroke-width': 1.1, fill: col });
    g.appendChild(el('line', { x1: x, y1: y, x2: tipx, y2: tipy }));
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

  function barbPanel(svg, y0, s) {
    const rows = [{ name: 'Observed', col: 'var(--obs)', pts: rowsIn(ob.rows, s, r => (r.wspd != null ? { wdir: r.wdir, wspd: r.wspd } : null)) }]
      .concat(TOOLS.map(t => {
        const src = reading()[t.k];
        return { name: t.name, col: t.col,
                 pts: src ? rowsIn(src.rows, s, r => (r.wspd != null ? { wdir: r.wdir, wspd: r.wspd } : null)) : [] };
      }));
    svg.appendChild(txt('WIND, FROM', { x: P.L + 4, y: y0 - 4, class: 'axl',
                                        'font-size': 10, 'letter-spacing': '0.06em' }));
    const SHORT = { 'Observed': 'OBS', 'Blend of Models': 'NBM', 'Aviation guidance (LAMP)': 'LAMP', 'GFS MOS': 'MOS' };
    rows.forEach((row, i) => {
      const y = y0 + 14 + i * P.barbRow;
      svg.appendChild(txt(SHORT[row.name] || row.name, { x: P.R + 4, y: y + 3, class: 'axl',
                                                         'font-size': 9, fill: row.col }));
      // one barb every two hours: the nearest reading to each mark, so an
      // hourly tool and a 3-hourly one land on the same grid
      for (let hh = 0; hh <= 24; hh += 2) {
        const at = s[0] + hh * 3600000;
        let best = null;
        row.pts.forEach(q => { if (best == null || Math.abs(q.t - at) < Math.abs(best.t - at)) best = q; });
        if (!best || Math.abs(best.t - at) > 5400000) continue;
        barb(svg, P.L + hh / 24 * (P.R - P.L), y, best.v.wdir, best.v.wspd, row.col);
      }
    });
    return 14 + rows.length * P.barbRow + 4;
  }

  function hourLabels(svg, y, s) {
    for (let hh = 0; hh <= 24; hh += 6) {
      const x = P.L + hh / 24 * (P.R - P.L);
      svg.appendChild(txt(hh === 24 ? 'midnight' : (hh === 0 ? '' : (hh % 12 || 12) + (hh < 12 ? 'am' : 'pm')),
                          { x, y, 'text-anchor': 'middle', class: 'axl', 'font-size': 9 }));
    }
  }

  function hover(svg, s, H) {
    const rule = el('line', { y1: 0, y2: H, stroke: 'var(--muted)', 'stroke-width': 0.8, visibility: 'hidden' });
    svg.appendChild(rule);
    svg.addEventListener('mousemove', ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const x = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
      if (x < P.L || x > P.R) { rule.setAttribute('visibility', 'hidden'); tip.hide(); return; }
      rule.setAttribute('x1', x); rule.setAttribute('x2', x); rule.setAttribute('visibility', 'visible');
      const at = s[0] + (x - P.L) / (P.R - P.L) * (s[1] - s[0]);
      const near = (rows, get) => {
        let best = null;
        rowsIn(rows, s, get).forEach(q => { if (best == null || Math.abs(q.t - at) < Math.abs(best.t - at)) best = q; });
        return best && Math.abs(best.t - at) <= 5400000 ? best : null;
      };
      const fmtRow = r => (r == null ? '—'
        : (r.temp != null ? WXC.deg(r.temp) : '—') + ' · '
          + (r.dew != null ? WXC.deg(r.dew) : '—') + ' · '
          + (r.sky != null ? r.sky + '%' + (r.cover ? ' ' + r.cover : '') : '—') + ' · '
          + (r.wspd != null ? (r.wdir != null ? r.wdir + '° at ' : '') + Math.round(r.wspd) + ' kt' : '—'));
      const pack = rows => { const q = near(rows, r2 => r2); return q && { temp: q.r.tempF, dew: q.r.dewF, sky: q.r.sky != null ? q.r.sky : OBS_PCT[q.r.cover], cover: q.r.cover, wdir: q.r.wdir, wspd: q.r.wspd }; };
      const lines = [['Observed (METAR)', fmtRow(pack(ob.rows))]]
        .concat(TOOLS.map(t => { const src = reading()[t.k]; return [t.name, fmtRow(src && pack(src.rows))]; }));
      const when = new Date(at).toLocaleTimeString('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' });
      tip.show(ev, tip.rows(when + ' · temp · dew · sky · wind', lines,
                            day === 'today' ? 'tools at their newest standing cycle'
                                            : 'tools as they stood at six the evening before'));
    });
    svg.addEventListener('mouseleave', () => { rule.setAttribute('visibility', 'hidden'); tip.hide(); });
  }

  /* An axis range from the day's own values, padded and rounded, with a
     floor on the spread so a flat day does not become a magnified wiggle. */
  function range(get, obsGet, s, minSpan, floor) {
    let vals = rowsIn(ob.rows, s, obsGet).map(q => q.v);
    TOOLS.forEach(t => { const src = reading()[t.k];
      if (src) vals = vals.concat(rowsIn(src.rows, s, get).map(q => q.v)); });
    if (!vals.length) return [0, minSpan];
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.15);
    lo = Math.floor((lo - pad) / 5) * 5; hi = Math.ceil((hi + pad) / 5) * 5;
    if (hi - lo < minSpan) hi = lo + minSpan;
    if (floor != null) lo = Math.max(lo, floor);
    return [lo, hi];
  }

  function draw() {
    if (!host || !adv || !ob) return;
    host.innerHTML = '';
    const s = span();
    const [tLo, tHi] = range(r => r.tempF, r => r.tempF, s, 15);
    const [degLo, degHi] = range(r => r.dewF, r => r.dewF, s, unit === 'C' ? 8 : 15);
    const [, spdHi] = range(r => r.wspd, r => r.wspd, s, 20, 0);
    const heights = [P.panelH + P.gap, P.panelH + P.gap, P.panelH + P.gap, P.panelH + P.gap];
    const H = heights.reduce((a, b) => a + b, 0) + 14 + (1 + TOOLS.length) * P.barbRow + 56;
    const svg = el('svg', { viewBox: '0 0 ' + P.W + ' ' + H, class: 'ts' });
    // who is which colour, once, at the top
    let lx = P.R;
    [{ name: 'Observed (METAR)', col: 'var(--obs)' }].concat(TOOLS.slice().reverse()).forEach(t => {
      const label = txt(t.name, { x: lx, y: 10, 'text-anchor': 'end', 'font-size': 10, fill: t.col });
      svg.appendChild(label);
      lx -= t.name.length * 6 + 26;
      svg.appendChild(el('circle', { cx: lx + 16, cy: 6.5, r: 3, fill: t.col }));
    });
    let y = 30;
    seriesPanel(svg, y, s, 'Temperature', r => r.tempF, r => r.tempF, tLo, tHi, v => WXC.deg(v), false);
    y += P.panelH + P.gap;
    seriesPanel(svg, y, s, 'Dewpoint', r => r.dewF, r => r.dewF, degLo, degHi, v => WXC.deg(v), false);
    y += P.panelH + P.gap;
    seriesPanel(svg, y, s, 'Sky cover', r => r.sky, r => OBS_PCT[r.cover], 0, 100, v => v + '%', true);
    y += P.panelH + P.gap;
    seriesPanel(svg, y, s, 'Wind speed', r => r.wspd, r => r.wspd, 0, spdHi, v => v + ' kt', false);
    y += P.panelH + P.gap;
    y += barbPanel(svg, y, s);
    hourLabels(svg, y + 10, s);
    hover(svg, s, y);
    host.appendChild(svg);

    const src = reading();
    const cyc = TOOLS.map(t => src[t.k] ? t.name + ' ' + src[t.k].cycle : null).filter(Boolean).join(', ');
    $('#advCap').textContent =
      (day === 'today'
        ? 'Each tool’s newest standing cycle under the station’s own METAR reports. '
        : 'The postmortem reads each tool as it stood at six the evening before, the same moment the standings judge, under what the station then recorded. ')
      + 'Temperature, dewpoint and wind compare directly; sky cover is percent where a tool publishes percent and the '
      + 'band midpoint of its published code (FEW 19%, SCT 44%, BKN 75%, OVC 100%) where it publishes a code, stepped '
      + 'to show the cadence. Cycles ' + cyc + '.';
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
