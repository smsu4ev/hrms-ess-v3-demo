/* V3.3 signature pad for Job Description acknowledgement.
   External script (CSP-safe). Draws on canvas, exports PNG data URL. */
(function () {
  "use strict";
  var canvas = document.getElementById("sigPad");
  var clearBtn = document.getElementById("sigClear");
  var form = document.getElementById("jdAckForm");
  var sigField = document.getElementById("sigData");
  var drawn = false;
  if (!canvas || !form) return;

  function fit() {
    var r = canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(280, r.width) * dpr;
    canvas.height = 160 * dpr;
    var ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#172b4d";
    return ctx;
  }
  var ctx = fit();
  var drawing = false, last = null;

  function pos(e) {
    var r = canvas.getBoundingClientRect();
    var t = (e.touches && e.touches[0]) || e;
    return { x: t.clientX - r.left, y: t.clientY - r.top };
  }
  canvas.addEventListener("pointerdown", function (e) {
    drawing = true; last = pos(e); drawn = true;
    canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!drawing) return;
    var p = pos(e);
    ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke();
    last = p;
    e.preventDefault();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) {
    canvas.addEventListener(ev, function () { drawing = false; });
  });
  if (clearBtn) clearBtn.addEventListener("click", function () {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawn = false;
  });

  form.addEventListener("submit", function (e) {
    if (!drawn) {
      alert("Please draw your signature on the pad.");
      e.preventDefault();
      return;
    }
    sigField.value = canvas.toDataURL("image/png");
  });
})();
