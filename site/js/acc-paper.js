/* The two pages at the top of the accuracy page.

   The research brief opens in a dialog that carries its download. Without a
   script, or with a modifier key held, the brief's link opens the PDF itself.
   The working paper is a plain link to its DOI and needs nothing here. The
   module draws on no data, so it runs whether or not the figures load. */
(() => {
  const dlg = document.getElementById('briefDlg');
  if (!dlg || typeof dlg.showModal !== 'function') return;
  const show = (e) => {
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    dlg.showModal();
  };
  ['briefOpen', 'briefEnlarge'].forEach((id) => {
    const t = document.getElementById(id);
    if (t) t.addEventListener('click', show);
  });
  // the body fills the dialog, so a click on the dialog itself is a click on the backdrop
  dlg.addEventListener('click', (e) => {
    if (e.target === dlg || e.target.closest('[data-close]')) dlg.close();
  });
})();
