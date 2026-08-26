/* The contract catalogue: a branch, a category, and one contract.

   Three page shapes off one registry. A branch page lists the categories under
   Climate & Weather or Energy; a category page lists that category's products
   and what state each is in; a contract page shows one product's ladder and how
   it settles.

   What a product's state means, and why it is the exchange's answer rather than
   the registry's: the registry names every product the exchange plans to carry,
   and the daily catalogue pass records which of them the exchange is actually
   listing today. The two disagree in both directions — products marked active
   that are not yet open, and products open that the registry has not caught up
   with — so the page shows what the exchange is doing and says when the
   registry expected otherwise.

   Nothing here computes a value. Prices are the exchange's own where a quote
   lane covers the product; where it does not, the page shows the ladder and its
   terms and says so. */
window.WXCat = (() => {
  const { el, txt, h, $, param } = WXC;
  let tip = null;

  const CAT = c => 'catalogue/' + c + '.json';
  const PRICE = id => 'catalogue/price/' + id + '.json';
  const SERIES = k => 'series/' + k + '.json';
  const SERIES_INDEX = 'series/index.json';
  const PROD = id => 'catalogue/product/' + id + '.json';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const nav = () => (window.WX && WX.nav) || { l1: [], categories: [] };

  // one line of plain English per state, because "unlisted" on its own reads
  // like a fault and is usually just a market the exchange has not opened
  const STATE = {
    listed: 'Listed',
    unlisted: 'Not currently listed',
    'no-contracts': 'Listed with no contracts open',
    deferred: 'Not read on the last pass',
    error: 'Could not be read',
  };
  const STATE_NOTE = {
    unlisted: 'The exchange is not carrying this contract at the moment. It appears here because it is part of the product family; when the exchange opens it, its ladder and prices appear with it.',
    'no-contracts': 'The market exists but has no strikes open right now.',
    deferred: 'The daily pass ran out of its time budget before reaching this one. It is read on the next pass.',
    error: 'The exchange did not answer for this product on the last pass.',
  };

  function badge(p) {
    const s = p.state || 'unlisted';
    const cls = s === 'listed' ? 'ok' : (s === 'error' ? 'bad' : 'off');
    return h('span', { class: 'pill ' + cls, text: STATE[s] || s });
  }

  // ---- the branch page: which categories sit under Climate & Weather or Energy
  async function branch() {
    const s = param('s') || (nav().l1[0] || {}).slug;
    const b = nav().l1.find(x => x.slug === s) || nav().l1[0];
    if (!b) return;
    document.title = b.name;
    $('#secTitle').textContent = b.name;
    const host = $('#cats'); host.innerHTML = '';
    const cats = nav().categories.filter(c => c.l1 === b.name);
    cats.forEach(c => {
      const card = h('a', { class: 'catcard', href: c.page }, [
        h('div', { class: 'ct', text: c.l2 }),
        h('div', { class: 'cn', text: c.active + ' listed' + (c.n > c.active ? ' · ' + (c.n - c.active) + ' not currently listed' : '') }),
      ]);
      host.appendChild(card);
    });
    $('#secCap').textContent = cats.length + ' categories · '
      + cats.reduce((a, c) => a + c.active, 0) + ' contracts the exchange is carrying, '
      + cats.reduce((a, c) => a + c.n, 0) + ' in the product family.';
  }

  // ---- the category page: every product in one category
  async function category() {
    const slug = param('c');
    const meta = nav().categories.find(c => c.slug === slug);
    if (!slug || !meta) { $('#catTitle').textContent = 'Unknown category'; return; }
    // Several categories have a display of their own — the map, the hurricane
    // page, the climate and crop panels — and the nav sends readers there. This
    // generic listing is still what an older link, a bookmark or a browser's
    // autocomplete resolves to, and it renders under the same highlighted tab,
    // so it reads as the current page while showing the superseded one. Send it
    // on instead, replacing the entry so Back does not bounce between the two.
    if (meta.page && meta.page.indexOf('category.html') !== 0) {
      location.replace(meta.page);
      return;
    }
    document.title = meta.l2;
    $('#catTitle').textContent = meta.l2.toUpperCase();
    $('#catCrumb').innerHTML = '<a href="section.html?s=' + esc(meta.l1slug || '') + '">' + esc(meta.l1) + '</a> · ' + esc(meta.l2);
    const r = await WXD.get(CAT(slug), 1440);
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440));
    const d = r.data;
    const host = $('#list'); host.innerHTML = '';
    if (!d || !(d.products || []).length) {
      host.appendChild(h('p', { class: 'cap', text: 'No catalogue for this category yet. It is written by the daily pass.' }));
      return;
    }
    const tb = h('table');
    tb.appendChild(h('tr', {}, [h('th', { text: 'Contract' }), h('th', { text: 'Code' }), h('th', { text: 'Status' }),
                                h('th', { class: 'num', text: 'Strikes' }), h('th', { text: 'Settles' })]));
    d.products.forEach(p => {
      const name = h('td', {}, [p.state === 'listed'
        ? h('a', { href: 'contract.html?id=' + encodeURIComponent(p.id), text: p.name || p.id })
        : h('span', { text: p.name || p.id })]);
      const tr = h('tr', { class: p.state === 'listed' ? '' : 'dim' }, [
        name,
        h('td', {}, [h('code', { text: p.id })]),
        h('td', {}, [badge(p)]),
        h('td', { class: 'num', text: p.strikes ? String(p.strikes) : '—' }),
        h('td', { text: (p.expiries || []).slice(0, 3).join(', ') || '—' }),
      ]);
      if (p.state !== 'listed' && STATE_NOTE[p.state]) tr.title = STATE_NOTE[p.state];
      tb.appendChild(tr);
    });
    host.appendChild(h('div', { class: 'card', style: 'padding:0' }, [tb]));
    const listed = d.products.filter(p => p.state === 'listed').length;
    const mismatch = d.products.filter(p => p.active && p.state !== 'listed');
    host.appendChild(h('p', { class: 'cap' },
      [h('span', { text: listed + ' of ' + d.products.length + ' contracts in this category are listed on the exchange right now. '
        + 'Rows in grey are part of the product family but not currently carried; they are shown so the family is complete. '
        + (mismatch.length ? mismatch.length + ' of them are expected to be listed and are not yet. ' : '')
        + 'Strike counts and settlement dates are read from the exchange once a day.' })]));
  }

  // ---- one contract
  async function contract() {
    const id = (param('id') || '').toUpperCase();
    if (!id) { $('#cTitle').textContent = 'No contract named'; return; }
    const r = await WXD.get(PROD(id), 1440);
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440));
    const p = r.data;
    if (!p) {
      $('#cTitle').textContent = id;
      $('#cBody').appendChild(h('p', { class: 'cap', text: 'No catalogue entry for this contract. Either the exchange is not listing it, or the daily pass has not read it yet.' }));
      return;
    }
    document.title = p.name || id;
    $('#cTitle').textContent = p.name || id;
    const cat = nav().categories.find(c => c.l2 === p.l2);
    $('#cCrumb').innerHTML = cat
      ? '<a href="section.html?s=' + esc(cat.l1slug) + '">' + esc(cat.l1) + '</a> · <a href="' + esc(cat.page) + '">' + esc(cat.l2) + '</a>'
      : esc(p.l1 || '') + ' · ' + esc(p.l2 || '');
    const head = h('div', { class: 'bar', style: 'margin:2px 0 8px' }, [
      h('code', { text: p.id }), badge(p),
      p.was ? h('span', { class: 'cap', style: 'margin:0', text: 'on the exchange under ' + p.was }) : h('span'),
    ]);
    $('#cBody').appendChild(head);

    if (p.state !== 'listed') {
      $('#cBody').appendChild(h('p', { class: 'cap', text: STATE_NOTE[p.state] || 'This contract is not currently listed.' }));
      return;
    }
    // prices first: the chart colours its strikes by them, and the ladder below
    // uses the same set
    let pr = null;
    try { pr = (await WXD.get(PRICE(p.id), 30)).data; } catch (e) { /* not quoted yet */ }
    const priced = {};
    ((pr && pr.rows) || []).forEach(r => { priced[String(r.spec || '') + '|' + String(r.strike)] = r; });

    // the underlying, where one of the series lanes covers this product
    try {
      const idx = (await WXD.get(SERIES_INDEX, 1440)).data;
      const key = idx && (idx.products || {})[p.id];
      if (key) {
        const sr = (await WXD.get(SERIES(key), 1440)).data;
        if (sr && (sr.points || []).length) {
          const c = chart(sr, p.contracts || [], priced);
          if (c) {
            $('#cBody').appendChild(h('div', { class: 'secttl', text: 'WHAT IT SETTLES ON' }));
            $('#cBody').appendChild(h('div', { class: 'card' }, [c]));
            const shown = (sr.points || []).length;
            const bits = [sr.title, sr.units ? 'in ' + sr.units : null, 'from ' + sr.source].filter(Boolean);
            // the mark to beat, computed from the series rather than written down,
            // so it stays right when a new high lands
            let rec = '';
            if ((idx.record || []).indexOf(key) >= 0 && sr.points.length) {
              const best = sr.points.reduce((a, b) => (b[1] > a[1] ? b : a));
              rec = ' The highest value in this record is ' + best[1] + ' ' + (sr.units || '')
                  + ', set in ' + String(best[0]).slice(0, 4) + '.';
            }
            const pnote = (idx.productNotes || {})[p.id];
            $('#cBody').appendChild(h('p', { class: 'cap', text: bits.join(' · ') + '. ' + (sr.note || '') + rec
              + (pnote ? ' ' + pnote : '')
              + (sr.expected && sr.title && sr.title.indexOf(sr.expected.split(',')[0]) < 0
                 ? ' The station drawn is ' + sr.expected + '.' : '') }));
          }
        }
      }
    } catch (e) { /* no series lane for this product; the ladder still stands */ }

    const url = WXM.contractUrl(p.productConid, ((p.contracts || [])[0] || {}).conidYes);
    if (url) {
      const go = h('button', { text: 'Open on IBKR →' });
      go.onclick = () => window.open(url, '_blank', 'noopener,noreferrer');
      $('#cBody').appendChild(h('div', { class: 'bar', style: 'margin:0 0 10px' }, [go]));
    }
    // the ladder, in the same Yes-green No-red language the temperature and
    // hurricane boards use. A plain list of strikes is the fallback, not the
    // default: it appears only when the exchange has quoted nothing at all.
    // a product the rotation has not reached yet is not a product without bids,
    // and must not be drawn as one
    const unquoted = !pr || !(pr.rows || []).length;
    const anyBids = ((pr && pr.rows) || []).some(r => r.mid != null);

    const byExp = {};
    (p.contracts || []).forEach(c => { (byExp[c.expiryLabel || c.spec || '—'] = byExp[c.expiryLabel || c.spec || '—'] || []).push(c); });
    Object.keys(byExp).forEach(k => {
      const rows = byExp[k];
      const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: k })]);
      rows.forEach(c => {
        const q = priced[String(c.spec || '') + '|' + String(c.strike)];
        const yes = q && q.mid != null ? Math.round(q.mid * 100) : null;
        const one = q && q.mid != null && (q.bid == null || q.ask == null);
        const u = WXM.contractUrl(p.productConid, c.conidYes);
        const bar = h('div', { class: 'lrow' + (one ? ' one' : '') }, [
          h('span', { class: 'lk', text: c.label || String(c.strike) }),
          h('span', { class: 'lb' }, [h('i', { style: 'width:' + (yes == null ? 0 : yes) + '%' })]),
          h('span', { class: 'lv' + (unquoted ? ' dim' : ''), text: unquoted ? '—' : (yes == null ? 'no bids' : yes + '¢' + (one ? '*' : '')) }),
        ]);
        const noBid = q && q.ask != null ? 100 - Math.round(q.ask * 100) : null;
        bind(bar, () => tip.rows((p.name || p.id) + ' — ' + (c.label || c.strike), [
          ['Yes price', yes == null ? 'no bids' : yes + '¢'],
          ['Yes bid', q && q.bid != null ? Math.round(q.bid * 100) + '¢' : '—'],
          ['No bid', noBid == null ? '—' : noBid + '¢'],
          ['Buy Yes now at', q && q.ask != null ? Math.round(q.ask * 100) + '¢' : null],
          ['Settles', expDate(c.expiration)],
          ['On the exchange', u ? '<a href="' + u + '" target="_blank" rel="noopener noreferrer">open this contract on IBKR →</a>' : null],
        ], 'Yes and No bids sum to $1; there are no sellers · not fee adjusted'
           + (pr && pr.asof ? ' · quoted ' + pr.asof.slice(11, 16) + 'Z' : '')));
        if (u) WXM.linkTo(bar.querySelector('.lv'), u, 'Open ' + (c.label || c.strike) + ' on IBKR');
        div.appendChild(bar);
      });
      $('#cBody').appendChild(div);
    });
    $('#cBody').appendChild(h('p', { class: 'cap', text: 'Strikes and settlement dates are read from the exchange once a day and '
      + 'prices every half hour. Yes green, No red; the two sides of a contract sum to a dollar and there are no sellers. '
      + (unquoted ? 'This contract has not come round on the price rotation yet, so no prices are shown for it; '
                    + 'that is not the same as it having no bids. '
                  : (anyBids ? '' : 'Nothing in this ladder has a bid on either side at the moment. '))
      + (pr && pr.dropped ? pr.dropped + ' further strikes were not quoted on the last pass. ' : '')
      + 'This site does not publish a fair value for any contract.' }));
  }

  // ---- the underlying, with the contract's strikes on it
  //
  // The same shape the climate page uses: the measured series, and the strikes
  // marked where they settle, coloured by nothing at all — this page publishes
  // no price and no fair value. A strike is a horizontal reach at its own
  // level from the last observation to its expiry, so a reader can see how far
  // the number has to travel and by when.
  // the same ramp the climate page uses, so a strike coloured by its price
  // reads the same wherever it appears on the site
  const RAMP = ['#8b0000', '#d62728', '#ff7f0e', '#ffd700', '#adff2f', '#3ddc84', '#40e0d0', '#4fc3f7', '#1f77b4', '#00008b'];
  function priceColor(p) {
    if (p == null) return 'var(--line)';
    const t = Math.max(0, Math.min(1, p)) * (RAMP.length - 1), i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = x => [1, 3, 5].map(k => parseInt(x.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((a, k) => Math.round(a + (B[k] - a) * f)).join(',') + ')';
  }

  function chart(sr, contracts, priced) {
    const all = sr.points || [];
    if (all.length < 6) return null;
    const cs = (contracts || []).filter(c => c.numeric && expMs(c.expiration));
    // How much history to show is decided by whether the strikes stay legible
    // in it. A crop's whole record spans sixty-five years and four tonnes per
    // hectare while its strikes sit inside half a tonne; drawn together the
    // markers land on top of each other. So the window is trimmed from the left
    // until the strike band is a readable share of the axis, and never below a
    // floor, and the caption says what window is drawn.
    const band = cs.length ? Math.max(...cs.map(c => c.strike)) - Math.min(...cs.map(c => c.strike)) : 0;
    const FLOOR = Math.min(24, all.length);
    let pts = all.slice(-240);
    if (band > 0) {
      for (let keep = all.length; keep >= FLOOR; keep -= Math.max(1, Math.round(all.length / 60))) {
        const win = all.slice(-keep);
        const vs = win.map(q => q[1]).concat(cs.map(c => c.strike));
        const span = Math.max(...vs) - Math.min(...vs);
        pts = win;
        if (span <= 0 || band / span >= 0.28) break;
      }
    }
    if (pts.length < 6) pts = all.slice(-Math.max(6, FLOOR));
    // The history gets the left of the panel and the listed contracts get a
    // column each on the right, the way the climate panels lay them out. A time
    // axis alone cannot do this here: a crop's expirations fall inside its own
    // history, so on one continuous axis every strike lands on top of the last
    // observation instead of in a grid.
    const W = 960, H = 330, L = 54, T = 18, B = 262;
    const R = cs.length ? 620 : 900, GR = 878;
    const px = k => {
      const t = String(k);
      if (t.length === 4) return Date.UTC(+t, 6, 1);
      if (t.length === 6) return Date.UTC(+t.slice(0, 4), +t.slice(4, 6) - 1, 15);
      return Date.UTC(+t.slice(0, 4), +t.slice(4, 6) - 1, +t.slice(6, 8) || 1);
    };
    const ts = pts.map(p => px(p[0])), vs = pts.map(p => p[1]);
    const sv = cs.map(c => c.strike);
    let lo = Math.min(...vs, ...(sv.length ? sv : [Infinity]));
    let hi = Math.max(...vs, ...(sv.length ? sv : [-Infinity]));
    if (!(hi > lo)) hi = lo + 1;
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;
    const t0 = ts[0], t1 = ts[ts.length - 1];
    const x = t => L + ((t - t0) / Math.max(t1 - t0, 1)) * (R - L);
    const y = v => B - ((v - lo) / (hi - lo)) * (B - T);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'serieschart' });

    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4;
      svg.appendChild(el('line', { x1: L, x2: GR, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(Math.round(v * 100) / 100, { x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }
    svg.appendChild(el('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + x(px(p[0])).toFixed(1) + ',' + y(p[1]).toFixed(1)).join(''),
                                 fill: 'none', stroke: 'var(--obs)', 'stroke-width': 1.8, 'pointer-events': 'none' }));
    const lastV = vs[vs.length - 1];
    svg.appendChild(txt('latest ' + lastV, { x: x(t1) - 4, y: y(lastV) - 8, 'text-anchor': 'end', class: 'ax' }));

    // time axis, thinned so labels never stack
    let lastYear = null, lastX = -1e9;
    pts.forEach(p => {
      const yr = String(p[0]).slice(0, 4);
      if (yr === lastYear) return;
      const xx = x(px(p[0]));
      if (xx - lastX < 46) { lastYear = yr; return; }
      lastYear = yr; lastX = xx;
      svg.appendChild(txt(yr, { x: xx, y: B + 15, 'text-anchor': 'middle', class: 'ax' }));
    });

    if (cs.length) {
      // one column per expiration, one marker per strike, coloured by price
      const specs = [];
      cs.forEach(c => { const k = c.expiryLabel || c.spec || String(c.expiration).slice(0, 4);
                        if (specs.indexOf(k) < 0) specs.push(k); });
      const gx = i => R + 44 + (specs.length === 1 ? (GR - R - 60) / 2 : (i / (specs.length - 1)) * (GR - R - 88));
      svg.appendChild(el('line', { x1: R + 18, x2: R + 18, y1: T, y2: B, stroke: 'var(--rule)', 'stroke-dasharray': '3 3' }));
      // a dashed guide at every distinct strike, so a marker reads against the
      // history's own scale rather than floating
      const levels = [...new Set(cs.map(c => c.strike))].sort((a, b) => a - b);
      // a marker is sized to the room its own ladder leaves it: AMOC has four
      // thresholds across six units and can afford big circles, a crop has a
      // dozen across half a tonne and cannot
      let gapPx = Infinity;
      for (let i = 1; i < levels.length; i++) gapPx = Math.min(gapPx, Math.abs(y(levels[i]) - y(levels[i - 1])));
      const rDot = levels.length < 2 ? 7 : Math.max(2.2, Math.min(7, gapPx / 2));
      let lastGuide = -1e9;
      levels.forEach(v => {
        if (Math.abs(y(v) - lastGuide) < 9) return;      // guides that touch are noise, not a grid
        lastGuide = y(v);
        svg.appendChild(el('line', { x1: R + 18, x2: GR, y1: y(v), y2: y(v),
                                     stroke: 'var(--line)', 'stroke-dasharray': '2 4', 'pointer-events': 'none' }));
        svg.appendChild(txt(v, { x: GR + 4, y: y(v) + 3.5, class: 'ax' }));
      });
      // an expiry label is whatever the exchange calls it, which can be a full
      // date; compacted to a month and year here, and skipped where the next
      // one would land on top of it
      const short = k => {
        const m = /([A-Z][a-z]{2})[a-z]*\s+\d{1,2},\s*(\d{4})/.exec(String(k));
        if (m) return m[1] + ' ' + m[2];
        const y2 = /(\d{4})/.exec(String(k));
        return y2 ? (String(k).length > 9 ? y2[1] : String(k)) : String(k);
      };
      let lastLab = -1e9;
      specs.forEach((k, i) => {
        if (gx(i) - lastLab < 54) return;
        lastLab = gx(i);
        svg.appendChild(txt(short(k), { x: gx(i), y: B + 15, 'text-anchor': 'middle', class: 'ax' }));
      });
      cs.forEach(c => {
        const k = c.expiryLabel || c.spec || String(c.expiration).slice(0, 4);
        const i = specs.indexOf(k);
        const q = (priced || {})[String(c.spec || '') + '|' + String(c.strike)];
        const yes = q && q.mid != null ? q.mid : null;
        const dot = el('circle', { cx: gx(i), cy: y(c.strike), r: rDot, fill: priceColor(yes),
                                   stroke: 'var(--ink)', 'stroke-width': rDot > 4 ? 1 : .5, 'pointer-events': 'all' });
        const gap = Math.round((c.strike - lastV) * 100) / 100;
        bind(dot, () => tip.rows(c.label || String(c.strike), [
          ['Yes price', yes == null ? 'no bids' : Math.round(yes * 100) + '¢'],
          ['Strike', String(c.strike) + ' ' + (sr.units || '')],
          ['Latest observation', lastV + ' ' + (sr.units || '')],
          ['Distance', (gap > 0 ? '+' : '') + gap],
          ['Settles', expDate(c.expiration)],
        ], 'colour is the exchange\u2019s Yes price; the strike is where the contract pays'));
        svg.appendChild(dot);
      });
      svg.appendChild(txt('listed contracts', { x: (R + 18 + GR) / 2, y: T - 4, 'text-anchor': 'middle', class: 'axl' }));
    }

    let kx = L;
    [[0, '0¢'], [0.25, '25¢'], [0.5, '50¢'], [0.75, '75¢'], [1, '100¢']].forEach(([v, lab]) => {
      svg.appendChild(el('rect', { x: kx, y: B + 26, width: 13, height: 9, fill: priceColor(v), stroke: 'var(--ink)', 'stroke-width': .5 }));
      svg.appendChild(txt(lab, { x: kx + 16, y: B + 34, class: 'ax' }));
      kx += 46;
    });
    svg.appendChild(txt('marker colour is the Yes price', { x: kx + 6, y: B + 34, class: 'ax' }));
    return svg;
  }

  const expMs = e => (!e || String(e).length < 8) ? null
    : Date.UTC(+String(e).slice(0, 4), +String(e).slice(4, 6) - 1, +String(e).slice(6, 8));
  function bind(node, html) {
    node.addEventListener('mousemove', e => { e.stopPropagation(); tip.show(e, html()); });
    node.addEventListener('mouseleave', () => tip.hide());
    return node;
  }

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const expDate = e => (!e || String(e).length < 8) ? '—'
    : MON[+String(e).slice(4, 6) - 1] + ' ' + (+String(e).slice(6, 8)) + ', ' + String(e).slice(0, 4);

  async function init(kind) {
    tip = WXC.tooltip();
    if (kind === 'branch') return branch();
    if (kind === 'category') return category();
    return contract();
  }
  return { init };
})();
