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

  return { mode, on, implied, ladder, pricePath, LABEL };
})();
