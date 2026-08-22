/* Hurricane tracking: NHC forecast tracks, cones and formation odds over the
   basin geography. Data in: hurricane.json (storms, outlook, season) and
   assets/hurricane-geo.json + assets/basemap.json (nation coastline).
   Equirectangular fitted to the basin box with independent x/y scales so
   the panel fills its frame (a deliberate stretch, not an error). */
window.WXHur = (() => {
  const { el, txt, h, $ } = WXC;
  const BASINS = {
    AL: { name: 'Atlantic', box: [-101.0, 4.0, -40.0, 48.0], outlook: 'Atlantic' },
    EP: { name: 'East and Central Pacific', box: [-180.0, 0.0, -85.0, 40.0], outlook: 'Pacific' },
  };
  let H = null, GEO = null, NATION = null, basin = 'AL', tip = null;

  function ptColor(p) {
    if (p.kt != null && p.kt >= 96) return '#c0392b';
    if (p.type === 'HU') return '#e08a1e';
    if (p.type === 'TS' || p.type === 'STS') return '#2b7bba';
    return '#7fa6c6';
  }
  function rings(coords) {
    if (!coords || !coords.length) return [];
    if (typeof coords[0][0] === 'number') return [coords];
    if (typeof coords[0][0][0] === 'number') return coords;
    return coords.flat();
  }
  function drawNhc(svg, X, Y, b) {
    const outl = (H.outlook || []).filter(o => (o.basin || '') === BASINS[b].outlook);
    const oplaced = [];
    outl.forEach(o => rings(o.region).forEach(r => {
      svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('') + 'Z',
        fill: 'rgba(224,138,30,.13)', stroke: '#e08a1e', 'stroke-width': 1.6, 'stroke-dasharray': '6 4' }));
      const cx = Math.min(r.reduce((s, p) => s + X(p[0]), 0) / r.length, 905);
      let cy = r.reduce((s, p) => s + Y(p[1]), 0) / r.length;
      while (oplaced.some(q => Math.abs(cx - q[0]) < 115 && Math.abs(cy - q[1]) < 34)) cy += 34;
      oplaced.push([cx, cy]);
      svg.appendChild(txt((o.prob7 || '?') + ' / 7 days', { x: cx, y: cy, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: '#b26a08', class: 'lbl' }));
      svg.appendChild(txt((o.prob2 || '?') + ' / 2 days', { x: cx, y: cy + 14, 'text-anchor': 'middle', 'font-size': 9.5, fill: '#b26a08', class: 'lbl' }));
    }));
    const storms = (H.storms || []).filter(s => b === 'AL' ? s.basin === 'AL' : s.basin !== 'AL');
    storms.forEach(s => {
      s.cone.forEach(c => rings(c).forEach(r => svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('') + 'Z', fill: 'rgba(100,116,139,.14)', stroke: '#64748b', 'stroke-width': 1 }))));
      s.past.forEach(c => rings(c).forEach(r => svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(''), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.3, 'stroke-dasharray': '3 3' }))));
      s.track.forEach(c => rings(c).forEach(r => svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(''), fill: 'none', stroke: 'var(--navy)', 'stroke-width': 2 }))));
      s.points.forEach((p, i) => {
        svg.appendChild(el('circle', { cx: X(p.lon), cy: Y(p.lat), r: p.tau === 0 ? 6 : 4.5, fill: ptColor(p), stroke: 'var(--panel)', 'stroke-width': 1.2 }));
        if (p.label) svg.appendChild(txt(p.label.replace(':00', '') + (p.kt ? ' · ' + p.kt + 'kt' : ''), { x: X(p.lon) + 8, y: Y(p.lat) + (i % 2 ? 14 : -8), 'font-size': 9, fill: 'var(--ink)', class: 'lbl' }));
        if (p.tau === 0) svg.appendChild(txt(s.classification + ' ' + s.name + ' · ' + s.intensityKt + 'kt · adv ' + (s.geometryAdvisory || s.advisory),
          { x: X(p.lon) + 10, y: Y(p.lat) - 20, 'font-size': 12.5, 'font-weight': 700, fill: 'var(--navy)', class: 'lbl' }));
      });
    });
    return { storms: storms.length, areas: outl.length };
  }

  function draw() {
    const B = BASINS[basin];
    const [b0, la0, b1, la1] = B.box, W = 980, Hh = 600;
    const kx = W / (b1 - b0), ky = Hh / (la1 - la0);
    const X = lon => (lon - b0) * kx, Y = lat => (la1 - lat) * ky;
    const svg = $('#basin'); svg.innerHTML = '';
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: Hh, fill: 'var(--map-sea)' }));
    const poly = (rr, fill, stroke, sw) => svg.appendChild(el('path', { d: rr.map(r => 'M' + r.map(p => X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('L') + 'Z').join(' '), fill, stroke, 'stroke-width': sw }));
    if (GEO) {
      Object.values(GEO.countries || {}).forEach(rr => poly(rr, 'var(--map-land)', 'var(--map-line)', .6));
      (NATION || []).forEach(r => poly([r], 'var(--map-land)', 'var(--map-line)', .6));
      Object.values(GEO.states || {}).forEach(rr => poly(rr, 'var(--map-land)', 'var(--map-line)', .7));
    }
    const counts = drawNhc(svg, X, Y, basin);
    $('#modeTitle').textContent = B.name.toUpperCase() + ' · NHC forecast tracks, cones and formation odds';
    $('#basinCap').textContent = (counts.storms ? '' : 'No active tropical cyclones in this basin at the last update. ') +
      'Orange dashed regions are NHC seven-day formation odds; cones and tracks draw automatically when a storm is active. ' +
      (basin === 'EP' ? 'The Central Pacific outlook is issued by CPHC and is not in this feed, so that part of the map shows storms only.' : '');
    const list = $('#storms'); list.innerHTML = '';
    (H.storms || []).forEach(s => list.appendChild(h('div', { class: 'stormrow' }, [
      h('b', { text: s.classification + ' ' + s.name }),
      h('span', { text: s.basin + ' · ' + s.intensityKt + ' kt · ' + (s.pressureMb || '--') + ' mb · advisory ' + s.advisory + (s.geometryAdvisory && s.geometryAdvisory !== s.advisory ? ' (map shows ' + s.geometryAdvisory + ')' : '') }),
      s.advisoryUrl ? h('a', { href: s.advisoryUrl, text: 'NHC advisory', target: '_blank', rel: 'noopener' }) : h('span'),
    ])));
    if (!(H.storms || []).length) list.appendChild(h('div', { class: 'cap', text: 'No active tropical cyclones in the NHC roster.' }));
    const s = H.season || {};
    $('#season').textContent = s.year ? `${s.year} Atlantic season to date from NHC best tracks: ${s.named} named (${(s.names || []).join(', ') || 'none'}), ${s.hurricanes} hurricanes, ${s.majors} major.` : '';
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('hurricane.json', 30);
    const [geo, bm] = await Promise.all([
      fetch('assets/hurricane-geo.json').then(x => x.json()).catch(() => null),
      fetch('assets/basemap.json').then(x => x.json()).catch(() => null)]);
    H = r.data; GEO = geo; NATION = bm ? bm.nationLonLat : null;
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 30));
    if (!H) { $('#basin').innerHTML = ''; $('#basin').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    [['b1', 'AL'], ['b2', 'EP']].forEach(([id, b]) => { $('#' + id).onclick = () => { basin = b; document.querySelectorAll('.bar button').forEach(x => x.classList.remove('on')); $('#' + id).classList.add('on'); draw(); }; });
    draw();
  }
  return { init };
})();
