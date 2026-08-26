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
