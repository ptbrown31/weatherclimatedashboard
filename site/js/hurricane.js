/* Hurricane tracking: NHC forecast tracks, cones, wind-speed probabilities
   and formation odds over the basin geography, the season's count, and,
   with the market layer on, the exchange's hurricane contracts at their
   quoted prices.

   Data in: hurricane.json (storms with NHC wind probabilities, outlook,
   season), market/hurricane.json through WXM (the quote job's hurricane
   group), reask.json (the vendor live-storm lane, usually off), and
   assets/hurricane-geo.json (countries, the coastal states and Hawaii, the
   counties the landfall contracts name, the nation coastline, the exchange's
   wind reference locations as the vendor's registry stands (the Atlantic and
   Gulf list plus the four Hawaii locations added for the 2026 Pacific season),
   and which view each region is drawn on). The drawn storm position is labeled from the
   geometry's own point and advisory, because the GIS service can trail
   NHC's roster. Equirectangular fitted to the basin box with independent
   x/y scales so the panel fills its frame (a deliberate stretch).

   Hover detail goes through the shared tooltip (WXC.tooltip): every map
   region, outlook area, storm point, cone, track, reference dot, tile and
   table row shows its values in the two-column form; a click pins the box
   on elements that do not navigate. NHC times are shown in UTC, the
   exchange's as-of in the viewer's clock. */
window.WXHur = (() => {
  const { el, txt, h, $, clockFull, dateShort } = WXC;
  const BASINS = {
    AL: { name: 'Atlantic', box: [-101.0, 4.0, -40.0, 48.0], outlook: 'Atlantic' },
    EP: { name: 'East and Central Pacific', box: [-180.0, 0.0, -85.0, 40.0], outlook: 'Pacific' },
  };
  // the strike labels the landfall contract uses, where they differ from the geometry's names
  const REGION_ALIAS = { 'The Bahamas': 'Bahamas, The', 'Bahamas': 'Bahamas, The' };
  const COUNT = { TROPA: 'Atlantic named storms', HCAB: 'Atlantic hurricanes', MHCMA: 'Atlantic major hurricanes by month', HCAT4: 'Category 4 hurricane in the US', HLF: 'Hurricane landfall' };
  // NHC GIS point types; anything not listed shows as its code
  // (the storm roster's classification uses NHC's CurrentStorms.json codes,
  // where PTC is a post-tropical cyclone or remnants and PC a potential
  // tropical cyclone, per NHC's Tropical Cyclone Status JSON File Reference)
  const TYPES = { HU: 'hurricane', MH: 'major hurricane', TS: 'tropical storm', TD: 'tropical depression', STS: 'subtropical storm', STD: 'subtropical depression',
    SS: 'subtropical storm', SD: 'subtropical depression',
    PTC: 'post-tropical cyclone', PC: 'potential tropical cyclone', EX: 'extratropical', RL: 'remnant low', LO: 'low', DB: 'disturbance' };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const RAMP = ['#fde8c8', '#f9cf94', '#f2ad5e', '#e68a2e', '#cf6a14', '#a84e0b', '#7a3607'];
  let H = null, GEO = null, NATION = null, MK = null, RK = null, SZN = null, basin = 'AL', tip = null;
  // every storm whose NHC advisories the site keeps (pipeline/advisories.py): its id, names, year and ocean
  let ADV = null;
  // the sea surface temperature anomaly under the map, and whether the reader wants it
  let SST = null;
  let sstOn = (() => { try { return localStorage.getItem('wx.hurricane.sst') !== 'off'; } catch (e) { return true; } })();
  // the hill shading over the land, one asset per basin, loaded when its basin is first shown
  const RELIEF = {};
  let reliefOn = (() => { try { return localStorage.getItem('wx.hurricane.relief') !== 'off'; } catch (e) { return true; } })();
  // the roads and city names, one asset per basin, loaded alongside the relief
  const FEAT = {};
  let featOn = (() => { try { return localStorage.getItem('wx.hurricane.roads') !== 'off'; } catch (e) { return true; } })();

  const cents = v => (v == null ? null : Math.round(v * 100));
  const local = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
  const pct = v => (v == null ? '—' : v + '¢');
  function ptColor(p) {
    if (p.kt != null && p.kt >= 96) return '#c0392b';
    if (p.type === 'HU') return '#e08a1e';
    if (p.type === 'TS' || p.type === 'STS') return '#2b7bba';
    return '#7fa6c6';
  }
  function ramp(p) {
    if (p == null) return 'var(--map-land)';
    const t = Math.max(0, Math.min(1, p)) * (RAMP.length - 1), i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = s => [1, 3, 5].map(k => parseInt(s.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((a, k) => Math.round(a + (B[k] - a) * f)).join(',') + ')';
  }
  function rings(coords) {
    if (!coords || !coords.length) return [];
    if (typeof coords[0][0] === 'number') return [coords];
    if (typeof coords[0][0][0] === 'number') return coords;
    return coords.flat();
  }

  // ---- tooltip text helpers
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // NHC times in UTC: "2026-08-23 12:00Z"
  const utc = iso => { const ms = iso ? Date.parse(iso) : NaN; return isNaN(ms) ? (iso || '—') : new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + 'Z'; };
  // the exchange's as-of in the viewer's clock
  const exAsof = () => { if (!MK || !MK.asof) return null; const ms = Date.parse(MK.asof); return dateShort(ms, local()) + ', ' + clockFull(ms, local()) + (MK.stale ? ' (stale)' : ''); };
  // "20261130" -> "Nov 30, 2026"
  const expDate = s => (s && /^\d{8}$/.test(String(s)) ? MONTHS[+String(s).slice(4, 6) - 1] + ' ' + (+String(s).slice(6, 8)) + ', ' + String(s).slice(0, 4) : (s || null));
  // one best bid with its size: "8¢ ×1000". There are no sellers on this exchange:
  // the feed's "ask" on a Yes contract is one dollar less the No bid, shown as such.
  const side = (p, sz) => (p == null ? '—' : cents(p) + '¢' + (sz != null ? ' ×' + Math.round(sz) : ''));
  const noBid = c => (c && c.ask != null ? Math.round((1 - c.ask) * 100) / 100 : null);
  /* An empty book is not a price.

     Both sides bidding the minimum leaves a midpoint of fifty cents that says
     nothing about the contract, and on a shaded map it would outrank every
     real price on the board. These boards show no price for one. A book with
     bids on one side only is a real resting bid and keeps its price. */
  const priced = c => !!(c && WXM.realMid(c));
  const oneSided = c => !!(c && c.mid != null && (c.bid == null || c.ask == null));
  // what a row prints where there is no price: an empty book and a contract
  // nobody has bid on are different states and say so
  const noPrice = c => (WXM.emptyBook(c) ? 'no price' : 'no bids');
  function bidsNote(c) {
    if (!c || c.mid == null) return null;
    const notes = [];
    if (c.bid == null || c.ask == null) notes.push(c.bid == null ? 'No bids only' : 'Yes bids only');
    if (c.from === 'no') notes.push('quoted from the No contract');
    return notes.join(', ') || null;
  }
  // the rows every contract tooltip shares
  /* The two prices, above everything else.

     A reader hovering a strike wants what it costs to take each side. The rest
     of the book stays underneath. Buying Yes costs the Yes ask; buying No costs
     one dollar less the Yes bid. */
  const priceHead = c => (!c ? '' : tip.price(
    c.ask == null ? null : c.ask,
    c.bid == null ? null : 1 - c.bid,
    c.ask == null ? null : (WXM.payout(cents(c.ask)) != null ? 'pays ' + WXM.payout(cents(c.ask)) + '\u00d7' : null),
    c.bid == null ? null : (WXM.payout(cents(1 - c.bid)) != null ? 'pays ' + WXM.payout(cents(1 - c.bid)) + '\u00d7' : null)));

  const quoteRows = c => (!c ? [] : [
    ['Yes bid', side(c.bid, c.bidSize)], ['No bid', side(noBid(c), c.askSize)],
    ['Yes price', priced(c) ? pct(cents(c.mid)) + (c.bid != null && c.ask != null ? ' (midpoint)' : '')
                            : (WXM.emptyBook(c) ? 'no price (both sides bid the minimum)' : 'no bids')],
    ['Buy Yes now at', c.ask == null ? null : pct(cents(c.ask)) + (WXM.payoutText(cents(c.ask)) ? ' · pays ' + WXM.payoutText(cents(c.ask)) : '')],
    ['Buy No now at', c.bid == null ? null : pct(100 - cents(c.bid)) + (WXM.payoutText(100 - cents(c.bid)) ? ' · pays ' + WXM.payoutText(100 - cents(c.bid)) : '')],
    ['Bids', bidsNote(c)], ['Settles', expDate(c.expiration)]]);
  // "<market> — <strike label> (<listing period>)"; a date-strike label ("By Nov 30, 2026") already names its period
  const contractTitle = (m, c) => esc((m && m.name) || '') + ' — ' + esc(c.label) + (c.expiryLabel && !/^By /.test(c.label) ? ' (' + esc(c.expiryLabel) + ')' : '');
  const asofFoot = () => { const a = exAsof(); return a ? 'as of ' + a : null; };
  // hover, leave, and a pinning click, on any element that does not navigate
  function attach(node, html) {
    node.onmousemove = e => tip.show(e, typeof html === 'function' ? html() : html);
    node.onmouseleave = () => tip.hide();
    node.onclick = e => tip.pin(e, typeof html === 'function' ? html() : html);
    node.setAttribute('data-tip-pin', '1');
  }
  /* Wind as NHC states it to the public. NHC analyses and forecasts intensity
     in knots and gives the public mph rounded to the nearest 5 (100 kt is
     115 mph, 40 kt is 45 mph, 30 kt is 35 mph, as its advisories of
     2026-09-28 read), so the number here is the advisory's number. The name
     after it is the strength NHC gives that wind: a hurricane's
     Saffir-Simpson category (64 kt and up, with categories 2 to 5 beginning
     at 83, 96, 113 and 137 kt), a tropical storm from 34 kt, a tropical
     depression below. A subtropical, post-tropical or potential tropical
     cyclone is not rated on the scale and keeps NHC's own name. A forecast
     point carries NHC's own category and label (ssnum, tcdvlp), which are
     used as they are; the knots decide only where those are missing. */
  const mph = kt => (kt == null ? null : Math.round(kt * 1.15078 / 5) * 5);
  const SUBTROPICAL = { SS: 1, SD: 1, STS: 1, STD: 1 };
  const UNRATED = { PTC: 'post-tropical cyclone', PC: 'potential tropical cyclone', EX: 'extratropical cyclone', RL: 'remnant low', LO: 'low', DB: 'disturbance' };
  const SHORT = { 'tropical storm': 'TS', 'tropical depression': 'TD', 'subtropical storm': 'SS', 'subtropical depression': 'SD' };
  function strength(kt, type, nhc) {
    const cat = n => ({ long: 'Category ' + n + ' hurricane', mid: 'Category ' + n, short: 'Cat ' + n });
    const named = w => ({ long: w, mid: w, short: SHORT[w] || w });
    const ss = nhc && nhc.ssnum != null ? Number(nhc.ssnum) : null;
    if (ss >= 1 && ss <= 5) return cat(ss);
    const dev = nhc && nhc.tcdvlp ? String(nhc.tcdvlp).trim().toLowerCase() : '';
    if (dev && !/hurricane/.test(dev)) return named(dev);
    if (kt == null) return null;
    if (SUBTROPICAL[type]) return named(kt >= 34 ? 'subtropical storm' : 'subtropical depression');
    if (UNRATED[type]) return named(UNRATED[type]);
    if (kt >= 64) return cat(kt >= 137 ? 5 : kt >= 113 ? 4 : kt >= 96 ? 3 : kt >= 83 ? 2 : 1);
    return named(kt >= 34 ? 'tropical storm' : 'tropical depression');
  }
  // "115 mph, Category 3 hurricane" (form long), "115 mph, Category 3" (mid), "115 mph Cat 3" (short)
  function windText(kt, type, nhc, form) {
    if (kt == null) return null;
    const st = strength(kt, type, nhc);
    if (!st) return mph(kt) + ' mph';
    return form === 'short' ? mph(kt) + ' mph ' + st.short : mph(kt) + ' mph, ' + st[form || 'long'];
  }
  // the storm's own motion, which NHC's roster already gives in mph
  const motionMph = s => (s.movementMph != null ? s.movementMph : s.movementKt);
  // the forecast point at the storm's current position, when it is the roster's
  // own advisory and says the same wind; an intermediate advisory can move the
  // roster's knots past a category boundary the point still carries
  const stormNow = st => {
    if (st.geometryStale) return null;
    const p0 = (st.points || []).find(p => p.tau === 0);
    return p0 && p0.kt === st.intensityKt ? p0 : null;
  };
  const compass = d => (d == null ? null : COMPASS[Math.round(((d % 360) + 360) % 360 / 22.5) % 16]);
  const position = (lat, lon) => (lat == null || lon == null ? null : Math.abs(lat).toFixed(2) + (lat < 0 ? 'S' : 'N') + ' ' + Math.abs(lon).toFixed(2) + (lon < 0 ? 'W' : 'E'));
  const typeText = t => (t ? esc(t) + (TYPES[t] ? ' · ' + TYPES[t] : '') : null);

  // ---- the exchange's hurricane group, by symbol
  const market = sym => (MK ? MK.markets.find(m => m.symbol === sym) : null);
  const yes = c => (priced(c) ? cents(c.mid) : null);
  // a ladder row's right-hand cell
  const yesText = c => { const y = yes(c); return y == null ? noPrice(c) : y + '\u00a2' + (oneSided(c) ? '*' : ''); };
  const quoteText = c => (!c ? 'not listed' : (!priced(c) ? noPrice(c) : 'Yes ' + pct(cents(c.mid)) + ' (Yes bid ' + pct(cents(c.bid)) + ' · No bid ' + pct(cents(noBid(c))) + (c.from === 'no' ? ', quoted from the No contract' : '') + ')'));
  function landfallQuotes() {
    const m = market('HLF'); if (!m) return null;
    const out = {};
    m.contracts.forEach(c => { out[c.label] = c; });
    return out;
  }
  const regionKey = label => REGION_ALIAS[label] || label;
  /* The views a landfall region is drawn on. The geometry tags Hawaii's state and counties
     Pacific, Mexico and Honduras both oceans, since the landfall contract names no ocean and
     each has a coastline on both, and everything else Atlantic. */
  const regionBasins = label => {
    const v = GEO && GEO.basins && GEO.basins[regionKey(label)];
    return Array.isArray(v) ? v : (v ? [v] : ['AL']);
  };
  const regionHere = label => regionBasins(label).indexOf(basin) >= 0;
  /* Nothing from one ocean on the other's view.

     A basin is a different question, so the Pacific view carries no Atlantic
     storm and no Atlantic contract, and the other way round. A storm's ocean
     comes from the NHC roster, matched by name, and once the roster has let it
     go, from the advisories the site keeps for it, newest season first, so a
     settled Pacific storm stays on the Pacific view. Anything neither names is
     treated as Atlantic, which is where this lane has run. */
  const stormBasin = nm => {
    const key = String(nm || '').toLowerCase();
    const r = ((H && H.storms) || []).find(x => String(x.name).toLowerCase() === key);
    if (r && r.basin) return r.basin;
    const a = Object.values((ADV && ADV.storms) || {}).filter(e => (e.names || []).some(x => String(x).toLowerCase() === key))
      .sort((p, q) => (q.year || 0) - (p.year || 0))[0];
    return a && a.basin ? a.basin : 'AL';
  };
  const inBasin = nm => (basin === 'AL' ? stormBasin(nm) === 'AL' : stormBasin(nm) !== 'AL');
  // what the owner has said the exchange will list but it has not listed yet
  const EXPECT = (window.WX && WX.expected) || {};
  const expectLandfall = () => (EXPECT.landfall || []).filter(e => (e.basin || 'AL') === basin);
  // the landfall contracts whose region is on the view being looked at
  const landfallHere = () => { const m = market('HLF'); return m ? m.contracts.filter(c => regionHere(c.label)) : []; };

  // ---- the map
  function drawNhc(svg, X, Y, b, g) {
    // g is the glyph scale: world units per screen unit at the current zoom.
    // Every size below is a screen intention, so each is multiplied by it.
    const halo = 'stroke-width:' + (3 * g).toFixed(2) + 'px';
    const outl = (H.outlook || []).filter(o => (o.basin || '') === BASINS[b].outlook);
    const oplaced = [];
    /* Labels that do not collide. Each takes the first of a few positions around
       its point where it overlaps no label or point already drawn; the storm's
       own line goes first, then its forecast points in order, and a forecast
       label that finds no room is left to the point's hover, which carries the
       same words. Widths are estimated from the character count. */
    const boxes = [];
    // a label must also sit inside the window on view, or it is cut off at its edge
    const inside = bx => bx[0] >= view.x + 2 * g && bx[2] <= view.x + view.w - 2 * g
                         && bx[1] >= view.y + 2 * g && bx[3] <= view.y + view.h - 2 * g;
    const free = bx => inside(bx) && !boxes.some(q => bx[0] < q[2] && bx[2] > q[0] && bx[1] < q[3] && bx[3] > q[1]);
    function place(text, x, y, size, attrs, spots, force) {
      const w = text.length * size * 0.56;
      const at = ([dx, dy, anchor]) => {
        const ax = x + dx * g, ay = y + dy * g;
        const x0 = anchor === 'end' ? ax - w : anchor === 'middle' ? ax - w / 2 : ax;
        return { ax, ay, anchor, bx: [x0 - g, ay - size * 0.9, x0 + w + g, ay + size * 0.3] };
      };
      const cands = spots.map(at);
      // the first free spot; a label that must be drawn takes the first spot
      // inside the view when none is free, overlap and all
      const pick = cands.find(c => free(c.bx)) || (force ? (cands.find(c => inside(c.bx)) || cands[0]) : null);
      if (!pick) return false;
      boxes.push(pick.bx);
      svg.appendChild(txt(text, Object.assign({ x: pick.ax, y: pick.ay, 'text-anchor': pick.anchor, 'font-size': size }, attrs)));
      return true;
    }
    const path = (r, close) => r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('') + (close ? 'Z' : '');
    outl.forEach(o => rings(o.region).forEach(r => {
      const area = el('path', { d: path(r, true), fill: 'rgba(224,138,30,.13)', stroke: '#e08a1e', 'stroke-width': 1.6 * g, 'stroke-dasharray': (6 * g) + ' ' + (4 * g) });
      attach(area, tip.rows('NHC seven-day formation outlook — ' + esc(BASINS[b].outlook), [['2-day', esc(o.prob2 || '—')], ['7-day', esc(o.prob7 || '—')]],
        'NHC Tropical Weather Outlook, as of ' + utc(H.outlookAsof || H.asof)));
      svg.appendChild(area);
      const cx = Math.min(r.reduce((s, p) => s + X(p[0]), 0) / r.length, 905);
      let cy = r.reduce((s, p) => s + Y(p[1]), 0) / r.length;
      while (oplaced.some(q => Math.abs(cx - q[0]) < 115 * g && Math.abs(cy - q[1]) < 34 * g)) cy += 34 * g;
      oplaced.push([cx, cy]);
      boxes.push([cx - 50 * g, cy - 11 * g, cx + 50 * g, cy + 18 * g]);
      svg.appendChild(txt((o.prob7 || '?') + ' / 7 days', { x: cx, y: cy, 'text-anchor': 'middle', 'font-size': 11 * g, 'font-weight': 700, fill: '#b26a08', class: 'lbl', style: halo }));
      svg.appendChild(txt((o.prob2 || '?') + ' / 2 days', { x: cx, y: cy + 14 * g, 'text-anchor': 'middle', 'font-size': 9.5 * g, fill: '#b26a08', class: 'lbl', style: halo }));
    }));
    const storms = (H.storms || []).filter(s => b === 'AL' ? s.basin === 'AL' : s.basin !== 'AL');
    storms.forEach(s => {
      const adv = s.geometryAdvisory || s.advisory;
      /* A superseded forecast is not drawn as though it were current.

         The GIS service stops issuing cone and track packages before NHC stops
         advising, so a decaying storm keeps its last package while the roster
         moves on: Edouard's cone was still the one from its landfall advisory
         two days after it went inland, and its landfall point was labeled the
         current position. When the geometry trails the roster the cone and the
         forecast track belong to an advisory NHC has replaced, so they are left
         off; the past track is history and stays; and the storm is drawn where
         the roster says it is now. */
      const stale = !!s.geometryStale;
      const advNow = stale ? s.advisory : adv;
      const geomRows = [['Advisory', esc(adv)], ['Geometry', stale ? 'stale (trails advisory ' + esc(s.advisory) + ')' : null]];
      const fetchedFoot = 'fetched ' + utc(s.geometryFetched);
      const name = esc(s.name);
      // a line is hard to hover at 2px, so each track gets an invisible wide twin for the pointer
      const hitLine = (d, html) => { const q = el('path', { d, fill: 'none', stroke: '#000', 'stroke-opacity': 0, 'stroke-width': 9 * g, 'pointer-events': 'stroke' }); attach(q, html); svg.appendChild(q); };
      if (!stale) (s.cone || []).forEach(c => rings(c).forEach(r => {
        // the cone lets the pointer through: the landfall regions beneath it carry the bids,
        // and the cone's advisory and fetch time are on every track point
        svg.appendChild(el('path', { d: path(r, true), fill: 'rgba(100,116,139,.14)', stroke: '#64748b', 'stroke-width': 1 * g, 'pointer-events': 'none' }));
      }));
      (s.past || []).forEach(c => rings(c).forEach(r => {
        svg.appendChild(el('path', { d: path(r), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.3 * g, 'stroke-dasharray': (3 * g) + ' ' + (3 * g) }));
        hitLine(path(r), tip.rows(name + ' — past track', geomRows, fetchedFoot));
      }));
      if (!stale) (s.track || []).forEach(c => rings(c).forEach(r => {
        svg.appendChild(el('path', { d: path(r), fill: 'none', stroke: 'var(--navy)', 'stroke-width': 2 * g }));
        hitLine(path(r), tip.rows(name + ' — NHC forecast track', geomRows, fetchedFoot));
      }));
      const hits = [];
      // where the roster says the storm is now, when the geometry's own points
      // are an advisory or more out of date
      const pts = !stale ? (s.points || [])
        : (s.lat != null && s.lon != null
            ? [{ lon: s.lon, lat: s.lat, kt: s.intensityKt, type: s.classification, tau: 0, label: null }] : []);
      // the points first, so no label is placed over one
      pts.forEach(p => {
        const pr = (p.tau === 0 ? 6 : 4.5) * g;
        svg.appendChild(el('circle', { cx: X(p.lon), cy: Y(p.lat), r: pr, fill: ptColor(p), stroke: 'var(--panel)', 'stroke-width': 1.2 * g }));
        boxes.push([X(p.lon) - pr, Y(p.lat) - pr, X(p.lon) + pr, Y(p.lat) + pr]);
      });
      // the storm's own line first, then a short label per point (the full name
      // is in its hover), each where it collides with nothing drawn before it
      pts.map((p, i) => [p, i]).sort((a, c) => (c[0].tau === 0) - (a[0].tau === 0)).forEach(([p, i]) => {
        const x = X(p.lon), y = Y(p.lat);
        if (p.tau === 0) place((p.type || s.classification) + ' ' + s.name + ' · '
          + (p.kt != null ? windText(p.kt, p.type || s.classification, p, 'mid') : windText(s.intensityKt, s.classification, null, 'mid')) + ' · adv ' + advNow,
          x, y, 12.5 * g, { 'font-weight': 700, fill: 'var(--navy)', class: 'lbl slbl', style: halo },
          [[10, -20, 'start'], [-10, -20, 'end'], [10, 30, 'start'], [-10, 30, 'end'], [14, 4, 'start'], [-14, 4, 'end']], true);
        if (p.label) {
          const sp = [[8, -8, 'start'], [8, 14, 'start'], [-8, -8, 'end'], [-8, 14, 'end'], [0, -13, 'middle'], [0, 21, 'middle']];
          if (i % 2) sp.unshift(sp.splice(1, 1)[0]);       // alternate sides along the track
          place(p.label.replace(':00', '') + (p.kt ? ' · ' + windText(p.kt, p.type, p, 'short') : ''), x, y, 9 * g,
                { fill: 'var(--ink)', class: 'lbl slbl', style: halo }, sp);
        }
      });
      pts.forEach(p => {
        // the hover target: an invisible circle wider than the drawn point
        const now = p.tau === 0;
        const rows = [
          ['Valid', esc(p.label || '—')],
          ['Wind', p.kt != null ? windText(p.kt, p.type || s.classification, p) : '—'],
          ['Type', typeText(p.type || s.classification)],
          ['Position', position(p.lat, p.lon)],
          ['Advisory', esc(advNow)],
          now ? null : ['Lead', (p.tau != null ? p.tau : '—') + ' h'],
          now ? ['Pressure', s.pressureMb ? esc(s.pressureMb) + ' mb' : '—'] : null,
          now ? ['Movement', s.movementDir != null ? s.movementDir + '° (' + compass(s.movementDir) + ')' + (motionMph(s) != null ? ' at ' + motionMph(s) + ' mph' : '') : '—'] : null,
          now ? ['NHC last update', utc(s.updated)] : null,
        ];
        const html = tip.rows(esc(p.type || s.classification) + ' ' + name + ' — ' + (now ? 'current position' : 'forecast point'), rows,
          (stale ? 'the cone and forecast track from advisory ' + esc(adv) + ' have been superseded and are not drawn · ' : '')
          + (now && s.advisoryUrl ? 'NHC advisory → opens in a new tab' : 'lead time from the advisory'));
        const hit = el('circle', { cx: X(p.lon), cy: Y(p.lat), r: (now ? 11 : 9) * g, fill: '#000', 'fill-opacity': 0, 'pointer-events': 'all', style: 'cursor:pointer' });
        if (now && s.advisoryUrl) {
          hit.onmousemove = e => tip.show(e, html);
          hit.onmouseleave = () => tip.hide();
          hit.onclick = () => window.open(s.advisoryUrl, '_blank', 'noopener');
        } else attach(hit, html);
        hits.push([now ? 1 : 0, hit]);
      });
      // the current position's target goes on top, so a slow storm's +12 h point cannot cover it
      hits.sort((a, b) => a[0] - b[0]).forEach(([, hit]) => svg.appendChild(hit));
    });
    return { storms: storms.length, areas: outl.length, boxes,
             stale: storms.filter(s2 => s2.geometryStale).map(s2 => s2.name) };
  }

  /* The two feeds' clocks. NHC issues full advisories at 03, 09, 15 and 21Z.
     LiveCyc runs four cycles a day at 00, 06, 12 and 18Z, but the thing a
     reader waits for is the file, and every 2026 file on the exchange's
     download directory has landed 3.9 to 5.7 hours after its cycle stamp,
     median about 4.7, which puts each one after the NHC advisory it takes
     its track from. So the LiveCyc countdown aims at the expected arrival,
     the cycle plus four and a half hours, not at the cycle stamp, which the
     first version counted to and which read as a file being due when the
     previous cycle's had only just arrived. Refreshed every half minute;
     typical, not promised. */
  const LIVECYC_LAG_H = 4.5;
  function nextMark(marks) {
    const now = Date.now(), d = new Date(now);
    // yesterday too: an offset mark near midnight can belong to the day before
    const base = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - 86400000;
    const all = [];
    for (let day = 0; day < 3; day++) marks.forEach(hh => all.push(base + day * 86400000 + hh * 3600000));
    return all.find(t => t > now);
  }
  function cdText(t) {
    const left = t - Date.now();
    if (left <= 0) return 'due now';
    const hrs = Math.floor(left / 3600000), min = Math.round((left % 3600000) / 60000);
    return 'in ' + (hrs ? hrs + 'h ' : '') + String(min).padStart(2, '0') + 'm';
  }
  let cdTimer = null;
  function wireCountdowns() {
    if (cdTimer) return;
    cdTimer = setInterval(() => {
      document.querySelectorAll('[data-cdt]').forEach(e => { e.textContent = cdText(+e.getAttribute('data-cdt')); });
    }, 30000);
  }
  function scheduleLine(label, marks, note) {
    wireCountdowns();
    const t = nextMark(marks);
    const sp = h('b', { 'data-cdt': String(t), text: cdText(t) });
    return h('p', { class: 'cap', style: 'margin:2px 0 8px' }, [
      h('b', { text: label + ' ' }),
      utc(new Date(t).toISOString()) + ' (' + clockFull(t, local()) + ') · ',
      sp,
      note ? ' · ' + note : '',
    ]);
  }

  /* Every still-running storm's LiveCyc row at a reference location, hottest
     first. It used to return only the hottest storm's row, and with two
     storms in the Gulf at once the hover on Port Arthur quoted one storm
     while the table underneath quoted the other, and the numbers read as a
     contradiction. A storm that has stopped updating does not appear; its
     last ladder describes a day that is over. */
  function vendorSite(id) {
    if (!RK || !RK.enabled) return null;
    const out = [];
    (RK.storms || []).filter(s => !WXStorm.dormant(s)).forEach(s => {
      // the newest cycle's row, and the Metryc interim's row for the same
      // location once one has landed, which is the vendor's later word on it
      const lc = s.livecyc || {};
      const row = lc.sites && lc.sites[id];
      const im = s.interim && s.interim.sites && s.interim.sites[id];
      if (!row && !im) return;
      const thr = lc.thresholds || (s.interim || {}).thresholds || [];
      const p = row ? row.p : thr.map(() => 0);
      const i80 = thr.indexOf(80);
      out.push({ storm: s.name, p80: i80 >= 0 ? p[i80] : p[0],
                 thresholds: thr, p, forecastTime: lc.forecastTime,
                 received: lc.lastModified,
                 interim: im ? { p: im.p, received: s.interim.lastModified } : null });
    });
    out.sort((a, b) => b.p80 - a.p80);
    return out.length ? out : null;
  }
  /* What a dot's size reads: the vendor's chance of a gust at one threshold,
     which used to be 80 mph whatever the storm. A strong storm takes the places
     in its path to nearly a hundred percent there and their dots all reach full
     size, so the size stopped telling which face the worst wind (owner,
     2026-10-09; Isaias read 98% and 97% at Fort Walton Beach and Pensacola).
     The threshold now follows the storm: the lowest one at which no location
     has reached ninety percent, so the most likely place to see the most
     extreme wind is still short of full size and the places behind it fall
     away by how far short they come (Isaias on 9 October: 100 mph, reading 86,
     73, 26, 3 and 2 percent down the coast). A weaker storm comes back down
     the ladder on its own. The chance is the vendor's published figure, the
     Metryc interim's where that is higher. */
  const SIZE_FULL = 90;                                      // percent
  const chanceAt = (v, t) => (v || []).reduce((m, x) => {
    const i = (x.thresholds || []).indexOf(t);
    if (i < 0) return m;
    return Math.max(m, x.p[i] || 0, x.interim && x.interim.p ? (x.interim.p[i] || 0) : 0);
  }, 0);
  let sizeT = 80;
  function sizeThreshold(locs) {
    const sites = locs.map(L => vendorSite(L.id)).filter(Boolean);
    const thr = [...new Set(sites.flatMap(v => v.flatMap(x => x.thresholds || [])))].sort((a, b) => a - b);
    if (!thr.length) return 80;
    const t = thr.find(u => !sites.some(v => chanceAt(v, u) >= SIZE_FULL));
    return t != null ? t : thr[thr.length - 1];
  }
  // the tooltip for a reference location, on the map and in the vendor table.
  // Takes one storm's row or the list vendorSite returns, and when more than
  // one storm is signalling on the location, every one is shown, each under
  // its own name and cycle, because a box quoting one storm over a table
  // quoting another reads as a contradiction rather than two hazards.
  function locationTip(L, v) {
    const list = Array.isArray(v) ? v : (v ? [v] : []);
    const rows = [['Region', esc(L.region)], ['Country', esc(L.country)], ['State', esc(L.state)]];
    let foot;
    if (list.length) {
      /* The ladder as published, to the same figure as the table underneath.
         Rounding it to whole percent put "3%" in the box over a cell reading
         3.3%, and culling anything under half a percent left the box showing
         one rung where the table and the storm card both showed three. A rung
         the page prints is a rung this box prints; eight is the cap, which
         only a strong storm reaches, and five each when storms share the box. */
      const cap = list.length > 1 ? 5 : 8;
      list.forEach(v2 => {
        rows.push(['Storm', esc(v2.storm) + ' · ' + esc(v2.file || 'LiveCyc')
          + (v2.forecastTime ? ' cycle ' + utc(v2.forecastTime) : '')
          + (v2.received ? ', received ' + utc(v2.received) : '')]);
        v2.thresholds.map((t, i) => (v2.p[i] ? ['&ge; ' + t + ' mph', esc(WXC.vendorPct(v2.p[i]))] : null))
          .filter(Boolean).slice(0, cap).forEach(r => rows.push(r));
        if (v2.interim) {
          // the interim's rungs under their own heading; a published zero at
          // every rung is printed at the lowest one, since it is a figure
          rows.push(['Metryc interim', 'received ' + utc(v2.interim.received)]);
          const im = v2.thresholds.map((t, i) => (v2.interim.p[i] ? ['interim &ge; ' + t + ' mph', esc(WXC.vendorPct(v2.interim.p[i]))] : null))
            .filter(Boolean);
          (im.length ? im.slice(0, cap) : [['interim &ge; ' + v2.thresholds[0] + ' mph', '0%']]).forEach(r => rows.push(r));
        }
      });
      foot = esc((RK && RK.attribution) || 'Powered by Reask') + '; probabilities as published'
           + (list.length > 1 ? '; each storm is its own hazard' : '');
    } else foot = (RK && RK.enabled) ? 'no storm probabilities published' : 'no live-storm probabilities (lane off)';
    return tip.rows(esc(L.name) + ' (' + esc(L.id) + ')', rows, foot);
  }
  const locationById = id => ((GEO && GEO.locations) || []).find(L => L.id === id);

  /* A reference location's box on the map: the contract to trade there.

     The newest vendor file's ladder and the series through the storm sit in
     the panel beside the map, so this box no longer repeats them (owner,
     2026-10-08). It shows one of the location's listed wind contracts as the
     prices to buy Yes and No now, in the box every other contract on this site
     uses: the highest listed threshold the vendor gives at least a ten percent
     chance, which is the rung still in play, or the lowest listed one when
     none reaches that. The chance is the vendor's later word where there is
     one, the Metryc interim over the cycle, as in the panel. A storm with
     figures here and no listed contract gives the vendor's chance at that rung
     instead, and two storms on one location each get their own block, hottest
     first, because each is its own hazard. A location with no figures keeps
     its plain description. */
  const IN_PLAY = 10;                                       // percent
  function inPlay(thr, p, strikes) {
    const at = t => p[thr.findIndex(x => Number(x) === Number(t))] || 0;
    const up = strikes.map(Number).sort((a, b) => a - b);
    // judged on the figure as the page prints it, so a rung reading 10% is in play
    const hot = up.filter(t => Math.round(at(t)) >= IN_PLAY);
    const t = hot.length ? hot[hot.length - 1] : up[0];
    return { t, p: at(t) };
  }
  const centsOf = v => (v == null ? '\u2014' : Math.round(v * 100) + '\u00a2');
  const paysOf = v => {
    const m = v == null ? null : WXM.payout(Math.round(v * 100));
    return m == null ? null : 'pays ' + m + '\u00d7';
  };
  function dotTip(L, v) {
    const list = Array.isArray(v) ? v : (v ? [v] : []);
    if (!list.length) return locationTip(L, v);
    const credit = esc((RK && RK.attribution) || 'Powered by Reask');
    const blocks = list.map(v2 => {
      const s = ((RK && RK.storms) || []).find(x => x.name === v2.storm);
      const lm = s ? lMarketOf(s, L.id) : null;
      const thr = v2.thresholds || [];
      const p = v2.interim ? v2.interim.p : v2.p;
      const file = v2.interim ? 'Metryc interim' : 'LiveCyc' + (v2.forecastTime ? ' ' + utc(v2.forecastTime) : '');
      const cs = (lm && lm.contracts) || [];
      if (!cs.length) {
        const r = inPlay(thr, p, thr);
        return tip.rows(esc(v2.storm) + ' \u00b7 ' + esc(L.name) + ' (' + esc(L.id) + ')',
          [['LiveCyc chance, gusts &ge; ' + r.t + ' mph', esc(WXC.vendorPct(r.p))], ['Contract', 'none listed here']],
          credit + ', ' + file + ' file');
      }
      const r = inPlay(thr, p, cs.map(c => c.strike));
      const c = cs.find(x => Number(x.strike) === r.t);
      // a book whose two sides both bid the minimum is empty and carries no price
      const empty = WXM.emptyBook(c);
      const yesBuy = empty ? null : c.ask, noBuy = empty || c.bid == null ? null : 1 - c.bid;
      // a click on the location pins this box, and a price in a pinned box opens the contract
      const url = WXM.contractUrl(lm.productConid, c.conidYes || c.conid);
      return '<b>' + esc(lm.name || v2.storm + ' \u00b7 ' + L.name) + ' \u2014 ' + esc(c.label || 'At Least ' + r.t) + '</b>'
        + tip.price(yesBuy, noBuy, paysOf(yesBuy), paysOf(noBuy), url)
        + tip.rows(null, [['Fair-value reference (LiveCyc)', esc(WXC.vendorPct(r.p))], ['Settles', esc(c.expiryLabel)]],
          (c.bid != null || c.ask != null
            ? 'Yes bid ' + centsOf(c.bid) + ' \u00b7 No bid ' + centsOf(c.ask == null ? null : 1 - c.ask)
            : 'no bids on either side')
          + (empty ? ' \u00b7 both sides bid the minimum, so no price is shown' : '')
          + ' \u00b7 the highest listed rung with a LiveCyc chance of ' + IN_PLAY + '% or more \u00b7 ' + credit
          + (url ? '<br><span class="tnot">Click the location to pin this box.</span>'
                 + '<span class="tyes">Click a price to open the contract on IBKR.</span>' : ''));
    });
    return blocks.join('<div style="height:8px"></div>')
      + (list.length > 1 ? '<div class="tf">each storm is its own hazard</div>' : '');
  }

  /* ---- the side panel: one location's ladder and series, filled from the map

     Beside the map on a wide screen while a storm is running in the basin on
     view. Its two slots stand empty, each saying what it will hold, until a red
     location is hovered or focused; the panel then draws that location's ladder
     from the newest vendor file and its series through the storm, the cards the
     storm section draws below, and keeps them until another location is
     hovered. The strongest locations are offered as a way in. */
  let sideSid = null, sideKey = '';
  function sideLive() {
    return !!(RK && RK.enabled && window.WXStorm)
      && (RK.storms || []).some(s => inBasin(s.name) && !WXStorm.dormant(s) && s.livecyc);
  }
  function sideSlot(id, label, empty) {
    return h('div', { class: 'msbox', id }, [h('div', { class: 'mslbl', text: label }), h('div', { class: 'msempty', text: empty })]);
  }
  function sideFrame() {
    const wrap = $('#mapWrap'), side = $('#mapSide');
    if (!wrap || !side) return;
    const on = sideLive();
    wrap.classList.toggle('has-side', on);
    side.hidden = !on;
    if (!on) { side.innerHTML = ''; sideKey = ''; sideSid = null; return; }
    const ranked = ((GEO && GEO.locations) || []).map(L => [L, vendorSite(L.id)])
      .filter(([, v]) => v && v.some(x => x.p.some(q => q > 0)))
      .sort((a, b) => (chanceAt(b[1], sizeT) - chanceAt(a[1], sizeT)) || (Math.max.apply(null, b[1][0].p) - Math.max.apply(null, a[1][0].p)));
    const key = basin + '|' + ranked.slice(0, 4).map(x => x[0].id).join(',') + '|' + (RK.asof || '');
    if (key === sideKey) return;
    sideKey = key;
    side.innerHTML = '';
    side.appendChild(h('div', { class: 'mshd', text: 'Live Hurricane contract at one location' }));
    side.appendChild(h('p', { class: 'cap', style: 'margin:0', text: 'Hover a red location on the map to fill these two panels.' }));
    side.appendChild(sideSlot('msLadder', 'Ladder', 'The chance of a gust at or above each threshold from the newest vendor file, '
      + 'with the exchange’s price once that threshold is listed.'));
    side.appendChild(sideSlot('msSeries', 'Through the storm', 'The same chances delivery by delivery, with the exchange’s price, '
      + 'as in the cards below the map.'));
    if (ranked.length) {
      const q = h('div', { class: 'msquick' }, [h('span', { text: 'Strongest now ' })]);
      ranked.slice(0, 4).forEach(([L]) => {
        const b = h('button', { text: L.name, title: 'show ' + L.name + ' in these panels' });
        b.onclick = () => showSide(L.id, true);
        b.onmouseenter = () => showSide(L.id);
        q.appendChild(b);
      });
      side.appendChild(q);
    }
    if (sideSid) showSide(sideSid, true);
  }
  /* A link can name a reference location (?loc=PC): the exchange's research
     button sends a reader here from one location's wind contract, through the
     aliases in config/category_aliases.json. Once the ledgers have landed the
     location fills the panel beside the map, or on a screen too narrow for the
     panel its card below is brought into view. A location nothing has been
     delivered for leaves the page as it opens. */
  function focusLoc(id) {
    if (!id || !locationById(id)) return;
    const series = (window.WXStorm && WXStorm.sites) ? WXStorm.sites() : {};
    if (!series[id] && !vendorSite(id)) return;
    const side = $('#mapSide');
    if (side && !side.hidden && getComputedStyle(side).display !== 'none') showSide(id, true);
    else if (window.WXStorm && WXStorm.showSite) WXStorm.showSite(id);
  }
  async function showSide(id, again) {
    const side = $('#mapSide'); if (!side || side.hidden) return;
    if (id === sideSid && !again) return;
    sideSid = id;
    document.querySelectorAll('#basin circle.msel').forEach(c => c.classList.remove('msel'));
    const dot = document.querySelector('#basin circle[data-loc="' + id + '"]'); if (dot) dot.classList.add('msel');
    const v = vendorSite(id);
    const top = v && v[0];
    const s = top && (RK.storms || []).find(x => x.name === top.storm);
    const nm = (locationById(id) || {}).name || id;
    const lad = $('#msLadder'), ser = $('#msSeries');
    if (lad) {
      lad.innerHTML = '';
      lad.appendChild(h('div', { class: 'mslbl', text: 'Ladder' }));
      const shown = s && shownFile(s);
      const row = shown && shown.file.sites && shown.file.sites[id];
      lad.classList.toggle('filled', !!row);
      lad.appendChild(row ? ladderCard(s, shown, id, row)
        : h('div', { class: 'msempty', text: nm + ' carries no figure in the newest vendor file.' }));
    }
    if (ser) {
      const got = WXStorm.seriesCard ? await WXStorm.seriesCard(id, top && top.storm) : null;
      if (sideSid !== id) return;           // a later hover has taken the panel
      ser.innerHTML = '';
      ser.appendChild(h('div', { class: 'mslbl', text: 'Through the storm' }));
      ser.classList.toggle('filled', !!got);
      ser.appendChild(got ? got.node : h('div', { class: 'msempty', text: 'No delivery has carried ' + nm + ' yet.' }));
    }
    // the panel's ladder and series can carry prices the map itself did not
    if ($('#basinCap')) WXM.tradeNote($('#mapWrap'), $('#basinCap'));
  }

  // ---- zoom and pan on the basin map
  //
  // The whole map is drawn in one coordinate space, so zooming is a matter of
  // moving the viewBox rather than redrawing anything: shapes, dots and their
  // links keep working untouched at every scale. The view survives a redraw
  // (quotes refresh every ten minutes) and resets when the basin changes,
  // because a window over one ocean means nothing over another.
  const VIEW0 = { x: 0, y: 0, w: 980, h: 600 };
  let view = Object.assign({}, VIEW0);
  /* The page opens on the active storms rather than the whole ocean, and keeps
     following them as advisories come in until the reader zooms or pans; from
     then on the view is the reader's until the basin changes. */
  let autoView = true;
  const MAXZ = 12;                       // beyond this the vector outlines are the limit, not the pixels
  /* Glyph scale. The viewBox zoom magnifies everything, geography and glyphs
     alike, so at 4x a storm label filled the Gulf. Dots, labels and stroke
     widths are drawn multiplied by this factor, the world units per screen
     unit, which keeps them the same size on screen at every zoom; the wheel
     still gets its instant viewBox response, and a short beat after the zoom
     settles the map redraws its glyphs at the new scale. */
  const GS = () => view.w / VIEW0.w;
  let rescaleTimer = null;
  function scheduleRescale() {
    clearTimeout(rescaleTimer);
    rescaleTimer = setTimeout(() => { if (H) draw(); }, 140);
  }
  function applyView() {
    const svg = $('#basin'); if (!svg) return;
    svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].map(n => Math.round(n * 100) / 100).join(' '));
    const z = VIEW0.w / view.w;
    const lbl = $('#basinZoomLevel');
    const names = stormsHere().map(s => s.name);
    const onStorm = names.length && autoView && z > 1.02;
    if (lbl) lbl.textContent = z < 1.02 ? 'whole basin' : onStorm ? names.join(' and ') + ', ' + Math.round(z * 10) / 10 + '×' : Math.round(z * 10) / 10 + '×';
    svg.classList.toggle('grab', z > 1.02);
    // the sea temperature switch only where there is a field to switch
    const ss = $('#basinSst');
    if (ss) ss.hidden = !(SST && SST.basins && SST.basins[basin]);
    const sb = $('#basinStorm');
    if (sb) { sb.hidden = !names.length; sb.textContent = names.join(' and '); sb.title = 'zoom to ' + names.join(' and ') + (names.length > 1 ? '’ tracks' : '’s track'); }
  }
  function zoomAbout(factor, cx, cy) {
    const z = VIEW0.w / view.w;
    const want = Math.min(Math.max(z * factor, 1), MAXZ);
    if (Math.abs(want - z) < 1e-6) return;
    autoView = false;
    const w = VIEW0.w / want, h = VIEW0.h / want;
    // keep the point under the pointer where it is
    view.x = cx - (cx - view.x) * (w / view.w);
    view.y = cy - (cy - view.y) * (h / view.h);
    view.w = w; view.h = h;
    clampView(); applyView(); scheduleRescale();
  }
  function clampView() {
    view.x = Math.min(Math.max(view.x, VIEW0.x), VIEW0.x + VIEW0.w - view.w);
    view.y = Math.min(Math.max(view.y, VIEW0.y), VIEW0.y + VIEW0.h - view.h);
  }
  const resetView = () => { autoView = true; view = Object.assign({}, VIEW0); applyView(); scheduleRescale(); };
  const wholeBasin = () => { autoView = false; view = Object.assign({}, VIEW0); applyView(); scheduleRescale(); };
  const toStorm = () => { const sv = stormView(); if (!sv) return; autoView = true; view = sv; applyView(); scheduleRescale(); };
  /* The window a reader most likely came for: the cones, forecast tracks and
     positions of the storms active in this basin, and the reference locations a
     running storm signals on, with room around them, at the map's own shape and
     never closer than four times. Null when no storm is active here. */
  function stormView() {
    const [b0, la0, b1, la1] = BASINS[basin].box;
    const kx = VIEW0.w / (b1 - b0), ky = VIEW0.h / (la1 - la0);
    const xs = [], ys = [];
    const add = (lon, lat) => {
      if (lon < b0 || lon > b1 || lat < la0 || lat > la1) return;
      xs.push((lon - b0) * kx); ys.push((la1 - lat) * ky);
    };
    stormsHere().forEach(s => {
      if (!s.geometryStale) {
        (s.cone || []).forEach(c => rings(c).forEach(r => r.forEach(q => add(q[0], q[1]))));
        (s.track || []).forEach(c => rings(c).forEach(r => r.forEach(q => add(q[0], q[1]))));
        (s.points || []).forEach(p => add(p.lon, p.lat));
      }
      if (s.lat != null && s.lon != null) add(s.lon, s.lat);
    });
    if (!xs.length) return null;
    ((GEO && GEO.locations) || []).forEach(L => {
      const v = vendorSite(L.id);
      if (v && v.some(x => x.p.some(q => q > 0))) add(L.lon, L.lat);
    });
    let x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    const px = Math.max(36, (x1 - x0) * 0.14), py = Math.max(36, (y1 - y0) * 0.14);
    x0 -= px; x1 += px; y0 -= py; y1 += py;
    const focus = [x0, y0, x1, y1];
    const ar = VIEW0.w / VIEW0.h;
    // no closer than four times: a storm with only a position and no cone yet
    // would otherwise fill the map with one dot
    let w = Math.max(x1 - x0, (y1 - y0) * ar, VIEW0.w / 4);
    w = Math.min(w, VIEW0.w);
    const hh = w / ar;
    const v = { x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - hh / 2, w, h: hh };
    v.x = Math.min(Math.max(v.x, VIEW0.x), VIEW0.x + VIEW0.w - v.w);
    v.y = Math.min(Math.max(v.y, VIEW0.y), VIEW0.y + VIEW0.h - v.h);
    v.focus = focus;
    return v;
  }
  function atPoint(ev) {
    const svg = $('#basin');
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const q = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [q.x, q.y];
  }
  function wireZoom() {
    const svg = $('#basin'); if (!svg || svg.dataset.zoom) return;
    svg.dataset.zoom = '1';
    svg.addEventListener('wheel', ev => {
      ev.preventDefault();
      const [cx, cy] = atPoint(ev);
      zoomAbout(ev.deltaY < 0 ? 1.18 : 1 / 1.18, cx, cy);
    }, { passive: false });
    let drag = null, moved = 0;
    /* The pointer is captured only once a press has moved far enough to be a
       drag. Capturing it on the press itself sent every click on a zoomed map
       to the map, so a location or a landfall region under the pointer never
       heard it, and the storm view opens zoomed in. */
    svg.addEventListener('pointerdown', ev => {
      if (VIEW0.w / view.w <= 1.02) return;            // nothing to pan at full extent
      drag = { sx: ev.clientX, sy: ev.clientY, vx: view.x, vy: view.y, id: ev.pointerId, held: false };
      moved = 0;
    });
    svg.addEventListener('pointermove', ev => {
      if (!drag) return;
      const k = view.w / svg.getBoundingClientRect().width;
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
      if (moved <= 4) return;
      if (!drag.held) {
        try { svg.setPointerCapture(drag.id); } catch (e) { /* the pointer has gone */ }
        drag.held = true; svg.classList.add('grabbing');
      }
      autoView = false;
      view.x = drag.vx - dx * k; view.y = drag.vy - dy * k;
      clampView(); applyView();
    });
    const end = ev => {
      if (!drag) return;
      if (drag.held) try { svg.releasePointerCapture(drag.id); } catch (e) { /* already released */ }
      drag = null; svg.classList.remove('grabbing');
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    // a pan that ends over a contract must not also open that contract
    svg.addEventListener('click', ev => { if (moved > 4) { ev.stopPropagation(); ev.preventDefault(); moved = 0; } }, true);
    const bar = $('#basinZoom'); if (!bar || bar.childElementCount) return;
    const mk = (label, title, fn) => { const b = h('button', { text: label, title }); b.onclick = fn; bar.appendChild(b); };
    mk('−', 'zoom out', () => zoomAbout(1 / 1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('+', 'zoom in', () => zoomAbout(1.5, view.x + view.w / 2, view.y + view.h / 2));
    const sb = h('button', { id: 'basinStorm', text: '', title: 'zoom to the active storm' });
    sb.onclick = toStorm; sb.hidden = true; bar.appendChild(sb);
    mk('Whole basin', 'back to the whole basin', wholeBasin);
    const sw = h('button', { id: 'basinSst', text: 'Sea temperature', title: 'show or hide the sea surface temperature anomaly' });
    sw.classList.toggle('on', sstOn);
    sw.onclick = () => {
      sstOn = !sstOn; sw.classList.toggle('on', sstOn);
      try { localStorage.setItem('wx.hurricane.sst', sstOn ? 'on' : 'off'); } catch (e) { /* a private window keeps the default */ }
      draw();
    };
    bar.appendChild(sw);
    const rw = h('button', { id: 'basinRelief', text: 'Relief', title: 'show or hide the hill shading over the land' });
    rw.classList.toggle('on', reliefOn);
    rw.onclick = () => {
      reliefOn = !reliefOn; rw.classList.toggle('on', reliefOn);
      try { localStorage.setItem('wx.hurricane.relief', reliefOn ? 'on' : 'off'); } catch (e) { /* a private window keeps the default */ }
      if (reliefOn) loadRelief(basin); else draw();
    };
    bar.appendChild(rw);
    const fw = h('button', { id: 'basinRoads', text: 'Roads and cities', title: 'show or hide the main roads and city names' });
    fw.classList.toggle('on', featOn);
    fw.onclick = () => {
      featOn = !featOn; fw.classList.toggle('on', featOn);
      try { localStorage.setItem('wx.hurricane.roads', featOn ? 'on' : 'off'); } catch (e) { /* a private window keeps the default */ }
      if (featOn) loadFeatures(basin); else draw();
    };
    bar.appendChild(fw);
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'basinZoomLevel', text: 'whole basin' }));
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: '· scroll to zoom, drag to pan' }));
  }

  /* ---- the sea surface temperature anomaly

     NOAA Coral Reef Watch's daily anomaly, the day's satellite SST less its own
     daily climatology at each pixel (pipeline/sst.py), drawn under the land so
     only the sea shows it. A hurricane draws its energy from the upper ocean,
     so warmer than usual water along a track is worth seeing. The scale is
     diverging and its middle is clear: water within half a degree of usual
     leaves the sea its own colour, warmer reads orange to red and cooler light
     to deep blue, and the wash stays light so the contracts on the land and the
     red reference locations still read over it. The browser smooths the 0.2 degree cells when it scales the
     image. Hovering the sea reads the cell under the pointer. */
  const SST_CACHE = {};
  const SST_SPAN = 4;                    // degrees Celsius at the ends of the scale
  const WARM = [[253, 219, 199], [244, 165, 130], [214, 96, 77], [178, 24, 43], [103, 0, 31]];
  const COOL = [[209, 229, 240], [146, 197, 222], [67, 147, 195], [33, 102, 172], [5, 48, 97]];
  function sstColor(a) {
    const m = Math.min(1, Math.abs(a) / SST_SPAN);
    const stops = a >= 0 ? WARM : COOL;
    const f = m * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(f)), t = f - i;
    const c = stops[i].map((v, j) => Math.round(v + (stops[i + 1][j] - v) * t));
    const alpha = Math.abs(a) < 0.5 ? 0 : Math.min(0.6, 0.1 + 0.55 * m);
    return [c[0], c[1], c[2], Math.round(255 * alpha)];
  }
  function sstImage(gr) {
    const key = basin + '|' + (SST && SST.asof) + '|' + gr.nx + 'x' + gr.ny;
    if (SST_CACHE[key]) return SST_CACHE[key];
    const raw = atob(gr.data);
    const vals = new Int8Array(raw.length);
    for (let k = 0; k < raw.length; k++) vals[k] = raw.charCodeAt(k) << 24 >> 24;
    const cv = document.createElement('canvas');
    cv.width = gr.nx; cv.height = gr.ny;
    const ctx = cv.getContext('2d');
    const im = ctx.createImageData(gr.nx, gr.ny);
    for (let k = 0; k < vals.length; k++) {
      if (vals[k] === -128) continue;     // land, ice or missing: left clear
      const c = sstColor(vals[k] / 10);
      im.data[4 * k] = c[0]; im.data[4 * k + 1] = c[1]; im.data[4 * k + 2] = c[2]; im.data[4 * k + 3] = c[3];
    }
    ctx.putImageData(im, 0, 0);
    return (SST_CACHE[key] = { url: cv.toDataURL('image/png'), vals });
  }
  function sstDay() {
    const d = SST && SST.asof ? new Date(SST.asof + 'T12:00:00Z') : null;
    return d ? MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate() : '';
  }
  function drawSst(svg, X, Y, kx, ky, b0, la1, gr) {
    const img = sstImage(gr);
    const half = gr.step / 2;
    const node = el('image', { href: img.url, x: X(gr.lon0 - half), y: Y(gr.lat0 + half),
                               width: gr.nx * gr.step * kx, height: gr.ny * gr.step * ky,
                               preserveAspectRatio: 'none', class: 'sstlayer' });
    node.addEventListener('mousemove', ev => {
      const [x, y] = atPoint(ev);
      const lon = b0 + x / kx, lat = la1 - y / ky;
      const i = Math.round((gr.lat0 - lat) / gr.step), j = Math.round((lon - gr.lon0) / gr.step);
      if (i < 0 || j < 0 || i >= gr.ny || j >= gr.nx) { tip.hide(); return; }
      const v = img.vals[i * gr.nx + j];
      if (v === -128) { tip.hide(); return; }
      const c = v / 10;
      tip.show(ev, tip.rows('Sea surface temperature anomaly',
        [['Anomaly', (c > 0 ? '+' : '') + c.toFixed(1) + ' °C (' + (c > 0 ? '+' : '') + (c * 1.8).toFixed(1) + ' °F)'],
         ['Reads', c >= 0.5 ? 'warmer than usual' : c <= -0.5 ? 'cooler than usual' : 'near its usual temperature'],
         ['Position', position(lat, lon)], ['Day', sstDay()]],
        'NOAA Coral Reef Watch daily 5 km SST anomaly, against its daily climatology'));
    });
    node.addEventListener('mouseleave', () => tip.hide());
    svg.appendChild(node);
  }

  /* ---- the hill shading over the land

     NOAA NCEI's ETOPO 2022 relief, shaded once under a sun in the north-west
     (scripts/build_relief.py) and checked in as an asset per basin. Laid over
     the land as light and shadow only: flat ground is left clear, so the
     landfall shading keeps its colour and the coastal plains a storm crosses
     stay plain, while the Sierra Madre, the Appalachians and the islands' high
     ground read as terrain. No pointer events; the regions under it keep their
     tooltips and links. */
  function loadRelief(b) {
    if (!reliefOn || RELIEF[b] !== undefined) { if (reliefOn && RELIEF[b]) draw(); return; }
    RELIEF[b] = null;                       // asked for; a failure leaves the map plain
    fetch(WXC.asset('relief-' + b + '.json')).then(x => x.json()).then(d => {
      RELIEF[b] = d && d.data ? Object.assign(d, { url: reliefImage(d) }) : null;
      if (RELIEF[b] && basin === b) draw();
    }).catch(() => {});
  }
  function reliefImage(d) {
    const raw = atob(d.data);
    const cv = document.createElement('canvas');
    cv.width = d.nx; cv.height = d.ny;
    const ctx = cv.getContext('2d');
    const im = ctx.createImageData(d.nx, d.ny);
    for (let k = 0; k < raw.length; k++) {
      const v = raw.charCodeAt(k) << 24 >> 24;
      if (!v) continue;
      const lit = v > 0;
      const c = lit ? 255 : 0;
      im.data[4 * k] = c; im.data[4 * k + 1] = c; im.data[4 * k + 2] = c;
      im.data[4 * k + 3] = Math.round(255 * Math.min(1, Math.abs(v) / 127) * (lit ? 0.4 : 0.55));
    }
    ctx.putImageData(im, 0, 0);
    return cv.toDataURL('image/png');
  }
  function drawRelief(svg, X, Y, kx, ky, d) {
    const half = d.step / 2;
    svg.appendChild(el('image', { href: d.url, x: X(d.lon0 - half), y: Y(d.lat0 + half),
                                  width: d.nx * d.step * kx, height: d.ny * d.step * ky,
                                  preserveAspectRatio: 'none', class: 'relieflayer', 'pointer-events': 'none' }));
  }

  /* ---- roads and city names

     Natural Earth's 1:10m roads and populated places (scripts/build_map_features.py),
     an asset per basin. A reader following a storm towards a coast wants to
     know which towns and which highways are in its way, and the reference
     locations alone do not say. Both stay out of the way of the contracts: the
     roads are thin lines under the storm layers with no pointer events, the
     expressways and major highways from the storm's own view inward and the
     secondary highways only past three times; the names come after the storm
     is drawn, the largest cities first, more of them the closer the view and
     near the storm, each placed where it collides with no storm label, no
     reference dot and no other name. */
  function loadFeatures(b) {
    if (!featOn || FEAT[b] !== undefined) { if (featOn && FEAT[b]) draw(); return; }
    FEAT[b] = null;                         // asked for; a failure leaves the map without them
    fetch(WXC.asset('features-' + b + '.json')).then(x => x.json()).then(d => {
      FEAT[b] = d && d.roads ? d : null;
      if (FEAT[b] && basin === b) draw();
    }).catch(() => {});
  }
  function drawRoads(svg, X, Y, g, z, F) {
    if (z < 1.5) return;
    const [vx0, vy0, vx1, vy1] = [view.x, view.y, view.x + view.w, view.y + view.h];
    const d1 = [], d2 = [];
    F.roads.forEach(([cls, pts]) => {
      if (cls === 2 && z < 3) return;
      const xy = pts.map(p => [X(p[0]), Y(p[1])]);
      // a run wholly outside the window adds nothing but length to the path
      if (xy.every(q => q[0] < vx0) || xy.every(q => q[0] > vx1) || xy.every(q => q[1] < vy0) || xy.every(q => q[1] > vy1)) return;
      (cls === 1 ? d1 : d2).push('M' + xy.map(q => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join('L'));
    });
    if (d2.length) svg.appendChild(el('path', { d: d2.join(''), fill: 'none', stroke: 'var(--map-road2)', 'stroke-width': .55 * g,
                                                'stroke-linecap': 'round', 'pointer-events': 'none', class: 'roads2' }));
    if (d1.length) svg.appendChild(el('path', { d: d1.join(''), fill: 'none', stroke: 'var(--map-road)', 'stroke-width': .85 * g,
                                                'stroke-linecap': 'round', 'pointer-events': 'none', class: 'roads1' }));
  }
  function drawPlaces(g2, X, Y, g, z, F, taken, refs, focus) {
    // Natural Earth's ranks present in the asset, one more step down the list per
    // zoom tier, and one step further inside the box the storm view frames, where
    // the reader is placing the track rather than the continent
    const tiers = [2, 3, 4, 6, 8];
    const t = z < 1.5 ? 0 : z < 2.5 ? 1 : z < 4 ? 2 : z < 7 ? 3 : 4;
    const maxRank = tiers[t], maxNear = tiers[Math.min(t + 1, tiers.length - 1)];
    const boxes = taken.slice();            // the storm's labels and points, then each name as it is placed
    // A dot the vendor has signalled is never written over. An unsignalled dot is
    // small and grey, and a coast lined with them would otherwise leave Miami or
    // Tampa unnamed, so the largest cities' names may graze one when nothing else fits.
    const hard = (refs || []).filter(q => q[4]), soft = (refs || []).filter(q => !q[4]);
    const clear = (bx, list) => !list.some(q => bx[0] < q[2] && bx[2] > q[0] && bx[1] < q[3] && bx[3] > q[1]);
    const inside = bx => bx[0] >= view.x + 2 * g && bx[2] <= view.x + view.w - 2 * g
                         && bx[1] >= view.y + 2 * g && bx[3] <= view.y + view.h - 2 * g;
    const free = (bx, strict) => inside(bx) && clear(bx, boxes) && clear(bx, hard) && (!strict || clear(bx, soft));
    for (const [name, lon, lat, rank] of F.places) {
      if (rank > maxNear) break;                     // the asset lists the largest cities first
      const x = X(lon), y = Y(lat);
      if (x < view.x || x > view.x + view.w || y < view.y || y > view.y + view.h) continue;
      const near = !!focus && x >= focus[0] && x <= focus[2] && y >= focus[1] && y <= focus[3];
      if (rank > (near ? maxNear : maxRank)) continue;
      // a city at a reference location is marked by that location's dot, and its
      // name stands clear of the dot however large the chance has drawn it
      const ref = (refs || []).find(q => x >= q[0] - g && x <= q[2] + g && y >= q[1] - g && y <= q[3] + g);
      const off = Math.max(4 * g, (ref ? (ref[2] - ref[0]) / 2 : 1.7 * g) + 2 * g);
      const size = (rank <= 2 ? 10.5 : 9.5) * g, w = String(name).length * size * 0.55;
      // right, left, above, below, then the same a little further out, where a
      // coast of dots leaves no room close in
      const ring = o => [[o, 3.5 * g, 'start'], [-o, 3.5 * g, 'end'], [0, -o - 2 * g, 'middle'], [0, o + 9 * g, 'middle']];
      const spots = ring(off).concat(ring(off + 3 * g));
      let pick = null;
      for (const strict of rank <= 2 ? [true, false] : [true]) {
        for (const [dx, dy, anchor] of spots) {
          const ax = x + dx, ay = y + dy;
          const x0 = anchor === 'end' ? ax - w : anchor === 'middle' ? ax - w / 2 : ax;
          const bx = [x0 - g, ay - size * 0.9, x0 + w + g, ay + size * 0.3];
          if (free(bx, strict)) { pick = { ax, ay, anchor, bx }; break; }
        }
        if (pick) break;
      }
      if (!pick) continue;
      boxes.push(pick.bx, [x - 2 * g, y - 2 * g, x + 2 * g, y + 2 * g]);
      if (!ref) g2.appendChild(el('circle', { cx: x, cy: y, r: 1.7 * g, fill: 'var(--map-place)' }));
      g2.appendChild(txt(name, { x: pick.ax, y: pick.ay, 'text-anchor': pick.anchor, 'font-size': size,
                                 fill: 'var(--map-place)', class: 'lbl plbl', style: 'stroke-width:' + (2.6 * g).toFixed(2) + 'px' }));
    }
  }

  /* ---- a satellite loop for each storm on view

     NOAA/NESDIS STAR's GOES floater follows each storm NHC is tracking, and the
     pipeline copies its newest three hours of GeoColor frames into this site's
     bucket every half hour (pipeline/floater.py; owner, 2026-10-10). Each storm in
     the basin on view gets a card under the map; WXC.satLoop draws and plays it. */
  let FLOAT = null;                    // snapshots/floater/index.json
  let loopStops = [];
  const loopUrl = f => WXD.base() + '/snapshots/' + f.key;
  function loopCard(sid, s, L) {
    const lp = WXC.satLoop({
      frames: L.frames.map(f => ({ t: f.t, src: loopUrl(f) })), width: 500, height: 500,
      head: (s.name || sid) + (s.intensityKt != null ? ' \u00b7 ' + windText(s.intensityKt, s.classification, null, 'mid') : ''),
      sub: WXC.loopSub(L.satellite, L.frames), credit: 'Imagery from NOAA/NESDIS STAR',
      alt: L.satellite + ' GeoColor satellite loop of ' + (s.name || sid), attrs: { 'data-storm': sid } });
    loopStops.push(lp.stop);
    return lp.card;
  }
  function drawLoops() {
    const host = $('#stormLoops'); if (!host) return;
    loopStops.forEach(f => f()); loopStops = [];
    host.innerHTML = '';
    const idx = (FLOAT && FLOAT.storms) || {};
    const here = stormsHere().map(s => [String(s.id || '').toUpperCase(), s])
      .filter(([sid]) => idx[sid] && WXC.loopFresh(idx[sid].frames));
    host.hidden = !here.length;
    if (!here.length) return;
    host.appendChild(h('div', { class: 'loops' }, here.map(([sid, s]) => loopCard(sid, s, idx[sid]))));
  }

  function draw() {
    const B = BASINS[basin];
    const [b0, la0, b1, la1] = B.box, W = 980, Hh = 600;
    const kx = W / (b1 - b0), ky = Hh / (la1 - la0);
    const X = lon => (lon - b0) * kx, Y = lat => (la1 - lat) * ky;
    const svg = $('#basin'); svg.innerHTML = '';
    if (autoView) view = stormView() || Object.assign({}, VIEW0);
    const g = GS();          // glyph scale at the current zoom, 1 at whole basin
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: Hh, fill: 'var(--map-sea)' }));
    const sstGrid = sstOn && SST && SST.basins ? SST.basins[basin] : null;
    if (sstGrid) drawSst(svg, X, Y, kx, ky, b0, la1, sstGrid);
    const lf = landfallQuotes();
    const hlf = market('HLF');
    let shadedHere = 0;      // regions drawn on this view that carry a listed contract
    const poly = (rr, fill, stroke, sw, html, url, nm) => {
      const p = el('path', { d: rr.map(r => 'M' + r.map(q => X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join('L') + 'Z').join(' '), fill, stroke, 'stroke-width': sw });
      if (html) attach(p, html);
      // a shaded region is a contract: clicking the map opens the one it stands for
      if (url) WXM.linkTo(p, url, 'Open the ' + (nm || 'landfall') + ' contract on IBKR');
      svg.appendChild(p);
    };
    // fill and tooltip for a region: shaded by the landfall contract's Yes price where one is listed with bids
    const fillFor = (label, nm) => {
      const c = lf ? lf[label] : null;
      if (c) shadedHere++;
      const season = c && c.expiryLabel ? c.expiryLabel : (hlf && hlf.contracts[0] && hlf.contracts[0].expiryLabel) || 'season';
      const rows = c ? [['Landfall contract (' + esc(season) + ')', priced(c) ? 'Yes ' + pct(cents(c.mid)) : noPrice(c)]].concat(c.mid == null ? [] : quoteRows(c).filter(r => r[0] !== 'Yes price' && r[0] !== 'Settles')).concat([['As of', exAsof()]]) : [];
      const url = c && hlf ? WXM.contractUrl(hlf.productConid, c.conidYes || c.conid) : null;
      const html = tip.rows(esc(nm), rows,
        c ? (url ? 'click to open this contract' : null) : (lf ? 'no landfall contract listed for this region' : null));
      return [priced(c) ? ramp(c.mid) : 'var(--map-land)', html, url];
    };
    if (GEO) {
      // a region is drawn only where some part of it is inside the view, so the
      // Pacific view does not carry invisible, focusable copies of the Caribbean
      const inView = rr => rr.some(r => r.some(q => q[0] >= b0 && q[0] <= b1 && q[1] >= la0 && q[1] <= la1));
      // a country is shaded on the views its contract is listed on, so a country that
      // faces both oceans is priced on both, and one that faces only the other ocean is
      // drawn as land with a pointer to the board that prices it
      Object.entries(GEO.countries || {}).forEach(([nm, rr]) => {
        if (!inView(rr)) return;
        const label = Object.keys(lf || {}).find(k => regionKey(k) === nm) || nm;
        if (regionHere(label)) { const [f, t, u] = fillFor(label, nm); poly(rr, f, 'var(--map-line)', .6 * g, t, u, nm); return; }
        const listed = lf && lf[label];
        poly(rr, 'var(--map-land)', 'var(--map-line)', .6 * g,
             tip.rows(esc(nm), [], listed ? 'its landfall contract is priced on the ' + (regionBasins(label).indexOf('AL') >= 0 ? 'Atlantic' : 'Pacific') + ' view' : null));
      });
      (NATION || []).forEach(r => poly([r], 'var(--map-land)', 'var(--map-line)', .6 * g));
      // a state or county is drawn on its own view only
      Object.entries(GEO.states || {}).forEach(([nm, rr]) => { if (!regionHere(nm)) return; const [f, t, u] = fillFor(nm, nm); poly(rr, f, 'var(--map-line)', .7 * g, t, u, nm); });
      // The geometry carries every coastal county, because the desk names new ones storm by
      // storm, so a county is drawn only where the exchange lists it, in the dark outline
      // that marks a named county over its state.
      const listedHere = new Set(Object.keys(lf || {}).map(regionKey));
      Object.entries(GEO.counties || {}).forEach(([nm, rr]) => {
        if (!regionHere(nm) || !listedHere.has(nm)) return;
        const [f, t, u] = fillFor(nm, nm); poly(rr, f, 'var(--ink)', .8 * g, t, u, nm);
      });
    }
    if (reliefOn && RELIEF[basin]) drawRelief(svg, X, Y, kx, ky, RELIEF[basin]);
    const zf = VIEW0.w / view.w;
    if (featOn && FEAT[basin]) drawRoads(svg, X, Y, g, zf, FEAT[basin]);
    const counts = drawNhc(svg, X, Y, basin, g);
    // the names sit under the reference dots but are placed after them, so none is drawn beneath one
    const plg = featOn && FEAT[basin] ? svg.appendChild(el('g', { class: 'places', 'pointer-events': 'none' })) : null;
    const refBoxes = [];
    // the reference locations: small dots, sized by the vendor's chance of a gust at the
    // threshold the storm has made telling (sizeThreshold) when the lane is live
    let vendorShown = 0;
    sizeT = sizeThreshold(((GEO && GEO.locations) || []).filter(L => L.lon >= b0 && L.lon <= b1 && L.lat >= la0 && L.lat <= la1));
    // the locations that have a delivery series loaded: those are the dots that
    // open one, which is not the same set as those with a current livecyc row
    const withSeries = (window.WXStorm && WXStorm.sites) ? WXStorm.sites() : {};
    (GEO && GEO.locations || []).forEach(L => {
      if (L.lon < b0 || L.lon > b1 || L.lat < la0 || L.lat > la1) return;
      const v = vendorSite(L.id);
      // a location the interim settles high is a signal even when the newest
      // cycle has moved on and reads zero there
      const any = !!(v && v.some(x => x.p.some(p => p > 0) || (x.interim && x.interim.p.some(p => p > 0))));
      const pT = chanceAt(v, sizeT);
      const r = (pT > 0 ? 3 + 9 * Math.sqrt(Math.min(pT, 100) / 100) : (any ? 3 : 2.2)) * g;
      if (any) vendorShown++;
      refBoxes.push([X(L.lon) - r, Y(L.lat) - r, X(L.lon) + r, Y(L.lat) + r, any]);
      const c = el('circle', { cx: X(L.lon), cy: Y(L.lat), r, fill: any ? 'rgba(192,57,43,.75)' : 'var(--muted)', 'fill-opacity': any ? .9 : .55,
                               stroke: any ? '#fff' : 'var(--panel)', 'stroke-width': (any ? 1 : .6) * g,
                               'data-loc': L.id, class: L.id === sideSid ? 'msel' : null });
      attach(c, dotTip(L, v));
      if (any || withSeries[L.id]) {
        c.addEventListener('mouseenter', () => showSide(L.id));
        c.addEventListener('focus', () => showSide(L.id));
      }
      if (any || withSeries[L.id]) {
        // A click pins the location's box, whose prices then open the contract
        // on IBKR (owner, 2026-10-08), and fills the panel beside the map; the
        // pin is attach()'s own handler. The key does the same for a reader
        // without a pointer.
        c.style.cursor = 'pointer';
        c.setAttribute('role', 'button');
        c.setAttribute('tabindex', '0');
        c.setAttribute('aria-label', L.name + ' \u2014 show its contract');
        c.addEventListener('click', () => showSide(L.id, true));
        c.addEventListener('keydown', ev => {
          if (ev.key !== 'Enter' && ev.key !== ' ') return;
          ev.preventDefault();
          const b = c.getBoundingClientRect();
          tip.pin({ clientX: b.left + b.width / 2, clientY: b.top + b.height / 2 }, dotTip(L, v));
          showSide(L.id, true);
        });
      }
      svg.appendChild(c);
    });
    if (plg) {
      const sv = stormView();
      drawPlaces(plg, X, Y, g, zf, FEAT[basin], counts.boxes || [], refBoxes, sv && sv.focus);
    }
    wireZoom(); applyView(); sideFrame();
    $('#modeTitle').textContent = B.name.toUpperCase() + ' · NHC forecast tracks, cones and formation odds' + (shadedHere ? ' · landfall regions shaded by the exchange’s Yes price' : '');
    /* The map's caption, kept to what the key and the boxes cannot say (owner,
       2026-10-08): that a basin has no storm, why a storm's cone is missing,
       and where each layer comes from, naming only the layers on view. What a
       shade means is in the key, what a contract pays is in its box and in the
       landfall section, and what a click does is in the box it pins. */
    {
      const cap = $('#basinCap'); cap.innerHTML = '';
      const sources = ['Storms, cones and outlooks from the National Hurricane Center']
        .concat(sstGrid ? ['sea temperature from NOAA Coral Reef Watch'] : [])
        .concat(reliefOn && RELIEF[basin] ? ['relief from NOAA NCEI ETOPO 2022'] : [])
        .concat(featOn && FEAT[basin] ? ['roads and cities from Natural Earth'] : []);
      cap.appendChild(h('p', { class: 'cap', style: 'margin:8px 2px 0', text:
        (counts.storms ? '' : 'No active tropical cyclones in this basin at the last update. ')
        + ((counts.stale || []).length
            ? counts.stale.join(' and ') + (counts.stale.length > 1 ? ' are' : ' is')
              + ' drawn at the position on the latest advisory: the cone service has stopped updating '
              + (counts.stale.length > 1 ? 'them' : 'it') + ', so the superseded cone and forecast track are not shown. '
            : '')
        + sources.join(', ') + '.' }));
      // the map's landfall regions and the panel beside it open their contracts; the
      // line goes after the caption, which stays the one short paragraph it is
      WXM.tradeNote($('#mapWrap'), cap);
    }
    /* The key, under the map rather than on it, where it can never sit on a
       storm's track: one item per thing the map is showing now. */
    {
      const key = $('#basinKey');
      if (key) {
        key.innerHTML = '';
        const scale = (stops, a, b) => h('span', { class: 'bkramp' }, [h('span', { class: 'bkend', text: a }),
          h('i', { class: 'bkbar', style: 'background:linear-gradient(90deg,' + stops.join(',') + ')' }),
          h('span', { class: 'bkend', text: b })]);
        if (sstGrid) {
          const st = [];
          for (let i = 0; i <= 16; i++) {
            const c = sstColor(-SST_SPAN + 2 * SST_SPAN * i / 16);
            st.push(c[3] ? 'rgba(' + c.slice(0, 3).join(',') + ',' + (c[3] / 255).toFixed(2) + ')' : 'var(--map-sea)');
          }
          key.appendChild(h('span', { class: 'bkitem' }, [h('b', { text: 'Sea temperature anomaly, ' + sstDay() + ' ' }),
            scale(st, '−' + SST_SPAN + ' °C', '+' + SST_SPAN + ' °C')]));
        }
        if (shadedHere) {
          const st = [];
          for (let i = 0; i <= 8; i++) st.push(ramp(i / 8));
          key.appendChild(h('span', { class: 'bkitem' }, [h('b', { text: 'Landfall contract, Yes price ' }), scale(st, '0¢', '100¢')]));
        }
        key.appendChild(h('span', { class: 'bkitem' }, [h('i', { class: 'bkdot' }), h('span', { text: 'a reference location' })]));
        if (vendorShown) key.appendChild(h('span', { class: 'bkitem' }, [h('i', { class: 'bkdot live' }),
          h('span', { text: 'sized by the chance of a gust of ' + sizeT + ' mph or more' })]));
      }
    }
  }

  // ---- active storms with NHC wind speed probabilities
  // the count and Category 4 contracts are Atlantic and United States contracts,
  // so the Pacific view says so instead of showing an empty section. The landfall
  // board is on whichever view its regions are on: the Atlantic view always, and
  // the Pacific view once the exchange lists a Pacific region such as Hawaii.
  function basinSections() {
    const only = $('#atlanticOnly'), note = $('#pacificNote'), lfs = $('#landfallSect');
    if (only) only.style.display = basin === 'AL' ? '' : 'none';
    const here = landfallHere().length;
    if (lfs) lfs.style.display = (basin === 'AL' || here || expectLandfall().length) ? '' : 'none';
    if (note) {
      note.style.display = basin === 'AL' ? 'none' : '';
      note.textContent = 'The season count and Category 4 contracts on this exchange are Atlantic and United States '
        + 'contracts, so they are shown on the Atlantic view. '
        + (here ? 'The landfall contracts on Pacific regions are listed above; the rest are on the Atlantic view.'
                : (expectLandfall().length
                    ? 'No landfall contract names a Pacific region yet. What the exchange is expected to list is above, without a price; the contracts it has listed are on the Atlantic view.'
                    : 'No landfall contract names a Pacific region at the moment, so the landfall board is on the Atlantic view too.'));
    }
  }

  // the same split the map uses: the Atlantic view is AL, the Pacific view is
  // everything else, so a storm appears under the basin it is actually in
  const stormsHere = () => ((H && H.storms) || []).filter(s => (basin === 'AL' ? s.basin === 'AL' : s.basin !== 'AL'));

  /* The forecaster's reasoning, for the basin being looked at.

     A reader on the Atlantic view is not asking about an East Pacific storm,
     and the discussions are long enough that carrying both would bury the one
     they came for. */
  function drawDiscussion() {
    if (window.WXDiscussion) WXDiscussion.drawStorms(stormsHere(), BASINS[basin].name);
  }

  function drawStorms() {
    const list = $('#storms'); list.innerHTML = '';
    const here = stormsHere();
    if (!here.length) {
      list.appendChild(h('p', { class: 'cap', text: 'No active storms in this basin at the last update.' }));
    } else {
      list.appendChild(scheduleLine('Next full NHC advisory typically', [3, 9, 15, 21],
        'intermediate and special advisories can come sooner'));
    }
    here.slice().sort((a, b) => (Date.parse(b.updated || '') || 0) - (Date.parse(a.updated || '') || 0)).forEach(s => {
      list.appendChild(h('div', { class: 'stormrow' }, [
        h('b', { text: s.classification + ' ' + s.name }),
        h('span', { text: s.basin + ' · ' + windText(s.intensityKt, s.classification, stormNow(s)) + ' · ' + (s.pressureMb || '--') + ' mb · advisory ' + s.advisory + (s.geometryAdvisory && String(s.geometryAdvisory).replace(/^0+/, '') !== String(s.advisory).replace(/^0+/, '') ? ' (map shows ' + s.geometryAdvisory + ')' : '') }),
        s.advisoryUrl ? h('a', { href: s.advisoryUrl, text: 'NHC advisory', target: '_blank', rel: 'noopener' }) : h('span'),
        s.windProbsUrl ? h('a', { href: s.windProbsUrl, text: 'wind speed probabilities', target: '_blank', rel: 'noopener' }) : h('span'),
      ]));
      if (s.windProbs && s.windProbs.length) {
        const tb = h('table', { class: 'pws' });
        // the product is issued in knots; the page reads in mph, with the
        // strength each threshold defines where it defines one: 34 kt is the
        // 39 mph of tropical-storm force, 64 kt the 74 mph of hurricane force,
        // and 50 kt (58 mph) is not a category boundary
        const thr = (v, name) => h('th', { class: 'num' }, name ? [v, h('br'), h('span', { class: 'pwsn', text: name })] : [v]);
        tb.appendChild(h('tr', {}, [h('th', { text: 'NHC five-day cumulative probability, sustained winds' }), thr('≥39 mph', 'tropical-storm force'), thr('≥58 mph'), thr('≥74 mph', 'hurricane force')]));
        s.windProbs.slice(0, 14).forEach(r => {
          const tr = h('tr', {}, [h('td', { text: r.location }), h('td', { class: 'num', text: r.p34 + '%' }), h('td', { class: 'num', text: r.p50 + '%' }), h('td', { class: 'num', text: r.p64 + '%' })]);
          attach(tr, tip.rows(esc(r.location) + ' — NHC wind speed probabilities',
            [['≥39 mph, tropical-storm force', r.p34 != null ? r.p34 + '%' : '—'], ['≥58 mph', r.p50 != null ? r.p50 + '%' : '—'], ['≥74 mph, hurricane force', r.p64 != null ? r.p64 + '%' : '—']],
            'sustained winds, cumulative through the 5-day forecast, NHC PWSAT, advisory ' + esc(s.advisory)));
          tb.appendChild(tr);
        });
        list.appendChild(h('div', { class: 'card', style: 'padding:0;margin:6px 0 0' }, [tb]));
        list.appendChild(h('p', { class: 'cap', style: 'margin:4px 0 12px', text: 'These are the storm’s sustained winds, a one-minute average, at the strengths that define a tropical storm (39 mph), storm-force winds (58 mph) and a hurricane (74 mph), reaching each place within five days. The Reask LiveCyc figures above price a different quantity, the peak gust at a reference location, so the two are not comparable number for number.' }));
      }
    });
    if (!(H.storms || []).length) list.appendChild(h('div', { class: 'cap', text: 'No active tropical cyclones in the NHC roster.' }));
  }

  // ---- season tiles and the count ladders
  function tile(label, value, sub, html, url) {
    const t = h('div', { class: 'tile' }, [h('div', { class: 'tv', text: value == null ? '—' : String(value) }), h('div', { class: 'tl', text: label }), sub ? h('div', { class: 'ts', text: sub }) : h('span')]);
    if (html) attach(t, html);
    if (url) { t.classList.add('lnk'); WXM.linkTo(t, url, 'Open ' + label + ' on IBKR'); }
    return t;
  }
  // `prod` is the market's product id on the exchange, which a contract link
  // needs alongside the contract's own conid; without it the rows are drawn
  // exactly as before rather than linking somewhere guessed

  function ladderPanel(title, rows, sub, mname, prod) {
    const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: title })]);
    if (sub) div.appendChild(h('div', { class: 'cap', style: 'margin:0 0 6px', text: sub }));
    rows.forEach(r => {
      const y = yes(r.c);
      const one = oneSided(r.c);
      const bar = h('div', { class: 'lrow' + (one ? ' one' : '') }, [
        h('span', { class: 'lk', text: r.label }),
        h('span', { class: 'lb' }, [h('i', { style: 'width:' + (y == null ? 0 : y) + '%' })]),
        h('span', { class: 'lv', text: yesText(r.c) }),
      ]);
      const url = r.c && WXM.contractUrl(prod, r.c.conidYes || r.c.conid);
      if (r.c) attach(bar, priceHead(r.c) + tip.rows(contractTitle({ name: mname }, r.c),
        quoteRows(r.c),
        asofFoot() + (url ? ' · click either side to open this strike on IBKR' : '')));
      if (url) WXM.linkTo(bar, url, 'Open ' + r.label + ' on IBKR');
      div.appendChild(bar);
    });
    if (!rows.length) div.appendChild(h('div', { class: 'cap', text: 'Not listed.' }));
    return div;
  }
  // ---- the category 4 landfall board, on its own rather than mixed in with
  // the season's counts: it asks a different question from them, and it is the
  // one contract here where a higher category does not also count.
  function drawCat4(m) {
    const host = $('#cat4'); if (!host) return;
    host.innerHTML = '';
    if (!m) { host.appendChild(h('p', { class: 'cap', text: WXM.on() ? 'Not in the quote snapshot.' : 'The market layer is off.' })); return; }
    const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: (m.name || 'Category 4 landfall') + ' (' + m.symbol + ')' })]);
    // nearest expiration on top: the dates are what separates these contracts,
    // and a string sort on the spec put November 2026 above September 2026
    m.contracts.slice().sort((a, b) => String(a.expiration || a.spec).localeCompare(String(b.expiration || b.spec))).forEach(c => {
      const y = yes(c);
      const one = oneSided(c);
      const bar = h('div', { class: 'lrow' + (one ? ' one' : '') }, [
        h('span', { class: 'lk', text: (c.label || '').replace(/^By /, 'by ') }),
        h('span', { class: 'lb' }, [h('i', { style: 'width:' + (y == null ? 0 : y) + '%' })]),
        h('span', { class: 'lv', text: yesText(c) }),
      ]);
      const url = WXM.contractUrl(m.productConid, c.conidYes || c.conid);
      attach(bar, priceHead(c) + tip.rows(contractTitle(m, c),
        quoteRows(c).filter(r => r[0] !== 'Settles').concat([['Expiration', expDate(c.expiration)]]),
        asofFoot() + (url ? ' · click either side to open this strike on IBKR' : '')));
      if (url) WXM.linkTo(bar, url, 'Open ' + c.label + ' on IBKR');
      div.appendChild(bar);
    });
    const chart = cat4Chart(m);
    if (chart) { host.appendChild(h('div', { class: 'card' }, [chart])); }
    host.appendChild(div);
    host.appendChild(h('p', { class: 'cap', text: 'A Yes pays if a hurricane makes landfall in the United States at exactly '
      + 'Category 4 on or before the date named.' }));
    WXM.tradeNote(host);
  }

  // ---- how much of the season's chance is still ahead
  //
  // The contract asks whether at least one qualifying landfall has happened by
  // a date, so the curve is the share of past seasons whose FIRST such landfall
  // had happened by that date, conditioned on none having happened yet this
  // year. That conditioning is what makes it comparable with a price today: the
  // probability that is left, not the probability the season started with.
  //
  // The second curve scales the same climatology by what the hurricane-count
  // market implies about this season against an average one. It is the market's
  // own view of the season's activity applied to the landfall rate, not a
  // forecast of this site's.
  function cat4Chart(m) {
    const cl = SZN && SZN.cat4;
    if (!cl || !(cl.cumulative || []).length) return null;
    const cum = {};
    cl.cumulative.forEach(([k, v]) => { cum[k] = v; });
    const now = new Date();
    const mmdd = d => String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
    const today = mmdd(now);
    const F0 = cum[today];
    if (F0 == null || F0 >= 1) return null;
    // the season's remaining window: today to the end of November
    const days = cl.cumulative.filter(([k]) => k >= today && k <= '11-30');
    if (days.length < 10) return null;
    const cond = f => (f - F0) / (1 - F0);
    // the count market's view of this season against an average one
    const hc = market('HCAB');
    const clim = ((SZN.climatology || {}).totals || {}).hurricanes;
    let factor = null;
    if (hc && clim) {
      const im = impliedCount(hc);
      if (im != null) factor = Math.max(0.2, Math.min(im / clim, 3));
    }

    const W = 960, Hh = 300, L = 52, R = 900, T = 20, B = 234;
    const yr = String((H.season || {}).year || now.getUTCFullYear());
    const here = (m.contracts || []).filter(c => String(c.expiration || '').slice(0, 4) === yr && priced(c));
    const ymax = Math.max(0.06, cond(cum['11-30']) * (factor && factor > 1 ? factor : 1) * 1.25,
                          ...here.map(c => c.mid * 1.15));
    const x = i => L + (i / (days.length - 1)) * (R - L);
    const y = p => B - (p / ymax) * (B - T);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + Hh, class: 'cpanel' });
    [0, 0.25, 0.5, 0.75, 1].forEach(f => {
      const v = ymax * f;
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(Math.round(v * 100) + '%', { x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    });
    const line = (scale, col, dash) => {
      const vy = i => y(Math.min(1 - Math.pow(1 - cond(days[i][1]), scale), ymax));
      const d = days.map((_, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + vy(i).toFixed(1)).join('');
      svg.appendChild(el('path', Object.assign({ d, fill: 'none', stroke: col, 'stroke-width': 2,
                                                 'pointer-events': 'none' }, dash ? { 'stroke-dasharray': dash } : {})));
      // one forecast per day: the value between two days is not something the
      // vendor issued, so the days themselves are marked
      const gap = days.length > 1 ? Math.abs(x(days.length - 1) - x(0)) / (days.length - 1) : 0;
      if (gap >= 5) days.forEach((_, i) => svg.appendChild(el('circle',
        { class: 'rdot', cx: x(i).toFixed(1), cy: vy(i).toFixed(1), r: Math.min(2.6, gap / 4),
          fill: col, 'pointer-events': 'none' })));
    };
    /* Each curve named where it ends.

       Both run the width of the panel and finish well separated, the scaled
       one always below the raw climatology, so the right-hand end holds the
       names without a key underneath. */
    const taken = [];
    const endLabel = (scale, col, nm) => {
      const i = days.length - 1;
      const v = cond(cum[days[i][0]] != null ? cum[days[i][0]] : cum['11-30']);
      const yy = y(Math.min(1 - Math.pow(1 - v, scale), ymax));
      const w = nm.length * 5.3;
      taken.push({ x0: R - 4 - w, x1: R - 4, y0: yy - 13, y1: yy - 1 });
      svg.appendChild(txt(nm, { x: R - 4, y: yy - 5, 'text-anchor': 'end', 'font-size': 10,
                                'font-weight': 700, fill: col, 'pointer-events': 'none' }));
    };
    line(1, 'var(--cool)', null);
    endLabel(1, 'var(--cool)', 'climatology ' + cl.window[0] + '-' + cl.window[1]);
    if (factor) { line(factor, 'var(--warm)', '5 4'); endLabel(factor, 'var(--warm)', 'scaled by the count market ×' + factor.toFixed(2)); }
    // today, where the whole remaining chance still sits
    svg.appendChild(el('line', { x1: L, x2: L, y1: T, y2: B, stroke: 'var(--muted)', 'stroke-dasharray': '4 3' }));
    svg.appendChild(txt('today', { x: L + 4, y: T + 10, class: 'ax' }));
    // month ticks
    let lastM = null;
    days.forEach(([k], i) => {
      if (k.slice(0, 2) === lastM) return;
      lastM = k.slice(0, 2);
      svg.appendChild(txt(MONTHS[+k.slice(0, 2) - 1].slice(0, 3), { x: x(i), y: B + 15, 'text-anchor': 'middle', class: 'ax' }));
    });
    // the listed contracts, at their own dates
    // only this season's dates belong on this season's remaining-window curve;
    // a contract for next year is a different question and stays in the ladder
    const thisYear = String((H.season || {}).year || now.getUTCFullYear());
    const dotAt = [];
    (m.contracts || []).forEach(c => {
      const e = String(c.expiration || '');
      if (e.length < 8 || e.slice(0, 4) !== thisYear) return;
      const k = e.slice(4, 6) + '-' + e.slice(6, 8);
      const i = days.findIndex(([d]) => d >= k);
      if (i < 0 || !priced(c)) return;
      const dot = el('circle', { cx: x(i), cy: y(Math.min(c.mid, ymax)), r: 4.5, fill: 'var(--accent)', 'pointer-events': 'all' });
      const f = cond(cum[k] != null ? cum[k] : cum['11-30']);
      attach(dot, priceHead(c) + tip.rows(c.label || 'contract', [
        ['Yes price', Math.round(c.mid * 100) + '¢'],
        ['Climatology, from today', (f * 100).toFixed(1) + '%'],
        [factor ? 'Scaled by the count market' : null, factor ? ((1 - Math.pow(1 - f, factor)) * 100).toFixed(1) + '%' : null],
        ['Difference to climatology', ((c.mid - f) * 100 > 0 ? '+' : '') + ((c.mid - f) * 100).toFixed(1) + ' points'],
      ], 'the climatology is the share of past seasons whose first qualifying landfall fell in this window'));
      svg.appendChild(dot);
      dotAt.push({ x: x(i), y: y(Math.min(c.mid, ymax)), label: c.label || 'contract' });
    });
    /* The curves carry their own names, and each dot carries its date.

       The label used to read "a listed contract", which named the kind of mark
       rather than the contract under it; the date is what tells one strike from
       the next, and it is what the ladder underneath is keyed on. A label at
       either end of the panel is anchored inward so it stays inside the frame. */
    dotAt.forEach(d => {
      const anchor = d.x > R - 46 ? 'end' : d.x < L + 46 ? 'start' : 'middle';
      const lx = d.x + (anchor === 'end' ? 5 : anchor === 'start' ? -5 : 0);
      const w = d.label.length * 5.3;
      const x0 = anchor === 'end' ? lx - w : anchor === 'start' ? lx : lx - w / 2;
      // above the dot unless a curve's own name is already there, in which case
      // below, so a strike near the end of the panel does not print over one
      const hits = ly => taken.some(a => !(x0 + w < a.x0 || x0 > a.x1 || ly < a.y0 || ly - 12 > a.y1));
      const ly = hits(d.y - 9) ? d.y + 17 : d.y - 9;
      taken.push({ x0, x1: x0 + w, y0: ly - 12, y1: ly });
      svg.appendChild(txt(d.label, { x: lx, y: ly, 'text-anchor': anchor, 'font-size': 10,
                          'font-weight': 700, class: 'lbl', fill: 'var(--accent)', 'pointer-events': 'none' }));
    });
    return svg;
  }
  // where the count ladder crosses fifty cents, which is the market's median
  function impliedCount(m) {
    const rows = (m.contracts || []).filter(c => priced(c) && c.numeric !== false)
      .map(c => [Number(c.strike), c.mid]).sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < rows.length; i++) {
      const [k0, p0] = rows[i - 1], [k1, p1] = rows[i];
      if (p0 >= 0.5 && p1 < 0.5 && p0 !== p1) return k0 + (p0 - 0.5) / (p0 - p1) * (k1 - k0);
    }
    return null;
  }

  function drawSeason() {
    const s = H.season || {};
    const tiles = $('#tiles'); tiles.innerHTML = '';
    const seasonTip = tip.rows((s.year || 'This') + ' season to date', [['Named storms', s.named], ['Hurricanes', s.hurricanes], ['Major hurricanes', s.majors]],
      ((s.names || []).length ? esc((s.names || []).join(', ')) + '<br>' : '') + 'from the ATCF best tracks, counted ' + esc(s.computedAt ? clockFull(Date.parse(s.computedAt), local()) + ' \u00b7 ' + dateShort(Date.parse(s.computedAt), local()) : (s.computed || '\u2014')));
    tiles.appendChild(tile('named storms, ' + (s.year || 'this season'), s.named, (s.names || []).join(', ') || 'none yet', seasonTip));
    tiles.appendChild(tile('hurricanes', s.hurricanes, 'from the ATCF best tracks', seasonTip));
    tiles.appendChild(tile('major hurricanes', s.majors, 'category 3 or stronger', seasonTip));
    const cat4 = market('HCAT4');
    drawCat4(cat4);
    const lad = $('#ladders'); lad.innerHTML = '';
    if (!MK) {
      $('#laddersCap').textContent = WXM.on() ? 'Exchange quotes unavailable.' : 'The market layer is off; no contract prices are shown.';
      return;
    }
    // one ladder per listing period (a market carries next season's contracts well ahead of time)
    const specNum = sp => sp.split('.').reduce((a, v, i) => a + (+v) * (i === 0 ? 10000 : (i === 1 ? 100 : 1)), 0);   // '2026.8' -> 20260800, '2026.12' -> 20261200
    const periods = sym => { const m = market(sym); if (!m) return []; return [...new Set(m.contracts.map(c => c.spec))].sort((a, b) => specNum(a) - specNum(b)).map(sp => ({ sp, label: (m.contracts.find(c => c.spec === sp) || {}).expiryLabel || sp, rows: m.contracts.filter(c => c.spec === sp).sort((a, b) => a.strike - b.strike).map(c => ({ label: c.label, c })) })); };
    const mname = sym => (market(sym) || {}).name || sym;
    const thisYear = String((H.season || {}).year || new Date().getUTCFullYear());
    // the current period of each count product gets the cumulative panel beside its
    // ladder; other listing periods (a following season, a later month) keep the
    // plain ladder, since the season they price has not started
    const thisMonth = new Date().getUTCMonth();
    COUNT_PANELS.forEach(cfg => {
      const ps = periods(cfg.sym);
      if (!ps.length) { lad.appendChild(ladderPanel(COUNT[cfg.sym] + ' (' + cfg.sym + ')', [], '')); return; }
      const isNow = p => cfg.monthly ? monthOfSpec(p.sp) === thisMonth : String(p.label).indexOf(thisYear) >= 0;
      ps.forEach(p => {
        if (isNow(p) && SZN) {
          const wrap = h('div', { class: 'cwrap' }, [h('div', { class: 'lt', text: cfg.title + ' in ' + p.label + ' (' + cfg.sym + ')' })]);
          wrap.appendChild(countPanel(cfg, p));
          wrap.appendChild(h('p', { class: 'cap', html: '<a href="allocator.html?m=' + encodeURIComponent('hur:' + cfg.sym)
            + '">Size a position on this ladder in the position allocation calculator →</a>' }));
          lad.appendChild(wrap);
        } else {
          lad.appendChild(ladderPanel(cfg.title + ', ' + p.label + ' (' + cfg.sym + ')', p.rows,
            cfg.step ? 'Yes price of “count above N”' : 'Yes price of “at least N”', mname(cfg.sym),
            (market(cfg.sym) || {}).productConid));
        }
      });
    });
    $('#laddersCap').textContent = '';
    WXM.tradeNote($('#ladders'), $('#laddersCap'));
  }

  // ---- the season's count so far, beside the ladder that prices it
  //
  // One panel per count product, on a shared vertical axis of storm count: on
  // the left how many have formed against the pace of an average season, on the
  // right the price of the Yes contract for "at least N" at each level, so the
  // two can be read across. The pace curve is the 1991-2020 climatological
  // formation calendar from the NHC best tracks; when a seasonal forecast total
  // is configured the curve is scaled to it, because a seasonal forecast is not
  // a government product and this site does not supply one by default.
  const COUNT_PANELS = [
    { sym: 'TROPA', key: 'named', title: 'Named storms', step: 1 },      // "Above N" pays at N+1
    { sym: 'HCAB', key: 'hurricanes', title: 'Hurricanes', step: 0 },    // "At Least N" pays at N
    { sym: 'MHCMA', key: 'majors', title: 'Major hurricanes', step: 1, monthly: true },
  ];
  const dstr = ms => MONTHS[new Date(ms).getUTCMonth()].slice(0, 3) + ' ' + new Date(ms).getUTCDate();

  // the climatology curve as [timestamp, cumulative mean] for the panel's window
  function climSeries(key, year, month) {
    const cl = SZN && SZN.climatology; if (!cl || !cl[key]) return null;
    const [m0, d0] = cl.start.split('-').map(Number);
    const t0 = Date.UTC(year, m0 - 1, d0);
    let pts = cl[key].map((v, i) => [t0 + i * 864e5, v]);
    if (month == null) return pts;
    // a monthly product: the cumulative count within that month alone
    const inM = pts.filter(p => new Date(p[0]).getUTCMonth() === month);
    if (!inM.length) return null;
    const before = pts.filter(p => p[0] < inM[0][0]).pop();
    const base = before ? before[1] : 0;
    return inM.map(p => [p[0], Math.round((p[1] - base) * 1000) / 1000]);
  }

  function countPanel(cfg, period) {
    const year = (H.season || {}).year || new Date().getUTCFullYear();
    const month = cfg.monthly ? monthOfSpec(period.sp) : null;
    const clim = climSeries(cfg.key, year, month);
    /* Formation dates from the same file as the count beside them.

       These used to come from the daily season job while the count above came
       from the half-hourly one, so on the day Dolly was named the card read
       four named storms and the chart under it read three. The hurricane job
       carries the dates now; the season snapshot is the fallback for a
       snapshot written before it did, and still the source of the climatology
       and the seasonal forecast this is drawn against. */
    const formed = (H.season || {}).events || (SZN && SZN.season) || {};
    const events = (formed[cfg.key] || [])
      .filter(e => month == null || new Date(e.date + 'T00:00:00Z').getUTCMonth() === month);
    const bars = period.rows.map(r => ({ n: r.c.strike + cfg.step, c: r.c })).filter(b => b.n != null).sort((a, b) => a.n - b.n);
    const fc = (SZN && SZN.forecast) || {};
    const cl = (SZN && SZN.climatology) || {};
    // the climatological pace for this window, and the same shape scaled to a
    // seasonal forecast total when one is configured. A monthly window scales by
    // the season's ratio, not its own, so August keeps its share of the season.
    const seasonTotal = (cl.totals || {})[cfg.key];
    const ratio = (fc[cfg.key] != null && seasonTotal) ? fc[cfg.key] / seasonTotal : null;
    const curve = clim;
    const fcurve = ratio && clim ? clim.map(p => [p[0], p[1] * ratio]) : null;
    const climTarget = month == null ? seasonTotal : (cl.monthlyMajors || {})[String(month + 1).padStart(2, '0')];
    const fcTarget = ratio != null && climTarget != null ? Math.round(climTarget * ratio * 100) / 100 : null;
    const target = fcTarget != null ? fcTarget : climTarget;

    const W = 960, Hh = 302, L = 52, R = 468, RL = 556, RR = 928, T = 30, B = 236;
    const ymax = Math.max(1, ...bars.map(b => b.n), events.length,
      curve ? Math.ceil(curve[curve.length - 1][1]) : 0, fcurve ? Math.ceil(fcurve[fcurve.length - 1][1]) : 0) + 1;
    const y = n => B - (n / ymax) * (B - T);
    const t0 = curve ? curve[0][0] : Date.UTC(year, 4, 1), t1 = curve ? curve[curve.length - 1][0] : Date.UTC(year, 10, 30);
    const x = t => L + ((t - t0) / (t1 - t0)) * (R - L);
    const px = p => RL + (p / 100) * (RR - RL);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + Hh, class: 'cpanel' });

    // shared count axis
    const stepN = ymax > 14 ? 2 : 1;
    for (let n = 0; n <= ymax; n += stepN) {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(n), y2: y(n), class: 'grid' }));
      svg.appendChild(el('line', { x1: RL, x2: RR, y1: y(n), y2: y(n), class: 'grid' }));
      svg.appendChild(txt(n, { x: L - 7, y: y(n) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }
    svg.appendChild(txt('count', { x: 14, y: (T + B) / 2, class: 'axl', transform: 'rotate(-90 14 ' + ((T + B) / 2) + ')', 'text-anchor': 'middle' }));

    // a hover band over the left plot: the pace and the count on any date. It goes
    // in before the curve and the storm dots, so a dot on top of it wins the pointer.
    const now0 = Date.now();
    const band = el('rect', { x: L, y: T, width: R - L, height: B - T, fill: 'transparent' });
    band.addEventListener('mousemove', e => {
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      const t = t0 + ((q.x - L) / (R - L)) * (t1 - t0);
      const cp = curve ? curve.reduce((a, p) => Math.abs(p[0] - t) < Math.abs(a[0] - t) ? p : a) : null;
      const formed = events.filter(ev => Date.parse(ev.date + 'T00:00:00Z') <= t).length;
      const fp = fcurve ? fcurve.reduce((a, p) => Math.abs(p[0] - t) < Math.abs(a[0] - t) ? p : a) : null;
      tip.show(e, tip.rows(dstr(t) + ', ' + year, [
        ['Formed by this date', t > now0 ? 'not yet' : String(formed)],
        ['An average season by now', cp ? String(Math.round(cp[1] * 10) / 10) : null],
        [fp ? esc(fc.label || 'Forecast') + ' pace by now' : null, fp ? String(Math.round(fp[1] * 10) / 10) : null],
        [month == null ? 'An average season ends near' : 'An average ' + MONTHS[month], climTarget != null ? String(Math.round(climTarget * 100) / 100) : null],
        [fcTarget != null ? esc(fc.label || 'Forecast') + ' total' : null, fcTarget != null ? String(fcTarget) : null],
      ].filter(r => r[0]), 'pace from the ' + esc(cl.period || '') + ' formation calendar in the NHC best tracks'
        + (fc.source ? '; forecast total from ' + esc(fc.source) : '')));
    });
    band.addEventListener('mouseleave', () => tip.hide());
    svg.appendChild(band);

    /* Both paces, each named on its own line where it ends.

       The filled blue line is the one the ladder's marker follows: the
       formation calendar scaled to the seasonal forecast total where one is
       published, so it is the forecaster's season rather than an average one,
       and the dashed line beside it is the average. Where no forecast is
       configured the blue line is that average and says so. The names used to
       sit in the corner of the plot with the count, which meant reading a
       figure in one place and matching it to a line in another. */
    const lead = fcurve || curve;
    const ends = [];
    if (lead) {
      const d = lead.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('');
      svg.appendChild(el('path', { d: d + 'L' + x(t1).toFixed(1) + ',' + y(0) + 'L' + x(t0).toFixed(1) + ',' + y(0) + 'Z', fill: 'var(--cool)', 'fill-opacity': .12, 'pointer-events': 'none' }));
      svg.appendChild(el('path', { d, fill: 'none', stroke: 'var(--cool)', 'stroke-width': 2, 'pointer-events': 'none' }));
      ends.push({ y: y(lead[lead.length - 1][1]), col: 'var(--cool)',
        name: fcurve ? esc(fc.label || fc.source || 'seasonal forecast') + ' forecast pace' : 'an average season' });
    }
    if (fcurve && curve) {
      svg.appendChild(el('path', { d: curve.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join(''),
        fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.4, 'stroke-dasharray': '5 4', 'pointer-events': 'none' }));
      ends.push({ y: y(curve[curve.length - 1][1]), col: 'var(--muted)', name: 'an average season' });
    }
    // two lines that finish close together would print their names on top of
    // each other, so the lower name drops far enough to clear the upper one
    ends.sort((a, b) => a.y - b.y);
    let lastEnd = -1e9;
    ends.forEach(e => {
      let ly = e.y - 5;
      if (ly - lastEnd < 12) ly = lastEnd + 12;
      lastEnd = ly;
      svg.appendChild(txt(e.name, { x: x(t1) - 4, y: ly, 'text-anchor': 'end', class: 'ax lbl', fill: e.col }));
    });
    if (month == null) for (let mo = new Date(t0).getUTCMonth(); mo <= new Date(t1).getUTCMonth(); mo++) {
      const tm = Date.UTC(year, mo, 1);
      if (tm < t0 || tm > t1) continue;
      svg.appendChild(el('line', { x1: x(tm), x2: x(tm), y1: T, y2: B, class: 'grid' }));
      svg.appendChild(txt(MONTHS[mo], { x: x(tm), y: B + 16, 'text-anchor': 'middle', class: 'ax' }));
    }
    if (month != null) for (let dd = 1; dd <= 31; dd += 7) {
      const tm = Date.UTC(year, month, dd);
      if (tm < t0 || tm > t1) continue;
      svg.appendChild(txt(dstr(tm), { x: x(tm), y: B + 16, 'text-anchor': 'middle', class: 'ax' }));
    }

    // what actually formed, as a step
    const now = Date.now();
    if (events.length) {
      let d = 'M' + x(t0).toFixed(1) + ',' + y(0);
      events.forEach((e, i) => {
        const tx = x(Date.parse(e.date + 'T00:00:00Z'));
        d += 'L' + tx.toFixed(1) + ',' + y(i) + 'L' + tx.toFixed(1) + ',' + y(i + 1);
      });
      d += 'L' + x(Math.min(now, t1)).toFixed(1) + ',' + y(events.length);
      svg.appendChild(el('path', { d, fill: 'none', stroke: 'var(--obs)', 'stroke-width': 2, 'pointer-events': 'none' }));
      events.forEach((e, i) => {
        const tx = x(Date.parse(e.date + 'T00:00:00Z')), ty = y(i + 1);
        const dot = el('circle', { cx: tx, cy: ty, r: 5, fill: 'var(--obs)', stroke: 'var(--panel)', 'stroke-width': 1 });
        attach(dot, tip.rows(esc(e.name) + ' — ' + cfg.title.toLowerCase().replace(/s$/, '') + ' number ' + (i + 1),
          [['Reached the threshold', dstr(Date.parse(e.date + 'T00:00:00Z')) + ', ' + year], ['Storm', esc(e.id || '')],
           ['Season count after it', String(i + 1)]], 'from the NHC best tracks'));
        svg.appendChild(dot);
      });
    }
    // today
    if (now > t0 && now < t1) {
      svg.appendChild(el('line', { x1: x(now), x2: x(now), y1: T - 6, y2: B, stroke: 'var(--muted)', 'stroke-dasharray': '4 3', 'pointer-events': 'none' }));
      svg.appendChild(txt('today', { x: x(now) + 4, y: T + 4, class: 'ax' }));
    }
    /* The count so far, and nothing else in the corner.

       The two figures that used to sit under it, the forecast total and the
       pace implied by today, are what the two named lines and the today marker
       carry; the hover band gives both of them for any date on the plot. */
    svg.appendChild(txt(String(events.length), { x: L + 12, y: T + 34, 'font-size': 30, 'font-weight': 700, fill: 'var(--navy)' }));
    svg.appendChild(txt('so far', { x: L + 12, y: T + 50, class: 'ax' }));

    // the ladder, on the same count axis
    svg.appendChild(txt('The market’s ladder', { x: RL, y: T - 10, class: 'axl', 'font-weight': 700 }));
    [0, 25, 50, 75, 100].forEach(p => {
      svg.appendChild(el('line', { x1: px(p), x2: px(p), y1: T, y2: B, class: 'grid' }));
      svg.appendChild(txt(p, { x: px(p), y: B + 16, 'text-anchor': 'middle', class: 'ax' }));
    });
    svg.appendChild(txt('Yes green, No red · ¢', { x: (RL + RR) / 2, y: B + 32, 'text-anchor': 'middle', class: 'axl' }));
    const prod = (market(cfg.sym) || {}).productConid;
    bars.forEach(b => {
      const v = yes(b.c), yy = y(b.n);
      if (v == null) {
        svg.appendChild(el('rect', { x: px(0), y: yy - 5, width: px(100) - px(0), height: 10, fill: 'transparent',
                                     stroke: 'var(--line)', 'stroke-width': .8, 'stroke-dasharray': '3 2', 'pointer-events': 'none' }));
        svg.appendChild(txt(noPrice(b.c), { x: (px(0) + px(100)) / 2, y: yy + 3.5, class: 'ax', 'text-anchor': 'middle' }));
        return;
      }
      // the same Yes-green / No-red split the other ladders use: both sides of a
      // pair that sums to a dollar, rather than one bar and an empty remainder
      const one = oneSided(b.c);
      const gw = px(v) - px(0);
      const yb = el('rect', { x: px(0), y: yy - 5, width: Math.max(gw, 1), height: 10, fill: 'var(--yes)', stroke: 'var(--panel)', 'stroke-width': .6 });
      const nb = el('rect', { x: px(0) + gw, y: yy - 5, width: Math.max(px(100) - px(0) - gw, 1), height: 10, fill: 'var(--no)', stroke: 'var(--panel)', 'stroke-width': .6 });
      const url = WXM.contractUrl(prod, b.c.conidYes || b.c.conid);
      const html = tip.rows(contractTitle({ name: (market(cfg.sym) || {}).name }, b.c),
        [['At least', String(b.n)]].concat(quoteRows(b.c)),
        asofFoot() + (url ? ' · click a price to open the contract' : ''));
      [yb, nb].forEach(bar => { attach(bar, html); if (url) WXM.linkTo(bar, url, 'Open ' + (b.c.label || b.n) + ' on IBKR'); svg.appendChild(bar); });
      if (one) svg.appendChild(el('rect', { x: px(0), y: yy - 5, width: px(100) - px(0), height: 10, fill: 'none',
                                            stroke: 'var(--panel)', 'stroke-width': 1.2, 'stroke-dasharray': '2 2', 'pointer-events': 'none' }));
      if (gw >= 26) svg.appendChild(txt(v + '¢' + (one ? '*' : ''), { x: px(0) + 3, y: yy + 3.5, class: 'ladtxt', 'pointer-events': 'none' }));
      if (px(100) - px(0) - gw >= 26) svg.appendChild(txt((100 - v) + '¢', { x: px(100) - 3, y: yy + 3.5, class: 'ladtxt', 'text-anchor': 'end', 'pointer-events': 'none' }));
    });
    /* Where each pace finishes, named on the line itself.

       The names sat in a row under the panel, which meant matching a dash
       pattern to a swatch. Each label now rides just above its own dashed
       line at the left end of the ladder, where the bars are widest and no
       price sits. Two lines close together are nudged apart. */
    const paces = [];
    if (target != null) {
      svg.appendChild(el('line', { x1: RL, x2: RR, y1: y(target), y2: y(target), stroke: 'var(--cool)', 'stroke-dasharray': '5 4', 'pointer-events': 'none' }));
      paces.push([y(target), 'var(--cool)', (fcTarget != null ? esc(fc.label || 'forecast') : (month == null ? 'an average season' : 'an average ' + MONTHS[month])) + ' ' + (Math.round(target * 100) / 100)]);
    }
    if (fcTarget != null && climTarget != null && Math.abs(climTarget - fcTarget) > 0.05) {
      svg.appendChild(el('line', { x1: RL, x2: RR, y1: y(climTarget), y2: y(climTarget), stroke: 'var(--muted)', 'stroke-dasharray': '5 4', 'pointer-events': 'none' }));
      paces.push([y(climTarget), 'var(--muted)', (month == null ? 'an average season' : 'an average ' + MONTHS[month]) + ' ' + (Math.round(climTarget * 100) / 100)]);
    }
    /* The ladder is bars edge to edge, so a label on the line needs its own
       ground to sit on. A small panel-colored chip riding just above each
       dashed line keeps it legible without a key underneath. */
    paces.sort((a, b) => a[0] - b[0]);
    let lastY = -1e9;
    paces.forEach(([yy, col, label]) => {
      let ly = yy - 5;
      if (ly - lastY < 13) ly = lastY + 13;
      lastY = ly;
      const w = label.length * 5.3 + 10;
      const cx = Math.min(RR - 2, RL + 2 + w);
      svg.appendChild(el('rect', { x: cx - w, y: ly - 8.5, width: w, height: 11.5, rx: 3,
                                   fill: 'var(--panel)', 'fill-opacity': .93, 'pointer-events': 'none' }));
      svg.appendChild(txt(label, { x: cx - 5, y: ly, 'text-anchor': 'end', 'font-size': 9.5,
                                   'font-weight': 700, fill: col, 'pointer-events': 'none' }));
    });
    return svg;
  }
  const monthOfSpec = sp => { const p = String(sp).split('.'); return p.length >= 2 ? (+p[1]) - 1 : null; };

  // ---- the landfall board
  //
  // Same Yes-green No-red language as every other ladder on the site. The two
  // things a table carried that a bar cannot — what a dollar of payout costs,
  // and whether the region is drawn on the map above — moved into the box.
  function drawLandfall() {
    const host = $('#landfall'); host.innerHTML = '';
    const m = market('HLF');
    if (!m && !expectLandfall().length) {
      host.appendChild(h('p', { class: 'cap', text: WXM.on() ? 'Landfall contracts not in the quote snapshot.' : 'The market layer is off.' })); return;
    }
    // the regions on this view; the others are on the other view, and the caption says so
    const rows = landfallHere();
    const elsewhere = (m ? m.contracts.length : 0) - rows.length;
    if (!rows.length) {
      /* Expected, not listed. The owner has said the exchange will carry these,
         so the view says so rather than reading as though nothing is coming;
         they carry no price, because there is no book to take one from. */
      const exp = expectLandfall();
      if (!exp.length) { host.appendChild(h('p', { class: 'cap', text: 'No landfall contract names a region on this view.' })); return; }
      const ed = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: 'Major hurricane landfall, expected' })]);
      exp.forEach(e => ed.appendChild(h('div', { class: 'lrow' }, [
        h('span', { class: 'lk', text: e.label + (e.note ? ' (' + e.note + ')' : '') }),
        h('span', { class: 'lb', style: 'background:transparent;border:1px dashed var(--rule)' }, [h('i', { style: 'width:0' })]),
        h('span', { class: 'lv dim', text: 'not listed' }),
      ])));
      host.appendChild(ed);
      host.appendChild(h('p', { class: 'cap', text: 'The exchange has not listed a landfall contract for this ocean yet. '
        + exp.map(e => e.label).join(' and ') + ' is expected, so there is no book and no price to show.' }));
      return;
    }
    const season = (rows[0] && rows[0].expiryLabel) || 'season';
    const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: 'Major hurricane landfall, ' + season })]);
    rows.slice().sort((a, b) => (b.mid == null ? -1 : b.mid) - (a.mid == null ? -1 : a.mid)).forEach(c => {
      const k = regionKey(c.label);
      const drawn = GEO && ((GEO.states || {})[k] || (GEO.counties || {})[k] || (GEO.countries || {})[k]);
      const onMap = !drawn ? 'not drawn' : (priced(c) ? 'shaded' : 'outline only (' + noPrice(c) + ')');
      const y = yes(c);
      const one = oneSided(c);
      const bar = h('div', { class: 'lrow' + (one ? ' one' : '') }, [
        h('span', { class: 'lk', text: c.label }),
        h('span', { class: 'lb' }, [h('i', { style: 'width:' + (y == null ? 0 : y) + '%' })]),
        h('span', { class: 'lv', text: yesText(c) }),
      ]);
      const url = WXM.contractUrl(m.productConid, c.conidYes || c.conid);
      attach(bar, priceHead(c) + tip.rows(contractTitle(m, c),
        quoteRows(c).concat([['On the map', onMap]]),
        asofFoot() + (url ? ' · click either side to open this strike on IBKR' : '')));
      if (url) WXM.linkTo(bar, url, 'Open ' + c.label + ' on IBKR');
      div.appendChild(bar);
    });
    host.appendChild(div);
    /* What this contract actually asks.

       The caption said "a hurricane", which is not the contract: the terms
       require a Category 3 or higher hurricane at the moment of landfall, count
       a landfall on land within 50 miles of the region's border, and say that
       hurricane-force winds reaching the area do not count if the eye stays
       offshore. Each condition moves the probability a long way, and the
       heading above the panel already said major. */
    host.appendChild(h('p', { class: 'cap', text: 'A Yes contract pays if a hurricane makes landfall in that region, or on land within 50 miles of the '
      + 'region\u2019s border, during the season named while at Category 3 or stronger. The exchange\u2019s terms define a landfall as the eye crossing '
      + 'onto land, so a storm that stays offshore has not made landfall for this contract even where hurricane-force winds reach the area.' }));
    WXM.tradeNote(host);
  }

  // ---- the vendor lane
  //
  /* One file per storm, drawn inside the storm it belongs to.

     Three kinds of file arrive for a storm. LiveCyc is a forecast from its own
     start and lands four times a day while the storm runs; the Metryc interim
     is the vendor's analysis of what has happened around the landfall; the
     Metryc final carries the gust each location recorded, in miles per hour,
     and is what the wind contracts settle on.

     They are shown one at a time, latest first, with one ordering rule that
     receipt time alone would get wrong: LiveCyc keeps arriving after an interim
     has landed, and reads near zero at a location whose peak has passed, so a
     later cycle must not print zeros over a landfall the interim has already
     measured. The interim therefore supersedes later cycles and the final
     supersedes everything.

     This used to be a section of its own, which meant every storm appeared
     twice on the page, once for its deliveries and once for its latest file.
     The storm module now asks for this panel and puts it inside the storm's
     own record. The panel opens with the map that steps through the storm
     (replayMap), and a settled storm's final is that map's last step. */
  const VENDOR_ROWS = 16;
  const GUST_LABELS = 12;                    // how many places on the map carry their number
  function shownFile(s) {
    if (s.final && s.final.sites && Object.keys(s.final.sites).length) return { kind: 'final', label: 'Metryc final', file: s.final };
    if (s.interim && s.interim.sites && Object.keys(s.interim.sites).length) return { kind: 'interim', label: 'Metryc interim', file: s.interim };
    if (s.livecyc && s.livecyc.sites && Object.keys(s.livecyc.sites).length) return { kind: 'livecyc', label: 'LiveCyc', file: s.livecyc };
    return null;
  }
  // the panel always says which file it is and when that file arrived
  function sourceLine(shown) {
    const n = Object.keys(shown.file.sites || {}).length;
    const got = shown.file.lastModified ? ' · file received ' + utc(shown.file.lastModified) : '';
    if (shown.kind === 'final') return 'Metryc final' + got + ' · peak gust at ' + n + ' locations';
    if (shown.kind === 'interim') return 'Metryc interim' + got + ' · ' + n + ' locations';
    return 'LiveCyc cycle ' + utc(shown.file.forecastTime) + got + ' · ' + n + ' locations with non-zero probability'
      + (shown.file.vendorName ? ' · delivered as ' + shown.file.vendorName + ', the depression’s number' : '');
  }
  // the exchange's wind contract for this storm and location, where one is
  // listed, so a row or a dot opens its market the way every other priced
  // surface on the site does
  function windUrl(storm, id) {
    // matched on the exchange's product name rather than on two letters taken
    // off the storm's: the exchange's code for a storm is not reliably the
    // first two letters of its name, and two unrelated products fit the
    // ladder's shape. The storm module matches the same way.
    const lm = lMarketOf(storm, id);
    const c0 = lm && (lm.contracts || [])[0];
    return lm && c0 ? WXM.contractUrl(lm.productConid, c0.conidYes || c0.conid) : null;
  }
  // the exchange's wind ladder for one location of one storm, matched on the
  // product name rather than on two letters taken off the storm's
  function lMarketOf(storm, id) {
    const nm = String((storm || {}).name || '').toLowerCase(), loc = String(id).toUpperCase();
    return (nm && MK && (MK.markets || []).find(m2 => /^L[A-Z]{2}[A-Z]{2}$/.test(m2.symbol)
      && m2.symbol.slice(3) === loc && String(m2.name || '').toLowerCase().indexOf(nm) >= 0)) || null;
  }
  // a Metryc gust in whole miles an hour, as the wind contracts resolve it (owner, 2026-10-12)
  const mph1 = v => (v == null ? null : WXC.settleMph(v) + ' mph');

  /* The gust a location's ladder gives even odds of reaching: its median (owner,
     2026-10-20, read the way the accuracy paper reads a temperature ladder).

     The ladder is the chance of a gust at or above each threshold, in percent.
     It is first made to fall with the threshold by pooling any neighbours that
     rise (a ladder should already fall), then read where it crosses fifty, on
     the straight line between the two thresholds either side, and printed in
     whole miles an hour as the contracts settle. A ladder already under fifty
     at its lowest threshold has no median the thresholds can name and reads as
     below that threshold; one still at fifty or more at its highest reads as at
     or above it. The site computes this figure from the vendor's published
     ladder by the owner's decision; the ladder itself is shown as published. */
  function gustMedian(thr, arr) {
    if (!thr || !thr.length) return null;
    const blocks = [];
    thr.forEach((t, i) => {
      blocks.push({ v: Math.max(0, Math.min(100, Number((arr && arr[i]) || 0))), n: 1 });
      while (blocks.length > 1 && blocks[blocks.length - 2].v < blocks[blocks.length - 1].v) {
        const b = blocks.pop(), a = blocks.pop();
        blocks.push({ v: (a.v * a.n + b.v * b.n) / (a.n + b.n), n: a.n + b.n });
      }
    });
    const q = [];
    blocks.forEach(b => { for (let k = 0; k < b.n; k++) q.push(b.v); });
    if (q[0] < 50) return { below: thr[0] };
    if (q[q.length - 1] >= 50) return { above: thr[thr.length - 1] };
    for (let i = 0; i < q.length - 1; i++) {
      if (q[i] >= 50 && q[i + 1] < 50)
        return { mph: WXC.settleMph(thr[i] + ((q[i] - 50) / (q[i] - q[i + 1])) * (thr[i + 1] - thr[i])) };
    }
    return null;
  }
  const gustText = m => (!m ? null : m.mph != null ? m.mph + ' mph' : m.below != null ? 'under ' + m.below + ' mph'
    : m.above + ' mph or more');
  // the figure a dot is sized and shaded by: a median under the lowest threshold has none
  const gustOf = m => (!m ? null : m.mph != null ? m.mph : m.above != null ? m.above : null);

  /* The strongest places carry their figure on the map.

     Each label takes the first free position on a ring around its dot, working
     outwards, and is dropped if nothing on either ring is clear: a number
     printed over another number is worse than one left to the hover. A label
     pushed out to the second ring gets a hairline back to its dot, because at
     that distance the pairing is no longer obvious. */
  function labelDots(g, pts, view, avoid, s) {
    s = s || 1;
    const placed = (avoid || []).slice();
    const dots = pts.map(p => ({ x0: p.px - p.r * .9, x1: p.px + p.r * .9, y0: p.py - p.r * .9, y1: p.py + p.r * .9 }));
    const clash = (b, arr) => arr.some(a => !(b.x1 < a.x0 || b.x0 > a.x1 || b.y1 < a.y0 || b.y0 > a.y1));
    let labeled = 0;
    pts.forEach(p => {
      const val = String(p.label);
      const w = (p.name.length * 5.3 + val.length * 6.2 + 6) * s;
      const spots = [];
      [0, 1].forEach(ring => {
        const d = p.r + (4 + ring * 15) * s;
        spots.push([p.px + d, p.py + 3.2 * s, 'start', ring], [p.px - d, p.py + 3.2 * s, 'end', ring],
                   [p.px, p.py - d - s, 'middle', ring], [p.px, p.py + d + 7.5 * s, 'middle', ring],
                   [p.px + d * .8, p.py - d * .8, 'start', ring], [p.px - d * .8, p.py - d * .8, 'end', ring],
                   [p.px + d * .8, p.py + d * .8 + 5 * s, 'start', ring], [p.px - d * .8, p.py + d * .8 + 5 * s, 'end', ring]);
      });
      // first pass keeps clear of the dots as well as of the other labels;
      // covering a place's own mark is only accepted when nothing else is free
      let put = null;
      for (let pass = 0; pass < 2 && !put; pass++) {
        for (let i = 0; i < spots.length; i++) {
          const lx = spots[i][0], ly = spots[i][1], anchor = spots[i][2], ring = spots[i][3];
          const bx = anchor === 'start' ? lx : anchor === 'end' ? lx - w : lx - w / 2;
          const box = { x0: bx - 2 * s, x1: bx + w + 2 * s, y0: ly - 9 * s, y1: ly + 3 * s };
          if (box.x0 < view.x0 + 2 * s || box.x1 > view.x1 - 2 * s || box.y0 < view.y0 + 2 * s || box.y1 > view.y1 - 2 * s) continue;
          if (clash(box, placed)) continue;
          if (!pass && clash(box, dots)) continue;
          put = { lx, ly, anchor, ring, box };
          break;
        }
      }
      if (!put) return;
      const lx = put.lx, ly = put.ly, anchor = put.anchor, box = put.box;
      placed.push(box);
      labeled++;
      if (put.ring) g.appendChild(el('line', { x1: p.px.toFixed(1), y1: p.py.toFixed(1),
        x2: (anchor === 'start' ? box.x0 : anchor === 'end' ? box.x1 : (box.x0 + box.x1) / 2).toFixed(1),
        y2: ((box.y0 + box.y1) / 2).toFixed(1), stroke: 'var(--muted)', 'stroke-width': .7 * s, 'pointer-events': 'none' }));
      // the label sits on the sea as often as on the land; a panel-colored
      // plate under it keeps both readable
      g.appendChild(el('rect', { x: box.x0.toFixed(1), y: box.y0.toFixed(1), width: (box.x1 - box.x0).toFixed(1),
                                 height: (box.y1 - box.y0).toFixed(1), rx: 2.5 * s, fill: 'var(--panel)',
                                 'fill-opacity': .78, 'pointer-events': 'none' }));
      const t = txt('', { x: lx.toFixed(1), y: ly.toFixed(1), 'text-anchor': anchor, 'font-size': 10.5 * s,
                          'pointer-events': 'none', class: 'glbl' });
      const sp = (s2, at) => { const n = el('tspan', at); n.textContent = s2; t.appendChild(n); };
      sp(val + ' ', { 'font-weight': 700, fill: 'var(--ink)' });
      sp(p.name, { fill: 'var(--muted)' });
      g.appendChild(t);
    });
    return labeled;
  }

  /* The storm step by step: NHC's forecast and each location's gust on one map
     (owner, 2026-10-20).

     The storm section's timeline moves it. At each full NHC advisory the map
     draws that advisory's cone and forecast track, where the storm was and the
     path it had taken by then, and at each location the median gust of the
     newest LiveCyc ladder the vendor had built by that hour (gustMedian). LiveCyc
     looks forward from its cycle, so a place the storm has passed falls back.
     The interim and the final close the storm: the interim's median, then the
     gust each place recorded, which the wind contracts settle on. The storm's
     own places, those with a contract and those in the vendor's record of it,
     are always drawn, a ring where the figure is under the lowest threshold or
     there is none; any other place is drawn once its figure reaches it.

     The view holds the places that matter to the storm and the cones of the
     advisories that carried LiveCyc figures, the storm's contract period, or of
     every advisory while the vendor has published nothing for it (owner,
     2026-10-20: Nolo's whole track ran a hundred degrees of longitude and left
     Hawaii a few pixels wide). Cones from before or after that period run off
     the edge. The view is fixed from the first step, so nothing moves while
     stepping, and the shading's scale is the storm's whole range for the same
     reason. The projection is equirectangular with longitude squeezed by the
     cosine of the middle latitude, so a coastline keeps its shape at this
     scale. */
  function replayMap(storm, doc, steps) {
    if (!GEO || !steps || !steps.length) return null;
    const thr = (doc && doc.thresholds) || [];
    const fin = (doc && doc.final) || null;
    const sites = (doc && doc.sites) || {};
    const nm = String(storm.name || '').toLowerCase();
    // the places with contracts: listed now, or priced in the ledger while they were
    const traded = new Set();
    ((MK && MK.markets) || []).forEach(m => {
      if (/^L[A-Z]{4}$/.test(m.symbol) && !/^LHL/.test(m.symbol) && String(m.name || '').toLowerCase().indexOf(nm) >= 0)
        traded.add(m.symbol.slice(3));
    });
    ((doc && doc.steps) || []).forEach(s => [s.prices, s.pricesEnd].forEach(pr => Object.keys(pr || {}).forEach(id => traded.add(id))));
    const loc = {};
    new Set([...Object.keys(sites), ...Object.keys(fin || {}), ...traded]).forEach(id => {
      const L = locationById(id);
      if (L && L.lat != null && L.lon != null)
        loc[id] = { id, L, name: (sites[id] && sites[id].name) || L.name || id, lat: L.lat, lon: L.lon };
    });
    // each step's figure at each place
    const figs = steps.map(st => {
      const out = {};
      Object.keys(loc).forEach(id => {
        if (st.kind === 'final') {
          const g = fin && fin[id];
          if (g != null && WXC.settleMph(g) > 0) out[id] = { mph: WXC.settleMph(g) };
          return;
        }
        if (!st.delivery) return;
        // a cycle files only the places it gave a chance, so a place it left out stands at zero
        const m = gustMedian(thr, (st.delivery.sites || {})[id]);
        if (m) out[id] = m;
      });
      return out;
    });
    const finalTop = Object.keys(fin || {}).filter(id => loc[id]).sort((a, b) => fin[b] - fin[a]).slice(0, 20);
    // the storm's own places: a contract, or a place in the vendor's record of the storm
    const own = new Set([...traded, ...Object.keys(sites)].filter(id => loc[id]));
    const key = new Set(own);
    figs.forEach((f, i) => Object.keys(f).forEach(id => { if (gustOf(f[id]) != null && steps[i].kind !== 'final') key.add(id); }));
    finalTop.forEach(id => key.add(id));
    // the advisories in step order, each once
    const advs = [];
    steps.forEach(st => { if (st.adv && advs.indexOf(st.adv) < 0) advs.push(st.adv); });
    const carried = advs.filter(a => steps.some(st => st.adv === a && st.delivery && (st.kind === 'adv' || st.kind === 'cycle')));
    const frame = [];
    (carried.length ? carried : advs).forEach(a => {
      (a.cone || []).forEach(r => r.forEach(q => frame.push(q)));
      (a.track || []).forEach(r => r.forEach(q => frame.push(q)));
      (a.points || []).forEach(p => frame.push([p.lon, p.lat]));
    });
    key.forEach(id => frame.push([loc[id].lon, loc[id].lat]));
    if (frame.length < 2) return null;

    const W = 960;
    const latMid = frame.reduce((s, q) => s + q[1], 0) / frame.length;
    const k = Math.cos(latMid * Math.PI / 180) || 1;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    frame.forEach(q => { x0 = Math.min(x0, q[0] * k); x1 = Math.max(x1, q[0] * k); y0 = Math.min(y0, -q[1]); y1 = Math.max(y1, -q[1]); });
    // room for the labels, and a floor on the span so two neighbouring places
    // do not zoom the frame down to a stretch of water
    const padX = Math.max((x1 - x0) * 0.14, 0.8), padY = Math.max((y1 - y0) * 0.16, 0.8);
    x0 -= padX; x1 += padX; y0 -= padY; y1 += padY;
    const Hh = Math.max(320, Math.min(600, Math.round(W * (y1 - y0) / (x1 - x0))));
    const sc = Math.min(W / (x1 - x0), Hh / (y1 - y0));
    const ox = (W - (x1 - x0) * sc) / 2, oy = (Hh - (y1 - y0) * sc) / 2;
    const X = lon => ox + (lon * k - x0) * sc, Y = lat => oy + (-lat - y0) * sc;
    const lo0 = (x0 - ox / sc) / k, lo1 = (x0 + (W - ox) / sc) / k, la0 = -(y0 + (Hh - oy) / sc), la1 = -(y0 - oy / sc);
    const inside = p => p.lon >= lo0 && p.lon <= lo1 && p.lat >= la0 && p.lat <= la1;

    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + Hh, class: 'gustmap replay',
                            'data-view': [lo0, lo1, la0, la1].map(v => v.toFixed(2)).join(',') });
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: Hh, fill: 'var(--map-sea)' }));
    const inView = rr => rr.some(r => r.some(q => q[0] >= lo0 && q[0] <= lo1 && q[1] >= la0 && q[1] <= la1));
    const land = (rr, stroke, sw) => {
      if (!inView(rr)) return;
      svg.appendChild(el('path', { d: rr.map(r => 'M' + r.map(q => X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join('L') + 'Z').join(' '),
                                   fill: 'var(--map-land)', stroke, 'stroke-width': sw, 'vector-effect': 'non-scaling-stroke',
                                   'pointer-events': 'none' }));
    };
    /* A Pacific storm's coordinates run west past the date line (pipeline/
       advisories.py writes 147 east as -213), so where the view reaches past it
       the land beyond is drawn a second time, a turn of the globe to the west. */
    const west = rr => rr.map(r => r.map(q => [q[0] > 0 ? q[0] - 360 : q[0], q[1]]));
    const lands = (rr, stroke, sw) => { land(rr, stroke, sw); if (lo0 < -180 && rr.some(r => r.some(q => q[0] > 0))) land(west(rr), stroke, sw); };
    Object.values(GEO.countries || {}).forEach(rr => lands(rr, 'var(--map-line)', .7));
    (NATION || []).forEach(r => lands([r], 'var(--map-line)', .7));
    Object.values(GEO.states || {}).forEach(rr => lands(rr, 'var(--map-line)', .5));
    const dyn = el('g');
    svg.appendChild(dyn);

    /* Zoom and pan, as on the basin map (owner, 2026-10-20). The viewBox moves,
       and a beat after a zoom settles the step is drawn again with its dots,
       labels, lines and key multiplied by the glyph scale, the world units per
       screen unit, so they keep their size on screen at every zoom; the labels
       go to the strongest places in view. The view stays where the reader left
       it while the timeline steps. The pointer is captured only once a press has
       moved far enough to be a drag, so a click on a place still reaches it. */
    const VIEW0 = { x: 0, y: 0, w: W, h: Hh };
    let view = Object.assign({}, VIEW0), cur = steps.length - 1, rescale = null;
    const MAXZ = 12;
    const lvl = h('span', { class: 'cap', style: 'margin:0', text: 'whole view' });
    function applyView() {
      svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].map(n => Math.round(n * 100) / 100).join(' '));
      const z = W / view.w;
      lvl.textContent = z < 1.02 ? 'whole view' : Math.round(z * 10) / 10 + '×';
      svg.classList.toggle('grab', z > 1.02);
    }
    const clampView = () => {
      view.x = Math.min(Math.max(view.x, 0), W - view.w);
      view.y = Math.min(Math.max(view.y, 0), Hh - view.h);
    };
    function zoomAbout(factor, cx, cy) {
      const z = W / view.w, want = Math.min(Math.max(z * factor, 1), MAXZ);
      if (Math.abs(want - z) < 1e-6) return;
      const w = W / want, hh = Hh / want;
      // keep the point under the pointer where it is
      view.x = cx - (cx - view.x) * (w / view.w);
      view.y = cy - (cy - view.y) * (hh / view.h);
      view.w = w; view.h = hh;
      clampView(); applyView();
      clearTimeout(rescale); rescale = setTimeout(() => setStep(cur), 140);
    }
    const atPoint = ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      return [q.x, q.y];
    };
    svg.addEventListener('wheel', ev => {
      ev.preventDefault();
      const [cx, cy] = atPoint(ev);
      zoomAbout(ev.deltaY < 0 ? 1.18 : 1 / 1.18, cx, cy);
    }, { passive: false });
    let drag = null, moved = 0;
    svg.addEventListener('pointerdown', ev => {
      if (W / view.w <= 1.02) return;                  // nothing to pan at full extent
      drag = { sx: ev.clientX, sy: ev.clientY, vx: view.x, vy: view.y, id: ev.pointerId, held: false };
      moved = 0;
    });
    svg.addEventListener('pointermove', ev => {
      if (!drag) return;
      const k = view.w / svg.getBoundingClientRect().width;
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
      if (moved <= 4) return;
      if (!drag.held) {
        try { svg.setPointerCapture(drag.id); } catch (e) { /* the pointer has gone */ }
        drag.held = true; svg.classList.add('grabbing');
      }
      view.x = drag.vx - dx * k; view.y = drag.vy - dy * k;
      clampView(); applyView();
    });
    const endDrag = () => {
      if (!drag) return;
      const held = drag.held;
      if (held) try { svg.releasePointerCapture(drag.id); } catch (e) { /* already released */ }
      drag = null; svg.classList.remove('grabbing');
      if (held) setStep(cur);                           // the key and the labels follow the view
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    // a pan that ends over a place must not also open its contract
    svg.addEventListener('click', ev => { if (moved > 4) { ev.stopPropagation(); ev.preventDefault(); moved = 0; } }, true);
    const bar = h('div', { class: 'bar rzoom' });
    const mk = (label, title, fn) => { const b = h('button', { text: label, title }); b.onclick = fn; bar.appendChild(b); };
    mk('−', 'zoom the storm map out', () => zoomAbout(1 / 1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('+', 'zoom the storm map in', () => zoomAbout(1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('Whole view', 'back to the storm map’s whole view', () => { view = Object.assign({}, VIEW0); applyView(); setStep(cur); });
    bar.appendChild(lvl);
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: '· scroll to zoom, drag to pan' }));

    let gmax = thr.length ? thr[0] + 10 : 1;
    figs.forEach(f => Object.values(f).forEach(m => { const g = gustOf(m); if (g != null) gmax = Math.max(gmax, g); }));
    const rOf = g => 3 + 8 * Math.sqrt(Math.min(g / gmax, 1));
    const path = (r, close) => r.map((q, i) => (i ? 'L' : 'M') + X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join('') + (close ? 'Z' : '');
    const day = iso => { const d = new Date(Date.parse(iso)); return isFinite(d) ? String(d.getUTCHours()).padStart(2, '0') + 'Z ' + d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] : '—'; };
    const cycOf = s => String((s && s.id) || '').replace(/^(\d{4})(\d{2})(\d{2})(\d{2})$/, (x, y, mo, d, hh) => hh + 'Z ' + (+d) + ' ' + MONTHS[+mo - 1]);
    const cap = h('p', { class: 'cap replaycap' });
    let out = { drawn: 0, labeled: 0, outside: 0 };

    function setStep(i) {
      cur = Math.max(0, Math.min(i, steps.length - 1));
      const st = steps[cur];
      const f = figs[cur];
      const s = view.w / W;                              // the glyph scale
      dyn.innerHTML = '';
      // the path so far: every advisory's hour-zero position up to the one in force
      const closing = st.kind === 'interim' || st.kind === 'final';
      const upto = closing ? advs.length - 1 : advs.indexOf(st.adv);
      const trail = advs.slice(0, upto + 1).map(a => (a.points || [])[0]).filter(Boolean);
      if (trail.length > 1) dyn.appendChild(el('path', { d: path(trail.map(p => [p.lon, p.lat])), fill: 'none', stroke: 'var(--muted)',
        'stroke-width': 1.4 * s, 'stroke-dasharray': (3 * s) + ' ' + (3 * s), class: 'rpath', 'pointer-events': 'none' }));
      trail.slice(0, closing ? trail.length : -1).forEach(p => dyn.appendChild(el('circle', { cx: X(p.lon).toFixed(1), cy: Y(p.lat).toFixed(1),
        r: 2.2 * s, fill: ptColor(p), 'pointer-events': 'none' })));
      const a = closing ? null : st.adv;
      if (a) {
        (a.cone || []).forEach(r => dyn.appendChild(el('path', { d: path(r, true), class: 'rcone', fill: 'rgba(100,116,139,.16)',
          stroke: '#64748b', 'stroke-width': s, 'pointer-events': 'none' })));
        (a.track || []).forEach(r => dyn.appendChild(el('path', { d: path(r), fill: 'none', stroke: 'var(--navy)', 'stroke-width': 2 * s,
          'pointer-events': 'none' })));
        (a.points || []).forEach(p => {
          const now = p.tau === 0;
          const c = el('circle', { cx: X(p.lon).toFixed(1), cy: Y(p.lat).toFixed(1), r: (now ? 6 : 4) * s, fill: ptColor(p),
                                   stroke: 'var(--panel)', 'stroke-width': 1.2 * s, class: now ? 'rnow' : 'rpt' });
          attach(c, tip.rows(esc(p.type || '') + ' ' + esc(a.name || storm.name) + ' — ' + (now ? 'position at advisory ' + esc(Number(a.adv)) : 'forecast point'),
            [['Valid', utc(p.t)], ['Wind', p.kt != null ? windText(p.kt, p.type, p) : '—'], ['Type', typeText(p.type)],
             ['Position', position(p.lat, p.lon)], ['Advisory', esc(Number(a.adv)) + ', issued ' + utc(a.issued)],
             now ? null : ['Lead', p.tau + ' h']], 'NWS National Hurricane Center, as issued'));
          dyn.appendChild(c);
        });
      }
      // the places, faintest first, so the strongest sit on top where places crowd together
      const pts = [];
      Object.keys(loc).forEach(id => {
        const p = loc[id], m = f[id], g = gustOf(m);
        if (!inside(p)) return;
        if (g == null && !own.has(id)) return;
        if (g == null && st.kind === 'final') return;
        pts.push({ id, name: p.name, L: p.L, lon: p.lon, lat: p.lat, m, g, px: X(p.lon), py: Y(p.lat), r: (g == null ? 3.2 : rOf(g)) * s });
      });
      pts.sort((p, q) => (q.g == null ? -1 : q.g) - (p.g == null ? -1 : p.g));
      pts.slice().reverse().forEach(p => {
        const c = p.g == null
          ? el('circle', { cx: p.px.toFixed(1), cy: p.py.toFixed(1), r: p.r.toFixed(2), fill: 'var(--panel)', 'fill-opacity': .85,
                           stroke: 'var(--muted)', 'stroke-width': s, class: 'gring' })
          : el('circle', { cx: p.px.toFixed(1), cy: p.py.toFixed(1), r: p.r.toFixed(2), fill: ramp(p.g / gmax), 'fill-opacity': .9,
                           stroke: 'var(--ink)', 'stroke-width': .5 * s, class: 'gdot' });
        const url = windUrl(storm, p.id);
        const what = st.kind === 'final' ? 'Peak gust recorded' : st.kind === 'interim' ? 'Metryc interim median gust' : 'LiveCyc median gust';
        attach(c, tip.rows(esc(p.name) + ' (' + esc(p.id) + ')',
          [[what, p.m ? gustText(p.m) : 'no figure yet'],
           st.kind === 'final' ? null : ['From', st.delivery ? (st.kind === 'interim' ? 'the Metryc interim file' : 'the LiveCyc cycle of ' + cycOf(st.delivery))
             : st.since ? 'no LiveCyc cycle since ' + cycOf(st.since) : 'no LiveCyc cycle yet'],
           ['Region', esc(p.L.region)], ['Country', esc(p.L.country)], ['State', esc(p.L.state)],
           ['Contract', traded.has(p.id) ? 'listed' : null]],
          esc((RK && RK.attribution) || 'Powered by Reask') + (st.kind === 'final' ? '; the final file, which the wind contracts settle on'
            : st.kind === 'interim' ? '; the median of the interim file’s ladder' : '; the median of the cycle’s ladder, which looks forward from its cycle')
            + (url ? ' · click to open this location’s contract' : '')));
        if (url) WXM.linkTo(c, url, 'Open the ' + p.name + ' wind contract on IBKR');
        dyn.appendChild(c);
      });
      const nowP = a && (a.points || [])[0];
      const avoid = nowP ? [{ x0: X(nowP.lon) - 7 * s, x1: X(nowP.lon) + 7 * s, y0: Y(nowP.lat) - 7 * s, y1: Y(nowP.lat) + 7 * s }] : [];
      const seen = p => p.px >= view.x && p.px <= view.x + view.w && p.py >= view.y && p.py <= view.y + view.h;
      const labeled = labelDots(dyn, pts.filter(p => p.g != null && seen(p)).slice(0, GUST_LABELS)
        .map(p => Object.assign({}, p, { label: p.m.mph != null ? p.m.mph : p.m.above + '+' })),
        { x0: view.x, y0: view.y, x1: view.x + view.w, y1: view.y + view.h }, avoid, s);
      // the key: what the shading and the size mean at this step, in the view's bottom left at every zoom
      const kg = el('g', { 'pointer-events': 'none' });
      const bx = view.x + 16 * s, by = view.y + view.h - 46 * s, bw = 150 * s;
      kg.appendChild(el('rect', { x: bx - 8 * s, y: by - 18 * s, width: bw + 112 * s, height: 46 * s, rx: 5 * s,
                                  fill: 'var(--panel)', 'fill-opacity': .9, stroke: 'var(--line)', 'stroke-width': .8 * s }));
      kg.appendChild(txt(st.kind === 'final' ? 'Peak gust recorded, mph' : st.kind === 'interim' ? 'Metryc interim median gust, mph'
        : 'LiveCyc median gust, mph', { x: bx, y: by - 5 * s, 'font-size': 10 * s, 'font-weight': 700, fill: 'var(--ink)', class: 'gkey' }));
      for (let j = 0; j < 48; j++) kg.appendChild(el('rect', { x: bx + j * (bw / 48), y: by, width: bw / 48 + .6 * s, height: 9 * s, fill: ramp(j / 47) }));
      kg.appendChild(txt('0', { x: bx, y: by + 20 * s, 'font-size': 9 * s, fill: 'var(--muted)' }));
      kg.appendChild(txt(String(gmax), { x: bx + bw, y: by + 20 * s, 'text-anchor': 'end', 'font-size': 9 * s, fill: 'var(--muted)' }));
      if (st.kind !== 'final' && thr.length) {
        kg.appendChild(el('circle', { cx: bx + bw + 16 * s, cy: by + 4.5 * s, r: 3.2 * s, fill: 'var(--panel)', stroke: 'var(--muted)', 'stroke-width': s }));
        kg.appendChild(txt('under ' + thr[0], { x: bx + bw + 23 * s, y: by + 8 * s, 'font-size': 9 * s, fill: 'var(--muted)' }));
      }
      dyn.appendChild(kg);
      // what this step is, under the map
      const outside = st.kind === 'final' ? Object.keys(fin || {}).filter(id => loc[id] && WXC.settleMph(fin[id]) > 0 && !inside(loc[id])).length : 0;
      const pathNote = advs.length ? 'The storm’s path through NHC’s advisories. ' : '';
      // the final file carries every reference location, whatever the storm did, so the count says which list it is
      const listed = Object.keys((storm.final && storm.final.sites) || {}).length || (storm.final && storm.final.n) || Object.keys(fin || {}).length;
      cap.textContent = st.kind === 'final'
        ? pathNote + 'Each place shows the peak gust it recorded in the Metryc final file, which the wind contracts settle on'
          + (outside ? '; ' + outside + ' further location' + (outside === 1 ? '' : 's') + ' recorded a gust outside the view.' : '.')
          + ' The final file carries all ' + listed + ' locations on the vendor’s list, whatever the storm did.'
        : st.kind === 'interim'
          ? pathNote + 'Each place shows the median gust of the Metryc interim file.'
          : (a ? 'NHC advisory ' + Number(a.adv) + ', issued ' + day(a.issued) + ', with the path so far. ' : 'No NHC advisory on file for this cycle. ')
            + (st.delivery ? 'Each place shows the median gust of the LiveCyc cycle of ' + cycOf(st.delivery) + '; rings are under ' + (thr[0] || 60) + ' mph.'
              : st.since ? 'No LiveCyc cycle since ' + cycOf(st.since) + '; rings mark the storm’s reference locations.'
              : 'No LiveCyc cycle yet; rings mark the storm’s reference locations.');
      out = { drawn: pts.length, labeled, outside, step: cur };
      svg.setAttribute('data-step', String(cur));
    }
    return { svg, cap, bar, setStep, get: () => out, steps: steps.length };
  }

  /* The vendor's latest file for one storm, built for the storm module. */
  /* One location's gust ladder from the newest vendor file: the card the
     storm's file panel draws per location, and the one the map's side panel
     draws for the location under the pointer. */
  function ladderCard(storm, shown, id, r) {
    const thr = shown.file.thresholds || [];
    const L = locationById(id) || { id, name: r.name, region: null, country: null, state: null };
    const card = h('div', { class: 'ladder vloc' }, [
      h('div', { class: 'lt', text: (r.name || L.name || id) + ' (' + id + ')' }),
      h('div', { class: 'cap', style: 'margin:0 0 6px', text: storm.name + ' · ' + shown.label })]);
    /* Each rung is the exchange's board for that threshold, in the Yes
       green and No red every other ladder on this site uses, with a tick
       where the vendor's published probability sits.

       Before the exchange lists a storm the bar is built from the vendor's
       probability instead, because that is the only number there is and a
       reader wants the shape of the ladder either way. It is drawn hatched
       and says so, and it is replaced by the price the moment a real one
       exists. The tick and the bar then sit on the same scale, which is
       the comparison worth having: the vendor's figure against what the
       exchange is charging for it. */
    const lm = lMarketOf(storm, id);
    const priceAt = t => {
      const c = lm && (lm.contracts || []).find(x => Number(x.strike) === Number(t));
      return c && WXM.realMid(c) ? { pc: Math.round(c.mid * 100), c } : null;
    };
    const carried = thr.map((t, i) => [t, r.p[i] || 0]).filter(x => x[1] > 0 || priceAt(x[0]));
    (carried.length ? carried : [[thr[0], 0]]).slice().reverse().forEach(([t, v]) => {
      const pr = priceAt(t);
      const pc = pr ? pr.pc : Math.max(0, Math.min(100, v));
      const bar = h('span', { class: 'vtrack' + (pr ? '' : ' vsoft') }, [
        h('span', { class: 'vyes', style: 'width:' + pc + '%' }),
        h('span', { class: 'vno', style: 'width:' + (100 - pc) + '%' }),
        h('span', { class: 'vmark', style: 'left:' + Math.max(0, Math.min(100, v)) + '%' })]);
      const row = h('div', { class: 'vrung' }, [
        h('span', { class: 'vlab', text: '\u2265 ' + t + ' mph' }), bar,
        h('span', { class: 'vpct', text: pr ? pr.pc + '\u00a2' : WXC.vendorPct(v) })]);
      attach(row, tip.rows(esc(r.name || L.name || id) + ' \u00b7 gusts of ' + t + ' mph or greater',
        [['Yes price', pr ? pr.pc + '\u00a2' : null],
         ['No price', pr ? (100 - pr.pc) + '\u00a2' : null],
         ['Yes bid', pr && pr.c.bid != null ? Math.round(pr.c.bid * 100) + '\u00a2' : null],
         ['No bid', pr && pr.c.ask != null ? Math.round((1 - pr.c.ask) * 100) + '\u00a2' : null],
         ['Vendor probability', esc(WXC.vendorPct(v))],
         ['Bar', pr ? 'the exchange\u2019s price' : 'the vendor\u2019s probability, no price yet']],
        pr ? 'the tick marks the vendor\u2019s figure on the same scale'
           : 'the exchange has not opened this threshold; the bar is the vendor\u2019s figure and the '
             + 'tick sits on it'));
      card.appendChild(row);
    });
    attach(card, locationTip(L, { storm: storm.name, file: shown.label, thresholds: thr, p: r.p,
                                  forecastTime: shown.kind === 'livecyc' ? shown.file.forecastTime : null,
                                  received: shown.file.lastModified }));
    const url = windUrl(storm, id);
    if (url) WXM.linkTo(card, url, 'Open the ' + (r.name || id) + ' wind contract on IBKR');
    return card;
  }

  /* A storm's panel: the map that steps through it, then, while the storm is
     running, the vendor's latest file. A settled storm's record is the map's
     last step, the gust each place recorded, so its panel is the map alone.
     Returns the map, which the storm module's timeline moves. */
  function stormFile(storm, into, replay) {
    // settled as the rest of the page reads it: the storm's index entry carries the final
    const settled = !!(storm && storm.final);
    const ctl = replay && replay.steps && replay.steps.length ? replayMap(storm, replay.doc, replay.steps) : null;
    if (ctl) {
      into.appendChild(h('div', { class: 'filehd', text: 'NHC forecasts and gusts, step by step' }));
      // a settled storm's record is the vendor's final file, and the panel says when it arrived
      const fin = settled ? shownFile(storm) : null;
      if (fin && fin.kind === 'final') into.appendChild(h('p', { class: 'cap filesrc', style: 'margin:0 0 6px', text: sourceLine(fin) }));
      else if (settled && storm.final && storm.final.lastModified)
        into.appendChild(h('p', { class: 'cap filesrc', style: 'margin:0 0 6px',
          text: 'Metryc final · file received ' + utc(storm.final.lastModified) + (storm.final.n ? ' · peak gust at ' + storm.final.n + ' locations' : '') }));
      into.appendChild(ctl.bar);
      into.appendChild(h('div', { class: 'card replaycard' }, [ctl.svg]));
      into.appendChild(ctl.cap);
      if (settled) return ctl;
    }
    const shown = shownFile(storm);
    if (!shown) {
      if (!ctl) {
        into.appendChild(h('div', { class: 'filehd', text: 'Vendor file' }));
        into.appendChild(h('p', { class: 'cap', style: 'margin:0', text: 'No vendor file published yet.' }));
      }
      return ctl;
    }
    into.appendChild(h('div', { class: 'filehd', text: shown.label }));
    into.appendChild(h('p', { class: 'cap filesrc', style: 'margin:0 0 6px', text: sourceLine(shown) }));
    const later = shown.kind === 'interim' && storm.livecyc && storm.livecyc.lastModified
                  && Date.parse(storm.livecyc.lastModified) > Date.parse(storm.interim.lastModified || 0);
    const all = Object.entries(shown.file.sites || {});
    const notes = [];
    let rows = all;
    if (shown.kind === 'final') {
      // no map could be drawn (no geography, or no ledger to step through): the figures themselves
      const gust = r => (r.peakGustMph == null ? -Infinity : r.peakGustMph);
      rows = all.slice().sort((a, b) => gust(b[1]) - gust(a[1])
                                     || String(a[1].name || '').localeCompare(String(b[1].name || ''))).slice(0, VENDOR_ROWS);
      const tb = h('table');
      tb.appendChild(h('tr', {}, [h('th', { text: 'Reference location' }), h('th', { class: 'num', text: 'Peak gust, mph' })]));
      rows.forEach(([id, r]) => {
        const tr = h('tr', {}, [h('td', { text: r.name + ' (' + id + ')' }),
                                h('td', { class: 'num', text: mph1(r.peakGustMph) || '—' })]);
        const L = locationById(id) || { id, name: r.name, region: null, country: null, state: null };
        attach(tr, tip.rows(esc(r.name) + ' (' + esc(id) + ')',
          [['Region', esc(L.region)], ['Country', esc(L.country)], ['State', esc(L.state)],
           ['Storm', esc(storm.name) + ' ' + storm.year],
           ['Peak gust', mph1(r.peakGustMph) || 'not covered']],
          esc((RK && RK.attribution) || 'Powered by Reask') + '; the final file, which the wind contracts settle on'));
        const url = windUrl(storm, id);
        if (url) WXM.linkTo(tr, url, 'Open the ' + r.name + ' wind contract on IBKR');
        tb.appendChild(tr);
      });
      into.appendChild(h('div', { class: 'card', style: 'padding:0' }, [tb]));
    } else {
      /* The vendor's lowest rung is kept.

         On a tropical storm it is the only rung carrying anything: Dolly's
         ladder was non-zero at 60 mph everywhere and at 70 almost nowhere. */
      const thr = shown.file.thresholds || [];
      const cols = [60, 70, 80, 90, 100, 110, 120, 130, 150].filter(t => thr.includes(t));
      const idx = cols.map(t => thr.indexOf(t));
      /* Rank by the ladder from the top rung down.

         Ordering on one fixed rung ranked nothing when no location reached it.
         A ladder is monotone, so comparing the highest threshold first and
         dropping to the next only to break ties puts the most exposed location
         at the top whatever the storm's strength; the name settles a file
         whose rungs are all zero, as a storm that stayed offshore leaves. */
      const rank = (a, b) => {
        for (let i = idx.length - 1; i >= 0; i--) {
          const d = (b[1].p[idx[i]] || 0) - (a[1].p[idx[i]] || 0);
          if (d) return d;
        }
        return String(a[1].name || '').localeCompare(String(b[1].name || ''));
      };
      rows = all.slice().sort(rank).slice(0, VENDOR_ROWS);
      /* A card per location rather than one wide table.

         The table put nine thresholds across the page and made a reader scan a
         row of small numbers to see the shape of one location's ladder, which
         is the thing worth seeing: how fast the chance falls away as the wind
         gets stronger. A card per location draws that fall as a bar per rung.
         The rungs run down from the strongest wind the location carries to the
         weakest, so the row a reader reaches for first is at the top.

         Only the rungs the vendor published a figure above zero on are drawn,
         with the lowest rung kept when there are none, because a published
         zero is still the vendor's answer and an empty card is not.

         The rungs read "at or above", not "above". The contract's own question
         is "wind gusts of [##] mph or greater" and it resolves Yes when the
         settlement value is greater than OR EQUAL TO the threshold, so a
         strict symbol here would have stated the settlement rule wrongly. The
         wind contract at a station is the other way round, strictly greater,
         and says so on its own page. */
      const grid = h('div', { class: 'ladders vgrid' });
      rows.forEach(([id, r]) => grid.appendChild(ladderCard(storm, shown, id, r)));
      into.appendChild(grid);
      WXM.tradeNote(into, grid);
    }
    /* Why a later cycle is not the one shown, where that applies, and what was
       left off. LiveCyc is forward-looking from its own start, so a location
       whose peak has already passed reads near zero on a cycle issued after it
       while the interim holds the figure for the landfall that happened. */
    if (later) notes.push('A LiveCyc cycle has arrived since this file. LiveCyc looks forward from its own '
      + 'start, so it reads near zero where the peak has already passed; the interim is the vendor’s word on '
      + 'what happened and is the one shown.');
    if (all.length > rows.length) notes.push('Showing the ' + rows.length + ' of ' + all.length
      + (shown.kind === 'final' ? ' reference locations that recorded the highest gusts.' : ' locations in the file that stand highest.'));
    if (notes.length) into.appendChild(h('p', { class: 'cap', text: notes.join(' ') }));
    return ctl;
  }

  /* What the section says above the storms: when the next file is due while a
     storm is running, and when the lane was last polled. The storm module
     carries the rest, including what to say when the lane is off. */
  function drawVendorNote() {
    const host = $('#vendorNote'); if (!host) return;
    host.innerHTML = '';
    if (!RK || !RK.enabled) return;
    const storms = (RK.storms || []).filter(s => inBasin(s.name));
    if (storms.some(s2 => !WXStorm.dormant(s2) && s2.livecyc)) {
      host.appendChild(scheduleLine('Next LiveCyc file expected about',
        [0, 6, 12, 18].map(hh => hh + LIVECYC_LAG_H),
        'the ' + String(new Date(nextMark([0, 6, 12, 18].map(hh => hh + LIVECYC_LAG_H))
                                  - LIVECYC_LAG_H * 3600000).getUTCHours()).padStart(2, '0')
          + 'Z cycle; this season’s files have landed 4 to 6 hours after their cycle, once the NHC advisory they take their track from is out'));
    }
    if (storms.length) host.appendChild(h('p', { class: 'cap', style: 'margin:0 0 6px',
      text: 'The vendor’s figures, shown as published; last poll '
        + (RK.polled ? clockFull(Date.parse(RK.polled), local()) : 'unknown') + '.' }));
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('hurricane.json', 30);
    const rk = await WXD.get('reask.json', 10);
    const sz = await WXD.get('season.json', 1440);
    const mk = await WXM.loadGroup('hurricane');
    const adv = await WXD.get('advisories/index.json', 30).catch(() => ({ data: null }));
    const geo = await fetch(WXC.asset('hurricane-geo.json')).then(x => x.json()).catch(() => null);
    H = r.data; GEO = geo; NATION = geo ? geo.nation : null; RK = rk.data; SZN = sz.data; MK = WXM.hurricaneMarkets();
    ADV = (adv && adv.data) || null;
    if (window.WXStorm && WXStorm.setAdvisories) WXStorm.setAdvisories(ADV);
    // the roster is what lets the vendor lane see that a numbered depression
    // and the named storm it became are one system
    if (window.WXStorm && WXStorm.setRoster) WXStorm.setRoster((H && H.storms) || []);
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 30));
    if (mk) { const q = WXC.statusEl([mk], 10); q.insertBefore(document.createTextNode('Quotes: '), q.firstChild); st.appendChild(q); }
    if (!H) { $('#basin').innerHTML = ''; $('#basin').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    // the basin buttons only: the zoom bar is a .bar too and its buttons are not
    // a selection. Changing ocean resets the view, since a window over one means
    // nothing over the other.
    [['b1', 'AL'], ['b2', 'EP']].forEach(([id, b]) => {
      $('#' + id).onclick = () => {
        basin = b;
        ['b1', 'b2'].forEach(x => $('#' + x).classList.remove('on'));
        $('#' + id).classList.add('on');
        resetView(); draw(); drawStorms(); drawLandfall(); drawVendorNote(); basinSections(); drawDiscussion(); drawLoops();
        loadRelief(b);
        loadFeatures(b);
        if (window.WXStorm && WXStorm.setBasin) {
          WXStorm.setBasin(b);
          Promise.resolve(WXStorm.draw(RK, MK)).then(() => draw()).catch(() => {});
        }
      };
    });
    /* A link can name the ocean it wants. The cyclone area page shows the two
       basins as maps and opens this page on whichever one was clicked, which
       only works if the choice survives the hop. */
    const want = (WXC.param('basin') || '').toUpperCase();
    if (want === 'EP' || want === 'PACIFIC') {
      basin = 'EP';
      $('#b1').classList.remove('on');
      $('#b2').classList.add('on');
      resetView();
    }
    draw(); drawStorms(); drawSeason(); drawLandfall(); drawVendorNote(); basinSections();
    drawDiscussion();
    // the sea surface temperature layer is a day-old field and the map does not wait for it
    WXD.get('sst.json', 2880).then(x => { SST = (x && x.data) || null; if (SST) draw(); }).catch(() => {});
    // the storms' satellite loops, read again every half hour so a page left open keeps up,
    // and drawn again when a loop's newest frame passes the age at which it is not shown
    let loopSig = null;
    const readLoops = () => WXD.get('floater/index.json', 30, { store: false }).then(x => {
      const d = (x && x.data) || null;
      const sig = JSON.stringify(Object.entries((d && d.storms) || {})
        .map(([k, w]) => [k, (w.frames || []).slice(-1)[0], WXC.loopFresh(w.frames)]));
      if (sig !== loopSig) { loopSig = sig; FLOAT = d; drawLoops(); }
    }).catch(() => {});
    readLoops();
    setInterval(readLoops, 30 * 60000);
    loadRelief(basin);
    loadFeatures(basin);
    if (window.WXStorm) {
      WXStorm.init(tip);
      // the vendor's latest file belongs to the storm, not to a section of its own
      if (WXStorm.setFilePanel) WXStorm.setFilePanel(stormFile);
      // this page holds the registry, so it is the one that can name a
      // reference location the vendor's file never mentioned
      if (WXStorm.setLocationName) WXStorm.setLocationName(id => (locationById(id) || {}).name || null);
      if (WXStorm.setBasin) WXStorm.setBasin(basin);
      // the map's dots ask this module for a location's series, so the map is
      // drawn again once the ledgers have landed and the dots can answer
      const wantLoc = (WXC.param('loc') || '').toUpperCase();
      Promise.resolve(WXStorm.draw(RK, MK)).then(() => { draw(); focusLoc(wantLoc); }).catch(() => {});
    }
  }
  return { init };
})();
