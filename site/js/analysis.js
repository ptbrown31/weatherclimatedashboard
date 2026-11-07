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
   describes. The page computes nothing but colours. Every value, the day's
   running values included, is the lane's, and the page never adds, averages
   or rounds one, since rounded hours do not add up to the day. It never shows
   a price, and never says ask, sell or offer.

   The layout is version 3 of the settlement display, the prototype the
   owner approved on 2026-11-04: the calendar and the map first, always on the page,
   side by side on a wide screen and stacked on a phone, then the open place's
   header, then the place-day itself (js/analysis-settle.js). The calendar opens a date for the same
   place, and the map, the page's only one, opens a place on the same date;
   each place is labelled with its value on the open date for the variable the
   switch above the map or a value card chooses. */
window.WXAnalysis = (() => {
  const { el, txt, h, $ } = WXC;
  const STATEMENT = 'A proposed settlement framework. No contract settles on it.';
  /* The five daily variables. `lo` and `hi` pin the map's nine colours, as
     the approved design pins them, so a day reads the same as another; the
     colours are the --ana-r and --ana-b tokens of site.css, a ramp for each
     theme. Precipitation's zero is left clear. */
  const VARS = {
    high:   { label: 'High', unit: '°F', lo: 20, hi: 100, ramp: 'r', dec: 0, noun: 'high' },
    low:    { label: 'Low', unit: '°F', lo: 10, hi: 90, ramp: 'r', dec: 0, noun: 'low' },
    gust:   { label: 'Peak gust', unit: 'mph', lo: 0, hi: 50, ramp: 'r', dec: 0, noun: 'peak gust' },
    wind:   { label: 'Mean wind', unit: 'mph', lo: 0, hi: 25, ramp: 'r', dec: 0, noun: 'mean wind' },
    precip: { label: 'Precipitation', unit: 'in', lo: 0, hi: 1.5, ramp: 'b', dec: 2, noun: 'precipitation', zeroClear: true },
  };
  // the analysis the framework resolves on and the only one the files carry
  // (owner's decisions 2026-09-29 and 2026-09-30), by its key in the files
  const PRODUCT = 'rtma', PRODUCT_NAME = 'RTMA';
  // the lane's second schema (2026-09-29): the sixty-seven settlement
  // locations, RTMA resolving, one rounding and a midnight-to-midnight rain day
  const DATA = 'analysis2/';
  // the day's hour count is the file's `of` (23, 24 or 25, docs/analysis.md
  // section 4); 24 only for a file written before that field existed
  const hoursInDay = e => (e && e.of) || 24;
  const VB = { w: 960, h: 600 };

  // ---- the page's state, which is also the address bar's
  const S = { var: 'high', day: null, loc: null, index: null, days: new Map(), locs: new Map() };
  const V = () => VARS[S.var];
  const fmt = (v, dec) => (v == null ? '' : Number(v).toFixed(dec == null ? V().dec : dec));
  const withUnit = (v, dec) => (v == null ? '' : fmt(v, dec) + (V().unit === 'in' ? ' in' : V().unit === 'mph' ? ' mph' : V().unit));
  const longDate = d => new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const shortDate = d => new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', timeZone: 'UTC' });
  // a date on a button, "Wed, Nov 4"
  const btnDate = d => new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
  // an analysis valid time on the reader's own clock with the zone named, its
  // weekday and then the hour in UTC, as the files keep it, "3:00 PM EST
  // Thursday (20:00 UTC)"; null for none
  function validText(iso) {
    const t = iso ? Date.parse(iso) : NaN;
    if (isNaN(t)) return null;
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return WXC.clockZone(t, tz) + ' ' + new Date(t).toLocaleDateString('en-US', { weekday: 'long', timeZone: tz })
      + ' (' + new Date(t).toISOString().slice(11, 16) + ' UTC)';
  }

  // a link that names a place and a variable opens on that variable's chart.
  // An address from before 2026-11-04 may carry hour= and the map's view
  // (z, cx, cy), which went with the map's frames; they are dropped
  let scrollVar = false;
  function readUrl() {
    const q = new URLSearchParams(location.search);
    if (VARS[q.get('var')]) S.var = q.get('var');
    scrollVar = !!(VARS[q.get('var')] && q.get('loc'));
    if (q.get('day')) S.day = q.get('day');
    if (q.get('loc')) S.loc = q.get('loc');
  }
  function writeUrl() {
    const q = new URLSearchParams();
    q.set('var', S.var);
    if (S.day) q.set('day', S.day);
    if (S.loc) q.set('loc', S.loc);
    try { history.replaceState(null, '', '?' + q.toString()); } catch (e) { /* a file: URL, or history refused */ }
  }

  // ---- the files, each fetched once and kept
  const once = (map, key, cadence, opts) => {
    if (!map.has(key)) map.set(key, WXD.get(DATA + key, cadence, opts));
    return map.get(key);
  };
  const dayFile = day => once(S.days, 'days/' + day + '.json', 60);
  const locFile = (id, day) => once(S.locs, 'loc/' + id + '/' + day + '.json', 60);

  /* One location's day for the selected variable, with the status the page
     draws it in. A day is final at its cut-off, 12:45 AM local time the next
     day, on the files published by then (owner's ruling 2026-10-14), and a
     place with no hour by then is under review and has no value. A day short
     of its hours carries its count; a closed day comes only from files
     written before the cut-off rule. */
  function resolution(entry, varKey) {
    const e = entry && entry[PRODUCT];
    if (!e) return null;
    const inReview = varKey === 'precip' ? !!(e.precip && e.precip.review) : !!e.review;
    if (inReview) return { value: null, review: true, provisional: true, status: 'under review' };
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
    const status = kind === 'closed' ? 'closed, ' + count : n < total ? kind + ', ' + count : kind;
    return { value: val.value, at: val.at || null, kind, hours: n, total, provisional: kind !== 'final', status };
  }

  async function init() {
    WXC.chrome('analysis-resolution.html');
    readUrl();
    const strip = $('#pageStatus'), hint = $('#anaPick');
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
    const pickers = $('#anaPickers');
    if (!S.index || !base) {
      pickers.appendChild(h('p', { class: 'cap' }, 'The analysis index is not available right now. ' + STATEMENT));
      if (hint) hint.hidden = true;
      return;
    }
    const src = S.index.sources || {};
    const validAt = s => { const v = validText(s && s.latest); return v ? 'valid ' + v : 'none read'; };
    const bar = $('#anaBar');
    bar.appendChild(h('span', { class: 'cap', style: 'margin:0', id: 'anaNewest', text: 'newest ' + PRODUCT_NAME + ' analysis, ' + validAt(src[PRODUCT]) }));
    const days = (S.index.days || []).slice();
    if (!days.length) {
      pickers.appendChild(h('p', { class: 'cap' }, 'No days have been read yet. ' + STATEMENT));
      if (hint) hint.hidden = true;
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
    if (foot) foot.textContent = 'Analyses are NOAA’s Real-Time Mesoscale Analysis (RTMA), read from NOAA Open Data on AWS and from NCEP’s NOMADS server. '
      + 'Times are analysis valid times, never fetch times. ' + STATEMENT;

    // the day's values as CSV, raw (the files' own units, every hour and
    // place) and processed (the day's values as they resolve), rewritten by
    // the job with the day
    const csvLinks = h('span', { class: 'anacsv', id: 'anaCsv' });
    bar.insertBefore(csvLinks, strip);
    const setCsv = () => {
      csvLinks.textContent = '';
      if (!S.day) return;
      const at = kind => WXD.base() + '/snapshots/' + DATA + 'csv/' + S.day + '-' + kind + '.csv';
      csvLinks.appendChild(document.createTextNode('CSV of the day at every place: '));
      csvLinks.appendChild(h('a', { href: at('raw'), download: S.day + '-raw.csv', text: 'raw values' }));
      csvLinks.appendChild(document.createTextNode(' · '));
      csvLinks.appendChild(h('a', { href: at('processed'), download: S.day + '-processed.csv', text: 'processed values' }));
    };

    /* A day's state from the index's dayStatus. A day is final once every
       place has resolved, under review when every place still open is under
       review, closed on a file written before the cut-off rule of 14
       October, and provisional otherwise; an index without the field gives
       no state. The calendar marks each listed day with it. */
    const status = S.index.dayStatus || {};
    const dayKind = d => {
      const st = status[d];
      if (!st || !st.places) return null;
      if (st.resolved === st.places) return 'final';
      if ((st.review || 0) && st.resolved + (st.review || 0) >= st.places) return 'review';
      return st.resolved + (st.closed || 0) + (st.review || 0) >= st.places ? 'closed' : 'provisional';
    };
    const KIND = { final: 'final', provisional: 'provisional', review: 'under review', closed: 'closed' };

    /* ---- The calendar, always on the page: the month of the open date, on
       which only the listed days can be chosen, each marked with its state.
       Choosing a date opens it for the same place. It is a grid with one tab
       stop: the arrows move between listed days, a week at a time up and
       down, Page Up and Page Down move a month among the months that hold
       listed days, Home and End go to the first and last listed day of the
       month on view, and Enter or Space opens the day. */
    const listed = new Set(days);
    const months = Array.from(new Set(days.map(d => d.slice(0, 7)))).sort();
    const monthName = ym => new Date(ym + '-15T12:00:00Z').toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    const addDays = (d, n) => new Date(Date.parse(d + 'T12:00:00Z') + n * 864e5).toISOString().slice(0, 10);
    const WEEK = [['Su', 'Sunday'], ['Mo', 'Monday'], ['Tu', 'Tuesday'], ['We', 'Wednesday'], ['Th', 'Thursday'], ['Fr', 'Friday'], ['Sa', 'Saturday']];
    const calTitle = h('h2', { class: 'anacalttl', id: 'anaCalTitle', 'aria-live': 'polite' });
    const calPrev = h('button', { type: 'button', class: 'anacalnav', id: 'anaCalPrev', text: '◀' });
    const calNext = h('button', { type: 'button', class: 'anacalnav', id: 'anaCalNext', text: '▶' });
    const calGrid = h('table', { class: 'anacalgrid', id: 'anaCalGrid', role: 'grid', 'aria-labelledby': 'anaCalTitle' });
    const calKey = h('div', { class: 'anacalkey', id: 'anaCalKey' });
    const cal = h('section', { class: 'anacal anapick1', id: 'anaCal', 'aria-label': 'Days' },
      [h('div', { class: 'anacalhead' }, [calTitle, h('span', { class: 'anacalnavs' }, [calPrev, calNext])]), calGrid, calKey]);
    ['final', 'provisional', 'review', 'closed'].filter(k => k !== 'closed' || days.some(d => dayKind(d) === 'closed'))
      .forEach(k => calKey.appendChild(h('span', {}, [h('i', { class: 'mk ' + k, 'aria-hidden': 'true' }), k === 'final' ? 'final at every place' : KIND[k]])));
    calKey.appendChild(h('span', { class: 'off', text: 'Days in grey are not on file. Choosing a day opens it for the same place.' }));
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
      // view, else the open day, else the month's first listed day
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
          td.className = 'on' + (d === S.day ? ' open' : '');
          td.setAttribute('data-status', kd || 'none');
          td.setAttribute('aria-selected', d === S.day ? 'true' : 'false');
          td.setAttribute('aria-label', longDate(d) + (kd ? ', ' + KIND[kd] : '') + (d === S.day ? ', open' : ''));
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
    // choosing a day keeps the focus on it, so the keyboard stays in the grid
    const pickDay = d => {
      if (!listed.has(d)) return;
      calFocus = d;
      const had = calGrid.contains(document.activeElement);
      if (d !== S.day) { S.day = d; changed('day'); } else drawMonth(d.slice(0, 7));
      if (had) focusDay(d);
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
    // the open day's month, unless the reader has stepped to another one
    const syncDay = () => {
      const ym = (S.day || days[days.length - 1]).slice(0, 7);
      if (calGrid.contains(document.activeElement) && calMonth) drawMonth(calMonth);
      else { calFocus = S.day; drawMonth(ym); }
    };

    /* ---- The map, always on the page and the page's only one: every place
       labelled with its value on the open date for the chosen variable, solid
       once final and dashed while provisional, hollow with no value, the open
       place ringed and named. A click or a tap within a few screen pixels of
       a place opens it on the same date. The map is for the pointer; the box
       under it is for the keyboard, a screen reader and a phone, so the map is
       hidden from assistive technology and every place, with its value, can
       be reached from the box. */
    const svg = el('svg', { viewBox: '0 0 960 600', class: 'anamap', id: 'anaPlaceMap', 'aria-hidden': 'true', focusable: 'false' });
    svg.appendChild(el('path', { d: base.statePaths, fill: 'var(--map-land)' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state', 'vector-effect': 'non-scaling-stroke' }));
    svg.appendChild(el('path', { d: base.statePaths, class: 'state2', 'vector-effect': 'non-scaling-stroke' }));
    const dots = el('g', { class: 'pds', id: 'anaDots' });
    svg.appendChild(dots);
    const locs = () => S.index.locations || [];
    const locOf = id => locs().find(L => L.id === id) || null;
    const byName = (a, b) => a.name.localeCompare(b.name) || a.state.localeCompare(b.state);
    const places = locs().slice().sort(byName);
    const placeName = L => L.name + ', ' + L.state;
    let dayEntries = null;     // the day file's locations, for the labels
    let dayRes = null;         // the day file's fetch result, so a file that could not be read is not a day with no hours
    // a value's colour: one of nine steps on the variable's pinned range, the
    // steps being site.css tokens so each theme has its own
    const step = v => {
      const Vv = V();
      if (v == null || (Vv.zeroClear && v <= 0)) return -1;
      return Math.max(0, Math.min(8, Math.floor((v - Vv.lo) / (Vv.hi - Vv.lo) * 9)));
    };
    const fill = v => { const k = step(v); return k < 0 ? 'var(--panel)' : 'var(--ana-' + V().ramp + k + ')'; };
    // the three deepest blues take the other ink, which --ana-ink-dk gives each theme
    const darkStep = v => V().ramp === 'b' && step(v) >= 6;
    // screen pixels per viewBox unit, 0 until the map is on the page
    const pxPerUnit = () => { const w = svg.getBoundingClientRect().width; return w ? w / VB.w : 0; };
    // a label's width in viewBox units at font size `fs`, close enough to fit a pill
    const textW = (s, fs) => Array.from(s).reduce((a, c) => a + (c === '.' ? 0.32 : 0.62), 0) * fs;
    let geo = [];              // where each place's label is, for the pointer
    function drawDots() {
      const k = pxPerUnit() || 0.68;
      // about 10.5 px type at any width, within what keeps the labels apart
      const fs = Math.max(12, Math.min(24, 10.5 / k));
      dots.textContent = '';
      geo = [];
      // smallest first, so where two places sit close the larger is on top;
      // the list arrives in population order, and the open place is last
      const order = locs().slice().reverse().filter(L => L.id !== S.loc);
      const cur = locOf(S.loc);
      if (cur) order.push(cur);
      order.forEach(L => {
        const res = resolution((dayEntries || {})[L.id], S.var);
        const v = res ? res.value : null;
        const g = el('g', { class: 'pd' + (L.id === S.loc ? ' cur' : ''), 'data-loc': L.id });
        let rad;
        if (v != null) {
          const t = fmt(v), hh = fs * 1.42, w = Math.max(hh, textW(t, fs) + fs * 0.75);
          g.appendChild(el('rect', { x: (L.px - w / 2).toFixed(2), y: (L.py - hh / 2).toFixed(2), width: w.toFixed(2), height: hh.toFixed(2), rx: (hh / 2).toFixed(2),
                                     fill: fill(v), class: 'pv ' + (res.provisional ? 'prov' : 'final') }));
          g.appendChild(txt(t, { x: L.px.toFixed(2), y: (L.py + fs * 0.36).toFixed(2), class: 'pvt' + (darkStep(v) ? ' dk' : ''), 'text-anchor': 'middle', style: 'font-size:' + fs.toFixed(2) + 'px' }));
          rad = Math.max(w, hh) / 2;
        } else {
          rad = fs * 0.4;
          g.appendChild(el('circle', { cx: L.px, cy: L.py, r: rad.toFixed(2), class: 'pv none' }));
        }
        if (L.id === S.loc) {
          g.insertBefore(el('circle', { cx: L.px, cy: L.py, r: (rad + fs * 0.42).toFixed(2), class: 'ring' }), g.firstChild);
          const east = L.px > 790;
          g.appendChild(txt(L.name, { x: (L.px + (east ? -1 : 1) * (rad + fs * 0.7)).toFixed(2), y: (L.py + fs * 0.36).toFixed(2), class: 'pname',
                                      'text-anchor': east ? 'end' : 'start', style: 'font-size:' + (fs * 1.05).toFixed(2) + 'px' }));
        }
        geo.push({ L, r: rad, res, v });
        dots.appendChild(g);
      });
      setLegend();
    }
    // the place nearest the pointer, within its label or twelve screen
    // pixels, so a dot a few pixels across on a phone is still easy to hit
    function near(ev) {
      const m = svg.getScreenCTM(), k = pxPerUnit();
      if (!m || !k) return null;
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const p = pt.matrixTransform(m.inverse());
      let best = null, bd = Infinity;
      geo.forEach(G => {
        const d = Math.hypot(G.L.px - p.x, G.L.py - p.y);
        if (d <= Math.max(G.r, 12 / k) && d < bd) { bd = d; best = G; }
      });
      return best;
    }
    const valueText = G => (G.v != null ? withUnit(G.v) + ', ' + G.res.status : G.res && G.res.review ? 'no value, under review'
      : dayRes && dayRes.source === 'none' ? 'the day file could not be read' : 'no value for this day yet');
    svg.addEventListener('pointermove', ev => {
      const G = near(ev);
      svg.classList.toggle('hot', !!G);
      if (G) tip.show(ev, tip.rows(placeName(G.L) + (G.L.code ? ' (' + G.L.code + ')' : ''), [[V().label, valueText(G)]], 'click to open the place on this day'));
      else tip.hide();
    });
    svg.addEventListener('pointerleave', () => { svg.classList.remove('hot'); tip.hide(); });
    svg.addEventListener('click', ev => { const G = near(ev); if (G) { tip.hide(); openPlace(G.L); } });

    /* The variable switch, above the map: a radio group of the five daily
       values, one tab stop, whose arrows, Home and End move the choice. It
       sets what the map labels, the highlighted card and var= in the address,
       and a card click sets it in turn. */
    const SHORT = { high: 'High', low: 'Low', gust: 'Gust', wind: 'Wind', precip: 'Precip' };
    const setVar = k => { if (VARS[k] && S.var !== k) { S.var = k; changed('var'); } };
    const varSw = h('div', { class: 'anaseg', id: 'anaVar', role: 'radiogroup', 'aria-label': 'Value the map shows' });
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

    /* The box finds a place by name: a combobox over a list of the matches
       that opens as the reader types, a name that starts with the text first,
       then a word in it that does, then any name or state that holds it. The
       arrows move through the matches, Enter opens the highlighted one, which
       is the first until the arrows move on, and Escape closes the list. Each
       match carries its value on the map. */
    const plQ = h('input', { type: 'text', id: 'anaPlaceQ', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
      'aria-controls': 'anaPlaceList', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', enterkeyhint: 'go', placeholder: 'Type a name' });
    // the list is reached through the box, so it is no tab stop of its own
    const plList = h('ul', { class: 'anapllist', id: 'anaPlaceList', role: 'listbox', 'aria-label': 'Places', tabindex: '-1', hidden: '' });
    const plCount = h('p', { class: 'cap anaplcount', id: 'anaPlaceCount', 'aria-live': 'polite' });
    const find = h('div', { class: 'anafind', role: 'search' }, [
      h('label', { for: 'anaPlaceQ', text: 'Find a place' }), h('div', { class: 'anafindbox' }, [plQ, plList]), plCount]);
    const norm = s => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    const plRank = (L, s) => {
      const n = norm(L.name);
      if (n.startsWith(s)) return 0;
      if (n.split(/[\s.-]+/).some(w => w.startsWith(s))) return 1;
      return (n + ', ' + norm(L.state) + (L.code ? ' ' + norm(L.code) : '')).includes(s) ? 2 : -1;
    };
    let plMatches = [], plActive = -1;
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
    }
    const plClose = () => { plList.hidden = true; plQ.setAttribute('aria-expanded', 'false'); plQ.removeAttribute('aria-activedescendant'); plActive = -1; };
    function plFilter(all) {
      const typed = plQ.value.trim(), s = norm(typed);
      plMatches = s ? places.map(L => [plRank(L, s), L]).filter(x => x[0] >= 0).sort((a, b) => a[0] - b[0] || byName(a[1], b[1])).map(x => x[1])
        : all ? places.slice() : [];
      plList.textContent = '';
      plMatches.forEach(L => {
        const res = resolution((dayEntries || {})[L.id], S.var);
        const li = h('li', { role: 'option', id: 'anaPlace-' + L.id, 'data-loc': L.id, 'aria-selected': 'false' }, [placeName(L)]);
        li.appendChild(h('span', { class: 'val', text: ' · ' + (res && res.value != null ? withUnit(res.value) : res && res.review ? 'under review' : 'no value') }));
        if (L.id === S.loc) { li.classList.add('cur'); li.appendChild(h('span', { class: 'cur', text: ' · open' })); }
        li.addEventListener('click', () => pickPlace(L));
        plList.appendChild(li);
      });
      plList.hidden = !plMatches.length;
      plQ.setAttribute('aria-expanded', plMatches.length ? 'true' : 'false');
      plCount.textContent = !s && !all ? places.length + ' places. Type a name to find one, or choose one on the map.'
        : plMatches.length ? plMatches.length + (plMatches.length === 1 ? ' place matches.' : ' places match.') + ' Enter opens the highlighted one.'
          : 'No place matches ' + typed + '.';
      plSetActive(plMatches.length ? 0 : -1);
    }
    plQ.addEventListener('input', () => plFilter(false));
    plQ.addEventListener('keydown', ev => {
      const n = plMatches.length;
      if (ev.key === 'ArrowDown') { ev.preventDefault(); if (plList.hidden) plFilter(true); else if (n) plSetActive(Math.min(n - 1, plActive + 1)); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); if (n && !plList.hidden) plSetActive(Math.max(0, plActive - 1)); }
      else if (ev.key === 'Enter') {
        ev.preventDefault();
        const L = !plList.hidden && plActive >= 0 ? plMatches[plActive] : (plQ.value.trim() ? plMatches[0] : null);
        if (L) pickPlace(L);
      } else if (ev.key === 'Escape') {
        if (!plList.hidden) { ev.preventDefault(); plClose(); } else if (plQ.value) { ev.preventDefault(); plQ.value = ''; plFilter(false); }
      }
    });
    // a click in the list keeps the focus in the box, and the list closes
    // when the focus leaves the box
    plList.addEventListener('pointerdown', ev => ev.preventDefault());
    find.addEventListener('focusout', ev => { if (!find.contains(ev.relatedTarget)) plClose(); });
    const pickPlace = L => { plQ.value = ''; plClose(); plCount.textContent = 'Opened ' + placeName(L) + '.'; openPlace(L); };
    const openPlace = L => { if (S.loc !== L.id) { S.loc = L.id; writeUrl(); openLoc(); } };

    // the legend: what the labels are, on which day, and the colours
    const legend = h('div', { class: 'analegend', id: 'anaLegend' });
    function setLegend() {
      legend.textContent = '';
      const Vv = V(), st = dayKind(S.day);
      const lohi = v => (Vv.dec ? Number(v).toFixed(Vv.dec) : String(v)) + (Vv.unit === '°F' ? '°F' : ' ' + Vv.unit);
      legend.appendChild(h('span', { class: 'what', text: Vv.label + ', ' + shortDate(S.day) + (st && st !== 'final' ? ', so far' : '') }));
      const ramp = h('span', { class: 'ramp', 'aria-hidden': 'true' });
      for (let k = 0; k < 9; k++) ramp.appendChild(h('i', { style: 'background:var(--ana-' + Vv.ramp + k + ')' }));
      legend.appendChild(h('span', { class: 'scale' }, [lohi(Vv.lo) + ' ', ramp, ' ' + lohi(Vv.hi)]));
      legend.appendChild(h('span', { class: 'kn', text: 'Dashed while provisional, hollow with no value' + (Vv.zeroClear ? ', clear when dry' : '') + '.' }));
    }

    const mapCard = h('section', { class: 'anamapc anapick1', id: 'anaMapCard', 'aria-label': 'Places' }, [
      h('div', { class: 'anamaphead' }, [h('h2', { text: 'Places' }), varSw]),
      h('div', { class: 'anamapbox' }, [svg]),
      h('div', { class: 'anamapfoot' }, [legend, find])]);
    pickers.appendChild(cal);
    pickers.appendChild(mapCard);
    // the labels keep their size on screen, so they are drawn again when the map's width changes
    let lastW = 0;
    const onSize = () => { const w = Math.round(svg.getBoundingClientRect().width); if (w && w !== lastW) { lastW = w; drawDots(); } };
    if (window.ResizeObserver) new ResizeObserver(onSize).observe(svg); else window.addEventListener('resize', onSize);

    // ---- the open place's header (spec section 8): its name and code, the
    //      day and its buttons, then where the place is and its zone, how far
    //      the centre of its grid square lies from there, the statement, and
    //      last its analysis grid point, its point on RTMA's precipitation
    //      grid (Grid 184) and the centre of its grid square, from the index's
    //      location list
    const head = $('#anaHead');
    const zoneFmts = {};
    const zoneFmt = (tz, o, lang) => { const k = (lang || 'en-US') + tz + JSON.stringify(o);
      return zoneFmts[k] || (zoneFmts[k] = new Intl.DateTimeFormat(lang || 'en-US', Object.assign({ timeZone: tz }, o))); };
    const zonePart = (tz, o, t) => ((zoneFmt(tz, o).formatToParts(t).find(p => p.type === 'timeZoneName')) || {}).value || '';
    /* The place's zone in words with its offset on the open day, "Mountain
       Time (UTC−7)", and both offsets on a day the clocks change, "Mountain
       Time (UTC−6, then UTC−7)". The offsets are read hour by hour across
       the local day; a browser without offset names gives the name alone. */
    function zoneWords(tz, day) {
      if (!tz) return '';
      let name = tz;
      try { name = zonePart(tz, { timeZoneName: 'long' }, Date.parse(day + 'T12:00:00Z')).replace(/ (Standard|Daylight) /, ' ') || tz; } catch (e) { /* the zone's own name */ }
      const offs = [];
      try {
        const t0 = Date.parse(day + 'T00:00:00Z');
        for (let k = -14; k <= 38; k++) {
          const t = t0 + k * 36e5;
          if (zoneFmt(tz, { year: 'numeric', month: '2-digit', day: '2-digit' }, 'en-CA').format(t) !== day) continue;
          const o = zonePart(tz, { timeZoneName: 'shortOffset' }, t).replace('GMT', 'UTC').replace('-', '−');
          if (o && offs[offs.length - 1] !== o) offs.push(o);
        }
      } catch (e) { offs.length = 0; }
      return name + (offs.length ? ' (' + offs.join(', then ') + ')' : '');
    }
    function renderHead(L) {
      head.textContent = '';
      const i = days.indexOf(S.day);
      const row = h('div', { class: 'anaheadrow' });
      const h2 = h('h2', { id: 'anaPlaceName' }, [placeName(L)]);
      if (L.code) h2.appendChild(h('span', { class: 'code', text: ' (' + L.code + ')' }));
      row.appendChild(h2);
      row.appendChild(h('span', { class: 'anaday', id: 'anaHeadDay', text: longDate(S.day) }));
      const nav = h('span', { class: 'ananav' });
      const prev = h('button', { type: 'button', text: '◀ ' + (i > 0 ? btnDate(days[i - 1]) : 'earlier'), id: 'anaPrevDay' });
      const next = h('button', { type: 'button', text: (i < days.length - 1 ? btnDate(days[i + 1]) : 'later') + ' ▶', id: 'anaNextDay' });
      prev.disabled = i <= 0; next.disabled = i >= days.length - 1;
      prev.onclick = () => { if (i > 0) { S.day = days[i - 1]; changed('day'); } };
      next.onclick = () => { if (i < days.length - 1) { S.day = days[i + 1]; changed('day'); } };
      const close = h('button', { type: 'button', text: 'Close', title: 'close the place' });
      close.onclick = closeLoc;
      nav.appendChild(prev); nav.appendChild(next); nav.appendChild(close);
      row.appendChild(nav);
      head.appendChild(row);
      const c = L.cell || {}, ij = a => (a && a.length >= 2 ? '(' + a[0] + ', ' + a[1] + ')' : 'not on record');
      const ll = (lat, lon) => lat.toFixed(4) + '° N, ' + Math.abs(lon).toFixed(4) + '° ' + (lon < 0 ? 'W' : 'E');
      head.appendChild(h('p', { class: 'anawhere', id: 'anaWhere', text: [L.position, zoneWords(L.tz, S.day)].filter(Boolean).join(' · ') }));
      const note = (c.distanceKm != null && L.lat != null ? 'The centre of its 2.5 km grid square is ' + c.distanceKm + ' km from the position, which is at '
        + ll(L.lat, L.lon) + '.' : '') + (L.note ? ' ' + L.note : '');
      if (note.trim()) head.appendChild(h('p', { class: 'cap', id: 'anaCell', text: note.trim() }));
      head.appendChild(h('p', { class: 'cap anastmt', text: STATEMENT + ' The values below are what the framework would have resolved for '
        + L.name + ' on ' + longDate(S.day) + ', from the ' + PRODUCT_NAME + ' analysis.' }));
      head.appendChild(h('p', { class: 'anafacts', id: 'anaFacts', text: 'Analysis grid point ' + ij(c.wexp) + ' · precipitation grid (Grid 184) point ' + ij(c.g184)
        + ' · grid square centred at ' + (c.centre && c.centre.length >= 2 ? ll(c.centre[0], c.centre[1]) : 'a point not on record') }));
    }

    // ---- the place-day: the settlement display, under the calendar and the map
    const panel = $('#locPanel');
    let settle = null;            // the settlement display, kept while a place is open
    function closeLoc() {
      S.loc = null; panel.hidden = true; head.hidden = true;
      if (settle) { settle.destroy(); settle = null; }
      if (hint) hint.hidden = false;
      writeUrl(); drawDots();
    }
    async function openLoc() {
      const L = locOf(S.loc);
      if (!L) { closeLoc(); return; }
      panel.hidden = false; head.hidden = false;
      if (hint) hint.hidden = true;
      renderHead(L);
      drawDots();
      const rl = await locFile(L.id, S.day);
      if (locOf(S.loc) !== L) return;
      renderPanel(L, rl.data, (dayEntries || {})[L.id] || (rl.data && rl.data.summary) || null, rl);
      // a link's var= lands on its chart once, the first time its place opens
      const chart = scrollVar && settle ? settle.chartOf(S.var) : null;
      scrollVar = false;
      if (chart && !chart.hidden) { try { chart.scrollIntoView({ block: 'start' }); } catch (e) { /* landed anyway */ } }
    }
    /* A live day's two files again, each past the CDN's minute with a stamp
       in the query string that changes once a minute, so the settlement
       display fills in without a reload. Only a live answer replaces what
       the page holds; the map follows the day file. */
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
      if (!settle) {
        settle = WXSettle.create({ product: PRODUCT, productName: PRODUCT_NAME,
          onVar: setVar,
          refetch: () => (S.loc ? refetchDay(locOf(S.loc), S.day) : null) });
        panel.textContent = '';
        panel.appendChild(settle.node);
      }
      const noneText = locRes && locRes.source === 'none'
        ? 'The hourly file for ' + L.name + ' on ' + longDate(S.day) + ' could not be read. The hourly charts and table appear once it can be.'
        : 'No hours read yet for ' + L.name + ' on ' + longDate(S.day) + '. The hourly charts and table appear once the first analysis of the day is read.';
      settle.show({ key: L.id + '/' + S.day, doc: doc || null, entry, unread, tz: L.tz, varKey: S.var, noneText,
                    place: { id: L.id, name: L.name, state: L.state, code: L.code || '' } });
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
    }

    /* One handler for every control, so the calendar, the map, its legend and
       labels, the header and the place-day move together and the address bar
       follows. */
    async function changed(what) {
      pressed();
      setCsv();
      if (what === 'day' || what === 'init') await showDay();
      else drawDots();
      if (!plList.hidden) plFilter(!plQ.value.trim());
      writeUrl();
      if (S.loc) openLoc(); else closeLoc();
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
                     review: 'Event review', closed: 'Closed incomplete days', hourly: 'Hourly values', hourlyContract: 'Hourly contract',
                     cell: 'Cell', strike: 'Value at the strike', resolvedDay: 'Fully resolved day', units: 'Units' };
    // the frame map these two describe left the page with display v3; the lane
    // still writes the frames, and the index still carries both for them
    const NOT_SHOWN = new Set(['window', 'lattice']);
    host.appendChild(h('h2', { text: 'Method' }));
    host.appendChild(h('p', { text: STATEMENT + ' The conventions below are fixed in the pipeline and every file the page reads carries the times of the analyses it was built from.' }));
    const dl = h('dl', { class: 'anadl' });
    Object.keys(conv).filter(k => !NOT_SHOWN.has(k)).forEach(k => {
      dl.appendChild(h('dt', { text: LABELS[k] || k }));
      dl.appendChild(h('dd', { text: String(conv[k]) }));
    });
    host.appendChild(dl);
    const src = S.index.sources || {};
    const lag = m => (m == null ? 'an unmeasured lag' : m < 90 ? 'about ' + Math.round(m) + ' minutes' : 'about ' + Math.floor(m / 60) + ' hours ' + (m % 60) + ' minutes');
    const latest = s => { const v = validText(s && s.latest); return v ? ', newest hour read ' + v : ''; };
    const s0 = src[PRODUCT] || {};
    host.appendChild(h('p', { text: 'Sources. RTMA is read from NOAA Open Data (' + (s0.bucket || 'noaa-rtma-pds') + '), '
      + lag(s0.lagMinutes) + ' after each hour' + latest(s0) + '; its precipitation is NOAA’s radar-only estimate. '
      + 'Every file is read from NCEP’s NOMADS server as well, and where the two copies differ the NOMADS copy counts if it was published by the cut-off.' }));
    host.appendChild(h('p', { text: 'The map shows each place’s value for the open day, the one its own 2.5 km grid square resolves on.' }));
    host.appendChild(h('p', { text: 'Station report conventions have no analogue here. There is no last report in the hour, no special report and no tenths group; '
      + 'each hour is the analysis at the top of the hour, and precipitation is the accumulation over the hour that starts then.' }));
  }

  return { init };
})();
