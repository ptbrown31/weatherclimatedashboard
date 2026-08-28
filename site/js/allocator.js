/* The allocation calculator: one amount, spread across a strike ladder three
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
    { key: 'aggressive', name: 'Aggressive', gamma: 0.5, col: 'var(--nbm)', blurb: 'concentrated — pays big only near your value' },
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

  /* The reader's probability that a contract pays.

     `thr` is the threshold on the continuous value: for "Above 84" on a
     quantity settled in whole units it is 84.5, because the settled figure is
     rounded and then compared strictly, so a day whose true high is 84.4
     rounds to 84 and does not pay. On a quantity published with its own
     decimals the threshold is the strike itself. `dir` is +1 for above, -1
     for below. */
  const pWin = (thr, dir, mu, sigma) => {
    // the degenerate branch mirrors pays(): above is strict, its complement
    // takes the boundary. Unreachable from the page (sigma is floored), kept
    // consistent so the exported maths cannot disagree with itself.
    if (!(sigma > 0)) return (dir > 0 ? (mu > thr) : !(mu > thr)) ? 1 : 0;
    const p = 1 - Phi((thr - mu) / sigma);
    return dir > 0 ? p : 1 - p;
  };

  /* Ladder rows -> the instruments a buyer could hold.

     One per buyable side per strike. cost is in dollars per contract and
     includes the fee. */
  function instruments(ladder, feeDollars) {
    const out = [];
    (ladder.rows || []).forEach(r => {
      if (r.strike == null || !isFinite(r.strike)) return;
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
        if (c < 1) out.push({ strike: r.strike, side: 'yes', dir, thr, cost: c, price: r.ask, label: r.label, conid: r.conidYes, atLeast: r.atLeast });
      }
      // buying No now costs one dollar less the Yes bid
      if (r.bid != null && r.bid > 0 && r.bid < 1) {
        const c = (1 - r.bid) + feeDollars;
        // the contract page is addressed by the Yes conid for either side
        if (c < 1) out.push({ strike: r.strike, side: 'no', dir, thr, cost: c, price: 1 - r.bid, label: r.label, conid: r.conidYes });
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
  function bins(instr, mu, sigma) {
    const ts = Array.from(new Set(instr.map(i => i.thr))).sort((a, b) => a - b);
    const edges = [-Infinity].concat(ts, [Infinity]);
    const out = [];
    for (let b = 0; b + 1 < edges.length; b++) {
      const lo = edges[b], hi = edges[b + 1];
      const m = (sigma > 0)
        ? Phi((hi - mu) / sigma) - Phi((lo - mu) / sigma)
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
    scenKey: 'middle',
    pick: { kind: 'teaching' },
  };

  function sigma() { return Math.max(S.band / 2, 1e-6); }   // the band is two sigma

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
    const instr = instruments(S.ladder, fee);
    const mu = S.value, sg = sigma();
    instr.forEach(i => { i.p = pWin(i.thr, i.side === 'yes' ? i.dir : -i.dir, mu, sg); });
    const B = bins(instr, mu, sg);
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
    return { instr, B, scen, fee, cover };
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

    // ---- vertical scale: strikes high at the top. The domain covers the
    //      ladder and the reader's band, so neither is ever off the chart.
    const strikes = lad.rows.map(r => r.strike);
    const mu = S.value, sg = sigma();
    let vHi = Math.max(Math.max(...strikes), mu + 2.6 * sg);
    let vLo = Math.min(Math.min(...strikes), mu - 2.6 * sg);
    const pad = Math.max((vHi - vLo) * 0.06, 0.5);
    vHi += pad; vLo -= pad;

    const W = 960, T = 46, Bm = 30;
    const rowH = Math.max(17, Math.min(34, 560 / Math.max(strikes.length, 1)));
    const H = Math.max(430, Math.min(760, T + Bm + strikes.length * rowH + 60));
    const y = v => T + (vHi - v) / (vHi - vLo) * (H - T - Bm);

    // four panels on one value axis:
    // what you think | what it costs | where the money goes | what it pays
    const P1 = { x0: 48, x1: 178 }, PL = 234, P2 = { x0: 240, x1: 430 },
          P3 = { x0: 462, x1: 696 }, P4 = { x0: 726, x1: 936 };
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts', id: 'allocSvg' });

    // value axis down the left, and a rule at every tick across all panels
    const step = (vHi - vLo) > 60 ? 10 : (vHi - vLo) > 25 ? 5 : (vHi - vLo) > 8 ? 2 : (lad.grain === 'integer' ? 1 : (vHi - vLo) / 8);
    for (let v = Math.ceil(vLo / step) * step; v <= vHi; v += step) {
      svg.appendChild(txt(fmv(v, ''), { x: P1.x0 - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
      svg.appendChild(el('line', { x1: P1.x0, x2: P4.x1, y1: y(v), y2: y(v), class: 'grid' }));
    }
    if (unit) svg.appendChild(txt(unit, { x: P1.x0 - 6, y: H - Bm + 14, 'text-anchor': 'end', class: 'ax' }));

    // ---- panel 1: the reader's curve
    svg.appendChild(txt('WHAT YOU THINK', { x: P1.x0, y: T - 28, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('drag the dot or band edges', { x: P1.x0, y: T - 16, class: 'ax' }));
    {
      const xd = p => P1.x0 + p * (P1.x1 - P1.x0);        // density 0..1 of max
      const pk = 1 / (sg * Math.sqrt(2 * Math.PI));
      const pts = [];
      const n = 90;
      for (let i = 0; i <= n; i++) {
        const v = vLo + (vHi - vLo) * i / n;
        const d = Math.exp(-0.5 * Math.pow((v - mu) / sg, 2)) / (sg * Math.sqrt(2 * Math.PI));
        pts.push([xd(d / pk * 0.92), y(v)]);
      }
      // the 95 percent band, shaded between its edges
      const b0 = Math.max(vLo, mu - 2 * sg), b1 = Math.min(vHi, mu + 2 * sg);
      svg.appendChild(el('rect', { x: P1.x0, y: y(b1), width: P1.x1 - P1.x0, height: Math.max(y(b0) - y(b1), 1), fill: 'var(--accent)', opacity: 0.08 }));
      const d = 'M' + xd(0).toFixed(1) + ',' + y(vLo).toFixed(1)
        + pts.map(p => 'L' + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('')
        + 'L' + xd(0).toFixed(1) + ',' + y(vHi).toFixed(1) + 'Z';
      svg.appendChild(el('path', { d, fill: 'var(--accent)', opacity: 0.16, 'pointer-events': 'none' }));
      svg.appendChild(el('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(''), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 1.6, 'pointer-events': 'none' }));
      // centre line and drag handles
      svg.appendChild(el('line', { x1: P1.x0, x2: P1.x1, y1: y(mu), y2: y(mu), stroke: 'var(--accent)', 'stroke-width': 1.4, 'stroke-dasharray': '4 3' }));
      const mid = (P1.x0 + P1.x1) / 2;
      [[mu, 'mu'], [mu + 2 * sg, 'hi'], [mu - 2 * sg, 'lo']].forEach(([v, kind]) => {
        if (v < vLo || v > vHi) return;
        const c = el('circle', { cx: mid, cy: y(v), r: kind === 'mu' ? 7 : 5,
                                 fill: kind === 'mu' ? 'var(--accent)' : 'var(--panel)', stroke: 'var(--accent)', 'stroke-width': 2,
                                 cursor: 'ns-resize', style: 'touch-action:none' });
        c.dataset.drag = kind;
        svg.appendChild(c);
      });
      svg.appendChild(txt(fmv(mu, unit), { x: mid + 12, y: y(mu) - 8, 'font-weight': 700, 'font-size': 12, fill: 'var(--accent)' }));
      [[mu + 2 * sg, '+' + fmv(2 * sg, '')], [mu - 2 * sg, '−' + fmv(2 * sg, '')]].forEach(([v, lab]) => {
        if (v < vLo || v > vHi) return;
        svg.appendChild(txt(lab, { x: mid + 10, y: y(v) + 3.5, class: 'ax' }));
      });
      /* Drag behaviour. Every change redraws the whole SVG, which destroys
         the element a pointer capture would live on and ends the drag after
         one move. So the move and release listeners go on the window for the
         duration of the drag, and the pixel-to-value map is frozen from the
         moment the drag starts — the rebuilt chart occupies the same box, so
         the frozen map stays true unless the domain itself shifts, which
         inside the ladder it does not. */
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
          // both land on the grid the contract settles on, so dragging cannot
          // produce a value the market has no way of resolving to
          if (k === 'mu') S.value = snapFix(snap(v));
          // the handle sits at the 95 percent edge, a full band from the centre
          else S.band = Math.max(grainStep(), snapFix(snap(Math.abs(v - S.value))));
          syncInputs(); draw();
        };
        const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', up);
      });
    }

    // the strike labels, once, serving the price and allocation panels alike
    lad.rows.forEach(r => {
      svg.appendChild(txt(r.label, { x: PL, y: y(r.strike) + 3.5, 'text-anchor': 'end', class: 'ax', 'font-size': 9 }));
    });

    // held sets, used by the price panel's outlines and the allocation bars
    const held = {};
    sel.hold.forEach(x => { held[x.i.side + '@' + x.i.strike] = x; });

    // ---- panel 2: the prices as they stand, with the recommendation on them
    svg.appendChild(txt('WHAT IT COSTS NOW', { x: P2.x0, y: T - 28, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('outlined = what this split buys', { x: P2.x0, y: T - 16, class: 'ax' }));
    {
      const cw = (P2.x1 - P2.x0) / 100;                    // pixels per cent
      lad.rows.forEach(r => {
        const yy = y(r.strike);
        const bh = Math.min(rowH * 0.72, 16), by = yy - bh / 2;
        const yesPx = r.mid != null ? r.mid : (r.ask != null ? r.ask : (r.bid != null ? r.bid : null));
        const oneSided = r.mid == null && yesPx != null;
        if (yesPx == null) {
          svg.appendChild(el('rect', { x: P2.x0, y: by, width: P2.x1 - P2.x0, height: bh, rx: 2, fill: 'none', stroke: 'var(--rule)', 'stroke-dasharray': '3 3' }));
          svg.appendChild(txt('no bids', { x: (P2.x0 + P2.x1) / 2, y: yy + 3.5, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 }));
          return;
        }
        const split = P2.x0 + yesPx * 100 * cw;
        const gy = el('rect', { x: P2.x0, y: by, width: Math.max(split - P2.x0, 0.5), height: bh, fill: 'var(--yes)', opacity: oneSided ? 0.45 : 0.85 });
        const rd = el('rect', { x: split, y: by, width: Math.max(P2.x1 - split, 0.5), height: bh, fill: 'var(--no)', opacity: oneSided ? 0.45 : 0.85 });
        svg.appendChild(gy); svg.appendChild(rd);
        // the recommendation: a heavy outline on the side this scenario buys,
        // and a pill with the count and the price paid
        ['yes', 'no'].forEach(side => {
          const x = held[side + '@' + r.strike];
          if (!x) return;
          const seg = side === 'yes'
            ? { x: P2.x0, w: Math.max(split - P2.x0, 3) } : { x: split, w: Math.max(P2.x1 - split, 3) };
          svg.appendChild(el('rect', { x: seg.x + 0.5, y: by - 1.5, width: Math.max(seg.w - 1, 2), height: bh + 3, rx: 2,
                                       fill: 'none', stroke: 'var(--ink)', 'stroke-width': 2.2, 'pointer-events': 'none' }));
          const t = x.n + ' ct @ ' + fmc(x.i.price);
          const tw = t.length * 5.4 + 10;
          const cx2 = Math.min(Math.max(seg.x + seg.w / 2, P2.x0 + tw / 2), P2.x1 - tw / 2);
          svg.appendChild(el('rect', { x: cx2 - tw / 2, y: yy - 6.5, width: tw, height: 13, rx: 6.5, fill: 'var(--panel)', opacity: 0.92, 'pointer-events': 'none' }));
          svg.appendChild(txt(t, { x: cx2, y: yy + 3.2, 'text-anchor': 'middle', 'font-size': 9, 'font-weight': 700, fill: 'var(--ink)', 'pointer-events': 'none' }));
        });
        // one hover band per row, with the whole story
        const band = el('rect', { x: P2.x0, y: by - 2, width: P2.x1 - P2.x0, height: bh + 4, fill: 'transparent' });
        bind(band, () => priceTip(r, R, held));
        if (lad.productConid && (r.conidYes != null) && window.WXM && WXM.contractUrl) {
          WXM.linkTo(band, WXM.contractUrl(lad.productConid, r.conidYes), 'Open ' + r.label + ' on IBKR');
        }
        svg.appendChild(band);
      });
      // a cents scale under the split bars
      [0, 50, 100].forEach(c => {
        svg.appendChild(txt(c + '¢', { x: P2.x0 + c * cw, y: H - Bm + 14, 'text-anchor': 'middle', class: 'ax' }));
      });
    }

    // ---- panel 3: where the money goes, with the payout multiple on every bar
    svg.appendChild(txt('WHERE THE ' + fm$(S.budget).replace(/\.00$/, '') + ' GOES — ' + scSel.name.toUpperCase(), { x: P3.x0, y: T - 28, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('$ → contracts → payout multiple', { x: P3.x0, y: T - 16, class: 'ax' }));
    {
      const spine = (P3.x0 + P3.x1) / 2;
      const maxSpend = Math.max(1e-9, ...SCEN.map(s => R.scen[s.key].hold.reduce((a, x) => Math.max(a, x.spent), 0)));
      const wOf = d => Math.min((P3.x1 - spine - 4), Math.max(2.5, d / maxSpend * (P3.x1 - spine - 8)));
      svg.appendChild(el('line', { x1: spine, x2: spine, y1: T, y2: H - Bm, stroke: 'var(--rule)' }));
      lad.rows.forEach(r => {
        const yy = y(r.strike);
        ['yes', 'no'].forEach(side => {
          const x = held[side + '@' + r.strike];
          const inst = R.instr.find(i => i.strike === r.strike && i.side === side);
          const right = side === 'yes';
          if (x) {
            const w = wOf(x.spent);
            const bar = el('rect', { x: right ? spine + 1 : spine - 1 - w, y: yy - Math.min(rowH * 0.36, 9), width: w, height: Math.min(rowH * 0.72, 18), rx: 2, fill: right ? 'var(--yes)' : 'var(--no)', opacity: 0.92, cursor: x.i.conid && lad.productConid ? 'pointer' : null });
            bind(bar, () => barTip(x, R, unit));
            if (x.i.conid && lad.productConid && window.WXM && WXM.contractUrl) {
              WXM.linkTo(bar, WXM.contractUrl(lad.productConid, x.i.conid), 'Open ' + x.i.label + ' on IBKR');
            }
            svg.appendChild(bar);
            /* Each label carries the whole arithmetic of its line: dollars in,
               contracts bought, and the payout multiple those dollars come
               back at if the line pays. The multiple IS the price seen from
               the other end — 7 cents a contract is 14 times your money — and
               printing it on every bar is the point of the page.

               The text sits in the opposite half of the spine: the bar's own
               half runs out of room exactly when the bar is worth reading,
               and the other side's half at this strike is empty in every
               uncrossed book, because buying both sides of one strike costs
               more than the dollar it returns. */
            const mult = x.n / Math.max(x.spent, 1e-9);
            const label = fm$(x.spent).replace(/\.00$/, '') + ' → ' + x.n + ' ct → ' + fmx(mult);
            const both = held['yes@' + r.strike] && held['no@' + r.strike];
            const lx = right ? (both ? spine + 1 + w + 5 : spine - 6) : (both ? spine - 1 - w - 5 : spine + 6);
            const anch = right ? (both ? 'start' : 'end') : (both ? 'end' : 'start');
            svg.appendChild(txt(label, { x: lx, y: yy + 3.5, 'text-anchor': anch, 'font-size': 9.5, 'font-weight': 600, fill: right ? 'var(--yes)' : 'var(--no)', 'pointer-events': 'none' }));
          } else if (inst) {
            // buyable but not bought under this scenario: a tick, so the
            // reader can still ask it questions
            const t2 = el('rect', { x: right ? spine + 1 : spine - 4, y: yy - 3, width: 3, height: 6, fill: 'var(--rule)' });
            bind(t2, () => instTip(inst, R, unit));
            svg.appendChild(t2);
          }
        });
      });
    }

    // ---- panel 4: what the whole set pays, as a function of the value
    svg.appendChild(txt('WHAT THE SET PAYS', { x: P4.x0, y: T - 28, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('gross $, by where it lands', { x: P4.x0, y: T - 16, class: 'ax' }));
    {
      const maxPay = Math.max(S.budget * 1.15, ...SCEN.map(s => {
        const sc = R.scen[s.key];
        return R.B.reduce((a, b) => Math.max(a, payoutAt(sc.hold, b.v)), 0);
      }));
      const xp = d => P4.x0 + Math.min(d / maxPay, 1) * (P4.x1 - P4.x0);
      // round-dollar ticks: the largest 1/2/5-series step that gives a few
      const dstep = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000]
        .filter(st => maxPay / st >= 2 && maxPay / st <= 6).pop() || Math.ceil(maxPay / 4);
      for (let d = dstep; d <= maxPay; d += dstep) {
        svg.appendChild(el('line', { x1: xp(d), x2: xp(d), y1: T, y2: H - Bm, class: 'grid' }));
        svg.appendChild(txt('$' + d, { x: xp(d), y: H - Bm + 14, 'text-anchor': 'middle', class: 'ax' }));
      }
      /* One line for the money in: every scenario spends the whole amount (to
         within coins), so right of this line a scenario is ahead and left of
         it behind, and the three curves need no cost line each. */
      svg.appendChild(el('line', { x1: xp(S.budget), x2: xp(S.budget), y1: T, y2: H - Bm, stroke: 'var(--ink)', 'stroke-dasharray': '4 3', opacity: 0.55 }));
      const bx = xp(S.budget), bRight = bx > (P4.x0 + P4.x1) / 2;
      svg.appendChild(txt('the ' + fm$(S.budget).replace(/\.00$/, '') + ' in', { x: bRight ? bx - 4 : bx + 4, y: T + 10, 'font-size': 9.5, fill: 'var(--muted)', 'text-anchor': bRight ? 'end' : 'start' }));
      // thresholds where any payoff can change, inside the drawn domain
      const ts = Array.from(new Set(R.instr.map(i => i.thr))).sort((a, b) => a - b).filter(t => t > vLo && t < vHi);
      const edges = [vLo].concat(ts, [vHi]);
      SCEN.forEach(s => {
        const sc = R.scen[s.key];
        if (!sc.hold.length) return;
        const on = s.key === scSel.key;
        let d = '';
        for (let b = 0; b + 1 < edges.length; b++) {
          const v0 = edges[b], v1 = edges[b + 1];
          const pay = payoutAt(sc.hold, (v0 + v1) / 2);
          d += (b ? 'L' : 'M') + xp(pay).toFixed(1) + ',' + y(v0).toFixed(1) + 'L' + xp(pay).toFixed(1) + ',' + y(v1).toFixed(1);
        }
        svg.appendChild(el('path', { d, fill: 'none', stroke: s.col, 'stroke-width': on ? 2.4 : 1.3, opacity: on ? 1 : 0.55, 'pointer-events': 'none' }));
      });
      // hover: read all three at a value
      const band = el('rect', { x: P4.x0, y: T, width: P4.x1 - P4.x0, height: H - T - Bm, fill: 'transparent' });
      let mark = null;
      band.addEventListener('mousemove', ev => {
        const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
        const q = pt.matrixTransform(svg.getScreenCTM().inverse());
        const v = vHi - (q.y - T) / (H - T - Bm) * (vHi - vLo);
        if (!mark) { mark = el('line', { x1: P4.x0, x2: P4.x1, stroke: 'var(--ink)', 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.5, 'pointer-events': 'none' }); svg.appendChild(mark); }
        mark.setAttribute('y1', q.y); mark.setAttribute('y2', q.y);
        const gr = lad.grain === 'integer' ? Math.round(v) : Math.round(v * 10) / 10;
        const rows = SCEN.map(s => {
          const sc = R.scen[s.key];
          const pay = payoutAt(sc.hold, v);
          return ['<span class="sw" style="background:' + s.col + '"></span>' + s.name,
                  fm$(pay) + ' (' + fmS(pay - sc.spent) + ' net)'];
        });
        tip.show(ev, tip.rows('If the value lands at ' + fmv(gr, unit), rows,
          'gross payout, and net of the money in'));
      });
      band.addEventListener('mouseleave', () => { tip.hide(); if (mark) { mark.remove(); mark = null; } });
      svg.insertBefore(band, svg.firstChild);
    }

    host.appendChild(svg);

    // ---- scenario summary, under the chart
    const chips = h('div', { class: 'allocChips' });
    SCEN.forEach(s => {
      const sc = R.scen[s.key], st = sc.stats;
      const on = s.key === scSel.key;
      const c = h('button', { class: 'allocChip' + (on ? ' on' : ''), type: 'button' }, [
        h('b', { text: s.name, style: 'color:' + s.col }),
        h('span', { text: s.blurb }),
        h('span', { text: 'your expected value ' + fmS(st.evNet) }),
        // signed, not assumed: a set can be ahead even in its worst interval
        h('span', { text: 'worst ' + fmS(st.worstNet) + ' · best ' + fmS(st.bestNet) }),
      ]);
      c.onclick = () => { S.scenKey = s.key; draw(); };
      chips.appendChild(c);
    });
    host.appendChild(chips);
    const note = $('#allocNote');
    if (note) {
      const nct = sel.hold.reduce((a, x) => a + x.n, 0);
      let msg;
      if (!R.instr.length) {
        msg = 'Nothing on this ladder can be bought right now — no strike has a resting bid on the other side to buy against.';
      } else {
        msg = 'The ' + scSel.name.toLowerCase() + ' split buys ' + nct + (nct === 1 ? ' contract' : ' contracts') + ' across '
          + sel.hold.length + (sel.hold.length === 1 ? ' line' : ' lines') + ' for ' + fm$(sel.spent)
          + (sel.cash > 0.005 ? ' — the ' + fm$(sel.cash).replace('$0.', '') + '¢ left cannot buy a whole contract' : '')
          + '. The expected values shown are under your curve, not anyone else’s.';
        if (R.cover < 0.995) {
          msg += ' Your curve puts ' + fmp(1 - R.cover) + ' of its chance where nothing buyable pays — no split avoids losing there.';
        }
      }
      note.textContent = (lad.synthetic ? lad.sub + ' ' : '') + msg;
    }
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
    if (iy) rows.push(['Your chance Yes pays', fmp(iy.p)]);
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
      ['Your chance it pays', fmp(i.p)],
      ['Breaks even if the chance is', fmp(i.cost)],
    ], 'the multiple is the price read from the other end');
  }
  function instTip(i, R, unit) {
    const yes = i.side === 'yes';
    return tip.rows((yes ? 'Yes' : 'No') + ' · ' + i.label + ' — not in this split', [
      [yes ? 'Buy Yes now at' : 'Buy No now at', fmc(i.price)],
      ['Cost with the fee', fmc(i.cost) + ' · pays ' + fmx(1 / i.cost)],
      ['Your chance it pays', fmp(i.p)],
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
    const wrap = $('#allocWrap'), ctl = $('#allocCtl');
    if (wrap && ctl && WXC.expander) ctl.appendChild(WXC.expander(wrap, 'Expand the chart'));
    loadPicker();
  }

  // pure pieces exposed for the verification harness, not for pages
  const _math = { erf, Phi, pWin, instruments, bins, crra, fill, payoutAt, parseRow, impliedMedian, pays };
  return { init, draw, _math, _state: S };
})();
