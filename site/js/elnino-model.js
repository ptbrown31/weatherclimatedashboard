/* The contract's model, re-run in the browser.

   This is bk_elnino/analysis.py's simulate() and wager.py's youden_threshold(), line for line,
   over the same 50,000 simulated paths as the published run (build_site_data.py writes them).
   Nothing here is fitted or drawn at random: every random input is read from the run, so at the
   article's settings the page reproduces the run's numbers, and a slider changes only what it
   names. tests/test_site_model.py checks this file against the run, path by path.

   Units: GDP in 100 x log points (about percent), temperatures in degrees C against NOAA's
   1901-2000 average, growth in 100 x log points a year.

   One path, for the settings S:
     2026-27 temperatures   today's forecast (mean + sd * z, the run's z), a range of 2027 (the
                            run's draws inside it), or a value the reader fixes
     shocks                 s_t = T_t - (c + b1 T_{t-2} + b2 T_{t-3}), the paper's filter fitted
                            on data through 2022; years after 2027 are the run's simulated shocks,
                            re-measured with that filter (analysis.remeasure)
     impulses               u_t = s_t - (c + r1 s_{t-1} + r2 s_{t-2}) (irf.InnovationFilter), or
                            s_t itself for the transitory estimate
     effect                 sum over shocks of u_tau * theta_{t-tau}, theta held at year 10,
                            measured from 2026 (irf.effect_path)
     no effect, M0          the IMF's average miss times its share, plus the path's own miss
                            around it times the spread
     the paper, M1          M0 + share * (effect - the paper's part of the IMF's past misses)
   The contract resolves Yes when average growth from 2026 to the end year beats the strike:
     mu + M / k > strike, k = end year - 2026, mu the IMF's average projected growth. */
(function (root) {
  'use strict';

  const FIELD_NAMES = ['d0', 'idx', 'z26', 'z27', 'future', 't27bins', 'd0base'];

  // ---------------------------------------------------------------- loading
  function views(buf, fields, n) {
    const a = new Int16Array(buf);
    const out = {};
    let off = 0;
    fields.forEach(f => {
      out[f.name] = { count: f.count, scale: f.scale, data: a.subarray(off, off + f.count * n) };
      off += f.count * n;
    });
    if (off !== a.length) throw new Error('path file length does not match its fields');
    return out;
  }

  async function load(base, io) {
    io = io || {};
    const getJSON = io.json || (u => fetch(u).then(r => { if (!r.ok) throw new Error(u + ' ' + r.status); return r.json(); }));
    const getBin = io.bin || (u => fetch(u).then(r => { if (!r.ok) throw new Error(u + ' ' + r.status); return r.arrayBuffer(); }));
    const url = f => base.replace(/\/?$/, '/') + f;
    const meta = await getJSON(url('model.json'));
    const model = new Model(meta, url, getBin);
    model.paths = views(await getBin(url(meta.paths.file)), meta.paths.fields, meta.n);
    await model.ensure(meta.defaults);
    return model;
  }

  // ---------------------------------------------------------------- the model
  function Model(meta, url, getBin) {
    this.meta = meta;
    this._url = url;
    this._getBin = getBin;
    this.irfTables = {};
    this.trendPaths = {};                           // the trend baseline's paths, by file
    this.n = meta.n;
    this.irfByKey = {};
    meta.irfs.forEach(e => { this.irfByKey[e.key] = e; });
  }

  // fetch what a setting needs that is not loaded yet: an estimate's draws, the trend baseline's paths
  Model.prototype.ensure = async function (S) {
    const e = this.irfByKey[S.irf];
    if (!e) throw new Error('unknown estimate ' + S.irf);
    if (!this.irfTables[S.irf]) {
      this.irfTables[S.irf] = new Int16Array(await this._getBin(this._url(e.draws.file)));
    }
    if (S.baseline === 'frozen') {
      const tm = this.trendMeta(S);
      if (!this.trendPaths[tm.file]) this.trendPaths[tm.file] = views(await this._getBin(this._url(tm.file)), tm.fields, this.n);
    }
  };

  Model.prototype.ready = function (S) {
    return !!this.irfTables[S.irf] && (S.baseline !== 'frozen' || !!this.trendPaths[this.trendMeta(S).file]);
  };

  // the trend baseline for the setting's first year of the average: its paths file, average and mean
  // miss path. A bundle without start years has only the published window (pathsFrozen, frozen).
  Model.prototype.trendMeta = function (S) {
    const t = this.meta.trend;
    const start = S.trendStart == null ? (t ? t.default : null) : S.trendStart;
    if (t && t.starts[String(start)]) return t.starts[String(start)];
    if (t && start != null) throw new Error('no trend baseline from ' + start);
    return Object.assign({}, this.meta.pathsFrozen, this.meta.frozen);
  };

  /* The estimate a path uses, after the uncertainty setting:
       boot   the run's bootstrap draw (the paper's statistical uncertainty, as estimated)
       wide   that draw's distance from the central estimate times 1.5
       nw     that distance rescaled to Newey-West widths, horizon by horizon (time series only)
       point  the central estimate on every path */
  function thetaRows(model, S) {
    const e = model.irfByKey[S.irf], tab = model.irfTables[S.irf];
    const H1 = e.draws.cols, rows = e.draws.rows, sc = e.draws.scale, th = e.theta;
    const out = new Float64Array(rows * H1);
    const nw = model.meta.nwScale;
    for (let r = 0; r < rows; r++) {
      for (let h = 0; h < H1; h++) {
        const d = tab[r * H1 + h] / sc;
        let v;
        if (S.uncertainty === 'point') v = th[h];
        else if (S.uncertainty === 'wide') v = th[h] + 1.5 * (d - th[h]);
        else if (S.uncertainty === 'nw') v = th[h] + (d - th[h]) * nw[h];
        else v = d;
        out[r * H1 + h] = v;
      }
    }
    return { rows, H1, data: out };
  }

  // the estimate a setting uses, with the 5th and 95th percentiles of its draws, horizon by horizon
  Model.prototype.irfBand = function (S) {
    const e = this.irfByKey[S.irf], TH = thetaRows(this, S);
    const q05 = [], q95 = [];
    for (let h = 0; h < TH.H1; h++) {
      const col = new Float64Array(TH.rows);
      for (let r = 0; r < TH.rows; r++) col[r] = TH.data[r * TH.H1 + h];
      col.sort();
      q05.push(quantile(col, 0.05)); q95.push(quantile(col, 0.95));
    }
    return { theta: e.theta.slice(), q05, q95 };
  };

  // theta at horizon h: zero before the shock, held at the last estimated horizon after it
  function resp(row, off, H1, h) {
    if (h < 0) return 0;
    return row[off + (h < H1 ? h : H1 - 1)];
  }

  Model.prototype.run = function (S) {
    const meta = this.meta, n = this.n, P = this.paths;
    if (!this.ready(S)) throw new Error('call ensure() first');
    const e = this.irfByKey[S.irf];
    const fam = meta.families[e.family];
    const ff = meta.forecastFilter;
    const years = meta.years;                       // 2026..2035
    const Y0 = 2000;                                // index origin of the per-path shock arrays
    const NY = years[years.length - 1] - Y0 + 1;
    const anchor = meta.anchor, base = meta.base;   // 2025, 2026
    const tYears = years.filter(t => t > base);     // 2027..2035, the years measured from 2026
    const nt = tYears.length;

    // ---- what every path shares
    const past = new Float64Array(NY);               // observed shocks, 2000..2025
    for (let t = Y0; t <= anchor; t++) past[t - Y0] = fam.shocks[String(t)];
    const Tobs = meta.noaa.T, T0 = meta.noaa.first; // NOAA annual, first year T0
    const innov = fam.innovation, nl = innov.length - 1;
    const futYears = meta.paths.futureYears;        // 2028..2035
    const useFuture = S.future === 'simulate';
    const scopeFirst = S.scope === 'incremental' ? 2026 : S.scope === 'recent' ? 2023 : anchor - meta.hMax;
    const impYears = [];
    for (let t = scopeFirst; t <= 2027; t++) impYears.push(t);
    if (useFuture) futYears.forEach(t => impYears.push(t));
    const transitory = e.transitory;

    // the observed years' impulses do not depend on the path
    const impPast = {};
    impYears.filter(t => t <= anchor).forEach(t => {
      impPast[t] = transitory ? past[t - Y0] : past[t - Y0] - predInnov(innov, nl, past, t - Y0);
    });

    // the paper's part of the IMF's past misses (analysis.paper_drag_by_lead), from 2026
    const drag = e.drag, L = drag.length;
    const C = new Float64Array(nt);
    let acc = 0;
    for (let k = 0; k < nt; k++) { acc += drag[Math.min(tYears[k] - anchor, L) - 1]; C[k] = acc; }
    const netOn = S.baseline === 'imf' && S.netting !== false;

    // no-effect paths: the baseline's mean path and each path's distance from it
    const frozen = S.baseline === 'frozen';
    const tm = frozen ? this.trendMeta(S) : null;
    const d0 = frozen ? this.trendPaths[tm.file].d0 : P.d0;
    const Dbar = frozen ? tm.Dbar : meta.imf.Dbar;
    const missShare = frozen ? 1 : (S.missShare == null ? 1 : S.missShare);
    const spread = S.spread == null ? 1 : S.spread;
    const share = S.share == null ? 1 : S.share;

    // 2026-27 temperatures
    const T26 = meta.temps.T26, T27 = meta.temps.T27;
    const z26 = P.z26, z27 = P.z27, bins = P.t27bins;
    const binK = typeof S.t27 === 'string' && S.t27.indexOf('bin:') === 0 ? meta.paths.binOrder.indexOf(S.t27.slice(4)) : -1;
    if (typeof S.t27 === 'string' && S.t27.indexOf('bin:') === 0 && binK < 0) throw new Error('unknown range ' + S.t27);

    const TH = thetaRows(this, S);
    const H1 = TH.H1;
    const fut = P.future, idx = P.idx;

    // outputs: deviation from 2026 under each view, year by year (2027..2035)
    const M0 = [], M1 = [], EF = [], EP = [];
    for (let k = 0; k < nt; k++) {
      M0.push(new Float64Array(n)); M1.push(new Float64Array(n));
      EF.push(new Float64Array(n)); EP.push(new Float64Array(n));
    }
    const shockMean = new Float64Array(NY);           // mean shock by year, for the figure
    const s = new Float64Array(NY), T = new Float64Array(NY + 10), imp = new Float64Array(NY);
    const fit = fam;                                  // the design's filter: coef, lags
    const fitC = fit.coef, fitL = fit.lags, ffC = ff.coef, ffL = ff.lags;
    const TY0 = Y0 - 10;                              // T array covers 1990..2035
    for (let t = TY0; t <= anchor; t++) T[t - TY0] = Tobs[t - T0];

    for (let i = 0; i < n; i++) {
      // temperatures
      const t26 = S.t26 === 'value' ? S.t26Value : T26.mean + T26.sd * (z26.data[i] / z26.scale);
      let t27;
      if (S.t27 === 'value') t27 = S.t27Value;
      else if (binK >= 0) t27 = meta.paths.t27center + bins.data[binK * n + i] / bins.scale;
      else t27 = T27.mean + T27.sd * (z27.data[i] / z27.scale);
      // shocks: observed, then 2026-27 from the filter's fitted values
      s.set(past);
      s[2026 - Y0] = t26 - fam.f26;
      s[2027 - Y0] = t27 - fam.f27;
      if (useFuture) {
        // re-measure the simulated shocks with the design's filter (analysis.remeasure)
        T[2026 - TY0] = t26; T[2027 - TY0] = t27;
        for (let j = 0; j < futYears.length; j++) {
          const t = futYears[j];
          let pf = ffC[0], pd = fitC[0];
          for (let q = 0; q < ffL.length; q++) pf += ffC[q + 1] * T[t - ffL[q] - TY0];
          T[t - TY0] = pf + fut.data[j * n + i] / fut.scale;
          for (let q = 0; q < fitL.length; q++) pd += fitC[q + 1] * T[t - fitL[q] - TY0];
          s[t - Y0] = T[t - TY0] - pd;
        }
      }
      // impulses
      for (let j = 0; j < impYears.length; j++) {
        const t = impYears[j];
        imp[t - Y0] = t <= anchor ? impPast[t] : (transitory ? s[t - Y0] : s[t - Y0] - predInnov(innov, nl, s, t - Y0));
      }
      // the path's estimate
      const off = idx.data[i] * H1, row = TH.data;
      // effect from 2026: sum_tau u_tau (theta_{t-tau} - theta_{2026-tau})
      for (let k = 0; k < nt; k++) {
        const t = tYears[k];
        let ep = 0, ef = 0;
        for (let j = 0; j < impYears.length; j++) {
          const tau = impYears[j];
          const v = imp[tau - Y0] * (resp(row, off, H1, t - tau) - resp(row, off, H1, base - tau));
          if (tau <= 2027) ep += v; else ef += v;
        }
        EP[k][i] = ep; EF[k][i] = ef;
        const d = d0.data[k * n + i] / d0.scale;
        const m0 = missShare * Dbar[k] + spread * (d - Dbar[k]);
        M0[k][i] = m0;
        M1[k][i] = m0 + share * (ep + ef - (netOn ? C[k] : 0));
      }
      for (let t = 2026; t <= 2035; t++) shockMean[t - Y0] += s[t - Y0];
    }
    for (let t = 2026; t <= 2035; t++) shockMean[t - Y0] /= n;

    return new Result(this, S, { tYears, M0, M1, EP, EF, shockMean, Y0 });
  };

  function predInnov(c, nl, s, j) {
    let p = c[0];
    for (let q = 1; q <= nl; q++) p += c[q] * s[j - q];
    return p;
  }

  // ---------------------------------------------------------------- results
  function Result(model, S, r) {
    this.model = model;
    this.S = S;
    this.tYears = r.tYears;
    this.M0 = r.M0; this.M1 = r.M1; this.EP = r.EP; this.EF = r.EF;
    this.shockMean = r.shockMean; this.Y0 = r.Y0;
    this._sorted = {};
  }

  Result.prototype.k = function (E) { return E - this.model.meta.base; };

  // the no-effect path's average projected growth from 2026 to E
  Result.prototype.mu = function (E) {
    const meta = this.model.meta;
    if (this.S.baseline === 'frozen') return this.model.trendMeta(this.S).mu;
    let s = 0;
    for (let t = meta.base + 1; t <= E; t++) s += meta.imf.growth[String(t)];
    return s / (E - meta.base);
  };

  Result.prototype.m = function (view, E) {
    const k = this.tYears.indexOf(E);
    if (k < 0) throw new Error('end year ' + E + ' not simulated');
    return view === 0 ? this.M0[k] : this.M1[k];
  };

  Result.prototype.sorted = function (view, E) {
    const key = view + ':' + E;
    if (!this._sorted[key]) this._sorted[key] = Float64Array.from(this.m(view, E)).sort();
    return this._sorted[key];
  };

  // chance of Yes, growth above the strike, under each view
  Result.prototype.pYes = function (view, E, strike) {
    const c = (strike - this.mu(E)) * this.k(E);
    return 1 - countLE(this.sorted(view, E), c) / this.sorted(view, E).length;
  };

  Result.prototype.growthMean = function (view, E) {
    const m = this.m(view, E);
    let s = 0;
    for (let i = 0; i < m.length; i++) s += m[i];
    return this.mu(E) + s / m.length / this.k(E);
  };

  Result.prototype.growthQuantile = function (view, E, q) {
    return this.mu(E) + quantile(this.sorted(view, E), q) / this.k(E);
  };

  // the cutoff that best separates the views (wager.youden_threshold), as a growth rate
  Result.prototype.youden = function (E) {
    const key = 'youden:' + E;
    if (this._sorted[key]) return this._sorted[key];
    const s0 = this.sorted(0, E), s1 = this.sorted(1, E);
    const y = youden(s0, s1);
    y.growth = this.mu(E) + y.threshold / this.k(E);
    y.E = E;
    this._sorted[key] = y;
    return y;
  };

  // level path quantiles from 2026 (100 x log points), for the fan chart
  Result.prototype.levelBand = function (view, q) {
    return this.tYears.map(t => quantile(this.sorted(view, t), q));
  };
  Result.prototype.levelMean = function (view) {
    return this.tYears.map(t => { const m = this.m(view, t); let s = 0; for (let i = 0; i < m.length; i++) s += m[i]; return s / m.length; });
  };
  // the paper's mean effect from 2026 with and without the simulated years after 2027 (no netting, no share)
  Result.prototype.effectMean = function (withFuture) {
    return this.tYears.map((t, k) => {
      let s = 0;
      for (let i = 0; i < this.EP[k].length; i++) s += this.EP[k][i] + (withFuture ? this.EF[k][i] : 0);
      return s / this.EP[k].length;
    });
  };

  // ---------------------------------------------------------------- statistics
  function countLE(sorted, c) {                      // searchsorted(..., side='right')
    let lo = 0, hi = sorted.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] <= c) lo = mid + 1; else hi = mid; }
    return lo;
  }

  // numpy's default (linear) quantile of a sorted array, with numpy's own interpolation
  function quantile(sorted, p) {
    const N = sorted.length;
    const pos = p * (N - 1);
    const lo = Math.floor(pos), hi = Math.min(lo + 1, N - 1), t = pos - lo;
    const a = sorted[lo], b = sorted[hi], d = b - a;
    return t >= 0.5 ? b - d * (1 - t) : a + d * t;
  }

  function merge(a, b) {
    const out = new Float64Array(a.length + b.length);
    let i = 0, j = 0, k = 0;
    while (i < a.length && j < b.length) out[k++] = a[i] <= b[j] ? a[i++] : b[j++];
    while (i < a.length) out[k++] = a[i++];
    while (j < b.length) out[k++] = b[j++];
    return out;
  }

  /* c maximising J(c) = P_paper(M <= c) - P_no effect(M <= c): the median of the 20,001 candidate
     cutoffs whose J is within 0.0025 of the best, as wager.youden_threshold does it. */
  function youden(s0, s1, tol) {
    tol = tol == null ? 0.0025 : tol;
    const pooled = merge(s0, s1);
    const K = 20001, p0 = 0.001, step = (0.999 - 0.001) / (K - 1);
    const cand = new Float64Array(K), J = new Float64Array(K);
    let i0 = 0, i1 = 0, best = -Infinity;
    for (let k = 0; k < K; k++) {
      const p = k === K - 1 ? 0.999 : p0 + step * k;
      const c = quantile(pooled, p);
      cand[k] = c;
      while (i0 < s0.length && s0[i0] <= c) i0++;
      while (i1 < s1.length && s1[i1] <= c) i1++;
      J[k] = i1 / s1.length - i0 / s0.length;
      if (J[k] > best) best = J[k];
    }
    const near = [];
    for (let k = 0; k < K; k++) if (J[k] >= best - tol) near.push(cand[k]);
    const m = near.length;
    const c = m % 2 ? near[(m - 1) >> 1] : (near[m / 2 - 1] + near[m / 2]) / 2;
    const sens = countLE(s1, c) / s1.length, fp = countLE(s0, c) / s0.length;
    return { threshold: c, sensitivity: sens, falsePositive: fp, J: sens - fp };
  }

  const api = { load, Model, youden, quantile, countLE, FIELD_NAMES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ElNinoModel = api;
})(typeof window !== 'undefined' ? window : this);
