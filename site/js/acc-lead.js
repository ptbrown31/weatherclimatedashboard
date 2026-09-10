/* Figure 1, the lead curve.

   Mean absolute error against the settle by hours before the end of the
   target day, the market against the eleven panel tools, on a matched sample
   so every line is judged on the same city-days at the same hour. The file is
   lead-curve.json (docs/accuracy.md section 3); nothing here is computed, the
   figure only chooses which of the builder's series to draw.

   Lead runs down to the right, so the day ends at the right edge and a curve
   is read the way the day is lived. The market is drawn through the bin
   centers with its bootstrap band because a ladder is quoted every ten
   minutes and its error moves smoothly within an hour. A tool is drawn as a
   step, one flat tread per hourly bin, because a tool's value changes only
   when a cycle lands and is otherwise the same record forward filled, so a
   line joining bin centers would draw motion the tool never had.

   Two views of the value. What a reader held is the default, the raw record
   floored at the running observed extreme (contract, standing value), which
   is the number anyone with the forecast and the observations in front of
   them was actually holding. Forecast only is the raw record, undefined after
   a tool's last live update for the day, so each tool's line ends where its
   forecasting stopped and a dot marks the spot.

   Bins above 30 h are hatched because the cohort there is partial (a ladder
   lists at a fixed clock time, so the eastern stations reach 36 h before the
   western ones do) and the hover names the dates and zones that fill them. */
window.WXAccLead = (() => {
  const { el, txt, h, $ } = WXC;
  const A = WXAcc;

  const COHORTS = [
    { key: 'matched11', label: 'All eleven', title: 'City-days where all eleven tools have a value and the market has a crossing at that hour' },
    { key: 'core5', label: 'Core five', title: 'City-days where the five core tools have a value and the market has a crossing at that hour' },
    { key: 'fixed30', label: 'Fixed cohort', title: 'City-days matched at every hour from 30 to 0, so every bin holds the same days' },
  ];
  const VALUES = [
    { key: 'held', label: 'What a reader held', title: 'The raw value floored at the running observed extreme, the number a reader with the observations was holding' },
    { key: 'raw', label: 'Forecast only', title: 'The raw record, undefined after each tool’s last live update for the day' },
  ];
  const MORE = [
    { key: 'off', label: 'Panel only', title: 'The eleven panel tools and the market' },
    { key: 'on', label: 'Six extra sources', title: 'Adds the extra sources on their own span, drawn faint, with the held value and no band' },
  ];
  const HATCH_ID = 'accLeadHatch';
  const HATCH_ABOVE = 30;

  const state = { metric: 'high', cohort: 'matched11', value: 'held', more: 'off' };
  let file = null, built = false;

  // ------------------------------------------------------------- data
  const fin = v => v != null && isFinite(v);
  const cohortName = key => (COHORTS.find(c => c.key === key) || {}).label || key;
  const cohortIds = key => A.COHORT[key === 'core5' ? 'core5' : 'matched11'];

  /* One drawable series per system for the current view.

     In the forecast-only view a tool's raw error is cut at lastLiveH: the
     file already leaves later bins null, and the cut here keeps the line
     honest against a builder that fills them. The own block has no raw
     value and no interval, so an extra source is the same in both views. */
  function seriesFor(block, sys, raw, hs) {
    const mae = (raw ? sys.maeRaw : sys.mae) || [];
    const lo = (raw ? sys.loRaw : sys.lo) || [];
    const hi = (raw ? sys.hiRaw : sys.hi) || [];
    const cut = raw && fin(sys.lastLiveH) ? sys.lastLiveH : null;
    const keep = i => (cut == null || hs[i] >= cut);
    return {
      mae: hs.map((_, i) => (keep(i) && fin(mae[i]) ? mae[i] : null)),
      lo: hs.map((_, i) => (keep(i) && fin(lo[i]) ? lo[i] : null)),
      hi: hs.map((_, i) => (keep(i) && fin(hi[i]) ? hi[i] : null)),
      lastLiveH: cut,
    };
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
    const block = file.metric && file.metric[state.metric];
    const series = block && block.cohorts && block.cohorts[state.cohort];
    if (!block || !series || !series.systems || !Array.isArray(block.h) || !block.h.length) {
      A.notYet(svg, 'This cohort is not in the published record.');
      if (keyEl) keyEl.innerHTML = '';
      return;
    }
    const raw = state.value === 'raw';
    const hs = block.h;
    const hmax = Math.max.apply(null, hs);
    const ids = cohortIds(state.cohort).filter(id => series.systems[id]);
    const S = {};
    ids.forEach(id => { S[id] = seriesFor(block, series.systems[id], raw, hs); });
    const own = block.own || {};
    const extras = state.more === 'on' ? A.EXTRA.filter(id => own[id] && Array.isArray(own[id].h)) : [];
    const beats = (raw ? series.beatsRaw : series.beats) || [];
    const nPer = series.n || [];
    const notes = series.binNote || [];

    // ---- geometry. The frame stops short so two strips fit under the axis.
    const H = 430;
    const g = A.frame(H, { L: 70, B: 298 });
    // half a bin of padding each side so the 36 h and 0 h treads are whole
    const x = A.scale(hmax + 0.5, -0.5, g.L, g.R);
    let ymax = 0;
    ids.forEach(id => { S[id].mae.concat(S[id].hi).forEach(v => { if (fin(v) && v > ymax) ymax = v; }); });
    extras.forEach(id => own[id].mae.forEach(v => { if (fin(v) && v > ymax) ymax = v; }));
    // the axis starts at zero: these are absolute errors, and a truncated
    // axis would exaggerate the very gap the figure is about
    ymax = Math.max(1, ymax * 1.08);
    const step = A.niceStep(ymax, 5);
    const y = A.scale(0, ymax, g.B, g.T);

    A.clear(svg, H);
    hatchDefs(svg);
    A.yAxis(svg, g, y, A.ticks(0, ymax, step), v => A.f1(v) + '°', 'Mean absolute error, °F (lower is better)');
    // the partial-cohort bins, hatched behind everything
    if (hmax > HATCH_ABOVE) {
      svg.appendChild(el('rect', { x: x(hmax + 0.5), y: g.T, width: x(HATCH_ABOVE + 0.5) - x(hmax + 0.5),
                                   height: g.B - g.T, fill: 'url(#' + HATCH_ID + ')', stroke: 'none',
                                   'pointer-events': 'none' }));
      svg.appendChild(txt('partial cohort above ' + HATCH_ABOVE + ' h',
        { x: (x(hmax + 0.5) + x(HATCH_ABOVE + 0.5)) / 2, y: g.T + 12, 'text-anchor': 'middle', class: 'ax' }));
    }
    A.leadAxis(svg, g, x, hmax, 0, 'Hours before the end of the target day');

    // ---- the extras first, faint, so the panel sits on top of them
    const extraLabels = [];
    extras.forEach(id => {
      const o = own[id];
      const p = stepLine(svg, x, y, o.h, o.mae, { stroke: A.color(id), 'stroke-width': 1.2, 'stroke-opacity': 0.75 });
      if (!p) return;
      const i0 = o.mae.findIndex(v => fin(v));
      if (i0 >= 0) extraLabels.push({ id, x: x(o.h[i0] + 0.5) + 3, y: y(o.mae[i0]) });
    });
    // labels for the extras, nudged apart where two lines start close
    extraLabels.sort((a, b) => a.y - b.y);
    for (let i = 1; i < extraLabels.length; i++) {
      if (extraLabels[i].y - extraLabels[i - 1].y < 11) extraLabels[i].y = extraLabels[i - 1].y + 11;
    }
    extraLabels.forEach(l => A.label(svg, l.x, l.y - 4, A.short(l.id), 'var(--t-extra)',
      { 'font-size': 9.5, 'font-weight': 600, 'text-anchor': 'start' }));

    // ---- the market band, then the tools, then the market line on top
    const cx = hs.map(hh => x(hh));
    if (S.FX) A.band(svg, cx, S.FX.lo.map(v => (fin(v) ? y(v) : null)), S.FX.hi.map(v => (fin(v) ? y(v) : null)), 'var(--accent)');
    ids.filter(id => id !== 'FX').forEach(id => {
      stepLine(svg, x, y, hs, S[id].mae, { stroke: A.color(id), 'stroke-width': A.width(id) });
    });
    if (S.FX) A.lineSeries(svg, cx, S.FX.mae.map(v => (fin(v) ? y(v) : null)),
                           { stroke: A.color('FX'), 'stroke-width': A.width('FX') });
    // in the forecast-only view each tool ends at its last live update
    if (raw) {
      ids.filter(id => id !== 'FX').forEach(id => {
        const s = S[id];
        if (!fin(s.lastLiveH)) return;
        const i = hs.indexOf(s.lastLiveH);
        if (i < 0 || !fin(s.mae[i])) return;
        svg.appendChild(el('circle', { cx: x(s.lastLiveH - 0.5), cy: y(s.mae[i]), r: 3.2, fill: A.color(id),
                                       stroke: 'var(--panel)', 'stroke-width': 1.2, 'pointer-events': 'none' }));
      });
    }
    // the market named on the figure, under its own left end where the tools are not
    if (S.FX) {
      const i0 = S.FX.mae.findIndex(v => fin(v));
      if (i0 >= 0) A.label(svg, x(hs[i0]) + 4, y(S.FX.mae[i0]) + 14, A.name('FX'), A.color('FX'));
    }

    // ---- two strips under the axis, the sample and the beats count per bin
    const of = beats.reduce((m, b) => (b && fin(b.of) ? Math.max(m, b.of) : m), 0) || (ids.length - 1);
    // in the forecast-only view a tool drops out of the count once its last
    // live update has passed, so the denominator shrinks toward the day's end
    const ofVaries = beats.some(b => b && fin(b.of) && b.of > 0 && b.of !== of);
    const s1 = g.B + 48, s2 = s1 + 24, sh = 20;
    svg.appendChild(txt('city-days', { x: g.L - 8, y: s1 + 13.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9.5 }));
    // the varying denominator needs two lines to stay inside the left margin
    if (ofVaries) {
      svg.appendChild(txt('beats, of', { x: g.L - 8, y: s2 + 9, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
      svg.appendChild(txt('live tools', { x: g.L - 8, y: s2 + 18.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
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
      // the beats cell darkens with the share of tools the market came in under
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
    const viewText = (state.metric === 'high' ? 'Highs' : 'Lows') + ', ' + cohortName(state.cohort).toLowerCase() + ', '
      + (raw ? 'forecast only' : 'what a reader held');
    hs.forEach((hh, i) => {
      const x0 = x(hh + 0.5), w = x(hh - 0.5) - x0;
      const r = el('rect', { x: x0, y: g.T, width: w, height: s2 + sh - g.T, fill: 'transparent', stroke: 'none' });
      r.addEventListener('mouseenter', () => { guide.setAttribute('x', x0); guide.setAttribute('width', w); guide.setAttribute('visibility', 'visible'); });
      r.addEventListener('mouseleave', () => guide.setAttribute('visibility', 'hidden'));
      A.hover(r, () => binTip(i, hh, ids, S, series, extras, own, nPer[i], beats[i], notes[i], viewText, raw));
      svg.appendChild(r);
    });

    // ---- the key and the method note follow the view
    if (keyEl) {
      const note = (raw ? 'A dot marks each tool’s last live update for the day. ' : '')
        + (extras.length ? 'The six extra sources are drawn on their own span with the held value and no band. ' : '')
        + 'The band is the market’s 95 percent bootstrap interval.';
      A.key(keyEl, ids.concat(extras), { note });
    }
    methodNote(methEl);
  }

  function binTip(i, hh, ids, S, series, extras, own, n, b, note, viewText, raw) {
    const title = hh === 0 ? 'The hour the day ends' : hh + ' hour' + (hh === 1 ? '' : 's') + ' before the day ends';
    let sub = viewText + '. ' + (fin(n) ? A.int(n) + ' city-days' : 'no sample') + (fin(n) && n < 30 ? ', under the 30 the bins need' : '') + '.';
    if (b && fin(b.k)) sub += ' Market beats ' + b.k + ' of ' + b.of + ' tools.';
    let rows = '';
    const row = (id, mae, nn, age, cph) => '<tr><td>' + A.swatch(id).replace(A.name(id), A.short(id)) + '</td><td>' + A.f2(mae)
      + '</td><td>' + A.int(nn) + '</td><td>' + A.f1(age) + '</td><td>' + A.f2(cph) + '</td></tr>';
    ids.forEach(id => {
      const sys = series.systems[id] || {};
      rows += row(id, S[id].mae[i], n, (sys.ageMedianH || [])[i], (sys.changesPerHour || [])[i]);
    });
    extras.forEach(id => {
      const o = own[id], j = o.h.indexOf(hh);
      if (j < 0) return;
      rows += row(id, o.mae[j], o.n[j], null, null);
    });
    let foot = '';
    if (S.FX && fin(S.FX.lo[i]) && fin(S.FX.hi[i])) foot += 'ForecastEx band ' + A.iv(S.FX.lo[i], S.FX.hi[i], A.f2) + '. ';
    if (note && Array.isArray(note.dates)) {
      foot += 'Partial cohort, dates ' + note.dates[0] + ' to ' + note.dates[1];
      const z = note.zones || {};
      const zs = Object.keys(z).map(tz => '<span title="' + tz + '">' + zoneShort(tz) + '</span> ' + A.int(z[tz]));
      if (zs.length) foot += ', zones ' + zs.join(', ');
      foot += '.';
    }
    if (extras.length) foot += (foot ? ' ' : '') + 'Extra sources are on their own span' + (raw ? ' with the held value' : '') + '.';
    return '<b>' + title + '</b><div class="tsub">' + sub + '</div>'
      + '<table class="l3"><tr><th>System</th><th>MAE °F</th><th>n</th><th>age h</th><th>changes/h</th></tr>' + rows + '</table>'
      + (foot ? '<div class="tf">' + foot + '</div>' : '');
  }

  function methodNote(container) {
    if (!container) return;
    const meta = file.meta || {};
    const co = (meta.cohorts || {})[state.cohort] || {};
    const nCo = state.metric === 'high' ? co.n_high : co.n_low;
    const noun = { matched11: 'the all-eleven cohort', core5: 'the core-five cohort', fixed30: 'the fixed cohort' }[state.cohort]
      || cohortName(state.cohort).toLowerCase();
    const sample = 'Sample ' + (fin(nCo) ? A.int(nCo) : A.dash) + ' city-days in ' + noun
      + (co.from ? ' from ' + co.from : '') + '. ' + A.windowAndBuilt(meta) + '.';
    A.methodNote(container, {
      title: 'Mean absolute error by lead',
      body: [
        'Error is mean absolute error, the average gap in degrees between a system\u2019s call and what the station recorded, at a given lead.',
        { tex: 'MAE_s(h) = \\frac{1}{N_h}\\sum_{i=1}^{N_h} \\left| f_{s,i}(h) - o_i \\right|' },
        'where $f_{s,i}(h)$ is system $s$\u2019s value for city-day $i$ at lead $h$, and $o_i$ is the settle.',
        'A system\u2019s value at a given lead is its most recent reading at or before that moment. Once a high or low has actually occurred, no forecast is shown as implying something more extreme, values are held at the running observed extreme. The market\u2019s value is where its price ladder crosses fifty cents, rounded up for highs and down for lows to match whole-degree settlement.',
      ],
      rules: [
        'Bins run hourly from 36 hours before the day ends to zero, and a bin needs at least 30 matched city-days before it\u2019s drawn.',
        'Three cohorts are available, all eleven tools plus the market, a core five (National Weather Service, Blend, Aviation Forecast, European and American models) plus the market, or a fixed set of city-days matched at every hour from 30 to 0.',
        'Bands are 95 percent bootstrap intervals over 1,000 resamples of the target dates, seed 20260910.',
        'Hours beyond the shaded threshold draw on a partial cohort, since not every tool has data that far out, hovering shows which dates and time zones fill those bins.',
        'The six extra sources run on their own span, held at their last value, with no band.',
      ],
      n: sample,
    });
  }

  // ------------------------------------------------------------- controls
  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A.tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Cohort' });
    A.tabs(bar, VALUES, k => { state.value = k; render(); }, { initial: state.value, label: 'Value' });
    A.tabs(bar, MORE, k => { state.more = k; render(); }, { initial: state.more, label: 'More' });
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

  return { draw };
})();
