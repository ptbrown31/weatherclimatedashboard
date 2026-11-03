/* Deterministic skill: one value per series, scored by lead, as the working paper's Fig 6 draws it.

   One chart from lead-curve.json (docs/accuracy.md section 3): the mean absolute error of the
   ForecastEx prediction market's central value and of every alternative series, each against its
   own standard, on one of two clocks. Lead counts back from the end of the target day, or from the
   report that set the day's extreme. Each grid-box series carries the trailing station bias
   correction, with its values as issued drawn faint behind it; the As issued view draws every
   series without the correction. Nothing here is computed; the module chooses which of the
   builder's series to draw. */
window.WXAccLead = (() => {
  const { $ } = WXC;
  const A = WXAcc;

  const COHORTS = [
    { key: 'all', label: 'Every day on record', title: 'Every city-day the ForecastEx prediction market priced at that hour, each series scored on the ones its own record covers' },
    { key: 'balanced', label: 'Balanced sample', title: 'Only the city-days on which every series holds a value at every lead from 1 to 30 hours before the end of the day' },
  ];
  const VALUES = [
    { key: 'corrected', label: 'Bias-corrected', title: 'The grid-box series with their trailing station offset removed, their values as issued drawn faint behind them' },
    { key: 'issued', label: 'As issued', title: 'Every series as issued, without the station bias correction' },
  ];
  const CLOCKS = [
    { key: 'abs', label: 'Before the day ends', title: 'Lead counted back from station-local midnight at the end of the target day' },
    { key: 'rel', label: 'Before the extreme', title: 'Lead counted back from the last report that reached the day’s extreme, using only the hours before it' },
  ];

  const state = { metric: 'high', cohort: 'all', values: 'corrected', clock: 'abs' };
  let file = null, built = false, valuesGroup = null;

  const fin = v => v != null && isFinite(v);
  const cohortName = key => (COHORTS.find(c => c.key === key) || {}).label || key;

  function seriesOf(id, d, faint) {
    const at = arr => (arr || []).map(v => (fin(v) ? v : null));
    return { id, v: at(d.mae), lo: at(d.lo), hi: at(d.hi), n: at(d.n), pi: d.pi || [], better: d.better || [],
             smooth: id === 'FX', dash: A.dashOf(id), opacity: faint ? 0.22 : null, width: faint ? 1.1 : null };
  }

  function legend(keyEl, note) {
    if (!keyEl) return;
    keyEl.innerHTML = '';
    const item = (col, text, style) => {
      const e = document.createElement('span');
      const i = document.createElement('i');
      i.setAttribute('style', 'border-color:' + col + (style || ''));
      e.appendChild(i); e.appendChild(document.createTextNode(text));
      keyEl.appendChild(e);
    };
    item(A.color('FX'), 'ForecastEx prediction market, with its 95 percent interval', ';border-top-width:3px');
    A.systemGroups(A.ORDER()).forEach(g => item(A.color(g.ids[0]), g.title));
    item('var(--muted)', 'a system’s second series', ';border-top-style:dashed');
    if (note) {
      const n = document.createElement('span');
      n.className = 'kn';
      n.textContent = note;
      keyEl.appendChild(n);
    }
  }

  function tipTable(title, sub, S, i, foot) {
    const rows = S.filter(s => s.opacity == null).map(s => ({ id: s.id, v: s.v[i], n: s.n[i], pi: s.pi[i], better: s.better[i] }));
    rows.sort((a, b) => (fin(a.v) ? a.v : Infinity) - (fin(b.v) ? b.v : Infinity));
    let body = '';
    rows.forEach((r, k) => {
      const pi = r.id === 'FX' || !fin(r.pi) ? A.dash : (r.better ? '<b>' + A.f1(r.pi) + '%</b>' : A.f1(r.pi) + '%');
      body += '<tr' + (r.id === 'FX' ? ' class="tfx"' : '') + '><td>' + (k + 1) + '</td><td>' + A.swatchShort(r.id) + '</td><td>'
        + A.f2(r.v) + '</td><td>' + pi + '</td><td>' + A.int(r.n) + '</td></tr>';
    });
    return '<b>' + title + '</b><div class="tsub">' + sub + '</div>'
      + '<table class="l3"><tr><th>#</th><th>Series</th><th>MAE °F</th><th>Market better</th><th>n</th></tr>'
      + body + '</table><div class="tf">' + foot + '</div>';
  }

  function render() {
    const svg = $('#accLead'), keyEl = $('#accLeadKey'), methEl = $('#accLeadMethod');
    if (!svg) return;
    const met = file.metric && file.metric[state.metric];
    const co = met && met.cohorts && met.cohorts[state.cohort];
    const ck = co && co.clocks && co.clocks[state.clock];
    if (valuesGroup) valuesGroup.hidden = state.cohort !== 'all';
    const values = state.cohort === 'all' ? state.values : 'corrected';
    const blk = ck && ck[values];
    if (!ck || !blk || !blk.series || !blk.series.FX) {
      A.notYet(svg, 'This view is not in the published record.');
      if (keyEl) keyEl.innerHTML = '';
      methodNote(methEl);
      return;
    }
    const bins = ck.bins;
    const ids = A.ORDER().filter(id => blk.series[id]);
    const S = [];
    // the values as issued, faint behind the corrected lines, for the series the correction moves
    if (values === 'corrected' && ck.issued) {
      ids.forEach(id => {
        const info = A.INFO[id] || {};
        if (id !== 'FX' && info.corrected && ck.issued.series[id]) S.push(seriesOf(id, ck.issued.series[id], true));
      });
    }
    ids.forEach(id => S.push(seriesOf(id, blk.series[id], false)));
    const beats = blk.beats || [];
    const lowest = blk.lowest || [];
    const rel = state.clock === 'rel';
    const ext = state.metric === 'high' ? 'maximum' : 'minimum';
    const view = (state.metric === 'high' ? 'Highs' : 'Lows') + ', ' + cohortName(state.cohort).toLowerCase() + ', '
      + (values === 'corrected' ? 'bias-corrected' : 'as issued');
    const share = ck.share || [];
    const fxS = S.find(s => s.id === 'FX' && s.opacity == null);
    A.leadChart(svg, {
      H: 470, hs: bins, series: S, floor: 1, fmt: v => A.f1(v) + '°', label: 'Mean absolute error, °F (lower is better)',
      name: true, xLabel: rel ? 'Hours before the day’s ' + ext + ' was observed' : 'Hours before the end of the target day',
      dayLine: !rel, hatch: !rel, steps: !rel,
      strips: { n: fxS.n, beats, ofLabel: 'systems' },
      secondary: { v: share, label: rel ? 'City-days still priced this far ahead, dashed' : 'City-days whose ' + ext + ' was already observed, dashed' },
      tip: (i, b) => {
        const n = fxS.n[i], bt = beats[i];
        let sub = view + '. ForecastEx ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample') + (fin(n) && n < 30 ? ', under the 30 a bin needs' : '') + '.';
        if (bt && fin(bt.k)) sub += ' Better than ' + bt.k + ' of ' + bt.of + ' systems after Holm.';
        if (lowest[i] === true) sub += ' Lowest error of all.';
        let foot = fxS && fin(fxS.lo[i]) && fxS && fin(fxS.hi[i]) ? 'ForecastEx interval ' + A.iv(fxS.lo[i], fxS.hi[i], A.f2) + '. ' : '';
        if (fin(share[i])) foot += A.pct(share[i]) + (rel ? ' of the city-days still hold a market price this far ahead. ' : ' of the city-days had already seen the day’s ' + ext + '. ');
        if (bt && bt.notBeaten && bt.notBeaten.length) foot += 'Not beaten: ' + bt.notBeaten.map(A.name).join(', ') + '. ';
        foot += 'The improvement is the market’s, on the city-days both hold, in bold where it holds after Holm. n is each series’ own city-days.';
        const title = rel ? (b === 1 ? 'The hour before the ' + ext : b + ' hours before the ' + ext) : A.leadTitle(b);
        return tipTable(title, sub, S, i, foot);
      },
    });
    legend(keyEl, (values === 'corrected' ? 'Faint lines are the corrected series as issued. ' : '')
      + 'LAMP is drawn heavier. The dashed ink line, right axis, is the share of city-days '
      + (rel ? 'still holding a market price that far ahead of the ' + ext + '.' : 'whose ' + ext + ' had already been observed.'));
    methodNote(methEl);
  }

  function methodNote(container) {
    if (!container) return;
    const meta = file.meta || {};
    const co = (meta.cohorts || {})[state.cohort] || {};
    const nCo = co['n_' + state.metric];
    const corr = meta.correction || {};
    A.methodNote(container, {
      title: 'Mean absolute error',
      body: [
        'Mean absolute error is the average gap in degrees between a system’s value and the observation it is built to predict, at a given lead.',
        { tex: 'MAE_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\left| f_{s,i}(h) - o_{s,i} \\right|' },
        'where $f_{s,i}(h)$ is series $s$’s value for city-day $i$ at lead $h$, and $o_{s,i}$ is the observation that series is verified against. The ForecastEx prediction market is always scored against the METAR settle its contracts pay on, the station’s highest or lowest METAR reading of the day rounded to the nearest whole degree. Each alternative is scored against its own standard, as the working paper scores it.',
        'The market’s improvement over a series is computed on the city-days both cover, each against its own standard.',
        { tex: 'PI_s = 100 \\times \\left(1 - \\frac{MAE(\\text{ForecastEx vs. METAR})}{MAE(s \\text{ vs. its own standard})}\\right)' },
        'Three station MOS products, the GFS MOS, the NAM MOS and the NBM station bulletin, issue a daytime high for 7 AM to 7 PM local standard time and an overnight low, so each is scored twice, its issued value against the National Weather Service climate report on the days the report times the extreme inside that window, and its hourly values against the METAR settle. The National Weather Service forecast’s daytime high is scored against the climate report the same way. That forecast and the National Blend of Models are also scored against URMA, the Service’s gridded analysis, at the grid box holding the station, the forecast over 7 AM to 7 PM local standard time and the Blend over the whole day. URMA scores the high only. Buckley Field has no climate report of its own, so the series scored against the report leave it out.',
        'An alternative forecast system’s value at a lead is its most recent forecast at or before that moment, timed by when it was captured. Eight systems are carried back to February from a forecast archive before their live capture began, and an archived run is credited only from the time it was published, its initialization plus the publication delay the archive reports for that model. LAMP and the three station MOS products are carried back the same way from Iowa State’s archive of the bulletins as issued, each timed to its cycle plus the median delay measured on its live capture, and the three issued maxima are read from that archive throughout. The value is held at the running observed extreme, so a forecast high is never shown below a high already observed and a forecast low never above a low already observed.',
        'The ForecastEx prediction market’s value is its median, where its ladder of Yes prices crosses fifty cents, rounded up for highs and down for lows to match whole-degree settlement, read on its most recent ladder snapshot if that is under an hour old. It is left as quoted, never held at the observed extreme. A contract’s Yes price is the midpoint of the Yes bid and one dollar less the No bid. With one side bid it is the midpoint against the empty side at its limit, a missing Yes bid counting as 1 cent and a missing No bid as a 99-cent ask, and a book bidding one cent against ninety-nine, or a lone side at that limit, is empty. The ladder is forced monotone across strikes before the crossing is read, and a crossing that would have to be read across two or more strikes with no price is left undefined. Before 3 July 2026 the ladder comes from the exchange’s published trade record instead, each strike’s last traded price carried forward hourly.',
        'The grid-box series, the raw models, the AI model, the National Blend of Models and the National Weather Service forecast, carry a trailing station bias correction. At each station and lead their mean error over the preceding ' + (corr.windowDays || 30) + ' target dates is subtracted, with at least ' + (corr.minDates || 10) + ' such dates required, counting only dates whose standard had been published at the forecast instant (the climate report and URMA a ' + (corr.publishLagH || 10) + '-hour allowance after the day ends), and the result is held at the observed extreme again. The station MOS products and the two URMA series are left as issued. The As issued view and the faint lines show every series without the correction.',
        'The dashed line on the right axis of the error chart is the share of the scored city-days whose extreme had already been observed at that hour, the last report of the day that reached the settle having come in before it. It is zero before the target day begins. Once it nears 100 percent every held value is pinned to the observed extreme, which is why the curves flatten late in the day.',
        'The Before the extreme view counts lead back from the report that set the day’s extreme instead of from the end of the day, and scores only the hours before that report, so every value is a forecast made before the extreme happened. The extreme’s time is the last report of the day that reached the settle value. Its dashed line is the share of the scored city-days on which the ForecastEx prediction market held a price that far ahead of the extreme.',
        'The record reaches 36 hours before the day ends, so a city-day can only be scored as far ahead of its extreme as that window allows. A low near sunrise falls about 18 hours before the day ends and drops out of the sample beyond about 19 hours before it, which leaves the days whose extreme came late, and those are harder for every system. That is what lifts the curves where the dashed line falls under half. In this view an alternative forecast system is drawn as a line rather than one step per bin, since each bin holds a different clock hour at every station.',
        'The paper scores the daily high. The lows are scored here with the same methods.',
      ],
      rules: [
        'Bins run hourly from 36 hours before the day ends to zero, and in the Before the extreme view bin $k$ holds the moments between $k-1$ and $k$ hours before the extreme’s report, for $k$ from 30 to 1. A series’ bin needs at least 30 city-days before it is drawn. The ForecastEx prediction market is drawn as a line through the bins with its band, since its prices move between hours, and each alternative forecast system as one step per bin, since its value changes only when a new forecast arrives.',
        'Bands are 95 percent intervals over 1,000 resamples of seven-day blocks of target dates, which keep a week of weather together. The market is better than a series at a lead when their paired difference in error is below zero at the 5 percent level after Holm’s adjustment across the series compared there. The beats strip counts the systems it is better than, a system counting only when every series of it is beaten.',
        'The city-days strip counts the ForecastEx prediction market’s city-days at each hour, and each series’ own count is in the hover.',
        'Bins above 30 hours are hatched because every ladder lists at one clock time, so how many hours ahead of its day a city is first priced depends on its time zone and the listing schedule, and only some city-days reach those bins.',
      ],
      span: A.cohortSpanLine(meta, state.cohort, state.metric),
      n: 'Sample ' + (fin(nCo) ? A.int(nCo) : A.dash) + ' city-days'
        + (state.cohort === 'balanced' ? ' in the balanced sample' : ' on the ForecastEx prediction market’s record') + '. ' + A.windowAndBuilt(meta) + '.',
    });
  }

  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A.tabs(bar, CLOCKS, k => { state.clock = k; render(); }, { initial: state.clock, label: 'Lead' });
    A.tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Days' });
    A.tabs(bar, VALUES, k => { state.values = k; render(); }, { initial: state.values, label: 'Values' });
    valuesGroup = bar.lastElementChild;
    built = true;
  }

  function draw(D) {
    const host = $('#accLead');
    if (!host) return;
    const bar = $('#accLeadBar'), keyEl = $('#accLeadKey'), methEl = $('#accLeadMethod');
    file = D && D.lead;
    if (!file || !file.metric) {
      if (bar) bar.innerHTML = '';
      if (keyEl) keyEl.innerHTML = '';
      if (methEl) methEl.innerHTML = '';
      A.notYet(host, A.unpublished(D));
      return;
    }
    if (bar && !built) controls(bar);
    render();
  }

  return { draw, state };
})();
