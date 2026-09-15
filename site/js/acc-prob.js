/* Probabilistic skill: the whole distribution, scored by lead.

   The systems that publish a distribution, the ForecastEx prediction
   market's price ladder and the four ensembles, scored on the same strikes
   at the same instant. Three charts on the lead curve's axis, each the full
   width of the page and drawn by WXAcc.leadChart:

     CRPS, the score of the whole distribution in degrees (lead-curve.json,
       metric.<m>.crps);
     reliability, the root-mean-square gap between a price bucket and the
       share of it that paid, in cents; and
     resolution, the share of the base-rate uncertainty the prices resolve
       (both from calibration.json, the halves of Murphy's decomposition of
       the Brier score);

   then the reliability diagrams those two numbers summarise, one per lead
   bin. Nothing here is computed; the module chooses which of the builder's
   series to draw. The Days and Frame tabs reach every chart; the Price tab
   changes the price rule the reliability, resolution and diagrams read, and
   CRPS always reads the ladder as quoted. */
window.WXAccProb = (() => {
  const { el, txt, $ } = WXC;
  const A = WXAcc;

  const COHORTS = [
    { key: 'own', label: 'Every day on record', title: 'Each system scored on the city-days its own record covers' },
    { key: 'fixed30', label: 'Fixed sample', title: 'City-days the ForecastEx prediction market priced at every hour from 30 to 0' },
  ];
  const PRICE = [
    { key: 'mid', label: 'Yes price midpoint', title: 'Reliability, resolution and the diagrams read the midpoint of the Yes bid and one dollar less the No bid, the single quoted side when only one side is bid' },
    { key: 'twoSided', label: 'Two-sided books only', title: 'Reliability, resolution and the diagrams read the midpoint on books with a bid on both sides only' },
  ];
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'Every system scored against the settle the contracts pay on' },
    { key: 'cli', label: 'NWS climate report', title: 'The ensembles scored against the National Weather Service climate report for the same date; the ForecastEx prediction market keeps the settle it pays on' },
  ];
  const st = { metric: 'high', cohort: 'own', price: 'mid', frame: 'metar' };
  let lead = null, cal = null, built = false;

  const fin = v => v != null && isFinite(v);
  const cents1 = v => (fin(v) ? (Math.round(v * 1000) / 10).toFixed(1) + ' c' : A.dash);
  const pct0 = v => (fin(v) ? Math.round(v * 100) + '%' : A.dash);
  const view = () => (st.metric === 'high' ? 'Highs' : 'Lows') + ', '
    + COHORTS.find(c => c.key === st.cohort).label.toLowerCase()
    + (st.frame === 'cli' ? ', climate-report frame' : '');
  const priceName = () => PRICE.find(p => p.key === st.price).label;

  // the ensembles a block carries, as page ids in the registry's order
  function ordered(keys) {
    const byId = {};
    keys.forEach(k => { byId[A.ensId(k)] = k; });
    return A.systemGroups(Object.keys(byId)).flatMap(g => g.ids).map(id => ({ id, key: byId[id] }));
  }

  // ------------------------------------------------------------- CRPS
  function drawCrps(svg) {
    const c = lead && lead.metric && lead.metric[st.metric] && lead.metric[st.metric].crps;
    const v = c && c.cohorts && c.cohorts[st.cohort] && c.cohorts[st.cohort][st.frame];
    if (!v || !v.systems || !v.systems.FX) { A.notYet(svg, 'CRPS is not in the published record.'); return []; }
    const hs = c.h;
    const clean = arr => hs.map((_, i) => (arr && fin(arr[i]) ? arr[i] : null));
    const ens = ordered(Object.keys(v.systems).filter(k => k !== 'FX'));
    const S = [{ id: 'FX', smooth: true, v: clean(v.systems.FX.crps), lo: clean(v.systems.FX.lo), hi: clean(v.systems.FX.hi), n: v.systems.FX.n || [] }]
      .concat(ens.map(e => ({ id: e.id, dash: A.ensDash(e.id), width: 1.8, v: clean(v.systems[e.key].crps), n: v.systems[e.key].n || [] })));
    const beats = v.beats || [];
    A.leadChart(svg, {
      H: 400, hs, series: S, floor: 0.5, fmt: x => A.f1(x) + '°', label: 'CRPS, °F (lower is better)', name: true,
      strips: { n: v.systems.FX.n || [], beats },
      tip: (i, hh) => {
        const b = beats[i], n = (v.systems.FX.n || [])[i];
        let sub = view() + '. ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample') + '.';
        if (b && fin(b.k)) sub += ' ForecastEx beats ' + b.k + ' of ' + b.of + ' ensembles.';
        const fx = S[0];
        const foot = fin(fx.lo[i]) && fin(fx.hi[i]) ? 'ForecastEx band ' + A.iv(fx.lo[i], fx.hi[i], A.f2) + '.' : '';
        return A.rankTip(A.leadTitle(hh), sub, S.map(s => ({ id: s.id, v: s.v[i], n: s.n[i] })), 'CRPS °F', A.f2, { foot });
      },
    });
    return S.map(s => s.id);
  }

  // ------------------------------------------------------------- reliability and resolution
  function calBlocks() {
    const m = cal && cal.metric && cal.metric[st.metric];
    const co = m && m.cohorts && m.cohorts[st.cohort];
    const fx = co && co.price && co.price[st.price];
    const ensAll = (co && co.ensembles) || {};
    const ens = ordered(Object.keys(ensAll)).map(e => ({ id: e.id, name: ensAll[e.key].name || A.name(e.id),
                                                          blk: ((ensAll[e.key].frame || {})[st.frame]) || null }))
      .filter(e => e.blk && e.blk.byLead);
    return { fx, ens };
  }

  function drawMeasure(svg, key, spec) {
    const { fx, ens } = calBlocks();
    if (!fx || !fx.byLead) { A.notYet(svg, 'The file carries no block for this view.'); return; }
    const by = fx.byLead, hs = by.h;
    const arr = (b, k) => hs.map((_, i) => (b[k] && fin(b[k][i]) ? b[k][i] : null));
    const S = [{ id: 'FX', smooth: true, v: arr(by, key), lo: arr(by, key + 'Lo'), hi: arr(by, key + 'Hi'), by }]
      .concat(ens.map(e => ({ id: e.id, dash: A.ensDash(e.id), width: 1.8, v: arr(e.blk.byLead, key), by: e.blk.byLead })));
    A.leadChart(svg, Object.assign({ hs, series: S, tip: (i, hh) => calTip(S, i, hh, key, spec.higherBetter) }, spec));
  }

  /* One hour of the reliability or resolution chart: every system's two
     numbers, its Brier skill and Brier score, ordered by the chart's own
     measure, best first. */
  function calTip(S, i, hh, key, higherBetter) {
    const v = (s, k) => (s.by[k] ? s.by[k][i] : null);
    const rank = s => (fin(v(s, key)) ? (higherBetter ? -v(s, key) : v(s, key)) : Infinity);
    let rows = '';
    S.slice().sort((a, b) => rank(a) - rank(b)).forEach((s, k) => {
      rows += '<tr' + (s.id === 'FX' ? ' class="tfx"' : '') + '><td>' + (k + 1) + '</td><td>' + A.swatch(s.id).replace(A.name(s.id), A.short(s.id))
        + '</td><td>' + cents1(v(s, 'reliability')) + '</td><td>' + pct0(v(s, 'resolution'))
        + '</td><td>' + pct0(v(s, 'bss')) + '</td><td>' + A.f3(v(s, 'brier')) + '</td><td>' + A.int(v(s, 'n')) + '</td></tr>';
    });
    const fx = S[0];
    let foot = '';
    if (fin(v(fx, key + 'Lo')) && fin(v(fx, key + 'Hi'))) {
      foot += 'ForecastEx ' + key + ' ' + A.iv(v(fx, key + 'Lo'), v(fx, key + 'Hi'), key === 'reliability' ? cents1 : pct0) + ', 95 percent interval. ';
    }
    if (fin(v(fx, 'base'))) {
      foot += A.pct(v(fx, 'base')) + ' of the ForecastEx prediction market’s contracts paid, so pricing every one at that rate scores '
        + A.f3(v(fx, 'unc')) + ', the uncertainty resolution is a share of.';
    }
    return '<b>' + A.leadTitle(hh) + '</b><div class="tsub">' + view() + ', ' + priceName() + '.</div>'
      + '<table class="l3"><tr><th>#</th><th>System</th><th>Reliability</th><th>Resolution</th><th>Brier skill</th><th>Brier</th><th>Contracts</th></tr>'
      + rows + '</table>' + (foot ? '<div class="tf">' + foot + '</div>' : '');
  }

  // ------------------------------------------------------------- reliability diagrams
  /* Four panels, one per lead bin, share paid against price. The marker
     area is the contract count, so a bin of 10,000 tail contracts and a
     bin of 500 central ones are seen for what they are; the bar is the
     Wilson interval on the share paid; the inset is the price histogram,
     the raw share of contracts in each decile before any pooling, which is
     the sharpness of the prices. The ensembles are plain lines on the same
     contracts. */
  function diagrams(host) {
    host.innerHTML = '';
    const { fx, ens } = calBlocks();
    if (!fx || !fx.reliability) { A.notYet(host, 'The file carries no block for this view.'); return; }
    const edges = (cal.bins && cal.bins.edges) || [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
    const bins = fx.leadBins || [];
    const rel = fx.reliability || [];
    const n = Math.min(bins.length, rel.length) || 1;
    const H = 292, TOP = 52, PW = 200, GAP = (960 - 46 - 16 - PW * n) / Math.max(n - 1, 1);
    const svg = el('svg', { viewBox: '0 0 960 ' + H, class: 'acc-cal-rel' });
    const C_FX = A.color('FX');
    const maxCount = Math.max(1, ...rel.map(r => Math.max(0, ...(r.count || []).filter(fin))));
    const maxHist = Math.max(0.01, ...rel.map(r => Math.max(0, ...(r.hist || []).filter(fin))));
    for (let i = 0; i < n; i++) {
      const r = rel[i] || {};
      const g = { L: 46 + i * (PW + GAP), T: TOP };
      g.R = g.L + PW; g.B = g.T + PW;
      const x = A.scale(0, 1, g.L, g.R), y = A.scale(0, 1, g.B, g.T);
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
      const hist = r.hist || [];
      const IW = 80, IH = 40, ix0 = g.R - IW - 8, iy1 = g.B - 10;
      svg.appendChild(txt('share of contracts by price', { x: ix0 + IW / 2, y: iy1 - IH - 5, 'text-anchor': 'middle', 'font-size': 8, fill: 'var(--muted)' }));
      svg.appendChild(el('line', { x1: ix0, x2: ix0 + IW, y1: iy1 + 0.5, y2: iy1 + 0.5, stroke: 'var(--rule)', 'stroke-width': 1 }));
      hist.forEach((s, k) => {
        if (!fin(s)) return;
        const bh = IH * s / maxHist;
        svg.appendChild(el('rect', { x: ix0 + k * (IW / hist.length) + 0.5, y: iy1 - bh, width: IW / hist.length - 1, height: bh,
                                     fill: 'var(--muted)', 'fill-opacity': 0.5, stroke: 'none' }));
      });
      const pts = [];
      (r.x || []).forEach((px, k) => {
        const py = r.y && r.y[k];
        if (!fin(px) || !fin(py)) return;
        if (fin(r.lo && r.lo[k]) && fin(r.hi && r.hi[k])) {
          svg.appendChild(el('line', { x1: x(px), x2: x(px), y1: y(r.lo[k]), y2: y(r.hi[k]), stroke: C_FX, 'stroke-width': 1.4, 'pointer-events': 'none' }));
        }
        const cnt = r.count && r.count[k];
        const rad = 2.2 + 7 * Math.sqrt((fin(cnt) ? cnt : 0) / maxCount);
        svg.appendChild(el('circle', { cx: x(px), cy: y(py), r: rad, fill: C_FX, 'fill-opacity': 0.45, stroke: C_FX,
                                       'stroke-width': 1.2, 'pointer-events': 'none' }));
        pts.push({ k, px, py, rad });
      });
      ens.forEach(e => {
        const er = (e.blk.reliability || [])[i] || {};
        const ex = [], ey = [];
        (er.x || []).forEach((px, k) => {
          const py = er.y && er.y[k];
          if (fin(px) && fin(py)) { ex.push(x(px)); ey.push(y(py)); }
        });
        const dash = A.ensDash(e.id);
        if (ex.length > 1) A.lineSeries(svg, ex, ey, Object.assign({ stroke: A.color(e.id), 'stroke-width': 1.6 }, dash ? { 'stroke-dasharray': dash } : {}));
        A.dots(svg, ex, ey, { fill: A.color(e.id), r: 2 });
      });
      pts.forEach(p => {
        const hit = el('circle', { cx: x(p.px), cy: y(p.py), r: Math.max(p.rad + 3, 9), fill: 'none', 'pointer-events': 'all' });
        A.hover(hit, () => binTip(r, p.k, edges, bins[i]));
        svg.appendChild(hit);
      });
      const leadTxt = bins[i] ? bins[i][0] + ' to ' + bins[i][1] + ' h before the day ends' : 'lead bin ' + (i + 1);
      svg.appendChild(txt(leadTxt, { x: g.L, y: 13, 'font-size': 11, 'font-weight': 700, fill: 'var(--ink)' }));
      svg.appendChild(txt(A.int(r.n) + ' contracts, ' + A.int(r.nCityDays) + ' city-days', { x: g.L, y: 27, class: 'ax' }));
      svg.appendChild(txt('Reliability ' + cents1(r.reliability) + ', resolution ' + pct0(r.resolution),
                          { x: g.L, y: 41, class: 'ax', 'font-weight': 700 }));
      if (!pts.length) svg.appendChild(txt('no priced bin', { x: (g.L + g.R) / 2, y: (g.T + g.B) / 2, 'text-anchor': 'middle', class: 'axl' }));
    }
    svg.appendChild(txt('Yes price, cents', { x: 480, y: H - 6, 'text-anchor': 'middle', class: 'ax' }));
    svg.appendChild(txt('Share that paid, percent', { x: 14, y: TOP + PW / 2, 'text-anchor': 'middle',
                                                      transform: 'rotate(-90 14 ' + (TOP + PW / 2) + ')', class: 'ax' }));
    host.appendChild(svg);
  }

  function binTip(r, k, edges, bin) {
    const raw = fin(r.hist && r.hist[k]) && fin(r.n) ? r.hist[k] * r.n : null;
    const cnt = r.count && r.count[k];
    const pooled = fin(raw) && fin(cnt) && cnt > raw * 1.02 + 1;
    return A.tooltip().rows('Prices ' + Math.round(edges[k] * 100) + ' to ' + Math.round(edges[k + 1] * 100) + ' c', [
      ['Mean price', fin(r.x[k]) ? Math.round(r.x[k] * 100) + ' c' : A.dash],
      ['Share that paid', A.pct1(r.y[k])],
      ['95% Wilson interval', A.iv(r.lo && r.lo[k], r.hi && r.hi[k], A.pct1)],
      ['Contracts' + (pooled ? ' (pooled)' : ''), A.int(cnt)],
      ['Share of all contracts', A.pct1(r.hist && r.hist[k])],
      ['Lead bin', bin ? bin[0] + ' to ' + bin[1] + ' h' : A.dash],
    ], pooled ? 'This decile carries the contracts of a thinner neighbor pooled toward 50 c.' : '');
  }

  // ------------------------------------------------------------- render
  function render() {
    const crps = $('#accCrps'), relSvg = $('#accRel'), resSvg = $('#accRes'), diag = $('#accDiag');
    const keyEl = $('#accProbKey'), meth = $('#accProbMethod');
    const ids = crps ? drawCrps(crps) : [];
    if (relSvg) drawMeasure(relSvg, 'reliability', { H: 360, floor: 0.1, fmt: v => Math.round(v * 100) + ' c',
                                                     label: 'Reliability, RMS calibration error, cents (lower is better)' });
    if (resSvg) drawMeasure(resSvg, 'resolution', { H: 360, ymax: 1, higherBetter: true, fmt: v => Math.round(v * 100) + '%',
                                                    label: 'Resolution, share of uncertainty resolved (higher is better)' });
    if (diag) diagrams(diag);
    const { ens } = calBlocks();
    const keyIds = ids.length ? ids : ['FX'].concat(ens.map(e => e.id));
    if (keyEl) {
      A.key(keyEl, keyIds, { dashes: true, note: 'Bands are the ForecastEx prediction market’s 95 percent bootstrap interval; in the diagrams marker area is the contract count and the bar the 95 percent Wilson interval.',
        since: id => { const s = id === 'FX' ? A.span(lead && lead.meta, 'FX', st.metric) : A.ensSpan(id, st.metric);
                       return s && s.start ? 'since ' + A.mdy(s.start) : ''; } });
    }
    method(meth);
  }

  function method(meth) {
    if (!meth) return;
    const meta = (lead && lead.meta) || (cal && cal.meta) || {};
    const { fx } = calBlocks();
    const rel = (fx && fx.reliability) || [];
    const nc = rel.reduce((s, r) => s + (fin(r.n) ? r.n : 0), 0);
    const es = ['AIFS'].concat(A.ENS_ROWS).map(id => A.ensSpan(id, st.metric)).filter(s => s && s.start).map(s => s.start).sort();
    A.methodNote(meth, {
      title: 'CRPS, reliability and resolution',
      body: [
        'CRPS, the continuous ranked probability score, measures a whole forecast distribution against what happened, in degrees. It shrinks as probability gathers near the observed value, and a forecast that puts all its probability on one whole degree scores its absolute error, so it reads on the same scale as the mean absolute error above.',
        { tex: 'CRPS_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\sum_{k} \\left( F_{s,i,h}(k) - \\mathbb{1}[o_i \\le k] \\right)^2' },
        'where $F_{s,i,h}(k)$ is system $s$’s probability that city-day $i$ settles at or below $k$ as it stood at lead $h$, $k$ runs over whole degrees across the ForecastEx prediction market’s strikes, and $o_i$ is the settle. On a ladder that sum is the Brier score of every one-degree contract added up.',
        'A contract’s price should equal the probability it pays off. The Brier score splits into three terms over ten price buckets, Murphy’s decomposition.',
        { tex: 'BS = \\frac{1}{N}\\sum_{j=1}^{N} (p_j - y_j)^2 = REL - RES + UNC' },
        { tex: 'REL = \\frac{1}{N}\\sum_k n_k(\\bar p_k - \\bar y_k)^2 \\qquad RES = \\frac{1}{N}\\sum_k n_k(\\bar y_k - \\bar y)^2 \\qquad UNC = \\bar y(1-\\bar y)' },
        '$y_j$ is 1 when contract $j$ paid, and bucket $k$ holds $n_k$ contracts with mean price $\\bar p_k$ and share paid $\\bar y_k$. REL is the penalty for buckets that pay away from their price, RES the credit for buckets that pay away from the base rate $\\bar y$, and UNC the score of pricing every contract at the base rate.',
        { tex: '\\text{Reliability} = \\sqrt{REL} \\qquad \\text{Resolution} = \\frac{RES}{UNC} \\qquad \\text{Brier skill} = \\frac{RES - REL}{UNC}' },
        'Reliability is the root-mean-square gap between a bucket’s price and its share paid, the root-mean-square form of the expected calibration error, so 4 cents means a typical bucket paid about 4 percentage points away from its price. Resolution is the variance of the share paid across buckets over the variance of the outcome, zero when every bucket pays at the base rate and 100 percent when every bucket pays all or nothing. Resolution ignores whether prices are honest and reliability has no sign, so the reliability diagrams show which way a bucket misses.',
      ],
      rules: [
        'For the ForecastEx prediction market the distribution is its price ladder, the Yes prices read as probabilities. CRPS reads the ladder as quoted, monotone and closed at the end strikes. Reliability, resolution and the diagrams read one strike on the last snapshot of each hour under the price rule the Price tab selects.',
        'For an ensemble the distribution is read over the hours of the day still to come. Its centre is the highest (for a low, the lowest) of the ensemble’s hourly means from that moment to the end of the day, its spread is the members’ spread at that hour, and the day’s extreme is the more extreme of that and what has already been observed. A strike the observations have already cleared is paid, any other pays only if the hours left reach it, and a contract pays when the unrounded extreme reaches half a degree past the strike, which is how settlement rounds. Nothing is fitted and no bias is removed.',
        'Two caveats belong with the ensembles. A model publishes a spread for each hour, not for the extreme of several hours, so reading the spread at the hour of the extreme as the extreme’s is an approximation this page makes. And the level bias each model carries in the error figures passes straight into its probability, which shows up as reliability.',
        'Every system is scored at the same instant on the same strikes, on the city-days the ForecastEx prediction market priced at that hour, and each ensemble is drawn on the part of those days its record covers. Two sets of days are available, every day on record, or the fixed sample the ForecastEx prediction market priced at every hour from 30 to 0.',
        'Buckets are ten cents wide. The diagrams pool a bucket under 50 contracts toward the 50-cent bucket, and reliability and resolution use the ten buckets as they are. Reliability has a floor set by sampling of about 50 divided by the square root of n percentage points for a bucket of n contracts near 50 cents.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates, and a lead with under 30 city-days is not drawn. The beats strip under CRPS counts the ensembles the ForecastEx prediction market beat at that hour, meaning the 95 percent interval of the paired difference over the days both hold lies entirely in its favor.',
      ],
      span: A.cohortSpanLine(meta, st.cohort) + (es.length ? ' The ensembles’ spreads are on record from ' + A.mdyY(es[0]) + '.' : '')
        + (st.frame === 'cli' ? ' In the climate-report frame each ensemble is scored against the National Weather Service report for the same date, the ForecastEx prediction market keeps the settle it pays on, and both are restricted to the city-days that hold a report.' : ''),
      n: 'Sample ' + A.int(nc) + ' ForecastEx contracts across the four lead bins, ' + view().toLowerCase() + ', ' + priceName() + '. ' + A.windowAndBuilt(meta) + '.',
    });
  }

  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { st.metric = k; render(); }, st.metric);
    A.tabs(bar, COHORTS, k => { st.cohort = k; render(); }, { initial: st.cohort, label: 'Days' });
    A.tabs(bar, PRICE, k => { st.price = k; render(); }, { initial: st.price, label: 'Price' });
    A.tabs(bar, FRAMES, k => { st.frame = k; render(); }, { initial: st.frame, label: 'Frame' });
    built = true;
  }

  function draw(D) {
    const host = $('#accCrps');
    if (!host) return;
    const bar = $('#accProbBar');
    lead = D && D.lead;
    cal = D && D.cal;
    if ((!lead || !lead.metric) && (!cal || !cal.metric)) {
      if (bar) bar.innerHTML = '';
      ['#accCrps', '#accRel', '#accRes'].forEach(s => { if ($(s)) A.notYet($(s), A.NOT_PUBLISHED); });
      if ($('#accDiag')) A.notYet($('#accDiag'), A.NOT_PUBLISHED);
      return;
    }
    if (bar && !built) controls(bar);
    render();
  }

  return { draw, state: st };
})();
