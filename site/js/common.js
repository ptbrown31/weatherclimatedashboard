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
    // the lessons page is being written and is deliberately not linked here
    // yet; it answers at its own address for anyone working on it
    const REF = [['daily-temperature-markets.html', 'Trading temp markets'],
                 ['allocator.html', 'Position calculator'],
                 ['accuracy.html', 'Accuracy'], ['faq.html', 'FAQ'], ['about.html', 'About']];
    const nav = cfg.nav || { l1: [], categories: [] };
    const ADOPT = { 'city.html': 'index.html', 'scorecard.html': 'index.html',
                    'hurricane.html': 'tropical-cyclone-markets.html', 'climate-gdp.html': 'climate.html' };
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
      // a page that belongs to a category without being the page that category
      // opens: the city chart and the scorecard are the map's, the basin
      // view is the cyclone area's, which opens on the two-ocean page instead,
      // and the world GDP contract's article is the climate series page's
      || (ADOPT[active] ? cats.find(c => pageOf(c) === ADOPT[active]) : null);
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
    offsite();
  }

  /* Links that leave the site open in their own tab.

     Done in one place rather than on every anchor. A page builds most of its
     links from snapshots after load, so marking them up by hand would cover
     only the ones written into the HTML, and every later link would have to
     remember. The click handler runs in the capture phase and catches anything
     on the page whatever built it; the attributes are set as well so a
     middle-click, a long-press and the keyboard all behave the same way. */
  function offsite() {
    const external = a => {
      if (!a || !a.getAttribute) return false;
      const href = a.getAttribute('href') || '';
      if (!/^https?:/i.test(href)) return false;
      try { return new URL(href, location.href).host !== location.host; } catch (e) { return false; }
    };
    const mark = root => {
      (root || document).querySelectorAll('a[href^="http"]').forEach(a => {
        if (!external(a) || a.target === '_blank') return;
        a.target = '_blank';
        a.rel = (a.rel ? a.rel + ' ' : '') + 'noopener noreferrer';
      });
    };
    mark(document);
    document.addEventListener('click', ev => {
      const a = ev.target && ev.target.closest && ev.target.closest('a[href]');
      if (!external(a) || a.target === '_blank') return;
      ev.preventDefault();
      window.open(a.href, '_blank', 'noopener,noreferrer');
    }, true);
    // links a page draws after its own load still get the attributes
    if (window.MutationObserver) {
      new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
        if (n.nodeType === 1) mark(n.querySelectorAll ? n : null);
      }))).observe(document.body, { childList: true, subtree: true });
    }
  }

  // ---- status strip for one or more snapshot results
  function statusEl(results, cadenceMin) {
    const worst = results.reduce((a, r) => (rank(r) > rank(a) ? r : a), results[0]);
    const cls = worst.source === 'none' ? 'none' : (worst.source === 'cache' ? 'cache' : (worst.stale ? 'stale' : 'live'));
    return h('span', { class: 'status ' + cls, text: statusText(worst, cadenceMin) });
  }
  const rank = r => (r.source === 'none' ? 3 : r.source === 'cache' ? 2 : r.stale ? 1 : 0);
  // Minutes are the right unit for a ten-minute feed and the wrong one for a
  // daily series, where they run to four digits and stop being read at all.
  const plur = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  function spanText(mins) {
    if (mins < 90) return plur(Math.round(mins), 'minute');
    if (mins < 48 * 60) return plur(Math.round(mins / 60), 'hour');
    return plur(Math.round(mins / (60 * 24)), 'day');
  }
  function statusText(r, cadenceMin) {
    if (r.source === 'none') return 'No data available. The data feed could not be reached and nothing is cached.';
    const when = r.asof ? clockFull(r.asof, Intl.DateTimeFormat().resolvedOptions().timeZone) : 'unknown time';
    const age = r.ageMin == null ? '' : (r.ageMin < 1 ? 'just now' : spanText(r.ageMin) + ' ago');
    const every = cadenceMin >= 48 * 60 ? 'every ' + spanText(cadenceMin)
                : cadenceMin >= 24 * 60 ? 'daily' : 'every ' + spanText(cadenceMin);
    if (r.source === 'cache') return `Showing the last data this browser saved (as of ${when}, ${age}); the live fetch failed.`;
    if (r.stale) return `Data as of ${when} (${age}); updates are normally ${every} and the feed is behind.`;
    return `Data as of ${when} (${age}) · updates ${every}`;
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
    let esc = null, corner = null;
    const close = () => {
      if (corner) { corner.remove(); corner = null; }
      container.classList.remove('full');
      document.body.classList.remove('wtfull');
      b.textContent = label || 'Expand';
      b.classList.remove('on');
      if (esc) { document.removeEventListener('keydown', esc); esc = null; }
      window.dispatchEvent(new Event('resize'));
    };
    /* A way out that is always on screen.

       The button that opened this sits in a bar that is now behind the overlay,
       and Escape only helps a reader who thinks to try it. So an expanded thing
       carries its own close in the corner, where a reader looks for one. */
    b.onclick = () => {
      if (container.classList.contains('full')) return close();
      container.classList.add('full');
      document.body.classList.add('wtfull');
      b.textContent = 'Close';
      b.classList.add('on');
      corner = h('button', { class: 'fullclose', title: 'Close (Esc)', 'aria-label': 'Close' });
      corner.textContent = '\u2715 Close';
      corner.onclick = ev => { ev.stopPropagation(); close(); };
      container.appendChild(corner);
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
         to buy one side or the other. */
      price(yesBuy, noBuy, yesSub, noSub, url) {
        const c = v => (v == null ? '—' : Math.round(v * 100) + '¢');
        const sub = t => (t ? '<span class="tps">' + t + '</span>' : '');
        // Given the contract's address, each side is a link to it, which a reader can
        // follow once the box is pinned: a box that follows the pointer cannot be clicked.
        // The exchange's page for a strike carries both sides, so both lead there.
        const href = url ? String(url).replace(/&/g, '&amp;').replace(/"/g, '&quot;') : '';
        const box = (cls, label, v, s) => (url
          ? '<a class="tp ' + cls + '" href="' + href + '" target="_blank" rel="noopener" title="' + label + ' on IBKR">'
          : '<div class="tp ' + cls + '">')
          + '<span class="tpl">' + label + '</span><span class="tpv">' + c(v) + '</span>' + sub(s)
          + (url ? '</a>' : '</div>');
        return '<div class="tprice">' + box('yes', 'Buy Yes', yesBuy, yesSub) + box('no', 'Buy No', noBuy, noSub) + '</div>';
      },
      // a pair may carry a third entry, a class for both its cells ('gs' opens a group)
      rows(title, pairs, foot) {
        const body = pairs.filter(p => p && p[1] != null && p[1] !== '').map(p => {
          const c = p[2] ? ' ' + p[2] : '';
          return '<span class="tk' + c + '">' + p[0] + '</span><span class="tv' + c + '">' + p[1] + '</span>';
        }).join('');
        return (title ? '<b>' + title + '</b>' : '') + (body ? '<div class="tg">' + body + '</div>' : '') + (foot ? '<div class="tf">' + foot + '</div>' : '');
      },
    };
  }

  const param = k => new URLSearchParams(location.search).get(k);
  const deg = v => (v == null ? '--' : (Math.round(v * 10) / 10).toFixed(v % 1 ? 1 : 0) + '°');
  /* A station's own page.

     Every station used to share one address with a station query on the end,
     so thirty-seven different places were one page as far as anything outside
     the browser could tell. Each has its own file now, and this builds the
     name of it. scripts/build.py names the files by the same rule and
     tests/test_build.py holds the two to one table. */
  function cityHref(c) {
    const city = String((c && c.city) || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return city + '-' + String((c && c.station) || '').toLowerCase() + '.html';
  }

  /* An asset's url, stamped with a hash of its own content.

     The scripts are cached for an hour and site/assets for a day, so a deploy
     that changes both can leave a reader running new code against an old
     asset. The build writes a hash per file and the url carries it, so the
     address changes exactly when the content does. Without a stamp the plain
     path is used, which is what local mode and any older build serve. */
  // the sixteen-point compass a wind is named by, from a bearing in degrees
  const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                   'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const compass = deg => (deg == null || isNaN(deg) ? '' : COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16]);

  const asset = name => 'assets/' + name
    + (window.WX && WX.assetV && WX.assetV[name] ? '?v=' + WX.assetV[name] : '');

  /* The roster minus the stations that carry no temperature contract.

     Twelve of them: nine shore stations that hold no ForecastEx product at all,
     and three whose only contract is MG wind. They belong on the wind map,
     which is why they are carried, and nowhere that implies a temperature
     market: the front map, the city page's two pickers, the article map, the
     scorecard's figures. One
     definition of that rule lives here so a surface added later cannot
     quietly include them; the roster row carries `windOnly` and
     pipeline/cities.py names the set. */
  const tempCities = list => (list || []).filter(c => !c.windOnly);

  /* A contract day's weekday name, from its own date string.

     Read at noon UTC so the name cannot slip a day either side of a timezone,
     and empty when there is no date, so a caller can fall back to its own
     wording rather than print "Invalid Date". */
  function weekdayOf(iso) {
    if (!iso) return '';
    const d = new Date(iso + 'T12:00:00Z');
    return isFinite(d) ? d.toLocaleDateString(undefined, { weekday: 'long', timeZone: 'UTC' }) : '';
  }

  /* A storm vendor's probability as every page prints it: to the whole
     percent (owner, 2026-10-08). A figure under half a percent reads as under
     one rather than as a zero the vendor did not publish, and one just short of
     certain as over 99 rather than as a certainty; a published zero is a zero.
     Plain text: a caller building HTML escapes it. */
  function vendorPct(v) {
    if (v == null || !isFinite(v)) return null;
    if (v > 0 && v < 0.5) return '<1%';
    if (v >= 99.5 && v < 100) return '>99%';
    return Math.round(v) + '%';
  }

  /* A Metryc gust as the Live Hurricane wind contracts resolve it: in whole
     miles an hour, rounded half up (owner, 2026-10-12). The vendor publishes a
     float and the exchange's terms report the settlement value to one decimal
     place; every page prints this figure and marks a strike settled against it,
     so a final of 69.6 mph reads 70 and clears a 70 mph strike. */
  const settleMph = v => (v == null || !isFinite(v) ? null : Math.round(v));

  /* A satellite loop, as the hurricane page draws one per storm and the landing
     page one of the lower 48: frames pipeline/floater.py copies into this site's
     bucket every half hour (owner, 2026-10-10). The newest frame shows at once and
     the loop plays while the card is on screen, its frames fetched only then and
     each shown once it has arrived. A reader who asks for reduced motion, or whose
     browser asks to save data, gets the newest frame and the buttons.

     A loop whose newest frame is more than two hours old is not drawn (loopFresh):
     its labels carry no date, and that age means the half-hourly pass or STAR has
     stopped for a while.

     opts: frames [{t, src}], head, sub, credit, alt, width and height (the frame's
     own size), crop [x, y, w, h] as fractions of the frame to show only that part,
     dated to put the date in each frame's label, cls and attrs for the card.
     Returns {card, stop}. */
  const LOOP_MAX_AGE = 2 * 36e5;
  // `maxAge` for a product with its own cadence: WPC's analyses come every three
  // hours and reach its server about two hours after their time
  const loopFresh = (frames, maxAge) => !!(frames && frames.length)
    && Date.now() - Date.parse(frames[frames.length - 1].t) <= (maxAge || LOOP_MAX_AGE);
  function satLoop(o) {
    const frames = o.frames, n = frames.length, tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    let k = n - 1, timer = null, held = false, loaded = false, io = null;
    const ready = frames.map((f, i) => i === n - 1);
    const img = h('img', { class: 'loopimg', src: frames[n - 1].src, width: o.width, height: o.height, decoding: 'async', alt: o.alt || '' });
    let view = img;
    if (o.crop) {
      // the frame is drawn larger than the box and moved so the box shows only the crop
      const [x, y, w, hh] = o.crop;
      img.style.cssText = 'width:' + (100 / w).toFixed(3) + '%;height:' + (100 / hh).toFixed(3) + '%;left:'
        + (-100 * x / w).toFixed(3) + '%;top:' + (-100 * y / hh).toFixed(3) + '%';
      view = h('div', { class: 'loopcrop', style: 'aspect-ratio:' + (w * o.width).toFixed(1) + ' / ' + (hh * o.height).toFixed(1) }, [img]);
    }
    const when = h('span', { class: 'looptime' });
    // `dated` for a loop that spans more than a day, so the same clock time twice is told apart
    const label = i => { const ms = Date.parse(frames[i].t);
      return new Date(ms).toISOString().slice(11, 16) + 'Z (' + clockFull(ms, tz) + (o.dated ? ' ' + dateShort(ms, tz) : '') + ')'
        + (i === n - 1 ? ' · latest' : ''); };
    const show = i => { k = (i + n) % n; img.src = frames[k].src; when.textContent = label(k); };
    const preload = () => {
      if (loaded) return; loaded = true;
      frames.forEach((f, i) => { if (ready[i]) return; const im = new Image(); im.onload = () => { ready[i] = true; }; im.src = f.src; });
    };
    const next = () => { for (let j = 1; j < n; j++) { const i = (k + j) % n; if (ready[i]) return i; } return k; };
    const play = h('button', { class: 'loopbtn', text: 'Play', title: 'play the loop' });
    const halt = () => { clearTimeout(timer); timer = null; play.textContent = 'Play'; };
    const step = () => { show(next()); timer = setTimeout(step, k === n - 1 ? 1400 : 180); };
    const start = () => { if (timer || n < 2) return; preload(); play.textContent = 'Pause'; timer = setTimeout(step, 400); };
    play.onclick = () => { if (timer) { halt(); held = true; } else { held = false; start(); } };
    const back = h('button', { class: 'loopbtn', text: '◀', title: 'the frame before' });
    const fwd = h('button', { class: 'loopbtn', text: '▶', title: 'the frame after' });
    back.onclick = () => { halt(); held = true; preload(); show(k - 1); };
    fwd.onclick = () => { halt(); held = true; preload(); show(k + 1); };
    when.textContent = label(k);
    const card = h('div', Object.assign({ class: 'card loopcard' + (o.cls ? ' ' + o.cls : '') }, o.attrs || {}), [
      o.head ? h('div', { class: 'lhd', text: o.head }) : null,
      o.sub ? h('div', { class: 'lsub', text: o.sub }) : null,
      view,
      h('div', { class: 'lbar' }, [back, play, fwd, when]),
      o.credit ? h('div', { class: 'lsrc', text: o.credit }) : null].filter(Boolean));
    const still = (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)
      || !!(navigator.connection && navigator.connection.saveData);
    if ('IntersectionObserver' in window) {
      io = new IntersectionObserver(es => es.forEach(e => {
        if (e.isIntersecting) { if (!still && !held) start(); } else if (timer) halt();
      }), { threshold: 0.35 });
      io.observe(card);
    }
    return { card, stop: () => { if (io) io.disconnect(); halt(); } };
  }
  // the loop's subtitle: the satellite, the hours the frames span, and their spacing
  function loopSub(satellite, frames) {
    const hours = Math.round((Date.parse(frames[frames.length - 1].t) - Date.parse(frames[0].t)) / 36e5);
    return satellite + ' GeoColor, ' + (hours >= 1 ? 'the last ' + hours + ' hour' + (hours === 1 ? '' : 's') : 'the newest frames')
      + ', a frame every ten minutes';
  }

  /* A cycle stamp in any of the forms the snapshots carry ('20261016T0600Z',
     '20261016T062634Z', '2026-10-16T17:00:00Z') as milliseconds, NaN for none. */
  function parseStamp(sid) {
    if (!sid) return NaN;
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?Z?$/.exec(sid);
    return Date.parse(m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6] || '00'}Z` : sid);
  }
  /* When a run was issued, in the least ink that still answers the question:
     the hour, a or p, and T or Y for whether that hour fell on the contract day
     being shown (dayIso) or the day before it (ydayIso), else the short date.
     The city chart's level labels and the landing map's hover both name runs
     this way, so a run reads the same in both places. */
  const MON3 = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function issuedTag(ms, tz, dayIso, ydayIso) {
    if (ms == null || isNaN(ms)) return '';
    const h = hourOf(ms, tz);
    const on = new Date(ms).toLocaleDateString('en-CA', { timeZone: tz });
    const d = on === dayIso ? 'T' : (on === ydayIso ? 'Y' : MON3[+on.slice(5, 7) - 1] + ' ' + (+on.slice(8, 10)));
    return (h % 12 || 12) + (h < 12 ? 'a' : 'p') + ' ' + d;
  }

  /* A forecast's line in miniature, for a hover box: its color, solid for a
     model's newest run and dotted for a run a newer one superseded, the same
     two styles the charts draw (owner, 2026-10-16). A grey halo under it keeps
     a dark color readable on the dark box and a light one on the light box. */
  function lineSwatch(col, dotted) {
    const d = dotted ? ' stroke-dasharray="2 3"' : '';
    return '<svg class="swl" width="16" height="6" viewBox="0 0 16 6" aria-hidden="true">'
      + '<line x1="1" y1="3" x2="15" y2="3" stroke="rgba(128,128,128,.7)" stroke-width="4.2"' + d + '/>'
      + '<line x1="1" y1="3" x2="15" y2="3" stroke="' + col + '" stroke-width="2.4"' + d + '/></svg>';
  }

  return {
    asset, compass, el, txt, h, $, clock, clockFull, dateShort, hourOf, minuteOf, hourTicks, P, chrome, statusEl, tooltip, param, deg, expander, cityHref, tempCities, weekdayOf, vendorPct,
    settleMph, satLoop, loopFresh, loopSub, lineSwatch, parseStamp, issuedTag };
})();
