/* The landing map: every station on one canvas, shaded by the day's level.

   Data in: summary.json (stations, observed so far, forecast levels),
   field.json (the derived shading), assets/basemap.json (state outlines,
   pre-projected). Four modes, each a day and a side.

   The reference the dots and the shading are read against differs by day.
   Tomorrow it is the NWS forecast. Today it is what the day is now expected
   to reach, which is what the station has already recorded taken with the
   forecast for the hours left: a forecast begins at the current hour, so a
   day whose low was set at dawn no longer has that low anywhere in it, and
   the forecast's own minimum is then the coolest hour still to come. The
   summary applies that before the field is interpolated, so the dots and the
   shading under them are the same quantity.

   With the market layer on, the dots carry the gap between the
   market-implied median (live: the exchange's ladder; else a labelled
   placeholder) and that reference. */
window.WXMap = (() => {
  const { el, txt, h, $, deg } = WXC;
  const RAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];
  let summary = null, field = null, base = null, world = null, mode = null, tip = null;
  const TOOL = { nws: 'National Weather Service', nbm: 'Blend of Models', lamp: 'Aviation guidance (LAMP)',
                 mav: 'GFS MOS', fx: 'ForecastEx' };
  const ord = n => n + (n % 10 === 1 && n !== 11 ? 'st' : n % 10 === 2 && n !== 12 ? 'nd' : n % 10 === 3 && n !== 13 ? 'rd' : 'th');

  // The four numbers the day turns on, precomputed by the pipeline so the
  // landing page stays light. Each is a fact with its own provenance, not a
  // summary of the site: the accuracy standing, yesterday's largest single
  // miss, the widest disagreement about tomorrow, and one hurricane contract.



  function fieldColor(v) {
    const [a, b] = field.domain, t = Math.max(0, Math.min(1, (v - a) / (b - a))) * (RAMP.length - 1);
    const i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = s => [1, 3, 5].map(k => parseInt(s.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((av, k) => Math.round(av + (B[k] - av) * f)).join(',') + ')';
  }

  // Both days are interpolated into one field file, so every view has shading
  // under its dots. Columns 2 and 3 are tomorrow, 4 and 5 today.
  const MODES = {
    hiT: { title: () => "TODAY'S HIGHS · shaded by the level now expected for " + tdy(), fld: 4, centred: true,
           val: c => c.nwsHighToday, when: 'today', div: c => WXM.on() ? (WXM.implied(c, 'today') || {}).divHigh : null },
    loT: { title: () => "TODAY'S LOWS · shaded by the level now expected for " + tdy(), fld: 5, centred: true,
           val: c => c.nwsLowToday, when: 'today', div: c => WXM.on() ? (WXM.implied(c, 'today') || {}).divLow : null },
    hi:  { title: () => "TOMORROW'S HIGHS · shaded by the National Weather Service forecast for " + tmw(), fld: 2, centred: true,
           val: c => c.nwsHighTomorrow, when: 'tomorrow', div: c => WXM.on() ? (WXM.implied(c) || {}).divHigh : null },
    lo:  { title: () => "TOMORROW'S LOWS · shaded by the National Weather Service forecast for " + tmw(), fld: 3, centred: true,
           val: c => c.nwsLowTomorrow, when: 'tomorrow', div: c => WXM.on() ? (WXM.implied(c) || {}).divLow : null },
  };
  const tmw = () => { const c = summary.cities.find(x => x.onConus); return c && c.markers ? c.markers.tomorrow : ''; };

  /* The page's own heading, which is the board being traded and the day it
     settles on. The board flips at 5 pm Eastern, so before then this reads
     "today's" and after it "tomorrow's", and it follows the selector rather than
     the clock once a reader has chosen. The date is the station's local calendar
     day the contracts settle on, spelled out: a reader who has just arrived
     should be able to tell what they are looking at without reading a legend. */
  const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                  'August', 'September', 'October', 'November', 'December'];
  function spellDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!m) return '';
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return DAYS[d.getUTCDay()] + ', ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate();
  }
  function pageTitle(key) {
    const when = (key === 'hiT' || key === 'loT') ? "Today's" : "Tomorrow's";
    const side = (key === 'loT' || key === 'lo') ? 'Lows' : 'Highs';
    const day = spellDate((key === 'hiT' || key === 'loT') ? tdy() : tmw());
    return 'ForecastEx Weather Prediction Market for ' + when + ' ' + side + (day ? ' ' + day : '');
  }

  // ---- the typical gap
  //
  // The market has sat below the NWS forecast on highs on every day measured so
  // far, so colouring by the raw sign paints the whole board one colour and says
  // the same thing every morning. What a reader actually wants is which cities
  // disagree UNUSUALLY, so the dots are centred on the median gap across the
  // board for the view being shown, and colour is the sign of the deviation from
  // it. The raw gap is never hidden: it is in the label's tooltip and named in
  // the caption.
  //
  // The median is taken across stations within the view rather than from a
  // per-station history, because it is available on the first day and needs no
  // accumulation. It answers "unusual relative to how the board is priced right
  // now", which is the question. Below five stations there is no useful median,
  // so the centring switches off and the caption says so.
  const MIN_FOR_BASE = 5;
  let gapBase = 0, gapN = 0;
  function median(v) {
    if (!v.length) return 0;
    const a = v.slice().sort((x, y) => x - y), i = a.length >> 1;
    return a.length % 2 ? a[i] : (a[i - 1] + a[i]) / 2;
  }
  function computeBase(M) {
    if (!M.centred) { gapBase = 0; gapN = 0; return; }
    const v = (summary.cities || []).map(c => M.div(c)).filter(x => x != null);
    gapN = v.length;
    gapBase = v.length >= MIN_FOR_BASE ? Math.round(median(v) * 10) / 10 : 0;
  }
  // what the colour and size encode: the gap less the board's typical gap
  const dev = v => (v == null ? null : Math.round((v - gapBase) * 10) / 10);
  const tdy = () => { const c = summary.cities.find(x => x.onConus); return c && c.markers ? c.markers.day : ''; };

  // Which board is the one being traded. The day-ahead contracts list around
  // midday Eastern and the current day's settle that evening, so before 5 pm ET
  // the live board is today's and after it the day-ahead board is the one worth
  // opening on. The other is always one click away.
  const FLIP_HOUR_ET = 17;
  function defaultMode() {
    let hr;
    try {
      hr = +new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hour12: false })
        .format(new Date());
    } catch (e) { hr = new Date().getHours(); }
    if (hr === 24) hr = 0;
    return hr < FLIP_HOUR_ET ? 'hiT' : 'hi';
  }

  // ---- tooltips. Dates on the snapshot are the station's local calendar
  //      dates as 'YYYY-MM-DD' strings, so they are printed by hand here;
  //      parsing them through Date would shift them by the browser's zone.
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const isoDate = s => { if (!s || s.length < 10) return ''; return MON[+s.slice(5, 7) - 1] + ' ' + (+s.slice(8, 10)); };
  const dg = v => (v == null ? '—' : deg(v));
  const gap = d => (d == null ? '' : ' (' + (d > 0 ? '+' + d : d < 0 ? '−' + Math.abs(d) : '0') + '°)');
  const srcTag = s => (s === 'tgroup' ? 'tenths' : s === 'body' ? 'whole degrees' : null);
  const FIELD_FOOT = 'inverse-distance interpolation of the listed stations’ NWS forecasts; not an NWS product';
  const TODAY_FOOT = 'inverse-distance interpolation of what each station has recorded so far today and the NWS forecast for the hours left; not an NWS product';

  // the market-implied median for one side, as a tooltip value: the number
  // with its gap against the NWS forecast, or the reason there is none
  const STATE_TEXT = { unavailable: 'quotes unavailable', unlisted: 'no market listed', day: 'quote summary is for another day',
                       'tomorrow-unlisted': 'tomorrow’s contracts not listed yet', 'no-bids': 'no bids yet' };
  function impliedText(m, v, d, edge) {
    if (!m) return 'not listed yet';
    if (v != null) return deg(v) + gap(d);
    if (edge) return edge === 'above' ? 'above the top strike' : 'below the bottom strike';
    return STATE_TEXT[m.state] || 'no bids yet';
  }

  /* When a board lists. US daily contracts for a local day are listed at noon
     Eastern the day before, which the snapshot stamps on the day it belongs
     to; the day-ahead board therefore lists a day later than the marker the
     current day carries. */
  function listingTime(mk, when) {
    if (!mk.listed) return null;
    const t = Date.parse(mk.listed);
    if (!isFinite(t)) return null;
    return when === 'tomorrow' ? t + 86400000 : t;
  }
  function countdown(ms) {
    const left = ms - Date.now();
    if (left <= 0) return null;
    const h = Math.floor(left / 3600000), m = Math.round((left % 3600000) / 60000);
    if (h >= 1) return h + 'h ' + String(m).padStart(2, '0') + 'm from now';
    return Math.max(1, Math.round(left / 60000)) + ' min from now';
  }

  /* One dot, read for the board on screen.

     The tooltip used to carry both days and both sides at once, twelve rows
     of which two answered the question the reader was asking. It now shows
     the side and the day the buttons select, and nothing else.

     The exchange's own number leads, because that is the thing being traded
     and the reason to be on this page; the forecasts that inform it follow as
     supporting rows. Where the board has not listed yet there is no implied
     value to show, so its place is taken by when the contracts list and how
     long that is from now. */
  function dotTip(c) {
    const mk = c.markers || {};
    const M = MODES[mode];
    const today = M.when === 'today';
    const low = mode === 'lo' || mode === 'loT';
    const side = low ? 'low' : 'high';
    const m = WXM.on() ? WXM.implied(c, today ? 'today' : undefined) : null;
    const tag = WXM.live() ? 'ForecastEx' : 'placeholder';
    const dayIso = today ? mk.day : mk.tomorrow;

    // ---- the exchange first
    const iv = m && (low ? m.impliedLow : m.impliedHigh);
    const idiv = m && (low ? m.divLow : m.divHigh);
    const iedge = m && (low ? m.edgeLow : m.edgeHigh);
    const lead = [];
    if (WXM.on()) {
      lead.push(['<b>Implied ' + side + ' (' + tag + ')</b>',
                 '<b>' + impliedText(m, iv, idiv, iedge) + '</b>']);
      if (iv == null) {
        const lt = listingTime(mk, M.when);
        if (lt) {
          const cd = countdown(lt);
          lead.push(['Contracts list', WXC.clockFull(lt, c.tz) + ' ' + WXC.dateShort(lt, c.tz)]);
          if (cd) lead.push(['That is', cd]);
        }
      }
    }

    // ---- the forecasts behind it, this side and this day only
    const support = today
      ? [['Expected ' + side + ' today',
          dg(low ? c.nwsLowToday : c.nwsHighToday)
            + ((low ? c.nwsLowTodayRunning : c.nwsHighTodayRunning) ? ' <span class="tk">(already recorded)</span>' : '')],
         ['NWS ' + side + ' issued for today', low ? dg(c.nwsIssuedLow) : dg(c.nwsIssuedHigh)],
         ['Observed ' + side + ' so far', obsSoFar(c, low)],
         ['Latest METAR', latestOb(c, mk)]]
      : [['NWS ' + side, dg(low ? c.nwsLowTomorrow : c.nwsHighTomorrow)],
         ['Blend of Models', dg(low ? c.nbmLowTomorrow : c.nbmHighTomorrow)],
         ['GFS MOS', dg(low ? c.mavLowTomorrow : c.mavHighTomorrow)]];

    // ---- what the dot itself is encoding
    const raw = M.div(c);
    const centred = M.centred && gapN >= MIN_FOR_BASE && raw != null;
    const sgn = x => (x > 0 ? '+' : '') + x.toFixed(1) + '°';
    const enc = centred ? [
      [today ? 'Gap to expected' : 'Gap to NWS', sgn(raw)],
      ['The board’s typical gap', sgn(gapBase) + ' (median of ' + gapN + ')'],
      ['This station, against that', sgn(raw - gapBase) + (Math.abs(raw - gapBase) < 0.5 ? ', about typical' : (raw > gapBase ? ', warmer than typical' : ', cooler than typical'))],
    ] : [];

    const head = c.city + ' (' + c.station + ') · ' + (today ? 'today' : 'tomorrow') + '’s ' + side
               + (dayIso ? ' ' + isoDate(dayIso) : '');
    return tip.rows(head, lead)
      + tip.rows('<span class="tk" style="display:block;margin-top:5px">Forecasts</span>', support)
      + (enc.length ? tip.rows('<span class="tk" style="display:block;margin-top:5px">Dot encoding</span>', enc, 'click → city chart')
                    : tip.rows('', [], 'click → city chart'));
  }

  // the observed extreme so far on the side being shown, with its decode
  function obsSoFar(c, low) {
    const v = low ? c.obsLowSoFar : c.obsHighSoFar;
    if (v == null) return '—';
    const t = srcTag(low ? c.obsLowSrc : c.obsHighSrc);
    return deg(v) + (t ? ' <span class="tk">(' + t + ')</span>' : '');
  }
  function latestOb(c, mk) {
    if (!(c.obsLatest && c.obsLatest.t)) return null;
    const ms = Date.parse(c.obsLatest.t), day = WXC.dateShort(ms, c.tz);
    return (day !== isoDate(mk.day) ? day + ' ' : '') + WXC.clockFull(ms, c.tz)
         + (c.obsLatest.type ? ' ' + c.obsLatest.type : '');
  }

  // one cell of the shading: the interpolated NWS level under the pointer
  function cellTip(i) {
    const cell = field.cells[i], M = MODES[mode];
    if (!cell || M.fld == null || cell.length <= M.fld) return '';
    const NAME = { 2: 'Tomorrow’s high', 3: 'Tomorrow’s low', 4: 'Today’s high', 5: 'Today’s low' };
    const foot = M.when === 'today' ? TODAY_FOOT : FIELD_FOOT;
    return tip.rows(M.when === 'today' ? 'Expected field (derived)' : 'NWS forecast field (derived)',
                    [[NAME[M.fld] || 'Forecast', deg(cell[M.fld])]], foot);
  }
  const cellIndex = e => { const i = e.target && e.target.getAttribute && e.target.getAttribute('data-i'); return i == null ? null : +i; };

  function draw() {
    const svg = $('#map'); svg.innerHTML = '';
    const M = MODES[mode];
    const defs = el('defs'), cp = el('clipPath', { id: 'us' });
    cp.appendChild(el('path', { d: base.statePaths })); defs.appendChild(cp); svg.appendChild(defs);
    // a field written before both days existed carries four columns; draw no
    // shading rather than reading past the end of a cell
    const haveField = M.fld != null && field && field.cells && field.cells.length
      && (field.cells[0] || []).length > M.fld;
    if (haveField) {
      const fg = el('g', { 'clip-path': 'url(#us)', 'data-tip-pin': '' });
      field.cells.forEach((cell, i) => fg.appendChild(el('rect', { x: cell[0] - 1, y: cell[1] - 1, width: field.step + 2, height: field.step + 2, fill: fieldColor(cell[M.fld]), 'data-i': i })));
      // one listener for the whole field, keyed by the cell index on the target
      fg.onmousemove = e => { const i = cellIndex(e); if (i != null) tip.show(e, cellTip(i)); };
      fg.onmouseleave = () => tip.hide();
      fg.onclick = e => { const i = cellIndex(e); if (i != null) tip.pin(e, cellTip(i)); };
      svg.appendChild(fg);
    } else {
      svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    }
    svg.appendChild(el('path', { d: base.statePaths, class: 'state' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2' }));
    $('#modeTitle').textContent = M.title();
    const hEl = $('#boardTitle');
    if (hEl) { hEl.textContent = pageTitle(mode); document.title = pageTitle(mode); }

    computeBase(M);
    plot(svg, summary.cities.filter(c => c.onConus), M, c => c.px, c => c.py, [2, 2, 958, 598], false);
    const legend = $('#legend');
    legend.innerHTML = '';
    if (WXM.on()) {
      const w = WXM.live() ? 'ForecastEx implied median' : 'placeholder';
      const centred = M.centred && gapN >= MIN_FOR_BASE;
      const sgn = gapBase > 0 ? '+' : '';
      // the colour key itself lives in the panel below the map; repeating it here
      // said the same thing twice, so this line carries only what that panel
      // cannot know: the gap the dots are centred on today
      const ref = M.when === 'today' ? 'expected' : 'NWS';
      legend.innerHTML = centred
        ? '<span>Typical gap today ' + sgn + gapBase.toFixed(1) + '° across ' + gapN + ' stations (' + w + ' minus ' + ref + '). '
          + 'Colour and size are each station’s distance from that, not from zero. Hover for the raw gap.</span>'
        : '<span>Too few stations priced to centre on a typical gap, so the dots show the raw gap against ' + ref + '.</span>';
    }
    else legend.innerHTML = '<span>Number is the ' + (M.when === 'today' ? 'expected' : 'NWS forecast') + ' level · pale shading is that level interpolated between stations (derived)</span>';
    drawWorld();
  }

  // Dots and labels for one canvas. Both maps carry the same encoding, so the
  // international stations read the same way they do on the city page's picker:
  // grey where the mode has no value for them, since US government feeds carry
  // observations everywhere but forecasts only for the US and, through NBM, Canada.
  const CANDS = [[9, 3], [9, -12], [-9, 3], [-9, -12], [9, 15], [9, -25], [-9, 15], [-9, -25], [0, 24], [0, -33], [18, 3], [-18, 3], [18, 15], [-18, 15], [18, -12], [-18, -12]];
  function plot(svg, cities, M, fx, fy, bounds, withUnit, opts) {
    opts = opts || {};
    const placed = [];
    const hit = b => b[0] < bounds[0] || b[1] < bounds[1] || b[2] > bounds[2] || b[3] > bounds[3]
      || placed.some(q => b[0] < q[2] && q[0] < b[2] && b[1] < q[3] && q[1] < b[3]);
    const rows = cities.slice().sort((a, b) => { const av = dev(M.div(a)), bv = dev(M.div(b)); return (bv == null ? -1 : Math.abs(bv)) - (av == null ? -1 : Math.abs(av)); });
    rows.forEach(c => {
      const v = dev(M.div(c)), av = M.val(c), X = fx(c), Y = fy(c);
      const g = el('g', { class: 'dot' });
      let r;
      if (v == null) {
        r = av == null ? 4.5 : 7;
        g.appendChild(el('circle', { cx: X, cy: Y, r, fill: av == null ? 'var(--line)' : 'var(--panel)', stroke: 'var(--ink)', 'stroke-width': 1.2 }));
      } else {
        // deviations from the typical gap are a degree or two, not five, so the
        // radius runs to full scale over a narrower range than the raw gap did
        const FULL = M.centred && gapN >= MIN_FOR_BASE ? 3 : 5;
        r = 5.5 + 8.5 * Math.min(Math.abs(v), FULL) / FULL;
        g.appendChild(el('circle', { cx: X, cy: Y, r: r + 3.5, fill: 'var(--panel)', 'fill-opacity': .95 }));
        g.appendChild(el('circle', { cx: X, cy: Y, r, fill: v > 0 ? 'var(--warm)' : (v < 0 ? 'var(--cool)' : 'var(--muted)'), 'fill-opacity': .97, stroke: 'var(--ink)', 'stroke-width': .6 }));
      }
      placed.push([X - r, Y - r, X + r, Y + r]);
      g.onmousemove = e => tip.show(e, dotTip(c));
      g.onmouseleave = () => tip.hide();
      g.onclick = () => { location.href = 'city.html?station=' + c.station; };
      svg.appendChild(g);
    });
    rows.forEach(c => {
      const v = dev(M.div(c)), av = M.val(c), X = fx(c), Y = fy(c);
      // a station with its own label is named whatever the view holds for it:
      // US government forecasts stop at the border, so abroad there is no
      // forecast value and the station would otherwise go unnamed
      if (!opts.label && av == null && v == null) return;
      const big = v != null && Math.abs(v) >= (M.centred && gapN >= MIN_FOR_BASE ? 1 : 1.5);
      // the international stations settle in Celsius, so their labels carry the unit
      // both canvases name their cities: a three-letter code is not something a
      // reader can be expected to decode, at home or abroad
      const s = opts.label
        ? opts.label(c)
        : c.city + (av != null ? ' ' + av.toFixed(0) + '°' + (withUnit ? (c.unit || '') : '') : '')
          + (big ? ' (' + (v > 0 ? '+' : '') + v.toFixed(0) + ')' : '');
      for (const [dx, dy] of CANDS) {
        const t = txt(s, { x: X + dx, y: Y + dy + 4, class: 'lbl', 'text-anchor': dx < 0 ? 'end' : (dx > 0 ? 'start' : 'middle'),
          'font-size': big ? 10.5 : 8.5, 'font-weight': 700, fill: big ? 'var(--navy)' : 'var(--ink)' });
        svg.appendChild(t);
        const b = t.getBBox(), bb = [b.x - 1, b.y - 1, b.x + b.width + 1, b.y + b.height + 1];
        if (!hit(bb)) { placed.push(bb); break; }
        t.remove();
      }
    });
  }

  // the international stations and Honolulu, on the world canvas below the national one
  function drawWorld() {
    const svg = $('#mapW'); if (!svg || !world) return;
    svg.innerHTML = '';
    svg.appendChild(el('rect', { x: 0, y: 0, width: 960, height: 480, fill: 'var(--map-sea)' }));
    svg.appendChild(el('path', { d: world.worldPaths, fill: 'var(--map-land)', stroke: 'var(--map-line)', 'stroke-width': .8 }));
    const M = MODES[mode];
    const side = (mode === 'loT' || mode === 'lo') ? 'impliedLow' : 'impliedHigh';
    /* Abroad the label carries the market's number and nothing else.

       There is no government forecast to compare against outside the United
       States and Canada, so the observed reading and the market's median sat
       side by side with nothing to relate them. A station with no listed board
       gets its name alone; a number that is not there is not written. The date
       travels with the number because these stations run on their own local
       days and the contract day is not the reader's. */
    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const dayLab = iso => (iso && /^\d{4}-\d{2}-\d{2}$/.test(iso))
      ? MON[+iso.slice(5, 7) - 1] + ' ' + (+iso.slice(8, 10)) : null;
    plot(svg, summary.cities.filter(c => !c.onConus), M, c => c.wx, c => c.wy, [2, 62, 958, 378], true,
         { label: c => {
             const u = c.unit || '';
             const im = WXM.on() ? (WXM.implied(c, M.when) || {})[side] : null;
             if (im == null) return c.city;
             const d = dayLab(c.markers && (M.when === 'today' ? c.markers.day : c.markers.tomorrow));
             return c.city + ' ' + im.toFixed(0) + '°' + u + (d ? ' · ' + d : '');
           } });
  }


  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.getAll(['summary.json', 'field.json']);
    const [bm, wd] = await Promise.all([
      fetch('assets/basemap.json').then(x => x.json()).catch(() => null),
      fetch('assets/world.json').then(x => x.json()).catch(() => null)]);
    summary = r['summary.json'].data; field = r['field.json'].data; base = bm; world = wd;
    await WXM.loadSummary();
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r['summary.json'], r['field.json']], 10));
    if (!summary || !base) { $('#map').innerHTML = ''; $('#map').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    const BTN = [['m1', 'hiT'], ['m2', 'hi'], ['m3', 'loT'], ['m4', 'lo']];
    if (!mode) mode = defaultMode();
    BTN.forEach(([id, m]) => {
      const b = $('#' + id); if (!b) return;
      b.classList.toggle('on', m === mode);
      b.onclick = () => { mode = m; BTN.forEach(([i]) => { const x = $('#' + i); if (x) x.classList.remove('on'); }); b.classList.add('on'); draw(); };
    });
    draw();
    // international stations and Honolulu are not on this canvas; list them
    // the international stations are labelled on the world canvas itself, so
    // there is no list under it any more
  }
  // the listing clock is exposed the way allocator.js exposes its maths, so
  // the gate can check a countdown against a time it chooses rather than
  // whatever the bundled sample happens to carry
  return { init, _listingTime: listingTime, _countdown: countdown };
})();
