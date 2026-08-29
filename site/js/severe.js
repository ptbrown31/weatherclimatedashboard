/* The SW severe-weather count panels.

   SWTUS, SWWUS and SWHUS settle on the month's national count of tornado,
   severe-wind and severe-hail storm reports in the SPC preliminary summary.
   Each listed expiration month gets one small chart: the climatological
   envelope of the day-of-month cumulative count (2005-2025, tenth to ninetieth
   percentile band with the median), the running month's own cumulative line,
   and the listed strikes as horizontal marks a reader can price the month
   against. The month-table total, the number the contract settles on, is
   printed in the chart's corner; the daily line is SPC's separate daily
   tabulation and the caption says so.

   Data in: snapshots/severe.json (the severe job) and the catalogue product
   doc for strikes and conids. Prices, where the book has any, come through
   the same priced map the ladders use. Nothing here computes a probability;
   the envelope is counting on public data. */
window.WXSevere = (() => {
  const { el, txt, h, $ } = WXC;
  const PRODUCTS = { SWTUS: 'torn', SWWUS: 'wind', SWHUS: 'hail' };
  const PH_NAME = { torn: 'tornado', wind: 'severe-wind', hail: 'severe-hail' };
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                  'August', 'September', 'October', 'November', 'December'];
  let tip = null;

  const daysIn = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();

  /* One month's chart: envelope, running line, strikes. */
  function monthChart(ph, y, m, sev, strikes, priced, prod) {
    const climo = ((sev.climo || {}).months || {})[String(m)];
    const env = climo && climo[ph] && climo[ph].env;
    if (!env) return null;
    const yearBlock = (sev.years || {})[String(y)] || {};
    const total = ((yearBlock.months || {})[String(m)] || {})[ph];
    const cum = ((yearBlock.daily || {})[String(m)] || {})[ph] || null;
    const nd = daysIn(y, m);

    const W = 320, H = 250, L = 44, R = 310, T = 26, B = 218;
    const hi = Math.max(env.p90[nd - 1] * 1.12, total || 0, ...(cum || [0]),
                        ...strikes.map(s => s.strike * 1.06), 1);
    const x = d => L + (d - 1) / (nd - 1) * (R - L);
    const yy = v => B - Math.min(v, hi) / hi * (B - T);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts' });

    // the envelope band and its median
    let dUp = '', dDn = '';
    for (let d = 1; d <= nd; d++) { dUp += (d > 1 ? 'L' : 'M') + x(d).toFixed(1) + ',' + yy(env.p90[d - 1]).toFixed(1); }
    for (let d = nd; d >= 1; d--) { dDn += 'L' + x(d).toFixed(1) + ',' + yy(env.p10[d - 1]).toFixed(1); }
    svg.appendChild(el('path', { d: dUp + dDn + 'Z', fill: 'var(--accent)', opacity: 0.12 }));
    svg.appendChild(el('path', { d: env.p50.slice(0, nd).map((v, i) => (i ? 'L' : 'M') + x(i + 1).toFixed(1) + ',' + yy(v).toFixed(1)).join(''),
                                 fill: 'none', stroke: 'var(--accent)', 'stroke-width': 1.2, 'stroke-dasharray': '4 3' }));

    // the strikes, each a horizontal mark. Every line is drawn; labels thin
    // when neighbouring strikes sit closer than a label is tall, and the
    // hover names each strike exactly
    let lastLab = null;
    strikes.sort((a, b) => b.strike - a.strike).forEach(c => {
      const sy = yy(c.strike);
      if (sy < T - 2) return;
      svg.appendChild(el('line', { x1: L, x2: R, y1: sy, y2: sy, stroke: 'var(--ink)', opacity: 0.28, 'stroke-dasharray': '2 3' }));
      const q = priced && priced[String(c.spec || '') + '|' + String(c.strike)];
      const yes = q && WXM.realMid(q) ? Math.round(q.mid * 100) + '¢' : null;
      const lab = c.label + (yes ? ' · ' + yes : '');
      if (lastLab == null || Math.abs(sy - lastLab) >= 10) {
        svg.appendChild(txt(lab, { x: R, y: sy - 3, 'text-anchor': 'end', 'font-size': 8.5, fill: 'var(--muted)' }));
        lastLab = sy;
      }
      if (prod && prod.productConid && c.conidYes != null && window.WXM && WXM.contractUrl) {
        const hit = el('rect', { x: L, y: sy - 8, width: R - L, height: 10, fill: 'transparent' });
        WXM.linkTo(hit, WXM.contractUrl(prod.productConid, c.conidYes), 'Open ' + c.label + ' on IBKR');
        svg.appendChild(hit);
      }
    });

    // the running month, a step line on the daily tabulation
    if (cum && cum.length) {
      const dline = cum.map((v, i) => (i ? 'L' : 'M') + x(i + 1).toFixed(1) + ',' + yy(v).toFixed(1)).join('');
      svg.appendChild(el('path', { d: dline, fill: 'none', stroke: 'var(--obs)', 'stroke-width': 2 }));
      const lastD = cum.length, lastV = cum[lastD - 1];
      svg.appendChild(el('circle', { cx: x(lastD), cy: yy(lastV), r: 3, fill: 'var(--obs)' }));
    }

    // the settlement number so far, top left, always the month table's value;
    // a month that has not started yet has nothing to print
    if (total != null && cum && cum.length) {
      svg.appendChild(txt(String(total) + ' reports' + (cum.length < nd ? ' so far' : ''),
                          { x: L, y: T - 8, 'font-size': 10.5, 'font-weight': 700, fill: 'var(--obs)' }));
    }
    // axis
    [0.5, 1].forEach(f => {
      const v = Math.round(hi * f);
      svg.appendChild(txt(String(v), { x: L - 4, y: yy(v) + 3, 'text-anchor': 'end', class: 'ax', 'font-size': 8.5 }));
      svg.appendChild(el('line', { x1: L, x2: R, y1: yy(v), y2: yy(v), class: 'grid' }));
    });
    [1, 10, 20, nd].forEach(d => svg.appendChild(txt(String(d), { x: x(d), y: B + 12, 'text-anchor': 'middle', class: 'ax', 'font-size': 8.5 })));

    const band = el('rect', { x: L, y: T, width: R - L, height: B - T, fill: 'transparent' });
    band.addEventListener('mousemove', ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const q2 = pt.matrixTransform(svg.getScreenCTM().inverse());
      const d = Math.max(1, Math.min(nd, Math.round(1 + (q2.x - L) / (R - L) * (nd - 1))));
      const rows = [['Day of month', String(d)]];
      if (cum && d <= cum.length) rows.push(['Count so far (daily tabulation)', String(cum[d - 1])]);
      rows.push(['Median through this day, 2005-2025', String(Math.round(env.p50[d - 1]))]);
      rows.push(['Envelope', Math.round(env.p10[d - 1]) + ' to ' + Math.round(env.p90[d - 1])]);
      if (!tip) tip = WXC.tooltip();
      tip.show(ev, tip.rows(MONTHS[m - 1] + ' ' + y + ' · ' + PH_NAME[ph] + ' reports', rows,
                            'the month table, not this daily line, is the settlement number'));
    });
    band.addEventListener('mouseleave', () => { if (tip) tip.hide(); });
    svg.appendChild(band);

    const wrap = h('div', { style: 'flex:1 1 300px;min-width:260px' });
    wrap.appendChild(h('div', { class: 'cap', style: 'font-weight:700;margin:0 0 2px', text: MONTHS[m - 1] + ' ' + y }));
    wrap.appendChild(svg);
    return wrap;
  }

  /* The whole panel for one product: one chart per listed expiration month,
     or the running month alone when nothing is listed. */
  function panel(host, prod, priced, pr, sev, note) {
    const ph = PRODUCTS[prod.id];
    if (!ph || !sev) return false;
    const div = h('div', { class: 'panel' });
    div.appendChild(h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: prod.name || prod.id }));
    if (note) div.appendChild(h('div', { class: 'psub cap', style: 'margin:2px 2px 6px', text: note }));

    // the listed months, from the contracts; the running month when none
    const byMonth = {};
    (prod.contracts || []).forEach(c => {
      const mm = /^(\d{4})\.(\d{1,2})$/.exec(String(c.spec || ''));
      if (mm) (byMonth[mm[1] + '-' + mm[2].padStart(2, '0')] = byMonth[mm[1] + '-' + mm[2].padStart(2, '0')] || []).push(c);
    });
    let keys = Object.keys(byMonth).sort();
    if (!keys.length) {
      const now = new Date(), key = now.getUTCFullYear() + '-' + String(now.getUTCMonth() + 1).padStart(2, '0');
      keys = [key]; byMonth[key] = [];
    }
    const row = h('div', { style: 'display:flex;gap:14px;flex-wrap:wrap' });
    let drawn = 0;
    keys.forEach(k => {
      const c = monthChart(ph, +k.slice(0, 4), +k.slice(5, 7), sev, byMonth[k], priced, prod);
      if (c) { row.appendChild(c); drawn++; }
    });
    if (!drawn) return false;
    div.appendChild(row);
    div.appendChild(h('p', { class: 'cap', style: 'margin:6px 2px 0',
      text: 'Shaded band and dashed line are the tenth-to-ninetieth percentile envelope and median of the '
        + 'day-of-month cumulative ' + PH_NAME[ph] + ' report count, '
        + ((sev.climo || {}).yearsFrom || '') + ' to ' + ((sev.climo || {}).yearsTo || '')
        + ', from the SPC preliminary summary. The solid line is this month’s daily tabulation and the '
        + 'corner figure is the month table, which is the count the contract settles on; the two are separate '
        + 'SPC products and can differ by a few percent. Dotted horizontals are the listed strikes'
        + (WXM.on() && WXM.live() ? ', with the Yes price midpoint where the book has bids; click one to open it on IBKR.' : '.') }));
    host.appendChild(div);
    return true;
  }

  return { PRODUCTS, panel };
})();
