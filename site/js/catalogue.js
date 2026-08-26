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
    // the underlying, where one of the series lanes covers this product
    try {
      const idx = (await WXD.get(SERIES_INDEX, 1440)).data;
      const key = idx && (idx.products || {})[p.id];
      if (key) {
        const sr = (await WXD.get(SERIES(key), 1440)).data;
        if (sr && (sr.points || []).length) {
          const c = chart(sr, p.contracts || []);
          if (c) {
            $('#cBody').appendChild(h('div', { class: 'secttl', text: 'WHAT IT SETTLES ON' }));
            $('#cBody').appendChild(h('div', { class: 'card' }, [c]));
            const bits = [sr.title, sr.units ? 'in ' + sr.units : null, 'from ' + sr.source].filter(Boolean);
            $('#cBody').appendChild(h('p', { class: 'cap', text: bits.join(' · ') + '. ' + (sr.note || '')
              + (sr.expected && sr.title && sr.title.indexOf(sr.expected.split(',')[0]) < 0
                 ? ' The station drawn is ' + sr.expected + '.' : '') }));
          }
        }
      }
    } catch (e) { /* no series lane for this product; the ladder still stands */ }

    const url = WXM.contractUrl(p.productConid, ((p.contracts || [])[0] || {}).conidYes);
    if (url) {
      const go = h('button', { text: 'Open on ForecastEx →' });
      go.onclick = () => window.open(url, '_blank', 'noopener,noreferrer');
      $('#cBody').appendChild(h('div', { class: 'bar', style: 'margin:0 0 10px' }, [go]));
    }
    // the ladder, grouped by the period each strike settles in
    const byExp = {};
    (p.contracts || []).forEach(c => { (byExp[c.expiryLabel || c.spec || '—'] = byExp[c.expiryLabel || c.spec || '—'] || []).push(c); });
    Object.keys(byExp).forEach(k => {
      const rows = byExp[k];
      const tb = h('table');
      tb.appendChild(h('tr', {}, [h('th', { text: k }), h('th', { class: 'num', text: 'Strike' }), h('th', { text: 'Settles' })]));
      rows.forEach(c => {
        const u = WXM.contractUrl(p.productConid, c.conidYes);
        const lab = h('td', {}, [u ? h('a', { href: u, text: c.label || String(c.strike), target: '_blank', rel: 'noopener noreferrer' })
                                   : h('span', { text: c.label || String(c.strike) })]);
        tb.appendChild(h('tr', {}, [lab,
          h('td', { class: 'num', text: c.numeric ? String(c.strike) : '—' }),
          h('td', { text: expDate(c.expiration) })]));
      });
      $('#cBody').appendChild(h('div', { class: 'card', style: 'padding:0;margin-bottom:10px' }, [tb]));
    });
    $('#cBody').appendChild(h('p', { class: 'cap', text: 'Strikes and settlement dates are read from the exchange once a day; '
      + 'prices are not shown on this page. This site does not publish a fair value for any contract.' }));
  }

  // ---- the underlying, with the contract's strikes on it
  //
  // The same shape the climate page uses: the measured series, and the strikes
  // marked where they settle, coloured by nothing at all — this page publishes
  // no price and no fair value. A strike is a horizontal reach at its own
  // level from the last observation to its expiry, so a reader can see how far
  // the number has to travel and by when.
  function chart(sr, contracts) {
    const pts = (sr.points || []).slice(-180);
    if (pts.length < 6) return null;
    const W = 960, H = 300, L = 54, R = 782, T = 18, B = 246;
    const px = k => String(k).length === 6 ? Date.UTC(+String(k).slice(0, 4), +String(k).slice(4, 6) - 1, 15)
                                           : Date.UTC(+String(k).slice(0, 4), +String(k).slice(4, 6) - 1, +String(k).slice(6, 8));
    const ts = pts.map(p => px(p[0])), vs = pts.map(p => p[1]);
    // strikes share the value axis, so the axis has to hold them too
    const sv = (contracts || []).filter(c => c.numeric).map(c => c.strike);
    let lo = Math.min(...vs, ...(sv.length ? sv : [Infinity]));
    let hi = Math.max(...vs, ...(sv.length ? sv : [-Infinity]));
    if (!(hi > lo)) { hi = lo + 1; }
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;
    const t0 = ts[0];
    const expTs = (contracts || []).map(c => expMs(c.expiration)).filter(Boolean);
    const t1 = Math.max(ts[ts.length - 1], ...(expTs.length ? expTs : [ts[ts.length - 1]]));
    const x = t => L + ((t - t0) / Math.max(t1 - t0, 1)) * (R - L);
    const y = v => B - ((v - lo) / (hi - lo)) * (B - T);
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'serieschart' });
    // value axis
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4;
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(Math.round(v * 10) / 10, { x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }
    // the observed series
    svg.appendChild(el('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + x(px(p[0])).toFixed(1) + ',' + y(p[1]).toFixed(1)).join(''),
                                 fill: 'none', stroke: 'var(--obs)', 'stroke-width': 1.8, 'pointer-events': 'none' }));
    // where the record ends, because everything to the right of it is a strike
    const lastT = ts[ts.length - 1], lastV = vs[vs.length - 1];
    svg.appendChild(el('line', { x1: x(lastT), x2: x(lastT), y1: T, y2: B, stroke: 'var(--muted)', 'stroke-dasharray': '4 3', 'pointer-events': 'none' }));
    svg.appendChild(txt('latest ' + lastV, { x: x(lastT) + 4, y: T + 10, class: 'ax' }));
    // the strikes
    (contracts || []).filter(c => c.numeric).forEach(c => {
      const e = expMs(c.expiration); if (!e) return;
      const yy = y(c.strike);
      const line = el('line', { x1: x(lastT), x2: x(e), y1: yy, y2: yy, stroke: 'var(--accent)', 'stroke-width': 1.2, opacity: .55 });
      const dot = el('circle', { cx: x(e), cy: yy, r: 3.4, fill: 'var(--accent)', 'pointer-events': 'all' });
      const gap = Math.round((c.strike - lastV) * 100) / 100;
      bind(dot, () => tip.rows(c.label || String(c.strike), [
        ['Strike', String(c.strike) + ' ' + (sr.units || '')],
        ['Latest observation', lastV + ' ' + (sr.units || '')],
        ['Distance', (gap > 0 ? '+' : '') + gap],
        ['Settles', expDate(c.expiration)],
      ], 'the strike is where the contract pays; this page shows no price'));
      svg.appendChild(line); svg.appendChild(dot);
    });
    // time axis: a label a year, and only where the last one has cleared, so a
    // long series thins its labels instead of stacking them on top of each other
    let lastYear = null, lastX = -1e9;
    pts.forEach(p => {
      const yr = String(p[0]).slice(0, 4);
      if (yr === lastYear) return;
      const xx = x(px(p[0]));
      if (xx - lastX < 46) { lastYear = yr; return; }
      lastYear = yr; lastX = xx;
      svg.appendChild(txt(yr, { x: xx, y: B + 15, 'text-anchor': 'middle', class: 'ax' }));
    });
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
