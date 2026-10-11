// Applies the theme picked in CleanSync (System, Light or Dark) before the page draws, so it
// never flashes the wrong colours. Loaded from index.html as a file: the Content-Security-Policy
// allows no inline scripts. Keep the key in step with src/lib/theme.ts.
(function () {
  try {
    var picked = localStorage.getItem("drivesync.theme");
    if (picked === "light" || picked === "dark") {
      document.documentElement.setAttribute("data-theme", picked);
    }
  } catch (e) {
    // Storage blocked (a private window, strict settings): follow the system.
  }
})();
