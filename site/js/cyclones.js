/* The tropical cyclone area: one small map per basin, as the way in.

   The exchange lists cyclone contracts over two oceans and the full page shows
   one at a time. A reader arriving from the navigation has to pick an ocean
   before anything on that page is the one they want, so this page asks the
   question with the two maps themselves: the Pacific on the left, the Atlantic
   on the right, each drawn from the same geography the full map uses, each
   opening its own view.

   These are thumbnails and carry no prices, no shading, no reference-location
   dots and no tooltips. The full page carries all of that, and a thumbnail
   that answers questions is a thumbnail nobody clicks. What they do carry is
   the coastline, the storms the NHC has active, and their forecast tracks, so
   the choice is made on where the weather is.

   Data in: assets/hurricane-geo.json for the coastline, snapshots/hurricane.json
   for the storms. Equirectangular fitted to the basin box, the same deliberate
   stretch the full map uses, so the two look like the same map. */
window.WXCyclones = (() => {
  const { el, txt, h, $, asset } = WXC;
  const BASINS = [
    { key: 'EP', name: 'East and Central Pacific', short: 'Pacific',
      box: [-180.0, 0.0, -85.0, 40.0] },
    { key: 'AL', name: 'Atlantic', short: 'Atlantic',
      box: [-101.0, 4.0, -40.0, 48.0] },
  ];
  const W = 480, Hh = 300;

  // a storm belongs to the Atlantic view or to the other one, the same split
  // the full map makes, so a storm appears under the ocean it is actually in
  const inBasin = (s, key) => (key === 'AL' ? s.basin === 'AL' : s.basin !== 'AL');

  function miniMap(B, geo, storms) {
    const [b0, la0, b1, la1] = B.box;
    const kx = W / (b1 - b0), ky = Hh / (la1 - la0);
    const X = lon => (lon - b0) * kx, Y = lat => (la1 - lat) * ky;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + Hh, class: 'minimap' });
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: Hh, fill: 'var(--map-sea)' }));
    const inView = rr => rr.some(r => r.some(q => q[0] >= b0 && q[0] <= b1 && q[1] >= la0 && q[1] <= la1));
    const land = rr => svg.appendChild(el('path', {
      d: rr.map(r => 'M' + r.map(q => X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join('L') + 'Z').join(' '),
      fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': 0.5 }));
    if (geo) {
      Object.values(geo.countries || {}).forEach(rr => { if (inView(rr)) land(rr); });
      (geo.nation || []).forEach(r => land([r]));
      // the coastal states carry Hawaii, which the nation outline does not, so
      // the Pacific thumbnail has the islands its storms are aimed at
      Object.values(geo.states || {}).forEach(rr => { if (inView(rr)) land(rr); });
    }
    // the storms: where each has been and where it is forecast to go, then the
    // position it is at now
    let drawn = 0;
    (storms || []).filter(s => inBasin(s, B.key)).forEach(s => {
      /* `past` and `track` nest to no fixed depth. A track is usually a list
         of one line, but a storm crossing the dateline is split into two, and
         that arrives as a list of lines inside the list. Walking down to the
         first level whose own first element is a number is what finds the
         lines wherever they sit; assuming one depth drew MNaN,NaN paths for
         the storm that crossed. */
      const lines = [];
      const walk = g => {
        if (!Array.isArray(g) || !g.length) return;
        if (typeof g[0] === 'number') return;                         // one point, not a line
        if (Array.isArray(g[0]) && typeof g[0][0] === 'number') { lines.push(g); return; }
        g.forEach(walk);
      };
      walk(s.past); walk(s.track);
      lines.forEach(line => {
        const d = line.filter(q => Array.isArray(q) && typeof q[0] === 'number' && typeof q[1] === 'number')
          .map(q => X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1));
        if (d.length > 1) {
          svg.appendChild(el('path', { d: 'M' + d.join('L'), fill: 'none', stroke: 'var(--accent)',
                                       'stroke-width': 1.6, 'stroke-opacity': 0.9 }));
        }
      });
      if (s.lon != null && s.lat != null) {
        drawn++;
        svg.appendChild(el('circle', { cx: X(s.lon), cy: Y(s.lat), r: 4.5, fill: 'var(--warm)',
                                       stroke: 'var(--panel)', 'stroke-width': 1.2 }));
        svg.appendChild(txt(s.name, { x: X(s.lon) + 8, y: Y(s.lat) + 3.5, 'font-size': 11,
                                      'font-weight': 700, fill: 'var(--ink)' }));
      }
    });
    return { svg, drawn };
  }

  async function init() {
    WXC.chrome('tropical-cyclone-markets.html');
    const root = $('#basins');
    const loads = [];
    let H = null, geo = null;
    try {
      const r = await WXD.get('hurricane.json', 30);
      loads.push(r);
      H = r.data;
      geo = await fetch(asset('hurricane-geo.json')).then(x => x.json()).catch(() => null);
    } catch (e) { /* the maps still draw, without the storms */ }
    const bar = $('#pageStatus');
    if (bar) {
      bar.textContent = '';
      bar.appendChild(WXC.statusEl(loads.length ? loads : [{ source: 'none', stale: true, asof: null, ageMin: null }], 30));
    }
    BASINS.forEach(B => {
      const m = miniMap(B, geo, (H && H.storms) || []);
      const card = h('a', { class: 'basincard', href: 'hurricane.html?basin=' + B.key,
                            'aria-label': 'Open the ' + B.name + ' view' }, [
        h('div', { class: 'lt', text: B.name }),
        h('div', { class: 'cap', style: 'margin:0 0 6px',
                   text: m.drawn ? m.drawn + ' active ' + (m.drawn === 1 ? 'storm' : 'storms') : 'No active storms' })]);
      card.appendChild(m.svg);
      root.appendChild(card);
    });
  }
  return { init };
})();
