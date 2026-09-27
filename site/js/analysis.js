/* The analysis resolution page: a proposed settlement framework, shown as it
   would have resolved.

   Daily high, low, peak gust, mean wind and precipitation at the fifty most
   populous places of the lower 48, read from NOAA's hourly 2.5 km analyses
   (RTMA within the hour, URMA about seven hours later) instead of a station's
   reports, and resolved the way the exchange's daily contracts resolve: whole
   units rounded half up, away from zero, strictly above or below a strike. No
   contract settles on it, the page says so on every view, and it is not in
   the navigation.

   Everything drawn here is read from the files docs/analysis.md section 4
   describes. The page computes nothing but colours, the running sum of the
   hourly precipitation analyses (pinned to the file's own day total once the
   day is complete), and the rung labels of a ladder that does not exist. It
   never shows a price, and never says ask, sell or offer. */
window.WXAnalysis = (() => {
  const { el, txt, h, $ } = WXC;
  const STATEMENT = 'A proposed settlement framework. No contract settles on it.';
  // the site's nine-stop ramp, as varmap.js draws it, and a blue one for rain
  const RAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];
  const RAIN = ['#eef4fb', '#d6e6f5', '#bcd7ee', '#9fc5e5', '#7fb0da', '#5f98cb', '#437fb8', '#2b66a1', '#184d86'];
  /* The five daily variables. `hourly` is the frame and hourly-series variable
     each is built from; `lo` and `hi` pin the ramp so a frame from one day
     reads the same as a frame from another; `yes` is the side of the strike
     the value has to be on; `rungs` for precipitation are the fixed strikes of
     docs/analysis.md, the others take whole units around the value. `exactDec`
     is the precision the job keeps the exact aggregate at (CONVENTIONS.rounding:
     a tenth, the mean wind a hundredth, precipitation a ten-thousandth of an
     inch), so the card shows the file's figure and never a re-rounding of it
     that could equal the whole value it sits beside. */
  const VARS = {
    high:   { label: 'High', hourly: 'temp', unit: '°F', lo: -20, hi: 110, ramp: RAMP, dec: 0, exactDec: 1, yes: 'above', noun: 'high' },
    low:    { label: 'Low', hourly: 'temp', unit: '°F', lo: -20, hi: 110, ramp: RAMP, dec: 0, exactDec: 1, yes: 'below', noun: 'low' },
    gust:   { label: 'Peak gust', hourly: 'gust', unit: 'mph', lo: 0, hi: 60, ramp: RAMP, dec: 0, exactDec: 1, yes: 'above', noun: 'peak gust' },
    wind:   { label: 'Mean wind', hourly: 'wind', unit: 'mph', lo: 0, hi: 30, ramp: RAMP, dec: 0, exactDec: 2, yes: 'above', noun: 'mean wind' },
    precip: { label: 'Precipitation', hourly: 'precip', unit: 'in', lo: 0, hi: 2, ramp: RAIN, dec: 2, exactDec: 4, yes: 'above',
              noun: 'precipitation', zeroClear: true, rungs: [0.01, 0.05, 0.10, 0.25, 0.50, 1.00, 2.00], running: true },
  };
  const PRODUCTS = { rtma: 'RTMA', urma: 'URMA' };
  // the hourly variables as the captions name them; the keys are the files'
  const HOURLY = { temp: 'temperature', wind: 'sustained wind', gust: 'gust', precip: 'precipitation' };
  // the precision the loc files carry each hourly value at (docs/analysis.md
  // section 4): a tenth, and precipitation a ten-thousandth of an inch, so
  // that the running sum of the hours can meet the day's exact total
  const hourlyDec = hv => (hv === 'precip' ? 4 : 1);
  // the day's hour count is the file's `of` (23, 24 or 25, docs/analysis.md
  // section 4); 24 only for a file written before that field existed
  const hoursInDay = e => (e && e.of) || 24;
  const VB = { w: 960, h: 600 };

  // ---- the page's state, which is also the address bar's
  const S = { var: 'high', product: 'urma', day: null, hour: null, loc: null,
              index: null, grid: null, frames: new Map(), days: new Map(), locs: new Map() };
  const V = () => VARS[S.var];
  const fmt = (v, dec) => (v == null ? '' : Number(v).toFixed(dec == null ? V().dec : dec));
  const withUnit = (v, dec) => (v == null ? '' : fmt(v, dec) + (V().unit === 'in' ? ' in' : V().unit === 'mph' ? ' mph' : V().unit));
  const dayLabel = d => (WXC.weekdayOf(d) ? WXC.weekdayOf(d) + ' ' + d : d);
  const stampOf = (day, hh) => day.replace(/-/g, '') + 'T' + hh + 'Z';
  const validOf = (day, hh) => day + 'T' + hh + ':00:00Z';

  function readUrl() {
    const q = new URLSearchParams(location.search);
    if (VARS[q.get('var')]) S.var = q.get('var');
    if (PRODUCTS[q.get('product')]) S.product = q.get('product');
    if (q.get('day')) S.day = q.get('day');
    if (q.get('hour') != null && /^\d{1,2}$/.test(q.get('hour'))) S.hour = String(q.get('hour')).padStart(2, '0');
    if (q.get('loc')) S.loc = q.get('loc');
  }
  function writeUrl() {
    const q = new URLSearchParams();
    q.set('var', S.var); q.set('product', S.product);
    if (S.day) q.set('day', S.day);
    if (S.hour) q.set('hour', S.hour);
    if (S.loc) q.set('loc', S.loc);
    try { history.replaceState(null, '', '?' + q.toString()); } catch (e) { /* a file: URL, or history refused */ }
  }

  // ---- colour: a value on the variable's pinned ramp, interpolated between
  //      stops so a 320 by 200 field reads as a field rather than nine bands
  const hex = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
  function shade(v) {
    const Vv = V();
    if (v == null || (Vv.zeroClear && v <= 0)) return null;
    const f = Math.max(0, Math.min(1, (v - Vv.lo) / (Vv.hi - Vv.lo)));
    const p = f * (Vv.ramp.length - 1), i = Math.min(Vv.ramp.length - 2, Math.floor(p)), t = p - i;
    const a = hex(Vv.ramp[i]), b = hex(Vv.ramp[i + 1]);
    return [0, 1, 2].map(k => Math.round(a[k] + (b[k] - a[k]) * t));
  }
  const css = rgb => (rgb ? 'rgb(' + rgb.join(',') + ')' : 'var(--panel)');

  // ---- the files, each fetched once and kept
  const once = (map, key, cadence, opts) => {
    if (!map.has(key)) map.set(key, WXD.get('analysis/' + key, cadence, opts));
    return map.get(key);
  };
  const dayFile = day => once(S.days, 'days/' + day + '.json', 60);
  const locFile = (id, day) => once(S.locs, 'loc/' + id + '/' + day + '.json', 60);
  // a frame is 256 KB of ints keyed by its valid hour, cheap to refetch and
  // the same on every read, so it is never written to this browser's
  // storage: sixteen of them fill the quota and every later page's save fails
  const frameFile = (product, hv, day, hh) => once(S.frames, 'grid/' + product + '/' + hv + '/' + stampOf(day, hh) + '.json', 60, { store: false });
  const hoursListed = () => {
    const g = ((S.grid || {}).frames || {})[S.product] || {};
    return ((g[V().hourly] || {})[S.day] || []).slice().sort();
  };

  /* One location's day for the selected product and variable, with the
     status the page draws it in. A closed incomplete day is closed whichever
     product it is: the job closes an RTMA day the same way it closes a URMA
     one, and one file state has to get one word. Otherwise RTMA is always
     provisional. A URMA day is final once its analyses are read; precipitation
     resolves at the first complete pass and a later re-read that differs is
     `revised`. A day short of its hours carries its count, a closed day is
     never final. `word` is what the chart rule and the tooltips call the
     value: a value that can still move is `running`, one that cannot is
     `resolved`, and a closed day's value is `closed`. */
  function resolution(entry, product, varKey) {
    const e = entry && entry[product];
    if (!e || !e[varKey]) return null;
    const val = e[varKey], n = e.hours || 0, total = hoursInDay(e);
    let kind;
    if (e.closed) kind = 'closed';
    else if (product === 'rtma') kind = 'provisional';
    else if (varKey === 'precip') kind = val.revised ? 'revised' : val.resolved ? 'final' : 'provisional';
    else kind = e.final ? 'final' : 'provisional';
    const count = n + ' of ' + total + ' hours';
    const provisional = kind === 'provisional' || kind === 'closed';
    const label = kind === 'closed' ? 'closed, ' + count : (kind === 'provisional' && n < total) ? kind + ', ' + count : kind;
    return { value: val.value, exact: val.exact, at: val.at || null, revised: val.revised || null,
             kind, label, hours: n, total, provisional,
             word: kind === 'closed' ? 'closed' : provisional ? 'running' : 'resolved',
             status: provisional ? kind + ', ' + count : kind };
  }

  async function init() {
    WXC.chrome('analysis-resolution.html');
    readUrl();
    const host = $('#anaMap'), strip = $('#pageStatus');
    const ri = await WXD.get('analysis/index.json', 10);
    /* The index's `asof` is the newest analysis valid hour, which NOAA's own
       lag keeps 47 to 107 minutes behind the clock, so judged against the ten
       minute job cadence it would read as behind on every load. The job's
       own time is `written`, and that is what the pill reports, with the age
       and the stale test recomputed the way data.js computes them. The valid
       hours stay visible beside it. */
    const job = Object.assign({}, ri);
    if (ri.data && ri.data.written && !isNaN(Date.parse(ri.data.written))) {
      job.asof = Date.parse(ri.data.written);
      job.ageMin = (Date.now() - job.asof) / 6e4;
      job.stale = job.ageMin > 2 * 10;
    }
    if (strip) { strip.textContent = ''; strip.appendChild(WXC.statusEl([job], 10)); }
    const base = await fetch(WXC.asset('basemap.json')).then(x => x.json()).catch(() => null);
    S.index = ri.data;
    if (!S.index || !base) {
      host.appendChild(h('p', { class: 'cap' }, 'The analysis index is not available right now. ' + STATEMENT));
      return;
    }
    const rg = await WXD.get('analysis/grid/index.json', 10);
    S.grid = rg.data || { frames: {} };
    const src = S.index.sources || {};
    const validAt = s => (s && s.latest ? String(s.latest).replace('T', ' ').replace(/:00Z$/, ' UTC') : 'none read');
    const bar0 = $('#anaBar');
    if (bar0) bar0.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'anaNewest',
      text: 'newest analysis RTMA ' + validAt(src.rtma) + ', URMA ' + validAt(src.urma) }));
    const days = (S.index.days || []).slice();
    if (!days.length) {
      host.appendChild(h('p', { class: 'cap' }, 'No days have been read yet. ' + STATEMENT));
      return;
    }
    if (days.indexOf(S.day) < 0) S.day = days[days.length - 1];
    const tip = WXC.tooltip();
    const foot = $('#foot');
    if (foot) foot.textContent = 'Analyses are NOAA’s Real-Time and Unrestricted Mesoscale Analyses, read from NOAA Open Data on AWS. '
      + 'Times are analysis valid times, never fetch times. ' + STATEMENT;

    // ---- controls: variable, product, day
    const bar = $('#anaBar');
    const varBtns = h('span', { class: 'anagrp' });
    Object.keys(VARS).forEach(k => {
      const b = h('button', { text: VARS[k].label, 'data-var': k });
      b.onclick = () => { if (S.var !== k) { S.var = k; changed('var'); } };
      varBtns.appendChild(b);
    });
    const prodBtns = h('span', { class: 'anagrp' });
    Object.keys(PRODUCTS).forEach(k => {
      const b = h('button', { text: PRODUCTS[k], 'data-product': k });
      b.onclick = () => { if (S.product !== k) { S.product = k; changed('product'); } };
      prodBtns.appendChild(b);
    });
    const daySel = h('select', { id: 'anaDay', title: 'day' });
    days.slice().reverse().forEach(d => daySel.appendChild(h('option', { value: d, text: dayLabel(d) })));
    daySel.onchange = () => { S.day = daySel.value; changed('day'); };
    // a place select beside the day: the reliable way to a panel where the
    // dots overlap or on a phone, and the keyboard's way in
    const locSel = h('select', { id: 'anaLoc', title: 'place' });
    locSel.appendChild(h('option', { value: '', text: 'Place' }));
    (S.index.locations || []).slice().sort((a, b) => a.name.localeCompare(b.name) || a.state.localeCompare(b.state))
      .forEach(L => locSel.appendChild(h('option', { value: L.id, text: L.name + ', ' + L.state })));
    locSel.onchange = () => {
      S.loc = locSel.value || null;
      writeUrl();
      if (S.loc) openLoc(true); else closeLoc();
    };
    bar.insertBefore(varBtns, strip);
    bar.insertBefore(prodBtns, strip);
    bar.insertBefore(daySel, strip);
    bar.insertBefore(locSel, strip);

    // ---- the map: land, the frame under a clip of the states, outlines, dots
    const svg = el('svg', { viewBox: '0 0 960 600', id: 'vmap' });
    const defs = el('defs');
    const clip = el('clipPath', { id: 'anaClip' });
    clip.appendChild(el('path', { d: base.statePaths }));
    defs.appendChild(clip);
    svg.appendChild(defs);
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    /* The frame is a 320 by 200 picture stretched over the 960 by 600 view,
       so each lattice point is a 3 px square. Pixelated rendering keeps the
       squares square; smoothing would invent values between points that the
       lattice never sampled. */
    const img = el('image', { id: 'anaFrame', x: 0, y: 0, width: 960, height: 600,
                              preserveAspectRatio: 'none', 'clip-path': 'url(#anaClip)',
                              style: 'image-rendering:pixelated;image-rendering:crisp-edges' });
    svg.appendChild(img);
    svg.appendChild(el('path', { d: base.statePaths, class: 'state', 'vector-effect': 'non-scaling-stroke' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2', 'vector-effect': 'non-scaling-stroke' }));
    const dots = el('g', { id: 'vdots' });
    svg.appendChild(dots);
    const canvas = document.createElement('canvas');
    canvas.width = (S.index.lattice || {}).cols || 320;
    canvas.height = (S.index.lattice || {}).rows || 200;
    const pitch = (S.index.lattice || {}).pitch || 3;
    let frame = null;          // the frame on screen, for the hover
    let dayEntries = null;     // the day file's locations, for the dots
    let dayRes = null;         // the day file's fetch result, so a file that could not be read is not a day with no hours

    function drawFrame(f) {
      frame = f;
      if (!f || !f.values) { img.removeAttribute('href'); return; }
      const ctx = canvas.getContext('2d');
      const im = ctx.createImageData(canvas.width, canvas.height);
      const d = im.data, sc = f.scale || 1, n = Math.min(f.values.length, canvas.width * canvas.height);
      for (let k = 0; k < n; k++) {
        const v = f.values[k];
        const c = v == null ? null : shade(v / sc);
        if (!c) continue;
        d[k * 4] = c[0]; d[k * 4 + 1] = c[1]; d[k * 4 + 2] = c[2]; d[k * 4 + 3] = 255;
      }
      ctx.putImageData(im, 0, 0);
      img.setAttribute('href', canvas.toDataURL('image/png'));
    }

    // ---- zoom and pan, lifted from varmap.js: the viewBox moves, the
    //      geometry stays, and the dots are redrawn a beat later at a size
    //      that holds on screen
    const VIEW0 = { x: 0, y: 0, w: VB.w, h: VB.h };
    let view = Object.assign({}, VIEW0);
    const MAXZ = 14;
    const GS = () => view.w / VIEW0.w;
    let rescale = null;
    const scheduleRescale = () => { clearTimeout(rescale); rescale = setTimeout(drawDots, 140); };
    function applyView() {
      svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].map(n => Math.round(n * 100) / 100).join(' '));
      const z = VIEW0.w / view.w;
      const lbl = $('#anaZoomLevel');
      if (lbl) lbl.textContent = z < 1.02 ? 'whole country' : Math.round(z * 10) / 10 + '×';
      svg.classList.toggle('grab', z > 1.02);
    }
    function clampView() {
      view.x = Math.min(Math.max(view.x, VIEW0.x), VIEW0.x + VIEW0.w - view.w);
      view.y = Math.min(Math.max(view.y, VIEW0.y), VIEW0.y + VIEW0.h - view.h);
    }
    function zoomAbout(factor, cx, cy) {
      const z = VIEW0.w / view.w;
      const want = Math.min(Math.max(z * factor, 1), MAXZ);
      if (Math.abs(want - z) < 1e-6) return;
      const w = VIEW0.w / want, hh = VIEW0.h / want;
      view.x = cx - (cx - view.x) * (w / view.w);
      view.y = cy - (cy - view.y) * (hh / view.h);
      view.w = w; view.h = hh;
      clampView(); applyView(); scheduleRescale();
    }
    const resetView = () => { view = Object.assign({}, VIEW0); applyView(); scheduleRescale(); };
    function atPoint(ev) {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      return [q.x, q.y];
    }
    svg.addEventListener('wheel', ev => {
      ev.preventDefault();
      const [cx, cy] = atPoint(ev);
      zoomAbout(ev.deltaY < 0 ? 1.18 : 1 / 1.18, cx, cy);
    }, { passive: false });
    let drag = null, moved = 0;
    svg.addEventListener('pointerdown', ev => {
      if (VIEW0.w / view.w <= 1.02) return;            // nothing to pan at full extent
      drag = { sx: ev.clientX, sy: ev.clientY, vx: view.x, vy: view.y, id: ev.pointerId };
      moved = 0; svg.setPointerCapture(ev.pointerId); svg.classList.add('grabbing');
    });
    svg.addEventListener('pointermove', ev => {
      if (!drag) return;
      const k = view.w / svg.getBoundingClientRect().width;
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
      view.x = drag.vx - dx * k; view.y = drag.vy - dy * k;
      clampView(); applyView();
    });
    const endDrag = () => {
      if (!drag) return;
      try { svg.releasePointerCapture(drag.id); } catch (e) { /* already released */ }
      drag = null; svg.classList.remove('grabbing');
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    // a pan that ends over a dot must not also open that location
    svg.addEventListener('click', ev => {
      if (moved > 4) { ev.stopPropagation(); ev.preventDefault(); moved = 0; }
    }, true);

    /* Hover reads the frame under the pointer. The pointer's viewBox
       coordinates divided by the lattice pitch name the lattice column and
       row, and the frame is row-major from the top left, so the value is one
       lookup. Over a dot, the dot's day value is added. */
    svg.addEventListener('mousemove', ev => {
      const [x, y] = atPoint(ev);
      const cols = canvas.width, rows = canvas.height;
      const c = Math.floor(x / pitch), r = Math.floor(y / pitch);
      const pairs = [];
      if (frame && frame.values && c >= 0 && r >= 0 && c < cols && r < rows) {
        const v = frame.values[r * cols + c];
        pairs.push(['Cell under the pointer', v == null ? 'off the grid' : withUnit(v / (frame.scale || 1), V().hourly === 'precip' ? 2 : 1)]);
      } else if (!frame) {
        pairs.push(['Cell under the pointer', 'no frame for this hour']);
      }
      const g = ev.target && ev.target.closest && ev.target.closest('g.dot');
      let foot = '';
      if (g) {
        const L = locOf(g.dataset.loc);
        const res = resolution((dayEntries || {})[g.dataset.loc], S.product, S.var);
        pairs.push([L ? L.name + ', ' + L.state : g.dataset.loc,
                    res && res.value != null ? res.word + ' ' + withUnit(res.value) + ' (' + PRODUCTS[S.product] + ', ' + res.status + ')'
                    : dayRes && dayRes.source === 'none' ? 'the day file could not be read' : 'no value for this day yet']);
        foot = 'click to open the location';
      }
      tip.show(ev, tip.rows(V().label + ' · ' + PRODUCTS[S.product] + (S.hour ? ' ' + S.hour + ':00 UTC' : ''), pairs, foot));
    });
    svg.addEventListener('mouseleave', () => tip.hide());

    const locOf = id => (S.index.locations || []).find(L => L.id === id) || null;

    /* The dots: one per place, coloured by the day's resolved value of the
       selected variable and product, sized with it as the other maps size
       theirs. Dashed and faded when provisional, because a value that can
       still move must not read as one that cannot; hollow when the day has
       no value yet, because a missing value is a fact about the day and an
       absent dot reads as an absent place. */
    function drawDots() {
      const g0 = GS();
      dots.textContent = '';
      // smallest first, so where two places sit in one metro (Phoenix and
      // Mesa, Dallas and Arlington) the larger one is drawn on top and is
      // the one a click lands on; the list arrives in population order
      (S.index.locations || []).slice().reverse().forEach(L => {
        const res = resolution((dayEntries || {})[L.id], S.product, S.var);
        const v = res ? res.value : null;
        const X = L.px, Y = L.py;
        const g = el('g', { class: 'dot', 'data-loc': L.id });
        const frac = v == null ? 0 : Math.min(1, Math.max(0, (v - V().lo) / (V().hi - V().lo)));
        const r = (v == null ? 4.5 : 6 + 5 * frac) * g0;
        g.appendChild(el('circle', { cx: X, cy: Y, r: r + 2.5 * g0, fill: 'var(--panel)', 'fill-opacity': .95 }));
        const attrs = { cx: X, cy: Y, r, fill: v == null ? 'var(--panel)' : css(shade(v) || hex(V().ramp[0])),
                        stroke: 'var(--ink)', 'stroke-width': .7 * g0, class: v == null ? 'absent' : (res.provisional ? 'prov' : 'final') };
        if (v != null && res.provisional) Object.assign(attrs, { 'stroke-dasharray': (2.2 * g0).toFixed(2) + ' ' + (1.8 * g0).toFixed(2),
                                                                'stroke-width': 1.1 * g0, 'fill-opacity': .55 });
        g.appendChild(el('circle', attrs));
        // the size rides in `style`: the class carries a font-size in the
        // stylesheet, and a rule beats a presentation attribute (varmap.js)
        if (v != null) g.appendChild(txt(fmt(v), { x: X, y: Y + 3.2 * g0, class: 'ax', 'text-anchor': 'middle',
                                                  style: 'font-size:' + (9.5 * g0).toFixed(2) + 'px', 'pointer-events': 'none' }));
        if (g0 < 0.42) g.appendChild(txt(L.name, { x: X, y: Y - r - 3 * g0, class: 'ax', 'text-anchor': 'middle',
                                                   style: 'font-size:' + (9.5 * g0).toFixed(2) + 'px', 'pointer-events': 'none' }));
        // a button to the keyboard and the reader, named on hover: at the
        // national extent the Dallas, Fort Worth and Arlington dots overlap
        // as the Northeast ones do, and the name is what tells them apart
        const title = el('title'); title.textContent = L.name + ', ' + L.state;
        g.insertBefore(title, g.firstChild);
        g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button');
        g.setAttribute('aria-label', L.name + ', ' + L.state + (v != null ? ', ' + res.word + ' ' + withUnit(v) : ', no value yet'));
        const open = () => { S.loc = L.id; writeUrl(); openLoc(true); };
        g.onclick = open;
        g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
        dots.appendChild(g);
      });
      raiseDot();
    }
    // the picked place is drawn last, so where dots overlap it is the one on top
    function raiseDot() {
      const g = S.loc && dots.querySelector('g.dot[data-loc="' + S.loc + '"]');
      if (g) dots.appendChild(g);
    }

    const zoomBar = h('div', { class: 'bar', id: 'anaZoom' });
    const mk = (label, title, fn) => { const b = h('button', { text: label, title }); b.onclick = fn; zoomBar.appendChild(b); };
    mk('−', 'zoom out', () => zoomAbout(1 / 1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('+', 'zoom in', () => zoomAbout(1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('Reset', 'back to the whole country', resetView);
    zoomBar.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'anaZoomLevel', text: 'whole country' }));
    zoomBar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: '· scroll to zoom, drag to pan, hover for the cell, click a dot or pick a place for its panel' }));
    host.appendChild(zoomBar);
    host.appendChild(svg);

    // ---- the hour stepper under the map, and the caption it moves
    const step = h('div', { class: 'bar anastep', id: 'anaStep' });
    const prevB = h('button', { text: '◀', title: 'previous hour' });
    const nextB = h('button', { text: '▶', title: 'next hour' });
    const playB = h('button', { text: 'Play', id: 'anaPlay', title: 'step through the hours' });
    const cap = h('span', { class: 'cap', style: 'margin:0', id: 'anaCap' });
    let timer = null;
    prevB.onclick = () => { stop(); stepHour(-1); };
    nextB.onclick = () => { stop(); stepHour(1); };
    function stop() { if (timer) { clearInterval(timer); timer = null; playB.textContent = 'Play'; playB.classList.remove('on'); } }
    playB.onclick = () => {
      if (timer) return stop();
      if (hoursListed().length < 2) return;
      playB.textContent = 'Pause'; playB.classList.add('on');
      timer = setInterval(() => stepHour(1, true), 600);
    };
    function stepHour(dir, wrap) {
      const hs = hoursListed();
      if (!hs.length) return;
      let i = hs.indexOf(S.hour);
      if (i < 0) i = dir > 0 ? -1 : hs.length;
      i += dir;
      if (i < 0 || i >= hs.length) { if (!wrap) return; i = (i + hs.length) % hs.length; }
      S.hour = hs[i];
      writeUrl();
      showFrame();
    }
    step.appendChild(prevB); step.appendChild(nextB); step.appendChild(playB); step.appendChild(cap);
    // the arrow keys step too while any of the three buttons has focus
    step.addEventListener('keydown', ev => {
      if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
      ev.preventDefault(); stop(); stepHour(ev.key === 'ArrowLeft' ? -1 : 1);
    });
    host.appendChild(step);
    const legend = h('div', { class: 'key', id: 'anaLegend' });
    host.appendChild(legend);

    function setCaption() {
      const hs = hoursListed();
      let t = dayLabel(S.day);
      if (S.hour && hs.indexOf(S.hour) >= 0) {
        t += ', ' + S.hour + ':00 UTC';
        const L = S.loc ? locOf(S.loc) : null;
        if (L) t += ' (' + WXC.clockFull(Date.parse(validOf(S.day, S.hour)), L.tz) + ' in ' + L.name + ')';
        if (!frame) t += ' · the frame for this hour could not be read';
      } else {
        t += ' · no ' + PRODUCTS[S.product] + ' ' + HOURLY[V().hourly] + ' frames listed for this UTC day';
      }
      t += ' · ' + hs.length + (hs.length === 1 ? ' hour' : ' hours') + ' on file';
      cap.textContent = t;
      // one hour is nothing to step through
      prevB.disabled = nextB.disabled = playB.disabled = hs.length < 2;
    }
    function setLegend() {
      legend.textContent = '';
      const Vv = V();
      const sw = h('span', { class: 'anaramp' });
      Vv.ramp.forEach(c => sw.appendChild(h('i', { style: 'background:' + c })));
      legend.appendChild(sw);
      const lo = (Vv.lo < 0 ? '−' + Math.abs(Vv.lo) : Vv.lo), hi = Vv.hi;
      legend.appendChild(h('span', { text: Vv.label + ' from the ' + PRODUCTS[S.product] + ' analysis, ' + lo + ' to ' + hi + ' '
        + Vv.unit + (Vv.zeroClear ? ', zero left clear' : '') + '.' }));
      legend.appendChild(h('span', { text: 'Dots carry the day’s resolved ' + Vv.noun + ' per place, dashed while provisional, hollow with no value yet.' }));
      legend.appendChild(h('span', { class: 'kn', text: 'The field is a 3 px subsample of the 2.5 km analysis, about 14 km between points nationally. ' + STATEMENT }));
    }

    let showing = 0;
    async function showFrame() {
      const mine = ++showing;
      const hs = hoursListed();
      // an hour the day does not list is replaced by its newest, and a day
      // with no frames has no hour at all, so the address never names one
      // the view does not show
      if (S.hour == null || hs.indexOf(S.hour) < 0) S.hour = hs.length ? hs[hs.length - 1] : null;
      writeUrl();
      if (!S.hour || hs.indexOf(S.hour) < 0) { drawFrame(null); setCaption(); return; }
      const r = await frameFile(S.product, V().hourly, S.day, S.hour);
      if (mine !== showing) return;          // a later step won the race
      drawFrame(r.data && r.data.values ? r.data : null);
      setCaption();
    }
    async function showDay() {
      const r = await dayFile(S.day);
      dayRes = r;
      dayEntries = (r.data && r.data.locations) || {};
      drawDots();
    }
    function pressed() {
      Array.from(varBtns.children).forEach(b => b.classList.toggle('on', b.dataset.var === S.var));
      Array.from(prodBtns.children).forEach(b => b.classList.toggle('on', b.dataset.product === S.product));
      daySel.value = S.day;
      locSel.value = S.loc || '';
    }

    /* One handler for every control, so the map, the caption, the legend,
       the dots and the panel move together and the address bar follows. */
    async function changed(what) {
      stop();
      pressed();
      setLegend();
      if (what === 'day' || what === 'init') await showDay();
      else drawDots();
      writeUrl();
      showFrame();
      if (S.loc) openLoc(false);
    }

    // ---- the location panel
    const panel = $('#locPanel');
    function closeLoc() { S.loc = null; panel.hidden = true; locSel.value = ''; writeUrl(); setCaption(); }
    async function openLoc(scroll) {
      const L = locOf(S.loc);
      if (!L) { S.loc = null; panel.hidden = true; locSel.value = ''; writeUrl(); return; }
      panel.hidden = false;
      locSel.value = L.id;
      raiseDot();
      const rl = await locFile(L.id, S.day);
      if (locOf(S.loc) !== L) return;
      renderPanel(L, rl.data, (dayEntries || {})[L.id] || (rl.data && rl.data.summary) || null, rl);
      setCaption();
      if (scroll) { try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* landed anyway */ } }
    }
    function renderPanel(L, doc, entry, locRes) {
      // neither file reached the page: the day is not one with no hours,
      // it is one the page could not read, and the text says which
      const unread = !!(dayRes && dayRes.source === 'none' && locRes && locRes.source === 'none');
      panel.textContent = '';
      const i = days.indexOf(S.day);
      const head = h('div', { class: 'anahead' });
      head.appendChild(h('h2', { text: L.name + ', ' + L.state }));
      head.appendChild(h('span', { class: 'cap', style: 'margin:0', text: dayLabel(S.day) + ' · ' + L.tz }));
      const nav = h('span', { class: 'ananav' });
      const prev = h('button', { text: '◀ ' + (i > 0 ? days[i - 1] : 'earlier'), id: 'anaPrevDay' });
      const next = h('button', { text: (i < days.length - 1 ? days[i + 1] : 'later') + ' ▶', id: 'anaNextDay' });
      prev.disabled = i <= 0; next.disabled = i >= days.length - 1;
      prev.onclick = () => { if (i > 0) { S.day = days[i - 1]; changed('day'); } };
      next.onclick = () => { if (i < days.length - 1) { S.day = days[i + 1]; changed('day'); } };
      const close = h('button', { text: 'Close', title: 'close the panel' });
      close.onclick = closeLoc;
      nav.appendChild(prev); nav.appendChild(next); nav.appendChild(close);
      head.appendChild(nav);
      panel.appendChild(head);
      panel.appendChild(h('p', { class: 'cap anastmt', text: STATEMENT + ' The values below are what the framework would have resolved for '
        + L.name + ' on ' + S.day + ' from the ' + PRODUCTS[S.product] + ' analysis.' }));

      // the resolution row: five cards, one per daily variable
      const cards = h('div', { class: 'anacards' });
      Object.keys(VARS).forEach(k => {
        const Vk = VARS[k];
        const res = resolution(entry, S.product, k);
        const other = resolution(entry, S.product === 'rtma' ? 'urma' : 'rtma', k);
        const unit = Vk.unit === 'in' ? ' in' : Vk.unit === 'mph' ? ' mph' : Vk.unit;
        const card = h('div', { class: 'anacard' + (k === S.var ? ' on' : ''), 'data-var': k });
        card.appendChild(h('div', { class: 'al', text: Vk.label }));
        if (!res || res.value == null) {
          card.appendChild(h('div', { class: 'av dim', text: 'no value' }));
          card.appendChild(h('div', { class: 'ae', text: unread ? 'the day file could not be read' : 'no ' + PRODUCTS[S.product] + ' hours read yet' }));
        } else {
          card.appendChild(h('div', { class: 'av', text: Number(res.value).toFixed(Vk.dec) + unit }));
          card.appendChild(h('div', { class: 'ae', text: 'exact ' + Number(res.exact).toFixed(Vk.exactDec) + unit }));
          card.appendChild(h('span', { class: 'pill anapill ' + (res.provisional ? 'off' : res.kind === 'revised' ? 'bad' : 'ok'), text: res.label }));
          if (res.at) card.appendChild(h('div', { class: 'ae', text: 'at ' + WXC.clockFull(Date.parse(res.at), L.tz) + ' local' }));
          if (res.revised) card.appendChild(h('div', { class: 'ae', text: 'revised ' + Number(res.revised.value).toFixed(Vk.dec) + unit
            + ' at ' + WXC.clockFull(Date.parse(res.revised.at), L.tz) + ' ' + WXC.dateShort(Date.parse(res.revised.at), L.tz) }));
        }
        if (other && other.value != null) card.appendChild(h('div', { class: 'ae', text: PRODUCTS[S.product === 'rtma' ? 'urma' : 'rtma'] + ' ' + Number(other.value).toFixed(Vk.dec) + unit }));
        card.onclick = () => { if (S.var !== k) { S.var = k; changed('var'); } };
        cards.appendChild(card);
      });
      panel.appendChild(cards);

      const hours = (doc && doc.hours) || [];
      if (!hours.length) {
        panel.appendChild(h('p', { class: 'cap', text: locRes && locRes.source === 'none'
          ? 'The hourly file for ' + L.name + ' on ' + S.day + ' could not be read. The hourly chart and table appear once it can be.'
          : 'No hours read yet for ' + L.name + ' on ' + S.day + '. The hourly chart and table appear once the first analysis of the day is read.' }));
      } else {
        panel.appendChild(h('div', { class: 'secttl', text: V().label.toUpperCase() + ' THROUGH THE DAY, ' + (V().running ? 'RUNNING SUM OF THE ' : '') + 'HOURLY ' + HOURLY[V().hourly].toUpperCase() }));
        plot(panel, L, hours, resolution(entry, S.product, S.var), entry);
        panel.appendChild(h('div', { class: 'secttl', text: 'EVERY HOUR, BOTH ANALYSES' }));
        panel.appendChild(hourTable(hours, L));
      }
      const c = L.cell || {};
      let note = 'Cell centre ' + ((c.centre || [])[0] != null ? c.centre[0].toFixed(4) + ' N, ' + Math.abs(c.centre[1]).toFixed(4) + (c.centre[1] < 0 ? ' W' : ' E') : 'unknown')
        + (c.distanceKm != null ? ', ' + c.distanceKm + ' km from the Census internal point at ' + L.lat.toFixed(4) + ' N, ' + Math.abs(L.lon).toFixed(4) + (L.lon < 0 ? ' W' : ' E') : '')
        + (c.wexp ? ', grid cell i ' + c.wexp[0] + ' j ' + c.wexp[1] : '') + '.';
      if (L.note) note += ' ' + L.note;
      panel.appendChild(h('p', { class: 'cap', id: 'anaCell', text: note }));
    }

    /* The hourly chart, in the language of WXK.plot: local hours 0 to 24
       along the foot, midnight and the day end marked, RTMA dashed and URMA
       solid in their own tokens, the resolved value as a rule across the
       plot, and a column of hypothetical rungs at the right. The rungs are
       hatched, say Yes or No and nothing else, and link to nothing, because
       there is no book behind them. */
    function plot(host, L, hours, res, entry) {
      const Vv = V(), hv = Vv.hourly;
      const NS = 'http://www.w3.org/2000/svg';
      const E = (n, a) => { const e = document.createElementNS(NS, n); for (const k in (a || {})) if (a[k] != null) e.setAttribute(k, a[k]); return e; };
      const T = (s, a) => { const e = E('text', a); e.textContent = s; return e; };
      const W = 960, H = 300, CW = 78, GAP = 26, Lm = 42, R = 16 + CW + GAP, TOP = 18, BOT = H - 30, PX = W - R;
      const svg2 = E('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts' });
      const defs = E('defs');
      const pat = E('pattern', { id: 'wxHatch', width: 5, height: 5, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(135)' });
      pat.appendChild(E('rect', { x: 0, y: 0, width: 5, height: 5, fill: 'transparent' }));
      pat.appendChild(E('rect', { x: 0, y: 0, width: 2, height: 5, fill: 'var(--panel)', 'fill-opacity': .55 }));
      defs.appendChild(pat);
      svg2.appendChild(defs);
      /* A running sum for precipitation, because the day resolves on the
         sum and the rule across the plot is where the curve should end. The
         hourlies are summed as the file carries them, never rounded on the
         way (a sum of hundredths ran a hundredth past the day's own total),
         and once the product-day is complete the last point is the summary's
         exact total, which is the figure the day resolved on. */
      const series = p => {
        let acc = 0, last = -1;
        const pts = hours.map((hr, k) => {
          const v = (hr[p] || {})[hv];
          if (v == null) return { k, v: null };
          last = k;
          if (Vv.running) { acc += v; return { k, v: acc }; }
          return { k, v };
        });
        const ep = entry && entry[p], ex = ep && ep.complete && ep[S.var] ? ep[S.var].exact : null;
        if (Vv.running && last >= 0 && ex != null) pts[last].v = Number(ex);
        return pts;
      };
      const rt = series('rtma'), ur = series('urma');
      const vs = [].concat(rt, ur).map(p => p.v).filter(v => v != null);
      if (res && res.value != null) vs.push(res.value);
      const rungs = rungsFor(res);
      rungs.forEach(r => vs.push(r.strike));
      if (!vs.length) { host.appendChild(h('p', { class: 'cap', text: 'No ' + HOURLY[hv] + ' values on file for this day.' })); return; }
      let lo = Math.min(...vs), hi = Math.max(...vs);
      const pad = Math.max(Vv.dec ? 0.05 : 2, (hi - lo) * 0.12);
      lo -= pad; hi += pad;
      if (Vv.zeroClear) lo = Math.max(lo, -pad);
      const x = k => Lm + k / 24 * (PX - Lm), y = v => BOT - (v - lo) / (hi - lo) * (BOT - TOP);
      const stepY = Vv.dec ? ((hi - lo) > 1 ? 0.5 : (hi - lo) > 0.4 ? 0.1 : 0.05) : (hi - lo) > 40 ? 10 : (hi - lo) > 16 ? 5 : 2;
      for (let v = Math.ceil(lo / stepY) * stepY; v <= hi + 1e-9; v += stepY) {
        svg2.appendChild(E('line', { x1: Lm, x2: PX, y1: y(v), y2: y(v), stroke: 'var(--line)', 'stroke-width': .5 }));
        svg2.appendChild(T(Number(v.toFixed(2)) + (Vv.dec ? '' : Vv.unit === 'mph' ? '' : Vv.unit), { x: Lm - 6, y: y(v) + 3.5, class: 'ax', 'text-anchor': 'end' }));
      }
      const hourLabel = k => (k % 12 === 0 ? 12 : k % 12) + (k < 12 || k === 24 ? ' AM' : ' PM');
      for (let k = 3; k < 24; k += 3) {
        svg2.appendChild(E('line', { x1: x(k), x2: x(k), y1: TOP, y2: BOT, stroke: 'var(--line)', 'stroke-width': .5 }));
        svg2.appendChild(T(hourLabel(k), { x: x(k), y: BOT + 14, class: 'ax', 'text-anchor': 'middle' }));
      }
      // the day's two edges, the way the city chart marks them; the day-end
      // label is placed once the rungs are, below
      [0, 24].forEach(k => svg2.appendChild(E('line', { x1: x(k), x2: x(k), y1: TOP, y2: BOT, stroke: 'var(--muted)', 'stroke-width': .9 })));
      svg2.appendChild(T('midnight', { x: x(0), y: BOT - 4, class: 'ax', 'text-anchor': 'start' }));
      const line = (pts, stroke, width, dash) => {
        let d = '';
        pts.forEach(p => {
          if (p.v == null) { d += ' '; return; }        // a gap where the hour is missing
          const X = x(p.k + (Vv.running ? 1 : 0)).toFixed(1), Y = y(p.v).toFixed(1);
          d += (d.endsWith(' ') || !d ? 'M' : 'L') + X + ' ' + Y;
        });
        d = d.trim();
        if (d) svg2.appendChild(E('path', { d, fill: 'none', stroke, 'stroke-width': width, 'stroke-dasharray': dash || null, class: 'anaseries' }));
      };
      line(rt, 'var(--ana-rtma)', 1.4, '5 3');
      line(ur, 'var(--ana-urma)', 1.9, null);
      if (res && res.value != null) {
        svg2.appendChild(E('line', { x1: Lm, x2: PX, y1: y(res.value), y2: y(res.value), stroke: 'var(--ink)', 'stroke-width': 1.1, class: 'anares' }));
        // labelled at the left end of the rule, and by its state: running
        // while it can still move, resolved once it cannot, closed on a day
        // the job closed short of its hours
        svg2.appendChild(T(res.word + ' ' + Number(res.value).toFixed(Vv.dec) + ' ' + Vv.unit + ' (' + PRODUCTS[S.product] + ', ' + res.status + ')',
                           { x: Lm + 4, y: y(res.value) - 3, class: 'ax anareslbl', 'text-anchor': 'start' }));
      }
      // the hypothetical ladder
      const LX = PX + GAP;
      svg2.appendChild(T('Would have resolved', { x: LX + CW / 2, y: TOP - 5, class: 'axl', 'text-anchor': 'middle' }));
      if (!rungs.length) {
        svg2.appendChild(E('rect', { x: LX + 30, y: TOP + 6, width: CW - 30, height: BOT - TOP - 12, fill: 'transparent', stroke: 'var(--line)', 'stroke-width': .8, 'stroke-dasharray': '3 3' }));
        svg2.appendChild(T('no value yet', { x: LX + CW / 2, y: (TOP + BOT) / 2, class: 'ax', 'text-anchor': 'middle' }));
      }
      // whole-unit rungs sit one unit apart, which on a day with a wide
      // range is under the eleven pixels a market rung takes, so a rung is as
      // tall as the gap allows and its label shrinks with it
      const gapPx = Vv.rungs ? 11 : (BOT - TOP) / (hi - lo);
      const RH = Math.max(6, Math.min(11, gapPx * 0.82)), RF = RH >= 9 ? 9 : 7;
      /* Each rung sits at its strike's height, but the fixed precipitation
         strikes 0.01, 0.05 and 0.10 are a few pixels apart on an axis that
         reaches 2 inches, so rungs closer than a rung's height are pushed
         apart along the column (top down, then back up off the foot) and
         keep their order; the strike label travels with its rung. */
      const ys = rungs.map(r => y(r.strike));
      const minGap = RH + 2;
      for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + minGap);
      for (let i = ys.length - 1; i >= 0; i--) {
        const cap = i === ys.length - 1 ? BOT - RH / 2 : ys[i + 1] - minGap;
        ys[i] = Math.min(ys[i], cap);
      }
      /* The column is a label strip and the rungs beside it: each strike
         sits at the strip's right edge against its rung, inside the column,
         where a label hung outside the column ran across the plot's edge and
         over the day-end label. The rung keeps the rest of the width. */
      const LBL = 30, RX = LX + LBL, RW = CW - LBL;
      rungs.forEach((r, i) => {
        const yy = ys[i];
        svg2.appendChild(E('rect', { x: RX, y: yy - RH / 2, width: RW, height: RH, fill: r.yes ? 'var(--yes)' : 'var(--no)', 'fill-opacity': .42, class: 'rung ' + (r.yes ? 'yes' : 'no') }));
        svg2.appendChild(E('rect', { x: RX, y: yy - RH / 2, width: RW, height: RH, fill: 'url(#wxHatch)', stroke: 'var(--line)',
                                     'stroke-width': .8, 'stroke-dasharray': '3 2', class: 'hatched', 'pointer-events': 'none' }));
        svg2.appendChild(T(r.yes ? 'Yes' : 'No', { x: RX + RW / 2, y: yy + RF * 0.36, class: 'anarung', 'text-anchor': 'middle', style: 'font-size:' + RF + 'px' }));
        svg2.appendChild(T((Vv.yes === 'below' ? '<' : '>') + Number(r.strike).toFixed(Vv.dec) + (Vv.dec ? '' : Vv.unit === 'mph' ? '' : Vv.unit),
                           { x: RX - 3, y: yy + RF * 0.36, class: 'ax anastrike', 'text-anchor': 'end', style: 'font-size:' + RF + 'px' }));
      });
      /* The day-end label sits at the foot of the plot's right edge, a gap
         away from the lowest rung, which the layout above pushes to the same
         foot; at one height the two read as one label, so the day-end label
         moves up out of any rung's band. */
      let dy = BOT - 4;
      ys.forEach(yy => { if (dy - 9 < yy + RH / 2 && dy > yy - RH / 2) dy = yy - RH / 2 - 3; });
      svg2.appendChild(T('day end', { x: x(24), y: dy, class: 'ax anadayend', 'text-anchor': 'end' }));
      // hover: one band per hour, both analyses at that hour
      const tp = WXC.tooltip();
      hours.forEach((hr, k) => {
        const xa = x(k), xb = x(k + 1);
        const band = E('rect', { x: xa, y: TOP, width: Math.max(xb - xa, 1), height: BOT - TOP, fill: 'transparent', class: 'hband' });
        const html = () => {
          const pairs = ['rtma', 'urma'].map(p => {
            const v = (hr[p] || {})[hv];
            return [PRODUCTS[p], v == null ? 'no analysis' : v.toFixed(hourlyDec(hv)) + ' ' + Vv.unit];
          });
          if (Vv.running) pairs.push(['Running sum of the hourly analyses', [rt[k], ur[k]].map(p => (p.v == null ? '·' : p.v.toFixed(hourlyDec(hv)))).join(' / ') + ' in']);
          return tp.rows(hourLabel(k) + ' local, ' + hr.t.slice(11, 16) + ' UTC', pairs);
        };
        band.addEventListener('mousemove', e => tp.show(e, html()));
        band.addEventListener('mouseleave', () => tp.hide());
        svg2.appendChild(band);
      });
      // the legend names each line, in its own colour
      let lx = Lm;
      [[(entry && entry.rtma && entry.rtma.closed) ? 'RTMA, closed' : 'RTMA, provisional', 'var(--ana-rtma)', '5 3'], ['URMA, final when complete', 'var(--ana-urma)', null]].forEach(([label, color, dash]) => {
        svg2.appendChild(E('line', { x1: lx, x2: lx + 14, y1: TOP - 6, y2: TOP - 6, stroke: color, 'stroke-width': 2, 'stroke-dasharray': dash }));
        svg2.appendChild(T(label, { x: lx + 18, y: TOP - 2.5, class: 'ax' }));
        lx += 26 + label.length * 6.0;
      });
      const card = h('div', { class: 'card ctr' });
      card.appendChild(svg2);
      host.appendChild(card);
    }

    /* The rungs a ladder on this value would have carried: whole units either
       side of the value, or the fixed precipitation strikes. Yes when the
       value is strictly on the contract's side of the strike; equal is No. */
    function rungsFor(res) {
      if (!res || res.value == null) return [];
      const Vv = V(), v = res.value;
      const strikes = Vv.rungs ? Vv.rungs.slice() : [-3, -2, -1, 0, 1, 2, 3].map(d => Math.round(v) + d);
      return strikes.map(s => ({ strike: s, yes: Vv.yes === 'below' ? v < s : v > s })).sort((a, b) => b.strike - a.strike);
    }

    function hourTable(hours, L) {
      const wrap = h('div', { class: 'anatab' });
      const t = h('table', { class: 'anahours' });
      const thead = h('thead');
      thead.appendChild(h('tr', {}, ['Local', 'UTC', 'RTMA temp', 'RTMA wind', 'RTMA gust', 'RTMA precip',
                                     'URMA temp', 'URMA wind', 'URMA gust', 'URMA precip'].map((s, i) => h('th', { class: i > 1 ? 'num' : '', text: s }))));
      t.appendChild(thead);
      const tbody = h('tbody');
      const cell = (o, k, dec) => { const v = (o || {})[k]; return h('td', { class: 'num' + (v == null ? ' dim' : ''), text: v == null ? '·' : Number(v).toFixed(dec) }); };
      hours.forEach(hr => {
        const tr = h('tr', {}, [h('td', { text: hr.local + ':00' }), h('td', { text: hr.t.slice(5, 16).replace('T', ' ') })]);
        ['rtma', 'urma'].forEach(p => { tr.appendChild(cell(hr[p], 'temp', 1)); tr.appendChild(cell(hr[p], 'wind', 1)); tr.appendChild(cell(hr[p], 'gust', 1)); tr.appendChild(cell(hr[p], 'precip', hourlyDec('precip'))); });
        tbody.appendChild(tr);
      });
      t.appendChild(tbody);
      wrap.appendChild(t);
      wrap.appendChild(h('p', { class: 'cap', text: 'Temperature in °F, wind and gust in mph to a tenth, precipitation in inches to a ten-thousandth over the hour ending at the stamp. '
        + 'Each hourly value is the analysis at the top of that hour in ' + L.tz + '.' }));
      return wrap;
    }

    // ---- the method note, from the index's own conventions
    methodNote();
    await changed('init');
  }

  function methodNote() {
    const host = $('#anaMethod');
    if (!host) return;
    const conv = S.index.conventions || {};
    // the keys pipeline/analysis.py CONVENTIONS writes; an unknown key is shown as itself
    const LABELS = { day: 'Day', high: 'High', low: 'Low', gust: 'Peak gust', wind: 'Mean wind', precip: 'Precipitation',
                     rounding: 'Rounding and the strike', provisional: 'Provisional, final and revised',
                     closed: 'Closed incomplete days', hourly: 'Hourly values', cell: 'Cell', lattice: 'Map lattice',
                     units: 'Units' };
    host.appendChild(h('h2', { text: 'Method' }));
    host.appendChild(h('p', { text: STATEMENT + ' The conventions below are fixed in the pipeline and every file the page reads carries the times of the analyses it was built from.' }));
    const dl = h('dl', { class: 'anadl' });
    Object.keys(conv).forEach(k => {
      dl.appendChild(h('dt', { text: LABELS[k] || k }));
      dl.appendChild(h('dd', { text: String(conv[k]) }));
    });
    host.appendChild(dl);
    const src = S.index.sources || {};
    const lag = m => (m == null ? 'an unmeasured lag' : m < 90 ? 'about ' + Math.round(m) + ' minutes' : 'about ' + Math.floor(m / 60) + ' hours ' + (m % 60) + ' minutes');
    const latest = s => (s && s.latest ? ', newest hour read ' + s.latest.replace('T', ' ').replace(':00:00Z', ' UTC') : '');
    host.appendChild(h('p', { text: 'Sources. RTMA is read from NOAA Open Data (' + ((src.rtma || {}).bucket || 'noaa-rtma-pds') + '), '
      + lag((src.rtma || {}).lagMinutes) + ' after each hour' + latest(src.rtma) + '. URMA is the same analysis run again with the late-arriving observations, read from '
      + ((src.urma || {}).bucket || 'noaa-urma-pds') + ', ' + lag((src.urma || {}).lagMinutes) + ' after each hour' + latest(src.urma)
      + '. URMA precipitation is rewritten for up to eight days as the River Forecast Centers rerun their gauge analyses, which is why a resolved total can gain a revised one beside it.' }));
    host.appendChild(h('p', { text: 'The map is a subsample. Each hourly field is sampled at a 3 px pitch in the site’s map space, about 14 km between points nationally, '
      + 'and the dot values come from the place’s own nearest 2.5 km cell rather than from the picture.' }));
    host.appendChild(h('p', { text: 'Station report conventions have no analogue here. There is no last report in the hour, no special report and no tenths group; '
      + 'the hourly value is the analysis at the top of the hour, and precipitation is the accumulation over the hour ending then.' }));
  }

  return { init };
})();
