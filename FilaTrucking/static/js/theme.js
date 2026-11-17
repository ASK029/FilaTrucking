/* Light / dark theme (QA-053). Loaded in <head>, before the stylesheet paints, so the saved choice is applied without a
   flash. The choice lives in this browser only (localStorage); the default is the dark theme the app always had. */
(function () {
  var KEY = 'fila-theme';
  var root = document.documentElement;

  function saved() {
    try { var v = window.localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : null; } catch (e) { return null; }
  }

  function apply(theme) {
    root.setAttribute('data-theme', theme);
    var next = theme === 'light' ? 'dark' : 'light';
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-label', 'Switch to ' + next + ' theme');
      buttons[i].setAttribute('title', 'Switch to ' + next + ' theme');
      buttons[i].setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
    }
  }

  apply(saved() || 'dark');
  document.addEventListener('DOMContentLoaded', function () { apply(root.getAttribute('data-theme') || 'dark'); });

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-theme-toggle]') : null;
    if (!button) { return; }
    var theme = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    try { window.localStorage.setItem(KEY, theme); } catch (e) { /* private window: the choice lasts for this page only */ }
    apply(theme);
  });
})();
