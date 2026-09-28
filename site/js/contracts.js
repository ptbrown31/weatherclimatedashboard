/* The hourly temperature and wind contract panels, drawn the same way wherever
   they appear.

   The city page is the primary surface. A reader arrives at a station and the
   variables are tabs inside it, so the two area pages and the city page call
   the same renderers rather than keeping two versions of one card. */
window.WXK = (() => {
  const { h } = WXC;
  const KT_TO_MPH = 1.15078;
  const mph = kt => (kt == null ? null : Math.floor(kt * KT_TO_MPH + 0.5));
  const fmtMph = v => (v == null ? null : Math.round(v) + ' mph');
  const F = v => (v == null ? '\u2014' : Math.round(v * 10) / 10 + '\u00b0');
  const hourLabel = hr => (hr % 12 === 0 ? 12 : hr % 12) + ':00 ' + (hr < 12 ? 'AM' : 'PM');
  const localDay = (ms, tz) => new Intl.DateTimeFormat('en-CA',
    { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms));


  // ---- the plot, in the language of the city page's own chart: observations
  // heavy and dark, each guidance family in its own colour, the strike ladder
  // as Yes and No bars at the strike's height with the price inside
  const NS = 'http://www.w3.org/2000/svg';
  const E = (n, a) => { const e = document.createElementNS(NS, n); for (const k in (a || {})) e.setAttribute(k, a[k]); return e; };
  const T = (s, a) => { const e = E('text', a); e.textContent = s; return e; };
  const FAMS = [['nws', 'var(--nws)', 'NWS'], ['nbm', 'var(--nbm)', 'Blend'],
                ['lamp', 'var(--lamp)', 'LAMP'], ['mav', 'var(--mav)', 'MOS']];

  function plot(host, o) {
    const cols = o.ladders || (o.ladder ? [{ title: o.ladderTitle, rows: o.ladder, empty: o.ladderEmpty }] : []);
    const CW = cols.length > 2 ? 62 : 78, GAP = 26;
    /* Room for the forecast peaks, when a caller supplies them.

       A wind contract settles on the single highest value of the day, so the
       quantity a reader is deciding on is each guidance family's peak, not its
       trace. Those go in their own narrow column between the plot and the
       strike ladder, at the height of the value, so a peak and the strikes it
       clears line up on one axis and the comparison needs no arithmetic. */
    const PW = (o.peaks || []).length ? 58 : 0;
    const W = 960, H = o.height || 300, L = 42, R = (cols.length ? 16 + cols.length * (CW + GAP) : 16) + PW, TOP = 18,
          BOT = H - ((o.hourLadders || []).length ? 44 : 30);
    const svg = E('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'ts' });
    /* The hatch an anticipated rung is drawn with.

       A figure that is not a price must not read as one at a glance, and the
       site already has a visual word for that: the vendor rungs on the
       hurricane page are hatched and faded (`.vsoft` in the stylesheet). CSS
       gradients do not apply to SVG shapes, so the same idea is a pattern
       here, at the same 135 degrees and the same weight. */
    const HATCH = 'wxHatch';
    const defs = E('defs');
    const pat = E('pattern', { id: HATCH, width: 5, height: 5, patternUnits: 'userSpaceOnUse',
                               patternTransform: 'rotate(135)' });
    pat.appendChild(E('rect', { x: 0, y: 0, width: 5, height: 5, fill: 'transparent' }));
    pat.appendChild(E('rect', { x: 0, y: 0, width: 2, height: 5, fill: 'var(--panel)', 'fill-opacity': .55 }));
    defs.appendChild(pat);
    svg.appendChild(defs);
    // the range is what the window shows, so a stale row outside it does not
    // stretch the axis
    const inWin = p => p.t == null || (p.t >= o.t0 && p.t <= o.t1);
    const all = [].concat((o.obs || []).filter(inWin), (o.points || []).filter(inWin),
                          ...(o.series || []).map(s2 => (s2.pts || []).filter(inWin)),
                          (o.ladder || []).map(r => ({ v: r.strike })),
                          ...(o.hourLadders || []).map(hl => hl.rows.map(r => ({ v: r.strike }))));
    const vs = all.map(p => p.v).filter(v => v != null);
    if (!vs.length) return;
    let lo = Math.min(...vs), hi = Math.max(...vs);
    const pad = Math.max(2, (hi - lo) * 0.12);
    lo -= pad; hi += pad;
    const PX = W - R, L2X = L, x = t => L + (t - o.t0) / (o.t1 - o.t0) * (PX - L),
          y = v => BOT - (v - lo) / (hi - lo) * (BOT - TOP);

    // axes: value on the left, local hours along the foot
    const step = (hi - lo) > 40 ? 10 : (hi - lo) > 16 ? 5 : 2;
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svg.appendChild(E('line', { x1: L, x2: PX, y1: y(v), y2: y(v), stroke: 'var(--line)', 'stroke-width': .5 }));
      svg.appendChild(T(v + (o.unit || ''), { x: L - 6, y: y(v) + 3.5, class: 'ax', 'text-anchor': 'end' }));
    }
    WXC.hourTicks(o.t0, o.t1, o.tz, o.every || 6).forEach(tk => {
      svg.appendChild(E('line', { x1: x(tk.t), x2: x(tk.t), y1: TOP, y2: BOT, stroke: 'var(--line)', 'stroke-width': .5 }));
      svg.appendChild(T(tk.label, { x: x(tk.t), y: BOT + 14, class: 'ax', 'text-anchor': 'middle' }));
    });

    /* How far the legend reaches, so a mark under it labels itself at the foot
       instead. This was a fixed 150, which was true when the legend held two
       entries and put "sunrise" through the middle of "Sustained" once it held
       four. Same arithmetic the legend itself uses below. */
    const legW = (o.legend || []).reduce((a, g) => a + 26 + g.label.length * 6.0, L);

    // the day's own marks, in the city chart's colours: midnight and day end
    // plain, sunrise and sunset in the sun colour, now as a solid rule
    (o.marks || []).forEach(m => {
      if (m.t == null || m.t < o.t0 || m.t > o.t1) return;
      const a = { x1: x(m.t), x2: x(m.t), y1: TOP, y2: BOT, stroke: m.color || 'var(--muted)', 'stroke-width': .9 };
      if (m.dash) a['stroke-dasharray'] = m.dash;
      svg.appendChild(E('line', a));
      // the legend sits at the top left, so a mark under it labels itself at
      // the foot of its own line instead
      if (m.label) {
        const clear = x(m.t) > legW + 8;
        svg.appendChild(T(m.label, { x: x(m.t), y: clear ? TOP - 3 : BOT - 4, class: 'ax', 'text-anchor': 'middle' }));
      }
    });

    const line = (pts, stroke, width, dash, step) => {
      const ps = pts.filter(p => p.v != null && p.t >= o.t0 && p.t <= o.t1);
      let d = '';
      ps.forEach((p, i) => {
        const X = x(p.t).toFixed(1), Y = y(p.v).toFixed(1);
        if (!i) d += 'M' + X + ' ' + Y;
        else if (step) d += 'L' + X + ' ' + y(ps[i - 1].v).toFixed(1) + 'L' + X + ' ' + Y;
        else d += 'L' + X + ' ' + Y;
      });
      // a reading holds until the next one, so the last step runs to the edge
      if (step && ps.length) d += 'L' + x(Math.min(o.t1, Date.now())).toFixed(1) + ' ' + y(ps[ps.length - 1].v).toFixed(1);
      if (d) svg.appendChild(E('path', Object.assign({ d, fill: 'none', stroke, 'stroke-width': width },
                                                     dash ? { 'stroke-dasharray': dash } : {})));
    };
    (o.series || []).forEach(s2 => line(s2.pts, s2.color, s2.width || 1.2, s2.dash, s2.step));

    // a threshold drawn across the plot, solid once the day has cleared it
    (o.hlines || []).forEach(L => {
      const a = { x1: L2X, x2: PX, y1: y(L.v), y2: y(L.v), stroke: L.color || 'var(--muted)',
                  'stroke-width': L.cleared ? 1.3 : 0.8 };
      if (!L.cleared) a['stroke-dasharray'] = '5 3';
      svg.appendChild(E('line', a));
      svg.appendChild(T(L.label, { x: PX - 3, y: y(L.v) - 3, class: 'ax', 'text-anchor': 'end' }));
    });
    (o.points || []).filter(p => p.v != null && p.t >= o.t0 && p.t <= o.t1).forEach(p => {
      svg.appendChild(E('circle', { cx: x(p.t), cy: y(p.v), r: 2.4, fill: o.pointColor || 'var(--warm)' }));
      svg.appendChild(E('line', { x1: x(p.t), x2: x(p.t), y1: y(p.v), y2: BOT, stroke: o.pointColor || 'var(--warm)',
                                  'stroke-width': .6, opacity: .35 }));
    });
    if (o.obs && o.obs.length) {
      line(o.obs, 'var(--obs)', 2.1);
      o.obs.filter(p => p.v != null).forEach(p => svg.appendChild(
        E('circle', { cx: x(p.t), cy: y(p.v), r: 1.8, fill: 'var(--obs)' })));
    }

    /* The ladder columns, in the language of the city chart: one bar per
       strike at the strike's own height, Yes in green from the left and No in
       red for the rest, the price inside the bar it belongs to, and a strike
       with no bid drawn as an outline rather than as a fifty-cent price. */
    cols.forEach((col, ci) => {
      const LX = PX + PW + GAP + ci * (CW + GAP);
      if (col.title) svg.appendChild(T(col.title, { x: LX + CW / 2, y: TOP - 5, class: 'axl', 'text-anchor': 'middle' }));
      // a column the exchange has opened nothing in keeps its place and says
      // so, rather than the panel changing shape on the day a strike appears
      if (!(col.rows || []).length) {
        svg.appendChild(E('rect', { x: LX, y: TOP + 6, width: CW, height: BOT - TOP - 12, fill: 'transparent',
                                    stroke: 'var(--line)', 'stroke-width': .8, 'stroke-dasharray': '3 3' }));
        svg.appendChild(T(col.empty || 'none listed', { x: LX + CW / 2, y: (TOP + BOT) / 2, class: 'ax',
                                                        'text-anchor': 'middle' }));
      }
      (col.rows || []).forEach(r => {
        // the market layer has already taken the empty-book test on the raw
        // quote and handed the price over in cents
        const yy = y(r.strike), pc = (r.real && r.yes != null) ? r.yes : null;
        if (r.soft && pc != null) {
          /* Not a price. This is the desk's own probability for a contract the
             exchange has not opened, so it is hatched, carries no link to a
             book that does not exist, and is labelled as a percentage rather
             than in cents. It disappears the moment a real price exists,
             because the pipeline stops writing it. */
          const gw = pc / 100 * CW;
          svg.appendChild(E('rect', { x: LX, y: yy - 5.5, width: Math.max(gw, 1), height: 11,
                                      fill: 'var(--yes)', 'fill-opacity': .42 }));
          svg.appendChild(E('rect', { x: LX + gw, y: yy - 5.5, width: Math.max(CW - gw, 1), height: 11,
                                      fill: 'var(--no)', 'fill-opacity': .42 }));
          svg.appendChild(E('rect', { x: LX, y: yy - 5.5, width: CW, height: 11,
                                      fill: 'url(#' + HATCH + ')', stroke: 'var(--line)',
                                      'stroke-width': .8, 'stroke-dasharray': '3 2',
                                      'pointer-events': 'none' }));
          if (gw >= 24) svg.appendChild(T(pc + '%', { x: LX + 3, y: yy + 3.2, class: 'ladtxt' }));
        } else if (pc == null) {
          svg.appendChild(E('rect', { x: LX, y: yy - 5.5, width: CW, height: 11, fill: 'transparent',
                                      stroke: 'var(--line)', 'stroke-width': .8, 'stroke-dasharray': '3 2' }));
          svg.appendChild(T(r.yes != null ? 'no price' : 'no bids',
                            { x: LX + CW / 2, y: yy + 3.2, class: 'ax', 'text-anchor': 'middle' }));
        } else {
          const gw = pc / 100 * CW;
          const yb = E('rect', { x: LX, y: yy - 5.5, width: Math.max(gw, 1), height: 11,
                                 fill: 'var(--yes)', stroke: 'var(--panel)', 'stroke-width': .6 });
          const nb = E('rect', { x: LX + gw, y: yy - 5.5, width: Math.max(CW - gw, 1), height: 11,
                                 fill: 'var(--no)', stroke: 'var(--panel)', 'stroke-width': .6 });
          // both halves open that strike on the exchange, the way the daily
          // ladder's bars do, so a reader clicks the price they are looking at
          const url = o.strikeUrl ? o.strikeUrl(r, col) : null;
          [yb, nb].forEach(bar => {
            svg.appendChild(bar);
            if (url) WXM.linkTo(bar, url, 'Open ' + (r.label || ('above ' + r.strike)) + ' on IBKR');
          });
          if (gw >= 24) svg.appendChild(T(pc + '\u00a2', { x: LX + 3, y: yy + 3.2, class: 'ladtxt' }));
          if (CW - gw >= 24) svg.appendChild(T((100 - pc) + '\u00a2', { x: LX + CW - 3, y: yy + 3.2,
                                                                       class: 'ladtxt', 'text-anchor': 'end' }));
        }
        if (ci === 0) svg.appendChild(T('>' + r.strike + (o.unit || ''), { x: LX - 5, y: yy + 3.5,
                                                                          class: 'ax', 'text-anchor': 'end' }));
      });
    });

    /* The forecast peaks, one tick per family at the height of its highest
       forecast value for the day. Labels are nudged apart when two families
       land within a few pixels of each other, because two numbers written over
       one another are worse than one placed slightly off its own tick. */
    if (PW) {
      const placed = [];
      (o.peaks || []).slice().sort((a, b) => b.v - a.v).forEach(pk => {
        const yy = y(pk.v);
        svg.appendChild(E('line', { x1: PX + 3, x2: PX + 15, y1: yy, y2: yy,
                                    stroke: pk.color, 'stroke-width': 2 }));
        let ly = yy + 3.2;
        while (placed.some(q => Math.abs(q - ly) < 9)) ly += 9;
        placed.push(ly);
        svg.appendChild(T(Math.round(pk.v) + '', { x: PX + 18, y: ly, class: 'ax', fill: pk.color }));
      });
      svg.appendChild(T('Peak', { x: PX + 3, y: TOP - 5, class: 'axl' }));
    }

    /* An hourly board draws its ladder IN PLACE: each listed hour at its own
       position on the time axis, and each strike at its own height on the
       temperature axis.

       It used to draw a marker line here and stack the prices in columns down
       the right-hand side, which put every strike at the same height whatever
       temperature it named and left the reader matching a column heading to a
       dotted line by eye. A rung now sits where both of its coordinates say it
       should, directly under the forecast lines crossing that hour, so the
       question the board answers — what is this hour priced at against what
       the guidance says it will do — is one glance rather than two. */
    const HB = 34, HH = 9;                       // rung width and height
    /* The rungs live in their own layer, appended last.

       Drawn in place, they sat under the hover bands the plot lays across
       itself for the tooltip, and an SVG element later in the document takes
       the click. So every rung had its link, its pointer cursor and its label,
       and not one of them could be clicked: `elementFromPoint` over a rung
       returned `rect.hband`. The layer is raised above the bands at the end of
       the draw, which costs the time tooltip over the 34 by 9 pixels of a rung
       and gives back the contract it names. */
    const rungLayer = E('g', { class: 'hrungs' });
    (o.hourLadders || []).forEach(hl => {
      const cx = x(hl.t);
      svg.appendChild(E('line', { x1: cx, x2: cx, y1: TOP, y2: BOT, stroke: 'var(--muted)',
                                  'stroke-width': .7, 'stroke-dasharray': '2 3' }));
      svg.appendChild(T(hl.label, { x: cx, y: BOT + (hl.i % 2 ? 36 : 26), class: 'ax', 'text-anchor': 'middle' }));
      (hl.rows || []).forEach(r => {
        const yy = y(r.strike);
        if (!isFinite(yy)) return;
        const pc = (r.real && r.yes != null) ? r.yes : null;
        const x0 = cx - HB / 2;
        if (pc == null) {
          rungLayer.appendChild(E('rect', { x: x0, y: yy - HH / 2, width: HB, height: HH, fill: 'transparent',
                                            stroke: 'var(--line)', 'stroke-width': .7, 'stroke-dasharray': '3 2' }));
          return;
        }
        const gw = pc / 100 * HB;
        const yb = E('rect', { x: x0, y: yy - HH / 2, width: Math.max(gw, 1), height: HH,
                               fill: 'var(--yes)', stroke: 'var(--panel)', 'stroke-width': .5 });
        const nb = E('rect', { x: x0 + gw, y: yy - HH / 2, width: Math.max(HB - gw, 1), height: HH,
                               fill: 'var(--no)', stroke: 'var(--panel)', 'stroke-width': .5 });
        // both halves open that strike on the exchange, as every other ladder
        // on this site does
        const url = o.hourStrikeUrl ? o.hourStrikeUrl(r, hl) : null;
        [yb, nb].forEach(bar => {
          rungLayer.appendChild(bar);
          if (url) WXM.linkTo(bar, url, 'Open above ' + r.strike + (o.unit || '')
                                        + ' at ' + hl.label + ' on IBKR');
        });
      });
    });

    /* Hover. One band per reading across the plot, so a pointer anywhere over
       it answers with everything known at that time rather than only where a
       line happens to have a point. The caller supplies what to say, because
       only it knows what its series mean. */
    if (o.hoverAt) {
      const seen = {};
      [].concat((o.obs || []), (o.points || []), ...(o.series || []).map(s2 => s2.pts || []))
        .forEach(p => { if (p && p.t != null && p.t >= o.t0 && p.t <= o.t1) seen[p.t] = 1; });
      const ts = Object.keys(seen).map(Number).sort((a, b) => a - b);
      const tp = WXC.tooltip();
      ts.forEach((t, i) => {
        const xa = i ? (x(ts[i - 1]) + x(t)) / 2 : L;
        const xb = i < ts.length - 1 ? (x(t) + x(ts[i + 1])) / 2 : PX;
        const band = E('rect', { x: xa, y: TOP, width: Math.max(xb - xa, 1), height: BOT - TOP,
                                 fill: 'transparent', class: 'hband' });
        const html = () => o.hoverAt(t);
        band.addEventListener('mousemove', e => tp.show(e, html()));
        band.addEventListener('mouseleave', () => tp.hide());
        svg.appendChild(band);
      });
    }

    // the legend names each line, in its own colour
    let lx = L;
    (o.legend || []).forEach(g => {
      svg.appendChild(E('line', { x1: lx, x2: lx + 14, y1: TOP - 6, y2: TOP - 6, stroke: g.color, 'stroke-width': 2 }));
      const t = T(g.label, { x: lx + 18, y: TOP - 2.5, class: 'ax' });
      svg.appendChild(t);
      // the swatch, the label and a gap. The per-character figure has to clear
      // the widest label the panel uses, not the average one, or a long entry
      // is written over by the next swatch
      lx += 26 + g.label.length * 6.0;
    });
    // raised above the hover bands, so a rung can actually be clicked
    if (rungLayer.childNodes.length) svg.appendChild(rungLayer);
    const card = document.createElement('div');
    card.className = 'card';
    card.appendChild(svg);
    host.appendChild(card);
  }

  function priceCell(r) {
    if (r.real && r.yes != null) return r.yes + '\u00a2';
    return r.yes != null ? 'no price' : '\u2014';
  }

  function ladder(rows, label) {
    const t = h('table', { class: 'lad' });
    t.appendChild(h('tr', {}, [h('th', {}, label), h('th', {}, 'Yes bid'), h('th', {}, 'No bid'), h('th', {}, 'Price')]));
    rows.slice().sort((a, b) => b.strike - a.strike).forEach(r => {
      t.appendChild(h('tr', {}, [
        h('td', {}, (label === 'Strike' ? 'Above ' + r.strike + '\u00b0' : 'Above ' + r.strike + ' mph')),
        h('td', {}, r.bid == null ? '\u2014' : r.bid + '\u00a2'),
        h('td', {}, r.noBid == null ? '\u2014' : r.noBid + '\u00a2'),
        h('td', {}, priceCell(r))]));
    });
    return t;
  }

  /* The observation that settles an hour: the last report in the hour ending at
     the listed time. A report exactly an hour before belongs to the hour
     before; a report exactly at the time counts. */
  function settling(rows, dayISO, hour, tz) {
    const inHour = (rows || []).filter(r => {
      const ms = Date.parse(r.t);
      if (!isFinite(ms)) return false;
      const onHour = ms % 36e5 === 0;
      const hr = onHour ? (WXC.hourOf(ms, tz) === 0 ? 24 : WXC.hourOf(ms, tz)) : WXC.hourOf(ms, tz) + 1;
      return hr === hour && localDay(onHour ? ms - 1 : ms, tz) === dayISO;
    });
    if (!inHour.length) return null;
    const last = inHour[inHour.length - 1];
    return { v: last.tempF, t: Date.parse(last.t), type: last.type, n: inHour.length };
  }

  // the guidance for each hour, from the file every station carries
  function guidance(fc, tz) {
    const out = [];
    [['nbm', 'Blend'], ['lamp', 'LAMP'], ['nws', 'NWS']].forEach(([k, name]) => {
      (((fc || {})[k] || {}).hourly || []).forEach(r => {
        const ms = Date.parse(r.t);
        if (!isFinite(ms) || r.tempF == null) return;
        const day = localDay(ms, tz), hour = WXC.hourOf(ms, tz);
        if (!out.some(g => g.day === day && g.hour === hour)) out.push({ day, hour, v: r.tempF, src: name });
      });
    });
    return out;
  }

  /* The day marks the city chart draws, from the same snapshot field: the
     start and end of the contract day, sunrise and sunset, and now. */
  function marksFor(fc, tz) {
    const m = (fc || {}).markers || {};
    const P = v => (v ? Date.parse(v) : null);
    const out = [];
    if (m.dayStart) out.push({ t: P(m.dayStart), label: 'midnight', color: 'var(--muted)' });
    if (m.sunrise) out.push({ t: P(m.sunrise), label: 'sunrise', color: '#e0a020', dash: '3 3' });
    if (m.sunset) out.push({ t: P(m.sunset), label: 'sunset', color: '#e0a020', dash: '3 3' });
    if (m.dayEnd) out.push({ t: P(m.dayEnd), label: 'day end', color: 'var(--muted)' });
    out.push({ t: Date.now(), color: 'var(--obs)', dash: '1 3' });
    return out;
  }

  function hourCard(hr, obsRows, guide, tz) {
    const box = h('div', { class: 'card' }, [
      h('div', { class: 'hd' }, [h('strong', {}, 'Hour ending ' + hourLabel(hr.hour)),
                                 h('span', { class: 'cap' }, ' ' + hr.day)]),
      ladder(hr.rows, 'Strike')]);
    const st = settling(obsRows, hr.day, hr.hour, tz);
    const g = guide.find(x => x.hour === hr.hour && x.day === hr.day);
    const bits = [];
    if (st) {
      const whole = Math.floor(st.v + 0.5);
      bits.push('Settles on ' + F(st.v) + (Math.abs(st.v - whole) > 0.05 ? ', which compares as ' + whole + '\u00b0' : '')
                + ' at ' + WXC.clockFull(st.t, tz));
    } else bits.push('No report in this hour yet');
    if (g) bits.push(g.src + ' ' + F(g.v));
    box.appendChild(h('p', { class: 'cap' }, bits.join(' \u00b7 ')));
    return box;
  }

  // every listed hour for one station, drawn like the city page's own plot:
  // the day's observations and guidance, with each listed hour's ladder
  // standing at the hour it measures
  function hours(host, city, board, obs, fc) {
    const tz = city.tz, guide = guidance(fc, tz);
    const rows = ((obs || {}).rows || []).map(r => ({ t: Date.parse(r.t), v: r.tempF })).filter(p => isFinite(p.t));
    const hl = board.hours.map(hr => ({
      t: Date.parse(hr.day + 'T00:00:00Z') + 0,  // replaced below with the local hour
      day: hr.day, hour: hr.hour, rows: hr.rows,
      label: (hr.hour % 12 === 0 ? 12 : hr.hour % 12) + (hr.hour < 12 ? 'a' : 'p'),
    }));
    hl.forEach((x, i) => { x.i = i; });
    // the hour's own instant, the end of the hour, in the station's clock
    hl.forEach(x => {
      const noon = Date.parse(x.day + 'T12:00:00Z');
      const off = (WXC.hourOf(noon, tz) - 12) * 36e5;
      x.t = Date.parse(x.day + 'T00:00:00Z') - off + x.hour * 36e5;
    });
    // the window is the listed hours' own days, midnight to the last hour, as
    // the city chart covers a day or two rather than the whole record
    const hts = hl.map(x => x.t);
    const t1 = Math.max(...hts) + 2 * 36e5;
    const firstDay = hl.map(x => x.day).sort()[0];
    const noon = Date.parse(firstDay + 'T12:00:00Z'), off = (WXC.hourOf(noon, tz) - 12) * 36e5;
    const t0 = Date.parse(firstDay + 'T00:00:00Z') - off;
    const series = FAMS.map(([k, col, name]) => ({
      color: col, label: name, dash: '4 3',
      pts: (((fc || {})[k] || {}).hourly || []).map(r => ({ t: Date.parse(r.t), v: r.tempF })),
    })).filter(s2 => s2.pts.some(p => p.v != null));
    // the same hover the wind panel gets: the observation at that time and every
    // family's figure for it side by side, which is the comparison a reader is
    // making by eye across four dashed lines
    const hoverAt = t => {
      const ob = rows.find(p => p.t === t);
      const rs = [['Observed', ob && ob.v != null ? F(ob.v) : null]];
      series.forEach(s2 => {
        const p = s2.pts.find(q => q.t === t);
        if (p && p.v != null) rs.push([s2.label, F(p.v)]);
      });
      const here = hl.find(x => Math.abs(x.t - t) < 18e5);
      if (here) {
        const best = (here.rows || []).slice().sort((a, b) => b.strike - a.strike)
          .find(r => r.real && r.yes != null);
        rs.push(['Listed hour', hourLabel(here.hour) + ' \u00b7 ' + here.rows.length + ' strikes']);
        if (best) rs.push(['Highest priced strike', 'above ' + best.strike + '\u00b0, Yes '
          + best.yes + '\u00a2']);
      }
      return WXC.tooltip().rows(WXC.clockFull(t, tz), rs,
        'the hour settles on the last report inside it');
    };
    const hmkt = (board && board.market) || null;
    const hourStrikeUrl = r => (hmkt && hmkt.productConid && (r.conidYes || r.conid)
      ? WXM.contractUrl(hmkt.productConid, r.conidYes || r.conid) : null);
    plot(host, { t0, t1, tz, unit: '\u00b0', obs: rows, series, hourLadders: hl, every: 3, height: 320,
                 marks: marksFor(fc, tz), hoverAt, hourStrikeUrl,
                 legend: [{ color: 'var(--obs)', label: 'Observed' }].concat(series.map(s2 => ({ color: s2.color, label: s2.label }))) });

    const lines = board.hours.map(hr => {
      const st = settling((obs || {}).rows || [], hr.day, hr.hour, tz);
      const g = guide.find(x => x.hour === hr.hour && x.day === hr.day);
      const bits = ['Hour ending ' + hourLabel(hr.hour)];
      if (st) {
        const whole = Math.floor(st.v + 0.5);
        bits.push('settles on ' + F(st.v) + (Math.abs(st.v - whole) > 0.05 ? ', which compares as ' + whole + '\u00b0' : '')
                  + ' at ' + WXC.clockFull(st.t, tz));
      } else if (g) bits.push(g.src + ' ' + F(g.v));
      else bits.push('no report yet');
      return bits.join(', ');
    });
    host.appendChild(h('p', { class: 'cap' }, lines.join(' \u00b7 ')));
    host.appendChild(h('p', { class: 'cap' }, board.label + (board.symbol ? ' \u00b7 ' + board.symbol : '')));
  }

  // the day's peak so far, across both columns, and the gust still forecast
  function peak(obs) {
    const t = ((obs || {}).wind || {}).today;
    if (!t || !t.peak) return null;
    return Object.assign({}, t.peak, { t: Date.parse(t.peak.t), speed: t.speed, gust: t.gust });
  }
  function gustAhead(fc, tz) {
    const now = Date.now();
    let best = null;
    [['nbm', 'Blend'], ['lamp', 'LAMP'], ['nws', 'NWS']].forEach(([k, name]) => {
      (((fc || {})[k] || {}).hourly || []).forEach(r => {
        const ms = Date.parse(r.t);
        if (!isFinite(ms) || ms < now || r.gust == null) return;
        if (!best || r.gust > best.kt) best = { kt: r.gust, t: ms, src: name };
      });
    });
    return best;
  }

  /* What the two columns are, in the station's own terms.

     Every figure here is quoted from the NWS ASOS User's Guide, section 3.2.2,
     rather than described from memory, because a reader comparing this panel
     against a settlement is entitled to know exactly which quantity each
     column holds. The gust criterion in particular is not the one people
     usually cite: the ten-knot test is between the gust and the LULL, not
     between the gust and the sustained wind. */
  function windNote() {
    const d = h('div', { class: 'prose wnote' });
    d.innerHTML = '<h2>Sustained wind and gusts</h2>'
      + '<ul>'
      + '<li>The sustained wind is a two-minute average, recomputed every five seconds, and it is '
      + 'reported in every observation. At 2 knots or less the station reports calm.</li>'
      + '<li>A gust has to clear two stages. A station registers one only while the sustained wind is '
      + 'at least 9 knots and a five-second average in the past minute runs 5 knots above it; that '
      + 'value is then held for ten minutes.</li>'
      + '<li>A held gust is reported only when it is at least 3 knots above the current sustained '
      + 'wind, the reported wind is above 2 knots, and it is at least 10 knots above the lowest '
      + 'five-second wind in the same ten minutes. The lowest gust a station will report is 14 knots.</li>'
      + '<li>The first stage is why a calm day carries no gust column at all: below a sustained 9 '
      + 'knots, about 10 miles per hour, no gust is registered however gusty the air is. An hour with '
      + 'no gust is a steady wind rather than a missing reading, and the contract settles on the '
      + 'larger of the two columns, which on such a day is the sustained wind.</li>'
      + '<li>Neither figure is an hourly accumulation. Each is measured in a window ending at the '
      + 'observation, two minutes for the sustained wind and ten for the gust, so a spike between two '
      + 'observations can go unrecorded by both.</li>'
      + '<li>The peak wind remark a report sometimes carries is a different quantity again, the '
      + 'greatest five-second average above 25 knots since the last hourly report. It is not in the '
      + 'settlement table and does not settle the contract.</li>'
      + '</ul>'
      + '<p class="cap">Definitions from the National Weather Service ASOS User\u2019s Guide, section 3.2.2. '
      + 'Readings on this page come from the aviation weather feed, the same station reports the '
      + 'settlement table is built from, and are converted from knots at 1.15078 miles per hour.</p>';
    return d;
  }

  function wind(host, city, obs, fc, board) {
    const tz = city.tz;
    const today = ((obs || {}).wind || {}).today || {};
    /* The board decides the day, not the observation record. Tomorrow's wind
       contract is listed during today, and keying the window off the latest
       observation drew that board over today's readings. A future day has no
       readings at all, which is the honest picture of it. */
    const dayISO = (board && board.day) || today.date || ((obs || {}).today || {}).date;
    const isToday = dayISO === (today.date || ((obs || {}).today || {}).date);
    const toMph = v => (v == null ? null : v * KT_TO_MPH);
    if (dayISO) {
      const noon = Date.parse(dayISO + 'T12:00:00Z'), off = (WXC.hourOf(noon, tz) - 12) * 36e5;
      const t0 = Date.parse(dayISO + 'T00:00:00Z') - off, t1 = t0 + 24 * 36e5;
      // the peak is the day's own running maximum, so the rows before the day
      // began are left out rather than carried into it. The wind block's rows
      // are every report of the day; the chart rows leave out any report with
      // no temperature, and a station whose temperature sensor is out goes on
      // reporting the wind that settles
      const rows = (isToday ? (today.rows || (obs || {}).rows || []) : [])
        .map(r => ({ t: Date.parse(r.t), sp: r.wspd, gu: r.wgst }))
        .filter(p => isFinite(p.t) && p.t >= t0 && p.t <= t1);
      const sust = rows.map(p => ({ t: p.t, v: toMph(p.sp) }));
      const gust = rows.map(p => ({ t: p.t, v: toMph(p.gu) })).filter(p => p.v != null);
      let run = null;
      const peakLine = rows.map(p => {
        const v = Math.max(p.sp == null ? -1 : toMph(p.sp), p.gu == null ? -1 : toMph(p.gu));
        if (v >= 0) run = run == null ? v : Math.max(run, v);
        return { t: p.t, v: run };
      });
      /* The forward line is the HIGHER of the two forecast columns, not the
         gust alone.

         Settlement takes the largest value across the sustained wind and the
         gust, and a station only reports a gust when it beats the sustained by
         ten knots, so on a quiet day the gust column is empty and the day
         settles on the sustained wind. Measured over 9,210 station-days since
         10 June: the sustained wind decides 38% of days overall, 81% of days
         that settle between 10 and 19 mph, and every day below 10; above 30
         mph it decides none of 1,561. A gust-only line is therefore honest
         exactly when the day is windy and reads high when it is calm, which is
         the opposite of what a line that is always drawn suggests. */
      /* Every family that forecasts wind gets a line, the way the temperature
         panel gives each one a line.

         This used to keep exactly one. That made the wind panel far thinner
         than the temperature panel beside it, and for no good reason: of the
         four families, the Blend and LAMP both publish an hourly gust, and the
         Blend publishes a standard deviation with it. Only GFS MOS carries no
         gust at all, and the forecast office's hourly product turns out not to
         publish one either, though its raw gridpoint does. Nothing here is
         scaled or inferred: a family is drawn on the column it publishes, and
         one that offers only the sustained wind says so in its own label. */
      const fams = [];
      FAMS.forEach(([k, color, name]) => {
        const pts = (((fc || {})[k] || {}).hourly || []).map(r => {
          const v = Math.max(r.gust == null ? -1 : toMph(r.gust), r.wspd == null ? -1 : toMph(r.wspd));
          return v >= 0 ? { t: Date.parse(r.t), v, gust: r.gust, wspd: r.wspd } : null;
        }).filter(Boolean);
        if (!pts.length) return;
        fams.push({ k, color, name, pts, hasGust: pts.some(p => p.gust != null) });
      });
      /* The lead family, for the sentence under the panel and the hover.
         A family that forecasts a gust wins over one that carries only the
         sustained wind, whatever order they sit in, because above 30 mph the
         gust is what settles on essentially every day. */
      const lead = fams.slice().sort((a, b) => (b.hasGust ? 1 : 0) - (a.hasGust ? 1 : 0))[0] || null;
      const fcGust = lead ? lead.pts : [], fcSrc = lead ? lead.name : null,
            fcHasGust = !!(lead && lead.hasGust);
      const series = [{ pts: sust, color: 'var(--sust)', width: 1.3, step: true },
                      { pts: peakLine, color: 'var(--obs)', width: 2.1, step: true }];
      fams.forEach(f => series.push({ pts: f.pts, color: f.color, width: 1.3, dash: '4 3' }));
      /* Each family's highest forecast value inside the contract day. That is
         the quantity the contract settles on, so it sits beside the ladder
         rather than being left for a reader to find by eye along a trace. */
      const peaks = fams.map(f => {
        const inDay = f.pts.filter(p => p.t >= t0 && p.t <= t1);
        if (!inDay.length) return null;
        return { v: inDay.reduce((a, b) => (b.v > a.v ? b : a)).v, color: f.color, label: f.name };
      }).filter(Boolean);
      // what a reader is actually asking: is the day's peak already in. The
      // honest version from public guidance is a comparison, not a likelihood
      const ahead = fcGust.filter(p => p.t > Date.now() && p.t <= t1);
      const aheadTop = ahead.length ? ahead.reduce((a, b) => (b.v > a.v ? b : a)) : null;
      const aheadMax = aheadTop ? aheadTop.v : null;
      // each listed threshold across the plot, solid once the day has cleared it
      const nowPeak = run;
      /* The rungs this panel draws: the exchange's ladder where there is one,
         and otherwise the desk's anticipated ladder, marked soft so every
         renderer below treats it as a figure rather than a price. The two are
         never both present; the pipeline writes the desk's block only for a
         day the exchange has not quoted. */
      const anticip = (board && !board.listed && board.anticipated) ? board.anticipated : null;
      const rungs = (board && board.listed) ? board.rows
        : (anticip ? anticip.rows.map(r => ({ strike: r.strike, yes: Math.round(r.p * 100),
                                              real: true, soft: true })) : []);
      const hlines = rungs.map(r => ({
        v: r.strike, cleared: nowPeak != null && nowPeak > r.strike,
        color: (nowPeak != null && nowPeak > r.strike) ? 'var(--yes)' : 'var(--muted)',
        label: r.strike + ' mph' + (nowPeak != null && nowPeak > r.strike ? ', cleared' : ''),
      }));
      const at = (arr, t) => (arr.find(p => p.t === t) || {});
      const hoverAt = t => {
        // an hour with no report is not an hour that reported no gust, so the
        // observed rows appear only where there is an observation to describe
        const o2 = rows.find(p => p.t === t);
        const f = at(fcGust, t);
        const rs = o2 ? [['Peak so far', fmtMph(at(peakLine, t).v)],
                         ['Sustained', fmtMph(toMph(o2.sp)), o2.sp == null ? null : o2.sp + ' kt'],
                         ['Gust', o2.gu == null ? 'none reported' : fmtMph(toMph(o2.gu)),
                          o2.gu == null ? null : o2.gu + ' kt']]
                      : [['Observation', 'none at this hour']];
        if (f.v != null) {
          rs.push([fcSrc + ' forecast', fmtMph(f.v)]);
          rs.push(['  of which gust', f.gust == null ? 'none forecast' : fmtMph(toMph(f.gust))]);
          rs.push(['  sustained', fmtMph(toMph(f.wspd))]);
        }
        const near = rungs.slice().sort((a, b) => a.strike - b.strike)
          .find(r => nowPeak == null || r.strike >= nowPeak);
        if (near) rs.push(['Next threshold', 'above ' + near.strike + ' mph'
          + (near.soft ? ', ' + near.yes + '% estimated'
             : (near.real && near.yes != null ? ', Yes ' + near.yes + '\u00a2' : ', no bids'))]);
        return WXC.tooltip().rows(WXC.clockFull(t, tz),
          rs.filter(r => r[1] != null).map(r => [r[0], r[2] ? r[1] + ' \u00b7 ' + r[2] : r[1]]),
          o2 && o2.gu == null && o2.sp != null
            ? 'a steady wind, not a missing reading: a station reports a gust only when the ten '
              + 'minutes before the observation were gusty enough to qualify'
            : 'settles on the larger of the two columns, in whole miles per hour');
      };
      const mkt = (board && board.market) || null;
      const strikeUrl = r => (mkt && mkt.productConid && (r.conidYes || r.conid)
        ? WXM.contractUrl(mkt.productConid, r.conidYes || r.conid) : null);
      plot(host, { t0, t1, tz, unit: '', series, points: gust, pointColor: 'var(--warm)', hlines,
                   marks: marksFor(fc, tz), hoverAt, strikeUrl,
                   ladder: rungs.length ? rungs : (board ? board.rows : null),
                   ladderTitle: 'Thresholds (mph)' + (anticip ? ' \u00b7 estimated' : ''),
                   ladderEmpty: 'no strikes listed',
                   every: 3, height: 300,
                   peaks,
                   legend: [{ color: 'var(--obs)', label: 'Peak so far (mph)' },
                            { color: 'var(--warm)', label: 'Gusts' },
                            { color: 'var(--sust)', label: 'Sustained' }].concat(
                              fams.map(f => ({ color: f.color,
                                               label: f.name + (f.hasGust ? ' gust' : ' sustained') }))) });
      if (aheadMax != null) {
        host.appendChild(h('p', { class: 'cap' }, (nowPeak != null && aheadMax <= nowPeak
          ? fcSrc + ' guidance for the remaining hours is below today\u2019s peak so far, '
            + fmtMph(aheadMax) + ' against ' + fmtMph(nowPeak) + '.'
          : fcSrc + ' guidance reaches ' + fmtMph(aheadMax) + ' at ' + WXC.clockFull(aheadTop.t, tz)
            + (nowPeak != null ? ', against ' + fmtMph(nowPeak) + ' so far' : '') + '.')
          + (fcHasGust ? '' : ' ' + fcSrc + ' carries no gust column, so this is its sustained wind.')));
      }
    }
    const pk = isToday ? peak(obs) : null, bits = [];
    if (pk) bits.push((pk.mph != null ? pk.mph : mph(pk.v)) + ' mph peak so far, from the '
      + (pk.from === 'gust' ? 'gust' : 'sustained wind') + ' column at ' + WXC.clockFull(pk.t, tz)
      + ', ' + pk.v + ' kt as reported');
    else bits.push(isToday ? 'no wind reading on record today' : 'no readings yet, the day has not begun');
    host.appendChild(h('p', { class: 'cap' }, bits.join(' \u00b7 ')));
    const soft = (board && !board.listed && board.anticipated) ? board.anticipated : null;
    host.appendChild(h('p', { class: 'cap' }, (board && board.listed)
      ? 'Trades to ' + board.lastTrade + '. Resolves ' + board.resolves + '.'
      : (soft
        /* Where the figure comes from is nobody's business on a public page.
           The reader needs two things: that this is an estimate rather than a
           price, and that the exchange's own prices take over the moment the
           contract lists. Naming the source adds nothing to either, and the
           feed's `method` label is not printed for the same reason. */
        ? 'Estimated, contract not yet listed. The hatched ladder is replaced by the exchange\u2019s '
          + 'prices as soon as the contract lists.'
        : 'The exchange has not opened this contract.')));
  }

  return { hours, wind, windNote, plot, ladder, settling, guidance, peak, gustAhead, mph };
})();
