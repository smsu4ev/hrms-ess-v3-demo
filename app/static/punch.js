/* V3.3 geo + selfie check-in/out. Loaded as an external script (CSP-safe).
   Flow per tap: get GPS fix -> open front-camera capture -> resize the
   photo on-device -> POST everything to /attendance/punch/submit. */
(function () {
  "use strict";
  var statusEl = document.getElementById("punchStatus");
  var btnIn = document.getElementById("btnPunchIn");
  var btnOut = document.getElementById("btnPunchOut");

  function say(msg, kind) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.className = "punch-status " + (kind || "");
  }

  function getPosition() {
    return new Promise(function (resolve, reject) {
      if (!navigator.geolocation) {
        reject(new Error("no-geo"));
        return;
      }
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true,
        timeout: 20000,
        maximumAge: 30000
      });
    });
  }

  function takePhoto() {
    return new Promise(function (resolve, reject) {
      var input = document.createElement("input");
      input.type = "file";
      input.accept = "image/*";
      input.setAttribute("capture", "user"); // front camera on mobiles
      input.onchange = function () {
        if (input.files && input.files[0]) resolve(input.files[0]);
        else reject(new Error("no-photo"));
      };
      input.oncancel = function () { reject(new Error("no-photo")); };
      document.body.appendChild(input);
      input.click();
      setTimeout(function () { input.remove(); }, 60000);
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
    }).then(function (r) { return r.json().then(function (j) { return { code: r.status, body: j }; }); });
  }

  function busy(disabled) {
    if (btnIn) btnIn.disabled = disabled;
    if (btnOut) btnOut.disabled = disabled;
  }

  function doPunch(type) {
    say(type === "in" ? "Getting your location…" : "Getting your location…");
    busy(true);
    getPosition()
      .then(function (pos) {
        say("Location found. Opening camera — take a clear selfie…");
        return takePhoto().then(function (file) { return { pos: pos, file: file }; });
      })
      .then(function (r) {
        say("Resizing photo…");
        return resizePhoto(r.file).then(function (blob) { return { pos: r.pos, blob: blob }; });
      })
      .then(function (r) {
        say("Sending check-" + type + "…");
        return postPunch(type, r.pos, r.blob);
      })
      .then(function (res) {
        busy(false);
        if (res.body && res.body.ok) {
          say("Checked " + type + " at " + res.body.time +
              " (" + (res.body.location || "") + ").", "ok");
          setTimeout(function () { window.location.reload(); }, 1200);
        } else {
          say((res.body && res.body.error) || "Check-in failed. Try again.", "err");
        }
      })
      .catch(function (e) {
        busy(false);
        var m = (e && e.message) || "";
        if (m === "no-geo" || e.code === 1 || e.code === 2 || e.code === 3) {
          say("Location is unavailable. Enable GPS/location permission for this site and retry. " +
              "If you cannot share location, ask HR for an attendance regularisation.", "err");
        } else if (m === "no-photo") {
          say("No photo taken. A selfie is required — tap again to retry.", "err");
        } else {
          say("Could not complete check-" + type + ". Please retry.", "err");
        }
      });
  }

  if (btnIn) btnIn.addEventListener("click", function () { doPunch("in"); });
  if (btnOut) btnOut.addEventListener("click", function () { doPunch("out"); });

  if (!navigator.geolocation) {
    say("This device/browser does not support location sharing. " +
        "Use a phone browser, or ask HR for an attendance regularisation.", "err");
  }
})();
