/* Figure 4, the city map: where ForecastEx beats or trails a chosen tool.

   The lead curve and the grid pool every station; a reader who trades one
   city wants to know whether the market beat the tool there. Each station
   is a dot on the CONUS whose color is the percent improvement of the
   market over the tool in one reading window, on the paired city-days the
   two share, with the bootstrap interval deciding whether the dot earns a
   color at all. A second view shows the two mean absolute errors side by
   side on one shared scale, so a large improvement over a poor tool is not
   read as a small error of the market's own.

   Every number comes from map.json (docs/accuracy.md section 3). The
   module chooses a metric, a tool, a window, a frame and a view, draws
   what the file holds for that choice, and computes nothing beyond the
   color and the radius that carry the file's own values. */
window.WXAccMap = (() => {
  const { el, txt, h, $ } = WXC;
  const A = WXAcc;

  // The basemap's viewBox is 960 by 600 and Washington sits at y 12, so the
  // map is shifted down to leave one title line and one summary line above it.
  const TOP = 40, H = 600 + TOP;
  const WINDOWS = [
    { key: 'morning', label: 'Morning 06 to 12 local', title: 'The standing value held on the morning of the target day, 06 to 12 in the station clock' },
    { key: 'eve', label: 'Evening before, 18:00 local', title: 'The standing value held at 18:00 station time on the day before' },
    { key: 'newsletter', label: 'Newsletter 5 PM to 5 AM ET', title: 'The hours the daily letter is written and read, 5 PM to 5 AM Eastern' },
  ];
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'The tool scored against the settle the contracts pay on' },
    { key: 'cli', label: 'NWS climate report', title: 'The tool scored against the National Weather Service climate report for the same date' },
  ];
  const MODES = [
    { key: 'pi', label: 'Improvement', title: 'Percent improvement of ForecastEx over the tool, paired' },
    { key: 'mae', label: 'Absolute error', title: 'Both mean absolute errors on one shared scale' },
  ];
  // "the National Weather Service" but "GFS MOS": a name that opens with an
  // acronym takes no article
  const withArticle = id => (/^[A-Z]{2,}/.test(A.name(id)) ? '' : 'the ') + A.name(id);
  const WNAME = { morning: 'morning 06 to 12 local', eve: 'evening before at 18:00 local', newsletter: 'newsletter hours, 5 PM to 5 AM ET' };
  const FNAME = { metar: 'METAR settle frame', cli: 'NWS climate report frame' };

  let D = null, base = null, basePending = null;
  const sel = { metric: 'high', tool: 'NDFD', win: 'morning', frame: 'metar', mode: 'pi' };
  let scale = null;

  // ------------------------------------------------------------- readers
  const cellsOf = () => {
    const m = D && D.map && D.map.metric && D.map.metric[sel.metric];
    const w = m && m.window && m.window[sel.win];
    return w || null;
  };
  const toolCells = () => { const w = cellsOf(); const f = w && w.frame && w.frame[sel.frame]; return (f && f[sel.tool]) || null; };
  const medianOf = () => { const w = cellsOf(); const f = w && w.median && w.median[sel.frame]; return (f && f[sel.tool]) || null; };
  // Buckley Field has no climate report of its own; Denver's stands in, so
  // the contract excludes it from that frame and the dot is drawn hollow
  const excluded = id => sel.frame === 'cli' && id === 'KBKF';
  const straddles = c => c.lo != null && c.hi != null && c.lo <= 0 && c.hi >= 0;

  /* The scales are fixed over the whole file, every metric, window, frame
     and tool, so a change of control changes the dots and never the key. The
     percent cap is the largest improvement seen, rounded up to a ten; the
     radius runs on the largest matched count; the error scale on the largest
     MAE either system posted anywhere. */
  function fitScales() {
    let piMax = 0, nMax = 0, maeMax = 0;
    const metric = (D.map && D.map.metric) || {};
    Object.keys(metric).forEach(mk => {
      const wins = (metric[mk] && metric[mk].window) || {};
      Object.keys(wins).forEach(wk => {
        const frames = (wins[wk] && wins[wk].frame) || {};
        Object.keys(frames).forEach(fk => {
          const tools = frames[fk] || {};
          Object.keys(tools).forEach(tk => {
            const cities = tools[tk] || {};
            Object.keys(cities).forEach(ck => {
              const c = cities[ck];
              if (!c) return;
              if (c.pi != null && isFinite(c.pi)) piMax = Math.max(piMax, Math.abs(c.pi));
              if (c.matched != null) nMax = Math.max(nMax, c.matched);
              if (c.fx && c.fx.mae != null) maeMax = Math.max(maeMax, c.fx.mae);
              if (c.tool && c.tool.mae != null) maeMax = Math.max(maeMax, c.tool.mae);
            });
          });
        });
      });
    });
    scale = { piCap: Math.max(20, Math.ceil(piMax / 10) * 10), nMax: Math.max(30, nMax),
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
  const minusPct = v => (v == null || isNaN(v) ? A.dash : (v < 0 ? '−' : '') + A.f1(Math.abs(v)) + '%');
  const pctIv = (lo, hi) => (lo == null || hi == null ? A.dash : minusPct(lo) + ' to ' + minusPct(hi));
  // the whole percent inside a dot, signed after rounding so a small value
  // never prints as a signed zero
  const wholePct = v => { if (v == null || isNaN(v)) return A.dash; const r = Math.round(v); return (r > 0 ? '+' : r < 0 ? '−' : '') + Math.abs(r); };

  // ------------------------------------------------------------- controls
  function controls(bar) {
    bar.innerHTML = '';
    A.metricTabs(bar, k => { sel.metric = k; render(); }, sel.metric);
    // the tool is a select: seventeen names are too many for a row of tabs
    const group = h('span', { class: 'tabgroup' });
    group.appendChild(h('span', { class: 'tl', text: 'Tool' }));
    const s = h('select', { class: 'acc-map-sel', 'aria-label': 'Tool' });
    const og1 = h('optgroup', { label: 'Panel tools' });
    A.PANEL.forEach(id => og1.appendChild(h('option', { value: id, text: A.name(id) })));
    const og2 = h('optgroup', { label: 'Extra sources, own span' });
    A.EXTRA.forEach(id => og2.appendChild(h('option', { value: id, text: A.name(id) })));
    s.appendChild(og1); s.appendChild(og2);
    s.value = sel.tool;
    s.onchange = () => { sel.tool = s.value; render(); };
    group.appendChild(s);
    bar.appendChild(group);
    A.tabs(bar, WINDOWS, k => { sel.win = k; render(); }, { initial: sel.win, label: 'Window' });
    A.tabs(bar, FRAMES, k => { sel.frame = k; render(); }, { initial: sel.frame, label: 'Frame' });
    A.tabs(bar, MODES, k => { sel.mode = k; render(); }, { initial: sel.mode, label: 'View' });
  }

  // ------------------------------------------------------------- tooltip
  function tipHtml(city, c, hollowWhy) {
    const tip = A.tooltip();
    const title = city.name + ', ' + city.id;
    const foot = WNAME[sel.win] + ', ' + FNAME[sel.frame];
    if (!c) return tip.rows(title, [['Improvement', 'no value']], hollowWhy + '. ' + foot);
    return tip.rows(title, [
      ['ForecastEx MAE', A.deg1(c.fx && c.fx.mae)],
      [A.name(sel.tool) + ' MAE', A.deg1(c.tool && c.tool.mae)],
      ['Improvement', signedPct(c.pi)],
      ['95 percent interval', pctIv(c.lo, c.hi)],
      ['Matched city-days', A.int(c.matched)],
      ['ForecastEx changes in the window', A.f1(c.fxChanges)],
      [A.short(sel.tool) + ' changes in the window', A.f1(c.toolChanges)],
    ], (straddles(c) ? 'The interval covers zero, so the dot is grey. ' : '') + foot);
  }

  // ------------------------------------------------------------- drawing
  function render() {
    const svg = $('#accMap'); if (!svg) return;
    const cells = toolCells(), med = medianOf();
    const cities = ((D.map && D.map.cities) || []).filter(c => c.px != null && c.py != null);
    A.clear(svg, H);
    const metricWord = sel.metric === 'high' ? 'highs' : 'lows';

    // the title line and the summary line above the map
    svg.appendChild(txt('ForecastEx against ' + withArticle(sel.tool) + ' on ' + metricWord + ', '
      + WNAME[sel.win] + ', ' + FNAME[sel.frame],
      { x: 16, y: 15, 'font-size': 13, 'font-weight': 700, fill: 'var(--navy)' }));
    let summary;
    if (!cells) summary = 'The file carries no values for this tool in this frame.';
    else if (med && med.pi != null) {
      summary = 'Median improvement across cities ' + signedPct(med.pi)
        + (med.lo != null && med.hi != null ? ', interval ' + pctIv(med.lo, med.hi) : '')
        + ', ' + A.int(med.colored) + ' of ' + cities.length + ' cities colored';
      if (sel.mode === 'mae') summary += '. Left dot ForecastEx, right dot the tool, darker is the larger error';
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
      const hollowWhy = !cells ? 'The tool carries no values in this frame'
        : excluded(city.id) ? 'Denver’s climate report stands in for Buckley Field, which the climate frame excludes'
        : 'Under 30 matched city-days for this tool';
      const ink = { 'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)', class: 'lbl', 'text-anchor': 'middle' };
      if (!c) {
        // hollow: the station is on the exchange, the tool has nothing to score here
        dot.appendChild(el('circle', { cx: X, cy: Y, r, fill: 'var(--panel)', stroke: 'var(--muted)',
                                       'stroke-width': 1.2, 'stroke-dasharray': '2.5 2' }));
        placed.push([X - r, Y - r, X + r, Y + r]);
      } else if (sel.mode === 'pi') {
        const grey = straddles(c) || c.pi == null;
        dot.appendChild(el('circle', { cx: X, cy: Y, r: r + 2, fill: 'var(--panel)', 'fill-opacity': 0.9 }));
        dot.appendChild(el('circle', { cx: X, cy: Y, r, fill: grey ? 'var(--line)' : piFill(c.pi),
                                       stroke: grey ? 'var(--rule)' : 'var(--ink)', 'stroke-width': grey ? 1 : 0.7 }));
        dot.appendChild(txt(wholePct(c.pi), Object.assign({ x: X, y: Y + 3.4 }, ink)));
        placed.push([X - r - 2, Y - r - 2, X + r + 2, Y + r + 2]);
      } else {
        // the pair: ForecastEx on the left in the accent ring, the tool on the
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
        key.appendChild(ramp(steps, 'Tool more accurate, −' + scale.piCap + '%', '+' + scale.piCap + '%, ForecastEx more accurate'));
        key.appendChild(sw('background:var(--line);border-color:var(--rule)', 'grey, the interval covers zero'));
      } else {
        const steps = [0, 0.25, 0.5, 0.75, 1].map(s => maeFill(s * scale.maeCap));
        key.appendChild(ramp(steps, 'Mean absolute error 0°', A.deg1(scale.maeCap) + ', one scale for both'));
        key.appendChild(sw('background:var(--panel);border:2px solid var(--accent)', 'left dot ForecastEx'));
        key.appendChild(sw('background:var(--panel);border:2px solid ' + A.color(sel.tool), 'right dot ' + A.name(sel.tool)));
      }
      key.appendChild(sw('background:var(--panel);border:1.2px dashed var(--muted)', 'hollow, under 30 matched city-days or excluded from the frame'));
      key.appendChild(h('span', { class: 'kn', text: 'Dot size follows the matched count. Colorado Springs was a test station the exchange never listed and Honolulu is outside the scope of this page, so neither is drawn.' }));
    }

    let n = 0, k = 0;
    rows.forEach(({ c }) => { if (c && c.matched != null) { n += c.matched; k++; } });
    A.methodNote($('#accMapMethod'), {
      title: 'Percent improvement by city, paired on the same city-days',
      equation: 'MAE_s,c = mean over the matched city-days of city c, in the window, of |value_s − settle|\n'
        + 'PI_c    = 100 × (MAE_tool,c − MAE_FX,c) / MAE_tool,c',
      rules: [
        'A city-day is matched when the tool has a value and the market has a crossing in the window, and a city with fewer than 30 matched city-days for the tool is hollow.',
        'The windows are the morning of the target day, 06 to 12 in the station clock, the evening before at 18:00 station time, and the newsletter hours, 5 PM to 5 AM Eastern.',
        'Positive PI_c means ForecastEx had the smaller error. The dot is grey when the bootstrap interval of the paired difference covers zero, and the printed number is PI_c, or each system’s MAE in the absolute-error view.',
        'Intervals are 95 percent percentile bootstrap over target dates, 1,000 draws, seed 20260910, with the market minus tool differences resampled under the same draws.',
        'The contracts pay on the METAR settle and the tools are scored against it by default. The climate-report frame scores the tool against the National Weather Service climate report for the same date, a different definition of the day’s extreme that runs about a degree warmer on highs, so the gap between the frames is a definition difference. Denver’s climate report stands in for Buckley Field, which is hollow in that frame.',
      ],
      n: cells ? 'Sample ' + A.int(n) + ' matched city-days across ' + k + ' cities in this view.'
               : 'The file carries no values for this tool in this frame.',
    });
  }

  // ------------------------------------------------------------- entry
  function draw(data) {
    D = data;
    const host = $('#accMap');
    if (!host) return;
    if (!D || !D.map || !D.map.metric) { A.notYet(host, A.NOT_PUBLISHED); return; }
    fitScales();
    // the first tool in the file that the select can name, when the default is absent
    const first = toolCells() ? sel.tool : A.PANEL.concat(A.EXTRA).find(id => {
      const w = cellsOf(); return w && w.frame && w.frame[sel.frame] && w.frame[sel.frame][id];
    });
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
