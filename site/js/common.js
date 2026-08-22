/* Shared helpers: SVG/HTML element construction, local-time formatting
   through the station's IANA zone, the page chrome, and the status strip.
   No dependencies. Every page loads config.js (written by scripts/build.py)
   before this file; window.WX holds the site configuration. */
window.WXC = (() => {
  const SVG = 'http://www.w3.org/2000/svg';
  const el = (n, a = {}, kids = []) => {
    const e = document.createElementNS(SVG, n);
    for (const k in a) if (a[k] != null) e.setAttribute(k, a[k]);
    kids.forEach(c => e.appendChild(c));
    return e;
  };
  const txt = (s, a = {}) => { const e = el('text', a); e.textContent = s; return e; };
  const h = (tag, a = {}, kids = []) => {
    const e = document.createElement(tag);
    for (const k in a) {
      if (k === 'class') e.className = a[k];
      else if (k === 'text') e.textContent = a[k];
      else if (k === 'html') e.innerHTML = a[k];
      else if (k.startsWith('on')) e[k] = a[k];
      else if (a[k] != null) e.setAttribute(k, a[k]);
    }
    (Array.isArray(kids) ? kids : [kids]).forEach(c => e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
    return e;
  };
  const $ = s => document.querySelector(s);

  // ---- time in the station's zone. Offsets are never added by hand: a
  //      fixed offset mis-buckets the hours around a DST change.
  const fmtCache = {};
  function parts(ms, tz) {
    const key = tz || 'UTC';
    if (!fmtCache[key]) fmtCache[key] = new Intl.DateTimeFormat('en-US', {
      timeZone: key, hour: 'numeric', minute: '2-digit', hour12: false,
      month: 'short', day: 'numeric', weekday: 'short' });
    const out = {};
    for (const p of fmtCache[key].formatToParts(new Date(ms))) out[p.type] = p.value;
    out.hour = +out.hour % 24; out.minute = +out.minute;
    return out;
  }
  const clock = (ms, tz) => { const p = parts(ms, tz); const h12 = p.hour % 12 || 12;
    return h12 + (p.minute ? ':' + String(p.minute).padStart(2, '0') : '') + (p.hour < 12 ? 'a' : 'p'); };
  const clockFull = (ms, tz) => { const p = parts(ms, tz); const h12 = p.hour % 12 || 12;
    return h12 + ':' + String(p.minute).padStart(2, '0') + (p.hour < 12 ? ' AM' : ' PM'); };
  const dateShort = (ms, tz) => { const p = parts(ms, tz); return p.month + ' ' + p.day; };
  const hourOf = (ms, tz) => parts(ms, tz).hour;
  const minuteOf = (ms, tz) => parts(ms, tz).minute;
  // ticks on local 6-hour marks between two instants
  function hourTicks(t0, t1, tz, every = 6) {
    const out = [];
    for (let t = Math.ceil(t0 / 36e5) * 36e5; t <= t1; t += 36e5) {
      const p = parts(t, tz);
      if (p.hour % every || p.minute) continue;
      out.push({ t, label: p.hour === 0 ? p.month + ' ' + p.day : clock(t, tz) });
    }
    return out;
  }
  const P = s => (s ? Date.parse(s) : NaN);

  // ---- page chrome: header with navigation, footer with the disclosure
  function chrome(active) {
    const cfg = window.WX || {};
    if (cfg.target === 'embed') return;
    const wrap = $('.wrap');
    if (!wrap) return;
    const nav = [['index.html', 'Map'], ['city.html', 'City'], ['scorecard.html', 'Scorecard'],
                 ['hurricane.html', 'Hurricanes'], ['climate.html', 'Climate'], ['about.html', 'About']];
    const header = h('header', { class: 'site' }, [
      h('a', { class: 'brand', href: 'index.html', text: cfg.siteTitle || 'Weather tools' }),
      h('nav', {}, nav.map(([href, label]) => h('a', { href, text: label, class: href === active ? 'on' : '' }))),
    ]);
    wrap.insertBefore(header, wrap.firstChild);
    const footer = h('footer', { class: 'site' }, [
      h('p', { text: cfg.disclosure || '' }),
      h('p', { html: 'Weather data: National Weather Service, National Hurricane Center and other NOAA sources, public domain. ' +
        'Nothing here is an official National Weather Service product. Values marked derived are computed here, not published by NWS. ' +
        '<a href="about.html">Sources and methods</a>.' }),
    ]);
    wrap.appendChild(footer);
  }

  // ---- status strip for one or more snapshot results
  function statusEl(results, cadenceMin) {
    const worst = results.reduce((a, r) => (rank(r) > rank(a) ? r : a), results[0]);
    const cls = worst.source === 'none' ? 'none' : (worst.source === 'cache' ? 'cache' : (worst.stale ? 'stale' : 'live'));
    return h('span', { class: 'status ' + cls, text: statusText(worst, cadenceMin) });
  }
  const rank = r => (r.source === 'none' ? 3 : r.source === 'cache' ? 2 : r.stale ? 1 : 0);
  function statusText(r, cadenceMin) {
    if (r.source === 'none') return 'No data available. The data feed could not be reached and nothing is cached.';
    const when = r.asof ? clockFull(r.asof, Intl.DateTimeFormat().resolvedOptions().timeZone) : 'unknown time';
    const age = r.ageMin == null ? '' : (r.ageMin < 1 ? 'just now' : Math.round(r.ageMin) + ' min ago');
    if (r.source === 'cache') return `Showing the last data this browser saved (as of ${when}, ${age}); the live fetch failed.`;
    if (r.stale) return `Data as of ${when} (${age}); updates are normally every ${cadenceMin} min and the feed is behind.`;
    return `Data as of ${when} (${age}) · updates every ${cadenceMin} min`;
  }

  // ---- tooltip
  function tooltip() {
    let tip = $('#tip');
    if (!tip) { tip = h('div', { id: 'tip', class: 'tip' }); document.body.appendChild(tip); }
    return {
      show(e, html) { tip.innerHTML = html; tip.style.opacity = 1;
        tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 270) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; },
      hide() { tip.style.opacity = 0; },
    };
  }

  const param = k => new URLSearchParams(location.search).get(k);
  const deg = v => (v == null ? '--' : (Math.round(v * 10) / 10).toFixed(v % 1 ? 1 : 0) + '°');
  return { el, txt, h, $, clock, clockFull, dateShort, hourOf, minuteOf, hourTicks, P, chrome, statusEl, tooltip, param, deg };
})();
