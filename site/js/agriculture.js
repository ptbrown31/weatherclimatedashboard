/* The agriculture tab.

   Drawn by the shared category-panels code; what is particular to crops is the
   window and the rounding. Yields are published to two decimals and the strikes
   are set on the same grid, so two decimals everywhere.

   The window is deliberately short. The record reaches to 1961, but the vertical
   scale is set by whatever is drawn, so every extra decade of low early yields
   squeezes the strikes — which sit within a few percent of each other — into a
   thinner band. Starting in 2015 gives the ladders a little over half the height
   on all three crops, against a third if the window opened in 2005. The cost is
   that a trend can only be fitted across the years shown. */
window.WXAg = (() => {
  const X0 = 2015;
  const fmt = v => (v == null ? '—' : Number(v).toFixed(2));

  function init() {
    return WXPanels.init('agriculture', {
      panel: { x0: X0, fmt, fmtAxis: fmt, fmtThreshold: fmt, thresholdSuffix: '', xLabel: 'Year', tightRight: true, clampZero: true,
               unsettledFromContracts: true },
      source: 'Series from the USDA Foreign Agricultural Service, Production Supply and Distribution.',
    });
  }
  return { init };
})();
