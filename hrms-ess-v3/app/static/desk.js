/* HRMS + ESS v3.7 — app shell behaviour (top bar + drawer model).
   Same-origin script, CSP-safe (default-src 'self', no eval). The shell
   lives OUTSIDE #pjax-main, so these overlays/menus persist across PJAX swaps.
   app.js calls window.__hrmsShellRefresh() after each PJAX content swap. */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }
  function $(id) { return document.getElementById(id); }
  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function sameOrigin(href) {
    try { return new URL(href, window.location.origin).origin === window.location.origin; }
    catch (e) { return false; }
  }
  function hide(el) { if (el) { el.hidden = true; } }
  function show(el) { if (el) { el.hidden = false; } }

  /* ---------- dropdown helper (avatar menu) ---------- */
  function dropdown(btnId, menuId) {
    var btn = $(btnId), menu = $(menuId);
    if (!btn || !menu) { return; }
    function close() { hide(menu); btn.setAttribute("aria-expanded", "false"); }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = !menu.hidden;
      document.querySelectorAll(".avatar-menu").forEach(hide);
      if (open) { close(); } else { show(menu); btn.setAttribute("aria-expanded", "true"); }
    });
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", close);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { close(); } });
  }

  /* ---------- theme toggle (cookie ess_theme + data-theme, server reads cookie) ---------- */
  function initTheme() {
    var btn = $("themeToggle");
    if (!btn) { return; }
    btn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      document.cookie = "ess_theme=" + next + "; path=/; max-age=31536000; SameSite=Lax";
    });
  }

  /* ---------- navigation drawer (v3.7: replaces rail + workspace sidebar) ---------- */
  function initDrawer() {
    var toggle = $("navToggle"), drawer = $("drawer"), scrim = $("drawerScrim"),
        closeBtn = $("drawerClose");
    if (!toggle || !drawer) { return; }
    function openDrawer() {
      document.body.classList.add("drawer-open");
      show(scrim);
      drawer.setAttribute("aria-hidden", "false");
      toggle.setAttribute("aria-expanded", "true");
    }
    function closeDrawer() {
      document.body.classList.remove("drawer-open");
      hide(scrim);
      drawer.setAttribute("aria-hidden", "true");
      toggle.setAttribute("aria-expanded", "false");
    }
    window.__hrmsCloseDrawer = closeDrawer;
    toggle.addEventListener("click", function () {
      if (document.body.classList.contains("drawer-open")) { closeDrawer(); }
      else { openDrawer(); }
    });
    // v3.8: the "More" pill-nav item opens the same drawer.
    var more = $("moreOpen");
    if (more) { more.addEventListener("click", openDrawer); }
    if (closeBtn) { closeBtn.addEventListener("click", closeDrawer); }
    if (scrim) { scrim.addEventListener("click", closeDrawer); }
    // Close on any drawer link click (PJAX navigation follows).
    var links = drawer.querySelectorAll("a");
    for (var i = 0; i < links.length; i++) {
      links[i].addEventListener("click", closeDrawer);
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && document.body.classList.contains("drawer-open")) { closeDrawer(); }
    });

    /* Collapsible groups: toggle aria + .open class (CSS animates max-height). */
    var groups = drawer.querySelectorAll(".d-group");
    for (var g = 0; g < groups.length; g++) {
      (function (grp) {
        var btn = grp.querySelector(".d-group-toggle");
        if (!btn) { return; }
        btn.addEventListener("click", function () {
          var open = grp.classList.toggle("open");
          btn.setAttribute("aria-expanded", open ? "true" : "false");
        });
      })(groups[g]);
    }
  }

  /* Expand the drawer group containing the current page's link.
     app.js marks .active on the exact-path link first; we expand around it. */
  function drawerSync() {
    var drawer = $("drawer");
    if (!drawer) { return; }
    var groups = drawer.querySelectorAll(".d-group");
    for (var i = 0; i < groups.length; i++) {
      var grp = groups[i];
      var hasActive = !!grp.querySelector(".d-links a.active");
      var btn = grp.querySelector(".d-group-toggle");
      grp.classList.toggle("open", hasActive);
      if (btn) { btn.setAttribute("aria-expanded", hasActive ? "true" : "false"); }
    }
  }

  /* ---------- count-up for [data-count] (IntersectionObserver, reduced-motion safe) ---------- */
  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }
  function bindCounters(scope) {
    var root = scope || document;
    var els = root.querySelectorAll("[data-count]");
    if (!els.length) { return; }
    var reduce = reducedMotion();
    function run(el) {
      var target = parseFloat(el.getAttribute("data-count")) || 0;
      if (reduce) { el.textContent = target; return; }
      var dur = 700, t0 = null;
      function step(ts) {
        if (t0 === null) { t0 = ts; }
        var p = Math.min(1, (ts - t0) / dur);
        el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))); // easeOutCubic
        if (p < 1) { window.requestAnimationFrame(step); }
      }
      window.requestAnimationFrame(step);
    }
    if (!("IntersectionObserver" in window)) {
      for (var i = 0; i < els.length; i++) { run(els[i]); }
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      for (var k = 0; k < entries.length; k++) {
        if (entries[k].isIntersecting) {
          run(entries[k].target);
          io.unobserve(entries[k].target);
        }
      }
    }, { threshold: 0.4 });
    for (var j = 0; j < els.length; j++) {
      if (!els[j].__countBound) { els[j].__countBound = true; io.observe(els[j]); }
    }
  }

  /* Called on boot and after every PJAX swap (by app.js). */
  function shellRefresh() {
    drawerSync();
    bindCounters(document);
  }
  window.__hrmsShellRefresh = shellRefresh;

  /* ---------- recent pages (client-side, localStorage) ---------- */
  var RECENT_KEY = "hrms_recents_v1";
  function getRecents() {
    try { return JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]"); }
    catch (e) { return []; }
  }
  function pushRecent(label, href) {
    try {
      var items = getRecents().filter(function (r) { return r.href !== href; });
      items.unshift({ label: label, href: href, ts: Date.now() });
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, 8)));
    } catch (e) { /* ignore */ }
  }
  function trackRecents() {
    // Capture-phase: record before app.js PJAX interception navigates.
    document.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("a") : null;
      if (!a) { return; }
      var href = a.getAttribute("href");
      if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) { return; }
      if (a.target && a.target !== "_self") { return; }
      if (a.hasAttribute("data-no-pjax") || a.hasAttribute("data-full") || a.hasAttribute("download")) { return; }
      if (!sameOrigin(href)) { return; }
      var label = (a.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
      if (label) { pushRecent(label, href); }
    }, true);
  }

  /* ---------- awesome bar ---------- */
  var ICON_PAGE = '<span class="nav-ico"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></span>';
  var ICON_USER = '<span class="nav-ico"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-3.8 4.7-5.5 8-5.5s6.5 1.7 8 5.5"/></svg></span>';

  function initAwesome() {
    var overlay = $("awesome"), scrim = $("awesomeScrim"), input = $("awesomeInput"),
        results = $("awesomeResults"), openBtn = $("awesomeOpen"), closeBtn = $("awesomeClose");
    if (!overlay || !input || !results) { return; }
    var apiUrl = overlay.getAttribute("data-api-url") || "/api/awesome";
    var selIndex = -1, flat = [], timer = null;

    function open() { show(overlay); show(scrim); input.value = ""; renderGroups({}, ""); selIndex = -1; input.focus(); }
    function close() { hide(overlay); hide(scrim); }

    function esc(s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function itemHtml(r, group) {
      var sub = r.dept || r.category || "";
      return '<a class="ares-item" href="' + esc(r.href || "#") + '" data-idx="' + group + '">' +
        (r.id && group === "e" ? ICON_USER : ICON_PAGE) +
        "<span>" + esc(r.label || r.name || r.title) + "</span>" +
        (sub ? '<span class="ares-sub">' + esc(sub) + "</span>" : "") + "</a>";
    }
    function groupHtml(name, rows, key) {
      if (!rows || !rows.length) { return ""; }
      return '<div class="ares-group"><div class="ares-gname">' + esc(name) + "</div>" +
        rows.map(function (r) { return itemHtml(r, key); }).join("") + "</div>";
    }
    function renderGroups(data, q) {
      flat = [];
      var html = "";
      if (!q) {
        var recents = getRecents();
        if (recents.length) {
          html += groupHtml("Recent", recents.slice(0, 5).map(function (r) {
            return { label: r.label, href: r.href };
          }), "r");
          flat = flat.concat(recents.slice(0, 5).map(function (r) { return { href: r.href }; }));
        }
      }
      var pages = data.pages || [], employees = data.employees || [], documents = data.documents || [];
      html += groupHtml("Pages & Actions", pages, "p");
      html += groupHtml("Employees", employees, "e");
      html += groupHtml("Documents", documents, "d");
      if (!html) {
        html = '<div class="ares-empty">' + (q ? "No results for &ldquo;" + esc(q) + "&rdquo;." : "Type to search pages, employees and documents.") + "</div>";
      }
      results.innerHTML = html;
      selIndex = -1;
    }
    function fetchResults(q) {
      fetch(apiUrl + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
        .then(function (resp) { return resp.ok ? resp.json() : { pages: [], employees: [], documents: [] }; })
        .then(function (data) { if (input.value.trim() === q) { renderGroups(data, q); } })
        .catch(function () { /* keep previous results */ });
    }
    function moveSel(dir) {
      var items = results.querySelectorAll(".ares-item");
      if (!items.length) { return; }
      selIndex = (selIndex + dir + items.length) % items.length;
      for (var i = 0; i < items.length; i++) { items[i].classList.toggle("sel", i === selIndex); }
      var el = items[selIndex];
      if (el && el.scrollIntoView) { el.scrollIntoView({ block: "nearest" }); }
    }

    if (openBtn) { openBtn.addEventListener("click", open); }
    if (closeBtn) { closeBtn.addEventListener("click", close); }
    if (scrim) { scrim.addEventListener("click", close); }
    document.addEventListener("keydown", function (e) {
      var mod = e.ctrlKey || e.metaKey;
      if (mod && (e.key === "k" || e.key === "K")) { e.preventDefault(); open(); return; }
      if (e.key === "Escape" && !overlay.hidden) { close(); }
    });
    input.addEventListener("input", function () {
      var q = input.value.trim();
      clearTimeout(timer);
      if (!q) { renderGroups({}, ""); return; }
      timer = setTimeout(function () { fetchResults(q); }, 220); // debounce
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); }
      else if (e.key === "Enter") {
        var items = results.querySelectorAll(".ares-item");
        var el = selIndex >= 0 ? items[selIndex] : items[0];
        if (el) { close(); el.click(); } // app.js PJAX intercepts the click
      }
    });
    results.addEventListener("click", function () { close(); }); // navigation follows
  }

  /* ---------- notifications slide-over ---------- */
  function initNotifications() {
    var panel = $("notifPanel"), scrim = $("notifScrim"), openBtn = $("notifOpen"),
        closeBtn = $("notifClose"), body = $("notifBody"), markAll = $("notifMarkAll");
    if (!panel || !body) { return; }
    var tab = "all";

    function open() { show(panel); show(scrim); }
    function close() { hide(panel); hide(scrim); }
    function applyTab() {
      var items = body.querySelectorAll(".panel-item");
      for (var i = 0; i < items.length; i++) {
        var show = tab === "all" || items[i].getAttribute("data-cat") === tab;
        items[i].style.display = show ? "" : "none";
      }
      var tabs = panel.querySelectorAll(".ptab");
      for (var j = 0; j < tabs.length; j++) {
        tabs[j].classList.toggle("active", tabs[j].getAttribute("data-ntab") === tab);
      }
    }
    function setBadge(n) {
      var badge = $("notifBadge");
      if (n > 0) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "nbadge"; badge.id = "notifBadge";
          if (openBtn) { openBtn.appendChild(badge); }
        }
        badge.textContent = n;
      } else if (badge) { badge.remove(); }
    }

    if (openBtn) { openBtn.addEventListener("click", function () { applyTab(); open(); }); }
    if (closeBtn) { closeBtn.addEventListener("click", close); }
    if (scrim) { scrim.addEventListener("click", close); }
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) { close(); } });

    panel.addEventListener("click", function (e) {
      var t = e.target.closest ? e.target.closest(".ptab") : null;
      if (t) { tab = t.getAttribute("data-ntab") || "all"; applyTab(); }
    });

    // Item click: mark read (server) then follow the link.
    body.addEventListener("click", function (e) {
      var item = e.target.closest ? e.target.closest(".panel-item") : null;
      if (!item) { return; }
      e.preventDefault();
      var readUrl = item.getAttribute("data-read"), link = item.getAttribute("data-link");
      item.classList.remove("unread");
      var unreadLeft = body.querySelectorAll('.panel-item.unread[style=""], .panel-item.unread:not([style])').length;
      setBadge(unreadLeft);
      fetch(readUrl, { method: "POST", credentials: "same-origin", keepalive: true,
                      headers: { "X-CSRFToken": csrfToken() } })
        .catch(function () { /* non-fatal */ });
      close();
      if (link) { window.location.href = link; }
    });
    body.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var item = e.target.closest ? e.target.closest(".panel-item") : null;
        if (item) { item.click(); }
      }
    });

    if (markAll) {
      markAll.addEventListener("click", function () {
        var url = markAll.getAttribute("data-url");
        var fd = "tab=" + encodeURIComponent(tab === "all" ? "" : tab);
        fetch(url, { method: "POST", credentials: "same-origin",
                     headers: { "Content-Type": "application/x-www-form-urlencoded",
                                "X-CSRFToken": csrfToken() },
                     body: fd })
          .then(function () {
            var items = body.querySelectorAll(".panel-item");
            for (var i = 0; i < items.length; i++) {
              if (tab === "all" || items[i].getAttribute("data-cat") === tab) {
                items[i].classList.remove("unread");
              }
            }
            var left = body.querySelectorAll(".panel-item.unread").length;
            setBadge(left);
          })
          .catch(function () { /* non-fatal */ });
      });
    }
  }

  ready(function () {
    dropdown("avatarBtn", "avatarMenu");
    initTheme();
    trackRecents();
    initAwesome();
    initNotifications();
    initDrawer();
    shellRefresh();
  });
})();
