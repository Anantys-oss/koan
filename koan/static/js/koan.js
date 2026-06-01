/* Koan Design System — runtime helpers (theme + lightweight interactions) */
(function () {
  const KEY = 'koan-theme';

  // Apply persisted theme ASAP (also done inline in <head> to avoid FOUC)
  function current() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(KEY, theme); } catch (e) {}
    document.querySelectorAll('[data-theme-label]').forEach(function (el) {
      el.textContent = theme === 'dark' ? 'Dark' : 'Light';
    });
  }
  window.koanToggleTheme = function () {
    apply(current() === 'dark' ? 'light' : 'dark');
    if (window.lucide) lucide.createIcons();
  };

  document.addEventListener('DOMContentLoaded', function () {
    if (window.lucide) lucide.createIcons();

    // Sidebar / tab "active" toggles via [data-toggle-active] groups
    document.querySelectorAll('[data-active-group]').forEach(function (group) {
      group.addEventListener('click', function (e) {
        const item = e.target.closest('[data-active-item]');
        if (!item) return;
        group.querySelectorAll('[data-active-item]').forEach(function (el) {
          el.classList.remove('active');
          el.setAttribute('aria-selected', 'false');
        });
        item.classList.add('active');
        item.setAttribute('aria-selected', 'true');
      });
    });

    // Demo-only: simple modal open/close via [data-open] / [data-close]
    document.querySelectorAll('[data-open]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const t = document.getElementById(btn.getAttribute('data-open'));
        if (t) t.style.display = 'grid';
      });
    });
    document.querySelectorAll('[data-close]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const t = btn.closest('.k-scrim');
        if (t) t.style.display = 'none';
      });
    });

    // Copy-to-clipboard for [data-copy]
    document.querySelectorAll('[data-copy]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!navigator.clipboard || !navigator.clipboard.writeText) return;
        const old = btn.getAttribute('aria-label');
        navigator.clipboard.writeText(btn.getAttribute('data-copy')).then(function () {
          btn.setAttribute('aria-label', 'Copied!');
          setTimeout(function () { btn.setAttribute('aria-label', old || 'Copy'); }, 1400);
        }).catch(function () {
          btn.setAttribute('aria-label', 'Copy failed');
          setTimeout(function () { btn.setAttribute('aria-label', old || 'Copy'); }, 1400);
        });
      });
    });
  });
})();
