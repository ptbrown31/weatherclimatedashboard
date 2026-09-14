/* Figure 1, the lead curve.

   Two scores by hours before the end of the target day, on the city-days the
   ForecastEx prediction market priced at that hour. The file is
   lead-curve.json (docs/accuracy.md section 3); nothing here is computed, the
   figure only chooses which of the builder's series to draw.

   MAE is mean absolute error against the settle, the ForecastEx prediction
   market's fifty-cent crossing against every alternative forecast system's
   single value. CRPS scores a whole distribution, so it is drawn for the
   systems that publish one: the ForecastEx prediction market's price ladder
   and the four ensembles, read at the same strikes at the same instant.

   Lead runs down to the right, so the day ends at the right edge and a curve
   is read the way the day is lived. The ForecastEx prediction market is drawn
   through the bin centers with its bootstrap band because a ladder is quoted
   every ten minutes and its score moves smoothly within an hour. A forecast
   is drawn as a step, one flat tread per hourly bin, because an alternative
   forecast system's value changes only when a cycle lands and is otherwise
   the same record forward filled, so a line joining bin centers would draw
   motion the forecast never had.

   Two views of the value under MAE. What a reader held is the default, the
   raw record floored at the running observed extreme (contract, standing
   value), which is the number anyone with the forecast and the observations
   in front of them was actually holding. Forecast only is the raw record,
   undefined after an alternative forecast system's last live update for the
   day, so each line ends where its forecasting stopped and a dot marks the
   spot. CRPS has the held view only, since a strike the observations have
   cleared is paid whatever a forecast said.

   Bins above 30 h are hatched because the sample there is partial (a ladder
   lists at a fixed clock time, so the eastern stations reach 36 h before the
   western ones do) and the hover names the dates and zones that fill them. */
window.WXAccLead = (() => {
  const { el, txt, $ } = WXC;
  const A = WXAcc;

  const SCORES = [
    { key: 'mae', label: 'MAE', title: 'Mean absolute error of each system’s single value' },
    { key: 'crps', label: 'CRPS', title: 'Continuous ranked probability score of each distribution, the ForecastEx prediction market’s ladder against the ensembles' },
  ];
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
  const HATCH_ID = 'accLeadHatch';
  const HATCH_ABOVE = 30;

  const state = { metric: 'high', score: 'mae', cohort: 'own', value: 'held', frame: 'metar' };
  let file = null, built = false, valueGroup = null;

  // ------------------------------------------------------------- data
  const fin = v => v != null && isFinite(v);
  const cohortName = key => (COHORTS.find(c => c.key === key) || {}).label || key;
  const clean = (hs, arr) => hs.map((_, i) => (arr && fin(arr[i]) ? arr[i] : null));

  /* One drawable series per system for the MAE view.

     In the forecast-only view an alternative forecast system's raw error is cut at lastLiveH: the
     file already leaves later bins null, and the cut here keeps the line
     honest against a builder that fills them. */
  function seriesFor(sys, raw, hs, frame) {
    const cli = frame === 'cli';
    const mae = (raw ? sys.maeRaw : cli ? sys.maeCli : sys.mae) || [];
    const lo = (raw ? sys.loRaw : cli ? sys.loCli : sys.lo) || [];
    const hi = (raw ? sys.hiRaw : cli ? sys.hiCli : sys.hi) || [];
    const cut = raw && fin(sys.lastLiveH) ? sys.lastLiveH : null;
    const keep = i => (cut == null || hs[i] >= cut);
    return {
      v: hs.map((_, i) => (keep(i) && fin(mae[i]) ? mae[i] : null)),
      lo: hs.map((_, i) => (keep(i) && fin(lo[i]) ? lo[i] : null)),
      hi: hs.map((_, i) => (keep(i) && fin(hi[i]) ? hi[i] : null)),
      lastLiveH: cut,
    };
  }

  /* What the drawing needs for the current view, whichever score it is:
     {hs, ids, S: {id: {v, lo, hi, n?, lastLiveH?}}, beats, nPer, notes},
     or null when the file does not carry the view. */
  function viewOf() {
    const block = file.metric && file.metric[state.metric];
    const series = block && block.cohorts && block.cohorts[state.cohort];
    if (!block || !series || !series.systems || !Array.isArray(block.h) || !block.h.length) return null;
    const hs = block.h;
    const notes = series.binNote || [];
    if (state.score === 'crps') {
      const c = block.crps;
      const v = c && c.cohorts && c.cohorts[state.cohort] && c.cohorts[state.cohort][state.frame];
      if (!v || !v.systems || !v.systems.FX || !Array.isArray(c.h) || !c.h.length) return null;
      const ch = c.h;
      // the market first, then the ensembles in the registry's order
      const ens = Object.keys(v.systems).filter(k => k !== 'FX');
      const byId = {};
      ens.forEach(k => { byId[A.ensId(k)] = v.systems[k]; });
      const order = A.systemGroups(Object.keys(byId)).flatMap(g => g.ids);
      const S = { FX: { v: clean(ch, v.systems.FX.crps), lo: clean(ch, v.systems.FX.lo), hi: clean(ch, v.systems.FX.hi),
                        n: v.systems.FX.n || [] } };
      order.forEach(id => {
        const s = byId[id];
        S[id] = { v: clean(ch, s.crps), lo: clean(ch, s.lo), hi: clean(ch, s.hi), n: s.n || [] };
      });
      return { hs: ch, ids: ['FX'].concat(order), S, beats: v.beats || [], nPer: v.systems.FX.n || [],
               notes: ch === hs ? notes : ch.map(hh => notes[hs.indexOf(hh)]), raw: false };
    }
    const raw = state.value === 'raw';
    const ids = A.ORDER.filter(id => series.systems[id]);
    const S = {};
    ids.forEach(id => { S[id] = seriesFor(series.systems[id], raw, hs, state.frame); });
    const beats = (raw ? series.beatsRaw : state.frame === 'cli' ? series.beatsCli : series.beats) || [];
    return { hs, ids, S, beats, nPer: series.n || [], notes, raw, series };
  }

  // ------------------------------------------------------------- drawing
  // a stepped path: one flat tread per hourly bin, the pen lifted at a gap
  function stepLine(svg, x, y, hs, vals, attrs) {
    const xs = [], ys = [];
    hs.forEach((hh, i) => {
      if (fin(vals[i])) { xs.push(x(hh + 0.5), x(hh - 0.5)); ys.push(y(vals[i]), y(vals[i])); }
      else { xs.push(null, null); ys.push(null, null); }
    });
    return A.lineSeries(svg, xs, ys, attrs);
  }

  function hatchDefs(svg) {
    const defs = el('defs');
    const pat = el('pattern', { id: HATCH_ID, patternUnits: 'userSpaceOnUse', width: 7, height: 7,
                                patternTransform: 'rotate(45)' });
    pat.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 7, stroke: 'var(--rule)', 'stroke-width': 1.1,
                                 'stroke-opacity': 0.55 }));
    defs.appendChild(pat);
    svg.appendChild(defs);
  }

  // short zone name for a tooltip line, the full IANA id on the title
  const zoneShort = tz => String(tz).split('/').pop().replace(/_/g, ' ');

  function render() {
    const svg = $('#accLead'), keyEl = $('#accLeadKey'), methEl = $('#accLeadMethod');
    if (!svg) return;
    if (valueGroup) valueGroup.hidden = state.score === 'crps';
    const V = viewOf();
    if (!V) {
      A.notYet(svg, 'This view is not in the published record.');
      if (keyEl) keyEl.innerHTML = '';
      return;
    }
    const crps = state.score === 'crps';
    const { hs, ids, S, beats, nPer, notes, raw } = V;
    const hmax = Math.max.apply(null, hs);

    // ---- geometry. The frame stops short so two strips fit under the axis.
    const H = 430;
    const g = A.frame(H, { L: 70, B: 298 });
    // half a bin of padding each side so the 36 h and 0 h treads are whole
    const x = A.scale(hmax + 0.5, -0.5, g.L, g.R);
    let ymax = 0;
    ids.forEach(id => { S[id].v.concat(S[id].hi).forEach(v => { if (fin(v) && v > ymax) ymax = v; }); });
    // the axis starts at zero: both scores are in degrees and are zero for a
    // perfect forecast, and a truncated axis would exaggerate the very gap
    // the figure is about
    ymax = Math.max(crps ? 0.5 : 1, ymax * 1.08);
    const step = A.niceStep(ymax, 5);
    const y = A.scale(0, ymax, g.B, g.T);

    A.clear(svg, H);
    hatchDefs(svg);
    A.yAxis(svg, g, y, A.ticks(0, ymax, step), v => A.f1(v) + '°',
            crps ? 'CRPS, °F (lower is better)' : 'Mean absolute error, °F (lower is better)');
    // the partial-sample bins, hatched behind everything
    if (hmax > HATCH_ABOVE) {
      svg.appendChild(el('rect', { x: x(hmax + 0.5), y: g.T, width: x(HATCH_ABOVE + 0.5) - x(hmax + 0.5),
                                   height: g.B - g.T, fill: 'url(#' + HATCH_ID + ')', stroke: 'none',
                                   'pointer-events': 'none' }));
      svg.appendChild(txt('partial sample above ' + HATCH_ABOVE + ' h',
        { x: (x(hmax + 0.5) + x(HATCH_ABOVE + 0.5)) / 2, y: g.T + 12, 'text-anchor': 'middle', class: 'ax' }));
    }
    A.leadAxis(svg, g, x, hmax, 0, 'Hours before the end of the target day');

    // ---- the ForecastEx prediction market band, then the other systems, then the ForecastEx prediction market line on top
    const cx = hs.map(hh => x(hh));
    if (S.FX) A.band(svg, cx, S.FX.lo.map(v => (fin(v) ? y(v) : null)), S.FX.hi.map(v => (fin(v) ? y(v) : null)), 'var(--accent)');
    ids.filter(id => id !== 'FX').forEach(id => {
      const dash = crps ? A.ensDash(id) : null;
      stepLine(svg, x, y, hs, S[id].v, Object.assign({ stroke: A.color(id), 'stroke-width': crps ? 1.8 : A.width(id) },
                                                     dash ? { 'stroke-dasharray': dash } : {}));
    });
    if (S.FX) A.lineSeries(svg, cx, S.FX.v.map(v => (fin(v) ? y(v) : null)),
                           { stroke: A.color('FX'), 'stroke-width': A.width('FX') });
    // in the forecast-only view each alternative forecast system ends at its last live update
    if (raw) {
      ids.filter(id => id !== 'FX').forEach(id => {
        const s = S[id];
        if (!fin(s.lastLiveH)) return;
        const i = hs.indexOf(s.lastLiveH);
        if (i < 0 || !fin(s.v[i])) return;
        svg.appendChild(el('circle', { cx: x(s.lastLiveH - 0.5), cy: y(s.v[i]), r: 3.2, fill: A.color(id),
                                       stroke: 'var(--panel)', 'stroke-width': 1.2, 'pointer-events': 'none' }));
      });
    }
    // the ForecastEx prediction market named on the figure, under its own left end where the other systems are not
    if (S.FX) {
      const i0 = S.FX.v.findIndex(v => fin(v));
      if (i0 >= 0) A.label(svg, x(hs[i0]) + 4, y(S.FX.v[i0]) + 14, A.name('FX'), A.color('FX'));
    }

    // ---- two strips under the axis, the sample and the beats count per bin
    const of = beats.reduce((m, b) => (b && fin(b.of) ? Math.max(m, b.of) : m), 0) || (ids.length - 1);
    // in the forecast-only view an alternative forecast system drops out of the count once its last
    // live update has passed, so the denominator shrinks toward the day's end
    const ofVaries = beats.some(b => b && fin(b.of) && b.of > 0 && b.of !== of);
    const s1 = g.B + 48, s2 = s1 + 24, sh = 20;
    svg.appendChild(txt('city-days', { x: g.L - 8, y: s1 + 13.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9.5 }));
    // the varying denominator needs two lines to stay inside the left margin
    if (ofVaries) {
      svg.appendChild(txt('beats, of', { x: g.L - 8, y: s2 + 9, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
      svg.appendChild(txt(raw ? 'live record' : 'scored', { x: g.L - 8, y: s2 + 18.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
    } else {
      svg.appendChild(txt('beats, of ' + of, { x: g.L - 8, y: s2 + 13.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9.5 }));
    }
    hs.forEach((hh, i) => {
      const x0 = x(hh + 0.5), w = x(hh - 0.5) - x0;
      const n = nPer[i], b = beats[i];
      svg.appendChild(el('rect', { x: x0, y: s1, width: w, height: sh, fill: 'var(--shade)', stroke: 'var(--panel)',
                                   'stroke-width': 1, 'pointer-events': 'none' }));
      if (fin(n)) svg.appendChild(txt(String(n), { x: x0 + w / 2, y: s1 + 13.5, 'text-anchor': 'middle', 'font-size': 8.5,
                                                   fill: n < 30 ? 'var(--muted)' : 'var(--ink)', 'pointer-events': 'none' }));
      // the beats cell darkens with the share of systems the ForecastEx prediction market came in under
      const share = b && fin(b.k) && b.of ? b.k / b.of : null;
      svg.appendChild(el('rect', { x: x0, y: s2, width: w, height: sh, fill: share == null ? 'var(--shade)' : 'var(--accent)',
                                   'fill-opacity': share == null ? 1 : 0.08 + 0.42 * share, stroke: 'var(--panel)',
                                   'stroke-width': 1, 'pointer-events': 'none' }));
      if (share != null) svg.appendChild(txt(String(b.k), { x: x0 + w / 2, y: s2 + 13.5, 'text-anchor': 'middle',
                                                            'font-size': 8.5, fill: 'var(--ink)', 'pointer-events': 'none' }));
    });

    // ---- hover, one column per bin over the frame and both strips
    const guide = el('rect', { x: 0, y: g.T, width: 0, height: s2 + sh - g.T, fill: 'var(--ink)', 'fill-opacity': 0.06,
                               stroke: 'none', 'pointer-events': 'none', visibility: 'hidden' });
    svg.appendChild(guide);
    const viewText = (state.metric === 'high' ? 'Highs' : 'Lows') + ', ' + (crps ? 'CRPS' : 'MAE') + ', '
      + cohortName(state.cohort).toLowerCase()
      + (crps ? '' : ', ' + (raw ? 'forecast only' : 'what a reader held'))
      + (state.frame === 'cli' ? ', climate-report frame' : '');
    hs.forEach((hh, i) => {
      const x0 = x(hh + 0.5), w = x(hh - 0.5) - x0;
      const r = el('rect', { x: x0, y: g.T, width: w, height: s2 + sh - g.T, fill: 'transparent', stroke: 'none' });
      r.addEventListener('mouseenter', () => { guide.setAttribute('x', x0); guide.setAttribute('width', w); guide.setAttribute('visibility', 'visible'); });
      r.addEventListener('mouseleave', () => guide.setAttribute('visibility', 'hidden'));
      A.hover(r, () => binTip(i, hh, ids, S, nPer[i], beats[i], notes[i], viewText, crps));
      svg.appendChild(r);
    });

    // ---- the key and the method note follow the view
    if (keyEl) {
      const note = (raw ? 'A dot marks each alternative forecast system’s last live update for the day. ' : '')
        + 'The band is the ForecastEx prediction market’s 95 percent bootstrap interval.';
      if (crps) {
        A.key(keyEl, ids, { note, dashes: true,
          since: id => { const s = id === 'FX' ? A.span(file.meta, 'FX', state.metric) : A.ensSpan(id, state.metric);
                         return s && s.start ? 'since ' + A.mdy(s.start) : ''; } });
      } else {
        A.key(keyEl, ids, { note, meta: file.meta, metric: state.metric });
      }
    }
    if (crps) crpsNote(methEl); else methodNote(methEl);
  }

  /* The tooltip is a standings table for the hour under the cursor: every
     system ordered by its score there, and how much worse than ForecastEx it
     is as a percentage. The order is recomputed for each bin, so moving along
     the curve shows the ranking change with lead rather than holding one
     fixed order the reader has to decode. */
  function binTip(i, hh, ids, S, n, b, note, viewText, crps) {
    const title = hh === 0 ? 'The hour the day ends' : hh + ' hour' + (hh === 1 ? '' : 's') + ' before the day ends';
    let sub = viewText + '. ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample') + (fin(n) && n < 30 ? ', under the 30 the bins need' : '') + '.';
    if (b && fin(b.k)) sub += ' ForecastEx beats ' + b.k + ' of ' + b.of + ' systems.';
    const fx = S.FX ? S.FX.v[i] : null;
    // worse than ForecastEx by this share of its score; a system with the
    // smaller score shows a negative number, which is the market being beaten
    const rel = v => (fin(v) && fin(fx) && fx > 0 ? 100 * (v - fx) / fx : null);
    const relTxt = v => (v == null ? A.dash : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(0) + '%');
    const entries = ids.map(id => ({ id, v: S[id].v[i], n: S[id].n ? S[id].n[i] : n }));
    entries.sort((p, q) => (fin(p.v) ? p.v : Infinity) - (fin(q.v) ? q.v : Infinity));
    let rows = '';
    entries.forEach((e, k) => {
      rows += '<tr' + (e.id === 'FX' ? ' class="tfx"' : '') + '><td>' + (k + 1)
        + '</td><td>' + A.swatch(e.id).replace(A.name(e.id), A.short(e.id))
        + '</td><td>' + A.f2(e.v)
        + '</td><td>' + (e.id === 'FX' ? '—' : relTxt(rel(e.v)))
        + '</td><td>' + A.int(e.n) + '</td></tr>';
    });
    let foot = '';
    if (S.FX && fin(S.FX.lo[i]) && fin(S.FX.hi[i])) foot += 'ForecastEx band ' + A.iv(S.FX.lo[i], S.FX.hi[i], A.f2) + '. ';
    if (note && Array.isArray(note.dates)) {
      foot += 'Partial sample, dates ' + note.dates[0] + ' to ' + note.dates[1];
      const z = note.zones || {};
      const zs = Object.keys(z).map(tz => '<span title="' + tz + '">' + zoneShort(tz) + '</span> ' + A.int(z[tz]));
      if (zs.length) foot += ', zones ' + zs.join(', ');
      foot += '.';
    }
    return '<b>' + title + '</b><div class="tsub">' + sub + '</div>'
      + '<table class="l3"><tr><th>#</th><th>System</th><th>' + (crps ? 'CRPS' : 'MAE') + ' °F</th><th>vs ForecastEx</th><th>n</th></tr>'
      + rows + '</table>' + (foot ? '<div class="tf">' + foot + '</div>' : '');
  }

  const HATCH_RULE = 'Bins above 30 hours are hatched because a ladder lists at a fixed clock time, so the eastern stations reach 36 hours before the western ones do, and hovering shows which dates and time zones fill those bins.';
  const CLI_SPAN = ' In the climate-report frame each alternative forecast system is scored against the National Weather Service report for the same date, the ForecastEx prediction market keeps the settle it pays on, and Buckley Field drops out because Denver’s report stands in for it.';

  function sampleLine(meta) {
    const co = (meta.cohorts || {})[state.cohort] || {};
    const nCo = state.metric === 'high' ? co.n_high : co.n_low;
    const noun = { own: 'the ForecastEx prediction market’s own record',
                   fixed30: 'the fixed sample' }[state.cohort] || cohortName(state.cohort).toLowerCase();
    return 'Sample ' + (fin(nCo) ? A.int(nCo) : A.dash) + ' city-days in ' + noun
      + (co.from ? ' from ' + co.from : '') + ', each system drawn on the part of that record it covers. '
      + A.windowAndBuilt(meta) + '.';
  }

  function methodNote(container) {
    if (!container) return;
    const meta = file.meta || {};
    A.methodNote(container, {
      title: 'Mean absolute error by lead',
      body: [
        'Error is mean absolute error, the average gap in degrees between a system’s call and what the station recorded, at a given lead.',
        { tex: 'MAE_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\left| f_{s,i}(h) - o_i \\right|' },
        'where $f_{s,i}(h)$ is system $s$’s value for city-day $i$ at lead $h$, and $o_i$ is the settle.',
        'A system’s value at a given lead is its most recent reading at or before that moment. Once a high or low has actually occurred, no forecast is shown as implying something more extreme, values are held at the running observed extreme. The ForecastEx prediction market’s value is where its price ladder crosses fifty cents, rounded up for highs and down for lows to match whole-degree settlement.',
      ],
      rules: [
        'Bins run hourly from 36 hours before the day ends to zero, and a bin needs at least 30 matched city-days before it’s drawn.',
        'Two sets of days are available, every day on each system’s own record, or a fixed sample of the city-days the ForecastEx prediction market priced at every hour from 30 to 0.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates.',
        'The beats strip counts the systems the ForecastEx prediction market beat at that hour, meaning the 95 percent interval of the paired difference in error over the days both hold lies entirely in its favor.',
        HATCH_RULE,
      ],
      span: A.cohortSpanLine(meta, state.cohort) + (state.frame === 'cli' ? CLI_SPAN : ''),
      n: sampleLine(meta),
    });
  }

  /* The CRPS view's note. Every distribution is read over the same strikes
     at the same instant, so the market and an ensemble are one measurement
     on one grid; what differs is where the probabilities come from. */
  function crpsNote(container) {
    if (!container) return;
    const meta = file.meta || {};
    const ens = A.ENS_ROWS.concat(['AIFS']).map(id => A.ensSpan(id, state.metric)).filter(s => s && s.start)
      .map(s => s.start).sort();
    const ensFrom = ens.length ? ' The ensembles’ spreads are on record from ' + A.mdyY(ens[0]) + '.' : '';
    A.methodNote(container, {
      title: 'CRPS by lead',
      body: [
        'CRPS, the continuous ranked probability score, measures a whole forecast distribution against what happened, in degrees. It shrinks as probability gathers near the observed value, and a forecast that puts all its probability on one whole degree scores its absolute error, so it reads on the same scale as the MAE view.',
        { tex: 'CRPS_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\sum_{k} \\left( F_{s,i,h}(k) - \\mathbb{1}[o_i \\le k] \\right)^2' },
        'where $F_{s,i,h}(k)$ is system $s$’s probability that city-day $i$ settles at or below $k$ as it stood at lead $h$, $k$ runs over whole degrees across the ForecastEx prediction market’s strikes, and $o_i$ is the settle.',
        'For the ForecastEx prediction market $F$ is its price ladder, the Yes prices read as probabilities. For an ensemble it is a normal curve on the ensemble’s own mean and spread at the hour the day’s extreme falls, with nothing fitted and no bias removed. A strike the observations have already cleared is paid in full for every system, as every value on the page is held at the running observed extreme.',
      ],
      rules: [
        'Every system is scored at the same instant on the same strikes, on the city-days the ForecastEx prediction market priced at that hour, and each ensemble is drawn on the part of those days its record covers.',
        'Bins run hourly from 36 hours before the day ends to zero, and a bin needs at least 30 city-days before it’s drawn.',
        'Two sets of days are available, every day on record, or a fixed sample of the city-days the ForecastEx prediction market priced at every hour from 30 to 0.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates. The beats strip counts the ensembles the ForecastEx prediction market beat at that hour, meaning the 95 percent interval of the paired difference in CRPS over the days both hold lies entirely in its favor.',
        HATCH_RULE,
      ],
      span: A.cohortSpanLine(meta, state.cohort) + ensFrom + (state.frame === 'cli'
        ? ' In the climate-report frame each ensemble is scored against the National Weather Service report for the same date, the ForecastEx prediction market keeps the settle it pays on, and both are restricted to the city-days that hold a report.'
        : ''),
      n: sampleLine(meta),
    });
  }

  // ------------------------------------------------------------- controls
  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A.tabs(bar, SCORES, k => { state.score = k; render(); }, { initial: state.score, label: 'Score' });
    A.tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Days' });
    A.tabs(bar, VALUES, k => { state.value = k; render(); }, { initial: state.value, label: 'Value' });
    // the value choice belongs to the MAE view; CRPS is on the held value only
    valueGroup = bar.lastElementChild;
    A.tabs(bar, FRAMES, k => { state.frame = k; render(); }, { initial: state.frame, label: 'Frame' });
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
      return;
    }
    if (bar && !built) controls(bar);
    render();
  }

  return { draw, state };
})();
