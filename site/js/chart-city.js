/* The city chart: observed against the forecasts that were standing before
   the day began, on one canvas through the end of the contract day.

   Data in (snapshots): obs/{STATION}.json, forecast/{STATION}.json and
   summary.json for the pickers. Everything is presentation from here on.
   Scales and axes are hand-rolled so the page has no dependencies.

   Series: observed METARs (what settlement reads); the standing NWS hourly
   forecast; NBM and LAMP hourly guidance; the as-issued lines from the
   archive (each source's last cycle before local midnight), which cover the
   elapsed part of the day where only observations exist otherwise; and the
   optional show-yesterday overlay. Level lines are each source's forecast
   high and low FOR the day, from those as-issued cycles. The market layer
   (ladders, strike chips, price panels) is drawn only when WXM.on(). */
window.WXCity = (() => {
  const { el, txt, h, $, clock, dateShort, hourTicks, P, deg } = WXC;
  const COL = { obs: 'var(--obs)', nws: 'var(--nws)', nbm: 'var(--nbm)', lamp: 'var(--lamp)', mav: 'var(--mav)' };
  const NAME = { nws: 'Weather Service', nbm: 'Blend of Models', lamp: 'LAMP', mav: 'GFS MOS' };
  const HP = ['#b2182b', '#7f1d1d', '#d6604d', '#a63603', '#e77f6b'];
  const LP = ['#08519c', '#08306b', '#4393c3', '#2166ac', '#7db8dc'];

  let cur = null, checked = new Set(), showYday = false, HV = null, tip = null;
  let summary = null, snaps = {}, svgId = 'chart', onSelect = null;

  function layout(market) {
    return market
      ? { W: 960, H: 655, L: 52, R: 610, T: 36, B: 346, PH0: 404, PH1: 494, PL0: 526, PL1: 616,
          GV: 614, GL0: 646, GL1: 668, GN: 672, LX: 790, LW: 130 }
      : { W: 960, H: 390, L: 52, R: 760, T: 36, B: 346, GV: 764, GL0: 796, GL1: 818, GN: 822 };
  }

  // ---- pickers: two small maps replace a dropdown. With the market layer
  //      on, dots carry the landing map's divergence encoding; off, they
  //      show observed-so-far against the day's NWS forecast high.
  function pickDot(svg, c, X, Y, scale) {
    const on = c.station === cur;
    const v = dotValue(c);
    const r = (v == null ? 7 : 8 + 9 * Math.min(Math.abs(v), 5) / 5) * scale;
    const g = el('g', { style: 'cursor:pointer' });
    g.appendChild(el('circle', { cx: X, cy: Y, r: r + 3, fill: 'var(--panel)', 'fill-opacity': .95 }));
    g.appendChild(el('circle', { cx: X, cy: Y, r, fill: v == null ? 'var(--line)' : (v > 0 ? 'var(--warm)' : (v < 0 ? 'var(--cool)' : 'var(--muted)')),
      'fill-opacity': .95, stroke: on ? 'var(--navy)' : 'var(--ink)', 'stroke-width': on ? 4 : .8 }));
    const t = el('title'); t.textContent = c.city + ' (' + c.station + ')' + (v != null ? ' · ' + (v > 0 ? '+' : '') + v + '°' : '');
    g.appendChild(t);
    if (on) g.appendChild(txt(c.city, { x: X, y: Y - r - 9, 'text-anchor': 'middle', 'font-size': 26, 'font-weight': 700,
      fill: 'var(--navy)', stroke: 'var(--panel)', 'stroke-width': 6, 'paint-order': 'stroke', 'stroke-linejoin': 'round' }));
    g.onclick = () => select(c.station);
    svg.appendChild(g);
  }
  function dotValue(c) {
    if (WXM.on()) { const m = WXM.implied(c); return m ? m.divHigh : null; }
    const ref = c.nwsIssuedHigh != null ? c.nwsIssuedHigh : c.nwsHighToday;
    return (c.obsHighSoFar != null && ref != null) ? Math.round((c.obsHighSoFar - ref) * 10) / 10 : null;
  }
  function drawPick(base) {
    const svg = $('#pick'); if (!svg) return;
    const pt = $('#pickTitle');
    if (pt) pt.textContent = WXM.on()
      ? 'United States — dot colour and size: the placeholder implied high against tomorrow’s NWS forecast (not a market value)'
      : 'United States — dot colour and size: observed so far against the NWS high issued for the day';
    svg.innerHTML = '';
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': 1 }));
    summary.cities.filter(c => c.onConus).forEach(c => pickDot(svg, c, c.px, c.py, 1));
    const w = $('#pickW'); if (!w) return;
    w.innerHTML = '';
    w.appendChild(el('rect', { x: 0, y: 0, width: 960, height: 480, fill: 'var(--map-sea)' }));
    w.appendChild(el('path', { d: base.worldPaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': .8 }));
    summary.cities.filter(c => !c.onConus).forEach(c => pickDot(w, c, c.wx, c.wy, 1.2));
  }

  const city = () => summary.cities.find(x => x.station === cur);

  async function select(sid, push) {
    cur = sid; checked = new Set();
    if (push !== false) { const u = new URL(location.href); u.searchParams.set('station', sid); history.replaceState(null, '', u); }
    if (onSelect) onSelect(sid);
    const r = await WXD.getAll([`forecast/${sid}.json`, `obs/${sid}.json`]);
    snaps = { fc: r[`forecast/${sid}.json`], ob: r[`obs/${sid}.json`] };
    const st = $('#chartStatus'); if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([snaps.ob, snaps.fc], 10)); }
    if (WXM.on()) {
      const c = city(), lv = levelsFor(c), lad = WXM.ladder(c, { high: lv.high, low: lv.low });
      ['high', 'low'].forEach(m => { const atm = lad[m].reduce((a, b) => Math.abs(b.yes - 50) < Math.abs(a.yes - 50) ? b : a); checked.add(m[0] + ':' + atm.strike); });
    }
    if (summary.base) drawPick(summary.base);
    drawStrikeRow(); draw();
  }

  // the day's reference high and low for the ladder: NWS as issued, else standing
  function levelsFor(c) {
    const fc = (snaps.fc && snaps.fc.data) || {};
    const ai = (fc.asIssued || {}).nws || {};
    return { high: ai.highToday != null ? ai.highToday : (fc.nws || {}).highToday, low: ai.lowToday != null ? ai.lowToday : (fc.nws || {}).lowToday };
  }

  function skColor(side, K, lad) {
    const i = lad[side === 'h' ? 'high' : 'low'].findIndex(L => L.strike === K);
    return (side === 'h' ? HP : LP)[((i % 5) + 5) % 5];
  }
  function drawStrikeRow() {
    const row = $('#skRow'); if (!row) return;
    row.innerHTML = '';
    if (!WXM.on()) return;
    const c = city(), lv = levelsFor(c), lad = WXM.ladder(c, { high: lv.high, low: lv.low });
    [['high', 'High strikes', 'h', '>'], ['low', 'Low strikes', 'l', '<']].forEach(([m, lab, pfx, cmp]) => {
      const div = h('div', {}, [h('span', { class: 'lbl2', text: lab })]);
      lad[m].forEach(L => {
        const key = pfx + ':' + L.strike;
        const b = h('button', { class: 'sk' + (checked.has(key) ? ' on' : ''), text: cmp + L.strike + '°' });
        b.style.setProperty('--c', skColor(pfx, L.strike, lad));
        b.onclick = () => { checked.has(key) ? checked.delete(key) : checked.add(key); b.classList.toggle('on'); draw(); };
        div.appendChild(b);
      });
      row.appendChild(div);
    });
    row.appendChild(h('div', { class: 'cap', text: 'Strike ladder: ' + WXM.LABEL + '.' }));
  }

  function draw() {
    const c = city();
    const fc = (snaps.fc && snaps.fc.data) || null;
    const ob = (snaps.ob && snaps.ob.data) || null;
    const market = WXM.on();
    const S = layout(market);
    const svg = $('#' + svgId);
    svg.setAttribute('viewBox', `0 0 ${S.W} ${S.H}`);
    svg.innerHTML = '';
    const g = el('g'); svg.appendChild(g);
    const tz = c.tz, unit = c.unit;
    const M = (fc && fc.markers) || c.markers;
    if (!M) { g.appendChild(txt('No data available for this station.', { x: S.L + 8, y: S.T + 16, class: 'axl' })); return; }
    const w0 = P(M.winStart), d0 = P(M.dayStart), d1 = P(M.dayEnd);
    const val = r => (unit === 'F' ? r.tempF : (r.tempC != null ? r.tempC : (r.tempF - 32) * 5 / 9));
    const rows = a => (a || []).map(r => ({ t: P(r.t), v: val(r) }));
    const inWin = a => a.filter(p => p.t >= w0 && p.t <= d1);

    const O = inWin(rows(ob && ob.rows));
    const F = inWin(rows(fc && fc.nws && fc.nws.hourly));
    const N = inWin(rows(fc && fc.nbm && fc.nbm.hourly));
    const LA = inWin(rows(fc && fc.lamp && fc.lamp.hourly));
    const AI = (fc && fc.asIssued) || {};
    const A = inWin(rows(AI.nws && AI.nws.rows)), NA = inWin(rows(AI.nbm && AI.nbm.rows)), LAI = inWin(rows(AI.lamp && AI.lamp.rows));
    const YD = (fc && fc.yesterday) || {};

    // ---- show-yesterday overlay, shifted exactly 24 h so the days line up by clock time
    const DAY = 864e5, ySeries = [];
    if (showYday) {
      const yObs = rows(ob && ob.rows).filter(p => p.t >= w0 - DAY && p.t < d0).map(p => ({ t: p.t + DAY, v: p.v })).filter(p => p.t >= w0 && p.t <= d1);
      if (yObs.length) ySeries.push({ nm: 'Yesterday observed', pts: yObs, col: 'var(--muted)', w: 1.7, dash: null, op: .9 });
      [['nws', 'Yesterday NWS as issued', '#8fbf95'], ['nbm', 'Yesterday NBM as issued', '#d4a86a'], ['lamp', 'Yesterday LAMP as issued', '#b3a4e0']].forEach(([k, nm, col]) => {
        const y = YD[k]; if (!y) return;
        const pts = rows(y.rows).map(p => ({ t: p.t + DAY, v: p.v })).filter(p => p.t >= w0 && p.t <= d1);
        if (pts.length) ySeries.push({ nm, pts, col, w: 1.5, dash: '6 3', op: .85 });
      });
    }

    // ---- levels for the day: each source's forecast high and low as issued
    //      before the day began; the standing official NWS value where no
    //      pre-day cycle exists yet (the archive is young for that source)
    const levels = [];
    const addLevel = (k, hi, lo, issued) => {
      const tag = issued ? ' (issued)' : '';
      if (hi != null) levels.push({ v: hi, nm: NAME[k] + tag, col: COL[k], k });
      if (lo != null) levels.push({ v: lo, nm: NAME[k] + tag, col: COL[k], k });
    };
    ['nws', 'nbm', 'lamp', 'mav'].forEach(k => {
      const ai = AI[k];
      if (ai && (ai.highToday != null || ai.lowToday != null)) addLevel(k, ai.highToday, ai.lowToday, ai.preDay || ai.levelPreDay);
      else if (fc && fc[k] && (fc[k].highToday != null || fc[k].lowToday != null)) addLevel(k, fc[k].highToday, fc[k].lowToday, false);
    });

    const lad = market ? WXM.ladder(c, levelsFor(c)) : null;
    const picked = market ? [...checked].map(k => { const [pfx, K] = k.split(':'); return { side: pfx, K: +K, col: skColor(pfx, +K, lad) }; }).sort((a, b) => b.K - a.K) : [];

    // ---- scales
    const x = t => S.L + (t - w0) / (d1 - w0) * (S.R - S.L);
    const step = unit === 'F' ? 5 : 2;
    const temps = [...O, ...F, ...N, ...LA, ...A, ...NA, ...LAI].map(p => p.v)
      .concat(ySeries.flatMap(s => s.pts.map(p => p.v))).concat(levels.map(l => l.v))
      .concat(lad ? lad.high.map(l => l.strike).concat(lad.low.map(l => l.strike)) : []);
    if (!temps.length) temps.push(unit === 'F' ? 70 : 20);
    const lo = Math.floor(Math.min(...temps) / step) * step - step / 2, hi = Math.ceil(Math.max(...temps) / step) * step + step / 2;
    const y = v => S.B - (v - lo) / (hi - lo) * (S.B - S.T);
    const lx = p => S.LX + (p / 100) * S.LW;
    const rightEdge = market ? S.LX + S.LW : S.R;

    // ---- night shading, outside the contract day's daylight
    const sr = M.sunrise ? P(M.sunrise) : null, ss = M.sunset ? P(M.sunset) : null;
    if (sr) g.appendChild(el('rect', { x: x(w0), y: S.T, width: Math.max(0, x(sr) - x(w0)), height: S.B - S.T, fill: 'var(--night)' }));
    if (ss) g.appendChild(el('rect', { x: x(ss), y: S.T, width: Math.max(0, x(d1) - x(ss)), height: S.B - S.T, fill: 'var(--night)' }));

    // ---- gridlines and axes
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      g.appendChild(el('line', { x1: S.L, x2: S.R, y1: y(v), y2: y(v), class: 'grid' }));
      if (market) g.appendChild(el('line', { x1: S.LX, x2: S.LX + S.LW, y1: y(v), y2: y(v), class: 'grid', opacity: .6 }));
      g.appendChild(txt(v + '°', { x: S.L - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'ax' }));
    }
    hourTicks(w0, d1, tz).forEach(tk => {
      g.appendChild(el('line', { x1: x(tk.t), x2: x(tk.t), y1: S.T, y2: S.B, class: 'grid' }));
      g.appendChild(txt(tk.label, { x: x(tk.t), y: S.B + 15, 'text-anchor': 'middle', class: 'ax' }));
    });

    // ---- day markers
    const marks = [['midnight', d0, 'var(--muted)', null], ['sunrise', sr, '#e0a020', '3 3'], ['sunset', ss, '#e0a020', '3 3'], ['day end', d1, 'var(--muted)', null]];
    if (market && M.listed) marks.unshift(['listed', P(M.listed), 'var(--muted)', null]);
    marks.forEach(([lb, t, col, dash]) => {
      if (t == null || t < w0 || t > d1) return;
      const a = { x1: x(t), x2: x(t), y1: S.T, y2: S.B, stroke: col, 'stroke-width': lb === 'midnight' ? 1.1 : 0.9 };
      if (dash) a['stroke-dasharray'] = dash;
      g.appendChild(el('line', a));
      g.appendChild(txt(lb, { x: x(t), y: S.T - 5, 'text-anchor': 'middle', class: 'mklab' }));
    });

    // ---- checked strikes: level lines across every panel
    picked.forEach(pk => {
      g.appendChild(el('line', { x1: S.L, x2: rightEdge, y1: y(pk.K), y2: y(pk.K), stroke: pk.col, 'stroke-width': 1.3, opacity: .5 }));
      g.appendChild(txt((pk.side === 'h' ? '>' : '<') + pk.K + '°', { x: S.R - 6, y: y(pk.K) - 3, 'text-anchor': 'end', 'font-size': 9, 'font-weight': 700, fill: pk.col, opacity: .85 }));
    });

    // ---- level lines across the contract day, values and names in the gutter
    levels.forEach(L => g.appendChild(el('line', { x1: x(d0), x2: S.R, y1: y(L.v), y2: y(L.v), stroke: L.col, 'stroke-width': 1.25, opacity: .85 })));
    const srt = levels.slice().sort((a, b) => b.v - a.v);
    const gap = 13; let prev = -1e9;
    const labY = srt.map(L => { let yy = Math.max(y(L.v), S.T + 6); if (yy - prev < gap) yy = prev + gap; prev = yy; return yy; });
    srt.forEach((L, i) => {
      g.appendChild(txt(L.v.toFixed(unit === 'F' ? 0 : 1), { x: S.GV, y: y(L.v) + 3.5, class: 'lvlval', fill: L.col }));
      g.appendChild(el('line', { x1: S.GL0, x2: S.GL1, y1: y(L.v), y2: labY[i], stroke: L.col, 'stroke-width': .7, opacity: .5 }));
      g.appendChild(txt(L.nm, { x: S.GN, y: labY[i] + 3.5, class: 'lvlnm', fill: L.col }));
    });
    if (levels.length) g.appendChild(el('line', { x1: S.R, x2: S.R, y1: S.T, y2: S.B, stroke: 'var(--rule)', 'stroke-width': .9 }));
    if (unit === 'C') g.appendChild(txt(N.length ? 'Non-US station: no NWS forecast; NBM guidance covers Canada.' : 'Non-US station: US government feeds carry observations only.',
      { x: S.L + 8, y: S.T + 16, class: 'axl' }));

    // ---- series
    const line = (pts, attrs) => el('path', Object.assign({ d: pts.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ',' + y(p.v).toFixed(1)).join(''),
      fill: 'none', 'stroke-width': 2, 'stroke-linejoin': 'round' }, attrs));
    ySeries.forEach(s => { const a = { stroke: s.col, 'stroke-width': s.w, opacity: s.op }; if (s.dash) a['stroke-dasharray'] = s.dash; g.appendChild(line(s.pts, a)); });
    if (showYday && !YD.nws && !YD.nbm) g.appendChild(txt('Yesterday’s as-issued forecast appears here once the archive is a day old.', { x: S.L + 8, y: S.T + 30, class: 'axl' }));
    if (LAI.length) g.appendChild(line(LAI, { stroke: COL.lamp, 'stroke-width': 1.2, 'stroke-dasharray': '2 3', opacity: .7 }));
    if (NA.length) g.appendChild(line(NA, { stroke: COL.nbm, 'stroke-width': 1.3, 'stroke-dasharray': '2 3', opacity: .75 }));
    if (A.length) {
      g.appendChild(line(A, { stroke: COL.nws, 'stroke-width': 1.4, 'stroke-dasharray': '2 3', opacity: .8 }));
      const ai = AI.nws;
      g.appendChild(txt((ai.preDay ? 'issued ' : 'first archived cycle, ') + clock(A[0].t, tz), { x: x(A[0].t) + 6, y: y(A[0].v) + 14, class: 'mklab', fill: COL.nws }));
    }
    if (LA.length) g.appendChild(line(LA, { stroke: COL.lamp, 'stroke-width': 1.6, 'stroke-dasharray': '1 3', opacity: .9 }));
    if (N.length) g.appendChild(line(N, { stroke: COL.nbm, 'stroke-width': 2, 'stroke-dasharray': '5 4', opacity: .9 }));
    if (F.length) g.appendChild(line(F, { stroke: COL.nws, 'stroke-width': 2.4, opacity: .95 }));
    if (O.length) {
      g.appendChild(line(O, { stroke: COL.obs, 'stroke-width': 2 }));
      O.forEach(p => g.appendChild(el('circle', { cx: x(p.t), cy: y(p.v), r: 1.9, fill: COL.obs })));
    }

    // ---- as-of marker
    const asof = snaps.ob && snaps.ob.asof;
    if (asof && asof > w0 && asof < d1) {
      g.appendChild(el('line', { x1: x(asof), x2: x(asof), y1: S.T, y2: market ? S.PL1 : S.B, stroke: 'var(--muted)', 'stroke-dasharray': '3 3' }));
      g.appendChild(txt('data as of', { x: x(asof) + 5, y: S.T + 30, class: 'axl' }));
    }

    // ---- observed extremes so far, contract day only
    const Oday = O.filter(p => p.t >= d0 && p.t < d1);
    if (Oday.length) {
      const hi2 = Oday.reduce((a, b) => b.v > a.v ? b : a), lo2 = Oday.reduce((a, b) => b.v < a.v ? b : a);
      [['Obs high ', hi2, 'var(--warm)', -5], ['Obs low ', lo2, 'var(--cool)', 13]].forEach(([lb, pt, col, dy]) => {
        g.appendChild(el('line', { x1: x(w0), x2: rightEdge, y1: y(pt.v), y2: y(pt.v), stroke: col, 'stroke-width': .9, 'stroke-dasharray': '2 2', opacity: .75 }));
        g.appendChild(txt(lb + pt.v.toFixed(1) + '°', { x: x(w0) + 5, y: y(pt.v) + dy, class: 'axl', fill: col, 'font-weight': 700 }));
      });
    }

    // ---- the market layer: ladders on the shared axis, price panels below
    const priceSer = [];
    if (market && lad) {
      g.appendChild(txt('Strike ladders (' + WXM.LABEL + ')', { x: 946, y: S.T - 8, 'text-anchor': 'end', class: 'axl' }));
      [['high', '>'], ['low', '<']].forEach(([m, cmp]) => {
        lad[m].forEach(L => {
          const yy = y(L.strike), gw = L.yes / 100 * S.LW;
          g.appendChild(el('rect', { x: S.LX, y: yy - 5.5, width: Math.max(gw, 1), height: 11, fill: 'var(--yes)', stroke: 'var(--panel)', 'stroke-width': .6 }));
          g.appendChild(el('rect', { x: S.LX + gw, y: yy - 5.5, width: Math.max(S.LW - gw, 1), height: 11, fill: 'var(--no)', stroke: 'var(--panel)', 'stroke-width': .6 }));
          if (gw >= 26) g.appendChild(txt(L.yes + '¢', { x: S.LX + 3, y: yy + 3.2, class: 'ladtxt' }));
          if (S.LW - gw >= 26) g.appendChild(txt((100 - L.yes) + '¢', { x: S.LX + S.LW - 3, y: yy + 3.2, class: 'ladtxt', 'text-anchor': 'end' }));
          g.appendChild(txt(cmp + L.strike + '°', { x: S.LX + S.LW + 5, y: yy + 3.5, class: 'ax', fill: m === 'high' ? 'var(--warm)' : 'var(--cool)' }));
        });
      });
      [0, 50, 100].forEach(p => g.appendChild(txt(p + (p === 100 ? '¢' : ''), { x: lx(p), y: S.B + 15, 'text-anchor': 'middle', class: 'ax' })));
      g.appendChild(txt('Yes green, No red · placeholders', { x: S.LX + S.LW / 2, y: S.B + 30, 'text-anchor': 'middle', class: 'ax' }));
      const obsRows = (ob && ob.rows) || [];
      const fseries = [AI.nws && AI.nws.rows, AI.nbm && AI.nbm.rows, fc.nws && fc.nws.hourly, fc.nbm && fc.nbm.hourly];
      [['h', 'Yes price — high strikes (placeholder)', S.PH0, S.PH1], ['l', 'Yes price — low strikes (placeholder)', S.PL0, S.PL1]].forEach(([side, ttl, p0, p1]) => {
        const ypp = v => p1 - (v / 100) * (p1 - p0);
        g.appendChild(el('line', { x1: S.L, x2: S.R, y1: p0 - 24, y2: p0 - 24, stroke: 'var(--line)' }));
        g.appendChild(txt(ttl, { x: S.L, y: p0 - 10, class: 'axl' }));
        [0, 50, 100].forEach(p => { g.appendChild(el('line', { x1: S.L, x2: S.R, y1: ypp(p), y2: ypp(p), class: 'grid' })); g.appendChild(txt(p + '¢', { x: S.L - 8, y: ypp(p) + 4, 'text-anchor': 'end', class: 'ax' })); });
        const endLabs = [];
        picked.filter(pk => pk.side === side).forEach(pk => {
          const pts = WXM.pricePath(obsRows, fseries, unit, pk.side, pk.K).filter(p => p.t >= w0);
          if (!pts.length) return;
          priceSer.push({ label: (pk.side === 'h' ? '>' : '<') + pk.K + '°', col: pk.col, pts });
          g.appendChild(el('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ',' + ypp(p.v).toFixed(1)).join(''), fill: 'none', stroke: pk.col, 'stroke-width': 1.8 }));
          endLabs.push({ y: ypp(pts[pts.length - 1].v), s: (pk.side === 'h' ? '>' : '<') + pk.K + '° ' + pts[pts.length - 1].v + '¢', col: pk.col });
        });
        endLabs.sort((a, b) => a.y - b.y);
        let pv = -1e9; endLabs.forEach(L => { let yy = Math.max(L.y, p0); if (yy - pv < 11) yy = pv + 11; pv = yy; g.appendChild(txt(L.s, { x: S.R + 6, y: yy + 3, 'font-size': 9, 'font-weight': 700, fill: L.col })); });
      });
    }

    // ---- titles
    g.appendChild(txt(c.city + ' (' + c.station + ') — ' + dateShort(d0, tz), { x: S.L, y: 16, 'font-size': 14, 'font-weight': 700, fill: 'var(--navy)' }));
    g.appendChild(txt('Temperature (°' + unit + '), local time' + (unit === 'C' ? ' — Celsius station' : ''), { x: S.W - 14, y: 16, 'text-anchor': 'end', class: 'axl' }));

    HV = { w0, d1, tz, S, market,
      series: [{ nm: 'Observed', pts: O, col: COL.obs }, { nm: 'NWS now', pts: F, col: COL.nws }, { nm: 'NBM', pts: N, col: COL.nbm },
               { nm: 'LAMP', pts: LA, col: COL.lamp }, { nm: 'NWS as issued', pts: A, col: COL.nws }, { nm: 'NBM as issued', pts: NA, col: COL.nbm },
               { nm: 'LAMP as issued', pts: LAI, col: COL.lamp }]
        .concat(ySeries.map(s => ({ nm: s.nm, pts: s.pts, col: s.col }))).filter(s => s.pts.length),
      prices: priceSer };
  }

  // ---- crosshair: temperature and time in the top panel, price in the bottom
  function hover(svg) {
    let hline = null;
    svg.addEventListener('mousemove', e => {
      if (!HV) return;
      const S = HV.S;
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      const inTemp = q.x >= S.L && q.x <= S.R && q.y >= S.T && q.y <= S.B;
      const inPrice = HV.market && q.x >= S.L && q.x <= S.R && ((q.y >= S.PH0 && q.y <= S.PH1) || (q.y >= S.PL0 && q.y <= S.PL1));
      const hoverSide = HV.market && q.y >= S.PL0 ? 'l' : 'h';
      if (!inTemp && !inPrice) { tip.hide(); if (hline) hline.remove(); hline = null; return; }
      const t = HV.w0 + (q.x - S.L) / (S.R - S.L) * (HV.d1 - HV.w0);
      const ts = clock(t, HV.tz) + ' · ' + dateShort(t, HV.tz);
      const near = pts => { let b = null; for (const p of pts) { const d2 = Math.abs(p.t - t); if (d2 <= 45 * 6e4 && (!b || d2 < b.d2)) b = { d2, p }; } return b && b.p; };
      const rows = [];
      (inTemp ? HV.series : []).forEach(s => { const p = near(s.pts); if (p) rows.push('<span style="color:' + s.col + '">●</span> ' + s.nm + ' <b>' + p.v.toFixed(1) + '°</b>'); });
      (inPrice ? HV.prices.filter(s => (hoverSide === 'h') === (s.label[0] === '>')) : []).forEach(s => { const p = near(s.pts); if (p) rows.push('<span style="color:' + s.col + '">●</span> Yes ' + s.label + ' <b>' + p.v + '¢</b>'); });
      if (!rows.length) { tip.hide(); if (hline) hline.remove(); hline = null; return; }
      tip.show(e, '<b>' + ts + '</b>' + rows.join('<br>'));
      if (!hline || !hline.isConnected) { hline = el('line', { stroke: 'var(--muted)', 'stroke-width': .8, 'stroke-dasharray': '2 2', 'pointer-events': 'none' }); svg.appendChild(hline); }
      hline.setAttribute('x1', q.x); hline.setAttribute('x2', q.x); hline.setAttribute('y1', S.T); hline.setAttribute('y2', HV.market ? S.PL1 : S.B);
    });
    svg.addEventListener('mouseleave', () => { tip.hide(); if (hline) hline.remove(); hline = null; });
  }

  async function init(opts = {}) {
    svgId = opts.svgId || 'chart';
    onSelect = opts.onSelect || null;
    tip = WXC.tooltip();
    const sres = await WXD.get('summary.json');
    summary = sres.data || { cities: [], asof: null };
    if (opts.basemap) summary.base = opts.basemap;
    const st = $('#pageStatus'); if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([sres], 10)); }
    const want = opts.station || WXC.param('station') || WXC.param('city') || 'KLAX';
    const svg = $('#' + svgId);
    hover(svg);
    const yb = $('#ydayBtn'); if (yb) yb.onclick = e => { showYday = !showYday; e.target.classList.toggle('on'); draw(); };
    if (!summary.cities.length) { svg.innerHTML = ''; svg.appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    await select(summary.cities.some(c => c.station === want) ? want : summary.cities[0].station, false);
  }

  return { init, select };
})();
