/* Theme: the viewer's system setting by default; ?theme=light|dark on the
   URL stamps data-theme on the root so the embed can match its host. Runs
   before the body renders to avoid a flash. */
(() => {
  const t = new URLSearchParams(location.search).get('theme');
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
})();
