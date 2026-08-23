/* The landing map: every station on one canvas, shaded by the NWS forecast.

   Data in: summary.json (stations, observed so far, forecast levels),
   field.json (the derived shading), assets/basemap.json (state outlines,
   pre-projected). Modes: tomorrow's highs and lows shaded by the NWS
   forecast, and today's observed-so-far against the forecast that was
   issued for the day. With the market layer on, the dots carry the gap
   between the market-implied median (live: the exchange's ladder; else a
   labelled placeholder) and the NWS forecast instead. */
window.WXMap = (() => {
  const { el, txt, h, $, deg } = WXC;
  const RAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];
  let summary = null, field = null, base = null, world = null, head = null, mode = 'hi', tip = null;
  const TOOL = { nws: 'National Weather Service', nbm: 'Blend of Models', lamp: 'Aviation guidance (LAMP)',
                 mav: 'GFS MOS', fx: 'ForecastEx' };
  const ord = n => n + (n % 10 === 1 && n !== 11 ? 'st' : n % 10 === 2 && n !== 12 ? 'nd' : n % 10 === 3 && n !== 13 ? 'rd' : 'th');

  // The four numbers the day turns on, precomputed by the pipeline so the
  // landing page stays light. Each is a fact with its own provenance, not a
  // summary of the site: the accuracy standing, yesterday's largest single
  // miss, the widest disagreement about tomorrow, and one hurricane contract.
  function drawCards() {
    const host = $('#cards'); if (!host) return;
    host.innerHTML = '';
    if (!head) return;
    const card = (value, label, sub, tipHtml, href) => {
      const d = h('div', { class: 'tile' + (href ? ' link' : '') }, [
        h('div', { class: 'tv', text: value }), h('div', { class: 'tl', text: label }),
        sub ? h('div', { class: 'ts', text: sub }) : h('span')]);
      if (tipHtml) { d.onmousemove = e => tip.show(e, tipHtml); d.onmouseleave = () => tip.hide(); }
      if (href) d.onclick = () => { location.href = href; };
      host.appendChild(d);
    };
    const a = head.accuracy;
    if (a && (a.rank || []).length) {
      // the exchange's own standing when it is scored, otherwise whoever leads
      const i = Math.max(0, a.rank.findIndex(r => r.src === 'fx'));
      const r = a.rank[i];
      card(deg(r.mae) + 'F', TOOL[r.src] + ' ranks ' + ord(i + 1) + ' of ' + a.rank.length,
        'Average error on daily highs, last ' + a.days + ' scored days',
        tip.rows('Accuracy over the last ' + a.days + ' scored days',
          a.rank.map((x, k) => [ord(k + 1) + ' ' + TOOL[x.src], deg(x.mae) + 'F  (n ' + x.n + ')']),
          'mean absolute error on the daily high, pooled across every station. n differs by source, because the archive holds fewer cycles for some of them · click → the scorecard'),
        'scorecard.html');
    }
    const e = head.largestError;
    if (e) card(deg(Math.abs(e.error)) + 'F', 'Largest error, ' + e.city + ' ' + e.side,
      'Settled ' + deg(e.observed) + ' against ' + deg(e.forecast) + ' from ' + TOOL[e.source],
      tip.rows(e.city + ' (' + e.station + ') — the day’s largest miss',
        [['Day', e.date], ['Observed ' + e.side, deg(e.observed)], [TOOL[e.source] + ' forecast', deg(e.forecast)],
         ['Error', (e.error > 0 ? '+' : '') + (Math.round(e.error * 10) / 10) + '°' + (e.error > 0 ? ', too warm' : ', too cold')]],
        'the single largest error on the newest scored day · click → that station’s chart'),
      'city.html?station=' + e.station);
    const w = head.widestSpread;
    if (w) card(deg(w.spread) + 'F', 'Widest disagreement, ' + w.city + ' ' + w.side,
      'Tomorrow: ' + deg(w.low) + ' to ' + deg(w.high) + ' across ' + w.sources.length + ' sources',
      tip.rows(w.city + ' (' + w.station + ') — the widest spread for ' + w.day,
        [['Coldest forecast', deg(w.low)], ['Warmest forecast', deg(w.high)], ['Spread', deg(w.spread)],
         ['Sources', w.sources.map(k => TOOL[k] || k).join(', ')]],
        'click → the scorecard’s tomorrow view'),
      'scorecard.html');
    const hu = head.hurricane;
    if (hu && hu.yes != null) {
      const c = Math.round(hu.yes * 100), pay = WXM.payout ? WXM.payout(Math.round((hu.ask != null ? hu.ask : hu.yes) * 100)) : null;
      card(c + '%', 'At least one major Atlantic hurricane', hu.expiryLabel + (pay ? ' · a Yes pays ' + pay + '×' : ''),
        tip.rows('The exchange on ' + hu.expiryLabel, [['Contract', hu.label], ['Yes price', c + '¢'],
          ['Buy Yes now at', hu.ask != null ? Math.round(hu.ask * 100) + '¢' : null],
          ['Pays', pay ? pay + '× net of fees' : null]], 'click → the hurricane page'),
        'hurricane.html');
    }
  }



  function fieldColor(v) {
    const [a, b] = field.domain, t = Math.max(0, Math.min(1, (v - a) / (b - a))) * (RAMP.length - 1);
    const i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = s => [1, 3, 5].map(k => parseInt(s.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((av, k) => Math.round(av + (B[k] - av) * f)).join(',') + ')';
  }

  const MODES = {
    hi:  { title: c => "TOMORROW'S HIGHS · shaded by the National Weather Service forecast for " + tmw(), fld: 2,
           val: c => c.nwsHighTomorrow, div: c => WXM.on() ? (WXM.implied(c) || {}).divHigh : null },
    lo:  { title: c => "TOMORROW'S LOWS · shaded by the National Weather Service forecast for " + tmw(), fld: 3,
           val: c => c.nwsLowTomorrow, div: c => WXM.on() ? (WXM.implied(c) || {}).divLow : null },
    obs: { title: c => "TODAY · observed high so far against the NWS high issued for the day", fld: null,
           val: c => c.obsHighSoFar,
           div: c => { const ref = c.nwsIssuedHigh != null ? c.nwsIssuedHigh : c.nwsHighToday;
                       return (c.obsHighSoFar != null && ref != null) ? Math.round((c.obsHighSoFar - ref) * 10) / 10 : null; } },
  };
  const tmw = () => { const c = summary.cities.find(x => x.onConus); return c && c.markers ? c.markers.tomorrow : ''; };

  // ---- tooltips. Dates on the snapshot are the station's local calendar
  //      dates as 'YYYY-MM-DD' strings, so they are printed by hand here;
  //      parsing them through Date would shift them by the browser's zone.
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const isoDate = s => { if (!s || s.length < 10) return ''; return MON[+s.slice(5, 7) - 1] + ' ' + (+s.slice(8, 10)); };
  const dg = v => (v == null ? '—' : deg(v));
  const pair = (a, b) => (a == null && b == null ? null : dg(a) + ' / ' + dg(b));
  const gap = d => (d == null ? '' : ' (' + (d > 0 ? '+' + d : d < 0 ? '−' + Math.abs(d) : '0') + '°)');
  const srcTag = s => (s === 'tgroup' ? 'tenths' : s === 'body' ? 'whole degrees' : null);
  const FIELD_FOOT = 'inverse-distance interpolation of the listed stations’ NWS forecasts; not an NWS product';

  // the market-implied median for one side, as a tooltip value: the number
  // with its gap against the NWS forecast, or the reason there is none
  const STATE_TEXT = { unavailable: 'quotes unavailable', unlisted: 'no market listed', day: 'quote summary is for another day',
                       'tomorrow-unlisted': 'tomorrow’s contracts not listed yet', 'no-bids': 'no bids yet' };
  function impliedText(m, v, d, edge) {
    if (!m) return 'not listed yet';
    if (v != null) return deg(v) + gap(d);
    if (edge) return edge === 'above' ? 'above the top strike' : 'below the bottom strike';
    return STATE_TEXT[m.state] || 'no bids yet';
  }

  function dotTip(c) {
    const mk = c.markers || {};
    const m = WXM.on() ? WXM.implied(c) : null;
    const tag = WXM.live() ? 'ForecastEx' : 'placeholder';
    const tomorrow = [
      ['NWS high / low tomorrow', dg(c.nwsHighTomorrow) + ' / ' + dg(c.nwsLowTomorrow)],
      ['NBM high / low tomorrow', pair(c.nbmHighTomorrow, c.nbmLowTomorrow)],
      ['GFS MOS high / low tomorrow', pair(c.mavHighTomorrow, c.mavLowTomorrow)],
      WXM.on() ? ['Implied high (' + tag + ')', impliedText(m, m && m.impliedHigh, m && m.divHigh, m && m.edgeHigh)] : null,
      WXM.on() ? ['Implied low (' + tag + ')', impliedText(m, m && m.impliedLow, m && m.divLow, m && m.edgeLow)] : null,
    ];
    const sh = srcTag(c.obsHighSrc), sl = srcTag(c.obsLowSrc);
    const obsTag = sh || sl ? ' <span class="tk">(' + (sh === sl || !sl ? sh : !sh ? sl : sh + ' / ' + sl) + ')</span>' : '';
    let latest = null;
    if (c.obsLatest && c.obsLatest.t) {
      const ms = Date.parse(c.obsLatest.t), day = WXC.dateShort(ms, c.tz);
      latest = (day !== isoDate(mk.day) ? day + ' ' : '') + WXC.clockFull(ms, c.tz) + (c.obsLatest.type ? ' ' + c.obsLatest.type : '');
    }
    const today = [
      c.nwsIssuedHigh != null ? ['NWS high issued for today', deg(c.nwsIssuedHigh)] : ['NWS high today (standing)', dg(c.nwsHighToday)],
      ['Observed high / low so far', (c.obsHighSoFar == null && c.obsLowSoFar == null) ? '—' : dg(c.obsHighSoFar) + ' / ' + dg(c.obsLowSoFar) + obsTag],
      ['Latest METAR', latest],
    ];
    return tip.rows(c.city + ' (' + c.station + ') — tomorrow ' + isoDate(mk.tomorrow), tomorrow) +
      tip.rows('<span class="tk" style="display:block;margin-top:5px">Today · ' + isoDate(mk.day) + '</span>', today, 'click → city chart');
  }

  // one cell of the shading: the interpolated NWS level under the pointer
  function cellTip(i) {
    const cell = field.cells[i], M = MODES[mode];
    if (!cell || M.fld == null) return '';
    return tip.rows('NWS forecast field (derived)', [[mode === 'lo' ? 'Tomorrow’s low' : 'Tomorrow’s high', deg(cell[M.fld])]], FIELD_FOOT);
  }
  const cellIndex = e => { const i = e.target && e.target.getAttribute && e.target.getAttribute('data-i'); return i == null ? null : +i; };

  function draw() {
    const svg = $('#map'); svg.innerHTML = '';
    const M = MODES[mode];
    const defs = el('defs'), cp = el('clipPath', { id: 'us' });
    cp.appendChild(el('path', { d: base.statePaths })); defs.appendChild(cp); svg.appendChild(defs);
    if (M.fld != null && field && field.cells) {
      const fg = el('g', { 'clip-path': 'url(#us)', 'data-tip-pin': '' });
      field.cells.forEach((cell, i) => fg.appendChild(el('rect', { x: cell[0] - 1, y: cell[1] - 1, width: field.step + 2, height: field.step + 2, fill: fieldColor(cell[M.fld]), 'data-i': i })));
      // one listener for the whole field, keyed by the cell index on the target
      fg.onmousemove = e => { const i = cellIndex(e); if (i != null) tip.show(e, cellTip(i)); };
      fg.onmouseleave = () => tip.hide();
      fg.onclick = e => { const i = cellIndex(e); if (i != null) tip.pin(e, cellTip(i)); };
      svg.appendChild(fg);
    } else {
      svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    }
    svg.appendChild(el('path', { d: base.statePaths, class: 'state' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2' }));
    $('#modeTitle').textContent = M.title();

    plot(svg, summary.cities.filter(c => c.onConus), M, c => c.px, c => c.py, [2, 2, 958, 598], false);
    const legend = $('#legend');
    legend.innerHTML = '';
    if (mode === 'obs') legend.innerHTML = '<span><i style="border-color:var(--warm)"></i>Running above the NWS high issued for the day</span><span><i style="border-color:var(--cool)"></i>Running below</span><span>Radius scales with the gap · number is the observed high so far</span>';
    else if (WXM.on()) { const w = WXM.live() ? 'ForecastEx implied median' : 'placeholder'; legend.innerHTML = '<span><i style="border-color:var(--warm)"></i>Implied above the NWS forecast (' + w + ')</span><span><i style="border-color:var(--cool)"></i>Implied below (' + w + ')</span><span><i style="border-color:var(--line)"></i>' + (WXM.live() ? 'Tomorrow’s contracts not listed yet, no bids, or the median sits beyond the ladder' : 'No value') + '</span><span>Pale shading is the NWS forecast level (derived)</span>'; }
    else legend.innerHTML = '<span>Number is the NWS forecast · pale shading is the NWS forecast level interpolated between stations (derived)</span>';
    drawWorld();
  }

  // Dots and labels for one canvas. Both maps carry the same encoding, so the
  // international stations read the same way they do on the city page's picker:
  // grey where the mode has no value for them, since US government feeds carry
  // observations everywhere but forecasts only for the US and, through NBM, Canada.
  const CANDS = [[9, 3], [9, -12], [-9, 3], [-9, -12], [9, 15], [9, -25], [-9, 15], [-9, -25], [0, 24], [0, -33], [18, 3], [-18, 3], [18, 15], [-18, 15], [18, -12], [-18, -12]];
  function plot(svg, cities, M, fx, fy, bounds, withUnit) {
    const placed = [];
    const hit = b => b[0] < bounds[0] || b[1] < bounds[1] || b[2] > bounds[2] || b[3] > bounds[3]
      || placed.some(q => b[0] < q[2] && q[0] < b[2] && b[1] < q[3] && q[1] < b[3]);
    const rows = cities.slice().sort((a, b) => { const av = M.div(a), bv = M.div(b); return (bv == null ? -1 : Math.abs(bv)) - (av == null ? -1 : Math.abs(av)); });
    rows.forEach(c => {
      const v = M.div(c), av = M.val(c), X = fx(c), Y = fy(c);
      const g = el('g', { class: 'dot' });
      let r;
      if (v == null) {
        r = av == null ? 4.5 : 7;
        g.appendChild(el('circle', { cx: X, cy: Y, r, fill: av == null ? 'var(--line)' : 'var(--panel)', stroke: 'var(--ink)', 'stroke-width': 1.2 }));
      } else {
        r = 5.5 + 8.5 * Math.min(Math.abs(v), 5) / 5;
        g.appendChild(el('circle', { cx: X, cy: Y, r: r + 3.5, fill: 'var(--panel)', 'fill-opacity': .95 }));
        g.appendChild(el('circle', { cx: X, cy: Y, r, fill: v > 0 ? 'var(--warm)' : (v < 0 ? 'var(--cool)' : 'var(--muted)'), 'fill-opacity': .97, stroke: 'var(--ink)', 'stroke-width': .6 }));
      }
      placed.push([X - r, Y - r, X + r, Y + r]);
      g.onmousemove = e => tip.show(e, dotTip(c));
      g.onmouseleave = () => tip.hide();
      g.onclick = () => { location.href = 'city.html?station=' + c.station; };
      svg.appendChild(g);
    });
    rows.forEach(c => {
      const v = M.div(c), av = M.val(c), X = fx(c), Y = fy(c);
      if (av == null && v == null) return;
      const big = v != null && Math.abs(v) >= 1.5;
      // the international stations settle in Celsius, so their labels carry the unit
      const s = c.station.slice(1) + (av != null ? ' ' + av.toFixed(0) + '°' + (withUnit ? (c.unit || '') : '') : '')
        + (big ? ' (' + (v > 0 ? '+' : '') + v.toFixed(0) + ')' : '');
      for (const [dx, dy] of CANDS) {
        const t = txt(s, { x: X + dx, y: Y + dy + 4, class: 'lbl', 'text-anchor': dx < 0 ? 'end' : (dx > 0 ? 'start' : 'middle'),
          'font-size': big ? 10.5 : 8.5, 'font-weight': 700, fill: big ? 'var(--navy)' : 'var(--ink)' });
        svg.appendChild(t);
        const b = t.getBBox(), bb = [b.x - 1, b.y - 1, b.x + b.width + 1, b.y + b.height + 1];
        if (!hit(bb)) { placed.push(bb); break; }
        t.remove();
      }
    });
  }

  // the international stations and Honolulu, on the world canvas below the national one
  function drawWorld() {
    const svg = $('#mapW'); if (!svg || !world) return;
    svg.innerHTML = '';
    svg.appendChild(el('rect', { x: 0, y: 0, width: 960, height: 480, fill: 'var(--map-sea)' }));
    svg.appendChild(el('path', { d: world.worldPaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': .8 }));
    plot(svg, summary.cities.filter(c => !c.onConus), MODES[mode], c => c.wx, c => c.wy, [2, 62, 958, 378], true);
  }

  // title text for the off-canvas list: these stations report in their own
  // unit (Celsius outside the US), so the unit letter is spelled out
  function intlTitle(c) {
    const u = v => (v == null ? '—' : deg(v) + (c.unit || ''));
    const m = WXM.on() ? WXM.implied(c) : null;
    const parts = [c.city + ' (' + c.station + ')', 'observed so far ' + u(c.obsHighSoFar) + ' / ' + u(c.obsLowSoFar)];
    if (c.nwsHighTomorrow != null || c.nwsLowTomorrow != null) parts.push('NWS tomorrow ' + u(c.nwsHighTomorrow) + ' / ' + u(c.nwsLowTomorrow));
    else if (c.nbmHighTomorrow != null || c.nbmLowTomorrow != null) parts.push('NBM tomorrow ' + u(c.nbmHighTomorrow) + ' / ' + u(c.nbmLowTomorrow));
    if (m && (m.impliedHigh != null || m.impliedLow != null)) parts.push('implied ' + u(m.impliedHigh) + ' / ' + u(m.impliedLow) + (WXM.live() ? '' : ' (placeholder)'));
    return parts.join(' · ');
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.getAll(['summary.json', 'field.json']);
    const [bm, wd] = await Promise.all([
      fetch('assets/basemap.json').then(x => x.json()).catch(() => null),
      fetch('assets/world.json').then(x => x.json()).catch(() => null)]);
    summary = r['summary.json'].data; field = r['field.json'].data; base = bm; world = wd;
    head = (await WXD.get('headline.json', 10)).data;
    drawCards();
    await WXM.loadSummary();
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r['summary.json'], r['field.json']], 10));
    if (!summary || !base) { $('#map').innerHTML = ''; $('#map').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    [['m1', 'hi'], ['m2', 'lo'], ['m3', 'obs']].forEach(([id, m]) => {
      $('#' + id).onclick = () => { mode = m; document.querySelectorAll('.bar button').forEach(b => b.classList.remove('on')); $('#' + id).classList.add('on'); draw(); };
    });
    draw();
    // international stations and Honolulu are not on this canvas; list them
    const intl = summary.cities.filter(c => !c.onConus);
    const ul = $('#intl'); if (ul) { ul.innerHTML = ''; intl.forEach(c => ul.appendChild(h('a', { href: 'city.html?station=' + c.station, text: c.city + ' ' + (c.obsHighSoFar != null ? deg(c.obsHighSoFar) : ''), style: 'margin-right:14px', title: intlTitle(c) }))); }
  }
  return { init };
})();
