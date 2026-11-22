/* فك الصفحة المشفّرة وتفعيل منع الزر الأيمن واختصارات عرض المصدر وأدوات المطوّر (انظر utils/page_shield.py).
   ملف خارجي بلا أي سكربت داخل الصفحة لأن سياسة الأمان في هذا النظام تمنعه. */
(function () {
  var s = document.currentScript;
  if (!s) return;
  var A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  var p = (s.getAttribute('data-p') || '').split('').reverse().join('');
  var k = parseInt(s.getAttribute('data-k'), 10) || 0;
  var guard = s.getAttribute('data-g') === '1';
  var B = '<div style="font-family:Tahoma,Arial,sans-serif;direction:rtl;text-align:center;padding:80px 20px;color:#333">' +
    '<div style="font-size:48px">&#128274;</div><h2 style="margin:16px 0 8px">غير مسموح</h2>' +
    '<p>أدوات المطوّر غير مسموح بها في هذا الموقع.<br>أغلقها ثم أعد تحميل الصفحة.</p>' +
    '<p style="color:#555;font-size:13px">Developer tools are not allowed on this site. Close them and reload.</p></div>';

  // كشف أدوات المطوّر: عبارة debugger لا تفعل شيئاً والأدوات مغلقة، وعندما تكون مفتوحة يتوقف التنفيذ عندها
  function opened() { var t = performance.now(); debugger; return performance.now() - t > 120; }
  if (guard && opened()) { document.write(B); return; }

  var o = '';
  for (var i = 0; i < p.length; i++) {
    var c = p.charAt(i), j = A.indexOf(c);
    o += j < 0 ? c : A.charAt((j - k + 64) % 64);
  }
  var b = atob(o), u = new Uint8Array(b.length);
  for (var n = 0; n < b.length; n++) u[n] = b.charCodeAt(n);
  document.write(new TextDecoder('utf-8').decode(u));

  function editable(e) { return e && e.closest && e.closest('input,textarea,select,[contenteditable="true"]'); }
  document.addEventListener('contextmenu', function (e) { if (!editable(e.target)) e.preventDefault(); });
  document.addEventListener('keydown', function (e) {
    var key = (e.key || '').toLowerCase(), ctrl = e.ctrlKey || e.metaKey;
    if (e.key === 'F12' || (ctrl && e.shiftKey && key && 'ijck'.indexOf(key) > -1) ||
        (e.metaKey && e.altKey && key && 'ijcu'.indexOf(key) > -1) || (ctrl && !e.shiftKey && (key === 'u' || key === 's'))) {
      e.preventDefault(); e.stopPropagation(); return false;
    }
  }, true);
  document.addEventListener('dragstart', function (e) { if (e.target && e.target.tagName === 'IMG') e.preventDefault(); });
  if (guard) {
    var iv = setInterval(function () {
      if (opened()) {
        clearInterval(iv);
        try { document.documentElement.innerHTML = '<head><meta charset="utf-8"></head><body>' + B + '</body>'; } catch (e) {}
      }
    }, 1000);
  }
})();
