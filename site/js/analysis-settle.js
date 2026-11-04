/* The settlement display of the analysis resolution page (approved by the
   owner on 2026-11-03): one place-day as it resolves on NOAA's RTMA, hour by
   hour, above the map.

   It shows the day's state (provisional, final, event review), when the next
   analysis and the next hour's precipitation are due, the five values a
   contract would resolve on, three charts of the hours on one time axis, and
   every hour in a table. A slider replays the day as of any minute, at
   NOAA's own publication times where the files carry them (from 14 October)
   and at times estimated from the usual lags, marked ≈, before then. While
   the day is live it follows the clock, and inside the minutes the files
   usually land, or while a countdown reads late, it fetches the place file
   again, so the day fills in without a reload.

   The page adds nothing up. Every figure is the lane's: each hour's value as
   the contract shows it (`show`), its exact value (the row's own fields, cut
   to a thousandth, precipitation to a ten-thousandth of an inch) and the
   day's values so far (`run`), which pipeline/analysis.py resolves with the
   code that resolves the day. Rounded hours do not add up to the day (Boston
   on 2026-09-28, 0.83 in from its rounded hours against the 0.84 it resolved
   on), so the page never adds, averages or rounds a value; the only sums it
   does are of times and of pixels. A place file written before 2026-11-03
   carries none of these fields (it has no `rowShape`), and the display draws
   it as the file stands, with no running figures and no replay. */
window.WXSettle = (() => {
  const { h } = WXC;
  const MIN = 60000, HOUR = 3600000;
  /* When the files land, from NOAA's own publication times over the two
     weeks to 2026-11-03: an analysis a median 49 minutes after its hour and
     usually by 52, an hour's precipitation a median 17 minutes after the hour
     ends and usually by 18. The countdowns run to these, and a day before 14
     October, when no publication time was kept, replays at them, marked ≈. */
  const LAG = { anl: { due: 49 * MIN, by: 52 * MIN }, pcp: { due: 17 * MIN, by: 18 * MIN } };
  // the draft terms' cut-off, 12:45 AM local on the day after (clocks never
  // change between midnight and then in the lane's zones), and how far past
  // it a final day's replay runs
  const CUTOFF = 45 * MIN, AFTER = 15 * MIN;
  // the minutes past each hour inside which a live day's file is fetched
  // again every minute: about when the analysis lands (from :44 to :54) and
  // when the hour's precipitation does (from :14 to :20)
  const inWindow = m => (m >= 44 && m <= 54) || (m >= 14 && m <= 20);
  // a countdown past its usual time keeps a live day fetched each minute for
  // this long, so a file the lane reads late, on its ten-minute pass outside
  // those windows, reaches the page a minute or two after the read and late
  // ends when the file is in; past it the windows fetch the day twice an hour
  const CHASE = 30 * MIN;
  const SRC = { nodd: 'NOAA Open Data', nomads: 'NOMADS' };
  // the precision each hourly value is shown at, as the contract rounds it and exactly
  const DEC = { temp: [0, 3], wind: [0, 3], gust: [0, 3], precip: [2, 4] };
  // the chart each daily variable is read on, for a link's var=
  const CHART = { high: 'anaTemp', low: 'anaTemp', gust: 'anaWind', wind: 'anaWind', precip: 'anaPcp' };
  const CARDS = [
    { k: 'high', label: 'High', unit: '°F', dec: 0, xdec: 3, kind: 'at' },
    { k: 'low', label: 'Low', unit: '°F', dec: 0, xdec: 3, kind: 'at' },
    { k: 'gust', label: 'Peak gust', unit: ' mph', dec: 0, xdec: 3, kind: 'at' },
    { k: 'wind', label: 'Mean wind', unit: ' mph', dec: 0, xdec: 3, kind: 'mean' },
    { k: 'precip', label: 'Precipitation', unit: ' in', dec: 2, xdec: 4, kind: 'total' },
  ];

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // a value the lane has already rounded or cut, written at its own precision;
  // toFixed only pads it, since the value already sits at that precision
  const fx = (v, d) => (v == null ? '·' : Number(v).toFixed(d));
  const pad2 = n => String(n).padStart(2, '0');

  // ---- times in the place's own zone, never by a fixed offset
  const fmts = {};
  const fmt = (tz, o) => { const k = tz + JSON.stringify(o); return fmts[k] || (fmts[k] = new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: tz }, o))); };
  const clock = (tz, ms) => fmt(tz, { hour: 'numeric', minute: '2-digit' }).format(ms);
  const clockZ = (tz, ms) => fmt(tz, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(ms);
  const hourZ = (tz, ms) => fmt(tz, { hour: 'numeric', timeZoneName: 'short' }).format(ms);
  const hourOnly = (tz, ms) => fmt(tz, { hour: 'numeric' }).format(ms);
  const zone = (tz, ms) => ((fmt(tz, { hour: 'numeric', timeZoneName: 'short' }).formatToParts(ms).find(p => p.type === 'timeZoneName')) || {}).value || '';
  const localHour = (tz, ms) => Number(fmt(tz, { hour: 'numeric', hourCycle: 'h23' }).format(ms)) % 24;
  const utc = ms => fmt('UTC', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(ms);
  const dayName = (tz, ms) => fmt(tz, { weekday: 'long', month: 'long', day: 'numeric' }).format(ms);
  const span = ms => {
    const s = Math.max(0, Math.ceil(ms / 1000)), hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
    return hh ? hh + ':' + pad2(mm) + ':' + pad2(s % 60) : pad2(mm) + ':' + pad2(s % 60);
  };

  /* The place-day as the display reads it. Each hour knows whether its
     analysis and its precipitation were read, whether either file came after
     the cut-off (`late`, shown and never counted), when each arrived (the
     file's publication time, or the usual lag, estimated), and the lane's
     shown and running values. A counted hour enters the day's values once
     every counted hour before it has arrived (`anlIn`, `pcpIn`): the running
     values of an hour cover every counted hour up to it, so an hour that
     arrived ahead of an earlier one waits for it rather than show a total the
     arrived hours do not make. Files arrive in order, so this is almost
     always the hour's own time. */
  function model(doc, entry, product, tz) {
    const rows = (doc && Array.isArray(doc.hours)) ? doc.hours : [];
    const running = !!(doc && doc.rowShape >= 2);
    const hours = rows.map((r, i) => {
      const t = Date.parse(r.t), v = r[product] || null;
      const o = { i, t, end: t + HOUR, local: r.local, repeat: /\*$/.test(String(r.local || '')), v };
      o.anl = !!(v && (v.temp != null || v.wind != null || v.gust != null));
      o.pcp = !!(v && v.precip != null);
      o.anlLate = !!(v && v.late);
      o.pcpLate = !!(v && v.pcpLate);
      o.anlEst = !(v && v.pub);
      o.pcpEst = !(v && v.pcpPub);
      o.anlAt = v && v.pub ? Date.parse(v.pub) : t + LAG.anl.due;
      o.pcpAt = v && v.pcpPub ? Date.parse(v.pcpPub) : o.end + LAG.pcp.due;
      o.show = running && v ? v.show || null : null;
      o.run = running && v ? v.run || null : null;
      return o;
    });
    let a = -Infinity, p = -Infinity;
    hours.forEach(o => {
      if (o.anl && !o.anlLate) { a = Math.max(a, o.anlAt); o.anlIn = a; }
      if (o.pcp && !o.pcpLate) { p = Math.max(p, o.pcpAt); o.pcpIn = p; }
    });
    const e = (entry && entry[product]) || null;
    const start = hours.length ? hours[0].t : NaN, end = hours.length ? hours[hours.length - 1].end : NaN;
    return { doc, tz: (doc && doc.tz) || tz || 'UTC', running, hours, start, end, cutoff: end + CUTOFF, e,
             final: !!(e && e.final), review: !!(e && e.review), pReview: !!(e && e.precip && e.precip.review),
             closed: !!(e && e.closed),
             estimated: hours.some(o => (o.anl && o.anlEst) || (o.pcp && o.pcpEst)) };
  }

  // the span the slider covers: a live day runs to the clock, a day past its
  // cut-off to a quarter hour after it; a file with no running figures is
  // shown as it stands, at the end of that span
  function bounds(P, now) {
    const past = !(now < P.cutoff + AFTER);
    return { min: P.start, max: past ? P.cutoff + AFTER : Math.max(P.start, now), live: !past };
  }

  // the day as of T: which hours are in, and the lane's running values at the
  // last counted hour in, for the analysis and for the precipitation
  function stateAt(P, T) {
    const inA = o => o.anl && !o.anlLate && o.anlIn <= T;
    const inP = o => o.pcp && !o.pcpLate && o.pcpIn <= T;
    let lastA = null, lastP = null, nA = 0, nP = 0;
    P.hours.forEach(o => { if (inA(o)) { lastA = o; nA++; } if (inP(o)) { lastP = o; nP++; } });
    const atCut = T >= P.cutoff;
    return { T, inA, inP, lastA, lastP, nA, nP, runA: lastA && lastA.run, runP: lastP && lastP.run,
             atCut, final: P.final && atCut, of: P.hours.length,
             // a late file is shown, dimmed, once the cut-off it missed has passed
             lateA: o => o.anl && o.anlLate && atCut, lateP: o => o.pcp && o.pcpLate && atCut };
  }

  // whether a countdown of the day reads late at `now` and has for no more
  // than CHASE, the same test as the countdowns' own late state
  function lateAt(P, now) {
    const S = stateAt(P, now);
    if (S.atCut) return false;
    const a = P.hours.find(o => !S.inA(o)), r = P.hours.find(o => !S.inP(o));
    const late = by => now > by && now <= by + CHASE;
    return !!((a && late(a.t + LAG.anl.by)) || (r && late(r.end + LAG.pcp.by)));
  }

  function create(opts) {
    const PRODUCT = opts.product || 'rtma', NAME = opts.productName || 'RTMA';
    const tip = WXC.tooltip();
    let P = null, key = null, T = 0, live = true, exact = false, playing = null, lastMin = -1, fetching = false;
    let ctx = {};

    // ---- the furniture, built once and kept, so a replay holds its time
    //      across redraws of the same place-day
    const node = h('div', { id: 'anaSettle' });
    const status = h('div', { class: 'anastatus', id: 'anaStatus' });
    const clockBox = h('div', { class: 'anaclock', id: 'anaClock' });
    const when = h('div', { class: 'anawhen', id: 'anaWhen' });
    const range = h('input', { type: 'range', id: 'anaT', 'aria-label': 'The time the day is shown as of' });
    const playB = h('button', { type: 'button', class: 'primary', id: 'anaPlayDay', text: 'Play the day' });
    const latestB = h('button', { type: 'button', id: 'anaLatest', text: 'Latest' });
    const exactBox = h('input', { type: 'checkbox', id: 'anaExact' });
    const est = h('span', { class: 'anaest', id: 'anaEst', text: 'Times marked ≈ are estimated from the usual lags, since NOAA’s own publication times are kept from 14 October.' });
    const next = h('div', { class: 'ananext', id: 'anaNext' });
    const old = h('p', { class: 'cap anaold', id: 'anaOld', text: 'This day’s file was written before the hour by hour figures, so the day is shown as the file stands, with no running figures and no replay.' });
    clockBox.appendChild(h('div', { class: 'anaclkrow' }, [when, range]));
    clockBox.appendChild(h('div', { class: 'anabtns' }, [playB, latestB,
      h('label', { class: 'anatoggle', for: 'anaExact' }, [exactBox, document.createTextNode(' Show exact values')]), est]));
    clockBox.appendChild(next);
    clockBox.appendChild(old);
    const cards = h('div', { class: 'anacards', id: 'anaCards' });
    const chart = (id, title, legend, cap) => {
      const svg = WXC.el('svg', { class: 'anasvg', role: 'img', 'aria-label': title + ' through the day' });
      const sec = h('section', { class: 'anachart', id }, [
        h('h3', {}, [document.createTextNode(title + ' '), h('span', { class: 'analeg', html: legend })]),
        h('p', { class: 'cap', text: cap }), h('div', { class: 'anaplot' }, [svg])]);
      return { sec, svg };
    };
    const C = {
      temp: chart('anaTemp', 'Temperature', '<span><i style="background:var(--ana-rtma)"></i>Each hour, °F</span>',
        'Each dot is one hour’s analysis, rounded to a whole degree, which is the value an hourly contract for that hour resolves on. The day’s high and low are the highest and lowest dots.'),
      wind: chart('anaWind', 'Wind speed and gust', '<span><i style="background:var(--accent)"></i>Sustained wind</span><span><i style="background:var(--vbar)"></i>Gust</span>',
        'Each hour’s analysed wind and gust at 10 m, rounded to a whole mile per hour. The peak gust is the highest gust of the day. The dashed line is the mean of the sustained winds so far, taken before rounding.'),
      pcp: chart('anaPcp', 'Precipitation', '<span><i style="background:var(--cool)"></i>Total so far</span><span><i style="background:var(--ana-rain-soft);height:8px"></i>Each hour</span>',
        'The line is the day’s total so far to a hundredth of an inch, the value the precipitation contract resolves on at the end of the day. Each bar is one hour’s accumulation, which NOAA publishes about 17 minutes after the hour ends.'),
    };
    const table = h('table', { class: 'anahours', id: 'anaHours' });
    const foot = h('p', { class: 'cap anafoot', id: 'anaFoot' });
    const hoursSec = h('section', { class: 'anahrs' }, [h('h3', { text: 'Every hour' }), h('div', { class: 'anatab' }, [table]), foot]);
    [status, clockBox, cards, C.temp.sec, C.wind.sec, C.pcp.sec, hoursSec].forEach(e => node.appendChild(e));

    const B = () => bounds(P, Date.now());
    function stop() {
      if (playing) { clearInterval(playing); playing = null; }
      playB.textContent = 'Play the day';
    }
    playB.onclick = () => {
      if (!P || !P.running) return;
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
    exactBox.addEventListener('change', () => { exact = exactBox.checked; render(); });
    let rz = null;
    const onResize = () => { clearTimeout(rz); rz = setTimeout(() => { if (P && !node.hidden) drawCharts(stateAt(P, T)); }, 120); };
    window.addEventListener('resize', onResize);

    /* Once a second: a live day follows the clock, the countdowns tick, and
       everything else is redrawn when the minute turns. Inside the arrival
       windows, and while a countdown reads late, a live day's file is fetched
       again each minute. */
    const ticker = setInterval(() => {
      if (!P || !node.isConnected || node.closest('[hidden]')) return;
      const now = Date.now(), b = bounds(P, now);
      if (live) T = b.max;
      const m = Math.floor(now / MIN);
      if (m !== lastMin) {
        lastMin = m;
        render();
        if (b.live && !P.final && opts.refetch && !fetching && !document.hidden
            && (inWindow(new Date(now).getUTCMinutes()) || lateAt(P, now))) {
          const want = key;
          fetching = true;
          Promise.resolve(opts.refetch()).then(r => {
            if (r && r.doc && want === key) load(r.doc, r.entry, true);
          }).catch(() => { /* the next minute tries again */ }).then(() => { fetching = false; });
        }
      } else if (live && P.running) renderTop(stateAt(P, T));
    }, 1000);

    function load(doc, entry, keep) {
      P = model(doc, entry, PRODUCT, ctx.tz);
      // the file was just read, so the next fetch waits for the next minute
      lastMin = Math.floor(Date.now() / MIN);
      const b = B();
      if (!keep || live || !P.running) { T = b.max; live = b.live && P.running; } else T = Math.min(b.max, Math.max(b.min, T));
      render();
    }

    /* show({key, doc, entry, unread, varKey}): the place-day to draw. The same
       key keeps the replay's time; a new one opens on the latest. */
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
      clockBox.hidden = !hasRows;
      [C.temp.sec, C.wind.sec, C.pcp.sec, hoursSec].forEach(e => { e.hidden = !hasRows; });
      renderCards(S);
      if (!hasRows) return;
      const b = B();
      range.min = String(b.min); range.max = String(b.max); range.step = String(MIN); range.value = String(T);
      // a file with no running figures has nothing to replay
      range.hidden = playB.hidden = latestB.hidden = when.hidden = !P.running;
      old.hidden = P.running;
      est.hidden = !P.estimated;
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
      else if (P.review && (S.atCut || !P.running)) { cls = 'review'; word = 'Event review'; text = 'No ' + NAME + ' hour was published by the 12:45 AM cut-off, so the day goes to event review.'; }
      else if (P.closed) { cls = 'closed'; word = 'Closed'; text = 'Closed on ' + n + ' of ' + of + ' hours, under the rule before 14 October.'; }
      else if (P.running ? S.final : P.final) { cls = 'final'; word = 'Final'; text = 'Resolved at ' + cutAt + ', on ' + n + ' of ' + of + ' hours.'; }
      else if (P.running && S.atCut) { cls = 'prov'; word = 'Provisional'; text = n + ' of ' + of + ' hours were in by ' + cutAt + '. The day turns final when the site next writes it.'; }
      else if (!n) { cls = 'prov'; word = 'Provisional'; text = 'No hour of this day is in yet. The day becomes final at ' + cutAt + '.'; }
      else { cls = 'prov'; word = 'Provisional'; text = n + ' of ' + of + ' hours are in. The day becomes final at ' + cutAt + '.'; }
      status.innerHTML = '<span class="anapill ' + cls + '">' + esc(word) + '</span><span class="anastxt">' + esc(text) + '</span>';
    }

    // ---- the clock and the two countdowns
    function renderTop(S) {
      const tz = P.tz;
      when.innerHTML = '<b>' + esc(clockZ(tz, T)) + '</b><span>' + esc(utc(T)) + ' UTC · ' + esc(dayName(tz, T)) + '</span>';
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

    // ---- the five values
    function renderCards(S) {
      const tz = P.tz, e = P.e || {};
      const so = (P.running ? S.final : P.final) ? '' : ' so far';
      cards.textContent = '';
      CARDS.forEach(c => {
        const card = h('div', { class: 'anacard' + (c.k === ctx.varKey ? ' on' : ''), 'data-var': c.k, title: 'show this variable on the map' });
        card.appendChild(h('div', { class: 'al', text: c.label }));
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
          card.appendChild(h('div', { class: 'av dim', text: 'no value' }));
          card.appendChild(h('div', { class: 'ae', text: why }));
        } else {
          card.appendChild(h('div', { class: 'av', text: fx(val.value, c.dec) + c.unit }));
          const d = c.kind === 'at' ? (val.at ? 'at ' + hourZ(tz, Date.parse(val.at)) : '')
            : (c.kind === 'mean' ? 'mean of ' : 'total of ') + n + (n === 1 ? ' hour' : ' hours');
          card.appendChild(h('div', { class: 'ae', text: d + so }));
          if (exact && val.exact != null) card.appendChild(h('div', { class: 'ae anaexact', text: 'exact ' + fx(val.exact, c.xdec) + c.unit }));
        }
        card.onclick = () => { if (opts.onVar) opts.onVar(c.k); };
        cards.appendChild(card);
      });
    }

    // ---- the three charts on one time axis
    const M = { l: 46, r: 18, t: 22, b: 30 };
    const nice = (rng, steps) => steps.find(s => rng / s <= 6) || steps[steps.length - 1];
    const path = pts => pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('');
    let geo = {};
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
      return { x, y, g, pre, post };
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
        svg.innerHTML = r.s;
        geo[k] = { W, H: r.H, f: r.f };
      });
    }

    // hover: the nearest hour (temperature, wind) or the hour band (precipitation)
    function tipFor(k, o) {
      const S = stateAt(P, T), tz = P.tz;
      const title = k === 'pcp' ? hourOnly(tz, o.t) + ' to ' + hourZ(tz, o.end) + ' · ' + utc(o.t) + ' to ' + utc(o.end) + ' UTC'
        : hourZ(tz, o.t) + ' · ' + utc(o.t) + ' UTC';
      const pairs = [];
      let footer;
      const arrived = (at, isEst, src) => (isEst ? 'Arrived about ' : 'Published ') + clock(tz, at) + (src && SRC[src] ? ' on ' + SRC[src] : '') + (isEst ? ', estimated' : '');
      if (k !== 'pcp') {
        const inn = S.inA(o), lt = S.lateA(o);
        if (!inn && !lt) {
          footer = S.atCut ? 'Not published by the cut-off.' : 'Not published yet. Expected about ' + clock(tz, o.t + LAG.anl.due) + '.';
        } else {
          const one = (name, key) => { pairs.push([name, fx(shown(o, key), P.running ? DEC[key][0] : DEC[key][1]) + (key === 'temp' ? '°F' : ' mph')]);
            if (exact && P.running && o.v[key] != null) pairs.push(['Exact', fx(o.v[key], DEC[key][1]) + (key === 'temp' ? '°F' : ' mph')]); };
          if (k === 'temp') one('Temperature', 'temp'); else { one('Wind', 'wind'); one('Gust', 'gust'); }
          footer = lt ? 'Published ' + clock(tz, o.anlAt) + ', after the cut-off, and not counted.' : arrived(o.anlAt, o.anlEst, o.v.src);
        }
      } else {
        const inn = S.inP(o), lt = S.lateP(o);
        if (!inn && !lt) {
          footer = S.atCut ? 'Not published by the cut-off.' : 'Not published yet. Expected about ' + clock(tz, o.end + LAG.pcp.due) + '.';
        } else {
          pairs.push(['That hour', fx(shown(o, 'precip'), P.running ? 2 : 4) + ' in']);
          if (exact && P.running) pairs.push(['Exact', fx(o.v.precip, 4) + ' in']);
          const rp = o.run && o.run.precip;
          if (!lt && rp) pairs.push(['Total by ' + hourOnly(tz, o.end), fx(exact ? rp.exact : rp.value, exact ? 4 : 2) + ' in']);
          footer = lt ? 'Published ' + clock(tz, o.pcpAt) + ', after the cut-off, and not counted.' : arrived(o.pcpAt, o.pcpEst, o.v.pcpSrc);
        }
      }
      return tip.rows(title, pairs, footer);
    }
    Object.keys(C).forEach(k => {
      const svg = C[k].svg;
      const move = ev => {
        const g = geo[k];
        if (!g || !P || !P.hours.length) return;
        const box = svg.getBoundingClientRect(), px = (ev.clientX - box.left) * g.W / box.width;
        if (px < M.l - 10 || px > g.W - M.r + 10) { tip.hide(); return; }
        const t = P.start + (px - M.l) / (g.W - M.l - M.r) * (P.end - P.start);
        const o = k === 'pcp' ? P.hours.find(x => t >= x.t && t < x.end)
          : P.hours.reduce((b, x) => (Math.abs(x.t - t) < Math.abs(b.t - t) ? x : b), P.hours[0]);
        if (!o) { tip.hide(); return; }
        tip.show(ev, tipFor(k, o));
      };
      svg.addEventListener('pointermove', move);
      svg.addEventListener('pointerdown', move);
      svg.addEventListener('pointerleave', () => tip.hide());
    });

    // ---- every hour
    function renderTable(S) {
      const tz = P.tz, run = P.running;
      const hi = run ? S.runA && S.runA.high : (P.e || {}).high, lo = run ? S.runA && S.runA.low : (P.e || {}).low;
      const pk = run ? S.runA && S.runA.gust : (P.e || {}).gust;
      const oh = hi && hourAt(hi.at), ol = lo && hourAt(lo.at), op = pk && hourAt(pk.at);
      const ex = exact || !run;
      const cell = (o, k, isIn, isLate, cls) => {
        if (!(isIn || isLate)) return '<td class="num' + (cls || '') + '">·</td>';
        const v = ex ? o.v[k] : shown(o, k);
        return '<td class="num' + (cls || '') + (isLate ? ' dim late' : '') + '"' + (isLate ? ' title="published after the cut-off, not counted"' : '') + '>'
          + fx(v, ex ? DEC[k][1] : DEC[k][0]) + '</td>';
      };
      let body = '';
      P.hours.forEach(o => {
        const a = S.inA(o), la = S.lateA(o), p = S.inP(o), lp = S.lateP(o);
        let arr;
        if (a) arr = (o.anlEst ? '≈' : '') + clock(tz, o.anlAt);
        else if (la) arr = clock(tz, o.anlAt) + ', after the cut-off';
        else arr = S.atCut ? 'not published' : 'due ' + clock(tz, o.t + LAG.anl.due);
        const rp = p && o.run && o.run.precip;
        const total = rp ? fx(ex ? rp.exact : rp.value, ex ? 4 : 2) : '·';
        body += '<tr class="' + (a || la ? '' : 'pending') + '" data-t="' + esc(new Date(o.t).toISOString()) + '"><td>' + esc(hourZ(tz, o.t)) + (o.repeat ? ' (second)' : '') + '</td><td>' + esc(utc(o.t)) + '</td>'
          + cell(o, 'temp', a, la, o === oh && a ? ' hi' : o === ol && a ? ' lo' : '')
          + cell(o, 'wind', a, la) + cell(o, 'gust', a, la, o === op && a ? ' pk' : '')
          + cell(o, 'precip', p, lp) + '<td class="num ptotal">' + (run ? total : '·') + '</td>'
          + '<td class="arr">' + esc(arr) + '</td></tr>';
      });
      table.innerHTML = '<thead><tr><th>Local time</th><th>UTC</th><th class="num">Temp °F</th><th class="num">Wind mph</th><th class="num">Gust mph</th>'
        + '<th class="num">Precip that hour, in</th><th class="num">Precip total, in</th><th>Analysis published</th></tr></thead><tbody>' + body + '</tbody>';
      const anyLate = P.hours.some(o => S.lateA(o) || S.lateP(o));
      foot.textContent = (run
        ? 'Values are shown the way the contracts round them, temperature to a whole degree, wind and gust to a whole mile per hour and precipitation to a hundredth of an inch. Show exact values gives each to a thousandth, precipitation to a ten-thousandth of an inch, cut rather than rounded. '
          + 'The hourly amounts are rounded for display, so they can add up to a slightly different figure than the total. The running total is summed from NOAA’s own numbers before rounding and rounded once, and it is the column the contract resolves on. '
        : 'This file carries each hour as stored, temperature, wind and gust to a thousandth and precipitation to a ten-thousandth of an inch, cut rather than rounded, and no running total. ')
        + 'Each row is the ' + NAME + ' analysis at the top of the hour in ' + tz + ' and the precipitation over the hour that starts then, which NOAA files under the hour it ends.'
        + (anyLate ? ' A dimmed value was published after the cut-off and is not counted.' : '');
    }

    function destroy() {
      stop();
      clearInterval(ticker);
      window.removeEventListener('resize', onResize);
      tip.hide();
      key = null; P = null;
    }

    return { node, show, destroy, chartOf: k => document.getElementById(CHART[k] || 'anaTemp'), state: () => (P ? { T, live, exact } : null) };
  }

  return { create, CHART, LAG, CHASE, inWindow, _model: model, _stateAt: stateAt, _bounds: bounds, _lateAt: lateAt, zone };
})();
