/* The landing map: every station on one canvas, over the NWS's own forecast.

   Data in: summary.json (stations, observed so far, forecast levels, the city
   chart's level lines), ndfd/<view>.json (NDFD's daytime high or overnight
   low at every 4-pixel cell, pipeline/ndfd.py), assets/basemap.json (state
   outlines, pre-projected), market/summary.json through market.js. Four
   modes, each a day and a side.

   The reference the dots are read against differs by day. Tomorrow it is the
   NWS forecast. Today it is what the day is now expected to reach, which is
   what the station has already recorded taken with the forecast for the hours
   left: a forecast begins at the current hour, so a day whose low was set at
   dawn no longer has that low anywhere in it, and the forecast's own minimum
   is then the coolest hour still to come. The shading under them is NDFD as
   issued, today's from before the day began.

   With the market layer on, each dot carries the ForecastEx value inside it
   (the whole-degree central value the accuracy paper scores, from the
   exchange's ladder; else a labeled placeholder) and is colored and sized by
   that value's gap from the reference, measured from the typical gap across
   the map. */
window.WXMap = (() => {
  const { el, txt, h, $, deg } = WXC;
  /* The NWS's own temperature colors (owner, 2026-10-20), the scale its
     graphical forecasts draw (graphical.weather.gov), sampled every five
     degrees from that page's color bar. One fixed scale for every day and
     season, so a color is the same temperature on every map. */
  const NWS_SCALE = [[-10, '#c4b6c4'], [-5, '#dabbda'], [0, '#ffc8ff'], [5, '#ef8cef'], [10, '#d873dc'], [15, '#bf5ac6'],
    [20, '#923abb'], [25, '#631eaa'], [30, '#372398'], [35, '#195abe'], [40, '#06b9e1'], [45, '#00d2e4'], [50, '#01d577'],
    [55, '#0fc00d'], [60, '#6ccf03'], [65, '#e4e304'], [70, '#ffff00'], [75, '#ffc300'], [80, '#fa6e00'], [85, '#e93601'],
    [90, '#cf2202'], [95, '#b71102'], [100, '#a50e0c'], [105, '#d17482'], [110, '#f2d4dd']];
  const rgbOf = hx => [1, 3, 5].map(k => parseInt(hx.slice(k, k + 2), 16));
  function nwsRGB(f) {
    const S = NWS_SCALE;
    if (f <= S[0][0]) return rgbOf(S[0][1]);
    if (f >= S[S.length - 1][0]) return rgbOf(S[S.length - 1][1]);
    let i = 0; while (S[i + 1][0] <= f) i++;
    const t = (f - S[i][0]) / (S[i + 1][0] - S[i][0]), a = rgbOf(S[i][1]), b = rgbOf(S[i + 1][1]);
    return a.map((v, k) => Math.round(v + (b[k] - v) * t));
  }
  let summary = null, base = null, world = null, mode = null, tip = null;
  const FIELD = {};                        // view -> {meta, cells, href}, NDFD as the pipeline sampled it

  // the ForecastEx value for a station on the side and day the mode selects
  const fxOf = (c, when, low) => {
    const m = WXM.on() ? WXM.implied(c, when === 'today' ? 'today' : undefined) : null;
    return m ? (low ? m.impliedLow : m.impliedHigh) : null;
  };
  // The four views. `val` is the NWS level the gap is measured against, `fx`
  // the ForecastEx value the dot carries.
  const MODES = {
    hiT: { centered: true, low: false, when: 'today', val: c => c.nwsHighToday, fx: c => fxOf(c, 'today', false),
           div: c => WXM.on() ? (WXM.implied(c, 'today') || {}).divHigh : null },
    loT: { centered: true, low: true, when: 'today', val: c => c.nwsLowToday, fx: c => fxOf(c, 'today', true),
           div: c => WXM.on() ? (WXM.implied(c, 'today') || {}).divLow : null },
    hi:  { centered: true, low: false, when: 'tomorrow', val: c => c.nwsHighTomorrow, fx: c => fxOf(c, 'tomorrow', false),
           div: c => WXM.on() ? (WXM.implied(c) || {}).divHigh : null },
    lo:  { centered: true, low: true, when: 'tomorrow', val: c => c.nwsLowTomorrow, fx: c => fxOf(c, 'tomorrow', true),
           div: c => WXM.on() ? (WXM.implied(c) || {}).divLow : null },
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
  // far, so coloring by the raw sign paints the whole map one color and says
  // the same thing every morning. What a reader actually wants is which cities
  // disagree UNUSUALLY, so the dots are centered on the median gap across the
  // map for the view being shown, and color is the sign of the deviation from
  // it. The raw gap is never hidden: it is in the label's tooltip and named in
  // the caption.
  //
  // The median is taken across stations within the view rather than from a
  // per-station history, because it is available on the first day and needs no
  // accumulation. It answers "unusual relative to how the map is priced right
  // now", which is the question. Below five stations there is no useful median,
  // so the centering switches off and the caption says so.
  const MIN_FOR_BASE = 5;
  let gapBase = 0, gapN = 0;
  function median(v) {
    if (!v.length) return 0;
    const a = v.slice().sort((x, y) => x - y), i = a.length >> 1;
    return a.length % 2 ? a[i] : (a[i - 1] + a[i]) / 2;
  }
  function computeBase(M) {
    if (!M.centered) { gapBase = 0; gapN = 0; return; }
    const v = (summary.cities || []).map(c => M.div(c)).filter(x => x != null);
    gapN = v.length;
    gapBase = v.length >= MIN_FOR_BASE ? Math.round(median(v) * 10) / 10 : 0;
  }
  // what the color and size encode: the gap less the map's typical gap
  const dev = v => (v == null ? null : Math.round((v - gapBase) * 10) / 10);
  const tdy = () => { const c = summary.cities.find(x => x.onConus); return c && c.markers ? c.markers.day : ''; };

  // Which board is the one being traded. The day-ahead contracts list in the
  // late morning Eastern and the current day's settle that evening, so before
  // 5 pm ET the live board is today's and after it the day-ahead board is the
  // one worth opening on. The other is always one click away. The hour is
  // anchored on when the current day settles, not on when the next one lists,
  // so the exchange moving its listings earlier in August 2026 did not move it.
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
  const srcTag = s => (s === 'tgroup' ? 'tenths' : s === 'body' ? 'whole degrees' : null);

  // why a station has no ForecastEx value, in the reader's words
  const STATE_TEXT = { unavailable: 'quotes unavailable', unlisted: 'no market listed', day: 'quote summary is for another day',
                       'tomorrow-unlisted': 'tomorrow’s contracts not listed yet', 'no-bids': 'no bids yet' };
  function fxText(m, v, edge, unit) {
    if (!m) return 'not listed yet';
    // the number in the dot, with its unit where the station settles in Celsius
    if (v != null) return Math.round(v) + '°' + (unit === 'C' ? 'C' : '');
    if (edge === 'above') return 'above the top strike';
    if (edge === 'below') return 'below the bottom strike';
    if (edge === 'gap') return 'two or more unpriced strikes at the crossing';
    return STATE_TEXT[m.state] || 'no bids yet';
  }

  /* When a board lists. The snapshot stamps each day with the exchange's
     measured listing schedule (since the 8/28 target, about 10:30 am Eastern
     the day before for US cities); the day-ahead board therefore lists a day
     later than the marker the current day carries. The stamp is the top of a
     ten-minute sampling band, so the board can be open slightly before it. */
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

  /* One dot, read for the weather map on screen: the side and the day the
     buttons select, and nothing else.

     The exchange's number leads, the one written inside the dot, because that
     is the thing being traded; where the board has not listed yet its place is
     taken by when the contracts list. Under it, every model's forecast for the
     day as the station's city chart draws it with its level lines, named by
     the run (owner, 2026-10-20): today the run each issued before the day
     began, tomorrow its newest. The pipeline reads those numbers off the same
     rows by the chart's own rule (snapshots.chart_levels), so the two pages
     agree to the degree. */
  const MODEL_ROWS = [['nws', 'Weather Service'], ['nbm', 'Blend of Models'], ['lamp', 'LAMP'], ['mav', 'GFS MOS'], ['hrrr', 'HRRR']];
  function dotTip(c) {
    const mk = c.markers || {};
    const M = MODES[mode];
    const today = M.when === 'today', low = M.low;
    const side = low ? 'low' : 'high';
    const m = WXM.on() ? WXM.implied(c, today ? 'today' : undefined) : null;
    const tag = WXM.live() ? 'ForecastEx' : 'Placeholder';
    const dayIso = today ? mk.day : mk.tomorrow;

    // ---- the exchange first
    const lead = [];
    if (WXM.on()) {
      const fv = m && (low ? m.impliedLow : m.impliedHigh);
      lead.push(['<b>' + tag + ' ' + side + '</b>', '<b>' + fxText(m, fv, m && (low ? m.edgeLow : m.edgeHigh), c.unit) + '</b>']);
      if (fv == null) {
        const lt = listingTime(mk, M.when);
        if (lt) {
          const cd = countdown(lt);
          lead.push(['Contracts list by', WXC.clockFull(lt, c.tz) + ' ' + WXC.dateShort(lt, c.tz)]);
          if (cd) lead.push(['That is', cd]);
        }
      }
    }

    // ---- the models, as the city chart's level lines carry them
    const lv = (c.levels || {})[today ? 'today' : 'tomorrow'] || {};
    const ydayIso = today ? mk.yesterday : mk.day;
    const fmt = v => (v == null ? '—' : (c.unit === 'C' ? v.toFixed(1) : v.toFixed(0)) + '°');
    const support = MODEL_ROWS.filter(([k]) => lv[k]).map(([k, nm]) => {
      const r = lv[k], t = WXC.issuedTag(WXC.parseStamp(low ? r[3] : r[2]), c.tz, dayIso, ydayIso);
      return [nm + (t ? ' (' + t + ')' : ''), fmt(low ? r[1] : r[0])];
    });
    if (today) support.push(['Observed ' + side + ' so far', obsSoFar(c, low)], ['Latest METAR', latestOb(c, mk)]);

    const head = c.city + ' (' + c.station + ') · ' + (today ? 'today' : 'tomorrow') + '’s ' + side
               + (dayIso ? ' ' + isoDate(dayIso) : '');
    return tip.rows(head, lead)
      + tip.rows('<span class="tk" style="display:block;margin-top:5px">Forecasts</span>', support,
                 (today ? 'each model’s run issued before the day began' : 'each model’s newest run')
                 + ' · click → city chart');
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

  /* ---- the shading: NDFD as the NWS issued it (owner, 2026-10-20)

     The pipeline samples NDFD's own 2.5 km grid at the centre of every 4-pixel
     cell of this map (pipeline/ndfd.py). Each cell is painted in the NWS's
     colors on a 240 by 150 canvas, drawn at the map's size with the browser's
     smoothing so neighbouring cells blend as they do on the NWS's maps, and
     clipped to the states. A hover reads the cell's own number, never a
     blended one. Today's views hold the issuance from before midnight Eastern
     and tomorrow's the newest; a view whose day is not the map's day is left
     unshaded rather than shown for the wrong day. */
  const FIELD_NAME = { maxt: 'Daytime high', mint: 'Overnight low' };
  function loadField(view) {
    if (FIELD[view]) return Promise.resolve(FIELD[view]);
    return WXD.get('ndfd/' + view + '.json', 60).then(r => {
      const d = r && r.data;
      if (!d || !d.b64 || !d.grid) return null;
      const bin = atob(d.b64), n = bin.length, cells = new Uint8Array(n);
      for (let i = 0; i < n; i++) cells[i] = bin.charCodeAt(i);
      const { nx, ny } = d.grid;
      const cv = document.createElement('canvas'); cv.width = nx; cv.height = ny;
      const cx = cv.getContext('2d'), im = cx.createImageData(nx, ny);
      const lut = {};
      for (let i = 0; i < n; i++) {
        const b = cells[i]; if (!b) continue;
        const rgb = lut[b] || (lut[b] = nwsRGB(b - d.offset));
        im.data[4 * i] = rgb[0]; im.data[4 * i + 1] = rgb[1]; im.data[4 * i + 2] = rgb[2]; im.data[4 * i + 3] = 255;
      }
      cx.putImageData(im, 0, 0);
      const big = document.createElement('canvas'); big.width = nx * d.grid.step; big.height = ny * d.grid.step;
      const bx = big.getContext('2d'); bx.imageSmoothingEnabled = true; bx.imageSmoothingQuality = 'high';
      bx.drawImage(cv, 0, 0, big.width, big.height);
      return (FIELD[view] = { meta: d, cells, href: big.toDataURL('image/png') });
    }).catch(() => null);
  }
  const fieldDay = view => ((view === 'hiT' || view === 'loT') ? tdy() : tmw());
  function cellTip(F, x, y) {
    const d = F.meta, i = Math.floor(x / d.grid.step), j = Math.floor(y / d.grid.step);
    if (i < 0 || j < 0 || i >= d.grid.nx || j >= d.grid.ny) return '';
    const b = F.cells[j * d.grid.nx + i];
    if (!b) return '';
    return tip.rows('NWS forecast · ' + FIELD_NAME[d.kind].toLowerCase() + ' ' + isoDate(d.day),
      [['NDFD', deg(b - d.offset)], ['Issued', issuedText(d)]],
      'the National Digital Forecast Database, the NWS’s gridded forecast, at its 2.5 km point nearest here');
  }
  function issuedText(d) {
    if (!d.issued) return null;
    const t = Date.parse(d.issued), tz = 'America/New_York';
    return WXC.clockFull(t, tz) + ' ET ' + WXC.dateShort(t, tz)
      + (d.preDay === true ? ', before the day began' : (d.preDay === false ? ', the first issuance held for the day' : ''));
  }
  function paintField(svg, view) {
    loadField(view).then(F => {
      if (view !== mode || !svg.isConnected) return;
      legendField(F && F.meta.day === fieldDay(view) ? F : null);
      if (!F || F.meta.day !== fieldDay(view)) return;
      const img = el('image', { href: F.href, x: 0, y: 0, width: F.meta.grid.nx * F.meta.grid.step,
                                height: F.meta.grid.ny * F.meta.grid.step, preserveAspectRatio: 'none',
                                'clip-path': 'url(#us)', class: 'ndfd', 'data-tip-pin': '' });
      const at = e => { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
                        return pt.matrixTransform(svg.getScreenCTM().inverse()); };
      img.addEventListener('mousemove', e => { const q = at(e), t = cellTip(F, q.x, q.y); if (t) tip.show(e, t); else tip.hide(); });
      img.addEventListener('mouseleave', () => tip.hide());
      img.addEventListener('click', e => { const q = at(e), t = cellTip(F, q.x, q.y); if (t) tip.pin(e, t); });
      const land = svg.querySelector('path.land');
      if (land) land.after(img); else svg.insertBefore(img, svg.firstChild);
    });
  }

  function draw() {
    const svg = $('#map'); svg.innerHTML = '';
    const M = MODES[mode];
    const defs = el('defs'), cp = el('clipPath', { id: 'us' });
    cp.appendChild(el('path', { d: base.statePaths })); defs.appendChild(cp); svg.appendChild(defs);
    // the land underneath, which is what shows until the shading arrives and
    // wherever NDFD carries no value
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)', class: 'land' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2' }));
    const hEl = $('#mapTitle');
    if (hEl) { hEl.textContent = pageTitle(mode); document.title = pageTitle(mode); }

    computeBase(M);
    legendDots(M);
    paintField(svg, mode);
    plot(svg, WXC.tempCities(summary.cities).filter(c => c.onConus), M, c => c.px, c => c.py, [2, 2, 958, 598], false);
    drawWorld();
  }

  /* ---- the legend under the map (owner, 2026-10-20): what a dot's number,
     color and size mean, and the NWS color scale of the shading under them,
     instead of a number beside every dot. */
  const dotSvg = (r, fill, txt2) => '<svg class="lgdot" width="' + (2 * r + 8) + '" height="' + (2 * r + 8)
    + '" viewBox="' + (-r - 4) + ' ' + (-r - 4) + ' ' + (2 * r + 8) + ' ' + (2 * r + 8) + '" aria-hidden="true">'
    + '<circle r="' + r + '" fill="' + fill + '" stroke="var(--ink)" stroke-width=".6"/>'
    + (txt2 ? '<text y="3.4" text-anchor="middle" font-size="9.5" font-weight="700" fill="var(--dot-ink)">' + txt2 + '</text>' : '')
    + '</svg>';
  function legendDots(M) {
    const host = $('#legendDots'); if (!host) return;
    if (!WXM.on()) { host.innerHTML = ''; return; }
    const w = WXM.live() ? 'ForecastEx' : 'placeholder';
    const ref = M.when === 'today' ? 'the NWS level expected for the day' : 'the NWS forecast';
    const centered = M.centered && gapN >= MIN_FOR_BASE;
    const sgn = gapBase > 0 ? '+' : '';
    host.innerHTML = '<span class="lgi">' + dotSvg(10, 'var(--warm)', '79') + 'warmer</span>'
      + '<span class="lgi">' + dotSvg(10, 'var(--cool)', '61') + 'cooler</span>'
      + '<span class="lgi">' + dotSvg(10, 'var(--muted)', '70') + 'at the typical gap</span>'
      + '<span class="lgi">' + dotSvg(10, 'var(--warm)') + dotSvg(16, 'var(--warm)') + 'farther from typical</span>'
      + '<span class="lgi">' + dotSvg(4.5, 'var(--panel)') + 'no ' + w + ' value</span>'
      + '<span class="lgt">The number is the ' + w + ' value for the day in whole degrees. Color and size are that value less '
      + ref + (centered ? ', measured from the typical gap of ' + sgn + gapBase.toFixed(1) + '° across ' + gapN + ' stations.'
                        : '; too few stations are priced to center on a typical gap.') + '</span>';
  }
  function legendField(F) {
    const host = $('#legendField'); if (!host) return;
    const M = MODES[mode];
    const x = t => ((t + 10) / 120 * 100).toFixed(2) + '%';
    const stops = NWS_SCALE.map(([t, c]) => '<stop offset="' + x(t) + '" stop-color="' + c + '"/>').join('');
    const ticks = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map(t => '<span style="left:' + x(t) + '">' + t + '</span>').join('');
    const what = (M.low ? 'overnight low for ' : 'daytime high for ') + isoDate(fieldDay(mode));
    host.innerHTML = '<svg class="lgbar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">'
      + '<defs><linearGradient id="nwsbar">' + stops + '</linearGradient></defs>'
      + '<rect x="0" y="0" width="100" height="10" fill="url(#nwsbar)"/></svg>'
      + '<div class="lgticks">' + ticks + '</div>'
      + '<div class="lgt">' + (F
          ? 'Shading: the NWS forecast ' + what + ' (°F), National Digital Forecast Database, issued ' + issuedText(F.meta) + '.'
          : 'No NWS forecast shading for the ' + what + ' yet.') + '</div>';
  }

  // Dots and labels for one canvas. Both maps carry the same encoding, so the
  // international stations read the same way they do on the city page's picker.
  // The ForecastEx value sits inside the dot; a station without one is a small
  // empty circle, and the hover says why.
  function plot(svg, cities, M, fx, fy, bounds, withUnit, opts) {
    opts = opts || {};
    const k = opts.small ? 0.8 : 1;             // the world canvas is a smaller scale
    const placed = [];
    const hit = b => b[0] < bounds[0] || b[1] < bounds[1] || b[2] > bounds[2] || b[3] > bounds[3]
      || placed.some(q => b[0] < q[2] && q[0] < b[2] && b[1] < q[3] && q[1] < b[3]);
    const rows = cities.slice().sort((a, b) => { const av = dev(M.div(a)), bv = dev(M.div(b)); return (bv == null ? -1 : Math.abs(bv)) - (av == null ? -1 : Math.abs(av)); });
    const radius = {};
    rows.forEach(c => {
      const v = dev(M.div(c)), f = M.fx(c), X = fx(c), Y = fy(c);
      const g = el('g', { class: 'dot', 'data-station': c.station });
      let r;
      if (f == null) {
        r = 4.5 * k;
        g.appendChild(el('circle', { cx: X, cy: Y, r: r + 2, fill: 'var(--panel)', 'fill-opacity': .9 }));
        g.appendChild(el('circle', { cx: X, cy: Y, r, fill: 'var(--panel)', stroke: 'var(--ink)', 'stroke-width': 1.2 }));
      } else {
        // deviations from the typical gap are a degree or two, not five, so the
        // radius runs to full scale over a narrower range than the raw gap did;
        // the smallest still holds three digits
        const FULL = M.centered && gapN >= MIN_FOR_BASE ? 3 : 5;
        r = (v == null ? 10 : 10 + 6 * Math.min(Math.abs(v), FULL) / FULL) * k;
        const fill = v == null || v === 0 ? 'var(--muted)' : (v > 0 ? 'var(--warm)' : 'var(--cool)');
        g.appendChild(el('circle', { cx: X, cy: Y, r: r + 3, fill: 'var(--panel)', 'fill-opacity': .95 }));
        g.appendChild(el('circle', { cx: X, cy: Y, r, fill, 'fill-opacity': .97, stroke: 'var(--ink)', 'stroke-width': .6 }));
        g.appendChild(txt(String(Math.round(f)), { x: X, y: Y + 3.4 * k, class: 'dotv', 'text-anchor': 'middle',
          'font-size': 9.5 * k, 'font-weight': 700, fill: 'var(--dot-ink)', 'pointer-events': 'none' }));
      }
      radius[c.station] = r;
      placed.push([X - r - 3, Y - r - 3, X + r + 3, Y + r + 3]);
      g.onmousemove = e => tip.show(e, dotTip(c));
      g.onmouseleave = () => tip.hide();
      g.onclick = () => { location.href = WXC.cityHref(c); };
      svg.appendChild(g);
    });
    rows.forEach(c => {
      const v = dev(M.div(c)), X = fx(c), Y = fy(c), r = radius[c.station] + 3;
      const big = v != null && Math.abs(v) >= (M.centered && gapN >= MIN_FOR_BASE ? 1 : 1.5);
      // the name beside the dot: a three-letter code is not something a reader
      // can be expected to decode, at home or abroad
      const s = opts.label ? opts.label(c) : c.city;
      const cands = [[r + 2, 3.5], [-(r + 2), 3.5], [0, r + 10], [0, -(r + 3)], [r + 2, -r + 2], [-(r + 2), -r + 2],
                     [r + 2, r + 5], [-(r + 2), r + 5]];
      for (const [dx, dy] of cands) {
        // a halo in the page's own ground, so a name reads on every color of the shading
        const t = txt(s, { x: X + dx, y: Y + dy, class: 'lbl', 'text-anchor': dx < 0 ? 'end' : (dx > 0 ? 'start' : 'middle'),
          'font-size': (big ? 10.5 : 8.5) * k, 'font-weight': 700, fill: big ? 'var(--navy)' : 'var(--ink)',
          stroke: 'var(--panel)', 'stroke-width': 2.6, 'stroke-linejoin': 'round', 'paint-order': 'stroke' });
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
    /* Abroad the dot carries the market's number, in the station's own unit, and
       the label the station's name and the local date the contract day is,
       because these stations run on their own local days and the contract day
       is not the reader's. There is no government forecast to measure it
       against outside the United States and Canada, so the dot is neutral. */
    const dayLab = iso => (iso && /^\d{4}-\d{2}-\d{2}$/.test(iso))
      ? MON[+iso.slice(5, 7) - 1] + ' ' + (+iso.slice(8, 10)) : null;
    plot(svg, summary.cities.filter(c => !c.onConus), M, c => c.wx, c => c.wy, [2, 62, 958, 378], true,
         { small: true, label: c => {
             const d = dayLab(c.markers && (M.when === 'today' ? c.markers.day : c.markers.tomorrow));
             return M.fx(c) == null || !d ? c.city : c.city + ' · ' + d;
           } });
  }


  /* ---- the surface analysis loop under the map (owner, 2026-10-20)

     The Weather Prediction Center's analysis of fronts and pressure drawn over
     GOES-East and -West imagery, every three hours for the last 24, which
     pipeline/floater.py copies into the bucket from WPC's
     "Analysis/Satellite Composite (CONUS)". It replaced GOES-East's CONUS
     GeoColor loop here, so the systems behind the day's weather are on the
     picture. WPC draws it on a Mercator projection; the pipeline carries each
     image onto this map's projection at the scale that holds all of WPC's
     picture, so the states keep the shapes they have on the map above, at about
     two thirds of the size, and what lies past the borders stays in view. */
  let WPC = null, wpcStop = null, wpcSig = null;
  // an analysis every three hours, on WPC's server about two hours after its
  // time, so the newest is up to five hours old in the ordinary run of things
  const WPC_MAX_AGE = 8 * 36e5;
  function drawWpc() {
    const host = $('#conusLoop'); if (!host) return;
    if (wpcStop) { wpcStop(); wpcStop = null; }
    host.innerHTML = '';
    const frames = (WPC && WPC.frames) || [];
    host.hidden = !WXC.loopFresh(frames, WPC_MAX_AGE);
    if (host.hidden) return;
    const f0 = frames[frames.length - 1];
    const lp = WXC.satLoop({
      frames: frames.map(f => ({ t: f.t, src: WXD.base() + '/snapshots/' + f.key })), width: f0.w || 900, height: f0.h || 675,
      cls: 'wide wpc', attrs: { 'data-source': 'wpc' }, dated: true,
      sub: 'Fronts and pressure analyzed over satellite imagery, the last ' + frames.length + ' analyses, one every three hours',
      credit: 'Analysis and imagery from NOAA/NWS Weather Prediction Center, redrawn on this map’s projection',
      alt: 'Weather Prediction Center surface analysis over satellite imagery of the contiguous United States' });
    wpcStop = lp.stop;
    // the frame's own shape as the index gives it (960 by 583), whatever the image file says
    lp.card.querySelector('img.loopimg').style.aspectRatio = (f0.w || 960) + ' / ' + (f0.h || 583);
    host.appendChild(h('div', { class: 'pickttl', text: 'SURFACE ANALYSIS' }));
    host.appendChild(lp.card);
  }
  // read again every half hour so a page left open keeps up, and drawn again when the
  // newest analysis passes the age at which the loop is not shown
  const readWpc = () => WXD.get('wpcsfc/index.json', 30, { store: false }).then(x => {
    const d = (x && x.data) || null, fr = (d && d.frames) || [];
    const sig = JSON.stringify([fr.slice(-1)[0], WXC.loopFresh(fr, WPC_MAX_AGE)]);
    if (sig !== wpcSig) { wpcSig = sig; WPC = d; drawWpc(); }
  }).catch(() => {});

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.getAll(['summary.json']);
    const [bm, wd] = await Promise.all([
      fetch(WXC.asset('basemap.json')).then(x => x.json()).catch(() => null),
      fetch(WXC.asset('world.json')).then(x => x.json()).catch(() => null)]);
    summary = r['summary.json'].data; base = bm; world = wd;
    await WXM.loadSummary();
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r['summary.json']], 10));
    if (!summary || !base) { $('#map').innerHTML = ''; $('#map').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    const BTN = [['m1', 'hiT'], ['m2', 'hi'], ['m3', 'loT'], ['m4', 'lo']];
    if (!mode) mode = defaultMode();
    BTN.forEach(([id, m]) => {
      const b = $('#' + id); if (!b) return;
      b.classList.toggle('on', m === mode);
      b.onclick = () => { mode = m; BTN.forEach(([i]) => { const x = $('#' + i); if (x) x.classList.remove('on'); }); b.classList.add('on'); draw(); };
    });
    draw();
    readWpc();
    setInterval(readWpc, 30 * 60000);
  }
  // the listing clock is exposed the way allocator.js exposes its maths, so
  // the gate can check a countdown against a time it chooses rather than
  // whatever the bundled sample happens to carry
  return { init, _listingTime: listingTime, _countdown: countdown };
})();
