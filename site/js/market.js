/* The market layer, in one file, behind one switch.

   WX.market is the default state per build target: 'live', 'placeholder'
   or 'off'; ?market=on|off on the URL overrides it (on means WX.marketSource,
   the live source where one is configured). When off, every function here
   returns null and the pages reserve no space for ladders, price panels,
   implied values or divergence encodings.

   'live' reads the snapshots the quote job writes from the exchange's public
   market-data endpoints (pipeline/market.py): per-station ladders with the
   Yes-side top of book, a rolling two-day history of each strike's quote,
   the market-implied medians, and the hurricane and climate product groups.
   The numbers are the exchange's own, not fee adjusted. On this exchange
   there are no sellers: every resting order is a bid to buy Yes or a bid to
   buy No, and the two sides of a pair sum to one dollar. The feed's "ask"
   on a Yes contract is therefore one dollar less the No bid, and the pages
   say "No bid"; the Yes price shown is the midpoint between the Yes bid
   and one dollar less the No bid. Pages call load()/loadSummary()/
   loadGroup() first and then the synchronous accessors below.

   'placeholder' reproduces the reference package's labelled synthetic data:
   deterministic ladders and a shaped price path so the layout has something
   to show. None of it is a market value. */
window.WXM = (() => {
  const cfg = () => window.WX || {};
  function mode() {
    const q = new URLSearchParams(location.search).get('market');
    if (q === 'on') return cfg().marketSource || 'placeholder';
    if (q === 'off') return 'off';
    return cfg().market || 'off';
  }
  const on = () => mode() !== 'off';
  const live = () => mode() === 'live';
  const PLACEHOLDER = 'placeholder value';
  const S = { station: null, snap: null, summary: null, groups: {} };

  // ---- loading (live only; the placeholder needs nothing)
  async function load(sid) {
    if (!live()) return null;
    const r = await WXD.get('market/' + sid + '.json', 10);
    S.station = sid; S.snap = r;
    return r;
  }
  async function loadSummary() {
    if (!live()) return null;
    S.summary = await WXD.get('market/summary.json', 10);
    return S.summary;
  }
  async function loadGroup(name) {
    if (!live()) return null;
    S.groups[name] = await WXD.get('market/' + name + '.json', 10);
    return S.groups[name];
  }
  const snapOf = sid => (live() && S.snap && S.snap.data && S.station === sid ? S.snap.data : null);
  const group = name => (live() && S.groups[name] && S.groups[name].data ? S.groups[name].data : null);
  const asofText = r => (r && r.asof ? WXC.clockFull(r.asof, Intl.DateTimeFormat().resolvedOptions().timeZone) : 'no data');

  function label() {
    if (!live()) return PLACEHOLDER;
    return 'ForecastEx quotes, ' + asofText(S.snap || S.summary || S.groups.hurricane || S.groups.climate);
  }

  // ---- placeholder generators (unchanged from the reference package)
  const lgs = (x, k = 0.62) => 1 / (1 + Math.exp(-k * x));
  const seedOf = (sid, day) => { let s = 0; for (const ch of sid + (day || '')) s = (s * 31 + ch.charCodeAt(0)) % 100003; return s; };

  function impliedPlaceholder(city, when) {
    const seed = seedOf(city.station, city.markers && (when === 'today' ? city.markers.day : city.markers.tomorrow));
    const out = { impliedHigh: null, impliedLow: null, divHigh: null, divLow: null, label: PLACEHOLDER };
    if (city.nwsHighTomorrow != null) {
      out.impliedHigh = Math.round((city.nwsHighTomorrow + ((seed % 13) - 6) * 0.9) * 10) / 10;
      out.divHigh = Math.round((out.impliedHigh - city.nwsHighTomorrow) * 10) / 10;
    }
    if (city.nwsLowTomorrow != null) {
      out.impliedLow = Math.round((city.nwsLowTomorrow + (((seed * 7) % 11) - 5) * 0.8) * 10) / 10;
      out.divLow = Math.round((out.impliedLow - city.nwsLowTomorrow) * 10) / 10;
    }
    return out;
  }
  function ladderPlaceholder(city, levels) {
    const seed = seedOf(city.station, city.markers && city.markers.day);
    const step = 1.0;
    const hb = levels.high != null ? levels.high : (city.unit === 'F' ? 80 : 27);
    const lb = levels.low != null ? levels.low : hb - (city.unit === 'F' ? 18 : 10);
    const ih = Math.round((hb + ((seed % 9) - 4) * 0.7) * 10) / 10;
    const il = Math.round((lb + (((seed * 3) % 7) - 3) * 0.6) * 10) / 10;
    const bh = Math.round(hb), bl = Math.round(lb);
    const clamp = p => Math.max(1, Math.min(99, Math.round(100 * p)));
    return {
      label: PLACEHOLDER, live: false,
      high: Array.from({ length: 11 }, (_, i) => { const s = bh + (i - 5) * step; return { strike: s, yes: clamp(1 - lgs(s - ih)) }; }),
      low: Array.from({ length: 11 }, (_, i) => { const s = bl + (i - 5) * step; return { strike: s, yes: clamp(lgs(s - il)) }; }),
    };
  }
  function pricePathPlaceholder(obsRows, forecastSeries, unit, side, K) {
    const hk = s => Math.floor(Date.parse(s) / 36e5);
    const f = {};
    forecastSeries.forEach(rows => (rows || []).forEach(r => { const k = hk(r.t); if (!(k in f)) f[k] = r.tempF; }));
    const pts = []; let runmin = null;
    for (const r of obsRows) {
      const e = f[hk(r.t)]; if (e == null) continue;
      const v = unit === 'F' ? r.tempF : r.tempC, ex = unit === 'F' ? e : (e - 32) * 5 / 9, gap = v - ex;
      runmin = runmin == null ? v : Math.min(runmin, v);
      const p = side === 'h' ? 100 * lgs(v + Math.max(0, 6 - Math.abs(gap)) + gap * 1.6 - K, 0.5)
                             : 100 * lgs(K - runmin - gap * 0.8, 0.6);
      pts.push({ t: Date.parse(r.t), v: Math.max(2, Math.min(98, Math.round(p))) });
    }
    return pts;
  }
  function climatePlaceholder(series, offsetC) {
    const year = new Date().getUTCFullYear();
    const last = s => (series[s] && series[s].length ? series[s][series[s].length - 1] : null);
    const trend = (s, years) => {
      const pts = (series[s] || []).slice(-years); if (pts.length < 3) return 0;
      const n = pts.length, sx = pts.reduce((a, q) => a + q[0], 0), sy = pts.reduce((a, q) => a + q[1], 0);
      const sxx = pts.reduce((a, q) => a + q[0] * q[0], 0), sxy = pts.reduce((a, q) => a + q[0] * q[1], 0);
      return (n * sxy - sx * sy) / (n * sxx - sx * sx);
    };
    const make = (symbol, title, seriesKey, unit, thresholds, years, sigma, monthly) => {
      const L = last(seriesKey); if (!L) return null;
      const slope = trend(seriesKey, monthly ? 120 : 15);
      const contracts = [];
      years.forEach(y => thresholds.forEach(th => {
        const proj = L[1] + slope * (y - L[0]);
        const p = 1 / (1 + Math.exp(-(proj - th) / sigma));
        const seed = seedOf(symbol + th, String(y));
        const yes = Math.max(0.01, Math.min(0.99, Math.round((p + ((seed % 7) - 3) * 0.01) * 100) / 100));
        contracts.push({ year: y, threshold: th, label: (th >= 100 ? th.toFixed(0) : th.toFixed(2)) + ' ' + unit.split(' ')[0],
          expiryLabel: (monthly ? 'any month of ' : '') + y, yes, label2: PLACEHOLDER });
      }));
      return { symbol, title, seriesKey, unit, name: title, contracts, placeholder: true };
    };
    const ta = last('tempAnnual'), sl = last('seaLevel'), co = last('co2'), am = last('amoc');
    const yrs = [year + 1, year + 2, year + 3, year + 4];
    return [
      ta && make('GTTA', 'Annual global temperature thresholds', 'tempAnnual', '°C above preindustrial', [1.5, 1.6, 1.7, 1.8, 1.9, 2.0], yrs, 0.08, false),
      ta && make('GTTM', 'Any-month global temperature thresholds', 'tempMonthly', '°C above preindustrial', [1.6, 1.7, 1.8, 1.9, 2.0, 2.1], yrs, 0.12, true),
      sl && make('GSL', 'Global sea level', 'seaLevel', 'mm (satellite altimetry)', [10, 20, 30, 40].map(d => Math.round(sl[1] + d)), yrs, 6, false),
      co && make('ACD', 'Atmospheric CO2', 'co2', 'ppm (Mauna Loa)', [2, 5, 8, 11].map(d => Math.round(co[1] + d)), yrs, 1.2, false),
      am && make('AMOCW', 'AMOC weakening', 'amoc', 'Sv, RAPID array annual mean', [13, 14, 15, 16], yrs, 0.8, false),
    ].filter(Boolean);
  }

  // ---- live accessors
  const cents = v => (v == null ? null : Math.round(v * 100));

  // What a dollar of payout costs, net of the execution fee: buying Yes at `c`
  // cents costs c + fee and returns 100, so the multiple is 100 / (c + fee).
  // The fee is config (exchange.fee_per_side) because the exchange's FAQ and the
  // owner's published convention word it differently; every figure that shows a
  // multiple also names the fee it used, so the assumption travels with it.
  const feeCents = () => Math.round(((cfg().feePerSide != null ? cfg().feePerSide : 0.005)) * 1000) / 10;
  function payout(c) {
    if (c == null) return null;
    const cost = c + feeCents();
    return cost > 0 && cost < 100 ? Math.round((100 / cost) * 10) / 10 : null;
  }
  const payoutText = c => { const m = payout(c); return m == null ? null : m + '× net of the ' + feeCents() + '¢ fee'; };
  // `yes` is the Yes price in cents: the midpoint when both sides have bids;
  // with bids on one side only it is that side's implied Yes price and
  // `side` says which ('bid' = Yes bids only, 'ask' = No bids only).
  // `noBid` is the No bid in cents (one dollar less the feed's Yes ask).
  const row = r => ({ strike: r.strike, yes: cents(r.mid), bid: cents(r.bid), ask: cents(r.ask), bidSize: r.bidSize, askSize: r.askSize, from: r.from, label: r.label,
                      noBid: r.ask == null ? null : 100 - cents(r.ask), noBidSize: r.askSize,
                      conid: r.conid, conidYes: r.conidYes, conidNo: r.conidNo, expiration: r.expiration, error: r.error || null,
                      side: r.bid != null && r.ask != null ? 'mid' : (r.bid != null ? 'bid' : (r.ask != null ? 'ask' : null)) });

  // ---- a link to the contract on the exchange
  //
  // The exchange's own app addresses a contract with two ids that are easy to
  // confuse: the product page's id in the path, and the Yes contract's id in
  // the query. They are adjacent numbers for the same market and they are not
  // interchangeable, so both come from the snapshot rather than being derived
  // from one another. A strike whose contracts were not listed in the pass has
  // no link, and no link is drawn: a guessed id would land on the wrong page.
  //
  // The ids change as contracts are relisted for a new day, which is why they
  // travel with the quote and are never written into the pages.
  const APP = 'https://www.interactivebrokers.com/predictionmarkets/app/#/';
  /* The regulatory document that governs a product.

     Every contract on the exchange is defined by one, and it is the document
     that says what the thing settles on and when. A reader deciding whether a
     number here means what they think it means should be one click from it. */
  function termsUrl(productId) {
    const nav = (window.WX && WX.nav) || {};
    const name = (nav.terms || {})[productId];
    return name ? (nav.termsBase || '') + name + 'TermsandConditions.pdf' : null;
  }
  function termsLink(productId, label) {
    const u = termsUrl(productId);
    return u ? '<a href="' + u + '" target="_blank" rel="noopener noreferrer">'
               + (label || 'Terms and conditions') + ' \u2192</a>' : '';
  }

  function contractUrl(productConid, yesConid) {
    if (!live() || !productConid || !yesConid) return null;
    return APP + encodeURIComponent(productConid) + '/product-details/contracts?exchange=FORECASTX&conid_yes='
      + encodeURIComponent(yesConid);
  }
  // turn a node into that link: opened in a new tab, because a reader following
  // a contract has not finished with the chart they were reading
  function linkTo(node, url, title) {
    if (!node || !url) return node;
    node.style.cursor = 'pointer';
    node.setAttribute('role', 'link');
    node.setAttribute('tabindex', '0');
    // the destination, on the element that carries the click. The tooltip used
    // to repeat it as an anchor, but a tooltip that follows the cursor can never
    // be clicked, so the row was removed; the target still has to be inspectable.
    node.setAttribute('data-contract-url', url);
    if (title) node.setAttribute('aria-label', title);
    const go = e => { e.preventDefault(); e.stopPropagation(); window.open(url, '_blank', 'noopener,noreferrer'); };
    node.addEventListener('click', go);
    node.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') go(e); });
    return node;
  }

  // implied high/low for the map: the market-implied median for the
  // station's tomorrow, against the NWS forecast for the same day
  // `state` names why a value may be missing: 'unavailable' (no quote summary
  // loaded), 'unlisted' (the station has no market today), 'day' (the summary
  // is for another day), 'tomorrow-unlisted' (tomorrow's contracts not listed
  // yet), 'no-bids' (listed, fewer than two strikes with bids), 'ok'
  // `when` is 'tomorrow' (the default, and the only day the reference field
  // covers) or 'today'. The day-ahead board is what trades for most of the
  // session, but the current day's board is the live one until it settles, so
  // both are addressable rather than only the one.
  function implied(city, when) {
    const today = when === 'today';
    if (!on()) return null;
    if (!live()) return impliedPlaceholder(city, when);
    const sm = S.summary && S.summary.data;
    if (!sm) return { state: 'unavailable', impliedHigh: null, impliedLow: null, divHigh: null, divLow: null, label: 'quotes unavailable' };
    const r = (sm.cities || []).find(c => c.station === city.station);
    if (!r || !r.listed) return { state: 'unlisted', impliedHigh: null, impliedLow: null, divHigh: null, divLow: null, label: 'no market listed' };
    const wantDay = today ? (city.markers && city.markers.day) : (city.markers && city.markers.tomorrow);
    const gotDay = today ? r.day : r.tomorrow;
    if (!city.markers || gotDay !== wantDay) return { state: 'day', impliedHigh: null, impliedLow: null, divHigh: null, divLow: null, label: 'quote summary is for another day' };
    // the snapshot spells these implied{High,Low}{Today,Tomorrow} and
    // implied{High,Low}{Today,Tomorrow}Edge, so the day sits in the middle
    const sfx = today ? 'Today' : 'Tomorrow';
    const g = k => r[k + sfx];
    const ge = hl => r['implied' + hl + sfx + 'Edge'];
    const listed = (g('quotedHigh') || 0) + (g('quotedLow') || 0) > 0 || g('impliedHigh') != null || g('impliedLow') != null
                   || ge('High') || ge('Low');
    const out = { state: listed ? 'ok' : (today ? 'today-unlisted' : 'tomorrow-unlisted'),
                  impliedHigh: g('impliedHigh'), impliedLow: g('impliedLow'), divHigh: null, divLow: null,
                  edgeHigh: ge('High'), edgeLow: ge('Low'), asof: r.asof,
                  quotedHigh: g('quotedHigh'), quotedLow: g('quotedLow'), when: today ? 'today' : 'tomorrow',
                  label: 'ForecastEx implied median, ' + asofText(S.summary) };
    if (out.state === 'ok' && out.impliedHigh == null && !out.edgeHigh && out.impliedLow == null && !out.edgeLow) out.state = 'no-bids';
    const refH = today ? city.nwsHighToday : city.nwsHighTomorrow;
    const refL = today ? city.nwsLowToday : city.nwsLowTomorrow;
    if (out.impliedHigh != null && refH != null) out.divHigh = Math.round((out.impliedHigh - refH) * 10) / 10;
    if (out.impliedLow != null && refL != null) out.divLow = Math.round((out.impliedLow - refL) * 10) / 10;
    return out;
  }

  // the day's ladders for the city page: high side P(high > K), low side
  // P(low < K), on one shared temperature axis; yes is the Yes price in cents
  // (null where the contract has no bids), bid and noBid the best bids
  // `when` picks the contract day: the one being traded now, or the day-ahead
  // board. Both are in the same snapshot; only the key differs.
  function ladder(city, levels, when) {
    if (!on()) return null;
    if (!live()) return ladderPlaceholder(city, levels);
    const d = snapOf(city.station); if (!d) return null;
    const mk = city.markers || (d.markers || {});
    const day = when === 'tomorrow' ? mk.tomorrow : mk.day;
    if (!day) return null;
    const L = (d.days || {})[day] || {};
    const im = (d.implied || {})[day] || {};
    return { label: 'ForecastEx quotes, ' + asofText(S.snap), live: true, asof: d.asof, day,
             when: when === 'tomorrow' ? 'tomorrow' : 'today',
             stale: S.snap.stale, source: S.snap.source,
             listed: !!(L.high || L.low), symbols: d.symbols,
             high: (L.high || []).map(row), low: (L.low || []).map(row),
             impliedHigh: im.high || null, impliedLow: im.low || null };
  }

  // the quote history of one strike through the day: live from the
  // snapshot's rolling history (10-minute samples), else the shaped placeholder
  function pricePath(obsRows, forecastSeries, unit, side, K, city) {
    if (!on()) return [];
    if (!live()) return pricePathPlaceholder(obsRows, forecastSeries, unit, side, K);
    const d = city && snapOf(city.station); if (!d) return [];
    const day = city.markers ? city.markers.day : d.markers && d.markers.day;
    const ser = (((d.history || {})[day] || {})[side === 'h' ? 'high' : 'low'] || {})[String(K)] || [];
    const pts = [];
    ser.forEach(s => {
      const b = s[1], a = s[2];
      const v = b != null && a != null ? (b + a) / 2 : (b != null ? b : a);
      if (v != null) pts.push({ t: s[0] * 60000, v: Math.round(v), bid: b, ask: a, side: b != null && a != null ? 'mid' : (b != null ? 'bid' : 'ask') });
    });
    return pts;
  }

  // the climate page's contract markers: live from the climate group, else placeholders
  const CLIMATE_UNITS = { tempAnnual: '°C above preindustrial', tempMonthly: '°C above preindustrial', seaLevel: 'mm (satellite altimetry)', co2: 'ppm (Mauna Loa)', amoc: 'Sv, RAPID array annual mean' };
  const yearOf = spec => { const m = /(20\d\d)/.exec(spec || ''); return m ? +m[1] : null; };
  function climateProducts(series, offsetC) {
    if (!on()) return [];
    if (!live()) return climatePlaceholder(series, offsetC);
    const g = group('climate'); if (!g) return [];
    return (g.markets || []).filter(m => m.seriesKey).map(m => ({
      symbol: m.symbol, title: m.name, name: m.name, seriesKey: m.seriesKey, unit: CLIMATE_UNITS[m.seriesKey] || '', placeholder: false, live: true,
      productConid: m.productConid,
      asof: g.asof,
      contracts: (m.contracts || []).filter(c => c.mid != null && yearOf(c.spec)).map(c => ({
        year: yearOf(c.spec), threshold: c.strike, label: c.label, expiryLabel: c.expiryLabel || String(yearOf(c.spec)),
        yes: realMid(c) ? c.mid : null, empty: emptyBook(c),
        bid: c.bid, ask: c.ask, noBid: c.ask == null ? null : Math.round((1 - c.ask) * 100) / 100, bidSize: c.bidSize, askSize: c.askSize, noBidSize: c.askSize, from: c.from, conid: c.conid, expiration: c.expiration, spec: c.spec,
        label2: 'ForecastEx quote, ' + asofText(S.groups.climate) })),
    })).filter(p => p.contracts.length);
  }

  // the hurricane group: every market under the exchange's hurricane category, quotes as fetched
  function hurricaneMarkets() {
    if (!live()) return null;
    const g = group('hurricane'); if (!g) return null;
    return { asof: g.asof, stale: S.groups.hurricane.stale, source: S.groups.hurricane.source, markets: g.markets || [] };
  }

  /* A book showing a 1c bid against a 99c ask is an empty book, not a 50c
     price: the midpoint of the widest possible spread says nothing. The test
     needs both sides present at the extremes, because a lone 1c bid with no
     opposite side is a one-sided book with a real resting bid, which the
     ladders have always shown as a one-sided price.

     Owner's decision, 2026-09-03: every board outside the daily temperature
     ladders shows no price for such a book, rather than its midpoint. The
     daily ladders are untouched and keep quoting every book they are sent. */
  function realMid(r) {
    if (!r || r.mid == null) return false;
    return !(r.bid != null && r.ask != null && r.bid <= 0.011 && r.ask >= 0.989);
  }
  // a book that exists but carries no price, which reads differently from a
  // contract nobody has bid on at all
  const emptyBook = r => !!(r && r.mid != null && !realMid(r));

  return { realMid, emptyBook, mode, on, live, load, loadSummary, loadGroup, implied, ladder, pricePath, climateProducts, hurricaneMarkets, label,
           payout, payoutText, feeCents, contractUrl, linkTo, termsUrl, termsLink, get LABEL() { return label(); }, PLACEHOLDER };
})();
