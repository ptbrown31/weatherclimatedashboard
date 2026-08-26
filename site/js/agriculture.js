/* The agriculture page: world crop yields as the USDA publishes them, with the
   listed thresholds drawn on the same axes.

   This is the climate page's panel, on a different set of series. The reason is
   not economy of code but that these contracts are the same kind of thing: one
   long annual record, a handful of thresholds above its recent level, and a
   question about where the trend lands. A reader who has learned to drag across
   the history on one page should not have to learn a second idiom here.

   The window is deliberately short. The record reaches to 1961, but the vertical
   scale is set by whatever is drawn, so every extra decade of low early yields
   squeezes the strikes — which sit within a few percent of each other — into a
   thinner and thinner band. Starting in 2015 gives the ladders a little over
   half the height on all three crops, against a third if the window opened in
   2005. The cost is that a trend can only be fitted across the years shown. */
window.WXAg = (() => {
  const { h, $ } = WXC;

  const CAT = 'catalogue/agriculture.json';
  const PROD = id => 'catalogue/product/' + id + '.json';
  const PRICE = id => 'catalogue/price/' + id + '.json';
  const SERIES = k => 'series/' + k + '.json';
  const SERIES_INDEX = 'series/index.json';
  // enough history to read a trend from, without flattening the ladders
  const X0 = 2015;

  // catalogue terms and catalogue prices arrive as two files and are joined the
  // way the contract pages join them, on the expiration and the strike
  function contracts(prod, price) {
    const priced = {};
    ((price && price.rows) || []).forEach(r => { priced[String(r.spec || '') + '|' + String(r.strike)] = r; });
    return (prod.contracts || []).map(c => {
      const q = priced[String(c.spec || '') + '|' + String(c.strike)] || {};
      const yr = parseInt(c.expiryLabel, 10);
      if (!isFinite(yr) || c.strike == null) return null;
      return {
        year: yr, threshold: c.strike, label: c.label || ('Above ' + c.strike),
        expiration: c.expiration, expiryLabel: c.expiryLabel,
        conidYes: c.conidYes, conid: c.conidYes,
        yes: q.mid != null ? q.mid : null,
        bid: q.bid != null ? q.bid : null, ask: q.ask != null ? q.ask : null,
        bidSize: q.bidSize, askSize: q.askSize, from: q.from,
      };
    }).filter(Boolean);
  }

  async function init() {
    const host = $('#panels'); const st = $('#pageStatus');
    host.innerHTML = '';
    const listing = await WXD.get(CAT, 1440);
    const idxRes = await WXD.get(SERIES_INDEX, 1440);
    if (st) { st.innerHTML = ''; st.appendChild(WXC.statusEl([listing, idxRes], 1440)); }
    const idx = idxRes.data || {};
    const products = ((listing.data || {}).products) || [];
    if (!products.length) {
      host.appendChild(h('p', { class: 'cap', text: 'No agriculture products are listed yet.' }));
      return;
    }
    await WXM.loadGroup('climate');   // the panel reads WXM for links and price labels

    const notes = [];
    let drawn = 0;
    for (const light of products) {
      const key = (idx.products || {})[light.id];
      // a product with no underlying series still deserves a line saying so,
      // rather than silently not appearing on its own page
      if (!key) {
        notes.push((light.name || light.id) + ': no published series to draw it against yet');
        continue;
      }
      if (light.state !== 'listed') {
        notes.push((light.name || light.id) + ': not currently listed');
        continue;
      }
      const [pRes, qRes, sRes] = await Promise.all([
        WXD.get(PROD(light.id), 1440), WXD.get(PRICE(light.id), 30), WXD.get(SERIES(key), 1440)]);
      const sr = sRes.data;
      if (!sr || !(sr.points || []).length) {
        notes.push((light.name || light.id) + ': the series has not been published yet');
        continue;
      }
      // the series carries years as strings; the panel plots numbers
      const ser = sr.points.map(q => [parseFloat(q[0]), q[1]]).filter(q => isFinite(q[0]) && q[1] != null);
      const prod = pRes.data || {};
      const cs = contracts(prod, qRes.data);
      const product = { name: prod.name || light.name || light.id,
                        productConid: prod.productConid, contracts: cs };
      WXClimate.panel(host, key, sr.title || product.name, sr.units || '', ser, product, 0,
                      sr.source || '', {
                        x0: X0,
                        // yields are published to two decimals and the strikes are
                        // set on the same grid
                        fmtThreshold: v => Number(v).toFixed(2),
                        thresholdSuffix: '',
                        xLabel: 'Year',
                        // a dozen strikes to a year: size the dots to their spacing
                        markerRadius: 'auto',
                        trendNote: 'byYear',
                      });
      if (sr.note) notes.push((sr.title || product.name) + ': ' + sr.note);
      drawn++;
    }
    if (!drawn) host.appendChild(h('p', { class: 'cap', text: 'No agriculture series are available to draw yet.' }));
    const foot = $('#foot');
    if (foot) {
      foot.textContent = 'Series: USDA Foreign Agricultural Service, Production Supply and Distribution. '
        + (WXM.on() ? (WXM.live()
            ? 'Markers are the exchange\'s listed contracts at the Yes midpoint, coloured by price; click one to open it on IBKR. '
            : 'Markers are placeholders, not market values. ') : '')
        + notes.join('. ') + (notes.length ? '.' : '');
    }
  }
  return { init };
})();
