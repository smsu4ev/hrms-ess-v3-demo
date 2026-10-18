/* HRMS + ESS v2.1 - mobile navigation drawer.
   Same-origin script, CSP-safe (default-src 'self'). */
(function () {
  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }
  ready(function () {
    var toggle = document.getElementById("navToggle");
    var sidebar = document.getElementById("sidebar");
    var scrim = document.getElementById("navScrim");
    if (!toggle || !sidebar) { return; }

    function openNav() {
      document.body.classList.add("nav-open");
      toggle.setAttribute("aria-expanded", "true");
    }
    function closeNav() {
      document.body.classList.remove("nav-open");
      toggle.setAttribute("aria-expanded", "false");
    }
    toggle.addEventListener("click", function () {
      if (document.body.classList.contains("nav-open")) { closeNav(); }
      else { openNav(); }
    });
    if (scrim) { scrim.addEventListener("click", closeNav); }
    // Close the drawer when a nav link is tapped.
    var links = sidebar.querySelectorAll("a");
    for (var i = 0; i < links.length; i++) {
      links[i].addEventListener("click", closeNav);
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeNav(); }
    });
  });
})();
