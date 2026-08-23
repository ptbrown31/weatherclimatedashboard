/* Snapshot access with two layers of graceful degradation.

   1. The CDN serves the last object a job wrote. If a job dies, the page
      still renders that object, with its as-of time visible and a stale
      state once the age exceeds twice the cadence.
   2. If the fetch itself fails (offline, CDN unreachable), the last good
      copy this browser saved is rendered, labelled as such. With neither,
      the page renders its frame and an explicit no-data state.

   Every result is {data, source: 'live'|'cache'|'none', asof, ageMin, stale}.
   The browser never calls a government endpoint; it reads only these files. */
window.WXD = (() => {
  const base = () => ((window.WX && window.WX.dataBaseUrl) || 'data').replace(/\/$/, '');
  const cadences = () => (window.WX && window.WX.cadenceMinutes) || { obs: 10, forecast: 30, hurricane: 30 };

  function wrap(data, source, cadenceMin, err) {
    const asof = data && data.asof ? Date.parse(data.asof) : null;
    const ageMin = asof ? (Date.now() - asof) / 6e4 : null;
    return { data, source, asof, ageMin, stale: ageMin == null || ageMin > 2 * cadenceMin, error: err ? String(err) : null };
  }

  async function get(key, cadenceMin) {
    cadenceMin = cadenceMin || cadences()[key.split('/')[0]] || 10;
    const url = `${base()}/snapshots/${key}`;
    const ck = 'wx:' + key;
    try {
      // No cache mode: the snapshots carry max-age=60 with stale-while-revalidate,
      // so the browser's own cache honours the same freshness the CDN does and a
      // second page in a session does not refetch data it loaded seconds ago.
      // Age is always computed from the payload's own `asof`, so a copy served
      // from cache still reports its true age in the status strip.
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + key);
      const data = await r.json();
      try { localStorage.setItem(ck, JSON.stringify({ at: Date.now(), data })); } catch (e) { /* quota: fine */ }
      return wrap(data, 'live', cadenceMin);
    } catch (e) {
      try {
        const c = JSON.parse(localStorage.getItem(ck) || 'null');
        if (c && c.data) return wrap(c.data, 'cache', cadenceMin, e);
      } catch (e2) { /* unreadable cache entry */ }
      return { data: null, source: 'none', asof: null, ageMin: null, stale: true, error: String(e) };
    }
  }

  // fetch several keys at once; returns {key: result}
  async function getAll(keys) {
    const out = {};
    const rs = await Promise.all(keys.map(k => get(k)));
    keys.forEach((k, i) => { out[k] = rs[i]; });
    return out;
  }

  return { get, getAll, base };
})();
