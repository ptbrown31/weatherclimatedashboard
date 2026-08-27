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

  const esc = s => String(s == null ? '' : s)
    .replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

  async function draw(station) {
    const host = $('#discussion'); if (!host) return;
    host.innerHTML = '';
    if (!idx) idx = (await WXD.get('discussion/index.json', 60)).data || {};
    const wfo = ((idx.stations) || {})[station];
    if (!wfo) {
      host.appendChild(h('p', { class: 'cap', text: 'No forecast office is mapped for this station. '
        + 'US government forecast products cover the United States; stations abroad have none here.' }));
      return;
    }
    const d = (await WXD.get('discussion/' + wfo + '.json', 60)).data;
    if (!d || !(d.body || d.text)) {
      host.appendChild(h('p', { class: 'cap', text: 'The discussion for this office has not been read yet.' }));
      return;
    }
    const det = h('details', { class: 'afd', open: 'open' });
    const sum = h('summary');
    sum.innerHTML = '<b>' + esc(d.source || 'National Weather Service') + '</b>'
      + (d.issued ? ' &middot; issued ' + esc(d.issued) : '')
      + ' &middot; <span class="afdmore">show or hide</span>';
    det.appendChild(sum);
    det.appendChild(h('pre', { class: 'afdtext', text: d.body || d.text }));
    const foot = h('p', { class: 'cap', style: 'margin-top:6px' });
    foot.innerHTML = 'Written by the forecaster on shift at the ' + esc(d.source || '') + ' office and carried '
      + 'here whole and unedited &mdash; a work of the United States government, in the public domain. '
      + 'This site did not write it and does not summarise it. '
      + '<a href="' + esc(d.url) + '" target="_blank" rel="noopener noreferrer">Read it on the '
      + 'National Weather Service site &rarr;</a>';
    host.appendChild(det);
    host.appendChild(foot);
  }
  return { draw };
})();
