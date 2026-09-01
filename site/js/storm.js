/* A live storm's wind contracts, delivery by delivery.

   One section per active storm. For each reference location the vendor has
   signalled on, a small chart of its probability ladder as it moved from one
   vendor delivery to the next, with the exchange's price for the matching
   contract marked on the same axis — a probability and a price in cents live
   on the same 0 to 100 scale, so the two can be read against each other
   directly. Below that, the pool contracts, whose strikes are place names.

   Everything here is forward-only. The horizontal axis is the delivery index,
   not the clock, because nobody knows how many deliveries a storm will produce
   or when it will dissipate; a new delivery appends a column and never rescales
   what is already drawn. The rightmost column is reserved for settlement from
   the first frame, and stays empty and labelled until the vendor's final file
   arrives. Nothing is coloured, ranked or marked by an outcome that has not
   happened: ticks and crosses appear only once there is a settled gust to
   compare against, and a location enters the page when the exchange lists its
   contracts, which is public, rather than on any internal listing rule.

   Data in: reask.json (the index: which storms, what state) and
   storm/{name}_{year}.json (the delivery ledger), plus the exchange's own
   markets through WXM. The vendor's probabilities are shown as published,
   under its mark. */
window.WXStorm = (() => {
  const { el, txt, h, $ } = WXC;
  const cents = v => (v == null ? null : Math.round(v * 100));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const MAX_CARDS = 12;
  let tip = null, ledgers = {}, RK = null, MK = null, open = null;

  /* When a storm stops updating. The vendor never announces an end; the
     final settlement file is definitive, and short of one, a storm whose
     newest delivery is older than a day and a half has outlived the
     six-hourly cadence by enough to be treated as over. A storm with no
     stamps at all is treated as live, which is the honest default for a
     roster entry whose ledger has not been read. Shared with hurricane.js so
     the map dots, the ladder tables and the delivery charts all agree on
     which storms are still running. */
  const STALE_MS = 36 * 3600000;
  /* A depression is named by its number, and on upgrade the same system gets
     a real name: Five became Edouard, and the vendor's files simply started
     arriving under the new name. The two are one storm, so the number-word
     roster entry is superseded, not a second hazard. The join is through the
     NHC roster's ATCF id, whose two digits are the storm number the word
     spells; hurricane.js hands the roster over as soon as it has one. */
  const NUMWORD = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
    'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen',
    'Nineteen', 'Twenty', 'Twenty-One', 'Twenty-Two', 'Twenty-Three', 'Twenty-Four', 'Twenty-Five',
    'Twenty-Six', 'Twenty-Seven', 'Twenty-Eight', 'Twenty-Nine', 'Thirty'];
  const numOf = name => { const i = NUMWORD.indexOf(String(name || '').trim()); return i > 0 ? i : null; };
  let ROSTER = [];
  function setRoster(list) { ROSTER = list || []; }
  function supersededBy(s) {
    const n = numOf(s && s.name);
    if (n == null) return null;
    const hit = ROSTER.find(r => {
      const m = /^al(\d{2})/i.exec(r.id || '');
      return m && +m[1] === n && numOf(r.name) == null;
    });
    return hit ? hit.name : null;
  }
  // what a folded storm's summary says about why it is folded
  const doneLabel = s => (s.final ? 'settled'
    : supersededBy(s) ? 'now named ' + supersededBy(s)
    : 'no longer updating');
  function stampOf(s) {
    const lc = (s && s.livecyc) || {};
    const t = Date.parse(lc.lastModified || lc.forecastTime || '');
    return isFinite(t) ? t : null;
  }
  function dormant(s) {
    if (s && s.final) return true;
    if (supersededBy(s)) return true;
    const t = stampOf(s);
    return t != null && (Date.now() - t) > STALE_MS;
  }
  // newest delivery first; a storm without a stamp sorts to the front
  const byRecency = (a, b) => {
    const ta = stampOf(a), tb = stampOf(b);
    return (tb == null ? Infinity : tb) - (ta == null ? Infinity : ta);
  };

  // a colour per threshold, cold to hot across the ladder
  const rung = (i, n) => 'hsl(' + Math.round(210 - 210 * (i / Math.max(1, n - 1))) + ' 70% 45%)';
  const stormCode = n => String(n || '').replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase();

  /* The stated calculation's wording, printed beside every display of the
     number, because a probability the site computes exists only together
     with the formula that computed it. */
  const METHOD = 'The calculation, stated: each location’s lifetime peak-gust distribution is its latest '
    + 'LiveCyc exceedance ladder, uniform within the published threshold bins, and where an interim '
    + 'settlement ladder exists its figure floors the lifetime one, because the contract asks about the '
    + 'storm’s whole lifetime while a LiveCyc ladder looks forward from its cycle. Locations are treated '
    + 'as independent and P(highest) is the probability of being the maximum, evaluated on a one-mph grid '
    + 'and normalised to sum to one. Independence is the one assumption: locations share the storm, and '
    + 'correlation concentrates the outcome on the leader, so the leader here is if anything understated. '
    + 'The vendor’s ladders are as published; the exchange’s prices are its own.';

  // the current stated-calculation figure per location NAME for one storm
  function calcByName(stormName) {
    const s = ((RK && RK.storms) || []).find(x => x.name === stormName);
    const lc = s && s.livecyc;
    if (!lc || !lc.pwin) return null;
    const out = {};
    Object.keys(lc.pwin).forEach(sid => {
      const nm = (lc.sites && lc.sites[sid] && lc.sites[sid].name) || sid;
      out[nm] = lc.pwin[sid];
    });
    return out;
  }

  // the exchange's market for one location of one storm, if it is listed
  function lMarket(storm, sid) {
    if (!MK) return null;
    const want = 'L' + stormCode(storm) + String(sid).toUpperCase();
    return MK.markets.find(m => m.symbol === want) || null;
  }
  function poolMarkets(storm) {
    if (!MK) return [];
    const pre = 'LHL' + stormCode(storm);
    return MK.markets.filter(m => m.symbol.indexOf(pre) === 0);
  }
  const priceAt = (m, threshold) => {
    if (!m) return null;
    const c = (m.contracts || []).find(x => Number(x.strike) === Number(threshold));
    return c && c.mid != null ? cents(c.mid) : null;
  };

  // ---- deliveries that never arrived
  //
  // The horizontal axis counts deliveries, so a cycle the vendor skipped would
  // close up and read as though no more time passed there than anywhere else.
  // The cadence is taken as the median spacing between consecutive cycles, which
  // is what the vendor actually ran rather than an assumption about what it
  // should run; a spacing longer than one and a half of those is a gap, and the
  // number of missing cycles is that spacing over the cadence. Only cycles are
  // compared: an interim settlement arrives on the exchange's schedule, not the
  // vendor's, so the step onto it is not a gap. With fewer than three cycles
  // there is no cadence to speak of and nothing is marked.
  function gaps(cyc) {
    // the forecast time if it parses, and otherwise the delivery id, which is
    // always the cycle's own YYYYMMDDHH and is the thing the ledger is keyed on
    const when = s => {
      const p = Date.parse(s.at || '');
      if (isFinite(p)) return p;
      const m = /^(\d{4})(\d{2})(\d{2})(\d{2})$/.exec(String(s.id || ''));
      return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4]) : NaN;
    };
    const t = cyc.map(s => (s.kind === 'livecyc' ? when(s) : NaN));
    const d = [];
    for (let k = 1; k < cyc.length; k++) if (isFinite(t[k]) && isFinite(t[k - 1])) d.push([k, t[k] - t[k - 1]]);
    if (d.length < 2) return [];
    const sorted = d.map(x => x[1]).slice().sort((a, b) => a - b);
    const cad = sorted[Math.floor(sorted.length / 2)];
    if (!(cad > 0)) return [];
    return d.filter(x => x[1] > cad * 1.5).map(x => ({
      after: x[0] - 1, hours: Math.round(x[1] / 36e5),
      missing: Math.max(1, Math.round(x[1] / cad) - 1), cadence: Math.round(cad / 36e5),
    }));
  }
  // the axis-break glyph: two short slashes, the convention for a stretch left out
  function slashes(g, cx, cy, h) {
    [-2.5, 2.5].forEach(o => g.appendChild(el('line', {
      x1: cx + o - 2.5, x2: cx + o + 2.5, y1: cy + h / 2, y2: cy - h / 2,
      stroke: 'var(--muted)', 'stroke-width': 1.4, 'pointer-events': 'none' })));
  }

  // ---- one location's card: the ladder through the deliveries.
  // Returns the node and a setCursor, so scrubbing moves a line and a few marks
  // rather than rebuilding every card on every step.
  function card(doc, sid, storm, gp) {
    const meta = doc.sites[sid] || {};
    const thr = doc.thresholds || [];
    const cyc = (doc.steps || []).filter(s => s.kind !== 'final');
    const fin = doc.final || null;
    const mk = lMarket(storm.name, sid);
    const rungs = [];
    const W = 470, H = 190, L = 34, R = 384, T = 16, B = 150, SETTLE = 424;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'scard' });
    const n = Math.max(cyc.length, 2);
    const x = i => L + (i / (n - 1)) * (R - L);
    const y = p => B - (p / 100) * (B - T);

    [0, 25, 50, 75, 100].forEach(p => {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(p), y2: y(p), class: 'grid' }));
      svg.appendChild(txt(p, { x: L - 5, y: y(p) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    });
    // the settlement column, reserved from the first delivery so the axis never moves
    svg.appendChild(el('line', { x1: SETTLE - 22, x2: SETTLE - 22, y1: T, y2: B, stroke: 'var(--rule)', 'stroke-dasharray': '3 3' }));
    svg.appendChild(txt(fin ? 'settled' : 'settles', { x: SETTLE - 18, y: T + 8, class: 'ax' }));

    // one hover band per delivery, in before the rungs so a price mark on top of
    // a band still takes the pointer
    cyc.forEach((s, k) => {
      const band = el('rect', { x: x(k) - (R - L) / (2 * (n - 1)), y: T, width: Math.max((R - L) / (n - 1), 6), height: B - T, fill: 'transparent' });
      bind(band, () => {
        const arr = (s.sites && s.sites[sid]) || [];
        const rows = thr.map((t, i) => arr[i] > 0 ? ['above ' + t + ' mph', arr[i] + '%'] : null).filter(Boolean);
        return tip.rows(esc(meta.name || sid) + ' — ' + label(s) + (k === cyc.length - 1 ? ' (latest)' : ''),
          rows.length ? rows : [['Ladder', 'nothing above zero at this delivery']],
          'delivery ' + (k + 1) + ' of ' + cyc.length + ' so far · ' + esc((RK && RK.attribution) || 'Powered by Reask'));
      });
      svg.appendChild(band);
    });

    // a missed cycle, marked between the two columns that sit either side of it.
    // The strip is narrower than the space between them so each delivery keeps
    // most of its own hover band, and it goes in before the rungs so a price mark
    // still takes the pointer ahead of it.
    const wcol = (R - L) / (n - 1);
    (gp || []).forEach(g => {
      if (g.after + 1 >= cyc.length) return;
      const mid = (x(g.after) + x(g.after + 1)) / 2;
      const strip = el('rect', { class: 'sgap', x: mid - wcol * 0.2, y: T, width: Math.max(wcol * 0.4, 5), height: B - T,
                                 fill: 'var(--rule)', opacity: .28, 'pointer-events': 'all' });
      bind(strip, () => tip.rows('A delivery is missing here',
        [['Between', label(cyc[g.after]) + ' and ' + label(cyc[g.after + 1])],
         ['Elapsed', g.hours + ' hours'],
         ['Cycles missing', String(g.missing)],
         ['Usual cadence', g.cadence + ' hours']],
        'the axis counts deliveries, so these two columns are further apart in time than the rest'));
      svg.appendChild(strip);
      const gl = el('g'); slashes(gl, mid, B, 9); svg.appendChild(gl);
    });

    thr.forEach((t, i) => {
      const col = rung(i, thr.length);
      // a rung starts at the delivery it first carried a probability on and runs forward
      const pts = [];
      cyc.forEach((s, k) => {
        const arr = s.sites && s.sites[sid];
        const p = arr && arr.length > i ? arr[i] : null;
        if (p == null) return;
        if (!pts.length && p <= 0) return;
        pts.push([k, p]);
      });
      if (!pts.length) return;
      const d = pts.map((p, k) => (k ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('');
      // the whole rung stays drawn, faintly, and the part up to the cursor is drawn
      // over it at full strength: what was known by that delivery reads first, and
      // what came afterwards is still visible rather than hidden
      const faint = el('path', { d: d, fill: 'none', stroke: col, 'stroke-width': 1.6, opacity: .22, 'pointer-events': 'none' });
      const solid = el('path', { d: d, fill: 'none', stroke: col, 'stroke-width': 1.6, 'pointer-events': 'none' });
      svg.appendChild(faint); svg.appendChild(solid);
      // one point per delivery: the vendor issues these at set times and nothing
      // is measured between them, so each reading is marked rather than implied
      if (pts.length > 1) {
        const gap = Math.abs(x(pts[pts.length - 1][0]) - x(pts[0][0])) / (pts.length - 1);
        if (gap >= 5) pts.forEach(v => svg.appendChild(el('circle',
          { class: 'rdot', cx: x(v[0]).toFixed(1), cy: y(v[1]).toFixed(1), r: Math.min(2.6, gap / 4),
            fill: col, 'pointer-events': 'none' })));
      }
      const last = pts[pts.length - 1];
      rungs.push({ t, col, pts, last, solid });
      // ticks and crosses only once a settled gust exists to compare against
      if (fin && fin[sid] != null) {
        const hit = fin[sid] >= t;
        svg.appendChild(txt(hit ? '✓' : '✕', { x: SETTLE - 8, y: y(last[1]) + 3.5, 'font-size': 10, fill: hit ? 'var(--yes)' : 'var(--muted)' }));
      }
    });

    // the cursor: a line at the delivery being read, a dot on each rung that had a
    // value there, and the exchange's price as it stood at that same delivery
    const cline = el('line', { class: 'scur', y1: T, y2: B, stroke: 'var(--ink)', 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: .6, 'pointer-events': 'none' });
    svg.appendChild(cline);
    const marks = el('g');
    svg.appendChild(marks);
    function setCursor(ti) {
      const k = Math.max(0, Math.min(ti, cyc.length - 1));
      cline.setAttribute('x1', x(k)); cline.setAttribute('x2', x(k));
      marks.innerHTML = '';
      const step = cyc[k] || {};
      const arr = (step.sites && step.sites[sid]) || [];
      const pr = (step.prices && step.prices[sid]) || {};
      rungs.forEach(r => {
        const upto = r.pts.filter(p => p[0] <= k);
        r.solid.setAttribute('d', upto.length > 1
          ? upto.map((p, j) => (j ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('')
          : '');
        const i = thr.indexOf(r.t);
        const v = arr.length > i ? arr[i] : null;
        if (v != null && v > 0) marks.appendChild(el('circle', { cx: x(k), cy: y(v), r: 2.6, fill: r.col, 'pointer-events': 'none' }));
        // at the newest delivery the live quote is the current price and is fresher
        // than the one recorded when the delivery landed; further back, only the
        // recorded price belongs beside that delivery's ladder. A delivery that
        // arrived before prices were being recorded simply has no square.
        const live = k === cyc.length - 1 ? priceAt(mk, r.t) : null;
        const price = live != null ? live : pr[String(r.t)];
        if (price == null) return;
        // hollow, to read as the market's price rather than the vendor's line, but
        // painted transparent so the pointer lands on it instead of the band beneath
        const sq = el('rect', { x: x(k) + 5, y: y(price) - 3.5, width: 7, height: 7, fill: 'transparent', stroke: r.col, 'stroke-width': 1.6, 'pointer-events': 'all' });
        bind(sq, () => tip.rows(esc(meta.name || sid) + ' — above ' + r.t + ' mph',
          [[live != null ? 'The exchange, now' : 'The exchange, at that delivery', Math.round(price) + '¢'],
           ['The vendor', (v == null ? '—' : v + '%')],
           ['Difference', v == null ? null : (price - v > 0 ? '+' : '') + Math.round((price - v) * 10) / 10 + ' points'],
           ['Delivery', label(step)]],
          (live != null ? 'the price as it stands now, against the latest ladder'
                        : 'both as they stood at that delivery') + '; a price in cents and a probability in percent share the same scale · '
          + esc((RK && RK.attribution) || 'Powered by Reask')));
        marks.appendChild(sq);
      });
    }
    if (fin && fin[sid] != null) svg.appendChild(txt(fin[sid] + ' mph', { x: SETTLE - 8, y: B - 2, 'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)' }));

    const wrap = h('div', { class: 'scardwrap' }, [
      h('div', { class: 'lt', text: (meta.name || sid) + ' (' + sid + ')' }),
      h('div', { class: 'cap', style: 'margin:0 0 4px', text: mk ? 'contracts listed · ' + mk.symbol : 'no contracts listed for this location yet' }),
    ]);
    wrap.appendChild(svg);
    return { node: wrap, setCursor };
  }

  const label = s => (s.kind === 'interim' ? 'interim settlement' : s.kind === 'final' ? 'final settlement' : String(s.id || '').replace(/^(\d{4})(\d{2})(\d{2})(\d{2})$/, '$2/$3 $4Z'));

  function bind(node, html) {
    node.addEventListener('mousemove', e => { e.stopPropagation(); tip.show(e, html()); });
    node.addEventListener('mouseleave', () => tip.hide());
    node.addEventListener('click', e => { e.stopPropagation(); tip.pin(e, html()); });
    node.setAttribute('data-tip-pin', '1');
    return node;
  }

  // ---- the timeline across deliveries
  //
  // One tick per delivery, in the order they arrived. The strip is sized to the
  // deliveries that exist now; when the next one lands the page redraws with one
  // more tick and the cursor stays where the reader left it, or follows the end
  // if that is where it already was. There is no scale to fix in advance and
  // nothing beyond the last tick, because the storm's length is not known.
  function timeline(cyc, gp, onMove) {
    const W = 960, H = 46, L = 16, R = 936;
    const n = Math.max(cyc.length, 2);
    const x = i => L + (i / (n - 1)) * (R - L);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'stimeline', tabindex: '0',
                            role: 'slider', 'aria-label': 'vendor delivery' });
    svg.appendChild(el('line', { x1: L, x2: R, y1: 20, y2: 20, stroke: 'var(--line)', 'stroke-width': 2 }));
    cyc.forEach((s, i) => {
      const interim = s.kind === 'interim';
      svg.appendChild(interim
        ? el('rect', { x: x(i) - 3.5, y: 16.5, width: 7, height: 7, fill: 'var(--accent)' })
        : el('circle', { cx: x(i), cy: 20, r: 3, fill: 'var(--muted)' }));
    });
    (gp || []).forEach(g => {
      if (g.after + 1 >= cyc.length) return;
      const gl = el('g'); slashes(gl, (x(g.after) + x(g.after + 1)) / 2, 20, 11); svg.appendChild(gl);
    });
    const cur = el('path', { d: '', fill: 'var(--ink)' });
    const lab = txt('', { x: L, y: 40, class: 'ax', 'font-weight': 700 });
    svg.appendChild(cur); svg.appendChild(lab);
    let ti = cyc.length - 1;
    function place() {
      const px = x(ti);
      cur.setAttribute('d', 'M' + px + ' 10L' + (px + 5) + ' 2L' + (px - 5) + ' 2Z');
      const s = cyc[ti] || {};
      lab.textContent = label(s) + '  ·  delivery ' + (ti + 1) + ' of ' + cyc.length + (ti === cyc.length - 1 ? ' (latest)' : '');
      // centred under the cursor, then measured and pulled back inside the frame so
      // the reading never runs off the end at the newest delivery
      const w = lab.getComputedTextLength ? lab.getComputedTextLength() : 0;
      lab.setAttribute('x', Math.min(Math.max(px - w / 2, 4), Math.max(4, W - 4 - w)));
      onMove(ti);
    }
    const at = ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      return Math.max(0, Math.min(cyc.length - 1, Math.round(((q.x - L) / (R - L)) * (n - 1))));
    };
    let dragging = false;
    svg.addEventListener('pointerdown', e => { dragging = true; svg.setPointerCapture(e.pointerId); ti = at(e); place(); });
    svg.addEventListener('pointermove', e => { if (dragging) { ti = at(e); place(); } });
    svg.addEventListener('pointerup', e => { dragging = false; try { svg.releasePointerCapture(e.pointerId); } catch (x) { /* already released */ } });
    svg.addEventListener('keydown', e => {
      const d = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : e.key === 'Home' ? -cyc.length : e.key === 'End' ? cyc.length : 0;
      if (!d) return;
      e.preventDefault();
      ti = Math.max(0, Math.min(cyc.length - 1, ti + d));
      place();
    });
    return { svg, place, step: d => { ti = Math.max(0, Math.min(cyc.length - 1, ti + d)); place(); },
             last: () => { ti = cyc.length - 1; place(); }, get: () => ti };
  }

  // ---- the pool contracts: candidate locations as the strikes
  function pools(storm) {
    const out = [];
    poolMarkets(storm.name).forEach(m => {
      const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: (m.name || m.symbol) + ' (' + m.symbol + ')' }),
        h('div', { class: 'cap', style: 'margin:0 0 6px', text: 'Which of these locations records the highest wind. The candidates are the strikes and the pool is fixed when it is opened.' })]);
      const calc = calcByName(storm.name);
      (m.contracts || []).slice().sort((a, b) => (b.mid == null ? -1 : b.mid) - (a.mid == null ? -1 : a.mid)).forEach(c => {
        const v = c.mid == null ? null : cents(c.mid);
        const cw = calc ? calc[c.label || String(c.strike)] : null;
        const bar = h('span', { class: 'lb' }, [h('i', { style: 'width:' + (v == null ? 0 : v) + '%' })]);
        if (cw != null) bar.appendChild(h('u', { class: 'calcmk', style: 'left:' + cw + '%',
          title: 'stated calculation ' + cw + '%' }));
        const row = h('div', { class: 'lrow' }, [
          h('span', { class: 'lk', text: c.label || String(c.strike) }),
          bar,
          h('span', { class: 'lv', text: (v == null ? 'no bids' : v + '¢')
            + (cw != null ? ' · calc ' + Math.round(cw) + '%' : '') })]);
        const url = WXM.contractUrl(m.productConid, c.conidYes || c.conid);
        bind(row, () => tip.rows((m.name || m.symbol) + ' — ' + esc(c.label || c.strike),
          [['Yes price', v == null ? 'no bids' : v + '¢'],
           ['Yes bid', c.bid == null ? '—' : cents(c.bid) + '¢'],
           ['No bid', c.ask == null ? '—' : (100 - cents(c.ask)) + '¢'],
           ['Buy Yes now at', c.ask == null ? null : cents(c.ask) + '¢' + (WXM.payoutText(cents(c.ask)) ? ' · pays ' + WXM.payoutText(cents(c.ask)) : '')],
           ],
          'settles on the vendor’s final peak gusts' + (url ? ' · click the price to open the contract' : '')));
        if (url) WXM.linkTo(row.querySelector('.lv'), url, 'Open ' + (c.label || c.strike) + ' on IBKR');
        div.appendChild(row);
      });
      if (!(m.contracts || []).length) div.appendChild(h('div', { class: 'cap', text: 'No candidates listed yet.' }));
      out.push(div);
    });
    return out;
  }

  /* The highest-wind pool's prices through time: one line per candidate,
     drawn from the hourly series the quote job keeps. A Yes price in cents
     is the market's own probability that the location takes the pool, so
     this is the market's P(win) through time and nothing else; the desk
     figures under the same title elsewhere are not this site's to publish. */
  async function poolSeries(m, ledger) {
    let doc = null;
    try { doc = (await WXD.get('lhl/' + m.symbol + '.json', 10)).data; } catch (e) { doc = null; }
    const pts = (doc && doc.points) || [];
    if (pts.length < 2) return null;
    // the stated calculation at each delivery, by location name, for the
    // dashed lines; each step's figure is from its own ladder alone
    const calcSteps = ((ledger && ledger.steps) || [])
      .filter(st => st.kind === 'livecyc' && st.pwin && st.at)
      .map(st => {
        const by = {};
        Object.keys(st.pwin).forEach(sid => {
          const nm = (st.siteMeta && st.siteMeta[sid] && st.siteMeta[sid].name) || sid;
          by[nm] = st.pwin[sid];
        });
        return { t: Date.parse(st.at), by };
      }).filter(q => isFinite(q.t));
    const W = 960, Hh = 260, L = 46, R = 830, T = 26, B = 232;
    const t0 = Date.parse(pts[0].t), t1 = Date.parse(pts[pts.length - 1].t);
    if (!(t1 > t0)) return null;
    const X = t => L + (t - t0) / (t1 - t0) * (R - L);
    const Y = v => B - (v / 100) * (B - T);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + Hh, class: 'ts lhlserie' });
    [0, 25, 50, 75, 100].forEach(v => {
      svg.appendChild(el('line', { x1: L, y1: Y(v), x2: R, y2: Y(v), stroke: 'var(--rule)', 'stroke-width': 0.5 }));
      svg.appendChild(txt(v + '¢', { x: L - 5, y: Y(v) + 3, 'text-anchor': 'end', class: 'axl', 'font-size': 9.5 }));
    });
    // the candidates, ranked by where they stand now, the top eight drawn
    const latest = pts[pts.length - 1].p || {};
    const names = Object.keys(latest).sort((a, b) => (latest[b] || 0) - (latest[a] || 0)).slice(0, 8);
    names.forEach((nm, i) => {
      const col = rung(names.length - 1 - i, Math.max(2, names.length));
      const line = pts.map(q => ({ t: Date.parse(q.t), v: (q.p || {})[nm] })).filter(q => q.v != null);
      if (line.length < 2) return;
      svg.appendChild(el('path', {
        d: line.map((q, j) => (j ? 'L' : 'M') + X(q.t).toFixed(1) + ' ' + Y(q.v).toFixed(1)).join(''),
        fill: 'none', stroke: col, 'stroke-width': i ? 1.6 : 2.4, opacity: i ? 0.85 : 1 }));
      const last = line[line.length - 1];
      svg.appendChild(el('circle', { cx: X(last.t), cy: Y(last.v), r: 2.4, fill: col }));
      svg.appendChild(txt(nm + ' ' + Math.round(last.v) + '¢', { x: R + 6, y: Y(last.v) + 3,
        'font-size': 9.5, fill: col, class: 'lbl' }));
      // the stated calculation, dashed in the same colour, clipped to the
      // chart's own window so the two read on one axis
      const cl = calcSteps.map(q => ({ t: q.t, v: q.by[nm] }))
        .filter(q => q.v != null && q.t >= t0 && q.t <= t1);
      if (cl.length > 1) {
        svg.appendChild(el('path', {
          d: cl.map((q, j) => (j ? 'L' : 'M') + X(q.t).toFixed(1) + ' ' + Y(q.v).toFixed(1)).join(''),
          fill: 'none', stroke: col, 'stroke-width': 1.1, 'stroke-dasharray': '4 3', opacity: 0.75,
          class: 'calcline' }));
      }
    });
    // day marks along the base, in UTC, the clock the storm runs on
    const DAY = 86400000;
    for (let t = Math.ceil(t0 / DAY) * DAY; t <= t1; t += DAY) {
      svg.appendChild(el('line', { x1: X(t), y1: T, x2: X(t), y2: B, stroke: 'var(--rule)', 'stroke-width': 0.5 }));
      svg.appendChild(txt(new Date(t).toISOString().slice(5, 10), { x: X(t), y: B + 14, 'text-anchor': 'middle', class: 'axl', 'font-size': 9.5 }));
    }
    // the crosshair reads every candidate at one instant
    const rule = el('line', { y1: T, y2: B, stroke: 'var(--muted)', 'stroke-width': 0.8, visibility: 'hidden' });
    svg.appendChild(rule);
    svg.addEventListener('mousemove', ev => {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const x = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
      if (x < L || x > R) { rule.setAttribute('visibility', 'hidden'); tip.hide(); return; }
      rule.setAttribute('x1', x); rule.setAttribute('x2', x); rule.setAttribute('visibility', 'visible');
      const at = t0 + (x - L) / (R - L) * (t1 - t0);
      let best = pts[0];
      pts.forEach(q => { if (Math.abs(Date.parse(q.t) - at) < Math.abs(Date.parse(best.t) - at)) best = q; });
      const rows = names.map(nm => [esc(nm), (best.p || {})[nm] != null ? best.p[nm] + '¢' : '—']);
      tip.show(ev, tip.rows('Pool Yes prices · ' + best.t.slice(0, 16).replace('T', ' ') + 'Z', rows,
                            'the exchange’s published prices, sampled hourly'));
    });
    svg.addEventListener('mouseleave', () => { rule.setAttribute('visibility', 'hidden'); tip.hide(); });
    const wrap = h('div');
    wrap.appendChild(h('div', { class: 'lt', style: 'margin:12px 0 2px',
      text: 'Highest-wind location (LHL) — P(win) through time' }));
    wrap.appendChild(svg);
    wrap.appendChild(h('p', { class: 'cap', style: 'margin:2px 0 0',
      text: 'Solid lines are the exchange’s Yes prices, sampled hourly from the quote record; a price in cents is the market’s probability that the location records the highest gust over the storm’s whole lifetime. Dashed lines are the stated calculation from each LiveCyc delivery, drawn at the delivery’s cycle time. ' + METHOD }));
    return wrap;
  }

  /* Before the exchange lists the pool there is no price to draw, but the
     stated calculation exists from the first delivery, so it stands alone
     with its formula until the prices join it at listing. */
  function appendStatedLadder(storm, host) {
    if (poolMarkets(storm.name).length) return;
    const calc = calcByName(storm.name);
    if (!calc) return;
    const names = Object.keys(calc).sort((a, b) => calc[b] - calc[a]);
    const div = h('div', { class: 'ladder' }, [
      h('div', { class: 'lt', text: 'Highest-wind location (LHL) — the stated calculation, awaiting listing' })]);
    names.forEach(nm => {
      div.appendChild(h('div', { class: 'lrow' }, [
        h('span', { class: 'lk', text: nm }),
        h('span', { class: 'lb' }, [h('i', { style: 'width:' + calc[nm] + '%' })]),
        h('span', { class: 'lv', text: calc[nm] + '%' })]));
    });
    const g = h('div', { class: 'ladders' }); g.appendChild(div);
    host.appendChild(g);
    host.appendChild(h('p', { class: 'cap', style: 'margin:2px 0 0',
      text: 'The exchange has not listed this pool yet; its prices join this display at listing. ' + METHOD }));
  }

  // ---- one storm
  async function drawStorm(storm, host) {
    const key = storm.name + '_' + storm.year;
    if (!(key in ledgers)) {
      const r = await WXD.get('storm/' + key + '.json', 10);
      ledgers[key] = r.data || null;
    }
    const doc = ledgers[key];
    host.innerHTML = '';
    const cyc = doc ? (doc.steps || []).filter(s => s.kind !== 'final') : [];
    const state = [];
    state.push(cyc.length + ' vendor deliver' + (cyc.length === 1 ? 'y' : 'ies') + ' so far');
    if (doc && (doc.steps || []).some(s => s.kind === 'interim')) state.push('interim settlement received');
    else state.push('interim settlement pending');
    if (doc && doc.final) state.push('final settlement received; the contracts have resolved');
    else state.push('final settlement pending, and its timing is not known in advance');
    const gp = gaps(cyc);
    if (gp.length) {
      const miss = gp.reduce((a, g) => a + g.missing, 0);
      state.push(miss + ' cycle' + (miss === 1 ? '' : 's') + ' the vendor did not deliver, marked on the charts');
    }
    /* Whether the exchange has listed this storm's wind contracts yet. The
       vendor's probabilities usually run ahead of the listing, so a panel
       with ladders and no prices is a storm the exchange has not opened,
       not a storm without a market coming; saying so stops the absence
       reading as nonexistence. */
    if (MK && !poolMarkets(storm.name).length
        && !(MK.markets || []).some(m => m.symbol.indexOf('L' + stormCode(storm.name)) === 0)) {
      state.push('no wind contracts listed on the exchange yet; the price squares, the pool ladder and its price series appear at listing');
    }
    host.appendChild(h('p', { class: 'cap', text: state.join(' · ') }));
    if (!doc || !cyc.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No probability ladder has been published for this storm yet.' }));
      // the stated calculation needs only the index's current ladder, so it
      // does not wait for the delivery ledger
      appendStatedLadder(storm, host);
      return;
    }
    // the locations worth showing: the strongest so far, which can only look backwards
    const peak = {};
    Object.keys(doc.sites || {}).forEach(sid => {
      peak[sid] = Math.max(...cyc.map(s => Math.max(...((s.sites && s.sites[sid]) || [0]))), 0);
    });
    const order = Object.keys(peak).filter(sid => peak[sid] > 0).sort((a, b) => peak[b] - peak[a]);
    const grid = h('div', { class: 'scards' });
    const cards = order.slice(0, MAX_CARDS).map(sid => card(doc, sid, storm, gp));
    cards.forEach(c => grid.appendChild(c.node));
    const tl = timeline(cyc, gp, ti => cards.forEach(c => c.setCursor(ti)));
    const bar = h('div', { class: 'bar sbar' });
    const prev = h('button', { text: '◀', title: 'the delivery before this one' });
    const next = h('button', { text: '▶', title: 'the delivery after this one' });
    const now = h('button', { text: 'Latest', title: 'jump to the most recent delivery' });
    prev.onclick = () => tl.step(-1); next.onclick = () => tl.step(1); now.onclick = () => tl.last();
    [prev, next, now].forEach(b => bar.appendChild(b));
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: 'drag the strip, use the arrows, or press ← and → when it has focus' }));
    host.appendChild(bar);
    host.appendChild(tl.svg);
    host.appendChild(grid);
    tl.place();
    if (order.length > MAX_CARDS) host.appendChild(h('p', { class: 'cap', text: order.length - MAX_CARDS + ' further locations have signalled and are not drawn; the strongest ' + MAX_CARDS + ' are shown.' }));
    const p = pools(storm);
    if (p.length) { const g = h('div', { class: 'ladders' }); p.forEach(x => g.appendChild(x)); host.appendChild(g); }
    let seriesShown = false;
    for (const m of poolMarkets(storm.name)) {
      const ser = await poolSeries(m, doc);
      if (ser) { host.appendChild(ser); seriesShown = true; }
    }
    /* The calculation never appears without its formula. The series caption
       carries it once prices accumulate; until then, a listed pool showing
       calc figures on its rows gets the formula right here. */
    if (!seriesShown && poolMarkets(storm.name).length && calcByName(storm.name)) {
      host.appendChild(h('p', { class: 'cap', style: 'margin:2px 0 0',
        text: 'The pool is listed and its price history begins with its first bids; the calc figure on each row is the stated calculation. ' + METHOD }));
    }
    appendStatedLadder(storm, host);
    host.appendChild(h('p', { class: 'cap attrib', text: ((RK && RK.attribution) || 'Powered by Reask') + '. Probabilities are the vendor’s, shown as published; the squares are the exchange’s own prices. The horizontal axis counts vendor deliveries, not time.' }));
  }

  async function draw(rk, mk) {
    RK = rk; MK = mk;
    const host = $('#liveStorms'); if (!host) return;
    host.innerHTML = '';
    const storms = ((rk && rk.storms) || []).filter(s => s && s.name).sort(byRecency);
    if (!rk || !rk.enabled) {
      host.appendChild(h('p', { class: 'cap', text: 'The live-storm wind lane is not enabled on this site' + (rk && rk.reason ? ' (' + esc(rk.reason) + ')' : '') + '. When it is on and a storm is active, each reference location the vendor signals on appears here, delivery by delivery, beside the exchange’s price for the same contract.' }));
      return;
    }
    if (!storms.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No storm with published probabilities at the moment. A storm appears here on its first vendor delivery.' }));
      return;
    }
    /* Storms still delivering come first, newest delivery first, each with its
       own tab. A storm that has stopped updating does not share that footing:
       its record folds shut below and is drawn only when opened, so the page
       leads with what is running rather than with what ran. */
    const live = storms.filter(s => !dormant(s));
    const done = storms.filter(dormant);
    if (live.length) {
      const bar = h('div', { class: 'bar' });
      const panel = h('div');
      if (!open || !live.some(s => s.name + '_' + s.year === open)) open = live[0].name + '_' + live[0].year;
      live.forEach(s => {
        const k = s.name + '_' + s.year;
        const b = h('button', { class: k === open ? 'on' : '', text: s.name });
        b.onclick = () => { open = k; bar.querySelectorAll('button').forEach(x => x.classList.remove('on')); b.classList.add('on'); drawStorm(s, panel); };
        bar.appendChild(b);
      });
      if (live.length > 1) host.appendChild(bar);
      host.appendChild(panel);
      await drawStorm(live.find(s => s.name + '_' + s.year === open) || live[0], panel);
    } else {
      host.appendChild(h('p', { class: 'cap', text: 'No storm is currently delivering. The storms below have stopped updating; open one to see its record.' }));
    }
    done.forEach(s => {
      const t = stampOf(s);
      const det = h('details', { class: 'stormdone' });
      det.appendChild(h('summary', {}, [
        h('b', { text: s.name + ' ' + s.year }),
        h('span', { text: doneLabel(s)
          + (t ? ' · last delivery ' + new Date(t).toISOString().slice(0, 10) : '') + ' · click to view' }),
      ]));
      const body = h('div');
      det.appendChild(body);
      let drawn = false;
      det.addEventListener('toggle', () => {
        if (det.open && !drawn) { drawn = true; drawStorm(s, body); }
      });
      host.appendChild(det);
    });
  }

  function init(t) { tip = t; }
  // ---- one location's series, for the map to open when a dot is clicked
  //
  // The map holds the geography and this module holds the deliveries, so rather
  // than draw the ladder twice the map asks for the same card the storm section
  // builds. Returns null when no loaded storm has signalled on that location,
  // which is the ordinary state outside a live storm.
  function siteCard(sid) {
    const keys = Object.keys(ledgers);
    for (let i = 0; i < keys.length; i++) {
      const doc = ledgers[keys[i]];
      if (!doc || !doc.sites || !doc.sites[sid]) continue;
      const cyc = (doc.steps || []).filter(x => x.kind !== 'final');
      if (!cyc.length) continue;
      const seen = cyc.some(x => ((x.sites || {})[sid] || []).some(v => v > 0));
      if (!seen) continue;
      const storm = { name: doc.name, year: doc.year };
      const c = card(doc, sid, storm, gaps(cyc));
      c.setCursor(cyc.length - 1);
      return { node: c.node, setCursor: c.setCursor, storm: doc.name, year: doc.year,
               deliveries: cyc.length, market: lMarket(doc.name, sid),
               url: (m => WXM.contractUrl(m && m.productConid, firstYes(m)))(lMarket(doc.name, sid)),
               attribution: (RK && RK.attribution) || 'Powered by Reask' };
    }
    return null;
  }
  // the lowest rung of a location's gust ladder: the contract a reader lands on
  // when they follow the location to the exchange
  function firstYes(m) {
    if (!m) return null;
    const cs = (m.contracts || []).slice().sort((a, b) => (a.strike || 0) - (b.strike || 0));
    const c = cs[0];
    return c ? (c.conidYes || c.conid) : null;
  }

  // which locations have a series to show, so the map can mark exactly the dots
  // that will open one rather than asking per dot
  function sites() {
    const out = {};
    Object.keys(ledgers).forEach(k => {
      const doc = ledgers[k];
      if (!doc || !doc.sites) return;
      const cyc = (doc.steps || []).filter(x => x.kind !== 'final');
      Object.keys(doc.sites).forEach(sid => {
        if (cyc.some(x => ((x.sites || {})[sid] || []).some(v => v > 0))) out[sid] = true;
      });
    });
    return out;
  }

  return { init, draw, siteCard, sites, dormant, stampOf, setRoster, supersededBy, doneLabel };
})();
