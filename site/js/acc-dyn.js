/* Figure 2, how the market moves between forecast cycles.

   A forecast tool changes its value when a new cycle lands, a few times a
   day; the market can change every ten minutes as reports come in and as
   the ladder is requoted. This figure reads that difference three ways from
   dynamics.json, under the conventions of docs/accuracy.md.

     a. Time to converge. For each system, the share of matched city-days
        whose value is within a tolerance of the settle from a lead onward,
        so the curve reads as "by this many hours out, this share had the
        answer and kept it". Under it, a strip of changes per hour by lead,
        one row per system, shaded on a log scale because the market's rate
        is an order of magnitude above a tool's.
     b. Changes per hour by station-local hour, the market with its
        bootstrap band against the panel tools, which peak at cycle hours.
     c. One city-day traced hour by hour from trace/<date>.json, then the
        event strip, the change in absolute error in the two hours after a
        report that moved the bank, one line per system.

   Every value drawn is the builder's; nothing here is computed beyond
   pixel placement. The systems drawn as lines are the market and the
   eleven panel tools. The six own-span sources are gray rows in the strip
   of panel a and are not drawn as lines against another source, per the
   contract's own-span rule. */
window.WXAccDyn = (() => {
  const A = WXAcc;
  const { el, txt, h, $ } = WXC;
  const fin = v => v != null && isFinite(v);
  const P = WXC.P;
  const HOUR = 36e5;

  // the systems drawn as lines, the market first so the key reads that way
  const LINES = ['FX'].concat(A.PANEL);
  const TOLS = [{ key: 'tol1', label: '1 F', deg: 1 }, { key: 'tol2', label: '2 F', deg: 2 }];

  // the state a tab change redraws from
  const S = { metric: 'high', tol: 'tol1', date: null, city: null };
  let dyn = null, bundle = null, N = null;
  const traces = {};   // trace files by date, false when the fetch failed
  let traceReq = 0;    // guards a slow fetch against a newer selection

  // ------------------------------------------------------------- small helpers
  // pointer position in viewBox units; the svg is scaled uniformly by CSS
  function pt(svg, e) {
    const r = svg.getBoundingClientRect();
    const vb = svg.viewBox.baseVal;
    return { x: (e.clientX - r.left) / r.width * vb.width, y: (e.clientY - r.top) / r.height * vb.height };
  }
  /* A step-after path through pixel points, broken at a null.

     A standing value holds until the next record replaces it, so the
     honest line is horizontal to the next capture and vertical there,
     never a slope that would claim the value drifted between records. */
  function stepPath(pts) {
    let d = '', pen = false;
    pts.forEach(p => {
      if (!p || !fin(p[0]) || !fin(p[1])) { pen = false; return; }
      d += pen ? 'H' + p[0].toFixed(1) + 'V' + p[1].toFixed(1) : 'M' + p[0].toFixed(1) + ',' + p[1].toFixed(1);
      pen = true;
    });
    return d;
  }
  function stepLine(svg, pts, attrs) {
    const d = stepPath(pts);
    if (!d) return null;
    return svg.appendChild(el('path', Object.assign({ d, fill: 'none', 'stroke-width': 1.5, 'stroke-linejoin': 'round',
                                                     'pointer-events': 'none' }, attrs || {})));
  }
  // an invisible rect over the frame that carries the hover, with a hairline
  function overlay(svg, g, make, vertical) {
    const line = el('line', { x1: g.L, x2: g.L, y1: g.T, y2: g.B, stroke: 'var(--rule)', 'stroke-width': 1,
                              'pointer-events': 'none', visibility: 'hidden' });
    svg.appendChild(line);
    const r = el('rect', { x: g.L, y: g.T, width: g.R - g.L, height: g.B - g.T, fill: 'var(--panel)',
                           'fill-opacity': 0, 'pointer-events': 'all' });
    svg.appendChild(r);
    A.hover(r, e => {
      const p = pt(svg, e);
      const out = make(p, e);
      if (out && out.x != null && vertical !== false) {
        line.setAttribute('x1', out.x); line.setAttribute('x2', out.x); line.setAttribute('visibility', 'visible');
      } else line.setAttribute('visibility', 'hidden');
      return out ? out.html : '';
    });
    r.addEventListener('mouseleave', () => line.setAttribute('visibility', 'hidden'));
    return r;
  }
  const rows = (title, pairs, foot) => A.tooltip().rows(title, pairs, foot);
  const hourFmt = v => (v === 0 ? '12 AM' : v === 12 ? 'noon' : (v % 12) + (v < 12 ? ' AM' : ' PM'));
  const metricWord = () => (S.metric === 'high' ? 'highs' : 'lows');
  const tolDeg = () => (TOLS.find(t => t.key === S.tol) || TOLS[0]).deg;
  const nearestIndex = (arr, v) => {
    let best = 0;
    arr.forEach((a, i) => { if (Math.abs(a - v) < Math.abs(arr[best] - v)) best = i; });
    return best;
  };
  // the last row of a time series at or before t; rows carry an ISO time first
  function asof(list, t, maxAgeMs) {
    if (!list || !list.length) return null;
    let lo = 0, hi = list.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (P(list[mid][0]) <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (ans < 0) return null;
    if (maxAgeMs != null && t - P(list[ans][0]) > maxAgeMs) return null;
    return list[ans];
  }
  // a city's reader name, from the map file when it is loaded, else the id
  function cityName(id) {
    const cities = bundle && bundle.map && bundle.map.cities;
    const c = cities && cities.find(x => x.id === id);
    return c && c.name ? c.name : id;
  }

  // ------------------------------------------------------------- entry
  function draw(D) {
    const host = $('#accDyn');
    if (!host) return;
    bundle = D || {};
    dyn = bundle.dyn || (bundle.converge ? bundle : null);
    if (!dyn) { A.notYet(host, A.NOT_PUBLISHED); return; }
    build(host);
    redraw();
  }

  // the card's furniture: controls in the bar, three panels in the host
  function build(host) {
    const bar = $('#accDynBar');
    if (bar) {
      bar.innerHTML = '';
      A.metricTabs(bar, m => { S.metric = m; redraw(); }, S.metric);
      A.tabs(bar, TOLS.map(t => ({ key: t.key, label: t.label, title: 'Within ' + t.deg + ' °F of the settle' })),
             t => { S.tol = t; drawA(); drawNote(); }, { initial: S.tol, label: 'Tolerance' });
    }
    host.innerHTML = '';
    N = {};
    const sub = t => host.appendChild(h('div', { class: 'accsub', text: t }));
    const svg = (id, H) => host.appendChild(el('svg', { id, viewBox: '0 0 ' + A.W + ' ' + H }));
    sub('a. Time to converge, and changes per hour by lead');
    N.a = svg('accDynA', 540);
    sub('b. Changes per hour by station-local hour');
    N.b = svg('accDynB', 300);
    sub('c. One city-day traced');
    const tb = host.appendChild(h('div', { class: 'bar acc-dyn-tracebar' }));
    const ti = dyn.traceIndex || {};
    const dates = (ti.dates || []).slice().reverse();
    N.date = h('select', { 'aria-label': 'Target date' });
    N.city = h('select', { 'aria-label': 'City' });
    tb.appendChild(h('label', { text: 'Date' })); tb.appendChild(N.date);
    tb.appendChild(h('label', { text: 'City' })); tb.appendChild(N.city);
    dates.forEach(d => N.date.appendChild(h('option', { value: d, text: d })));
    const def = ti.default || {};
    S.date = def.date && dates.includes(def.date) ? def.date : dates[0] || null;
    if (S.date) N.date.value = S.date;
    fillCities(def.city);
    N.date.onchange = () => { S.date = N.date.value; fillCities(null); drawC(); };
    N.city.onchange = () => { S.city = N.city.value; drawC(); };
    N.c = svg('accDynC', 380);
    N.rule = host.appendChild(h('p', { class: 'cap acc-dyn-rule' }));
    sub('Change in error after a report moved the bank');
    N.e = svg('accDynE', 240);
    const keyEl = $('#accDynKey');
    if (keyEl) A.key(keyEl, LINES, { short: true,
      note: 'the six own-span sources are the gray rows of the strip in panel a and are not drawn as lines' });
  }
  /* The city list for the chosen date, with the builder's default rule
     applied when the current city is not traced on that date: the city
     whose market error at 12 h is the median of the date's cities, the
     lower of the two when the count is even, ties broken by station id. */
  function fillCities(preferred) {
    const ti = dyn.traceIndex || {};
    const by = (ti.byDate || {})[S.date] || {};
    const ids = Object.keys(by).sort();
    N.city.innerHTML = '';
    ids.forEach(id => {
      const v = by[id] || {};
      N.city.appendChild(h('option', { value: id, text: cityName(id) + ' (' + id + ')',
        title: 'Market error at 12 h ' + A.deg1(v.maeH12) + ', ' + A.int(v.changes) + ' changes' }));
    });
    let pick = preferred && ids.includes(preferred) ? preferred : (S.city && ids.includes(S.city) ? S.city : null);
    if (!pick && ids.length) {
      const scored = ids.filter(id => fin(by[id] && by[id].maeH12))
        .sort((p, q) => (by[p].maeH12 - by[q].maeH12) || (p < q ? -1 : 1));
      pick = scored.length ? scored[(scored.length - 1) >> 1] : ids[0];
    }
    S.city = pick;
    if (pick) N.city.value = pick;
  }

  function redraw() {
    drawA(); drawB(); drawC(); drawE(); drawNote();
  }

  // ------------------------------------------------------------- panel a
  function drawA() {
    const svg = N.a;
    const cv = dyn.converge && dyn.converge.metric && dyn.converge.metric[S.metric] && dyn.converge.metric[S.metric][S.tol];
    const bl = dyn.rate && dyn.rate.byLead && dyn.rate.byLead.metric && dyn.rate.byLead.metric[S.metric];
    if (!cv || !cv.h || !cv.systems) { A.notYet(svg, 'The convergence block is not in the published file.'); return; }
    const stripRows = bl && bl.systems ? A.ORDER.filter(id => Array.isArray(bl.systems[id])) : [];
    const RH = 11;
    const gA = { L: 90, R: 850, T: 24, B: 252 };
    const stripT = gA.B + 48, stripB = stripT + stripRows.length * RH;
    const H = (stripRows.length ? stripB : gA.B) + 58;
    A.clear(svg, H);
    const g = { W: A.W, H, L: gA.L, R: gA.R, T: gA.T, B: stripRows.length ? stripB : gA.B };
    const hmax = Math.max.apply(null, cv.h), hmin = Math.min.apply(null, cv.h);
    const x = A.leadScale(g, hmax, hmin);
    const y = A.scale(0, 1, gA.B, gA.T);
    A.yAxis(svg, gA, y, [0, 0.25, 0.5, 0.75, 1], v => Math.round(v * 100) + '%', 'Within tolerance from here on', { grid: true });
    A.leadAxis(svg, g, x, hmax, hmin, 'Hours before the end of the target day');
    svg.appendChild(el('line', { x1: gA.L, x2: gA.R, y1: gA.B, y2: gA.B, class: 'grid' }));
    // the half line, where the median lead is read
    svg.appendChild(el('line', { x1: gA.L, x2: gA.R, y1: y(0.5), y2: y(0.5), stroke: 'var(--rule)', 'stroke-dasharray': '2 3' }));

    const drawn = LINES.filter(id => cv.systems[id] && Array.isArray(cv.systems[id].share));
    drawn.forEach(id => {
      const s = cv.systems[id];
      A.lineSeries(svg, cv.h.map(x), s.share.map(v => (fin(v) ? y(v) : null)),
                   { stroke: A.color(id), 'stroke-width': A.width(id) });
    });

    /* The median lead, printed where each line crosses one half. The share
       can only rise toward the end of the day, so the line is below one half
       to the left of the crossing and above it to the right; a label sits
       above-left or below-right of its dot, in lanes so two systems that
       cross near the same lead do not print on top of each other. */
    const med = drawn.map(id => ({ id, m: cv.systems[id].median })).filter(o => fin(o.m))
      .sort((p, q) => x(p.m) - x(q.m));
    const lanes = [];
    med.forEach(o => {
      const px = x(o.m);
      let k = 0;
      while (lanes[k] != null && px - lanes[k] < 78) k++;
      lanes[k] = px;
      const above = k % 2 === 0;
      const dy = above ? -(8 + 11 * (k / 2)) : 14 + 11 * ((k - 1) / 2);
      svg.appendChild(el('circle', { cx: px, cy: y(0.5), r: 3, fill: A.color(o.id), stroke: 'var(--panel)', 'stroke-width': 1 }));
      const text = A.short(o.id) + (o.m >= hmax ? ' before ' + hmax + ' h' : ' ' + A.f1(o.m) + ' h');
      // a label at the frame's edge is pulled inside it
      let end = above;
      if (end && px - 90 < gA.L) end = false;
      if (!end && px + 90 > gA.R) end = true;
      A.label(svg, px + (end ? -6 : 6), y(0.5) + dy, text, A.color(o.id), { 'text-anchor': end ? 'end' : 'start' });
    });

    // the never share at the right margin, beside where each line ends
    const nev = drawn.map(id => {
      const s = cv.systems[id];
      const last = s.share[s.share.length - 1];
      return { id, never: s.never, y0: fin(last) ? y(last) : (fin(s.never) ? y(1 - s.never) : null) };
    }).filter(o => fin(o.y0) && fin(o.never)).sort((p, q) => p.y0 - q.y0);
    let prev = -Infinity;
    nev.forEach(o => {
      let yy = Math.max(o.y0, gA.T + 5, prev + 11);
      o.yl = yy; prev = yy;
    });
    // push back up if the stack ran past the frame bottom
    for (let i = nev.length - 1, floor = gA.B; i >= 0; i--) {
      if (nev[i].yl > floor) nev[i].yl = floor;
      floor = nev[i].yl - 11;
    }
    nev.forEach(o => A.label(svg, gA.R + 6, o.yl + 3.5, A.pct(o.never) + ' never', A.color(o.id), { 'text-anchor': 'start' }));
    svg.appendChild(txt('never within ' + tolDeg() + ' °F', { x: gA.R + 6, y: gA.T - 8, class: 'ax' }));
    svg.appendChild(txt('Share of matched city-days within ' + tolDeg() + ' °F of the settle at this lead and at every later one, ' + metricWord(),
                        { x: gA.L, y: gA.T - 8, class: 'axl' }));

    overlay(svg, gA, p => {
      const hh = Math.round(x.invert(p.x));
      if (hh < hmin || hh > hmax) return null;
      const i = cv.h.indexOf(hh);
      if (i < 0) return null;
      const pairs = drawn.map(id => [id, cv.systems[id].share[i]]).filter(o => fin(o[1]))
        .sort((a, b) => b[1] - a[1]).map(o => [A.swatch(o[0]), A.pct(o[1])]);
      return { x: x(hh), html: rows('Lead ' + hh + ' h, within ' + tolDeg() + ' °F from here on', pairs) };
    });

    if (!stripRows.length) return;
    // ---- the strip: changes per hour by lead, one row per system
    let top = 0;
    stripRows.forEach(id => bl.systems[id].forEach(v => { if (fin(v) && v > top) top = v; }));
    const floor = 0.01;
    const shade = v => {
      if (!fin(v)) return null;
      const u = top > floor ? (Math.log10(Math.max(v, floor)) - Math.log10(floor)) / (Math.log10(top) - Math.log10(floor)) : 0;
      return 0.04 + 0.86 * Math.min(1, Math.max(0, u));
    };
    svg.appendChild(txt('Changes per hour by lead, one row per system, shade on a log scale', { x: gA.L, y: stripT - 10, class: 'axl' }));
    // the ramp, so the shade can be read as a number
    const ramp = [0.01, 0.1, 1, top].filter((v, i, a) => i === 0 || v > a[i - 1] * 1.5);
    let rx = gA.R;
    ramp.slice().reverse().forEach(v => {
      rx -= 46;
      svg.appendChild(el('rect', { x: rx, y: stripT - 19, width: 12, height: 10, fill: 'var(--navy)', 'fill-opacity': shade(v) }));
      svg.appendChild(txt(v >= 1 ? A.f1(v) : String(v), { x: rx + 15, y: stripT - 10, class: 'ax' }));
    });
    const cw = (gA.R - gA.L) / Math.max(1, hmax - hmin);
    stripRows.forEach((id, r) => {
      const yy = stripT + r * RH;
      svg.appendChild(txt(A.short(id), { x: gA.L - 8, y: yy + RH - 2.5, 'text-anchor': 'end', class: 'ax',
                                         'font-weight': id === 'FX' ? 700 : 400 }));
      bl.h.forEach((hh, i) => {
        const op = shade(bl.systems[id][i]);
        if (op == null) return;
        const x0 = Math.max(gA.L, x(hh + 0.5)), x1 = Math.min(gA.R, x(hh - 0.5));
        svg.appendChild(el('rect', { x: x0, y: yy, width: Math.max(0, x1 - x0 - 0.6), height: RH - 1,
                                     fill: 'var(--navy)', 'fill-opacity': op }));
      });
    });
    const gS = { L: gA.L, R: gA.R, T: stripT, B: stripB };
    const hi = el('rect', { x: 0, y: 0, width: cw, height: RH - 1, fill: 'none', stroke: 'var(--ink)', 'stroke-width': 1,
                            'pointer-events': 'none', visibility: 'hidden' });
    svg.appendChild(hi);
    const ov = overlay(svg, gS, p => {
      const hh = Math.round(x.invert(p.x));
      const r = Math.floor((p.y - stripT) / RH);
      const id = stripRows[r];
      const i = bl.h.indexOf(hh);
      if (!id || i < 0) { hi.setAttribute('visibility', 'hidden'); return null; }
      const x0 = Math.max(gA.L, x(hh + 0.5)), x1 = Math.min(gA.R, x(hh - 0.5));
      hi.setAttribute('x', x0); hi.setAttribute('y', stripT + r * RH); hi.setAttribute('width', Math.max(0, x1 - x0 - 0.6));
      hi.setAttribute('visibility', 'visible');
      return { html: rows(A.name(id), [['Lead', hh + ' h'], ['Changes per hour', A.f2(bl.systems[id][i])]],
                          'mean over the system\'s own-span city-days, ' + metricWord()) };
    }, false);
    ov.addEventListener('mouseleave', () => hi.setAttribute('visibility', 'hidden'));
  }

  // ------------------------------------------------------------- panel b
  function drawB() {
    const svg = N.b;
    const b = dyn.rate && dyn.rate.byLocalHour && dyn.rate.byLocalHour.metric && dyn.rate.byLocalHour.metric[S.metric];
    const pc = dyn.rate && dyn.rate.perCityDay && dyn.rate.perCityDay.metric && dyn.rate.perCityDay.metric[S.metric];
    if (!b || !b.hour) { A.notYet(svg, 'The by-hour block is not in the published file.'); return; }
    const H = 300;
    A.clear(svg, H);
    const g = A.frame(H, { L: 90, R: 850 });
    const x = A.scale(-0.5, 23.5, g.L, g.R);
    const tools = A.PANEL.filter(id => b.tools && Array.isArray(b.tools[id]));
    let top = 0;
    const bump = v => { if (fin(v) && v > top) top = v; };
    (b.fx || []).forEach(bump); (b.fxHi || []).forEach(bump);
    tools.forEach(id => b.tools[id].forEach(bump));
    const step = A.niceStep(top || 1, 5);
    const ymax = Math.ceil((top || 1) / step) * step;
    const y = A.scale(0, ymax, g.B, g.T);
    A.yAxis(svg, g, y, A.ticks(0, ymax, step), v => A.f1(v), 'Changes per hour');
    A.xAxis(svg, g, x, A.ticks(0, 23, 3), hourFmt, 'Station-local hour');
    A.band(svg, b.hour.map(x), (b.fxLo || []).map(v => (fin(v) ? y(v) : null)), (b.fxHi || []).map(v => (fin(v) ? y(v) : null)),
           'var(--accent)');
    tools.forEach(id => A.lineSeries(svg, b.hour.map(x), b.tools[id].map(v => (fin(v) ? y(v) : null)),
                                     { stroke: A.color(id), 'stroke-width': A.width(id) }));
    A.lineSeries(svg, b.hour.map(x), (b.fx || []).map(v => (fin(v) ? y(v) : null)),
                 { stroke: A.color('FX'), 'stroke-width': A.width('FX') });
    if (pc && pc.fx) {
      const meds = tools.map(id => pc.tools && pc.tools[id]).filter(fin);
      const t = 'Changes per city-day, ' + metricWord() + ', market median ' + A.int(pc.fx.median)
        + ' (quartiles ' + A.int(pc.fx.q1) + ' to ' + A.int(pc.fx.q3) + ')'
        + (meds.length ? ', panel tools ' + A.int(Math.min.apply(null, meds)) + ' to ' + A.int(Math.max.apply(null, meds)) : '');
      svg.appendChild(txt(t, { x: g.L, y: g.T - 8, class: 'axl' }));
    }
    overlay(svg, g, p => {
      const i = nearestIndex(b.hour, x.invert(p.x));
      const hr = b.hour[i];
      const pairs = [[A.swatch('FX'), A.f2(b.fx[i]) + (fin(b.fxLo[i]) ? ' (' + A.iv(b.fxLo[i], b.fxHi[i], A.f2) + ')' : '')]];
      tools.map(id => [id, b.tools[id][i]]).filter(o => fin(o[1])).sort((a, c) => c[1] - a[1])
        .forEach(o => pairs.push([A.swatch(o[0]), A.f2(o[1])]));
      return { x: x(hr), html: rows('Changes per hour in the local hour beginning ' + hourFmt(hr), pairs,
                                    'market band is the 95 percent bootstrap over dates') };
    });
  }

  // ------------------------------------------------------------- panel c
  function drawC() {
    const svg = N.c;
    if (!S.date || !S.city) { A.notYet(svg, 'No traced city-day is listed in the published file.'); N.rule.textContent = ''; return; }
    const date = S.date, city = S.city, req = ++traceReq;
    if (traces[date] === undefined) {
      A.notYet(svg, 'Loading the trace for ' + date + '.');
      A.trace(date).then(({ data }) => { traces[date] = data && data.cities ? data : false; })
        .catch(() => { traces[date] = false; })
        .then(() => { if (req === traceReq) drawC(); });
      return;
    }
    const file = traces[date];
    const c = file && file.cities && file.cities[city];
    if (!c) {
      A.notYet(svg, file === false ? 'The trace for ' + date + ' could not be loaded.' : 'No trace for ' + cityName(city) + ' on ' + date + '.');
      N.rule.textContent = '';
      return;
    }
    drawTrace(svg, c, date, city);
    const by = ((dyn.traceIndex || {}).byDate || {})[date] || {};
    const v = by[city] || {};
    N.rule.textContent = cityName(city) + ' (' + city + '), ' + date + ', ' + metricWord() + '. Market error at 12 h before the end of the day '
      + A.deg1(v.maeH12) + ' and ' + A.int(v.changes) + ' changes of the crossing over the day, both read on the high ladder. '
      + 'The default trace is the latest date and the city whose 12 h market error is the median of that date\'s cities, '
      + 'the lower of the two when the count is even, ties broken by station id.';
  }

  function drawTrace(svg, c, date, city) {
    const m = S.metric, tz = c.tz || 'UTC';
    const H = 380;
    A.clear(svg, H);
    const g = A.frame(H, { L: 90, R: 850 });
    const listing = P(c.listing), dayStart = P(c.dayStart), dayEnd = P(c.dayEnd);
    // the window opens three hours before listing so the tools' standing values are seen before the market opens
    const t0 = Math.floor((fin(listing) ? listing : dayStart - 12 * HOUR) / HOUR) * HOUR - 3 * HOUR;
    const t1 = fin(dayEnd) ? dayEnd : t0 + 40 * HOUR;
    const x = A.scale(t0, t1, g.L, g.R);
    const obs = c.obs || [];
    const mk = (c.market && c.market[m]) || [];
    const bank = (c.bank && c.bank[m]) || [];
    const settle = m === 'high' ? c.settleHigh : c.settleLow;
    const tools = A.PANEL.filter(id => c.tools && c.tools[id] && Array.isArray(c.tools[id][m]));

    // the vertical range: every temperature drawn, with a degree of air above and below
    let lo = Infinity, hi = -Infinity;
    const span = v => { if (fin(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } };
    obs.forEach(o => span(o[1]));
    mk.forEach(r => { span(r[1]); span(r[3]); span(r[4]); });
    bank.forEach(r => span(r[1]));
    span(settle);
    tools.forEach(id => c.tools[id][m].forEach(r => { if (P(r[0]) <= t1) span(r[1]); }));
    if (!isFinite(lo)) { lo = 50; hi = 90; }
    lo = Math.floor(lo) - 1.5; hi = Math.ceil(hi) + 1.5;
    const y = A.scale(lo, hi, g.B, g.T);
    const step = A.niceStep(hi - lo, 6);
    A.yAxis(svg, g, y, A.ticks(lo, hi, step), v => v + '°', 'Temperature (°F)');
    // the time axis in the station's clock, every six hours, the date at midnight
    const tks = WXC.hourTicks(t0, t1, tz, 6);
    tks.forEach(k => {
      svg.appendChild(el('line', { x1: x(k.t), x2: x(k.t), y1: g.T, y2: g.B, class: 'grid' }));
      svg.appendChild(txt(k.label, { x: x(k.t), y: g.B + 17, 'text-anchor': 'middle', class: 'ax' }));
    });
    svg.appendChild(txt('Station-local time, ' + tz.replace(/_/g, ' '), { x: (g.L + g.R) / 2, y: g.B + 38, 'text-anchor': 'middle', class: 'ax' }));
    svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: g.B, y2: g.B, class: 'grid' }));
    if (fin(dayStart) && dayStart > t0 && dayStart < t1) {
      svg.appendChild(el('line', { x1: x(dayStart), x2: x(dayStart), y1: g.T, y2: g.B, class: 'grid', 'stroke-dasharray': '4 4' }));
      svg.appendChild(txt('the target day begins', { x: x(dayStart) + 5, y: g.T + 12, class: 'ax' }));
    }
    // the listing time
    if (fin(listing) && listing >= t0 && listing <= t1) {
      svg.appendChild(el('line', { x1: x(listing), x2: x(listing), y1: g.T, y2: g.B, stroke: 'var(--muted)', 'stroke-width': 1,
                                   'stroke-dasharray': '2 3' }));
      svg.appendChild(txt('listed ' + WXC.clock(listing, tz), { x: x(listing) + 5, y: g.B - 6, class: 'ax' }));
    }
    // the settle, the dashed rule every line is read against
    if (fin(settle)) {
      svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: y(settle), y2: y(settle), stroke: 'var(--ink)', 'stroke-width': 1.2,
                                   'stroke-dasharray': '6 4' }));
      A.label(svg, g.R - 4, y(settle) - 5, 'settle ' + settle + '°', 'var(--ink)', { 'text-anchor': 'end' });
    }
    // the market's q10 to q90 band, then the tools, the bank, the market line
    const mxs = mk.map(r => x(P(r[0])));
    A.band(svg, mxs, mk.map(r => (fin(r[3]) ? y(r[3]) : null)), mk.map(r => (fin(r[4]) ? y(r[4]) : null)), 'var(--accent)');
    tools.forEach(id => {
      const recs = c.tools[id][m];
      const pts = [];
      const first = asof(recs, t0);
      if (first) pts.push([x(t0), y(first[1])]);
      let lastT = first ? t0 : null;
      recs.forEach(r => {
        const t = P(r[0]);
        if (t <= t0 || t > t1 || !fin(r[1])) return;
        pts.push([x(t), y(r[1])]);
        lastT = t;
      });
      if (!pts.length) return;
      stepLine(svg, pts, { stroke: A.color(id), 'stroke-width': A.width(id), 'stroke-opacity': 0.9 });
      // the value held after the last capture, dashed so it reads as held rather than issued
      const last = pts[pts.length - 1];
      if (lastT != null && lastT < t1) {
        svg.appendChild(el('line', { x1: last[0], x2: x(t1), y1: last[1], y2: last[1], stroke: A.color(id), 'stroke-width': 1,
                                     'stroke-dasharray': '2 4', 'stroke-opacity': 0.7, 'pointer-events': 'none' }));
      }
    });
    if (bank.length) {
      const pts = bank.map(r => [x(Math.max(t0, P(r[0]))), y(r[1])]);
      pts.push([x(t1), pts[pts.length - 1][1]]);
      stepLine(svg, pts, { stroke: 'var(--muted)', 'stroke-width': 2.2 });
    }
    stepLine(svg, mk.map(r => (fin(r[1]) ? [x(P(r[0])), y(r[1])] : null)), { stroke: 'var(--accent)', 'stroke-width': A.width('FX') });
    // the reports: hourly filled, specials hollow
    obs.forEach(o => {
      const t = P(o[0]);
      if (!fin(t) || t < t0 || t > t1 || !fin(o[1])) return;
      svg.appendChild(o[3] ? el('circle', { cx: x(t), cy: y(o[1]), r: 2.2, fill: 'var(--panel)', stroke: 'var(--ink)', 'stroke-width': 1 })
                           : el('circle', { cx: x(t), cy: y(o[1]), r: 2.6, fill: 'var(--ink)', 'fill-opacity': 0.75 }));
    });
    svg.appendChild(txt('Reports as dots, the bank as the grey step, the market whole-degree value as the accent line with its q10 to q90 band, each tool stepped at its capture times',
                        { x: g.L, y: g.T - 8, class: 'axl' }));

    overlay(svg, g, p => {
      const t = x.invert(p.x);
      const pairs = [];
      const r = asof(mk, t, HOUR);
      const label = m === 'high' ? 'Market high' : 'Market low';
      if (r && fin(r[1])) {
        pairs.push([label, r[1] + '° (crossing ' + A.f1(r[2]) + ')']);
        pairs.push(['q10 to q90', A.iv(r[3], r[4])]);
      } else pairs.push([label, r ? 'no crossing' : A.dash]);
      if (r) pairs.push(['Strikes quoted', A.int(r[5]) + (fin(r[6]) ? ' (' + A.int(r[6]) + ' two-sided)' : '')]);
      const bk = asof(bank, t);
      if (bk) pairs.push(['Bank', bk[1] + '°']);
      const ob = asof(obs, t);
      if (ob) pairs.push(['Last report', A.f1(ob[1]) + '° at ' + WXC.clock(P(ob[0]), tz) + (ob[3] ? ' (special)' : '')]);
      tools.forEach(id => {
        const v = asof(c.tools[id][m], t);
        if (v) pairs.push([A.swatch(id), A.deg1(v[1])]);
      });
      return { x: p.x, html: rows(WXC.clockFull(t, tz) + ' ' + WXC.dateShort(t, tz) + ', station clock', pairs,
                                  'standing values, the last record at or before this minute') };
    });
  }

  // ------------------------------------------------------------- the event strip
  function drawE() {
    const svg = N.e;
    const ev = dyn.event && dyn.event.metric && dyn.event.metric[S.metric];
    if (!ev || !ev.k || !ev.systems) { A.notYet(svg, 'The event block is not in the published file.'); return; }
    const H = 240;
    A.clear(svg, H);
    const g = A.frame(H, { L: 90, R: 850 });
    const kmax = Math.max.apply(null, ev.k);
    const x = A.scale(0, kmax, g.L, g.R);
    const drawn = LINES.filter(id => ev.systems[id] && Array.isArray(ev.systems[id].delta));
    let lo = 0, hi = 0;
    drawn.forEach(id => {
      const s = ev.systems[id];
      ['delta', 'lo', 'hi'].forEach(k => (s[k] || []).forEach(v => { if (fin(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }));
    });
    const step = A.niceStep((hi - lo) || 0.1, 4);
    lo = Math.floor(lo / step - 1e-9) * step; hi = Math.ceil(hi / step + 1e-9) * step;
    if (hi === lo) hi = lo + step;
    const y = A.scale(lo, hi, g.B, g.T);
    const sgn = v => (v > 1e-9 ? '+' : v < -1e-9 ? '−' : '') + (step < 0.1 ? A.f2(Math.abs(v)) : A.f1(Math.abs(v)));
    A.yAxis(svg, g, y, A.ticks(lo, hi, step), sgn, 'Change in absolute error (°F)');
    A.xAxis(svg, g, x, A.ticks(0, kmax, 20), v => v + ' min', 'Minutes after a report that moved the bank');
    svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: y(0), y2: y(0), stroke: 'var(--rule)', 'stroke-width': 1.2 }));
    const fx = ev.systems.FX;
    if (fx) A.band(svg, ev.k.map(x), (fx.lo || []).map(v => (fin(v) ? y(v) : null)), (fx.hi || []).map(v => (fin(v) ? y(v) : null)), 'var(--accent)');
    drawn.filter(id => id !== 'FX').forEach(id =>
      A.lineSeries(svg, ev.k.map(x), ev.systems[id].delta.map(v => (fin(v) ? y(v) : null)), { stroke: A.color(id), 'stroke-width': A.width(id) }));
    if (fx) A.lineSeries(svg, ev.k.map(x), fx.delta.map(v => (fin(v) ? y(v) : null)), { stroke: A.color('FX'), 'stroke-width': A.width('FX') });
    svg.appendChild(txt(A.int(ev.events) + ' reports moved the bank toward the day\'s extreme, ' + metricWord()
                        + '. Below zero means the system moved closer to the settle', { x: g.L, y: g.T - 8, class: 'axl' }));
    overlay(svg, g, p => {
      const i = nearestIndex(ev.k, x.invert(p.x));
      const k = ev.k[i];
      const pairs = drawn.map(id => [id, ev.systems[id].delta[i]]).filter(o => fin(o[1])).sort((a, b) => a[1] - b[1])
        .map(o => [A.swatch(o[0]), A.signed1(o[1]) + (o[0] === 'FX' && fin(ev.systems.FX.lo[i]) ? ' (' + A.iv(ev.systems.FX.lo[i], ev.systems.FX.hi[i], A.signed1) + ')' : '')]);
      return { x: x(k), html: rows(k + ' minutes after the report, change in absolute error', pairs,
                                   'relative to ten minutes before the report; market band is the 95 percent bootstrap over dates') };
    });
  }

  // ------------------------------------------------------------- method note
  function drawNote() {
    const note = $('#accDynMethod');
    if (!note) return;
    const meta = dyn.meta || {};
    const coh = (meta.cohorts && meta.cohorts.matched11) || {};
    const n = S.metric === 'high' ? coh.n_high : coh.n_low;
    const ev = dyn.event && dyn.event.metric && dyn.event.metric[S.metric];
    const pc = dyn.rate && dyn.rate.perCityDay && dyn.rate.perCityDay.metric && dyn.rate.perCityDay.metric[S.metric];
    let sample = 'Sample ' + A.int(n) + ' matched city-days for ' + metricWord() + (ev ? ', ' + A.int(ev.events) + ' bank-moving reports' : '') + '.';
    if (pc && pc.fx && fin(pc.fx.median)) {
      const meds = A.PANEL.map(id => pc.tools && pc.tools[id]).filter(fin);
      sample += ' The market changed its crossing a median ' + A.int(pc.fx.median) + ' times per city-day (quartiles '
        + A.int(pc.fx.q1) + ' to ' + A.int(pc.fx.q3) + ')'
        + (meds.length ? ', the panel tools between ' + A.int(Math.min.apply(null, meds)) + ' and ' + A.int(Math.max.apply(null, meds)) + ' times.' : '.');
    }
    A.methodNote(note, {
      title: 'How convergence, movement, and reaction are measured',
      body: [
        'Convergence measures how early a system locks onto the right temperature and stays there. For a tolerance of $d$ degrees, a system has converged by lead $h$ if its value stayed within $d$ degrees of the settle from $h$ onward.',
        { tex: 'S_s(h; d) = \\frac{1}{N}\\left|\\{\\, i : |v_{s,i}(h\') - o_i| \\le d \\text{ for every } h\' \\le h \\,\\}\\right|' },
        'Median lead is the hour where $S_s(h; d)$ crosses one half.',
        'Movement rate is how often a system\u2019s value changes, either per hour of lead or per station-local clock hour, counting only the hours a system was actually live.',
        'Reaction traces the average change in a system\u2019s error in the minutes around a report that moved the observed extreme, comparing error just before the report to error afterward. A negative number means the system moved closer to the eventual settle.',
      ],
      rules: [
        'The market\u2019s value is the whole-degree crossing of its price ladder, rounded the same way as in the lead-curve figure, the tolerance is 1 or 2 degrees, selectable by tab.',
        'Convergence in the top panel uses the matched cohort of eleven tools plus the market at lead zero, or, for six additional sources shown on their own axis, whichever city-days each source covers.',
        'A qualifying report raised the observed extreme by at least a degree on highs, or lowered it on lows, before the day\u2019s true extreme was reached. Bands are the same 1,000-draw bootstrap used elsewhere, seed 20260910, and a point under 30 qualifying events is left blank.',
        'The traced city-day at the bottom shows every report as a dot, the running observed extreme as a grey step, the market\u2019s ten-minute price track with its 10th-to-90th-percentile band, and each tool\u2019s forecast held flat after its last update.',
      ],
      n: sample,
    });
  }

  return { draw };
})();
