/* Figure 3: calibration of ForecastEx strike prices.

   A strike contract is a probability the exchange states in cents: a Yes
   price of 30 c says the day's high clears the strike three times in ten.
   This figure tests whether those prices are honest and how much they say,
   against the systems that publish a spread of their own.

   Two parts. A row of four reliability diagrams, one per lead bin, share
   paid against price. Under them, the two halves of the Brier score's Murphy
   decomposition by hour of lead, each drawn in a unit a reader can hold:
   reliability as the root-mean-square gap between a price bucket and the
   share of it that paid, in cents, and resolution as the share of the
   base-rate uncertainty the prices resolve. The Brier score itself, its
   skill score and the three terms are in the hover. The whole distribution's
   score by lead, CRPS, is the lead curve's CRPS view, so it is not repeated
   here.

   Everything drawn here comes from calibration.json as the builder ships it
   under docs/accuracy.md; the module bins nothing and scores nothing, and
   the site computes no probability of its own. draw(D) takes the bundle
   WXAcc.init assembles and reads D.cal. */
window.WXAccCal = (() => {
  const { el, txt, h, $ } = WXC;
  const A = WXAcc;

  // the price rules the page offers, in the reader's words
  const PRICE = [
    { key: 'mid', label: 'Yes price midpoint', title: 'the midpoint of the Yes bid and one dollar less the No bid; the single quoted side when only one side is bid' },
    { key: 'twoSided', label: 'Two-sided books only', title: 'the midpoint on books with a bid on both sides; a one-sided book is unquoted' },
  ];
  const C_FX = 'var(--accent)';
  const fin = v => v != null && isFinite(v);
  const cents = v => (fin(v) ? Math.round(v * 100) + ' c' : A.dash);
  // a reliability to a tenth of a cent, where the market's few cents need it
  const cents1 = v => (fin(v) ? (Math.round(v * 1000) / 10).toFixed(1) + ' c' : A.dash);
  const pct0 = v => (fin(v) ? Math.round(v * 100) + '%' : A.dash);

  /* The truth an alternative forecast system is held to, as on the lead curve
     and the map. The market keeps the settle its contracts pay on, whatever
     the frame; only the systems with a spread are rescored. */
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'Every system scored against the settle the contracts pay on' },
    { key: 'cli', label: 'NWS climate report', title: 'The systems with a spread scored against the National Weather Service climate report for the same date; the ForecastEx prediction market keeps the settle it pays on' },
  ];

  const st = { metric: 'high', price: 'mid', frame: 'metar' };
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
      A.tabs(bar, FRAMES, k => { st.frame = k; render(host, keyEl, meth); }, { initial: st.frame, label: 'Frame' });
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
    host.appendChild(h('div', { class: 'accsub', text: 'Reliability diagrams by lead bin, ' + describe() }));
    const ens = ensembleSeries();
    host.appendChild(reliability(block, edges, ens));
    host.appendChild(h('div', { class: 'accsub', text: 'Reliability and resolution by lead' }));
    host.appendChild(byLead(block, ens));
    host.appendChild(h('p', { class: 'cap acc-cal-note',
      text: ens.length
        ? 'Four of the alternative forecast systems publish the spread of their ensemble members as well as a centre, so a probability can be read off them without anything being fitted, and they are scored here on the same contracts. The rest publish a single temperature and appear in the error figures only.'
        : 'The alternative forecast systems publish no probabilities, so this figure has no line for them, and the site computes none of its own.' }));
    if (keyEl) legend(keyEl, ens);
    if (meth) method(meth, block);
  }

  // "highs, Yes price midpoint": the rule's label keeps its capital Yes
  const describe = () => (st.metric === 'high' ? 'highs' : 'lows') + ', ' + PRICE.find(p => p.key === st.price).label
    + (st.frame === 'cli' ? ', climate-report frame' : '');

  /* The systems with a spread of their own, in draw order. Each keeps the
     hue and dash it carries everywhere else on the page (WXAcc.color and
     WXAcc.ensDash), so a reader who knows the Canadian ensemble from the lead
     curve's CRPS view or the scorecard finds it here too. */
  function ensembleSeries() {
    const e = (cal && cal.metric && cal.metric[st.metric] && cal.metric[st.metric].ensembles) || {};
    const all = Object.keys(e).map(id => ({ id, rowId: A.ensId(id), name: e[id].name || A.name(A.ensId(id)),
                                            block: ((e[id].frame || {})[st.frame]) || {},
                                            color: A.color(A.ensId(id)), dash: A.ensDash(A.ensId(id)) }))
                              .filter(o => o.block && o.block.byLead);
    // in the registry's order, as the lead curve's CRPS view and the scorecard list them
    const order = A.systemGroups(all.map(o => o.rowId)).flatMap(g => g.ids);
    return order.map(rid => all.find(o => o.rowId === rid));
  }

  // ------------------------------------------------------- reliability row
  /* Four panels, one per lead bin, share paid against price. The marker
     area is the contract count, so a bin of 10,000 tail contracts and a
     bin of 500 central ones are seen for what they are; the bar is the
     Wilson interval on the share paid; the inset is the price histogram,
     the raw share of contracts in each decile before any pooling, which is
     the sharpness of the prices: a market that knows the outcome prices most
     contracts near 0 or 100. */
  function reliability(block, edges, ens) {
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
        const bh = IH * s / maxHist;
        svg.appendChild(el('rect', { x: ix0 + k * (IW / hist.length) + 0.5, y: iy1 - bh, width: IW / hist.length - 1, height: bh,
                                     fill: 'var(--muted)', 'fill-opacity': 0.5, stroke: 'none' }));
      });
      // Wilson bars, then the markers over them
      const pts = [];
      (r.x || []).forEach((px, k) => {
        const py = r.y && r.y[k];
        if (!fin(px) || !fin(py)) return;
        if (fin(r.lo && r.lo[k]) && fin(r.hi && r.hi[k])) {
          svg.appendChild(el('line', { x1: x(px), x2: x(px), y1: y(r.lo[k]), y2: y(r.hi[k]), stroke: C_FX, 'stroke-width': 1.4,
                                       'pointer-events': 'none' }));
        }
        const cnt = r.count && r.count[k];
        const rad = 2.2 + 7 * Math.sqrt((fin(cnt) ? cnt : 0) / maxCount);
        svg.appendChild(el('circle', { cx: x(px), cy: y(py), r: rad, fill: C_FX, 'fill-opacity': 0.45, stroke: C_FX,
                                       'stroke-width': 1.2, 'pointer-events': 'none' }));
        pts.push({ k, px, py, rad });
      });
      /* The systems that publish a spread, on the same contracts and the
         same axes. Drawn as a plain line rather than sized markers: what
         matters is where the curve sits against the diagonal, and the market
         keeps the weight of the panel. */
      (ens || []).forEach(e => {
        const er = (e.block.reliability || [])[i] || {};
        const ex = [], ey = [];
        (er.x || []).forEach((px, k) => {
          const py = er.y && er.y[k];
          if (fin(px) && fin(py)) { ex.push(x(px)); ey.push(y(py)); }
        });
        if (ex.length > 1) A.lineSeries(svg, ex, ey, Object.assign({ stroke: e.color, 'stroke-width': 1.6 },
                                                                     e.dash ? { 'stroke-dasharray': e.dash } : {}));
        A.dots(svg, ex, ey, { fill: e.color, r: 2 });
      });
      // hit targets last so they sit over the drawing
      pts.forEach(p => {
        const hit = el('circle', { cx: x(p.px), cy: y(p.py), r: Math.max(p.rad + 3, 9), fill: 'none', 'pointer-events': 'all' });
        A.hover(hit, () => binTip(r, p.k, edges, bins[i]));
        svg.appendChild(hit);
      });
      // the title: the lead bin, the sample, and the market's two measures
      const lead = bins[i] ? bins[i][0] + ' to ' + bins[i][1] + ' h before the day ends' : 'lead bin ' + (i + 1);
      svg.appendChild(txt(lead, { x: g.L, y: 13, 'font-size': 11, 'font-weight': 700, fill: 'var(--ink)' }));
      svg.appendChild(txt(A.int(r.n) + ' contracts, ' + A.int(r.nCityDays) + ' city-days',
                          { x: g.L, y: 27, class: 'ax' }));
      svg.appendChild(txt('Reliability ' + cents1(r.reliability) + ', resolution ' + pct0(r.resolution),
                          { x: g.L, y: 41, class: 'ax', 'font-weight': 700 }));
      if (!pts.length) svg.appendChild(txt('no priced bin', { x: (g.L + g.R) / 2, y: (g.T + g.B) / 2, 'text-anchor': 'middle', class: 'axl' }));
    }
    svg.appendChild(txt('Yes price, cents', { x: 480, y: H - 6, 'text-anchor': 'middle', class: 'ax' }));
    svg.appendChild(txt('Share that paid, percent', { x: 14, y: TOP + PW / 2, 'text-anchor': 'middle',
                                                      transform: 'rotate(-90 14 ' + (TOP + PW / 2) + ')', class: 'ax' }));
    return svg;
  }

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

  // ------------------------------------------------------- by lead
  // a stepped path: one flat tread per hourly bin, the pen lifted at a gap
  function stepLine(svg, x, y, hs, vals, attrs) {
    const xs = [], ys = [];
    hs.forEach((hh, i) => {
      if (fin(vals[i])) { xs.push(x(hh + 0.5), x(hh - 0.5)); ys.push(y(vals[i]), y(vals[i])); }
      else { xs.push(null, null); ys.push(null, null); }
    });
    return A.lineSeries(svg, xs, ys, attrs);
  }

  /* Two panels side by side on the lead curve's axis, the day ending at the
     right. Reliability, lower is better, in cents; resolution, higher is
     better, as a percent. The market is drawn through the bin centers with
     its bootstrap band, an ensemble as a step with its dash, as on the lead
     curve. One hover column per hour spans both panels and lists every
     system's Brier score and the terms behind the two lines. */
  function byLead(block, ens) {
    const by = block.byLead || {};
    const hs = by.h || [];
    const H = 318, T = 30, B = H - 58;
    const svg = el('svg', { viewBox: '0 0 960 ' + H, class: 'acc-cal-lead' });
    if (!hs.length) { svg.appendChild(txt('no lead in the published record', { x: 480, y: H / 2, 'text-anchor': 'middle', class: 'axl' })); return svg; }
    const series = [{ id: 'FX', name: A.name('FX'), color: C_FX, dash: null, by }]
      .concat((ens || []).map(e => ({ id: e.rowId, name: e.name, color: e.color, dash: e.dash, by: e.block.byLead || {} })));
    const hmax = Math.max.apply(null, hs);
    const panels = [
      { key: 'reliability', title: 'Reliability', sub: 'lower is better', axis: 'RMS calibration error, cents', L: 70, R: 450,
        fmt: v => Math.round(v * 100) + ' c', floor: 0.1 },
      { key: 'resolution', title: 'Resolution', sub: 'higher is better', axis: 'Share of uncertainty resolved, percent', L: 572, R: 952,
        fmt: v => Math.round(v * 100) + '%', top: 1 },
    ];
    const guides = [];
    panels.forEach(pn => {
      const g = { L: pn.L, R: pn.R, T, B };
      const x = A.scale(hmax + 0.5, -0.5, g.L, g.R);
      let ymax = pn.top || 0;
      if (!pn.top) {
        series.forEach(s => (s.by[pn.key] || []).concat(s.by[pn.key + 'Hi'] || []).forEach(v => { if (fin(v) && v > ymax) ymax = v; }));
        ymax = Math.max(pn.floor, ymax * 1.08);
      }
      const step = A.niceStep(ymax, 5);
      const y = A.scale(0, ymax, g.B, g.T);
      A.yAxis(svg, g, y, A.ticks(0, ymax, step), pn.fmt, null);
      const ly = (g.T + g.B) / 2, lx = g.L - 50;
      svg.appendChild(txt(pn.axis, { x: lx, y: ly, 'text-anchor': 'middle', transform: 'rotate(-90 ' + lx + ' ' + ly + ')', class: 'ax' }));
      A.leadAxis(svg, g, x, hmax, 0, 'Hours before the end of the target day');
      const t = txt('', { x: g.L, y: 14, 'font-size': 11 });
      const b1 = el('tspan', { 'font-weight': 700, fill: 'var(--ink)' }); b1.textContent = pn.title;
      const b2 = el('tspan', { fill: 'var(--muted)' }); b2.textContent = ', ' + pn.sub;
      t.appendChild(b1); t.appendChild(b2); svg.appendChild(t);
      // the market's band, the ensembles, then the market's line on top
      const cx = hs.map(hh => x(hh));
      const fx = series[0].by;
      A.band(svg, cx, (fx[pn.key + 'Lo'] || []).map(v => (fin(v) ? y(v) : null)),
             (fx[pn.key + 'Hi'] || []).map(v => (fin(v) ? y(v) : null)), C_FX);
      series.slice(1).forEach(s => stepLine(svg, x, y, hs, s.by[pn.key] || [],
        Object.assign({ stroke: s.color, 'stroke-width': 1.8 }, s.dash ? { 'stroke-dasharray': s.dash } : {})));
      A.lineSeries(svg, cx, (fx[pn.key] || []).map(v => (fin(v) ? y(v) : null)), { stroke: C_FX, 'stroke-width': A.width('FX') });
      const guide = el('rect', { x: 0, y: g.T, width: 0, height: g.B - g.T, fill: 'var(--ink)', 'fill-opacity': 0.06,
                                 stroke: 'none', 'pointer-events': 'none', visibility: 'hidden' });
      svg.appendChild(guide);
      guides.push({ guide, x });
      pn.x = x;
    });
    // hover columns last, one per hour in each panel, both guides moving together
    panels.forEach(pn => {
      hs.forEach((hh, i) => {
        const x0 = pn.x(hh + 0.5), w = pn.x(hh - 0.5) - x0;
        const r = el('rect', { x: x0, y: T, width: w, height: B - T, fill: 'transparent', stroke: 'none' });
        r.addEventListener('mouseenter', () => guides.forEach(gd => {
          gd.guide.setAttribute('x', gd.x(hh + 0.5)); gd.guide.setAttribute('width', gd.x(hh - 0.5) - gd.x(hh + 0.5));
          gd.guide.setAttribute('visibility', 'visible');
        }));
        r.addEventListener('mouseleave', () => guides.forEach(gd => gd.guide.setAttribute('visibility', 'hidden')));
        A.hover(r, () => leadTip(series, i, hh));
        svg.appendChild(r);
      });
    });
    return svg;
  }

  /* A table for the hour under the cursor: each system's two measures, its
     Brier score and its skill against the base rate, in draw order. */
  function leadTip(series, i, hh) {
    const title = hh === 0 ? 'The hour the day ends' : hh + ' hour' + (hh === 1 ? '' : 's') + ' before the day ends';
    const v = (s, k) => (s.by[k] ? s.by[k][i] : null);
    let rows = '';
    series.forEach(s => {
      rows += '<tr' + (s.id === 'FX' ? ' class="tfx"' : '') + '><td>' + A.swatch(s.id).replace(A.name(s.id), A.short(s.id))
        + '</td><td>' + cents1(v(s, 'reliability')) + '</td><td>' + pct0(v(s, 'resolution'))
        + '</td><td>' + pct0(v(s, 'bss')) + '</td><td>' + A.f3(v(s, 'brier')) + '</td><td>' + A.int(v(s, 'n')) + '</td></tr>';
    });
    const fx = series[0];
    let foot = '';
    if (fin(v(fx, 'reliabilityLo')) && fin(v(fx, 'reliabilityHi'))) {
      foot += 'ForecastEx reliability ' + A.iv(v(fx, 'reliabilityLo'), v(fx, 'reliabilityHi'), cents1)
        + ', resolution ' + A.iv(v(fx, 'resolutionLo'), v(fx, 'resolutionHi'), pct0) + ', 95 percent intervals. ';
    }
    if (fin(v(fx, 'base'))) {
      foot += A.pct(v(fx, 'base')) + ' of the ForecastEx prediction market’s contracts paid, so pricing every one at that rate scores '
        + A.f3(v(fx, 'unc')) + ', the uncertainty the resolution is a share of.';
    }
    return '<b>' + title + '</b><div class="tsub">' + describe() + '.</div>'
      + '<table class="l3"><tr><th>System</th><th>Reliability</th><th>Resolution</th><th>Brier skill</th><th>Brier</th><th>Contracts</th></tr>'
      + rows + '</table>' + (foot ? '<div class="tf">' + foot + '</div>' : '');
  }

  // ------------------------------------------------------- legend, note
  function legend(keyEl, ens) {
    keyEl.innerHTML = '';
    const item = (text, style) => keyEl.appendChild(h('span', {}, [h('i', { style }), text]));
    item('ForecastEx', 'border-color:' + C_FX + ';border-top-width:3px');
    (ens || []).forEach(e => item(e.name, 'border-color:' + e.color + ';border-top-width:2px'
      + (e.dash ? ';border-top-style:dashed' : '')));
    keyEl.appendChild(h('span', { class: 'kn', text: 'marker area is the contract count' }));
    keyEl.appendChild(h('span', { class: 'kn', text: 'bar is the 95 percent Wilson interval, band the 95 percent bootstrap interval' }));
  }

  /* This figure scores the ForecastEx prediction market alone, so its span is the ForecastEx prediction market's own
     record on the metric shown. Highs reach back to the day the exchange
     opened its temperature board; lows were too thinly quoted to score
     until months later, and the line says so rather than leaving the
     reader to assume both ran the same length. */
  function fxSpan() {
    const meta = (cal && cal.meta) || {};
    const sp = A.span(meta, 'FX', st.metric);
    if (!sp || !sp.start) return '';
    const word = st.metric === 'high' ? 'highs' : 'lows';
    let t = 'Scored on the ForecastEx prediction market’s ' + word + ' from ' + A.mdyY(sp.start) + ' to ' + A.mdyY(sp.end || meta.asof)
          + (sp.days ? ', ' + A.int(sp.days) + ' days' : '') + '.';
    const other = A.span(meta, 'FX', st.metric === 'high' ? 'low' : 'high');
    if (other && other.start && other.start !== sp.start) {
      t += ' The ' + (st.metric === 'high' ? 'lows' : 'highs') + ' record starts ' + A.mdyY(other.start)
         + ', so the two tabs do not cover the same period.';
    }
    const es = ['AIFS'].concat(A.ENS_ROWS).map(id => A.ensSpan(id, st.metric)).filter(s => s && s.start).map(s => s.start).sort();
    if (es.length) t += ' The ensembles’ spreads are on record from ' + A.mdyY(es[0]) + '.';
    return t;
  }

  function method(meth, block) {
    const rel = block.reliability || [];
    const nc = rel.reduce((s, r) => s + (fin(r.n) ? r.n : 0), 0);
    const ncd = Math.max(0, ...rel.map(r => (fin(r.nCityDays) ? r.nCityDays : 0)));
    A.methodNote(meth, {
      title: 'Method',
      body: [
        'A contract’s price should equal the probability it pays off. A reliability diagram plots the average price in a bucket against the share of contracts in that bucket that actually paid, and perfectly calibrated prices fall on the diagonal.',
        { tex: 'p = \\frac{\\text{Yes bid} + (1 - \\text{No bid})}{2}' },
        'used when both sides are quoted, the single quoted side otherwise.',
        { tex: 'BS = \\frac{1}{N}\\sum_{j=1}^{N} (p_j - y_j)^2 = REL - RES + UNC' },
        { tex: 'REL = \\frac{1}{N}\\sum_k n_k(\\bar p_k - \\bar y_k)^2 \\qquad RES = \\frac{1}{N}\\sum_k n_k(\\bar y_k - \\bar y)^2 \\qquad UNC = \\bar y(1-\\bar y)' },
        '$y_j$ is 1 when contract $j$ paid, and bucket $k$ holds $n_k$ contracts with mean price $\\bar p_k$ and share paid $\\bar y_k$. This is Murphy’s decomposition of the Brier score. REL is the penalty for buckets that pay away from their price, RES the credit for buckets that pay away from the base rate $\\bar y$, and UNC the score of pricing every contract at the base rate.',
        { tex: '\\text{Reliability} = \\sqrt{REL} \\qquad \\text{Resolution} = \\frac{RES}{UNC} \\qquad \\text{Brier skill} = \\frac{RES - REL}{UNC}' },
        'Reliability is the root-mean-square gap between a bucket’s price and its share paid, the root-mean-square form of the expected calibration error, so 4 cents means a typical bucket paid about 4 percentage points away from its price. Resolution is the share of the base-rate uncertainty the buckets resolve, the variance of the share paid across buckets over the variance of the outcome, zero when every bucket pays at the base rate and 100 percent when every bucket pays all or nothing. The Brier skill against the base rate combines the two and is in the hover.',
        'The two answer different questions. Resolution ignores whether prices are honest, so prices that sort outcomes well but sit off the diagonal still score well on it, and reliability says how far off they sit. Reliability has no sign, so the diagrams show which way a bucket misses.',
      ],
      rules: [
        'A contract is one strike on the last ladder snapshot of each hour, for a city-day with a settle, and it pays when the settle clears the strike.',
        'Buckets are ten cents wide. The diagrams pool a bucket under 50 contracts toward the 50-cent bucket, and reliability and resolution use the ten buckets as they are.',
        'Reliability has a floor set by sampling of about 50 divided by the square root of n percentage points for a bucket of n contracts near 50 cents, so a thin slice shows a gap even when its prices are honest. Contracts on one ladder move together, so the intervals resample target dates rather than contracts.',
        'Four of the alternative forecast systems publish the spread of their ensemble members as well as a centre, so a probability can be read off them and scored on these same contracts. The reading is a normal curve on the model’s own forecast of the day’s extreme with the model’s own spread at the hour that extreme falls on, and the contract pays when the unrounded extreme reaches half a degree past the strike, which is how settlement rounds. Nothing is fitted and no bias is removed. Their values are banked at the running observed extreme exactly as every other value on the page is, since a reader watching the reports knows a strike already cleared.',
        'The frame tab changes the truth those systems are held to, as it does on the lead curve and the map. In the climate-report frame the contract’s outcome is recomputed against the National Weather Service report for the same date, which is a different definition of the day’s extreme, and Buckley Field drops out because Denver’s report stands in for it. The ForecastEx prediction market keeps the settle its own contracts pay on in either frame.',
        'Two caveats belong with those lines. A model publishes a spread for each hour, not for the day’s extreme, so reading the peak hour’s spread as the extreme’s is an approximation this page makes rather than one the model makes. And the level bias each model carries in the error figures passes straight into its probability here, which is much of why its reliability sits above the ForecastEx prediction market’s.',
        'The score of the whole distribution by lead, CRPS, is the lead curve’s CRPS view.',
        'Intervals are 95 percent bootstrap intervals over 1,000 resamples of the target dates, and a lead with under 30 city-days is not drawn.',
      ],
      span: fxSpan(),
      n: 'Sample ' + A.int(nc) + ' contracts across the four lead bins, up to ' + A.int(ncd) + ' city-days in a bin, ' + describe() + '.',
    });
  }

  return { draw };
})();
