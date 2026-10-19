/* HRMS + ESS v3.1 - mobile navigation drawer + PJAX app-like navigation.
   Same-origin script, CSP-safe (default-src 'self', no eval). */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }

  /* ---------- mobile drawer (v2.1) ---------- */
  function initDrawer() {
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
    window.__hrmsCloseNav = closeNav;
    toggle.addEventListener("click", function () {
      if (document.body.classList.contains("nav-open")) { closeNav(); }
      else { openNav(); }
    });
    if (scrim) { scrim.addEventListener("click", closeNav); }
    var links = sidebar.querySelectorAll("a");
    for (var i = 0; i < links.length; i++) {
      links[i].addEventListener("click", closeNav);
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeNav(); }
    });
  }

  /* ---------- PJAX app-like navigation (v3.1) ---------- */
  var CACHE_TTL_MS = 60000;   // prefetch cache freshness
  var CACHE_MAX = 10;         // max cached pages
  var cache = new Map();      // url -> { html, ts }
  var inFlight = null;        // current fetch promise (avoid duplicates)
  var progressTimer = null;

  function progressStart() {
    var bar = document.getElementById("pjax-progress");
    if (!bar) { return; }
    bar.classList.add("on");
    bar.style.width = "0%";
    clearTimeout(progressTimer);
    progressTimer = setTimeout(function () { bar.style.width = "70%"; }, 60);
  }

  function progressDone() {
    var bar = document.getElementById("pjax-progress");
    if (!bar) { return; }
    clearTimeout(progressTimer);
    bar.style.width = "100%";
    setTimeout(function () { bar.classList.remove("on"); bar.style.width = "0%"; }, 250);
  }

  function mainEl() {
    return document.getElementById("pjax-main");
  }

  function isDownloadLike(url) {
    var u = url.split("#")[0];
    return /\/download(\/|$)/i.test(u) || /\/export(\/|$)/i.test(u) ||
           /\/wps(\/|$)/i.test(u) || /\.csv(\?|$)/i.test(u) ||
           /\.xlsx(\?|$)/i.test(u) || /[?&]format=(csv|xlsx)(&|$)/i.test(u);
  }

  // Links that must keep full page loads (data-no-pjax, data-full, downloads,
  // photos, external, new-tab, the logout POST form, any <form> context).
  function pjaxEligible(a) {
    if (!a || a.tagName !== "A") { return false; }
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) { return false; }
    if (a.target && a.target !== "_self") { return false; }
    if (a.hasAttribute("download")) { return false; }
    if (a.hasAttribute("data-no-pjax")) { return false; }
    if (a.hasAttribute("data-full")) { return false; }
    if (a.closest("form")) { return false; }
    var url;
    try { url = new URL(href, window.location.origin); }
    catch (e) { return false; }
    if (url.origin !== window.location.origin) { return false; } // external
    if (/^\/photo\//.test(url.pathname)) { return false; }       // images
    if (isDownloadLike(url.href)) { return false; }             // exports
    return true;
  }

  function cacheGet(url) {
    var hit = cache.get(url);
    if (hit && (Date.now() - hit.ts) < CACHE_TTL_MS) { return hit.html; }
    if (hit) { cache.delete(url); }
    return null;
  }

  function cachePut(url, html) {
    if (cache.has(url)) { cache.delete(url); }
    cache.set(url, { html: html, ts: Date.now() });
    while (cache.size > CACHE_MAX) {
      cache.delete(cache.keys().next().value); // evict oldest
    }
  }

  function fetchPage(url) {
    var hit = cacheGet(url);
    if (hit) { return Promise.resolve(hit); }
    if (inFlight && inFlight.url === url) { return inFlight.promise; }
    var p = fetch(url, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "pjax" }
    }).then(function (resp) {
      if (!resp.ok) { throw new Error("HTTP " + resp.status); }
      // Session expired -> server redirects to /login: do a real navigation.
      if (resp.redirected && /\/login(\?|$)/.test(resp.url)) {
        window.location.href = resp.url;
        return new Promise(function () {}); // never resolves; browser leaves
      }
      return resp.text().then(function (html) { cachePut(url, html); return html; });
    });
    inFlight = { url: url, promise: p };
    p.then(function () { if (inFlight && inFlight.url === url) { inFlight = null; }; },
           function () { if (inFlight && inFlight.url === url) { inFlight = null; }; });
    return p;
  }

  function rerunScripts(container) {
    // Re-execute inline scripts inside swapped content (fresh elements).
    var scripts = container.querySelectorAll("script");
    for (var i = 0; i < scripts.length; i++) {
      var old = scripts[i];
      var fresh = document.createElement("script");
      for (var j = 0; j < old.attributes.length; j++) {
        var attr = old.attributes[j];
        fresh.setAttribute(attr.name, attr.value);
      }
      fresh.textContent = old.textContent;
      old.parentNode.replaceChild(fresh, old);
    }
  }

  function markActiveNav(url) {
    try {
      var path = new URL(url, window.location.origin).pathname;
      var links = document.querySelectorAll("#sidebar a");
      for (var i = 0; i < links.length; i++) {
        var lp;
        try { lp = new URL(links[i].getAttribute("href"), window.location.origin).pathname; }
        catch (e) { continue; }
        if (lp === path) { links[i].classList.add("active"); }
        else { links[i].classList.remove("active"); }
      }
    } catch (e) { /* non-fatal */ }
  }

  /* ---------- collapsible nav groups (v3.6) ----------
     Expansion triggers compose as: (1) click toggles .open on the group
     (touch/mobile + pinned-open on desktop); (2) pure-CSS hover/focus-within
     expands on desktop (hover:hover, min-width 861px); (3) the group holding
     the current page's active link carries .has-active and stays expanded.
     Mobile drawer (<=860px): tap toggles; opening one group collapses the
     others, except a group with .has-active keeps its state. Delegated on
     document so it survives PJAX; re-synced after every swap. */
  function syncNavGroups() {
    var groups = document.querySelectorAll(".navgroup");
    for (var i = 0; i < groups.length; i++) {
      var g = groups[i];
      if (g.querySelector("a.active")) { g.classList.add("has-active"); }
      else { g.classList.remove("has-active"); }
      var head = g.querySelector(".navgroup-head");
      if (head) {
        var expanded = g.classList.contains("open") || g.classList.contains("has-active");
        head.setAttribute("aria-expanded", expanded ? "true" : "false");
      }
    }
  }

  function initNavGroups() {
    document.addEventListener("click", function (e) {
      var head = e.target.closest ? e.target.closest(".navgroup-head") : null;
      if (!head) { return; }
      e.preventDefault();
      var group = head.closest(".navgroup");
      if (!group) { return; }
      var willOpen = !group.classList.contains("open");
      group.classList.toggle("open", willOpen);
      head.setAttribute("aria-expanded", willOpen ? "true" : "false");
      // Mobile accordion: opening one group closes the others, except the
      // group holding the current page (.has-active) which may stay expanded.
      if (willOpen && window.matchMedia("(max-width: 860px)").matches) {
        var groups = document.querySelectorAll(".navgroup");
        for (var i = 0; i < groups.length; i++) {
          if (groups[i] !== group && !groups[i].classList.contains("has-active")) {
            groups[i].classList.remove("open");
            var h = groups[i].querySelector(".navgroup-head");
            if (h) { h.setAttribute("aria-expanded", "false"); }
          }
        }
      }
    });
  }

  function swapContent(url, html, push) {
    var doc;
    try { doc = new DOMParser().parseFromString(html, "text/html"); }
    catch (e) { window.location.href = url; return; }
    var incoming = doc.getElementById("pjax-main");
    var current = mainEl();
    if (!incoming || !current) { window.location.href = url; return; }
    // If the fetched page is actually the login page, go there properly.
    if (doc.querySelector(".authpage") || /\/login(\?|$)/.test(url)) {
      window.location.href = url; return;
    }
    current.innerHTML = incoming.innerHTML;
    if (doc.title) { document.title = doc.title; }
    rerunScripts(current);
    markActiveNav(url);
    syncNavGroups();
    if (push !== false) {
      try { window.history.pushState({ pjax: true }, "", url); }
      catch (e) { /* ignore */ }
    }
    if (window.__hrmsCloseNav) { window.__hrmsCloseNav(); }
    window.scrollTo(0, 0);
  }

  function navigate(url, opts) {
    var current = mainEl();
    if (!current) { window.location.href = url; return; }
    progressStart();
    current.classList.add("pjax-loading");
    fetchPage(url).then(function (html) {
      progressDone();
      current.classList.remove("pjax-loading");
      swapContent(url, html, !(opts && opts.replace));
    }).catch(function () {
      // Any fetch/parse error -> fall back to a normal full navigation.
      progressDone();
      current.classList.remove("pjax-loading");
      window.location.href = url;
    });
  }

  function initPjax() {
    if (!mainEl()) { return; } // login page etc: no pjax container, no pjax
    markActiveNav(window.location.href);
    syncNavGroups();

    // Intercept clicks on eligible internal GET links (sidebar, bottom nav, tiles).
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented) { return; }
      if (e.button !== 0) { return; }                       // not left click
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
      var a = e.target.closest ? e.target.closest("a") : null;
      if (!pjaxEligible(a)) { return; }
      var url = new URL(a.getAttribute("href"), window.location.origin).href;
      if (url === window.location.href) { e.preventDefault(); return; }
      e.preventDefault();
      navigate(url);
    });

    // Back / forward buttons reuse the same loader (or fresh cache).
    window.addEventListener("popstate", function () {
      if (!mainEl()) { window.location.reload(); return; }
      navigate(window.location.href, { replace: true });
    });

    // Prefetch on hover / touch so taps feel instant.
    function prefetch(a) {
      if (!pjaxEligible(a)) { return; }
      var url = new URL(a.getAttribute("href"), window.location.origin).href;
      if (cacheGet(url) || (inFlight && inFlight.url === url)) { return; }
      fetchPage(url).catch(function () { /* ignore prefetch errors */ });
    }
    document.addEventListener("mouseover", function (e) {
      var a = e.target.closest ? e.target.closest("a") : null;
      if (a) { prefetch(a); }
    }, { passive: true });
    document.addEventListener("touchstart", function (e) {
      var a = e.target.closest ? e.target.closest("a") : null;
      if (a) { prefetch(a); }
    }, { passive: true });
  }

  ready(function () {
    initDrawer();
    initPjax();
    initNavGroups();
    syncNavGroups();
  });
})();
