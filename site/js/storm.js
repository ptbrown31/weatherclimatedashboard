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

  // a colour per threshold, cold to hot across the ladder
  const rung = (i, n) => 'hsl(' + Math.round(210 - 210 * (i / Math.max(1, n - 1))) + ' 70% 45%)';
  const stormCode = n => String(n || '').replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase();

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
      (m.contracts || []).slice().sort((a, b) => (b.mid == null ? -1 : b.mid) - (a.mid == null ? -1 : a.mid)).forEach(c => {
        const v = c.mid == null ? null : cents(c.mid);
        const row = h('div', { class: 'lrow' }, [
          h('span', { class: 'lk', text: c.label || String(c.strike) }),
          h('span', { class: 'lb' }, [h('i', { style: 'width:' + (v == null ? 0 : v) + '%' })]),
          h('span', { class: 'lv', text: v == null ? 'no bids' : v + '¢' })]);
        const url = WXM.contractUrl(m.productConid, c.conidYes || c.conid);
        bind(row, () => tip.rows((m.name || m.symbol) + ' — ' + esc(c.label || c.strike),
          [['Yes price', v == null ? 'no bids' : v + '¢'],
           ['Yes bid', c.bid == null ? '—' : cents(c.bid) + '¢'],
           ['No bid', c.ask == null ? '—' : (100 - cents(c.ask)) + '¢'],
           ['Buy Yes now at', c.ask == null ? null : cents(c.ask) + '¢' + (WXM.payoutText(cents(c.ask)) ? ' · pays ' + WXM.payoutText(cents(c.ask)) : '')],
           ['On the exchange', url ? '<a href="' + url + '" target="_blank" rel="noopener noreferrer">open this contract on IBKR →</a>' : null]],
          'settles on the vendor’s final peak gusts' + (url ? ' · click the price to open the contract' : '')));
        if (url) WXM.linkTo(row.querySelector('.lv'), url, 'Open ' + (c.label || c.strike) + ' on IBKR');
        div.appendChild(row);
      });
      if (!(m.contracts || []).length) div.appendChild(h('div', { class: 'cap', text: 'No candidates listed yet.' }));
      out.push(div);
    });
    return out;
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
    host.appendChild(h('p', { class: 'cap', text: state.join(' · ') }));
    if (!doc || !cyc.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No probability ladder has been published for this storm yet.' }));
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
    host.appendChild(h('p', { class: 'cap attrib', text: ((RK && RK.attribution) || 'Powered by Reask') + '. Probabilities are the vendor’s, shown as published; the squares are the exchange’s own prices. The horizontal axis counts vendor deliveries, not time.' }));
  }

  async function draw(rk, mk) {
    RK = rk; MK = mk;
    const host = $('#liveStorms'); if (!host) return;
    host.innerHTML = '';
    const storms = ((rk && rk.storms) || []).filter(s => s && s.name);
    if (!rk || !rk.enabled) {
      host.appendChild(h('p', { class: 'cap', text: 'The live-storm wind lane is not enabled on this site' + (rk && rk.reason ? ' (' + esc(rk.reason) + ')' : '') + '. When it is on and a storm is active, each reference location the vendor signals on appears here, delivery by delivery, beside the exchange’s price for the same contract.' }));
      return;
    }
    if (!storms.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No storm with published probabilities at the moment. A storm appears here on its first vendor delivery.' }));
      return;
    }
    // several storms can run at once, so each gets its own tab and its own ledger
    const bar = h('div', { class: 'bar' });
    const panel = h('div');
    if (!open || !storms.some(s => s.name + '_' + s.year === open)) open = storms[0].name + '_' + storms[0].year;
    storms.forEach(s => {
      const k = s.name + '_' + s.year;
      const b = h('button', { class: k === open ? 'on' : '', text: s.name });
      b.onclick = () => { open = k; bar.querySelectorAll('button').forEach(x => x.classList.remove('on')); b.classList.add('on'); drawStorm(s, panel); };
      bar.appendChild(b);
    });
    if (storms.length > 1) host.appendChild(bar);
    host.appendChild(panel);
    await drawStorm(storms.find(s => s.name + '_' + s.year === open) || storms[0], panel);
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

  return { init, draw, siteCard, sites };
})();
