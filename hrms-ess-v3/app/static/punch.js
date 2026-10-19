/* Geo + selfie check-in/out (V3.5.1 fix for iOS Safari).
   iOS Safari silently ignores a programmatic file-input click that is NOT
   inside the original tap handler, so the camera must open synchronously
   on a user tap. Strategy:
   - GPS acquisition starts on page load and keeps a fresh fix ready.
   - Tapping Check In/Out with a fresh fix opens the camera in the SAME tap.
   - If no fix is ready yet, a visible "Take Selfie" button appears once the
     fix arrives; tapping IT opens the camera (also a direct gesture).
   The file input is created once at startup and kept in the DOM. */
(function () {
  "use strict";
  var statusEl = document.getElementById("punchStatus");
  var locEl = document.getElementById("punchLoc");
  var btnIn = document.getElementById("btnPunchIn");
  var btnOut = document.getElementById("btnPunchOut");
  var btnSelfie = document.getElementById("btnSelfie");

  var latestPos = null;      // last GPS fix (GeolocationPosition)
  var latestAt = 0;          // Date.now() when the fix arrived
  var pendingType = null;    // "in" | "out" while a punch is in progress
  var photoInput = null;

  function say(msg, kind) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.className = "punch-status " + (kind || "");
  }

  function locSay() {
    if (!locEl) return;
    if (latestPos) {
      var acc = Math.round(latestPos.coords.accuracy || 0);
      locEl.textContent = "Location ready (±" + acc + " m).";
    } else {
      locEl.textContent = "Getting your location… allow location access if asked.";
    }
  }

  function busy(disabled) {
    if (btnIn) btnIn.disabled = disabled;
    if (btnOut) btnOut.disabled = disabled;
  }

  function freshFix() {
    return latestPos && (Date.now() - latestAt) < 90000;
  }

  function startGps() {
    if (!navigator.geolocation) {
      say("This device/browser does not support location sharing. " +
          "Use a phone browser, or ask HR for an attendance regularisation.", "err");
      return;
    }
    var opts = { enableHighAccuracy: true, timeout: 20000, maximumAge: 10000 };
    var onFix = function (pos) {
      latestPos = pos;
      latestAt = Date.now();
      locSay();
      if (pendingType && btnSelfie) {
        btnSelfie.style.display = "";
        say("Location found. Now tap \u201CTake Selfie\u201D to open the camera.");
      }
    };
    var onErr = function () { locSay(); };
    navigator.geolocation.getCurrentPosition(onFix, onErr, opts);
    if (navigator.geolocation.watchPosition) {
      navigator.geolocation.watchPosition(onFix, onErr, opts);
    }
  }

  function ensureInput() {
    if (photoInput) return photoInput;
    photoInput = document.createElement("input");
    photoInput.type = "file";
    photoInput.accept = "image/*";
    photoInput.setAttribute("capture", "user"); // front camera on mobiles
    photoInput.style.display = "none";
    photoInput.addEventListener("change", onPhotoChosen);
    document.body.appendChild(photoInput);
    return photoInput;
  }

  function openCamera() {
    // MUST be called synchronously from a click handler (iOS requirement).
    var input = ensureInput();
    input.value = "";
    input.click();
  }

  function onPhotoChosen() {
    var file = photoInput.files && photoInput.files[0];
    if (!file || !pendingType) {
      if (!file) say("No photo taken. A selfie is required — tap Check In/Out to retry.", "err");
      pendingType = null;
      busy(false);
      if (btnSelfie) btnSelfie.style.display = "none";
      return;
    }
    var type = pendingType;
    var pos = latestPos;
    say("Resizing photo…");
    resizePhoto(file).then(function (blob) {
      say("Sending check-" + type + "…");
      return postPunch(type, pos, blob);
    }).then(function (res) {
      pendingType = null;
      busy(false);
      if (btnSelfie) btnSelfie.style.display = "none";
      if (res.body && res.body.ok) {
        say("Checked " + type + " at " + res.body.time +
            " (" + (res.body.location || "") + ").", "ok");
        setTimeout(function () { window.location.reload(); }, 1200);
      } else {
        say((res.body && res.body.error) || "Check-in failed. Try again.", "err");
      }
    }).catch(function () {
      pendingType = null;
      busy(false);
      if (btnSelfie) btnSelfie.style.display = "none";
      say("Could not complete the punch. Please retry.", "err");
    });
  }

  function resizePhoto(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        var maxW = 800;
        var w = img.width, h = img.height;
        if (w > maxW) { h = Math.round(h * maxW / w); w = maxW; }
        var c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        c.toBlob(function (blob) {
          if (blob) resolve(blob);
          else reject(new Error("resize-failed"));
        }, "image/jpeg", 0.72);
      };
      img.onerror = function () { reject(new Error("bad-photo")); };
      img.src = url;
    });
  }

  function postPunch(type, pos, blob) {
    var form = new FormData();
    form.append("csrf_token", document.getElementById("punchCsrf").value);
    form.append("punch_type", type);
    form.append("latitude", pos.coords.latitude);
    form.append("longitude", pos.coords.longitude);
    form.append("accuracy", pos.coords.accuracy || 0);
    form.append("photo", blob, "selfie.jpg");
    return fetch("/attendance/punch/submit", {
      method: "POST",
      body: form,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    }).then(function (r) {
      return r.json().then(function (j) { return { code: r.status, body: j }; });
    });
  }

  function doPunch(type) {
    pendingType = type;
    busy(true);
    if (freshFix()) {
      say("Opening camera — take a clear selfie…");
      openCamera(); // synchronous within this tap handler — required by iOS
    } else {
      say("Getting your location… the Take Selfie button will appear when ready.");
      startGps();
    }
  }

  if (btnIn) btnIn.addEventListener("click", function () { doPunch("in"); });
  if (btnOut) btnOut.addEventListener("click", function () { doPunch("out"); });
  if (btnSelfie) btnSelfie.addEventListener("click", function () {
    if (pendingType && freshFix()) {
      say("Opening camera — take a clear selfie…");
      openCamera(); // synchronous within this tap handler
    }
  });

  ensureInput();
  startGps();
  locSay();
})();
