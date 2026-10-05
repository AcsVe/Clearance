// Same behaviour as the script's print pages: open the print dialog
// shortly after load (kept as a file because the CSP blocks inline JS).
window.addEventListener('load', function () { setTimeout(function () { window.print(); }, 800); });
