/* A map of one variable, station by station.

   The landing map is the daily temperature board. Hourly temperature and wind
   each get the same treatment, so a reader who arrives at a variable still
   arrives at a location, and a dot leads to the same station page every other
   map leads to.

   The value a dot carries is the station's own record, never a number this
   site computes: the day's strongest wind so far across the sustained and gust
   columns, or the latest temperature reported. A station without one is drawn
   hollow rather than dropped, because a missing reading is a fact about the
   station and an absent dot reads as an absent station. */
window.WXVarMap = (() => {
  const { el, txt, h, $ } = WXC;

  /* What the map colours by. `title`, `val` and `note` take the selected day,
     so one control moves the map and the boards under it together.

     Wind draws the exchange's own central value: the strike where the MG
     ladder's Yes price crosses fifty cents, which pipeline/exchange.py
     interpolates with the same `implied_median` the temperature sides use. It
     replaced the strongest reading so far, which climbed through the day and
     said nothing about where the day would end up. The contract settles on the
     day's finished maximum, so a running total is the wrong quantity to
     compare stations by and is worst exactly when it matters, early in a storm.
     The peak so far is still on every station's own page, where the shape of
     the day is the point. The site forecasts nothing here; the number is the
     exchange's prices read back. */
  const VARS = {
    wind: {
      // the third day is named, the way the button that selects it is: "for
      // Sunday" reads as something a person would say, "for the day after
      // tomorrow" does not
      title: (day, mk) => 'ForecastEx central wind for ' + (
        day === 'tomorrow' ? 'tomorrow'
        : day === 'dayafter' ? (WXC.weekdayOf((mk || {}).dayAfter) || 'the day after tomorrow')
        : 'today'),
      unit: 'mph',
      market: true,
      val: (c, day) => {
        const im = WXM.impliedWind(c, day);
        if (im && im.value != null) return Math.round(im.value);
        // no board here yet: the desk's own centre, drawn so it reads as
        // something other than a price (see `soft` below)
        const an = WXM.anticipatedWind(c, day);
        return an && an.value != null ? Math.round(an.value) : null;
      },
      soft: (c, day) => {
        const im = WXM.impliedWind(c, day);
        if (im && im.value != null) return false;
        const an = WXM.anticipatedWind(c, day);
        return !!(an && an.value != null);
      },
      note: (c, day) => {
        const im = WXM.impliedWind(c, day) || {};
        if (im.value != null) {
          const n = im.quoted || 0;
          return 'the ladder is centred on ' + Math.round(im.value) + ' mph, read across '
            + n + (n === 1 ? ' quoted strike' : ' quoted strikes');
        }
        const an = WXM.anticipatedWind(c, day);
        if (an && an.value != null) {
          return 'estimated, contract not yet listed \u00b7 centre near ' + Math.round(an.value) + ' mph';
        }
        if (im.edge === 'above') return 'every strike is priced Yes: the centre is above the top of the ladder';
        if (im.edge === 'below') return 'every strike is priced No: the centre is below the bottom of the ladder';
        if (im.state === 'no-bids') return 'listed, with no bids to read a centre from';
        if (im.state === 'day') return 'the quote summary is for another day';
        return 'the exchange has not opened this contract';
      },
      lo: 10, hi: 55,
    },
    hourly: {
      // the hourly temperature contracts are written on the daily temperature
      // stations; a station carried for its wind has no business on this map
      tempOnly: true,
      title: () => 'Latest temperature reported',
      unit: '\u00b0',
      val: c => ((c.obsLatest || {}).tempF != null ? Math.round(c.obsLatest.tempF) : null),
      note: c => ((c.obsLatest || {}).tempF == null ? 'no report'
        : Math.round(c.obsLatest.tempF) + '\u00b0 at ' + WXC.clockFull(Date.parse(c.obsLatest.t), c.tz)),
      lo: 40, hi: 95,
    },
  };
  const RAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];

  async function init(kind, opts) {
    const V = VARS[kind];
    const host = $('#varmap');
    if (!host || !V) return null;
    const tip = WXC.tooltip();
    let day = ['tomorrow', 'dayafter'].indexOf((opts || {}).day) >= 0 ? opts.day : 'today';
    // the wind variable reads the exchange's ladders, which live in the market
    // summary rather than the roster one; without it every dot is simply blank
    if (V.market) { try { await WXM.loadSummary(); } catch (e) { /* dots stay blank */ } }
    const rs = await WXD.get('summary.json');
    const base = await fetch(WXC.asset('basemap.json')).then(x => x.json()).catch(() => null);
    const roster = (rs.data || {}).cities || [];
    const cities = (V.tempOnly ? WXC.tempCities(roster) : roster).filter(c => c.onConus);
    const strip = $('#pageStatus');
    if (strip) { strip.textContent = ''; strip.appendChild(WXC.statusEl([rs], 10)); }
    if (!base || !cities.length) {
      host.appendChild(h('p', { class: 'cap' }, 'The map data is not available right now.'));
      return null;
    }
    // one path for the whole country, drawn exactly as the landing map draws it
    // The outlines carry a non-scaling stroke so a border stays a hairline at
    // every zoom rather than thickening with the geometry.
    const vb = (base.viewBox || '0 0 960 600').split(/\s+/).map(Number);
    const svg = el('svg', { viewBox: base.viewBox || '0 0 960 600', id: 'vmap' });
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state', 'vector-effect': 'non-scaling-stroke' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2', 'vector-effect': 'non-scaling-stroke' }));
    const dots = el('g', { id: 'vdots' });
    svg.appendChild(dots);
    // the scale is recomputed per day: tomorrow's board can sit well away from
    // today's, and a fixed ramp would flatten whichever day it was not built for
    let lo = V.lo, hi = V.hi;
    function rescaleRamp() {
      const vals = cities.map(c => V.val(c, day)).filter(v => v != null);
      lo = Math.min(V.lo, ...vals); hi = Math.max(V.hi, ...vals);
    }
    const shade = v => RAMP[Math.max(0, Math.min(RAMP.length - 1,
      Math.round((v - lo) / Math.max(hi - lo, 1) * (RAMP.length - 1))))];
    rescaleRamp();
    /* Zoom and pan, the same shape the basin map uses: the whole map is drawn
       in one coordinate space, so a zoom moves the viewBox rather than
       redrawing the geography, and every dot keeps working untouched.

       The dots are redrawn a beat after the zoom settles, scaled by the world
       units per screen unit, because the viewBox magnifies every glyph alike
       and a station's figure would fill a state at four times in. The wheel
       still gets its instant response. Zoomed in far enough for them to fit,
       each dot also carries its city's name. */
    const VIEW0 = { x: vb[0] || 0, y: vb[1] || 0, w: vb[2] || 960, h: vb[3] || 600 };
    let view = Object.assign({}, VIEW0);
    const MAXZ = 14;
    const GS = () => view.w / VIEW0.w;

    function drawDots() {
      const g0 = GS();
      dots.textContent = '';
      cities.forEach(c => {
        const v = V.val(c, day), X = c.px, Y = c.py;
        // the station on the dot, so a page can find its own panel from a click
        const g = el('g', { class: 'dot', 'data-station': c.station });
        const r = (v == null ? 4.5 : 7 + 6 * Math.min(1, Math.max(0, (v - lo) / Math.max(hi - lo, 1)))) * g0;
        g.appendChild(el('circle', { cx: X, cy: Y, r: r + 3 * g0, fill: 'var(--panel)', 'fill-opacity': .95 }));
        // a dot standing on the desk's figure rather than the exchange's prices
        // is outlined the way an anticipated rung is hatched: visibly not a price
        const isSoft = v != null && V.soft && V.soft(c, day);
        g.appendChild(el('circle', Object.assign(
          { cx: X, cy: Y, r, fill: v == null ? 'var(--line)' : shade(v),
            stroke: 'var(--ink)', 'stroke-width': .7 * g0 },
          isSoft ? { 'stroke-dasharray': (2.2 * g0).toFixed(2) + ' ' + (1.8 * g0).toFixed(2),
                     'stroke-width': 1.1 * g0, 'fill-opacity': .55 } : {})));
        /* The size goes in `style`, not in the font-size attribute. The `ax`
           class carries font-size in the stylesheet, a CSS rule beats a
           presentation attribute, and a px size inside a scaled viewBox is in
           USER units, so the attribute was ignored and every label grew with
           the zoom until a city's name covered three states. */
        if (v != null) g.appendChild(txt(String(v), { x: X, y: Y + 3.4 * g0, class: 'ax', 'text-anchor': 'middle',
                                                      style: 'font-size:' + (10 * g0).toFixed(2) + 'px',
                                                      'pointer-events': 'none' }));
        if (g0 < 0.42) g.appendChild(txt(c.city, { x: X, y: Y - r - 3 * g0, class: 'ax', 'text-anchor': 'middle',
                                                   style: 'font-size:' + (9.5 * g0).toFixed(2) + 'px',
                                                   'pointer-events': 'none' }));
        g.onmousemove = e => tip.show(e, '<b>' + c.city + '</b><br>' + V.note(c, day) + '<br>open the station page');
        g.onmouseleave = () => tip.hide();
        /* The page gets first refusal on a click.

           On a board that already carries a panel per station, sending the
           reader to another page to see something that is further down the one
           they are on is the wrong move. The caller says whether it handled
           the click; if it did not, the dot opens the station's page as before. */
        g.onclick = () => {
          if (opts && typeof opts.onPick === 'function' && opts.onPick(c)) return;
          location.href = WXC.cityHref(c);
        };
        dots.appendChild(g);
      });
    }
    let rescale = null;
    const scheduleRescale = () => { clearTimeout(rescale); rescale = setTimeout(drawDots, 140); };
    function applyView() {
      svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h]
        .map(n => Math.round(n * 100) / 100).join(' '));
      const z = VIEW0.w / view.w;
      const lbl = $('#vmapZoomLevel');
      if (lbl) lbl.textContent = z < 1.02 ? 'whole country' : Math.round(z * 10) / 10 + '\u00d7';
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
    /* A pan starts on pointerdown but takes the pointer only once it has
       moved past the click threshold. Capturing on pointerdown made Chromium
       deliver every click to the svg itself, so a dot clicked while the map
       was zoomed never opened its station. Until the threshold the pointer
       is tracked on the svg with a window-level pointerup, so a click that
       ends off the map still ends the gesture. */
    let drag = null, moved = 0;
    const DRAG_PX = 4;
    svg.addEventListener('pointerdown', ev => {
      if (VIEW0.w / view.w <= 1.02) return;            // nothing to pan at full extent
      if (ev.button != null && ev.button !== 0) return;
      drag = { sx: ev.clientX, sy: ev.clientY, vx: view.x, vy: view.y, id: ev.pointerId, captured: false };
      moved = 0;
    });
    svg.addEventListener('pointermove', ev => {
      if (!drag || ev.pointerId !== drag.id) return;
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      moved = Math.max(moved, Math.hypot(dx, dy));           // straight-line distance, so a diagonal wobble is still a click
      if (moved <= DRAG_PX) return;                    // still a click in the making
      if (!drag.captured) {
        drag.captured = true; svg.classList.add('grabbing');
        try { svg.setPointerCapture(drag.id); } catch (e) { /* the pointer is gone; the window pointerup ends it */ }
      }
      const k = view.w / svg.getBoundingClientRect().width;
      view.x = drag.vx - dx * k; view.y = drag.vy - dy * k;
      clampView(); applyView();
    });
    const endDrag = ev => {
      if (!drag || (ev && ev.pointerId != null && ev.pointerId !== drag.id)) return;
      if (drag.captured) { try { svg.releasePointerCapture(drag.id); } catch (e) { /* already released */ } }
      drag = null; svg.classList.remove('grabbing');
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
    // a pan that ends over a dot must not also open that station
    svg.addEventListener('click', ev => {
      if (moved > DRAG_PX) { ev.stopPropagation(); ev.preventDefault(); moved = 0; }
    }, true);

    const bar = h('div', { class: 'bar', id: 'vmapZoom' });
    const mk = (label, title, fn) => { const b = h('button', { text: label, title }); b.onclick = fn; bar.appendChild(b); };
    mk('\u2212', 'zoom out', () => zoomAbout(1 / 1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('+', 'zoom in', () => zoomAbout(1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('Reset', 'back to the whole country', resetView);
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'vmapZoomLevel', text: 'whole country' }));
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: '\u00b7 scroll to zoom, drag to pan' }));
    host.appendChild(bar);
    host.appendChild(svg);
    const cap = h('p', { class: 'cap', id: 'vmapCap' });
    const markersOf = () => (cities.find(c => c && c.markers) || {}).markers || {};
    const setCap = () => { cap.textContent = V.title(day, markersOf()) + ' (' + V.unit
      + '). A dot opens that station\u2019s page.'; };
    setCap();
    host.appendChild(cap);
    drawDots();
    applyView();
    /* The caller owns the day control, because the same button has to move the
       boards under the map as well. Changing it here re-reads every value,
       rebuilds the colour scale for that day and redraws, with the zoom and pan
       left exactly where the reader put them. */
    function setDay(next) {
      const want = ['tomorrow', 'dayafter'].indexOf(next) >= 0 ? next : 'today';
      if (want === day) return day;
      day = want;
      rescaleRamp(); drawDots(); setCap();
      return day;
    }
    return { cities, setDay, day: () => day };
  }
  return { init };
})();
