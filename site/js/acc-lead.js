/* Deterministic skill: one value per system, scored by lead.

   Two charts on one lead axis from lead-curve.json (docs/accuracy.md section
   3). The first is mean absolute error against each system's own target (the
   settle for all but the National Weather Service forecast), the ForecastEx
   prediction market's median (the whole degree where its ladder crosses fifty
   cents) against every alternative forecast system's single value. The
   second, under it, is time to converge: the share of city-days whose value
   was within a tolerance of the truth from a lead to the end of the day. Both
   are drawn by WXAcc.leadChart; nothing here is computed, the module only
   chooses which of the builder's series to draw.

   Two views of the value. What a reader held is the default, the record held
   at the running observed extreme (never below a high or above a low already
   observed), the number anyone with the forecast and the observations in
   front of them was actually holding. Forecast only is the record as issued,
   undefined after an alternative forecast system's last update of the day, so
   each line ends where its forecasting stopped and a dot marks the spot. The
   probabilistic scores are the next section's (acc-prob.js). */
window.WXAccLead = (() => {
  const { $ } = WXC;
  const A = WXAcc;

  const COHORTS = [
    { key: 'own', label: 'Every day on record', title: 'Every city-day the ForecastEx prediction market priced at that hour, each system scored on the ones its own record covers' },
    { key: 'fixed30', label: 'Fixed sample', title: 'Only the city-days the ForecastEx prediction market priced at every hour from 30 to 0, so the bins from 30 to 0 hold the same days' },
  ];
  // Own target is the default: each system against the observation its daily
  // value is built to predict. Only the National Weather Service forecast's
  // differs, a daytime high or overnight low scored against the climate report
  // on the days the report puts the extreme inside that window.
  const FRAMES = [
    { key: 'target', label: 'Own target', title: 'Each system scored against the observation it is built to predict: the METAR settle for the ForecastEx prediction market and every alternative forecast system but one, and for the National Weather Service forecast, a daytime high and overnight low, its climate report on the days the report puts the extreme inside the forecast’s window' },
    { key: 'metar', label: 'METAR settle', title: 'Every system, the National Weather Service forecast included, scored against the settle the contracts pay on' },
    { key: 'cli', label: 'NWS climate report', title: 'Alternative forecast systems scored against the National Weather Service climate report for the same date; the ForecastEx prediction market keeps the settle it pays on' },
  ];
  const FRAME_NOTE = { target: '', metar: ', METAR-settle frame', cli: ', climate-report frame' };
  const VALUES = [
    { key: 'held', label: 'What a reader held', title: 'Each value held at the running observed extreme, never below a high or above a low already observed, the number a reader with the observations was holding' },
    { key: 'raw', label: 'Forecast only', title: 'Each forecast as issued, ending at the system’s last update of the day' },
  ];
  const TOLS = [
    { key: 'tol1', label: 'Within 1 °F', title: 'Converged when within one degree of the truth at every hour from that lead on' },
    { key: 'tol2', label: 'Within 2 °F', title: 'Converged when within two degrees of the truth at every hour from that lead on' },
  ];

  const LEADS = [
    { key: 'day', label: 'Before the day ends', title: 'Lead counted back from station-local midnight at the end of the target day' },
    { key: 'extreme', label: 'Before the extreme', title: 'Lead counted back from the report that set the day’s extreme, using only the hours before it' },
  ];

  const state = { metric: 'high', cohort: 'own', value: 'held', frame: 'target', tol: 'tol1', lead: 'day' };
  let file = null, built = false, leadGroup = null, frameGroup = null;

  const fin = v => v != null && isFinite(v);
  const cohortName = key => (COHORTS.find(c => c.key === key) || {}).label || key;

  /* One drawable MAE series per system, with its own sample per bin. In the
     forecast-only view an alternative forecast system's raw error is cut at
     lastLiveH: the file already leaves later bins null, and the cut keeps the
     line honest against a builder that fills them. */
  function seriesFor(id, sys, raw, hs, frame) {
    const pick = base => field(sys, base, raw, frame) || [];
    const mae = pick('mae'), lo = pick('lo'), hi = pick('hi'), n = pick('n');
    const cut = raw && id !== 'FX' && fin(sys.lastLiveH) ? sys.lastLiveH : null;
    const keep = i => (cut == null || hs[i] >= cut);
    const at = arr => hs.map((_, i) => (keep(i) && fin(arr[i]) ? arr[i] : null));
    return { id, v: at(mae), lo: at(lo), hi: at(hi), n: hs.map((_, i) => (keep(i) && fin(n[i]) ? n[i] : null)),
             smooth: id === 'FX', lastLiveH: cut };
  }

  /* A system's series for a view: base 'mae', 'lo', 'hi' or 'n'. The forecast
     only view has a METAR and an own-target version; the climate-report frame
     carries none, so there it is the METAR one, as it always was. A file built
     before the own-target frame existed reads as METAR. */
  function field(sys, base, raw, frame) {
    if (frame === 'target') {
      const t = sys[base + (raw ? 'RawTarget' : 'Target')];
      if (t) return t;
    }
    return sys[base + (raw ? 'Raw' : frame === 'cli' ? 'Cli' : '')];
  }
  function beatsFor(series, raw, frame) {
    if (frame === 'target') {
      const t = raw ? series.beatsRawTarget : series.beatsTarget;
      if (t) return t;
    }
    return (raw ? series.beatsRaw : frame === 'cli' ? series.beatsCli : series.beats) || [];
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
    // the lead can be counted from the end of the day or back from the extreme;
    // the second exists only where the file carries it, and it is drawn in the
    // own-target frame
    const hasRel = !!(block.relative && Array.isArray(block.relative.k) && block.relative.k.length);
    if (leadGroup) leadGroup.hidden = !hasRel;
    const rel = hasRel && state.lead === 'extreme';
    if (frameGroup) frameGroup.hidden = rel;
    if (rel) { renderRelative(block, svg, conv, keyEl, methEl); return; }
    const raw = state.value === 'raw';
    const hs = block.h;
    const ids = A.ORDER.filter(id => series.systems[id]);
    const observed = series.observed || [];
    const S = ids.map(id => seriesFor(id, series.systems[id], raw, hs, state.frame));
    const beats = beatsFor(series, raw, state.frame);
    const notes = series.binNote || [];
    const view = (state.metric === 'high' ? 'Highs' : 'Lows') + ', ' + cohortName(state.cohort).toLowerCase() + ', '
      + (raw ? 'forecast only' : 'what a reader held') + FRAME_NOTE[state.frame];

    A.leadChart(svg, {
      H: 430, hs, series: S, floor: 1, fmt: v => A.f1(v) + '°', label: 'Mean absolute error, °F (lower is better)',
      name: true, strips: { n: series.n || [], beats, ofLabel: raw ? 'live record' : 'scored' },
      secondary: { v: observed, label: 'City-days with the extreme already observed, dashed' },
      tip: (i, hh) => {
        const n = (series.n || [])[i], b = beats[i], note = notes[i];
        let sub = view + '. ForecastEx ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample') + (fin(n) && n < 30 ? ', under the 30 a bin needs' : '') + '.';
        if (b && fin(b.k)) sub += ' ForecastEx beats ' + b.k + ' of ' + b.of + ' systems.';
        const fx = S[0];
        let foot = fx && fin(fx.lo[i]) && fin(fx.hi[i]) ? 'ForecastEx band ' + A.iv(fx.lo[i], fx.hi[i], A.f2) + '. ' : '';
        if (note && Array.isArray(note.dates)) {
          const z = note.zones || {};
          foot += 'Partial sample, target days ' + A.mdyY(note.dates[0]) + ' to ' + A.mdyY(note.dates[1])
            + (Object.keys(z).length ? ', zones ' + Object.keys(z).map(tz => String(tz).split('/').pop().replace(/_/g, ' ') + ' ' + A.int(z[tz])).join(', ') : '') + '. ';
        }
        if (fin(observed[i])) foot += A.pct(observed[i]) + ' of these city-days had already seen the day’s extreme. ';
        foot += 'n is each system’s own city-days at this hour.';
        return A.rankTip(A.leadTitle(hh), sub, S.map(s => ({ id: s.id, v: s.v[i], n: s.n[i] })), 'MAE °F', A.f2, { foot });
      },
    });
    drawConverge(conv, block, ids, view);

    if (keyEl) {
      A.key(keyEl, ids, { meta: file.meta, metric: state.metric,
        note: (raw ? 'A dot marks each alternative forecast system’s last live update for the day. ' : '')
          + 'The band is the ForecastEx prediction market’s 95 percent bootstrap interval. The dashed line, right axis, is the share of its city-days whose extreme had already been observed.' });
    }
    methodNote(methEl);
  }

  /* The same error curves with lead counted back from the report that set the
     day's extreme instead of from the end of the day. Only the hours before
     that report are scored, so every value is a forecast made before the
     extreme happened. The builder's relative block carries each system's
     curve by whole hours before the extreme, the share of the market's
     city-days holding a forecast that far ahead, and the share of those hours
     at which an earlier report had already reached the settle value. */
  function renderRelative(block, svg, conv, keyEl, methEl) {
    const rb = block.relative;
    const co = rb.cohorts && rb.cohorts[state.cohort];
    if (!co || !co.systems) {
      A.notYet(svg, 'This view is not in the published record.');
      if (keyEl) keyEl.innerHTML = '';
      return;
    }
    const raw = state.value === 'raw';
    const ks = rb.k;
    const ids = A.ORDER.filter(id => co.systems[id]);
    const pick = (sy, a) => field(sy, a, raw, 'target') || [];
    const S = ids.map(id => {
      const sy = co.systems[id];
      return { id, v: pick(sy, 'mae').map(v => (fin(v) ? v : null)), lo: pick(sy, 'lo'), hi: pick(sy, 'hi'),
               n: pick(sy, 'n'), smooth: id === 'FX' };
    });
    const beats = beatsFor(co, raw, 'target');
    const share = co.share || [], tie = co.tieShare || [];
    const base = (state.metric === 'high' ? 'Highs' : 'Lows') + ', ' + cohortName(state.cohort).toLowerCase() + ', '
      + (raw ? 'forecast only' : 'what a reader held');
    const kTitle = k => (k === 1 ? 'The hour before the extreme' : k + ' hours before the extreme');
    A.leadChart(svg, {
      H: 430, hs: ks, series: S, floor: 1, fmt: v => A.f1(v) + '°', label: 'Mean absolute error, °F (lower is better)',
      name: true, xLabel: 'Hours before the day’s extreme was observed', dayLine: false, hatch: false, steps: false,
      strips: { n: co.n || [], beats, ofLabel: raw ? 'live record' : 'scored' },
      secondary: { v: share, label: 'City-days with a forecast this far ahead, dashed' },
      tip: (i, k) => {
        const n = (co.n || [])[i], b = beats[i];
        let sub = base + '. ForecastEx ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample')
          + (fin(n) && n < 30 ? ', under the 30 a bin needs' : '') + '.';
        if (b && fin(b.k)) sub += ' ForecastEx beats ' + b.k + ' of ' + b.of + ' systems.';
        const fx = S[0];
        let foot = fx && fin(fx.lo[i]) && fin(fx.hi[i]) ? 'ForecastEx band ' + A.iv(fx.lo[i], fx.hi[i], A.f2) + '. ' : '';
        if (fin(share[i])) foot += A.pct(share[i]) + ' of the ForecastEx prediction market’s city-days hold a forecast this far ahead. ';
        if (fin(tie[i])) foot += 'At ' + A.pct(tie[i]) + ' of them an earlier report had already reached the day’s extreme value. ';
        // past the point where most days drop out, the sample is the days whose
        // extreme came late, which are harder for every system
        if (fin(share[i]) && share[i] < 0.5) foot += 'Under half of the city-days reach this far, since only those whose extreme came late enough can be scored here, and a late extreme is a harder day for every system. ';
        foot += 'n is each system’s own city-days at this lead.';
        return A.rankTip(kTitle(k), sub, S.map(x => ({ id: x.id, v: x.v[i], n: x.n[i] })), 'MAE °F', A.f2, { foot });
      },
    });
    drawConverge(conv, block, ids, base, 'target');
    if (keyEl) {
      A.key(keyEl, ids, { meta: file.meta, metric: state.metric,
        note: (raw ? 'Forecast only, each system’s value as issued before the extreme. ' : '')
          + 'The band is the ForecastEx prediction market’s 95 percent bootstrap interval. The dashed line, right axis, is the share of its city-days holding a forecast this far ahead of the extreme.' });
    }
    methodNote(methEl);
  }

  /* Time to converge, on the same leads. The share is read off the builder's
     converge block for the day basis, frame and tolerance shown. It is always
     on the value a reader held: a forecast-only record stops at the last
     update, after which every hour would pass for want of a value. */
  function drawConverge(svg, block, ids, view, frame) {
    if (!svg) return;
    const cv = block.converge;
    const co = cv && cv[state.tol] && cv[state.tol][state.cohort];
    const fr = co && (co[frame || state.frame] || co.metar);
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
            + (fin(s.sys.median) ? A.f1(s.sys.median) + ' h' : 'not reached') + '</td></tr>';
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
        'Mean absolute error is the average gap in degrees between a system’s value and the observation it is built to predict, at a given lead.',
        { tex: 'MAE_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\left| f_{s,i}(h) - o_i \\right|' },
        'where $f_{s,i}(h)$ is system $s$’s value for city-day $i$ at $h$ hours before the day ends at station-local midnight, and $o_i$ is that observation. For the ForecastEx prediction market and every alternative forecast system but one it is the settle, the station’s highest or lowest METAR reading of the day rounded to the nearest whole degree, which the contracts pay on and which the extreme of a system’s hourly forecasts predicts.',
        'The exception is the National Weather Service forecast. Its daily value is a daytime high for 7 AM to 7 PM and an overnight low for 7 PM to 8 AM local standard time, so in the default own-target frame it is scored against the Service’s climate report on the city-days whose report puts the extreme inside that window, for a low between midnight and 8 AM, the part of the window the report’s day covers. On the other city-days that forecast was not predicting the day’s extreme and is not scored. Buckley Field has no climate report of its own, only Denver’s, so the forecast is not scored there in this frame. The METAR settle frame scores every system against the settle, that forecast included, and the climate-report frame scores every alternative forecast system against the report on every date.',
        'An alternative forecast system’s value at a lead is its most recent forecast at or before that moment, timed by when it was captured. Eight systems are carried back to February from a forecast archive before their live capture began, and an archived run is credited only from the time it was published, its initialization plus the publication delay the archive reports for that model. In the default view it is held at the running observed extreme, so a forecast high is never shown below a high already observed and a forecast low never above a low already observed. The forecast-only view shows the forecast as issued and ends at the system’s last update of the day.',
        'The ForecastEx prediction market’s value is its median, where its ladder of Yes prices crosses fifty cents, rounded up for highs and down for lows to match whole-degree settlement, read on its most recent ladder snapshot if that is under an hour old and held the same way. A contract’s Yes price is the midpoint of the Yes bid and one dollar less the No bid. With one side bid it is the midpoint against the empty side at its limit, a missing Yes bid counting as 1 cent and a missing No bid as a 99-cent ask, and a book bidding one cent against ninety-nine, or a lone side at that limit, is empty. The ladder is forced monotone across strikes before the crossing is read, and a crossing that would have to be read across two or more strikes with no price is left undefined. Before 17 June 2026 the ladder comes from the exchange’s published trade record instead, each strike’s last traded price carried forward hourly.',
        'Time to converge measures how early a system locks onto the temperature the day ends on and stays there. For a tolerance of $d$ degrees, a city-day has converged by lead $h$ if its value was within $d$ degrees of the truth at every hour from $h$ to the end of the day at which it had a value.',
        { tex: 'C_s(h; d) = \\frac{1}{N}\\left|\\{\\, i : |f_{s,i}(h\') - o_i| \\le d \\text{ for every } h\' \\le h \\,\\}\\right|' },
        'It is measured on the held value in either view, since a forecast-only record stops at the last update. An hour with no value, a gap in the ForecastEx prediction market’s book or an undefined median, does not break the run.',
        'The dashed line on the right axis of the error chart is the share of the ForecastEx prediction market’s city-days at that hour whose extreme had already been observed, some METAR report at or before that moment having reached the settle. It is zero before the target day begins. Once it nears 100 percent every held value is pinned to the observed extreme, which is why the curves flatten late in the day.',
        'The Before the extreme view counts lead back from the report that set the day’s extreme instead of from the end of the day, and scores only the hours before that report, so every value is a forecast made before the extreme happened. The extreme’s time is the last report of the day that reached the settle value. Temperatures are whole degrees, so that value often recurs at consecutive reports, and the hover gives the share of city-days at each lead where an earlier report had already reached it. Its dashed line is the share of the ForecastEx prediction market’s city-days holding a forecast that far ahead of the extreme. This view is drawn in the own-target frame.',
        'The record reaches 36 hours before the day ends, so a city-day can only be scored as far ahead of its extreme as that window allows. A low near sunrise falls about 18 hours before the day ends and drops out of the sample beyond about 19 hours before it, which leaves the days whose extreme came late, and those are harder for every system. That is what lifts the curves where the dashed line falls under half. In this view an alternative forecast system is drawn as a line rather than one step per bin, since each bin holds a different clock hour at every station.',
        'The two charts answer different questions. Error is the average miss at each hour. Convergence rewards a value that is right and then does not move, so a forecast that is rarely revised can converge early on the days it happens to be right while a value that follows each report, and is closer on average, can step outside the tolerance on the way and have to converge again.',
      ],
      rules: [
        'Bins run hourly from 36 hours before the day ends to zero, and in the Before the extreme view bin $k$ holds the moments between $k-1$ and $k$ hours before the extreme’s report. A system’s bin needs at least 30 city-days before it is drawn. The ForecastEx prediction market is drawn as a line through the bins with its band, since its prices move between hours, and each alternative forecast system as one step per bin, since its value changes only when a new forecast arrives.',
        'Two sets of days are available, every city-day the ForecastEx prediction market priced at that hour, or only the fixed sample of city-days it priced at every hour from 30 to 0.',
        'Thin order books, dates with too few price snapshots and days with gaps in the observation record are excluded, and the counts are listed in the details under the scorecard.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates.',
        'The city-days strip counts the ForecastEx prediction market’s city-days at each hour, and each system’s own count is in the hover. The beats strip counts the systems the ForecastEx prediction market beat at that hour, meaning the 95 percent interval of the paired difference in error over the days both hold, both sides resampled under the same draws, lies entirely in its favor.',
        'Bins above 30 hours are hatched because every ladder lists at one clock time, so how many hours ahead of its day a city is first priced depends on its time zone and the listing schedule, and only some city-days reach those bins. Hovering shows which dates and time zones fill them.',
      ],
      span: A.cohortSpanLine(meta, state.cohort, state.metric) + (state.frame === 'cli'
        ? ' In the climate-report frame each alternative forecast system is scored against the National Weather Service climate report for the same date, a different definition of the day’s extreme that runs about a degree warmer on highs, and the ForecastEx prediction market keeps the settle it pays on. Buckley Field has no climate report of its own, only Denver’s, so it drops out of that frame.'
        : state.frame === 'metar'
        ? ' In the METAR settle frame the National Weather Service forecast is also scored against the settle, a different definition of the day’s extreme from its daytime high and overnight low, which is why its error is larger here than in the own-target frame.'
        : ''),
      n: 'Sample ' + (fin(nCo) ? A.int(nCo) : A.dash) + ' city-days in ' + noun + (co.from ? ' from ' + A.mdyY(co.from) : '')
        + '. ' + A.windowAndBuilt(meta) + '.',
    });
  }

  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A.tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Days' });
    A.tabs(bar, VALUES, k => { state.value = k; render(); }, { initial: state.value, label: 'Value' });
    A.tabs(bar, LEADS, k => { state.lead = k; render(); }, { initial: state.lead, label: 'Lead' });
    leadGroup = bar.lastElementChild;
    A.tabs(bar, FRAMES, k => { state.frame = k; render(); }, { initial: state.frame, label: 'Frame' });
    frameGroup = bar.lastElementChild;
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
