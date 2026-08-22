/* The market layer, in one file, behind one switch.

   WX.market is 'placeholder' or 'off' per build target; ?market=on|off on
   the URL overrides it (the embed takes ?market=on to show placeholders).
   When off, every function here returns null and the pages reserve no
   space for ladders, price panels, implied values or divergence encodings.

   'placeholder' reproduces the reference package's labelled synthetic data:
   a strike ladder and implied high/low per station, deterministic from the
   station id and the day so a page is stable across reloads, and a price
   path shaped from the observed-minus-forecast gap purely so the layout
   has something to show. None of it is a market value. No live source
   exists in this repository; wiring one is a separate decision. */
window.WXM = (() => {
  function mode() {
    const q = new URLSearchParams(location.search).get('market');
    if (q === 'on') return 'placeholder';
    if (q === 'off') return 'off';
    return (window.WX && window.WX.market) || 'off';
  }
  const on = () => mode() !== 'off';
  const LABEL = 'placeholder, not a market value';
  const lgs = (x, k = 0.62) => 1 / (1 + Math.exp(-k * x));
  const seedOf = (sid, day) => { let s = 0; for (const ch of sid + (day || '')) s = (s * 31 + ch.charCodeAt(0)) % 100003; return s; };

  // implied high/low for the map: deliberately offset from the NWS forecast
  // so the divergence encoding has something visible to show
  function implied(city) {
    if (!on()) return null;
    const seed = seedOf(city.station, city.markers && city.markers.tomorrow);
    const out = { impliedHigh: null, impliedLow: null, divHigh: null, divLow: null, label: LABEL };
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

  // today's ladders for the city page: high side P(high > K), low side
  // P(low < K), on one shared temperature axis
  function ladder(city, levels) {
    if (!on()) return null;
    const seed = seedOf(city.station, city.markers && city.markers.day);
    const step = 1.0;
    const hb = levels.high != null ? levels.high : (city.unit === 'F' ? 80 : 27);
    const lb = levels.low != null ? levels.low : hb - (city.unit === 'F' ? 18 : 10);
    const ih = Math.round((hb + ((seed % 9) - 4) * 0.7) * 10) / 10;
    const il = Math.round((lb + (((seed * 3) % 7) - 3) * 0.6) * 10) / 10;
    const bh = Math.round(hb), bl = Math.round(lb);
    const clamp = p => Math.max(1, Math.min(99, Math.round(100 * p)));
    return {
      label: LABEL,
      high: Array.from({ length: 11 }, (_, i) => { const s = bh + (i - 5) * step; return { strike: s, yes: clamp(1 - lgs(s - ih)) }; }),
      low: Array.from({ length: 11 }, (_, i) => { const s = bl + (i - 5) * step; return { strike: s, yes: clamp(lgs(s - il)) }; }),
    };
  }

  // a synthetic price path per strike, shaped from the observed-minus-
  // forecast gap; joins by UTC hour number, as-issued series first
  function pricePath(obsRows, forecastSeries, unit, side, K) {
    if (!on()) return [];
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

  // placeholder climate contracts for the climate page: a few thresholds at
  // a few expirations per product, priced from a logistic around a simple
  // extrapolation of the series, so the markers have a shape to show
  function climateProducts(series, offsetC) {
    if (!on()) return [];
    const year = new Date().getUTCFullYear();
    const last = s => (series[s] && series[s].length ? series[s][series[s].length - 1] : null);
    const trend = (s, years) => {          // per-year slope over the last `years` points
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
          expiryLabel: (monthly ? 'any month of ' : '') + y, yes, label2: LABEL });
      }));
      return { symbol, title, seriesKey, unit, name: title, contracts, placeholder: true };
    };
    const ta = last('tempAnnual'), sl = last('seaLevel'), co = last('co2'), am = last('amoc');
    const yrs = [year + 1, year + 2, year + 3, year + 4];
    return [
      ta && make('GTTA', 'Annual global temperature thresholds', 'tempAnnual', '°C above preindustrial',
        [1.5, 1.6, 1.7, 1.8, 1.9, 2.0].map(t => t), yrs, 0.08, false),
      ta && make('GTTM', 'Any-month global temperature thresholds', 'tempMonthly', '°C above preindustrial',
        [1.6, 1.7, 1.8, 1.9, 2.0, 2.1], yrs, 0.12, true),
      sl && make('GSL', 'Global sea level', 'seaLevel', 'mm (satellite altimetry)',
        [10, 20, 30, 40].map(d => Math.round(sl[1] + d)), yrs, 6, false),
      co && make('ACD', 'Atmospheric CO2', 'co2', 'ppm (Mauna Loa)',
        [2, 5, 8, 11].map(d => Math.round(co[1] + d)), yrs, 1.2, false),
      am && make('AMOCW', 'AMOC weakening', 'amoc', 'Sv, RAPID array annual mean',
        [13, 14, 15, 16], yrs, 0.8, false),
    ].filter(Boolean);
  }

  return { mode, on, implied, ladder, pricePath, climateProducts, LABEL };
})();
