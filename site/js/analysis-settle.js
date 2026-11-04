/* The settlement display of the analysis resolution page: one place-day as it
   resolves on NOAA's RTMA, hour by hour, under the calendar and the map.
   Version 3 of the owner's approved design (2026-11-04).

   In order: the day's state, when the next analysis and the next hour's
   precipitation are due, the five values a contract would resolve on, three
   charts of the hours on one time axis with a CSV file for each value under
   them, every hour in a table, and a replay that shows the day as of any
   minute at the files' own publication times. While the day is live it
   follows the clock, and inside the minutes the files usually land, or while a
   countdown reads late, it fetches the place file again, so the day fills in
   without a reload.

   The page adds nothing up. Every figure is the lane's, as the place file
   writes it (`rowShape` 3, docs/analysis.md section 4): each hour as stored
   (`raw`), converted before rounding (`x`) and as the contract shows it
   (`show`), the day so far rounded (`run`) and before rounding (`runX`), each
   file's times on both servers (`anlFile`, `pcpFile`) and each hour's hourly
   contract (`hourly`). Rounded hours do not add up to the day (Boston on
   2026-09-28, 0.83 in from its rounded hours against the 0.84 it resolved
   on), so the page never adds, averages or rounds a value; it compares times,
   for the replay and the countdowns, and places pixels. A publication time is
   never estimated. An hour comes in at its file's first publication (`pub`,
   `pcpPub`) or, failing that, the earliest Last-Modified time on record, and a
   time not on record is said to be. A file written before 2026-11-04
   (`rowShape` 2) has no stored or converted values and no file records, which
   the display leaves blank, and one written before 2026-11-03 (no `rowShape`)
   is drawn as the file stands, with no running figures and no replay. */
window.WXSettle = (() => {
  const { h } = WXC;
  const MIN = 60000, HOUR = 3600000;
  /* When the files land, from NOAA's own publication times over the two
     weeks to 2026-11-03: an analysis a median 49 minutes after its hour and
     usually by 52, an hour's precipitation a median 17 minutes after the hour
     ends and usually by 18. The countdowns run to these, for a file not in
     yet; they are never shown as a file's publication time. */
  const LAG = { anl: { due: 49 * MIN, by: 52 * MIN }, pcp: { due: 17 * MIN, by: 18 * MIN } };
  // the draft terms' cut-off, 12:45 AM local on the day after (clocks never
  // change between midnight and then in the lane's zones), and how far past
  // the last cut-off a final day's replay runs
  const CUTOFF = 45 * MIN, AFTER = 15 * MIN;
  // the minutes past each hour inside which a live day's file is fetched
  // again every minute: about when the analysis lands (from :44 to :54) and
  // when the hour's precipitation does (from :14 to :20)
  const inWindow = m => (m >= 44 && m <= 54) || (m >= 14 && m <= 20);
  // a countdown past its usual time keeps a live day fetched each minute for
  // this long, so a file the lane reads late, on its ten-minute pass outside
  // those windows, reaches the page a minute or two after the read and late
  // ends when the file is in; past it the windows fetch the day twice an hour.
  // An hourly contract the file still holds provisional past its own cut-off
  // is fetched for as long, since the lane's next pass settles it
  const CHASE = 30 * MIN;
  const SRC = { nodd: 'NOAA Open Data', nomads: 'NOMADS' };
  // NOMADS keeps no history, and its copies are on record from 15:13 UTC on
  // 3 November 2026, when the timing probe started
  const NOMADS_FROM = Date.parse('2026-11-03T15:13:00Z');
  // a file published after the cut-off that the rule before 14 October still
  // counted; one real case, the precipitation hour ending 2026-09-30T07:00Z
  // for western places on 29 September
  const OLD_RULE = 'published after the cut-off, counted under the rule before 14 October 2026';
  // the precision each hourly value is shown at, as the contract rounds it,
  // and as a file without `rowShape` carries it, cut
  const DEC = { temp: [0, 3], wind: [0, 3], gust: [0, 3], precip: [2, 4] };
  // the chart each daily variable is read on, for a link's var=
  const CHART = { high: 'anaTemp', low: 'anaTemp', gust: 'anaWind', wind: 'anaWind', precip: 'anaPcp' };
  const CARDS = [
    { k: 'high', label: 'High', unit: '°F', dec: 0, kind: 'at' },
    { k: 'low', label: 'Low', unit: '°F', dec: 0, kind: 'at' },
    { k: 'gust', label: 'Peak gust', unit: ' mph', dec: 0, kind: 'at' },
    { k: 'wind', label: 'Mean wind', unit: ' mph', dec: 0, kind: 'mean' },
    { k: 'precip', label: 'Precipitation', unit: ' in', dec: 2, kind: 'total' },
  ];
  // the CSV files under the charts (spec section 8), their names as the
  // prototype gives them, and their columns
  const CSV = { temp: [['temp', 'Temperature CSV']], wind: [['wind', 'Wind speed CSV'], ['gust', 'Wind gust CSV']], pcp: [['precip', 'Precipitation CSV']] };
  const CSV_NAME = { temp: 'temperature', wind: 'wind speed', gust: 'wind gust', precip: 'precipitation' };
  const CSV_HEAD = ['location_code', 'location', 'contract_date', 'time_zone', 'local_time', 'valid_utc', 'file', 'copy_used',
    'nodd_first_found', 'nodd_last_modified', 'nomads_first_found', 'nomads_last_modified',
    'stored_value', 'stored_unit', 'converted_unrounded', 'unit'];
  const CSV_TAIL = {
    temp: ['rounded', 'counted', 'hourly_resolution_value_state', 'hourly_final_at',
           'high_so_far_unrounded', 'high_so_far_rounded', 'low_so_far_unrounded', 'low_so_far_rounded'],
    wind: ['rounded', 'counted', 'mean_so_far_unrounded', 'mean_so_far_rounded'],
    gust: ['rounded', 'counted', 'peak_so_far_unrounded', 'peak_so_far_rounded'],
    precip: ['rounded_for_display', 'counted', 'total_so_far_unrounded', 'total_so_far_rounded'],
  };
  const UNIT = { temp: ['K', 'F', '°F'], wind: ['m/s', 'mph', 'mph'], gust: ['m/s', 'mph', 'mph'], precip: ['mm', 'in', 'in'] };

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // a value the lane has already rounded or cut, written at its own precision;
  // toFixed only pads it, since the value already sits at that precision
  const fx = (v, d) => (v == null ? '·' : Number(v).toFixed(d));
  const pad2 = n => String(n).padStart(2, '0');
  const str = v => (v == null ? '' : String(v));

  // ---- times in the place's own zone, never by a fixed offset
  const fmts = {};
  const fmt = (tz, o) => { const k = tz + JSON.stringify(o); return fmts[k] || (fmts[k] = new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: tz }, o))); };
  const clock = (tz, ms) => fmt(tz, { hour: 'numeric', minute: '2-digit' }).format(ms);
  const clockS = (tz, ms) => fmt(tz, { hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(ms);
  const clockZ = (tz, ms) => fmt(tz, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(ms);
  const hourZ = (tz, ms) => fmt(tz, { hour: 'numeric', timeZoneName: 'short' }).format(ms);
  const hourOnly = (tz, ms) => fmt(tz, { hour: 'numeric' }).format(ms);
  const zone = (tz, ms) => ((fmt(tz, { hour: 'numeric', timeZoneName: 'short' }).formatToParts(ms).find(p => p.type === 'timeZoneName')) || {}).value || '';
  const localHour = (tz, ms) => Number(fmt(tz, { hour: 'numeric', hourCycle: 'h23' }).format(ms)) % 24;
  const utc = ms => fmt('UTC', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(ms);
  const dayName = (tz, ms) => fmt(tz, { weekday: 'long', month: 'long', day: 'numeric' }).format(ms);
  const isoZ = ms => new Date(ms).toISOString().replace('.000Z', 'Z');
  // a local time with its UTC offset and zone, "2026-09-28 16:00 -04:00 (EDT)", for the CSV files
  function localStamp(tz, ms) {
    const p = {};
    fmt(tz, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(ms).forEach(x => { p[x.type] = x.value; });
    let off = '';
    try {
      const o = fmt(tz, { timeZoneName: 'longOffset' }).formatToParts(ms).find(x => x.type === 'timeZoneName');
      off = o ? o.value.replace('GMT', '').replace(/^$/, '+00:00') : '';
    } catch (e) { /* a browser without longOffset writes the zone's name alone */ }
    return p.year + '-' + p.month + '-' + p.day + ' ' + pad2(Number(p.hour) % 24) + ':' + p.minute + (off ? ' ' + off : '') + ' (' + zone(tz, ms) + ')';
  }
  const span = ms => {
    const s = Math.max(0, Math.ceil(ms / 1000)), hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
    return hh ? hh + ':' + pad2(mm) + ':' + pad2(s % 60) : pad2(mm) + ':' + pad2(s % 60);
  };
  const when = s => { const t = s ? Date.parse(s) : NaN; return isNaN(t) ? null : t; };
  // when a file came in: its first publication on either server, as the lane
  // judges the cut-off on, or the earliest Last-Modified time on record
  function arrival(pub, F) {
    const t = when(pub);
    if (t != null) return { at: t, src: null };
    let best = null;
    ['nodd', 'nomads'].forEach(s => {
      const lm = F && F[s] ? when(F[s].lm) : null;
      if (lm != null && (!best || lm < best.at)) best = { at: lm, src: s };
    });
    return best || { at: null, src: null };
  }

  /* The place-day as the display reads it. Each hour knows whether its
     analysis and its precipitation were read, whether either file came after
     the cut-off (`late`, shown and never counted), when each came in, and the
     lane's figures. A counted hour enters the day's values once every counted
     hour before it has arrived (`anlIn`, `pcpIn`): the running values of an
     hour cover every counted hour up to it, so an hour that arrived ahead of
     an earlier one waits for it rather than show a total the arrived hours do
     not make. Files arrive in order, so this is almost always the hour's own
     time. The day can be replayed only when every counted file's time is on
     record (`timed`); otherwise it is shown as it stands. */
  function model(doc, entry, product, tz) {
    const rows = (doc && Array.isArray(doc.hours)) ? doc.hours : [];
    const running = !!(doc && doc.rowShape >= 2);
    const hours = rows.map((r, i) => {
      const t = Date.parse(r.t), v = r[product] || null;
      const o = { i, t, iso: r.t, end: t + HOUR, local: r.local, repeat: /\*$/.test(String(r.local || '')), v };
      o.anl = !!(v && (v.temp != null || v.wind != null || v.gust != null));
      o.pcp = !!(v && v.precip != null);
      o.anlLate = !!(v && v.late);
      o.pcpLate = !!(v && v.pcpLate);
      o.anlFile = (v && v.anlFile) || null;
      o.pcpFile = (v && v.pcpFile) || null;
      const a = arrival(v && v.pub, o.anlFile), p = arrival(v && v.pcpPub, o.pcpFile);
      o.anlAt = a.at; o.anlBy = (v && v.pub ? v.pubSrc || v.src : a.src) || null;
      o.pcpAt = p.at; o.pcpBy = (v && v.pcpPub ? v.pcpPubSrc || v.pcpSrc : p.src) || null;
      o.show = running && v ? v.show || null : null;
      o.run = running && v ? v.run || null : null;
      o.runX = (v && v.runX) || null;
      o.raw = (v && v.raw) || null;
      o.x = (v && v.x) || null;
      o.hourly = (v && v.hourly) || null;
      if (o.hourly) { o.hcut = when(o.hourly.cut); o.hat = when(o.hourly.at); }
      return o;
    });
    const timed = running && hours.every(o => (!o.anl || o.anlLate || o.anlAt != null) && (!o.pcp || o.pcpLate || o.pcpAt != null));
    let a = -Infinity, p = -Infinity;
    hours.forEach(o => {
      if (o.anl && !o.anlLate) { a = Math.max(a, o.anlAt == null ? -Infinity : o.anlAt); o.anlIn = a; }
      if (o.pcp && !o.pcpLate) { p = Math.max(p, o.pcpAt == null ? -Infinity : o.pcpAt); o.pcpIn = p; }
    });
    const e = (entry && entry[product]) || null;
    const start = hours.length ? hours[0].t : NaN, end = hours.length ? hours[hours.length - 1].end : NaN;
    const cutoff = end + CUTOFF;
    /* A final day's replay runs on past its cut-off to the last hour's own
       hourly cut-off, three hours after it, so the 11 PM hour shows final,
       and past any file the rule before 14 October counted late; then a
       quarter hour more, as the prototype does. */
    const lastCut = Math.max(-Infinity, ...hours.map(o => (o.hcut == null ? -Infinity : o.hcut)));
    const replayEnd = Math.max(cutoff, lastCut, timed ? Math.max(a, p) : -Infinity) + AFTER;
    return { doc, tz: (doc && doc.tz) || tz || 'UTC', running, timed, hours, start, end, cutoff, replayEnd, e,
             final: !!(e && e.final), review: !!(e && e.review), pReview: !!(e && e.precip && e.precip.review),
             closed: !!(e && e.closed) };
  }

  // the span the slider covers: a live day runs to the clock, a day past its
  // last cut-off to a quarter hour after it; a day that cannot be replayed is
  // shown as it stands, at the end of that span
  function bounds(P, now) {
    const past = !(now < P.replayEnd);
    return { min: P.start, max: past ? P.replayEnd : Math.max(P.start, now), live: !past };
  }

  // the day as of T: which hours are in, and the lane's running values at the
  // last counted hour in, for the analysis and for the precipitation. A day
  // that cannot be replayed is always at its end, with every counted hour in
  function stateAt(P, T) {
    const inA = o => o.anl && !o.anlLate && (!P.timed || o.anlIn <= T);
    const inP = o => o.pcp && !o.pcpLate && (!P.timed || o.pcpIn <= T);
    let lastA = null, lastP = null, nA = 0, nP = 0;
    P.hours.forEach(o => { if (inA(o)) { lastA = o; nA++; } if (inP(o)) { lastP = o; nP++; } });
    const atCut = T >= P.cutoff;
    return { T, inA, inP, lastA, lastP, nA, nP, runA: lastA && lastA.run, runP: lastP && lastP.run,
             atCut, final: P.final && atCut, of: P.hours.length,
             // a late file is shown, dimmed, once the cut-off it missed has passed
             lateA: o => o.anl && o.anlLate && atCut, lateP: o => o.pcp && o.pcpLate && atCut };
  }

  /* An hour's hourly contract as it stood at T, from the lane's state and the
     moment it became final (`at`). It shows final only from that moment on,
     provisional before it, and an hour under review shows provisional until
     its hourly cut-off, three hours after the hour. */
  function hourlyAt(o, T) {
    const hr = o && o.hourly;
    if (!hr) return null;
    if (hr.state === 'final') {
      const at = o.hat != null ? o.hat : o.hcut;
      return at != null && T >= at ? 'final' : 'provisional';
    }
    if (hr.state === 'review') return o.hcut != null && T >= o.hcut ? 'review' : 'provisional';
    return 'provisional';
  }
  // the hourly contract's state in words (owner's wording of 2026-11-04);
  // `q` is the apostrophe, typographic on the page and plain in a CSV file
  function hourlyText(o, T, tz, q) {
    const st = hourlyAt(o, T);
    if (!st) return '';
    const hr = o.hourly, ap = q || '’';
    const cut = o.hcut != null ? clockS(tz, o.hcut) : 'not on record';
    if (st === 'final') {
      if (hr.basis === 'nomads') {
        return 'final at ' + (o.hat != null ? clockS(tz, o.hat) : 'a time not on record') + ', when NOMADS published the file'
          + (hr.src && hr.src !== 'nomads' && SRC[hr.src] ? '. The value shown is ' + SRC[hr.src] + ap + 's copy.' : '');
      }
      if (hr.basis === 'cutoff') return 'final at the hourly cut-off, ' + clockS(tz, o.hat != null ? o.hat : o.hcut) + ', from NOAA Open Data' + ap + 's copy';
      if (hr.basis === 'norecord') return 'final by the hourly cut-off, ' + cut + ' (NOMADS' + ap + 's copy is not on record before 3 Nov 15:13 UTC)';
      return 'final at ' + clockS(tz, o.hat != null ? o.hat : o.hcut);
    }
    if (st === 'review') {
      // a file read before both servers were (basis norecord) may be on NOAA
      // Open Data, published after the cut-off, with no NOMADS copy on record
      if (hr.basis === 'norecord') {
        const od = o.anlFile && o.anlFile.nodd && o.anlFile.nodd.lm;
        return 'no value: ' + (od ? 'NOAA Open Data published it after the hourly cut-off, ' : 'not published by the hourly cut-off, ') + cut
          + ', and NOMADS' + ap + 's copy is not on record. Under review.';
      }
      return 'no value: not published by the hourly cut-off, ' + cut + ', under review';
    }
    return 'provisional, until NOMADS publishes the file or the hourly cut-off, ' + cut;
  }
  // the same, in the every-hour table's few words
  const HOURLY_WORD = { nomads: 'final', cutoff: 'final at the cut-off', norecord: 'final by the cut-off' };
  function hourlyWord(o, T) {
    const st = hourlyAt(o, T);
    if (st === 'final') return HOURLY_WORD[o.hourly.basis] || 'final';
    return st === 'review' ? 'under review' : st || '';
  }

  // whether an hour counts for the day, from the file's record, or from the
  // row's own late flag in a file written before the records
  function counted(F, has, late) {
    if (F && F.counts && F.afterCut) return OLD_RULE;
    if (F && F.counts) return 'counted';
    if (F && F.afterCut) return 'published after the cut-off, not counted';
    if (F) return 'not counted';
    if (!has) return '';
    return late ? 'published after the cut-off, not counted' : 'counted';
  }
  const CSV_COUNT = { counted: 'yes', 'published after the cut-off, not counted': 'no, after the cut-off', 'not counted': 'no', '': 'not published' };

  // whether a countdown of the day reads late at `now` and has for no more
  // than CHASE, the same test as the countdowns' own late state
  function lateAt(P, now) {
    const S = stateAt(P, now);
    if (S.atCut) return false;
    const a = P.hours.find(o => !S.inA(o)), r = P.hours.find(o => !S.inP(o));
    const late = by => now > by && now <= by + CHASE;
    return !!((a && late(a.t + LAG.anl.by)) || (r && late(r.end + LAG.pcp.by)));
  }
  // whether an hour the file holds provisional is past its own cut-off, for
  // no more than CHASE, so the lane's next pass has settled it
  const hourlyDue = (P, now) => P.hours.some(o => o.hourly && o.hourly.state === 'provisional'
    && o.hcut != null && now >= o.hcut && now <= o.hcut + CHASE);

  function create(opts) {
    const PRODUCT = opts.product || 'rtma', NAME = opts.productName || 'RTMA';
    const tip = WXC.tooltip();
    let P = null, key = null, T = 0, live = true, playing = null, lastMin = -1, fetching = false;
    let ctx = {};

    // ---- the furniture, built once and kept, so a replay holds its time
    //      across redraws of the same place-day
    const node = h('div', { id: 'anaSettle' });
    const status = h('div', { class: 'anastatus', id: 'anaStatus' });
    const next = h('div', { class: 'ananext', id: 'anaNext' });
    const cards = h('div', { class: 'anacards', id: 'anaCards' });
    const none = h('p', { class: 'cap anaold', id: 'anaNone' });
    const chart = (id, title, legend, cap, csv) => {
      const svg = WXC.el('svg', { class: 'anasvg', role: 'img', tabindex: '0', 'data-tip-pin': '1',
        'aria-label': title + ' through the day. The arrow keys step through the hours.' });
      const row = h('div', { class: 'anacsvrow', 'data-for': csv }, [h('span', { text: 'CSV for this place-day:' })]);
      CSV[csv].forEach(([k, label]) => {
        const b = h('button', { type: 'button', 'data-csv': k, id: 'anaCsv-' + k, text: label });
        b.onclick = () => save(k);
        row.appendChild(b);
      });
      const sec = h('section', { class: 'anachart', id }, [
        h('h3', {}, [document.createTextNode(title + ' '), h('span', { class: 'analeg', html: legend })]),
        h('p', { class: 'cap', text: cap }), h('div', { class: 'anaplot' }, [svg]), row]);
      return { sec, svg };
    };
    const C = {
      temp: chart('anaTemp', 'Temperature', '<span><i style="background:var(--ana-rtma)"></i>Each hour, °F</span>',
        'Each dot is one hour’s analysis, rounded to a whole degree, which is the value an hourly contract for that hour resolves on. '
        + 'The day’s high and low are the highest and lowest dots. Point at an hour, or tap it, for the value as stored, converted and rounded, '
        + 'the hourly contract’s state and when its file was published.', 'temp'),
      wind: chart('anaWind', 'Wind speed and gust', '<span><i style="background:var(--accent)"></i>Sustained wind</span><span><i style="background:var(--vbar)"></i>Gust</span>',
        'Each hour’s analysed wind and gust at 10 m, rounded to a whole mile per hour. The peak gust is the highest gust of the day. '
        + 'The dashed line is the mean of the sustained winds so far, taken before rounding.', 'wind'),
      pcp: chart('anaPcp', 'Precipitation', '<span><i style="background:var(--cool)"></i>Total so far</span><span><i style="background:var(--ana-rain-soft);height:8px"></i>Each hour</span>',
        'The line is the day’s total so far to a hundredth of an inch, the value the precipitation contract resolves on at the end of the day. '
        + 'Each bar is one hour’s accumulation, which NOAA files under the hour it ends.', 'pcp'),
    };
    const table = h('table', { class: 'anahours', id: 'anaHours' });
    const foot = h('p', { class: 'cap anafoot', id: 'anaFoot' });
    const hoursSec = h('section', { class: 'anahrs' }, [h('h3', { text: 'Every hour' }), h('div', { class: 'anatab' }, [table]), foot]);
    // the replay, last on the page as the specification orders it, and held
    // at the foot of the screen while the day is on it, so the charts it moves
    // stay in view
    const replay = h('div', { class: 'anareplay', id: 'anaClock', role: 'group', 'aria-label': 'Replay the day' });
    const whenEl = h('div', { class: 'anawhen', id: 'anaWhen' });
    const range = h('input', { type: 'range', id: 'anaT', 'aria-label': 'Replay the day: the time it is shown as of' });
    const playB = h('button', { type: 'button', class: 'primary', id: 'anaPlayDay', text: 'Play the day' });
    const latestB = h('button', { type: 'button', id: 'anaLatest', text: 'Latest' });
    const old = h('p', { class: 'cap anaold', id: 'anaOld' });
    replay.appendChild(h('div', { class: 'anaclkrow' }, [h('div', { class: 'anabtns' }, [playB, latestB]), whenEl, range]));
    replay.appendChild(old);
    [status, next, cards, none, C.temp.sec, C.wind.sec, C.pcp.sec, hoursSec, replay].forEach(e => node.appendChild(e));

    const B = () => bounds(P, Date.now());
    function stop() {
      if (playing) { clearInterval(playing); playing = null; }
      playB.textContent = 'Play the day';
    }
    playB.onclick = () => {
      if (!P || !P.timed) return;
      if (playing) return stop();
      const b = B();
      if (T >= b.max - MIN) T = b.min;
      live = false;
      playB.textContent = 'Pause';
      playing = setInterval(() => {
        const b2 = B();
        T = Math.min(b2.max, T + MIN);
        if (T >= b2.max) { stop(); live = b2.live; }
        render();
      }, 30);
    };
    latestB.onclick = () => { if (!P) return; stop(); const b = B(); T = b.max; live = b.live; render(); };
    range.addEventListener('input', () => {
      if (!P) return;
      stop();
      const b = B();
      T = Math.min(b.max, Math.max(b.min, Number(range.value)));
      // dragged to the end of a live day, the display follows the clock again
      live = b.live && T >= b.max - MIN / 2;
      render();
    });
    let rz = null;
    const onResize = () => { clearTimeout(rz); rz = setTimeout(() => { if (P && !node.hidden) drawCharts(stateAt(P, T)); }, 120); };
    window.addEventListener('resize', onResize);

    /* Once a second: a live day follows the clock, the countdowns tick, and
       everything else is redrawn when the minute turns. Inside the arrival
       windows, while a countdown reads late, and while an hourly contract the
       file holds provisional is past its cut-off, a live day's file is fetched
       again each minute. */
    const ticker = setInterval(() => {
      if (!P || !node.isConnected || node.closest('[hidden]')) return;
      const now = Date.now(), b = bounds(P, now);
      if (live) T = b.max;
      const m = Math.floor(now / MIN);
      if (m !== lastMin) {
        lastMin = m;
        render();
        if (b.live && opts.refetch && !fetching && !document.hidden
            && ((!P.final && (inWindow(new Date(now).getUTCMinutes()) || lateAt(P, now))) || hourlyDue(P, now))) {
          const want = key;
          fetching = true;
          Promise.resolve(opts.refetch()).then(r => {
            if (r && r.doc && want === key) load(r.doc, r.entry, true);
          }).catch(() => { /* the next minute tries again */ }).then(() => { fetching = false; });
        }
      } else if (live && P.timed) renderTop(stateAt(P, T));
    }, 1000);

    function load(doc, entry, keep) {
      P = model(doc, entry, PRODUCT, ctx.tz);
      // the file was just read, so the next fetch waits for the next minute
      lastMin = Math.floor(Date.now() / MIN);
      const b = B();
      if (!keep || live || !P.timed) { T = b.max; live = b.live; } else T = Math.min(b.max, Math.max(b.min, T));
      render();
    }

    /* show({key, doc, entry, unread, tz, varKey, place, noneText}): the
       place-day to draw. The same key keeps the replay's time; a new one opens
       on the latest. */
    function show(c) {
      ctx = c || {};
      if (ctx.key !== key) { stop(); key = ctx.key; load(ctx.doc, ctx.entry, false); return; }
      if (P && P.doc === ctx.doc) { render(); return; }
      load(ctx.doc, ctx.entry, true);
    }

    function render() {
      if (!P) return;
      const hasRows = P.hours.length > 0 && !!P.doc;
      const S = stateAt(P, T);
      renderStatus(S);
      [C.temp.sec, C.wind.sec, C.pcp.sec, hoursSec, replay].forEach(e => { e.hidden = !hasRows; });
      none.hidden = hasRows || !ctx.noneText;
      none.textContent = ctx.noneText || '';
      renderCards(S);
      if (!hasRows) { next.innerHTML = ''; return; }
      const b = B();
      range.min = String(b.min); range.max = String(b.max); range.step = String(MIN); range.value = String(T);
      // a file that cannot be replayed says why, in the replay's place
      const rp = P.timed;
      range.hidden = playB.hidden = latestB.hidden = whenEl.hidden = !rp;
      replay.classList.toggle('still', !rp);
      old.hidden = rp;
      old.textContent = !P.running
        ? 'This day’s file was written before the hour by hour figures, so the day is shown as the file stands, with no running figures and no replay.'
        : 'Some of this day’s files have no publication time on record, so the day is shown as it stands, with no replay.';
      renderTop(S);
      drawCharts(S);
      renderTable(S);
    }

    // ---- the day's state, one line
    function renderStatus(S) {
      const tz = P.tz, e = P.e || {};
      // the cut-off with its date, or plainly when there are no hours to date it by
      const cutAt = P.hours.length ? 'the cut-off, ' + clock(tz, P.cutoff) + ' on ' + dayName(tz, P.cutoff) : 'the 12:45 AM cut-off';
      const n = P.running ? (S.runA ? S.runA.hours : 0) : (e.hours || 0);
      const of = e.of || S.of;
      let cls, word, text;
      if (ctx.unread) { cls = 'prov'; word = 'No data'; text = 'The day file could not be read.'; }
      else if (P.review && (S.atCut || !P.running)) { cls = 'review'; word = 'Under review'; text = 'No ' + NAME + ' hour was published by the 12:45 AM cut-off, so the day is under review.'; }
      else if (P.closed) { cls = 'closed'; word = 'Closed'; text = 'Closed on ' + n + ' of ' + of + ' hours, under the rule before 14 October.'; }
      else if (P.running ? S.final : P.final) { cls = 'final'; word = 'Final'; text = 'Resolved at ' + cutAt + ', on ' + n + ' of ' + of + ' hours.'; }
      else if (P.running && S.atCut) { cls = 'prov'; word = 'Provisional'; text = n + ' of ' + of + ' hours were in by ' + cutAt + '. The day turns final when the site next writes it.'; }
      else if (!n) { cls = 'prov'; word = 'Provisional'; text = 'No hour of this day is in yet. The day becomes final at ' + cutAt + '.'; }
      else { cls = 'prov'; word = 'Provisional'; text = n + ' of ' + of + ' hours are in. The day becomes final at ' + cutAt + '.'; }
      status.innerHTML = '<span class="anapill ' + cls + '">' + esc(word) + '</span><span class="anastxt">' + esc(text) + '</span>';
    }

    // ---- the replay's clock and the two countdowns
    function renderTop(S) {
      const tz = P.tz;
      whenEl.innerHTML = '<b>' + esc(clockZ(tz, T)) + '</b><span>' + esc(utc(T)) + ' UTC · ' + esc(dayName(tz, T)) + '</span>';
      if (!P.running) { next.innerHTML = ''; return; }
      const a = P.hours.find(o => !S.inA(o)), r = P.hours.find(o => !S.inP(o));
      next.innerHTML = due('anl', a && { h: a, due: a.t + LAG.anl.due, by: a.t + LAG.anl.by }, S)
        + due('pcp', r && { h: r, due: r.end + LAG.pcp.due, by: r.end + LAG.pcp.by }, S);
    }
    function due(kind, d, S) {
      const tz = P.tz, cut = clock(tz, P.cutoff);
      const what = kind === 'anl' ? 'Temperature, wind and gust' : 'Precipitation';
      const box = (w, label, cd, cls, note, state) => '<div class="anadue" data-kind="' + kind + '" data-state="' + state + '"><span class="what">'
        + esc(w) + '</span><div class="main"><b>' + esc(label) + '</b>' + (cd ? '<span class="cd' + cls + '">' + esc(cd) + '</span>' : '')
        + '</div><span class="ananote">' + esc(note) + '</span></div>';
      if (!d) {
        if (S.final) return box(what, 'All ' + S.of + ' hours counted', '', '', 'Final since the cut-off, ' + cut + '.', 'done');
        if (S.atCut) return box(what, 'All ' + S.of + ' hours in', '', '', 'The cut-off has passed, and the day turns final when the site next writes it.', 'done');
        return box(what, 'All ' + S.of + ' hours in', span(P.cutoff - T), '', 'The day becomes final at the cut-off, ' + cut + '.', 'done');
      }
      const label = kind === 'anl' ? 'Next analysis' : 'Next accumulation';
      const w = kind === 'anl' ? what + ' for ' + hourZ(tz, d.h.t) : what + ' from ' + hourOnly(tz, d.h.t) + ' to ' + hourZ(tz, d.h.end);
      if (S.atCut) return box(w, label, 'missed', ' late', kind === 'anl'
        ? 'Not published by the 12:45 AM cut-off, so the day resolves on the hours that were.'
        : 'Not published by the 12:45 AM cut-off, so the day’s total leaves this hour out.', 'missed');
      if (T < d.due) return box(w, label, span(d.due - T), '', 'Expected about ' + clock(tz, d.due) + ', usually by ' + clock(tz, d.by) + '.', 'counting');
      if (T <= d.by) return box(w, label, 'due now', '', 'Usually published by ' + clock(tz, d.by) + '.', 'due');
      // late means the file is not in yet, past the time it is usually out
      // by. The lane reads each file within about two minutes of NOAA
      // publishing it inside the arrival windows, the precipitation as soon
      // as it lands, and the display keeps fetching while a countdown reads
      // late (CHASE), so the state ends when the file is in. A replay runs
      // at NOAA's own times, where late means NOAA was late
      return box(w, label, 'late', ' late', 'Usually published by ' + clock(tz, d.by) + '. It counts if published by the cut-off, ' + cut + '.', 'late');
    }

    // ---- the five values; a card is a button that sets the map's variable
    function renderCards(S) {
      const tz = P.tz, e = P.e || {};
      const so = (P.running ? S.final : P.final) ? '' : ' so far';
      // a redraw keeps the keyboard on the card it was on
      const had = cards.contains(document.activeElement) ? document.activeElement.dataset.var : null;
      cards.textContent = '';
      CARDS.forEach(c => {
        const on = c.k === ctx.varKey;
        const card = h('button', { type: 'button', class: 'anacard' + (on ? ' on' : ''), 'data-var': c.k, 'aria-pressed': on ? 'true' : 'false',
                                   title: 'Show this value on the map' });
        card.appendChild(h('span', { class: 'al', text: c.label }));
        const review = c.k === 'precip' ? P.pReview : P.review;
        let val = null, n = null;
        if (P.running) {
          const run = c.k === 'precip' ? S.runP : S.runA;
          val = run ? run[c.k] : null;
          n = c.k === 'precip' ? (val && val.hours) : (run && run.hours);
        } else {
          val = e[c.k] || null;
          n = c.k === 'precip' ? (val && val.hours) : e.hours;
        }
        let why = null;
        if (ctx.unread) why = 'the day file could not be read';
        else if (review && (S.atCut || !P.running)) why = c.k === 'precip' ? 'no ' + NAME + ' precipitation published by the 12:45 AM cut-off' : 'no ' + NAME + ' hour published by the 12:45 AM cut-off';
        else if (!val || val.value == null) why = P.running && P.hours.some(o => o.anl || o.pcp) ? 'no hour in yet' : 'no ' + NAME + ' hours read yet';
        if (why) {
          card.appendChild(h('span', { class: 'av dim', text: 'no value' }));
          card.appendChild(h('span', { class: 'ae', text: why }));
        } else {
          card.appendChild(h('span', { class: 'av', text: fx(val.value, c.dec) + c.unit }));
          const d = c.kind === 'at' ? (val.at ? 'at ' + hourZ(tz, Date.parse(val.at)) : '')
            : (c.kind === 'mean' ? 'mean of ' : 'total of ') + n + (n === 1 ? ' hour' : ' hours');
          card.appendChild(h('span', { class: 'ae', text: d + so }));
        }
        card.onclick = () => { if (opts.onVar) opts.onVar(c.k); };
        cards.appendChild(card);
        if (had === c.k) card.focus({ preventScroll: true });
      });
    }

    // ---- the three charts on one time axis
    const M = { l: 46, r: 18, t: 22, b: 30 };
    const nice = (rng, steps) => steps.find(s => rng / s <= 6) || steps[steps.length - 1];
    const path = pts => pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('');
    let geo = {};
    const focusHour = {};          // the hour the keyboard is on, per chart
    function frame(W, H, yMin, yMax, yStep, yFmt, S) {
      const x = t => M.l + (t - P.start) / (P.end - P.start) * (W - M.l - M.r);
      const y = v => H - M.b - (v - yMin) / (yMax - yMin) * (H - M.t - M.b);
      let g = '';
      for (let k = Math.ceil(yMin / yStep - 1e-9); k * yStep <= yMax + 1e-9; k++) {
        const v = k * yStep;
        g += '<line class="grid" x1="' + M.l + '" x2="' + (W - M.r) + '" y1="' + y(v).toFixed(1) + '" y2="' + y(v).toFixed(1) + '"/>'
          + '<text x="' + (M.l - 6) + '" y="' + (y(v) + 4).toFixed(1) + '" text-anchor="end">' + esc(yFmt(v)) + '</text>';
      }
      const every = W < 560 ? 6 : 3;
      P.hours.forEach(o => {
        if (o.repeat || localHour(P.tz, o.t) % every) return;
        g += '<line class="axis" x1="' + x(o.t).toFixed(1) + '" x2="' + x(o.t).toFixed(1) + '" y1="' + (H - M.b) + '" y2="' + (H - M.b + 4) + '"/>'
          + '<text x="' + x(o.t).toFixed(1) + '" y="' + (H - M.b + 17) + '" text-anchor="middle">' + esc(hourOnly(P.tz, o.t)) + '</text>';
      });
      g += '<line class="axis" x1="' + M.l + '" x2="' + (W - M.r) + '" y1="' + (H - M.b) + '" y2="' + (H - M.b) + '"/>'
        + '<line class="axis dayend" x1="' + x(P.end).toFixed(1) + '" x2="' + x(P.end).toFixed(1) + '" y1="' + M.t + '" y2="' + (H - M.b) + '"/>';
      let pre = '', post = '';
      if (T < P.end) {
        const xn = Math.max(M.l, x(T));
        pre = '<rect class="future" x="' + xn.toFixed(1) + '" y="' + M.t + '" width="' + (x(P.end) - xn).toFixed(1) + '" height="' + (H - M.t - M.b) + '"/>';
        if (x(P.end) - xn > 150) pre += '<text class="tocome" x="' + (x(P.end) - 8).toFixed(1) + '" y="' + (M.t + 14) + '" text-anchor="end">to come</text>';
        if (T > P.start) post = '<line class="now" x1="' + xn.toFixed(1) + '" x2="' + xn.toFixed(1) + '" y1="' + (M.t - 6) + '" y2="' + (H - M.b) + '"/>'
          + '<text class="lab nowlab" x="' + xn.toFixed(1) + '" y="' + (M.t - 9) + '" text-anchor="middle">' + esc(clock(P.tz, T)) + '</text>';
      }
      return { x, y, g, pre, post, H };
    }
    // the next hour due, as a dashed line at its time with its expected arrival
    function dueMark(f, H, d, top) {
      if (!d) return '';
      const xx = f.x(d.t);
      return '<line class="duemark" x1="' + xx.toFixed(1) + '" x2="' + xx.toFixed(1) + '" y1="' + M.t + '" y2="' + (H - M.b) + '"/>'
        + '<text class="duelab" x="' + (xx + 4).toFixed(1) + '" y="' + (top ? M.t + 12 : H - M.b - 6) + '">next, due ' + esc(clock(P.tz, d.due)) + '</text>';
    }
    // how far right a label may run before it meets the next-due line, the
    // now line or the plot's edge
    function roomTo(f, W, S, kind) {
      const d = nextDue(S, kind);
      let lim = W - M.r;
      if (d) lim = Math.min(lim, f.x(d.t) - 4);
      if (T < P.end && T > P.start) lim = Math.min(lim, f.x(T) - 4);
      return lim;
    }
    function nextDue(S, kind) {
      if (S.atCut) return null;
      if (kind === 'anl') { const a = P.hours.find(o => !S.inA(o)); return a && { t: a.t, due: a.t + LAG.anl.due }; }
      const r = P.hours.find(o => !S.inP(o)); return r && { t: r.end, due: r.end + LAG.pcp.due };
    }
    // the hour's value as shown, or its exact value on a file with no shown values
    const shown = (o, k) => (o.show ? o.show[k] : (o.v ? o.v[k] : null));
    const hourAt = at => (at ? P.hours.find(o => o.t === Date.parse(at)) || null : null);
    function segments(list) {
      const segs = []; let cur = [], prev = null;
      list.forEach(o => { if (prev && o.i !== prev.i + 1) { segs.push(cur); cur = []; } cur.push(o); prev = o; });
      if (cur.length) segs.push(cur);
      return segs;
    }
    function mark(f, W, o, v, txtv, color, above, cls) {
      const cx = f.x(o.t), cy = f.y(v), ty = above ? cy - 12 : cy + 20;
      const anchor = cx < M.l + 60 ? 'start' : cx > W - M.r - 60 ? 'end' : 'middle';
      return '<circle class="' + cls + '" cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="7" fill="none" stroke="' + color + '" stroke-width="2"/>'
        + '<text class="lab ' + cls + 'lab" x="' + cx.toFixed(1) + '" y="' + ty.toFixed(1) + '" text-anchor="' + anchor + '" style="fill:' + color + '">' + esc(txtv) + '</text>';
    }

    function drawTemp(W, S) {
      const H = 250, got = P.hours.filter(o => S.inA(o) && shown(o, 'temp') != null), late = P.hours.filter(o => S.lateA(o) && shown(o, 'temp') != null);
      const vals = got.concat(late).map(o => Number(shown(o, 'temp')));
      let lo = vals.length ? Math.min(...vals) - 2 : 40, hi = vals.length ? Math.max(...vals) + 2 : 60;
      if (hi - lo < 6) { const m = (hi + lo) / 2; lo = Math.floor(m - 3); hi = Math.ceil(m + 3); }
      const step = nice(hi - lo, [1, 2, 5, 10, 20]);
      lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
      const f = frame(W, H, lo, hi, step, v => v + '°', S);
      let s = f.pre + f.g;
      segments(got).forEach(seg => { s += '<path class="series" d="' + path(seg.map(o => [f.x(o.t), f.y(shown(o, 'temp'))])) + '" fill="none" stroke="var(--ana-rtma)" stroke-width="2"/>'; });
      got.forEach(o => { s += '<circle class="hr" cx="' + f.x(o.t).toFixed(1) + '" cy="' + f.y(shown(o, 'temp')).toFixed(1) + '" r="3.6" fill="var(--ana-rtma)"/>'; });
      late.forEach(o => { s += '<circle class="hr late" cx="' + f.x(o.t).toFixed(1) + '" cy="' + f.y(shown(o, 'temp')).toFixed(1) + '" r="3.6" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>'; });
      const hiV = P.running ? S.runA && S.runA.high : (P.e || {}).high, loV = P.running ? S.runA && S.runA.low : (P.e || {}).low;
      const oh = hiV && hourAt(hiV.at), ol = loV && hourAt(loV.at);
      if (oh && shown(oh, 'temp') != null) s += mark(f, W, oh, shown(oh, 'temp'), 'High ' + hiV.value + '°F', 'var(--warm)', true, 'mkhi');
      if (ol && ol !== oh && shown(ol, 'temp') != null) s += mark(f, W, ol, shown(ol, 'temp'), 'Low ' + loV.value + '°F', 'var(--cool)', false, 'mklo');
      s += dueMark(f, H, nextDue(S, 'anl')) + f.post;
      return { H, s, f };
    }

    function drawWind(W, S) {
      const H = 230, got = P.hours.filter(o => S.inA(o) && shown(o, 'wind') != null), late = P.hours.filter(o => S.lateA(o) && shown(o, 'wind') != null);
      const top = Math.max(10, ...got.concat(late).map(o => Number(shown(o, 'gust') != null ? shown(o, 'gust') : shown(o, 'wind'))));
      const step = nice(top, [2, 5, 10, 20]);
      const f = frame(W, H, 0, Math.ceil((top + 2) / step) * step, step, v => String(v), S);
      let s = f.pre + f.g;
      segments(got).forEach(seg => {
        const gs = seg.filter(o => shown(o, 'gust') != null);
        if (gs.length) s += '<path class="series gustline" d="' + path(gs.map(o => [f.x(o.t), f.y(shown(o, 'gust'))])) + '" fill="none" stroke="var(--vbar)" stroke-width="2"/>';
        s += '<path class="series windline" d="' + path(seg.map(o => [f.x(o.t), f.y(shown(o, 'wind'))])) + '" fill="none" stroke="var(--accent)" stroke-width="2"/>';
      });
      got.forEach(o => {
        if (shown(o, 'gust') != null) s += '<circle class="hr" cx="' + f.x(o.t).toFixed(1) + '" cy="' + f.y(shown(o, 'gust')).toFixed(1) + '" r="3" fill="var(--vbar)"/>';
        s += '<circle class="hr" cx="' + f.x(o.t).toFixed(1) + '" cy="' + f.y(shown(o, 'wind')).toFixed(1) + '" r="3" fill="var(--accent)"/>';
      });
      late.forEach(o => { s += '<circle class="hr late" cx="' + f.x(o.t).toFixed(1) + '" cy="' + f.y(shown(o, 'wind')).toFixed(1) + '" r="3" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>'; });
      const mean = P.running ? S.runA && S.runA.wind : (P.e || {}).wind;
      const last = got[got.length - 1];
      if (mean && mean.value != null && last) {
        const yy = f.y(mean.value), xe = f.x(last.t);
        // the label sits past the line's end while there is room before the
        // next marker, and above the line's start once there is not
        const room = xe + 92 < roomTo(f, W, S, 'anl');
        s += '<line class="meanline" x1="' + f.x(P.start).toFixed(1) + '" x2="' + xe.toFixed(1) + '" y1="' + yy.toFixed(1) + '" y2="' + yy.toFixed(1) + '" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="6 4"/>'
          + '<text class="lab meanlab" x="' + (room ? xe + 6 : M.l + 4).toFixed(1) + '" y="' + (room ? yy + 4 : yy - 6).toFixed(1) + '" style="fill:var(--accent)">Mean ' + mean.value + ' mph</text>';
      }
      const pk = P.running ? S.runA && S.runA.gust : (P.e || {}).gust, op = pk && hourAt(pk.at);
      if (op && shown(op, 'gust') != null) s += mark(f, W, op, shown(op, 'gust'), 'Peak gust ' + pk.value + ' mph', 'var(--vbar)', true, 'mkpk');
      s += dueMark(f, H, nextDue(S, 'anl')) + f.post;
      return { H, s, f };
    }

    function drawPcp(W, S) {
      const H = 210, wet = P.hours.filter(o => S.inP(o)), late = P.hours.filter(o => S.lateP(o));
      // the bars stand at each hour's exact accumulation and the line at the
      // lane's running total, exact; both only place pixels
      const amt = o => Number(o.v.precip);
      const totals = wet.map(o => (o.run && o.run.precip ? Number(o.run.precip.exact) : 0));
      const peak = Math.max(0, ...wet.concat(late).map(amt), ...totals);
      const top = [0.1, 0.25, 0.5, 1, 1.5, 2, 3, 4, 6].find(v => v >= peak * 1.15) || 8;
      const step = nice(top, [0.02, 0.05, 0.1, 0.25, 0.5, 1]);
      const f = frame(W, H, 0, top, step, v => v.toFixed(2), S);
      let s = f.pre + f.g;
      wet.forEach(o => { s += '<rect class="pbar" x="' + (f.x(o.t) + 1).toFixed(1) + '" y="' + f.y(amt(o)).toFixed(1) + '" width="' + Math.max(1, f.x(o.end) - f.x(o.t) - 2).toFixed(1) + '" height="' + Math.max(0, f.y(0) - f.y(amt(o))).toFixed(1) + '" fill="var(--ana-rain-soft)"/>'; });
      late.forEach(o => { s += '<rect class="pbar late" x="' + (f.x(o.t) + 1).toFixed(1) + '" y="' + f.y(amt(o)).toFixed(1) + '" width="' + Math.max(1, f.x(o.end) - f.x(o.t) - 2).toFixed(1) + '" height="' + Math.max(0, f.y(0) - f.y(amt(o))).toFixed(1) + '" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>'; });
      const runP = P.running ? S.runP && S.runP.precip : null;
      if (P.running && wet.length && runP) {
        const pts = [[f.x(P.start), f.y(0)]];
        let prev = 0;
        wet.forEach((o, k) => { pts.push([f.x(o.end), f.y(prev)]); prev = totals[k]; pts.push([f.x(o.end), f.y(prev)]); });
        const lastEnd = wet[wet.length - 1].end, xe = f.x(lastEnd), ye = f.y(prev);
        const anchor = xe + 100 > roomTo(f, W, S, 'pcp') ? 'end' : 'start';
        s += '<path class="runline" d="' + path(pts) + '" fill="none" stroke="var(--cool)" stroke-width="2.2"/>'
          + '<circle cx="' + xe.toFixed(1) + '" cy="' + ye.toFixed(1) + '" r="3.6" fill="var(--cool)"/>'
          + '<text class="lab runlab" x="' + (xe + (anchor === 'end' ? -8 : 8)).toFixed(1) + '" y="' + (ye - 8).toFixed(1) + '" text-anchor="' + anchor + '" style="fill:var(--cool)">'
          + fx(runP.value, 2) + ' in' + (S.final ? '' : ' so far') + '</text>';
        if (Number(runP.exact) === 0 && xe - M.l > 260) s += '<text class="dry" x="' + ((M.l + xe) / 2).toFixed(1) + '" y="' + (f.y(0) - 12).toFixed(1) + '" text-anchor="middle">dry so far</text>';
      }
      s += dueMark(f, H, nextDue(S, 'pcp'), true) + f.post;
      return { H, s, f };
    }

    const DRAW = { temp: drawTemp, wind: drawWind, pcp: drawPcp };
    function drawCharts(S) {
      if (!(P.end > P.start)) return;
      Object.keys(DRAW).forEach(k => {
        const svg = C[k].svg, W = Math.max(300, Math.round((svg.parentNode && svg.parentNode.clientWidth) || 900));
        const r = DRAW[k](W, S);
        svg.setAttribute('viewBox', '0 0 ' + W + ' ' + r.H);
        svg.setAttribute('width', W); svg.setAttribute('height', r.H);
        // the shared axis, in pixels, for anyone checking the three line up
        svg.setAttribute('data-x0', r.f.x(P.start).toFixed(1));
        svg.setAttribute('data-x1', r.f.x(P.end).toFixed(1));
        // the hour the keyboard is on, as a band behind the marks
        const kb = focusHour[k] != null && P.hours[focusHour[k]] && document.activeElement === svg ? P.hours[focusHour[k]] : null;
        const band = kb ? (k === 'pcp' ? [r.f.x(kb.t), r.f.x(kb.end)] : [r.f.x(kb.t) - 6, r.f.x(kb.t) + 6]) : null;
        svg.innerHTML = (band ? '<rect class="kband" x="' + band[0].toFixed(1) + '" y="' + M.t + '" width="' + (band[1] - band[0]).toFixed(1) + '" height="' + (r.H - M.t - M.b) + '"/>' : '') + r.s;
        geo[k] = { W, H: r.H, f: r.f };
      });
    }

    /* ---- the hover (spec section 8): for each value on the chart, the
       value as stored, converted before rounding and rounded, for
       temperature the hourly contract's value and state, the day so far
       after that hour rounded and before rounding, and the file: when it was
       published on each server and first found there, or that no time is on
       record, the copy used, and whether the hour counts. Every figure is a
       string the lane wrote. */
    const kv = (k, v, cls) => (v == null || v === '' ? '' : '<span class="tk">' + esc(k) + '</span><span class="tv' + (cls ? ' ' + cls : '') + '">' + v + '</span>');
    const sec = rows => { const b = rows.filter(Boolean).join(''); return b ? '<div class="tg anatg">' + b + '</div>' : ''; };
    const muted = s => (s == null || s === '' ? '' : ' <span class="tm">' + esc(s) + '</span>');
    function fileRows(F, by, at, validMs, src, late, has, pubSrcOnly) {
      const tz = P.tz, rows = [];
      ['nodd', 'nomads'].forEach(s => {
        let v = F ? F[s] : null;
        // a file written before the records names only its first publication and its server
        if (!F && pubSrcOnly && by === s && at != null) v = { lm: isoZ(at) };
        const lm = v ? when(v.lm) : null, seen = v ? when(v.seen) : null;
        let txt;
        if (lm == null && seen == null) txt = s === 'nomads' && validMs < NOMADS_FROM ? 'not on record before 3 Nov 15:13 UTC' : 'not on record';
        else txt = [lm != null ? 'published ' + clockS(tz, lm) : '', seen != null ? 'found ' + clockS(tz, seen) : ''].filter(Boolean).join(', ');
        rows.push(kv(SRC[s], esc(txt)));
      });
      const word = counted(F, has, late);
      const used = has && SRC[src] ? SRC[src] : '';
      if (used) rows.push(kv('Copy used', esc([used, word].filter(Boolean).join(', '))));
      else if (word) rows.push(kv('For the day', esc(word)));
      return rows;
    }
    function tipFor(k, o) {
      const S = stateAt(P, T), tz = P.tz;
      const pc = k === 'pcp', F = pc ? o.pcpFile : o.anlFile;
      const title = pc ? hourOnly(tz, o.t) + ' to ' + hourZ(tz, o.end) : hourZ(tz, o.t) + (o.repeat ? ' (second)' : '');
      const subT = pc ? utc(o.t) + ' to ' + utc(o.end) + ' UTC' : utc(o.t) + ' UTC';
      let html = '<b>' + esc(title) + '</b><span class="tsub">' + esc(subT + (F && F.name ? ' · ' + F.name : '')) + '</span>';
      const inn = pc ? S.inP(o) : S.inA(o), lt = pc ? S.lateP(o) : S.lateA(o);
      const v = o.v || {}, u = (kk, unit) => (kk == null ? '' : esc(kk) + ' ' + unit);
      const ex = !P.running;             // a file without `rowShape` carries each hour cut, and no shown value
      const rounded = kk => (o.show && o.show[kk] != null ? '<b>' + esc(fx(o.show[kk], DEC[kk][0])) + ' ' + UNIT[kk][2] + '</b>' : '');
      // the converted value as the lane wrote it, or on an older file the row's
      // own value, cut, which is all that file carries
      const conv = kk => (o.x && o.x[kk] != null ? u(o.x[kk], UNIT[kk][2]) : v[kk] != null ? u(fx(v[kk], DEC[kk][1]), UNIT[kk][2]) + ' <span class="tm">cut</span>' : '');
      const valueRows = (kk, name) => [kv(name ? name + ' as stored' : 'As stored', o.raw && o.raw[kk] != null ? u(o.raw[kk], UNIT[kk][0]) : '', 'num'),
        kv('Converted', conv(kk), 'num'), kv('Rounded', rounded(kk) + (pc ? ' <span class="tm">for display only</span>' : ''))];
      const soFar = (name, r, x, unit, dec) => kv(name, r && r.value != null ? esc(fx(r.value, dec)) + ' ' + unit + muted(x != null ? x : (ex ? null : r.exact)) : '');
      let body = '';
      if (!inn && !lt) {
        const note = S.atCut ? 'Not published by the cut-off.' : 'Not published yet. Expected about ' + clock(tz, pc ? o.end + LAG.pcp.due : o.t + LAG.anl.due) + '.';
        body += '<div class="tf anagap">' + esc(note) + '</div>';
        if (k === 'temp' && o.hourly) body += sec([kv('Hourly contract', esc(hourlyText(o, T, tz)))]);
        if (F) body += sec(fileRows(F, null, null, pc ? o.end : o.t, null, false, false));
        return html + body;
      }
      if (k === 'temp') {
        const hrs = o.hourly ? hourlyAt(o, T) : null;
        const hv = o.hourly && o.hourly.value != null && hrs !== 'review' ? esc(o.hourly.value) + ' °F, ' : '';
        body += sec(valueRows('temp').concat([o.hourly ? kv('Hourly contract', hv + esc(hourlyText(o, T, tz))) : '']));
        const r = o.run || {}, x = o.runX || {};
        body += sec([soFar('High so far', r.high, x.high, '°F', 0), soFar('Low so far', r.low, x.low, '°F', 0)]);
      } else if (k === 'wind') {
        body += sec(valueRows('wind', 'Wind'));
        body += sec(valueRows('gust', 'Gust'));
        const r = o.run || {}, x = o.runX || {};
        body += sec([soFar('Mean so far', r.wind, x.wind, 'mph', 0), soFar('Peak so far', r.gust, x.gust, 'mph', 0)]);
      } else {
        body += sec(valueRows('precip'));
        const rp = o.run && o.run.precip;
        body += sec([soFar('Total so far', rp, (o.runX || {}).precip, 'in', 2), kv('Hours in it', rp && rp.hours != null ? esc(rp.hours) : '')]);
      }
      const has = true, src = (F && F.src) || (pc ? v.pcpSrc : v.src);
      body += sec(fileRows(F, pc ? o.pcpBy : o.anlBy, pc ? o.pcpAt : o.anlAt, pc ? o.end : o.t, src, pc ? o.pcpLate : o.anlLate, has, true));
      return html + body;
    }
    // the hour under the pointer: the nearest (temperature, wind) or the hour band (precipitation)
    function hourAtX(k, px) {
      const g = geo[k];
      if (!g || !P || !P.hours.length || px < M.l - 10 || px > g.W - M.r + 10) return null;
      const t = P.start + (px - M.l) / (g.W - M.l - M.r) * (P.end - P.start);
      return k === 'pcp' ? P.hours.find(x => t >= x.t && t < x.end) || null
        : P.hours.reduce((b, x) => (Math.abs(x.t - t) < Math.abs(b.t - t) ? x : b), P.hours[0]);
    }
    Object.keys(C).forEach(k => {
      const svg = C[k].svg;
      const at = (ev, pin) => {
        const g = geo[k];
        if (!g) return;
        const box = svg.getBoundingClientRect(), px = (ev.clientX - box.left) * g.W / box.width;
        const o = hourAtX(k, px);
        if (!o) { if (!pin) tip.hide(); return; }
        if (pin) tip.pin(ev, tipFor(k, o)); else tip.show(ev, tipFor(k, o));
      };
      // a mouse shows the hour it is over; a click, or a tap on a phone, pins
      // the box so it stays to be read until a tap elsewhere or Escape
      svg.addEventListener('pointermove', ev => { if (ev.pointerType !== 'touch') at(ev, false); });
      svg.addEventListener('pointerdown', ev => at(ev, true));
      svg.addEventListener('pointerleave', ev => { if (ev.pointerType !== 'touch') tip.hide(); });
      // the keyboard steps through the hours, and the box follows
      const showKey = () => {
        const o = P && P.hours[focusHour[k]], g = geo[k];
        if (!o || !g) return;
        drawCharts(stateAt(P, T));
        const box = svg.getBoundingClientRect(), sc = box.width / g.W;
        const x = g.f.x(k === 'pcp' ? o.t + HOUR / 2 : o.t) * sc + box.left;
        tip.show({ clientX: x, clientY: box.top + Math.min(box.height / 2, 60) }, tipFor(k, o));
      };
      svg.addEventListener('keydown', ev => {
        if (!P || !P.hours.length) return;
        const n = P.hours.length, i = focusHour[k];
        let j = null;
        if (ev.key === 'ArrowRight') j = i == null ? 0 : Math.min(n - 1, i + 1);
        else if (ev.key === 'ArrowLeft') j = i == null ? n - 1 : Math.max(0, i - 1);
        else if (ev.key === 'Home') j = 0;
        else if (ev.key === 'End') j = n - 1;
        else if (ev.key === 'Escape') { tip.hide(); return; }
        if (j == null) return;
        ev.preventDefault();
        focusHour[k] = j;
        showKey();
      });
      svg.addEventListener('blur', () => { tip.hide(); if (focusHour[k] != null) { focusHour[k] = null; if (P) drawCharts(stateAt(P, T)); } });
    });

    /* ---- a CSV file for each value, for the whole place-day whatever the
       replay shows: one row per hour, with the file's times on both servers,
       the value as stored, converted and rounded, whether the hour counts and
       the day so far, as the place file writes them. Assembled here from the
       lane's strings and saved through a Blob link. */
    const csvCell = v => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const srv = (F, s, f) => (F && F[s] && F[s][f] ? F[s][f] : '');
    function csvFor(kind) {
      const pl = ctx.place || {}, tz = P.tz, pc = kind === 'precip';
      const head = CSV_HEAD.concat(CSV_TAIL[kind]);
      const lines = [head];
      P.hours.forEach(o => {
        const F = pc ? o.pcpFile : o.anlFile, v = o.v || {};
        const has = pc ? o.pcp : v[kind] != null;
        const src = (F && F.src) || (pc ? v.pcpSrc : v.src) || '';
        const r = [pl.code || '', pl.name ? pl.name + ', ' + pl.state : pl.id || '', (P.doc && P.doc.day) || '', tz,
          localStamp(tz, pc ? o.end : o.t), pc ? isoZ(o.end) : o.iso, (F && F.name) || '', has && SRC[src] ? SRC[src] : '',
          srv(F, 'nodd', 'seen'), srv(F, 'nodd', 'lm'), srv(F, 'nomads', 'seen'), srv(F, 'nomads', 'lm'),
          has && o.raw ? str(o.raw[kind]) : '', UNIT[kind][0], has && o.x ? str(o.x[kind]) : '', UNIT[kind][1],
          has && o.show && o.show[kind] != null ? fx(o.show[kind], DEC[kind][0]) : ''];
        const word = counted(F, has, pc ? o.pcpLate : o.anlLate);
        r.push(word in CSV_COUNT ? CSV_COUNT[word] : word);
        const run = has ? o.run || {} : {}, x = has ? o.runX || {} : {};
        const so = (a, b, dec) => [str(x[a]), run[b] && run[b].value != null ? fx(run[b].value, dec) : ''];
        if (kind === 'temp') {
          const hr = o.hourly;
          const fin = hr && hr.state === 'final' ? hr.at || (hr.basis === 'norecord' ? hr.cut : '') || '' : '';
          r.push(hr ? hourlyText(o, Infinity, tz, "'") : '', fin);
          r.push(...so('high', 'high', 0), ...so('low', 'low', 0));
        } else if (kind === 'wind') r.push(...so('wind', 'wind', 0));
        else if (kind === 'gust') r.push(...so('gust', 'gust', 0));
        else r.push(...so('precip', 'precip', 2));
        lines.push(r);
      });
      const name = ((P.doc && P.doc.day) || '') + ' ' + (pl.code || pl.id || '') + ' ' + CSV_NAME[kind] + '.csv';
      return { name, text: lines.map(l => l.map(csvCell).join(',')).join('\n') + '\n' };
    }
    function save(kind) {
      if (!P || !P.hours.length) return;
      const { name, text } = csvFor(kind);
      const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
      const a = h('a', { href: url, download: name, hidden: '' });
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    // ---- every hour
    function renderTable(S) {
      const tz = P.tz, run = P.running;
      const hi = run ? S.runA && S.runA.high : (P.e || {}).high, lo = run ? S.runA && S.runA.low : (P.e || {}).low;
      const pk = run ? S.runA && S.runA.gust : (P.e || {}).gust;
      const oh = hi && hourAt(hi.at), ol = lo && hourAt(lo.at), op = pk && hourAt(pk.at);
      const ex = !run;
      const cell = (o, k, isIn, isLate, cls) => {
        if (!(isIn || isLate)) return '<td class="num' + (cls || '') + '">·</td>';
        return '<td class="num' + (cls || '') + (isLate ? ' dim late' : '') + '"' + (isLate ? ' title="published after the cut-off, not counted"' : '') + '>'
          + fx(ex ? o.v[k] : shown(o, k), DEC[k][ex ? 1 : 0]) + '</td>';
      };
      const arrived = (at, by) => (at == null ? 'not on record' : clock(tz, at) + (SRC[by] ? ' on ' + SRC[by] : ''));
      let body = '';
      P.hours.forEach(o => {
        const a = S.inA(o), la = S.lateA(o), p = S.inP(o), lp = S.lateP(o);
        let arr;
        if (a) arr = arrived(o.anlAt, o.anlBy);
        else if (la) arr = arrived(o.anlAt, o.anlBy) + ', after the cut-off';
        else arr = S.atCut ? 'not published' : 'due ' + clock(tz, o.t + LAG.anl.due);
        const rp = p && o.run && o.run.precip;
        const total = rp ? fx(rp.value, 2) : '·';
        // the hourly contract once its hour has begun, as it stood at T
        const hw = o.hourly && T >= o.t ? hourlyWord(o, T) : '';
        const hcell = hw ? '<td class="hst" data-state="' + esc(hourlyAt(o, T)) + '" title="' + esc(hourlyText(o, T, tz)) + '">' + esc(hw) + '</td>' : '<td class="hst"></td>';
        body += '<tr class="' + (a || la ? '' : 'pending') + '" data-t="' + esc(new Date(o.t).toISOString()) + '"><td>' + esc(hourZ(tz, o.t)) + (o.repeat ? ' (second)' : '') + '</td><td>' + esc(utc(o.t)) + '</td>'
          + cell(o, 'temp', a, la, o === oh && a ? ' hi' : o === ol && a ? ' lo' : '') + hcell
          + cell(o, 'wind', a, la) + cell(o, 'gust', a, la, o === op && a ? ' pk' : '')
          + cell(o, 'precip', p, lp) + '<td class="num ptotal">' + (run ? total : '·') + '</td>'
          + '<td class="arr">' + esc(arr) + '</td></tr>';
      });
      table.innerHTML = '<thead><tr><th>Local time</th><th>UTC</th><th class="num">Temp °F</th><th>Hourly contract</th><th class="num">Wind mph</th><th class="num">Gust mph</th>'
        + '<th class="num">Precip that hour, in</th><th class="num">Precip total, in</th><th>Analysis published</th></tr></thead><tbody>' + body + '</tbody>';
      const anyLate = P.hours.some(o => S.lateA(o) || S.lateP(o));
      foot.textContent = (run
        ? 'Values are shown the way the contracts round them, temperature to a whole degree, wind and gust to a whole mile per hour and precipitation to a hundredth of an inch. '
          + 'Point at an hour on a chart, or tap it, for the value as stored, converted before rounding and rounded. '
          + 'The hourly amounts are rounded for display, so they can add up to a slightly different figure than the total. The running total is summed from NOAA’s own numbers before rounding and rounded once, and it is the column the contract resolves on. '
        : 'This file carries each hour as stored, temperature, wind and gust to a thousandth and precipitation to a ten-thousandth of an inch, cut rather than rounded, and no running total. ')
        + 'Each row is the ' + NAME + ' analysis at the top of the hour in ' + tz + ' and the precipitation over the hour that starts then, which NOAA files under the hour it ends. '
        + 'The hourly contract is each hour’s own value, final once NOMADS has the file or at its own cut-off three hours after the hour. '
        + 'Each publication time is the server’s own, the earlier of the two, and a time not on record is said to be.'
        + (anyLate ? ' A dimmed value was published after the cut-off and is not counted.' : '');
    }

    function destroy() {
      stop();
      clearInterval(ticker);
      window.removeEventListener('resize', onResize);
      tip.hide();
      key = null; P = null;
    }

    return { node, show, destroy, chartOf: k => document.getElementById(CHART[k] || 'anaTemp'),
             state: () => (P ? { T, live, timed: P.timed, end: P.replayEnd } : null), csv: k => (P ? csvFor(k) : null) };
  }

  return { create, CHART, LAG, CHASE, inWindow, _model: model, _stateAt: stateAt, _bounds: bounds, _lateAt: lateAt, _hourlyAt: hourlyAt, zone };
})();
