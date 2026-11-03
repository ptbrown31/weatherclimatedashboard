/* The scorecard: the working paper's Tables 3 and 4.

   Table 3 is one row per series, grouped as the paper groups them, at the six leads of its tables,
   30, 18 and 12 hours before the end of the target day and 18, 12 and 6 hours before the report that
   set the day's extreme. Each cell is the series' mean absolute error with the ForecastEx prediction
   market's percent improvement over it in brackets, in bold where the improvement holds after Holm's
   adjustment across the series. A group's row is the mean over its systems, each on its first series,
   and the groups and the series inside them are ordered by error at 18 hours before the end of the day.
   Table 4 is the CRPS of the market and the four calibrated ensembles on the contracts all five share.

   draw(D) receives the bundle WXAcc.init assembles and reads D.grid. Nothing here is computed. */
window.WXAccGrid = (() => {
  const { h, $ } = WXC;
  const A = () => window.WXAcc;

  const COHORTS = [
    { key: 'all', label: 'Every day on record', title: 'Every city-day the ForecastEx prediction market priced at that hour, each series scored on the ones its own record covers' },
    { key: 'balanced', label: 'Balanced sample', title: 'Only the city-days on which every series holds a value at every lead from 1 to 30 hours before the end of the day' },
  ];
  const VALUES = [
    { key: 'corrected', label: 'Bias-corrected', title: 'The grid-box series with their trailing station offset removed' },
    { key: 'issued', label: 'As issued', title: 'Every series as issued, without the station bias correction' },
  ];
  const LEADS = ['30 h', '18 h', '12 h', '18 h', '12 h', '6 h'];
  /* Each exclusion the builder counts, as a reader reads it. */
  const REASON = {
    backfill_rows: e => int(e.count) + ' forecast rows recorded after the fact rather than when they were available',
    ecmwf_ifs_lag_rows: e => int(e.count) + ' ECMWF ensemble-mean forecasts recorded under five hours after their run',
    no_settlement: e => int(e.count) + ' city-days with no settlement value',
    no_market_median: e => int(e.count) + ' city-days with no market central value at any lead',
    settle_ingest_error: e => int(e.count) + ' city-days at Buckley Field whose recorded settle carries an ingest error',
  };

  const state = { metric: 'high', cohort: 'all', values: 'corrected' };
  let host = null, bar = null, keyEl = null, methodEl = null, grid = null, controlsBuilt = false, valuesGroup = null;
  const fin = v => v != null && isFinite(v);
  const int = n => A().int(n);

  function cell(c, i, withPi) {
    if (!c || !fin(c.mae[i])) return '<td class="na">' + A().dash + '</td>';
    const pi = withPi && fin(c.pi && c.pi[i]) ? ' <span class="pi' + (c.better && c.better[i] ? ' sig' : '') + '">(' + Math.round(c.pi[i]) + '%)</span>' : '';
    return '<td title="' + int(c.n[i]) + ' city-days">' + A().f2(c.mae[i]) + pi + '</td>';
  }
  const signed = v => (fin(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) : A().dash);

  function table3(block, issued) {
    const S = issued || block.series;
    const fx = block.series.FX;
    const head = '<thead><tr><th rowspan="2">Series</th><th colspan="3">Hours before the end of the day</th>'
      + '<th colspan="3">Hours before the extreme</th><th rowspan="2" title="Mean error at 18 h before the end of the day, °F, positive when a series runs warm">ME 18 h</th>'
      + '<th rowspan="2">Offset</th><th rowspan="2">Record since</th></tr><tr>' + LEADS.map(l => '<th>' + l + '</th>').join('') + '</tr></thead>';
    let body = '';
    if (fx) {
      body += '<tr class="acc-g-fx"><td>' + A().swatch('FX') + '</td>' + [0, 1, 2, 3, 4, 5].map(i => cell(fx, i, false)).join('')
        + '<td>' + signed(fx.me18) + '</td><td>as quoted</td><td>' + since('FX') + '</td></tr>';
    }
    // the groups, in the order the file gives them (by the 18 h mean of their systems)
    (block.groups || []).forEach(g => {
      body += '<tr class="acc-g-group"><td>' + g.title + '</td>' + g.mean.map(v => '<td>' + (fin(v) ? A().f2(v) : A().dash) + '</td>').join('')
        + '<td colspan="3"></td></tr>';
      g.series.forEach(id => {
        const c = S[id];
        if (!c) return;
        const corrected = (A().INFO[id] || {}).corrected;
        body += '<tr><td><span class="acc-g-name">' + A().swatch(id) + '</span><span class="acc-g-qual">' + A().qual(id) + '</span></td>'
          + [0, 1, 2, 3, 4, 5].map(i => cell(c, i, true)).join('')
          + '<td>' + signed(c.me18) + '</td><td>' + (issued ? 'as issued' : corrected ? 'corrected' : 'as issued') + '</td><td>' + since(id) + '</td></tr>';
      });
    });
    return '<table class="acc-grid acc-t3">' + head + '<tbody>' + body + '</tbody></table>';
  }

  function table4(block) {
    const cr = block.crps;
    if (!cr || !cr.series || !cr.series.FX) return '';
    const NAMES = { AIFS: 'ECMWF AIFS ensemble', GEFS: 'NOAA GEFS', GEM: 'ECCC GEPS', ICON: 'DWD ICON-EPS' };
    const head = '<thead><tr><th rowspan="2">System</th><th colspan="3">Hours before the end of the day</th><th colspan="3">Hours before the extreme</th></tr><tr>'
      + LEADS.map(l => '<th>' + l + '</th>').join('') + '</tr></thead>';
    let body = '<tr class="acc-g-fx"><td>' + A().swatch('FX') + '</td>' + [0, 1, 2, 3, 4, 5].map(i => cell(cr.series.FX, i, false)).join('') + '</tr>';
    (cr.order || []).forEach(k => {
      const c = cr.series[k];
      if (c) body += '<tr><td>' + NAMES[k] + '</td>' + [0, 1, 2, 3, 4, 5].map(i => cell(c, i, true)).join('') + '</tr>';
    });
    return '<div class="accsub">CRPS, °F, the ensembles calibrated by EMOS, on the contracts all five share</div>'
      + '<table class="acc-grid acc-t4">' + head + '<tbody>' + body + '</tbody></table>';
  }

  function since(id) {
    const sp = A().span(grid.meta, id, state.metric);
    return sp && sp.start ? A().mdyY(sp.start) : A().dash;
  }

  function render() {
    if (valuesGroup) valuesGroup.hidden = state.cohort !== 'all';
    const met = grid.metric && grid.metric[state.metric];
    const block = met && met.cohorts && met.cohorts[state.cohort];
    if (!block || !block.series || !block.series.FX) {
      A().notYet(host, 'This view is not in the published record.');
      renderMethod();
      return;
    }
    const issued = state.cohort === 'all' && state.values === 'issued' ? block.issued : null;
    host.innerHTML = '<div class="accsub">Mean absolute error, °F, with the market’s improvement in brackets</div>'
      + '<div class="acc-grid-wrap">' + table3(block, issued) + '</div>'
      + (state.cohort === 'all' ? '<div class="acc-grid-wrap">' + table4(block) + '</div>' : '');
    if (keyEl) {
      keyEl.innerHTML = '';
      keyEl.appendChild(h('span', { class: 'kn', text: 'An improvement in bold holds after Holm’s adjustment across the series. A group’s row is the mean over its systems, each on its first series. Hover a cell for its city-days.' }));
    }
    renderMethod();
  }

  function renderMethod() {
    if (!methodEl) return;
    const meta = grid.meta || {};
    const coh = (meta.cohorts || {})[state.cohort] || {};
    const excl = Array.isArray(meta.exclusions) ? meta.exclusions.filter(e => REASON[e.reason]) : [];
    const exclText = excl.length
      ? 'Excluded before scoring, ' + excl.map(e => REASON[e.reason](e) + (fin(e.dates) ? ' (' + int(e.dates) + ' date' + (e.dates === 1 ? '' : 's') + ')' : '')).join('; ') + '.'
      : null;
    const rules = [
      'An alternative forecast system’s value at a lead is its most recent forecast at or before that moment, held at the running observed extreme. The ForecastEx prediction market’s value is its median, where its ladder of Yes prices crosses fifty cents, left as quoted. The settle is the station’s highest or lowest METAR reading of the day rounded to the nearest whole degree.',
      'Every series is scored against its own standard, as in the error chart above, and the market against the METAR settle. Each series is scored on the city-days its own record covers, and each improvement on the city-days the market and the series share.',
      'The fifteen grid-box series carry the trailing station bias correction and the rest are scored as issued, as the Offset column marks. The As issued view scores every series without it.',
      'Five systems contribute two series each, which gives 25 series across 20 systems. A group’s row is the mean over its systems, each on its first series, and the groups and the series inside them are ordered by error at 18 hours before the end of the day.',
      'For CRPS the ForecastEx prediction market’s distribution is its ladder of Yes prices, as quoted, forced monotone across strikes and closed at the end strikes. Every ensemble is calibrated by EMOS and scored against the METAR settle, on the contracts and city-day hours the market and all four ensembles share.',
      'An improvement is in bold when the paired difference in error is below zero at the 5 percent level after Holm’s adjustment across the series compared at that lead, over 1,000 resamples of seven-day blocks of target dates.',
    ];
    if (exclText) rules.push(exclText);
    const n = 'Sample ' + int(coh.n_high) + ' city-days on highs and ' + int(coh.n_low) + ' on lows'
      + (state.cohort === 'all' ? ' on the ForecastEx prediction market’s record' : ' in the balanced sample')
      + '. ' + A().windowAndBuilt(meta) + '.';
    A().methodNote(methodEl, {
      title: 'Scoring',
      body: [
        'Mean absolute error and mean error are both in degrees Fahrenheit, on the daily high and the daily low. Mean error is signed, positive when a system runs warm, and is given at 18 hours before the end of the day.',
        { tex: 'PI_s = 100 \\times \\left(1 - \\frac{MAE(\\text{ForecastEx vs. METAR})}{MAE(s \\text{ vs. its own standard})}\\right)' },
        'A positive improvement means the market was closer to the observation it is judged on than the series was to the observation it is judged on.',
        { tex: 'CRPS = \\sum_{k} \\left(F(k) - \\mathbb{1}[\\text{settle} \\le k]\\right)^2' },
        'On highs, $F(k) = 1 - P(k)$, on lows, $F(k-1) = P(k)$, where $P(k)$ is the probability the contract at strike $k$ pays, the Yes price for the ForecastEx prediction market and the calibrated distribution for an ensemble. CRPS scores the whole distribution.',
        'The paper scores the daily high. The lows are scored here with the same methods.',
      ],
      rules, n, span: A().cohortSpanLine(meta, state.cohort, state.metric),
    });
  }

  function buildControls() {
    if (controlsBuilt || !bar) return;
    controlsBuilt = true;
    bar.innerHTML = '';
    A().metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A().tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Days' });
    A().tabs(bar, VALUES, k => { state.values = k; render(); }, { initial: state.values, label: 'Values' });
    valuesGroup = bar.lastElementChild;
  }

  function draw(D) {
    host = $('#accGrid'); bar = $('#accGridBar'); keyEl = $('#accGridKey'); methodEl = $('#accGridMethod');
    if (!host) return;
    grid = (D && D.grid) || null;
    if (!grid || !grid.metric) {
      A().notYet(host, A().unpublished(D));
      if (keyEl) keyEl.innerHTML = '';
      if (methodEl) methodEl.innerHTML = '';
      return;
    }
    buildControls();
    render();
  }

  return { draw, state };
})();
