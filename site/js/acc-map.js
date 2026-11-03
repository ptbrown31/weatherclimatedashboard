/* The city map: where ForecastEx beats or trails a chosen series, as the working paper's Fig 8 draws it.

   The lead curve and the grid pool every station; a reader who trades one city wants to know whether
   the ForecastEx prediction market beat the series there. Each station is a dot on the CONUS whose
   color is the market's percent improvement over the series at one lead, on the paired city-days the
   two share, each against its own standard, and whose outline is bold where the difference holds after
   Holm's adjustment across the series at that station. A second view shows the two mean absolute
   errors side by side on one shared scale, so a large improvement over a poor series is not read as a
   small error of the market's own.

   Every number comes from map.json (docs/accuracy.md section 3). The module chooses a metric, a series,
   a lead and a view, draws what the file holds for that choice, and computes nothing beyond the color
   and the radius that carry the file's own values. */
window.WXAccMap = (() => {
  const { el, txt, h, $ } = WXC;
  const A = WXAcc;

  // The basemap's viewBox is 960 by 600 and Washington sits at y 12, so the
  // map is shifted down to leave one title line and one summary line above it.
  const TOP = 40, H = 600 + TOP;
  // the paper's four single leads (its Fig 8), two counted back from the end of the target day and two from
  // the report that set the day's extreme
  const LEADS = [
    { key: 'abs_30', label: '30 h', title: '30 hours before the end of the target day, 6 PM local the evening before' },
    { key: 'abs_18', label: '18 h', title: '18 hours before the end of the target day, 6 AM local on the day' },
    { key: 'rel_18', label: '18 h before the extreme', title: '18 hours before the last report that reached the day’s extreme' },
    { key: 'rel_12', label: '12 h before the extreme', title: '12 hours before the last report that reached the day’s extreme' },
  ];
  const MODES = [
    { key: 'pi', label: 'Improvement', title: 'Percent improvement of the ForecastEx prediction market over the series, paired' },
    { key: 'mae', label: 'Absolute error', title: 'Both mean absolute errors on one shared scale' },
  ];
  // "the National Weather Service" but "GFS MOS": a name that opens with an
  // acronym takes no article
  // a series' qualifier goes in brackets in a sentence: "the National Weather Service (climate report)"
  const inSentence = id => A.name(id).replace(/, (.+)$/, ' ($1)');
  const withArticle = id => (/^[A-Z]{2,}/.test(A.name(id)) ? '' : 'the ') + inSentence(id);
  const LNAME = { abs_30: '30 hours before the day ends', abs_18: '18 hours before the day ends',
                  rel_18: '18 hours before the extreme', rel_12: '12 hours before the extreme' };

  let D = null, base = null, basePending = null;
  const sel = { metric: 'high', tool: 'NDFD', lead: 'abs_30', mode: 'pi' };
  let scale = null;

  // ------------------------------------------------------------- readers
  // a cell is [pi, lo, hi, n, market MAE, series MAE, sig], sig 1 better, -1 worse, 0 neither after Holm
  const asCell = a => (a ? { pi: a[0], lo: a[1], hi: a[2], matched: a[3], fx: { mae: a[4] }, tool: { mae: a[5] }, sig: a[6] } : null);
  const blockOf = (met, lead, id) => {
    const m = D && D.map && D.map.metric && D.map.metric[met];
    return (m && m[lead] && m[lead][id]) || null;
  };
  function toolCells() {
    const b = blockOf(sel.metric, sel.lead, sel.tool);
    if (!b || !b.cells) return null;
    const out = {};
    Object.keys(b.cells).forEach(c => { out[c] = asCell(b.cells[c]); });
    return out;
  }
  const medianOf = () => (blockOf(sel.metric, sel.lead, sel.tool) || {}).median || null;
  // Buckley Field has no climate report of its own, so a series scored against the report has no value there
  const OWN_MAX = ['NDFD', 'GFS_MOS', 'NAM_MOS', 'NBS_MOS'];
  const excluded = id => id === 'KBKF' && OWN_MAX.includes(sel.tool);

  /* The scales are fixed over the whole file, every metric, lead and series, so a change of control
     changes the dots and never the key. The percent cap is the largest improvement seen, rounded up to
     a ten; the radius runs on the largest matched count; the error scale on the largest MAE either side
     posted anywhere. */
  function fitScales() {
    let piMax = 0, nMax = 0, maeMax = 0;
    const metric = (D.map && D.map.metric) || {};
    Object.keys(metric).forEach(mk => {
      Object.keys(metric[mk] || {}).forEach(lk => {
        const series = metric[mk][lk] || {};
        Object.keys(series).forEach(sk => {
          const cells = (series[sk] && series[sk].cells) || {};
          Object.keys(cells).forEach(ck => {
            const c = cells[ck];
            if (!c) return;
            if (c[0] != null && isFinite(c[0])) piMax = Math.max(piMax, Math.abs(c[0]));
            if (c[3] != null) nMax = Math.max(nMax, c[3]);
            if (c[4] != null) maeMax = Math.max(maeMax, c[4]);
            if (c[5] != null) maeMax = Math.max(maeMax, c[5]);
          });
        });
      });
    });
    scale = { piCap: Math.max(20, Math.min(100, Math.ceil(piMax / 10) * 10)), nMax: Math.max(30, nMax),
              maeCap: Math.max(1, Math.ceil(maeMax * 2) / 2) };
  }
  // the dot grows with the square root of the matched count, so area, which
  // the eye reads, follows the sample
  const radius = n => 10 + 7 * Math.sqrt(Math.min(1, Math.max(0, (n || 0) / scale.nMax)));
  // percent of the theme hue mixed into the panel color; a floor keeps a
  // small but real difference visible against the land
  const mix = (hue, share) => 'color-mix(in srgb, ' + hue + ' ' + Math.round(18 + 82 * Math.min(1, Math.max(0, share))) + '%, var(--panel))';
  const piFill = pi => mix(pi >= 0 ? 'var(--ok)' : 'var(--bad)', Math.abs(pi) / scale.piCap);
  const maeFill = mae => mix('var(--navy)', mae / scale.maeCap);
  const signedPct = v => (v == null || isNaN(v) ? A.dash : (v > 0 ? '+' : v < 0 ? '−' : '') + A.f1(Math.abs(v)) + '%');
  const pctIv = (lo, hi) => (lo == null || hi == null ? A.dash : signedPct(lo) + ' to ' + signedPct(hi));
  // the whole percent inside a dot, signed after rounding so a small value
  // never prints as a signed zero
  const wholePct = v => { if (v == null || isNaN(v)) return A.dash; const r = Math.round(v); return (r > 0 ? '+' : r < 0 ? '−' : '') + Math.abs(r); };

  // ------------------------------------------------------------- controls
  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { sel.metric = k; render(); }, sel.metric);
    // the series is a select, in the paper's groups: twenty-five names are too many for a row of tabs
    const group = h('span', { class: 'tabgroup' });
    group.appendChild(h('span', { class: 'tl', text: 'Compare with' }));
    const s = h('select', { class: 'acc-map-sel', 'aria-label': 'Series compared with the market' });
    A.systemGroups(A.ORDER()).forEach(g => {
      const og = h('optgroup', { label: g.title });
      g.ids.forEach(id => og.appendChild(h('option', { value: id, text: A.name(id) })));
      s.appendChild(og);
    });
    s.value = sel.tool;
    s.onchange = () => { sel.tool = s.value; render(); };
    group.appendChild(s);
    bar.appendChild(group);
    A.tabs(bar, LEADS, k => { sel.lead = k; render(); }, { initial: sel.lead, label: 'Lead' });
    A.tabs(bar, MODES, k => { sel.mode = k; render(); }, { initial: sel.mode, label: 'View' });
  }

  // ------------------------------------------------------------- tooltip
  function tipHtml(city, c, hollowWhy) {
    const tip = A.tooltip();
    const title = city.name + ', ' + city.id;
    const foot = LNAME[sel.lead] + '. ' + A.qual(sel.tool).replace(/^./, m => m.toUpperCase()) + '.';
    if (!c || c.pi == null) return tip.rows(title, [['Improvement', 'no value']], hollowWhy + '. ' + foot);
    return tip.rows(title, [
      ['ForecastEx MAE', A.deg1(c.fx && c.fx.mae)],
      [A.name(sel.tool) + ' MAE', A.deg1(c.tool && c.tool.mae)],
      ['Improvement', signedPct(c.pi)],
      ['95 percent interval', pctIv(c.lo, c.hi)],
      ['After Holm', c.sig === 1 ? 'the market is better' : c.sig === -1 ? 'the series is better' : 'no difference'],
      ['Matched city-days', A.int(c.matched)],
    ], foot);
  }

  // ------------------------------------------------------------- drawing
  function render() {
    const svg = $('#accMap'); if (!svg) return;
    const cells = toolCells(), med = medianOf();
    const cities = ((D.map && D.map.cities) || []).filter(c => c.px != null && c.py != null);
    A.clear(svg, H);
    const metricWord = sel.metric === 'high' ? 'highs' : 'lows';

    // the title line and the summary line above the map
    svg.appendChild(txt('ForecastEx against ' + withArticle(sel.tool) + ' on ' + metricWord + ', ' + LNAME[sel.lead],
      { x: 16, y: 15, 'font-size': 13, 'font-weight': 700, fill: 'var(--navy)' }));
    let summary;
    if (!cells) summary = 'The file carries no values for this series at this lead.';
    else if (med && med.pi != null) {
      const sig = Object.keys(cells).filter(k => cells[k] && cells[k].sig === 1 && !excluded(k)).length;
      summary = 'Median improvement ' + signedPct(med.pi) + ' across the ' + A.int(med.cities) + ' cities with at least 30 matched city-days'
        + (med.lo != null && med.hi != null ? ', interval ' + pctIv(med.lo, med.hi) : '')
        + '; the market is better after Holm at ' + sig + ' of them';
      if (sel.mode === 'mae') summary += '. Left dot ForecastEx, right dot the system, darker is the larger error';
    } else summary = 'No median published for this selection';
    svg.appendChild(txt(summary, { x: 16, y: 31, class: 'axl', fill: 'var(--muted)' }));

    const g = el('g', { transform: 'translate(0 ' + TOP + ')' });
    svg.appendChild(g);
    if (base && base.statePaths) {
      g.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': 1 }));
    } else {
      g.appendChild(txt('The state outlines did not load, so the stations are drawn on their own.', { x: 16, y: 20, class: 'axl', fill: 'var(--muted)' }));
    }

    // dots first, the largest under the smallest so no dot is buried
    const placed = [];
    const rows = cities.map(city => {
      const c = cells && !excluded(city.id) ? (cells[city.id] || null) : null;
      const r = c ? radius(c.matched) : 9;
      // the box a dot occupies: a circle, or the pair's rounded box
      const hw = sel.mode === 'mae' && c ? 2 * r + 3 : r + 2, hh = r + 2;
      return { city, c, r, X: city.px, Y: city.py, hw, hh };
    }).sort((a, b) => b.r - a.r);
    relax(rows);
    rows.forEach(row => {
      const { city, c, r, X, Y } = row;
      const dot = el('g', { class: 'acc-map-dot' });
      // a dot pushed off its station keeps a leader back to the point it stands for
      const dx = X - city.px, dy = Y - city.py, dd = Math.hypot(dx, dy);
      if (dd > 2.5) {
        dot.appendChild(el('line', { x1: city.px, y1: city.py, x2: X, y2: Y, stroke: 'var(--muted)', 'stroke-width': 1 }));
        dot.appendChild(el('circle', { cx: city.px, cy: city.py, r: 1.8, fill: 'var(--muted)', stroke: 'none' }));
      }
      const hollowWhy = !cells ? 'The series carries no values at this lead'
        : excluded(city.id) ? 'Buckley Field has no climate report of its own, so a series scored against the report has no value there'
        : 'Under 30 matched city-days for this series';
      const ink = { 'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)', class: 'lbl', 'text-anchor': 'middle' };
      if (!c || c.pi == null) {
        // hollow: the station is on the exchange, the system has nothing to score here
        dot.appendChild(el('circle', { cx: X, cy: Y, r, fill: 'var(--panel)', stroke: 'var(--muted)',
                                       'stroke-width': 1.2, 'stroke-dasharray': '2.5 2' }));
        placed.push([X - r, Y - r, X + r, Y + r]);
      } else if (sel.mode === 'pi') {
        // the outline is bold where the difference holds after Holm, as the paper outlines it
        const bold = c.sig === 1 || c.sig === -1;
        dot.appendChild(el('circle', { cx: X, cy: Y, r: r + 2, fill: 'var(--panel)', 'fill-opacity': 0.9 }));
        dot.appendChild(el('circle', { cx: X, cy: Y, r, fill: piFill(c.pi),
                                       stroke: 'var(--ink)', 'stroke-width': bold ? 2.4 : 0.6 }));
        dot.appendChild(txt(wholePct(c.pi), Object.assign({ x: X, y: Y + 3.4 }, ink)));
        placed.push([X - r - 2, Y - r - 2, X + r + 2, Y + r + 2]);
      } else {
        // the pair: ForecastEx on the left in the accent ring, the system on the
        // right in its own color, both filled on the one error scale
        const fxMae = c.fx && c.fx.mae, tMae = c.tool && c.tool.mae;
        const xl = X - r - 1, xr = X + r + 1;
        dot.appendChild(el('rect', { x: xl - r - 2, y: Y - r - 2, width: 4 * r + 6, height: 2 * r + 4, rx: r + 2,
                                     fill: 'var(--panel)', 'fill-opacity': 0.9 }));
        dot.appendChild(el('circle', { cx: xl, cy: Y, r, fill: fxMae == null ? 'var(--line)' : maeFill(fxMae),
                                       stroke: 'var(--accent)', 'stroke-width': 2 }));
        dot.appendChild(el('circle', { cx: xr, cy: Y, r, fill: tMae == null ? 'var(--line)' : maeFill(tMae),
                                       stroke: A.color(sel.tool), 'stroke-width': 2 }));
        dot.appendChild(txt(A.f1(fxMae), Object.assign({ x: xl, y: Y + 3.4 }, ink)));
        dot.appendChild(txt(A.f1(tMae), Object.assign({ x: xr, y: Y + 3.4 }, ink)));
        placed.push([xl - r - 2, Y - r - 2, xr + r + 2, Y + r + 2]);
      }
      A.hover(dot, () => tipHtml(city, c, hollowWhy));
      g.appendChild(dot);
    });

    /* City names by a collision placer.

       Each name tries the positions around its dot in turn, nearest first,
       and keeps the first that overlaps neither a dot nor a name already
       placed nor the edge of the map. The box is measured with getBBox,
       which needs the text in the document, so the name is appended before
       it is judged and removed when it fails. A station whose every
       position collides goes unnamed rather than overprinted; its hover
       still names it. */
    const bounds = [2, 2, A.W - 2, 600 - 2];
    const hit = b => b[0] < bounds[0] || b[1] < bounds[1] || b[2] > bounds[2] || b[3] > bounds[3]
      || placed.some(q => b[0] < q[2] && q[0] < b[2] && b[1] < q[3] && q[1] < b[3]);
    rows.forEach(({ city, c, r, X, Y }) => {
      // in the pair view the box is twice as wide, so the side positions start further out
      const rx = sel.mode === 'mae' && c ? 2 * r + 1 : r;
      const cands = [];
      [4, 12, 22].forEach(d => {
        cands.push([rx + d, 3.5], [-(rx + d), 3.5], [0, -(r + d + 1)], [0, r + d + 8]);
        cands.push([rx + d, -(r + 2)], [-(rx + d), -(r + 2)], [rx + d, r + 8], [-(rx + d), r + 8]);
      });
      for (const [dx, dy] of cands) {
        const t = txt(city.name, { x: X + dx, y: Y + dy, class: 'lbl', 'font-size': 9.5, 'font-weight': 700,
                                   fill: 'var(--ink)', 'text-anchor': dx < 0 ? 'end' : dx > 0 ? 'start' : 'middle' });
        g.appendChild(t);
        const b = t.getBBox();
        const bb = [b.x - 1, b.y - 1, b.x + b.width + 1, b.y + b.height + 1];
        if (!hit(bb)) { placed.push(bb); break; }
        t.remove();
      }
    });

    keyAndNote(cells, rows);
  }

  /* Dots pushed apart where stations sit closer than a dot is wide.

     Austin and San Antonio are thirty pixels apart on this map, and the
     corridor from Washington to Boston packs four stations into the width
     of two dots, so drawing every dot on its station would print one over
     another. Each overlapping pair is moved apart along the shorter axis
     of the overlap, half each, until no two boxes meet or forty passes have
     run; the dot then carries a leader back to the station's own point, so
     the position on the map is still the station's. Every box is kept
     inside the map frame. */
  function relax(rows) {
    const pad = 3;
    for (let pass = 0; pass < 40; pass++) {
      let moved = false;
      for (let i = 0; i < rows.length; i++) {
        for (let j = i + 1; j < rows.length; j++) {
          const a = rows[i], b = rows[j];
          const dx = b.X - a.X, dy = b.Y - a.Y;
          const ox = a.hw + b.hw + pad - Math.abs(dx), oy = a.hh + b.hh + pad - Math.abs(dy);
          if (ox <= 0 || oy <= 0) continue;
          if (ox < oy) { const s = (dx < 0 ? -1 : 1) * ox / 2; a.X -= s; b.X += s; }
          else { const s = (dy < 0 ? -1 : 1) * oy / 2; a.Y -= s; b.Y += s; }
          moved = true;
        }
      }
      if (!moved) break;
    }
    rows.forEach(r => {
      r.X = Math.min(A.W - r.hw - 2, Math.max(r.hw + 2, r.X));
      r.Y = Math.min(600 - r.hh - 2, Math.max(r.hh + 2, r.Y));
    });
  }

  // ------------------------------------------------------------- key and note
  function keyAndNote(cells, rows) {
    const key = $('#accMapKey');
    if (key) {
      key.innerHTML = '';
      const sw = (style, text) => {
        const e = h('span');
        e.appendChild(h('i', { class: 'acc-map-sw', style }));
        e.appendChild(document.createTextNode(text));
        return e;
      };
      const ramp = (fills, left, right) => {
        const e = h('span');
        e.appendChild(document.createTextNode(left));
        const bar = h('span', { class: 'acc-map-ramp' });
        fills.forEach(f => bar.appendChild(h('i', { class: 'acc-map-sw', style: 'background:' + f })));
        e.appendChild(bar);
        e.appendChild(document.createTextNode(right));
        return e;
      };
      if (sel.mode === 'pi') {
        const steps = [-1, -0.66, -0.33, 0.33, 0.66, 1].map(s => piFill(s * scale.piCap));
        key.appendChild(ramp(steps, 'Series more accurate, −' + scale.piCap + '%', '+' + scale.piCap + '%, ForecastEx more accurate'));
        key.appendChild(sw('background:var(--panel);border:2.4px solid var(--ink)', 'bold outline, the difference holds after Holm'));
      } else {
        const steps = [0, 0.25, 0.5, 0.75, 1].map(s => maeFill(s * scale.maeCap));
        key.appendChild(ramp(steps, 'Mean absolute error 0°', A.deg1(scale.maeCap) + ', one scale for both'));
        key.appendChild(sw('background:var(--panel);border:2px solid var(--accent)', 'left dot ForecastEx'));
        const ts = A.since(D.map && D.map.meta, sel.tool, sel.metric);
        key.appendChild(sw('background:var(--panel);border:2px solid ' + A.color(sel.tool), 'right dot ' + A.name(sel.tool) + (ts ? ', ' + ts : '')));
      }
      key.appendChild(sw('background:var(--panel);border:1.2px dashed var(--muted)', 'hollow, under 30 matched city-days or no value for the series there'));
      key.appendChild(h('span', { class: 'kn', text: 'Dot size follows the number of matched city-days.' }));
    }

    let n = 0, k = 0;
    rows.forEach(({ c }) => { if (c && c.matched != null) { n += c.matched; k++; } });
    A.methodNote($('#accMapMethod'), {
      title: 'Percent improvement by city',
      body: [
        'Each city is scored on the city-days where both the series and the ForecastEx prediction market have a value at the chosen lead, so the comparison is always paired on the same days, each side against its own standard.',
        { tex: 'PI_{s,c} = 100 \\times \\left(1 - \\frac{MAE_{FX,c}}{MAE_{s,c}}\\right)' },
        'where $MAE_{s,c}$ is series $s$’s mean absolute error over the matched city-days of city $c$, against the observation it is built to predict, and $MAE_{FX,c}$ is the ForecastEx prediction market’s on the same days against the METAR settle. A positive $PI_{s,c}$ means the market had the smaller error in that city. The outline is bold where the paired difference holds at the 5 percent level after Holm’s adjustment across the series compared at that city and lead.',
      ],
      rules: [
        'The four leads are the working paper’s, 30 and 18 hours before the end of the target day, 6 PM the evening before and 6 AM on the day, and 18 and 12 hours before the last report that reached the day’s extreme.',
        'A city with fewer than 30 matched city-days is hollow. Buckley Field has no climate report of its own, only Denver’s, so it drops out of the series scored against the report and is drawn hollow.',
        'A series’ value is its most recent forecast at or before that moment, held at the running observed extreme, with the trailing station bias correction on the grid-box series. The ForecastEx prediction market’s value is its median, where its ladder of Yes prices crosses fifty cents, left as quoted. The settle is the station’s highest or lowest METAR reading of the day rounded to the nearest whole degree.',
        'Intervals are 95 percent intervals over 1,000 resamples of seven-day blocks of target dates, with the market and the series resampled together so both sides of the ratio move under the same draws.',
        'The median above the map is taken over every city with at least 30 matched city-days, and its interval is the spread of that median across the same resamples.',
      ],
      span: A.spanLine(D.map && D.map.meta, ['FX', sel.tool], sel.metric, { lead: 'Records run from' }),
      n: cells ? 'Sample ' + A.int(n) + ' matched city-days across ' + k + ' cities in this view.'
               : 'The file carries no values for this series at this lead.',
    });
  }

  // ------------------------------------------------------------- entry
  function draw(data) {
    D = data;
    const host = $('#accMap');
    if (!host) return;
    if (!D || !D.map || !D.map.metric) { A.notYet(host, A.unpublished(D)); return; }
    fitScales();
    // the first series in the file that the select can name, when the default is absent
    const first = toolCells() ? sel.tool : A.ORDER().find(id => id !== 'FX' && blockOf(sel.metric, sel.lead, id));
    if (first) sel.tool = first;
    const bar = $('#accMapBar');
    if (bar) controls(bar);
    if (!basePending) {
      basePending = fetch(WXC.asset('basemap.json')).then(r => r.json()).then(b => { base = b; }).catch(() => { base = null; });
    }
    render();
    basePending.then(render);
  }
  return { draw };
})();
