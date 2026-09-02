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
  // the newest thing the vendor has said about the storm: a cycle, or the
  // interim, which can land days after the last cycle and keeps the storm
  // in play until the final resolves it
  function stampOf(s) {
    const lc = (s && s.livecyc) || {};
    const ts = [Date.parse(lc.lastModified || lc.forecastTime || ''), Date.parse(((s && s.interim) || {}).lastModified || '')]
      .filter(isFinite);
    return ts.length ? Math.max.apply(null, ts) : null;
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

  /* The vendor's mark, inside each plot built on its numbers: small, muted,
     drawn before the data so every line and label sits over it and nothing
     is displaced. The internal dashboards carry the vendor's raster badge in
     their page header; a chart corner takes a quiet vector line instead. */
  function reaskMark(svg, x, y, anchor) {
    svg.appendChild(txt('POWERED BY REASK', { x, y, 'text-anchor': anchor || 'end', 'font-size': 7.5,
      'letter-spacing': '0.12em', fill: 'var(--muted)', opacity: 0.5, 'pointer-events': 'none' }));
  }

  // a colour per threshold, cold to hot across the ladder
  const rung = (i, n) => 'hsl(' + Math.round(210 - 210 * (i / Math.max(1, n - 1))) + ' 70% 45%)';
  const stormCode = n => String(n || '').replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase();

  /* The pool figure per location NAME for one storm, when there is one to
     show. The site computes none of its own: the pool's calculation is the
     desk's and reaches the exchange through the market maker, so the page
     shows the exchange's price. A storm the owner has ruled on carries the
     ruling's figure, and that is the only figure drawn. */
  function calcByName(stormName) {
    const s = ((RK && RK.storms) || []).find(x => x.name === stormName);
    const lc = s && s.livecyc;
    if (!lc || !lc.pwin || lc.pwinMethod !== 'override') return null;
    const out = {};
    // a candidate the interim settled high can be absent from the newest
    // cycle, so its name is looked for on the interim's rows as well
    const im = (s.interim && s.interim.sites) || {};
    Object.keys(lc.pwin).forEach(sid => {
      const nm = (lc.sites && lc.sites[sid] && lc.sites[sid].name) || (im[sid] && im[sid].name) || sid;
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

  // A delivery stamp on the Eastern clock, the one the exchange's own day runs
  // on: "1:44a". Time only; the cycle rows beside it carry the date.
  const ETFMT = (typeof Intl !== 'undefined' && Intl.DateTimeFormat)
    ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true })
    : null;
  function etTime(iso) {
    const t = Date.parse(iso || '');
    if (!isFinite(t) || !ETFMT) return '';
    try { return ETFMT.format(t).toLowerCase().replace(' ', '').replace(/m$/, ''); } catch (e) { return ''; }
  }

  // the storm's index entry, which knows whether it is still delivering; a
  // caller holding only a name and a year gets the same answer through it
  function stormEntry(storm) {
    return ((RK && RK.storms) || []).find(s => s.name === storm.name && String(s.year) === String(storm.year)) || storm;
  }

  // ---- the deliveries, in the order they arrived
  //
  // The ledger files the interim after every cycle whatever the clock said,
  // and the page reads time left to right, so a cycle that landed after the
  // interim belongs after it here. The moment a file was recorded orders it,
  // the vendor's stamp stands in where there is none, and steps with neither
  // go last, in the order the ledger holds them. The final is not a delivery
  // on this axis.
  const arrivedAt = s => {
    const t = [s && s.ts, s && s.at].map(v => Date.parse(v || '')).find(isFinite);
    return t == null ? Infinity : t;
  };
  function delivered(doc) {
    return ((doc && doc.steps) || []).filter(s => s.kind !== 'final')
      .map((s, i) => [s, i])
      .sort((a, b) => (arrivedAt(a[0]) - arrivedAt(b[0])) || (a[1] - b[1]))
      .map(x => x[0]);
  }

  // ---- the columns of a storm's axis
  //
  // Every delivery so far in the order it arrived, then what is still to
  // come: the next LiveCyc file while the storm is still delivering, the
  // Metryc interim until it has arrived, and the final settlement until the
  // contracts have resolved. The pending columns hold their place from the
  // first frame, so a chart never rescales when one of them lands; the column
  // fills in where it already was.
  function columns(doc, entry) {
    const steps = (doc && doc.steps) || [];
    const arrived = delivered(doc);
    const cyc = arrived.filter(s => s.kind === 'livecyc');
    const cols = arrived.map(s => ({ kind: s.kind, step: s }));
    const settled = !!(doc && doc.final);
    const interim = arrived.some(s => s.kind === 'interim');
    // the next cycle is promised only during the LiveCyc phase, while cycles
    // are still coming. Once the interim has landed the storm is waiting on
    // the final and nothing else; and the vendor stops issuing cycles once a
    // storm is done, so a storm whose cycles have gone stale gets no promise
    // either. A storm with no stamp at all is taken as still delivering.
    const lcStamp = entry && entry.livecyc ? Date.parse(entry.livecyc.lastModified || entry.livecyc.forecastTime || '') : NaN;
    const cycling = !isFinite(lcStamp) || (Date.now() - lcStamp) <= STALE_MS;
    if (!settled && !interim && !(entry && dormant(entry)) && cycling) {
      const last = cyc[cyc.length - 1];
      const m = last && /^(\d{4})(\d{2})(\d{2})(\d{2})$/.exec(String(last.id || ''));
      const hh = m ? (+m[4] + 6) % 24 : null;
      cols.push({ kind: 'next', cycle: hh == null ? '' : (hh < 10 ? '0' : '') + hh + 'Z' });
    }
    if (!settled && !interim) cols.push({ kind: 'interim-ph' });
    if (settled) cols.push({ kind: 'final', step: steps.find(s => s.kind === 'final') || { id: 'FINAL', kind: 'final' } });
    else cols.push({ kind: 'final-ph' });
    return cols;
  }

  // The delivery axis, drawn the same way wherever the vendor's deliveries are
  // the x axis: the NHC cycle each file is built on in UTC, the moment the file
  // arrived on the Eastern clock, and the date where the day turns over. A
  // column still to come is ticked dashed and labelled for what it waits on.
  // Cycle labels thin rather than overlap, so a storm that runs a week keeps
  // an axis that can still be read; the pending columns always show. What
  // the rows are is said once, in the section's key, because a heading on
  // the chart itself has nowhere to sit that the end columns cannot reach.
  function deliveryAxis(svg, cols, x, B, size) {
    const n = cols.length;
    if (!n) return;
    const room = Math.max(2, Math.floor((x(n - 1) - x(0)) / (size * 5.2)));
    const every = Math.max(1, Math.ceil(n / room));
    let day = '';
    cols.forEach((c, k) => {
      const s = c.step || {};
      const m = String(s.id || '').match(/^(\d{4})(\d{2})(\d{2})(\d{2})$/);
      const pending = !c.step;
      const show = pending || !m || k % every === 0 || k === n - 1;
      svg.appendChild(el('line', { x1: x(k), x2: x(k), y1: B, y2: B + (show ? 4 : 2),
        stroke: 'var(--rule)', 'stroke-width': 1, 'stroke-dasharray': pending ? '2 2' : null }));
      let r1 = '', r2 = '';
      if (m) { r1 = m[4] + 'Z'; r2 = etTime(s.ts) || etTime(s.at); }
      else if (c.kind === 'next') { r1 = c.cycle || 'next'; r2 = 'next'; }
      else if (c.kind === 'interim' || c.kind === 'interim-ph') { r1 = 'Metryc'; r2 = 'Interim'; }
      else if (c.kind === 'final' || c.kind === 'final-ph') { r1 = 'Metryc'; r2 = 'final'; }
      const op = pending ? 0.55 : 1;
      // row one: the NHC cycle the file is built on, in UTC
      if (show && r1) svg.appendChild(txt(r1, { x: x(k), y: B + 4 + size, 'text-anchor': 'middle',
        class: 'axl', 'font-size': size, opacity: op }));
      // row two: when the file itself arrived, on the Eastern clock
      if (show && r2) svg.appendChild(txt(r2, { x: x(k), y: B + 5 + size * 2.15,
        'text-anchor': 'middle', class: 'axl', 'font-size': size * 0.9, opacity: 0.75 * op }));
      if (!m) return;
      // row three: the cycle's date, only where the day turns over
      const d = m[2] + '/' + m[3];
      if (d !== day) {
        if (show) svg.appendChild(txt(d, { x: x(k), y: B + 6 + size * 3.3, 'text-anchor': 'middle',
          class: 'axl', 'font-size': size, opacity: .75 }));
        day = d;
      }
    });
  }

  // ---- which series stands forward
  //
  // Both series are always drawn; the choice is which one is read first, and
  // the exchange's price is the default. One state for the whole section, so
  // a click restyles every chart at once. Each chart opened to fill the window
  // carries its own copy of the switch, because the section's is behind it then.
  let EMPH = 'exchange';
  function setEmph(mode) {
    EMPH = mode === 'livecyc' ? 'livecyc' : 'exchange';
    document.querySelectorAll('svg.scard, svg.lhlserie').forEach(s => {
      s.classList.toggle('emph-livecyc', EMPH === 'livecyc');
      s.classList.toggle('emph-exchange', EMPH === 'exchange');
    });
    document.querySelectorAll('.emphtog button').forEach(b => b.classList.toggle('on', b.dataset.mode === EMPH));
  }
  const NOTE = 'Each LiveCyc file looks forward from its own cycle and gives the chance that a gust above '
    + 'the strike is still to come. The contract pays on the highest gust over the whole storm, so the '
    + 'exchange prices what has already been recorded together with what is still ahead. Before the storm '
    + 'reaches a location the two run close. Once the strongest winds have passed, LiveCyc falls toward '
    + 'zero while the price keeps what has already happened, so a gap between the lines there is expected '
    + 'rather than a disagreement.';
  function controls() {
    const tog = h('div', { class: 'emphtog', role: 'group', 'aria-label': 'which series stands forward' });
    [['exchange', 'Exchange price'], ['livecyc', 'LiveCyc']].forEach(([m, t]) => {
      const b = h('button', { class: m === EMPH ? 'on' : '', text: t, 'data-mode': m,
                              title: 'bring the ' + t + ' lines forward' });
      b.onclick = () => setEmph(m);
      tog.appendChild(b);
    });
    const note = h('div', { class: 'note emphnote' });
    note.innerHTML = '<b>LiveCyc looks forward. The price covers the whole storm.</b> ' + NOTE;
    return h('div', { class: 'emphrow' }, [
      h('div', { class: 'emphctl' }, [h('span', { class: 'emphl', text: 'Highlight' }), tog]),
      note]);
  }
  // the key: a colour per strike, and the two line styles
  function legend(thr, used) {
    const d = h('div', { class: 'slegend' });
    (thr || []).forEach((t, i) => {
      if (used && !used[t]) return;
      d.appendChild(h('span', {}, [h('i', { class: 'sw', style: 'background:' + rung(i, thr.length) }), '≥' + t]));
    });
    d.appendChild(h('span', { class: 'kk' }, [h('i', { class: 'kl' }), 'exchange price, the Yes midpoint']));
    d.appendChild(h('span', { class: 'kk' }, [h('i', { class: 'kl dash' }), 'LiveCyc, and the Metryc interim once it lands, as published']));
    d.appendChild(h('span', { class: 'kk', text: 'axis rows, the NHC cycle over the file’s arrival in ET, dated where the day turns' }));
    return d;
  }
  // the hover box for one column: the strikes down the side, the series across
  function colTip(title, sub, head, rows, foot) {
    const cell = v => '<td>' + (v == null ? '—' : v) + '</td>';
    return '<b>' + title + '</b>' + (sub ? '<div class="tf" style="margin:0 0 2px">' + sub + '</div>' : '')
      + '<table class="l3"><tr>' + head.map(v => '<th>' + v + '</th>').join('') + '</tr>'
      + rows.map(r => '<tr>' + r.map(cell).join('') + '</tr>').join('') + '</table>'
      + (foot ? '<div class="tf">' + foot + '</div>' : '');
  }

  // ---- one location's card: the two series through the deliveries.
  //
  // A dashed line per strike is the vendor's LiveCyc probability as published,
  // a solid line in the same colour the exchange's Yes price for the same
  // contract, both on the delivery axis with the pending columns held open at
  // the end. Returns the node, a setCursor so scrubbing moves a line and a few
  // marks rather than rebuilding every card, and the strikes it drew.
  function card(doc, sid, storm, gp) {
    const meta = doc.sites[sid] || {};
    const thr = doc.thresholds || [];
    const cyc = delivered(doc);                                          // what the timeline scrubs
    const cols = columns(doc, stormEntry(storm));
    const fin = doc.final || null;
    // a storm under the owner's ruling carries its recorded prices and no live quote
    const ruled = doc.pricesFrom === 'override';
    const listed = lMarket(storm.name, sid);
    const mk = ruled ? null : listed;
    // a click anywhere on the chart opens the location's contract, when one is listed
    const url = listed ? WXM.contractUrl(listed.productConid, firstYes(listed)) : null;
    const W = 470, H = 198, L = 34, R = 450, T = 16, B = 150;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'scard emph-' + EMPH });
    if (url) WXM.linkTo(svg.appendChild(el('rect', { class: 'plotlink', x: 0, y: 0, width: W, height: H, fill: 'transparent' })),
                        url, 'Open the ' + (meta.name || sid) + ' contract on IBKR');
    // the vendor's mark in the top left, above the plot, where no column or
    // shading can cover it
    reaskMark(svg, L + 2, T - 5, 'start');
    const N = Math.max(cols.length, 2);
    const x = i => L + (i / (N - 1)) * (R - L);
    const y = p => B - (p / 100) * (B - T);
    const wcol = (R - L) / (N - 1);
    const colOf = {};
    cols.forEach((c, k) => { if (c.step && c.step.id != null) colOf[c.step.id] = k; });
    const lastLc = cols.map((c, k) => (c.kind === 'livecyc' ? k : -1)).filter(k => k >= 0).pop();
    // the newest thing the vendor has said, cycle or interim: where the price
    // now belongs, and what the latest column is
    const newest = cols.map((c, k) => (c.step && c.kind !== 'final' ? k : -1)).filter(k => k >= 0).pop();
    const used = {};

    [0, 25, 50, 75, 100].forEach(p => {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(p), y2: y(p), class: 'grid' }));
      svg.appendChild(txt(p, { x: L - 5, y: y(p) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    });
    // what is still to come, shaded from the first pending column to the end
    const pend = cols.findIndex(c => !c.step);
    if (pend >= 0) svg.appendChild(el('rect', { class: 'pending', x: x(pend) - wcol / 2, y: T,
      width: x(cols.length - 1) - x(pend) + wcol / 2, height: B - T, fill: 'var(--shade)', 'pointer-events': 'none' }));
    deliveryAxis(svg, cols, x, B, 7.5);

    // the strikes worth a row anywhere on this card: a LiveCyc figure above
    // zero at some delivery, a recorded price, or a price now
    thr.forEach((t, i) => {
      cols.forEach(c => {
        if (!c.step) return;
        const arr = c.step.sites && c.step.sites[sid];
        if (arr && arr.length > i && arr[i] > 0) used[t] = true;
        if (((c.step.prices || {})[sid] || {})[String(t)] != null) used[t] = true;
      });
      if (mk && priceAt(mk, t) != null) used[t] = true;
    });

    // one hover band per delivered column, in before the lines so a mark on
    // top of a band still takes the pointer
    cols.forEach((c, k) => {
      if (!c.step) return;
      const s = c.step;
      const band = el('rect', { class: 'hband', x: x(k) - wcol / 2, y: T, width: Math.max(wcol, 6), height: B - T, fill: 'transparent' });
      bind(band, () => {
        const arr = (s.sites && s.sites[sid]) || [];
        const pr = (s.prices && s.prices[sid]) || {};
        const nowCol = k === newest && !!mk;
        // the figure in the second column is whichever file this column is:
        // a forecast cycle's LiveCyc, or the Metryc interim's folded ladder
        const head = ['strike', c.kind === 'interim' ? 'Metryc interim' : 'LiveCyc', 'exchange']
          .concat(nowCol ? ['now'] : []).concat(fin && fin[sid] != null ? ['settled'] : []);
        const rows = [];
        thr.forEach((t, i) => {
          if (!used[t]) return;
          // a zero the vendor printed is a figure and reads as one
          const v = arr.length > i && arr[i] != null ? arr[i] : null;
          const p = pr[String(t)];
          const r = ['≥' + t + ' mph', v == null ? null : v + '%', p == null ? null : Math.round(p) + '¢'];
          if (nowCol) { const live = priceAt(mk, t); r.push(live == null ? null : live + '¢'); }
          if (fin && fin[sid] != null) r.push(fin[sid] >= t ? '✓ Yes' : '✕ No');
          rows.push(r);
        });
        const et = etTime(s.ts) || etTime(s.at);
        const what = c.kind === 'final' ? 'final settlement' + (fin && fin[sid] != null ? ' · ' + fin[sid] + ' mph' : '')
          : c.kind === 'interim' ? 'the Metryc interim file'
              + (arr.length ? '' : ' · it carries no figure for this location')
          : 'delivery ' + (k + 1) + ' of ' + (lastLc + 1) + ' so far';
        return colTip(esc(meta.name || sid) + ' · ' + label(s) + (k === newest ? ' (latest)' : ''),
          et ? 'file ' + et + ' ET' : '', head,
          rows.length ? rows : [['—', 'nothing above zero', null]],
          what + (url ? ' · click to open the contract' : '') + ' · ' + esc((RK && RK.attribution) || 'Powered by Reask'));
      }, url);
      svg.appendChild(band);
    });

    // a missed cycle, marked between the two columns that sit either side of it.
    // The strip is narrower than the space between them so each delivery keeps
    // most of its own hover band, and it goes in before the lines so a mark
    // still takes the pointer ahead of it.
    (gp || []).forEach(g => {
      const a = cyc[g.after], b = cyc[g.after + 1];
      if (!a || !b || colOf[a.id] == null || colOf[b.id] == null) return;
      const mid = (x(colOf[a.id]) + x(colOf[b.id])) / 2;
      const strip = el('rect', { class: 'sgap', x: mid - wcol * 0.2, y: T, width: Math.max(wcol * 0.4, 5), height: B - T,
                                 fill: 'var(--rule)', opacity: .28, 'pointer-events': 'all' });
      bind(strip, () => tip.rows('A delivery is missing here',
        [['Between', label(a) + ' and ' + label(b)],
         ['Elapsed', g.hours + ' hours'],
         ['Cycles missing', String(g.missing)],
         ['Usual cadence', g.cadence + ' hours']],
        'the axis counts deliveries, so these two columns are further apart in time than the rest'));
      svg.appendChild(strip);
      const gl = el('g'); slashes(gl, mid, B, 9); svg.appendChild(gl);
    });

    // the vendor's LiveCyc ladder, dashed: one line per strike from the
    // delivery it first carried a figure on, through the interim if there is
    // one, and at settlement a snap to 100 or 0 for how the strike resolved
    const kf = cols.length - 1;
    let anyHit = false, anyMiss = false, ri = 0;
    thr.forEach((t, i) => {
      const col = rung(i, thr.length);
      const pts = [];
      cols.forEach((c, k) => {
        if (!c.step || c.kind === 'final') return;
        const arr = c.step.sites && c.step.sites[sid];
        const p = arr && arr.length > i ? arr[i] : null;
        if (p == null) return;
        if (!pts.length && p <= 0) return;
        pts.push([k, p]);
      });
      if (!pts.length) return;
      const d = pts.map((p, j) => (j ? 'L' : 'M') + x(p[0]).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('');
      svg.appendChild(el('path', { class: 'lcline', d: d, fill: 'none', stroke: col, 'stroke-width': 1.7,
                                   'stroke-dasharray': '4 3', 'pointer-events': 'none' }));
      // one point per delivery: the vendor issues these at set times and nothing
      // is measured between them, so each reading is marked rather than implied
      // a rung with a single reading, which a location the interim alone
      // names has, is still a reading and gets its dot
      if ((pts.length > 1 && wcol >= 5) || pts.length === 1) pts.forEach(v => svg.appendChild(el('circle',
        { class: 'ldot', cx: x(v[0]).toFixed(1), cy: y(v[1]).toFixed(1), r: Math.min(2.6, wcol / 4),
          fill: col, 'pointer-events': 'none' })));
      if (fin && fin[sid] != null) {
        const hit = fin[sid] >= t, last = pts[pts.length - 1];
        const xr = x(kf) - 4 + (ri % 5) * 2, yr = y(hit ? 100 : 0);
        svg.appendChild(el('path', { class: 'lcline', d: 'M' + x(last[0]).toFixed(1) + ',' + y(last[1]).toFixed(1)
          + 'L' + xr.toFixed(1) + ',' + yr.toFixed(1), fill: 'none', stroke: col, 'stroke-width': 1,
          'stroke-dasharray': '2 2', 'pointer-events': 'none' }));
        svg.appendChild(el('circle', { class: 'ldot', cx: xr.toFixed(1), cy: yr.toFixed(1), r: 2.6, fill: col, 'pointer-events': 'none' }));
        if (hit) anyHit = true; else anyMiss = true;
        ri++;
      }
    });
    if (anyHit) svg.appendChild(txt('✓', { x: x(kf) + 8, y: y(100) + 4, 'font-size': 10, fill: 'var(--yes)' }));
    if (anyMiss) svg.appendChild(txt('✕', { x: x(kf) + 8, y: y(0) + 1, 'font-size': 10, fill: 'var(--muted)' }));

    // the exchange's price, solid and in the same colour: one line per strike
    // through the price recorded with each delivery, a square at each reading
    thr.forEach((t, i) => {
      const col = rung(i, thr.length);
      const pp = [];
      cols.forEach((c, k) => {
        if (!c.step) return;
        const v = ((c.step.prices || {})[sid] || {})[String(t)];
        if (v != null) pp.push([k, v]);
      });
      if (pp.length > 1) svg.appendChild(el('path', { class: 'pxline',
        d: pp.map((q, j) => (j ? 'L' : 'M') + x(q[0]).toFixed(1) + ',' + y(q[1]).toFixed(1)).join(''),
        fill: 'none', stroke: col, 'stroke-width': 2.4, 'pointer-events': 'none' }));
      pp.forEach(q => svg.appendChild(el('rect', { class: 'pxdot', x: (x(q[0]) - 2.2).toFixed(1), y: (y(q[1]) - 2.2).toFixed(1),
        width: 4.4, height: 4.4, fill: col, 'pointer-events': 'none' })));
      // the price now is fresher than the one recorded when the newest delivery
      // landed: a hollow square beside that column, hollow to read as the market's
      const live = mk && newest != null ? priceAt(mk, t) : null;
      if (live != null) svg.appendChild(el('rect', { class: 'pxnow', x: x(newest) + 5, y: y(live) - 3.5, width: 7, height: 7,
        fill: 'transparent', stroke: col, 'stroke-width': 1.6, 'pointer-events': 'none' }));
    });

    // the cursor: a line at the delivery being read and a dot on each series there
    const cline = el('line', { class: 'scur', y1: T, y2: B, stroke: 'var(--ink)', 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: .6, 'pointer-events': 'none' });
    svg.appendChild(cline);
    const marks = el('g');
    svg.appendChild(marks);
    function setCursor(ti) {
      const s = cyc[Math.max(0, Math.min(ti, cyc.length - 1))] || {};
      const k = colOf[s.id];
      if (k == null) return;
      cline.setAttribute('x1', x(k)); cline.setAttribute('x2', x(k));
      marks.innerHTML = '';
      const arr = (s.sites && s.sites[sid]) || [];
      const pr = (s.prices && s.prices[sid]) || {};
      thr.forEach((t, i) => {
        if (!used[t]) return;
        const col = rung(i, thr.length);
        const v = arr.length > i ? arr[i] : null;
        // a forecast at zero is drawn as nothing; an interim at zero is a published figure
        if (v != null && (v > 0 || s.kind === 'interim')) marks.appendChild(el('circle', { cx: x(k), cy: y(v), r: 2.8, fill: col, 'pointer-events': 'none' }));
        const p = pr[String(t)];
        if (p != null) marks.appendChild(el('rect', { x: x(k) - 3.2, y: y(p) - 3.2, width: 6.4, height: 6.4, fill: 'var(--panel)',
          stroke: col, 'stroke-width': 1.6, 'pointer-events': 'none' }));
      });
    }
    if (fin && fin[sid] != null) svg.appendChild(txt(fin[sid] + ' mph', { x: x(kf) - 6, y: B - 3, 'text-anchor': 'end',
      'font-size': 9.5, 'font-weight': 700, fill: 'var(--ink)' }));

    const wrap = h('div', { class: 'scardwrap', 'data-sid': sid });
    // the key and the switch travel with the card when it fills the window
    wrap.appendChild(h('div', { class: 'xhdr' }, [controls(), legend(thr, used)]));
    const title = h('div', { class: 'lth' }, [h('div', { class: 'lt', text: (meta.name || sid) + ' (' + sid + ')' })]);
    title.appendChild(WXC.expander(wrap, 'Expand'));
    wrap.appendChild(title);
    if (!ruled) wrap.appendChild(h('div', { class: 'cap', style: 'margin:0 0 4px',
      text: listed ? 'contracts listed · ' + listed.symbol : 'no contracts listed for this location yet' }));
    else if (listed) wrap.appendChild(h('div', { class: 'cap', style: 'margin:0 0 4px', text: listed.symbol }));
    wrap.appendChild(svg);
    return { node: wrap, setCursor, used };
  }

  const label = s => (s.kind === 'interim' ? 'Metryc interim' : s.kind === 'final' ? 'final settlement' : String(s.id || '').replace(/^(\d{4})(\d{2})(\d{2})(\d{2})$/, '$2/$3 $4Z'));

  // a hover box on the node; a click follows the node to its contract when it
  // has one, and pins the box when it has not
  function bind(node, html, url) {
    node.addEventListener('mousemove', e => { e.stopPropagation(); tip.show(e, html()); });
    node.addEventListener('mouseleave', () => tip.hide());
    if (url) return WXM.linkTo(node, url, 'Open the contract on IBKR');
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

  // ---- the pool contracts: candidate locations as the strikes.
  // The same bars every other contract on the site gets: Yes green from the
  // left, No red to the right, the two summing to a dollar, rows ordered by
  // whoever stands highest. The dashed tick on a row is the raw calculation.
  function pools(storm, skip) {
    const out = [];
    poolMarkets(storm.name).filter(m => !(skip || []).includes(m.symbol)).forEach(m => {
      const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: (m.name || m.symbol) + ' (' + m.symbol + ')' }),
        h('div', { class: 'cap', style: 'margin:0 0 6px', text: 'Which of these locations records the highest wind. The candidates are the strikes and the pool is fixed when it is opened.' })]);
      const calc = calcByName(storm.name);
      const cw = c => (calc && calc[c.label || String(c.strike)]) != null ? calc[c.label || String(c.strike)] : null;
      const rows = (m.contracts || []).slice().sort((a, b) =>
        ((b.mid == null ? -1 : b.mid) - (a.mid == null ? -1 : a.mid))
        || ((cw(b) || 0) - (cw(a) || 0)));
      if (!rows.length) { div.appendChild(h('div', { class: 'cap', text: 'No candidates listed yet.' })); out.push(div); return; }
      const W2 = 420, T2 = 6, rowH = 26, LX = 8, RX = 412;
      const H2 = T2 + rows.length * rowH + 30;
      const svg = el('svg', { viewBox: '0 0 ' + W2 + ' ' + H2, class: 'plad', style: 'width:100%;height:auto' });
      const px = q => LX + Math.max(0, Math.min(1, q)) * (RX - LX);
      rows.forEach((c, i) => {
        const nm = c.label || String(c.strike);
        const yTop = T2 + i * rowH, bh = rowH - 8, by = yTop + 3;
        const g = el('g', { class: 'prow' });
        const v = c.mid == null ? null : cents(c.mid);
        const cv = cw(c);
        if (v != null) {
          const split = px(c.mid);
          g.appendChild(el('rect', { x: LX, y: by, width: Math.max(split - LX, 1), height: bh, rx: 2, fill: 'var(--yes)', opacity: 0.85 }));
          g.appendChild(el('rect', { x: split, y: by, width: Math.max(RX - split, 1), height: bh, rx: 2, fill: 'var(--no)', opacity: 0.85 }));
          g.appendChild(txt(v + '¢', { x: Math.max(split - 4, LX + 22), y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                       'font-size': 10, 'font-weight': 700, fill: '#fff' }));
          g.appendChild(txt((100 - v) + '¢', { x: RX - 4, y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                               'font-size': 10, 'font-weight': 700, fill: '#fff' }));
        } else {
          g.appendChild(el('rect', { x: LX, y: by, width: RX - LX, height: bh, rx: 2, fill: 'none',
                                     stroke: 'var(--rule)', 'stroke-dasharray': '3 3' }));
          g.appendChild(txt('no bids', { x: RX - 4, y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                         'font-size': 9.5, fill: 'var(--muted)' }));
        }
        g.appendChild(txt(nm, { x: LX + 4, y: by + bh / 2 + 3.5, 'font-size': 10, 'font-weight': 700,
                                fill: v != null ? '#fff' : 'var(--ink)' }));
        if (cv != null) g.appendChild(el('line', { class: 'calcmk', x1: px(cv / 100), x2: px(cv / 100),
          y1: by - 1.5, y2: by + bh + 1.5, stroke: 'var(--ink)', 'stroke-width': 1.6, 'stroke-dasharray': '2 2' }));
        const url = WXM.contractUrl(m.productConid, c.conidYes || c.conid);
        bind(g, () => tip.rows((m.name || m.symbol) + ' — ' + esc(nm),
          [['Yes price', v == null ? 'no bids' : v + '¢'],
           ['Yes bid', c.bid == null ? '—' : cents(c.bid) + '¢'],
           ['No bid', c.ask == null ? '—' : (100 - cents(c.ask)) + '¢'],
           ['Buy Yes now at', c.ask == null ? null : cents(c.ask) + '¢' + (WXM.payoutText(cents(c.ask)) ? ' · pays ' + WXM.payoutText(cents(c.ask)) : '')],
           ['Calculation', cv == null ? null : cv + '%'],
          ],
          'settles on the vendor’s final peak gusts' + (url ? ' · click to open the contract' : '')));
        if (url) {
          const link = el('rect', { x: LX, y: yTop, width: RX - LX, height: rowH, fill: 'transparent' });
          WXM.linkTo(link, url, 'Open ' + nm + ' on IBKR');
          g.appendChild(link);
        }
        svg.appendChild(g);
      });
      [0, 25, 50, 75, 100].forEach(cc => svg.appendChild(txt(String(cc), { x: px(cc / 100),
        y: T2 + rows.length * rowH + 12, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 })));
      svg.appendChild(txt('Yes green, No red · ¢' + (calc ? ' · dashed tick: the calculation' : ''), { x: (LX + RX) / 2,
        y: H2 - 4, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 }));
      div.appendChild(svg);
      out.push(div);
    });
    return out;
  }

  // ---- the pool's panel: the chart on the left, the market's ladder on the
  // right, the same shape as the count contracts. In the chart the exchange's
  // price is solid, one column per delivery on the same axis as the location
  // cards, with the price drawn continuously between columns at the pace it
  // actually moved, because the market trades between deliveries. The site
  // draws no calculation of its own behind it; a storm the owner has ruled
  // on carries the ruling's figure, dashed, and that is the only case.
  async function poolSeries(m, ledger) {
    const cyc = delivered(ledger).filter(s => s.kind === 'livecyc' || s.kind === 'interim');
    if (!cyc.length) return null;
    const ruledCalc = !!(ledger && ledger.pricesFrom === 'override');
    // the ruling's figure at each delivery, under the location's name
    const calc = cyc.map(s => {
      const by = {};
      Object.keys((ruledCalc && s.pwin) || {}).forEach(sid => {
        by[(s.siteMeta && s.siteMeta[sid] && s.siteMeta[sid].name) || sid] = s.pwin[sid];
      });
      return by;
    });
    let doc = null;
    try { doc = (await WXD.get('lhl/' + m.symbol + '.json', 10)).data; } catch (e) { doc = null; }
    // a pool under the owner's ruling: the series is the ruling's points, one
    // per delivery, and the book now is the last of them
    const ruled = !!(doc && doc.override);
    // A recorded price is both sides of the book. Older points hold a single
    // midpoint, which is read as a book of no width rather than dropped.
    const book = v => {
      if (v == null) return null;
      const a = Array.isArray(v) ? v : [v, v];
      if (a[0] == null && a[1] == null) return null;
      const lo = a[0] == null ? a[1] : a[0], hi = a[1] == null ? a[0] : a[1];
      return { bid: lo, ask: hi, mid: Math.round((lo + hi) * 5) / 10 };
    };
    const rec = ((doc && doc.points) || []).map(q => ({ t: Date.parse(q.t), p: q.p || {} }))
      .filter(q => isFinite(q.t));
    const now = {};
    if (ruled) {
      const lp = rec.length ? rec[rec.length - 1].p : {};
      Object.keys(lp).forEach(nm => { const b = book(lp[nm]); if (b) now[nm] = b; });
    } else {
      (m.contracts || []).forEach(c => {
        if (!c.label) return;
        const b = book([c.bid == null ? null : Math.round(c.bid * 1000) / 10,
                        c.ask == null ? null : Math.round(c.ask * 1000) / 10]);
        if (b) now[c.label] = b;
      });
    }
    // the ladder's tick is the index's current figure, the same fold the
    // newest delivery carries and the one a ruling governs; failing that,
    // the newest delivery that carried a figure at all
    const latestCalc = calcByName(ledger.name) || calc.slice().reverse().find(by => Object.keys(by).length) || {};
    const names = (m.contracts || []).map(c => c.label).filter(Boolean)
      .sort((a, b) => (((now[b] || {}).mid || 0) - ((now[a] || {}).mid || 0))
                   || ((latestCalc[b] || 0) - (latestCalc[a] || 0))).slice(0, 8);
    if (!names.length) return null;
    const colOf = i => rung(names.length - 1 - i, Math.max(2, names.length));

    const W = 960, Hh = 250, L = 46, R = 600, T = 26, B = 186;
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + Hh, class: 'lhlserie emph-' + EMPH });
    // a click anywhere on the chart opens the pool at the location standing highest
    const top = (m.contracts || []).find(cc => (cc.label || String(cc.strike)) === names[0]) || {};
    const url = WXM.contractUrl(m.productConid, top.conidYes || top.conid);
    if (url) WXM.linkTo(svg.appendChild(el('rect', { class: 'plotlink', x: 0, y: 0, width: R + 12, height: Hh, fill: 'transparent' })),
                        url, 'Open the ' + (m.name || m.symbol) + ' contract on IBKR');
    // the vendor's mark in the top left, between the legend line and the plot
    reaskMark(svg, L + 2, T - 4, 'start');
    // the same columns as the location cards, so the two read against each
    // other; a delivery before the calculation existed has a column and no dot
    const cols = columns(ledger, stormEntry({ name: ledger.name, year: ledger.year }));
    const N = Math.max(cols.length, 2);
    const x = i => L + (i / (N - 1)) * (R - L);
    const Y = p => B - (p / 100) * (B - T);
    const wcol = (R - L) / (N - 1);
    const kcol = cyc.map(s => cols.findIndex(c => c.step && c.step.id === s.id));
    const X = k => x(kcol[k]);
    [0, 25, 50, 75, 100].forEach(p => {
      svg.appendChild(el('line', { x1: L, x2: R, y1: Y(p), y2: Y(p), class: 'grid' }));
      svg.appendChild(txt(p + '%', { x: L - 5, y: Y(p) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    });
    const pend = cols.findIndex(c => !c.step);
    if (pend >= 0) svg.appendChild(el('rect', { class: 'pending', x: x(pend) - wcol / 2, y: T,
      width: x(cols.length - 1) - x(pend) + wcol / 2, height: B - T, fill: 'var(--shade)', 'pointer-events': 'none' }));
    deliveryAxis(svg, cols, x, B, 9);
    // the legend, inside the frame, with what the axis rows are
    svg.appendChild(el('line', { x1: L, x2: L + 20, y1: 11, y2: 11, stroke: 'var(--ink)', 'stroke-width': 2.4 }));
    svg.appendChild(txt('exchange price', { x: L + 25, y: 14, class: 'ax' }));
    if (ruledCalc) {
      svg.appendChild(el('line', { x1: L + 110, x2: L + 130, y1: 11, y2: 11, stroke: 'var(--ink)',
        'stroke-width': 1.7, 'stroke-dasharray': '5 4' }));
      svg.appendChild(txt('calculation', { x: L + 135, y: 14, class: 'ax' }));
    }
    svg.appendChild(txt('axis rows, the NHC cycle over the file’s arrival in ET', { x: R, y: 14, 'text-anchor': 'end',
      class: 'ax', opacity: .75 }));

    // time anchors: a delivery sits at its column, and the price walks between
    // columns at the pace it actually moved. Past the newest delivery it walks
    // on toward the next column at the vendor's six-hour cadence, so a quote
    // three hours after the last file sits halfway to where the next file will
    // land. Quotes from before the first delivery have no column and are not
    // drawn.
    const anchors = cyc.map(s => [s.ts, s.at].map(v => Date.parse(v || '')).find(isFinite));
    const nextK = cols.findIndex(c => c.kind === 'next');
    const CADENCE = 6 * 3600000;
    const tx = t => {
      if (anchors.some(a => a == null) || t < anchors[0]) return null;
      for (let k = 0; k + 1 < anchors.length; k++)
        if (t <= anchors[k + 1])
          return X(k) + (t - anchors[k]) / Math.max(anchors[k + 1] - anchors[k], 1) * (X(k + 1) - X(k));
      const lastX = X(cyc.length - 1);
      if (nextK < 0) return lastX;
      return lastX + Math.min(1, Math.max(0, (t - anchors[anchors.length - 1]) / CADENCE)) * (x(nextK) - lastX);
    };

    // a hover band per delivery
    const NEAR = 45 * 60000;
    const atDelivery = k => {
      const t = anchors[k];
      if (t == null || !rec.length) return {};
      let best = rec[0];
      rec.forEach(q => { if (Math.abs(q.t - t) < Math.abs(best.t - t)) best = q; });
      return Math.abs(best.t - t) <= NEAR ? best.p : {};
    };
    cyc.forEach((s, k) => {
      const band = el('rect', { class: 'hband', x: X(k) - wcol / 2, y: T,
        width: Math.max(wcol, 6), height: B - T, fill: 'transparent' });
      bind(band, () => {
        const pr = atDelivery(k);
        const head = ruledCalc ? ['location', 'calculation', 'exchange'] : ['location', 'exchange'];
        const rows = names.map(nm => {
          const b = book(pr[nm]);
          const px = b == null ? null : (ruled || b.bid === b.ask ? Math.round(b.mid) + '¢'
                                         : Math.round(b.mid) + '¢ (Yes bid ' + b.bid + ', No bid ' + Math.round(100 - b.ask) + ')');
          return ruledCalc ? [esc(nm), calc[k][nm] != null ? calc[k][nm] + '%' : null, px] : [esc(nm), px];
        });
        const et = etTime(s.ts) || etTime(s.at);
        return colTip(label(s) + (k === cyc.length - 1 ? ' (latest)' : ''), et ? 'file ' + et + ' ET' : '',
          head, rows, 'delivery ' + (k + 1) + ' of ' + cyc.length + (url ? ' · click to open the contract' : '')
          + ' · ' + esc((RK && RK.attribution) || 'Powered by Reask'));
      }, url);
      svg.appendChild(band);
    });

    names.forEach((nm, i) => {
      const col = colOf(i);
      // the raw calculation, dashed, a dot at each delivery it was made
      const line = [];
      calc.forEach((by, k) => { if (by[nm] != null) line.push([k, by[nm]]); });
      if (line.length > 1) svg.appendChild(el('path', { class: 'calcline',
        d: line.map((q, j) => (j ? 'L' : 'M') + X(q[0]).toFixed(1) + ' ' + Y(q[1]).toFixed(1)).join(''),
        fill: 'none', stroke: col, 'stroke-width': 1.7, 'stroke-dasharray': '5 4',
        'pointer-events': 'none' }));
      line.forEach(q => svg.appendChild(el('circle', { class: 'cdot', cx: X(q[0]).toFixed(1),
        cy: Y(q[1]).toFixed(1), r: 2.4, fill: col, 'pointer-events': 'none' })));
      // the exchange's price, solid and in front: the spread as a band, the
      // midpoint as the line
      const seq = rec.map(q => ({ t: q.t, b: book(q.p[nm]), X: tx(q.t) }))
        .filter(z => z.b && z.X != null);
      if (seq.length > 1) {
        svg.appendChild(el('path', { class: 'pxband',
          d: seq.map((z, j) => (j ? 'L' : 'M') + z.X.toFixed(1) + ' ' + Y(z.b.ask).toFixed(1)).join('')
           + seq.slice().reverse().map(z => 'L' + z.X.toFixed(1) + ' ' + Y(z.b.bid).toFixed(1)).join('') + 'Z',
          fill: col, stroke: 'none', 'pointer-events': 'none' }));
        svg.appendChild(el('path', { class: 'pxline',
          d: seq.map((z, j) => (j ? 'L' : 'M') + z.X.toFixed(1) + ' ' + Y(z.b.mid).toFixed(1)).join(''),
          fill: 'none', stroke: col, 'stroke-width': 2.4, 'pointer-events': 'none' }));
      }
      if (seq.length) {
        const zl = seq[seq.length - 1];
        svg.appendChild(el('circle', { class: 'pxdot', cx: zl.X.toFixed(1), cy: Y(zl.b.mid).toFixed(1), r: 3,
          fill: col, 'pointer-events': 'none' }));
      }
    });

    // ---- the market's ladder, to the right of the chart: the same bars as
    // the count contracts, rows in the chart's own order and colours
    const LX2 = 636, RX2 = 946, LT = 20, rowH = 26;
    const lad = el('g', { class: 'plad' });
    svg.appendChild(lad);
    lad.appendChild(txt('The market’s ladder', { x: LX2, y: 13, 'font-size': 11.5, 'font-weight': 700, fill: 'var(--navy)' }));
    const px2 = q => LX2 + Math.max(0, Math.min(1, q / 100)) * (RX2 - LX2);
    names.forEach((nm, i) => {
      const c = (m.contracts || []).find(cc => (cc.label || String(cc.strike)) === nm) || {};
      const col = colOf(i);
      const yTop = LT + i * rowH, bh = rowH - 8, by = yTop + 3;
      const g = el('g', { class: 'prow' });
      const b = now[nm] || null;
      const v = b ? Math.round(b.mid) : null;
      const cv = latestCalc[nm] != null ? latestCalc[nm] : null;
      if (v != null) {
        const split = px2(v);
        g.appendChild(el('rect', { x: LX2, y: by, width: Math.max(split - LX2, 1), height: bh, rx: 2, fill: 'var(--yes)', opacity: 0.85 }));
        g.appendChild(el('rect', { x: split, y: by, width: Math.max(RX2 - split, 1), height: bh, rx: 2, fill: 'var(--no)', opacity: 0.85 }));
        g.appendChild(txt(v + '¢', { x: Math.max(split - 4, LX2 + 22), y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                          'font-size': 10, 'font-weight': 700, fill: '#fff' }));
        g.appendChild(txt((100 - v) + '¢', { x: RX2 - 4, y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                                  'font-size': 10, 'font-weight': 700, fill: '#fff' }));
      } else {
        g.appendChild(el('rect', { x: LX2, y: by, width: RX2 - LX2, height: bh, rx: 2, fill: 'none',
                                   stroke: 'var(--rule)', 'stroke-dasharray': '3 3' }));
        g.appendChild(txt('no bids', { x: RX2 - 4, y: by + bh / 2 + 3.5, 'text-anchor': 'end',
                                       'font-size': 9.5, fill: 'var(--muted)' }));
      }
      // the dot ties the row to its line in the chart
      g.appendChild(el('circle', { cx: LX2 - 9, cy: by + bh / 2, r: 3, fill: col }));
      g.appendChild(txt(nm, { x: LX2 + 4, y: by + bh / 2 + 3.5, 'font-size': 10, 'font-weight': 700,
                              fill: v != null ? '#fff' : 'var(--ink)' }));
      if (cv != null) g.appendChild(el('line', { class: 'calcmk', x1: px2(cv), x2: px2(cv),
        y1: by - 1.5, y2: by + bh + 1.5, stroke: 'var(--ink)', 'stroke-width': 1.6, 'stroke-dasharray': '2 2' }));
      const url = WXM.contractUrl(m.productConid, c.conidYes || c.conid);
      bind(g, () => tip.rows((m.name || m.symbol) + ' — ' + esc(nm),
        ruled
          ? [['Yes price', b == null ? 'no bids' : v + '¢'], ['Calculation', cv == null ? null : cv + '%']]
          : [['Yes price', b == null ? 'no bids' : v + '¢'],
             ['Yes bid', c.bid == null ? '—' : cents(c.bid) + '¢'],
             ['No bid', c.ask == null ? '—' : (100 - cents(c.ask)) + '¢'],
             ['Buy Yes now at', c.ask == null ? null : cents(c.ask) + '¢' + (WXM.payoutText(cents(c.ask)) ? ' · pays ' + WXM.payoutText(cents(c.ask)) : '')],
             ['Calculation', cv == null ? null : cv + '%']],
        'settles on the vendor’s final peak gusts' + (url ? ' · click to open the contract' : '')));
      if (url) {
        const link = el('rect', { x: LX2 - 14, y: yTop, width: RX2 - LX2 + 14, height: rowH, fill: 'transparent' });
        WXM.linkTo(link, url, 'Open ' + nm + ' on IBKR');
        g.appendChild(link);
      }
      lad.appendChild(g);
    });
    [0, 25, 50, 75, 100].forEach(cc => lad.appendChild(txt(String(cc), { x: px2(cc),
      y: LT + names.length * rowH + 12, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 })));
    lad.appendChild(txt('Yes green, No red · ¢' + (ruledCalc ? ' · dashed tick: the calculation' : ''), { x: (LX2 + RX2) / 2,
      y: LT + names.length * rowH + 24, 'text-anchor': 'middle', class: 'ax', 'font-size': 9 }));

    const wrap = h('div', { class: 'cwrap', style: 'margin:12px 0 0' });
    wrap.appendChild(h('div', { class: 'xhdr' }, [controls()]));
    const title = h('div', { class: 'lth' }, [h('div', { class: 'lt', text: (m.name || m.symbol) + ' (' + m.symbol + ')' })]);
    title.appendChild(WXC.expander(wrap, 'Expand'));
    wrap.appendChild(title);
    wrap.appendChild(svg);
    return wrap;
  }

  /* A storm past a location, before the settlement file.

     A forward-looking ladder declines once the storm moves past a place,
     while the interim settlement that carries what was recorded can lag by
     many hours, so in that window the figures sag for a reason that has
     nothing to do with the threat having been overstated. The criterion is
     deliberately cheap and public. A location has decayed when its
     lowest-rung probability has fallen to half its peak across the
     deliveries, from a peak worth noticing; where the NHC roster carries
     the storm's position and past track, the storm must also have receded
     from such a location beyond its closest approach. An interim or final
     file ends the state, because from then on the figures carry what was
     recorded. */
  function passedPending(storm, doc) {
    const steps = ((doc && doc.steps) || []).filter(st => st.kind === 'livecyc');
    if (steps.length < 2) return false;
    if ((doc.steps || []).some(st => st.kind === 'interim') || doc.final) return false;
    const flat = a => {
      const out = [];
      const walk = v => {
        if (Array.isArray(v) && v.length === 2 && typeof v[0] === 'number') out.push(v);
        else if (Array.isArray(v)) v.forEach(walk);
        else if (v && typeof v === 'object') Object.values(v).forEach(walk);
      };
      walk(a); return out;
    };
    const km = (a, b, c, d) => {
      const r = Math.PI / 180, dl = (d - b) * r, df = (c - a) * r;
      const q = Math.sin(df / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(dl / 2) ** 2;
      return 6371 * 2 * Math.asin(Math.sqrt(q));
    };
    const rst = ROSTER.find(r => String(r.name).toLowerCase() === String(storm.name).toLowerCase());
    const lastMeta = steps[steps.length - 1].siteMeta || {};
    let hit = false;
    Object.keys(doc.sites || {}).forEach(sid => {
      if (hit) return;
      const seq = steps.map(st => (((st.sites || {})[sid]) || [0])[0] || 0);
      const peak = Math.max.apply(null, seq);
      if (!(peak >= 25 && seq[seq.length - 1] <= peak * 0.5)) return;
      const meta = lastMeta[sid] || {};
      if (rst && rst.lat != null && meta.lat != null) {
        const dNow = km(rst.lat, rst.lon, meta.lat, meta.lon);
        const past = flat(rst.past || []);
        if (past.length) {
          const dMin = Math.min.apply(null, past.map(pp => km(pp[1], pp[0], meta.lat, meta.lon)));
          if (dNow < dMin + 50) return;      // not yet receded past this place
        }
      }
      hit = true;
    });
    return hit;
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
    const cyc = delivered(doc);
    const state = [];
    state.push(cyc.length + ' vendor deliver' + (cyc.length === 1 ? 'y' : 'ies') + ' so far');
    if (doc && (doc.steps || []).some(s => s.kind === 'interim')) state.push('Metryc interim received');
    else state.push('Metryc interim pending');
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
      state.push('no wind contracts listed on the exchange yet; the price lines, the pool ladder and its price series appear at listing');
    }
    host.appendChild(h('p', { class: 'cap', text: state.join(' · ') }));
    if (doc && passedPending(storm, doc)) {
      const nt = h('div', { class: 'note warn' });
      nt.innerHTML = '<b>Settlement data pending.</b> This storm appears to have passed some of its '
        + 'reference locations. A forward-looking ladder declines once the storm moves past a place, and '
        + 'the vendor’s Metryc interim file, which folds in what was actually recorded, has not been '
        + 'published yet. Figures for passed locations read low until it arrives, and the exchange’s '
        + 'quotes can sit above them for the same reason. Final values appear here when the file does; '
        + 'the vendor’s final settlement file follows the storm’s last NHC advisory, typically within a '
        + 'day or two of it.';
      host.appendChild(nt);
    }
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
    // the switch and the key for every chart in the section, once, above them
    const used = {};
    cards.forEach(c => Object.keys(c.used || {}).forEach(t => { used[t] = true; }));
    host.appendChild(controls());
    host.appendChild(legend(doc.thresholds || [], used));
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
    const shown = [];
    for (const m of poolMarkets(storm.name)) {
      const ser = await poolSeries(m, doc);
      if (ser) { host.appendChild(ser); shown.push(m.symbol); }
    }
    const p = pools(storm, shown);
    if (p.length) { const g = h('div', { class: 'ladders' }); p.forEach(x => g.appendChild(x)); host.appendChild(g); }
    host.appendChild(h('p', { class: 'cap attrib', text: (RK && RK.attribution) || 'Powered by Reask' }));
    setEmph(EMPH);
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
  // ---- the map asks for a location: bring its card into view
  //
  // The map holds the geography and this section holds the deliveries, so a
  // click on a signalled location scrolls to the card that already draws its
  // series rather than drawing it a second time under the map. The storm the
  // card belongs to comes to the front first when another storm's tab is
  // open, and a storm that has stopped updating is unfolded. Resolves to
  // whether a card was found; when none is, the section itself is scrolled to.
  const pause = ms => new Promise(r => setTimeout(r, ms));
  async function showSite(sid) {
    const host = $('#liveStorms'); if (!host) return false;
    const storms = ((RK && RK.storms) || []).filter(s => s && s.name);
    const keyOf = s => s.name + '_' + s.year;
    const carries = s => {
      const d = ledgers[keyOf(s)];
      if (d && d.sites) return !!d.sites[sid];
      return !!(s.livecyc && s.livecyc.sites && s.livecyc.sites[sid]);
    };
    // the open storm first, then any running storm carrying the location, then a folded one
    let target = storms.find(s => keyOf(s) === open && carries(s))
      || storms.find(s => !dormant(s) && carries(s)) || storms.find(carries);
    if (!target) { host.scrollIntoView({ behavior: 'smooth', block: 'start' }); return false; }
    let within = host;
    if (dormant(target)) {
      const det = [...host.querySelectorAll('details.stormdone')].find(d => {
        const b = d.querySelector('summary b'); return b && b.textContent === target.name + ' ' + target.year; });
      if (det) { if (!det.open) det.open = true; within = det; }
    } else if (keyOf(target) !== open) {
      const b = [...host.querySelectorAll('.bar button')].find(x => x.textContent === target.name);
      if (b) b.click();
    }
    let node = null;
    for (let i = 0; i < 30 && !node; i++) {
      node = within.querySelector('.scardwrap[data-sid="' + sid + '"]');
      if (!node) await pause(100);
    }
    if (!node) { host.scrollIntoView({ behavior: 'smooth', block: 'start' }); return false; }
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    node.classList.add('flash');
    setTimeout(() => node.classList.remove('flash'), 1800);
    return true;
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
      const cyc = delivered(doc);
      Object.keys(doc.sites).forEach(sid => {
        if (cyc.some(x => ((x.sites || {})[sid] || []).some(v => v > 0))) out[sid] = true;
      });
    });
    return out;
  }

  return { init, draw, showSite, sites, dormant, stampOf, setRoster, supersededBy, doneLabel };
})();
