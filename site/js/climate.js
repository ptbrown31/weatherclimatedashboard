/* The climate page: the settlement-basis series as NOAA and partners
   publish them, with a drag-to-fit trend tool. With the market layer on,
   contract markers sit at (expiration, threshold), coloured by the Yes
   price. Hover: a dot on the series at the cursor's year with the value,
   the ten-year change and the latest point; markers carry the quote and
   pin on click; threshold lines list the expirations that carry them. */
window.WXClimate = (() => {
  const { el, txt, h, $ } = WXC;
  const RAMP = ['#8b0000', '#d62728', '#ff7f0e', '#ffd700', '#adff2f', '#3ddc84', '#40e0d0', '#4fc3f7', '#1f77b4', '#00008b'];
  const X0 = { tempAnnual: 2000, tempMonthly: 2000, seaLevel: 1993, co2: 1995, amoc: 2004 };
  const PANELS = [
    ['tempAnnual', 'Global temperature, annual', '°C above preindustrial'],
    ['tempMonthly', 'Global temperature, monthly', '°C above preindustrial'],
    ['seaLevel', 'Global mean sea level', 'mm, satellite altimetry'],
    ['co2', 'Atmospheric CO2', 'ppm, Mauna Loa'],
    ['amoc', 'AMOC overturning at 26°N', 'Sv, RAPID array annual mean'],
  ];
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  let tip = null;

  function priceColor(p) {
    if (p == null) return 'var(--line)';
    const t = Math.max(0, Math.min(1, p)) * (RAMP.length - 1), i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = s => [1, 3, 5].map(k => parseInt(s.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((a, k) => Math.round(a + (B[k] - a) * f)).join(',') + ')';
  }

  // ---- formatting for the hover text
  const fv = v => (v == null ? '—' : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2));
  const sgn = v => (v == null ? '—' : (v > 0 ? '+' : '') + fv(v));
  const cents = v => (v == null ? '—' : Math.round(v * 100) + '¢');
  const size = v => (v == null ? '' : ' ×' + Math.round(v));
  // decimal years carry the month as (m − ½)/12, the NOAA convention; whole years stay as they are
  const yearLabel = y => (Number.isInteger(y) ? String(y) : MON[Math.max(0, Math.min(11, Math.floor((y - Math.floor(y)) * 12 + 1e-6)))] + ' ' + Math.floor(y));
  const expLabel = s => { const m = /^(\d{4})(\d{2})(\d{2})$/.exec(s || ''); return m ? MON[+m[2] - 1] + ' ' + (+m[3]) + ', ' + m[1] : null; };
  // the point nearest a year, by binary search on the sorted series
  function nearest(ser, yr) {
    let lo = 0, hi = ser.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ser[m][0] < yr) lo = m; else hi = m; }
    return Math.abs(ser[lo][0] - yr) <= Math.abs(ser[hi][0] - yr) ? lo : hi;
  }

  function panel(host, key, title, unit, ser, product, offsetC, source, opts) {
    opts = opts || {};
    // the panel is drawn by this page and by any other that reuses it, so it
    // owns its tooltip rather than depending on this page's init having run
    tip = tip || WXC.tooltip();
    // presentation choices the climate page derives from its own series keys;
    // another page passes them in
    const fmtThr = opts.fmtThreshold || (v => (key.startsWith('temp') ? v.toFixed(2) : String(v)));
    // values here span a fraction of a percent to millions; the default rule
    // suits the climate series and another page passes its own
    const fmtV = opts.fmt || fv;
    const fmtAx = opts.fmtAxis || (v => (v >= 100 ? v.toFixed(0) : v.toFixed(2)));
    const thrSuffix = opts.thresholdSuffix != null ? opts.thresholdSuffix : (key.startsWith('temp') ? '°C' : '');
    const div = h('div', { class: 'panel' });
    div.appendChild(h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: title }));
    div.appendChild(h('div', { class: 'psub cap', style: 'margin:2px 2px 6px', text: unit + (product ? ' · markers: ' + WXM.LABEL : '') }));
    const svg = el('svg', { viewBox: '0 0 960 330', class: 'ts' }); div.appendChild(svg);
    const note = h('div', { class: 'note', style: 'display:none;margin:6px 0 0;font-size:12px' }); div.appendChild(note);
    host.appendChild(div);

    const cs = product ? product.contracts : [];
    const unitShort = unit.split(/[ ,]/)[0];
    const W = 960, L = 56, R = 910, T = 16, B = 296;
    const x0 = opts.x0 != null ? opts.x0 : (X0[key] != null ? X0[key] : Math.min(...cs.map(c => c.year)) - 4);
    // The climate ladders run to the 2040s, so the axis is given room to the
    // right regardless of what is listed today. A category whose contracts
    // settle within a year or two would spend half its width on empty years, so
    // it can ask for an axis that ends just past the last thing drawn.
    const lastX = Math.max(...cs.map(c => c.year), (ser.length ? ser[ser.length - 1][0] : 0));
    const x1 = opts.tightRight ? lastX + Math.max(0.5, (lastX - x0) * 0.04)
                               : Math.max(...cs.map(c => c.year), new Date().getUTCFullYear() + 5) + 3;
    const pts = ser.filter(q => q[0] >= x0);
    const vals = pts.map(q => q[1]).concat(cs.map(c => c.threshold));
    if (!vals.length) { svg.appendChild(txt('No data available.', { x: L, y: T + 16, class: 'axl' })); return; }
    let lo = Math.min(...vals);
    const hi = Math.max(...vals), pad = (hi - lo) * 0.08 || 1;
    // a quantity that cannot go below zero should not be given a negative axis:
    // generation, consumption and production all bottom out at nothing
    const floorAtZero = opts.clampZero && lo >= 0 && lo - pad < 0;
    const yLo = floorAtZero ? 0 : lo - pad, yHi = hi + pad;
    const X = v => L + (v - x0) / (x1 - x0) * (R - L), Y = v => B - (v - yLo) / (yHi - yLo) * (B - T);
    const last = ser[ser.length - 1];
    const latestText = fmtV(last[1]) + ' ' + unitShort + ' (' + yearLabel(last[0]) + ')';

    // a decade tick is right for a century of history and leaves a short window
    // with two labels on it
    const xStep = (x1 - x0) <= 25 ? 5 : 10;
    for (let yr = Math.ceil(x0 / xStep) * xStep; yr <= x1; yr += xStep) {
      svg.appendChild(el('line', { x1: X(yr), x2: X(yr), y1: T, y2: B, class: 'grid' }));
      svg.appendChild(txt(yr, { x: X(yr), y: B + 16, 'text-anchor': 'middle', class: 'ax' }));
    }
    // threshold lines, each with a hit band; hover lists the expirations listed at that level
    const thr = [...new Set(cs.map(c => c.threshold))].sort((a, b) => a - b);
    const thrTip = v => {
      const at = cs.filter(c => c.threshold === v);
      const seen = {}; const rows = [];
      at.forEach(c => { if (seen[c.expiryLabel]) return; seen[c.expiryLabel] = 1; rows.push([c.expiryLabel, cents(c.yes)]); });
      return tip.rows('threshold ' + fmtThr(v) + ' ' + unit, [['Listed for', rows.length + (rows.length === 1 ? ' expiration' : ' expirations')], ...rows.map(r => [r[0], 'Yes ' + r[1]])],
        'series now ' + fmtV(last[1]) + ' ' + unitShort + ' (' + sgn(last[1] - v) + ' vs threshold)');
    };
    let lastLabY = 1e9;
    thr.slice().reverse().forEach(v => {
      svg.appendChild(el('line', { x1: L, x2: R, y1: Y(v), y2: Y(v), class: 'grid', 'stroke-dasharray': '5 4' }));
      const hit = el('line', { x1: L, x2: R, y1: Y(v), y2: Y(v), stroke: 'transparent', 'stroke-width': 5, 'data-tip': '1' });
      hit.onmousemove = e => tip.show(e, thrTip(v)); hit.onmouseleave = () => tip.hide();
      svg.appendChild(hit);
      if (Math.abs(Y(v) - lastLabY) < 11) return;
      lastLabY = Y(v);
      const lab = txt(fmtThr(v) + thrSuffix, { x: R + 4, y: Y(v) + 3.5, class: 'ax', 'data-tip': '1' });
      lab.onmousemove = e => tip.show(e, thrTip(v)); lab.onmouseleave = () => tip.hide();
      svg.appendChild(lab);
    });
    const yt = 5, step = (yHi - yLo) / yt;
    for (let i = 0; i <= yt; i++) { const v = yLo + i * step; svg.appendChild(txt(fmtAx(v), { x: L - 8, y: Y(v) + 3.5, 'text-anchor': 'end', class: 'ax' })); }
    /* Settled record, then the publisher's own estimate.

       A department publishes a figure for a period before that period is over
       and keeps revising it, so the tail of a series is not history: the crop
       lanes carry a world yield for a marketing year still under way. Drawn as
       one line it ran straight through the strikes listed against it, which said
       the answer was already known when the contract had not settled.

       Which years are still open needs no calendar, because the exchange states
       it: a contract is listed for a year only while that year can still resolve.
       So the record is solid up to the first year with a contract on it and
       dashed from there, in the same colour, because it is the same source —
       just not final.

       This is asked for rather than assumed, because a listed contract does not
       always mean the publisher's figure is provisional. A monthly carbon
       dioxide reading is final for its month even while an annual contract for
       that year is still trading, and dashing it would say the reading might yet
       change when it will not. It is the crop lanes that carry a genuine
       projection: a world yield for a marketing year still under way.

       With no contracts, or with every contract past the end of the series, this
       is one unbroken line and nothing changes. */
    const firstOpen = (opts.unsettledFromContracts && cs.length)
      ? Math.min(...cs.map(c => c.year)) : Infinity;
    const settled = pts.filter(q => q[0] < firstOpen);
    const openPts = pts.filter(q => q[0] >= firstOpen);
    const draw = (qs, dash) => {
      if (qs.length < 2) return;
      svg.appendChild(el('path', Object.assign({
        d: qs.map((q, i) => (i ? 'L' : 'M') + X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join(''),
        fill: 'none', stroke: 'var(--obs)',
        'stroke-width': opts.lineWidth || (key === 'tempMonthly' ? 1 : 1.8) },
        dash ? { 'stroke-dasharray': '4 3', opacity: .75 } : {})));
    };
    draw(settled, false);
    // the join carries the last settled point so the two meet rather than gap
    draw(settled.length ? [settled[settled.length - 1]].concat(openPts) : openPts, true);
    // ---- the seasonal projection
    //
    // A monthly series that stops in June cannot be read against a strike for
    // next January without some idea of what January looks like, and for rain
    // or an average temperature the honest first answer is: what January has
    // usually been. So each calendar month is averaged and carried forward,
    // dashed, to reach the listed expirations.
    //
    // Two averages are computed and they can disagree. The recent decade is the
    // one drawn, because a series with any trend in it is dragged by its oldest
    // years; the full record is on the hover so the difference is visible rather
    // than buried in a choice made here. This is a climatology, not a forecast:
    // it carries no weather information about the month in question.
    let proj = [];
    if (opts.project && pts.length > 24) {
      const mOf = q => Math.max(0, Math.min(11, Math.round((q - Math.floor(q)) * 12 - 0.5)));
      const monthly = ser.filter(q => !Number.isInteger(q[0]));
      if (monthly.length > 24) {
        const lastX = monthly[monthly.length - 1][0];
        const byM = {}, byMRecent = {};
        monthly.forEach(q => {
          const m = mOf(q[0]);
          (byM[m] = byM[m] || []).push(q[1]);
          if (q[0] >= lastX - 10) (byMRecent[m] = byMRecent[m] || []).push(q[1]);
        });
        const mean = a => (a && a.length ? a.reduce((s2, v) => s2 + v, 0) / a.length : null);
        const endX = Math.max(...cs.map(c => c.year), lastX);
        for (let x = lastX + 1 / 12; x <= endX + 1e-6; x += 1 / 12) {
          const m = mOf(x), v = mean(byMRecent[m]) != null ? mean(byMRecent[m]) : mean(byM[m]);
          if (v == null) continue;
          proj.push({ x: Math.round(x * 10000) / 10000, v, m, full: mean(byM[m]), recent: mean(byMRecent[m]),
                      n: (byM[m] || []).length, nRecent: (byMRecent[m] || []).length });
        }
        if (proj.length) {
          const d2 = 'M' + X(lastX).toFixed(1) + ',' + Y(monthly[monthly.length - 1][1]).toFixed(1)
                   + proj.map(q => 'L' + X(q.x).toFixed(1) + ',' + Y(q.v).toFixed(1)).join('');
          svg.appendChild(el('path', { d: d2, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 1.6,
                                       'stroke-dasharray': '5 4', opacity: .9, 'pointer-events': 'none' }));
        }
      }
    }

    let dragHint = null;
    if (pts.length > 4) { dragHint = txt('← drag across the history to project a linear trend', { x: L + 8, y: T + 14, 'font-size': 11, fill: 'var(--accent)', 'font-weight': 600 }); svg.appendChild(dragHint); }

    const mono = opts.marker ? opts.marker === 'triangle' : key === 'tempMonthly';
    // a ladder of a dozen strikes in one year needs smaller markers than the two
    // or three a climate threshold set carries. Sizing is opt-in rather than
    // automatic: this page's panels were laid out at the fixed radius and are
    // left at it.
    const gaps = thr.slice(1).map((v, i) => Math.abs(Y(v) - Y(thr[i]))).filter(g => g > 0.01);
    const rad = opts.markerRadius === 'auto'
      ? Math.max(3, Math.min(8, gaps.length ? Math.min(...gaps) * 0.62 : 8))
      : (opts.markerRadius || 8);
    cs.forEach(c => {
      const col = priceColor(c.yes), cx = X(c.year), cy = Y(c.threshold);
      const m = mono ? el('path', { d: 'M' + cx + ' ' + (cy - 8) + ' L' + (cx - 8) + ' ' + (cy + 6) + ' L' + (cx + 8) + ' ' + (cy + 6) + ' Z', fill: col, stroke: 'var(--ink)', 'stroke-width': 1, 'data-tip': '1', 'data-tip-pin': '1' })
                     : el('circle', { cx, cy, r: rad, fill: col, stroke: 'var(--ink)', 'stroke-width': 1, 'data-tip': '1', 'data-tip-pin': '1' });
      const url = WXM.contractUrl(product.productConid, c.conidYes || c.conid);
      const noBid = c.ask == null ? null : Math.round((1 - c.ask) * 100) / 100;
      const book = c.bid != null && c.ask != null ? null : (c.bid != null ? 'Yes bids only; the Yes price shown is the Yes bid' : (c.ask != null ? 'No bids only; the Yes price shown is one dollar less the No bid' : 'no bids'));
      if (url) WXM.linkTo(m, url, 'Open ' + c.label + ' on IBKR');
      const html = () => tip.rows(product.name + ' — ' + c.label, [
        ['Settles', c.expiryLabel], ['Expires', /\d{1,2}, \d{4}/.test(c.expiryLabel || '') ? null : expLabel(c.expiration)],
        ['Yes price', cents(c.yes) + (c.bid != null && c.ask != null ? ' (midpoint)' : '')],
        ['Yes bid', c.bid != null ? cents(c.bid) + size(c.bidSize) : null],
        ['No bid', noBid != null ? cents(noBid) + size(c.askSize) : null],
        ['Buy Yes now at', c.ask != null ? cents(c.ask) + (WXM.payoutText(Math.round(c.ask * 100)) ? ' · pays ' + WXM.payoutText(Math.round(c.ask * 100)) : '') : null],
        ['Bids', c.from === 'no' ? (book ? book + '; ' : '') + 'quoted from the No contract' : book],
        ['Series now', fmtV(last[1]) + ' ' + unitShort + ' (' + sgn(last[1] - c.threshold) + ' vs ' + fmtThr(c.threshold) + ')'],
        ],
        (c.label2 || WXM.LABEL) + (url ? ' · click the marker to open the contract' : ''));
      m.onmousemove = e => tip.show(e, html());
      m.onmouseleave = () => tip.hide();
      m.onclick = e => tip.pin(e, html());
      m.style.cursor = 'pointer';
      svg.appendChild(m);
    });

    // ---- pointer state shared by the hover dot and the trend tool
    let drag = null;
    const toPt = e => { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()); };
    const toYear = e => x0 + (toPt(e).x - L) / (R - L) * (x1 - x0);

    // the series hover: a dot at the nearest point to the cursor's year; off while
    // dragging and over markers or threshold hits (those carry their own text)
    let dot = null;
    const clearDot = () => { if (dot) { dot.remove(); dot = null; } };
    const tenYear = q => { const i = nearest(ser, q[0] - 10); const p = ser[i]; return Math.abs(p[0] - (q[0] - 10)) <= 0.6 && p !== q ? q[1] - p[1] : null; };
    svg.addEventListener('mousemove', e => {
      if (drag != null || !pts.length || (e.target.closest && e.target.closest('[data-tip]'))) { clearDot(); if (drag != null) tip.hide(); return; }
      const p = toPt(e);
      if (p.x < L - 4 || p.x > R + 4 || p.y < T - 4 || p.y > B + 4) { clearDot(); tip.hide(); return; }
      const cur = x0 + (p.x - L) / (R - L) * (x1 - x0);
      // past the end of the record the line is the projection, and it has to say
      // so plainly: it is what this month has usually been, not a forecast
      if (proj.length && cur > proj[0].x - 1 / 24) {
        let k = 0;
        proj.forEach((r, i) => { if (Math.abs(r.x - cur) < Math.abs(proj[k].x - cur)) k = i; });
        const r = proj[k];
        if (!dot) { dot = el('circle', { r: 4.5, fill: 'var(--accent)', stroke: 'var(--panel)', 'stroke-width': 1.5, 'pointer-events': 'none' }); svg.appendChild(dot); }
        dot.setAttribute('cx', X(r.x)); dot.setAttribute('cy', Y(r.v));
        tip.show(e, tip.rows(title + ' — projection', [
          ['Month', MON[r.m] + ' ' + Math.floor(r.x)],
          ['Usual for this month', fmtV(r.v) + ' ' + unitShort],
          ['Last 10 ' + MON[r.m] + 's', r.recent == null ? null : fmtV(r.recent) + ' ' + unitShort + ' (' + r.nRecent + ')'],
          ['Whole record', r.full == null ? null : fmtV(r.full) + ' ' + unitShort + ' (' + r.n + ')'],
          ['Latest observed', latestText]],
          'the average this calendar month has been, carried forward. Not a forecast: it carries no information '
          + 'about the weather in that month.'));
        return;
      }
      const q = pts[nearest(pts, x0 + (p.x - L) / (R - L) * (x1 - x0))];
      if (!dot) { dot = el('circle', { r: 4.5, fill: 'var(--accent)', stroke: 'var(--panel)', 'stroke-width': 1.5, 'pointer-events': 'none' }); svg.appendChild(dot); }
      dot.setAttribute('cx', X(q[0])); dot.setAttribute('cy', Y(q[1]));
      const d10 = tenYear(q);
      // the sea-level series is a ten-day sample, not monthly: say "Date" and keep the decimal year
      const when = opts.xLabel ? [opts.xLabel, yearLabel(q[0])] : Number.isInteger(q[0]) ? ['Year', yearLabel(q[0])] : key === 'seaLevel' ? ['Date', yearLabel(q[0]) + ' (' + q[0].toFixed(3) + ')'] : ['Month', yearLabel(q[0])];
      const open = q[0] >= firstOpen;
      tip.show(e, tip.rows(title + (open ? ' — not yet settled' : ''),
        [when, [open ? 'Current estimate' : 'Value', fmtV(q[1]) + ' ' + unitShort],
        ['Change over 10 years', d10 == null ? null : sgn(d10) + ' ' + unitShort], ['Latest', latestText]],
        open ? 'the publisher revises this until the period closes, and a contract is listed against it, '
               + 'so it is an estimate rather than a settled figure' : source));
    });
    svg.addEventListener('mouseleave', () => { clearDot(); tip.hide(); drag = null; });
    document.addEventListener('mouseup', () => { drag = null; });

    // the Climate-at-a-Glance trend tool: drag to fit, dashed extrapolation
    if (pts.length > 4) {
      let selRect = null, fitG = null;
      svg.addEventListener('mousedown', e => { drag = toYear(e); });
      svg.addEventListener('mousemove', e => {
        if (drag == null) return;
        const a = Math.min(drag, toYear(e)), b = Math.max(drag, toYear(e));
        if (!selRect) { selRect = el('rect', { y: T, height: B - T, fill: 'rgba(59,111,181,.12)' }); svg.appendChild(selRect); }
        selRect.setAttribute('x', X(a)); selRect.setAttribute('width', X(b) - X(a));
      });
      svg.addEventListener('mouseup', e => {
        if (drag == null) return;
        const a = Math.min(drag, toYear(e)), b = Math.max(drag, toYear(e)); drag = null;
        if (b - a < 1) { if (selRect) { selRect.remove(); selRect = null; } return; }
        const w = pts.filter(q => q[0] >= a && q[0] <= b); if (w.length < 3) return;
        const n = w.length, sx = w.reduce((s, q) => s + q[0], 0), sy = w.reduce((s, q) => s + q[1], 0);
        const sxx = w.reduce((s, q) => s + q[0] * q[0], 0), sxy = w.reduce((s, q) => s + q[0] * q[1], 0);
        const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx), icpt = (sy - slope * sx) / n;
        if (dragHint) { dragHint.remove(); dragHint = null; }
        if (fitG) fitG.remove(); fitG = el('g'); svg.appendChild(fitG);
        const f = v => slope * v + icpt;
        fitG.appendChild(el('line', { x1: X(a), y1: Y(f(a)), x2: X(b), y2: Y(f(b)), stroke: 'var(--accent)', 'stroke-width': 2.4 }));
        fitG.appendChild(el('line', { x1: X(b), y1: Y(f(b)), x2: X(x1), y2: Y(f(x1)), stroke: 'var(--accent)', 'stroke-width': 1.8, 'stroke-dasharray': '6 4', opacity: .85 }));
        note.style.display = 'inline-block';
        const head = 'fit ' + Math.round(a) + '–' + Math.round(b) + ': ' + (slope * 10).toFixed(3) + ' per decade';
        // A handful of thresholds read best as the year the trend crosses each.
        // A ladder of thirty does not — that note runs to a paragraph and says
        // the same thing thirty times. There the useful form is inverted: what
        // the trend projects for each year contracts actually settle in, which
        // is the number to hold against that year's column of strikes.
        if (opts.trendNote === 'byYear') {
          const seen = {}, per = [];
          cs.forEach(c => {
            const lab = c.expiryLabel || String(c.year);
            if (seen[lab] || !(c.year > b)) return;
            seen[lab] = 1; per.push([c.year, lab]);
          });
          per.sort((u, v) => u[0] - v[0]);
          note.textContent = head + per.map(([x, lab]) => ' · ' + lab + ': ' + fmtThr(f(x)) + thrSuffix).join('');
        } else {
          const cross = thr.map(v => { const yr2 = (v - icpt) / slope; return (yr2 > b && yr2 < 2100 && slope !== 0) ? Math.round(yr2) : null; });
          note.textContent = head + thr.map((v, i) => cross[i] ? (' · crosses ' + fmtThr(v) + thrSuffix + ' in ' + cross[i]) : '').join('');
        }
      });
      svg.addEventListener('dblclick', () => { if (selRect) { selRect.remove(); selRect = null; } if (fitG) { fitG.remove(); fitG = null; } note.style.display = 'none'; });
    }
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('climate.json', 1440);
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440));
    const D = r.data;
    const host = $('#panels'); host.innerHTML = '';
    if (!D || !D.series) { host.appendChild(h('p', { class: 'cap', text: 'No data available.' })); return; }
    const off = D.offsetC || 0;
    const series = Object.assign({}, D.series);
    ['tempAnnual', 'tempMonthly'].forEach(k => { if (series[k]) series[k] = series[k].map(q => [q[0], Math.round((q[1] + off) * 1000) / 1000]); });
    await WXM.loadGroup('climate');
    const products = WXM.climateProducts(series, off);
    const byKey = {}; products.forEach(p => { byKey[p.seriesKey] = p; });
    PANELS.forEach(([k, title, unit]) => { if (series[k]) panel(host, k, title, unit, series[k], byKey[k], off, (D.sources || {})[k] || ''); });
    const notes = Object.entries(D.notes || {}).map(([k, v]) => k + ': ' + v).join('; ');
    $('#foot').textContent = 'Series: NCEI Climate at a Glance global land+ocean anomalies (+' + off + ' °C to the preindustrial baseline, the convention the contracts use), NOAA GML Mauna Loa CO2, NOAA/NESDIS STAR sea level altimetry, and the RAPID AMOC monitoring project (UK NERC) annual means.' + (notes ? ' ' + notes + '.' : '') + (WXM.on() ? (WXM.live() ? ' Markers are the exchange\'s listed contracts at the Yes midpoint, coloured by price.' : ' Markers are placeholders, not market values.') : '');
  }
  return { init, panel, priceColor };
})();
