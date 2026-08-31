/* The forecaster's own reasoning for this station's area.

   Every National Weather Service office publishes an Area Forecast Discussion:
   a few hundred words from the forecaster on shift about what the models are
   doing, where they disagree, and what the office decided. On a page that shows
   a market disagreeing with a forecast, it is the obvious next question, and it
   is the only place the reasoning is written down in words.

   It is shown whole and unedited, folded shut because it runs to several
   thousand characters, with the office named, the issuance time given, and a
   link to the office's own page. It is a US government work in the public
   domain; the attribution is there because a reader should know whose words
   these are, not because the licence demands it. */
window.WXDiscussion = (() => {
  const { h, $ } = WXC;
  let idx = null;
  /* Each draw awaits a fetch, so two in flight can finish out of order and
     the slower one repaints over the newer request. Toggling between the day
     and its postmortem is exactly that case, and it leaves the wrong
     reasoning on screen beside the right numbers. Every draw takes a ticket
     and paints only if it is still the newest. */
  let seq = 0;

  const esc = s => String(s == null ? '' : s)
    .replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

  /* The discussion that stood at one moment.

     Asked for a moment, this reaches for the office's recent issuances and
     takes the newest one this site had read by then. A postmortem drawn
     against the forecast that stood at six the evening before deserves the
     reasoning that stood then too; today's words over yesterday's numbers
     would be the wrong pairing, and quietly so. Where the window does not
     reach back that far it says which discussion it is showing instead of
     letting the reader assume. */
  async function standingAt(wfo, atMs) {
    const p = (await WXD.get('discussion/' + wfo + '-past.json', 60).catch(() => null));
    const list = (p && p.data && p.data.issuances) || [];
    const held = list.filter(x => Date.parse(x.seen) <= atMs)
      .sort((a, b) => Date.parse(b.seen) - Date.parse(a.seen))[0];
    return held || null;
  }

  async function draw(station, atMs) {
    const host = $('#discussion'); if (!host) return;
    const mine = ++seq;
    const stale = () => mine !== seq;
    host.innerHTML = '';
    if (!idx) idx = (await WXD.get('discussion/index.json', 60)).data || {};
    if (stale()) return;
    const wfo = ((idx.stations) || {})[station];
    if (!wfo) {
      host.appendChild(h('p', { class: 'cap', text: 'No forecast office is mapped for this station. '
        + 'US government forecast products cover the United States; stations abroad have none here.' }));
      return;
    }
    const now = (await WXD.get('discussion/' + wfo + '.json', 60)).data;
    const held = atMs ? await standingAt(wfo, atMs) : null;
    if (stale()) return;
    const d = held || now;
    if (!d || !(d.body || d.text)) {
      host.appendChild(h('p', { class: 'cap', text: 'The discussion for this office has not been read yet.' }));
      return;
    }
    const missed = atMs && !held;
    const det = h('details', { class: 'afd', open: 'open' });
    const sum = h('summary');
    sum.innerHTML = '<b>' + esc(d.source || (d.office ? 'National Weather Service, ' + d.office : 'National Weather Service')) + '</b>'
      + (d.issued ? ' &middot; issued ' + esc(d.issued) : '')
      + (held ? ' &middot; <span class="tk">standing at the moment scored</span>' : '')
      + ' &middot; <span class="afdmore">show or hide</span>';
    det.appendChild(sum);
    det.appendChild(h('pre', { class: 'afdtext', text: d.body || d.text }));
    if (missed) {
      host.appendChild(h('p', { class: 'cap', style: 'margin:0 0 6px',
        text: 'The standing discussion is shown; this office\u2019s recent issuances do not reach back to '
              + 'the moment the postmortem scores. The window fills as the office publishes.' }));
    }
    const foot = h('p', { class: 'cap', style: 'margin-top:6px' });
    foot.innerHTML = 'Written by the forecaster on shift at the ' + esc(d.source || d.office || '') + ' office and carried '
      + 'here whole and unedited, a work of the United States government in the public domain. '
      + 'This site did not write it and does not summarise it. '
      + '<a href="' + esc(d.url) + '" target="_blank" rel="noopener noreferrer">Read it on the '
      + 'National Weather Service site &rarr;</a>';
    host.appendChild(det);
    host.appendChild(foot);
  }
  /* The same treatment for a storm.

     The National Hurricane Center writes a Tropical Cyclone Discussion for
     each active system, which is the hurricane counterpart of the office
     discussion a city page carries. The storm snapshot holds it, so this only
     has to lay it out. */
  function drawStorms(storms, basinName) {
    const host = $('#stormDiscussion'); if (!host) return;
    host.innerHTML = '';
    const where = basinName ? ' in the ' + basinName : '';
    const withText = (storms || []).filter(s => s.discussion && s.discussion.text);
    if (!withText.length) {
      host.appendChild(h('p', { class: 'cap', text: (storms || []).length
        ? 'No discussion has been issued yet for the active storms' + where + '.'
        : 'No active storms' + where + ', so there is no discussion to carry.' }));
      return;
    }
    withText.forEach(s => {
      const d = s.discussion;
      const det = h('details', { class: 'afd', open: withText.length === 1 ? 'open' : null });
      const sum = h('summary');
      sum.innerHTML = '<b>' + esc(s.name || s.id) + '</b> &middot; ' + esc(d.source || 'National Hurricane Center')
        + (d.issued ? ' &middot; issued ' + esc(String(d.issued).replace('T', ' ').replace(/\+.*$/, ' UTC')) : '')
        + ' &middot; <span class="afdmore">show or hide</span>';
      det.appendChild(sum);
      det.appendChild(h('pre', { class: 'afdtext', text: d.text }));
      host.appendChild(det);
    });
    const foot = h('p', { class: 'cap', style: 'margin-top:6px' });
    foot.innerHTML = 'Written by the specialist on shift at the National Hurricane Center and carried here whole '
      + 'and unedited, a work of the United States government in the public domain. '
      + '<a href="https://www.nhc.noaa.gov/cyclones/">Read the originals on the National Hurricane Center site &rarr;</a>';
    host.appendChild(foot);
  }

  return { draw, drawStorms };
})();
