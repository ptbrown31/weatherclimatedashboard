/* The allocation calculator: what a view spread across a strike ladder costs
   and what it pays.

   A reader types two numbers — the value they expect, and how far off they
   could be — and the page turns that into a normal curve, prices every strike's
   Yes and No against it, and sizes a set of purchases three ways. Everything on
   the page is arithmetic on the reader's own numbers and the exchange's public
   prices; the site contributes no forecast of its own, and the default ladder
   is made up.

   The sizing rule is the Kelly criterion (Kelly 1956): choose the allocation
   that maximises the expected logarithm of wealth under the reader's curve.
   The three scenarios are the full Kelly allocation and half and a quarter of
   it — fractional Kelly, the standard way to trade growth for drawdown
   (MacLean, Thorp and Ziemba). The payoff of a set of these contracts is
   constant between neighbouring strike thresholds, so the expected log is
   computed exactly on those intervals rather than on a sampled grid.

   Prices follow the exchange's structure: there are no sellers, only bids to
   buy Yes or No that sum to a dollar. Buying Yes now costs one dollar less the
   No bid; buying No now costs one dollar less the Yes bid. The per-side fee is
   added to every cost here, so a "cost" on this page is what a buyer actually
   pays. A side with no resting bid opposite it cannot be bought now and is
   left out. */
window.WXAlloc = (() => {
  const { el, txt, h, $ } = WXC;
  const SCEN = [
    { key: 'conservative', name: 'Conservative', frac: 0.25, col: 'var(--cool)' },
    { key: 'middle', name: 'Middle', frac: 0.5, col: 'var(--lamp)' },
    { key: 'aggressive', name: 'Aggressive', frac: 1, col: 'var(--nbm)' },
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
     includes the fee; win is the net gain per contract when it pays (1 - cost)
     and -cost when it does not. */
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
     outcome space, and each carries its normal probability mass. */
  function bins(instr, mu, sigma) {
    const ts = Array.from(new Set(instr.map(i => i.thr))).sort((a, b) => a - b);
    const edges = [-Infinity].concat(ts, [Infinity]);
    const out = [];
    for (let b = 0; b + 1 < edges.length; b++) {
      const lo = edges[b], hi = edges[b + 1];
      const m = (sigma > 0)
        ? Phi((hi - mu) / sigma) - Phi((lo - mu) / sigma)
        : ((mu > lo && mu <= hi) ? 1 : 0);
      if (m <= 0) continue;
      // a representative value strictly inside the interval decides payoffs
      const v = !isFinite(lo) ? hi - 1 : (!isFinite(hi) ? lo + 1 : (lo + hi) / 2);
      out.push({ lo, hi, mass: m, v });
    }
    // renormalise the tail mass lost to the erf approximation
    const tot = out.reduce((a, b2) => a + b2.mass, 0);
    if (tot > 0) out.forEach(b2 => { b2.mass /= tot; });
    return out;
  }

  // does instrument i pay when the value is v
  const pays = (i, v) => {
    const above = v > i.thr;
    return i.side === 'yes' ? (i.dir > 0 ? above : !above) : (i.dir > 0 ? !above : above);
  };

  /* Full-Kelly weights: maximise sum_b mass_b * log(1 + sum_j f_j g_jb) with
     f >= 0 and sum f <= CAP, by projected gradient ascent with backtracking.
     g is the net return per dollar staked: (1-cost)/cost when the contract
     pays, -1 when it does not. The problem is concave and small (tens of
     instruments, tens of intervals), so this converges in a few hundred
     steps. Deterministic: no randomness anywhere. */
  const CAP = 0.995;                     // full ruin in some interval means log(0)
  function kelly(instr, B) {
    const n = instr.length;
    if (!n || !B.length) return new Array(n).fill(0);
    const g = instr.map(i => B.map(b => (pays(i, b.v) ? (1 - i.cost) / i.cost : -1)));
    const W = f => B.map((b, bi) => 1 + f.reduce((a, fj, j) => a + fj * g[j][bi], 0));
    const G = f => {
      const w = W(f);
      let s = 0;
      for (let bi = 0; bi < B.length; bi++) { if (w[bi] <= 1e-9) return -Infinity; s += B[bi].mass * Math.log(w[bi]); }
      return s;
    };
    const grad = f => {
      const w = W(f);
      return instr.map((_, j) => B.reduce((a, b, bi) => a + b.mass * g[j][bi] / Math.max(w[bi], 1e-9), 0));
    };
    // Euclidean projection onto {f >= 0, sum f <= CAP}
    function project(f) {
      f = f.map(v => Math.max(0, v));
      let s = f.reduce((a, b) => a + b, 0);
      if (s <= CAP) return f;
      // shift by the theta that lands the clipped sum on the cap
      const sorted = f.slice().sort((a, b) => b - a);
      let acc = 0, theta = 0;
      for (let k = 0; k < sorted.length; k++) {
        acc += sorted[k];
        const t = (acc - CAP) / (k + 1);
        if (k + 1 === sorted.length || sorted[k + 1] <= t) { theta = t; break; }
      }
      return f.map(v => Math.max(0, v - theta));
    }
    let f = new Array(n).fill(0), best = 0, eta = 0.25;
    for (let it = 0; it < 600; it++) {
      const d = grad(f);
      let cand = project(f.map((v, j) => v + eta * d[j]));
      let gc = G(cand);
      let tries = 0;
      while (gc < best - 1e-12 && tries < 30) { eta *= 0.5; cand = project(f.map((v, j) => v + eta * d[j])); gc = G(cand); tries++; }
      if (gc <= best + 1e-12 && tries >= 30) break;
      if (gc > best) { f = cand; best = gc; }
      if (it % 20 === 19) eta = Math.min(eta * 2, 0.5);   // recover step size
    }
    /* Polish by exact coordinate ascent.

       Adjacent strikes make near-equivalent instruments, so the optimum sits
       on a nearly flat ridge; gradient steps crawl along it and can stop with
       the right objective but the wrong split between neighbours — a bar on
       the page the true optimum does not buy. One coordinate at a time the
       problem is one-dimensional and concave, so golden-section solves it
       exactly, and cycling to a fixed point lands on the optimum itself.
       Each 1-D evaluation is O(intervals) via the cached wealth vector, so
       the whole polish costs less than the gradient phase did. */
    const w = W(f);
    const g1 = (j, x) => {                 // G with f_j moved to x, using w
      const df = x - f[j];
      let sacc = 0;
      for (let bi = 0; bi < B.length; bi++) {
        const wb = w[bi] + df * g[j][bi];
        if (wb <= 1e-9) return -Infinity;
        sacc += B[bi].mass * Math.log(wb);
      }
      return sacc;
    };
    const PHI2 = (Math.sqrt(5) - 1) / 2;
    for (let sweep = 0; sweep < 400; sweep++) {
      let gained = 0;
      for (let j = 0; j < n; j++) {
        const others = f.reduce((a, v, k) => a + (k === j ? 0 : v), 0);
        let lo = 0, hi = Math.max(0, CAP - others);
        for (let it2 = 0; it2 < 60; it2++) {
          const a = hi - PHI2 * (hi - lo), b = lo + PHI2 * (hi - lo);
          if (g1(j, a) < g1(j, b)) lo = a; else hi = b;
        }
        const x = (lo + hi) / 2, before = g1(j, f[j]), after = g1(j, x);
        if (after > before + 1e-14) {
          const df = x - f[j];
          for (let bi = 0; bi < B.length; bi++) w[bi] += df * g[j][bi];
          f[j] = x; gained += after - before;
        }
      }
      if (gained < 1e-12) break;
    }
    // the ridge polish can leave a dust of tiny weights; below a tenth of a
    // percent of the bankroll they can never round to a contract at any
    // sensible budget and only clutter the picture
    return f.map(v => (v < 1e-4 ? 0 : v));
  }

  /* One scenario: fractional Kelly at `frac`, turned into whole contracts.

     Contracts are floored, never rounded up, so a scenario cannot spend more
     than its fractions imply; what the flooring leaves over stays in cash and
     is shown, because paying for fewer, likelier outcomes IS the conservative
     lesson. */
  function scenario(instr, fstar, frac, budget) {
    const hold = instr.map((i, j) => {
      const dollars = budget * fstar[j] * frac;
      const n = Math.floor(dollars / i.cost + 1e-9);
      return { i, n, spent: n * i.cost };
    }).filter(x => x.n > 0);
    const spent = hold.reduce((a, x) => a + x.spent, 0);
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
     a case with something to allocate. Every price is invented and says so. */
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

  function compute() {
    const fee = (window.WXM && WXM.feeCents ? WXM.feeCents() : 0.5) / 100;
    const instr = instruments(S.ladder, fee);
    const mu = S.value, sg = sigma();
    instr.forEach(i => { i.p = pWin(i.thr, i.side === 'yes' ? i.dir : -i.dir, mu, sg); });
    const B = bins(instr, mu, sg);
    const fstar = kelly(instr, B);
    const scen = {};
    SCEN.forEach(sc => {
      const s = scenario(instr, fstar, sc.frac, S.budget);
      s.stats = scenarioStats(s, B);
      scen[sc.key] = s;
    });
    return { instr, B, fstar, scen, fee };
  }

  // ------------------------------------------------------------------- svg
  const fm$ = v => '$' + (Math.round(v * 100) / 100).toFixed(2);
  const fmS = v => (v < 0 ? '\u2212' : '+') + fm$(Math.abs(v)).slice(1);   // signed, for net outcomes
  const fmc = v => {
    const c = Math.round(v * 1000) / 10;                  // costs carry the half-cent fee
    return (c % 1 ? c.toFixed(1) : String(c)) + '¢';
  };
  const fmp = v => (v >= 0.995 ? '>99' : v <= 0.005 ? '<1' : Math.round(v * 100)) + '%';
  const fmv = (v, unit) => {
    const s = Math.abs(v) >= 100 ? v.toFixed(0) : (Math.round(v * 10) / 10).toString();
    return s + (unit ? ' ' + unit : '');
  };

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

    const W = 960, T = 44, Bm = 30;
    const rowH = Math.max(17, Math.min(34, 560 / Math.max(strikes.length, 1)));
    const H = Math.max(430, Math.min(760, T + Bm + strikes.length * rowH + 60));
    const y = v => T + (vHi - v) / (vHi - vLo) * (H - T - Bm);

    // panel x-ranges: belief | allocation | payout
    const P1 = { x0: 64, x1: 268 }, P2 = { x0: 330, x1: 640 }, P3 = { x0: 700, x1: 936 };
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts', id: 'allocSvg' });

    // value axis down the left, and a rule at every strike across all panels
    const step = (vHi - vLo) > 60 ? 10 : (vHi - vLo) > 25 ? 5 : (vHi - vLo) > 8 ? 2 : (lad.grain === 'integer' ? 1 : (vHi - vLo) / 8);
    for (let v = Math.ceil(vLo / step) * step; v <= vHi; v += step) {
      svg.appendChild(txt(fmv(v, ''), { x: P1.x0 - 8, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
      svg.appendChild(el('line', { x1: P1.x0, x2: P3.x1, y1: y(v), y2: y(v), class: 'grid' }));
    }
    svg.appendChild(txt(unit ? 'value (' + unit + ')' : 'value', { x: 14, y: T - 26, class: 'ax' }));

    // ---- panel 1: the reader's curve
    svg.appendChild(txt('WHAT YOU THINK', { x: P1.x0, y: T - 26, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('drag the dot or the band edges', { x: P1.x0, y: T - 14, class: 'ax' }));
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
      svg.appendChild(txt(fmv(mu, unit), { x: mid + 14, y: y(mu) - 8, 'font-weight': 700, 'font-size': 12, fill: 'var(--accent)' }));
      [[mu + 2 * sg, '+' + fmv(2 * sg, '')], [mu - 2 * sg, '−' + fmv(2 * sg, '')]].forEach(([v, lab]) => {
        if (v < vLo || v > vHi) return;
        svg.appendChild(txt(lab, { x: mid + 12, y: y(v) + 3.5, class: 'ax' }));
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
          if (k === 'mu') S.value = Math.round(v * 10) / 10;
          // the handle sits at the 95 percent edge, a full band from the centre
          else S.band = Math.max(0.2, Math.round(Math.abs(v - S.value) * 10) / 10);
          syncInputs(); draw();
        };
        const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', up);
      });
    }

    // ---- panel 2: the allocation, one bar per buyable side with money in it
    svg.appendChild(txt('WHERE THE ' + fm$(S.budget) + ' GOES — ' + scSel.name.toUpperCase(), { x: P2.x0, y: T - 26, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('Yes to the right, No to the left · label is dollars → contracts', { x: P2.x0, y: T - 14, class: 'ax' }));
    {
      const spine = (P2.x0 + P2.x1) / 2 + 20;
      const maxSpend = Math.max(1e-9, ...SCEN.map(s => R.scen[s.key].hold.reduce((a, x) => Math.max(a, x.spent), 0)));
      const wOf = d => Math.min((P2.x1 - spine - 4), Math.max(2.5, d / maxSpend * (P2.x1 - spine - 8)));
      svg.appendChild(el('line', { x1: spine, x2: spine, y1: T, y2: H - Bm, stroke: 'var(--rule)' }));
      const held = {};
      sel.hold.forEach(x => { held[x.i.side + '@' + x.i.strike] = x; });
      // every strike appears, holding or not, so the ladder's structure shows
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
            /* The dollar label sits on the empty half of the spine. A No bar
               grows left, into the same space as the ladder's own labels, so
               its text goes to the right of the spine instead — the Yes half
               at that strike is necessarily empty, because buying both sides
               of one strike costs more than the dollar it returns and the
               sizing never does it. */
            const label = fm$(x.spent).replace('.00', '') + ' → ' + x.n + ' ct';
            // a crossed book can put both sides of one strike in the set, in
            // which case the No text falls back to the end of its own bar
            const both = held['yes@' + r.strike] && held['no@' + r.strike];
            const lx = right ? spine + 1 + w + 5 : (both ? spine - 1 - w - 5 : spine + 6);
            svg.appendChild(txt(label, { x: lx, y: yy + 3.5, 'text-anchor': (!right && both) ? 'end' : 'start', 'font-size': 9.5, 'font-weight': 600, fill: right ? 'var(--yes)' : 'var(--no)', 'pointer-events': 'none' }));
          } else if (inst) {
            // buyable but not bought at these numbers: a tick, so the reader can
            // still ask it questions
            const t2 = el('rect', { x: right ? spine + 1 : spine - 4, y: yy - 3, width: 3, height: 6, fill: 'var(--rule)' });
            bind(t2, () => instTip(inst, R, unit));
            svg.appendChild(t2);
          }
        });
        svg.appendChild(txt(lad.rows.find(q => q.strike === r.strike).label, { x: P2.x0, y: yy + 3.5, class: 'ax', 'font-size': 9 }));
      });
    }

    // ---- panel 3: what the whole set pays, as a function of the value
    svg.appendChild(txt('WHAT THE SET PAYS', { x: P3.x0, y: T - 26, class: 'ax', 'font-weight': 700 }));
    svg.appendChild(txt('gross dollars, by where the value lands', { x: P3.x0, y: T - 14, class: 'ax' }));
    {
      const maxPay = Math.max(S.budget * 0.4, ...SCEN.map(s => {
        const sc = R.scen[s.key];
        return R.B.reduce((a, b) => Math.max(a, payoutAt(sc.hold, b.v)), 0);
      }));
      const xp = d => P3.x0 + Math.min(d / maxPay, 1) * (P3.x1 - P3.x0);
      // round-dollar ticks: the largest 1/2/5-series step that gives a few
      const dstep = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000]
        .filter(st => maxPay / st >= 2 && maxPay / st <= 6).pop() || Math.ceil(maxPay / 4);
      for (let d = dstep; d <= maxPay; d += dstep) {
        svg.appendChild(el('line', { x1: xp(d), x2: xp(d), y1: T, y2: H - Bm, class: 'grid' }));
        svg.appendChild(txt('$' + d, { x: xp(d), y: H - Bm + 14, 'text-anchor': 'middle', class: 'ax' }));
      }
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
        // the scenario's outlay, on the same dollar axis: right of this line the set is ahead
        svg.appendChild(el('line', { x1: xp(sc.spent), x2: xp(sc.spent), y1: T, y2: H - Bm, stroke: s.col, 'stroke-dasharray': '3 3', opacity: on ? 0.8 : 0.3 }));
        if (on) {
          const cx = xp(sc.spent), rightHalf = cx > (P3.x0 + P3.x1) / 2;
          svg.appendChild(txt('cost ' + fm$(sc.spent), { x: rightHalf ? cx - 4 : cx + 4, y: T + 10, 'font-size': 9.5, fill: s.col, 'text-anchor': rightHalf ? 'end' : 'start' }));
        }
      });
      // hover: read all three at a value
      const band = el('rect', { x: P3.x0, y: T, width: P3.x1 - P3.x0, height: H - T - Bm, fill: 'transparent' });
      let mark = null;
      band.addEventListener('mousemove', ev => {
        const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
        const q = pt.matrixTransform(svg.getScreenCTM().inverse());
        const v = vHi - (q.y - T) / (H - T - Bm) * (vHi - vLo);
        if (!mark) { mark = el('line', { x1: P3.x0, x2: P3.x1, stroke: 'var(--ink)', 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.5, 'pointer-events': 'none' }); svg.appendChild(mark); }
        mark.setAttribute('y1', q.y); mark.setAttribute('y2', q.y);
        const gr = lad.grain === 'integer' ? Math.round(v) : Math.round(v * 10) / 10;
        const rows = SCEN.map(s => {
          const sc = R.scen[s.key];
          const pay = payoutAt(sc.hold, v);
          return ['<span class="sw" style="background:' + s.col + '"></span>' + s.name,
                  fm$(pay) + ' (' + (pay - sc.spent >= 0 ? '+' : '−') + fm$(Math.abs(pay - sc.spent)).slice(1) + ' net)'];
        });
        tip.show(ev, tip.rows('If the value lands at ' + fmv(gr, unit), rows,
          'gross payout, and net of what each scenario spent'));
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
        h('span', { text: 'spends ' + fm$(sc.spent) + ' · keeps ' + fm$(sc.cash) }),
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
      const nb = R.instr.filter(i => i.p > i.cost).length;
      const anyHold = SCEN.some(s2 => R.scen[s2.key].hold.length);
      const nct = sel.hold.reduce((a, x) => a + x.n, 0);
      let msg;
      if (sel.hold.length) {
        msg = 'At your numbers, ' + nb + ' of the ' + R.instr.length + ' buyable sides are priced below your probability that they pay. '
          + 'The ' + scSel.name.toLowerCase() + ' scenario buys ' + nct + (nct === 1 ? ' contract' : ' contracts') + ' across '
          + sel.hold.length + ' of them for ' + fm$(sel.spent) + '; the expected values shown are under your curve, not anyone else’s.';
      } else if (anyHold) {
        msg = 'The ' + scSel.name.toLowerCase() + ' fractions round below one whole contract at this budget. '
          + 'A bolder scenario still buys; so would more dollars.';
      } else if (!R.instr.length) {
        msg = 'Nothing on this ladder can be bought right now \u2014 no strike has a resting bid on the other side to buy against.';
      } else {
        msg = 'At your numbers nothing on this ladder is priced below your probability that it pays, so no scenario buys anything. Move the value or widen the band to see an allocation.';
      }
      note.textContent = (lad.synthetic ? lad.sub + ' ' : '') + msg;
    }
  }

  // hover content for a held bar and an unheld tick
  function bind(node, make) {
    node.addEventListener('mousemove', ev => tip.show(ev, make()));
    node.addEventListener('mouseleave', () => tip.hide());
  }
  function barTip(x, R, unit) {
    const i = x.i;
    const yes = i.side === 'yes';
    return tip.rows((yes ? 'Buy Yes' : 'Buy No') + ' · ' + i.label, [
      [yes ? 'Buy Yes now at' : 'Buy No now at', fmc(i.price)],
      ['Cost with the fee', fmc(i.cost)],
      ['Contracts', String(x.n)],
      ['Dollars in', fm$(x.spent)],
      ['Pays if it hits', fm$(x.n) + ' (' + (x.n / Math.max(x.spent, 1e-9)).toFixed(1) + '×)'],
      ['Your chance it pays', fmp(i.p)],
      ['Breaks even if the chance is', fmp(i.cost)],
    ], 'your chance above the breakeven chance is the whole reason this bar exists');
  }
  function instTip(i, R, unit) {
    const yes = i.side === 'yes';
    return tip.rows((yes ? 'Yes' : 'No') + ' · ' + i.label + ' — not bought', [
      [yes ? 'Buy Yes now at' : 'Buy No now at', fmc(i.price)],
      ['Cost with the fee', fmc(i.cost)],
      ['Your chance it pays', fmp(i.p)],
      ['Breaks even if the chance is', fmp(i.cost)],
    ], i.p > i.cost ? 'a small edge the sizing rounded below one contract' : 'at your numbers this side is not cheap');
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
        sub: 'Live prices from the exchange, as of ' + (doc.asof || 'recently') + '. Click a bar to open that contract on IBKR.',
        unit: celsius ? '°C' : '°F', grain: 'integer', rows, synthetic: false,
        productConid: ((doc.symbols || {})[it.side] || {}).productConid,
        defaults: { value: imp != null ? Math.round(imp * 10) / 10 : null, band: celsius ? 2.5 : 4 },
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
        sub: 'Live prices from the exchange, as of ' + (doc.asof || 'recently') + '.' + (productConid ? ' Click a bar to open that contract on IBKR.' : ''),
        unit: '', grain: isCount ? 'integer' : 'continuous', rows: parsed, synthetic: false, productConid,
        defaults: { value: imp != null ? Math.round(imp * 100) / 100 : null, band: Math.max(Math.round(span / 4 * 100) / 100, isCount ? 2 : 0.1) },
      });
    }, ($('#allocMarket').selectedOptions[0] || {}).text || id);
  }

  function setLadder(lad) {
    S.ladder = lad;
    if (lad.defaults) {
      if (lad.defaults.value != null) S.value = lad.defaults.value;
      if (lad.defaults.band != null) S.band = lad.defaults.band;
    }
    const t = $('#allocTitle'); if (t) t.textContent = lad.title;
    const st = $('#allocAsof'); if (st) st.textContent = lad.sub || '';
    syncInputs(); draw();
  }

  function syncInputs() {
    const v = $('#allocValue'), b = $('#allocBand'), bu = $('#allocBudget');
    if (v) v.value = S.value;
    if (b) b.value = S.band;
    if (bu) bu.value = S.budget;
  }

  // ------------------------------------------------------------------- init
  function init() {
    tip = WXC.tooltip();
    const v = $('#allocValue'), b = $('#allocBand'), bu = $('#allocBudget');
    if (v) v.oninput = () => { const x = parseFloat(v.value); if (isFinite(x)) { S.value = x; draw(); } };
    if (b) b.oninput = () => { const x = parseFloat(b.value); if (isFinite(x) && x > 0) { S.band = x; draw(); } };
    if (bu) bu.oninput = () => { const x = parseFloat(bu.value); if (isFinite(x) && x >= 1 && x <= 1e6) { S.budget = x; draw(); } };
    const wrap = $('#allocWrap'), ctl = $('#allocCtl');
    if (wrap && ctl && WXC.expander) ctl.appendChild(WXC.expander(wrap, 'Expand the chart'));
    loadPicker();
  }

  // pure pieces exposed for the verification harness, not for pages
  const _math = { erf, Phi, pWin, instruments, bins, kelly, scenario, payoutAt, parseRow, impliedMedian, pays };
  return { init, draw, _math, _state: S };
})();
