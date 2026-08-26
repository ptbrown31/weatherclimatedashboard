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
    const products = ((listing.data || {}).products) || [];
    if (!products.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No products are listed in this category yet.' }));
      return;
    }
    await WXM.loadGroup('climate');   // the panel reads WXM for links and price labels

    let drawn = 0, laddered = 0, unlisted = 0;
    const notes = [];
    for (const light of products) {
      const name = light.name || light.id;
      if (light.state !== 'listed') {
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
        const ser = sr.points.map(q => [parseFloat(q[0]), q[1]]).filter(q => isFinite(q[0]) && q[1] != null);
        const cs = contracts(prod, qRes.data);
        const product = { name: prod.name || name, productConid: prod.productConid, contracts: cs };
        WXClimate.panel(host, key, sr.title || product.name, sr.units || '', ser, product, 0,
                        sr.source || '', Object.assign({ markerRadius: 'auto', trendNote: 'byYear' }, opts.panel || {}));
        if (sr.note && notes.indexOf(sr.note) < 0) notes.push(sr.note);
        drawn++;
        continue;
      }
      // no series: the ladder, with a heading so the product is still named
      const div = h('div', { class: 'panel' }, [
        h('div', { style: 'font-size:14px;font-weight:700;color:var(--navy)', text: prod.name || name }),
        h('div', { class: 'psub cap', style: 'margin:2px 2px 6px',
                   text: opts.ladderNote || 'This contract resolves on an event, not on a published series, so its '
                         + 'strikes are listed rather than plotted.' }),
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
        + laddered + ' resolve on an event and are listed as ladders, and '
        + unlisted + ' are not currently listed. '
        + notes.join(' ');
    }
  }
  return { init, xOf };
})();
