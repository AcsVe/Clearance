/* Clearance pages: fill the employee fields from the roster picker; auto-print on the print page. */
(function () {
  'use strict';
  document.addEventListener('change', function (e) {
    var sel = e.target;
    if (!sel.matches('[data-employee-picker]')) return;
    var o = sel.options[sel.selectedIndex], form = sel.form;
    if (!o || !o.value) return;
    [['employee_name', 'name'], ['employee_number', 'number'], ['employee_email', 'email'],
     ['department_name', 'dept'], ['job_title', 'job']].forEach(function (p) {
      var input = form.elements[p[0]];
      if (input) input.value = o.getAttribute('data-' + p[1]) || '';
    });
    form.querySelectorAll('[data-fill]').forEach(function (input) {
      input.value = o.getAttribute('data-' + input.getAttribute('data-fill')) || '';
    });
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-print]')) window.print();
  });
  if (document.body && document.body.hasAttribute('data-autoprint')) {
    window.addEventListener('load', function () { setTimeout(function () { window.print(); }, 600); });
  }
})();
