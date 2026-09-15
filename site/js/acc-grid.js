/* Figure 3, the scorecard grid.

   One HTML table with a row per system, grouped and ordered as the forecast
   systems table at the foot of the page is. Both take that grouping from the
   one registry, config/forecast_systems.json, so a change there moves both.

   Three leads, 30, 18 and 12 hours before the end of the target day. Under
   each, the mean absolute error on the high and on the low, then the mean
   error on the high and on the low, every value with its sample printed
   small. Every system that publishes a distribution (the ForecastEx
   prediction market's ladder and the four ensembles) carries its CRPS under
   each error cell, footnoted, because a distribution says more than its
   centre does. The three ensembles with no single-run row of their own are
   rows here, scored on the centre of their ensemble over the hours left in
   the day.

   The shading on the high-error cell is the paired difference from the
   National Weather Service row on the same city-days, per lead, so a column
   is read on its own scale. A cell whose paired interval covered zero, or
   whose sample is under 30 city-days, is left grey. Each pair is compared on
   the days the two share, so a row's own span never has to match another's.

   draw(D) receives the bundle WXAcc.init assembles and reads D.grid; a
   caller passing the grid file itself as the first argument is accepted. */
window.WXAccGrid = (() => {
  const { h, $ } = WXC;
  const A = () => window.WXAcc;

  // the leads the builder scores, in the order they are read
  const LEADS_DEFAULT = [30, 18, 12];
  const MIN_N = 30;
  // the four columns under each lead: the metric, the side, and the cell field
  const COLS = [
    { group: 'MAE', side: 'High', key: 'maeHigh', n: 'n', fmt: 'f2', title: 'Mean absolute error on the daily high, °F' },
    { group: 'MAE', side: 'Low', key: 'maeLow', n: 'nLow', fmt: 'f2', title: 'Mean absolute error on the daily low, °F' },
    { group: 'Mean error', side: 'High', key: 'meHigh', n: 'n', fmt: 'signed2', title: 'Mean error on the daily high, °F, positive when the system ran warm' },
    { group: 'Mean error', side: 'Low', key: 'meLow', n: 'nLow', fmt: 'signed2', title: 'Mean error on the daily low, °F, positive when the system ran warm' },
  ];

  const COHORTS = [
    { key: 'own', label: 'Every day on record', title: 'Each system scored on the city-days its own record covers' },
    { key: 'fixed30', label: 'Fixed sample', title: 'City-days the ForecastEx prediction market priced at every hour from 30 to 0' },
  ];
  const FRAMES = [
    { key: 'metar', label: 'METAR settle', title: 'Systems scored against the station’s METAR settle, the truth the ForecastEx prediction market pays on' },
    { key: 'cli', label: 'NWS climate report', title: 'Systems scored against the National Weather Service climate report for the same date; the ForecastEx prediction market stays scored against the settle' },
  ];
  // what the reader calls each exclusion reason the builder counts
  const REASON = {
    backfill_rows: 'backfilled forecast rows', ecmwf_ifs_lag_rows: 'European-ensemble rows captured under five hours after their run',
    thin_city_day: 'thin-book city-days', thin_date: 'thin-book dates', capture_short: 'capture-short dates',
    empty_local_hour: 'city-days with an empty local hour in the report record', quarantined_settle: 'quarantined settles',
    odd_day_length: 'days of other than 24 hours',
  };

  // ------------------------------------------------------------- formats
  const fin = v => v != null && isFinite(v);
  const f2 = v => A().f2(v);
  // a bias that rounds to zero prints as zero, never as a signed nothing
  const signed2 = v => (!fin(v) ? A().dash : Math.abs(v) < 0.005 ? '0.00' : (v > 0 ? '+' : '−') + f2(Math.abs(v)));
  const FMT = { f2, signed2 };
  const int = v => A().int(v);
  // a percent difference with its sign, one decimal, for the tooltip
  const ssText = v => (!fin(v) ? A().dash : (v > 0 ? '+' : v < 0 ? '−' : '') + A().f1(Math.abs(v)) + '%');

  // ------------------------------------------------------------- state
  let host = null, bar = null, keyEl = null, methodEl = null;
  let grid = null;
  const state = { cohort: 'own', frame: 'metar' };
  let controlsBuilt = false;

  // ------------------------------------------------------------- data access
  const rowsOf = (cohort) => ((grid && grid.cohorts && grid.cohorts[cohort]) || []).filter(r => r && r.id);
  const cellOf = (row, frame, lead) => {
    const fr = row.frame && row.frame[frame];
    return (fr && fr.h && fr.h[String(lead)]) || null;
  };
  const leads = () => (grid && Array.isArray(grid.h) && grid.h.length ? grid.h.map(Number) : LEADS_DEFAULT);
  const metaOf = () => (grid && grid.meta) || {};

  /* The rows in groups, in the registry's order: the same grouping and the
     same order as the forecast systems table, from the same file. */
  function groupedRows() {
    const rows = rowsOf(state.cohort);
    const byId = {};
    rows.forEach(r => { byId[r.id] = r; });
    return A().systemGroups(rows.map(r => r.id)).map(g => ({ title: g.title, rows: g.ids.map(id => byId[id]) }));
  }

  /* Shading. Per lead, the strongest colored cell in the column sets the
     scale, so one very large difference does not wash out the rest of the
     column. Positive is better than the National Weather Service row and
     takes the site's ok token, negative its bad token; the interval rule and
     the sample rule leave a cell grey. The builder ships the paired
     difference for highs, so the shading sits on the high-error cell. */
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
    if (!colorable(cell)) return { token: 'var(--rule)', opacity: 0.28 };
    const share = colMax > 0 ? Math.abs(cell.ssHigh) / colMax : 0;
    return { token: cell.ssHigh >= 0 ? 'var(--ok)' : 'var(--bad)', opacity: 0.12 + 0.5 * share };
  }

  // a cell carries a CRPS only for a system with a distribution; the key is absent otherwise
  const hasCrps = cell => !!cell && ('crps' in cell || 'crpsLow' in cell);
  const CRPS_KEY = { maeHigh: 'crps', maeLow: 'crpsLow' };

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
      ['Days', COHORTS.find(c => c.key === state.cohort).label],
      ['Truth', frameName],
      ['MAE high', cell ? f2(cell.maeHigh) + '°F' : A().dash],
      ['MAE low', cell ? f2(cell.maeLow) + '°F' : A().dash],
      ['Mean error high', cell ? signed2(cell.meHigh) + '°F' : A().dash],
      ['Mean error low', cell ? signed2(cell.meLow) + '°F' : A().dash],
      ['City-days, highs', cell ? int(cell.n) : A().dash],
      ['City-days, lows', cell ? int(cell.nLow) : A().dash],
    ];
    if (hasCrps(cell)) {
      pairs.push(['CRPS high', f2(cell.crps) + (fin(cell.crps) ? '°F' : '')]);
      pairs.push(['CRPS low', f2(cell.crpsLow) + (fin(cell.crpsLow) ? '°F' : '')]);
    }
    if (row.id !== 'NDFD' && cell) {
      pairs.push(['High error against the NWS row', ssText(cell.ssHigh)]);
      pairs.push(['95% interval', fin(cell.ssLo) && fin(cell.ssHi)
        ? ssText(cell.ssLo) + ' to ' + ssText(cell.ssHi)
        : (cell.n >= MIN_N ? 'covers zero, not colored' : 'under ' + MIN_N + ' city-days')]);
    }
    const rs = A().span(metaOf(), row.id, 'high');
    if (rs) pairs.push(['Record', A().mdyY(rs.start) + ' to ' + A().mdyY(rs.end) + (fin(rs.days) ? ', ' + int(rs.days) + ' days' : '')]);
    return A().tooltip().rows(nm + ', ' + L + ' h before the day ends', pairs);
  }

  /* The first day a system was scored on. Every row carries it, in every
     view, because the depth of a system's own record is not the same thing
     as the days a sample could use. */
  function recordSince(row) {
    const sp = A().span(metaOf(), row.id, 'high');
    if (!sp) return row.start || A().dash;
    return A().mdy(sp.start) + (fin(sp.days) ? ' · ' + int(sp.days) + 'd' : '');
  }

  // ------------------------------------------------------------- table
  function buildTable(groups) {
    const L = leads();
    const allRows = groups.flatMap(g => g.rows);
    const scale = columnScale(allRows);
    const width = 2 + L.length * COLS.length;
    const table = h('table', { class: 'acc-grid-table' });
    const thead = h('thead');
    // three header rows: the lead, the metric over its two sides, the side
    const r1 = h('tr'), r2 = h('tr'), r3 = h('tr');
    r1.appendChild(h('th', { rowspan: 3, text: 'System' }));
    r1.appendChild(h('th', { rowspan: 3, class: 'acc-grid-start', text: 'Record since',
                             title: 'The first day this system was scored on. Systems started at different times, so the rows do not cover the same period.' }));
    L.forEach(x => {
      r1.appendChild(h('th', { class: 'acc-grid-g', colspan: COLS.length, text: x + ' h',
                               title: x + ' hours before the station-local midnight that ends the target day' }));
      const metrics = [];
      COLS.forEach(c => { if (!metrics.includes(c.group)) metrics.push(c.group); });
      metrics.forEach((m, k) => r2.appendChild(h('th', { class: 'acc-grid-m' + (k === 0 ? ' acc-grid-first' : ''),
                                                       colspan: COLS.filter(c => c.group === m).length, text: m })));
      COLS.forEach((c, i) => r3.appendChild(h('th', { class: 'num' + (i === 0 ? ' acc-grid-first' : '') + (c.side === 'High' && i > 0 ? ' acc-grid-mid' : ''),
                                                    text: c.side, title: c.title })));
    });
    thead.appendChild(r1); thead.appendChild(r2); thead.appendChild(r3);
    table.appendChild(thead);

    const tbody = h('tbody');
    groups.forEach(g => {
      tbody.appendChild(h('tr', { class: 'acc-grid-grp' }, [h('th', { colspan: width, text: g.title })]));
      g.rows.forEach(row => {
        const tr = h('tr', { class: row.id === 'FX' ? 'acc-grid-fx' : (row.id === 'NDFD' ? 'acc-grid-ref' : '') });
        const nameTd = h('td', { class: 'acc-grid-sys' });
        nameTd.appendChild(h('span', { class: 'acc-grid-sw', style: 'background:' + A().color(row.id) }));
        nameTd.appendChild(document.createTextNode(A().name(row.id)));
        if (row.id === 'NDFD') nameTd.appendChild(h('span', { class: 'acc-grid-n', text: 'reference row' }));
        tr.appendChild(nameTd);
        tr.appendChild(h('td', { class: 'acc-grid-start', text: recordSince(row) }));
        L.forEach(x => {
          const cell = cellOf(row, state.frame, x);
          COLS.forEach((c, i) => {
            const ck = hasCrps(cell) ? CRPS_KEY[c.key] : null;
            const td = numCell(cell && cell[c.key], cell && cell[c.n], FMT[c.fmt],
              c.key === 'maeHigh' && row.id !== 'NDFD' ? fillFor(cell, scale[x]) : null,
              { cls: i === 0 ? 'acc-grid-first' : (c.side === 'High' ? 'acc-grid-mid' : ''),
                mark: ck ? '†' : '', sub: ck ? 'CRPS ' + f2(cell[ck]) : null });
            A().hover(td, () => tipFor(row, x, cell));
            tr.appendChild(td);
          });
        });
        tbody.appendChild(tr);
      });
    });
    table.appendChild(tbody);
    return table;
  }

  // ------------------------------------------------------------- render
  function render() {
    if (!host) return;
    host.innerHTML = '';
    if (!grid) { A().notYet(host, A().NOT_PUBLISHED); return; }
    const groups = groupedRows();
    if (!groups.length) { A().notYet(host, 'The builder shipped no rows for this view.'); renderKey(); renderMethod(); return; }
    host.appendChild(h('div', { class: 'acc-grid-foot acc-grid-top', text: 'Lead in hours before the station-local midnight that ends the target day. Each cell prints its value and its sample of city-days. Rows are grouped and ordered as in the forecast systems table at the foot of the page.' }));
    host.appendChild(h('div', { class: 'acc-grid-scroll' }, [buildTable(groups)]));
    const foot = ['† CRPS in degrees for every system that publishes a distribution, the ForecastEx prediction market’s ladder and each ensemble’s normal curve read at the same strikes at the same hour. It is scored against the same truth as the cell, on the cell’s city-days that the distribution covers.',
                  'The American, Canadian and German ensemble rows score the ensemble’s centre, the highest (for a low, the lowest) hourly ensemble mean over the hours left in the day, held at the running observed extreme like every other value, on the city-days the ForecastEx prediction market priced.'];
    if (state.frame === 'cli') foot.push('In the climate-report frame every alternative forecast system is scored against the National Weather Service climate report for the same date, the ForecastEx prediction market stays scored against the settle, and Buckley Field is excluded because Denver’s report stands in for it.');
    foot.forEach(t => host.appendChild(h('div', { class: 'acc-grid-foot', text: t })));
    renderKey();
    renderMethod();
  }

  /* The sentence under the table saying how far back the record goes: each
     row's own first scored day is in its Record column. */
  function spanFoot() {
    const meta = metaOf();
    const coh = (meta.cohorts || {})[state.cohort];
    const base = 'Record since is each system’s own first scored day. ';
    if (state.cohort === 'own') {
      return base + 'Every row is scored on the days its own record covers, so the rows do not cover the same period. '
        + 'Each comparison against another row is made on the days the pair share.';
    }
    return base + (coh && coh.from
      ? 'The fixed sample is scored from ' + A().mdyY(coh.from)
        + ' on the days the ForecastEx prediction market priced at every hour from 30 to 0.'
      : '');
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
    keyEl.appendChild(item('var(--ok)', 0.55, 'lower high error than the National Weather Service row'));
    keyEl.appendChild(item('var(--bad)', 0.55, 'higher high error than the National Weather Service row'));
    keyEl.appendChild(item('var(--rule)', 0.28, 'interval covers zero, or under ' + MIN_N + ' city-days'));
    keyEl.appendChild(h('span', { class: 'kn', text: 'shading on the MAE high cell, scaled per lead column' }));
  }

  function renderMethod() {
    if (!methodEl) return;
    const meta = metaOf();
    const coh = (meta.cohorts || {})[state.cohort];
    const excl = Array.isArray(meta.exclusions) ? meta.exclusions : [];
    const exclText = excl.length
      ? 'Excluded and counted, ' + excl.map(e => (REASON[e.reason] || String(e.reason).replace(/_/g, ' ')) + ' ' + int(e.count)
          + (fin(e.dates) ? ' on ' + int(e.dates) + ' date' + (e.dates === 1 ? '' : 's') : '')).join('; ') + '.'
      : null;
    const rules = [
      'v is the standing value at lead h, the last record at or before that instant held at the running observed extreme, and settle is the station’s METAR settle. The ForecastEx prediction market is always scored against the settle. In the climate-report frame an alternative forecast system’s settle is replaced by the National Weather Service climate report for the same date.',
      'SS_s is positive when a system’s high error is below the National Weather Service row’s on the same city-days. Its interval is a paired bootstrap over target dates, 1,000 draws, 95 percent percentile. A cell whose interval covers zero, or whose sample is under ' + MIN_N + ' city-days, is grey.',
      'CRPS is computed over the ForecastEx prediction market’s strikes at the same snapshot, closed at the end strikes. The market’s distribution is its monotone ladder, its Yes prices read as probabilities. An ensemble’s is a normal curve on its own mean and spread at the hour the day’s extreme falls, with nothing fitted, banked at the running observed extreme as every value on the page is.',
      'Rows are grouped and ordered as in the forecast systems table at the foot of the page. Every day on record scores each system on the city-days its own record covers, and the fixed sample restricts those days to the ones the ForecastEx prediction market priced at every hour from 30 to 0.',
    ];
    if (exclText) rules.push(exclText);
    let n;
    if (coh && (fin(coh.n_high) || fin(coh.n_low))) {
      n = 'Sample ' + int(coh.n_high) + ' city-days on highs and ' + int(coh.n_low) + ' on lows'
        + (fin(coh.cities) ? ' over ' + int(coh.cities) + ' cities' : '') + (coh.from ? ' from ' + coh.from : '') + ', the sample at each lead printed in its cell.';
    } else {
      n = 'Sample varies by system and is printed in every cell.';
    }
    if (meta.window) n += ' ' + A().windowAndBuilt(meta) + '.';
    A().methodNote(methodEl, {
      title: 'Scoring',
      body: [
        'Mean absolute error and mean error are both in degrees Fahrenheit, on the daily high and the daily low. Mean error is signed, positive when a system runs warm.',
        { tex: 'SS_s = 100\\left(1 - \\frac{MAE_s}{MAE_{NWS}}\\right)' },
        'A positive skill score means a system beat the National Weather Service on the same city-days. Its interval is a paired bootstrap, and a cell is greyed out when that interval covers zero or the sample is under ' + MIN_N + ' city-days.',
        { tex: 'CRPS = \\sum_{k} \\left(F(k) - \\mathbb{1}[\\text{settle} \\le k]\\right)^2' },
        'On highs, $F(k) = 1 - P(k)$, on lows, $F(k-1) = P(k)$, where $P(k)$ is the probability the contract at strike $k$ pays, the Yes price for the ForecastEx prediction market and the normal curve for an ensemble. CRPS scores the whole distribution, not just its centre.',
      ],
      rules, n, span: spanFoot(),
    });
  }

  // ------------------------------------------------------------- controls
  function buildControls() {
    if (controlsBuilt || !bar) return;
    controlsBuilt = true;
    bar.innerHTML = '';
    A().tabs(bar, COHORTS, k => { state.cohort = k; render(); }, { initial: state.cohort, label: 'Days' });
    A().tabs(bar, FRAMES, k => { state.frame = k; render(); }, { initial: state.frame, label: 'Frame' });
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
