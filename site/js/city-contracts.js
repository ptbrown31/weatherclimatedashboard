/* The other contracts at one station, stacked inside the city page.

   A reader arrives at a station, so the variables sit inside it. The daily high
   and low are the chart above; this section carries the wind and then the
   hourly temperature, each drawn by the shared renderer the area pages use.

   They used to be tabs. Stacking them shows both at once, which is what a
   reader comparing them wanted, and it puts the hourly board where the owner
   asked for it: under the wind series rather than behind it. Wind comes first
   because it is the one a station can have without a temperature market; the
   hourly board only exists on the daily temperature stations.

   A section appears only where the station has something to show under it,
   either a listed market or a reading of its own. */
window.WXCityContracts = (() => {
  const { h, $ } = WXC;

  async function init(opts) {
    const station = (opts && opts.station) || WXC.param('station') || window.WX_STATION;
    const host = $('#cityContracts');
    if (!host || !station) return;
    let city = null, obs = null, fc = null, hourly = null;
    let wind = null, windTmw = null, windAfter = null;
    const loads = [];
    try {
      const rs = await WXD.get('summary.json');
      city = ((rs.data || {}).cities || []).find(c => c.station === station);
      if (!city) return;
      const ro = await WXD.get('obs/' + station + '.json', 10);
      const rf = await WXD.get('forecast/' + station + '.json', 30);
      loads.push(rs, ro, rf);
      obs = ro.data; fc = rf.data;
      await WXM.load(station);
      hourly = WXM.hourly(city);
      // the exchange lists the wind contract a day ahead as well, and the day
      // after that is where an estimate stands in, so all three are read
      wind = WXM.windLadder(city);
      windTmw = WXM.windLadder(city, 'tomorrow');
      windAfter = WXM.windLadder(city, 'dayafter');
    } catch (e) { return; }

    const hasHourly = !!(hourly && hourly.listed);
    const hasWind = !!((wind && wind.listed) || (windTmw && windTmw.listed)
                       || (windAfter && windAfter.anticipated)
                       || WXK.peak(obs) || WXK.gustAhead(fc, city.tz));
    if (!hasHourly && !hasWind) return;

    host.appendChild(h('div', { class: 'secttl', style: 'margin-top:22px' }, 'OTHER CONTRACTS AT THIS STATION'));
    const strip = h('span', { id: 'ctrStatus' });
    const bar = h('div', { class: 'bar' }, [strip]);
    host.appendChild(bar);
    strip.appendChild(WXC.statusEl(loads, 10));

    /* One section per family, each with its own heading, panel and way through
       to the contract terms. The expander goes on the first chart in the
       section, which is the one a reader opens out. */
    function section(title, id, more, fill) {
      const wrap = h('div', { id });
      wrap.appendChild(h('div', { class: 'secttl', style: 'margin-top:18px' }, title));
      const exSlot = h('span');
      wrap.appendChild(h('div', { class: 'bar' }, [exSlot]));
      const panel = h('div', { class: 'ctr' });
      wrap.appendChild(panel);
      fill(panel);
      panel.appendChild(h('p', { class: 'cap' }, [h('a', { href: more }, 'Contract details →')]));
      const card = panel.querySelector('.card');
      if (card) {
        card.classList.add('expandable');
        exSlot.appendChild(WXC.expander(card, 'Expand'));
      }
      host.appendChild(wrap);
    }

    if (hasWind) windSection();

    /* The wind days as buttons, the way the daily temperature chart above
       offers its own days.

       They were stacked panels, which put three charts of the same shape down
       the page and made a reader scroll to compare two of them. One chart and a
       row of buttons is the pattern this page already uses a few inches higher,
       so the two sections now read the same way. */
    function windSection() {
      const labelled = [[wind, 'Today'], [windTmw, 'Tomorrow'], [windAfter, null]];
      const days = labelled.filter(function (e) { return e[0] && (e[0].listed || e[0].anticipated); });
      const wrap = h('div', { id: 'cityWind' });
      wrap.appendChild(h('div', { class: 'secttl', style: 'margin-top:18px' }, 'WIND'));
      const bar = h('div', { class: 'bar', id: 'cityWindDays' });
      const exSlot = h('span');
      const panel = h('div', { class: 'ctr' });
      const note = WXK.windNote();
      wrap.appendChild(bar); wrap.appendChild(panel);
      host.appendChild(wrap);

      function draw(i) {
        panel.textContent = '';
        Array.from(bar.querySelectorAll('button')).forEach(function (b, j) { b.classList.toggle('on', j === i); });
        WXK.wind(panel, city, obs, fc, days.length ? days[i][0] : wind);
        // the two columns are the same two whatever day is drawn, so the note
        // sits under the panel rather than inside the chart that changes
        panel.appendChild(note);
        panel.appendChild(h('p', { class: 'cap' }, [h('a', { href: 'wind-markets.html' }, 'Contract details \u2192')]));
        const card = panel.querySelector('.card');
        exSlot.textContent = '';
        if (card) {
          card.classList.add('expandable');
          exSlot.appendChild(WXC.expander(card, 'Expand'));
        }
      }
      days.forEach(function (e, i) {
        const w = e[0];
        const label = e[1] || WXC.weekdayOf(w.day) || 'Later';
        const b = h('button', { text: label });
        b.dataset.day = w.day;
        b.onclick = function () { draw(i); };
        bar.appendChild(b);
      });
      bar.appendChild(exSlot);
      draw(0);
    }
    if (hasHourly) {
      section('HOURLY TEMPERATURE', 'cityHourly', 'hourly-temperature-markets.html',
              panel => { WXK.hours(panel, city, hourly, obs, fc); });
    }
  }
  return { init };
})();
