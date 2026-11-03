/* The analysis resolution page: a proposed settlement framework, shown as it
   would have resolved.

   Daily high, low, peak gust, mean wind and precipitation at the sixty-seven
   settlement locations the owner approved on 2026-09-29 (the grid cell that
   holds each main city's City Hall), read from NOAA's hourly 2.5 km RTMA
   instead of a station's reports, precipitation included (owner's decision
   2026-09-29). Resolved in whole units rounded once, half up, away from zero,
   with a value equal to the strike resolving Yes (owner's decision
   2026-09-28; the exchange's current daily contracts resolve strictly). No
   contract settles on it, the page says so on every view, and it is not in
   the navigation.

   Everything drawn here is read from the files docs/analysis.md section 4
   describes. The page computes nothing but colours and the rung labels of a
   ladder that does not exist. Every value, the day's running values
   included, is the lane's, and the page never adds, averages or rounds one,
   since rounded hours do not add up to the day. It never shows a price, and
   never says ask, sell or offer.

   A picked place opens its settlement display (js/analysis-settle.js, the
   owner's approved design of 2026-11-03) above the map: the day's state, the
   countdowns to the next files, the five values, the hours on three charts
   and in a table, and a replay. The map, its frames, stepper and cell mode,
   the CSV links, the hypothetical ladder and the method follow it.

   The controls follow the owner's request of 2026-11-03. The day is a button
   that opens a month calendar of the listed days, the place a button that
   opens a small map of the places with a box to type a name, and the
   variable a switch on the map, since the display shows all five at once. */
window.WXAnalysis = (() => {
  const { el, txt, h, $ } = WXC;
  const STATEMENT = 'A proposed settlement framework. No contract settles on it.';
  // the site's nine-stop ramp, as varmap.js draws it, and a blue one for rain
  const RAMP = ['#c9dcec', '#d4e6ea', '#dcecd9', '#e9eecb', '#f4ecc1', '#f5ddb3', '#eec9a5', '#e3b49c', '#d8a098'];
  const RAIN = ['#eef4fb', '#d6e6f5', '#bcd7ee', '#9fc5e5', '#7fb0da', '#5f98cb', '#437fb8', '#2b66a1', '#184d86'];
  /* The five daily variables. `hourly` is the frame and hourly-series variable
     each is built from; `lo` and `hi` pin the ramp so a frame from one day
     reads the same as a frame from another; `yes` is the side of the strike
     the value has to be on; `rungs` for precipitation are the fixed strikes of
     docs/analysis.md, the others take whole units around the value. `exactDec`
     is the precision the job cuts the exact aggregate to (CONVENTIONS.rounding:
     a thousandth, precipitation a ten-thousandth of an
     inch), so the card shows the file's figure and never a re-rounding of it
     that could equal the whole value it sits beside. */
  const VARS = {
    high:   { label: 'High', hourly: 'temp', unit: '°F', lo: -20, hi: 110, ramp: RAMP, dec: 0, exactDec: 3, yes: 'above', noun: 'high' },
    low:    { label: 'Low', hourly: 'temp', unit: '°F', lo: -20, hi: 110, ramp: RAMP, dec: 0, exactDec: 3, yes: 'below', noun: 'low' },
    gust:   { label: 'Peak gust', hourly: 'gust', unit: 'mph', lo: 0, hi: 60, ramp: RAMP, dec: 0, exactDec: 3, yes: 'above', noun: 'peak gust' },
    wind:   { label: 'Mean wind', hourly: 'wind', unit: 'mph', lo: 0, hi: 30, ramp: RAMP, dec: 0, exactDec: 3, yes: 'above', noun: 'mean wind' },
    precip: { label: 'Precipitation', hourly: 'precip', unit: 'in', lo: 0, hi: 2, ramp: RAIN, dec: 2, exactDec: 4, yes: 'above',
              noun: 'precipitation', zeroClear: true, rungs: [0.01, 0.05, 0.10, 0.25, 0.50, 1.00, 2.00], running: true },
  };
  // the analysis the framework resolves on and the only one the files carry
  // (owner's decisions 2026-09-29 and 2026-09-30), by its key in the files
  const PRODUCT = 'rtma', PRODUCT_NAME = 'RTMA';
  // the lane's second schema (2026-09-29): the sixty-seven settlement
  // locations, RTMA resolving, one rounding and a midnight-to-midnight rain day
  const DATA = 'analysis2/';
  // the CSV links under the controls, set once the bar is built
  let setCsv = () => {};
  // the hourly variables as the captions name them; the keys are the files'
  const HOURLY = { temp: 'temperature', wind: 'sustained wind', gust: 'gust', precip: 'precipitation' };
  // the precision the loc files carry each hourly value at (docs/analysis.md
  // section 4), cut rather than rounded: a thousandth, and precipitation a
  // ten-thousandth of an inch
  const hourlyDec = hv => (hv === 'precip' ? 4 : 3);
  // the day's hour count is the file's `of` (23, 24 or 25, docs/analysis.md
  // section 4); 24 only for a file written before that field existed
  const hoursInDay = e => (e && e.of) || 24;
  const VB = { w: 960, h: 600 };

  // ---- the page's state, which is also the address bar's
  // `view` is the map's zoom when it is in, `{z, cx, cy}` in viewBox units,
  // kept by applyView below so a zoomed cell can be linked; null at the
  // whole country
  const S = { var: 'high', day: null, hour: null, loc: null, view: null,
              index: null, grid: null, frames: new Map(), days: new Map(), locs: new Map() };
  const V = () => VARS[S.var];
  const fmt = (v, dec) => (v == null ? '' : Number(v).toFixed(dec == null ? V().dec : dec));
  const withUnit = (v, dec) => (v == null ? '' : fmt(v, dec) + (V().unit === 'in' ? ' in' : V().unit === 'mph' ? ' mph' : V().unit));
  const dayLabel = d => (WXC.weekdayOf(d) ? WXC.weekdayOf(d) + ' ' + d : d);
  const stampOf = (day, hh) => day.replace(/-/g, '') + 'T' + hh + 'Z';
  const validOf = (day, hh) => day + 'T' + hh + ':00:00Z';

  // a link that names a place and a variable opens on that variable's chart
  let scrollVar = false;
  function readUrl() {
    const q = new URLSearchParams(location.search);
    if (VARS[q.get('var')]) S.var = q.get('var');
    scrollVar = !!(VARS[q.get('var')] && q.get('loc'));
    if (q.get('day')) S.day = q.get('day');
    if (q.get('hour') != null && /^\d{1,2}$/.test(q.get('hour'))) S.hour = String(q.get('hour')).padStart(2, '0');
    if (q.get('loc')) S.loc = q.get('loc');
    const z = parseFloat(q.get('z')), cx = parseFloat(q.get('cx')), cy = parseFloat(q.get('cy'));
    if (z > 1.02 && isFinite(cx) && isFinite(cy)) S.view = { z, cx, cy };
  }
  function writeUrl() {
    const q = new URLSearchParams();
    q.set('var', S.var);
    if (S.day) q.set('day', S.day);
    if (S.hour) q.set('hour', S.hour);
    if (S.loc) q.set('loc', S.loc);
    // the view only when zoomed in: a hundredth of a unit places the centre
    // within a pixel at any zoom the page allows (a tenth was 5 px out at
    // 96 times), and the whole country needs no parameters
    if (S.view && S.view.z > 1.02) { q.set('z', S.view.z.toFixed(2)); q.set('cx', S.view.cx.toFixed(2)); q.set('cy', S.view.cy.toFixed(2)); }
    try { history.replaceState(null, '', '?' + q.toString()); } catch (e) { /* a file: URL, or history refused */ }
  }

  // ---- colour: a value on the variable's pinned ramp, interpolated between
  //      stops so a 320 by 200 field reads as a field rather than nine bands
  const hex = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
  function shade(v) {
    const Vv = V();
    if (v == null || (Vv.zeroClear && v <= 0)) return null;
    const f = Math.max(0, Math.min(1, (v - Vv.lo) / (Vv.hi - Vv.lo)));
    const p = f * (Vv.ramp.length - 1), i = Math.min(Vv.ramp.length - 2, Math.floor(p)), t = p - i;
    const a = hex(Vv.ramp[i]), b = hex(Vv.ramp[i + 1]);
    return [0, 1, 2].map(k => Math.round(a[k] + (b[k] - a[k]) * t));
  }
  const css = rgb => (rgb ? 'rgb(' + rgb.join(',') + ')' : 'var(--panel)');

  // ---- the files, each fetched once and kept
  const once = (map, key, cadence, opts) => {
    if (!map.has(key)) map.set(key, WXD.get(DATA + key, cadence, opts));
    return map.get(key);
  };
  const dayFile = day => once(S.days, 'days/' + day + '.json', 60);
  const locFile = (id, day) => once(S.locs, 'loc/' + id + '/' + day + '.json', 60);
  // a frame is 256 KB of ints keyed by its valid hour, cheap to refetch and
  // the same on every read, so it is never written to this browser's
  // storage: sixteen of them fill the quota and every later page's save fails
  const frameFile = (hv, day, hh) => once(S.frames, 'grid/' + PRODUCT + '/' + hv + '/' + stampOf(day, hh) + '.json', 60, { store: false });
  const hoursListed = () => {
    const g = ((S.grid || {}).frames || {})[PRODUCT] || {};
    return ((g[V().hourly] || {})[S.day] || []).slice().sort();
  };

  /* One location's day for the selected variable, with the status the page
     draws it in. A day is final at its cut-off, 12:45 AM local time the next
     day, on the files published by then (owner's ruling 2026-10-14), and a
     place with no hour by then is marked for event review and has no value.
     A day short of its hours carries its count; a closed day comes only from
     files written before the cut-off rule. `word` is what the chart rule and
     the tooltips call the value: a value that can still move is `running`,
     one that cannot is `resolved`, and a closed day's value is `closed`. */
  function resolution(entry, varKey) {
    const e = entry && entry[PRODUCT];
    if (!e) return null;
    const inReview = varKey === 'precip' ? !!(e.precip && e.precip.review) : !!e.review;
    if (inReview) {
      const total = hoursInDay(e);
      return { value: null, exact: null, at: null, kind: 'review', label: 'event review', hours: 0, total,
               provisional: true, word: 'review', status: 'event review', review: true };
    }
    if (!e[varKey]) return null;
    const val = e[varKey], total = hoursInDay(e);
    let n = e.hours || 0;
    let kind;
    if (e.closed) kind = 'closed';
    else if (varKey === 'precip') kind = val.resolved ? 'final' : 'provisional';
    else kind = e.final ? 'final' : 'provisional';
    // the precipitation's own hour count, which an hour without its analysis can differ from
    if (varKey === 'precip' && val.hours != null) n = val.hours;
    const count = n + ' of ' + total + ' hours';
    const provisional = kind !== 'final';
    // a day short of hours says so whatever its state: a final day that
    // resolved on the hours available (owner's decision 2026-09-29) reads
    // "final, 23 of 24 hours"
    const label = kind === 'closed' ? 'closed, ' + count : n < total ? kind + ', ' + count : kind;
    return { value: val.value, exact: val.exact, at: val.at || null, kind, label, hours: n, total, provisional,
             word: kind === 'closed' ? 'closed' : provisional ? 'running' : 'resolved', status: label };
  }

  async function init() {
    WXC.chrome('analysis-resolution.html');
    readUrl();
    const host = $('#anaMap'), strip = $('#pageStatus');
    const ri = await WXD.get(DATA + 'index.json', 10);
    /* The index's `asof` is the newest analysis valid hour, which NOAA's own
       lag keeps 47 to 107 minutes behind the clock, so judged against the ten
       minute job cadence it would read as behind on every load. The job's
       own time is `written`, and that is what the pill reports, with the age
       and the stale test recomputed the way data.js computes them. The valid
       hours stay visible beside it. */
    const job = Object.assign({}, ri);
    if (ri.data && ri.data.written && !isNaN(Date.parse(ri.data.written))) {
      job.asof = Date.parse(ri.data.written);
      job.ageMin = (Date.now() - job.asof) / 6e4;
      job.stale = job.ageMin > 2 * 10;
    }
    if (strip) { strip.textContent = ''; strip.appendChild(WXC.statusEl([job], 10)); }
    const base = await fetch(WXC.asset('basemap.json')).then(x => x.json()).catch(() => null);
    S.index = ri.data;
    if (!S.index || !base) {
      host.appendChild(h('p', { class: 'cap' }, 'The analysis index is not available right now. ' + STATEMENT));
      return;
    }
    const rg = await WXD.get(DATA + 'grid/index.json', 10);
    S.grid = rg.data || { frames: {} };
    const src = S.index.sources || {};
    const validAt = s => (s && s.latest ? String(s.latest).replace('T', ' ').replace(/:00Z$/, ' UTC') : 'none read');
    const bar0 = $('#anaBar');
    if (bar0) bar0.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'anaNewest',
      text: 'newest ' + PRODUCT_NAME + ' analysis ' + validAt(src[PRODUCT]) }));
    const days = (S.index.days || []).slice();
    if (!days.length) {
      host.appendChild(h('p', { class: 'cap' }, 'No days have been read yet. ' + STATEMENT));
      return;
    }
    // the page opens on the newest day every place has resolved, so a first
    // look shows how the contracts settled rather than a day still running;
    // a day in the address wins, and an index without the field (older
    // files, the first minutes after a deploy) opens on the newest day
    const resolvedDay = S.index.lastResolvedDay;
    if (days.indexOf(S.day) < 0) S.day = (resolvedDay && days.indexOf(resolvedDay) >= 0) ? resolvedDay : days[days.length - 1];
    const tip = WXC.tooltip();
    const foot = $('#foot');
    if (foot) foot.textContent = 'Analyses are NOAA’s Real-Time Mesoscale Analysis (RTMA), read from NOAA Open Data on AWS, or from NCEP’s NOMADS server for a file NOAA Open Data lacks. '
      + 'Times are analysis valid times, never fetch times. ' + STATEMENT;

    // ---- The controls are the day and the place, each a button that opens
    //      a popover, and Zoom to the cell. The variable is switched on the map.
    const bar = $('#anaBar');
    /* A day's state from the index's dayStatus. A day is final once every
       place has resolved, in event review when every place still open is in
       review, closed on a file written before the cut-off rule of 14
       October, and provisional otherwise; an index without the field gives
       no state. The calendar marks each listed day with it, and the day
       button names any state but final, so the reader sees why the page
       opened where it did. */
    const status = S.index.dayStatus || {};
    const dayKind = d => {
      const st = status[d];
      if (!st || !st.places) return null;
      if (st.resolved === st.places) return 'final';
      if ((st.review || 0) && st.resolved + (st.review || 0) >= st.places) return 'review';
      return st.resolved + (st.closed || 0) + (st.review || 0) >= st.places ? 'closed' : 'provisional';
    };
    const KIND = { final: 'final', provisional: 'provisional', review: 'event review', closed: 'closed' };
    const dayMark = d => { const k = dayKind(d); return k && k !== 'final' ? ' · ' + KIND[k] : ''; };

    /* The two popovers work alike. Each opens under its button, which says
       whether it is open. Escape closes it and puts focus back on the button,
       as a pick does, and a click outside it or focus moving out of it closes
       it. Only one is open at a time. It is kept inside the page at any
       width, moved left as far as it has to go, so a phone never scrolls
       sideways. `draw` fills it, `focus` places the focus once it is in
       place, `fit` follows a resize and `close` tidies up. */
    let openPop = null;
    function popover(btn, panel, opts) {
      const wrap = h('span', { class: 'anapop' }, [btn, panel]);
      const P = { node: wrap, isOpen: () => !panel.hidden };
      const outside = ev => { if (!wrap.contains(ev.target)) P.close(false); };
      // Escape is heard on the whole document while the panel is open, so it
      // still closes it when the focus has fallen to the page, as it does
      // when a focused button turns disabled
      const esc = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); P.close(true); } };
      const fit = () => {
        const W = document.documentElement.clientWidth;
        panel.style.left = '';
        panel.style.maxWidth = (W - 16) + 'px';
        const r = panel.getBoundingClientRect(), over = r.right - (W - 8);
        if (over > 0) panel.style.left = -Math.min(over, Math.max(0, r.left - 8)) + 'px';
        if (opts.fit) opts.fit();
      };
      P.open = how => {
        if (openPop && openPop !== P) openPop.close(false);
        openPop = P;
        panel.hidden = false;
        btn.setAttribute('aria-expanded', 'true');
        opts.draw();
        fit();
        document.addEventListener('pointerdown', outside, true);
        document.addEventListener('keydown', esc, true);
        window.addEventListener('resize', fit);
        opts.focus(how);
      };
      P.close = refocus => {
        if (panel.hidden) return;
        panel.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
        if (openPop === P) openPop = null;
        document.removeEventListener('pointerdown', outside, true);
        document.removeEventListener('keydown', esc, true);
        window.removeEventListener('resize', fit);
        if (opts.close) opts.close();
        if (refocus) btn.focus({ preventScroll: true });
      };
      // a touch opens without raising a phone's keyboard; a click or a key
      // puts the focus straight into the panel
      let pointer = '';
      btn.addEventListener('pointerdown', ev => { pointer = ev.pointerType || ''; });
      btn.addEventListener('click', ev => {
        const how = ev.detail > 0 && pointer === 'touch' ? 'touch' : 'other';
        pointer = '';
        if (P.isOpen()) P.close(true); else P.open(how);
      });
      wrap.addEventListener('focusout', ev => {
        const to = ev.relatedTarget;
        if (to && !wrap.contains(to)) P.close(false);
      });
      return P;
    }

    /* ---- The day is a button that shows it and opens a month calendar on
       which only the listed days can be picked. The arrows move between listed
       days, a week at a time up and down, Page Up and Page Down move a month
       among the months that hold listed days, Home and End go to the first
       and last listed day of the month on view, and Enter picks. */
    const listed = new Set(days);
    const months = Array.from(new Set(days.map(d => d.slice(0, 7)))).sort();
    const longDate = d => new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
    const monthName = ym => new Date(ym + '-15T12:00:00Z').toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    const addDays = (d, n) => new Date(Date.parse(d + 'T12:00:00Z') + n * 864e5).toISOString().slice(0, 10);
    const WEEK = [['Su', 'Sunday'], ['Mo', 'Monday'], ['Tu', 'Tuesday'], ['We', 'Wednesday'], ['Th', 'Thursday'], ['Fr', 'Friday'], ['Sa', 'Saturday']];
    const dayV = h('span', { class: 'v' });
    const dayBtn = h('button', { type: 'button', class: 'anapopbtn', id: 'anaDayBtn', 'aria-haspopup': 'dialog', 'aria-expanded': 'false',
      'aria-controls': 'anaCal', title: 'Pick a day. The page opens on the newest day every place has resolved.' },
      [h('span', { class: 'k', text: 'Day' }), ' ', dayV]);
    const calTitle = h('span', { class: 'anacalttl', id: 'anaCalTitle', 'aria-live': 'polite' });
    const calPrev = h('button', { type: 'button', class: 'anacalnav', id: 'anaCalPrev', text: '◀' });
    const calNext = h('button', { type: 'button', class: 'anacalnav', id: 'anaCalNext', text: '▶' });
    const calGrid = h('table', { class: 'anacalgrid', id: 'anaCalGrid', role: 'grid', 'aria-labelledby': 'anaCalTitle' });
    const calKey = h('div', { class: 'anacalkey', id: 'anaCalKey' });
    const cal = h('div', { class: 'anapanel anacal', id: 'anaCal', role: 'dialog', 'aria-label': 'Pick a day', tabindex: '-1', hidden: '' },
      [h('div', { class: 'anacalhead' }, [calPrev, calTitle, calNext]), calGrid, calKey]);
    ['final', 'provisional', 'review', 'closed'].filter(k => k !== 'closed' || days.some(d => dayKind(d) === 'closed'))
      .forEach(k => calKey.appendChild(h('span', {}, [h('i', { class: 'mk ' + k, 'aria-hidden': 'true' }), KIND[k]])));
    calKey.appendChild(h('span', { class: 'off', text: 'Days in grey are not on file and cannot be picked.' }));
    let calMonth = null, calFocus = null;
    function drawMonth(ym) {
      calMonth = ym;
      const y = +ym.slice(0, 4), m = +ym.slice(5, 7);
      const lead = new Date(Date.UTC(y, m - 1, 1)).getUTCDay(), n = new Date(Date.UTC(y, m, 0)).getUTCDate();
      calTitle.textContent = monthName(ym);
      const i = months.indexOf(ym);
      calPrev.disabled = i <= 0;
      calNext.disabled = i < 0 || i >= months.length - 1;
      calPrev.setAttribute('aria-label', i > 0 ? 'Earlier month, ' + monthName(months[i - 1]) : 'No earlier month on file');
      calNext.setAttribute('aria-label', i >= 0 && i < months.length - 1 ? 'Later month, ' + monthName(months[i + 1]) : 'No later month on file');
      // the grid's one tab stop is the day that had it when that day is on
      // view, else the picked day, else the month's first listed day
      if (!calFocus || calFocus.slice(0, 7) !== ym) calFocus = S.day && S.day.slice(0, 7) === ym ? S.day : days.find(d => d.slice(0, 7) === ym) || null;
      calGrid.textContent = '';
      const head = h('tr');
      WEEK.forEach(w => head.appendChild(h('th', { scope: 'col', abbr: w[1], text: w[0] })));
      calGrid.appendChild(h('thead', {}, [head]));
      const body = h('tbody');
      let row = h('tr');
      for (let k = 0; k < lead; k++) row.appendChild(h('td', { class: 'pad' }));
      for (let k = 1; k <= n; k++) {
        const d = ym + '-' + String(k).padStart(2, '0');
        const td = h('td', { 'data-day': d }, [h('span', { class: 'n', text: String(k) })]);
        if (listed.has(d)) {
          const kd = dayKind(d);
          td.className = 'on';
          td.setAttribute('data-status', kd || 'none');
          td.setAttribute('aria-selected', d === S.day ? 'true' : 'false');
          td.setAttribute('aria-label', longDate(d) + (kd ? ', ' + KIND[kd] : ''));
          td.tabIndex = d === calFocus ? 0 : -1;
          if (kd) td.appendChild(h('i', { class: 'mk ' + kd, 'aria-hidden': 'true' }));
        } else {
          td.className = 'off';
          td.setAttribute('aria-disabled', 'true');
          td.setAttribute('aria-label', longDate(d) + ', not on file');
        }
        row.appendChild(td);
        if ((lead + k) % 7 === 0) { body.appendChild(row); row = h('tr'); }
      }
      if (row.children.length) {
        while (row.children.length < 7) row.appendChild(h('td', { class: 'pad' }));
        body.appendChild(row);
      }
      calGrid.appendChild(body);
    }
    function focusDay(d) {
      if (!d) return;
      calFocus = d;
      if (d.slice(0, 7) !== calMonth) drawMonth(d.slice(0, 7));
      else calGrid.querySelectorAll('td.on').forEach(td => { td.tabIndex = td.dataset.day === d ? 0 : -1; });
      const td = calGrid.querySelector('td[data-day="' + d + '"]');
      if (td) td.focus();
    }
    const stepDay = (d, dir) => { const i = days.indexOf(d) + dir; return i >= 0 && i < days.length ? days[i] : null; };
    // a week up or down; past the listed days' edge, the nearest listed day
    // in that direction
    function weekDay(d, dir) {
      const t = addDays(d, 7 * dir);
      if (listed.has(t)) return t;
      if (dir < 0) { const c = days.filter(x => x <= t); return c.length ? c[c.length - 1] : (days[0] < d ? days[0] : null); }
      const c = days.filter(x => x >= t);
      return c.length ? c[0] : (days[days.length - 1] > d ? days[days.length - 1] : null);
    }
    // the same date in the next or the last month that holds listed days, or
    // the listed day nearest it there
    function monthDay(d, dir) {
      const i = months.indexOf(d.slice(0, 7)) + dir;
      if (i < 0 || i >= months.length) return null;
      const ym = months[i], inMonth = days.filter(x => x.slice(0, 7) === ym);
      const last = new Date(Date.UTC(+ym.slice(0, 4), +ym.slice(5, 7), 0)).getUTCDate();
      const t = Date.parse(ym + '-' + String(Math.min(+d.slice(8, 10), last)).padStart(2, '0'));
      return inMonth.reduce((b, x) => (Math.abs(Date.parse(x) - t) < Math.abs(Date.parse(b) - t) ? x : b), inMonth[0]);
    }
    const pickDay = d => {
      if (!listed.has(d)) return;
      dayPop.close(true);
      if (d !== S.day) { S.day = d; changed('day'); }
    };
    calGrid.addEventListener('click', ev => {
      const td = ev.target.closest && ev.target.closest('td.on');
      if (td) pickDay(td.dataset.day);
    });
    calGrid.addEventListener('keydown', ev => {
      const td = ev.target.closest && ev.target.closest('td.on');
      if (!td) return;
      const d = td.dataset.day, onView = days.filter(x => x.slice(0, 7) === calMonth);
      let to = null;
      switch (ev.key) {
        case 'ArrowLeft': to = stepDay(d, -1); break;
        case 'ArrowRight': to = stepDay(d, 1); break;
        case 'ArrowUp': to = weekDay(d, -1); break;
        case 'ArrowDown': to = weekDay(d, 1); break;
        case 'PageUp': to = monthDay(d, -1); break;
        case 'PageDown': to = monthDay(d, 1); break;
        case 'Home': to = onView[0]; break;
        case 'End': to = onView[onView.length - 1]; break;
        case 'Enter': case ' ': ev.preventDefault(); pickDay(d); return;
        default: return;
      }
      ev.preventDefault();
      if (to) focusDay(to);
    });
    // a month arrow that turns disabled under the focus hands it to the
    // grid, so the keyboard is never left on nothing
    const monthStep = (dir, from) => {
      const i = months.indexOf(calMonth) + dir;
      if (i < 0 || i >= months.length) return;
      calFocus = null;
      drawMonth(months[i]);
      if (from && from.disabled) focusDay(calFocus);
    };
    calPrev.onclick = () => monthStep(-1, calPrev);
    calNext.onclick = () => monthStep(1, calNext);
    const dayPop = popover(dayBtn, cal, {
      draw: () => { calFocus = S.day; drawMonth((S.day || days[days.length - 1]).slice(0, 7)); },
      focus: () => focusDay(calFocus),
    });
    const syncDay = () => {
      dayV.textContent = S.day ? dayLabel(S.day) + dayMark(S.day) : '';
      dayBtn.setAttribute('data-day', S.day || '');
    };

    /* ---- The place is a button that shows the open place and opens a small
       map of the places with a box to type a name. The map is for the
       pointer. The box is for the keyboard, a screen reader and a phone, so
       the map is hidden from assistive technology and every place can be
       reached from the box. Typing filters the list, the arrows move through
       it, and Enter picks the highlighted place, the first match unless the
       arrows moved on. The map is the page's own projection and outlines. */
    const locV = h('span', { class: 'v' });
    const locBtn = h('button', { type: 'button', class: 'anapopbtn', id: 'anaLocBtn', 'aria-haspopup': 'dialog', 'aria-expanded': 'false',
      'aria-controls': 'anaPlaces', title: 'Pick a place on a map of the places, or type its name' },
      [h('span', { class: 'k', text: 'Place' }), ' ', locV]);
    const plQ = h('input', { type: 'text', id: 'anaPlaceQ', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
      'aria-controls': 'anaPlaceList', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', enterkeyhint: 'go', placeholder: 'Type a name' });
    const plCount = h('p', { class: 'cap anaplcount', id: 'anaPlaceCount', 'aria-live': 'polite' });
    const plMap = el('svg', { viewBox: '0 0 960 600', class: 'anamini', id: 'anaPlaceMap', 'aria-hidden': 'true', focusable: 'false' });
    // the list is reached through the box, so it is no tab stop of its own,
    // which a scrolling list otherwise becomes in Chromium
    const plList = h('ul', { class: 'anapllist', id: 'anaPlaceList', role: 'listbox', 'aria-label': 'Places', tabindex: '-1' });
    const plBody = h('div', { class: 'anaplbody' }, [plMap, plList]);
    const pl = h('div', { class: 'anapanel anaplaces', id: 'anaPlaces', role: 'dialog', 'aria-label': 'Pick a place', tabindex: '-1', hidden: '' },
      [h('div', { class: 'anaplrow' }, [h('label', { for: 'anaPlaceQ', text: 'Find a place' }), plQ]), plCount, plBody]);
    const byName = (a, b) => a.name.localeCompare(b.name) || a.state.localeCompare(b.state);
    const places = (S.index.locations || []).slice().sort(byName);
    plMap.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    plMap.appendChild(el('path', { d: base.statePaths, class: 'state', 'vector-effect': 'non-scaling-stroke' }));
    plMap.appendChild(el('path', { d: base.statePaths, class: 'state2', 'vector-effect': 'non-scaling-stroke' }));
    const plDots = el('g', { class: 'pds' });
    // smallest first, as on the main map, so a larger place is drawn on top
    (S.index.locations || []).slice().reverse().forEach(L => {
      const g = el('g', { class: 'pd', 'data-loc': L.id });
      g.appendChild(el('circle', { cx: L.px, cy: L.py, r: 17, class: 'ring' }));
      g.appendChild(el('circle', { cx: L.px, cy: L.py, r: 8, class: 'pt' }));
      plDots.appendChild(g);
    });
    plMap.appendChild(plDots);
    // the place nearest the pointer, within twelve screen pixels, so a dot a
    // few pixels across on a phone is still easy to hit
    function plNear(ev) {
      const m = plMap.getScreenCTM(), w = plMap.getBoundingClientRect().width;
      if (!m || !w) return null;
      const pt = plMap.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const p = pt.matrixTransform(m.inverse());
      let best = null, bd = 12 * VB.w / w;
      places.forEach(L => { const dd = Math.hypot(L.px - p.x, L.py - p.y); if (dd < bd) { bd = dd; best = L; } });
      return best;
    }
    const plHl = id => {
      plDots.querySelectorAll('g.pd.hl').forEach(g => g.classList.remove('hl'));
      const g = id && plDots.querySelector('g.pd[data-loc="' + id + '"]');
      if (g) { g.classList.add('hl'); plDots.appendChild(g); }
    };
    const norm = s => String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    // a name that starts with the text first, then a word in it that does,
    // then anything else that holds it, such as the state
    const plRank = (L, s) => {
      const n = norm(L.name);
      if (n.startsWith(s)) return 0;
      if (n.split(/[\s.-]+/).some(w => w.startsWith(s))) return 1;
      return (n + ', ' + norm(L.state)).includes(s) ? 2 : -1;
    };
    let plMatches = places.slice(), plActive = -1;
    function plSetActive(i) {
      plActive = i;
      Array.from(plList.children).forEach((li, k) => { li.classList.toggle('act', k === i); li.setAttribute('aria-selected', k === i ? 'true' : 'false'); });
      const li = plList.children[i];
      if (li) {
        plQ.setAttribute('aria-activedescendant', li.id);
        // the list scrolls itself, never the page
        const top = li.offsetTop, bot = top + li.offsetHeight;
        if (top < plList.scrollTop) plList.scrollTop = top;
        else if (bot > plList.scrollTop + plList.clientHeight) plList.scrollTop = bot - plList.clientHeight;
      } else plQ.removeAttribute('aria-activedescendant');
      plHl(li ? li.dataset.loc : null);
    }
    function plFilter() {
      const typed = plQ.value.trim(), s = norm(typed);
      plMatches = s ? places.map(L => [plRank(L, s), L]).filter(x => x[0] >= 0).sort((a, b) => a[0] - b[0] || byName(a[1], b[1])).map(x => x[1])
        : places.slice();
      plList.textContent = '';
      plMatches.forEach(L => {
        const li = h('li', { role: 'option', id: 'anaPlace-' + L.id, 'data-loc': L.id, 'aria-selected': 'false' }, [L.name + ', ' + L.state]);
        if (L.id === S.loc) { li.classList.add('cur'); li.appendChild(h('span', { class: 'cur', text: ' · open' })); }
        li.addEventListener('click', () => pickPlace(L));
        li.addEventListener('pointerenter', () => plHl(L.id));
        plList.appendChild(li);
      });
      const hit = new Set(plMatches.map(L => L.id));
      plDots.querySelectorAll('g.pd').forEach(g => g.classList.toggle('dim', !!s && !hit.has(g.dataset.loc)));
      plQ.setAttribute('aria-expanded', plMatches.length ? 'true' : 'false');
      plCount.textContent = !s ? places.length + ' places. Type to narrow the list, or click one on the map.'
        : plMatches.length ? plMatches.length + (plMatches.length === 1 ? ' place matches.' : ' places match.') + ' Enter opens the highlighted one.'
          : 'No place matches ' + typed + '.';
      plSetActive(s && plMatches.length ? 0 : -1);
    }
    plQ.addEventListener('input', plFilter);
    plQ.addEventListener('keydown', ev => {
      const n = plMatches.length;
      if (ev.key === 'ArrowDown') { ev.preventDefault(); if (n) plSetActive(plActive < 0 ? 0 : Math.min(n - 1, plActive + 1)); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); if (n) plSetActive(Math.max(0, plActive - 1)); }
      else if (ev.key === 'Enter') {
        ev.preventDefault();
        const L = plActive >= 0 ? plMatches[plActive] : (plQ.value.trim() ? plMatches[0] : null);
        if (L) pickPlace(L);
      }
    });
    plMap.addEventListener('pointermove', ev => {
      const L = plNear(ev);
      plMap.classList.toggle('hot', !!L);
      plHl(L ? L.id : (plMatches[plActive] || {}).id);
      if (L) tip.show(ev, tip.rows(L.name + ', ' + L.state, [], 'click to open the place')); else tip.hide();
    });
    plMap.addEventListener('pointerleave', () => { tip.hide(); plHl((plMatches[plActive] || {}).id); });
    plMap.addEventListener('click', ev => { const L = plNear(ev); if (L) pickPlace(L); });
    const pickPlace = L => { locPop.close(true); openPlace(L); };
    const locPop = popover(locBtn, pl, {
      draw: () => { plQ.value = ''; plFilter(); plList.scrollTop = 0; },
      // side by side, the list is as tall as the map beside it
      fit: () => { plList.style.maxHeight = getComputedStyle(plBody).flexDirection === 'column' ? '' : Math.max(160, Math.round(plMap.getBoundingClientRect().height)) + 'px'; },
      focus: how => { if (how === 'touch') pl.focus({ preventScroll: true }); else plQ.focus(); },
      close: () => tip.hide(),
    });
    const syncPlace = () => {
      const L = S.loc ? locOf(S.loc) : null;
      locV.textContent = L ? L.name + ', ' + L.state : 'none picked';
      locBtn.setAttribute('data-loc', L ? L.id : '');
      plDots.querySelectorAll('g.pd').forEach(g => g.classList.toggle('cur', !!L && g.dataset.loc === L.id));
    };

    // Zoom to the cell beside the place button, live once a place is picked
    // and its index entry carries the cell geometry
    const zoomCellB = h('button', { text: 'Zoom to the cell', id: 'anaZoomCell', title: 'zoom the map to the picked place’s resolving cell' });
    zoomCellB.onclick = () => { if (S.loc) zoomToCell(locOf(S.loc)); };
    const syncPick = () => { syncPlace(); zoomCellB.disabled = !(S.loc && cellGeom(locOf(S.loc))); };
    bar.insertBefore(dayPop.node, strip);
    bar.insertBefore(locPop.node, strip);
    bar.insertBefore(zoomCellB, strip);
    // the day's values as CSV, raw (the files' own units, every hour and
    // place) and processed (the day's values as they resolve), rewritten by
    // the job with the day
    const csvLinks = h('span', { class: 'anacsv', id: 'anaCsv' });
    bar.insertBefore(csvLinks, strip);
    setCsv = () => {
      csvLinks.textContent = '';
      if (!S.day) return;
      const at = kind => WXD.base() + '/snapshots/' + DATA + 'csv/' + S.day + '-' + kind + '.csv';
      csvLinks.appendChild(document.createTextNode('CSV of the day: '));
      csvLinks.appendChild(h('a', { href: at('raw'), download: S.day + '-raw.csv', text: 'raw values' }));
      csvLinks.appendChild(document.createTextNode(' · '));
      csvLinks.appendChild(h('a', { href: at('processed'), download: S.day + '-processed.csv', text: 'processed values' }));
    };

    // ---- the map: land, the frame under a clip of the states, outlines, dots
    const svg = el('svg', { viewBox: '0 0 960 600', id: 'vmap' });
    const defs = el('defs');
    const clip = el('clipPath', { id: 'anaClip' });
    clip.appendChild(el('path', { d: base.statePaths }));
    defs.appendChild(clip);
    svg.appendChild(defs);
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    /* The frame is a 320 by 200 picture stretched over the 960 by 600 view,
       so each lattice point is a 3 px square. Pixelated rendering keeps the
       squares square; smoothing would invent values between points that the
       lattice never sampled. */
    const img = el('image', { id: 'anaFrame', x: 0, y: 0, width: 960, height: 600,
                              preserveAspectRatio: 'none', 'clip-path': 'url(#anaClip)',
                              style: 'image-rendering:pixelated;image-rendering:crisp-edges' });
    svg.appendChild(img);
    // the window cells of cell mode sit under the outlines and the dots:
    // clipped to the land like the raster, so the shoreline stays the
    // reference a reader orients by, while the resolving cell's outline
    // and label (in the dots layer) are drawn over everything
    const cells = el('g', { id: 'vcells', 'clip-path': 'url(#anaClip)' });
    svg.appendChild(cells);
    svg.appendChild(el('path', { d: base.statePaths, class: 'state', 'vector-effect': 'non-scaling-stroke' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2', 'vector-effect': 'non-scaling-stroke' }));
    const dots = el('g', { id: 'vdots' });
    svg.appendChild(dots);
    const canvas = document.createElement('canvas');
    canvas.width = (S.index.lattice || {}).cols || 320;
    canvas.height = (S.index.lattice || {}).rows || 200;
    const pitch = (S.index.lattice || {}).pitch || 3;
    let frame = null;          // the frame on screen, for the hover
    let dayEntries = null;     // the day file's locations, for the dots
    let dayRes = null;         // the day file's fetch result, so a file that could not be read is not a day with no hours

    function drawFrame(f) {
      frame = f;
      if (!f || !f.values) { img.removeAttribute('href'); if (cellOn) drawWindows(); return; }
      const ctx = canvas.getContext('2d');
      const im = ctx.createImageData(canvas.width, canvas.height);
      const d = im.data, sc = f.scale || 1, n = Math.min(f.values.length, canvas.width * canvas.height);
      for (let k = 0; k < n; k++) {
        const v = f.values[k];
        const c = v == null ? null : shade(v / sc);
        if (!c) continue;
        d[k * 4] = c[0]; d[k * 4 + 1] = c[1]; d[k * 4 + 2] = c[2]; d[k * 4 + 3] = 255;
      }
      ctx.putImageData(im, 0, 0);
      img.setAttribute('href', canvas.toDataURL('image/png'));
      // the windows are the frame's, so a new hour redraws them in cell mode
      if (cellOn) drawWindows();
    }

    // ---- zoom and pan, lifted from varmap.js: the viewBox moves, the
    //      geometry stays, and the dots are redrawn a beat later at a size
    //      that holds on screen
    const VIEW0 = { x: 0, y: 0, w: VB.w, h: VB.h };
    let view = Object.assign({}, VIEW0);
    /* 96 times in, against varmap's 14: a 2.5 km cell is about 0.48 viewBox
       units wide (docs/analysis.md section 1), so at 14 it is 8 screen px
       on a 1200 px page and never readable; at 96 it is about 55 px, and
       the view is 10 units across, which the wheel reaches in 28 steps of
       1.18 and clampView holds like any other size. */
    const MAXZ = 96;
    const GS = () => view.w / VIEW0.w;
    let rescale = null;
    // the address follows the view once the zoom settles, not on every
    // wheel tick or pointer move (Safari refuses a replaceState storm)
    const scheduleRescale = () => { clearTimeout(rescale); rescale = setTimeout(() => { drawDots(); writeUrl(); }, 140); };
    function applyView() {
      // three decimals: at 96 times in a hundredth of a unit is a screen
      // pixel, and a pan rounded to it stepped
      svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].map(n => Math.round(n * 1000) / 1000).join(' '));
      const z = VIEW0.w / view.w;
      S.view = z > 1.02 ? { z, cx: view.x + view.w / 2, cy: view.y + view.h / 2 } : null;
      const lbl = $('#anaZoomLevel');
      if (lbl) lbl.textContent = z < 1.02 ? 'whole country' : Math.round(z * 10) / 10 + '×';
      svg.classList.toggle('grab', z > 1.02);
    }
    // a view the address carried is restored before the first draw
    if (S.view) {
      const z = Math.min(Math.max(S.view.z, 1), MAXZ);
      view.w = VIEW0.w / z; view.h = VIEW0.h / z;
      view.x = S.view.cx - view.w / 2; view.y = S.view.cy - view.h / 2;
      clampView(); applyView();
    }
    function clampView() {
      view.x = Math.min(Math.max(view.x, VIEW0.x), VIEW0.x + VIEW0.w - view.w);
      view.y = Math.min(Math.max(view.y, VIEW0.y), VIEW0.y + VIEW0.h - view.h);
    }
    function zoomAbout(factor, cx, cy) {
      const z = VIEW0.w / view.w;
      const want = Math.min(Math.max(z * factor, 1), MAXZ);
      if (Math.abs(want - z) < 1e-6) return;
      const w = VIEW0.w / want, hh = VIEW0.h / want;
      view.x = cx - (cx - view.x) * (w / view.w);
      view.y = cy - (cy - view.y) * (hh / view.h);
      view.w = w; view.h = hh;
      clampView(); applyView(); scheduleRescale();
    }
    const resetView = () => { view = Object.assign({}, VIEW0); applyView(); scheduleRescale(); };
    function atPoint(ev) {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      return [q.x, q.y];
    }
    svg.addEventListener('wheel', ev => {
      ev.preventDefault();
      const [cx, cy] = atPoint(ev);
      zoomAbout(ev.deltaY < 0 ? 1.18 : 1 / 1.18, cx, cy);
    }, { passive: false });
    /* A pan starts on pointerdown but takes the pointer only once it has
       moved past the click threshold. Capturing on pointerdown made Chromium
       deliver every click to the svg itself, so nothing under the pointer
       (a window cell, the label, the outline, the position dot, a plain dot)
       opened while the map was zoomed. Until the threshold the pointer is
       tracked on the svg with a window-level pointerup, so a click that
       ends off the map still ends the gesture. */
    let drag = null, moved = 0;
    const DRAG_PX = 4;
    svg.addEventListener('pointerdown', ev => {
      if (VIEW0.w / view.w <= 1.02) return;            // nothing to pan at full extent
      if (ev.button != null && ev.button !== 0) return;
      drag = { sx: ev.clientX, sy: ev.clientY, vx: view.x, vy: view.y, id: ev.pointerId, captured: false };
      moved = 0;
    });
    svg.addEventListener('pointermove', ev => {
      if (!drag || ev.pointerId !== drag.id) return;
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      moved = Math.max(moved, Math.hypot(dx, dy));           // straight-line distance, so a diagonal wobble is still a click
      if (moved <= DRAG_PX) return;                    // still a click in the making
      if (!drag.captured) {
        drag.captured = true; svg.classList.add('grabbing');
        try { svg.setPointerCapture(drag.id); } catch (e) { /* the pointer is gone; the window pointerup ends it */ }
      }
      const k = view.w / svg.getBoundingClientRect().width;
      view.x = drag.vx - dx * k; view.y = drag.vy - dy * k;
      clampView(); applyView();
    });
    const endDrag = ev => {
      if (!drag || (ev && ev.pointerId != null && ev.pointerId !== drag.id)) return;
      if (drag.captured) { try { svg.releasePointerCapture(drag.id); } catch (e) { /* already released */ } }
      drag = null; svg.classList.remove('grabbing');
      // a pan moves which windows are in view, and the address
      if (moved > DRAG_PX) scheduleRescale();
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
    // a pan that ends over a dot must not also open that location
    svg.addEventListener('click', ev => {
      if (moved > DRAG_PX) { ev.stopPropagation(); ev.preventDefault(); moved = 0; }
    }, true);

    /* Hover reads the frame under the pointer. The pointer's viewBox
       coordinates divided by the lattice pitch name the lattice column and
       row, and the frame is row-major from the top left, so the value is one
       lookup. Over a dot, the dot's day value is added. */
    svg.addEventListener('mousemove', ev => {
      const [x, y] = atPoint(ev);
      const cols = canvas.width, rows = canvas.height;
      const c = Math.floor(x / pitch), r = Math.floor(y / pitch);
      const pairs = [];
      // the frame's own precision: its ints are the value times `scale`
      const hourDec = frame && frame.scale > 1 ? Math.max(0, Math.round(Math.log10(frame.scale))) : (V().hourly === 'precip' ? 2 : 1);
      const closest = sel => (ev.target && ev.target.closest ? ev.target.closest(sel) : null);
      const wc = cellOn ? closest('path.wcell') : null;
      const g = closest('g.dot');
      if (wc) {
        /* a window cell: its grid coordinates are the resolving cell's plus
           the cell's offset in the window, and its value is the hour's
           analysis there, the number a 2.5 km cell would resolve on */
        const grp = wc.closest('g.wwin'), L = locOf(grp.dataset.loc), win = windowOf(L);
        const geo = L && cellGeom(L, win && win.grid), a = +wc.dataset.a, b = +wc.dataset.b;
        const v = windowValue(win, a, b);
        pairs.push([L ? L.name + ', ' + L.state : grp.dataset.loc, geo && geo.i != null ? 'cell i ' + (geo.i + a) + ', j ' + (geo.j + b) : 'cell']);
        // a null is off the grid when its index leaves the grid, otherwise
        // the analysis had no value there this hour (a bitmap gap); a dry
        // precipitation cell is a zero, and says so
        const off = geo && geo.i != null && !onGrid(geo.grid, geo.i + a, geo.j + b);
        pairs.push([HOURLY[V().hourly] + ' in the cell', v == null ? (off ? 'off the grid' : 'missing in this hour') : withUnit(v, hourDec)]);
      } else if (cellOn) {
        // the field is hidden at this zoom, so nothing is read under the
        // pointer; over a mark the resolving cell's own row below says it all
        if (!g) pairs.push(['Lattice field', 'hidden at this zoom']);
      } else if (frame && frame.values && c >= 0 && r >= 0 && c < cols && r < rows) {
        const v = frame.values[r * cols + c];
        pairs.push(['Cell under the pointer', v == null ? 'off the grid' : withUnit(v / (frame.scale || 1), hourDec)]);
      } else if (!frame) {
        pairs.push(['Cell under the pointer', 'no frame for this hour']);
      }
      let foot = '';
      if (g && !wc) {
        const L = locOf(g.dataset.loc);
        const res = resolution((dayEntries || {})[g.dataset.loc], S.var);
        const geo = cellOn && L ? cellGeom(L) : null;
        if (geo && geo.i != null) {
          // the resolving cell itself, with the hour's value there when the frame carries the window
          const v = windowValue(windowOf(L), 0, 0);
          pairs.push(['Resolving cell', 'i ' + geo.i + ', j ' + geo.j + (v == null ? '' : ', ' + withUnit(v, hourDec) + ' this hour')]);
        }
        pairs.push([L ? L.name + ', ' + L.state : g.dataset.loc,
                    res && res.value != null ? res.word + ' ' + withUnit(res.value) + ' (' + res.status + ')'
                    : res && res.review ? 'no value, event review'
                    : dayRes && dayRes.source === 'none' ? 'the day file could not be read' : 'no value for this day yet']);
      }
      if (g || wc) foot = 'click to open the location';
      tip.show(ev, tip.rows(V().label + ' · ' + PRODUCT_NAME + (S.hour ? ' ' + S.hour + ':00 UTC' : ''), pairs, foot));
    });
    svg.addEventListener('mouseleave', () => tip.hide());

    const locOf = id => (S.index.locations || []).find(L => L.id === id) || null;

    /* The dots: one per place, coloured by the day's resolved value of the
       selected variable, sized with it as the other maps size
       theirs. Dashed and faded when provisional, because a value that can
       still move must not read as one that cannot; hollow when the day has
       no value yet, because a missing value is a fact about the day and an
       absent dot reads as an absent place. */
    /* ---- cell mode (docs/analysis.md section 6). Past a zoom where a 2.5 km
       cell is CELL_PX wide on screen the lattice raster is hidden, because
       a 3 px lattice block stretched to that zoom is a flat patch far larger
       than a cell and would be read as the analysis; in its place each
       place's window of true cell values is drawn as parallelograms from
       the index's local basis, and the resolving cell is outlined with the
       day's value floating beside it. A frame without windows and an index
       without the geometry keys (the files the lane wrote before this) still
       render: no window cells, and no mark for a place with no geometry. */
    const CELL_PX = 12;           // the on-screen cell width that turns cell mode on
    const CELL_ZOOM_PX = 30;      // what Zoom to the cell makes the picked cell
    let cellOn = false;
    const locs = () => S.index.locations || [];
    const pxPerUnit = () => svg.getBoundingClientRect().width / view.w;    // 0 until the map is on the page
    // the grid a frame's cells are on: the RTMA precipitation is on G184,
    // every other variable on the wexp grid (section 1)
    const gridKey = () => (V().hourly === 'precip' ? 'g184' : 'wexp');
    const windowOf = L => (L && frame && frame.windows && frame.windows[L.id]) || null;
    // the two grids' extents in cells (docs/analysis.md section 1, the
    // fixed definitions in pipeline/grib2.py), so a null window cell can be
    // told off the grid from missing in the hour's bitmap
    const GRID_CELLS = { wexp: [2345, 1597], g184: [2145, 1377] };
    const onGrid = (grid, i, j) => { const n = GRID_CELLS[grid]; return !n || (i >= 0 && j >= 0 && i < n[0] && j < n[1]); };
    function windowValue(win, a, b) {
      if (!win || !win.values) return null;
      const half = win.half == null ? 10 : win.half, n = 2 * half + 1;
      if (Math.abs(a) > half || Math.abs(b) > half || win.values.length !== n * n) return null;
      const v = win.values[(b + half) * n + (a + half)];        // row-major, dj down the rows, di along each
      return v == null ? null : v / ((frame && frame.scale) || 1);
    }
    /* The geometry of a place's resolving cell on the grid `grid` (the
       frame's window grid when it has one, else the grid the variable
       uses): centre and outline in viewBox units and the basis
       `di`, `dj`, so cell (i + a, j + b) is centred at centre + a·di + b·dj.
       Null for an index written before the keys existed. */
    function cellGeom(L, grid) {
      const c = (L && L.cell) || {};
      const g = grid || gridKey();
      const px = c[g + 'Px'], basis = c[g + 'Basis'], ij = c[g];
      if (!px || !basis || !basis.di || !basis.dj) return null;
      return { grid: g, cx: px[0], cy: px[1], box: c[g + 'Box'] || null, di: basis.di, dj: basis.dj,
               i: ij ? ij[0] : null, j: ij ? ij[1] : null };
    }
    // the parallelogram of cell (a, b) of a window: centre ± di/2 ± dj/2
    function cellPath(geo, a, b) {
      const x = geo.cx + a * geo.di[0] + b * geo.dj[0], y = geo.cy + a * geo.di[1] + b * geo.dj[1];
      const hx = geo.di[0] / 2, hy = geo.di[1] / 2, kx = geo.dj[0] / 2, ky = geo.dj[1] / 2;
      const P = (sx, sy) => (x + sx * hx + sy * kx).toFixed(3) + ' ' + (y + sx * hy + sy * ky).toFixed(3);
      return 'M' + P(-1, -1) + 'L' + P(1, -1) + 'L' + P(1, 1) + 'L' + P(-1, 1) + 'Z';
    }
    // the picked place's cell on screen, or the first place's that has a
    // basis; every cell on the grid is within a few percent of the same size
    function cellWidthPx() {
      const k = pxPerUnit();
      if (!k) return 0;
      let geo = S.loc ? cellGeom(locOf(S.loc)) : null;
      for (let n = 0; !geo && n < locs().length; n++) geo = cellGeom(locs()[n]);
      return geo ? Math.hypot(geo.di[0], geo.di[1]) * k : 0;
    }
    const openPlace = L => { S.loc = L.id; writeUrl(); openLoc(true); };

    // the windows of the places in view, one path per cell, coloured as the
    // raster colours its lattice points; a null cell and precipitation's
    // clear zero leave the land showing, as they do on the raster, but are
    // still drawn as clear paths so hover can say which of the two they are
    function drawWindows() {
      cells.textContent = '';
      if (!cellOn || !frame || !frame.windows) return;
      locs().forEach(L => {
        const win = windowOf(L);
        if (!win || !win.values) return;
        const geo = cellGeom(L, win.grid);
        if (!geo) return;
        const half = win.half == null ? 10 : win.half, n = 2 * half + 1;
        if (win.values.length !== n * n) return;
        // in view: the window reaches half + 0.5 steps along each basis vector
        const reach = (half + 0.5) * (Math.hypot(geo.di[0], geo.di[1]) + Math.hypot(geo.dj[0], geo.dj[1]));
        if (geo.cx + reach < view.x || geo.cx - reach > view.x + view.w || geo.cy + reach < view.y || geo.cy - reach > view.y + view.h) return;
        const grp = el('g', { class: 'wwin', 'data-loc': L.id });
        for (let b = -half; b <= half; b++) {
          for (let a = -half; a <= half; a++) {
            const c = shade(windowValue(win, a, b));
            const attrs = { d: cellPath(geo, a, b), class: 'wcell', 'data-a': a, 'data-b': b };
            // a hairline in the panel colour between cells, so the grid reads as cells
            if (c) Object.assign(attrs, { fill: css(c), stroke: 'var(--panel)', 'stroke-width': .35, 'vector-effect': 'non-scaling-stroke' });
            // a clear cell is a transparent fill, not fill none with
            // pointer-events all: Chromium hit-tests the latter one cell off
            else Object.assign(attrs, { fill: 'transparent', stroke: 'none', class: 'wcell clear' });
            grp.appendChild(el('path', attrs));
          }
        }
        grp.onclick = () => openPlace(L);
        cells.appendChild(grp);
      });
    }

    /* The marks of cell mode, one g.dot per place with geometry so the
       keyboard and the picked-dot rules hold: the resolving cell's outline,
       a small dot at the position (the City Hall), and the day's value in a label at a
       fixed screen offset up and to the right of the cell, flipped when it
       would leave the view, with a leader to the cell's centre. Sizes are
       in screen pixels converted at the current zoom, since the viewBox
       magnifies every glyph alike. */
    function drawCellMarks() {
      const u = 1 / pxPerUnit();          // viewBox units per screen pixel
      const g0 = GS();
      locs().slice().reverse().forEach(L => {
        const geo = cellGeom(L);
        // an index entry without the geometry keys keeps its plain dot and
        // value, so a place never vanishes from the map; Zoom to the cell
        // stays disabled for it
        if (!geo) { drawPlainDot(L, g0); return; }
        const res = resolution((dayEntries || {})[L.id], S.var);
        const v = res ? res.value : null;
        const g = el('g', { class: 'dot cellmark', 'data-loc': L.id });
        const title = el('title'); title.textContent = L.name + ', ' + L.state;
        g.appendChild(title);
        if (v != null) {
          const text = fmt(v), fs = 11 * u, pad = 5 * u;
          const w = text.length * 6.6 * u + 2 * pad, hgt = 16 * u;
          // the label's lower-left corner sits 26 px up and right of the
          // cell centre, then flips left or down to stay in the view
          let lx = geo.cx + 26 * u, ly = geo.cy - 26 * u;
          if (lx + w > view.x + view.w) lx = geo.cx - 26 * u - w;
          if (ly - hgt < view.y) ly = geo.cy + 26 * u + hgt;
          const rx = lx, ry = ly - hgt;
          // the leader leaves the label at the edge point nearest the cell centre
          const ex = Math.min(Math.max(geo.cx, rx), rx + w), ey = Math.min(Math.max(geo.cy, ry), ry + hgt);
          g.appendChild(el('line', { x1: ex, y1: ey, x2: geo.cx, y2: geo.cy, stroke: 'var(--cell-line)', 'stroke-width': 1,
                                     'vector-effect': 'non-scaling-stroke', class: 'analead' }));
          const lbl = el('g', { class: 'anacl' });
          lbl.appendChild(el('rect', { x: rx, y: ry, width: w, height: hgt, rx: 3 * u, fill: 'var(--panel)', stroke: 'var(--ink)',
                                       'stroke-width': .8, 'vector-effect': 'non-scaling-stroke' }));
          lbl.appendChild(txt(text, { x: rx + w / 2, y: ry + hgt / 2 + fs * .36, 'text-anchor': 'middle', style: 'font-size:' + fs.toFixed(3) + 'px' }));
          g.appendChild(lbl);
        }
        // the outline is the projected box when the index has it, else the basis parallelogram
        const d = geo.box && geo.box.length === 4 ? 'M' + geo.box.map(p => p[0] + ' ' + p[1]).join('L') + 'Z' : cellPath(geo, 0, 0);
        // a dark border in both themes (the cells are pale in both), on a thin
        // panel-coloured halo so it still reads where the cell meets dark water
        g.appendChild(el('path', { d, fill: 'none', stroke: 'var(--panel)', 'stroke-width': 4, 'vector-effect': 'non-scaling-stroke',
                                   class: 'rcellhalo', 'pointer-events': 'none' }));
        g.appendChild(el('path', { d, fill: 'none', stroke: 'var(--cell-line)', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke',
                                   class: 'rcell ' + (v == null ? 'absent' : res.provisional ? 'prov' : 'final') }));
        g.appendChild(el('circle', { cx: L.px, cy: L.py, r: 2.2 * u, fill: 'var(--cell-line)', class: 'posdot' }));
        g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button');
        g.setAttribute('aria-label', L.name + ', ' + L.state + (v != null ? ', ' + res.word + ' ' + withUnit(v) : ', no value yet'));
        g.onclick = () => openPlace(L);
        g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openPlace(L); } });
        dots.appendChild(g);
      });
    }

    // one plain dot, at the size the zoom holds on screen (`g0` is viewBox
    // units per screen pixel at the national extent's scale)
    function drawPlainDot(L, g0) {
      const res = resolution((dayEntries || {})[L.id], S.var);
      const v = res ? res.value : null;
      const X = L.px, Y = L.py;
      const g = el('g', { class: 'dot', 'data-loc': L.id });
      const frac = v == null ? 0 : Math.min(1, Math.max(0, (v - V().lo) / (V().hi - V().lo)));
      const r = (v == null ? 4.5 : 6 + 5 * frac) * g0;
      g.appendChild(el('circle', { cx: X, cy: Y, r: r + 2.5 * g0, fill: 'var(--panel)', 'fill-opacity': .95 }));
      const attrs = { cx: X, cy: Y, r, fill: v == null ? 'var(--panel)' : css(shade(v) || hex(V().ramp[0])),
                      stroke: 'var(--ink)', 'stroke-width': .7 * g0, class: v == null ? 'absent' : (res.provisional ? 'prov' : 'final') };
      if (v != null && res.provisional) Object.assign(attrs, { 'stroke-dasharray': (2.2 * g0).toFixed(2) + ' ' + (1.8 * g0).toFixed(2),
                                                              'stroke-width': 1.1 * g0, 'fill-opacity': .55 });
      g.appendChild(el('circle', attrs));
      // the size rides in `style`: the class carries a font-size in the
      // stylesheet, and a rule beats a presentation attribute (varmap.js);
      // the value is in the ink (anaval), the muted grey was faint on dark
      if (v != null) g.appendChild(txt(fmt(v), { x: X, y: Y + 3.2 * g0, class: 'ax anaval', 'text-anchor': 'middle',
                                                style: 'font-size:' + (9.5 * g0).toFixed(2) + 'px', 'pointer-events': 'none' }));
      if (g0 < 0.42) g.appendChild(txt(L.name, { x: X, y: Y - r - 3 * g0, class: 'ax', 'text-anchor': 'middle',
                                                 style: 'font-size:' + (9.5 * g0).toFixed(2) + 'px', 'pointer-events': 'none' }));
      // a button to the keyboard and the reader, named on hover: at the
      // national extent the Dallas, Fort Worth and Arlington dots overlap
      // as the Northeast ones do, and the name is what tells them apart
      const title = el('title'); title.textContent = L.name + ', ' + L.state;
      g.insertBefore(title, g.firstChild);
      g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button');
      g.setAttribute('aria-label', L.name + ', ' + L.state + (v != null ? ', ' + res.word + ' ' + withUnit(v) : ', no value yet'));
      g.onclick = () => openPlace(L);
      g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openPlace(L); } });
      dots.appendChild(g);
    }

    function drawDots() {
      const on = cellWidthPx() >= CELL_PX;
      if (on !== cellOn) {
        cellOn = on;
        img.style.display = on ? 'none' : '';
        setCaption();
        // what the pointer was over has changed under it; the next move rebuilds the box
        tip.hide();
      }
      dots.textContent = '';
      if (on) { drawWindows(); drawCellMarks(); raiseDot(); return; }
      cells.textContent = '';
      const g0 = GS();
      // smallest first, so where two places sit in one metro (Phoenix and
      // Mesa, Dallas and Arlington) the larger one is drawn on top and is
      // the one a click lands on; the list arrives in population order
      locs().slice().reverse().forEach(L => drawPlainDot(L, g0));
      raiseDot();
    }
    /* Zoom to the cell: the view set so the picked cell is CELL_ZOOM_PX wide
       on screen and centred, then drawn at once rather than a beat later. */
    function zoomToCell(L) {
      const geo = cellGeom(L);
      const rect = svg.getBoundingClientRect();
      if (!geo || !rect.width) return false;
      const want = Math.min(MAXZ, Math.max(1, CELL_ZOOM_PX * VIEW0.w / (Math.hypot(geo.di[0], geo.di[1]) * rect.width)));
      view.w = VIEW0.w / want; view.h = VIEW0.h / want;
      view.x = geo.cx - view.w / 2; view.y = geo.cy - view.h / 2;
      clampView(); applyView();
      clearTimeout(rescale); drawDots(); writeUrl();
      return true;
    }
    // the picked place is drawn last, so where dots overlap it is the one on top
    function raiseDot() {
      const g = S.loc && dots.querySelector('g.dot[data-loc="' + S.loc + '"]');
      if (g) dots.appendChild(g);
    }

    const zoomBar = h('div', { class: 'bar', id: 'anaZoom' });
    /* The variable switch has been on the map since 2026-11-03, at the
       owner's request, because the settlement display shows every variable
       at once and the top bar lost its five buttons. It sets what the map
       shades, the ladder under the display, the highlighted card and `var=`
       in the address, and a card click sets it in turn. It is a radio group,
       so it is one tab stop and the arrows move the choice. */
    const SHORT = { high: 'High', low: 'Low', gust: 'Gust', wind: 'Wind', precip: 'Precip' };
    const setVar = k => { if (VARS[k] && S.var !== k) { S.var = k; changed('var'); } };
    const varSw = h('div', { class: 'anaseg', id: 'anaVar', role: 'radiogroup', 'aria-label': 'Variable shaded on the map' });
    Object.keys(VARS).forEach(k => {
      const b = h('button', { type: 'button', role: 'radio', 'data-var': k, 'aria-checked': 'false', 'aria-label': VARS[k].label,
                              title: VARS[k].label, text: SHORT[k] || VARS[k].label });
      b.onclick = () => setVar(k);
      varSw.appendChild(b);
    });
    varSw.addEventListener('keydown', ev => {
      const keys = Object.keys(VARS), i = keys.indexOf(S.var);
      let j = null;
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') j = (i + 1) % keys.length;
      else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') j = (i - 1 + keys.length) % keys.length;
      else if (ev.key === 'Home') j = 0;
      else if (ev.key === 'End') j = keys.length - 1;
      if (j == null) return;
      ev.preventDefault();
      setVar(keys[j]);
      const b = varSw.querySelector('[data-var="' + keys[j] + '"]');
      if (b) b.focus();
    });
    zoomBar.appendChild(varSw);
    const mk = (label, title, fn) => { const b = h('button', { text: label, title }); b.onclick = fn; zoomBar.appendChild(b); };
    mk('−', 'zoom out', () => zoomAbout(1 / 1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('+', 'zoom in', () => zoomAbout(1.5, view.x + view.w / 2, view.y + view.h / 2));
    mk('Reset', 'back to the whole country', resetView);
    zoomBar.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'anaZoomLevel', text: 'whole country' }));
    zoomBar.appendChild(h('span', { class: 'cap', style: 'margin:0', text: '· scroll to zoom, drag to pan, hover for the cell, click a dot or pick a place for its panel' }));
    host.appendChild(zoomBar);
    host.appendChild(svg);
    // a view restored from the address was applied before the readout
    // existed; applied again now, the readout says where the map is
    if (S.view) applyView();

    // ---- the hour stepper under the map, and the caption it moves
    const step = h('div', { class: 'bar anastep', id: 'anaStep' });
    const prevB = h('button', { text: '◀', title: 'previous hour' });
    const nextB = h('button', { text: '▶', title: 'next hour' });
    const playB = h('button', { text: 'Play', id: 'anaPlay', title: 'step through the hours' });
    const cap = h('span', { class: 'cap', style: 'margin:0', id: 'anaCap' });
    let timer = null;
    prevB.onclick = () => { stop(); stepHour(-1); };
    nextB.onclick = () => { stop(); stepHour(1); };
    function stop() { if (timer) { clearInterval(timer); timer = null; playB.textContent = 'Play'; playB.classList.remove('on'); } }
    playB.onclick = () => {
      if (timer) return stop();
      if (hoursListed().length < 2) return;
      playB.textContent = 'Pause'; playB.classList.add('on');
      timer = setInterval(() => stepHour(1, true), 600);
    };
    function stepHour(dir, wrap) {
      const hs = hoursListed();
      if (!hs.length) return;
      let i = hs.indexOf(S.hour);
      if (i < 0) i = dir > 0 ? -1 : hs.length;
      i += dir;
      if (i < 0 || i >= hs.length) { if (!wrap) return; i = (i + hs.length) % hs.length; }
      S.hour = hs[i];
      writeUrl();
      showFrame();
    }
    step.appendChild(prevB); step.appendChild(nextB); step.appendChild(playB); step.appendChild(cap);
    // the arrow keys step too while any of the three buttons has focus
    step.addEventListener('keydown', ev => {
      if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
      ev.preventDefault(); stop(); stepHour(ev.key === 'ArrowLeft' ? -1 : 1);
    });
    host.appendChild(step);
    const legend = h('div', { class: 'key', id: 'anaLegend' });
    host.appendChild(legend);

    function setCaption() {
      const hs = hoursListed();
      let t = dayLabel(S.day);
      if (S.hour && hs.indexOf(S.hour) >= 0) {
        t += ', ' + S.hour + ':00 UTC';
        const L = S.loc ? locOf(S.loc) : null;
        if (L) t += ' (' + WXC.clockFull(Date.parse(validOf(S.day, S.hour)), L.tz) + ' in ' + L.name + ')';
        if (!frame) t += ' · the frame for this hour could not be read';
      } else {
        t += ' · no ' + PRODUCT_NAME + ' ' + HOURLY[V().hourly] + ' frames listed for this UTC day';
      }
      t += ' · ' + hs.length + (hs.length === 1 ? ' hour' : ' hours') + ' on file';
      // cell mode hides the raster, and the caption says what stands in for it
      if (cellOn) t += ' · the lattice field is hidden at this zoom, and the 2.5 km cells around each place are drawn where the frame carries them'
        + (V().zeroClear ? ', dry cells left clear' : '');
      cap.textContent = t;
      // one hour is nothing to step through
      prevB.disabled = nextB.disabled = playB.disabled = hs.length < 2;
    }
    function setLegend() {
      legend.textContent = '';
      const Vv = V();
      const sw = h('span', { class: 'anaramp' });
      Vv.ramp.forEach(c => sw.appendChild(h('i', { style: 'background:' + c })));
      legend.appendChild(sw);
      const lo = (Vv.lo < 0 ? '−' + Math.abs(Vv.lo) : Vv.lo), hi = Vv.hi;
      legend.appendChild(h('span', { text: Vv.label + ' from the ' + PRODUCT_NAME + ' analysis, ' + lo + ' to ' + hi + ' '
        + Vv.unit + (Vv.zeroClear ? ', zero left clear' : '') + '.' }));
      legend.appendChild(h('span', { text: 'Dots carry the day’s ' + Vv.noun + ' per place, solid once it has resolved, dashed while provisional, hollow with no value yet.' }));
      legend.appendChild(h('span', { text: 'Zoomed in far enough, the map draws the 2.5 km cells around each place and outlines the cell the place resolves on.' }));
      legend.appendChild(h('span', { class: 'kn', text: 'The field is a 3 px subsample of the 2.5 km analysis, about 14 km between points nationally. ' + STATEMENT }));
    }

    let showing = 0;
    async function showFrame() {
      const mine = ++showing;
      const hs = hoursListed();
      // an hour the day does not list is replaced by its newest, and a day
      // with no frames has no hour at all, so the address never names one
      // the view does not show
      if (S.hour == null || hs.indexOf(S.hour) < 0) S.hour = hs.length ? hs[hs.length - 1] : null;
      writeUrl();
      if (!S.hour || hs.indexOf(S.hour) < 0) { drawFrame(null); setCaption(); return; }
      const r = await frameFile(V().hourly, S.day, S.hour);
      if (mine !== showing) return;          // a later step won the race
      drawFrame(r.data && r.data.values ? r.data : null);
      setCaption();
    }
    async function showDay() {
      const r = await dayFile(S.day);
      dayRes = r;
      dayEntries = (r.data && r.data.locations) || {};
      drawDots();
    }
    function pressed() {
      Array.from(varSw.children).forEach(b => {
        const on = b.dataset.var === S.var;
        b.setAttribute('aria-checked', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
      syncDay();
      syncPick();
    }

    /* One handler for every control, so the map, the caption, the legend,
       the dots and the panel move together and the address bar follows. */
    async function changed(what) {
      stop();
      pressed();
      setLegend();
      setCsv();
      if (what === 'day' || what === 'init') await showDay();
      else drawDots();
      writeUrl();
      showFrame();
      if (S.loc) openLoc(false);
    }

    // ---- the location panel, above the map: the place's settlement display
    //      first, then its hypothetical ladder and its cell
    const panel = $('#locPanel');
    const pickHint = $('#anaPick');
    let settle = null;            // the settlement display, kept while a place is open
    function closeLoc() {
      S.loc = null; panel.hidden = true;
      if (settle) { settle.destroy(); settle = null; }
      if (pickHint) pickHint.hidden = false;
      syncPick(); writeUrl(); setCaption();
    }
    async function openLoc(scroll) {
      const L = locOf(S.loc);
      if (!L) { S.loc = null; panel.hidden = true; if (pickHint) pickHint.hidden = false; syncPick(); writeUrl(); return; }
      panel.hidden = false;
      if (pickHint) pickHint.hidden = true;
      syncPick();
      raiseDot();
      const rl = await locFile(L.id, S.day);
      if (locOf(S.loc) !== L) return;
      renderPanel(L, rl.data, (dayEntries || {})[L.id] || (rl.data && rl.data.summary) || null, rl);
      setCaption();
      // a link's var= lands on its chart once, the first time its place opens
      const chart = scrollVar && settle ? settle.chartOf(S.var) : null;
      scrollVar = false;
      if (chart && !chart.hidden) { try { chart.scrollIntoView({ block: 'start' }); } catch (e) { /* landed anyway */ } }
      else if (scroll) { try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* landed anyway */ } }
    }
    /* A live day's two files again, each past the CDN's minute with a stamp
       in the query string that changes once a minute, so the settlement
       display fills in without a reload. Only a live answer replaces what
       the page holds; the dots follow the day file. */
    async function refetchDay(L, day) {
      const stamp = String(Math.floor(Date.now() / 60000));
      const lk = 'loc/' + L.id + '/' + day + '.json', dk = 'days/' + day + '.json';
      const [rl, rd] = await Promise.all([WXD.get(DATA + lk, 60, { bust: stamp }), WXD.get(DATA + dk, 60, { bust: stamp })]);
      if (S.day !== day || S.loc !== L.id) return null;
      if (rd.source === 'live' && rd.data) {
        S.days.set(dk, Promise.resolve(rd));
        dayRes = rd; dayEntries = rd.data.locations || {};
        drawDots();
      }
      if (rl.source !== 'live' || !rl.data) return null;
      S.locs.set(lk, Promise.resolve(rl));
      return { doc: rl.data, entry: (dayEntries || {})[L.id] || rl.data.summary || null };
    }
    function renderPanel(L, doc, entry, locRes) {
      // neither file reached the page: the day is not one with no hours,
      // it is one the page could not read, and the text says which
      const unread = !!(dayRes && dayRes.source === 'none' && locRes && locRes.source === 'none');
      panel.textContent = '';
      const i = days.indexOf(S.day);
      const head = h('div', { class: 'anahead' });
      head.appendChild(h('h2', { text: L.name + ', ' + L.state }));
      head.appendChild(h('span', { class: 'cap', style: 'margin:0', text: dayLabel(S.day) + ' · ' + L.tz }));
      const nav = h('span', { class: 'ananav' });
      const prev = h('button', { text: '◀ ' + (i > 0 ? days[i - 1] : 'earlier'), id: 'anaPrevDay' });
      const next = h('button', { text: (i < days.length - 1 ? days[i + 1] : 'later') + ' ▶', id: 'anaNextDay' });
      prev.disabled = i <= 0; next.disabled = i >= days.length - 1;
      prev.onclick = () => { if (i > 0) { S.day = days[i - 1]; changed('day'); } };
      next.onclick = () => { if (i < days.length - 1) { S.day = days[i + 1]; changed('day'); } };
      const close = h('button', { text: 'Close', title: 'close the panel' });
      close.onclick = closeLoc;
      // the map is below the panel, so zooming from here scrolls down to it
      const zc = h('button', { text: 'Zoom to the cell', id: 'anaZoomCellPanel', title: 'zoom the map to this place’s resolving cell' });
      zc.disabled = !cellGeom(L);
      zc.onclick = () => { if (zoomToCell(L)) { try { host.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* landed anyway */ } } };
      nav.appendChild(zc); nav.appendChild(prev); nav.appendChild(next); nav.appendChild(close);
      head.appendChild(nav);
      panel.appendChild(head);
      panel.appendChild(h('p', { class: 'cap anastmt', text: STATEMENT + ' The values below are what the framework would have resolved for '
        + L.name + ' on ' + S.day + ' from the ' + PRODUCT_NAME + ' analysis.' }));

      // the settlement display: the day's state, the countdowns, the five
      // values, the hours on three charts and in a table, and the replay.
      // Kept across redraws of the same place-day, so a replay holds its time
      if (!settle) {
        settle = WXSettle.create({ product: PRODUCT, productName: PRODUCT_NAME,
          onVar: setVar,
          refetch: () => (S.loc ? refetchDay(locOf(S.loc), S.day) : null) });
      }
      panel.appendChild(settle.node);
      settle.show({ key: L.id + '/' + S.day, doc: doc || null, entry, unread, tz: L.tz, varKey: S.var });

      const hours = (doc && doc.hours) || [];
      if (!hours.length) {
        panel.appendChild(h('p', { class: 'cap', text: locRes && locRes.source === 'none'
          ? 'The hourly file for ' + L.name + ' on ' + S.day + ' could not be read. The hourly charts and table appear once it can be.'
          : 'No hours read yet for ' + L.name + ' on ' + S.day + '. The hourly charts and table appear once the first analysis of the day is read.' }));
      } else {
        // the hypothetical ladder, on the day's value as the file stands
        panel.appendChild(h('div', { class: 'secttl', text: V().label.toUpperCase() + ' ON A HYPOTHETICAL LADDER' }));
        plot(panel, L, hours, resolution(entry, S.var), entry, !!(doc && doc.rowShape >= 2));
      }
      const c = L.cell || {};
      let note = 'Cell centre ' + ((c.centre || [])[0] != null ? c.centre[0].toFixed(4) + ' N, ' + Math.abs(c.centre[1]).toFixed(4) + (c.centre[1] < 0 ? ' W' : ' E') : 'unknown')
        + (c.distanceKm != null ? ', ' + c.distanceKm + ' km from the position at ' + L.lat.toFixed(4) + ' N, ' + Math.abs(L.lon).toFixed(4) + (L.lon < 0 ? ' W' : ' E') : '')
        + (c.wexp ? ', grid cell i ' + c.wexp[0] + ' j ' + c.wexp[1] : '')
        + (c.g184 ? ', and i ' + c.g184[0] + ' j ' + c.g184[1] + ' on the grid RTMA files its precipitation on' : '') + '.'
        + (L.position ? ' The position is ' + L.position + '.' : '');
      if (L.note) note += ' ' + L.note;
      panel.appendChild(h('p', { class: 'cap', id: 'anaCell', text: note }));
    }

    /* The hypothetical ladder, on a chart in the language of WXK.plot: local
       hours 0 to 24 along the foot, midnight and the day end marked, the
       hourly analyses as one line in their own token, the day's value as the
       file stands as a rule across the plot, and a column of hypothetical
       rungs at the right. The rungs are hatched, say Yes or No and nothing
       else, and link to nothing, because there is no book behind them. */
    function plot(host, L, hours, res, entry, running) {
      const Vv = V(), hv = Vv.hourly;
      const NS = 'http://www.w3.org/2000/svg';
      const E = (n, a) => { const e = document.createElementNS(NS, n); for (const k in (a || {})) if (a[k] != null) e.setAttribute(k, a[k]); return e; };
      const T = (s, a) => { const e = E('text', a); e.textContent = s; return e; };
      const W = 960, H = 300, CW = 78, GAP = 26, Lm = 42, R = 16 + CW + GAP, TOP = 18, BOT = H - 30, PX = W - R;
      const svg2 = E('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts' });
      const defs = E('defs');
      const pat = E('pattern', { id: 'wxHatch', width: 5, height: 5, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(135)' });
      pat.appendChild(E('rect', { x: 0, y: 0, width: 5, height: 5, fill: 'transparent' }));
      pat.appendChild(E('rect', { x: 0, y: 0, width: 2, height: 5, fill: 'var(--panel)', 'fill-opacity': .55 }));
      defs.appendChild(pat);
      svg2.appendChild(defs);
      /* For precipitation the line is the day's running total, because the
         day resolves on the total and the rule across the plot is where the
         line should end. Each point is the lane's own running total at that
         hour, exact, from the code that resolves the day, so the last counted
         hour sits on the day's total. The page adds nothing up, since a sum
         of the file's cut hourly figures runs short of the total and a sum of
         rounded ones can land either side of it. A file written before the
         running totals existed has no line for precipitation. */
      const series = p => hours.map((hr, k) => {
        const c = hr[p] || null;
        if (!Vv.running) return { k, v: c && c[hv] != null ? c[hv] : null };
        const r = running && c && c.precip != null && !c.pcpLate && c.run && c.run.precip;
        return { k, v: r ? Number(r.exact) : null };
      });
      const rt = series(PRODUCT);
      const vs = rt.map(p => p.v).filter(v => v != null);
      if (res && res.value != null) vs.push(res.value);
      const rungs = rungsFor(res);
      rungs.forEach(r => vs.push(r.strike));
      if (!vs.length) { host.appendChild(h('p', { class: 'cap', text: 'No ' + HOURLY[hv] + ' values on file for this day.' })); return; }
      let lo = Math.min(...vs), hi = Math.max(...vs);
      const pad = Math.max(Vv.dec ? 0.05 : 2, (hi - lo) * 0.12);
      lo -= pad; hi += pad;
      if (Vv.zeroClear) lo = Math.max(lo, -pad);
      const x = k => Lm + k / 24 * (PX - Lm), y = v => BOT - (v - lo) / (hi - lo) * (BOT - TOP);
      const stepY = Vv.dec ? ((hi - lo) > 1 ? 0.5 : (hi - lo) > 0.4 ? 0.1 : 0.05) : (hi - lo) > 40 ? 10 : (hi - lo) > 16 ? 5 : 2;
      for (let v = Math.ceil(lo / stepY) * stepY; v <= hi + 1e-9; v += stepY) {
        svg2.appendChild(E('line', { x1: Lm, x2: PX, y1: y(v), y2: y(v), stroke: 'var(--line)', 'stroke-width': .5 }));
        svg2.appendChild(T(Number(v.toFixed(2)) + (Vv.dec ? '' : Vv.unit === 'mph' ? '' : Vv.unit), { x: Lm - 6, y: y(v) + 3.5, class: 'ax', 'text-anchor': 'end' }));
      }
      const hourLabel = k => (k % 12 === 0 ? 12 : k % 12) + (k < 12 || k === 24 ? ' AM' : ' PM');
      for (let k = 3; k < 24; k += 3) {
        svg2.appendChild(E('line', { x1: x(k), x2: x(k), y1: TOP, y2: BOT, stroke: 'var(--line)', 'stroke-width': .5 }));
        svg2.appendChild(T(hourLabel(k), { x: x(k), y: BOT + 14, class: 'ax', 'text-anchor': 'middle' }));
      }
      // the day's two edges, the way the city chart marks them; the day-end
      // label is placed once the rungs are, below
      [0, 24].forEach(k => svg2.appendChild(E('line', { x1: x(k), x2: x(k), y1: TOP, y2: BOT, stroke: 'var(--muted)', 'stroke-width': .9 })));
      svg2.appendChild(T('midnight', { x: x(0), y: BOT - 4, class: 'ax', 'text-anchor': 'start' }));
      const line = (pts, stroke, width, dash) => {
        let d = '';
        pts.forEach(p => {
          if (p.v == null) { d += ' '; return; }        // a gap where the hour is missing
          const X = x(p.k + (Vv.running ? 1 : 0)).toFixed(1), Y = y(p.v).toFixed(1);
          d += (d.endsWith(' ') || !d ? 'M' : 'L') + X + ' ' + Y;
        });
        d = d.trim();
        if (d) svg2.appendChild(E('path', { d, fill: 'none', stroke, 'stroke-width': width, 'stroke-dasharray': dash || null, class: 'anaseries' }));
      };
      line(rt, 'var(--ana-rtma)', 1.9, null);
      if (res && res.value != null) {
        svg2.appendChild(E('line', { x1: Lm, x2: PX, y1: y(res.value), y2: y(res.value), stroke: 'var(--ink)', 'stroke-width': 1.1, class: 'anares' }));
        // labelled at the left end of the rule, and by its state: running
        // while it can still move, resolved once it cannot, closed on a day
        // the job closed short of its hours
        svg2.appendChild(T(res.word + ' ' + Number(res.value).toFixed(Vv.dec) + ' ' + Vv.unit + ' (' + res.status + ')',
                           { x: Lm + 4, y: y(res.value) - 3, class: 'ax anareslbl', 'text-anchor': 'start' }));
      }
      // the hypothetical ladder
      const LX = PX + GAP;
      svg2.appendChild(T('Would have resolved', { x: LX + CW / 2, y: TOP - 5, class: 'axl', 'text-anchor': 'middle' }));
      if (!rungs.length) {
        svg2.appendChild(E('rect', { x: LX + 30, y: TOP + 6, width: CW - 30, height: BOT - TOP - 12, fill: 'transparent', stroke: 'var(--line)', 'stroke-width': .8, 'stroke-dasharray': '3 3' }));
        svg2.appendChild(T('no value yet', { x: LX + CW / 2, y: (TOP + BOT) / 2, class: 'ax', 'text-anchor': 'middle' }));
      }
      // whole-unit rungs sit one unit apart, which on a day with a wide
      // range is under the eleven pixels a market rung takes, so a rung is as
      // tall as the gap allows and its label shrinks with it
      const gapPx = Vv.rungs ? 11 : (BOT - TOP) / (hi - lo);
      const RH = Math.max(6, Math.min(11, gapPx * 0.82)), RF = RH >= 9 ? 9 : 7;
      /* Each rung sits at its strike's height, but the fixed precipitation
         strikes 0.01, 0.05 and 0.10 are a few pixels apart on an axis that
         reaches 2 inches, so rungs closer than a rung's height are pushed
         apart along the column (top down, then back up off the foot) and
         keep their order; the strike label travels with its rung. */
      const ys = rungs.map(r => y(r.strike));
      const minGap = RH + 2;
      for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + minGap);
      for (let i = ys.length - 1; i >= 0; i--) {
        const cap = i === ys.length - 1 ? BOT - RH / 2 : ys[i + 1] - minGap;
        ys[i] = Math.min(ys[i], cap);
      }
      /* The column is a label strip and the rungs beside it: each strike
         sits at the strip's right edge against its rung, inside the column,
         where a label hung outside the column ran across the plot's edge and
         over the day-end label. The rung keeps the rest of the width. */
      const LBL = 30, RX = LX + LBL, RW = CW - LBL;
      rungs.forEach((r, i) => {
        const yy = ys[i];
        svg2.appendChild(E('rect', { x: RX, y: yy - RH / 2, width: RW, height: RH, fill: r.yes ? 'var(--yes)' : 'var(--no)', 'fill-opacity': .42, class: 'rung ' + (r.yes ? 'yes' : 'no') }));
        svg2.appendChild(E('rect', { x: RX, y: yy - RH / 2, width: RW, height: RH, fill: 'url(#wxHatch)', stroke: 'var(--line)',
                                     'stroke-width': .8, 'stroke-dasharray': '3 2', class: 'hatched', 'pointer-events': 'none' }));
        svg2.appendChild(T(r.yes ? 'Yes' : 'No', { x: RX + RW / 2, y: yy + RF * 0.36, class: 'anarung', 'text-anchor': 'middle', style: 'font-size:' + RF + 'px' }));
        svg2.appendChild(T((Vv.yes === 'below' ? '\u2264' : '\u2265') + Number(r.strike).toFixed(Vv.dec) + (Vv.dec ? '' : Vv.unit === 'mph' ? '' : Vv.unit),
                           { x: RX - 3, y: yy + RF * 0.36, class: 'ax anastrike', 'text-anchor': 'end', style: 'font-size:' + RF + 'px' }));
      });
      /* The day-end label sits at the foot of the plot's right edge, a gap
         away from the lowest rung, which the layout above pushes to the same
         foot; at one height the two read as one label, so the day-end label
         moves up out of any rung's band. */
      let dy = BOT - 4;
      ys.forEach(yy => { if (dy - 9 < yy + RH / 2 && dy > yy - RH / 2) dy = yy - RH / 2 - 3; });
      svg2.appendChild(T('day end', { x: x(24), y: dy, class: 'ax anadayend', 'text-anchor': 'end' }));
      // hover: one band per hour, the analysis at that hour
      const tp = WXC.tooltip();
      hours.forEach((hr, k) => {
        const xa = x(k), xb = x(k + 1);
        const band = E('rect', { x: xa, y: TOP, width: Math.max(xb - xa, 1), height: BOT - TOP, fill: 'transparent', class: 'hband' });
        const html = () => {
          const v = (hr[PRODUCT] || {})[hv];
          const pairs = [[PRODUCT_NAME, v == null ? 'no analysis' : v.toFixed(hourlyDec(hv)) + ' ' + Vv.unit]];
          if (Vv.running) pairs.push(['Running sum of the hourly analyses', rt[k].v == null ? '·' : rt[k].v.toFixed(hourlyDec(hv)) + ' in']);
          return tp.rows(hourLabel(k) + ' local, ' + hr.t.slice(11, 16) + ' UTC', pairs);
        };
        band.addEventListener('mousemove', e => tp.show(e, html()));
        band.addEventListener('mouseleave', () => tp.hide());
        svg2.appendChild(band);
      });
      // the legend names the line, in its colour, and the day's state
      const e0 = entry && entry[PRODUCT];
      const rstate = e0 ? (e0.review ? 'event review' : e0.closed ? 'closed' : e0.final ? 'final' : 'provisional') : 'provisional';
      svg2.appendChild(E('line', { x1: Lm, x2: Lm + 14, y1: TOP - 6, y2: TOP - 6, stroke: 'var(--ana-rtma)', 'stroke-width': 2 }));
      svg2.appendChild(T(PRODUCT_NAME + ', ' + rstate, { x: Lm + 18, y: TOP - 2.5, class: 'ax' }));
      const card = h('div', { class: 'card ctr' });
      card.appendChild(svg2);
      host.appendChild(card);
    }

    /* The rungs a ladder on this value would have carried: whole units either
       side of the value, or the fixed precipitation strikes. Yes when the
       value is at the strike or on the contract's side of it, so the rung at
       the day's own value reads Yes (owner's decision 2026-09-28). */
    function rungsFor(res) {
      if (!res || res.value == null) return [];
      const Vv = V(), v = res.value;
      // the whole-unit values arrive whole from the lane, so the rungs sit at it and a unit either side
      const strikes = Vv.rungs ? Vv.rungs.slice() : [-3, -2, -1, 0, 1, 2, 3].map(d => v + d);
      return strikes.map(s => ({ strike: s, yes: Vv.yes === 'below' ? v <= s : v >= s })).sort((a, b) => b.strike - a.strike);
    }

    // ---- the method note, from the index's own conventions
    methodNote();
    await changed('init');
  }

  function methodNote() {
    const host = $('#anaMethod');
    if (!host) return;
    const conv = S.index.conventions || {};
    // the keys pipeline/analysis.py CONVENTIONS writes; an unknown key is shown as itself
    const LABELS = { day: 'Day', high: 'High', low: 'Low', gust: 'Peak gust', wind: 'Mean wind', precip: 'Precipitation',
                     rounding: 'Rounding', provisional: 'Final and provisional', missing: 'Missing files',
                     review: 'Event review', closed: 'Closed incomplete days', hourly: 'Hourly values', cell: 'Cell', window: 'Window', strike: 'Value at the strike', resolvedDay: 'Fully resolved day',
                     lattice: 'Map lattice', units: 'Units' };
    host.appendChild(h('h2', { text: 'Method' }));
    host.appendChild(h('p', { text: STATEMENT + ' The conventions below are fixed in the pipeline and every file the page reads carries the times of the analyses it was built from.' }));
    const dl = h('dl', { class: 'anadl' });
    Object.keys(conv).forEach(k => {
      dl.appendChild(h('dt', { text: LABELS[k] || k }));
      dl.appendChild(h('dd', { text: String(conv[k]) }));
    });
    host.appendChild(dl);
    const src = S.index.sources || {};
    const lag = m => (m == null ? 'an unmeasured lag' : m < 90 ? 'about ' + Math.round(m) + ' minutes' : 'about ' + Math.floor(m / 60) + ' hours ' + (m % 60) + ' minutes');
    const latest = s => (s && s.latest ? ', newest hour read ' + s.latest.replace('T', ' ').replace(':00:00Z', ' UTC') : '');
    const s0 = src[PRODUCT] || {};
    host.appendChild(h('p', { text: 'Sources. RTMA is read from NOAA Open Data (' + (s0.bucket || 'noaa-rtma-pds') + '), '
      + lag(s0.lagMinutes) + ' after each hour' + latest(s0) + '; its precipitation is NOAA’s radar-only estimate. '
      + 'A file NOAA Open Data does not have half an hour after it usually lands is read from NCEP’s NOMADS server.' }));
    host.appendChild(h('p', { text: 'The map is a subsample. Each hourly field is sampled at a 3 px pitch in the site’s map space, about 14 km between points nationally, '
      + 'and the dot values come from the place’s own 2.5 km cell rather than from the picture.' }));
    host.appendChild(h('p', { text: 'Station report conventions have no analogue here. There is no last report in the hour, no special report and no tenths group; '
      + 'each hour is the analysis at the top of the hour, and precipitation is the accumulation over the hour that starts then.' }));
  }

  return { init };
})();
