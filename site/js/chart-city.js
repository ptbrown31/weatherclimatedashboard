/* The city chart: observed against the forecasts that were standing before
   the day began, on one canvas through the end of the contract day.

   Data in (snapshots): obs/{STATION}.json, forecast/{STATION}.json and
   summary.json for the pickers. Everything is presentation from here on.
   Scales and axes are hand-rolled so the page has no dependencies.

   Series: observed METARs (what settlement reads); the standing NWS hourly
   forecast; NBM and LAMP hourly guidance; the as-issued lines from the
   archive (each source's last cycle before local midnight), which cover the
   elapsed part of the day where only observations exist otherwise; and the
   optional show-yesterday overlay. Level lines are each source's forecast
   high and low FOR the day, from those as-issued cycles. The market layer
   (ladders, strike chips, price panels) is drawn only when WXM.on(). */
window.WXCity = (() => {
  const { el, txt, h, $, clock, dateShort, hourTicks, P, deg } = WXC;
  let locIndex = null;
  const esc = t => String(t == null ? '' : t).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
  const COL = { obs: 'var(--obs)', nws: 'var(--nws)', nbm: 'var(--nbm)', lamp: 'var(--lamp)', mav: 'var(--mav)' };
  const NAME = { nws: 'Weather Service', nbm: 'Blend of Models', lamp: 'LAMP', mav: 'GFS MOS' };
  // mid-luminance hues, readable as text on both the light and the dark panel
  const HP = ['#c0392b', '#e6550d', '#d6604d', '#b5651d', '#e07b6b'];
  const LP = ['#2b7bba', '#3690c0', '#4393c3', '#5f8fd6', '#2a9d8f'];

  /* Which contract day the chart is on.

     'today' is the day being traded now, 'tomorrow' the day-ahead board on its
     own, and 'both' the wide layout that carries the two side by side. The
     day-ahead board is worth a view of its own: it is the one a reader is
     positioning into rather than watching settle, and on the wide layout it
     gets half the width and none of the observations. */
  let cur = null, checked = new Set(), showYday = false, dayMode = 'today', HV = null, tip = null;
  let summary = null, snaps = {}, svgId = 'chart', onSelect = null;
  const DESC = { nws: 'the official National Weather Service forecast', nbm: 'National Blend of Models guidance',
                 lamp: 'LAMP, observation-updated same-day guidance', mav: 'GFS MOS, a single-model statistical forecast' };
  const SRC = { tgroup: 'remarks T-group, tenths of a degree', body: 'body group, whole degrees' };

  // '20260821T225639Z', '20260821T2030Z' or '2026-08-21T23:00:00Z' -> a local clock reading
  function stamp(sid, tz) {
    if (!sid) return null;
    let m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?Z?$/.exec(sid);
    const iso = m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6] || '00'}Z` : sid;
    const ms = Date.parse(iso);
    return isNaN(ms) ? sid : WXC.clockFull(ms, tz) + ' · ' + WXC.dateShort(ms, tz);
  }
  const sw = col => '<span class="sw" style="background:' + col + '"></span>';
  const cents = v => (v == null ? '—' : v + '¢');
  const size = n => (n ? ' ×' + Math.round(n) : '');
  const isoDate = d => { if (!d) return ''; const [y, m, dd] = d.split('-'); return ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m - 1] + ' ' + (+dd) + (y ? ', ' + y : ''); };

  // the exchange's page for one strike, or null when this pass did not list it
  const strikeUrl = (lad, side, L) => WXM.contractUrl(
    ((lad && lad.symbols || {})[side === 'h' ? 'high' : 'low'] || {}).productConid,
    L && (L.conidYes || L.conid));

  // one strike of the ladder: the exchange's top of book and the strike's own day so far
  function ladderTip(L, side, lad, c, pinHint) {
    const cmp = side === 'h' ? '>' : '<', unit = c.unit;
    if (!lad.live) return tip.rows(cmp + L.strike + '°' + unit, [['Yes', L.yes + '¢'], ['No', (100 - L.yes) + '¢']], WXM.PLACEHOLDER);
    const im = side === 'h' ? lad.impliedHigh : lad.impliedLow;
    const url = strikeUrl(lad, side, L);
    /* The two prices, and then very little.

       This box carried twelve rows: the midpoint, both bids, both buy prices,
       which contract the quote came from, the change since the previous sample,
       the first sample of the day, the implied median, the settlement date and
       the quote time. Most of it restated the same two numbers in different
       forms, and the two a reader actually acts on were fifth and sixth.

       What is left is what you would act on: what it costs to take each side,
       what that pays if it comes in, what the contract is asking, and when it
       settles. The book is one line at the foot for anyone who wants it. */
    const buyYes = L.noBid == null ? null : (100 - L.noBid) / 100;
    const buyNo = L.bid == null ? null : (100 - L.bid) / 100;
    const payYes = buyYes == null ? null : WXM.payoutText(Math.round(buyYes * 100));
    const payNo = buyNo == null ? null : WXM.payoutText(Math.round(buyNo * 100));
    const asks = side === 'h' ? 'Settles Yes if the day\u2019s high is above ' + L.strike + '\u00b0' + unit
                              : 'Settles Yes if the day\u2019s low is below ' + L.strike + '\u00b0' + unit;
    const head = '<b>' + (c.city || c.station) + ' \u2014 ' + (L.label || (cmp + L.strike + '\u00b0' + unit)) + '</b>';
    const mult = t => (t ? String(t).split(' ')[0] : null);
    const rows = [
      ['Settles', L.expiration ? isoDate(L.expiration.slice(0, 4) + '-' + L.expiration.slice(4, 6) + '-' + L.expiration.slice(6, 8)) : null],
      ['Market\u2019s median for the day', im && im.value != null ? WXC.deg(im.value)
        : (im && im.edge ? 'beyond the ladder (' + im.edge + ')' : null)],
    ];
    const book = L.bid == null && L.noBid == null ? 'no bids on either side'
      : 'Yes bid ' + (L.bid == null ? '\u2014' : cents(L.bid)) + ' \u00b7 No bid '
        + (L.noBid == null ? '\u2014' : cents(L.noBid)) + ' \u00b7 they buy, they do not sell, and the two sum to a dollar';
    return head + '<div class="tsub">' + asks + '</div>'
      + tip.price(buyYes, buyNo, mult(payYes) ? 'pays ' + mult(payYes) : null,
                  mult(payNo) ? 'pays ' + mult(payNo) : null)
      + tip.rows(null, rows,
      book + ' \u00b7 payouts are net of the fee each side'
      + (lad.asof ? ' \u00b7 quoted ' + WXC.clock(Date.parse(lad.asof), c.tz) : '')
      + (url ? ' \u00b7 click to open the contract' : ''));
  }

  const impliedState = m => ({ unavailable: 'quotes unavailable', unlisted: 'no market listed', day: 'quote summary is for another day',
                                'tomorrow-unlisted': 'tomorrow’s contracts not listed yet', 'no-bids': 'no bids yet' })[m && m.state] || null;
  // a picker dot: tomorrow's numbers against the forecast, then today so far
  function pickTip(c) {
    const m = WXM.on() ? WXM.implied(c) : null, mk = c.markers || {};
    const gap = (v, ref) => (v != null && ref != null ? ' (' + (v - ref > 0 ? '+' : '') + (Math.round((v - ref) * 10) / 10) + '°)' : '');
    const rows = [
      ['NWS high / low, tomorrow', c.nwsHighTomorrow != null || c.nwsLowTomorrow != null ? WXC.deg(c.nwsHighTomorrow) + ' / ' + WXC.deg(c.nwsLowTomorrow) : null],
      ['NBM high / low, tomorrow', c.nbmHighTomorrow != null ? WXC.deg(c.nbmHighTomorrow) + ' / ' + WXC.deg(c.nbmLowTomorrow) : null],
      ['Implied high (' + (WXM.live() ? 'ForecastEx' : 'placeholder') + ')', m ? (m.impliedHigh != null ? WXC.deg(m.impliedHigh) + gap(m.impliedHigh, c.nwsHighTomorrow) : (m.edgeHigh ? 'beyond the ladder (' + m.edgeHigh + ')' : impliedState(m))) : null],
      ['Implied low (' + (WXM.live() ? 'ForecastEx' : 'placeholder') + ')', m ? (m.impliedLow != null ? WXC.deg(m.impliedLow) + gap(m.impliedLow, c.nwsLowTomorrow) : (m.edgeLow ? 'beyond the ladder (' + m.edgeLow + ')' : impliedState(m))) : null],
      ['NWS high issued for today', c.nwsIssuedHigh != null ? WXC.deg(c.nwsIssuedHigh) : null],
      ['Expected high today', c.nwsHighToday != null ? WXC.deg(c.nwsHighToday) + (c.nwsHighTodayRunning ? ' (already recorded)' : '') : null],
      ['Observed so far today', c.obsHighSoFar != null ? WXC.deg(c.obsHighSoFar) + ' / ' + WXC.deg(c.obsLowSoFar) : null],
      ['Latest report', c.obsLatest && c.obsLatest.t ? (c.obsLatest.type || 'METAR') + ' ' + WXC.clock(Date.parse(c.obsLatest.t), c.tz) : null],
    ];
    return tip.rows(c.city + ' (' + c.station + ') — tomorrow ' + isoDate(mk.tomorrow), rows, 'click → this station’s chart');
  }

  // The full view carries both contract days at once: the trace runs to the end
  // of tomorrow and a second ladder sits beside the first, so the day being
  // traded now and the day-ahead board can be read against the same axis and
  // the same forecast. It is the wider layout, not a different chart.
  function layout(market, full) {
    if (market && full) {
      return { W: 960, H: 655, L: 52, R: 548, T: 36, B: 346, PH0: 404, PH1: 494, PL0: 526, PL1: 616,
               GV: 552, GL0: 578, GL1: 594, GN: 598, LX: 726, LW: 96, LX2: 848, LW2: 96, full: true };
    }
    return market
      ? { W: 960, H: 655, L: 52, R: 610, T: 36, B: 346, PH0: 404, PH1: 494, PL0: 526, PL1: 616,
          GV: 614, GL0: 646, GL1: 668, GN: 672, LX: 790, LW: 130 }
      : { W: 960, H: 390, L: 52, R: 760, T: 36, B: 346, GV: 764, GL0: 796, GL1: 818, GN: 822 };
  }

  // ---- pickers: two small maps replace a dropdown. With the market layer
  //      on, dots carry the landing map's divergence encoding; off, they
  //      show observed-so-far against the day's NWS forecast high.
  function pickDot(svg, c, X, Y, scale) {
    const on = c.station === cur;
    const v = dotValue(c);
    const r = (v == null ? 7 : 8 + 9 * Math.min(Math.abs(v), 5) / 5) * scale;
    const g = el('g', { style: 'cursor:pointer' });
    g.appendChild(el('circle', { cx: X, cy: Y, r: r + 3, fill: 'var(--panel)', 'fill-opacity': .95 }));
    g.appendChild(el('circle', { cx: X, cy: Y, r, fill: v == null ? 'var(--line)' : (v > 0 ? 'var(--warm)' : (v < 0 ? 'var(--cool)' : 'var(--muted)')),
      'fill-opacity': .95, stroke: on ? 'var(--navy)' : 'var(--ink)', 'stroke-width': on ? 4 : .8 }));
    g.onmousemove = e => { if (tip) tip.show(e, pickTip(c)); };
    g.onmouseleave = () => { if (tip) tip.hide(); };
    if (on) g.appendChild(txt(c.city, { x: X, y: Y - r - 9, 'text-anchor': 'middle', 'font-size': 26, 'font-weight': 700,
      fill: 'var(--navy)', stroke: 'var(--panel)', 'stroke-width': 6, 'paint-order': 'stroke', 'stroke-linejoin': 'round' }));
    g.onclick = () => select(c.station);
    svg.appendChild(g);
  }
  function dotValue(c) {
    if (WXM.on()) { const m = WXM.implied(c); return m ? m.divHigh : null; }
    // only the issued forecast, which is what the title claims. The standing
    // figure folds the observation into itself, so the difference would be zero
    // by construction rather than a comparison.
    const ref = c.nwsIssuedHigh;
    return (c.obsHighSoFar != null && ref != null) ? Math.round((c.obsHighSoFar - ref) * 10) / 10 : null;
  }
  function drawPick(base) {
    const svg = $('#pick'); if (!svg) return;
    const pt = $('#pickTitle');
    if (pt) pt.textContent = WXM.on()
      ? (WXM.live() ? 'United States — dot colour and size: the market-implied high (ForecastEx) against tomorrow’s NWS forecast'
                    : 'United States — dot colour and size: the placeholder implied high against tomorrow’s NWS forecast (not a market value)')
      : 'United States — dot colour and size: observed so far against the NWS high issued for the day';
    svg.innerHTML = '';
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': 1 }));
    summary.cities.filter(c => c.onConus).forEach(c => pickDot(svg, c, c.px, c.py, 1));
    const w = $('#pickW'); if (!w) return;
    w.innerHTML = '';
    w.appendChild(el('rect', { x: 0, y: 0, width: 960, height: 480, fill: 'var(--map-sea)' }));
    w.appendChild(el('path', { d: base.worldPaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': .8 }));
    summary.cities.filter(c => !c.onConus).forEach(c => pickDot(w, c, c.wx, c.wy, 1.2));
  }

  /* The page's own heading: which station, and which contract day it is showing.

     A reader arriving on a link needs both before anything else, and the day
     matters as much as the place: the same chart means a different thing on the
     day-ahead board than on today's. */
  function drawTitle(c) {
    const node = $('#cityTitle'); if (!node || !c) return;
    const mk = c.markers || {};
    const one = d => (d ? isoDate(d).replace(/, \d{4}$/, '') : '');
    const shown = dayMode === 'both' && mk.day && mk.tomorrow
      ? one(mk.day) + ' \u2013 ' + one(mk.tomorrow)
      : one(dayMode === 'tomorrow' ? mk.tomorrow : mk.day);
    const t = (c.city || c.station) + ' (' + c.station + ')' + (shown ? ', ' + shown : '');
    node.textContent = t;
    document.title = t;
  }

  /* A small map saying where this station is.

     The picker below shows every station and answers "which one shall I look
     at". This answers "where is the one I am looking at", which is a different
     question and worth a second of a reader's time: a page headed KMDW does not
     tell most people it is on the south-west side of Chicago.

     It reuses the projected geometry the picker already loads, cropped to a box
     around the station so the dot is not a pinprick on a continent. The crop is
     in the same projected units, so no reprojection is involved. */
  function drawLocator(c) {
    const host = $('#locator'); if (!host || !c) return;
    host.innerHTML = '';
    // the metro-scale picture where there is one, and the outline where there is
    // not: the National Map covers the United States, and a map that pretended
    // otherwise abroad would be worse than an outline that admits what it is
    const meta = (locIndex && (locIndex.stations || {})[c.station]) || null;
    if (meta) {
      /* The live regional frame where the pipeline has one: the wider USGS
         image with the current reading at every METAR field around the
         resolving station, drawn from the obs snapshot. The base image is
         static and public domain; only the numbers on it are live, and they
         arrive through this site's own snapshots, never a live tile call. */
      const ob = (snaps.ob && snaps.ob.data) || null;
      const near = (ob && ob.nearby) || [];
      const region = meta.region && near.length ? meta.region : null;
      const m = region || meta;
      const box = h('div', { class: 'locbox' });
      const img = h('img', { src: WXD.base() + '/snapshots/locator/' + c.station + (region ? '_region' : '') + '.png',
                             alt: 'Aerial and topographic map centred on ' + (c.city || c.station),
                             loading: 'lazy', width: String(m.w || 760), height: String(m.h || 475) });
      box.appendChild(img);
      if (region) {
        const W2 = m.w, H2 = m.h;
        const svg = el('svg', { viewBox: '0 0 ' + W2 + ' ' + H2,
                                style: 'position:absolute;inset:0;width:100%;height:100%' });
        const coslat = Math.cos(c.lat * Math.PI / 180);
        const px = lon => W2 / 2 + ((lon - c.lon) * 111.320 * coslat) / m.halfWKm * (W2 / 2);
        const py = lat => H2 / 2 - ((lat - c.lat) * 110.574) / m.halfHKm * (H2 / 2);
        const rows0 = (ob && ob.rows) || [];
        const centreTemp = rows0.length ? rows0[rows0.length - 1].tempF : null;
        const wind = n => (n.wspd == null ? null
          : (n.wdir != null ? n.wdir + '\u00b0 at ' : '') + n.wspd + ' kt'
            + (n.wgst ? ', gusting ' + n.wgst : ''));
        const COVER_FRAC = { CLR: 0, SKC: 0, CAVOK: 0, FEW: 0.25, SCT: 0.5, BKN: 0.75, OVC: 1, OVX: 1, VV: 1 };
        const COVER_NAME = { CLR: 'clear', SKC: 'clear', CAVOK: 'clear', FEW: 'few clouds', SCT: 'scattered',
                             BKN: 'broken', OVC: 'overcast', OVX: 'sky obscured', VV: 'sky obscured' };
        /* The station-model glyph, the aviation convention throughout.

           The circle is the sky: filled by the fraction of it the reported
           cover code names, a wedge from twelve o'clock. The staff points
           toward the direction the wind comes FROM (a north wind's staff
           points up the map), a pennant is fifty knots, a full barb ten, a
           half barb five, barbs on the clockwise side; a ringed circle with
           no staff is calm. Temperature sits upper-left of the circle and
           dewpoint lower-left, which is where seventy years of surface
           charts put them. */
        const model = (g, x2, y2, n, big, col) => {
          const r = big ? 6.5 : 4.5;
          const frac = n.cover != null ? COVER_FRAC[n.cover] : null;
          g.appendChild(el('circle', { cx: x2, cy: y2, r, fill: 'var(--panel)', stroke: col,
                                       'stroke-width': big ? 2 : 1.4 }));
          if (frac != null && frac > 0) {
            if (frac >= 1) {
              g.appendChild(el('circle', { cx: x2, cy: y2, r: r - (big ? 1.6 : 1.1), fill: col }));
            } else {
              const a1 = -Math.PI / 2, a2 = a1 + frac * 2 * Math.PI, ri = r - (big ? 1.6 : 1.1);
              g.appendChild(el('path', { d: 'M' + x2 + ' ' + y2
                + 'L' + (x2 + ri * Math.cos(a1)).toFixed(1) + ' ' + (y2 + ri * Math.sin(a1)).toFixed(1)
                + 'A' + ri + ' ' + ri + ' 0 ' + (frac > 0.5 ? 1 : 0) + ' 1 '
                + (x2 + ri * Math.cos(a2)).toFixed(1) + ' ' + (y2 + ri * Math.sin(a2)).toFixed(1) + 'Z', fill: col }));
            }
          }
          if (n.wspd != null && n.wspd < 3) {
            g.appendChild(el('circle', { cx: x2, cy: y2, r: r + 2.5, fill: 'none', stroke: col, 'stroke-width': 1 }));
          } else if (n.wspd != null && n.wdir != null) {
            /* The traditional barb, textbook proportions. The staff runs from
               the sky circle toward the direction the wind comes from; the
               feathers sit on the clockwise side at sixty degrees off the
               staff, slanting outward, the first exactly at the tip; a
               pennant is a solid flag whose base lies along the staff. A
               lone half barb sits one space in from the tip so it cannot be
               read as a full one. Every stroke is drawn twice, a pale halo
               under the ink, which is what keeps a barb legible over roads
               and contours. */
            const rad = n.wdir * Math.PI / 180;
            const ux = Math.sin(rad), uy = -Math.cos(rad);          // toward where the wind is FROM
            const pxv = -uy, pyv = ux;                              // the clockwise side (NH convention)
            const L = big ? 30 : 24;
            const bl = big ? 11 : 9, gap = big ? 5.5 : 4.5;
            const bx2 = 0.866 * pxv + 0.5 * ux, by3 = 0.866 * pyv + 0.5 * uy;   // sixty degrees off the staff
            const at = d => [x2 + ux * (r + L - d), y2 + uy * (r + L - d)];
            const seg = (x3, y3, x4, y4, wgt) => {
              g.appendChild(el('line', { x1: x3, y1: y3, x2: x4, y2: y4, stroke: 'var(--panel)',
                                         'stroke-width': wgt + 1.8, 'stroke-linecap': 'round', opacity: 0.9 }));
              g.appendChild(el('line', { x1: x3, y1: y3, x2: x4, y2: y4, stroke: col, 'stroke-width': wgt }));
            };
            const [sx, sy] = [x2 + ux * r, y2 + uy * r];
            const [ex, ey] = at(0);
            seg(sx, sy, ex, ey, 1.6);
            let left = Math.round(n.wspd / 5) * 5, d0 = 0;
            while (left >= 50) {
              const [t1x, t1y] = at(d0), [t2x, t2y] = at(d0 + gap * 1.3);
              const d = 'M' + t1x.toFixed(1) + ' ' + t1y.toFixed(1)
                + 'L' + (t1x + bx2 * bl).toFixed(1) + ' ' + (t1y + by3 * bl).toFixed(1)
                + 'L' + t2x.toFixed(1) + ' ' + t2y.toFixed(1) + 'Z';
              g.appendChild(el('path', { d, fill: 'var(--panel)', stroke: 'var(--panel)', 'stroke-width': 1.8, opacity: 0.9 }));
              g.appendChild(el('path', { d, fill: col }));
              left -= 50; d0 += gap * 1.8;
            }
            while (left >= 10) {
              const [t1x, t1y] = at(d0);
              seg(t1x, t1y, t1x + bx2 * bl, t1y + by3 * bl, 1.6);
              left -= 10; d0 += gap;
            }
            if (left >= 5) {
              if (d0 === 0) d0 = gap;                               // a lone half barb sits in from the tip
              const [t1x, t1y] = at(d0);
              seg(t1x, t1y, t1x + bx2 * bl * 0.55, t1y + by3 * bl * 0.55, 1.6);
            }
            if (n.wgst) {
              g.appendChild(txt('G' + n.wgst, { x: ex + ux * 7, y: ey + uy * 7 + 3,
                                                'text-anchor': 'middle', 'font-size': big ? 9.5 : 8.5,
                                                'font-weight': 700, fill: col, 'pointer-events': 'none' }));
            }
          }
        };
        const chip = (x2, y2, t2, big, col, anchor) => {
          const fs = big ? 14 : 10.5;
          const tw = String(t2).length * fs * 0.62 + 6;
          const cx2 = anchor === 'end' ? x2 - tw / 2 : x2 + tw / 2;
          svg.appendChild(el('rect', { x: cx2 - tw / 2, y: y2 - fs + 2, width: tw, height: fs + 4, rx: 4,
                                       fill: 'var(--panel)', opacity: 0.85, 'pointer-events': 'none' }));
          svg.appendChild(txt(t2, { x: cx2, y: y2, 'text-anchor': 'middle', 'font-size': fs,
                                    'font-weight': 700, fill: col, 'pointer-events': 'none' }));
        };
        const labels = (x2, y2, n, big, col) => {
          const off = big ? 10 : 7.5;
          if (n.tempF != null) chip(x2 - off, y2 - (big ? 8 : 6), Math.round(n.tempF) + '\u00b0', big, col, 'end');
          if (n.dewF != null) chip(x2 - off, y2 + (big ? 16 : 13), Math.round(n.dewF) + '\u00b0', false, 'var(--muted)', 'end');
        };
        const placed = [];
        near.forEach(n => {
          const x2 = px(n.lon), y2 = py(n.lat);
          if (x2 < 8 || x2 > W2 - 8 || y2 < 10 || y2 > H2 - 6) return;
          model(svg, x2, y2, n, false, 'var(--ink)');
          // temperature and dewpoint only where they do not sit on another
          // station's; the glyph itself always draws
          if (!placed.some(q => Math.abs(q[0] - x2) < 52 && Math.abs(q[1] - y2) < 26)) {
            labels(x2, y2, n, false, 'var(--ink)');
            placed.push([x2, y2]);
          }
          const hit = el('circle', { cx: x2, cy: y2, r: 14, fill: 'transparent' });
          hit.addEventListener('mousemove', ev => {
            const rows = [['Temperature', n.tempF != null ? WXC.deg(n.tempF) : '\u2014'],
                          ['Dewpoint', n.dewF != null ? WXC.deg(n.dewF) : '\u2014'],
                          ['Wind', wind(n) || 'calm'],
                          ['Sky', n.cover ? (COVER_NAME[n.cover] || n.cover) : '\u2014'],
                          ['Observed', n.t ? WXC.clockFull(Date.parse(n.t), c.tz) : '\u2014']];
            tip.show(ev, tip.rows(n.name + ' (' + n.id + ')', rows,
                                  'a nearby report for context; the contract settles on ' + c.station + ' alone'));
          });
          hit.addEventListener('mouseleave', () => tip.hide());
          svg.appendChild(hit);
        });
        // the resolving station over everything: the same model, ringed, its
        // temperature the settlement-convention reading from its own record,
        // and named on the map as the one that settles
        const selfN = Object.assign({}, ob.nearbySelf || {}, centreTemp != null ? { tempF: centreTemp } : {});
        svg.appendChild(el('circle', { cx: W2 / 2, cy: H2 / 2, r: 11, fill: 'none',
                                       stroke: 'var(--accent)', 'stroke-width': 2.4 }));
        model(svg, W2 / 2, H2 / 2, selfN, true, 'var(--accent)');
        labels(W2 / 2, H2 / 2, selfN, true, 'var(--accent)');
        {
          const lbl = c.station + ' \u00b7 settlement station';
          const fs = 10.5, tw = lbl.length * fs * 0.6 + 12, ly = H2 / 2 + 24;
          svg.appendChild(el('rect', { x: W2 / 2 - tw / 2, y: ly - fs, width: tw, height: fs + 6, rx: 5,
                                       fill: 'var(--panel)', opacity: 0.92, stroke: 'var(--accent)',
                                       'stroke-width': 1, 'pointer-events': 'none' }));
          svg.appendChild(txt(lbl, { x: W2 / 2, y: ly + 2, 'text-anchor': 'middle', 'font-size': fs,
                                     'font-weight': 700, fill: 'var(--accent)', 'pointer-events': 'none' }));
          const selfHit = el('circle', { cx: W2 / 2, cy: H2 / 2, r: 18, fill: 'transparent' });
          selfHit.addEventListener('mousemove', ev => {
            const rows = [['Temperature', selfN.tempF != null ? WXC.deg(selfN.tempF) : '\u2014'],
                          ['Dewpoint', selfN.dewF != null ? WXC.deg(selfN.dewF) : '\u2014'],
                          ['Wind', wind(selfN) || 'calm'],
                          ['Sky', selfN.cover ? (COVER_NAME[selfN.cover] || selfN.cover) : '\u2014'],
                          ['Observed', ob.latest && ob.latest.t ? WXC.clockFull(Date.parse(ob.latest.t), c.tz) : '\u2014']];
            tip.show(ev, tip.rows((c.city || c.station) + ' (' + c.station + ') \u00b7 settlement station', rows,
                                  'the contract settles on this thermometer alone, decoded the way settlement reads it'));
          });
          selfHit.addEventListener('mouseleave', () => tip.hide());
          svg.appendChild(selfHit);
        }
        box.appendChild(svg);
      } else {
        // the image is centred on the station, so the marker is the middle of it
        box.appendChild(h('span', { class: 'locpin' }));
      }
      box.classList.add('expandable');
      host.appendChild(box);
      // twenty-four kilometres in a third of a column is a thumbnail; the same
      // picture full-window is a map
      const bar = h('div', { class: 'bar', style: 'margin:6px 0 0' });
      bar.appendChild(WXC.expander(box, 'Expand map'));
      host.appendChild(bar);
      const across = Math.round((m.halfWKm || 12) * 2);
      const bits = [c.city, c.station];
      if (c.lat != null && c.lon != null) {
        bits.push(Math.abs(c.lat).toFixed(3) + '\u00b0' + (c.lat >= 0 ? 'N' : 'S') + ' '
                  + Math.abs(c.lon).toFixed(3) + '\u00b0' + (c.lon >= 0 ? 'E' : 'W'));
      }
      const asof = region && snaps.ob && snaps.ob.asof
        ? ' Readings as of ' + WXC.clockFull(snaps.ob.asof, c.tz) + ', from aviationweather.gov METARs through this site\u2019s own snapshots.' : '';
      const capEl = h('div', { class: 'cap', style: 'margin:5px 0 0' });
      capEl.innerHTML = esc(bits.join(' \u00b7 ')) + ' \u00b7 about ' + across + ' km across.<br>'
        + (region ? 'The ringed station is the one the contract settles on; the others are the reporting fields '
                    + 'around it, each drawn as a station model: temperature upper-left, dewpoint lower-left, '
                    + 'the circle filled by cloud cover, and a wind barb pointing where the wind comes from, a '
                    + 'half barb five knots, a full barb ten and a pennant fifty. A city\u2019s temperature varies '
                    + 'with land cover, distance from the centre, shade and water, so where each thermometer sits '
                    + 'matters.'
                  : 'The contract settles on this one station, and a city\u2019s temperature varies with land cover, '
                    + 'distance from the centre, shade and water, so where it sits matters.')
        + esc(asof) + ' Imagery: '
        + '<a href="https://basemap.nationalmap.gov/" target="_blank" rel="noopener noreferrer">USGS The '
        + 'National Map</a>, a work of the United States government.';
      host.appendChild(capEl);
      return;
    }
    if (!summary.base) return;
    const px = c.wx, py = c.wy;
    if (px == null || py == null) return;
    const paths = summary.base.worldPaths;
    if (!paths) return;
    const w = 300, hh = 150;
    const x0 = Math.max(0, Math.min(960 - w, px - w / 2));
    const y0 = Math.max(60, Math.min(380 - hh, py - hh / 2));
    const svg = el('svg', { viewBox: `${x0} ${y0} ${w} ${hh}`, class: 'loc' });
    svg.appendChild(el('rect', { x: x0, y: y0, width: w, height: hh, fill: 'var(--map-sea)' }));
    svg.appendChild(el('path', { d: paths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': .8 }));
    svg.appendChild(el('circle', { cx: px, cy: py, r: 5.5, fill: 'none', stroke: 'var(--accent)',
                                   'stroke-width': 1.6, opacity: .55 }));
    svg.appendChild(el('circle', { cx: px, cy: py, r: 2.6, fill: 'var(--accent)' }));
    host.appendChild(svg);
    const bits = [c.city, c.station];
    if (c.lat != null && c.lon != null) {
      bits.push(Math.abs(c.lat).toFixed(2) + '\u00b0' + (c.lat >= 0 ? 'N' : 'S') + ' '
                + Math.abs(c.lon).toFixed(2) + '\u00b0' + (c.lon >= 0 ? 'E' : 'W'));
    }
    host.appendChild(h('div', { class: 'cap', style: 'margin:4px 0 0',
                                text: bits.join(' \u00b7 ') + ' \u00b7 detailed imagery is a United States '
                                      + 'government product and covers the United States only.' }));
  }

  const city = () => summary.cities.find(x => x.station === cur);

  async function select(sid, push) {
    cur = sid; checked = new Set();
    if (push !== false) { const u = new URL(location.href); u.searchParams.set('station', sid); history.replaceState(null, '', u); }
    if (onSelect) onSelect(sid);
    // the station's own record, below the chart; it loads on its own so a slow
    // scorecard never holds up the chart the page is for
    if (window.WXCityScore) WXCityScore.draw(sid).catch(() => {});
    if (window.WXCityScore) WXCityScore.drawTable(sid).catch(() => {});
    if (window.WXCityDays) WXCityDays.draw(sid).catch(() => {});
    if (window.WXDiscussion) WXDiscussion.draw(sid).catch(() => {});
    const c0 = city();
    drawTitle(c0);
    drawLocator(c0);
    const keys = [`forecast/${sid}.json`, `obs/${sid}.json`]
      .concat(c0 && c0.unit === 'F' ? [`normals/${sid}.json`, `subhourly/${sid}.json`] : []);
    const r = await WXD.getAll(keys);
    snaps = { fc: r[`forecast/${sid}.json`], ob: r[`obs/${sid}.json`], nm: r[`normals/${sid}.json`] || null,
              sub: r[`subhourly/${sid}.json`] || null };
    drawLocator(c0);                             // again, now the obs snapshot can dress the map
    const mres = await WXM.load(sid);            // the station's quote snapshot (live market layer only)
    const st = $('#chartStatus');
    if (st) {
      // the weather strip and the quote strip are separate: a missing quote file must not
      // read as a weather outage over a fully drawn chart
      st.innerHTML = ''; st.appendChild(WXC.statusEl([snaps.ob, snaps.fc], 10));
      if (mres) { const q = WXC.statusEl([mres], 10); q.insertBefore(document.createTextNode('Quotes: '), q.firstChild); st.appendChild(q); }
    }
    if (WXM.on()) {
      // the strikes shown by default: the one nearest 50¢ on each side
      const c = city(), lv = levelsFor(c), lad = WXM.ladder(c, { high: lv.high, low: lv.low });
      if (lad) ['high', 'low'].forEach(m => {
        const q = lad[m].filter(L => L.yes != null); if (!q.length) return;
        const atm = q.reduce((a, b) => Math.abs(b.yes - 50) < Math.abs(a.yes - 50) ? b : a); checked.add(m[0] + ':' + atm.strike);
      });
    }
    if (summary.base) drawPick(summary.base);
    drawStrikeRow(); draw();
  }

  // the day's reference high and low for the ladder: NWS as issued, else standing
  function levelsFor(c) {
    const fc = (snaps.fc && snaps.fc.data) || {};
    const ai = (fc.asIssued || {}).nws || {};
    return { high: ai.highToday != null ? ai.highToday : (fc.nws || {}).highToday, low: ai.lowToday != null ? ai.lowToday : (fc.nws || {}).lowToday };
  }

  function skColor(side, K, lad) {
    const i = lad[side === 'h' ? 'high' : 'low'].findIndex(L => L.strike === K);
    return (side === 'h' ? HP : LP)[((i % 5) + 5) % 5];
  }
  function drawStrikeRow() {
    const row = $('#skRow'); if (!row) return;
    row.innerHTML = '';
    if (!WXM.on()) return;
    const c = city(), lv = levelsFor(c), lad = WXM.ladder(c, { high: lv.high, low: lv.low });
    if (!lad || (lad.live && !lad.listed)) {
      row.appendChild(h('div', { class: 'cap', text: lad ? 'No daily temperature contracts listed for this station and day on the exchange.' : 'Market data unavailable.' }));
      return;
    }
    [['high', 'High strikes', 'h', '>'], ['low', 'Low strikes', 'l', '<']].forEach(([m, lab, pfx, cmp]) => {
      if (!lad[m].length) return;
      const div = h('div', {}, [h('span', { class: 'lbl2', text: lab })]);
      lad[m].forEach(L => {
        const key = pfx + ':' + L.strike;
        const one = L.side === 'bid' || L.side === 'ask';
        const b = h('button', { class: 'sk' + (checked.has(key) ? ' on' : ''), text: cmp + L.strike + '°' });
        if (L.yes != null) {
          // the price inside the chip is the contract link; the chip around it
          // still selects the strike for the chart, which is a different job
          const url = strikeUrl(lad, pfx, L);
          const pc = h('span', { class: 'skp' + (url ? ' lnk' : ''), text: ' ' + L.yes + '¢' + (one ? '*' : '') });
          if (url) WXM.linkTo(pc, url, 'Open ' + (L.label || L.strike) + ' on IBKR');
          b.appendChild(pc);
        }
        b.onmousemove = e => tip.show(e, ladderTip(L, pfx, lad, c, false));
        b.onmouseleave = () => tip.hide();
        b.style.setProperty('--c', skColor(pfx, L.strike, lad));
        b.onclick = () => { checked.has(key) ? checked.delete(key) : checked.add(key); b.classList.toggle('on'); draw(); };
        div.appendChild(b);
      });
      row.appendChild(div);
    });
    row.appendChild(h('div', { class: 'cap', text: 'Strike ladder: ' + lad.label + (lad.live ? '; the number is the Yes price in cents, midway between the Yes bid and one dollar less the No bid, or (*) the one side with bids; hover for the bids. Click a price to open that contract on ForecastEx; click the strike to draw it on the chart.' : '.') }));

  }

  function draw() {
    const c = city();
    const fc = (snaps.fc && snaps.fc.data) || null;
    const ob = (snaps.ob && snaps.ob.data) || null;
    const market = WXM.on();
    const S = layout(market, dayMode === 'both');
    const svg = $('#' + svgId);
    svg.setAttribute('viewBox', `0 0 ${S.W} ${S.H}`);
    svg.innerHTML = '';
    const g = el('g'); svg.appendChild(g);
    const tz = c.tz, unit = c.unit;
    const M0 = (fc && fc.markers) || c.markers;
    if (!M0) { g.appendChild(txt('No data available for this station.', { x: S.L + 8, y: S.T + 16, class: 'axl' })); return; }
    /* On the day-ahead view every boundary moves forward one local day.

       The lead-in keeps the same shape it has on today's view, half a day
       before the contract day opens, so the two views are read the same way.
       Sunrise and sunset are a day old and are dropped rather than drawn in
       the wrong place. */
    const DAY = 24 * 3600 * 1000;
    const M = dayMode !== 'tomorrow' ? M0 : (() => {
      const ds = P(M0.dayEnd), de = ds + DAY;
      const iso = t => new Date(t).toISOString().replace('.000', '');
      return Object.assign({}, M0, {
        day: M0.tomorrow, yesterday: M0.day, tomorrow: null,
        dayStart: iso(ds), dayEnd: iso(de), ydayStart: M0.dayStart,
        winStart: iso(ds - (P(M0.dayStart) - P(M0.winStart))),
        sunrise: null, sunset: null,
      });
    })();
    const w0 = P(M.winStart), d0 = P(M.dayStart);
    // the full view runs to the end of the day-ahead contract day, which is one
    // more local day past the end of this one
    const d1 = P(M.dayEnd) + (S.full ? DAY : 0);
    const dEnd1 = P(M.dayEnd);
    const val = r => (unit === 'F' ? r.tempF : (r.tempC != null ? r.tempC : (r.tempF - 32) * 5 / 9));
    const rows = a => (a || []).map(r => ({ t: P(r.t), v: val(r) }));
    const inWin = a => a.filter(p => p.t >= w0 && p.t <= d1);

    const O = inWin(rows(ob && ob.rows));
    const F = inWin(rows(fc && fc.nws && fc.nws.hourly));
    const N = inWin(rows(fc && fc.nbm && fc.nbm.hourly));
    const LA = inWin(rows(fc && fc.lamp && fc.lamp.hourly));
    const AI = (fc && fc.asIssued) || {};
    const A = inWin(rows(AI.nws && AI.nws.rows)), NA = inWin(rows(AI.nbm && AI.nbm.rows)), LAI = inWin(rows(AI.lamp && AI.lamp.rows));
    const YD = (fc && fc.yesterday) || {};

    // ---- show-yesterday overlay, shifted exactly 24 h so the days line up by clock time
    const ySeries = [];
    if (showYday) {
      const yObs = rows(ob && ob.rows).filter(p => p.t >= w0 - DAY && p.t < d0).map(p => ({ t: p.t + DAY, v: p.v })).filter(p => p.t >= w0 && p.t <= d1);
      // asking for yesterday makes yesterday the subject: it comes in at full
      // strength in the sources' own colours and today drops back to a
      // reference behind it, which is the comparison the button is for
      if (yObs.length) ySeries.push({ nm: 'Yesterday observed', pts: yObs, col: COL.obs, w: 2.4, dash: null, op: 1 });
      [['nws', 'Yesterday NWS as issued', COL.nws], ['nbm', 'Yesterday NBM as issued', COL.nbm],
       ['lamp', 'Yesterday LAMP as issued', COL.lamp]].forEach(([k, nm, col]) => {
        const y = YD[k]; if (!y) return;
        const pts = rows(y.rows).map(p => ({ t: p.t + DAY, v: p.v })).filter(p => p.t >= w0 && p.t <= d1);
        if (pts.length) ySeries.push({ nm, pts, col, w: 2, dash: '6 3', op: 1 });
      });
    }

    // ---- levels for the day: each source's forecast high and low as issued
    //      before the day began; the standing official NWS value where no
    //      pre-day cycle exists yet (the archive is young for that source)
    const levels = [];
    const addLevel = (k, hi, lo, issued, meta) => {
      const tag = issued ? ' (issued)' : '';
      if (hi != null) levels.push({ v: hi, nm: NAME[k] + tag, col: COL[k], k, kind: 'high', issued, cycle: meta.cycleHigh, fromHourly: meta.fromHourly });
      if (lo != null) levels.push({ v: lo, nm: NAME[k] + tag, col: COL[k], k, kind: 'low', issued, cycle: meta.cycleLow, fromHourly: meta.fromHourly });
    };
    ['nws', 'nbm', 'lamp', 'mav'].forEach(k => {
      const ai = AI[k];
      if (ai && (ai.highToday != null || ai.lowToday != null)) addLevel(k, ai.highToday, ai.lowToday, ai.preDay || ai.levelPreDay, { cycleHigh: ai.levelCycleHigh || ai.cycle, cycleLow: ai.levelCycleLow || ai.cycle, fromHourly: ai.fromHourly });
      else if (fc && fc[k] && (fc[k].highToday != null || fc[k].lowToday != null)) addLevel(k, fc[k].highToday, fc[k].lowToday, false, { cycleHigh: fc[k].cycle, cycleLow: fc[k].cycle, fromHourly: fc[k].highTodayFrom === 'hourly' || fc[k].partialDay });
    });
    const levelTip = L => {
      // every source that sits at exactly this level shares the line, so the tooltip lists them all
      const same = levels.filter(x => x.kind === L.kind && x.v === L.v);
      const rows = [['Value', WXC.deg(L.v)]];
      same.forEach(x => {
        const pre = same.length > 1 ? NAME[x.k] + ' · ' : '';
        rows.push([pre + 'Cycle', stamp(x.cycle, tz)]);
        rows.push([pre + 'Issued', x.issued ? 'before the day began (as issued)' : 'standing forecast; no pre-day cycle in the archive yet']);
        rows.push([pre + 'Derived from', x.fromHourly ? 'the hourly series (no day value in that cycle)' : (x.k === 'nws' ? 'the day/night product (official high or low)' : (x.k === 'mav' ? 'the N/X line' : (x.k === 'nbm' ? 'the NBS day max/min line' : 'the hourly series')))]);
      });
      return tip.rows(same.map(x => NAME[x.k]).join(', ') + ' — forecast ' + L.kind + ' for ' + isoDate(M.day), rows,
        same.map(x => DESC[x.k]).join('; ') + ' · click to pin');
    };
    const bind = (node, html, pin) => { node.addEventListener('mousemove', e => { e.stopPropagation(); tip.show(e, html()); }); node.addEventListener('mouseleave', () => tip.hide()); if (pin) { node.addEventListener('click', e => { e.stopPropagation(); tip.pin(e, html()); }); node.setAttribute('data-tip-pin', '1'); } node.style.cursor = 'default'; return node; };

    // NCEI daily normals for the day, drawn as quiet reference ticks in the gutter
    const nm = snaps.nm && snaps.nm.data && snaps.nm.data.days ? snaps.nm.data.days[M.day.slice(5)] : null;
    const normals = nm && nm.tmax != null ? [{ v: nm.tmax, nm: 'normal high' }, { v: nm.tmin, nm: 'normal low' }] : [];

    const lad = market ? WXM.ladder(c, levelsFor(c), dayMode === 'tomorrow' ? 'tomorrow' : undefined) : null;
    const picked = market ? [...checked].map(k => { const [pfx, K] = k.split(':'); return { side: pfx, K: +K, col: skColor(pfx, +K, lad) }; }).sort((a, b) => b.K - a.K) : [];

    // ---- scales
    const x = t => S.L + (t - w0) / (d1 - w0) * (S.R - S.L);
    const step = unit === 'F' ? 5 : 2;
    const temps = [...O, ...F, ...N, ...LA, ...A, ...NA, ...LAI].map(p => p.v)
      .concat(ySeries.flatMap(s => s.pts.map(p => p.v))).concat(levels.map(l => l.v)).concat(normals.map(n => n.v))
      .concat(lad ? lad.high.map(l => l.strike).concat(lad.low.map(l => l.strike)) : []);
    if (!temps.length) temps.push(unit === 'F' ? 70 : 20);
    const lo = Math.floor(Math.min(...temps) / step) * step - step / 2, hi = Math.ceil(Math.max(...temps) / step) * step + step / 2;
    const y = v => S.B - (v - lo) / (hi - lo) * (S.B - S.T);
    const lx = p => S.LX + (p / 100) * S.LW;
    const rightEdge = market ? S.LX + S.LW : S.R;

    // ---- night shading, outside the contract day's daylight
    const sr = M.sunrise ? P(M.sunrise) : null, ss = M.sunset ? P(M.sunset) : null;
    if (sr) g.appendChild(el('rect', { x: x(w0), y: S.T, width: Math.max(0, x(sr) - x(w0)), height: S.B - S.T, fill: 'var(--night)' }));
    if (ss) {
      const night1End = S.full && sr ? Math.min(sr + 864e5, d1) : d1;
      g.appendChild(el('rect', { x: x(ss), y: S.T, width: Math.max(0, x(night1End) - x(ss)), height: S.B - S.T, fill: 'var(--night)' }));
      if (S.full && sr) {
        const ss2 = ss + 864e5;
        if (ss2 < d1) g.appendChild(el('rect', { x: x(ss2), y: S.T, width: Math.max(0, x(d1) - x(ss2)), height: S.B - S.T, fill: 'var(--night)' }));
      }
    }

    // ---- gridlines and axes
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      g.appendChild(el('line', { x1: S.L, x2: S.R, y1: y(v), y2: y(v), class: 'grid' }));
      if (market) g.appendChild(el('line', { x1: S.LX, x2: S.LX + S.LW, y1: y(v), y2: y(v), class: 'grid', opacity: .6 }));
      g.appendChild(txt(v + '°', { x: S.L - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'ax' }));
    }
    hourTicks(w0, d1, tz).forEach(tk => {
      g.appendChild(el('line', { x1: x(tk.t), x2: x(tk.t), y1: S.T, y2: S.B, class: 'grid' }));
      g.appendChild(txt(tk.label, { x: x(tk.t), y: S.B + 15, 'text-anchor': 'middle', class: 'ax' }));
    });

    // ---- day markers
    const marks = [['midnight', d0, 'var(--muted)', null], ['sunrise', sr, '#e0a020', '3 3'], ['sunset', ss, '#e0a020', '3 3'], ['day end', d1, 'var(--muted)', null]];
    /* The second day gets its sun too.

       In the full view the chart runs to the end of the day-ahead contract day,
       and only the first day's sunrise and sunset were marked — so half the
       chart had no reference for when its daylight began or ended, which is the
       thing a temperature trace is shaped by. The snapshot carries one day's
       times, so the day-ahead pair is the same solar calculation a day on: near
       enough at these latitudes to place the marks, and marked as the second
       day's so it is not read as the first's. */
    if (S.full && sr && ss) {
      const DAYMS = 864e5;
      marks.push(['tomorrow’s midnight', dEnd1, 'var(--muted)', null]);
      marks.push(['tomorrow’s sunrise', sr + DAYMS, '#e0a020', '3 3']);
      marks.push(['tomorrow’s sunset', ss + DAYMS, '#e0a020', '3 3']);
    }
    if (market && M.listed) marks.unshift(['listed', P(M.listed), 'var(--muted)', null]);
    const MARK_NOTE = { midnight: 'the contract day begins (station local time)', sunrise: 'NOAA solar approximation for the station', sunset: 'NOAA solar approximation for the station',
      'tomorrow’s midnight': 'the day-ahead contract day begins (station local time)',
      'tomorrow’s sunrise': 'the same solar approximation carried forward one day',
      'tomorrow’s sunset': 'the same solar approximation carried forward one day',
                        'day end': 'the contract day ends; the settlement value is the extreme through here', listed: 'when the exchange listed this day’s contracts (the record’s first quote)' };
    marks.forEach(([lb, t, col, dash]) => {
      if (t == null || t < w0 || t > d1) return;
      const a = { x1: x(t), x2: x(t), y1: S.T, y2: S.B, stroke: col, 'stroke-width': lb === 'midnight' ? 1.1 : 0.9 };
      if (dash) a['stroke-dasharray'] = dash;
      g.appendChild(el('line', a));
      const html = () => tip.rows(lb.charAt(0).toUpperCase() + lb.slice(1), [['Time', WXC.clockFull(t, tz)]], MARK_NOTE[lb]);
      g.appendChild(bind(txt(lb, { x: x(t), y: S.T - 5, 'text-anchor': 'middle', class: 'mklab' }), html, false));
    });

    // ---- checked strikes: level lines across every panel
    picked.forEach(pk => {
      g.appendChild(el('line', { x1: S.L, x2: rightEdge, y1: y(pk.K), y2: y(pk.K), stroke: pk.col, 'stroke-width': 1.3, opacity: .5, 'pointer-events': 'none' }));
      g.appendChild(txt((pk.side === 'h' ? '>' : '<') + pk.K + '°', { x: S.R - 6, y: y(pk.K) - 3, 'text-anchor': 'end', 'font-size': 9, 'font-weight': 700, fill: pk.col, opacity: .85 }));
    });

    // ---- level lines across the contract day, values and names in the gutter
    levels.forEach(L => {
      g.appendChild(el('line', { x1: x(d0), x2: S.R, y1: y(L.v), y2: y(L.v), stroke: L.col, 'stroke-width': 1.25, opacity: .85, 'pointer-events': 'none' }));
      g.appendChild(bind(el('line', { x1: x(d0), x2: S.R, y1: y(L.v), y2: y(L.v), stroke: 'transparent', 'stroke-width': 7, 'pointer-events': 'stroke' }), () => levelTip(L), true));
    });
    const srt = levels.slice().sort((a, b) => b.v - a.v);
    const gap = 13; let prev = -1e9;
    const labY = srt.map(L => { let yy = Math.max(y(L.v), S.T + 6); if (yy - prev < gap) yy = prev + gap; prev = yy; return yy; });
    srt.forEach((L, i) => {
      g.appendChild(bind(txt(L.v.toFixed(unit === 'F' ? 0 : 1), { x: S.GV, y: y(L.v) + 3.5, class: 'lvlval', fill: L.col }), () => levelTip(L), true));
      g.appendChild(el('line', { x1: S.GL0, x2: S.GL1, y1: y(L.v), y2: labY[i], stroke: L.col, 'stroke-width': .7, opacity: .5 }));
      // the full view gives its width to two ladders, so the names shorten to
      // the initials the key below already spells out rather than running into
      // the strike labels
      const SHORT = { 'Weather Service': 'NWS', 'Blend of Models': 'NBM', 'GFS MOS': 'MOS', 'LAMP': 'LAMP' };
      const nm = S.full
        ? (Object.keys(SHORT).reduce((a, k) => (String(L.nm).indexOf(k) === 0 ? SHORT[k] : a), null)
           || String(L.nm).split(' ')[0]) + (String(L.nm).indexOf('issued') >= 0 ? ' (iss)' : '')
        : L.nm;
      g.appendChild(bind(txt(nm, { x: S.GN, y: labY[i] + 3.5, class: 'lvlnm', fill: L.col }), () => levelTip(L), true));
    });
    if (levels.length) g.appendChild(el('line', { x1: S.R, x2: S.R, y1: S.T, y2: S.B, stroke: 'var(--rule)', 'stroke-width': .9 }));
    const nmeta = snaps.nm && snaps.nm.data;
    const normalTip = n => tip.rows('NCEI daily normal ' + n.nm.replace('normal ', '') + ' — ' + isoDate(M.day).replace(/, \d{4}$/, ''), [
      ['Normal', WXC.deg(n.v)],
      ['Spread (1 sd)', nm && (n.nm === 'normal high' ? nm.tmaxSd : nm.tminSd) != null ? '±' + (n.nm === 'normal high' ? nm.tmaxSd : nm.tminSd).toFixed(1) + '°' : null],
      ['Station', nmeta ? (nmeta.ghcn || '') + (nmeta.distanceKm != null ? ' · ' + nmeta.distanceKm + ' km from the airport' : '') : null],
      ['Dataset', nmeta && nmeta.dataset],
    ], 'NCEI climate normals · click to pin');
    normals.forEach(n => {
      g.appendChild(el('line', { x1: S.L, x2: S.L + 14, y1: y(n.v), y2: y(n.v), stroke: 'var(--muted)', 'stroke-width': 1.4 }));
      g.appendChild(bind(el('line', { x1: S.L, x2: S.L + 90, y1: y(n.v), y2: y(n.v), stroke: 'transparent', 'stroke-width': 9, 'pointer-events': 'stroke' }), () => normalTip(n), true));
      g.appendChild(bind(txt(n.nm + ' ' + n.v.toFixed(0) + '°', { x: S.L + 17, y: y(n.v) + 3.5, class: 'mklab' }), () => normalTip(n), true));
    });
    if (unit === 'C') g.appendChild(txt(N.length ? 'Non-US station: no NWS forecast; NBM guidance covers Canada.' : 'Non-US station: US government feeds carry observations only.',
      { x: S.L + 8, y: S.T + 16, class: 'axl' }));

    // ---- series
    /* Every series here is a sequence of readings — an observation on the hour,
       a forecast for a step — so each one carries a dot at the value it actually
       holds. A bare line between them draws a temperature for every instant in
       between that nothing measured.

       The dots are drawn only where they would read as separate marks; on a
       crowded axis they would thicken the line rather than clarify it. */
    const dotsFor = pts => {
      if (pts.length < 2) return 0;
      const gap = Math.abs(x(pts[pts.length - 1].t) - x(pts[0].t)) / (pts.length - 1);
      return gap >= 5 ? Math.min(2.6, Math.max(1.5, gap / 4)) : 0;
    };
    const line = (pts, attrs) => {
      const grp = el('g');
      grp.appendChild(el('path', Object.assign({ d: pts.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ',' + y(p.v).toFixed(1)).join(''),
        fill: 'none', 'stroke-width': 2, 'stroke-linejoin': 'round' }, attrs)));
      const r = dotsFor(pts);
      if (r) pts.forEach(p => grp.appendChild(el('circle', { class: 'rdot', cx: x(p.t).toFixed(1), cy: y(p.v).toFixed(1),
        r, fill: attrs.stroke || 'var(--ink)', opacity: attrs.opacity == null ? 1 : attrs.opacity,
        'pointer-events': 'none' })));
      return grp;
    };
    /* What happened between the hourly reports.

       The trace is the hourly METAR record, which is what a contract settles on
       and is twenty-four readings of a day that had nearly three hundred. The
       band is the range the five minute stream covered inside each of those
       hours: where it is tall, the hourly number is one sample of a range, and a
       brief peak between two reports is a thing that happened and is not in the
       record the contract reads.

       Drawn first and faintly, so it sits behind everything and reads as
       context. It is never a line: a line through it would look like a second
       observation record, and there is only one of those. */
    const sub = ((snaps.sub && snaps.sub.data) || {}).hourly || [];
    if (sub.length) {
      const band = sub.map(q => ({ t: Date.parse(q.h), lo: q.lo, hi: q.hi, n: q.n }))
        .filter(q => isFinite(q.t) && q.lo != null && q.hi != null && q.t >= w0 && q.t <= d1)
        .sort((a, b) => a.t - b.t);
      if (band.length > 1) {
        // each hour is a box across the hour it covers, not a point at its start
        const HR = 3600000;
        const d = band.map(q => 'M' + x(q.t).toFixed(1) + ',' + y(q.hi).toFixed(1)
                              + 'L' + x(q.t + HR).toFixed(1) + ',' + y(q.hi).toFixed(1)
                              + 'L' + x(q.t + HR).toFixed(1) + ',' + y(q.lo).toFixed(1)
                              + 'L' + x(q.t).toFixed(1) + ',' + y(q.lo).toFixed(1) + 'Z').join(' ');
        g.appendChild(el('path', { d, fill: 'var(--obs)', 'fill-opacity': .13, stroke: 'none',
                                   'pointer-events': 'none' }));
      }
    }
    // with yesterday overlaid, today is the thing being compared against and
    // steps back; without it, today is at full strength as usual
    const dim = showYday ? 0.3 : 1;
    const od = v => Math.round(v * dim * 100) / 100;
    const drawYesterday = () => ySeries.forEach(sr => {
      const a = { stroke: sr.col, 'stroke-width': sr.w, opacity: sr.op };
      if (sr.dash) a['stroke-dasharray'] = sr.dash;
      g.appendChild(line(sr.pts, a));
    });
    if (showYday && !YD.nws && !YD.nbm) g.appendChild(txt('Yesterday’s as-issued forecast appears here once the archive is a day old.', { x: S.L + 8, y: S.T + 30, class: 'axl' }));
    if (LAI.length) g.appendChild(line(LAI, { stroke: COL.lamp, 'stroke-width': 1.2, 'stroke-dasharray': '2 3', opacity: od(.7) }));
    if (NA.length) g.appendChild(line(NA, { stroke: COL.nbm, 'stroke-width': 1.3, 'stroke-dasharray': '2 3', opacity: od(.75) }));
    if (A.length) {
      g.appendChild(line(A, { stroke: COL.nws, 'stroke-width': 1.4, 'stroke-dasharray': '2 3', opacity: od(.8) }));
      const ai = AI.nws;
      g.appendChild(txt((ai.preDay ? 'issued ' : 'first archived cycle, ') + clock(A[0].t, tz), { x: x(A[0].t) + 6, y: y(A[0].v) + 14, class: 'mklab', fill: COL.nws }));
    }
    if (LA.length) g.appendChild(line(LA, { stroke: COL.lamp, 'stroke-width': 1.6, 'stroke-dasharray': '1 3', opacity: od(.9) }));
    if (N.length) g.appendChild(line(N, { stroke: COL.nbm, 'stroke-width': 2, 'stroke-dasharray': '5 4', opacity: od(.9) }));
    if (F.length) g.appendChild(line(F, { stroke: COL.nws, 'stroke-width': 2.4, opacity: od(.95) }));
    const obsMeta = {};
    ((ob && ob.rows) || []).forEach(r => { obsMeta[P(r.t)] = r; });
    const obsTip = p => { const r = obsMeta[p.t] || {}; const inDay = p.t >= d0 && p.t < d1; return tip.rows((r.type || 'METAR') + ' observation' + (inDay ? '' : ' — before the contract day'), [
      ['Time', WXC.clockFull(p.t, tz) + ' · ' + WXC.dateShort(p.t, tz)], ['Temperature', WXC.deg(p.v)],
      ['Decoded from', SRC[r.src] || r.src || null],
      ['Counts toward ' + isoDate(M.day).replace(/, \d{4}$/, '') + ' settlement', inDay ? (r.type === 'SPECI' ? 'yes (SPECI reports count)' : 'yes') : 'no (outside the contract day)'],
    ], 'aviationweather.gov METAR · the crosshair lists every series at this time'); };
    if (O.length) {
      g.appendChild(line(O, { stroke: COL.obs, 'stroke-width': 2, opacity: od(1) }));
      O.forEach(p => { g.appendChild(el('circle', { cx: x(p.t), cy: y(p.v), r: 1.9, fill: COL.obs, opacity: od(1) })); g.appendChild(bind(el('circle', { cx: x(p.t), cy: y(p.v), r: 5, fill: 'transparent', 'pointer-events': 'all' }), () => obsTip(p), false)); });
    }
    // last, so the day being asked about sits on top of the day it is compared with
    drawYesterday();

    /* The legend, in the figure's own empty ground.

       Lower right of the temperature panel: by then the day's trace has fallen
       away and nothing else is competing for the space, so the legend sits where
       a reader is already looking rather than under the chart where it has to be
       found. Each source carries when its standing cycle was issued, which is
       the fact that decides how much of the day that line had already seen. */
    (function legend() {
      const items = [{ nm: 'Observed (METAR)', col: COL.obs, w: 2.4, dash: null, iss: null }];
      if (sub.length) items.push({ nm: 'Range between reports', col: COL.obs, band: true,
                                    iss: 'not settlement' });
      ['nws', 'nbm', 'lamp', 'mav'].forEach(k => {
        const cyc = c[k + 'Cycle'];
        const ms = parseStamp(cyc);
        if (!FULLNAME[k]) return;
        const age = isNaN(ms) ? null : Date.now() - ms;
        items.push({ nm: FULLNAME[k], col: COL[k], w: 2, dash: k === 'nbm' ? '5 4' : (k === 'lamp' ? '1 3' : null),
                     iss: isNaN(ms) ? null : clock(ms, tz) + (age != null ? ' \u00b7 ' + ageText(age) : '') });
      });
      const lh = 12.5, bw = 216;
      /* Lower right of the whole figure, not of the temperature panel: on the
         market layouts that is the ground to the right of the price series
         and below the strike ladders, which nothing else uses. Without the
         market panels the figure ends at the temperature panel and the old
         corner is still the right one. */
      const y1 = (market && S.PL1 != null) ? S.PL1 : S.B - 6;
      const y0 = y1 - items.length * lh - 6;
      const bx = (market && S.PL1 != null) ? S.W - bw - 8 : S.R - bw - 6;
      g.appendChild(el('rect', { x: bx, y: y0, width: bw, height: y1 - y0, rx: 4,
                                 fill: 'var(--panel)', 'fill-opacity': .95, stroke: 'var(--line)',
                                 'stroke-width': .8, 'pointer-events': 'none' }));
      items.forEach((it, i) => {
        const yy = y0 + 12 + i * lh;
        if (it.band) {
          g.appendChild(el('rect', { x: bx + 7, y: yy - 5, width: 15, height: 7, fill: it.col,
                                     'fill-opacity': .18, 'pointer-events': 'none' }));
        } else {
          g.appendChild(el('line', Object.assign({ x1: bx + 7, x2: bx + 22, y1: yy - 2, y2: yy - 2,
            stroke: it.col, 'stroke-width': it.w || 2, 'pointer-events': 'none' },
            it.dash ? { 'stroke-dasharray': it.dash } : {})));
        }
        g.appendChild(txt(it.nm, { x: bx + 27, y: yy + 1, 'font-size': 9, fill: 'var(--ink)',
                                   'pointer-events': 'none' }));
        if (it.iss) g.appendChild(txt(it.iss, { x: bx + bw - 7, y: yy + 1, 'font-size': 8.5,
                                                'text-anchor': 'end', fill: 'var(--muted)',
                                                'pointer-events': 'none' }));
      });
    })();

    // ---- as-of marker
    const asof = snaps.ob && snaps.ob.asof;
    if (asof && asof > w0 && asof < d1) {
      g.appendChild(el('line', { x1: x(asof), x2: x(asof), y1: S.T, y2: market ? S.PL1 : S.B, stroke: 'var(--muted)', 'stroke-dasharray': '3 3', 'pointer-events': 'none' }));
      g.appendChild(txt('data as of', { x: x(asof) + 5, y: S.T + 30, class: 'axl' }));
    }

    // ---- observed extremes so far, contract day only
    const Oday = O.filter(p => p.t >= d0 && p.t < d1);
    if (Oday.length) {
      const hi2 = Oday.reduce((a, b) => b.v > a.v ? b : a), lo2 = Oday.reduce((a, b) => b.v < a.v ? b : a);
      [['Obs high ', hi2, 'var(--warm)', -5], ['Obs low ', lo2, 'var(--cool)', 13]].forEach(([lb, pt, col, dy]) => {
        g.appendChild(el('line', { x1: x(w0), x2: rightEdge, y1: y(pt.v), y2: y(pt.v), stroke: col, 'stroke-width': .9, 'stroke-dasharray': '2 2', opacity: .75, 'pointer-events': 'none' }));
        const r = obsMeta[pt.t] || {};
        const html = () => tip.rows('Observed ' + (lb.trim().split(' ')[1]) + ' so far, ' + isoDate(M.day), [
          ['Value', WXC.deg(pt.v)], ['At', WXC.clockFull(pt.t, tz)], ['Report', (r.type || 'METAR') + (r.src ? ', ' + SRC[r.src] : '')],
          ['Reports so far today', ob && ob.today && ob.today.n != null ? String(ob.today.n) : null],
        ], 'the settlement value is this extreme at the end of the contract day');
        g.appendChild(bind(txt(lb + pt.v.toFixed(1) + '°', { x: x(w0) + 5, y: y(pt.v) + dy, class: 'axl', fill: col, 'font-weight': 700 }), html, true));
      });
    }

    // ---- the market layer: ladders on the shared axis, price panels below
    const priceSer = [];
    // one ladder, wherever it is put. The full view calls this twice, for the
    // day being traded and the day-ahead board, so both read against the same
    // temperature axis and the same forecast trace.
    function drawLadder(LD, LX, LW, heading, labels) {
      if (!LD) return;
      const lxp = p => LX + (p / 100) * LW;
      g.appendChild(txt(heading, { x: LX + LW / 2, y: S.T - 8, 'text-anchor': 'middle', class: 'axl' }));
      [['high', '>'], ['low', '<']].forEach(([m, cmp]) => {
        LD[m].forEach(L => {
          const yy = y(L.strike);
          if (L.yes == null) {
            g.appendChild(bind(el('rect', { x: LX, y: yy - 5.5, width: LW, height: 11, fill: 'transparent', stroke: 'var(--line)', 'stroke-width': .8, 'stroke-dasharray': '3 2' }), () => ladderTip(L, m === 'high' ? 'h' : 'l', LD, c), true));
            g.appendChild(txt('no bids', { x: LX + LW / 2, y: yy + 3.2, class: 'ax', 'text-anchor': 'middle', 'pointer-events': 'none' }));
          } else {
            const gw = L.yes / 100 * LW;
            const yb = el('rect', { x: LX, y: yy - 5.5, width: Math.max(gw, 1), height: 11, fill: 'var(--yes)', stroke: 'var(--panel)', 'stroke-width': .6 });
            const nb = el('rect', { x: LX + gw, y: yy - 5.5, width: Math.max(LW - gw, 1), height: 11, fill: 'var(--no)', stroke: 'var(--panel)', 'stroke-width': .6 });
            const sd = m === 'high' ? 'h' : 'l';
            const url = strikeUrl(LD, sd, L);
            [yb, nb].forEach(bar => {
              g.appendChild(bind(bar, () => ladderTip(L, sd, LD, c), !url));
              if (url) WXM.linkTo(bar, url, 'Open ' + (L.label || L.strike) + ' on IBKR');
            });
            if (LD.live && L.side !== 'mid') g.appendChild(el('rect', { x: LX, y: yy - 5.5, width: LW, height: 11, fill: 'none', stroke: 'var(--panel)', 'stroke-width': 1.2, 'stroke-dasharray': '2 2', 'pointer-events': 'none' }));
            if (gw >= 26) g.appendChild(txt(L.yes + '¢', { x: LX + 3, y: yy + 3.2, class: 'ladtxt', 'pointer-events': 'none' }));
            if (LW - gw >= 26) g.appendChild(txt((100 - L.yes) + '¢', { x: LX + LW - 3, y: yy + 3.2, class: 'ladtxt', 'text-anchor': 'end', 'pointer-events': 'none' }));
          }
          if (labels !== false) {
            /* The temperature beside the bar is the switch for that strike's
               price line below. It used to be a second row of chips above the
               chart doing the same job; one control in the place a reader is
               already reading the ladder is fewer things on the page. */
            const sd2 = m === 'high' ? 'h' : 'l';
            const key = sd2 + ':' + L.strike;
            const on = checked.has(key);
            const at = S.full ? { x: LX - 5, 'text-anchor': 'end' } : { x: LX + LW + 5 };
            const lab = txt(cmp + L.strike + '°', Object.assign({ y: yy + 3.5,
              class: 'ax strikepick' + (on ? ' on' : ''),
              fill: on ? skColor(sd2, L.strike, LD) : (m === 'high' ? 'var(--warm)' : 'var(--cool)'),
              'font-weight': on ? 700 : 400 }, at));
            lab.style.cursor = 'pointer';
            lab.onclick = ev => {
              ev.stopPropagation();
              if (checked.has(key)) checked.delete(key); else checked.add(key);
              draw();
            };
            /* No hover box here, and no pin on click. The temperature is a
               switch, and flicking a switch kept planting a tooltip that then
               had to be dismissed; the bar beside it already answers every
               question the box used to. */
            g.appendChild(lab);
          }
        });
      });
      if (LD.live && !LD.listed) g.appendChild(txt('not listed yet', { x: LX + LW / 2, y: (S.T + S.B) / 2, 'text-anchor': 'middle', class: 'axl' }));
      [0, 50, 100].forEach(p => g.appendChild(txt(p + (p === 100 ? '¢' : ''), { x: lxp(p), y: S.B + 15, 'text-anchor': 'middle', class: 'ax' })));
    }

    if (market && lad) {
      drawLadder(lad, S.LX, S.LW, S.full ? 'Today · ' + isoDate(lad.day).replace(/, \d{4}$/, '') : 'Strike ladders (' + lad.label + ')');
      if (S.full) {
        const lad2 = WXM.ladder(c, levelsFor(c), 'tomorrow');
        drawLadder(lad2, S.LX2, S.LW2, lad2 ? 'Tomorrow · ' + isoDate(lad2.day).replace(/, \d{4}$/, '') : 'Tomorrow', false);
      }
      g.appendChild(txt('Yes green, No red' + (lad.live ? ' · dotted: bids on one side only' : ' · placeholders'),
                        { x: S.LX + S.LW / 2, y: S.B + 30, 'text-anchor': 'middle', class: 'ax' }));
      const obsRows = (ob && ob.rows) || [];
      const fseries = [AI.nws && AI.nws.rows, AI.nbm && AI.nbm.rows, fc && fc.nws && fc.nws.hourly, fc && fc.nbm && fc.nbm.hourly];
      const how = lad.live ? 'ForecastEx Yes price, midway between the Yes bid and one dollar less the No bid, sampled every 10 minutes' : 'placeholder';
      [['h', 'Yes price — high strikes (' + how + ')', S.PH0, S.PH1], ['l', 'Yes price — low strikes (' + how + ')', S.PL0, S.PL1]].forEach(([side, ttl, p0, p1]) => {
        const ypp = v => p1 - (v / 100) * (p1 - p0);
        g.appendChild(el('line', { x1: S.L, x2: S.R, y1: p0 - 24, y2: p0 - 24, stroke: 'var(--line)' }));
        g.appendChild(txt(ttl, { x: S.L, y: p0 - 10, class: 'axl' }));
        [0, 50, 100].forEach(p => { g.appendChild(el('line', { x1: S.L, x2: S.R, y1: ypp(p), y2: ypp(p), class: 'grid' })); g.appendChild(txt(p + '¢', { x: S.L - 8, y: ypp(p) + 4, 'text-anchor': 'end', class: 'ax' })); });
        /* Every strike, faintly; the chosen one dark.

           Drawing only what had been picked meant an empty panel until a reader
           knew there was something to pick, and gave no sense of how this strike
           sits against its neighbours. All of them are drawn now — the whole
           ladder is the shape of the market's opinion — and choosing one brings
           it forward rather than summoning it. */
        const chosen = new Set(picked.filter(pk => pk.side === side).map(pk => pk.K));
        const all = (lad && lad[side === 'h' ? 'high' : 'low']) || [];
        all.forEach(L => {
          if (chosen.has(L.strike)) return;
          const pts = WXM.pricePath(obsRows, fseries, unit, side, L.strike, c).filter(p => p.t >= w0);
          if (pts.length < 2) return;
          g.appendChild(el('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ',' + ypp(p.v).toFixed(1)).join(''),
                                     fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1,
                                     opacity: .22, 'pointer-events': 'none' }));
        });
        const endLabs = [];
        picked.filter(pk => pk.side === side).forEach(pk => {
          const pts = WXM.pricePath(obsRows, fseries, unit, pk.side, pk.K, c).filter(p => p.t >= w0);
          if (!pts.length) return;
          priceSer.push({ label: (pk.side === 'h' ? '>' : '<') + pk.K + '°', col: pk.col, pts });
          g.appendChild(el('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ',' + ypp(p.v).toFixed(1)).join(''), fill: 'none', stroke: pk.col, 'stroke-width': 1.8 }));
          // a price history is a run of quotes, not a continuous price
          const pr = pts.length > 1
            ? Math.abs(x(pts[pts.length - 1].t) - x(pts[0].t)) / (pts.length - 1) : 0;
          if (pr >= 5) pts.forEach(p => g.appendChild(el('circle',
            { class: 'rdot', cx: x(p.t).toFixed(1), cy: ypp(p.v).toFixed(1), r: Math.min(2.4, pr / 4),
              fill: pk.col, 'pointer-events': 'none' })));
          endLabs.push({ y: ypp(pts[pts.length - 1].v), s: (pk.side === 'h' ? '>' : '<') + pk.K + '° ' + pts[pts.length - 1].v + '¢', col: pk.col });
        });
        endLabs.sort((a, b) => a.y - b.y);
        let pv = -1e9; endLabs.forEach(L => { let yy = Math.max(L.y, p0); if (yy - pv < 11) yy = pv + 11; pv = yy; g.appendChild(txt(L.s, { x: S.R + 6, y: yy + 3, 'font-size': 9, 'font-weight': 700, fill: L.col })); });
      });
    }

    // the embed has no footer and no strike row: when it shows live prices, the
    // disclosure and the not-a-quote sentence go directly under the chart
    const card = svg.closest ? svg.closest('.card') : null;
    let emb = document.getElementById('embedDisclosure');
    if (market && lad && lad.live && window.WX && WX.target === 'embed' && card) {
      if (!emb) { emb = h('p', { class: 'cap', id: 'embedDisclosure' }); card.insertAdjacentElement('afterend', emb); }
      emb.textContent = (WX.disclosure || '') + ' Prices are the exchange’s published quotes at the time shown, not a bid or a solicitation to trade by this site, and can be stale.';
    } else if (emb) emb.remove();

    // ---- titles
    // the standalone page has a heading of its own directly above this, so the
    // figure repeating it is one line of the same words twice; the embed has no
    // page around it and still needs to say what it is showing
    if (WX.target === 'embed' || svgId !== 'chart') {
      g.appendChild(txt(c.city + ' (' + c.station + ') — ' + dateShort(d0, tz), { x: S.L, y: 16, 'font-size': 14, 'font-weight': 700, fill: 'var(--navy)' }));
    }
    g.appendChild(txt('Temperature (°' + unit + '), local time' + (unit === 'C' ? ' — Celsius station' : ''), { x: S.W - 14, y: 16, 'text-anchor': 'end', class: 'axl' }));

    HV = { w0, d1, tz, S, market,
      series: [{ nm: 'Observed', pts: O, col: COL.obs }, { nm: 'NWS now', pts: F, col: COL.nws }, { nm: 'NBM', pts: N, col: COL.nbm },
               { nm: 'LAMP', pts: LA, col: COL.lamp }, { nm: 'NWS as issued', pts: A, col: COL.nws }, { nm: 'NBM as issued', pts: NA, col: COL.nbm },
               { nm: 'LAMP as issued', pts: LAI, col: COL.lamp }]
        .concat(ySeries.map(s => ({ nm: s.nm, pts: s.pts, col: s.col }))).filter(s => s.pts.length),
      prices: priceSer };
  }

  // ---- crosshair: temperature and time in the top panel, price in the bottom
  function hover(svg) {
    let hline = null, vline = null;
    svg.addEventListener('mousemove', e => {
      if (!HV) return;
      const S = HV.S;
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      const inTemp = q.x >= S.L && q.x <= S.R && q.y >= S.T && q.y <= S.B;
      const inPrice = HV.market && q.x >= S.L && q.x <= S.R && ((q.y >= S.PH0 && q.y <= S.PH1) || (q.y >= S.PL0 && q.y <= S.PL1));
      const hoverSide = HV.market && q.y >= S.PL0 ? 'l' : 'h';
      const clear = () => { tip.hide(); if (hline) hline.remove(); if (vline) vline.remove(); hline = vline = null; };
      if (!inTemp && !inPrice) { clear(); return; }
      const t = HV.w0 + (q.x - S.L) / (S.R - S.L) * (HV.d1 - HV.w0);
      const ts = clock(t, HV.tz) + ' · ' + dateShort(t, HV.tz);
      const near = pts => { let b = null; for (const p of pts) { const d2 = Math.abs(p.t - t); if (d2 <= 45 * 6e4 && (!b || d2 < b.d2)) b = { d2, p }; } return b && b.p; };
      const rows = [];
      (inTemp ? HV.series : []).forEach(s => { const p = near(s.pts); if (p) rows.push([sw(s.col) + s.nm, p.v.toFixed(1) + '°']); });
      if (inPrice) {
        HV.prices.filter(s => (hoverSide === 'h') === (s.label[0] === '>')).forEach(s => {
          const p = near(s.pts);
          if (p) rows.push([sw(s.col) + 'Yes ' + s.label, (p.side && p.side !== 'mid' ? p.v + '¢ (' + (p.side === 'bid' ? 'Yes bids only' : 'No bids only') + ')' : p.v + '¢') + (p.bid != null && p.ask != null ? ' · Yes bid ' + p.bid + ' / No bid ' + (100 - p.ask) : '')]);
        });
        const o = HV.series.find(s => s.nm === 'Observed'); const op = o && near(o.pts);
        if (op) rows.push([sw(COL.obs) + 'Observed', op.v.toFixed(1) + '°']);
      }
      if (!rows.length) { clear(); return; }
      tip.show(e, tip.rows(ts, rows, inPrice ? 'Yes price in cents, midway between the Yes bid and one dollar less the No bid' : null));
      const mk = () => el('line', { stroke: 'var(--muted)', 'stroke-width': .8, 'stroke-dasharray': '2 2', 'pointer-events': 'none' });
      if (!hline || !hline.isConnected) { hline = mk(); svg.appendChild(hline); }
      hline.setAttribute('x1', q.x); hline.setAttribute('x2', q.x); hline.setAttribute('y1', S.T); hline.setAttribute('y2', HV.market ? S.PL1 : S.B);
      /* And a line the other way, across the ladders.

         The vertical line answers "what did every series say at this moment".
         The horizontal one answers the question the ladder is for: at the
         temperature under the cursor, which strikes are above it and which
         below, and what is each one priced at. It runs from the plot through
         both ladders, so a temperature on the trace can be read straight across
         into the bars. */
      if (inTemp && HV.market && HV.S.LX != null) {
        const rEnd = (HV.S.full && HV.S.LX2 != null) ? HV.S.LX2 + HV.S.LW2 : HV.S.LX + HV.S.LW;
        if (!vline || !vline.isConnected) { vline = mk(); svg.appendChild(vline); }
        vline.setAttribute('x1', S.L); vline.setAttribute('x2', rEnd);
        vline.setAttribute('y1', q.y); vline.setAttribute('y2', q.y);
      } else if (vline) { vline.remove(); vline = null; }
    });
    svg.addEventListener('mouseleave', () => { tip.hide(); if (hline) hline.remove(); if (vline) vline.remove(); hline = vline = null; });
  }

  /* How current each source is, in the figure's own legend.

     This was a table under the chart and a distinction that did not survive
     contact with what the products are. There is no useful line between "the
     newest cycle" and "the cycle standing before the day began": they are both
     forecasts, issued at different times, and what a reader needs is when each
     one was issued and therefore how much of the day it had already seen.

     So the legend carries the issue time beside the name, in the empty ground
     at the lower right of the plot, where the day's trace has already fallen
     away and nothing else is competing for the space. */
  const TYPICAL_H = { nws: 6, nbm: 1, lamp: 1, mav: 6 };
  const FULLNAME = { nws: 'Weather Service', nbm: 'Blend of Models',
                     lamp: 'Aviation guidance', mav: 'GFS MOS' };

  function ageText(ms) {
    if (ms == null || !isFinite(ms)) return '';
    const m = Math.max(0, Math.round(ms / 60000));
    if (m < 60) return m + ' min old';
    const hh = Math.floor(m / 60), mm = m % 60;
    return hh + 'h ' + (mm ? String(mm).padStart(2, '0') + 'm ' : '') + 'old';
  }
  function parseStamp(sid) {
    if (!sid) return NaN;
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?Z?$/.exec(sid);
    return Date.parse(m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6] || '00'}Z` : sid);
  }

  async function init(opts = {}) {
    svgId = opts.svgId || 'chart';
    onSelect = opts.onSelect || null;
    tip = WXC.tooltip();
    const sres = await WXD.get('summary.json');
    // v=2: past a month-cached copy from before the regional frames
    try { locIndex = (await WXD.get('locator/index.json?v=2', 1440)).data; } catch (e) { locIndex = null; }
    summary = sres.data || { cities: [], asof: null };
    if (opts.basemap) summary.base = opts.basemap;
    await WXM.loadSummary();                      // implied medians for the picker dots (live market layer only)
    const st = $('#pageStatus'); if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([sres], 10)); }
    const want = opts.station || WXC.param('station') || WXC.param('city') || 'KLAX';
    const svg = $('#' + svgId);
    hover(svg);
    const xh = $('#chartExpand'), card = $('#chartCard');
    if (xh && card && !xh.childElementCount) xh.appendChild(WXC.expander(card, 'Expand'));
    const dh = $('#cityDaysExpand'), dcard = $('#cityDaysWrap');
    if (dh && dcard && !dh.childElementCount) dh.appendChild(WXC.expander(dcard, 'Expand'));
    const yb = $('#ydayBtn'); if (yb) yb.onclick = e => { showYday = !showYday; e.target.classList.toggle('on'); draw(); };
    /* One control, three states, so the day being read is always visible
       rather than inferred from whether a toggle is pressed. */
    const modes = { dayToday: 'today', dayTomorrow: 'tomorrow', dayBoth: 'both' };
    Object.entries(modes).forEach(([id, mode]) => {
      const b = $('#' + id); if (!b) return;
      b.classList.toggle('on', dayMode === mode);
      b.onclick = () => {
        dayMode = mode;
        Object.keys(modes).forEach(k => { const o = $('#' + k); if (o) o.classList.toggle('on', k === id); });
        draw(); drawTitle(city());
      };
    });
    if (!summary.cities.length) { svg.innerHTML = ''; svg.appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    await select(summary.cities.some(c => c.station === want) ? want : summary.cities[0].station, false);
  }

  return { init, select };
})();
