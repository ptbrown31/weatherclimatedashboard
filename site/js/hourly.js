/* The hourly temperature area, a thin index over the city pages.

   The station is the unit a reader works in, so this page lists the cities the
   exchange has opened and draws each one's hours through the shared renderer.
   The same panel appears on the city page under its own tab. */
window.WXHourly = (() => {
  const { h, $ } = WXC;
  const SLUG = 'hourly-temperatures';
  const EXC = { YHC: 'CYVR', FPO: 'LFPG' };
  const cityOf = p => String(p.category || p.name || '').replace(/ Hourly Temperature$/, '').trim();

  function stationOf(prod, roster) {
    const code = String(prod.id || '').slice(-3);
    const byCode = roster.find(c => c.station === EXC[code] || String(c.station || '').slice(1) === code);
    const byCity = roster.find(c => c.city === cityOf(prod));
    if (byCode && byCity && byCode.station !== byCity.station) return null;
    return byCode || byCity || null;
  }

  async function init() {
    WXC.chrome('hourly-temperature-markets.html');
    const root = $('#board');
    await WXVarMap.init('hourly');
    const loads = [];
    let cat = null, roster = [];
    try {
      const rc = await WXD.get('catalogue/' + SLUG + '.json', 1440);
      const rs = await WXD.get('summary.json');
      loads.push(rs);
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
    const listed = cat.products.filter(p => p.state === 'listed');
    const pending = cat.products.filter(p => p.state !== 'listed');
    say(listed.length + ' of ' + cat.products.length + ' cities listed');

    for (const p of listed) {
      const c = stationOf(p, roster);
      const sec = h('section', { class: 'prod ctr' }, [h('h2', {},
        [h('a', { href: WXC.cityHref ? WXC.cityHref(c || {}) : 'city.html?station=' + (c || {}).station }, p.name)])]);
      root.appendChild(sec);
      if (!c) { sec.appendChild(h('p', { class: 'cap' }, 'No station on this site matches ' + p.id + '.')); continue; }
      let board = null, obs = null, fc = null;
      try {
        const ro = await WXD.get('obs/' + c.station + '.json', 10);
        const rf = await WXD.get('forecast/' + c.station + '.json', 30);
        loads.push(ro, rf);
        obs = ro.data; fc = rf.data;
        await WXM.load(c.station);
        board = WXM.hourly(c);
      } catch (e) { /* the empty state below */ }
      say(listed.length + ' of ' + cat.products.length + ' cities listed');
      if (board && board.listed) WXK.hours(sec, c, board, obs, fc);
      else sec.appendChild(h('p', { class: 'cap' }, WXM.live() ? 'No hours on the board.' : 'Live quotes are off in this build.'));
    }
    if (pending.length) {
      root.appendChild(h('p', { class: 'cap', style: 'margin-top:14px' },
        'Not opened at ' + pending.map(cityOf).sort().join(', ') + '.'));
    }
  }
  return { init };
})();
