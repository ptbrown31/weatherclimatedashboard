/* Probabilistic skill: the whole distribution, as the working paper's Fig 9 draws it.

   From calibration.json (docs/accuracy.md section 3): the CRPS of the ForecastEx prediction market's
   price ladder and of the four ensembles by lead, on either clock, and the reliability diagrams of
   the paper's two windows with their summaries. The ensembles are calibrated by EMOS, with their raw
   distributions drawn faint behind; the As issued view draws them raw. The market's ladder is used
   as quoted. Nothing here is computed. */
window.WXAccProb = (() => {
  const { $, el, txt, h } = WXC;
  const A = WXAcc;

  const VALUES = [
    { key: 'calibrated', label: 'EMOS-calibrated', title: 'The ensembles calibrated by EMOS on the preceding 30 days, their raw distributions drawn faint behind' },
    { key: 'issued', label: 'As issued', title: 'The ensembles’ own distributions, without calibration' },
  ];
  const CLOCKS = [
    { key: 'abs', label: 'Before the day ends', title: 'Lead counted back from station-local midnight at the end of the target day' },
    { key: 'rel', label: 'Before the extreme', title: 'Lead counted back from the last report that reached the day’s extreme, using only the hours before it' },
  ];
  // AIFS in the AI model's purple, the other three in the raw models' tan with a dash each
  const ENS = ['AIFS', 'GEFS', 'GEM', 'ICON'];
  const ENS_COLOR = { AIFS: 'var(--g-ai)', GEFS: 'var(--g-raw)', GEM: 'var(--g-raw)', ICON: 'var(--g-raw)' };
  const ENS_DASH = { AIFS: null, GEFS: '5 3', GEM: '2 2', ICON: '7 3 2 3' };

  const st = { metric: 'high', values: 'calibrated', clock: 'abs' };
  let cal = null, built = false;
  const fin = v => v != null && isFinite(v);
  const ensName = k => (cal && cal.ensembles && cal.ensembles[k] && cal.ensembles[k].name) || k;
  const colorOf = k => (k === 'FX' ? A.color('FX') : ENS_COLOR[k]);
  const nameOf = k => (k === 'FX' ? 'ForecastEx prediction market' : ensName(k));
  const sw = k => '<span class="sw" style="background:' + colorOf(k) + '"></span>' + nameOf(k);

  // ------------------------------------------------------------- CRPS by lead
  function drawCrps(svg) {
    const m = cal.metric && cal.metric[st.metric];
    const ck = m && m.crps && m.crps.clocks && m.crps.clocks[st.clock];
    const blk = ck && ck[st.values];
    if (!blk || !blk.series || !blk.series.FX) { A.notYet(svg, 'This view is not in the published record.'); return null; }
    const bins = ck.bins;
    const at = arr => (arr || []).map(v => (fin(v) ? v : null));
    const S = [];
    if (st.values === 'calibrated' && ck.issued) {
      ENS.forEach(k => { const d = ck.issued.series[k]; if (d) S.push({ id: k, v: at(d.mae), n: at(d.n), opacity: 0.22, width: 1.1, dash: ENS_DASH[k] }); });
    }
    ['FX'].concat(ENS).forEach(k => {
      const d = blk.series[k];
      if (!d) return;
      S.push({ id: k, v: at(d.mae), lo: at(d.lo), hi: at(d.hi), n: at(d.n), pi: d.pi || [], better: d.better || [],
               smooth: k === 'FX', dash: ENS_DASH[k] || null, width: k === 'FX' ? null : 1.8 });
    });
    // the chart colours a series by its id, which gives the AI model its purple and the other three the
    // raw models' tan, as the ensemble medians are coloured in the error chart
    const fxS = S.find(s => s.id === 'FX' && s.opacity == null);
    const rel = st.clock === 'rel';
    const ext = st.metric === 'high' ? 'maximum' : 'minimum';
    const beats = blk.beats || [];
    {
      A.leadChart(svg, {
        H: 400, hs: bins, series: S, floor: 0.8, fmt: v => A.f2(v) + '°', label: 'CRPS, °F (lower is better)',
        name: true, xLabel: rel ? 'Hours before the day’s ' + ext + ' was observed' : 'Hours before the end of the target day',
        dayLine: !rel, hatch: !rel, steps: false,
        strips: { n: fxS.n, beats, ofLabel: 'ensembles' },
        tip: (i, b) => {
          const rows = S.filter(s => s.opacity == null).map(s => ({ id: s.id, v: s.v[i], n: s.n[i], pi: (s.pi || [])[i], better: (s.better || [])[i] }));
          rows.sort((a, c) => (fin(a.v) ? a.v : Infinity) - (fin(c.v) ? c.v : Infinity));
          let body = '';
          rows.forEach((r, k) => {
            const pi = r.id === 'FX' || !fin(r.pi) ? A.dash : (r.better ? '<b>' + A.f1(r.pi) + '%</b>' : A.f1(r.pi) + '%');
            body += '<tr' + (r.id === 'FX' ? ' class="tfx"' : '') + '><td>' + (k + 1) + '</td><td>' + sw(r.id) + '</td><td>'
              + A.f2(r.v) + '</td><td>' + pi + '</td><td>' + A.int(r.n) + '</td></tr>';
          });
          const title = rel ? (b === 1 ? 'The hour before the ' + ext : b + ' hours before the ' + ext) : A.leadTitle(b);
          const bt = beats[i];
          return '<b>' + title + '</b><div class="tsub">' + (st.metric === 'high' ? 'Highs' : 'Lows') + ', '
            + (st.values === 'calibrated' ? 'ensembles calibrated by EMOS' : 'ensembles as issued') + '.'
            + (bt && fin(bt.k) ? ' Better than ' + bt.k + ' of ' + bt.of + ' ensembles after Holm.' : '') + '</div>'
            + '<table class="l3"><tr><th>#</th><th>System</th><th>CRPS °F</th><th>Market better</th><th>n</th></tr>' + body + '</table>'
            + '<div class="tf">Every row is scored on the contracts and city-days the market and all four ensembles share. The improvement is in bold where it holds after Holm.</div>';
        },
      });
    }
    return S;
  }

  // ------------------------------------------------------------- reliability
  /* The two windows of the paper, side by side: contracts priced 12 to 24 hours before the end of
     the target day, and 6 to 18 hours before the extreme. The market's circles have area in
     proportion to their contracts; each ensemble is a line. No interval is drawn per bin, because
     contracts on one city-day share a single outcome. */
  function diagrams(host) {
    host.innerHTML = '';
    const m = cal.metric && cal.metric[st.metric];
    const relb = m && m.reliability;
    if (!relb || !relb.abs || !relb.abs.calibrated || !relb.abs.calibrated.FX) {
      A.notYet(host, 'This view is not in the published record.');
      return;
    }
    const PW = 330, TOP = 44, H = PW + 92;
    const svg = el('svg', { viewBox: '0 0 960 ' + H, class: 'acc-cal-rel' });
    const ext = st.metric === 'high' ? 'maximum' : 'minimum';
    const tag = st.values;
    const panels = [['abs', 'Priced ' + relb.abs.window[0] + ' to ' + relb.abs.window[1] + ' h before the day ends'],
                    ['rel', 'Priced ' + relb.rel.window[0] + ' to ' + relb.rel.window[1] + ' h before the ' + ext]];
    let maxCount = 1;
    panels.forEach(([w]) => ((relb[w].calibrated.FX || {}).bins || []).forEach(b => { if (b[2] > maxCount) maxCount = b[2]; }));
    panels.forEach(([w, title], i) => {
      const g = { L: 70 + i * (PW + 150), T: TOP };
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
        svg.appendChild(txt(String(Math.round(v * 100)), { x: g.L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
      });
      const blk = relb[w];
      const lineOf = (d, col, dash, op) => {
        const xs = [], ys = [];
        ((d && d.bins) || []).forEach(b => { if (fin(b[0]) && fin(b[1])) { xs.push(x(b[0])); ys.push(y(b[1])); } });
        if (xs.length > 1) A.lineSeries(svg, xs, ys, Object.assign({ stroke: col, 'stroke-width': op ? 1.1 : 1.6 },
                                                                    dash ? { 'stroke-dasharray': dash } : {}, op ? { 'stroke-opacity': op } : {}));
        if (!op) A.dots(svg, xs, ys, { fill: col, r: 2 });
      };
      if (tag === 'calibrated') ENS.forEach(k => lineOf(blk.issued[k], ENS_COLOR[k], ENS_DASH[k], 0.25));
      ENS.forEach(k => lineOf(blk[tag][k], ENS_COLOR[k], ENS_DASH[k], null));
      const fx = blk.calibrated.FX;
      (fx.bins || []).forEach(b => {
        if (!fin(b[0]) || !fin(b[1])) return;
        const rad = 2.2 + 8 * Math.sqrt(b[2] / maxCount);
        svg.appendChild(el('circle', { cx: x(b[0]), cy: y(b[1]), r: rad, fill: A.color('FX'), 'fill-opacity': 0.45,
                                       stroke: A.color('FX'), 'stroke-width': 1.2, 'pointer-events': 'none' }));
        const hit = el('circle', { cx: x(b[0]), cy: y(b[1]), r: Math.max(rad + 3, 9), fill: 'none', 'pointer-events': 'all' });
        A.hover(hit, () => A.tooltip().rows('ForecastEx, mean price ' + Math.round(b[0] * 100) + ' c', [
          ['Share that paid', A.pct1(b[1])], ['Contracts', A.int(b[2])], ['Window', title]], ''));
        svg.appendChild(hit);
      });
      svg.appendChild(txt(title, { x: g.L, y: 14, 'font-size': 11.5, 'font-weight': 700, fill: 'var(--ink)' }));
      svg.appendChild(txt(A.int((fx.summary || {}).n) + ' ForecastEx contracts', { x: g.L, y: 30, class: 'ax' }));
      svg.appendChild(txt('Yes price, cents', { x: (g.L + g.R) / 2, y: g.B + 36, 'text-anchor': 'middle', class: 'ax' }));
      svg.appendChild(txt('Share that paid, percent', { x: g.L - 40, y: (g.T + g.B) / 2, 'text-anchor': 'middle',
                                                        transform: 'rotate(-90 ' + (g.L - 40) + ' ' + (g.T + g.B) / 2 + ')', class: 'ax' }));
    });
    host.appendChild(svg);
    host.appendChild(summaryTable(relb, tag, ext));
  }

  function summaryTable(relb, tag, ext) {
    const wrap = h('div', { class: 'acc-sources-wrap' });
    const t = h('table', { class: 'acc-rel-sum' });
    t.innerHTML = '<thead><tr><th>System</th><th>Mean gap, ' + relb.abs.window[0] + ' to ' + relb.abs.window[1] + ' h</th><th>Slope</th>'
      + '<th>Mean gap, ' + relb.rel.window[0] + ' to ' + relb.rel.window[1] + ' h before the ' + ext + '</th><th>Slope</th></tr></thead>';
    const tb = h('tbody');
    ['FX'].concat(ENS).forEach(k => {
      const cell = w => {
        const d = (k === 'FX' ? relb[w].calibrated.FX : relb[w][tag][k]) || {};
        const s = d.summary || {};
        const iv = fin(s.slopeLo) && fin(s.slopeHi) ? ' [' + A.f2(s.slopeLo) + ', ' + A.f2(s.slopeHi) + ']' : '';
        return '<td>' + (fin(s.mad) ? A.f3(s.mad) : A.dash) + '</td><td>' + (fin(s.slope) ? A.f2(s.slope) + iv : A.dash) + '</td>';
      };
      const tr = h('tr');
      tr.innerHTML = '<td>' + sw(k) + '</td>' + cell('abs') + cell('rel');
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    wrap.appendChild(t);
    return wrap;
  }

  // ------------------------------------------------------------- render
  function render() {
    const crps = $('#accCrps'), diag = $('#accDiag'), keyEl = $('#accProbKey'), meth = $('#accProbMethod');
    if (crps) drawCrps(crps);
    if (diag) diagrams(diag);
    if (keyEl) {
      keyEl.innerHTML = '';
      ['FX'].concat(ENS).forEach(k => {
        const e = h('span');
        e.appendChild(h('i', { style: 'border-color:' + colorOf(k) + (k === 'FX' ? ';border-top-width:3px' : '')
                                     + (ENS_DASH[k] ? ';border-top-style:dashed' : '') }));
        e.appendChild(document.createTextNode(nameOf(k)));
        keyEl.appendChild(e);
      });
      keyEl.appendChild(h('span', { class: 'kn', text: (st.values === 'calibrated' ? 'Faint lines are the ensembles as issued, before calibration. ' : '')
        + 'The band is the ForecastEx prediction market’s 95 percent interval. On the diagrams its circles have area in proportion to their contracts.' }));
    }
    method(meth);
  }

  function method(meth) {
    if (!meth) return;
    const meta = cal.meta || {};
    const m = cal.metric && cal.metric[st.metric];
    const span = m && m.crps && m.crps.span;
    const emos = (meta.emos || (meta.notes || {}).emos) || {};
    A.methodNote(meth, {
      title: 'CRPS and the reliability diagrams',
      body: [
        'CRPS, the continuous ranked probability score, measures a whole forecast distribution against what happened, in degrees. It shrinks as probability gathers near the observed value, and a forecast that puts all its probability on one whole degree scores its absolute error, so it reads on the same scale as the mean absolute error above.',
        { tex: 'CRPS_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\sum_{k} \\left( F_{s,i,h}(k) - \\mathbb{1}[o_i \\le k] \\right)^2' },
        'where $F_{s,i,h}(k)$ is system $s$’s probability that city-day $i$ settles at or below $k$ as it stood at lead $h$, $k$ runs over whole degrees across the ForecastEx prediction market’s strikes, and $o_i$ is the settle, the station’s highest or lowest METAR reading of the day rounded to the nearest whole degree. Every system here is scored against the settle.',
        'For the ForecastEx prediction market the distribution is its price ladder. CRPS reads the midpoint ladder as quoted, forced monotone across strikes by pooling violations and closed at the end strikes, each price held to the exchange’s range of 1 to 99 cents, and the diagrams read each contract’s own Yes price.',
        'For the ForecastEx prediction market $p$ is the contract’s Yes price, with both sides bid',
        { tex: 'p = \\frac{\\text{Yes bid} + (1 - \\text{No bid})}{2}' },
        'and with one side bid, the midpoint against the empty side at its limit, a missing Yes bid counting as 1 cent and a missing No bid as a 99-cent ask. A lone side at that limit is an empty book.',
        'For an ensemble the distribution is built from each remaining hour’s ensemble mean and member spread, each hour a normal distribution, with the hourly errors taken as perfectly correlated. A high exceeds a strike when one of the hours left does, so the chance it pays is read from the hour that comes closest, and a low is the mirror. That is the Fréchet bound for the extreme of dependent variables, and it is the more conservative assumption for the market, since independence would make the ensembles look falsely sharp.',
        'The ensembles are calibrated by ensemble model output statistics, EMOS. For each ensemble, station and lead window a normal distribution whose mean is a linear function of the ensemble mean and whose variance is a linear function of the ensemble variance is fitted by minimising CRPS over the preceding 30 days, on the forecast as it is scored, held at the extreme already observed. The fit is applied to every remaining hour. ' + (fin(emos.fits) ? A.int(emos.fits) + ' fits are in this build, ' + A.int(emos.implausible) + ' set aside as implausible.' : ''),
        'A contract’s price should equal the probability it pays off. A reliability diagram plots the average price in a ten-cent bucket against the share of the bucket’s contracts that paid, and honest prices fall on the diagonal. The table gives two summaries per window. The mean gap is the count-weighted mean absolute difference between price and payout share over the ten buckets, and the slope is that of a logistic regression of outcomes on the log-odds of the price, which equals one for calibrated prices.',
        'The paper scores the daily high. The lows are scored here with the same methods.',
      ],
      rules: [
        'All five are read at the same instant on the same strikes. Lead counts down to station-local midnight, the moment the target day ends, or in the Before the extreme view to the last report that reached the day’s extreme; the ForecastEx prediction market is read on the last ladder snapshot of each hour and an ensemble on its most recent capture at or before that hour.',
        'Every CRPS line is drawn on the contracts and city-day hours the ForecastEx prediction market and all four ensembles share, so every line is scored on the same contracts and the same days. The diagrams take each strike’s last price in its hour on the city-days all four ensembles cover.',
        'Bands are 95 percent intervals over 1,000 resamples of seven-day blocks of target dates, and a lead with under 30 city-days is not drawn. The beats strip counts the ensembles the market is better than, its paired difference in CRPS below zero at the 5 percent level after Holm’s adjustment across the four. The market’s slope carries its interval from the same resamples, and the ensembles’ slopes stand alone, as the paper gives them.',
        'No interval is drawn on a bucket, because contracts on the same city-day share one outcome and are not independent, which makes intervals from resampling whole weeks about three times as wide as binomial ones.',
      ],
      span: span && span.start ? 'Scored from ' + A.mdyY(span.start) + ' to ' + A.mdyY(span.end) + ', ' + A.int(span.days) + ' days on which the market and all four ensembles share contracts.' : '',
      n: 'Sample ' + A.int(nContracts(m)) + ' ForecastEx contracts in the two diagram windows. ' + A.windowAndBuilt(meta) + '.',
    });
  }

  // the market's contracts in the two reliability windows, the diagrams' sample
  function nContracts(m) {
    const r = (m && m.reliability) || {};
    return ['abs', 'rel'].reduce((t, w) => {
      const s = r[w] && r[w].calibrated && r[w].calibrated.FX && r[w].calibrated.FX.summary;
      return t + (s && fin(s.n) ? s.n : 0);
    }, 0);
  }

  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { st.metric = k; render(); }, st.metric);
    A.tabs(bar, CLOCKS, k => { st.clock = k; render(); }, { initial: st.clock, label: 'Lead' });
    A.tabs(bar, VALUES, k => { st.values = k; render(); }, { initial: st.values, label: 'Ensembles' });
    built = true;
  }

  function draw(D) {
    const host = $('#accCrps');
    if (!host) return;
    const bar = $('#accProbBar'), keyEl = $('#accProbKey'), meth = $('#accProbMethod');
    cal = D && D.cal;
    if (!cal || !cal.metric) {
      if (bar) bar.innerHTML = '';
      if (keyEl) keyEl.innerHTML = '';
      if (meth) meth.innerHTML = '';
      A.notYet(host, A.unpublished(D));
      if ($('#accDiag')) A.notYet($('#accDiag'), A.unpublished(D));
      return;
    }
    if (bar && !built) controls(bar);
    render();
  }

  return { draw, state: st };
})();
