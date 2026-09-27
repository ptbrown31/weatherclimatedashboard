/* The wind area, a thin index over the city pages.

   Each city's peak so far today, the gust still forecast, and the ladder when
   the exchange opens one. The same panel appears on the city page under its own
   tab. Nothing here is dimmed for being decided: a wind contract trades to
   11:59 PM local whatever the day has already done. */
window.WXWind = (() => {
  const { h, $ } = WXC;
  const SLUG = 'wind';
  const EXC = { YHC: 'CYVR', FPO: 'LFPG' };
  const cityOf = p => String(p.category || p.name || '').replace(/ (Max|Average) Wind Speed$/, '').trim();

  function stationOf(prod, roster) {
    const code = String(prod.id || '').slice(-3);
    const byCode = roster.find(c => c.station === EXC[code] || String(c.station || '').slice(1) === code);
    const byCity = roster.find(c => c.city === cityOf(prod));
    if (byCode && byCity && byCode.station !== byCity.station) return null;
    return byCode || byCity || null;
  }

  async function init() {
    WXC.chrome('wind-markets.html');
    const root = $('#board');
    /* Clicking a dot scrolls to that station's panel rather than leaving the
       page. Every listed station already has a chart further down, so the thing
       the reader asked to see is on screen; navigating away would show them the
       same panel on a page of its own. A station with no panel here, which is
       any the board does not list, still opens its own page. */
    const vm = await WXVarMap.init('wind', {
      onPick: c => {
        const el = document.getElementById('wind-' + c.station);
        if (!el) return false;
        /* Smooth where the browser will animate it, and landed either way.

           A smooth scroll is silently a no-op in some environments, which
           leaves the reader looking at the map they just clicked with nothing
           apparently changed. The position is checked a moment later and set
           outright if the animation never happened, so the panel is reached
           whatever the browser does with the request. */
        const top = el.getBoundingClientRect().top + window.scrollY - 12;
        const still = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        try { window.scrollTo({ top, behavior: still ? 'smooth' : 'auto' }); }
        catch (e) { window.scrollTo(0, top); }
        /* Re-measured, not remembered. The charts below lay out while the scroll
           is in flight, so the position taken before it started is off by the
           time it lands, and it landed far enough past to push the station's
           own heading above the fold. */
        setTimeout(() => {
          const want = el.getBoundingClientRect().top + window.scrollY - 12;
          if (Math.abs(window.scrollY - want) > 8) window.scrollTo(0, want);
        }, 450);
        el.classList.add('pick');
        setTimeout(() => el.classList.remove('pick'), 1600);
        return true;
      },
    });
    const loads = [];
    let cat = null, roster = [];
    try {
      const rc = await WXD.get('catalogue/' + SLUG + '.json', 1440);
      const rs = await WXD.get('summary.json');
      loads.push(rs);
      // the map is drawn from the quote summary, so its age belongs here too
      const rm = WXM.summaryLoad && WXM.summaryLoad();
      if (rm) loads.push(rm);
      cat = rc.data;
      roster = (rs.data || {}).cities || [];
    } catch (e) { /* the empty state below */ }
    const say = txt => {
      const bar = $('#pageStatus');
      if (!bar) return;
      bar.textContent = '';
      /* The contract listing is deliberately NOT in this strip.

         It is rebuilt once a day, the live feeds every ten minutes, and the
         strip reports one age against one cadence. Mixing them made the page
         say "6 hours ago, updates every 10 minutes" about a file that updates
         daily, while the market data behind it was seven minutes old. A stale
         listing has its own, louder failure below: the page says the listing is
         unavailable and draws nothing. */
      bar.appendChild(WXC.statusEl(loads.length ? loads : [{ source: 'none', stale: true, asof: null, ageMin: null }], 10));
      if (txt) bar.appendChild(h('span', { class: 'cap', style: 'margin-left:8px' }, txt));
    };
    say('');
    if (!cat || !(cat.products || []).length) {
      root.appendChild(h('p', { class: 'cap' }, 'The contract listing is not available right now.'));
      return;
    }
    const maxes = cat.products.filter(p => String(p.id).startsWith('MG'));
    const others = cat.products.filter(p => !String(p.id).startsWith('MG'));

    /* Read each station's weather once, not once per day.

       The observations and the forecast do not change when the reader switches
       day, so they are fetched here and kept. The MARKET snapshot deliberately
       is not: WXM holds one station at a time (`S.station`/`S.snap`), so
       loading all nine up front leaves only the last one in hand and every
       other station reads as having no board. It is loaded next to the draw
       that needs it, which is what the single-day version of this page did. */
    const seen = [];
    for (const p of maxes) {
      const c = stationOf(p, roster);
      if (!c) { seen.push({ p, c: null }); continue; }
      let obs = null, fc = null;
      try {
        const ro = await WXD.get('obs/' + c.station + '.json', 10);
        const rf = await WXD.get('forecast/' + c.station + '.json', 30);
        loads.push(ro, rf);
        obs = ro.data; fc = rf.data;
      } catch (e) { /* the station draws what it can */ }
      seen.push({ p, c, obs, fc });
    }

    /* The exchange opens tomorrow's wind board during today, and until now this
       page showed only the day under way. The day-ahead board is the one still
       worth a decision, so it gets the same Today/Tomorrow control the daily
       temperature board has, and the same control moves the map above: one day
       for the whole page rather than a map and a list that can disagree. */
    let day = 'today';
    const bar = h('div', { class: 'bar', id: 'windDays' });
    const btn = (id, label) => {
      const b = h('button', { text: label });
      b.dataset.day = id;
      b.onclick = () => { if (day !== id) { day = id; if (vm) vm.setDay(id); render(); } };
      bar.appendChild(b);
      return b;
    };
    btn('today', 'Today');
    btn('tomorrow', 'Tomorrow');
    /* The third day is named, not called "day after".

       It is the first day the exchange has normally not opened, so it is the
       one a reader comes to for an estimate rather than a price, and a weekday
       is how anyone refers to it out loud. The date comes from the roster's
       own marker, read at noon UTC so the name cannot slip a day either side. */
    const after = (roster.find(c => c.markers && c.markers.dayAfter) || {}).markers;
    if (after && after.dayAfter) {
      btn('dayafter', WXC.weekdayOf(after.dayAfter) || 'Day after');
    }
    const dayCap = h('span', { class: 'cap', style: 'margin-left:8px' });
    bar.appendChild(dayCap);
    root.appendChild(bar);
    const list = h('div');
    root.appendChild(list);

    let drawing = 0;
    async function render() {
      const mine = ++drawing;
      Array.from(bar.querySelectorAll('button')).forEach(b => b.classList.toggle('on', b.dataset.day === day));
      list.textContent = '';
      let open = 0, boards = 0;
      for (const { p, c, obs, fc } of seen) {
        // a second click while the first draw is still loading must not
        // interleave two days' sections into one list
        if (mine !== drawing) return;
        const sec = h('section', { class: 'prod ctr', id: 'wind-' + c.station }, [h('h2', {},
          c ? [h('a', { href: WXC.cityHref ? WXC.cityHref(c) : 'city.html?station=' + c.station }, p.name)] : p.name)]);
        list.appendChild(sec);
        if (!c) { sec.appendChild(h('p', { class: 'cap' }, 'No station on this site matches ' + p.id + '.')); continue; }
        let board = null;
        try { await WXM.load(c.station); board = WXM.windLadder(c, day); } catch (e) { /* drawn without a ladder */ }
        if (mine !== drawing) return;
        if (board && board.listed) { open++; }
        boards++;
        if (board && board.day) dayCap.textContent = '\u00b7 ' + board.day;
        WXK.wind(sec, c, obs, fc, board);
      }
      const dayWord = day === 'today' ? 'today' : day === 'tomorrow' ? 'tomorrow'
        : (bar.querySelector('button[data-day="dayafter"]') || {}).textContent || 'that day';
      say(open + ' of ' + (boards || maxes.length) + ' listed for ' + dayWord);
      // one note for the board, not one per city
      if (maxes.length) list.appendChild(WXK.windNote());
      if (others.length) {
        list.appendChild(h('p', { class: 'cap', style: 'margin-top:14px' },
          'Average wind speed contracts at ' + others.map(cityOf).sort().join(', ') + ' settle on the day\u2019s average.'));
      }
    }
    render();
  }
  return { init };
})();
