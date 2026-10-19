/* HRMS + ESS v3.7 - app-like navigation (PJAX) + nav sync.
   Same-origin script, CSP-safe (default-src 'self', no eval).
   Shell behaviour (drawer open/close, counters) lives in desk.js; after every
   content swap we call window.__hrmsShellRefresh() (exposed by desk.js). */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
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

  /* Active-link highlight across the v3.7 app shell: top-bar links, drawer
     links and bottom-nav links match by exact path; re-run after every
     PJAX swap. desk.js expands the drawer group containing the .active
     link via window.__hrmsShellRefresh(). */
  function markActiveNav(url) {
    try {
      var path = new URL(url, window.location.origin).pathname;
      var links = document.querySelectorAll("#drawer a, .topbar-links a, .bottomnav a");
      for (var i = 0; i < links.length; i++) {
        var a = links[i];
        var lp;
        try { lp = new URL(a.getAttribute("href"), window.location.origin).pathname; }
        catch (e) { continue; }
        if (lp === path) { a.classList.add("active"); }
        else { a.classList.remove("active"); }
      }
    } catch (e) { /* non-fatal */ }
  }

  /* ---------- drawer group auto-expand sync (v3.7) ----------
     The v3.6 rail/sidebar collapse logic is gone with the old shell;
     markActiveNav() handles link highlighting and desk.js auto-expands
     the drawer group holding the active link; both re-run after PJAX. */

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
    // V3.7 page-enter animation + drawer group auto-expand + counters.
    current.classList.remove("pjax-enter");
    void current.offsetWidth; // restart the keyframe animation
    current.classList.add("pjax-enter");
    if (window.__hrmsShellRefresh) { window.__hrmsShellRefresh(); }
    if (push !== false) {
      try { window.history.pushState({ pjax: true }, "", url); }
      catch (e) { /* ignore */ }
    }
    if (window.__hrmsCloseDrawer) { window.__hrmsCloseDrawer(); }
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
    initPjax();
  });
})();
