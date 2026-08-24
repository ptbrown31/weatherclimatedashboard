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

  // ---- one location's card: the ladder through the deliveries
  function card(doc, sid, storm) {
    const meta = doc.sites[sid] || {};
    const thr = doc.thresholds || [];
    const cyc = (doc.steps || []).filter(s => s.kind !== 'final');
    const fin = doc.final || null;
    const mk = lMarket(storm.name, sid);
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
      svg.appendChild(el('path', { d: pts.map((p, k) => (k ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join(''),
        fill: 'none', stroke: col, 'stroke-width': 1.6, 'pointer-events': 'none' }));
      const last = pts[pts.length - 1];
      // the exchange's price for the same rung, on the same scale
      const price = priceAt(mk, t);
      if (price != null) {
        // hollow, to read as the market's price rather than the vendor's line, but
        // painted transparent so the pointer lands on it instead of the band beneath
        const sq = el('rect', { x: x(last[0]) + 5, y: y(price) - 3.5, width: 7, height: 7, fill: 'transparent', stroke: col, 'stroke-width': 1.6, 'pointer-events': 'all' });
        bind(sq, () => tip.rows(esc(meta.name || sid) + ' — above ' + t + ' mph',
          [['The exchange', price + '¢'], ['The vendor’s latest', last[1] + '%'],
           ['Difference', (price - last[1] > 0 ? '+' : '') + Math.round((price - last[1]) * 10) / 10 + ' points']],
          'a price in cents and a probability in percent share the same scale · ' + esc((RK && RK.attribution) || 'Powered by Reask')));
        svg.appendChild(sq);
      }
      // ticks and crosses only once a settled gust exists to compare against
      if (fin && fin[sid] != null) {
        const hit = fin[sid] >= t;
        svg.appendChild(txt(hit ? '✓' : '✕', { x: SETTLE - 8, y: y(last[1]) + 3.5, 'font-size': 10, fill: hit ? 'var(--yes)' : 'var(--muted)' }));
      }
    });
    if (fin && fin[sid] != null) svg.appendChild(txt(fin[sid] + ' mph', { x: SETTLE - 8, y: B - 2, 'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)' }));

    const wrap = h('div', { class: 'scardwrap' }, [
      h('div', { class: 'lt', text: (meta.name || sid) + ' (' + sid + ')' }),
      h('div', { class: 'cap', style: 'margin:0 0 4px', text: mk ? 'contracts listed · ' + mk.symbol : 'no contracts listed for this location yet' }),
    ]);
    wrap.appendChild(svg);
    return wrap;
  }

  const label = s => (s.kind === 'interim' ? 'interim settlement' : s.kind === 'final' ? 'final settlement' : String(s.id || '').replace(/^(\d{4})(\d{2})(\d{2})(\d{2})$/, '$2/$3 $4Z'));

  function bind(node, html) {
    node.addEventListener('mousemove', e => { e.stopPropagation(); tip.show(e, html()); });
    node.addEventListener('mouseleave', () => tip.hide());
    node.addEventListener('click', e => { e.stopPropagation(); tip.pin(e, html()); });
    node.setAttribute('data-tip-pin', '1');
    return node;
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
        bind(row, () => tip.rows((m.name || m.symbol) + ' — ' + esc(c.label || c.strike),
          [['Yes price', v == null ? 'no bids' : v + '¢'],
           ['Yes bid', c.bid == null ? '—' : cents(c.bid) + '¢'],
           ['No bid', c.ask == null ? '—' : (100 - cents(c.ask)) + '¢'],
           ['Buy Yes now at', c.ask == null ? null : cents(c.ask) + '¢' + (WXM.payoutText(cents(c.ask)) ? ' · pays ' + WXM.payoutText(cents(c.ask)) : '')]],
          'settles on the vendor’s final peak gusts'));
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
    order.slice(0, MAX_CARDS).forEach(sid => grid.appendChild(card(doc, sid, storm)));
    host.appendChild(grid);
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
  return { init, draw };
})();
