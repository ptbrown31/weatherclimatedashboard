/* Figure 3: calibration and the Brier score of ForecastEx strike prices.

   A strike contract is a probability the exchange states in cents: a Yes
   price of 30 c says the day's high clears the strike three times in ten.
   This figure tests whether those prices are honest (reliability, the share
   that paid against the price), how much they say (resolution and the
   sharpness of the ladder), and what they score (the Brier score and its
   Murphy decomposition), by hours before the target day ends.

   Everything drawn here comes from calibration.json as the builder ships it
   under docs/accuracy.md; the module bins nothing and scores nothing. The
   eleven forecast tools publish no probabilities, so there is no tool line,
   and the site computes no probability of its own.

   Three stacked SVGs in the card: a row of four reliability panels (one per
   lead bin), the decomposition bars by lead, and the sharpness line with
   the error of the market's median on a second axis. draw(D) takes the
   bundle WXAcc.init assembles and reads D.cal. */
window.WXAccCal = (() => {
  const { el, txt, h, $ } = WXC;
  const A = WXAcc;

  // the three price rules of the file, in the reader's words
  const PRICE = [
    { key: 'mid', label: 'Yes price midpoint', title: 'the midpoint of the Yes bid and one dollar less the No bid; the single quoted side when only one side is bid' },
    { key: 'twoSided', label: 'Two-sided books only', title: 'the midpoint on books with a bid on both sides; a one-sided book is unquoted' },
    { key: 'yesBid', label: 'Yes bid', title: 'the Yes bid alone' },
  ];
  /* The truncated score. The builder's truncated Brier keeps prices strictly
     between 2 and 98 cents, the band the calibration working paper uses, and
     `retained` is the share of contracts inside it. Contracts at 1 c or 99 c
     are nearly free points for a score, so the truncated score is the harder
     test of the prices that carry information. */
  const TRUNC = [0.02, 0.98];
  const TRUNC_LABEL = '2 to 98 c';
  const RANGE = [
    { key: 'all', label: 'All', title: 'every priced contract' },
    { key: 'trunc', label: TRUNC_LABEL, title: 'prices strictly between 2 and 98 cents, the truncated score' },
  ];
  // series colors: the market keeps the accent; the Murphy terms take the
  // site's penalty, credit and neutral tokens; the median's error the navy
  const C_BS = 'var(--accent)', C_TRUNC = 'var(--navy)', C_REL = 'var(--bad)', C_RES = 'var(--ok)',
        C_UNC = 'var(--muted)', C_WIDTH = 'var(--accent)', C_MAE = 'var(--navy)';
  const fin = v => v != null && isFinite(v);
  const cents = v => (fin(v) ? Math.round(v * 100) + ' c' : A.dash);

  const st = { metric: 'high', price: 'mid', range: 'all' };
  let cal = null;

  function draw(D) {
    const host = $('#accCal'), bar = $('#accCalBar'), keyEl = $('#accCalKey'), meth = $('#accCalMethod');
    if (!host) return;
    if (bar) bar.innerHTML = '';
    if (keyEl) keyEl.innerHTML = '';
    if (meth) meth.innerHTML = '';
    cal = D && D.cal;
    if (!cal || !cal.metric) { A.notYet(host, A.NOT_PUBLISHED); return; }
    if (bar) {
      A.metricTabs(bar, k => { st.metric = k; render(host, keyEl, meth); }, st.metric);
      A.tabs(bar, PRICE, k => { st.price = k; render(host, keyEl, meth); }, { initial: st.price, label: 'Price' });
      A.tabs(bar, RANGE, k => { st.range = k; render(host, keyEl, meth); }, { initial: st.range, label: 'Range' });
    }
    render(host, keyEl, meth);
  }

  function render(host, keyEl, meth) {
    host.innerHTML = '';
    const m = cal.metric[st.metric];
    const block = m && m.price && m.price[st.price];
    if (!block || !block.reliability) {
      A.notYet(host, 'The file carries no ' + (st.metric === 'high' ? 'highs' : 'lows') + ' block under this price rule.');
      return;
    }
    const edges = (cal.bins && cal.bins.edges) || [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
    host.appendChild(h('div', { class: 'accsub', text: 'Reliability by lead bin, ' + describe() }));
    host.appendChild(reliability(block, edges));
    host.appendChild(h('div', { class: 'accsub', text: 'Brier score and its decomposition by lead' }));
    host.appendChild(decomposition(block));
    host.appendChild(h('div', { class: 'accsub', text: 'Sharpness of the ladder and the error of its median' }));
    host.appendChild(sharpness(block));
    host.appendChild(h('p', { class: 'cap acc-cal-note',
      text: 'The eleven forecast tools publish no probabilities, so this figure has no tool line, and the site computes none of its own.' }));
    if (keyEl) legend(keyEl);
    if (meth) method(meth, block);
  }

  // "highs, Yes price midpoint": the rule's label keeps its capital Yes
  const describe = () => (st.metric === 'high' ? 'highs' : 'lows') + ', ' + PRICE.find(p => p.key === st.price).label;

  // ------------------------------------------------------- reliability row
  /* Four panels, one per lead bin, share paid against price. The marker
     area is the contract count, so a bin of 10,000 tail contracts and a
     bin of 500 central ones are seen for what they are; the bar is the
     Wilson interval on the share paid; the inset is the price histogram,
     the raw share of contracts in each decile before any pooling. */
  function reliability(block, edges) {
    const bins = block.leadBins || [];
    const rel = block.reliability || [];
    const n = Math.min(bins.length, rel.length) || 1;
    const H = 292, TOP = 52, PW = 200, GAP = (960 - 46 - 16 - PW * n) / Math.max(n - 1, 1);
    const svg = el('svg', { viewBox: '0 0 960 ' + H, class: 'acc-cal-rel' });
    const maxCount = Math.max(1, ...rel.map(r => Math.max(0, ...(r.count || []).filter(fin))));
    const maxHist = Math.max(0.01, ...rel.map(r => Math.max(0, ...(r.hist || []).filter(fin))));
    for (let i = 0; i < n; i++) {
      const r = rel[i] || {};
      const g = { L: 46 + i * (PW + GAP), T: TOP, W: PW, H: PW };
      g.R = g.L + PW; g.B = g.T + PW;
      const x = A.scale(0, 1, g.L, g.R), y = A.scale(0, 1, g.B, g.T);
      // frame and the identity line, the line a calibrated price sits on
      svg.appendChild(el('rect', { x: g.L, y: g.T, width: PW, height: PW, fill: 'none', stroke: 'var(--line)' }));
      [0.25, 0.5, 0.75].forEach(v => {
        svg.appendChild(el('line', { x1: x(v), x2: x(v), y1: g.T, y2: g.B, class: 'grid' }));
        svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: y(v), y2: y(v), class: 'grid' }));
      });
      svg.appendChild(el('line', { x1: g.L, y1: g.B, x2: g.R, y2: g.T, stroke: 'var(--rule)', 'stroke-width': 1,
                                   'stroke-dasharray': '4 3', fill: 'none', 'pointer-events': 'none' }));
      [0, 0.5, 1].forEach(v => {
        svg.appendChild(txt(String(Math.round(v * 100)), { x: x(v), y: g.B + 16, 'text-anchor': 'middle', class: 'ax' }));
        if (i === 0) svg.appendChild(txt(String(Math.round(v * 100)), { x: g.L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
      });
      // the inset histogram, lower right, where a reliability curve has no business being
      const hist = r.hist || [];
      const IW = 80, IH = 40, ix0 = g.R - IW - 8, iy1 = g.B - 10;
      svg.appendChild(txt('share of contracts by price', { x: ix0 + IW / 2, y: iy1 - IH - 5, 'text-anchor': 'middle',
                                                           'font-size': 8, fill: 'var(--muted)' }));
      svg.appendChild(el('line', { x1: ix0, x2: ix0 + IW, y1: iy1 + 0.5, y2: iy1 + 0.5, stroke: 'var(--rule)', 'stroke-width': 1 }));
      hist.forEach((s, k) => {
        if (!fin(s)) return;
        const bh = IH * s / maxHist, faded = st.range === 'trunc' && tail(k, edges);
        svg.appendChild(el('rect', { x: ix0 + k * (IW / hist.length) + 0.5, y: iy1 - bh, width: IW / hist.length - 1, height: bh,
                                     fill: 'var(--muted)', 'fill-opacity': faded ? 0.18 : 0.5, stroke: 'none' }));
      });
      // Wilson bars, then the markers over them
      const pts = [];
      (r.x || []).forEach((px, k) => {
        const py = r.y && r.y[k];
        if (!fin(px) || !fin(py)) return;
        const faded = st.range === 'trunc' && tail(k, edges);
        const op = faded ? 0.25 : 1;
        if (fin(r.lo && r.lo[k]) && fin(r.hi && r.hi[k])) {
          svg.appendChild(el('line', { x1: x(px), x2: x(px), y1: y(r.lo[k]), y2: y(r.hi[k]), stroke: C_BS, 'stroke-width': 1.4,
                                       'stroke-opacity': op, 'pointer-events': 'none' }));
        }
        const cnt = r.count && r.count[k];
        const rad = 2.2 + 7 * Math.sqrt((fin(cnt) ? cnt : 0) / maxCount);
        svg.appendChild(el('circle', { cx: x(px), cy: y(py), r: rad, fill: C_BS, 'fill-opacity': 0.45 * op, stroke: C_BS,
                                       'stroke-width': 1.2, 'stroke-opacity': op, 'pointer-events': 'none' }));
        pts.push({ k, px, py, rad });
      });
      // hit targets last so they sit over the drawing
      pts.forEach(p => {
        const hit = el('circle', { cx: x(p.px), cy: y(p.py), r: Math.max(p.rad + 3, 9), fill: 'none', 'pointer-events': 'all' });
        A.hover(hit, () => binTip(r, p.k, edges, bins[i]));
        svg.appendChild(hit);
      });
      // the title: the lead bin, the sample, the two scores
      const lead = bins[i] ? bins[i][0] + ' to ' + bins[i][1] + ' h before the day ends' : 'lead bin ' + (i + 1);
      svg.appendChild(txt(lead, { x: g.L, y: 13, 'font-size': 11, 'font-weight': 700, fill: 'var(--ink)' }));
      svg.appendChild(txt(A.int(r.n) + ' contracts, ' + A.int(r.nCityDays) + ' city-days',
                          { x: g.L, y: 27, class: 'ax' }));
      const t3 = txt('', { x: g.L, y: 41, class: 'ax' });
      const s1 = el('tspan', { 'font-weight': st.range === 'all' ? 700 : 400, fill: st.range === 'all' ? 'var(--ink)' : null });
      s1.textContent = 'BS ' + A.f3(r.brier);
      const s2 = el('tspan', { 'font-weight': st.range === 'trunc' ? 700 : 400, fill: st.range === 'trunc' ? 'var(--ink)' : null });
      s2.textContent = TRUNC_LABEL + ' ' + A.f3(r.brierTrunc) + ' (' + A.pct(r.retained) + ' kept)';
      t3.appendChild(s1); t3.appendChild(document.createTextNode(', ')); t3.appendChild(s2);
      svg.appendChild(t3);
      if (!pts.length) svg.appendChild(txt('no priced bin', { x: (g.L + g.R) / 2, y: (g.T + g.B) / 2, 'text-anchor': 'middle', class: 'axl' }));
    }
    svg.appendChild(txt('Yes price, cents', { x: 480, y: H - 6, 'text-anchor': 'middle', class: 'ax' }));
    svg.appendChild(txt('Share that paid, percent', { x: 14, y: TOP + PW / 2, 'text-anchor': 'middle',
                                                      transform: 'rotate(-90 14 ' + (TOP + PW / 2) + ')', class: 'ax' }));
    return svg;
  }
  // a decile lies outside the truncated range when it starts below the low bound or ends above the high one
  const tail = (k, edges) => edges[k] < TRUNC[0] - 1e-9 || edges[k + 1] > TRUNC[1] + 1e-9;

  function binTip(r, k, edges, bin) {
    const T = A.tooltip();
    const raw = fin(r.hist && r.hist[k]) && fin(r.n) ? r.hist[k] * r.n : null;
    const cnt = r.count && r.count[k];
    const pooled = fin(raw) && fin(cnt) && cnt > raw * 1.02 + 1;
    const rows = [
      ['Mean price', cents(r.x[k])],
      ['Share that paid', A.pct1(r.y[k])],
      ['95% Wilson interval', A.iv(r.lo && r.lo[k], r.hi && r.hi[k], A.pct1)],
      ['Contracts' + (pooled ? ' (pooled)' : ''), A.int(cnt)],
      ['Share of all contracts', A.pct1(r.hist && r.hist[k])],
      ['Lead bin', bin ? bin[0] + ' to ' + bin[1] + ' h' : A.dash],
    ];
    return T.rows('Prices ' + Math.round(edges[k] * 100) + ' to ' + Math.round(edges[k + 1] * 100) + ' c', rows,
                  pooled ? 'This decile carries the contracts of a thinner neighbor pooled toward 50 c.' : '');
  }

  // ------------------------------------------------------- decomposition
  /* The Brier score and its Murphy terms by lead. A bar each for
     reliability (penalty), resolution (credit) and uncertainty (the score
     of the base rate), the net score as a marker with its bootstrap
     whisker, and the truncated score as a second marker with the retained
     share printed over it. The 0.25 rule is the score of pricing every
     contract at 50 c; the base rate rule is the score of pricing every
     contract at that lead's share paid, which is the uncertainty term. */
  function decomposition(block) {
    const by = block.byLead || {};
    const hs = by.h || [];
    const H = 300, g = A.frame(H, { T: 26, B: H - 56 });
    const svg = el('svg', { viewBox: '0 0 960 ' + H, class: 'acc-cal-bars' });
    const nG = hs.length || 1, gw = (g.R - g.L) / nG;
    const cx = i => g.L + (i + 0.5) * gw;
    const vals = [0.25].concat(['rel', 'res', 'unc', 'hi', 'brierTrunc'].flatMap(k => (by[k] || []).filter(fin)));
    const ymax = Math.max(...vals) * 1.12;
    const step = A.niceStep(ymax, 5);
    const y = A.scale(0, Math.ceil(ymax / step) * step, g.B, g.T);
    A.yAxis(svg, g, y, A.ticks(0, y.invert(g.T), step), A.f2, 'Brier score');
    hs.forEach((hh, i) => svg.appendChild(txt(hh + ' h', { x: cx(i), y: g.B + 17, 'text-anchor': 'middle', class: 'ax' })));
    svg.appendChild(txt('Hours before the end of the target day', { x: (g.L + g.R) / 2, y: g.B + 38, 'text-anchor': 'middle', class: 'ax' }));
    svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: g.B, y2: g.B, stroke: 'var(--rule)', 'stroke-width': 1 }));
    // the day boundary, between the last lead of the day before and the first of the day
    const k24 = hs.findIndex(v => v <= 24);
    if (k24 > 0) {
      const xb = g.L + k24 * gw;
      svg.appendChild(el('line', { x1: xb, x2: xb, y1: g.T, y2: g.B, class: 'grid', 'stroke-dasharray': '4 4' }));
      svg.appendChild(txt('the target day begins', { x: xb + 5, y: g.T + 10, class: 'ax' }));
    }
    // the 50 c rule
    if (y.invert(g.T) >= 0.25) {
      svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: y(0.25), y2: y(0.25), stroke: 'var(--rule)', 'stroke-width': 1,
                                   'stroke-dasharray': '6 4', fill: 'none' }));
      svg.appendChild(txt('every contract at 50 c scores 0.25', { x: g.L + 4, y: y(0.25) - 5, class: 'ax' }));
    }
    const BW = 14;
    hs.forEach((hh, i) => {
      const c = cx(i);
      const rel = by.rel && by.rel[i], res = by.res && by.res[i], unc = by.unc && by.unc[i];
      const bs = by.brier && by.brier[i], lo = by.lo && by.lo[i], hi = by.hi && by.hi[i];
      const bt = by.brierTrunc && by.brierTrunc[i], kept = by.retained && by.retained[i], base = by.base && by.base[i];
      const bar = (v, dx, fill) => {
        if (!fin(v)) return;
        svg.appendChild(el('rect', { x: c + dx, y: y(v), width: BW, height: Math.max(0, g.B - y(v)), fill, 'fill-opacity': 0.8,
                                     stroke: 'none', 'pointer-events': 'none' }));
      };
      bar(rel, -44, C_REL); bar(res, -27, C_RES); bar(unc, -10, C_UNC);
      // the base rate rule across the group
      if (fin(base)) {
        const v = base * (1 - base);
        svg.appendChild(el('line', { x1: c - 48, x2: c + 48, y1: y(v), y2: y(v), stroke: C_TRUNC, 'stroke-width': 1,
                                     'stroke-dasharray': '3 3', fill: 'none', 'pointer-events': 'none' }));
        if (i === hs.length - 1) svg.appendChild(txt('base rate', { x: c + 48, y: y(v) + 11, 'text-anchor': 'end', 'font-size': 9, fill: C_TRUNC }));
      }
      const lead = st.range === 'all';
      // the net score with its whisker
      if (fin(bs)) {
        const xm = c + 16;
        if (fin(lo) && fin(hi)) {
          svg.appendChild(el('line', { x1: xm, x2: xm, y1: y(lo), y2: y(hi), stroke: C_BS, 'stroke-width': 1.4, 'pointer-events': 'none' }));
          [lo, hi].forEach(v => svg.appendChild(el('line', { x1: xm - 3, x2: xm + 3, y1: y(v), y2: y(v), stroke: C_BS, 'stroke-width': 1.2 })));
        }
        svg.appendChild(el('circle', { cx: xm, cy: y(bs), r: 4.2, fill: lead ? C_BS : 'var(--panel)', stroke: C_BS, 'stroke-width': 1.6,
                                       'pointer-events': 'none' }));
      }
      // the truncated score, a diamond, with the retained share above it
      if (fin(bt)) {
        const xt = c + 36, yt = y(bt), d = 4.6;
        svg.appendChild(el('path', { d: 'M' + xt + ',' + (yt - d) + 'L' + (xt + d) + ',' + yt + 'L' + xt + ',' + (yt + d) + 'L' + (xt - d) + ',' + yt + 'Z',
                                     fill: lead ? 'var(--panel)' : C_TRUNC, stroke: C_TRUNC, 'stroke-width': 1.5, 'pointer-events': 'none' }));
        svg.appendChild(txt(A.pct(kept), { x: xt, y: yt - 8, 'text-anchor': 'middle', 'font-size': 9, fill: C_TRUNC }));
      }
      if (!fin(bs) && !fin(bt)) {
        svg.appendChild(txt('under 30', { x: c, y: (g.T + g.B) / 2 - 6, 'text-anchor': 'middle', class: 'ax' }));
        svg.appendChild(txt('city-days', { x: c, y: (g.T + g.B) / 2 + 7, 'text-anchor': 'middle', class: 'ax' }));
      }
      const hit = el('rect', { x: g.L + i * gw, y: g.T, width: gw, height: g.B - g.T, fill: 'none', 'pointer-events': 'all' });
      A.hover(hit, () => leadTip(block, i));
      svg.appendChild(hit);
    });
    return svg;
  }

  function leadTip(block, i) {
    const T = A.tooltip(), by = block.byLead || {};
    const v = k => (by[k] ? by[k][i] : null);
    const mv = block.movedPerHour || {};
    const mi = (mv.h || []).indexOf(by.h[i]);
    const base = v('base');
    const rows = [
      ['Contracts', A.int(v('n'))],
      ['Brier score', A.f3(v('brier'))],
      ['Brier on the ten bins', A.f3(v('brierBinned'))],
      ['95% bootstrap interval', A.iv(v('lo'), v('hi'), A.f3)],
      ['Reliability', A.f3(v('rel'))],
      ['Resolution', A.f3(v('res'))],
      ['Uncertainty', A.f3(v('unc'))],
      ['Skill against the base rate', A.f3(v('bss'))],
      ['Base rate, share paid', A.pct1(base)],
      ['Its score', fin(base) ? A.f3(base * (1 - base)) : A.dash],
      ['Brier, ' + TRUNC_LABEL, A.f3(v('brierTrunc'))],
      ['Share of contracts kept', A.pct(v('retained'))],
      ['Strikes moved per hour', mi >= 0 ? A.f1(mv.contracts[mi]) : A.dash],
    ];
    const foot = fin(v('brier')) ? '' : 'Under 30 city-days at this lead, so the scores are not drawn.';
    return T.rows(by.h[i] + ' h before the day ends, ' + describe(), rows, foot);
  }

  // ------------------------------------------------------- sharpness
  /* How tight the ladder is, in degrees, by lead: the median over ladders
     of the strike where the monotone ladder crosses 90 c less the strike
     where it crosses 10 c. A ladder can be sharp and wrong, so the error of
     its median (the whole-degree crossing against the settle) rides on a
     second axis; a market that narrows before its error falls is
     overconfident, one that narrows after is slow. */
  function sharpness(block) {
    const sh = block.sharpness || {};
    const hs = sh.h || [];
    const H = 236, g = A.frame(H, { T: 24, B: H - 56, R: 898 });
    const svg = el('svg', { viewBox: '0 0 960 ' + H, class: 'acc-cal-sharp' });
    const nG = hs.length || 1, gw = (g.R - g.L) / nG;
    const cx = i => g.L + (i + 0.5) * gw;
    const wmax = Math.max(1, ...(sh.width || []).filter(fin)) * 1.15;
    const mmax = Math.max(0.5, ...(sh.maeMedian || []).filter(fin)) * 1.15;
    const ws = A.niceStep(wmax, 4), ms = A.niceStep(mmax, 3);
    const yw = A.scale(0, Math.ceil(wmax / ws) * ws, g.B, g.T);
    const ym = A.scale(0, Math.ceil(mmax / ms) * ms, g.B, g.T);
    A.yAxis(svg, g, yw, A.ticks(0, yw.invert(g.T), ws), A.deg1, 'Ladder width, °F');
    A.ticks(0, ym.invert(g.T), ms).forEach(v => svg.appendChild(txt(A.deg1(v), { x: g.R + 8, y: ym(v) + 3.5, class: 'ax', style: 'fill:' + C_MAE })));
    svg.appendChild(txt('Error of the median, °F', { x: 948, y: (g.T + g.B) / 2, 'text-anchor': 'middle', class: 'ax', style: 'fill:' + C_MAE,
                                                      transform: 'rotate(90 948 ' + (g.T + g.B) / 2 + ')' }));
    hs.forEach((hh, i) => svg.appendChild(txt(hh + ' h', { x: cx(i), y: g.B + 17, 'text-anchor': 'middle', class: 'ax' })));
    svg.appendChild(txt('Hours before the end of the target day', { x: (g.L + g.R) / 2, y: g.B + 38, 'text-anchor': 'middle', class: 'ax' }));
    svg.appendChild(el('line', { x1: g.L, x2: g.R, y1: g.B, y2: g.B, stroke: 'var(--rule)', 'stroke-width': 1 }));
    const k24 = hs.findIndex(v => v <= 24);
    if (k24 > 0) {
      const xb = g.L + k24 * gw;
      svg.appendChild(el('line', { x1: xb, x2: xb, y1: g.T, y2: g.B, class: 'grid', 'stroke-dasharray': '4 4' }));
      svg.appendChild(txt('the target day begins', { x: xb + 5, y: g.T + 10, class: 'ax' }));
    }
    const xs = hs.map((_, i) => cx(i));
    const wy = (sh.width || []).map(v => (fin(v) ? yw(v) : null));
    const my = (sh.maeMedian || []).map(v => (fin(v) ? ym(v) : null));
    A.lineSeries(svg, xs, wy, { stroke: C_WIDTH, 'stroke-width': 2.4 });
    A.dots(svg, xs, wy, { fill: C_WIDTH, stroke: 'var(--panel)', 'stroke-width': 1, r: 3.4 });
    A.lineSeries(svg, xs, my, { stroke: C_MAE, 'stroke-width': 1.8, 'stroke-dasharray': '5 4' });
    A.dots(svg, xs, my, { fill: 'var(--panel)', stroke: C_MAE, 'stroke-width': 1.6, r: 3 });
    // series names at the first drawn point of each
    const first = arr => arr.findIndex(fin);
    const fw = first(wy), fm = first(my);
    if (fw >= 0) A.label(svg, xs[fw] + 6, wy[fw] - 8, 'ladder width', C_WIDTH);
    if (fm >= 0) A.label(svg, xs[fm] + 6, my[fm] + (fw === fm && Math.abs(my[fm] - wy[fm]) < 18 ? 16 : -8), 'error of the median', C_MAE);
    if (fw < 0 && fm < 0) svg.appendChild(txt('under 30 ladders at every lead', { x: (g.L + g.R) / 2, y: (g.T + g.B) / 2, 'text-anchor': 'middle', class: 'axl' }));
    hs.forEach((hh, i) => {
      const hit = el('rect', { x: g.L + i * gw, y: g.T, width: gw, height: g.B - g.T, fill: 'none', 'pointer-events': 'all' });
      A.hover(hit, () => {
        const T = A.tooltip();
        return T.rows(hh + ' h before the day ends, ' + describe(), [
          ['Ladder width, 10 c to 90 c', A.deg1(sh.width && sh.width[i])],
          ['Error of the median', A.deg1(sh.maeMedian && sh.maeMedian[i])],
          ['Ladders with both crossings', A.int(sh.n && sh.n[i])],
        ], fin(sh.width && sh.width[i]) ? '' : 'Under 30 ladders at this lead, so the width is not drawn.');
      });
      svg.appendChild(hit);
    });
    return svg;
  }

  // ------------------------------------------------------- legend, note
  function legend(keyEl) {
    keyEl.innerHTML = '';
    const item = (text, style) => keyEl.appendChild(h('span', {}, [h('i', { style }), text]));
    item('Brier score, all prices, whisker its bootstrap interval', 'border-color:' + C_BS + ';border-top-width:3px');
    item('Brier score, ' + TRUNC_LABEL + ', share kept printed above', 'border-color:' + C_TRUNC + ';border-top-style:dashed;border-top-width:2px');
    item('Reliability', 'border-color:' + C_REL + ';border-top-width:8px');
    item('Resolution', 'border-color:' + C_RES + ';border-top-width:8px');
    item('Uncertainty', 'border-color:' + C_UNC + ';border-top-width:8px');
    item('Ladder width', 'border-color:' + C_WIDTH + ';border-top-width:3px');
    item('Error of the median', 'border-color:' + C_MAE + ';border-top-style:dashed;border-top-width:2px');
    // two short notes rather than one, since a key entry never wraps
    keyEl.appendChild(h('span', { class: 'kn', text: 'marker area is the contract count' }));
    keyEl.appendChild(h('span', { class: 'kn', text: 'bar is the 95 percent Wilson interval' }));
  }

  function method(meth, block) {
    const rel = block.reliability || [];
    const nc = rel.reduce((s, r) => s + (fin(r.n) ? r.n : 0), 0);
    const ncd = Math.max(0, ...rel.map(r => (fin(r.nCityDays) ? r.nCityDays : 0)));
    A.methodNote(meth, {
      title: 'Method',
      equation: [
        'BS = mean over contracts of (p − y)²,  y = 1 when the contract paid, else 0',
        'BS_binned = REL − RES + UNC  (Murphy, on the ten price bins; the file carries BS_binned beside BS)',
        'REL = Σ n_k (p̄_k − ȳ_k)² / n     RES = Σ n_k (ȳ_k − ȳ)² / n     UNC = ȳ (1 − ȳ)',
        'BS_trunc = BS over the contracts with 0.02 < p < 0.98;  retained = their share of all contracts',
        'width = median over ladders of strike(ladder crosses 0.9) − strike(ladder crosses 0.1), in °F',
        'p = (Yes bid + (1 − No bid)) / 2 when both sides are quoted',
      ].join('\n'),
      rules: [
        'The Yes price of a strike is the Yes bid plus one dollar less the No bid, halved, when both sides are quoted, and the single quoted side otherwise. Two-sided books only keeps the contracts where both sides are bid. Yes bid scores the Yes bid alone. A book bidding one cent against ninety-nine is unquoted.',
        'A contract is one strike on the hourly-last ladder snapshot of an eligible city-day with a settle. It pays when the settle clears the strike strictly, above for highs and below for lows. The standing hour of a snapshot is its lead rounded up less one, and a lead bin holds the hours above its low bound up to and including its high bound.',
        'Reliability bins are ten bins of width ten cents by price, the bin of a price p being the whole part of 10p. A bin under 50 contracts is pooled toward 50 cents and the receiving bin carries the pooled count while the histogram keeps the raw share. The horizontal position is the mean price in the bin, the vertical the share of contracts that paid, and the bar its 95 percent Wilson interval.',
        'The Brier score by lead carries a 95 percent percentile bootstrap over target dates with 1,000 draws and a fixed seed. A lead with under 30 city-days is not drawn. The skill score is one less the Brier score over the uncertainty term, so it is measured against the base rate, the share of contracts that paid at that lead.',
        'The truncated score keeps prices strictly between 2 and 98 cents, the range where a price says something the strike alone does not, and the share kept is printed over its marker. The Murphy terms are computed on the ten price bins, so their identity reproduces the binned score rather than the raw one; the difference is the within-bin variance, a few thousandths here. The rule at 0.25 is the score of pricing every contract at 50 cents, and the base rate rule is the score of pricing every contract at that lead\'s share paid, which is the uncertainty term.',
        'Sharpness is the width of the ladder under the selected price rule after it is made monotone, the median over ladders that cross both 10 and 90 cents. The error of the median is the mean absolute difference between the whole-degree crossing of the ladder at 50 cents, rounded up for highs and down for lows, and the settle, over ladders that cross.',
      ],
      n: 'Sample ' + A.int(nc) + ' contracts across the four lead bins, up to ' + A.int(ncd) + ' city-days in a bin, ' + describe() + '.',
    });
  }

  return { draw };
})();
