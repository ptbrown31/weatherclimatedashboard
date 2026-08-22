/* The forecast scorecard: how each source did against what happened.
   Data in: scorecard.json (daily). Error is forecast minus observed; a
   positive bias runs warm. */
window.WXScore = (() => {
  const { h, $, deg } = WXC;
  const NAME = { nws: 'NWS', nbm: 'NBM', mav: 'GFS MOS', lamp: 'LAMP' };
  const ORDER = ['nws', 'nbm', 'mav', 'lamp'];
  let S = null, cur = null;

  const fmt = v => (v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1));
  const pct = v => (v == null ? '—' : Math.round(v * 100) + '%');
  const signed = v => (v == null ? '—' : (v > 0 ? '+' : '') + fmt(v));

  function cell(stat) {
    if (!stat) return [h('td', { class: 'num', text: '—' }), h('td', { class: 'num', text: '—' }), h('td', { class: 'num', text: '—' }), h('td', { class: 'num', text: '—' })];
    return [h('td', { class: 'num', text: String(stat.n) }), h('td', { class: 'num', text: fmt(stat.mae) }),
            h('td', { class: 'num', text: signed(stat.bias) }), h('td', { class: 'num', text: pct(stat.within2) })];
  }
  function table(title, rows) {
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: title }), ...ORDER.flatMap(s => [h('th', { class: 'num', text: NAME[s] + ' n' }), h('th', { class: 'num', text: 'MAE' }), h('th', { class: 'num', text: 'bias' }), h('th', { class: 'num', text: '≤2°' })])]));
    rows.forEach(([label, bySource, onclick]) => {
      const tr = h('tr', {}, [h('td', { text: label }), ...ORDER.flatMap(s => cell(bySource[s]))]);
      if (onclick) { tr.style.cursor = 'pointer'; tr.onclick = onclick; }
      t.appendChild(tr);
    });
    return t;
  }

  function drawOverall() {
    const host = $('#overall'); host.innerHTML = '';
    const hi = {}, lo = {};
    ORDER.forEach(s => { const o = S.overall[s] || {}; hi[s] = o.high; lo[s] = o.low; });
    host.appendChild(table('All stations', [['Daily high', hi], ['Daily low', lo]]));
  }
  function drawStations() {
    const host = $('#stations'); host.innerHTML = '';
    const rows = Object.entries(S.stations).sort((a, b) => a[1].city.localeCompare(b[1].city)).map(([sid, st]) => {
      const hi = {}; ORDER.forEach(s => { hi[s] = (st.summary[s] || {}).high; });
      return [st.city + ' (' + sid + ')', hi, () => { cur = sid; drawDays(); location.hash = sid; }];
    });
    host.appendChild(table('Daily high, by station', rows));
  }
  function drawDays() {
    const host = $('#days'); host.innerHTML = '';
    const st = S.stations[cur]; if (!st) return;
    host.appendChild(h('div', { class: 'secttl', text: st.city.toUpperCase() + ' · the last ' + st.days.length + ' scored days (' + st.unit + ')' }));
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: 'Day' }), h('th', { class: 'num', text: 'Observed high' }), h('th', { class: 'num', text: 'low' }),
      ...ORDER.flatMap(s => [h('th', { class: 'num', text: NAME[s] + ' high' }), h('th', { class: 'num', text: 'err' }), h('th', { class: 'num', text: 'low' }), h('th', { class: 'num', text: 'err' })])]));
    st.days.forEach(d => {
      t.appendChild(h('tr', {}, [h('td', { text: d.date }), h('td', { class: 'num', text: fmt(d.obsHigh) }), h('td', { class: 'num', text: fmt(d.obsLow) }),
        ...ORDER.flatMap(s => { const f = d[s] || {}; return [h('td', { class: 'num', text: fmt(f.high) }), h('td', { class: 'num', text: signed(f.errHigh) }), h('td', { class: 'num', text: fmt(f.low) }), h('td', { class: 'num', text: signed(f.errLow) })]; })]));
    });
    host.appendChild(t);
    host.appendChild(h('p', { class: 'cap', text: 'Forecasts are the cycle each source issued before local midnight; lead is hours from issuance to midnight. Error is forecast minus observed.' }));
  }

  async function init() {
    const r = await WXD.get('scorecard.json', 1440);
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r], 1440));
    S = r.data;
    if (!S || !S.stations) { $('#overall').textContent = 'No scorecard available yet.'; return; }
    $('#since').textContent = 'Scored from ' + S.firstDay + ' (the day the archive started). ' + S.method + '.';
    drawOverall(); drawStations();
    cur = (location.hash || '').slice(1);
    if (!S.stations[cur]) cur = Object.keys(S.stations)[0];
    drawDays();
  }
  return { init };
})();
