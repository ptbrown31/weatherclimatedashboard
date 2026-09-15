/* Deterministic skill: one value per system, scored by lead.

   Two charts on one lead axis from lead-curve.json (docs/accuracy.md section
   3). The first is mean absolute error against the settle, the ForecastEx
   prediction market's median (the whole degree where its ladder crosses fifty
   cents) against every alternative forecast system's single value. The
   second, under it, is time to converge: the share of city-days whose value
   was within a tolerance of the truth from a lead to the end of the day. Both
   are drawn by WXAcc.leadChart; nothing here is computed, the module only
   chooses which of the builder's series to draw.

   Two views of the value. What a reader held is the default, the raw record
   floored at the running observed extreme, the number anyone with the
   forecast and the observations in front of them was actually holding.
   Forecast only is the raw record, undefined after an alternative forecast
   system's last live update for the day, so each line ends where its
   forecasting stopped and a dot marks the spot. The probabilistic scores are
   the next section's (acc-prob.js). */
window.WXAccLead = (() => {
  const { $ } = WXC;
  const A = WXAcc;

  const COHORTS = [
    { key: 'own', label: 'Every day on record', title: 'Each system scored on the city-days its own record covers' },
    { key: 'fixed30', label: 'Fixed sample', title: 'City-days the ForecastEx prediction market priced at every hour from 30 to 0, so every bin holds the same days' },
  ];
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'Every system scored against the settle the contracts pay on' },
    { key: 'cli', label: 'NWS climate report', title: 'Alternative forecast systems scored against the National Weather Service climate report for the same date; the ForecastEx prediction market keeps the settle it pays on' },
  ];
  const VALUES = [
    { key: 'held', label: 'What a reader held', title: 'The raw value floored at the running observed extreme, the number a reader with the observations was holding' },
    { key: 'raw', label: 'Forecast only', title: 'The raw record, undefined after each alternative forecast system’s last live update for the day' },
  ];
  const TOLS = [
    { key: 'tol1', label: 'Within 1 °F', title: 'Converged when within one degree of the truth at every hour from that lead on' },
    { key: 'tol2', label: 'Within 2 °F', title: 'Converged when within two degrees of the truth at every hour from that lead on' },
  ];

  const state = { metric: 'high', cohort: 'own', value: 'held', frame: 'metar', tol: 'tol1' };
  let file = null, built = false;

  const fin = v => v != null && isFinite(v);
  const cohortName = key => (COHORTS.find(c => c.key === key) || {}).label || key;

  /* One drawable MAE series per system. In the forecast-only view an
     alternative forecast system's raw error is cut at lastLiveH: the file
     already leaves later bins null, and the cut keeps the line honest against
     a builder that fills them. */
  function seriesFor(id, sys, raw, hs, frame) {
    const cli = frame === 'cli';
    const pick = (a, b, c) => (raw ? sys[a] : cli ? sys[c] : sys[b]) || [];
    const mae = pick('maeRaw', 'mae', 'maeCli'), lo = pick('loRaw', 'lo', 'loCli'), hi = pick('hiRaw', 'hi', 'hiCli');
    const cut = raw && id !== 'FX' && fin(sys.lastLiveH) ? sys.lastLiveH : null;
    const keep = i => (cut == null || hs[i] >= cut);
    const at = arr => hs.map((_, i) => (keep(i) && fin(arr[i]) ? arr[i] : null));
    return { id, v: at(mae), lo: at(lo), hi: at(hi), smooth: id === 'FX', lastLiveH: cut };
  }

  function render() {
    const svg = $('#accLead'), conv = $('#accConv'), keyEl = $('#accLeadKey'), methEl = $('#accLeadMethod');
    if (!svg) return;
    const block = file.metric && file.metric[state.metric];
    const series = block && block.cohorts && block.cohorts[state.cohort];
    if (!block || !series || !series.systems || !Array.isArray(block.h) || !block.h.length) {
      A.notYet(svg, 'This view is not in the published record.');
      if (conv) A.notYet(conv, 'This view is not in the published record.');
      if (keyEl) keyEl.innerHTML = '';
      return;
    }
    const raw = state.value === 'raw';
    const hs = block.h;
    const ids = A.ORDER.filter(id => series.systems[id]);
    const S = ids.map(id => seriesFor(id, series.systems[id], raw, hs, state.frame));
    const beats = (raw ? series.beatsRaw : state.frame === 'cli' ? series.beatsCli : series.beats) || [];
    const notes = series.binNote || [];
    const view = (state.metric === 'high' ? 'Highs' : 'Lows') + ', ' + cohortName(state.cohort).toLowerCase() + ', '
      + (raw ? 'forecast only' : 'what a reader held') + (state.frame === 'cli' ? ', climate-report frame' : '');

    A.leadChart(svg, {
      H: 430, hs, series: S, floor: 1, fmt: v => A.f1(v) + '°', label: 'Mean absolute error, °F (lower is better)',
      name: true, strips: { n: series.n || [], beats, ofLabel: raw ? 'live record' : 'scored' },
      tip: (i, hh) => {
        const n = (series.n || [])[i], b = beats[i], note = notes[i];
        let sub = view + '. ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample') + (fin(n) && n < 30 ? ', under the 30 the bins need' : '') + '.';
        if (b && fin(b.k)) sub += ' ForecastEx beats ' + b.k + ' of ' + b.of + ' systems.';
        const fx = S[0];
        let foot = fx && fin(fx.lo[i]) && fin(fx.hi[i]) ? 'ForecastEx band ' + A.iv(fx.lo[i], fx.hi[i], A.f2) + '. ' : '';
        if (note && Array.isArray(note.dates)) {
          const z = note.zones || {};
          foot += 'Partial sample, dates ' + note.dates[0] + ' to ' + note.dates[1]
            + (Object.keys(z).length ? ', zones ' + Object.keys(z).map(tz => String(tz).split('/').pop().replace(/_/g, ' ') + ' ' + A.int(z[tz])).join(', ') : '') + '.';
        }
        return A.rankTip(A.leadTitle(hh), sub, S.map(s => ({ id: s.id, v: s.v[i], n })), 'MAE °F', A.f2, { foot });
      },
    });
    drawConverge(conv, block, ids, view);

    if (keyEl) {
      A.key(keyEl, ids, { meta: file.meta, metric: state.metric,
        note: (raw ? 'A dot marks each alternative forecast system’s last live update for the day. ' : '')
          + 'The band is the ForecastEx prediction market’s 95 percent bootstrap interval.' });
    }
    methodNote(methEl);
  }

  /* Time to converge, on the same leads. The share is read off the builder's
     converge block for the day basis, frame and tolerance shown. It is always
     on the value a reader held: a forecast-only record stops at the last
     update, after which every hour would pass for want of a value. */
  function drawConverge(svg, block, ids, view) {
    if (!svg) return;
    const cv = block.converge;
    const co = cv && cv[state.tol] && cv[state.tol][state.cohort];
    const fr = co && co[state.frame];
    if (!fr || !fr.systems || !Array.isArray(cv.h)) { A.notYet(svg, 'Time to converge is not in the published record.'); return; }
    const hs = cv.h;
    const tolName = TOLS.find(t => t.key === state.tol).label.replace('Within', 'within');
    const S = ids.filter(id => fr.systems[id]).map(id => ({ id, v: (fr.systems[id].share || []).map(v => (fin(v) ? v : null)),
                                                            smooth: id === 'FX', sys: fr.systems[id] }));
    A.leadChart(svg, {
      H: 340, hs, series: S, ymax: 1, fmt: v => Math.round(v * 100) + '%',
      label: 'Share of city-days converged, ' + tolName + (state.value === 'raw' ? ', held value' : '') + ' (higher is better)',
      tip: (i, hh) => {
        const rows = S.slice().sort((a, b) => (fin(b.v[i]) ? b.v[i] : -1) - (fin(a.v[i]) ? a.v[i] : -1));
        let body = '';
        rows.forEach((s, k) => {
          body += '<tr' + (s.id === 'FX' ? ' class="tfx"' : '') + '><td>' + (k + 1) + '</td><td>'
            + A.swatch(s.id).replace(A.name(s.id), A.short(s.id)) + '</td><td>' + A.pct(s.v[i]) + '</td><td>'
            + (fin(s.sys.median) ? A.f1(s.sys.median) + ' h' : 'never half') + '</td></tr>';
        });
        return '<b>' + A.leadTitle(hh) + '</b><div class="tsub">' + view.replace(', forecast only', ', what a reader held') + ', ' + tolName + '.</div>'
          + '<table class="l3"><tr><th>#</th><th>System</th><th>Converged</th><th>Half by</th></tr>' + body + '</table>'
          + '<div class="tf">Half by is the lead at which half of the system’s city-days had converged.</div>';
      },
    });
  }

  function methodNote(container) {
    if (!container) return;
    const meta = file.meta || {};
    const co = (meta.cohorts || {})[state.cohort] || {};
    const nCo = state.metric === 'high' ? co.n_high : co.n_low;
    const noun = { own: 'the ForecastEx prediction market’s own record', fixed30: 'the fixed sample' }[state.cohort];
    A.methodNote(container, {
      title: 'Mean absolute error and time to converge',
      body: [
        'Error is mean absolute error, the average gap in degrees between a system’s call and what the station recorded, at a given lead.',
        { tex: 'MAE_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\left| f_{s,i}(h) - o_i \\right|' },
        'where $f_{s,i}(h)$ is system $s$’s value for city-day $i$ at lead $h$, and $o_i$ is the settle, the station’s highest or lowest hourly METAR reading of the day rounded to the nearest whole degree. Lead counts down to station-local midnight, the moment the target day ends.',
        'A system’s value at a given lead is its most recent reading at or before that moment. Once a high or low has actually occurred, no forecast is shown as implying something more extreme, values are held at the running observed extreme. The ForecastEx prediction market’s value is its median, where its price ladder crosses fifty cents, rounded up for highs and down for lows to match whole-degree settlement. A median that would have to be read across two or more strikes with no price is left undefined, since late in the day a ladder often quotes only its far strikes and a line drawn across the empty run would invent a value the prices do not state.',
        'Time to converge measures how early a system locks onto the temperature the day ends on and stays there. For a tolerance of $d$ degrees, a city-day has converged by lead $h$ if its value was within $d$ degrees of the truth at every hour from $h$ to the end of the day at which it held a value.',
        { tex: 'C_s(h; d) = \\frac{1}{N}\\left|\\{\\, i : |f_{s,i}(h\') - o_i| \\le d \\text{ for every } h\' \\le h \\,\\}\\right|' },
        'Convergence is always measured on the value a reader held, in either value view, since a forecast-only record stops at the last update and every later hour would pass for want of a value. An hour with no value, a gap in the ForecastEx prediction market’s book or a median left undefined, is no evidence either way and does not break the run.',
        'The two charts answer different questions. Error is the average miss at each hour. Convergence rewards a value that is right and then does not move, so a forecast that is rarely revised can converge early on the days it happens to be right while a value that follows each report, and is closer on average, can step outside the tolerance on the way and have to converge again.',
      ],
      rules: [
        'An alternative forecast system’s value is its most recent forecast captured at or before the moment, and the ForecastEx prediction market’s is read on its most recent ladder snapshot. A contract’s Yes price is the midpoint of the Yes bid and one dollar less the No bid when both sides are bid; with one side bid it is the midpoint against the empty side at its limit, a missing Yes bid counting as 1 cent and a missing No bid as a 99-cent ask, and a book bidding one cent against ninety-nine, or a lone side at that limit, is unquoted. The ladder is forced monotone across strikes by pooling violations before its fifty-cent crossing is read.',
        'Bins run hourly from 36 hours before the day ends to zero, and an error bin needs at least 30 matched city-days before it’s drawn. Thin order books, short recording windows and days with gaps in the observation record are excluded and counted.',
        'Two sets of days are available, every day on each system’s own record, or a fixed sample of the city-days the ForecastEx prediction market priced at every hour from 30 to 0.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates.',
        'The beats strip counts the systems the ForecastEx prediction market beat at that hour, meaning the 95 percent interval of the paired difference in error over the days both hold, both sides resampled under the same draws, lies entirely in its favor.',
        'Bins above 30 hours are hatched because a ladder lists at a fixed clock time, so the eastern stations reach 36 hours before the western ones do, and hovering shows which dates and time zones fill those bins.',
      ],
      span: A.cohortSpanLine(meta, state.cohort) + (state.frame === 'cli'
        ? ' In the climate-report frame each alternative forecast system is scored against the National Weather Service report for the same date, a different definition of the day\u2019s extreme that runs about a degree warmer on highs, the ForecastEx prediction market keeps the settle it pays on, and Buckley Field drops out because Denver’s report stands in for it.'
        : ''),
      n: 'Sample ' + (fin(nCo) ? A.int(nCo) : A.dash) + ' city-days in ' + noun + (co.from ? ' from ' + co.from : '')
        + ', each system drawn on the part of that record it covers. ' + A.windowAndBuilt(meta) + '.',
    });
  }

  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A.tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Days' });
    A.tabs(bar, VALUES, k => { state.value = k; render(); }, { initial: state.value, label: 'Value' });
    A.tabs(bar, FRAMES, k => { state.frame = k; render(); }, { initial: state.frame, label: 'Frame' });
    A.tabs(bar, TOLS, k => { state.tol = k; render(); }, { initial: state.tol, label: 'Converged' });
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
      A.notYet(host, A.NOT_PUBLISHED);
      if ($('#accConv')) A.notYet($('#accConv'), A.NOT_PUBLISHED);
      return;
    }
    if (bar && !built) controls(bar);
    render();
  }

  return { draw, state };
})();
