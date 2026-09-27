/* One station's wind, and nothing else.

   Nine stations carry no daily temperature contract: six shore stations that
   hold no ForecastEx product at all, and three whose only contract is MG
   wind. The city page is built around a daily high and low market that none
   of them has, so a reader who clicked one on the wind map would land on a
   page whose chart, pickers, scorecard and "every scored day" all belong to a
   market that is not there. This page is what they get instead: the wind
   panel, the note under it, and the way back to the board.

   It is the same panel the wind area and the city page's wind tab draw, from
   the same renderer, so a station that gains a product code later reads the
   same here as it does there. pipeline/cities.py names the set and
   scripts/build.py chooses this template over city.html for it.

   Data in: summary.json for the station's row, obs and forecast snapshots for
   the two wind columns and the guidance line, and the market snapshot for the
   ladder, which for these stations is an explicit unlisted one. */
window.WXWindStation = (() => {
  const { h, $ } = WXC;

  async function init() {
    WXC.chrome('wind-markets.html');
    const station = WXC.param('station') || window.WX_STATION;
    const host = $('#windPanel');
    if (!host || !station) return;

    const bar = $('#pageStatus');
    const loads = [];
    let city = null, obs = null, fc = null, wind = null, windTmw = null, windAfter = null;
    try {
      const rs = await WXD.get('summary.json');
      city = ((rs.data || {}).cities || []).find(c => c.station === station);
      const ro = await WXD.get('obs/' + station + '.json', 10);
      const rf = await WXD.get('forecast/' + station + '.json', 30);
      loads.push(rs, ro, rf);
      obs = ro.data; fc = rf.data;
      if (city) {
        await WXM.load(station);
        // the exchange opens tomorrow's wind board during today, so both are
        // read and the page shows whichever of them exists
        wind = WXM.windLadder(city);
        windTmw = WXM.windLadder(city, 'tomorrow');
        windAfter = WXM.windLadder(city, 'dayafter');
      }
    } catch (e) { /* the status strip below says so, and the frame still draws */ }

    if (bar) {
      bar.textContent = '';
      bar.appendChild(WXC.statusEl(loads.length ? loads
        : [{ source: 'none', stale: true, asof: null, ageMin: null }], 10));
    }
    if (!city) {
      host.appendChild(h('p', { class: 'cap' }, 'This station is not on the roster right now.'));
      return;
    }

    /* Every listed day, not only the one under way, and the day heading appears
       only when there is more than one to tell apart. The same rule the city
       page's wind tab follows, so the two never disagree about a day. */
    /* The days as buttons, the way the daily temperature chart offers its own.

       They were stacked, which put three charts of the same shape down the page
       and made a reader scroll to compare two of them. One chart and a row of
       buttons is the pattern the rest of the site uses for a day selector. */
    const labelled = [[wind, 'Today'], [windTmw, 'Tomorrow'], [windAfter, null]];
    const days = labelled.filter(function (e) { return e[0] && (e[0].listed || e[0].anticipated); });
    const exSlot = $('#windExpand');
    const dayBar = h('span', { id: 'windDayBar' });
    if (exSlot && exSlot.parentNode) exSlot.parentNode.insertBefore(dayBar, exSlot);
    const note = WXK.windNote();

    function draw(i) {
      host.textContent = '';
      Array.from(dayBar.querySelectorAll('button')).forEach(function (b, j) { b.classList.toggle('on', j === i); });
      WXK.wind(host, city, obs, fc, days.length ? days[i][0] : wind);
      // the two columns are the same two whatever day is drawn, so one note for
      // the page rather than one under each chart
      host.appendChild(note);
      const card = host.querySelector('.card');
      if (card && exSlot) {
        card.classList.add('expandable');
        exSlot.textContent = '';
        exSlot.appendChild(WXC.expander(card, 'Expand'));
      }
    }
    days.forEach(function (e, i) {
      const w = e[0];
      const b = h('button', { text: e[1] || WXC.weekdayOf(w.day) || 'Later' });
      b.dataset.day = w.day;
      b.onclick = function () { draw(i); };
      dayBar.appendChild(b);
    });
    draw(0);

    const links = $('#windLinks');
    if (links) {
      links.textContent = '';
      links.appendChild(h('a', { href: 'wind-markets.html' }, 'Contract details and every listed station \u2192'));
    }

    // the expander is rebuilt by draw(), because the card it opens is replaced
    // every time the day changes
  }
  return { init };
})();
