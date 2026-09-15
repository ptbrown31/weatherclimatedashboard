/* Probabilistic skill: the whole distribution, scored by lead.

   The systems that publish a distribution, the ForecastEx prediction
   market's price ladder and the four ensembles, scored on the same strikes
   at the same instant. Two charts on the lead curve's axis, each the full
   width of the page and drawn by WXAcc.leadChart:

     CRPS, the score of the whole distribution in degrees (lead-curve.json,
       metric.<m>.crps); and
     the Brier score, contract by contract, drawn as its square root in cents
       (calibration.json, price.<rule>.brier), over every quoted strike or the
       near-money strikes only;

   then the reliability diagrams, one per lead bin. Nothing here is computed;
   the module chooses which of the builder's series to draw. The Days and
   Frame tabs reach every chart; the Price tab changes the price rule the
   Brier score and the diagrams read, and CRPS always reads the ladder as
   quoted; the Strikes tab reaches the Brier score only. */
window.WXAccProb = (() => {
  const { el, txt, $ } = WXC;
  const A = WXAcc;

  const COHORTS = [
    { key: 'own', label: 'Every shared day', title: 'Every city-day and hour at which the ForecastEx prediction market and all four ensembles hold a value, so every line is drawn on the same contracts' },
    { key: 'fixed30', label: 'Fixed sample', title: 'Those shared days restricted to the city-days the ForecastEx prediction market priced at every hour from 30 to 0' },
  ];
  const PRICE = [
    { key: 'mid', label: 'Yes price midpoint', title: 'The Brier score and the diagrams read the midpoint of the Yes bid and one dollar less the No bid; with one side bid, the midpoint against the empty side at its limit, a missing Yes bid counting as 1 c and a missing No bid as a 99 c ask' },
    { key: 'twoSided', label: 'Two-sided books only', title: 'The Brier score and the diagrams read the midpoint on books with a bid on both sides only' },
  ];
  const STRIKES = [
    { key: 'all', label: 'Every quoted strike', title: 'The Brier score over every strike the ForecastEx prediction market quoted at that hour' },
    { key: 'nearMoney', label: 'Near-money strikes', title: 'The Brier score over the middle listed strike of the day’s ladder and the strike on either side, where the outcome is least settled' },
  ];
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'Every system scored against the settle the contracts pay on' },
    { key: 'cli', label: 'NWS climate report', title: 'The ensembles scored against the National Weather Service climate report for the same date; the ForecastEx prediction market keeps the settle it pays on' },
  ];
  const st = { metric: 'high', cohort: 'own', price: 'mid', frame: 'metar', strikes: 'all' };
  let lead = null, cal = null, built = false;

  const fin = v => v != null && isFinite(v);
  const cents1 = v => (fin(v) ? (Math.round(v * 1000) / 10).toFixed(1) + ' c' : A.dash);
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

  // ------------------------------------------------------------- Brier score
  function calBlocks() {
    const m = cal && cal.metric && cal.metric[st.metric];
    const co = m && m.cohorts && m.cohorts[st.cohort];
    const fx = co && co.price && co.price[st.price];
    const ensAll = (co && co.ensembles) || {};
    // an ensemble's diagram cells sit under the price rule, on that rule's contracts
    const cells = (fx && fx.ensembles) || {};
    const ens = ordered(Object.keys(ensAll)).map(e => ({ id: e.id, name: ensAll[e.key].name || A.name(e.id),
                                                          cells: (cells[e.key] || {})[st.frame] || null }))
      .filter(e => Array.isArray(e.cells));
    return { fx, ens };
  }

  /* The Brier score by lead, drawn as its square root in cents so it reads as
     the typical gap between a contract's price and what it paid. Every
     system is scored on the market's own contracts at the hour. */
  function drawBrier(svg) {
    const { fx } = calBlocks();
    const b = fx && fx.brier;
    const v = b && b.strikes && b.strikes[st.strikes] && b.strikes[st.strikes][st.frame];
    if (!v || !v.systems || !v.systems.FX) { A.notYet(svg, 'The Brier score is not in the published record.'); return; }
    const hs = b.h;
    const clean = arr => hs.map((_, i) => (arr && fin(arr[i]) ? arr[i] : null));
    const ens = ordered(Object.keys(v.systems).filter(k => k !== 'FX'));
    const sys = v.systems;
    const S = [{ id: 'FX', smooth: true, v: clean(sys.FX.rms), lo: clean(sys.FX.lo), hi: clean(sys.FX.hi), n: sys.FX.n || [], brier: sys.FX.brier || [] }]
      .concat(ens.map(e => ({ id: e.id, dash: A.ensDash(e.id), width: 1.8, v: clean(sys[e.key].rms), n: sys[e.key].n || [], brier: sys[e.key].brier || [] })));
    const beats = v.beats || [];
    const strikesName = STRIKES.find(x => x.key === st.strikes).label.toLowerCase();
    A.leadChart(svg, {
      H: 400, hs, series: S, floor: 0.1, fmt: x => Math.round(x * 100) + ' c', name: true,
      label: 'Brier score as root mean square, cents (lower is better)',
      strips: { n: sys.FX.days || [], beats },
      tip: (i, hh) => {
        const bt = beats[i], d = (sys.FX.days || [])[i];
        let sub = view() + ', ' + priceName() + ', ' + strikesName + '. ' + (fin(d) ? A.int(d) + ' city-days' : 'no sample') + '.';
        if (bt && fin(bt.k)) sub += ' ForecastEx beats ' + bt.k + ' of ' + bt.of + ' ensembles.';
        const f = S[0];
        const foot = (fin(f.lo[i]) && fin(f.hi[i]) ? 'ForecastEx ' + A.iv(f.lo[i], f.hi[i], cents1) + ', 95 percent interval. ' : '')
          + 'n is the number of contracts. The Brier score itself is the square of the value shown, ForecastEx '
          + A.f3(f.brier[i]) + ' at this hour.';
        return A.rankTip(A.leadTitle(hh), sub, S.map(x => ({ id: x.id, v: x.v[i], n: x.n[i] })), 'RMS', cents1, { foot });
      },
    });
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
    const PW = n <= 3 ? 250 : 200;
    const H = PW + 80, TOP = 40, GAP = (960 - 46 - 16 - PW * n) / Math.max(n - 1, 1);
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
        const er = e.cells[i] || {};
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
      ['95 percent Wilson interval', A.iv(r.lo && r.lo[k], r.hi && r.hi[k], A.pct1)],
      ['Contracts' + (pooled ? ' (pooled)' : ''), A.int(cnt)],
      ['Share of all contracts', A.pct1(r.hist && r.hist[k])],
      ['Lead bin', bin ? bin[0] + ' to ' + bin[1] + ' h' : A.dash],
    ], pooled ? 'This decile carries the contracts of a thinner neighbor pooled toward 50 c.' : '');
  }

  // ------------------------------------------------------------- render
  function render() {
    const crps = $('#accCrps'), brier = $('#accBrier'), diag = $('#accDiag');
    const keyEl = $('#accProbKey'), meth = $('#accProbMethod');
    const ids = crps ? drawCrps(crps) : [];
    if (brier) drawBrier(brier);
    if (diag) diagrams(diag);
    const { ens } = calBlocks();
    const keyIds = ids.length ? ids : ['FX'].concat(ens.map(e => e.id));
    if (keyEl) {
      const m = cal && cal.metric && cal.metric[st.metric];
      const sh = m && m.cohorts && m.cohorts[st.cohort] && m.cohorts[st.cohort].shared;
      A.key(keyEl, keyIds, { dashes: true, since: () => '',
        note: (sh && sh.start ? 'Every line is drawn on the city-days all five share, ' + A.mdy(sh.start) + ' to ' + A.mdy(sh.end) + ', ' + A.int(sh.days) + ' days. ' : '')
          + 'Bands are the ForecastEx prediction market’s 95 percent bootstrap interval; in the diagrams marker area is the contract count and the bar the 95 percent Wilson interval.' });
    }
    method(meth);
  }

  // which days every line in the view was drawn on
  function sharedLine() {
    const m = cal && cal.metric && cal.metric[st.metric];
    const sh = m && m.cohorts && m.cohorts[st.cohort] && m.cohorts[st.cohort].shared;
    if (!sh || !sh.start) return '';
    return 'Every line is drawn on the ' + A.int(sh.days) + ' days from ' + A.mdyY(sh.start) + ' to ' + A.mdyY(sh.end)
      + ' on which the ForecastEx prediction market and all four ensembles hold a value'
      + (st.cohort === 'fixed30' ? ', within the fixed sample.' : '.');
  }

  function method(meth) {
    if (!meth) return;
    const meta = (lead && lead.meta) || (cal && cal.meta) || {};
    const { fx } = calBlocks();
    const rel = (fx && fx.reliability) || [];
    const nc = rel.reduce((s, r) => s + (fin(r.n) ? r.n : 0), 0);
    A.methodNote(meth, {
      title: 'CRPS, the Brier score and the reliability diagrams',
      body: [
        'CRPS, the continuous ranked probability score, measures a whole forecast distribution against what happened, in degrees. It shrinks as probability gathers near the observed value, and a forecast that puts all its probability on one whole degree scores its absolute error, so it reads on the same scale as the mean absolute error above.',
        { tex: 'CRPS_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\sum_{k} \\left( F_{s,i,h}(k) - \\mathbb{1}[o_i \\le k] \\right)^2' },
        'where $F_{s,i,h}(k)$ is system $s$’s probability that city-day $i$ settles at or below $k$ as it stood at lead $h$, $k$ runs over whole degrees across the ForecastEx prediction market’s strikes, and $o_i$ is the settle, the station’s highest or lowest hourly METAR reading of the day rounded to the nearest whole degree.',
        'The Brier score takes the same squared gaps contract by contract instead of adding them up over a ladder, so it reads in probability rather than in degrees and every contract counts the same whatever its ladder. It is drawn as its square root, in cents, the typical gap between a contract’s price and what the contract paid, 0 or 100 cents.',
        { tex: 'BS_s(h) = \\frac{1}{N_h}\\sum_{j=1}^{N_h} (p_{s,j} - y_j)^2 \\qquad \\text{drawn as } 100\\sqrt{BS_s(h)} \\text{ cents}' },
        'where $p_{s,j}$ is system $s$’s probability that contract $j$ pays and $y_j$ is 1 when it paid. Summed over every one-degree strike of a ladder the Brier score would be CRPS again, which is why it is averaged per contract here. Pricing every contract at 50 cents scores 50 cents; a system that knew every outcome would score zero. Rankings are the same as on the Brier score itself.',
        'For the ForecastEx prediction market $p$ is the contract\u2019s Yes price, with both sides bid',
        { tex: 'p = \\frac{\\text{Yes bid} + (1 - \\text{No bid})}{2}' },
        'and with one side bid, the midpoint against the empty side at its limit, a missing Yes bid counting as 1 cent and a missing No bid as a 99-cent ask. A lone side at that limit is an empty book.',
        'A contract’s price should equal the probability it pays off. A reliability diagram plots the average price in a ten-cent bucket against the share of the bucket’s contracts that paid, and honest prices fall on the diagonal.',
      ],
      rules: [
        'For the ForecastEx prediction market the distribution is its price ladder. The Brier score and the diagrams read each contract\u2019s Yes price under the rule the Price tab selects, the midpoint above or two-sided books only. CRPS always reads the midpoint ladder, forced monotone across strikes by pooling violations and closed at the end strikes.',
        'For an ensemble the distribution is read over the hours of the day still to come. Its centre is the highest (for a low, the lowest) of the ensemble’s hourly means from that moment to the end of the day, its spread is the members’ spread at the hour that mean falls on, and the day’s extreme is the more extreme of that and what has already been observed. Any strike the observations have not cleared pays only if the hours left reach it, and a contract pays when the unrounded extreme reaches half a degree past the strike, which is how settlement rounds. Nothing is fitted and no bias is removed.',
        'Two caveats belong with the ensembles. A model publishes a spread for each hour, not for the extreme of several hours, so reading the spread at the hour of the extreme as the extreme’s is an approximation this page makes. And the level bias in each ensemble’s centre, which the mean error in the scorecard shows, passes straight into its probability.',
        'Every system is scored alike. A contract the observations have already settled is scored at 100 cents for every system, and every other probability is held to the exchange’s range of 1 to 99 cents, so no system is credited with a certainty a quote cannot express.',
        'All five are read at the same instant on the same strikes. Lead counts down to station-local midnight, the moment the target day ends; the ForecastEx prediction market is read on the last ladder snapshot of each hour and an ensemble on its most recent capture at or before that hour. Every chart and diagram uses only the contracts the ForecastEx prediction market quoted at hours when all four ensembles hold a reading, so every line is drawn on the same contracts and the same days. CRPS also needs the ladder\u2019s median to be defined at that hour, so its city-day counts run slightly under the Brier score\u2019s.',
        'Near-money strikes are the middle listed strike of the day’s ladder and the strike on either side. The middle is fixed by the listing, before any lead is scored and whatever any system forecast, so it picks the same contracts for every system.',
        'Two sets of days are available, every day all five share, or those restricted to the fixed sample the ForecastEx prediction market priced at every hour from 30 to 0. Thin order books, dates with too few price snapshots and days with gaps in the observation record are excluded, and the counts are listed in the details under the scorecard.',
        'The diagrams pool a bucket under 50 contracts toward the 50-cent bucket. The last six hours have no diagram, since by then most contracts are settled or priced at a cent.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates, and a lead with under 30 city-days is not drawn. The beats strips count the ensembles the ForecastEx prediction market beat at that hour, meaning the 95 percent interval of the paired difference over the days both hold, resampled under the same draws, lies entirely in its favor.',
      ],
      span: sharedLine()
        + (st.frame === 'cli' ? ' In the climate-report frame each ensemble is scored against the National Weather Service climate report for the same date, a different definition of the day’s extreme that runs about a degree warmer on highs; the ForecastEx prediction market keeps the settle it pays on, and both are restricted to the city-days that hold a report, so Buckley Field, which has no climate report of its own, drops out.' : ''),
      n: 'Sample ' + A.int(nc) + ' ForecastEx contracts across the three diagram lead bins, ' + view().toLowerCase() + ', ' + priceName() + '. ' + A.windowAndBuilt(meta) + '.',
    });
  }

  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { st.metric = k; render(); }, st.metric);
    A.tabs(bar, COHORTS, k => { st.cohort = k; render(); }, { initial: st.cohort, label: 'Days' });
    A.tabs(bar, PRICE, k => { st.price = k; render(); }, { initial: st.price, label: 'Price' });
    A.tabs(bar, FRAMES, k => { st.frame = k; render(); }, { initial: st.frame, label: 'Frame' });
    A.tabs(bar, STRIKES, k => { st.strikes = k; render(); }, { initial: st.strikes, label: 'Strikes' });
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
      ['#accCrps', '#accBrier'].forEach(s => { if ($(s)) A.notYet($(s), A.NOT_PUBLISHED); });
      if ($('#accDiag')) A.notYet($('#accDiag'), A.NOT_PUBLISHED);
      return;
    }
    if (bar && !built) controls(bar);
    render();
  }

  return { draw, state: st };
})();
