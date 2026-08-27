/* A seasonal ARIMA projection, fitted in the page from the series on the page.

   The panels already offer a straight line through a window you drag. That is
   useful and it is also the wrong shape for most of these series: rain has a
   season, a crop yield has a trend and a season, and a straight line through
   either says January and July are the same month.

   This fits ARIMA(p,1,0)(1,1,0)_m by least squares — differenced once, and once
   more at the seasonal lag where the series has a season, with an autoregressive
   term on what is left. There is no moving-average term and no likelihood
   maximisation: those need an optimiser this page does not carry, and on series
   of a few hundred points the difference is small against the width of the band
   the projection is drawn with. It is stated on the page as what it is.

   What the differencing buys is that the trend and the season are carried by the
   model rather than assumed: a seasonal difference removes a repeating annual
   shape whatever its form, and a regular difference removes a level that drifts.
   Neither is estimated as a fixed curve, so neither is imposed on the forecast.

   The band is the model's own uncertainty growing with the horizon, not a
   confidence interval anyone has validated. Twelve months out on a monthly
   series it is wide, which is the honest answer.

   Nothing here is a fair value, a price, or a claim about a contract. It is
   arithmetic on a public series, and a reader can see the record it was fitted
   to on the same axes. */
window.WXForecast = (() => {

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

  // lag-one autocorrelation, the cheap test for a level that wanders
  function ac1(v) {
    const n = v.length;
    if (n < 8) return 0;
    const mean = v.reduce((a, b) => a + b, 0) / n;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) {
      const d = v[i] - mean;
      den += d * d;
      if (i) num += d * (v[i - 1] - mean);
    }
    return den > 0 ? num / den : 0;
  }

  function diff(v, lag) {
    const out = [];
    for (let i = lag; i < v.length; i++) out.push(v[i] - v[i - lag]);
    return out;
  }

  /* Least squares for an autoregression of order p, by Gaussian elimination on
     the normal equations. p is small and the matrix is well conditioned after
     differencing, so this needs no pivoting beyond the largest row. */
  function fitAR(z, p) {
    const n = z.length - p;
    if (n < p + 4) return null;
    const A = [], b = [];
    for (let i = 0; i < n; i++) {
      const row = [];
      for (let k = 1; k <= p; k++) row.push(z[p + i - k]);
      A.push(row); b.push(z[p + i]);
    }
    const M = [], y = [];
    for (let r = 0; r < p; r++) {
      const row = [];
      for (let c = 0; c < p; c++) {
        let s = 0;
        for (let i = 0; i < n; i++) s += A[i][r] * A[i][c];
        row.push(s);
      }
      let s = 0;
      for (let i = 0; i < n; i++) s += A[i][r] * b[i];
      M.push(row); y.push(s);
    }
    for (let c = 0; c < p; c++) {
      let piv = c;
      for (let r = c + 1; r < p; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
      if (Math.abs(M[piv][c]) < 1e-12) return null;
      [M[c], M[piv]] = [M[piv], M[c]]; [y[c], y[piv]] = [y[piv], y[c]];
      for (let r = 0; r < p; r++) {
        if (r === c) continue;
        const f = M[r][c] / M[c][c];
        for (let k = c; k < p; k++) M[r][k] -= f * M[c][k];
        y[r] -= f * y[c];
      }
    }
    const phi = y.map((v, i) => v / M[i][i]);
    if (phi.some(v => !isFinite(v))) return null;
    let ss = 0;
    for (let i = 0; i < n; i++) {
      let f = 0;
      for (let k = 0; k < p; k++) f += phi[k] * A[i][k];
      ss += (b[i] - f) * (b[i] - f);
    }
    return { phi, sigma: Math.sqrt(ss / Math.max(1, n - p)) };
  }

  /* Project a series forward to `toX`.

     `series` is [[x, value], ...] ascending. Returns
     {points: [{x, v, lo, hi}], m, p, sigma} or null when the record is too
     short to fit anything, which is a state the caller should say rather than
     paper over. */
  function project(series, toX) {
    const s = (series || []).filter(q => q && isFinite(q[0]) && q[1] != null);
    if (s.length < 24) return null;
    const xs = s.map(q => q[0]), ys = s.map(q => q[1]);
    const m = period(xs);
    const step = m > 1 ? 1 / m : (xs.length > 1 ? xs[xs.length - 1] - xs[xs.length - 2] : 1);
    /* How far to run.

       Far enough to reach the strikes, but not past the point where the band is
       wider than anything the series has ever done. Drought contracts settle out
       to 2035, which on a weekly series is nearly five hundred steps and a band
       of plus or minus a hundred and forty percentage points — a picture of
       nothing. The run stops at the cap and the caller says it stopped. */
    const MAX_STEPS = 120;
    const want = Math.ceil((toX - xs[xs.length - 1]) / step);
    if (!(want > 0)) return null;
    const horizon = Math.min(want, MAX_STEPS);
    /* How many times to difference, decided from the series rather than fixed.

       Differencing a series that does not need it is not harmless: each pass
       roughly doubles the variance of what is left, so a doubly-differenced
       month of rain came out with a standard error wider than the wettest month
       on record and the band said nothing.

       A series sampled through the year is differenced at the seasonal lag,
       which is the standard shape for monthly data and costs nothing where the
       season is weak. Whether to difference again is asked of the data: a level
       that wanders needs it, one already stationary is only roughened by it.

       Measuring how strong the season is directly was tried and dropped. On
       carbon dioxide the trend is so much larger than the annual cycle that the
       calendar looked to account for almost none of the variance, the seasonal
       difference was skipped, and the projection came out falling through a
       record that has risen every year since 1958. */
    const seasonal = m > 1 && ys.length >= m * 3;
    const d1 = seasonal ? diff(ys, m) : ys.slice();
    // lag-one autocorrelation near one is a level that wanders; near or below
    // zero is a series already stationary, which differencing would only rough up
    const regular = ac1(d1) > 0.55;
    const w = regular ? diff(d1, 1) : d1.slice();
    if (w.length < 12) return null;
    /* Fit around the mean, not around zero.

       An autoregression with no intercept pulls its forecast toward zero, which
       on an undifferenced series means toward no rain at all. On a differenced
       one the mean is the average step, which is the drift — the trend — and
       must be kept. Taking the mean out before fitting and putting it back after
       handles both. */
    const mw = w.reduce((a, b) => a + b, 0) / w.length;
    const wc = w.map(v => v - mw);
    const p = Math.min(3, Math.max(1, Math.floor(wc.length / 20)));
    const fit = fitAR(wc, p) || fitAR(wc, 1);
    if (!fit) return null;

    const hist = ys.slice();
    const wser = wc.slice();
    const out = [];
    for (let k = 1; k <= horizon; k++) {
      let wc_n = 0;
      for (let i = 0; i < fit.phi.length; i++) wc_n += fit.phi[i] * wser[wser.length - 1 - i];
      wser.push(wc_n);
      const wn = wc_n + mw;
      const n = hist.length;
      // undo whichever differences were taken, in the order they were applied
      let val;
      if (regular && seasonal) val = hist[n - 1] + (hist[n - m] - hist[n - m - 1]) + wn;
      else if (regular) val = hist[n - 1] + wn;
      else if (seasonal) val = hist[n - m] + wn;
      else val = wn;
      hist.push(val);
      // the band widens with the square root of the horizon, which is what a
      // differenced model's error does when the terms are small
      const band = 1.96 * fit.sigma * Math.sqrt(k);
      out.push({ x: Math.round((xs[xs.length - 1] + k * step) * 10000) / 10000,
                 v: val, lo: val - band, hi: val + band });
    }
    return { points: out, m, p: fit.phi.length, sigma: fit.sigma, seasonal, regular,
             capped: want > horizon, reach: out.length ? out[out.length - 1].x : null };
  }

  return { project, period };
})();
