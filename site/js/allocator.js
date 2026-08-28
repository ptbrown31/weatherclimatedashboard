/* The position allocation calculator: one amount, spread across a strike ladder three
   ways.

   A reader types two numbers — the value they expect, and how far off they
   could be — and the page turns that into a normal curve, prices every strike's
   Yes and No against it, and splits a fixed amount across the ladder. The
   whole amount is always spent: the question the page answers is never how
   much to commit but where, and the three scenarios are three answers to
   where. Everything on the page is arithmetic on the reader's own numbers and
   the exchange's public prices; the site contributes no forecast of its own,
   and the default ladder is made up.

   The split maximises the expected utility of the payout under the reader's
   curve, with utility from the standard power family (constant relative risk
   aversion). The middle scenario uses the logarithm — Kelly's growth-optimal
   rule (Kelly 1956), in its original fully-invested form. Conservative uses a
   much more risk-averse member (gamma 4), which behaves like quarter-Kelly:
   it hedges widely and its payout curve is smooth. Aggressive uses a less
   risk-averse member (gamma one half), the double-Kelly over-bettor: it
   concentrates near the reader's value and pays big only if the value lands
   close. The Kelly-fraction-to-power-utility correspondence is the standard
   one (MacLean, Thorp and Ziemba). The payoff of a set of these contracts is
   constant between neighbouring strike thresholds, so the expected utility is
   computed exactly on those intervals rather than on a sampled grid.

   Prices follow the exchange's structure: there are no sellers, only bids to
   buy Yes or No that sum to a dollar. Buying Yes now costs one dollar less the
   No bid; buying No now costs one dollar less the Yes bid. The per-side fee is
   added to every cost here, so a "cost" on this page is what a buyer actually
   pays. A side with no resting bid opposite it cannot be bought now and is
   left out. */
window.WXAlloc = (() => {
  const { el, txt, h, $ } = WXC;
  /* gamma is relative risk aversion; 1/gamma is the equivalent Kelly fraction,
     so these three are quarter-Kelly, Kelly, and double-Kelly. */
  const SCEN = [
    { key: 'conservative', name: 'Conservative', gamma: 4, col: 'var(--cool)', blurb: 'hedges widest — the smoothest payout' },
    { key: 'middle', name: 'Middle', gamma: 1, col: 'var(--lamp)', blurb: 'growth-optimal — Kelly’s own rule' },
    { key: 'aggressive', name: 'Aggressive', gamma: 0.5, col: 'var(--nbm)', blurb: 'concentrated — pays big only near the prediction' },
  ];
  let tip = null;

  // ---------------------------------------------------------------- math
  // Abramowitz & Stegun 7.1.26; |error| < 1.5e-7, far below anything drawn here
  function erf(x) {
    const s = x < 0 ? -1 : 1; x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return s * y;
  }
  const Phi = z => 0.5 * (1 + erf(z / Math.SQRT2));

  /* The shape of the user's uncertainty.

     A normal curve says the prediction is as likely to be too low as too high
     and by the same amount. Plenty of the quantities on this exchange are not
     like that: a wind speed or a rainfall total has a long right tail, a crop
     yield a long left one — the bad year is much worse than the good year is
     good.

     The skewed shapes keep both numbers the user gave. The median stays on the
     predicted value, and the 95 percent span stays the width they typed; what
     changes is how that span is divided, roughly three parts of it on the long
     side to one on the short. They are lognormal in shape but shifted, so they
     work on a quantity that can go negative — a temperature in Fahrenheit —
     which a bare lognormal cannot represent at all.

       W = exp(sigma_log Z)          median 1, long right tail
       V = mu + dir k (W - 1)        median mu, k set by the 95 percent span

     Everything downstream asks this file two questions — the chance the value
     lands below a threshold, and the height of the curve at a point — so those
     are the only two functions the shapes have to provide. */
  const SHAPES = { normal: 'Symmetric', right: 'Long tail above', left: 'Long tail below' };
  const SIGMA_LOG = 0.6;              // fixed skew: the long tail is about three times the short one
  const Z95 = 1.959964;

  function shapeK(band) {             // the scale that makes the 95% span match the band
    const w = Math.exp(Z95 * SIGMA_LOG) - Math.exp(-Z95 * SIGMA_LOG);
    return (2 * band) / w;
  }

  /* P(value <= v) under the user's curve. */
  function cdf(v, mu, band, shape) {
    const sg = Math.max(band / 2, 1e-9);
    if (shape !== 'right' && shape !== 'left') return Phi((v - mu) / sg);
    const dir = shape === 'right' ? 1 : -1;
    const k = shapeK(Math.max(band, 1e-9));
    const w = 1 + dir * (v - mu) / k;                 // the lognormal variable this v implies
    if (w <= 0) return dir > 0 ? 0 : 1;
    const below = Phi(Math.log(w) / SIGMA_LOG);
    return dir > 0 ? below : 1 - below;
  }

  /* The height of the curve at v, for drawing only. */
  function pdf(v, mu, band, shape) {
    const sg = Math.max(band / 2, 1e-9);
    if (shape !== 'right' && shape !== 'left') {
      return Math.exp(-0.5 * Math.pow((v - mu) / sg, 2)) / (sg * Math.sqrt(2 * Math.PI));
    }
    const dir = shape === 'right' ? 1 : -1;
    const k = shapeK(Math.max(band, 1e-9));
    const w = 1 + dir * (v - mu) / k;
    if (w <= 1e-12) return 0;
    const lw = Math.log(w);
    return Math.exp(-0.5 * Math.pow(lw / SIGMA_LOG, 2)) / (w * SIGMA_LOG * Math.sqrt(2 * Math.PI) * k);
  }

  /* The reader's probability that a contract pays.

     `thr` is the threshold on the continuous value: for "Above 84" on a
     quantity settled in whole units it is 84.5, because the settled figure is
     rounded and then compared strictly, so a day whose true high is 84.4
     rounds to 84 and does not pay. On a quantity published with its own
     decimals the threshold is the strike itself. `dir` is +1 for above, -1
     for below. */
  const pWin = (thr, dir, mu, band, shape) => {
    // the degenerate branch mirrors pays(): above is strict, its complement
    // takes the boundary. Unreachable from the page (the band is floored), kept
    // consistent so the exported maths cannot disagree with itself.
    if (!(band > 0)) return (dir > 0 ? (mu > thr) : !(mu > thr)) ? 1 : 0;
    const p = 1 - cdf(thr, mu, band, shape);
    return dir > 0 ? p : 1 - p;
  };

  /* Ladder rows -> the instruments a buyer could hold.

     One per buyable side per strike. cost is in dollars per contract and
     includes the fee. */
  /* The window the calculator will allocate inside.

     Outside it the exchange's book thins out: a contract the market puts at
     one chance in fifty has few resting bids on the far side, so a position
     sized here could not actually be filled at anything near the price shown,
     and the payout multiples that make those strikes look attractive are the
     least attainable numbers on the board. They stay drawn, faint, so the
     shape of the whole ladder is still visible — they are simply not
     allocated to. */
  const LIQUID_LO = 0.05, LIQUID_HI = 0.95;

  function instruments(ladder, feeDollars) {
    const out = [];
    (ladder.rows || []).forEach(r => {
      if (r.strike == null || !isFinite(r.strike)) return;
      /* The market's own probability for this strike, which is the Yes price.
         The midpoint where both sides have bids; the one live side otherwise,
         which is the best the book will say. */
      const mkt = r.mid != null ? r.mid : (r.ask != null ? r.ask : (r.bid != null ? r.bid : null));
      const tradeable = mkt != null && mkt >= LIQUID_LO && mkt <= LIQUID_HI;
      const dir = r.dir;                                   // +1 above, -1 below
      const half = ladder.grain === 'integer' ? 0.5 : 0;
      // above k pays past k; at-least k pays at k itself, so its threshold
      // sits half a unit BELOW the strike on an integer quantity, and exactly
      // at it on a continuous one (P(V >= k) = P(V > k) there)
      const thr = r.atLeast ? r.strike - half : r.strike + (dir > 0 ? half : -half);
      // buying Yes now costs one dollar less the No bid, which the feed
      // carries as the ask on the Yes contract
      if (r.ask != null && r.ask > 0 && r.ask < 1) {
        const c = r.ask + feeDollars;
        if (c < 1) out.push({ strike: r.strike, side: 'yes', dir, thr, cost: c, price: r.ask, label: r.label, conid: r.conidYes, atLeast: r.atLeast, mkt, tradeable });
      }
      // buying No now costs one dollar less the Yes bid
      if (r.bid != null && r.bid > 0 && r.bid < 1) {
        const c = (1 - r.bid) + feeDollars;
        // the contract page is addressed by the Yes conid for either side
        if (c < 1) out.push({ strike: r.strike, side: 'no', dir, thr, cost: c, price: 1 - r.bid, label: r.label, conid: r.conidYes, mkt, tradeable });
      }
    });
    return out;
  }

  /* The outcome intervals.

     Every instrument's payoff is decided by which side of its threshold the
     value lands on, so between two neighbouring thresholds nothing changes.
     The intervals between the sorted thresholds are therefore the exact
     outcome space, and each carries its normal probability mass. Intervals
     the curve gives less than a billionth of a chance are dropped: they are
     numerical dust, and under a utility that hates zero payout they would
     otherwise demand meaningless hedges. */
  function bins(instr, mu, band, shape) {
    const ts = Array.from(new Set(instr.map(i => i.thr))).sort((a, b) => a - b);
    const edges = [-Infinity].concat(ts, [Infinity]);
    const out = [];
    for (let b = 0; b + 1 < edges.length; b++) {
      const lo = edges[b], hi = edges[b + 1];
      const m = (band > 0)
        ? cdf(hi, mu, band, shape) - cdf(lo, mu, band, shape)
        : ((mu > lo && mu <= hi) ? 1 : 0);
      if (m <= 1e-9) continue;
      // a representative value strictly inside the interval decides payoffs
      const v = !isFinite(lo) ? hi - 1 : (!isFinite(hi) ? lo + 1 : (lo + hi) / 2);
      out.push({ lo, hi, mass: m, v });
    }
    const tot = out.reduce((a, b2) => a + b2.mass, 0);
    if (tot > 0) out.forEach(b2 => { b2.mass /= tot; });
    return out;
  }

  // does instrument i pay when the value is v
  const pays = (i, v) => {
    const above = v > i.thr;
    return i.side === 'yes' ? (i.dir > 0 ? above : !above) : (i.dir > 0 ? !above : above);
  };

  /* The split: maximise sum_b mass_b * U(x_b) over the simplex sum f = 1,
     f >= 0, where f_j is the share of the money on instrument j and
     x_b = sum_j f_j * a_jb is the payout per dollar in interval b, with
     a_jb = 1/cost when j pays there and 0 when it does not.

     U is the power utility with relative risk aversion `gamma`; gamma 1 is
     the logarithm and the problem is then exactly Kelly's fully-invested
     horse race generalised to overlapping claims. The payout floor XF stands
     in for U(0), which is minus infinity for gamma >= 1: it keeps the
     arithmetic finite in intervals no buyable contract covers, where the
     term is constant in f and moves nothing.

     Projected gradient with backtracking finds the neighbourhood; pairwise
     transfers polished by golden section finish the job, because adjacent
     strikes make near-equivalent instruments and gradient steps crawl along
     the nearly flat ridge between them — stopping with the right objective
     but the wrong split, a bar on the page the true optimum does not hold.
     On the simplex a single coordinate cannot move alone, so the polish
     moves money between pairs. Deterministic throughout. */
  const XF = 1e-4;
  function crra(instr, B, gamma) {
    const n = instr.length;
    if (!n || !B.length) return new Array(n).fill(0);
    if (n === 1) return [1];
    const a = instr.map(i => B.map(b => (pays(i, b.v) ? 1 / i.cost : 0)));
    const U = gamma === 1 ? (x => Math.log(Math.max(x, XF)))
      : (x => (Math.pow(Math.max(x, XF), 1 - gamma) - 1) / (1 - gamma));
    const dU = x => Math.pow(Math.max(x, XF), -gamma);
    const X = f => B.map((b, bi) => f.reduce((s, fj, j) => s + fj * a[j][bi], 0));
    const G = f => {
      const x = X(f);
      let s = 0;
      for (let bi = 0; bi < B.length; bi++) s += B[bi].mass * U(x[bi]);
      return s;
    };
    const grad = f => {
      const x = X(f);
      return instr.map((_, j) => B.reduce((s, b, bi) => s + b.mass * dU(x[bi]) * a[j][bi], 0));
    };
    // Euclidean projection onto the simplex {f >= 0, sum f = 1}
    function project(f) {
      const sorted = f.slice().sort((p, q) => q - p);
      let acc = 0, theta = 0;
      for (let k = 0; k < sorted.length; k++) {
        acc += sorted[k];
        const t = (acc - 1) / (k + 1);
        if (k + 1 === sorted.length || sorted[k + 1] <= t) { theta = t; break; }
      }
      return f.map(v => Math.max(0, v - theta));
    }
    let f = new Array(n).fill(1 / n), best = G(f), eta = 0.1;
    for (let it = 0; it < 600; it++) {
      const d = grad(f);
      const scale = Math.max(1e-12, Math.max(...d.map(Math.abs)));
      let cand = project(f.map((v, j) => v + (eta / scale) * d[j]));
      let gc = G(cand);
      let tries = 0;
      while (gc < best - 1e-12 && tries < 30) { eta *= 0.5; cand = project(f.map((v, j) => v + (eta / scale) * d[j])); gc = G(cand); tries++; }
      if (gc <= best + 1e-14 && tries >= 30) break;
      if (gc > best) { f = cand; best = gc; }
      if (it % 20 === 19) eta = Math.min(eta * 2, 0.5);
    }
    // pairwise polish on the cached payout vector
    const x = X(f);
    const PHI2 = (Math.sqrt(5) - 1) / 2;
    const pairG = (j, k, t) => {           // move t from k to j
      let s = 0;
      for (let bi = 0; bi < B.length; bi++) s += B[bi].mass * U(x[bi] + t * (a[j][bi] - a[k][bi]));
      return s;
    };
    for (let sweep = 0; sweep < 80; sweep++) {
      let gained = 0;
      for (let j = 0; j < n; j++) {
        for (let k = j + 1; k < n; k++) {
          let lo = -f[j], hi = f[k];
          if (hi - lo < 1e-12) continue;
          for (let it2 = 0; it2 < 44; it2++) {
            const p = hi - PHI2 * (hi - lo), q = lo + PHI2 * (hi - lo);
            if (pairG(j, k, p) < pairG(j, k, q)) lo = p; else hi = q;
          }
          const t = (lo + hi) / 2;
          const before = pairG(j, k, 0), after = pairG(j, k, t);
          if (after > before + 1e-14) {
            for (let bi = 0; bi < B.length; bi++) x[bi] += t * (a[j][bi] - a[k][bi]);
            f[j] += t; f[k] -= t;
            gained += after - before;
          }
        }
      }
      if (gained < 1e-12) break;
    }
    return f;
  }

  /* Shares -> whole contracts, with the change reinvested.

     Flooring each line leaves coins, and the brief here is that the whole
     amount goes in. So the leftover buys one contract at a time, each time
     the one that most raises the scenario's own expected utility, until it
     cannot afford the cheapest thing left. What remains after that is
     genuinely unspendable. */
  function fill(instr, f, budget, B, gamma) {
    const U = gamma === 1 ? (x => Math.log(Math.max(x, XF)))
      : (x => (Math.pow(Math.max(x, XF), 1 - gamma) - 1) / (1 - gamma));
    const n = instr.map((i, j) => Math.floor(budget * f[j] / i.cost + 1e-9));
    let spent = instr.reduce((s, i, j) => s + n[j] * i.cost, 0);
    // payout per dollar of budget, per interval, for the utility comparisons
    const x = B.map(b => instr.reduce((s, i, j) => s + (pays(i, b.v) ? n[j] : 0), 0) / budget);
    const score = () => B.reduce((s, b, bi) => s + b.mass * U(x[bi]), 0);
    for (let guard = 0; guard < 4000; guard++) {
      const residual = budget - spent;
      let bj = -1, bg = -Infinity;
      const base = score();
      for (let j = 0; j < instr.length; j++) {
        if (instr[j].cost > residual + 1e-9) continue;
        let s = 0;
        for (let bi = 0; bi < B.length; bi++) s += B[bi].mass * U(x[bi] + (pays(instr[j], B[bi].v) ? 1 / budget : 0));
        const gain = s - base;
        if (gain > bg + 1e-15) { bg = gain; bj = j; }
      }
      if (bj < 0) break;
      n[bj] += 1; spent += instr[bj].cost;
      for (let bi = 0; bi < B.length; bi++) if (pays(instr[bj], B[bi].v)) x[bi] += 1 / budget;
    }
    const hold = instr.map((i, j) => ({ i, n: n[j], spent: n[j] * i.cost })).filter(q => q.n > 0);
    return { hold, spent, cash: budget - spent };
  }

  // gross payout of a set of holdings at value v
  const payoutAt = (hold, v) => hold.reduce((a, x) => a + (pays(x.i, v) ? x.n : 0), 0);

  function scenarioStats(sc, B) {
    let ev = 0, worst = Infinity, bestv = -Infinity;
    B.forEach(b => {
      const p = payoutAt(sc.hold, b.v);
      ev += b.mass * p;
      worst = Math.min(worst, p); bestv = Math.max(bestv, p);
    });
    if (!B.length || !sc.hold.length) { worst = 0; bestv = 0; }
    return { ev, worst, best: bestv, evNet: ev - sc.spent, worstNet: worst - sc.spent, bestNet: bestv - sc.spent };
  }

  // ---------------------------------------------------------- ladder sources
  /* The teaching ladder. Made up: a plausible daily-high board whose prices
     centre a degree and a half below the default belief, so the page opens on
     a case with something to tilt toward. Every price is invented and says so. */
  function teachingLadder() {
    const mu0 = 86.5, s0 = 2.2, rows = [];
    for (let k = 80; k <= 94; k++) {
      const yesFair = 1 - Phi((k + 0.5 - mu0) / s0);
      const half = 0.02 + 0.02 * Math.exp(-Math.pow((yesFair - 0.5) / 0.3, 2));
      const bid = Math.max(0.01, Math.round((yesFair - half) * 100) / 100);
      const ask = Math.min(0.99, Math.round((yesFair + half) * 100) / 100);
      if (bid < ask) rows.push({ strike: k, label: 'Above ' + k, dir: 1, bid, ask, mid: (bid + ask) / 2 });
    }
    return {
      title: 'A made-up daily-high ladder',
      sub: 'Example prices, invented for teaching — they are not a market. Load a live ladder above to work on real prices.',
      unit: '°F', grain: 'integer', rows, synthetic: true,
      defaults: { value: 88, band: 4 },
    };
  }

  // "Above 84" / "Below 68" / "At Least 3" -> a numeric strike and a direction
  function parseRow(r) {
    const lab = String(r.label || '');
    let dir = null, strike = null;
    if (/^above\s/i.test(lab)) dir = 1;
    else if (/^below\s/i.test(lab)) dir = -1;
    else if (/^at least\s/i.test(lab)) dir = 1;
    if (dir == null || r.strike == null || !isFinite(r.strike)) return null;
    strike = +r.strike;
    // "At Least k" pays at k itself; "Above k" pays strictly beyond it. The
    // strike keeps its printed value — rewriting it moved the row a gridline
    // below its own label — and the threshold shift happens in instruments().
    const atLeast = /^at least\s/i.test(lab);
    return { strike, label: lab, dir, atLeast, bid: r.bid != null ? +r.bid : null, ask: r.ask != null ? +r.ask : null, mid: r.mid != null ? +r.mid : null,
             conidYes: r.conidYes != null ? r.conidYes : r.conid, conidNo: r.conidNo };
  }

  // implied crossing of the Yes price through 50 cents, for the value prefill
  function impliedMedian(rows) {
    const rs = rows.filter(r => r.mid != null).sort((a, b) => a.strike - b.strike);
    // too thin to interpolate: the middle of the ladder still beats keeping a
    // value typed against some other market's units
    if (rs.length < 2) {
      const all = rows.slice().sort((a, b) => a.strike - b.strike);
      return all.length ? (all[0].strike + all[all.length - 1].strike) / 2 : null;
    }
    const dirUp = rs[0].dir > 0;              // above-ladders fall with strike
    for (let i = 0; i + 1 < rs.length; i++) {
      const a = rs[i], b = rs[i + 1];
      const crosses = dirUp ? (a.mid >= 0.5 && b.mid < 0.5) : (a.mid <= 0.5 && b.mid > 0.5);
      if (crosses && a.mid !== b.mid) return a.strike + (0.5 - a.mid) / (b.mid - a.mid) * (b.strike - a.strike);
    }
    return (rs[0].strike + rs[rs.length - 1].strike) / 2;
  }

  // ------------------------------------------------------------------ state
  const S = {
    ladder: null,            // {title, sub, unit, grain, rows, synthetic}
    value: 0, band: 1, budget: 100,
    shape: 'normal',
    scenKey: 'middle',
    pick: { kind: 'teaching' },
  };

  function sigma() { return Math.max(S.band / 2, 1e-6); }   // the band is two sigma
  const beliefCdf = v => cdf(v, S.value, Math.max(S.band, 1e-9), S.shape);
  const beliefPdf = v => pdf(v, S.value, Math.max(S.band, 1e-9), S.shape);

  /* The resolution the contract settles on.

     A daily temperature settles on a whole degree, so a value of 88.3 is not a
     value the market can ever resolve to and typing one invites false
     precision the ladder cannot honour. An integer-grained board therefore
     steps in ones; a board that settles on a published figure with its own
     decimals keeps a tenth of a strike interval, which is fine enough to sit
     between strikes and coarse enough to type. */
  function grainStep() {
    const lad = S.ladder || {};
    if (lad.grain === 'integer') return 1;
    const ks = (lad.rows || []).map(r => r.strike).filter(v => isFinite(v)).sort((a, b) => a - b);
    let gap = Infinity;
    for (let i = 1; i < ks.length; i++) if (ks[i] - ks[i - 1] > 1e-9) gap = Math.min(gap, ks[i] - ks[i - 1]);
    if (!isFinite(gap)) return 0.1;
    // a round-ish tenth of the strike spacing
    const raw = gap / 10;
    const mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    return [1, 2, 5, 10].map(f => f * mag).find(v => v >= raw * 0.999) || mag * 10;
  }
  const snap = v => { const st = grainStep(); return Math.round(v / st) * st; };
  // the step decides how many decimals the box should show
  const snapFix = v => {
    const st = grainStep();
    const dp = Math.max(0, Math.min(6, Math.ceil(-Math.log(st) / Math.LN10)));
    return +v.toFixed(dp);
  };

  function compute() {
    const fee = (window.WXM && WXM.feeCents ? WXM.feeCents() : 0.5) / 100;
    const all = instruments(S.ladder, fee);
    const mu = S.value, band = Math.max(S.band, 1e-9);
    all.forEach(i => {
      i.p = pWin(i.thr, i.side === 'yes' ? i.dir : -i.dir, mu, band, S.shape);
      // what the user's curve says this side is worth, against what it costs
      i.edge = i.p - i.cost;
    });
    // only the liquid window is allocated across; the rest is drawn and skipped
    const instr = all.filter(i => i.tradeable);
    const B = bins(instr, mu, band, S.shape);
    // the share of the curve where at least one buyable contract pays: money
    // placed anywhere is lost in the rest, whatever the split
    const cover = B.reduce((s, b) => s + (instr.some(i => pays(i, b.v)) ? b.mass : 0), 0);
    const scen = {};
    SCEN.forEach(sc => {
      const f = crra(instr, B, sc.gamma);
      const s = fill(instr, f, S.budget, B, sc.gamma);
      s.stats = scenarioStats(s, B);
      scen[sc.key] = s;
    });
    return { all, instr, B, scen, fee, cover,
             skipped: all.filter(i => !i.tradeable).length };
  }

  // ------------------------------------------------------------------- svg
  const fm$ = v => '$' + (Math.round(v * 100) / 100).toFixed(2);
  const fmS = v => (v < 0 ? '−' : '+') + fm$(Math.abs(v)).slice(1);   // signed, for net outcomes
  const fmc = v => {
    const c = Math.round(v * 1000) / 10;                  // costs carry the half-cent fee
    return (c % 1 ? c.toFixed(1) : String(c)) + '¢';
  };
  const fmp = v => (v >= 0.995 ? '>99' : v <= 0.005 ? '<1' : Math.round(v * 100)) + '%';
  const fmv = (v, unit) => {
    const s = Math.abs(v) >= 100 ? v.toFixed(0) : (Math.round(v * 10) / 10).toString();
    return s + (unit ? ' ' + unit : '');
  };
  // a payout multiple: 14x for the cheap tail, 1.05x for the near-certainty
  const fmx = m => (m >= 20 ? Math.round(m) : m >= 2 ? m.toFixed(1) : m.toFixed(2)) + '×';

  /* Axis ticks that land on readable numbers, with the decimals the step
     needs. The old rule took an eighth of the domain and printed it to one
     decimal, so a rice ladder spanning half a bushel printed 5.2 twice and
     skipped 5.1 entirely. */
  function ticks(lo, hi, want) {
    const span = Math.max(hi - lo, 1e-9);
    const raw = span / Math.max(want, 2);
    const mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    const step = [1, 2, 2.5, 5, 10].map(f => f * mag).find(v => v >= raw * 0.999) || mag * 10;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    // the fewest decimals that still tell every tick apart: deriving them from
    // the step alone rounded a 2.5 step's middle tick onto its neighbour
    let dp = 0;
    while (dp < 6 && new Set(out.map(v => v.toFixed(dp))).size < out.length) dp++;
    return { vals: out, dp };
  }

  function draw() {
    const host = $('#alloc'); if (!host) return;
    if (tip) tip.hide();                  // the element under the cursor is about to be rebuilt
    host.innerHTML = '';
    const lad = S.ladder, unit = (lad && lad.unit) || '';
    if (!lad || !(lad.rows || []).length) {
      host.appendChild(h('p', { class: 'cap', text: 'Nothing to draw.' }));
      const note0 = $('#allocNote'); if (note0) note0.textContent = '';
      return;
    }
    const R = compute();
    const scSel = SCEN.find(s => s.key === S.scenKey) || SCEN[1];
    const sel = R.scen[scSel.key];
    const mu = S.value, band = Math.max(S.band, 1e-9);

    /* The value axis covers what is actually in play.

       It used to span every strike on the board plus the whole prediction,
       which on a ladder like rice — strikes a hundredth apart, a prediction a
       tenth wide — left the interesting part squeezed into a fifth of the
       panel with the labels on top of one another. It now covers the strikes
       the calculator will trade and the middle of the curve, and only widens
       past that for strikes it is actually holding. */
    const liquid = R.instr.map(i => i.strike);
    const held = {};
    sel.hold.forEach(x => { held[x.i.side + '@' + x.i.strike] = x; });
    const heldStrikes = sel.hold.map(x => x.i.strike);
    const anchor = liquid.concat(heldStrikes);
    const all = lad.rows.map(r => r.strike);
    const lo0 = anchor.length ? Math.min(...anchor) : Math.min(...all);
    const hi0 = anchor.length ? Math.max(...anchor) : Math.max(...all);
    let vHi = Math.max(hi0, mu + 2.4 * sigma());
    let vLo = Math.min(lo0, mu - 2.4 * sigma());
    const pad = Math.max((vHi - vLo) * 0.08, grainStep() * 0.5);
    vHi += pad; vLo -= pad;

    const W = 960, T = 52, Bm = 34;
    const shown = lad.rows.filter(r => r.strike >= vLo && r.strike <= vHi);
    const rowH = Math.max(15, Math.min(30, 520 / Math.max(shown.length, 1)));
    const H = Math.max(440, Math.min(780, T + Bm + shown.length * rowH + 60));
    const y = v => T + (vHi - v) / (vHi - vLo) * (H - T - Bm);

    // the prediction | the ladder | collateral and payout | the whole set
    const P1 = { x0: 46, x1: 150 }, PL = 214, P2 = { x0: 220, x1: 430 },
          P3 = { x0: 470, x1: 700 }, P4 = { x0: 738, x1: 944 };
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts', id: 'allocSvg' });

    const tk = ticks(vLo, vHi, 7);
    tk.vals.forEach(v => {
      svg.appendChild(txt(v.toFixed(tk.dp), { x: P1.x0 - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
      svg.appendChild(el('line', { x1: P1.x0, x2: P4.x1, y1: y(v), y2: y(v), class: 'grid' }));
    });
    if (unit) svg.appendChild(txt(unit, { x: P1.x0 - 6, y: H - Bm + 15, 'text-anchor': 'end', class: 'ax' }));

    /* Two lines of title, then the note. The titles name what the column is
       for in the reader's words, which makes them longer than one line of a
       960-wide figure holds. */
    const head = (x0, lines, note) => {
      lines.forEach((ln, i) => svg.appendChild(txt(ln, { x: x0, y: T - 42 + i * 11, class: 'ax', 'font-weight': 700 })));
      if (note) svg.appendChild(txt(note, { x: x0, y: T - 42 + lines.length * 11 + 1, class: 'ax' }));
    };

    // ---- 1: the prediction
    head(P1.x0, ['THE PREDICTION'], 'drag the dot or the band edges');
    {
      const xd = q => P1.x0 + q * (P1.x1 - P1.x0);
      let pk = 0;
      const n = 110, pts = [];
      for (let i = 0; i <= n; i++) {
        const v = vLo + (vHi - vLo) * i / n;
        const d = beliefPdf(v);
        pk = Math.max(pk, d);
        pts.push([v, d]);
      }
      pk = pk || 1;
      const b0 = Math.max(vLo, quantile(0.025)), b1 = Math.min(vHi, quantile(0.975));
      svg.appendChild(el('rect', { x: P1.x0, y: y(b1), width: P1.x1 - P1.x0,
                                   height: Math.max(y(b0) - y(b1), 1), fill: 'var(--accent)', opacity: 0.08 }));
      const path = pts.map((q, i) => (i ? 'L' : 'M') + xd(q[1] / pk * 0.92).toFixed(1) + ',' + y(q[0]).toFixed(1)).join('');
      svg.appendChild(el('path', { d: 'M' + xd(0).toFixed(1) + ',' + y(vLo).toFixed(1) + path.slice(1)
                                     + 'L' + xd(0).toFixed(1) + ',' + y(vHi).toFixed(1) + 'Z',
                                   fill: 'var(--accent)', opacity: 0.16, 'pointer-events': 'none' }));
      svg.appendChild(el('path', { d: path, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 1.6, 'pointer-events': 'none' }));
      svg.appendChild(el('line', { x1: P1.x0, x2: P1.x1, y1: y(mu), y2: y(mu), stroke: 'var(--accent)',
                                   'stroke-width': 1.4, 'stroke-dasharray': '4 3' }));
      const mid = (P1.x0 + P1.x1) / 2;
      [[mu, 'mu'], [quantile(0.975), 'hi'], [quantile(0.025), 'lo']].forEach(([v, kind]) => {
        if (v < vLo || v > vHi) return;
        const c = el('circle', { cx: mid, cy: y(v), r: kind === 'mu' ? 7 : 5,
                                 fill: kind === 'mu' ? 'var(--accent)' : 'var(--panel)', stroke: 'var(--accent)',
                                 'stroke-width': 2, cursor: 'ns-resize', style: 'touch-action:none' });
        c.dataset.drag = kind;
        svg.appendChild(c);
      });
      svg.appendChild(txt(fmv(mu, unit), { x: mid + 11, y: y(mu) - 8, 'font-weight': 700, 'font-size': 12, fill: 'var(--accent)' }));
      svg.addEventListener('pointerdown', ev => {
        const k = ev.target && ev.target.dataset && ev.target.dataset.drag;
        if (!k) return;
        ev.preventDefault();
        const box = svg.getBoundingClientRect();
        const scale = (vHi - vLo) / ((H - T - Bm) / H * box.height);
        const yTop = box.top + T / H * box.height;
        const vAt = cy => vHi - (cy - yTop) * scale;
        const move = e => {
          const v = vAt(e.clientY);
          if (k === 'mu') S.value = snapFix(snap(v));
          else S.band = Math.max(grainStep(), snapFix(snap(Math.abs(v - S.value))));
          syncInputs(); draw();
        };
        const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', up);
      });
    }

    /* Strike labels, thinned. Two strikes a hundredth apart cannot both carry
       a label at this height; the ladder's shape is still drawn for every one
       of them, and the box on hover names the strike exactly. */
    // strikes ascend, so their y positions descend; the gap is a distance
    let lastLab = null;
    shown.slice().sort((a, b) => b.strike - a.strike).forEach(r => {
      const yy = y(r.strike);
      if (lastLab != null && Math.abs(yy - lastLab) < 10.5) return;
      lastLab = yy;
      const inst = R.all.find(i => i.strike === r.strike);
      svg.appendChild(txt(r.label, { x: PL, y: yy + 3.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9,
                                     opacity: (inst && !inst.tradeable) ? 0.42 : 1 }));
    });

    // ---- 2: the ladder, its prices, and where the user disagrees
    head(P2.x0, ['WHERE IN THE LADDER', 'TO ALLOCATE'],
         'market price, then the user’s own');
    {
      const cw = (P2.x1 - P2.x0) / 100;
      const px = q => P2.x0 + Math.max(0, Math.min(1, q)) * 100 * cw;
      const impl = [];
      shown.forEach(r => {
        const yy = y(r.strike);
        const bh = Math.min(rowH * 0.68, 15), by = yy - bh / 2;
        const iy = R.all.find(i => i.strike === r.strike && i.side === 'yes');
        const ino = R.all.find(i => i.strike === r.strike && i.side === 'no');
        const inst = iy || ino;
        const mkt = inst ? inst.mkt : null;
        const live = inst && inst.tradeable;
        if (mkt == null) {
          svg.appendChild(el('rect', { x: P2.x0, y: by, width: P2.x1 - P2.x0, height: bh, rx: 2, fill: 'none',
                                       stroke: 'var(--rule)', 'stroke-dasharray': '3 3', opacity: 0.6 }));
          return;
        }
        const split = px(mkt);
        const op = live ? 0.85 : 0.2;
        svg.appendChild(el('rect', { x: P2.x0, y: by, width: Math.max(split - P2.x0, 0.5), height: bh, fill: 'var(--yes)', opacity: op }));
        svg.appendChild(el('rect', { x: split, y: by, width: Math.max(P2.x1 - split, 0.5), height: bh, fill: 'var(--no)', opacity: op }));
        // the user's own probability that this strike's Yes pays, and the gap
        const pu = iy ? iy.p : (ino ? 1 - ino.p : null);
        if (pu != null && live) {
          impl.push([px(pu), yy]);
          const a = split, b = px(pu);
          if (Math.abs(b - a) > 3) {
            svg.appendChild(el('line', { x1: a, x2: b, y1: yy, y2: yy, stroke: 'var(--ink)', 'stroke-width': 1.1, opacity: 0.75 }));
            const d = b > a ? -1 : 1;
            svg.appendChild(el('path', { d: 'M' + b + ',' + yy + 'L' + (b + d * 4.5) + ',' + (yy - 3) + 'L' + (b + d * 4.5) + ',' + (yy + 3) + 'Z',
                                         fill: 'var(--ink)', opacity: 0.75 }));
          }
        }
        ['yes', 'no'].forEach(side => {
          const x = held[side + '@' + r.strike];
          if (!x) return;
          const seg = side === 'yes' ? { x: P2.x0, w: Math.max(split - P2.x0, 3) } : { x: split, w: Math.max(P2.x1 - split, 3) };
          svg.appendChild(el('rect', { x: seg.x + 0.5, y: by - 1.5, width: Math.max(seg.w - 1, 2), height: bh + 3, rx: 2,
                                       fill: 'none', stroke: 'var(--ink)', 'stroke-width': 2.2, 'pointer-events': 'none' }));
          const t = x.n + ' ct @ ' + fmc(x.i.price);
          const tw = t.length * 5.4 + 10;
          const cx2 = Math.min(Math.max(seg.x + seg.w / 2, P2.x0 + tw / 2), P2.x1 - tw / 2);
          svg.appendChild(el('rect', { x: cx2 - tw / 2, y: yy - 6.5, width: tw, height: 13, rx: 6.5, fill: 'var(--panel)', opacity: 0.93, 'pointer-events': 'none' }));
          svg.appendChild(txt(t, { x: cx2, y: yy + 3.2, 'text-anchor': 'middle', 'font-size': 9, 'font-weight': 700, fill: 'var(--ink)', 'pointer-events': 'none' }));
        });
        const band2 = el('rect', { x: P2.x0, y: by - 2, width: P2.x1 - P2.x0, height: bh + 4, fill: 'transparent' });
        bind(band2, () => priceTip(r, R, held));
        if (lad.productConid && r.conidYes != null && window.WXM && WXM.contractUrl) {
          WXM.linkTo(band2, WXM.contractUrl(lad.productConid, r.conidYes), 'Open ' + r.label + ' on IBKR');
        }
        svg.appendChild(band2);
      });
      // the user's implied ladder, as one curve through the arrow heads
      if (impl.length > 1) {
        impl.sort((a, b) => a[1] - b[1]);
        svg.appendChild(el('path', { d: impl.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ',' + q[1].toFixed(1)).join(''),
                                     fill: 'none', stroke: 'var(--ink)', 'stroke-width': 1.6, 'stroke-dasharray': '4 2',
                                     opacity: 0.8, 'pointer-events': 'none' }));
      }
      [0, 50, 100].forEach(c => svg.appendChild(txt(c + '¢', { x: px(c / 100), y: H - Bm + 15, 'text-anchor': 'middle', class: 'ax' })));
    }

    // ---- 3: what each line ties up, and what it returns if it comes in
    head(P3.x0, ['COLLATERAL AND PAYOUT AT', 'EACH STRIKE IF CORRECT'],
         'one scale: the gap is the multiple');
    {
      /* Both sides on the same dollar scale.

         Scaled separately the two bars said nothing: a line that ties up two
         dollars to return sixty looked much the same as one that ties up
         twenty to return thirty. On one scale the payout bar is longer than
         the collateral bar by exactly the multiple, which is the fact the
         column exists to show. The spine sits left of centre because the
         payout is always the longer of the two. */
      const spine = P3.x0 + (P3.x1 - P3.x0) * 0.26;
      const maxPay = Math.max(1e-9, ...SCEN.map(s2 => R.scen[s2.key].hold.reduce((a, x) => Math.max(a, x.n), 0)));
      const maxCol = Math.max(1e-9, ...SCEN.map(s2 => R.scen[s2.key].hold.reduce((a, x) => Math.max(a, x.spent), 0)));
      // one dollars-per-pixel that fits the widest bar on each side
      const per = Math.min((spine - P3.x0 - 4) / maxCol, (P3.x1 - spine - 30) / maxPay);
      const wL = d => Math.max(1.5, d * per);
      const wR = d => Math.max(1.5, d * per);
      svg.appendChild(el('line', { x1: spine, x2: spine, y1: T, y2: H - Bm, stroke: 'var(--rule)' }));
      svg.appendChild(txt('◂ committed', { x: spine - 4, y: H - Bm + 15, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
      svg.appendChild(txt('returned ▸', { x: spine + 4, y: H - Bm + 15, class: 'ax', 'font-size': 9 }));
      shown.forEach(r => {
        ['yes', 'no'].forEach(side => {
          const x = held[side + '@' + r.strike]; if (!x) return;
          const yy = y(r.strike), bh = Math.min(rowH * 0.62, 14), by = yy - bh / 2;
          const cw2 = wL(x.spent), pw = wR(x.n);
          svg.appendChild(el('rect', { x: spine - cw2, y: by, width: cw2, height: bh, rx: 1.5, fill: 'var(--collat)' }));
          svg.appendChild(el('rect', { x: spine, y: by, width: pw, height: bh, rx: 1.5, fill: 'var(--payout)' }));
          const mult = x.n / Math.max(x.spent, 1e-9);
          const lab = fmx(mult);
          const lw = lab.length * 5.6 + 8;
          const cx = Math.min(spine + pw + 4 + lw / 2, P3.x1 - lw / 2);
          svg.appendChild(el('rect', { x: cx - lw / 2, y: yy - 6, width: lw, height: 12, rx: 6, fill: 'var(--panel)', opacity: 0.9, 'pointer-events': 'none' }));
          svg.appendChild(txt(lab, { x: cx, y: yy + 3.2, 'text-anchor': 'middle', 'font-size': 9, 'font-weight': 700,
                                     fill: 'var(--payout)', 'pointer-events': 'none' }));
          const hit = el('rect', { x: spine - cw2, y: by - 2, width: cw2 + pw, height: bh + 4, fill: 'transparent',
                                   cursor: x.i.conid && lad.productConid ? 'pointer' : null });
          bind(hit, () => barTip(x, R, unit));
          if (x.i.conid && lad.productConid && window.WXM && WXM.contractUrl) {
            WXM.linkTo(hit, WXM.contractUrl(lad.productConid, x.i.conid), 'Open ' + x.i.label + ' on IBKR');
          }
          svg.appendChild(hit);
        });
      });
      svg.appendChild(txt(fm$(sel.spent).replace(/\.00$/, '') + ' in total', { x: P3.x0, y: H - Bm + 27, class: 'ax' }));
    }

    // ---- 4: the whole set, outcome by outcome
    head(P4.x0, ['WHAT THE ENTIRE ALLOCATION PAYS', 'BY WHERE THE NUMBER LANDS'], 'gross dollars');
    {
      const maxPay = Math.max(S.budget * 1.15, ...SCEN.map(s2 => {
        const sc = R.scen[s2.key];
        return R.B.reduce((a, b) => Math.max(a, payoutAt(sc.hold, b.v)), 0);
      }));
      const xp = d => P4.x0 + Math.min(d / maxPay, 1) * (P4.x1 - P4.x0);
      const dt = ticks(0, maxPay, 3);
      dt.vals.forEach(d => {
        if (d <= 0) return;
        svg.appendChild(el('line', { x1: xp(d), x2: xp(d), y1: T, y2: H - Bm, class: 'grid' }));
        svg.appendChild(txt('$' + d.toFixed(dt.dp), { x: xp(d), y: H - Bm + 15, 'text-anchor': 'middle', class: 'ax' }));
      });
      /* One bar per outcome band, not a line.

         What the set returns is a step: it is one number for every value
         between two neighbouring thresholds and jumps at each of them. Drawn
         as a bar per band that reads immediately; drawn as a line it invited
         the eye to interpolate across jumps that cannot be interpolated. */
      const ts = Array.from(new Set(R.instr.map(i => i.thr))).sort((a, b) => a - b).filter(t => t > vLo && t < vHi);
      const edges = [vLo].concat(ts, [vHi]);
      for (let b = 0; b + 1 < edges.length; b++) {
        const v0 = edges[b], v1 = edges[b + 1];
        const pay = payoutAt(sel.hold, (v0 + v1) / 2);
        const yTop = y(v1), hgt = Math.max(y(v0) - y(v1) - 1.4, 1.2);
        svg.appendChild(el('rect', { x: P4.x0, y: yTop + 0.7, width: Math.max(xp(pay) - P4.x0, 0.6), height: hgt,
                                     fill: scSel.col, opacity: 0.85 }));
      }
      SCEN.filter(s2 => s2.key !== scSel.key).forEach(s2 => {
        const sc = R.scen[s2.key];
        if (!sc.hold.length) return;
        let d = '';
        for (let b = 0; b + 1 < edges.length; b++) {
          const v0 = edges[b], v1 = edges[b + 1];
          const pay = payoutAt(sc.hold, (v0 + v1) / 2);
          d += (b ? 'L' : 'M') + xp(pay).toFixed(1) + ',' + y(v0).toFixed(1) + 'L' + xp(pay).toFixed(1) + ',' + y(v1).toFixed(1);
        }
        svg.appendChild(el('path', { d, fill: 'none', stroke: s2.col, 'stroke-width': 1.2, opacity: 0.6, 'pointer-events': 'none' }));
      });
      const bx = xp(S.budget), bRight = bx > (P4.x0 + P4.x1) / 2;
      svg.appendChild(el('line', { x1: bx, x2: bx, y1: T, y2: H - Bm, stroke: 'var(--ink)', 'stroke-dasharray': '4 3', opacity: 0.6 }));
      svg.appendChild(txt('the ' + fm$(S.budget).replace(/\.00$/, '') + ' committed', { x: bx, y: H - Bm + 27,
                          'font-size': 9.5, fill: 'var(--muted)', 'text-anchor': 'middle' }));
      const band3 = el('rect', { x: P4.x0, y: T, width: P4.x1 - P4.x0, height: H - T - Bm, fill: 'transparent' });
      let mark = null;
      band3.addEventListener('mousemove', ev => {
        const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
        const q = pt.matrixTransform(svg.getScreenCTM().inverse());
        const v = vHi - (q.y - T) / (H - T - Bm) * (vHi - vLo);
        if (!mark) { mark = el('line', { x1: P4.x0, x2: P4.x1, stroke: 'var(--ink)', 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.5, 'pointer-events': 'none' }); svg.appendChild(mark); }
        mark.setAttribute('y1', q.y); mark.setAttribute('y2', q.y);
        const gr = lad.grain === 'integer' ? Math.round(v) : +v.toFixed(tk.dp);
        const rows = SCEN.map(s2 => {
          const sc = R.scen[s2.key];
          const pay = payoutAt(sc.hold, v);
          return ['<span class="sw" style="background:' + s2.col + '"></span>' + s2.name,
                  fm$(pay) + ' (' + fmS(pay - sc.spent) + ' net)'];
        });
        tip.show(ev, tip.rows('If the number lands at ' + fmv(gr, unit), rows, 'gross payout, and net of the capital committed'));
      });
      band3.addEventListener('mouseleave', () => { tip.hide(); if (mark) { mark.remove(); mark = null; } });
      svg.insertBefore(band3, svg.firstChild);
    }

    host.appendChild(svg);

    const chips = h('div', { class: 'allocChips' });
    SCEN.forEach(s2 => {
      const sc = R.scen[s2.key], st = sc.stats;
      const on = s2.key === scSel.key;
      const c = h('button', { class: 'allocChip' + (on ? ' on' : ''), type: 'button' }, [
        h('b', { text: s2.name, style: 'color:' + s2.col }),
        h('span', { text: s2.blurb }),
        h('span', { text: 'expected value under the curve ' + fmS(st.evNet) }),
        h('span', { text: 'worst ' + fmS(st.worstNet) + ' · best ' + fmS(st.bestNet) }),
      ]);
      c.onclick = () => { S.scenKey = s2.key; draw(); };
      chips.appendChild(c);
    });
    host.appendChild(chips);

    const note = $('#allocNote');
    if (note) {
      const nct = sel.hold.reduce((a, x) => a + x.n, 0);
      let msg;
      if (!R.all.length) {
        msg = 'Nothing on this ladder can be bought right now — no strike has a resting bid on the other side to buy against.';
      } else if (!R.instr.length) {
        msg = 'Every strike on this ladder sits outside the 5 to 95 percent window the calculator will allocate inside, so none is priced where a position could reliably be filled.';
      } else {
        msg = 'The ' + scSel.name.toLowerCase() + ' split buys ' + nct + (nct === 1 ? ' contract' : ' contracts') + ' across '
          + sel.hold.length + (sel.hold.length === 1 ? ' line' : ' lines') + ' for ' + fm$(sel.spent)
          + (sel.cash > 0.005 ? ' — the ' + fm$(sel.cash).replace('$0.', '') + '¢ left cannot buy a whole contract' : '')
          + '. Expected values are under the user’s own curve, not the market’s.';
        if (R.skipped) {
          msg += ' ' + R.skipped + ' of the ' + R.all.length + ' buyable sides are drawn faint and left out: the market puts them '
            + 'outside 5 to 95 percent, where the book thins and a position could not reliably be filled.';
        }
        if (R.cover < 0.995) {
          msg += ' The curve puts ' + fmp(1 - R.cover) + ' of its chance where nothing allocated pays.';
        }
      }
      note.textContent = (lad.synthetic ? lad.sub + ' ' : '') + msg;
    }
  }

  /* The value at a given probability under the user's curve, by bisection —
     the skewed shapes have no closed-form inverse and this is called a handful
     of times per redraw. */
  function quantile(q) {
    const mu = S.value, band = Math.max(S.band, 1e-9);
    let lo = mu - 6 * band, hi = mu + 6 * band;
    for (let i = 0; i < 60; i++) {
      const m = (lo + hi) / 2;
      if (beliefCdf(m) < q) lo = m; else hi = m;
    }
    return (lo + hi) / 2;
  }

  // hover content
  function bind(node, make) {
    node.addEventListener('mousemove', ev => tip.show(ev, make()));
    node.addEventListener('mouseleave', () => tip.hide());
  }
  function priceTip(r, R, held) {
    const iy = R.instr.find(i => i.strike === r.strike && i.side === 'yes');
    const ino = R.instr.find(i => i.strike === r.strike && i.side === 'no');
    const rows = [];
    if (iy) rows.push(['Buy Yes now at', fmc(iy.price) + ' · pays ' + fmx(1 / iy.cost)]);
    if (ino) rows.push(['Buy No now at', fmc(ino.price) + ' · pays ' + fmx(1 / ino.cost)]);
    if (!iy && !ino) rows.push(['No resting bids', 'neither side can be bought now']);
    if (iy) rows.push(['The user’s chance Yes pays', fmp(iy.p)]);
    const hy = held['yes@' + r.strike], hn = held['no@' + r.strike];
    if (hy) rows.push(['This split buys', hy.n + ' Yes for ' + fm$(hy.spent)]);
    if (hn) rows.push(['This split buys', hn.n + ' No for ' + fm$(hn.spent)]);
    return tip.rows(r.label, rows, 'multiples are per contract, net of nothing; the fee is inside the cost');
  }
  function barTip(x, R, unit) {
    const i = x.i;
    const yes = i.side === 'yes';
    return tip.rows((yes ? 'Buy Yes' : 'Buy No') + ' · ' + i.label, [
      [yes ? 'Buy Yes now at' : 'Buy No now at', fmc(i.price)],
      ['Cost with the fee', fmc(i.cost)],
      ['Contracts', String(x.n)],
      ['Dollars in', fm$(x.spent)],
      ['Pays if it hits', fm$(x.n) + ' (' + fmx(x.n / Math.max(x.spent, 1e-9)) + ')'],
      ['The user’s chance it pays', fmp(i.p)],
      ['Breaks even if the chance is', fmp(i.cost)],
    ], 'the multiple is the price read from the other end');
  }
  function instTip(i, R, unit) {
    const yes = i.side === 'yes';
    return tip.rows((yes ? 'Yes' : 'No') + ' · ' + i.label + ' — not in this split', [
      [yes ? 'Buy Yes now at' : 'Buy No now at', fmc(i.price)],
      ['Cost with the fee', fmc(i.cost) + ' · pays ' + fmx(1 / i.cost)],
      ['The user’s chance it pays', fmp(i.p)],
      ['Breaks even if the chance is', fmp(i.cost)],
    ], 'another scenario may hold it; this one found better uses for the money');
  }

  // ------------------------------------------------------------------ picker
  const PRODUCT_CONID = {};
  async function loadPicker() {
    const sel = $('#allocMarket'); if (!sel) return;
    sel.innerHTML = '';
    sel.appendChild(h('option', { value: 'teaching', text: 'Teaching example (made-up prices)' }));
    try {
      const [sres, cres] = await Promise.all([WXD.get('summary.json'), WXD.get('catalogue/index.json', 1440)]);
      const cities = ((sres.data || {}).cities || []).filter(c => c.station);
      if (cities.length) {
        const g = h('optgroup', { label: 'Daily temperatures' });
        cities.sort((a, b) => (a.city || '').localeCompare(b.city || '')).forEach(c => {
          g.appendChild(h('option', { value: 'city:' + c.station, text: (c.city || c.station) + ' — daily high / low' }));
        });
        sel.appendChild(g);
      }
      const cats = ((cres.data || {}).categories || []).filter(c => c.slug !== 'daily-temperatures');
      for (const c of cats) {
        try {
          /* The count products are quoted through the hurricane snapshot, not
             the catalogue price lane, and only the numeric ladders belong
             here: a landfall board's strikes are places and a cumulative
             category-4 contract's are dates, and neither is a value a normal
             curve can land on. */
          if (c.slug === 'tropical-cyclones') {
            const hd = (await WXD.get('market/hurricane.json', 30)).data || {};
            // the exchange's own category carries products the registry files
            // elsewhere (the tornado count belongs to Weather), and those are
            // already offered through their own lane
            const mineHere = sym => {
              const slug = ((window.WX && WX.nav && WX.nav.product) || {})[String(sym).toUpperCase()];
              return slug === undefined || slug === 'tropical-cyclones';
            };
            const ms = (hd.markets || []).filter(m => mineHere(m.symbol)
              && (m.contracts || []).map(parseRow).filter(Boolean).length >= 2);
            if (!ms.length) continue;
            const g = h('optgroup', { label: c.l2 });
            ms.forEach(m => g.appendChild(h('option', { value: 'hur:' + m.symbol, text: m.name })));
            sel.appendChild(g);
            continue;
          }
          const doc = (await WXD.get('catalogue/' + c.slug + '.json', 1440)).data || {};
          const ps = (doc.products || []).filter(p => p.active);
          if (!ps.length) continue;
          const g = h('optgroup', { label: c.l2 });
          ps.forEach(p => { PRODUCT_CONID[p.id] = p.productConid; g.appendChild(h('option', { value: 'prod:' + p.id, text: p.name })); });
          sel.appendChild(g);
        } catch (e) { /* a category without a doc just stays off the list */ }
      }
    } catch (e) { /* offline: the teaching ladder still works */ }
    const q = WXC.param('m');
    if (q) { sel.value = q; if (sel.value !== q) sel.value = 'teaching'; }
    sel.onchange = () => pick(sel.value);
    if (sel.value && sel.value !== 'teaching') pick(sel.value); else pick('teaching');
  }

  let pickSeq = 0;
  async function pick(v) {
    const sub = $('#allocSub');
    const seq = ++pickSeq;               // a slower earlier fetch must not win
    if (v === 'teaching' || !v) {
      if (sub) sub.innerHTML = '';
      const lab = $('#allocSubLab'); if (lab) lab.style.display = 'none';
      setLadder(teachingLadder());
      return;
    }
    const [kind, id] = v.split(':');
    const failed = () => {
      // WXD.get reports failure in its result rather than throwing, and a
      // feed that could not be reached is not the same fact as a ladder with
      // nothing quoted on it
      if (sub) { sub.innerHTML = ''; sub.textContent = 'could not be loaded just now'; }
      const lab = $('#allocSubLab'); if (lab) lab.style.display = 'flex';
      setLadder({ title: 'Ladder unavailable', rows: [],
                  sub: 'That ladder could not be fetched just now. It usually comes back on the next try.' });
    };
    try {
      if (kind === 'city') {
        const res = await WXD.get('market/' + id + '.json', 10);
        if (seq !== pickSeq) return;
        if (!res.data) return failed();
        cityPicker(res.data);
      } else if (kind === 'hur') {
        const res = await WXD.get('market/hurricane.json', 30);
        if (seq !== pickSeq) return;
        if (!res.data) return failed();
        const m = ((res.data || {}).markets || []).find(x => x.symbol === id) || {};
        prodPicker(id, { rows: m.contracts || [], asof: res.data.asof }, m.productConid);
      } else {
        const res = await WXD.get('catalogue/price/' + id + '.json', 30);
        if (seq !== pickSeq) return;
        if (!res.data) return failed();
        prodPicker(id, res.data, PRODUCT_CONID[id]);
      }
    } catch (e) {
      if (seq === pickSeq) failed();
    }
  }

  function subSelect(items, onpick, name) {
    const sub = $('#allocSub'); if (!sub) return;
    const lab = $('#allocSubLab'); if (lab) lab.style.display = 'flex';
    sub.innerHTML = '';
    if (!items.length) {
      sub.textContent = 'nothing quoted right now';
      // a coherent empty state: the page describes the market it failed to
      // load rather than keeping the previous ladder's name over its chart
      setLadder({ title: name || 'No quoted ladder', rows: [],
                  sub: 'The exchange has no quoted ladder here at the moment. Pick another market, or the teaching example.' });
      return;
    }
    const sel = h('select');
    items.forEach(it => sel.appendChild(h('option', { value: it.key, text: it.text })));
    sel.onchange = () => onpick(items.find(i => i.key === sel.value));
    sub.appendChild(sel);
    onpick(items[0]);
  }

  function cityPicker(doc) {
    const items = [];
    // newest day first: after settlement the oldest listed day is over, and a
    // calculator that opens on a finished market answers nothing
    Object.keys(doc.days || {}).sort().reverse().forEach(day => {
      ['high', 'low'].forEach(side => {
        const rows = (doc.days[day] || {})[side];
        if (rows && rows.length) items.push({ key: day + ':' + side, text: day + ' · ' + side, day, side, rows });
      });
    });
    subSelect(items, it => {
      const rows = it.rows.map(parseRow).filter(Boolean);
      // the international boards are Celsius-native; the doc says which
      const celsius = doc.unit === 'C';
      const impDoc = (((doc.implied || {})[it.day] || {})[it.side] || {}).value;
      const imp = impDoc != null ? impDoc : impliedMedian(rows);
      setLadder({
        title: (doc.city || doc.station) + ' — daily ' + it.side + ', ' + it.day,
        sub: 'Live prices from the exchange, as of ' + (doc.asof || 'recently') + '. Click a row to open that contract on IBKR.',
        unit: celsius ? '°C' : '°F', grain: 'integer', rows, synthetic: false,
        productConid: ((doc.symbols || {})[it.side] || {}).productConid,
        defaults: { value: imp, band: celsius ? 3 : 4 },
      });
    }, (doc.city || doc.station) + ' — daily temperature');
  }

  function prodPicker(id, doc, productConid) {
    const rows = (doc.rows || []).filter(r => r.numeric !== false);
    const bySpec = {};
    rows.forEach(r => { (bySpec[r.spec || ''] = bySpec[r.spec || ''] || []).push(r); });
    // '2026.10' sorts before '2026.8' as a string; parse the parts instead
    const specKey = sp => String(sp).split('.').map(x => +x || 0);
    const items = Object.keys(bySpec).sort((a, b) => {
      const ka = specKey(a), kb = specKey(b);
      for (let i = 0; i < 3; i++) { const d = (ka[i] || 0) - (kb[i] || 0); if (d) return d; }
      return 0;
    }).map(spec => ({
      key: spec, text: (bySpec[spec][0].expiryLabel || spec) + ' (' + bySpec[spec].length + ' strikes)', rows: bySpec[spec],
    }));
    subSelect(items, it => {
      const parsed = it.rows.map(parseRow).filter(Boolean).sort((a, b) => a.strike - b.strike);
      const name = ($('#allocMarket').selectedOptions[0] || {}).text || id;
      // counts are integers; everything else here settles on a published figure
      // that carries its own decimals
      const isCount = /storm|hurricane|tornado|record/i.test(name);
      const imp = impliedMedian(parsed);
      const span = parsed.length ? parsed[parsed.length - 1].strike - parsed[0].strike : 4;
      setLadder({
        title: name + ' — ' + it.text.replace(/ \(\d+ strikes\)/, ''),
        sub: 'Live prices from the exchange, as of ' + (doc.asof || 'recently') + '.' + (productConid ? ' Click a row to open that contract on IBKR.' : ''),
        unit: '', grain: isCount ? 'integer' : 'continuous', rows: parsed, synthetic: false, productConid,
        defaults: { value: imp, band: Math.max(span / 4, isCount ? 2 : 0.1) },
      });
    }, ($('#allocMarket').selectedOptions[0] || {}).text || id);
  }

  function setLadder(lad) {
    S.ladder = lad;
    // the grain belongs to the ladder, so the defaults are rounded after it is
    // in place rather than by each picker
    if (lad.defaults) {
      if (lad.defaults.value != null) S.value = snapFix(snap(lad.defaults.value));
      if (lad.defaults.band != null) S.band = Math.max(grainStep(), snapFix(snap(lad.defaults.band)));
    }
    const t = $('#allocTitle'); if (t) t.textContent = lad.title;
    const st = $('#allocAsof'); if (st) st.textContent = lad.sub || '';
    syncInputs(); draw();
  }

  function syncInputs() {
    const v = $('#allocValue'), b = $('#allocBand'), bu = $('#allocBudget');
    const st = grainStep();
    if (v) { v.value = S.value; v.step = st; }
    if (b) { b.value = S.band; b.step = st; b.min = st; }
    if (bu) bu.value = S.budget;
  }

  /* The schematic: the same ladder, read three times.

     It is the calculator's own shape — strikes down a vertical axis, Yes green
     and No red — with everything the calculator adds taken away, so a reader
     meets the one idea the whole page rests on before meeting any of the
     machinery: on an overlapping ladder a single outcome settles every rung at
     once, and which side of each rung pays is decided by where the number
     lands relative to it. */
  function schematic() {
    const svg = $('#schematic'); if (!svg) return;
    svg.innerHTML = '';
    const STRIKES = [150, 140, 130, 120, 110];
    const CASES = [100, 135, 155];
    const W = 720, H = 290, T = 72, B = 252, L = 96, colW = 176, gap = 22;
    const vLo = 95, vHi = 165;
    const y = v => B - (v - vLo) / (vHi - vLo) * (B - T);

    svg.appendChild(txt('One ladder, three outcomes', { x: 4, y: 18, 'font-size': 13, 'font-weight': 700, fill: 'var(--ink)' }));
    svg.appendChild(txt('every rung settles at once; the outcome decides which side of each one pays',
                        { x: 4, y: 33, class: 'ax' }));

    STRIKES.forEach(k => {
      svg.appendChild(txt('≥' + k + ' mph', { x: L - 10, y: y(k) + 3.5, 'text-anchor': 'end', class: 'ax', 'font-size': 10 }));
      svg.appendChild(el('line', { x1: L, x2: L + 3 * colW + 2 * gap, y1: y(k), y2: y(k), class: 'grid' }));
    });

    CASES.forEach((v, ci) => {
      const x0 = L + ci * (colW + gap), x1 = x0 + colW;
      svg.appendChild(txt('the wind reaches ' + v + ' mph', { x: x0, y: T - 16, 'font-size': 10.5, 'font-weight': 700, fill: 'var(--ink)' }));
      STRIKES.forEach(k => {
        const paysYes = v >= k;
        const yy = y(k), bh = 15;
        svg.appendChild(el('rect', { x: x0, y: yy - bh / 2, width: colW, height: bh, rx: 2,
                                     fill: paysYes ? 'var(--yes)' : 'var(--no)', opacity: 0.85 }));
        svg.appendChild(txt(paysYes ? 'Yes pays' : 'No pays', { x: x0 + colW / 2, y: yy + 3.4, 'text-anchor': 'middle',
                            'font-size': 9.5, 'font-weight': 600, fill: '#FFFFFF', 'pointer-events': 'none' }));
      });
      // where the number actually landed
      const yv = y(v);
      svg.appendChild(el('line', { x1: x0 - 6, x2: x1 + 6, y1: yv, y2: yv, stroke: 'var(--ink)', 'stroke-width': 2 }));
      svg.appendChild(el('circle', { cx: x0 - 6, cy: yv, r: 3.4, fill: 'var(--ink)' }));
      svg.appendChild(txt(v + ' mph', { x: x1 + 9, y: yv + 3.4, 'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)' }));
    });
    svg.appendChild(txt('The calculator below is this figure with prices on it, and a choice about where to put the money.',
                        { x: 4, y: H - 12, class: 'ax' }));
  }

  // ------------------------------------------------------------------- init
  function init() {
    tip = WXC.tooltip();
    const v = $('#allocValue'), b = $('#allocBand'), bu = $('#allocBudget');
    if (v) {
      v.oninput = () => { const x = parseFloat(v.value); if (isFinite(x)) { S.value = x; draw(); } };
      // snapping on the way in would fight a reader typing 8 on the way to 88
      v.onchange = () => { const x = parseFloat(v.value); if (isFinite(x)) { S.value = snapFix(snap(x)); syncInputs(); draw(); } };
    }
    if (b) {
      b.oninput = () => { const x = parseFloat(b.value); if (isFinite(x) && x > 0) { S.band = x; draw(); } };
      b.onchange = () => { const x = parseFloat(b.value); if (isFinite(x) && x > 0) { S.band = Math.max(grainStep(), snapFix(snap(x))); syncInputs(); draw(); } };
    }
    if (bu) bu.oninput = () => { const x = parseFloat(bu.value); if (isFinite(x) && x >= 1 && x <= 1e6) { S.budget = x; draw(); } };
    const sh = $('#allocShape');
    if (sh) { sh.value = S.shape; sh.onchange = () => { S.shape = sh.value; draw(); }; }
    const wrap = $('#allocWrap'), ctl = $('#allocCtl');
    if (wrap && ctl && WXC.expander) ctl.appendChild(WXC.expander(wrap, 'Expand the chart'));
    schematic();
    loadPicker();
  }

  // pure pieces exposed for the verification harness, not for pages
  const _math = { erf, Phi, cdf, pdf, pWin, instruments, bins, crra, fill, payoutAt, parseRow,
                  impliedMedian, pays, ticks, LIQUID_LO, LIQUID_HI };
  return { init, draw, _math, _state: S };
})();
