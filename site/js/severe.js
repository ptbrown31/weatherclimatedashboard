/* The running-month block for the SW severe-weather count contracts.

   SWTUS, SWWUS and SWHUS settle on the month's national count of tornado,
   severe-wind and severe-hail storm reports in the SPC preliminary summary.
   Their long history is a series like any other and the series lane draws it;
   this module adds the month in progress, in the same shape as the hurricane
   season panels. On the left, the count so far as a big figure over the
   month's cumulative line, against the climatological median ("an average
   month") and a faint tenth-to-ninetieth envelope, with the pace the count
   implies. On the right, the market's ladder for the running month, Yes
   green against No red, with the average month and the count so far marked
   at their levels.

   Data in: snapshots/severe.json and the catalogue product doc. Prices come
   through the same priced map the ladders use, and a placeholder book shows
   no price (WXM.realMid). Nothing here computes a probability. */
window.WXSevere = (() => {
  const { el, txt, h, $ } = WXC;
  const PRODUCTS = { SWTUS: 'torn', SWWUS: 'wind', SWHUS: 'hail' };
  const PH_NAME = { torn: 'tornado', wind: 'severe-wind', hail: 'severe-hail' };
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                  'August', 'September', 'October', 'November', 'December'];
  let tip = null;

  const daysIn = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();
  const fmtN = v => (v >= 1000 ? (Math.round(v / 100) / 10).toLocaleString() + 'k' : String(Math.round(v)));

  /* The left chart: the month so far against an average month. */
  function monthChart(ph, y, m, sev) {
    const climo = ((sev.climo || {}).months || {})[String(m)];
    const env = climo && climo[ph] && climo[ph].env;
    if (!env) return null;
    const totals = climo[ph].totals;
    const yearBlock = (sev.years || {})[String(y)] || {};
    const total = ((yearBlock.months || {})[String(m)] || {})[ph];
    const cum = ((yearBlock.daily || {})[String(m)] || {})[ph] || null;
    const nd = daysIn(y, m);
    const day = cum ? cum.length : 0;

    const W = 620, H = 300, L = 46, R = 600, T = 34, B = 260;
    const hi = Math.max(env.p90[nd - 1], totals.p90, total || 0, 1) * 1.1;
    const x = d => L + (d - 1) / (nd - 1) * (R - L);
    const yy = v => B - Math.min(v, hi) / hi * (B - T);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts' });

    // envelope, faint, and the average month as a dashed curve
    let dUp = '', dDn = '';
    for (let d = 1; d <= nd; d++) dUp += (d > 1 ? 'L' : 'M') + x(d).toFixed(1) + ',' + yy(env.p90[d - 1]).toFixed(1);
    for (let d = nd; d >= 1; d--) dDn += 'L' + x(d).toFixed(1) + ',' + yy(env.p10[d - 1]).toFixed(1);
    svg.appendChild(el('path', { d: dUp + dDn + 'Z', fill: 'var(--accent)', opacity: 0.09, 'pointer-events': 'none' }));
    svg.appendChild(el('path', { d: env.p50.slice(0, nd).map((v, i) => (i ? 'L' : 'M') + x(i + 1).toFixed(1) + ',' + yy(v).toFixed(1)).join(''),
                                 fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.3, 'stroke-dasharray': '5 4', 'pointer-events': 'none' }));
    svg.appendChild(txt('an average month', { x: R - 4, y: yy(env.p50[nd - 1]) - 6, 'text-anchor': 'end',
                                              'font-size': 10, fill: 'var(--muted)' }));

    // today, then the month's own line over everything
    if (day > 0 && day < nd) {
      svg.appendChild(el('line', { x1: x(day), x2: x(day), y1: T, y2: B, stroke: 'var(--muted)',
                                   'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.7 }));
      svg.appendChild(txt('today', { x: x(day) + 4, y: T + 10, 'font-size': 9.5, fill: 'var(--muted)' }));
    }
    if (cum && cum.length) {
      svg.appendChild(el('path', { d: cum.map((v, i) => (i ? 'L' : 'M') + x(i + 1).toFixed(1) + ',' + yy(v).toFixed(1)).join(''),
                                   fill: 'none', stroke: 'var(--ink)', 'stroke-width': 2.2, 'pointer-events': 'none' }));
      svg.appendChild(el('circle', { cx: x(cum.length), cy: yy(cum[cum.length - 1]), r: 3.5, fill: 'var(--ink)' }));
    }

    // the big figure and the pace it implies, hurricane-panel style
    if (total != null && day > 0) {
      svg.appendChild(txt(String(total), { x: L + 10, y: T + 26, 'font-size': 30, 'font-weight': 700, fill: 'var(--accent)' }));
      svg.appendChild(txt('so far', { x: L + 12, y: T + 40, 'font-size': 10.5, fill: 'var(--muted)' }));
      const base = env.p50[Math.min(day, nd) - 1];
      if (base > 0 && day < nd) {
        const implied = total / base * totals.p50;
        svg.appendChild(txt('pace implied by today: ' + fmtN(implied) + ' (an average month ' + fmtN(totals.p50) + ')',
                            { x: L + 12, y: T + 54, 'font-size': 10, fill: 'var(--muted)' }));
      }
    }

    // axes
    const step = hi > 4000 ? 2000 : hi > 1500 ? 500 : hi > 400 ? 200 : hi > 150 ? 50 : hi > 40 ? 20 : 10;
    for (let v = step; v <= hi; v += step) {
      svg.appendChild(txt(fmtN(v), { x: L - 4, y: yy(v) + 3, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
      svg.appendChild(el('line', { x1: L, x2: R, y1: yy(v), y2: yy(v), class: 'grid' }));
    }
    [1, 10, 20, nd].forEach(d => svg.appendChild(txt(String(d), { x: x(d), y: B + 13, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 })));
    svg.appendChild(txt('day of the month', { x: (L + R) / 2, y: H - 4, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 }));

    svg.addEventListener('mousemove', ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const q2 = pt.matrixTransform(svg.getScreenCTM().inverse());
      if (q2.x < L || q2.x > R || q2.y < T || q2.y > B) { if (tip) tip.hide(); return; }
      const d = Math.max(1, Math.min(nd, Math.round(1 + (q2.x - L) / (R - L) * (nd - 1))));
      const rows = [['Day of month', String(d)]];
      if (cum && d <= cum.length) rows.push(['Count so far (daily tabulation)', String(cum[d - 1])]);
      rows.push(['Median through this day, 2005-2025', String(Math.round(env.p50[d - 1]))]);
      rows.push(['Envelope', Math.round(env.p10[d - 1]) + ' to ' + Math.round(env.p90[d - 1])]);
      if (!tip) tip = WXC.tooltip();
      tip.show(ev, tip.rows(MONTHS[m - 1] + ' ' + y + ' · ' + PH_NAME[ph] + ' reports', rows,
                            'the month table, not this daily line, is the settlement number'));
    });
    svg.addEventListener('mouseleave', () => { if (tip) tip.hide(); });
    return svg;
  }

  /* The right side: the market's ladder for the running month. */
  function ladderChart(ph, y, m, strikes, priced, prod, sev) {
    const climo = ((sev.climo || {}).months || {})[String(m)] || {};
    const totals = climo[ph] && climo[ph].totals;
    const total = (((sev.years || {})[String(y)] || {}).months || {})[String(m)] || {};
    const C = total[ph];
    const rows = strikes.slice().sort((a, b) => b.strike - a.strike);
    if (!rows.length) return null;

    const W = 420, T = 26, rowH = 26, LX = 8, RX = 386;
    const H = T + rows.length * rowH + 34;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts' });
    const px = q => LX + Math.max(0, Math.min(1, q)) * (RX - LX);
    svg.appendChild(txt('The market’s ladder', { x: LX, y: 13, 'font-size': 11.5, 'font-weight': 700, fill: 'var(--navy)' }));

    rows.forEach((c, i) => {
      const yTop = T + i * rowH, bh = rowH - 8, by = yTop + 3;
      const q = priced && priced[String(c.spec || '') + '|' + String(c.strike)];
      const real = q && WXM.realMid(q);
      if (real) {
        const split = px(q.mid);
        svg.appendChild(el('rect', { x: LX, y: by, width: Math.max(split - LX, 1), height: bh, rx: 2, fill: 'var(--yes)', opacity: 0.85 }));
        svg.appendChild(el('rect', { x: split, y: by, width: Math.max(RX - split, 1), height: bh, rx: 2, fill: 'var(--no)', opacity: 0.85 }));
        const yesC = Math.round(q.mid * 100), noC = 100 - yesC;
        svg.appendChild(txt(yesC + '¢', { x: Math.max(split - 4, LX + 22), y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                          'font-size': 10, 'font-weight': 700, fill: '#fff' }));
        svg.appendChild(txt(noC + '¢', { x: RX - 4, y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                         'font-size': 10, 'font-weight': 700, fill: '#fff' }));
      } else {
        svg.appendChild(el('rect', { x: LX, y: by, width: RX - LX, height: bh, rx: 2, fill: 'none',
                                     stroke: 'var(--rule)', 'stroke-dasharray': '3 3' }));
        svg.appendChild(txt('no price', { x: RX - 4, y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                          'font-size': 9.5, fill: 'var(--muted)' }));
      }
      svg.appendChild(txt(c.label, { x: LX + 4, y: by + bh / 2 + 3.5, 'font-size': 10,
                                     'font-weight': 700, fill: real ? '#fff' : 'var(--ink)' }));
      const hit = el('rect', { x: LX, y: yTop, width: RX - LX, height: rowH, fill: 'transparent',
                               cursor: prod && prod.productConid && c.conidYes != null ? 'pointer' : null });
      if (prod && prod.productConid && c.conidYes != null && window.WXM && WXM.contractUrl) {
        WXM.linkTo(hit, WXM.contractUrl(prod.productConid, c.conidYes), 'Open ' + c.label + ' on IBKR');
      }
      svg.appendChild(hit);
    });

    /* Level markers between the rows they bracket, the hurricane convention:
       the average month dashed, the count so far solid. */
    const yOf = v => {
      let i = rows.findIndex(c => v > c.strike);      // first row the level sits above
      if (i < 0) i = rows.length;
      return T + i * rowH - 1.5;
    };
    /* Two markers often share a gap, the count having passed every strike
       being the common case, so markers at one level share one line and lay
       their chips side by side instead of on top of each other. */
    const marks = [];
    if (C != null && C > 0) marks.push({ v: C, label: 'so far', dash: null });
    if (totals) marks.push({ v: totals.p50, label: 'an average month', dash: '5 4' });
    const groups = {};
    marks.forEach(mk => { (groups[yOf(mk.v)] = groups[yOf(mk.v)] || []).push(mk); });
    Object.entries(groups).forEach(([mys, g]) => {
      const my = +mys;
      const attrs = { x1: LX, x2: RX, y1: my, y2: my, stroke: 'var(--ink)', 'stroke-width': 1.2, opacity: 0.85 };
      if (g.every(mk => mk.dash)) attrs['stroke-dasharray'] = g[0].dash;
      svg.appendChild(el('line', attrs));
      let cx = LX + 12;
      g.forEach(mk => {
        const t2 = mk.label + ' ' + fmtN(mk.v);
        const tw = t2.length * 5.6 + 10;
        svg.appendChild(el('rect', { x: cx, y: my - 7, width: tw, height: 14, rx: 7, fill: 'var(--panel)', opacity: 0.95 }));
        svg.appendChild(txt(t2, { x: cx + 5, y: my + 3.5, 'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)' }));
        cx += tw + 6;
      });
    });

    [0, 25, 50, 75, 100].forEach(cc => {
      svg.appendChild(txt(String(cc), { x: px(cc / 100), y: T + rows.length * rowH + 14, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 }));
    });
    svg.appendChild(txt('Yes green, No red · ¢', { x: (LX + RX) / 2, y: H - 4, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 }));
    return svg;
  }

  /* The block: the running month's chart and ladder side by side. */
  function monthBlock(host, prod, priced, sev, note) {
    const ph = PRODUCTS[prod.id];
    if (!ph || !sev) return false;
    const now = new Date();
    const y = now.getUTCFullYear(), m = now.getUTCMonth() + 1;
    const chart = monthChart(ph, y, m, sev);
    if (!chart) return false;

    const strikes = (prod.contracts || []).filter(c => {
      const mm = /^(\d{4})\.(\d{1,2})$/.exec(String(c.spec || ''));
      return mm && +mm[1] === y && +mm[2] === m;
    });
    const div = h('div', { class: 'panel' });
    div.appendChild(h('div', { class: 'cap', style: 'font-weight:700;margin:0 0 4px',
                               text: MONTHS[m - 1] + ' ' + y + ' in progress' }));
    if (note) div.appendChild(h('div', { class: 'psub cap', style: 'margin:2px 2px 6px', text: note }));
    const row = h('div', { style: 'display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start' });
    row.appendChild(h('div', { style: 'flex:3 1 460px;min-width:320px' }, [chart]));
    const lad = ladderChart(ph, y, m, strikes, priced, prod, sev);
    if (lad) row.appendChild(h('div', { style: 'flex:2 1 300px;min-width:260px' }, [lad]));
    else row.appendChild(h('div', { style: 'flex:2 1 300px;min-width:260px' }, [
      h('p', { class: 'cap', text: 'No ladder is listed for the running month.' })]));
    div.appendChild(row);
    if (lad && WXM.on() && WXM.live()) {
      div.appendChild(h('p', { class: 'cap', style: 'margin:4px 2px 0' }, [
        h('a', { href: 'allocator.html?m=' + prod.id, text: 'Size a position on this ladder in the position allocation calculator →' })]));
    }
    div.appendChild(h('p', { class: 'cap', style: 'margin:6px 2px 0',
      text: 'The dashed curve and the faint band are the median and tenth-to-ninetieth percentile envelope of the '
        + 'day-of-month cumulative ' + PH_NAME[ph] + ' report count, '
        + ((sev.climo || {}).yearsFrom || '') + ' to ' + ((sev.climo || {}).yearsTo || '')
        + ', from the SPC preliminary summary. The solid line is this month’s daily tabulation and the large figure '
        + 'is the month table, which is the count the contract settles on; the two are separate SPC products and '
        + 'can differ by a few percent.'
        + (sev.staleSince ? ' The feed has not updated since ' + sev.staleSince.slice(0, 10) + '.' : '') }));
    host.appendChild(div);
    return true;
  }

  return { PRODUCTS, monthBlock };
})();
