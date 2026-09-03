/* Hurricane tracking: NHC forecast tracks, cones, wind-speed probabilities
   and formation odds over the basin geography, the season's count, and,
   with the market layer on, the exchange's hurricane contracts at their
   quoted prices.

   Data in: hurricane.json (storms with NHC wind probabilities, outlook,
   season), market/hurricane.json through WXM (the quote job's hurricane
   group), reask.json (the vendor live-storm lane, usually off), and
   assets/hurricane-geo.json (countries, the coastal states and Hawaii, the
   counties the landfall contracts name, the nation coastline, the 163 wind
   reference locations, and which view each region is drawn on). The drawn storm position is labelled from the
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
  const TYPES = { HU: 'hurricane', MH: 'major hurricane', TS: 'tropical storm', TD: 'tropical depression', STS: 'subtropical storm', STD: 'subtropical depression',
    PTC: 'potential tropical cyclone', PC: 'post-tropical cyclone', EX: 'extratropical', RL: 'remnant low', LO: 'low', DB: 'disturbance' };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const RAMP = ['#fde8c8', '#f9cf94', '#f2ad5e', '#e68a2e', '#cf6a14', '#a84e0b', '#7a3607'];
  let H = null, GEO = null, NATION = null, MK = null, RK = null, SZN = null, basin = 'AL', tip = null;

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
     one dollar less the Yes bid, because there are no sellers and the two sides
     sum to a dollar. */
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
  const mph = kt => (kt == null ? null : Math.round(kt * 1.151));
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
  // the view a landfall region is drawn on: the geometry tags Hawaii's state and
  // counties as Pacific, and every other region is an Atlantic one
  const regionBasin = label => (GEO && GEO.basins && GEO.basins[regionKey(label)]) || 'AL';
  /* Nothing from one ocean on the other's view.

     A basin is a different question, so the Pacific view carries no Atlantic
     storm and no Atlantic contract, and the other way round. A storm's ocean
     comes from the NHC roster, matched by name, and anything the roster does
     not name is treated as Atlantic, which is where this lane has run. */
  const stormBasin = nm => {
    const r = ((H && H.storms) || []).find(x => String(x.name).toLowerCase() === String(nm || '').toLowerCase());
    return r && r.basin ? r.basin : 'AL';
  };
  const inBasin = nm => (basin === 'AL' ? stormBasin(nm) === 'AL' : stormBasin(nm) !== 'AL');
  // what the owner has said the exchange will list but it has not listed yet
  const EXPECT = (window.WX && WX.expected) || {};
  const expectLandfall = () => (EXPECT.landfall || []).filter(e => (e.basin || 'AL') === basin);
  // the landfall contracts whose region is on the view being looked at
  const landfallHere = () => { const m = market('HLF'); return m ? m.contracts.filter(c => regionBasin(c.label) === basin) : []; };

  // ---- the map
  function drawNhc(svg, X, Y, b, g) {
    // g is the glyph scale: world units per screen unit at the current zoom.
    // Every size below is a screen intention, so each is multiplied by it.
    const halo = 'stroke-width:' + (3 * g).toFixed(2) + 'px';
    const outl = (H.outlook || []).filter(o => (o.basin || '') === BASINS[b].outlook);
    const oplaced = [];
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
         two days after it went inland, and its landfall point was labelled the
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
      pts.forEach((p, i) => {
        svg.appendChild(el('circle', { cx: X(p.lon), cy: Y(p.lat), r: (p.tau === 0 ? 6 : 4.5) * g, fill: ptColor(p), stroke: 'var(--panel)', 'stroke-width': 1.2 * g }));
        if (p.label) svg.appendChild(txt(p.label.replace(':00', '') + (p.kt ? ' · ' + p.kt + 'kt' : ''), { x: X(p.lon) + 8 * g, y: Y(p.lat) + (i % 2 ? 14 : -8) * g, 'font-size': 9 * g, fill: 'var(--ink)', class: 'lbl', style: halo }));
        if (p.tau === 0) svg.appendChild(txt((p.type || s.classification) + ' ' + s.name + ' · ' + (p.kt != null ? p.kt : s.intensityKt) + 'kt · adv ' + advNow,
          { x: X(p.lon) + 10 * g, y: Y(p.lat) - 20 * g, 'font-size': 12.5 * g, 'font-weight': 700, fill: 'var(--navy)', class: 'lbl', style: halo }));
        // the hover target: an invisible circle wider than the drawn point
        const now = p.tau === 0;
        const rows = [
          ['Valid', esc(p.label || '—')],
          ['Wind', p.kt != null ? p.kt + ' kt (' + mph(p.kt) + ' mph)' : '—'],
          ['Type', typeText(p.type || s.classification)],
          ['Position', position(p.lat, p.lon)],
          ['Advisory', esc(advNow)],
          now ? null : ['Lead', (p.tau != null ? p.tau : '—') + ' h'],
          now ? ['Pressure', s.pressureMb ? esc(s.pressureMb) + ' mb' : '—'] : null,
          now ? ['Movement', s.movementDir != null ? s.movementDir + '° (' + compass(s.movementDir) + ')' + (s.movementKt != null ? ' at ' + s.movementKt + ' kt' : '') : '—'] : null,
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
    return { storms: storms.length, areas: outl.length,
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
        v2.thresholds.map((t, i) => (v2.p[i] ? ['&gt; ' + t + ' mph', v2.p[i] + '%'] : null))
          .filter(Boolean).slice(0, cap).forEach(r => rows.push(r));
        if (v2.interim) {
          // the interim's rungs under their own heading; a published zero at
          // every rung is printed at the lowest one, since it is a figure
          rows.push(['Metryc interim', 'received ' + utc(v2.interim.received)]);
          const im = v2.thresholds.map((t, i) => (v2.interim.p[i] ? ['interim &gt; ' + t + ' mph', v2.interim.p[i] + '%'] : null))
            .filter(Boolean);
          (im.length ? im.slice(0, cap) : [['interim &gt; ' + v2.thresholds[0] + ' mph', '0%']]).forEach(r => rows.push(r));
        }
      });
      foot = esc((RK && RK.attribution) || 'Powered by Reask') + '; probabilities as published'
           + (list.length > 1 ? '; each storm is its own hazard' : '');
    } else foot = (RK && RK.enabled) ? 'no storm probabilities published' : 'no live-storm probabilities (lane off)';
    return tip.rows(esc(L.name) + ' (' + esc(L.id) + ')', rows, foot);
  }
  const locationById = id => ((GEO && GEO.locations) || []).find(L => L.id === id);

  // ---- zoom and pan on the basin map
  //
  // The whole map is drawn in one coordinate space, so zooming is a matter of
  // moving the viewBox rather than redrawing anything: shapes, dots and their
  // links keep working untouched at every scale. The view survives a redraw
  // (quotes refresh every ten minutes) and resets when the basin changes,
  // because a window over one ocean means nothing over another.
  const VIEW0 = { x: 0, y: 0, w: 980, h: 600 };
  let view = Object.assign({}, VIEW0);
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
    if (lbl) lbl.textContent = z < 1.02 ? 'whole basin' : Math.round(z * 10) / 10 + '×';
    svg.classList.toggle('grab', z > 1.02);
  }
  function zoomAbout(factor, cx, cy) {
    const z = VIEW0.w / view.w;
    const want = Math.min(Math.max(z * factor, 1), MAXZ);
    if (Math.abs(want - z) < 1e-6) return;
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
  const resetView = () => { view = Object.assign({}, VIEW0); applyView(); scheduleRescale(); };
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
    const end = ev => {
      if (!drag) return;
      try { svg.releasePointerCapture(drag.id); } catch (e) { /* already released */ }
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
    mk('Reset', 'back to the whole basin', resetView);
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'basinZoomLevel', text: 'whole basin' }));
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: '· scroll to zoom, drag to pan' }));
  }

  function draw() {
    const B = BASINS[basin];
    const [b0, la0, b1, la1] = B.box, W = 980, Hh = 600;
    const kx = W / (b1 - b0), ky = Hh / (la1 - la0);
    const X = lon => (lon - b0) * kx, Y = lat => (la1 - lat) * ky;
    const svg = $('#basin'); svg.innerHTML = '';
    const g = GS();          // glyph scale at the current zoom, 1 at whole basin
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: Hh, fill: 'var(--map-sea)' }));
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
      // a country is shaded on the view its contract is listed on (the Atlantic
      // board carries every country), so the Pacific view shows Mexico as land
      // with a pointer to that board rather than as a priced region
      Object.entries(GEO.countries || {}).forEach(([nm, rr]) => {
        if (!inView(rr)) return;
        const label = Object.keys(lf || {}).find(k => regionKey(k) === nm) || nm;
        if (regionBasin(label) === basin) { const [f, t, u] = fillFor(label, nm); poly(rr, f, 'var(--map-line)', .6 * g, t, u, nm); return; }
        const listed = lf && lf[label];
        poly(rr, 'var(--map-land)', 'var(--map-line)', .6 * g,
             tip.rows(esc(nm), [], listed ? 'its landfall contract is priced on the Atlantic view' : null));
      });
      (NATION || []).forEach(r => poly([r], 'var(--map-land)', 'var(--map-line)', .6 * g));
      // a state or county is drawn on its own view only
      Object.entries(GEO.states || {}).forEach(([nm, rr]) => { if (regionBasin(nm) !== basin) return; const [f, t, u] = fillFor(nm, nm); poly(rr, f, 'var(--map-line)', .7 * g, t, u, nm); });
      Object.entries(GEO.counties || {}).forEach(([nm, rr]) => { if (regionBasin(nm) !== basin) return; const [f, t, u] = fillFor(nm, nm); poly(rr, f, 'var(--ink)', .8 * g, t, u, nm); });
    }
    const counts = drawNhc(svg, X, Y, basin, g);
    // the reference locations: small dots, scaled by the vendor's P(gust > 80 mph) when the lane is live
    let vendorShown = 0;
    // the locations that have a delivery series loaded: those are the dots that
    // open one, which is not the same set as those with a current livecyc row
    const withSeries = (window.WXStorm && WXStorm.sites) ? WXStorm.sites() : {};
    (GEO && GEO.locations || []).forEach(L => {
      if (L.lon < b0 || L.lon > b1 || L.lat < la0 || L.lat > la1) return;
      const v = vendorSite(L.id);
      // a location the interim settles high is a signal even when the newest
      // cycle has moved on and reads zero there
      const im80 = x => (x.interim && x.interim.p ? (x.interim.p[x.thresholds.indexOf(80)] || 0) : 0);
      const any = !!(v && v.some(x => x.p.some(p => p > 0) || (x.interim && x.interim.p.some(p => p > 0))));
      const p80 = v ? Math.max.apply(null, v.map(x => Math.max(x.p80 || 0, im80(x)))) : 0;
      const r = (p80 > 0 ? 3 + 9 * Math.sqrt(Math.min(p80, 100) / 100) : (any ? 3 : 2.2)) * g;
      if (any) vendorShown++;
      const c = el('circle', { cx: X(L.lon), cy: Y(L.lat), r, fill: any ? 'rgba(192,57,43,.75)' : 'var(--muted)', 'fill-opacity': any ? .85 : .55, stroke: 'var(--panel)', 'stroke-width': .6 * g });
      attach(c, locationTip(L, v));
      if (any || withSeries[L.id]) {
        // a click goes to the location's card in the storm section below,
        // where its series is already drawn, rather than drawing it again here
        c.style.cursor = 'pointer';
        c.setAttribute('role', 'button');
        c.setAttribute('tabindex', '0');
        c.setAttribute('aria-label', L.name + ' — go to its storm probability series');
        const hit = ev => {
          ev.preventDefault(); ev.stopPropagation();
          if (window.WXStorm && WXStorm.showSite) WXStorm.showSite(L.id);
        };
        // replaces the pin handler attach() set: the card below is where this
        // click goes, and a pinned box left behind here is noise on the way
        c.onclick = hit;
        c.removeAttribute('data-tip-pin');
        c.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') hit(ev); });
      }
      svg.appendChild(c);
    });
    wireZoom(); applyView();
    $('#modeTitle').textContent = B.name.toUpperCase() + ' · NHC forecast tracks, cones and formation odds' + (shadedHere ? ' · landfall regions shaded by the exchange’s Yes price' : '');
    /* The map's caption, in two blocks: what is drawn and what a click does.
       The conditional sentences carry facts that are not always true, so a
       caption never describes shading or an outlook that is not there. */
    {
      const cap = $('#basinCap'); cap.innerHTML = '';
      const para = t => cap.appendChild(h('p', { class: 'cap', style: 'margin:8px 2px 0', text: t }));
      para((counts.storms ? '' : 'No active tropical cyclones in this basin at the last update. ')
        + 'Orange dashed regions show NHC seven-day formation odds. NHC cones and tracks are shown when a '
        + 'storm is active. '
        + (basin === 'EP' ? 'The Central Pacific outlook is issued by CPHC and is not in this feed, so that '
                          + 'part of the map shows storms only. ' : '')
        + (shadedHere ? 'States, countries and the named counties are shaded by the Yes price of the Major '
              + 'Hurricane (Category 3+) landfall contract for that region, which pays on a landfall in the region '
              + 'or within 50 miles of its border; clicking a shaded region opens '
              + 'that contract on the exchange. ' : '')
        + 'The point locations are associated with the Live Hurricane contract.'
        + ((counts.stale || []).length
            ? ' ' + counts.stale.join(' and ') + (counts.stale.length > 1 ? ' are' : ' is')
              + ' drawn at the position on the latest advisory: the cone service has stopped updating '
              + (counts.stale.length > 1 ? 'them' : 'it') + ', so the superseded cone and forecast track are not shown.'
            : ''));
      para('During a live storm, clicking a red reference location opens its probability series below the '
        + 'map; clicking the same location again opens its wind contract.');
    }
    if (shadedHere || vendorShown) {
      const kg = el('g', { 'pointer-events': 'none' });
      const bx = 16, by = Hh - 84, bw = 132;
      kg.appendChild(el('rect', { x: bx - 8, y: by - 16, width: bw + 108, height: 76, rx: 5,
                                  fill: 'var(--panel)', 'fill-opacity': .88, stroke: 'var(--line)', 'stroke-width': .8 }));
      let yy = by;
      if (shadedHere) {
        kg.appendChild(txt('Landfall contract, Yes price', { x: bx, y: yy - 3, 'font-size': 10, 'font-weight': 700, fill: 'var(--ink)' }));
        for (let i = 0; i < 48; i++) {
          kg.appendChild(el('rect', { x: bx + i * (bw / 48), y: yy + 2, width: bw / 48 + .6, height: 9,
                                      fill: ramp(i / 47) }));
        }
        kg.appendChild(txt('0¢', { x: bx, y: yy + 22, 'font-size': 9, fill: 'var(--muted)' }));
        kg.appendChild(txt('100¢', { x: bx + bw, y: yy + 22, 'text-anchor': 'end', 'font-size': 9, fill: 'var(--muted)' }));
        yy += 34;
      }
      kg.appendChild(el('circle', { cx: bx + 5, cy: yy + 2, r: 3.4, fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.4 }));
      kg.appendChild(txt('a reference location', { x: bx + 14, y: yy + 5.5, 'font-size': 9.5, fill: 'var(--ink)' }));
      if (vendorShown) {
        yy += 14;
        kg.appendChild(el('circle', { cx: bx + 5, cy: yy + 2, r: 4.6, fill: 'rgba(192,57,43,.55)', stroke: 'rgba(192,57,43,.9)', 'stroke-width': 1 }));
        kg.appendChild(txt('sized by the chance of a gust over 80 mph', { x: bx + 14, y: yy + 5.5, 'font-size': 9.5, fill: 'var(--ink)' }));
      }
      svg.appendChild(kg);
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
        h('span', { text: s.basin + ' · ' + s.intensityKt + ' kt · ' + (s.pressureMb || '--') + ' mb · advisory ' + s.advisory + (s.geometryAdvisory && String(s.geometryAdvisory).replace(/^0+/, '') !== String(s.advisory).replace(/^0+/, '') ? ' (map shows ' + s.geometryAdvisory + ')' : '') }),
        s.advisoryUrl ? h('a', { href: s.advisoryUrl, text: 'NHC advisory', target: '_blank', rel: 'noopener' }) : h('span'),
        s.windProbsUrl ? h('a', { href: s.windProbsUrl, text: 'wind speed probabilities', target: '_blank', rel: 'noopener' }) : h('span'),
      ]));
      if (s.windProbs && s.windProbs.length) {
        const tb = h('table', { class: 'pws' });
        // the product is issued in knots; the page reads in mph, at the
        // strengths those thresholds define — 34 kt is the 39 mph of a
        // tropical storm, 64 kt the 74 mph of a hurricane
        tb.appendChild(h('tr', {}, [h('th', { text: 'NHC five-day cumulative probability, sustained winds' }), h('th', { class: 'num', text: '≥39 mph' }), h('th', { class: 'num', text: '≥58 mph' }), h('th', { class: 'num', text: '≥74 mph' })]));
        s.windProbs.slice(0, 14).forEach(r => {
          const tr = h('tr', {}, [h('td', { text: r.location }), h('td', { class: 'num', text: r.p34 + '%' }), h('td', { class: 'num', text: r.p50 + '%' }), h('td', { class: 'num', text: r.p64 + '%' })]);
          attach(tr, tip.rows(esc(r.location) + ' — NHC wind speed probabilities',
            [['≥39 mph (34 kt)', r.p34 != null ? r.p34 + '%' : '—'], ['≥58 mph (50 kt)', r.p50 != null ? r.p50 + '%' : '—'], ['≥74 mph (64 kt)', r.p64 != null ? r.p64 + '%' : '—']],
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
    m.contracts.slice().sort((a, b) => String(a.spec).localeCompare(String(b.spec))).forEach(c => {
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
      + 'Category 4 on or before the date named. The exchange\u2019s terms are explicit that a higher or lower category does not '
      + 'qualify, so a Category 5 landfall does not resolve this contract Yes. Each date is cumulative, asking whether at least '
      + 'one qualifying landfall has happened by then. The climatology drawn against it counts landfalls on the continental '
      + 'United States only: Maria crossed Puerto Rico at exactly Category 4 in 2017 and is not in the line. The exchange\u2019s '
      + 'terms say \u201cthe United States\u201d without saying whether a Puerto Rico or Virgin Islands landfall counts, so a '
      + 'reader taking the wider reading should treat this line as the low end.' }));
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
    const endLabel = (scale, col, nm) => {
      const i = days.length - 1;
      const v = cond(cum[days[i][0]] != null ? cum[days[i][0]] : cum['11-30']);
      const yy = y(Math.min(1 - Math.pow(1 - v, scale), ymax));
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
    let firstDot = null;
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
      if (!firstDot) firstDot = { x: x(i), y: y(Math.min(c.mid, ymax)) };
    });
    // the curves carry their own names; only the dots still need one, and it
    // goes on the first of them rather than in a row underneath
    if (firstDot) {
      svg.appendChild(txt('a listed contract', { x: firstDot.x, y: firstDot.y - 9, 'text-anchor': 'middle',
                          'font-size': 10, 'font-weight': 700, fill: 'var(--accent)', 'pointer-events': 'none' }));
    }
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
    $('#laddersCap').textContent = 'Exchange contracts as quoted ' + clockFull(Date.parse(MK.asof), local()) + (MK.stale ? ' (stale)' : '') + '; the bar is the Yes price in cents, midway between the Yes bid and one dollar less the No bid, or (*) the one side with bids (hover shows both bids). Where both sides bid the minimum the book is empty and no price is shown. There are no sellers on this exchange, only bids to buy Yes or No. Season counts are this site’s own reading of the best tracks, not the exchange’s settlement count.';
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

    // both paces: the one that is filled is the one the ladder's marker follows
    const lead = fcurve || curve;
    if (lead) {
      const d = lead.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('');
      svg.appendChild(el('path', { d: d + 'L' + x(t1).toFixed(1) + ',' + y(0) + 'L' + x(t0).toFixed(1) + ',' + y(0) + 'Z', fill: 'var(--cool)', 'fill-opacity': .12, 'pointer-events': 'none' }));
      svg.appendChild(el('path', { d, fill: 'none', stroke: 'var(--cool)', 'stroke-width': 2, 'pointer-events': 'none' }));
    }
    if (fcurve && curve) {
      svg.appendChild(el('path', { d: curve.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join(''),
        fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.4, 'stroke-dasharray': '5 4', 'pointer-events': 'none' }));
      svg.appendChild(txt('an average season', { x: x(t1) - 4, y: y(curve[curve.length - 1][1]) - 5, 'text-anchor': 'end', class: 'ax lbl', fill: 'var(--muted)' }));
    }
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
    // the count so far, large, and what the pace says
    const at = c => c ? (c.find(p => p[0] >= now) || c[c.length - 1])[1] : null;
    const paceNow = at(lead), climNow = at(curve);
    svg.appendChild(txt(String(events.length), { x: L + 12, y: T + 34, 'font-size': 30, 'font-weight': 700, fill: 'var(--navy)' }));
    const climTgt = climTarget == null ? null : Math.round(climTarget * 100) / 100;
    const note = 'so far · ' + (fc[cfg.key] != null
      ? esc(fc.label || fc.source || 'forecast') + ' ' + fc[cfg.key] + (month != null && fcTarget != null ? ', ' + MONTHS[month] + ' share ' + fcTarget : '')
      : (climTgt == null ? 'no climatology for this period'
         : month == null ? 'an average season ends near ' + climTgt
         : 'an average ' + MONTHS[month] + ' has ' + climTgt));
    svg.appendChild(txt(note, { x: L + 12, y: T + 56, class: 'ax' }));
    if (paceNow != null) svg.appendChild(txt('pace implied by today: ' + (Math.round(paceNow * 10) / 10)
      + (fcurve && climNow != null ? ' (an average season ' + (Math.round(climNow * 10) / 10) + ')' : ''),
      { x: L + 12, y: T + 70, class: 'ax' }));

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
       ground to sit on. A small panel-coloured chip riding just above each
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
      + 'onto land, so a storm that stays offshore has not made landfall for this contract even where hurricane-force winds reach the area. '
      + (elsewhere ? 'The ' + (elsewhere > 1 ? elsewhere + ' regions' : 'one region') + ' in the other basin '
                     + (elsewhere > 1 ? 'are' : 'is') + ' listed on the ' + (basin === 'AL' ? 'Pacific' : 'Atlantic') + ' view. ' : '')
      + 'Prices as quoted ' + clockFull(Date.parse(MK.asof), local()) + '. Yes green, No red; the Yes price is midway between the Yes bid '
      + 'and one dollar less the No bid where both sides have bids, else the one side shown, and there are no sellers, only bids to buy '
      + 'Yes or No. A region whose book is empty, both sides bidding the minimum, shows no price and is left unshaded. “Pays” in the box is what a dollar of payout costs at the price a Yes could be bought at now, net of the '
      + WXM.feeCents() + '¢ per-side execution fee.' }));
  }

  // ---- the vendor lane
  //
  /* One file on the table, not a stack of them.

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
     supersedes everything. */
  const VENDOR_ROWS = 16;
  function shownFile(s) {
    if (s.final && s.final.sites && Object.keys(s.final.sites).length) return { kind: 'final', label: 'Metryc final', file: s.final };
    if (s.interim && s.interim.sites && Object.keys(s.interim.sites).length) return { kind: 'interim', label: 'Metryc interim', file: s.interim };
    if (s.livecyc && s.livecyc.sites && Object.keys(s.livecyc.sites).length) return { kind: 'livecyc', label: 'LiveCyc', file: s.livecyc };
    return null;
  }
  // the table always says which file it is and when that file arrived
  function sourceLine(shown) {
    const n = Object.keys(shown.file.sites || {}).length;
    const got = shown.file.lastModified ? ' · file received ' + utc(shown.file.lastModified) : '';
    if (shown.kind === 'final') return 'Metryc final' + got + ' · peak gust at ' + n + ' locations';
    if (shown.kind === 'interim') return 'Metryc interim' + got + ' · ' + n + ' locations';
    return 'LiveCyc cycle ' + utc(shown.file.forecastTime) + got + ' · ' + n + ' locations with non-zero probability';
  }
  function drawVendor() {
    const host = $('#vendor'); host.innerHTML = '';
    if (!RK) { host.appendChild(h('p', { class: 'cap', text: 'Vendor lane status unavailable.' })); return; }
    if (!RK.enabled) {
      host.appendChild(h('p', { class: 'cap', text: 'Not enabled on this site (' + (RK.reason || 'off') + '). When a storm is active and the lane is on, this section shows the vendor’s probability that the peak gust exceeds each threshold at the reference locations, as published, four times a day.' }));
      return;
    }
    const storms = (RK.storms || []).filter(s => inBasin(s.name)).slice().sort((a, b) => {
      const ta = WXStorm.stampOf(a), tb = WXStorm.stampOf(b);
      return (tb == null ? Infinity : tb) - (ta == null ? Infinity : ta);
    });
    if (storms.some(s2 => !WXStorm.dormant(s2) && s2.livecyc)) {
      host.appendChild(scheduleLine('Next LiveCyc file expected about',
        [0, 6, 12, 18].map(hh => hh + LIVECYC_LAG_H),
        'the ' + String(new Date(nextMark([0, 6, 12, 18].map(hh => hh + LIVECYC_LAG_H))
                                  - LIVECYC_LAG_H * 3600000).getUTCHours()).padStart(2, '0')
          + 'Z cycle; this season’s files have landed 4 to 6 hours after their cycle, once the NHC advisory they take their track from is out'));
    }
    if (!storms.length && basin !== 'AL') {
      host.appendChild(h('p', { class: 'cap', text: 'No storm in this ocean has published probabilities. A storm appears here on its first vendor delivery.' }));
    }
    if (!storms.length && basin === 'AL') {
      host.appendChild(h('p', { class: 'cap', text: 'Lane on; no storm with published probabilities this year yet (last poll ' + (RK.polled ? clockFull(Date.parse(RK.polled), local()) : 'unknown') + ').' }));
    }
    storms.forEach(s => {
      const lc = s.livecyc;
      /* A storm that has stopped updating folds shut. Its ladder is a record
         of the last delivery rather than a probability for today, so it does
         not share the page with the running storms; the record stays one
         click away. */
      const over = WXStorm.dormant(s);
      let into = host;
      if (over) {
        const t = WXStorm.stampOf(s);
        const det = h('details', { class: 'stormdone' });
        det.appendChild(h('summary', {}, [h('b', { text: s.name + ' ' + s.year }),
          h('span', { text: WXStorm.doneLabel(s)
            + (t ? ' · last delivery ' + new Date(t).toISOString().slice(0, 10) : '') + ' · click to view' })]));
        into = h('div');
        det.appendChild(into);
        host.appendChild(det);
      }
      const shown = shownFile(s);
      const later = shown && shown.kind === 'interim' && s.livecyc && s.livecyc.lastModified
                    && Date.parse(s.livecyc.lastModified) > Date.parse(s.interim.lastModified || 0);
      into.appendChild(h('div', { class: 'stormrow' }, [h('b', { text: s.name + ' ' + s.year }),
        h('span', { text: shown ? sourceLine(shown) : 'no vendor file published yet' })]));
      if (!shown) return;
      const code2 = String(s.name || '').replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase();
      // the exchange's wind contract for this storm and location, where one is
      // listed, so a row opens its market the way every other priced surface does
      const linkRow = (tr, id, nm) => {
        const lm = MK && (MK.markets || []).find(m2 => m2.symbol === 'L' + code2 + String(id).toUpperCase());
        const c0 = lm && (lm.contracts || [])[0];
        const url = lm && c0 ? WXM.contractUrl(lm.productConid, c0.conidYes || c0.conid) : null;
        if (url) WXM.linkTo(tr, url, 'Open the ' + nm + ' wind contract on IBKR');
      };
      const tb = h('table');
      const all = Object.entries(shown.file.sites || {});
      let rows;
      if (shown.kind === 'final') {
        /* The final file is a different quantity: the gust each location
           recorded, in miles per hour, with no ladder behind it. It replaces
           the probabilities rather than joining them. */
        const gust = r => (r.peakGustMph == null ? -Infinity : r.peakGustMph);
        // the vendor publishes a full float; a tenth of a mile an hour is the
        // precision the figure carries and the settlement is read at
        const mph = v => (v == null ? null : v.toFixed(1) + ' mph');
        rows = all.slice().sort((a, b) => gust(b[1]) - gust(a[1])
                                       || String(a[1].name || '').localeCompare(String(b[1].name || ''))).slice(0, VENDOR_ROWS);
        tb.appendChild(h('tr', {}, [h('th', { text: 'Reference location' }), h('th', { class: 'num', text: 'Peak gust, mph' })]));
        rows.forEach(([id, r]) => {
          const tr = h('tr', {}, [h('td', { text: r.name + ' (' + id + ')' }),
                                  h('td', { class: 'num', text: mph(r.peakGustMph) || '—' })]);
          const L = locationById(id) || { id, name: r.name, region: null, country: null, state: null };
          attach(tr, tip.rows(esc(r.name) + ' (' + esc(id) + ')',
            [['Region', esc(L.region)], ['Country', esc(L.country)], ['State', esc(L.state)],
             ['Storm', esc(s.name) + ' ' + s.year],
             ['Peak gust', mph(r.peakGustMph) || 'not covered']],
            esc((RK && RK.attribution) || 'Powered by Reask') + '; the final file, which the wind contracts settle on'));
          linkRow(tr, id, r.name);
          tb.appendChild(tr);
        });
      } else {
        /* The vendor's lowest rung is on the table.

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
        tb.appendChild(h('tr', {}, [h('th', { text: 'Reference location' })].concat(cols.map(t => h('th', { class: 'num', text: '> ' + t + ' mph' })))));
        rows.forEach(([id, r]) => {
          const tr = h('tr', {}, [h('td', { text: r.name + ' (' + id + ')' })].concat(idx.map(i => h('td', { class: 'num', text: (r.p[i] != null ? r.p[i] : 0) + '%' }))));
          const L = locationById(id) || { id, name: r.name, region: null, country: null, state: null };
          attach(tr, locationTip(L, { storm: s.name, file: shown.label, thresholds: thr, p: r.p,
                                      forecastTime: shown.kind === 'livecyc' ? shown.file.forecastTime : null,
                                      received: shown.file.lastModified }));
          linkRow(tr, id, r.name);
          tb.appendChild(tr);
        });
      }
      into.appendChild(h('div', { class: 'card', style: 'padding:0' }, [tb]));
      /* Why a later cycle is not the one on the table, where that applies, and
         what was left off. LiveCyc is forward-looking from its own start, so a
         location whose peak has already passed reads near zero on a cycle
         issued after it while the interim holds the figure for the landfall
         that happened. */
      const notes = [];
      if (later) notes.push('A LiveCyc cycle has arrived since this file. LiveCyc looks forward from its own '
        + 'start, so it reads near zero where the peak has already passed; the interim is the vendor’s word on '
        + 'what happened and is the one shown.');
      /* The final file carries every reference location, whatever the storm did.
         Milton's lists Brownsville and Corpus Christi, neither of which the storm
         went near, so the ranking and the cap are what make it readable and the
         count has to say which list it is counting. */
      if (all.length > rows.length) notes.push(shown.kind === 'final'
        ? 'Showing the ' + rows.length + ' of ' + all.length + ' reference locations that recorded the highest gusts. '
          + 'The final file carries every location on the vendor’s list, not only those the storm reached.'
        : 'Showing the ' + rows.length + ' of ' + all.length + ' locations in the file that stand highest.');
      if (notes.length) into.appendChild(h('p', { class: 'cap', text: notes.join(' ') }));
    });
    host.appendChild(h('p', { class: 'cap attrib', text: (RK.attribution || 'Powered by Reask') + '. Probabilities are the vendor’s, shown as published; last poll ' + (RK.polled ? clockFull(Date.parse(RK.polled), local()) : 'unknown') + '.' }));
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('hurricane.json', 30);
    const rk = await WXD.get('reask.json', 10);
    const sz = await WXD.get('season.json', 1440);
    const mk = await WXM.loadGroup('hurricane');
    const geo = await fetch(WXC.asset('hurricane-geo.json')).then(x => x.json()).catch(() => null);
    H = r.data; GEO = geo; NATION = geo ? geo.nation : null; RK = rk.data; SZN = sz.data; MK = WXM.hurricaneMarkets();
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
        resetView(); draw(); drawStorms(); drawLandfall(); drawVendor(); basinSections(); drawDiscussion();
        if (window.WXStorm && WXStorm.setBasin) {
          WXStorm.setBasin(b);
          Promise.resolve(WXStorm.draw(RK, MK)).then(() => draw()).catch(() => {});
        }
      };
    });
    draw(); drawStorms(); drawSeason(); drawLandfall(); drawVendor(); basinSections();
    drawDiscussion();
    if (window.WXStorm) {
      WXStorm.init(tip);
      if (WXStorm.setBasin) WXStorm.setBasin(basin);
      // the map's dots ask this module for a location's series, so the map is
      // drawn again once the ledgers have landed and the dots can answer
      Promise.resolve(WXStorm.draw(RK, MK)).then(() => draw()).catch(() => {});
    }
  }
  return { init };
})();
