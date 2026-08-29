/* A catalogue category drawn as a page of panels.

   The agriculture and energy tabs are the same idea: every product in the
   category, in sequence, each as its measured series with the listed strikes
   marked on it. That is the climate page's panel, so this reaches for it rather
   than inventing a second way to draw a series against a threshold.

   Where a product has no series it falls back to the ladder the contract pages
   draw, in the same Yes-green No-red language as the temperature and hurricane
   boards. That is the last resort and it looks like one: a milestone contract —
   whether a fusion machine reaches first plasma — has nothing to plot against
   time, and pretending otherwise would be worse than a list.

   A product the exchange is not currently listing still gets a heading and a
   line saying so, because absence from a page reads as an oversight and this is
   a fact about the market.

   Expirations arrive as text and have to become a position on a year axis: a
   crop year is "2027", a month of generation is "August 2026", a weekly fuel
   price is "December 28, 2026". All three land on the same axis here, a month
   at its middle and a date at its day, which is the convention the underlying
   series are written with. */
window.WXPanels = (() => {
  const { h, $ } = WXC;

  const CAT = c => 'catalogue/' + c + '.json';
  const PROD = id => 'catalogue/product/' + id + '.json';
  const PRICE = id => 'catalogue/price/' + id + '.json';
  const SERIES = k => 'series/' + k + '.json';
  const SERIES_INDEX = 'series/index.json';
  const MON = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
               'august', 'september', 'october', 'november', 'december'];

  // "2027" | "August 2026" | "December 28, 2026" -> a number on the year axis
  function xOf(label) {
    const s = String(label || '').trim();
    let m = /^(\d{4})$/.exec(s);
    if (m) return +m[1];
    m = /^([A-Za-z]+)\s+(\d{4})$/.exec(s);
    if (m) {
      const i = MON.indexOf(m[1].toLowerCase());
      if (i >= 0) return +m[2] + (i + 0.5) / 12;
    }
    m = /^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$/.exec(s);
    if (m) {
      const i = MON.indexOf(m[1].toLowerCase());
      if (i >= 0) {
        const d = new Date(Date.UTC(+m[3], i, +m[2]));
        const day = (d - Date.UTC(+m[3], 0, 1)) / 86400000;
        return +m[3] + (day + 0.5) / 366;
      }
    }
    return null;
  }

  /* A series period as a number on the year axis.

     The lanes write what their source gives them: a crop year is "1961", a
     month of rain is "199002", a drought reading is "20000104", and the energy
     lane already writes decimal years. All four have to land on one axis, and a
     month has to sit inside its year rather than at its start — parsing
     "199002" as a number gives a hundred and ninety-nine thousand, which is how
     an entire page came out blank. */
  function xOfPeriod(v) {
    if (typeof v === 'number') return isFinite(v) ? v : null;
    const s = String(v == null ? '' : v).replace(/-/g, '');
    if (/^\d{4}$/.test(s)) return +s;
    if (/^\d{6}$/.test(s)) {
      const y = +s.slice(0, 4), m = +s.slice(4, 6);
      return m >= 1 && m <= 12 ? y + (m - 0.5) / 12 : null;
    }
    if (/^\d{8}$/.test(s)) {
      const y = +s.slice(0, 4), m = +s.slice(4, 6), d = +s.slice(6, 8);
      if (!(m >= 1 && m <= 12 && d >= 1 && d <= 31)) return null;
      const day = (Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 1)) / 86400000;
      return Math.round((y + (day + 0.5) / 366) * 10000) / 10000;
    }
    const f = parseFloat(s);
    return isFinite(f) ? f : null;
  }

  // catalogue terms and catalogue prices are two files, joined on expiry and strike
  function contracts(prod, price) {
    const priced = {};
    ((price && price.rows) || []).forEach(r => { priced[String(r.spec || '') + '|' + String(r.strike)] = r; });
    return (prod.contracts || []).map(c => {
      const q = priced[String(c.spec || '') + '|' + String(c.strike)] || {};
      const x = xOf(c.expiryLabel);
      if (x == null || c.strike == null) return null;
      return { year: x, threshold: c.strike, label: c.label || ('Above ' + c.strike),
               expiration: c.expiration, expiryLabel: c.expiryLabel,
               conidYes: c.conidYes, conid: c.conidYes,
               yes: q.mid != null ? q.mid : null,
               bid: q.bid != null ? q.bid : null, ask: q.ask != null ? q.ask : null,
               bidSize: q.bidSize, askSize: q.askSize, from: q.from };
    }).filter(Boolean);
  }

  function priceMap(price) {
    const m = {};
    ((price && price.rows) || []).forEach(r => { m[String(r.spec || '') + '|' + String(r.strike)] = r; });
    return m;
  }

  async function init(slug, opts) {
    opts = opts || {};
    const host = $('#panels'); const st = $('#pageStatus');
    host.innerHTML = '';
    const listing = await WXD.get(CAT(slug), 1440);
    const idxRes = await WXD.get(SERIES_INDEX, 1440);
    if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([listing, idxRes], 1440)); }
    const idx = idxRes.data || {};
    let products = ((listing.data || {}).products) || [];
    // a category can name the products its readers come for; those lead in
    // the given order and the rest keep the listing's own order
    if (opts.first && opts.first.length) {
      const rank = id => { const i = opts.first.indexOf(id); return i < 0 ? opts.first.length : i; };
      products = products.slice().sort((a, b) => rank(a.id) - rank(b.id));
    }
    if (!products.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No products are listed in this category yet.' }));
      return;
    }
    await WXM.loadGroup('climate');   // the panel reads WXM for links and price labels

    let drawn = 0, laddered = 0, unlisted = 0, pending = 0, evented = 0;
    const notes = [];
    for (const light of products) {
      const name = light.name || light.id;
      if (light.state !== 'listed') {
        // an unlisted SW product still shows the history and the month in
        // progress, since the counts it would settle on are public either way
        if (window.WXSevere && WXSevere.PRODUCTS[light.id]) {
          const key2 = (idx.products || {})[light.id];
          const [pRes2, sevRes, sRes2] = await Promise.all([
            WXD.get(PROD(light.id), 1440), WXD.get('severe.json', 30),
            key2 ? WXD.get(SERIES(key2), 1440) : Promise.resolve(null)]);
          const prod2 = pRes2.data || {};
          const sr2 = sRes2 && sRes2.data;
          let any = false;
          if (sr2 && (sr2.points || []).length) {
            host.appendChild(h('div', { class: 'psub cap', style: 'margin:0 2px 4px',
              text: (prod2.name || name) + ' is not currently listed on the exchange; the series it would settle on is below.' }));
            const ser2 = sr2.points.map(q => [xOfPeriod(q[0]), q[1]]).filter(q => q[0] != null && q[1] != null);
            WXClimate.panel(host, key2, sr2.title || name, sr2.units || '', ser2,
                            { id: light.id, name: prod2.name || name, productConid: prod2.productConid, contracts: [] },
                            0, sr2.source || '', Object.assign({ markerRadius: 'auto', trendNote: 'byYear' }, opts.panel || {}));
            any = true;
          }
          if (sevRes.data && WXSevere.monthBlock(host, Object.assign({ id: light.id, name }, prod2),
                                                 null, sevRes.data)) any = true;
          if (any) { unlisted++; continue; }
        }
        host.appendChild(h('div', { class: 'panel' }, [
          h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: name }),
          h('div', { class: 'psub cap', style: 'margin:2px 2px 6px', text: 'Not currently listed on the exchange.' }),
        ]));
        unlisted++;
        continue;
      }
      const key = (idx.products || {})[light.id];
      const [pRes, qRes] = await Promise.all([WXD.get(PROD(light.id), 1440), WXD.get(PRICE(light.id), 30)]);
      const prod = pRes.data || {};
      const sRes = key ? await WXD.get(SERIES(key), 1440) : null;
      const sr = sRes && sRes.data;

      if (sr && (sr.points || []).length) {
        const ser = sr.points.map(q => [xOfPeriod(q[0]), q[1]]).filter(q => q[0] != null && q[1] != null);
        const cs = contracts(prod, qRes.data);
        const product = { id: light.id, name: prod.name || name,
                          productConid: prod.productConid, contracts: cs };
        WXClimate.panel(host, key, sr.title || product.name, sr.units || '', ser, product, 0,
                        sr.source || '', Object.assign({ markerRadius: 'auto', trendNote: 'byYear' }, opts.panel || {}));
        if (sr.note && notes.indexOf(sr.note) < 0) notes.push(sr.note);
        // the SW count contracts get the month in progress under the history,
        // the hurricane season shape at monthly scale
        if (window.WXSevere && WXSevere.PRODUCTS[light.id]) {
          const sevRes = await WXD.get('severe.json', 30);
          if (sevRes.data) WXSevere.monthBlock(host, Object.assign({ id: light.id }, prod),
                                               priceMap(qRes.data), sevRes.data);
        }
        drawn++;
        continue;
      }
      // No series: the ladder, with a heading so the product is still named.
      // Why there is no series matters and the two reasons are different. A
      // milestone contract has nothing to plot and never will. A product that
      // does have a series behind it, but whose series has not been published,
      // is a gap in this site rather than a fact about the contract, and saying
      // "resolves on an event" there would simply be untrue.
      const why = key
        ? 'The series behind this contract has not been published yet, so its strikes are listed in the meantime.'
        : (opts.ladderNote || 'This contract resolves on an event, not on a published series, so its strikes are '
                              + 'listed rather than plotted.');
      if (key) pending++; else evented++;
      const div = h('div', { class: 'panel' }, [
        h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: prod.name || name }),
        h('div', { class: 'psub cap', style: 'margin:2px 2px 6px', text: why }),
      ]);
      host.appendChild(div);
      WXCat.ladder(div, Object.assign({}, prod, { name: prod.name || name }), priceMap(qRes.data), qRes.data);
      laddered++;
    }

    const foot = $('#foot');
    if (foot) {
      foot.textContent = (opts.source ? opts.source + ' ' : '')
        + (WXM.on() ? (WXM.live()
            ? 'Markers are the exchange\'s listed contracts at the Yes midpoint, coloured by price; click one to open it on IBKR. '
            : 'Markers are placeholders, not market values. ') : '')
        + drawn + ' of these are drawn against their published series, '
        + evented + ' resolve on an event and are listed as ladders, '
        + (pending ? pending + ' are waiting on a series this site has not published yet, ' : '')
        + 'and ' + unlisted + ' are not currently listed. '
        + notes.join(' ');
    }
  }
  return { init, xOf, xOfPeriod };
})();
