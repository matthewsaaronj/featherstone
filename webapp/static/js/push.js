// Registers the service worker on every page, and drives the notification
// controls on the Settings page (#push-panel).
(function () {
  "use strict";
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }

  var panel = document.getElementById("push-panel");
  if (!panel) return;
  var status = panel.querySelector("[data-push-status]");
  var onBtn = panel.querySelector("[data-push-on]");
  var offBtn = panel.querySelector("[data-push-off]");
  var testBtn = panel.querySelector("[data-push-test]");
  var key = panel.getAttribute("data-key");

  function say(text, tone) {
    status.textContent = text;
    status.className = "push-status" + (tone ? " " + tone : "");
  }
  function show(el, on) { if (el) el.hidden = !on; }

  function b64ToBytes(s) {
    var pad = "=".repeat((4 - (s.length % 4)) % 4);
    var raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body || {}) });
  }

  function saveSubscription(sub) {
    var json = sub.toJSON();
    json.label = isIOS ? "iPhone" : (navigator.platform || "Browser");
    return post("/push/subscribe", json);
  }

  var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  var isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  async function refresh() {
    show(onBtn, false); show(offBtn, false); show(testBtn, false);
    if (!key) { say("Notifications aren't set up on the server yet."); return; }
    if (!supported) {
      if (isIOS && !standalone) {
        say("On iPhone, first add this app to your Home Screen: tap Share, then “Add to Home Screen”. Open it from there and come back to Settings.");
      } else {
        say("This browser doesn't support notifications.");
      }
      return;
    }
    if (Notification.permission === "denied") {
      say("Notifications are blocked for this app. Turn them on in your phone's Settings → Notifications → Featherstone.", "warn");
      return;
    }
    var reg = await navigator.serviceWorker.ready;
    var sub = await reg.pushManager.getSubscription();
    if (sub) {
      // Re-register with the server every time: the phone can remember a
      // subscription the server never saved (or later dropped).
      try {
        var r = await saveSubscription(sub);
        if (!r.ok) throw new Error("server said " + r.status);
      } catch (e) {
        say("This device is subscribed, but the server couldn't save it (" + e.message + "). Try turning notifications off and on.", "warn");
        show(offBtn, true);
        return;
      }
      say("Notifications are on for this device.", "ok");
      show(offBtn, true); show(testBtn, true);
    } else {
      say("Notifications are off on this device.");
      show(onBtn, true);
    }
  }

  onBtn && onBtn.addEventListener("click", async function () {
    try {
      var perm = await Notification.requestPermission();   // must follow a tap on iPhone
      if (perm !== "granted") { refresh(); return; }
      var reg = await navigator.serviceWorker.ready;
      var sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
      var r = await saveSubscription(sub);
      if (!r.ok) throw new Error("server said " + r.status);
      refresh();
    } catch (e) {
      say("Couldn't turn on notifications: " + e.message, "warn");
    }
  });

  offBtn && offBtn.addEventListener("click", async function () {
    var reg = await navigator.serviceWorker.ready;
    var sub = await reg.pushManager.getSubscription();
    if (sub) { await post("/push/unsubscribe", { endpoint: sub.endpoint }); await sub.unsubscribe(); }
    refresh();
  });

  testBtn && testBtn.addEventListener("click", async function () {
    testBtn.disabled = true;
    try {
      // Make sure the server has this device before testing it.
      var reg = await navigator.serviceWorker.ready;
      var sub = await reg.pushManager.getSubscription();
      if (sub) await saveSubscription(sub);
      var r = await post("/push/test");
      var d = await r.json();
      say(d.ok ? "Test sent — it should arrive in a few seconds."
               : "The test didn't go through: " + (d.error || "unknown error") + ".", d.ok ? "ok" : "warn");
    } finally { testBtn.disabled = false; }
  });

  refresh();
})();
