/* The projection: a straight line through the recent record, with the average
   seasonal cycle laid on top.

   This used to be a seasonal ARIMA, and a differenced model earns its keep on
   series where the future hinges on the last few observations. These are not
   those series: a reader looking at carbon dioxide or sea level wants the most
   generic possible reading of "more of the same" — a projection that does not
   visibly leave the recent trend, keeps the seasonal shape the record plainly
   has, and never invents momentum. The ARIMA did invent momentum: it chased
   one warm year into a runaway warming rate, read a quiet stretch as a flat
   future, and on a weekly series its differenced band exploded off the chart.

   So: an ordinary least-squares line through the last twelve years (the whole
   record when it is shorter), a seasonal term that is nothing more than the
   average deviation from that line at each point of the cycle, and a band of
   twice the residual scatter that does not widen — because the model claims
   nothing about the far future beyond "the recent line, continued", and a
   widening cone would dress that claim up as something more.

   The line and the season are fitted together, in two passes: the line, the
   per-phase means of what is left, then the line again on the deseasonalised
   record, so a season that peaks late in the window cannot masquerade as
   trend. A series that has never been negative is never projected negative:
   for a generic tool that is a fact about the quantity, not a modelling
   choice.

   Nothing here is a fair value, a price, or a claim about a contract. It is
   arithmetic on a public series, and a reader can see the record it was
   fitted to on the same axes. */
window.WXForecast = (() => {

  const WINDOW_YEARS = 12;
  const MAX_STEPS = 600;

  // the seasonal period implied by how the series is spaced: a year of monthly
  // readings, a year of weekly ones, or an annual series with no season in it
  function period(xs) {
    if (xs.length < 4) return 1;
    const d = [];
    for (let i = 1; i < xs.length; i++) d.push(xs[i] - xs[i - 1]);
    d.sort((a, b) => a - b);
    const med = d[Math.floor(d.length / 2)];
    if (med <= 0) return 1;
    const perYear = Math.round(1 / med);
    if (perYear >= 300) return 365;
    if (perYear >= 40) return 52;
    if (perYear >= 10) return 12;
    if (perYear >= 3) return 4;
    return 1;
  }

  const phaseOf = (x, m) => ((Math.round((x - Math.floor(x)) * m) % m) + m) % m;

  function ols(pts) {
    const n = pts.length;
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    pts.forEach(([x, y2]) => { sx += x; sy += y2; sxx += x * x; sxy += x * y2; });
    const den = n * sxx - sx * sx;
    if (Math.abs(den) < 1e-12) return null;
    const b = (n * sxy - sx * sy) / den;
    return { a: (sy - b * sx) / n, b };
  }

  /* Project a series forward to `toX`.

     `series` is [[x, value], ...] ascending, x in years. Returns
     {points: [{x, v, lo, hi}], ...} or null when the record is too short to
     fit anything, which is a state the caller should say rather than paper
     over. */
  function project(series, toX) {
    const s = (series || []).filter(q => q && isFinite(q[0]) && q[1] != null);
    if (s.length < 8) return null;
    const xs = s.map(q => q[0]), ys = s.map(q => q[1]);
    const m = period(xs);
    const step = m > 1 ? 1 / m : (xs.length > 1 ? Math.max(xs[xs.length - 1] - xs[xs.length - 2], 1e-6) : 1);
    const want = Math.ceil((toX - xs[xs.length - 1]) / step);
    if (!(want > 0)) return null;
    const horizon = Math.min(want, MAX_STEPS);

    // the window: the last twelve years, or everything when that is thin
    const cut = xs[xs.length - 1] - WINDOW_YEARS;
    let win = s.filter(q => q[0] >= cut);
    if (win.length < Math.max(8, m)) win = s.slice();
    const windowYears = Math.round((win[win.length - 1][0] - win[0][0]) * 10) / 10;

    // pass one: the line
    let fit = ols(win);
    if (!fit) return null;

    // the season: the average deviation from the line at each phase, centred
    // so the season carries shape and the line carries level and slope
    const seasonal = m > 1 && win.length >= 2 * m;
    const seas = new Array(m).fill(0);
    if (seasonal) {
      const sum = new Array(m).fill(0), cnt = new Array(m).fill(0);
      win.forEach(([x, y2]) => { const p = phaseOf(x, m); sum[p] += y2 - (fit.a + fit.b * x); cnt[p] += 1; });
      let tot = 0, ntot = 0;
      for (let p = 0; p < m; p++) if (cnt[p]) { seas[p] = sum[p] / cnt[p]; tot += seas[p] * cnt[p]; ntot += cnt[p]; }
      const centre = ntot ? tot / ntot : 0;
      for (let p = 0; p < m; p++) seas[p] -= centre;
      // pass two: the line again, on the deseasonalised record
      const refit = ols(win.map(([x, y2]) => [x, y2 - seas[phaseOf(x, m)]]));
      if (refit) fit = refit;
    }

    // the scatter the fit leaves behind, which is the band
    let ss = 0;
    win.forEach(([x, y2]) => {
      const e = y2 - (fit.a + fit.b * x) - (seasonal ? seas[phaseOf(x, m)] : 0);
      ss += e * e;
    });
    const sigma = Math.sqrt(ss / Math.max(1, win.length - 2 - (seasonal ? m - 1 : 0)));
    const band = 2 * sigma;

    // a quantity that has never been negative is not projected negative
    const floor0 = Math.min(...ys) >= 0;
    const out = [];
    for (let k = 1; k <= horizon; k++) {
      const x = Math.round((xs[xs.length - 1] + k * step) * 10000) / 10000;
      let v = fit.a + fit.b * x + (seasonal ? seas[phaseOf(x, m)] : 0);
      let lo = v - band, hi = v + band;
      if (floor0) { v = Math.max(v, 0); lo = Math.max(lo, 0); hi = Math.max(hi, 0); }
      out.push({ x, v, lo, hi });
    }
    return { points: out, m, seasonal, windowYears, slope: fit.b, sigma,
             capped: want > horizon, reach: out.length ? out[out.length - 1].x : null };
  }

  return { project, period };
})();
