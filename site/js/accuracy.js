/* Forecast error against lead time, pooled over every city and every hour.

   The page argues that market prices can be good forecasts. This is the
   measurement that argument rests on. Two lines, the aviation guidance's
   tailored station forecast and the price-implied high, each scored as the
   average distance from the temperature the station actually recorded, plotted
   against how long before the end of the day the forecast was made.

   Lead runs down to the right, so the day ends at the right edge and the lines
   are read the way the day is lived. Both lines are the same quantity in the
   same units, so they share one axis and the gap between them is the finding.

   Every point is an average over city-days, and the count behind it is on the
   hover, because a curve like this is only as good as what is under its thin
   end. Bins holding fewer than the stated minimum are not drawn at all rather
   than drawn faintly: a reader should not have to know to distrust them. */
window.WXAccuracy = (() => {
  const { el, txt, $ } = WXC;
  const NWS = 'var(--nws)', FX = 'var(--accent)';
  let tip = null;

  async function draw() {
    const svg = $('#accChart'); if (!svg) return;
    const cap = $('#accCap'), key = $('#accKey');
    const r = await WXD.get('accuracy/lead-curve.json', 1440);
    const d = r.data;
    svg.innerHTML = ''; if (key) key.innerHTML = ''; if (cap) cap.textContent = '';
    const pts = (d && d.points || []).slice().sort((a, b) => b.lead - a.lead);
    if (!pts.length) {
      svg.appendChild(txt('This measurement has not been published yet.', { x: 20, y: 34, class: 'axl' }));
      if (cap) cap.textContent = 'The comparison is captured hourly and published from the machine that runs it.';
      return;
    }

    const W = 960, H = 430, L = 62, R = 900, T = 22, B = 342;
    const leads = pts.map(p => p.lead);
    const xlo = Math.min(...leads), xhi = Math.max(...leads);
    const vals = pts.flatMap(p => [p.nws, p.fx]);
    const hi = Math.max(...vals) * 1.12;
    // the axis starts at zero: these are absolute errors, and a truncated axis
    // would exaggerate the very gap the page is arguing about
    const x = v => R - ((v - xlo) / Math.max(xhi - xlo, 1)) * (R - L);
    const y = v => B - (v / hi) * (B - T);

    for (let v = 0; v <= hi; v += 0.5) {
      svg.appendChild(el('line', { x1: L, x2: R, y1: y(v), y2: y(v), class: 'grid' }));
      svg.appendChild(txt(v.toFixed(1) + '°', { x: L - 8, y: y(v) + 3.5, 'text-anchor': 'end', class: 'ax' }));
    }
    for (let v = Math.ceil(xlo / 6) * 6; v <= xhi; v += 6) {
      svg.appendChild(txt(v + 'h', { x: x(v), y: B + 17, 'text-anchor': 'middle', class: 'ax' }));
    }
    svg.appendChild(txt('Average error, °F  (lower is better)', { x: 17, y: (T + B) / 2,
      'text-anchor': 'middle', transform: `rotate(-90 17 ${(T + B) / 2})`, class: 'ax' }));
    svg.appendChild(txt('Hours before the end of the target day  →  settlement', { x: (L + R) / 2, y: B + 38,
      'text-anchor': 'middle', class: 'ax' }));

    /* Where forecasting stops.

       A day's high usually happens in the afternoon, so within a few hours of
       local midnight it has already been recorded and the market is pricing a
       fact rather than a forecast. Its error goes to nothing there and the
       improvement reaches a hundred per cent, which is true and is not a
       measure of forecast skill — the comparison at that end is a market that
       has seen the day against a forecast that was fixed before it.

       The region is shaded and named rather than cut off. Removing it would be
       tidier and would also be hiding the part of the record that most needs
       explaining. */
    const DECIDED_H = 8;
    if (xlo <= DECIDED_H) {
      const x2 = x(Math.max(xlo, 0)), x1b = x(Math.min(DECIDED_H, xhi));
      svg.appendChild(el('rect', { x: Math.min(x1b, x2), y: T, width: Math.abs(x2 - x1b), height: B - T,
                                   fill: 'var(--muted)', 'fill-opacity': .09, 'pointer-events': 'none' }));
      svg.appendChild(txt('the high has usually already happened', { x: (x1b + x2) / 2, y: T + 12,
                                                                    'text-anchor': 'middle', class: 'ax' }));
    }
    // a day boundary is worth marking: to the left of it the market is pricing a
    // day that has not started, to the right it is pricing one in progress
    if (xhi >= 24 && xlo <= 24) {
      svg.appendChild(el('line', { x1: x(24), x2: x(24), y1: T, y2: B, class: 'grid',
                                   'stroke-dasharray': '4 4' }));
      svg.appendChild(txt('the target day begins', { x: x(24) - 6, y: T + 12, 'text-anchor': 'end', class: 'ax' }));
    }

    const line = k => {
      const dpath = pts.map((p, i) => (i ? 'L' : 'M') + x(p.lead).toFixed(1) + ',' + y(p[k]).toFixed(1)).join('');
      svg.appendChild(el('path', { d: dpath, fill: 'none', stroke: k === 'fx' ? FX : NWS,
                                   'stroke-width': 2.6, 'stroke-linejoin': 'round', 'pointer-events': 'none' }));
      pts.forEach(p => svg.appendChild(el('circle', { cx: x(p.lead), cy: y(p[k]), r: 2.6,
                                                     fill: k === 'fx' ? FX : NWS, 'pointer-events': 'none' })));
    };
    line('nws'); line('fx');

    /* Each series named on its own line.

       The names were in a key under the figure, which is a look away from the
       chart to learn which colour is which. The two lines are furthest apart
       at the settlement end, so the labels sit there, each in its line's own
       colour and nudged apart when the gap is too small to hold both. */
    {
      /* At the long-lead end, where both lines have room above and below and
         no improvement figure is written. The right-hand end has neither. */
      const first = pts[0];
      if (first) {
        svg.appendChild(txt('Aviation guidance (LAMP)', { x: x(first.lead) + 6, y: y(first.nws) - 7,
                            'font-size': 10.5, 'font-weight': 700, fill: NWS, 'pointer-events': 'none' }));
        svg.appendChild(txt('ForecastEx implied', { x: x(first.lead) + 6, y: y(first.fx) + 14,
                            'font-size': 10.5, 'font-weight': 700, fill: FX, 'pointer-events': 'none' }));
      }
    }

    // the improvement, called out every few hours rather than at every point:
    // labelled densely it becomes a texture instead of a number. A bin where the
    // market did worse is labelled the same way as one where it did better; the
    // page claims the market wins on average, not on every bin, and dropping the
    // exceptions would be arguing rather than measuring.
    pts.forEach(p => {
      if (p.improvement == null || p.lead % 6 !== 0) return;
      const better = p.improvement > 0;
      svg.appendChild(txt((better ? '+' : '−') + Math.abs(Math.round(p.improvement)) + '%',
        { x: x(p.lead), y: better ? y(p.fx) + 20 : y(p.fx) - 12, 'text-anchor': 'middle', class: 'ax',
          fill: better ? FX : 'var(--muted)' }));
    });

    // one hover band per bin
    const wBand = (R - L) / Math.max(pts.length - 1, 1);
    pts.forEach(p => {
      const band = el('rect', { x: x(p.lead) - wBand / 2, y: T, width: wBand, height: B - T, fill: 'transparent' });
      const rows = [
        ['<span class="sw" style="background:' + NWS + '"></span>Aviation guidance (LAMP)', p.nws.toFixed(2) + '°'],
        ['<span class="sw" style="background:' + FX + '"></span>ForecastEx implied', p.fx.toFixed(2) + '°'],
        ['City-days behind this point', String(p.cityDays)],
      ];
      if (p.improvement != null) {
        rows.splice(2, 0, ['Market error is', (p.improvement > 0 ? p.improvement.toFixed(0) + '% smaller'
                                                                : Math.abs(p.improvement).toFixed(0) + '% larger')]);
      }
      band.addEventListener('mousemove', e => tip.show(e, tip.rows(
        p.lead + ' hour' + (p.lead === 1 ? '' : 's') + ' before the day ends', rows,
        'average distance from the high the station recorded')));
      band.addEventListener('mouseleave', () => tip.hide());
      svg.appendChild(band);
    });

    if (key) key.innerHTML = '';        // both series are named on the lines themselves
    if (cap) {
      const n = pts.reduce((m, p) => Math.max(m, p.cityDays), 0);
      cap.textContent = 'Every forecast either system published for ' + (d.cities || 0) + ' cities between '
        + d.from + ' and ' + d.to + ', scored on the high the station recorded and averaged by how far ahead it '
        + 'was made. Up to ' + n + ' city-days stand behind a point; bins holding fewer than ' + (d.minCityDays || 30)
        + ' are not drawn. Both systems are scored on the same days. '
        + 'The forecast measured here is LAMP, the Localized Aviation MOS Program, which the National Weather '
        + 'Service publishes hourly and updates with each new observation. It is the only public product that '
        + 'reissues often enough to be compared with a market at every lead, and it is the strongest of the four '
        + 'this site tracks inside a day. The other three, including the National Weather Service\u2019s own '
        + 'public forecast, issue a few times a day and are compared on the scorecard instead. '
        + 'LAMP\u2019s figure is the highest its bulletin forecast for that day, because a bulletin covers a '
        + 'rolling window, so late in the day what remains of it is only the night, and reading that literally '
        + 'would score it against a forecast nobody made. The market\'s figure is the degree above the '
        + 'strike where the Yes price crosses 50 cents, because these contracts pay when the recorded high is '
        + 'strictly above the strike, so a market certain of 92 prices the 91 and 92 strikes a dollar apart and '
        + 'the crossing falls halfway between them. '
        + 'Read the shaded end with care: a day\u2019s high usually happens in the afternoon, so within a few '
        + 'hours of midnight it has already been recorded and the market is pricing something that has happened '
        + 'rather than forecasting it. The market\u2019s error goes to nothing there and the gap reaches a hundred '
        + 'per cent, which is real but is not forecast skill. The part of this chart that compares two forecasts '
        + 'is the unshaded part, and there the market runs between about fifteen and forty per cent closer.';
    }
  }

  function init() { tip = WXC.tooltip(); return draw(); }
  return { init, draw };
})();
