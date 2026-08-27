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
    // Two rows. The first is the two branches the contracts divide into; the
    // second is the categories of whichever branch you are in, and it stays
    // visible so a reader can move sideways without going back up. Reference
    // pages sit to the right of the first row, lighter, because they are not
    // contracts. No city tab: a city is reached by its dot on the map, which is
    // where a reader is already looking when they want one.
    // the scorecard lives on the daily temperatures page now; scorecard.html
    // still answers, because the daily letter links its four views directly
    const REF = [['daily-temperature-markets.html', 'Trading temp markets'],
                 ['accuracy.html', 'Accuracy'], ['faq.html', 'FAQ'], ['about.html', 'About']];
    const nav = cfg.nav || { l1: [], categories: [] };
    const cats = nav.categories || [];
    const pageOf = c => (c.page || '').split('?')[0];
    const paramOf = c => { const q = (c.page || '').split('?')[1]; return q ? new URLSearchParams(q).get('c') : null; };
    const q = new URLSearchParams(location.search);
    const here = q.get('c');
    // which category this page belongs to: by ?c= on the shared category page,
    // by the product's own id on a contract page, or by the page's file name,
    // with the city chart counted as the map's
    const byProduct = (nav.product || {})[(q.get('id') || '').toUpperCase()];
    const mine = cats.find(c => c.slug === here)
      || (byProduct ? cats.find(c => c.slug === byProduct) : null)
      || cats.find(c => !paramOf(c) && pageOf(c) === active)
      || (['city.html', 'scorecard.html'].indexOf(active) >= 0
          ? cats.find(c => pageOf(c) === 'index.html') : null);
    // a branch page names its branch directly and belongs to no one category
    const bySection = q.get('s');
    const branch = mine ? mine.l1
      : (bySection ? ((nav.l1.find(x => x.slug === bySection) || {}).name || '') : '')
        || ((nav.l1[0] || {}).name || '');

    const row1 = h('nav', { class: 'l1' }, (nav.l1 || []).map(b =>
      h('a', { href: 'section.html?s=' + b.slug, text: b.name, class: b.name === branch ? 'on' : '' })));
    if (REF.length) {
      const ref = h('span', { class: 'refnav' }, REF.map(([href, label]) =>
        h('a', { href, text: label, class: href === active ? 'on' : '' })));
      row1.appendChild(ref);
    }
    const inBranch = cats.filter(c => c.l1 === branch);
    const row2 = h('nav', { class: 'l2' }, inBranch.map(c =>
      h('a', { href: c.page, text: c.l2, class: (mine && c.slug === mine.slug) ? 'on' : '' })));
    const header = h('header', { class: 'site' }, [
      h('a', { class: 'brand', href: 'index.html', text: cfg.siteTitle || 'Weather tools' }),
      row1, inBranch.length ? row2 : h('span'),
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

  // ---- tooltip: one shared box, placed beside the pointer and flipped to stay
  //      on screen; a click pins it (so the text can be read or selected) and a
  //      click elsewhere or Escape releases it. Content is HTML built by the
  //      pages from snapshot values; `rows(title, [[label, value], ...])`
  //      lays out the common two-column form.
  /* Open any chart to fill the window.

     The series panels do this by redrawing themselves into a bigger box, which
     they can because every coordinate in them is derived. A chart with a fixed
     viewBox cannot, but it does not need to: the browser scales it, and a chart
     that is twice as wide is twice as readable even if the type scales with it.

     So this is the general case — mark a container expandable, get a button and
     Escape — and a panel that wants to re-derive its geometry does that itself.
     Returns the button so a caller can place it. */
  function expander(container, label) {
    const b = h('button', { class: 'zb ex', text: label || 'Expand' });
    let esc = null;
    const close = () => {
      container.classList.remove('full');
      document.body.classList.remove('wtfull');
      b.textContent = label || 'Expand';
      b.classList.remove('on');
      if (esc) { document.removeEventListener('keydown', esc); esc = null; }
      window.dispatchEvent(new Event('resize'));
    };
    b.onclick = () => {
      if (container.classList.contains('full')) return close();
      container.classList.add('full');
      document.body.classList.add('wtfull');
      b.textContent = 'Close';
      b.classList.add('on');
      esc = ev => { if (ev.key === 'Escape') close(); };
      document.addEventListener('keydown', esc);
      window.dispatchEvent(new Event('resize'));
    };
    return b;
  }

  function tooltip() {
    let tip = $('#tip');
    if (!tip) {
      tip = h('div', { id: 'tip', class: 'tip' }); document.body.appendChild(tip);
      document.addEventListener('keydown', ev => { if (ev.key === 'Escape') { tip.dataset.pinned = ''; tip.style.opacity = 0; } });
      document.addEventListener('click', ev => { if (tip.dataset.pinned && !tip.contains(ev.target) && !ev.target.closest('[data-tip-pin]')) { tip.dataset.pinned = ''; tip.style.opacity = 0; } });
    }
    const place = e => {
      const W = tip.offsetWidth || 260, Hh = tip.offsetHeight || 60;
      let x = e.clientX + 14, y = e.clientY + 14;
      if (x + W > window.innerWidth - 8) x = Math.max(8, e.clientX - 14 - W);
      if (y + Hh > window.innerHeight - 8) y = Math.max(8, e.clientY - 14 - Hh);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    };
    return {
      show(e, html) { if (tip.dataset.pinned) return; tip.innerHTML = html; tip.style.opacity = 1; place(e); },
      hide() { if (tip.dataset.pinned) return; tip.style.opacity = 0; },
      pin(e, html) { tip.dataset.pinned = ''; tip.innerHTML = html; tip.style.opacity = 1; place(e); tip.dataset.pinned = '1'; tip.classList.add('pinned'); setTimeout(() => tip.classList.remove('pinned'), 400); },
      pinned: () => !!tip.dataset.pinned,
      /* The two prices, given the room they deserve.

         A reader hovering a strike wants what it costs to take each side, and
         those were the fifth and sixth lines of a six-line table. They now sit
         above everything else, big, in the Yes-green No-red the rest of the site
         uses. The detail stays underneath for anyone who wants the book.

         These are the prices to BUY each side: the Yes ask, and one dollar less
         the Yes bid for the No. There are no sellers on this exchange, only bids
         to buy one side or the other, and the two sum to a dollar. */
      price(yesBuy, noBuy, yesSub, noSub) {
        const c = v => (v == null ? '—' : Math.round(v * 100) + '¢');
        const sub = t => (t ? '<span class="tps">' + t + '</span>' : '');
        return '<div class="tprice">'
          + '<div class="tp yes"><span class="tpl">Buy Yes</span><span class="tpv">' + c(yesBuy) + '</span>'
          + sub(yesSub) + '</div>'
          + '<div class="tp no"><span class="tpl">Buy No</span><span class="tpv">' + c(noBuy) + '</span>'
          + sub(noSub) + '</div>'
          + '</div>';
      },
      rows(title, pairs, foot) {
        const body = pairs.filter(p => p && p[1] != null && p[1] !== '').map(p => '<span class="tk">' + p[0] + '</span><span class="tv">' + p[1] + '</span>').join('');
        return (title ? '<b>' + title + '</b>' : '') + (body ? '<div class="tg">' + body + '</div>' : '') + (foot ? '<div class="tf">' + foot + '</div>' : '');
      },
    };
  }

  const param = k => new URLSearchParams(location.search).get(k);
  const deg = v => (v == null ? '--' : (Math.round(v * 10) / 10).toFixed(v % 1 ? 1 : 0) + '°');
  return { el, txt, h, $, clock, clockFull, dateShort, hourOf, minuteOf, hourTicks, P, chrome, statusEl, tooltip, param, deg, expander };
})();
