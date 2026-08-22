/* The climate page: the settlement-basis series as NOAA and partners
   publish them, with a drag-to-fit trend tool. With the market layer on,
   placeholder contract markers sit at (expiration, threshold), coloured
   by a placeholder price, to show the layout. */
window.WXClimate = (() => {
  const { el, txt, h, $ } = WXC;
  const RAMP = ['#8b0000', '#d62728', '#ff7f0e', '#ffd700', '#adff2f', '#3ddc84', '#40e0d0', '#4fc3f7', '#1f77b4', '#00008b'];
  const X0 = { tempAnnual: 2000, tempMonthly: 2000, seaLevel: 1993, co2: 1995, amoc: 2004 };
  const PANELS = [
    ['tempAnnual', 'Global temperature, annual', '°C above preindustrial'],
    ['tempMonthly', 'Global temperature, monthly', '°C above preindustrial'],
    ['seaLevel', 'Global mean sea level', 'mm, satellite altimetry'],
    ['co2', 'Atmospheric CO2', 'ppm, Mauna Loa'],
    ['amoc', 'AMOC overturning at 26°N', 'Sv, RAPID array annual mean'],
  ];
  let tip = null;

  function priceColor(p) {
    if (p == null) return 'var(--line)';
    const t = Math.max(0, Math.min(1, p)) * (RAMP.length - 1), i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = s => [1, 3, 5].map(k => parseInt(s.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((a, k) => Math.round(a + (B[k] - a) * f)).join(',') + ')';
  }

  function panel(host, key, title, unit, ser, product, offsetC) {
    const div = h('div', { class: 'panel' });
    div.appendChild(h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: title }));
    div.appendChild(h('div', { class: 'psub cap', style: 'margin:2px 2px 6px', text: unit + (product ? ' · markers: ' + WXM.LABEL : '') }));
    const svg = el('svg', { viewBox: '0 0 960 330', class: 'ts' }); div.appendChild(svg);
    const note = h('div', { class: 'note', style: 'display:none;margin:6px 0 0;font-size:12px' }); div.appendChild(note);
    host.appendChild(div);

    const cs = product ? product.contracts : [];
    const W = 960, L = 56, R = 910, T = 16, B = 296;
    const x0 = X0[key] != null ? X0[key] : Math.min(...cs.map(c => c.year)) - 4;
    const x1 = Math.max(...cs.map(c => c.year), new Date().getUTCFullYear() + 5) + 3;
    const pts = ser.filter(q => q[0] >= x0);
    const vals = pts.map(q => q[1]).concat(cs.map(c => c.threshold));
    if (!vals.length) { svg.appendChild(txt('No data available.', { x: L, y: T + 16, class: 'axl' })); return; }
    const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.08 || 1;
    const X = v => L + (v - x0) / (x1 - x0) * (R - L), Y = v => B - (v - (lo - pad)) / ((hi + pad) - (lo - pad)) * (B - T);

    for (let yr = Math.ceil(x0 / 10) * 10; yr <= x1; yr += 10) {
      svg.appendChild(el('line', { x1: X(yr), x2: X(yr), y1: T, y2: B, class: 'grid' }));
      svg.appendChild(txt(yr, { x: X(yr), y: B + 16, 'text-anchor': 'middle', class: 'ax' }));
    }
    const thr = [...new Set(cs.map(c => c.threshold))].sort((a, b) => a - b);
    let lastLabY = 1e9;
    thr.slice().reverse().forEach(v => {
      svg.appendChild(el('line', { x1: L, x2: R, y1: Y(v), y2: Y(v), class: 'grid', 'stroke-dasharray': '5 4' }));
      if (Math.abs(Y(v) - lastLabY) < 11) return;
      lastLabY = Y(v);
      svg.appendChild(txt(key.startsWith('temp') ? v.toFixed(2) + '°C' : v, { x: R + 4, y: Y(v) + 3.5, class: 'ax' }));
    });
    const yt = 5, step = (hi - lo + 2 * pad) / yt;
    for (let i = 0; i <= yt; i++) { const v = lo - pad + i * step; svg.appendChild(txt(v >= 100 ? v.toFixed(0) : v.toFixed(2), { x: L - 8, y: Y(v) + 3.5, 'text-anchor': 'end', class: 'ax' })); }
    if (pts.length) svg.appendChild(el('path', { d: pts.map((q, i) => (i ? 'L' : 'M') + X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join(''), fill: 'none', stroke: 'var(--obs)', 'stroke-width': key === 'tempMonthly' ? 1 : 1.8 }));
    let dragHint = null;
    if (pts.length > 4) { dragHint = txt('← drag across the history to project a linear trend', { x: L + 8, y: T + 14, 'font-size': 11, fill: 'var(--accent)', 'font-weight': 600 }); svg.appendChild(dragHint); }

    const mono = key === 'tempMonthly';
    cs.forEach(c => {
      const col = priceColor(c.yes), cx = X(c.year), cy = Y(c.threshold);
      const m = mono ? el('path', { d: 'M' + cx + ' ' + (cy - 8) + ' L' + (cx - 8) + ' ' + (cy + 6) + ' L' + (cx + 8) + ' ' + (cy + 6) + ' Z', fill: col, stroke: 'var(--ink)', 'stroke-width': 1 })
                     : el('circle', { cx, cy, r: 8, fill: col, stroke: 'var(--ink)', 'stroke-width': 1 });
      m.onmousemove = e => tip.show(e, '<b>' + product.name + ' ' + c.label + '</b>settles ' + c.expiryLabel + '<br>Yes ' + Math.round(c.yes * 100) + '¢ — ' + WXM.LABEL);
      m.onmouseleave = () => tip.hide();
      svg.appendChild(m);
    });

    // the Climate-at-a-Glance trend tool: drag to fit, dashed extrapolation
    if (pts.length > 4) {
      let selRect = null, fitG = null, drag = null;
      const toYear = e => { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; const q = pt.matrixTransform(svg.getScreenCTM().inverse()); return x0 + (q.x - L) / (R - L) * (x1 - x0); };
      svg.addEventListener('mousedown', e => { drag = toYear(e); });
      svg.addEventListener('mousemove', e => {
        if (drag == null) return;
        const a = Math.min(drag, toYear(e)), b = Math.max(drag, toYear(e));
        if (!selRect) { selRect = el('rect', { y: T, height: B - T, fill: 'rgba(59,111,181,.12)' }); svg.appendChild(selRect); }
        selRect.setAttribute('x', X(a)); selRect.setAttribute('width', X(b) - X(a));
      });
      svg.addEventListener('mouseup', e => {
        if (drag == null) return;
        const a = Math.min(drag, toYear(e)), b = Math.max(drag, toYear(e)); drag = null;
        if (b - a < 1) { if (selRect) { selRect.remove(); selRect = null; } return; }
        const w = pts.filter(q => q[0] >= a && q[0] <= b); if (w.length < 3) return;
        const n = w.length, sx = w.reduce((s, q) => s + q[0], 0), sy = w.reduce((s, q) => s + q[1], 0);
        const sxx = w.reduce((s, q) => s + q[0] * q[0], 0), sxy = w.reduce((s, q) => s + q[0] * q[1], 0);
        const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx), icpt = (sy - slope * sx) / n;
        if (dragHint) { dragHint.remove(); dragHint = null; }
        if (fitG) fitG.remove(); fitG = el('g'); svg.appendChild(fitG);
        const f = v => slope * v + icpt;
        fitG.appendChild(el('line', { x1: X(a), y1: Y(f(a)), x2: X(b), y2: Y(f(b)), stroke: 'var(--accent)', 'stroke-width': 2.4 }));
        fitG.appendChild(el('line', { x1: X(b), y1: Y(f(b)), x2: X(x1), y2: Y(f(x1)), stroke: 'var(--accent)', 'stroke-width': 1.8, 'stroke-dasharray': '6 4', opacity: .85 }));
        const cross = thr.map(v => { const yr2 = (v - icpt) / slope; return (yr2 > b && yr2 < 2100 && slope !== 0) ? Math.round(yr2) : null; });
        note.style.display = 'inline-block';
        note.textContent = 'fit ' + Math.round(a) + '–' + Math.round(b) + ': ' + (slope * 10).toFixed(3) + ' per decade' + thr.map((v, i) => cross[i] ? (' · crosses ' + v + ' in ' + cross[i]) : '').join('');
      });
      svg.addEventListener('dblclick', () => { if (selRect) { selRect.remove(); selRect = null; } if (fitG) { fitG.remove(); fitG = null; } note.style.display = 'none'; });
    }
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('climate.json', 1440);
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440));
    const D = r.data;
    const host = $('#panels'); host.innerHTML = '';
    if (!D || !D.series) { host.appendChild(h('p', { class: 'cap', text: 'No data available.' })); return; }
    const off = D.offsetC || 0;
    const series = Object.assign({}, D.series);
    ['tempAnnual', 'tempMonthly'].forEach(k => { if (series[k]) series[k] = series[k].map(q => [q[0], Math.round((q[1] + off) * 1000) / 1000]); });
    const products = WXM.climateProducts(series, off);
    const byKey = {}; products.forEach(p => { byKey[p.seriesKey] = p; });
    PANELS.forEach(([k, title, unit]) => { if (series[k]) panel(host, k, title, unit, series[k], byKey[k], off); });
    const notes = Object.entries(D.notes || {}).map(([k, v]) => k + ': ' + v).join('; ');
    $('#foot').textContent = 'Series: NCEI Climate at a Glance global land+ocean anomalies (+' + off + ' °C to the preindustrial baseline, the convention the contracts use), NOAA GML Mauna Loa CO2, NOAA/NESDIS STAR sea level altimetry, and the RAPID AMOC monitoring project (UK NERC) annual means.' + (notes ? ' ' + notes + '.' : '') + (WXM.on() ? ' Markers are placeholders, not market values.' : '');
  }
  return { init };
})();
