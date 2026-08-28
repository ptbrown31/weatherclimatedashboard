/* The climate page: the settlement-basis series as NOAA and partners
   publish them, with a drag-to-fit trend tool. With the market layer on,
   contract markers sit at (expiration, threshold), coloured by the Yes
   price. Hover: a dot on the series at the cursor's year with the value,
   the ten-year change and the latest point; markers carry the quote and
   pin on click; threshold lines list the expirations that carry them. */
window.WXClimate = (() => {
  const { el, txt, h, $ } = WXC;
  /* Price as colour: red at nothing, green at a dollar.

     A marker's colour is the Yes price, which on these contracts is the market's
     probability that the series ends above that strike. So the scale runs from
     red at no chance to green at a certainty, which is the same green and red
     the Yes and No language uses everywhere else on the site.

     The middle is yellow rather than a blend of the ends, and the ends are dark
     rather than saturated, so the ramp also varies in brightness. A reader who
     cannot separate red from green can still read it as light in the middle and
     dark at both ends, which a pure red-to-green fade does not give them. */
  const RAMP = ['#A3162A', '#C63A2E', '#E06A34', '#F09B3E', '#F5CE4C',
                '#D8DA4A', '#9BC63F', '#52A94A', '#157F3C'];
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
    const div = h('div', { class: 'panel' + (opts._full ? ' full' : '') });
    div.appendChild(h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: title }));
    const sub = h('div', { class: 'psub cap', style: 'margin:2px 2px 6px',
                           text: unit + (product ? ' · markers from ' + WXM.LABEL : '') });
    // the document that defines what this settles on, one click away
    const tl = product && product.id ? WXM.termsLink(product.id) : '';
    if (tl) sub.innerHTML += ' · ' + tl;
    // the same ladder, loaded into the position allocation calculator with its live
    // prices; the count products travel under their own route there
    if (product && product.id && opts.allocLink !== false) {
      const slug = ((window.WX && WX.nav && WX.nav.product) || {})[product.id];
      const m = (slug === 'tropical-cyclones' ? 'hur:' : 'prod:') + product.id;
      sub.innerHTML += ' · <a href="allocator.html?m=' + encodeURIComponent(m) + '">Position allocation calculator →</a>';
    }
    div.appendChild(sub);
    const ctl = h('div', { class: 'zoomrow' }); div.appendChild(ctl);
    const svg = el('svg', { class: 'ts' }); div.appendChild(svg);
    const note = h('div', { class: 'note', style: 'display:none;margin:6px 0 0;font-size:12px' }); div.appendChild(note);
    if (opts._before && opts._before.parentNode === host) host.insertBefore(div, opts._before);
    else host.appendChild(div);
    // the page behind must not scroll under a panel that covers it, and Escape
    // has to close it: a reader who cannot find the button should not be stuck
    let esc = null;
    document.body.classList.toggle('wtfull', !!opts._full);
    if (opts._full) {
      esc = ev => { if (ev.key === 'Escape') rebuild(opts.x0 == null ? undefined : opts.x0, undefined, false); };
      document.addEventListener('keydown', esc);
    }

    /* Redraw this panel over a shorter window.
       Zooming holds the right edge and moves the left one, because the right
       edge is where the contracts are and where the record has just got to; a
       zoom that recentred would walk the thing being looked at off the chart.
       The panel is rebuilt rather than rescaled so that everything derived from
       the window — the tick step, the marker radius, the axis floor — is derived
       again from the window actually shown. */
    const rebuild = (x0new, project, full) => {
      const before = div.nextSibling;
      if (esc) { document.removeEventListener('keydown', esc); esc = null; }
      div.remove();
      panel(host, key, title, unit, ser, product, offsetC, source,
            Object.assign({}, opts, { x0: x0new, _before: before,
                                      _project: project === undefined ? opts._project : project,
                                      _full: full === undefined ? opts._full : full }));
    };

    const cs = product ? product.contracts : [];
    const unitShort = unit.split(/[ ,]/)[0];
    /* The same panel, larger. Every coordinate below is in viewBox units, so
       opening one full-window is a matter of giving it a bigger box and letting
       the browser map it: the zoom, the projection, the markers and the hovers
       are the code they always were, drawn at a different size. */
    const FULL = !!opts._full;
    /* Full-window, the box follows the window.

       A fixed landscape box filled a desktop and left a phone with a chart an
       inch tall above half a screen of nothing: a shape that suits a wide window
       is the wrong shape for a tall one. So the box takes the aspect of the room
       it has, within limits — never wider than about two and a half to one,
       never taller than it is wide — and the layout below is derived from it
       rather than from constants. */
    let VW = 960, VH = 356;
    if (FULL) {
      const availH = Math.max(260, (window.innerHeight || 800) - 200);
      const availW = Math.max(300, (window.innerWidth || 1200) - 46);
      const asp = Math.max(0.4, Math.min(1.2, availH / availW));
      VW = availW < 620 ? 960 : 1600;
      VH = Math.round(VW * asp);
    }
    const W = VW, L = FULL ? 66 : 56, R = VW - (FULL ? 80 : 50),
          T = FULL ? 22 : 16, B = VH - (FULL ? 70 : 60);
    svg.setAttribute('viewBox', '0 0 ' + VW + ' ' + VH);
    const x0 = opts.x0 != null ? opts.x0 : (X0[key] != null ? X0[key] : Math.min(...cs.map(c => c.year)) - 4);
    // The climate ladders run to the 2040s, so the axis is given room to the
    // right regardless of what is listed today. A category whose contracts
    // settle within a year or two would spend half its width on empty years, so
    // it can ask for an axis that ends just past the last thing drawn.
    const lastX = Math.max(...cs.map(c => c.year), (ser.length ? ser[ser.length - 1][0] : 0));
    const x1 = opts.tightRight ? lastX + Math.max(0.5, (lastX - x0) * 0.04)
                               : Math.max(...cs.map(c => c.year), new Date().getUTCFullYear() + 5) + 3;
    /* The zoom control, sized to the record rather than fixed.

       Offering "50 years" on a series that starts in 2001 is a button that does
       nothing, so the choices are drawn from the span actually available, and
       the widest one is the whole record. Each window ends at the right edge and
       reaches back from it. */
    if (ctl && ser.length > 2) {
      const span = x1 - ser[0][0];
      const steps = (span <= 15 ? [2, 5, 10] : span <= 45 ? [5, 10, 20, 40] : [10, 25, 50, 100])
        .filter(n => n < span - 0.5);
      const cur = Math.round((x1 - x0) * 10) / 10;
      ctl.innerHTML = '';
      ctl.appendChild(h('span', { class: 'zl', text: 'Show' }));
      steps.concat([null]).forEach(n => {
        const label = n == null ? 'All' : n + 'y';
        const target = n == null ? ser[0][0] : x1 - n;
        const on = n == null ? Math.abs(x0 - ser[0][0]) < 0.6 : Math.abs(cur - n) < 0.6;
        const b = h('button', { class: 'zb' + (on ? ' on' : ''), text: label });
        b.onclick = () => rebuild(target);
        ctl.appendChild(b);
      });
      // the projection is off until asked for: it is a model, not a reading, and
      // a page that draws one unbidden invites it to be read as the record
      /* Ten readings is enough to fit a line and a band to.

         The gate was twenty-four, which suited the differenced model this
         page used to run and locks out an annual series like the RAPID AMOC
         record, twenty-one years long and the one climate series here with a
         contract settling forty years out. */
      if (window.WXForecast && ser.length >= 10) {
        const pb = h('button', { class: 'zb fc' + (opts._project ? ' on' : ''),
                                 text: opts._project ? 'Hide projection' : 'Project forward' });
        pb.onclick = () => rebuild(x0, !opts._project);
        ctl.appendChild(pb);
      }
      /* Full-window, and back again.

         The chart itself is not the control: dragging it fits a trend, a marker
         opens a contract and a double-click clears the fit, so a bare click
         would have to be told apart from three things a reader is already doing
         with the same button. This says what it does instead. */
      const xb = h('button', { class: 'zb ex' + (FULL ? ' on' : ''),
                               text: FULL ? 'Close' : 'Expand' });
      xb.onclick = () => rebuild(x0, undefined, !FULL);
      ctl.appendChild(xb);
      // an expanded panel carries its own way out in the corner: the control row
      // scrolls, and Escape only helps a reader who thinks to try it
      if (FULL) {
        const corner = h('button', { class: 'fullclose', title: 'Close (Esc)', 'aria-label': 'Close' });
        corner.textContent = '\u2715 Close';
        corner.onclick = () => rebuild(x0, undefined, false);
        div.appendChild(corner);
      }
    }
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

    // A decade tick is right for a century of history and leaves a five-year
    // window with one label on it, which is not an axis. The step follows the
    // window so a zoomed panel still says which years it is showing.
    const xSpan = x1 - x0;
    const xStep = xSpan <= 6 ? 1 : xSpan <= 12 ? 2 : xSpan <= 30 ? 5 : 10;
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
    /* A reading is a reading, so it gets a mark.

       These records are monthly, annual or weekly counts, and a bare line
       between them draws a value for every instant in between that nobody
       measured. A dot at each reading says where the record actually is.

       The dots appear when there is room for them to read as separate marks.
       Four hundred monthly points across eight hundred pixels are two pixels
       apart, and dotting them makes a thicker line rather than a clearer one —
       so on a crowded axis the line stands alone, and zooming in brings the
       readings out, which is a large part of what the zoom is for. */
    const spacing = pts.length > 1 ? (X(pts[pts.length - 1][0]) - X(pts[0][0])) / (pts.length - 1) : 99;
    const dotR = spacing >= 4.5 ? Math.min(3, Math.max(1.6, spacing / 3.6)) : 0;
    const draw = (qs, dash) => {
      if (qs.length < 2) return;
      svg.appendChild(el('path', Object.assign({
        d: qs.map((q, i) => (i ? 'L' : 'M') + X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join(''),
        fill: 'none', stroke: 'var(--obs)',
        'stroke-width': opts.lineWidth || (key === 'tempMonthly' ? 1 : 1.8) },
        dash ? { 'stroke-dasharray': '4 3', opacity: .75 } : {})));
      if (!dotR) return;
      qs.forEach(q => svg.appendChild(el('circle', {
        class: 'rdot', cx: X(q[0]).toFixed(1), cy: Y(q[1]).toFixed(1), r: dotR,
        fill: dash ? 'var(--panel)' : 'var(--obs)', stroke: 'var(--obs)',
        'stroke-width': dash ? 1.2 : 0, 'pointer-events': 'none' })));
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

    /* The projection, when it has been asked for.

       It is fitted to the whole record, not to the window on screen, because a
       five-year window on a monthly series is sixty points and a seasonal model
       wants more than that. It runs to the far edge of the listed strikes so a
       reader can hold it against every contract on the panel, and it carries its
       band, which is wide by the end and should be.

       Where the fit fails — too short a record, a singular system — the panel
       says so rather than drawing a line it does not stand behind. */
    if (opts._project && window.WXForecast) {
      const fc = WXForecast.project(ser, Math.max(x1, lastX));
      if (!fc || !fc.points.length) {
        note.style.display = 'inline-block';
        note.textContent = 'This record is too short to fit a projection to.';
      } else {
        const inWin = fc.points.filter(q => q.x <= x1 + 1e-6);
        if (inWin.length) {
          // the band is often wider than the panel: monthly rain a year out is
          // plus or minus most of a wet month. It is clipped to the plot rather
          // than allowed to stretch the axis, because rescaling every panel to
          // fit an uncertainty band would flatten the record it belongs to
          const cy = v => Math.max(T, Math.min(B, Y(v)));
          const band = inWin.map(q => X(q.x).toFixed(1) + ',' + cy(q.hi).toFixed(1)).join(' ')
            + ' ' + inWin.slice().reverse().map(q => X(q.x).toFixed(1) + ',' + cy(q.lo).toFixed(1)).join(' ');
          svg.appendChild(el('polygon', { points: band, fill: 'var(--fcst)', 'fill-opacity': .13,
                                          stroke: 'none', 'pointer-events': 'none' }));
          const last = ser[ser.length - 1];
          const d3 = 'M' + X(last[0]).toFixed(1) + ',' + Y(last[1]).toFixed(1)
            + inWin.map(q => 'L' + X(q.x).toFixed(1) + ',' + Y(q.v).toFixed(1)).join('');
          svg.appendChild(el('path', { d: d3, fill: 'none', stroke: 'var(--fcst)', 'stroke-width': 2,
                                       'stroke-dasharray': '7 3', 'pointer-events': 'none' }));
          if (dotR) inWin.forEach(q => svg.appendChild(el('circle', {
            class: 'rdot', cx: X(q.x).toFixed(1), cy: Y(q.v).toFixed(1), r: dotR, fill: 'var(--fcst)',
            'pointer-events': 'none' })));
        }
        note.style.display = 'inline-block';
        note.textContent = 'A straight line fitted to the last ' + fc.windowYears + ' years of the record'
          + (fc.seasonal ? ', with the average seasonal cycle laid on top' : '')
          + '. The shaded band is twice the scatter that fit leaves behind and does not widen: the model claims '
          + 'nothing beyond the recent line continued. The projection is fitted from the record and adds '
          + 'nothing to it.'
          + (fc.capped ? ' It stops short of the furthest strike.' : '');
      }
    }

    /* The key for that colour, on the panel rather than in a caption.

       Without it a reader has to guess whether green is dear or likely. It is
       drawn only where there are priced markers to explain. */
    if (cs.some(c => c.yes != null)) {
      const kw = FULL ? 200 : 132, kh = FULL ? 9 : 7, kx = R - kw, ky = B + (FULL ? 44 : 36);
      const gid = 'pg' + Math.abs(Math.round(X(x0) * 977 + Y(hi) * 31)) + key.replace(/[^a-z0-9]/gi, '');
      const defs = el('defs');
      const lg = el('linearGradient', { id: gid, x1: '0', x2: '1', y1: '0', y2: '0' });
      RAMP.forEach((col, i) => lg.appendChild(el('stop',
        { offset: (i / (RAMP.length - 1) * 100).toFixed(1) + '%', 'stop-color': col })));
      defs.appendChild(lg); svg.appendChild(defs);
      svg.appendChild(el('rect', { x: kx, y: ky, width: kw, height: kh, fill: 'url(#' + gid + ')',
                                   stroke: 'var(--line)', 'stroke-width': .5 }));
      svg.appendChild(txt('0¢', { x: kx - 5, y: ky + kh, 'text-anchor': 'end', class: 'ax' }));
      svg.appendChild(txt('100¢', { x: kx + kw + 5, y: ky + kh, class: 'ax' }));
      svg.appendChild(txt('Yes price: the market’s chance it ends above the strike',
                          { x: kx - 5, y: ky - 4, 'text-anchor': 'end', class: 'ax' }));
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
      /* What a reader needs, and not the rest.

         This box had nine rows under the prices: the midpoint, both bids, the
         buy price again, the fee, the settlement date, the expiry, the series
         level and a link that could not be clicked. Most of it restated the two
         prices in another form. What is left is what changes the meaning of the
         number: when it settles, and where the series stands against the strike.
         The book itself is one line at the foot for anyone who wants it. */
      const html = () => '<b>' + product.name + ' — ' + c.label + '</b>'
        + tip.price(c.ask, c.bid == null ? null : 1 - c.bid,
                    c.ask == null ? null : (WXM.payoutText(Math.round(c.ask * 100)) || '').split(' ')[0]
                      ? 'pays ' + (WXM.payoutText(Math.round(c.ask * 100)) || '').split(' ')[0] : null,
                    c.bid == null ? null : (WXM.payoutText(Math.round((1 - c.bid) * 100)) || '').split(' ')[0]
                      ? 'pays ' + (WXM.payoutText(Math.round((1 - c.bid) * 100)) || '').split(' ')[0] : null)
        + tip.rows(null, [
          ['Settles', c.expiryLabel],
          ['Series now', fmtV(last[1]) + ' ' + unitShort + ' (' + sgn(last[1] - c.threshold) + ' vs ' + fmtThr(c.threshold) + ')'],
        ], (c.bid != null || c.ask != null
              ? 'Yes bid ' + cents(c.bid) + ' · No bid ' + cents(c.ask == null ? null : 1 - c.ask)
                + ' · they buy, they do not sell, and the two sum to a dollar'
              : 'no bids on either side')
           + (WXM.termsUrl(product.id) ? ' · ' + WXM.termsLink(product.id, 'terms') : ''));
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
