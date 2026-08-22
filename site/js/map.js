/* The landing map: every station on one canvas, shaded by the NWS forecast.

   Data in: summary.json (stations, observed so far, forecast levels),
   field.json (the derived shading), assets/basemap.json (state outlines,
   pre-projected). Modes: tomorrow's highs and lows shaded by the NWS
   forecast, and today's observed-so-far against the forecast that was
   issued for the day. With the market layer on, the dots carry the gap
   between the placeholder implied value and the NWS forecast instead. */
window.WXMap = (() => {
  const { el, txt, h, $, deg } = WXC;
  const RAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];
  let summary = null, field = null, base = null, mode = 'hi', tip = null;

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

  function draw() {
    const svg = $('#map'); svg.innerHTML = '';
    const M = MODES[mode];
    const defs = el('defs'), cp = el('clipPath', { id: 'us' });
    cp.appendChild(el('path', { d: base.statePaths })); defs.appendChild(cp); svg.appendChild(defs);
    if (M.fld != null && field && field.cells) {
      const fg = el('g', { 'clip-path': 'url(#us)' });
      field.cells.forEach(cell => fg.appendChild(el('rect', { x: cell[0] - 1, y: cell[1] - 1, width: field.step + 2, height: field.step + 2, fill: fieldColor(cell[M.fld]) })));
      svg.appendChild(fg);
    } else {
      svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    }
    svg.appendChild(el('path', { d: base.statePaths, class: 'state' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2' }));
    $('#modeTitle').textContent = M.title();

    const placed = [];
    const hit = b => b[0] < 2 || b[1] < 2 || b[2] > 958 || b[3] > 598 || placed.some(q => b[0] < q[2] && q[0] < b[2] && b[1] < q[3] && q[1] < b[3]);
    const cities = summary.cities.filter(c => c.onConus);
    const rows = cities.slice().sort((a, b) => { const av = M.div(a), bv = M.div(b); return (bv == null ? -1 : Math.abs(bv)) - (av == null ? -1 : Math.abs(av)); });
    rows.forEach(c => {
      const v = M.div(c), av = M.val(c);
      const g = el('g', { class: 'dot' });
      let r;
      if (v == null) {
        r = av == null ? 4.5 : 7;
        g.appendChild(el('circle', { cx: c.px, cy: c.py, r, fill: av == null ? 'var(--line)' : 'var(--panel)', stroke: 'var(--ink)', 'stroke-width': 1.2 }));
      } else {
        r = 5.5 + 8.5 * Math.min(Math.abs(v), 5) / 5;
        g.appendChild(el('circle', { cx: c.px, cy: c.py, r: r + 3.5, fill: 'var(--panel)', 'fill-opacity': .95 }));
        g.appendChild(el('circle', { cx: c.px, cy: c.py, r, fill: v > 0 ? 'var(--warm)' : (v < 0 ? 'var(--cool)' : 'var(--muted)'), 'fill-opacity': .97, stroke: 'var(--ink)', 'stroke-width': .6 }));
      }
      placed.push([c.px - r, c.py - r, c.px + r, c.py + r]);
      g.onmousemove = e => {
        const m = WXM.on() ? WXM.implied(c) : null;
        tip.show(e, '<b>' + c.city + ' (' + c.station + ')</b>' +
          'NWS high tomorrow ' + deg(c.nwsHighTomorrow) + ' · low ' + deg(c.nwsLowTomorrow) + '<br>' +
          'NWS high issued for today ' + deg(c.nwsIssuedHigh != null ? c.nwsIssuedHigh : c.nwsHighToday) + '<br>' +
          'Observed so far today ' + deg(c.obsHighSoFar) + (c.obsLowSoFar != null ? ' / ' + deg(c.obsLowSoFar) : '') +
          (m && m.impliedHigh != null ? '<br>Implied high ' + deg(m.impliedHigh) + ' (' + (m.divHigh > 0 ? '+' : '') + m.divHigh + '°) — placeholder' : ''));
      };
      g.onmouseleave = () => tip.hide();
      g.onclick = () => { location.href = 'city.html?station=' + c.station; };
      svg.appendChild(g);
    });
    const CANDS = [[9, 3], [9, -12], [-9, 3], [-9, -12], [9, 15], [9, -25], [-9, 15], [-9, -25], [0, 24], [0, -33], [18, 3], [-18, 3], [18, 15], [-18, 15], [18, -12], [-18, -12]];
    rows.forEach(c => {
      const v = M.div(c), av = M.val(c);
      if (av == null && v == null) return;
      const big = v != null && Math.abs(v) >= 1.5;
      const s = c.station.slice(1) + (av != null ? ' ' + av.toFixed(0) + '°' : '') + (big ? ' (' + (v > 0 ? '+' : '') + v.toFixed(0) + ')' : '');
      for (const [dx, dy] of CANDS) {
        const t = txt(s, { x: c.px + dx, y: c.py + dy + 4, class: 'lbl', 'text-anchor': dx < 0 ? 'end' : (dx > 0 ? 'start' : 'middle'),
          'font-size': big ? 10.5 : 8.5, 'font-weight': 700, fill: big ? 'var(--navy)' : 'var(--ink)' });
        svg.appendChild(t);
        const b = t.getBBox(), bb = [b.x - 1, b.y - 1, b.x + b.width + 1, b.y + b.height + 1];
        if (!hit(bb)) { placed.push(bb); break; }
        t.remove();
      }
    });
    const legend = $('#legend');
    legend.innerHTML = '';
    if (mode === 'obs') legend.innerHTML = '<span><i style="border-color:var(--warm)"></i>Running above the NWS high issued for the day</span><span><i style="border-color:var(--cool)"></i>Running below</span><span>Radius scales with the gap · number is the observed high so far</span>';
    else if (WXM.on()) legend.innerHTML = '<span><i style="border-color:var(--warm)"></i>Implied above the NWS forecast (placeholder)</span><span><i style="border-color:var(--cool)"></i>Implied below (placeholder)</span><span>Pale shading is the NWS forecast level (derived)</span>';
    else legend.innerHTML = '<span>Number is the NWS forecast · pale shading is the NWS forecast level interpolated between stations (derived)</span>';
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.getAll(['summary.json', 'field.json']);
    const bm = await fetch('assets/basemap.json').then(x => x.json()).catch(() => null);
    summary = r['summary.json'].data; field = r['field.json'].data; base = bm;
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r['summary.json'], r['field.json']], 10));
    if (!summary || !base) { $('#map').innerHTML = ''; $('#map').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    [['m1', 'hi'], ['m2', 'lo'], ['m3', 'obs']].forEach(([id, m]) => {
      $('#' + id).onclick = () => { mode = m; document.querySelectorAll('.bar button').forEach(b => b.classList.remove('on')); $('#' + id).classList.add('on'); draw(); };
    });
    draw();
    // international stations and Honolulu are not on this canvas; list them
    const intl = summary.cities.filter(c => !c.onConus);
    const ul = $('#intl'); if (ul) { ul.innerHTML = ''; intl.forEach(c => ul.appendChild(h('a', { href: 'city.html?station=' + c.station, text: c.city + ' ' + (c.obsHighSoFar != null ? deg(c.obsHighSoFar) : ''), style: 'margin-right:14px' }))); }
  }
  return { init };
})();
