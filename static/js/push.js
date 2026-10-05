// Opt-in Web Push for this device (the server only stores the browser's
// subscription; the VAPID private key never leaves the server).
(function () {
  var btn = document.getElementById('push-toggle');
  var status = document.getElementById('push-status');
  if (!btn) return;
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    btn.disabled = true;
    status.textContent = 'هذا المتصفح لا يدعم الإشعارات. على iPhone ثبّت التطبيق على الشاشة الرئيسية أولًا.';
    return;
  }
  function b64ToBytes(s) {
    var pad = '='.repeat((4 - s.length % 4) % 4);
    var raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': btn.dataset.csrf },
      body: JSON.stringify(body) });
  }
  navigator.serviceWorker.ready.then(function (reg) {
    return reg.pushManager.getSubscription().then(function (sub) {
      btn.textContent = sub ? '🔕 إيقاف الإشعارات على هذا الجهاز' : '🔔 تفعيل الإشعارات على هذا الجهاز';
      btn.addEventListener('click', function () {
        if (sub) {
          var endpoint = sub.endpoint;
          sub.unsubscribe().then(function () { return post('/notifications/unsubscribe', { endpoint: endpoint }); })
            .then(function () { location.reload(); });
          return;
        }
        Notification.requestPermission().then(function (perm) {
          if (perm !== 'granted') { status.textContent = 'لم يُسمح بالإشعارات من إعدادات المتصفح.'; return; }
          fetch('/notifications/vapid-key', { credentials: 'same-origin' }).then(function (r) { return r.json(); })
            .then(function (d) { return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(d.key) }); })
            .then(function (s) { return post('/notifications/subscribe', s.toJSON()); })
            .then(function () { location.reload(); })
            .catch(function (e) { status.textContent = 'تعذر التفعيل: ' + e; });
        });
      });
    });
  });
})();
