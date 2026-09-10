/* Figure 5, the scorecard grid.

   One HTML table: ForecastEx first, then every source in the selected
   cohort ordered by its 12 h mean absolute error, against four leads
   (30, 18, 12 and 6 hours before the end of the target day). Each lead
   carries the mean absolute error on highs and on lows, the mean error on
   highs and the share of city-days within one degree on highs, every one
   with its sample printed small. The market's error cell also carries the
   CRPS of its ladder, footnoted, because a ladder is a distribution and a
   central value alone says less than the ladder does.

   The shading is the paired difference from the National Weather Service
   row on the same city-days, per lead, so a column is read on its own
   scale. A cell whose paired interval covered zero, or whose sample is
   under 30 city-days, is left grey, and the own-span cohort is never
   colored at all, because its rows are not scored on the same days.

   Highs are the default and lows a tab. The builder ships the bias, the
   one-degree share, the paired difference and the CRPS for highs only, so
   the lows view carries the mean absolute error alone, uncolored.

   draw(D) receives the bundle WXAcc.init assembles and reads D.grid; a
   caller passing the grid file itself as the first argument is accepted. */
window.WXAccGrid = (() => {
  const { h, $ } = WXC;
  const A = () => window.WXAcc;

  // the four leads the builder scores, in the order they are read
  const LEADS_DEFAULT = [30, 18, 12, 6];
  // the lead the rows are ordered by: the last lead where most of the
  // day is still ahead and every system has issued a fresh value
  const SORT_H = 12;
  const MIN_N = 30;

  const COHORTS = [
    { key: 'matched11', label: 'Matched eleven', title: 'City-days where all eleven panel tools and the market have a value at the lead' },
    { key: 'core5', label: 'Core five', title: 'The National Weather Service, the Blend, the Aviation Forecast, the European and the American model with the market' },
    { key: 'own', label: 'Own span', title: 'Each source on its own days from its own start, never colored against another' },
  ];
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'Tools scored against the station’s METAR settle, the truth the market pays on' },
    { key: 'cli', label: 'NWS climate report', title: 'Tools scored against the National Weather Service climate report for the same date; the market stays scored against the settle' },
  ];
  // what the reader calls each exclusion reason the builder counts
  const REASON = {
    backfill_rows: 'backfilled tool rows', ecmwf_ifs_lag_rows: 'European-ensemble rows captured under five hours after their run',
    thin_city_day: 'thin-book city-days', thin_date: 'thin-book dates', capture_short: 'capture-short dates',
    empty_local_hour: 'city-days with an empty local hour in the report record', quarantined_settle: 'quarantined settles',
    odd_day_length: 'days of other than 24 hours',
  };

  // ------------------------------------------------------------- formats
  const fin = v => v != null && isFinite(v);
  const f2 = v => A().f2(v);
  // a bias that rounds to zero prints as zero, never as a signed nothing
  const signed2 = v => (!fin(v) ? A().dash : Math.abs(v) < 0.005 ? '0.00' : (v > 0 ? '+' : '−') + f2(Math.abs(v)));
  const pct = v => A().pct(v);
  const int = v => A().int(v);
  // a percent difference with its sign, one decimal, for the tooltip
  const ssText = v => (!fin(v) ? A().dash : (v > 0 ? '+' : v < 0 ? '−' : '') + A().f1(Math.abs(v)) + '%');

  // ------------------------------------------------------------- state
  let host = null, bar = null, keyEl = null, methodEl = null;
  let grid = null;
  const state = { metric: 'high', cohort: 'matched11', frame: 'metar', nl: 'off', csv: false };
  let controlsBuilt = false;

  // ------------------------------------------------------------- data access
  const rowsOf = (cohort) => ((grid && grid.cohorts && grid.cohorts[cohort]) || []).filter(r => r && r.id);
  const cellOf = (row, frame, lead) => {
    const fr = row.frame && row.frame[frame];
    return (fr && fr.h && fr.h[String(lead)]) || null;
  };
  const leads = () => (grid && Array.isArray(grid.h) && grid.h.length ? grid.h.map(Number) : LEADS_DEFAULT);
  // the row's headline error in the view: highs by maeHigh, lows by maeLow
  const headline = (cell, metric) => (cell ? (metric === 'high' ? cell.maeHigh : cell.maeLow) : null);

  /* Row order. ForecastEx stays first so the market is always the row a
     reader compares against; the sources follow by their 12 h error in
     the selected frame, a missing value sorting last, ties broken by the
     contract's naming order so two equal rows never swap between builds. */
  function orderedRows() {
    const rows = rowsOf(state.cohort);
    const order = A().ORDER;
    const fx = rows.filter(r => r.id === 'FX');
    const rest = rows.filter(r => r.id !== 'FX');
    const keyOf = r => {
      const v = headline(cellOf(r, state.frame, SORT_H), state.metric);
      return fin(v) ? v : Infinity;
    };
    rest.sort((a, b) => {
      const d = keyOf(a) - keyOf(b);
      if (d !== 0 && isFinite(d)) return d;
      if (keyOf(a) !== keyOf(b)) return keyOf(a) === Infinity ? 1 : -1;
      return order.indexOf(a.id) - order.indexOf(b.id);
    });
    return fx.concat(rest);
  }

  /* Shading. Per lead, the strongest colored cell in the column sets the
     scale, so a column with one 99 percent cell (the market at 6 h, when the
     day's high has usually been recorded) does not wash out the rest of the
     table. Positive is better than the National Weather Service row and
     takes the site's ok token, negative its bad token; the interval rule and
     the sample rule leave a cell grey, and own span is never colored. */
  function colorable(cell) {
    return !!cell && fin(cell.ssHigh) && fin(cell.ssLo) && fin(cell.ssHi) && fin(cell.n) && cell.n >= MIN_N;
  }
  function columnScale(rows) {
    const out = {};
    leads().forEach(L => {
      let m = 0;
      rows.forEach(r => { const c = cellOf(r, state.frame, L); if (colorable(c)) m = Math.max(m, Math.abs(c.ssHigh)); });
      out[L] = m;
    });
    return out;
  }
  function fillFor(cell, colMax) {
    if (state.cohort === 'own' || state.metric !== 'high') return null;
    if (!colorable(cell)) return { token: 'var(--rule)', opacity: 0.28 };
    const share = colMax > 0 ? Math.abs(cell.ssHigh) / colMax : 0;
    return { token: cell.ssHigh >= 0 ? 'var(--ok)' : 'var(--bad)', opacity: 0.12 + 0.5 * share };
  }

  // ------------------------------------------------------------- cells
  function numCell(value, n, fmt, fill, extra) {
    const td = h('td', { class: 'acc-grid-c' + (extra && extra.cls ? ' ' + extra.cls : '') });
    if (fill) td.appendChild(h('span', { class: 'acc-grid-fill', style: 'background:' + fill.token + ';opacity:' + fill.opacity }));
    td.appendChild(h('span', { class: 'acc-grid-v', text: fmt(value) + (extra && extra.mark ? extra.mark : '') }));
    td.appendChild(h('span', { class: 'acc-grid-n', text: fin(n) ? 'n ' + int(n) : A().dash }));
    if (extra && extra.sub) td.appendChild(h('span', { class: 'acc-grid-n', text: extra.sub }));
    return td;
  }

  // the tooltip for every cell of one (row, lead) group
  function tipFor(row, L, cell) {
    const nm = A().name(row.id);
    const frameName = state.frame === 'cli' && row.id !== 'FX' ? 'NWS climate report' : 'METAR settle';
    const pairs = [
      ['Cohort', COHORTS.find(c => c.key === state.cohort).label],
      ['Truth', frameName],
      ['MAE high', cell ? f2(cell.maeHigh) + '°F' : A().dash],
      ['City-days, highs', cell ? int(cell.n) : A().dash],
      ['MAE low', cell ? f2(cell.maeLow) + '°F' : A().dash],
      ['City-days, lows', cell ? int(cell.nLow) : A().dash],
      ['Mean error high', cell ? signed2(cell.meHigh) + '°F' : A().dash],
      ['Within 1°F, highs', cell ? pct(cell.hr1High) : A().dash],
    ];
    if (row.id === 'FX') pairs.push(['CRPS of the ladder', cell ? f2(cell.crps) : A().dash]);
    if (row.id !== 'NDFD' && state.cohort !== 'own' && cell) {
      pairs.push(['Against the NWS row', ssText(cell.ssHigh)]);
      pairs.push(['95% interval', fin(cell.ssLo) && fin(cell.ssHi)
        ? ssText(cell.ssLo) + ' to ' + ssText(cell.ssHi)
        : (cell.n >= MIN_N ? 'covers zero, not colored' : 'under ' + MIN_N + ' city-days')]);
    }
    if (state.cohort === 'own') pairs.push(['Own span from', row.start || A().dash]);
    return A().tooltip().rows(nm + ', ' + L + ' h before the day ends', pairs);
  }

  // ------------------------------------------------------------- table
  function buildTable(rows) {
    const L = leads();
    const highs = state.metric === 'high';
    const own = state.cohort === 'own';
    const scale = columnScale(rows);
    const sub = highs
      ? [['MAE high', 'mean absolute error on the daily high, °F'], ['MAE low', 'mean absolute error on the daily low, °F'],
         ['ME high', 'mean error on the daily high, positive when the value ran warm'], ['Within 1°', 'share of city-days within one degree on the high']]
      : [['MAE low', 'mean absolute error on the daily low, °F']];
    const table = h('table', { class: 'acc-grid-table' });
    const thead = h('thead');
    const r1 = h('tr');
    r1.appendChild(h('th', { rowspan: 2, text: 'System' }));
    if (own) r1.appendChild(h('th', { rowspan: 2, text: 'Own span from' }));
    L.forEach(x => r1.appendChild(h('th', { class: 'acc-grid-g', colspan: sub.length, text: x + ' h' + (x === SORT_H ? ' (sort)' : ''),
                                            title: x + ' hours before the station-local midnight that ends the target day' })));
    thead.appendChild(r1);
    const r2 = h('tr');
    L.forEach(() => sub.forEach((s, i) => r2.appendChild(h('th', { class: 'num' + (i === 0 ? ' acc-grid-first' : ''), text: s[0], title: s[1] }))));
    thead.appendChild(r2);
    table.appendChild(thead);

    const tbody = h('tbody');
    rows.forEach(row => {
      const tr = h('tr', { class: row.id === 'FX' ? 'acc-grid-fx' : (row.id === 'NDFD' ? 'acc-grid-ref' : '') });
      const nameTd = h('td', { class: 'acc-grid-sys' });
      nameTd.appendChild(h('span', { class: 'acc-grid-sw', style: 'background:' + A().color(row.id) }));
      nameTd.appendChild(document.createTextNode(A().name(row.id)));
      if (row.id === 'NDFD' && !own) nameTd.appendChild(h('span', { class: 'acc-grid-n', text: 'reference row' }));
      tr.appendChild(nameTd);
      if (own) tr.appendChild(h('td', { class: 'acc-grid-start', text: row.start || A().dash }));
      L.forEach(x => {
        const cell = cellOf(row, state.frame, x);
        const tds = [];
        if (highs) {
          const isFx = row.id === 'FX';
          tds.push(numCell(cell && cell.maeHigh, cell && cell.n, f2, fillFor(cell, scale[x]),
            { cls: 'acc-grid-first', mark: isFx ? '†' : '', sub: isFx ? 'CRPS ' + f2(cell && cell.crps) : null }));
          tds.push(numCell(cell && cell.maeLow, cell && cell.nLow, f2, null));
          tds.push(numCell(cell && cell.meHigh, cell && cell.n, signed2, null));
          tds.push(numCell(cell && cell.hr1High, cell && cell.n, pct, null));
        } else {
          tds.push(numCell(cell && cell.maeLow, cell && cell.nLow, f2, null, { cls: 'acc-grid-first' }));
        }
        tds.forEach(td => { A().hover(td, () => tipFor(row, x, cell)); tr.appendChild(td); });
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  // ------------------------------------------------------------- newsletter
  /* The newsletter block as the builder ranks it: the last seven resolved
     days, highs, every system scored on the same city-days, ties named
     where two systems agree to two decimals, which is the builder's rule. */
  function buildNewsletter() {
    const nl = grid.newsletter || {};
    const rows = Array.isArray(nl.rows) ? nl.rows.filter(r => r && r.id) : [];
    const w = nl.window || {};
    const box = h('div', { class: 'acc-grid-nl' });
    box.appendChild(h('div', { class: 'accsub', text: 'Newsletter window, highs, last ' + (fin(w.days) ? int(w.days) : A().dash) + ' resolved days'
      + (w.from && w.to ? ', ' + w.from + ' to ' + w.to : '') }));
    if (!rows.length) {
      box.appendChild(h('div', { class: 'acc-grid-foot', text: 'No system reached the newsletter window’s eligibility in these days.' }));
      return box;
    }
    const byRank = {};
    rows.forEach(r => { byRank[r.rank] = (byRank[r.rank] || 0) + 1; });
    const t = h('table', { class: 'acc-grid-table acc-grid-nltable' });
    const th = h('tr');
    [['Rank', ''], ['System', ''], ['MAE high', 'num'], ['City-days', 'num']].forEach(([s, c]) => th.appendChild(h('th', { class: c, text: s })));
    t.appendChild(h('thead', {}, [th]));
    const tb = h('tbody');
    rows.forEach(r => {
      const tr = h('tr', { class: r.id === 'FX' ? 'acc-grid-fx' : '' });
      tr.appendChild(h('td', { class: 'num', text: int(r.rank) + (byRank[r.rank] > 1 ? ' tie' : '') }));
      const nameTd = h('td', { class: 'acc-grid-sys' });
      nameTd.appendChild(h('span', { class: 'acc-grid-sw', style: 'background:' + A().color(r.id) }));
      nameTd.appendChild(document.createTextNode(A().name(r.id)));
      tr.appendChild(nameTd);
      tr.appendChild(h('td', { class: 'num', text: f2(r.mae) }));
      tr.appendChild(h('td', { class: 'num', text: int(r.n) }));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    box.appendChild(h('div', { class: 'acc-grid-scroll' }, [t]));
    box.appendChild(h('div', { class: 'acc-grid-foot', text: 'Mean absolute error of each system’s standing high over the newsletter’s hours, 5 PM to 5 AM ET, against the METAR settle, on the city-days every ranked system has a value for. A tie is two systems equal to two decimals.' }));
    return box;
  }

  // ------------------------------------------------------------- csv
  /* The visible table as text. Built from the same rows and cells the
     table was drawn from, so the file and the screen never disagree. */
  const q = s => (/[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s);
  const num = v => (fin(v) ? String(v) : '');
  function buildCsv(rows) {
    const L = leads();
    const highs = state.metric === 'high';
    const own = state.cohort === 'own';
    const head = ['system', 'id'];
    if (own) head.push('own_span_from');
    L.forEach(x => {
      if (highs) head.push('mae_high_' + x + 'h', 'n_high_' + x + 'h', 'mae_low_' + x + 'h', 'n_low_' + x + 'h',
                           'me_high_' + x + 'h', 'within_1F_high_' + x + 'h', 'vs_nws_pct_' + x + 'h', 'vs_nws_lo_' + x + 'h', 'vs_nws_hi_' + x + 'h', 'crps_' + x + 'h');
      else head.push('mae_low_' + x + 'h', 'n_low_' + x + 'h');
    });
    const lines = ['# cohort=' + state.cohort + ' frame=' + state.frame + ' metric=' + state.metric
                   + (grid.meta && grid.meta.window ? ' window=' + grid.meta.window.from + '..' + grid.meta.window.to : '')
                   + (grid.meta && grid.meta.built ? ' built=' + grid.meta.built : ''), head.join(',')];
    rows.forEach(row => {
      const out = [q(A().name(row.id)), row.id];
      if (own) out.push(row.start || '');
      L.forEach(x => {
        const c = cellOf(row, state.frame, x) || {};
        if (highs) out.push(num(c.maeHigh), num(c.n), num(c.maeLow), num(c.nLow), num(c.meHigh), num(c.hr1High),
                            own ? '' : num(c.ssHigh), own ? '' : num(c.ssLo), own ? '' : num(c.ssHi), row.id === 'FX' ? num(c.crps) : '');
        else out.push(num(c.maeLow), num(c.nLow));
      });
      lines.push(out.join(','));
    });
    if (state.nl === 'on' && grid.newsletter && Array.isArray(grid.newsletter.rows)) {
      const w = grid.newsletter.window || {};
      lines.push('', '# newsletter window highs days=' + num(w.days) + ' from=' + (w.from || '') + ' to=' + (w.to || ''),
                 'rank,system,id,mae_high,n');
      grid.newsletter.rows.forEach(r => lines.push([num(r.rank), q(A().name(r.id)), r.id, num(r.mae), num(r.n)].join(',')));
    }
    return lines.join('\n') + '\n';
  }

  // ------------------------------------------------------------- render
  let csvLink = null, csvPre = null, csvBtn = null;
  function render() {
    if (!host) return;
    host.innerHTML = '';
    if (!grid) { A().notYet(host, A().NOT_PUBLISHED); return; }
    const rows = orderedRows();
    if (!rows.length) { A().notYet(host, 'The builder shipped no rows for this cohort.'); renderKey(); renderMethod(); return; }
    host.appendChild(h('div', { class: 'acc-grid-foot acc-grid-top', text: 'Lead in hours before the station-local midnight that ends the target day. Each cell prints its value and its sample of city-days. Rows after ForecastEx are ordered by their ' + SORT_H + ' h ' + (state.metric === 'high' ? 'MAE high' : 'MAE low') + '.' }));
    host.appendChild(h('div', { class: 'acc-grid-scroll' }, [buildTable(rows)]));
    const foot = [];
    if (state.metric === 'high') foot.push('† CRPS of the market’s ladder against the settle, in degrees, on the same city-days as its MAE.');
    if (state.metric === 'low') foot.push('The builder ships the bias, the one-degree share, the paired difference and the CRPS for highs only, so the lows view carries the mean absolute error alone, uncolored.');
    if (state.cohort === 'own') foot.push('Own-span rows are scored on each source’s own days from the start printed, so they are not colored against another source.');
    if (state.frame === 'cli') foot.push('In the climate-report frame every tool is scored against the National Weather Service climate report for the same date, the market stays scored against the settle, and Buckley Field is excluded because Denver’s report stands in for it.');
    foot.forEach(t => host.appendChild(h('div', { class: 'acc-grid-foot', text: t })));
    if (state.nl === 'on') host.appendChild(buildNewsletter());
    // the CSV, as a link and as text on the page
    const csv = buildCsv(rows);
    const fname = 'accuracy-grid-' + state.cohort + '-' + state.frame + '-' + state.metric + '.csv';
    if (csvLink) {
      csvLink.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
      csvLink.download = fname;
    }
    if (csvPre) { csvPre.textContent = csv; csvPre.hidden = !state.csv; }
    renderKey();
    renderMethod();
  }

  function renderKey() {
    if (!keyEl) return;
    keyEl.innerHTML = '';
    const item = (token, opacity, text) => {
      const s = h('span');
      s.appendChild(h('span', { class: 'acc-grid-sw', style: 'background:' + token + ';opacity:' + opacity }));
      s.appendChild(document.createTextNode(text));
      return s;
    };
    if (state.cohort === 'own' || state.metric !== 'high') {
      keyEl.appendChild(h('span', { class: 'kn', text: state.metric !== 'high' ? 'Lows carry no paired difference, so no cell is colored.' : 'Own span is never colored against another source.' }));
      return;
    }
    keyEl.appendChild(item('var(--ok)', 0.55, 'lower error than the National Weather Service row'));
    keyEl.appendChild(item('var(--bad)', 0.55, 'higher error than the National Weather Service row'));
    keyEl.appendChild(item('var(--rule)', 0.28, 'interval covers zero, or under ' + MIN_N + ' city-days'));
    keyEl.appendChild(h('span', { class: 'kn', text: 'shade scaled per lead column' }));
  }

  function renderMethod() {
    if (!methodEl) return;
    const meta = (grid && grid.meta) || {};
    const coh = (meta.cohorts || {})[state.cohort];
    const excl = Array.isArray(meta.exclusions) ? meta.exclusions : [];
    const exclText = excl.length
      ? 'Excluded and counted, ' + excl.map(e => (REASON[e.reason] || String(e.reason).replace(/_/g, ' ')) + ' ' + int(e.count)
          + (fin(e.dates) ? ' on ' + int(e.dates) + ' date' + (e.dates === 1 ? '' : 's') : '')).join('; ') + '.'
      : null;
    const rules = [
      'v is the standing value at lead h, the last record at or before that instant held at the running observed extreme, and settle is the station’s METAR settle. The market is always scored against the settle. In the climate-report frame a tool’s settle is replaced by the National Weather Service climate report for the same date.',
      'SS_s is positive when a system’s error is below the National Weather Service row’s on the same city-days. Its interval is a paired bootstrap over target dates, 1,000 draws, seed 20260910, 95 percent percentile. A cell whose interval covers zero, or whose sample is under ' + MIN_N + ' city-days, is grey, and the own-span cohort is never colored.',
      'CRPS is computed on the market’s monotone ladder at the same snapshot, its Yes prices read as a distribution over whole degrees, closed at the end strikes.',
      'Rows after ForecastEx are ordered by the ' + SORT_H + ' h mean absolute error in the selected view. The matched eleven cohort holds the city-days where all eleven panel tools and the market have a value at the lead, the core five the National Weather Service, the Blend, the Aviation Forecast, the European and the American model with the market, and own span each source from its own start.',
    ];
    if (exclText) rules.push(exclText);
    let n;
    if (coh && (fin(coh.n_high) || fin(coh.n_low))) {
      n = 'Sample ' + int(state.metric === 'high' ? coh.n_high : coh.n_low) + ' city-days on ' + (state.metric === 'high' ? 'highs' : 'lows')
        + (fin(coh.cities) ? ' over ' + int(coh.cities) + ' cities' : '') + (coh.from ? ' from ' + coh.from : '') + ', the sample at each lead printed in its cell.';
    } else {
      n = 'Sample varies by source in the own-span cohort and is printed in every cell.';
    }
    if (meta.window) n += ' ' + A().windowAndBuilt(meta) + '.';
    A().methodNote(methodEl, {
      title: 'How the grid is scored',
      body: [
        'Mean absolute error and mean error are both in degrees Fahrenheit, mean error is signed, positive when a system runs warm. Hit rate is the share of city-days within one degree of the settle.',
        { tex: 'SS_s = 100\\left(1 - \\frac{MAE_s}{MAE_{NWS}}\\right)' },
        'A positive skill score means a system beat the National Weather Service on the same city-days. Its interval is a paired bootstrap, and a cell is greyed out when that interval covers zero or the sample is under ' + MIN_N + ' city-days.',
        { tex: 'CRPS = \\sum_{k} \\left(F(k) - \\mathbb{1}[\\text{settle} \\le k]\\right)^2' },
        'On highs, $F(k) = 1 - \\text{YesPrice}(k)$, on lows, $F(k-1) = \\text{YesPrice}(k)$. CRPS scores the whole distribution the market is pricing, not just its fifty-cent crossing.',
      ],
      rules, n,
    });
  }

  // ------------------------------------------------------------- controls
  function buildControls() {
    if (controlsBuilt || !bar) return;
    controlsBuilt = true;
    bar.innerHTML = '';
    A().metricTabs(bar, k => { state.metric = k; render(); }, state.metric);
    A().tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Cohort' });
    A().tabs(bar, FRAMES, k => { state.frame = k; render(); }, { initial: state.frame, label: 'Frame' });
    A().tabs(bar, [{ key: 'off', label: 'Hide' }, { key: 'on', label: 'Show' }], k => { state.nl = k; render(); },
             { initial: state.nl, label: 'Newsletter window' });
    const g = h('span', { class: 'tabgroup acc-grid-csv-ctl' });
    csvLink = h('a', { class: 'vbtn acc-grid-dl', text: 'Download CSV', href: '#',
                       title: 'The visible table as CSV. Some embedded viewers block downloads; the text is also shown on the page.' });
    csvBtn = h('button', { class: 'vbtn', text: 'Show CSV' });
    csvBtn.onclick = () => {
      state.csv = !state.csv;
      csvBtn.classList.toggle('on', state.csv);
      csvBtn.textContent = state.csv ? 'Hide CSV' : 'Show CSV';
      if (csvPre) csvPre.hidden = !state.csv;
    };
    g.appendChild(csvLink); g.appendChild(csvBtn);
    bar.appendChild(g);
    // the CSV text sits under the method note, out of the card, so the
    // table keeps its height when it is shown
    if (methodEl && !csvPre) {
      csvPre = h('pre', { class: 'acc-grid-csv', hidden: '' });
      csvPre.hidden = true;
      methodEl.parentNode.insertBefore(csvPre, methodEl.nextSibling);
    }
  }

  // ------------------------------------------------------------- entry
  function draw(D) {
    host = $('#accGrid'); bar = $('#accGridBar'); keyEl = $('#accGridKey'); methodEl = $('#accGridMethod');
    if (!host) return;
    // the bundle, or the grid file itself when a caller passes that
    grid = D && D.cohorts && D.h ? D : (D && D.grid) || null;
    if (grid && !(grid.cohorts && typeof grid.cohorts === 'object')) grid = null;
    if (!grid) {
      A().notYet(host, A().NOT_PUBLISHED);
      if (keyEl) keyEl.innerHTML = '';
      if (methodEl) methodEl.innerHTML = '';
      return;
    }
    if (!grid.cohorts[state.cohort]) state.cohort = Object.keys(grid.cohorts)[0] || state.cohort;
    buildControls();
    render();
  }

  return { draw, state };
})();
