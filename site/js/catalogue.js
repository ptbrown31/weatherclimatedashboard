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
    // the heading is the branch. The title says what the branch is a branch of,
    // because it is read on its own in a tab and in a search result
    document.title = b.name + ' prediction markets';
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
    document.title = meta.l2 + ' prediction markets';
    $('#catTitle').textContent = meta.l2.toUpperCase();
    $('#catCrumb').innerHTML = '<a href="section.html?s=' + esc(meta.l1slug || '') + '">' + esc(meta.l1) + '</a> · ' + esc(meta.l2);
    const r = await WXD.get(CAT(slug), 1440);
    const st = $('#pageStatus');
    if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440)); }
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
    const st = $('#pageStatus');
    if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440)); }
    const p = r.data;
    if (!p) {
      $('#cTitle').textContent = id;
      $('#cBody').appendChild(h('p', { class: 'cap', text: 'No catalogue entry for this contract. Either the exchange is not listing it, or the daily pass has not read it yet.' }));
      return;
    }
    document.title = (p.name || id) + ' prediction market';
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
          // the same panel the category pages draw, so a product looks the same
          // wherever it is met and carries the same zoom and projection
          const host = h('div');
          $('#cBody').appendChild(h('div', { class: 'secttl', text: 'SETTLEMENT BASIS' }));
          $('#cBody').appendChild(host);
          const ser = (sr.points || []).map(q => [WXPanels.xOfPeriod(q[0]), q[1]])
            .filter(q => q[0] != null && q[1] != null);
          const cs = (p.contracts || []).map(c => {
            const q = priced[String(c.spec || '') + '|' + String(c.strike)] || {};
            const x = WXPanels.xOf(c.expiryLabel);
            if (x == null || c.strike == null) return null;
            return { year: x, threshold: c.strike, label: c.label || ('Above ' + c.strike),
                     expiration: c.expiration, expiryLabel: c.expiryLabel,
                     conidYes: c.conidYes, conid: c.conidYes,
                     yes: q.mid != null ? q.mid : null, bid: q.bid, ask: q.ask,
                     bidSize: q.bidSize, askSize: q.askSize, from: q.from };
          }).filter(Boolean);
          const nums = ser.map(q => q[1]).concat(cs.map(c => c.threshold));
          const big = nums.length && Math.max(...nums.map(Math.abs)) >= 1000;
          const fmt = v => (v == null ? '—' : Math.abs(v) >= 10000 ? Math.round(v).toLocaleString('en-US')
                                            : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2));
          WXClimate.panel(host, key, sr.title || p.name, sr.units || '', ser,
                          { id: p.id, name: p.name || p.id, productConid: p.productConid, contracts: cs }, 0,
                          sr.source || '',
                          { markerRadius: 'auto', trendNote: 'byYear', tightRight: true, clampZero: true,
                            allocLink: false,
                            fmt, fmtAxis: fmt, fmtThreshold: fmt, thresholdSuffix: '',
                            x0: ser.length ? Math.max(ser[0][0], (cs.length ? Math.min(...cs.map(c => c.year)) : ser[ser.length - 1][0]) - 12) : undefined });
          if (true) {
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

    // the SW count contracts add the month in progress under the history,
    // the hurricane season shape at monthly scale
    if (window.WXSevere && WXSevere.PRODUCTS[p.id]) {
      try {
        const sev = (await WXD.get('severe.json', 30)).data;
        if (sev) {
          const host3 = h('div');
          $('#cBody').appendChild(h('div', { class: 'secttl', text: 'MONTH IN PROGRESS' }));
          $('#cBody').appendChild(host3);
          WXSevere.monthBlock(host3, p, priced, sev);
        }
      } catch (e) { /* the ladder below still stands */ }
    }

    const url = WXM.contractUrl(p.productConid, ((p.contracts || [])[0] || {}).conidYes);
    const row = h('div', { class: 'bar', style: 'margin:0 0 10px' });
    if (url) {
      const go = h('button', { text: 'Open on IBKR →' });
      go.onclick = () => window.open(url, '_blank', 'noopener,noreferrer');
      row.appendChild(go);
    }
    /* The same board, loaded into the position allocation calculator with its live
       prices. A count product travels under its own route there; a daily
       temperature board is addressed by its station, recovered from the
       product code (the ICAO less its first letter, with the exchange's two
       exceptions), and when the station cannot be recovered the link opens
       the calculator plain rather than wrongly. */
    (async () => {
      const slug = ((window.WX && WX.nav && WX.nav.product) || {})[p.id];
      let m = (slug === 'tropical-cyclones' ? 'hur:' : 'prod:') + p.id;
      if (slug === 'daily-temperatures') {
        m = '';
        try {
          const code = String(p.id).replace(/^(UH|UL|SH|SL)/, '');
          const EXC = { YHC: 'CYVR', FPO: 'LFPG' };
          const cities = ((await WXD.get('summary.json')).data || {}).cities || [];
          const hit = EXC[code] || (cities.find(c2 => c2.station && c2.station.slice(1) === code) || {}).station;
          if (hit) m = 'city:' + hit;
        } catch (e) { /* the plain link still stands */ }
      }
      const go2 = h('button', { text: 'Position allocation calculator →' });
      go2.onclick = () => { location.href = 'allocator.html' + (m ? '?m=' + encodeURIComponent(m) : ''); };
      row.appendChild(go2);
    })();
    $('#cBody').appendChild(row);
    // the ladder, in the same Yes-green No-red language the temperature and
    // hurricane boards use. A plain list of strikes is the fallback, not the
    // default: it appears only when the exchange has quoted nothing at all.
    ladder($('#cBody'), p, priced, pr);
  }

  // The ladder, in the same Yes-green No-red language the temperature and
  // hurricane boards use. Extracted so a category page can draw it for a
  // product that has no series behind it — a milestone contract has nothing to
  // plot against time, and a list of strikes is the last resort, not the first.
  function ladder(host, p, priced, pr) {
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
        // an empty book's widest-spread midpoint is not a price (WXM.realMid)
        const yes = q && WXM.realMid(q) ? Math.round(q.mid * 100) : null;
        const one = q && q.mid != null && (q.bid == null || q.ask == null);
        const u = WXM.contractUrl(p.productConid, c.conidYes);
        // a row with no real price draws a hollow bar rather than a full red
        // one, which read as No at a dollar
        const bar = h('div', { class: 'lrow' + (one ? ' one' : '') }, [
          h('span', { class: 'lk', text: c.label || String(c.strike) }),
          h('span', { class: 'lb', style: yes == null ? 'background:transparent;border:1px dashed var(--rule)' : null },
            [h('i', { style: 'width:' + (yes == null ? 0 : yes) + '%' })]),
          h('span', { class: 'lv' + (unquoted ? ' dim' : ''),
                      text: unquoted ? '—'
                        : (yes != null ? yes + '¢' + (one ? '*' : '')
                          : (q && q.mid != null ? 'no price' : 'no bids')) }),
        ]);
        const noBid = q && q.ask != null ? 100 - Math.round(q.ask * 100) : null;
        bind(bar, () => tip.rows((p.name || p.id) + ' — ' + (c.label || c.strike), [
          ['Yes price', yes == null ? 'no bids' : yes + '¢'],
          ['Yes bid', q && q.bid != null ? Math.round(q.bid * 100) + '¢' : '—'],
          ['No bid', noBid == null ? '—' : noBid + '¢'],
          ['Buy Yes now at', q && q.ask != null ? Math.round(q.ask * 100) + '¢' : null],
          ['Settles', expDate(c.expiration)],

        ], 'Yes and No bids sum to $1; there are no sellers · not fee adjusted'
           + (pr && pr.asof ? ' · quoted ' + pr.asof.slice(11, 16) + 'Z' : '')));
        if (u) WXM.linkTo(bar.querySelector('.lv'), u, 'Open ' + (c.label || c.strike) + ' on IBKR');
        div.appendChild(bar);
      });
      host.appendChild(div);
    });
    host.appendChild(h('p', { class: 'cap', text: 'Strikes and settlement dates are read from the exchange once a day and '
      + 'prices every half hour. Yes green, No red; the two sides of a contract sum to a dollar and there are no sellers. '
      + (unquoted ? 'This contract has not come round on the price rotation yet, so no prices are shown for it; '
                    + 'that is not the same as it having no bids. '
                  : (anyBids ? '' : 'Nothing in this ladder has a bid on either side at the moment. '))
      + (pr && pr.dropped ? pr.dropped + ' further strikes were not quoted on the last pass. ' : '')
      + 'This site does not publish a fair value for any contract.' }));
  }

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
  return { ladder, init };
})();
