/* The energy tabs.

   Both energy categories are drawn by the shared category-panels code; what
   lives here is only what is particular to energy, which is how the numbers
   read. These series run from a third of a percent, for petroleum's share of
   generation, to three and a half million million cubic feet of gas produced in
   a month. One rounding rule cannot serve that range, so the formatter picks by
   magnitude and thousands are grouped, because an unseparated seven-digit axis
   label is not a number a reader can take in at a glance.

   The window opens in 2014. Most of these are monthly, so a decade is already a
   hundred and forty points, and the contracts sit within a year or two of the
   end; opening earlier would compress the strikes into a band the way the crop
   panels were before they were pulled in. */
window.WXEnergy = (() => {
  // grouped thousands above ten thousand, otherwise enough decimals to separate
  // adjacent strikes: fuel shares differ by tenths and gasoline by cents
  const fmt = v => {
    if (v == null) return '—';
    const a = Math.abs(v);
    if (a >= 10000) return Math.round(v).toLocaleString('en-US');
    if (a >= 100) return v.toFixed(1);
    return v.toFixed(2);
  };
  const X0 = 2014;

  function init(slug) {
    return WXPanels.init(slug, {
      panel: { x0: X0, fmt, fmtAxis: fmt, fmtThreshold: fmt, thresholdSuffix: '', xLabel: 'Period', tightRight: true, clampZero: true },
      source: 'Series from the US Energy Information Administration.',
      ladderNote: 'This contract resolves on an event rather than on a published series, so its strikes are '
                  + 'listed rather than plotted.',
    });
  }
  return { init, fmt };
})();
