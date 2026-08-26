/* The weather tab.

   Rain, monthly average temperature, drought and storm counts, drawn like the
   climate and crop tabs rather than hidden behind a listing.

   Two things are particular here. These series are monthly and run back to 1990,
   so the window is short: a thirty-year record drawn in full leaves the strikes,
   which sit within an inch or two of each other, sharing a few pixels. And the
   contracts settle on months the record has not reached yet, so each panel
   carries a seasonal projection — what that calendar month has usually been —
   forward to the listed expirations. That is a climatology and the hover says
   so; it is not a forecast of the month in question. */
window.WXWeather = (() => {
  // rain in inches and temperature in degrees are both read to a tenth; drought
  // is a percentage and reads the same way
  const fmt = v => (v == null ? '—' : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
  const X0 = 2012;

  function init() {
    return WXPanels.init('weather', {
      panel: { x0: X0, fmt, fmtAxis: fmt, fmtThreshold: fmt, thresholdSuffix: '',
               xLabel: 'Month', tightRight: true, clampZero: true, project: true },
      source: 'Series: NOAA Climate at a Glance city time series and the US Drought Monitor.',
    });
  }
  return { init };
})();
