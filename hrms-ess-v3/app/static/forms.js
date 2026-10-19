/* V3.6 Frappe-model form helpers (workstream C).
   Same-origin script, CSP-safe (no eval, no inline handlers).
   PJAX-safe: init() is idempotent; elements are bound once via data-fjs-bound,
   document-level delegated handlers are registered once. */
(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }

  /* ---- Tab switching: [data-tabs] buttons toggle sibling [data-tabpane] panes ---- */
  function initTabs(root) {
    var bars = (root || document).querySelectorAll("[data-tabs]");
    for (var i = 0; i < bars.length; i++) {
      var bar = bars[i];
      if (bar.dataset.fjsBound) { continue; }
      bar.dataset.fjsBound = "1";
      var buttons = bar.querySelectorAll("[data-tab]");
      (function (btns, groupBar) {
        for (var j = 0; j < btns.length; j++) {
          btns[j].addEventListener("click", function () {
            var target = this.getAttribute("data-tab");
            var scope = groupBar.closest("[data-tabgroup]") || document;
            for (var k = 0; k < btns.length; k++) {
              var on = btns[k] === this;
              btns[k].classList.toggle("active", on);
              btns[k].setAttribute("aria-selected", on ? "true" : "false");
            }
            var panes = scope.querySelectorAll("[data-tabpane]");
            for (var g = 0; g < panes.length; g++) {
              panes[g].classList.toggle("active",
                panes[g].getAttribute("data-tabpane") === target);
            }
          });
        }
      })(buttons, bar);
    }
  }

  /* ---- Dirty-form pill: form[data-dirty-watch] shows [data-not-saved] on change ---- */
  function initDirty(root) {
    var forms = (root || document).querySelectorAll("form[data-dirty-watch]");
    for (var i = 0; i < forms.length; i++) {
      var form = forms[i];
      if (form.dataset.fjsBound) { continue; }
      form.dataset.fjsBound = "1";
      var pill = form.querySelector("[data-not-saved]");
      var mark = function () { if (pill) { pill.hidden = false; } };
      form.addEventListener("input", mark);
      form.addEventListener("change", mark);
      form.addEventListener("submit", function () { if (pill) { pill.hidden = true; } });
    }
  }

  /* ---- Bulk select: [data-bulk] form; [data-bulk-all] toggles [data-bulk-row];
         [data-bulk-btn] is enabled only when >= 1 row is checked ---- */
  function initBulk(root) {
    var forms = (root || document).querySelectorAll("[data-bulk]");
    for (var i = 0; i < forms.length; i++) {
      var form = forms[i];
      if (form.dataset.fjsBound) { continue; }
      form.dataset.fjsBound = "1";
      var all = form.querySelector("[data-bulk-all]");
      var btn = form.querySelector("[data-bulk-btn]");
      var rows = form.querySelectorAll("[data-bulk-row]");
      var sync = function () {
        var n = 0, total = 0;
        for (var r = 0; r < rows.length; r++) {
          total++;
          if (rows[r].checked) { n++; }
        }
        if (btn) { btn.disabled = n === 0; }
        if (all) {
          all.checked = total > 0 && n === total;
          all.indeterminate = n > 0 && n < total;
        }
      };
      if (all) {
        all.addEventListener("change", function () {
          for (var r = 0; r < rows.length; r++) { rows[r].checked = all.checked; }
          sync();
        });
      }
      for (var r = 0; r < rows.length; r++) { rows[r].addEventListener("change", sync); }
      sync();
    }
  }

  /* ---- Delegated one-time handlers (survive PJAX without rebinding) ---- */
  function initDelegated() {
    if (window.__fjsDelegated) { return; }
    window.__fjsDelegated = true;
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (form && form.hasAttribute && form.hasAttribute("data-confirm")) {
        var msg = form.getAttribute("data-confirm") || "Are you sure?";
        if (!window.confirm(msg)) { e.preventDefault(); }
      }
    }, true);
    document.addEventListener("click", function (e) {
      var el = e.target && e.target.closest ? e.target.closest("[data-print]") : null;
      if (el) { e.preventDefault(); window.print(); }
    });
    // Auto-submit filter selects (CSP-safe replacement for onchange="this.form.submit()").
    document.addEventListener("change", function (e) {
      var sel = e.target && e.target.closest ? e.target.closest("[data-autosubmit]") : null;
      if (sel && sel.form) { sel.form.submit(); }
    });
  }

  function init() {
    document.documentElement.classList.add("js");
    initDelegated();
    initTabs(document);
    initDirty(document);
    initBulk(document);
  }

  onReady(init);
  // Expose for PJAX-driven re-renders (app.js re-executes page scripts;
  // init() is idempotent so a second call is harmless).
  window.fjsInit = init;
})();
