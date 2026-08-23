/* The forecast scorecard: how each source did against what happened.
   Data in: scorecard.json (daily). Error is forecast minus observed; a
   positive bias runs warm. Hover detail comes from one mousemove listener
   per table (delegated to the cell) with the row key and source in data
   attributes, so the day tables stay cheap to build. */
window.WXScore = (() => {
  const { h, $, deg } = WXC;
  const NAME = { nws: 'NWS', nbm: 'NBM', mav: 'GFS MOS', lamp: 'LAMP' };
  const ORDER = ['nws', 'nbm', 'mav', 'lamp'];
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const HOT = 'rgba(30,90,160,.08)';   // the accent, faint: the hovered row
  let S = null, cur = null, tip = null;

  const fmt = v => (v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1));
  const pct = v => (v == null ? '—' : Math.round(v * 100) + '%');
  const signed = v => (v == null ? '—' : (v > 0 ? '+' : '') + fmt(v));
  const degs = v => (v == null ? '—' : fmt(v) + '°');
  // bias with its lean in words; the rounded value decides, so "+0.0" never reads warm
  function biasWord(v) {
    if (v == null) return '—';
    const r = Math.round(v * 10) / 10;
    return signed(v) + '°' + (r > 0 ? ' · runs warm' : r < 0 ? ' · runs cool' : '');
  }
  const statRows = (st, prefix = '') => (st ? [
    [prefix + 'n', String(st.n)], [prefix + 'MAE', degs(st.mae)], [prefix + 'bias', biasWord(st.bias)],
    prefix ? null : ['within 1°', pct(st.within1)], [prefix + 'within 2°', pct(st.within2)]] : [[prefix + 'n', '0']]);
  // cycle ids arrive as 20260821T225639Z, 20260821T2030Z or 2026-08-21T23:00:00Z
  function fmtCycle(c) {
    const m = /^(\d{4})-?(\d{2})-?(\d{2})T(\d{2}):?(\d{2})/.exec(c || '');
    return m ? MON[+m[2] - 1] + ' ' + (+m[3]) + ' ' + m[4] + ':' + m[5] + 'Z' : (c || null);
  }
  const src = s => (S.sources && S.sources[s]) || '';

  function cell(stat, s) {
    const a = { class: 'num', 'data-src': s };
    if (!stat) return [0, 1, 2, 3].map(() => h('td', Object.assign({ text: '—' }, a)));
    return [h('td', Object.assign({ text: String(stat.n) }, a)), h('td', Object.assign({ text: fmt(stat.mae) }, a)),
            h('td', Object.assign({ text: signed(stat.bias) }, a)), h('td', Object.assign({ text: pct(stat.within2) }, a))];
  }
  // one listener per table: the cell's data-src and the row's data-key pick the content
  function bindTips(t, tipFor, pinOnClick) {
    let hot = null;
    const target = e => { const td = e.target.closest('td'); const tr = td && td.parentNode; return td && tr && tr.dataset.key != null ? [tr, td] : null; };
    t.addEventListener('mousemove', e => {
      const hit = target(e);
      if (hot && (!hit || hot !== hit[0])) { hot.style.background = ''; hot = null; }
      if (!hit) { tip.hide(); return; }
      if (hot !== hit[0]) { hot = hit[0]; hot.style.background = HOT; }
      const html = tipFor(hit[0].dataset.key, hit[1].dataset.src || '');
      if (html) tip.show(e, html); else tip.hide();
    });
    t.addEventListener('mouseleave', () => { if (hot) { hot.style.background = ''; hot = null; } tip.hide(); });
    if (pinOnClick) {
      t.setAttribute('data-tip-pin', '1');
      t.addEventListener('click', e => { const hit = target(e); if (!hit) return; const html = tipFor(hit[0].dataset.key, hit[1].dataset.src || ''); if (html) tip.pin(e, html); });
    }
  }
  function table(title, rows, tipFor, pinOnClick) {
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: title }), ...ORDER.flatMap(s => [h('th', { class: 'num', text: NAME[s] + ' n' }), h('th', { class: 'num', text: 'MAE' }), h('th', { class: 'num', text: 'bias' }), h('th', { class: 'num', text: '≤2°' })])]));
    rows.forEach(([label, bySource, onclick, key]) => {
      const tr = h('tr', { 'data-key': key }, [h('td', { text: label }), ...ORDER.flatMap(s => cell(bySource[s], s))]);
      if (onclick) { tr.style.cursor = 'pointer'; tr.onclick = onclick; }
      t.appendChild(tr);
    });
    if (tipFor) bindTips(t, tipFor, pinOnClick);
    return t;
  }

  function drawOverall() {
    const host = $('#overall'); host.innerHTML = '';
    const hi = {}, lo = {};
    ORDER.forEach(s => { const o = S.overall[s] || {}; hi[s] = o.high; lo[s] = o.low; });
    const tipFor = (side, s) => {
      if (!NAME[s]) return null;
      const st = (S.overall[s] || {})[side];
      return tip.rows(NAME[s] + ' — daily ' + side + ', all stations', statRows(st), src(s));
    };
    host.appendChild(table('All stations', [['Daily high', hi, null, 'high'], ['Daily low', lo, null, 'low']], tipFor, true));
  }
  function drawStations() {
    const host = $('#stations'); host.innerHTML = '';
    const rows = Object.entries(S.stations).sort((a, b) => a[1].city.localeCompare(b[1].city)).map(([sid, st]) => {
      const hi = {}; ORDER.forEach(s => { hi[s] = (st.summary[s] || {}).high; });
      return [st.city + ' (' + sid + ')', hi, () => { cur = sid; drawDays(); location.hash = sid; }, sid];
    });
    const tipFor = (sid, s) => {
      const st = S.stations[sid]; if (!st || !NAME[s]) return null;
      const sm = st.summary[s] || {};
      return tip.rows(st.city + ' (' + sid + ') — ' + NAME[s] + ' daily high', [...statRows(sm.high), ...statRows(sm.low, 'daily low ')],
        'click → the station\'s scored days');
    };
    host.appendChild(table('Daily high, by station', rows, tipFor, false));   // the click selects; no pin
  }
  function drawDays() {
    const host = $('#days'); host.innerHTML = '';
    const st = S.stations[cur]; if (!st) return;
    host.appendChild(h('div', { class: 'secttl', text: st.city.toUpperCase() + ' · the last ' + st.days.length + ' scored days (' + st.unit + ')' }));
    const t = h('table');
    t.appendChild(h('tr', {}, [h('th', { text: 'Day' }), h('th', { class: 'num', text: 'Observed high' }), h('th', { class: 'num', text: 'low' }),
      ...ORDER.flatMap(s => [h('th', { class: 'num', text: NAME[s] + ' high' }), h('th', { class: 'num', text: 'err' }), h('th', { class: 'num', text: 'low' }), h('th', { class: 'num', text: 'err' })])]));
    st.days.forEach((d, i) => {
      t.appendChild(h('tr', { 'data-key': String(i) }, [h('td', { text: d.date }), h('td', { class: 'num', text: fmt(d.obsHigh) }), h('td', { class: 'num', text: fmt(d.obsLow) }),
        ...ORDER.flatMap(s => { const f = d[s] || {}; const a = { class: 'num', 'data-src': s };
          return [h('td', Object.assign({ text: fmt(f.high) }, a)), h('td', Object.assign({ text: signed(f.errHigh) }, a)), h('td', Object.assign({ text: fmt(f.low) }, a)), h('td', Object.assign({ text: signed(f.errLow) }, a))]; })]));
    });
    const tipFor = (i, s) => {
      const d = st.days[+i]; if (!d) return null;
      if (NAME[s]) {
        const f = d[s];
        if (!f) return tip.rows(st.city + ' — ' + d.date + ' — ' + NAME[s], [['Observed high', deg(d.obsHigh)], ['Observed low', deg(d.obsLow)], ['METARs scored', d.n]], 'no ' + NAME[s] + ' forecast archived for this day');
        return tip.rows(st.city + ' — ' + d.date + ' — ' + NAME[s], [
          ['Forecast high / observed / error', deg(f.high) + ' / ' + deg(d.obsHigh) + ' / ' + signed(f.errHigh)],
          ['Forecast low / observed / error', deg(f.low) + ' / ' + deg(d.obsLow) + ' / ' + signed(f.errLow)],
          ['Cycle', fmtCycle(f.cycle)], ['Lead', f.lead != null ? f.lead.toFixed(1) + ' h to local midnight' : null],
          ['METARs scored', d.n]], src(s));
      }
      // the day itself: the observed extremes and which source came closest on the high
      // (a comparison of the published errors in the row, nothing modelled)
      const scored = ORDER.filter(k => d[k] && d[k].errHigh != null);
      let best = null;
      if (scored.length) {
        const m = Math.min(...scored.map(k => Math.abs(d[k].errHigh)));
        best = scored.filter(k => Math.abs(d[k].errHigh) === m).map(k => NAME[k]).join(' / ') + ' (' + signed(d[scored.find(k => Math.abs(d[k].errHigh) === m)].errHigh) + ')';
      }
      return tip.rows(st.city + ' — ' + d.date, [['Observed high', deg(d.obsHigh)], ['Observed low', deg(d.obsLow)], ['METARs scored', d.n], ['Closest on the high', best]],
        scored.length ? 'error = forecast minus observed' : 'no forecasts scored for this day');
    };
    bindTips(t, tipFor, true);
    host.appendChild(t);
    host.appendChild(h('p', { class: 'cap', text: 'Forecasts are the cycle each source issued before local midnight; lead is hours from issuance to midnight. Error is forecast minus observed. Hover a cell for the cycle and lead; click to pin.' }));
  }

  async function init() {
    tip = WXC.tooltip();
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
