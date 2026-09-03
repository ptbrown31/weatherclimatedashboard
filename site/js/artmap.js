/* The station map on the article page.

   The article says these markets cover named stations in named cities, and the
   next question is which. This draws them: the contract stations on the map,
   each one a link to its own page.

   No market data. The board with prices on it is the front page, and this is an
   article — a reader who wants today's numbers is one click away and told so. */
window.WXArtMap = (() => {
  const { el, txt, h, $ } = WXC;

  async function init() {
    const svg = $('#artMap'); if (!svg) return;
    const [base, sum] = await Promise.all([
      fetch(WXC.asset('basemap.json')).then(r => r.json()).catch(() => null),
      WXD.get('summary.json').then(r => r.data).catch(() => null),
    ]);
    if (!base || !sum || !sum.cities) { svg.remove(); return; }
    svg.innerHTML = '';
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)',
                                 stroke: 'var(--map-line)', 'stroke-width': 1 }));
    const us = sum.cities.filter(c => c.onConus && c.px != null);
    const placed = [];
    us.forEach(c => {
      const a = el('a', { href: WXC.cityHref(c) });
      a.appendChild(el('circle', { cx: c.px, cy: c.py, r: 4.2, fill: 'var(--accent)',
                                   stroke: 'var(--panel)', 'stroke-width': 1.2 }));
      svg.appendChild(a);
      // a label only where it does not sit on another one; the dot is the
      // contract either way and every dot is a link
      for (const [dx, dy] of [[7, 4], [-7, 4], [0, -7], [0, 13]]) {
        const t = txt(c.city, { x: c.px + dx, y: c.py + dy, class: 'lbl',
                                'text-anchor': dx < 0 ? 'end' : (dx > 0 ? 'start' : 'middle'),
                                'font-size': 9, 'font-weight': 700, fill: 'var(--ink)' });
        a.appendChild(t);
        const b = t.getBBox();
        const box = [b.x - 1, b.y - 1, b.x + b.width + 1, b.y + b.height + 1];
        if (!placed.some(q => !(box[2] < q[0] || box[0] > q[2] || box[3] < q[1] || box[1] > q[3]))) {
          placed.push(box); break;
        }
        a.removeChild(t);
      }
    });
    const cap = $('#artMapCap');
    if (cap) cap.textContent = us.length + ' United States stations carry daily high and low contracts, with '
      + sum.cities.filter(c => !c.onConus).length + ' more abroad and in Hawaii quoted in Celsius. '
      + 'Click a station for its chart.';
  }
  return { init };
})();
