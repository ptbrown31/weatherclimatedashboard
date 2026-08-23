/* Hurricane tracking: NHC forecast tracks, cones, wind-speed probabilities
   and formation odds over the basin geography, the season's count, and,
   with the market layer on, the exchange's hurricane contracts at their
   quoted prices.

   Data in: hurricane.json (storms with NHC wind probabilities, outlook,
   season), market/hurricane.json through WXM (the quote job's hurricane
   group), reask.json (the vendor live-storm lane, usually off), and
   assets/hurricane-geo.json (countries, coastal states, the six counties
   the landfall contracts name, the nation coastline, and the 163 wind
   reference locations). The drawn storm position is labelled from the
   geometry's own point and advisory, because the GIS service can trail
   NHC's roster. Equirectangular fitted to the basin box with independent
   x/y scales so the panel fills its frame (a deliberate stretch). */
window.WXHur = (() => {
  const { el, txt, h, $, clockFull } = WXC;
  const BASINS = {
    AL: { name: 'Atlantic', box: [-101.0, 4.0, -40.0, 48.0], outlook: 'Atlantic' },
    EP: { name: 'East and Central Pacific', box: [-180.0, 0.0, -85.0, 40.0], outlook: 'Pacific' },
  };
  // the strike labels the landfall contract uses, where they differ from the geometry's names
  const REGION_ALIAS = { 'The Bahamas': 'Bahamas, The', 'Bahamas': 'Bahamas, The' };
  const COUNT = { TROPA: 'Atlantic named storms', HCAB: 'Atlantic hurricanes', MHCMA: 'Atlantic major hurricanes by month', HCAT4: 'Category 4 hurricane in the US', HLF: 'Hurricane landfall' };
  const RAMP = ['#fde8c8', '#f9cf94', '#f2ad5e', '#e68a2e', '#cf6a14', '#a84e0b', '#7a3607'];
  let H = null, GEO = null, NATION = null, MK = null, RK = null, basin = 'AL', tip = null;

  const cents = v => (v == null ? null : Math.round(v * 100));
  const local = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
  const pct = v => (v == null ? '—' : v + '¢');
  function ptColor(p) {
    if (p.kt != null && p.kt >= 96) return '#c0392b';
    if (p.type === 'HU') return '#e08a1e';
    if (p.type === 'TS' || p.type === 'STS') return '#2b7bba';
    return '#7fa6c6';
  }
  function ramp(p) {
    if (p == null) return 'var(--map-land)';
    const t = Math.max(0, Math.min(1, p)) * (RAMP.length - 1), i = Math.floor(t), f = t - i;
    if (f < 1e-6 || i >= RAMP.length - 1) return RAMP[Math.min(i, RAMP.length - 1)];
    const hx = s => [1, 3, 5].map(k => parseInt(s.slice(k, k + 2), 16));
    const A = hx(RAMP[i]), B = hx(RAMP[i + 1]);
    return 'rgb(' + A.map((a, k) => Math.round(a + (B[k] - a) * f)).join(',') + ')';
  }
  function rings(coords) {
    if (!coords || !coords.length) return [];
    if (typeof coords[0][0] === 'number') return [coords];
    if (typeof coords[0][0][0] === 'number') return coords;
    return coords.flat();
  }

  // ---- the exchange's hurricane group, by symbol
  const market = sym => (MK ? MK.markets.find(m => m.symbol === sym) : null);
  const yes = c => (c && c.mid != null ? cents(c.mid) : null);
  const quoteText = c => (!c ? 'not listed' : (c.mid == null ? 'no book' : 'Yes ' + pct(cents(c.mid)) + ' (bid ' + pct(cents(c.bid)) + ' · ask ' + pct(cents(c.ask)) + (c.from === 'no' ? ', from the No book' : '') + ')'));
  function landfallQuotes() {
    const m = market('HLF'); if (!m) return null;
    const out = {};
    m.contracts.forEach(c => { out[c.label] = c; });
    return out;
  }
  const regionKey = label => REGION_ALIAS[label] || label;

  // ---- the map
  function drawNhc(svg, X, Y, b) {
    const outl = (H.outlook || []).filter(o => (o.basin || '') === BASINS[b].outlook);
    const oplaced = [];
    outl.forEach(o => rings(o.region).forEach(r => {
      svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('') + 'Z',
        fill: 'rgba(224,138,30,.13)', stroke: '#e08a1e', 'stroke-width': 1.6, 'stroke-dasharray': '6 4' }));
      const cx = Math.min(r.reduce((s, p) => s + X(p[0]), 0) / r.length, 905);
      let cy = r.reduce((s, p) => s + Y(p[1]), 0) / r.length;
      while (oplaced.some(q => Math.abs(cx - q[0]) < 115 && Math.abs(cy - q[1]) < 34)) cy += 34;
      oplaced.push([cx, cy]);
      svg.appendChild(txt((o.prob7 || '?') + ' / 7 days', { x: cx, y: cy, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: '#b26a08', class: 'lbl' }));
      svg.appendChild(txt((o.prob2 || '?') + ' / 2 days', { x: cx, y: cy + 14, 'text-anchor': 'middle', 'font-size': 9.5, fill: '#b26a08', class: 'lbl' }));
    }));
    const storms = (H.storms || []).filter(s => b === 'AL' ? s.basin === 'AL' : s.basin !== 'AL');
    storms.forEach(s => {
      (s.cone || []).forEach(c => rings(c).forEach(r => svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('') + 'Z', fill: 'rgba(100,116,139,.14)', stroke: '#64748b', 'stroke-width': 1 }))));
      (s.past || []).forEach(c => rings(c).forEach(r => svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(''), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.3, 'stroke-dasharray': '3 3' }))));
      (s.track || []).forEach(c => rings(c).forEach(r => svg.appendChild(el('path', { d: r.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(''), fill: 'none', stroke: 'var(--navy)', 'stroke-width': 2 }))));
      (s.points || []).forEach((p, i) => {
        svg.appendChild(el('circle', { cx: X(p.lon), cy: Y(p.lat), r: p.tau === 0 ? 6 : 4.5, fill: ptColor(p), stroke: 'var(--panel)', 'stroke-width': 1.2 }));
        if (p.label) svg.appendChild(txt(p.label.replace(':00', '') + (p.kt ? ' · ' + p.kt + 'kt' : ''), { x: X(p.lon) + 8, y: Y(p.lat) + (i % 2 ? 14 : -8), 'font-size': 9, fill: 'var(--ink)', class: 'lbl' }));
        if (p.tau === 0) svg.appendChild(txt((p.type || s.classification) + ' ' + s.name + ' · ' + (p.kt != null ? p.kt : s.intensityKt) + 'kt · adv ' + (s.geometryAdvisory || s.advisory) + (s.geometryStale ? ' (stale)' : ''),
          { x: X(p.lon) + 10, y: Y(p.lat) - 20, 'font-size': 12.5, 'font-weight': 700, fill: 'var(--navy)', class: 'lbl' }));
      });
    });
    return { storms: storms.length, areas: outl.length };
  }

  // the vendor ladder at a reference location: the hottest active storm's LiveCyc row
  function vendorSite(id) {
    if (!RK || !RK.enabled) return null;
    let best = null;
    (RK.storms || []).forEach(s => {
      const lc = s.livecyc; if (!lc || !lc.sites || !lc.sites[id]) return;
      const row = lc.sites[id];
      const i80 = lc.thresholds.indexOf(80);
      const p80 = i80 >= 0 ? row.p[i80] : row.p[0];
      if (!best || p80 > best.p80) best = { storm: s.name, p80, thresholds: lc.thresholds, p: row.p, forecastTime: lc.forecastTime };
    });
    return best;
  }

  function draw() {
    const B = BASINS[basin];
    const [b0, la0, b1, la1] = B.box, W = 980, Hh = 600;
    const kx = W / (b1 - b0), ky = Hh / (la1 - la0);
    const X = lon => (lon - b0) * kx, Y = lat => (la1 - lat) * ky;
    const svg = $('#basin'); svg.innerHTML = '';
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: Hh, fill: 'var(--map-sea)' }));
    const lf = basin === 'AL' ? landfallQuotes() : null;
    const poly = (rr, fill, stroke, sw, title) => {
      const p = el('path', { d: rr.map(r => 'M' + r.map(q => X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1)).join('L') + 'Z').join(' '), fill, stroke, 'stroke-width': sw });
      if (title) { const t = el('title'); t.textContent = title; p.appendChild(t); }
      svg.appendChild(p);
    };
    const fillFor = (label) => {
      if (!lf) return [ 'var(--map-land)', null ];
      const c = lf[label];
      return c && c.mid != null ? [ramp(c.mid), label + ': ' + quoteText(c)] : ['var(--map-land)', null];
    };
    if (GEO) {
      Object.entries(GEO.countries || {}).forEach(([nm, rr]) => {
        const label = Object.keys(lf || {}).find(k => regionKey(k) === nm) || nm;
        const [f, t] = fillFor(label);
        poly(rr, f, 'var(--map-line)', .6, t);
      });
      (NATION || []).forEach(r => poly([r], 'var(--map-land)', 'var(--map-line)', .6));
      Object.entries(GEO.states || {}).forEach(([nm, rr]) => { const [f, t] = fillFor(nm); poly(rr, f, 'var(--map-line)', .7, t); });
      Object.entries(GEO.counties || {}).forEach(([nm, rr]) => { const [f, t] = fillFor(nm); poly(rr, f, 'var(--ink)', .8, t); });
    }
    const counts = drawNhc(svg, X, Y, basin);
    // the reference locations: small dots, scaled by the vendor's P(gust > 80 mph) when the lane is live
    let vendorShown = 0;
    (GEO && GEO.locations || []).forEach(L => {
      if (L.lon < b0 || L.lon > b1 || L.lat < la0 || L.lat > la1) return;
      const v = vendorSite(L.id);
      const r = v && v.p80 > 0 ? 3 + 9 * Math.sqrt(Math.min(v.p80, 100) / 100) : 2.2;
      if (v && v.p80 > 0) vendorShown++;
      const c = el('circle', { cx: X(L.lon), cy: Y(L.lat), r, fill: v && v.p80 > 0 ? 'rgba(192,57,43,.75)' : 'var(--muted)', 'fill-opacity': v ? .85 : .55, stroke: 'var(--panel)', 'stroke-width': .6 });
      c.onmousemove = e => tip.show(e, '<b>' + L.name + ' (' + L.id + ')</b>' + (L.state ? L.state + ', ' : '') + L.country +
        (v ? '<br>' + v.storm + ' · ' + v.thresholds.map((t, i) => v.p[i] >= 0.5 ? t + ' mph: ' + v.p[i] + '%' : null).filter(Boolean).slice(0, 5).join(' · ') + '<br>Reask LiveCyc ' + v.forecastTime : '<br>no live-storm probabilities'));
      c.onmouseleave = () => tip.hide();
      svg.appendChild(c);
    });
    $('#modeTitle').textContent = B.name.toUpperCase() + ' · NHC forecast tracks, cones and formation odds' + (lf ? ' · landfall regions shaded by the exchange’s Yes price' : '');
    $('#basinCap').textContent = (counts.storms ? '' : 'No active tropical cyclones in this basin at the last update. ') +
      'Orange dashed regions are NHC seven-day formation odds; cones and tracks draw automatically when a storm is active. ' +
      (basin === 'EP' ? 'The Central Pacific outlook is issued by CPHC and is not in this feed, so that part of the map shows storms only. ' : '') +
      (lf ? 'States, countries and the six named counties are shaded by the Yes price of the landfall contract for that region (hover for bid and ask); unshaded regions have no listed contract or no book. ' : '') +
      (vendorShown ? 'Red dots scale with the vendor’s probability of a gust above 80 mph at that reference location. ' : '');
    const key = $('#basinKey'); key.innerHTML = '';
    if (lf) {
      const sw = [0.05, 0.25, 0.5, 0.75].map(p => '<span><i style="background:' + ramp(p) + ';border-color:' + ramp(p) + '"></i>' + Math.round(p * 100) + '¢</span>').join('');
      key.innerHTML = '<span>Landfall contract, Yes price:</span>' + sw + '<span><i style="border-color:var(--muted)"></i>reference location</span>';
    }
  }

  // ---- active storms with NHC wind speed probabilities
  function drawStorms() {
    const list = $('#storms'); list.innerHTML = '';
    (H.storms || []).forEach(s => {
      list.appendChild(h('div', { class: 'stormrow' }, [
        h('b', { text: s.classification + ' ' + s.name }),
        h('span', { text: s.basin + ' · ' + s.intensityKt + ' kt · ' + (s.pressureMb || '--') + ' mb · advisory ' + s.advisory + (s.geometryAdvisory && String(s.geometryAdvisory).replace(/^0+/, '') !== String(s.advisory).replace(/^0+/, '') ? ' (map shows ' + s.geometryAdvisory + ')' : '') }),
        s.advisoryUrl ? h('a', { href: s.advisoryUrl, text: 'NHC advisory', target: '_blank', rel: 'noopener' }) : h('span'),
        s.windProbsUrl ? h('a', { href: s.windProbsUrl, text: 'wind speed probabilities', target: '_blank', rel: 'noopener' }) : h('span'),
      ]));
      if (s.windProbs && s.windProbs.length) {
        const tb = h('table', { class: 'pws' });
        tb.appendChild(h('tr', {}, [h('th', { text: 'NHC five-day cumulative probability' }), h('th', { class: 'num', text: '≥34 kt' }), h('th', { class: 'num', text: '≥50 kt' }), h('th', { class: 'num', text: '≥64 kt' })]));
        s.windProbs.slice(0, 14).forEach(r => tb.appendChild(h('tr', {}, [h('td', { text: r.location }), h('td', { class: 'num', text: r.p34 + '%' }), h('td', { class: 'num', text: r.p50 + '%' }), h('td', { class: 'num', text: r.p64 + '%' })])));
        list.appendChild(h('div', { class: 'card', style: 'padding:0;margin:6px 0 12px' }, [tb]));
      }
    });
    if (!(H.storms || []).length) list.appendChild(h('div', { class: 'cap', text: 'No active tropical cyclones in the NHC roster.' }));
  }

  // ---- season tiles and the count ladders
  function tile(label, value, sub) {
    return h('div', { class: 'tile' }, [h('div', { class: 'tv', text: value == null ? '—' : String(value) }), h('div', { class: 'tl', text: label }), sub ? h('div', { class: 'ts', text: sub }) : h('span')]);
  }
  function ladderPanel(title, rows, sub) {
    const div = h('div', { class: 'ladder' }, [h('div', { class: 'lt', text: title })]);
    if (sub) div.appendChild(h('div', { class: 'cap', style: 'margin:0 0 6px', text: sub }));
    rows.forEach(r => {
      const y = yes(r.c);
      const bar = h('div', { class: 'lrow', title: quoteText(r.c) }, [
        h('span', { class: 'lk', text: r.label }),
        h('span', { class: 'lb' }, [h('i', { style: 'width:' + (y == null ? 0 : y) + '%' })]),
        h('span', { class: 'lv', text: y == null ? 'no book' : y + '¢' }),
      ]);
      div.appendChild(bar);
    });
    if (!rows.length) div.appendChild(h('div', { class: 'cap', text: 'Not listed.' }));
    return div;
  }
  function drawSeason() {
    const s = H.season || {};
    const tiles = $('#tiles'); tiles.innerHTML = '';
    tiles.appendChild(tile('named storms, ' + (s.year || 'this season'), s.named, (s.names || []).join(', ') || 'none yet'));
    tiles.appendChild(tile('hurricanes', s.hurricanes, 'from the ATCF best tracks'));
    tiles.appendChild(tile('major hurricanes', s.majors, 'category 3 or stronger'));
    const cat4 = market('HCAT4');
    if (cat4) cat4.contracts.slice().sort((a, b) => a.spec.localeCompare(b.spec)).slice(0, 4).forEach(c =>
      tiles.appendChild(tile('Cat 4 US landfall ' + c.label.replace(/^By /, 'by '), yes(c) == null ? '—' : yes(c) + '¢', 'Yes price, ' + quoteText(c).replace(/^Yes [^(]*/, '').replace(/[()]/g, ''))));
    const lad = $('#ladders'); lad.innerHTML = '';
    if (!MK) {
      $('#laddersCap').textContent = WXM.on() ? 'Exchange quotes unavailable.' : 'The market layer is off; no contract prices are shown.';
      return;
    }
    const byStrike = sym => { const m = market(sym); return m ? m.contracts.slice().sort((a, b) => a.strike - b.strike).map(c => ({ label: c.label, c })) : []; };
    lad.appendChild(ladderPanel(COUNT.TROPA + ' (TROPA)', byStrike('TROPA'), 'P(count above N) as the Yes price; ' + (s.named != null ? s.named + ' so far' : '')));
    lad.appendChild(ladderPanel(COUNT.HCAB + ' (HCAB)', byStrike('HCAB'), 'P(at least N) as the Yes price; ' + (s.hurricanes != null ? s.hurricanes + ' so far' : '')));
    const mh = market('MHCMA');
    if (mh) {
      const specNum = sp => sp.split('.').reduce((a, v, i) => a + (+v) * (i ? 1 : 100), 0);   // '2026.8' -> 202608, '2026.10' -> 202610
      const specs = [...new Set(mh.contracts.map(c => c.spec))].sort((a, b) => specNum(a) - specNum(b));
      specs.forEach(sp => lad.appendChild(ladderPanel(COUNT.MHCMA.replace('by month', '') + mh.contracts.find(c => c.spec === sp).expiryLabel + ' (MHCMA)',
        mh.contracts.filter(c => c.spec === sp).sort((a, b) => a.strike - b.strike).map(c => ({ label: c.label, c })), 'P(count above N) in that month')));
    }
    $('#laddersCap').textContent = 'Exchange contracts as quoted ' + clockFull(Date.parse(MK.asof), local()) + (MK.stale ? ' (stale)' : '') + '; the bar is the Yes-side midpoint in cents, bid and ask on hover. Season counts are this site’s own reading of the best tracks, not the exchange’s settlement count.';
  }

  // ---- the landfall board
  function drawLandfall() {
    const host = $('#landfall'); host.innerHTML = '';
    const m = market('HLF');
    if (!m) { host.appendChild(h('p', { class: 'cap', text: WXM.on() ? 'Landfall contracts not in the quote snapshot.' : 'The market layer is off.' })); return; }
    const tb = h('table');
    tb.appendChild(h('tr', {}, [h('th', { text: 'Region (' + (m.contracts[0] && m.contracts[0].expiryLabel || 'season') + ')' }), h('th', { class: 'num', text: 'Yes bid' }), h('th', { class: 'num', text: 'Yes ask' }), h('th', { class: 'num', text: 'Mid' }), h('th', { text: 'On the map' })]));
    m.contracts.slice().sort((a, b) => (b.mid == null ? -1 : b.mid) - (a.mid == null ? -1 : a.mid)).forEach(c => {
      const k = regionKey(c.label);
      const onMap = GEO && ((GEO.states || {})[k] || (GEO.counties || {})[k] || (GEO.countries || {})[k]) ? 'shaded' : 'not drawn';
      tb.appendChild(h('tr', {}, [h('td', { text: c.label }), h('td', { class: 'num', text: pct(cents(c.bid)) }), h('td', { class: 'num', text: pct(cents(c.ask)) }), h('td', { class: 'num', text: c.mid == null ? 'no book' : pct(cents(c.mid)) }), h('td', { text: onMap })]));
    });
    host.appendChild(h('div', { class: 'card', style: 'padding:0' }, [tb]));
    host.appendChild(h('p', { class: 'cap', text: 'A Yes contract pays if a hurricane makes landfall in that region during the season named. Prices as quoted ' + clockFull(Date.parse(MK.asof), local()) + '.' }));
  }

  // ---- the vendor lane
  function drawVendor() {
    const host = $('#vendor'); host.innerHTML = '';
    if (!RK) { host.appendChild(h('p', { class: 'cap', text: 'Vendor lane status unavailable.' })); return; }
    if (!RK.enabled) {
      host.appendChild(h('p', { class: 'cap', text: 'Not enabled on this site (' + (RK.reason || 'off') + '). When a storm is active and the lane is on, this section shows the vendor’s probability that the peak gust exceeds each threshold at the reference locations, as published, four times a day.' }));
      return;
    }
    const storms = RK.storms || [];
    if (!storms.length) {
      host.appendChild(h('p', { class: 'cap', text: 'Lane on; no storm with published probabilities this year yet (last poll ' + (RK.polled ? clockFull(Date.parse(RK.polled), local()) : 'unknown') + ').' }));
    }
    storms.forEach(s => {
      const lc = s.livecyc;
      host.appendChild(h('div', { class: 'stormrow' }, [h('b', { text: s.name + ' ' + s.year }),
        h('span', { text: lc ? 'LiveCyc cycle ' + lc.forecastTime + ' · ' + Object.keys(lc.sites || {}).length + ' locations with non-zero probability' : 'no LiveCyc cycle yet' }),
        h('span', { text: s.interim ? 'interim settlement file received' : '' }),
        h('span', { text: s.final ? 'final settlement file received' : '' })]));
      if (lc && lc.sites) {
        const cols = [70, 80, 90, 100, 110, 120, 130, 150].filter(t => lc.thresholds.includes(t));
        const idx = cols.map(t => lc.thresholds.indexOf(t));
        const i80 = lc.thresholds.indexOf(80);
        const rows = Object.entries(lc.sites).sort((a, b) => (b[1].p[i80] || 0) - (a[1].p[i80] || 0)).slice(0, 16);
        const tb = h('table');
        tb.appendChild(h('tr', {}, [h('th', { text: 'Reference location' })].concat(cols.map(t => h('th', { class: 'num', text: '> ' + t + ' mph' })))));
        rows.forEach(([id, r]) => tb.appendChild(h('tr', {}, [h('td', { text: r.name + ' (' + id + ')' })].concat(idx.map(i => h('td', { class: 'num', text: r.p[i] + '%' }))))));
        host.appendChild(h('div', { class: 'card', style: 'padding:0' }, [tb]));
      }
      if (s.final && s.final.sites) {
        const fin = Object.entries(s.final.sites).sort((a, b) => b[1].peakGustMph - a[1].peakGustMph).slice(0, 10);
        host.appendChild(h('p', { class: 'cap', text: 'Final peak gusts: ' + fin.map(([id, r]) => r.name + ' ' + r.peakGustMph + ' mph').join(' · ') }));
      }
    });
    host.appendChild(h('p', { class: 'cap attrib', text: (RK.attribution || 'Powered by Reask') + '. Probabilities are the vendor’s, shown as published; last poll ' + (RK.polled ? clockFull(Date.parse(RK.polled), local()) : 'unknown') + '.' }));
  }

  // ---- anything else the exchange lists in its hurricane category (a storm's
  //      wind contracts appear here automatically when listed)
  function drawOthers() {
    const host = $('#others'); host.innerHTML = '';
    if (!MK) { host.appendChild(h('p', { class: 'cap', text: WXM.on() ? 'Exchange quotes unavailable.' : 'The market layer is off.' })); return; }
    const others = MK.markets.filter(m => !COUNT[m.symbol]);
    if (!others.length) { host.appendChild(h('p', { class: 'cap', text: 'Nothing beyond the count and landfall contracts is listed at the moment.' })); return; }
    others.forEach(m => {
      const tb = h('table');
      tb.appendChild(h('tr', {}, [h('th', { text: m.name + ' (' + m.symbol + ')' }), h('th', { text: 'Settles' }), h('th', { class: 'num', text: 'Yes bid' }), h('th', { class: 'num', text: 'Yes ask' }), h('th', { class: 'num', text: 'Mid' })]));
      m.contracts.slice().sort((a, b) => a.spec.localeCompare(b.spec) || a.strike - b.strike).forEach(c =>
        tb.appendChild(h('tr', {}, [h('td', { text: c.label }), h('td', { text: c.expiryLabel || c.spec }), h('td', { class: 'num', text: pct(cents(c.bid)) }), h('td', { class: 'num', text: pct(cents(c.ask)) }), h('td', { class: 'num', text: c.mid == null ? 'no book' : pct(cents(c.mid)) })])));
      host.appendChild(h('div', { class: 'card', style: 'padding:0;margin-bottom:10px' }, [tb]));
    });
  }

  async function init() {
    tip = WXC.tooltip();
    const r = await WXD.get('hurricane.json', 30);
    const rk = await WXD.get('reask.json', 10);
    const mk = await WXM.loadGroup('hurricane');
    const geo = await fetch('assets/hurricane-geo.json').then(x => x.json()).catch(() => null);
    H = r.data; GEO = geo; NATION = geo ? geo.nation : null; RK = rk.data; MK = WXM.hurricaneMarkets();
    const st = $('#pageStatus'); st.innerHTML = ''; st.appendChild(WXC.statusEl([r].concat(mk ? [mk] : []), 30));
    if (!H) { $('#basin').innerHTML = ''; $('#basin').appendChild(txt('No data available.', { x: 60, y: 50, class: 'axl' })); return; }
    [['b1', 'AL'], ['b2', 'EP']].forEach(([id, b]) => { $('#' + id).onclick = () => { basin = b; document.querySelectorAll('.bar button').forEach(x => x.classList.remove('on')); $('#' + id).classList.add('on'); draw(); }; });
    draw(); drawStorms(); drawSeason(); drawLandfall(); drawVendor(); drawOthers();
  }
  return { init };
})();
