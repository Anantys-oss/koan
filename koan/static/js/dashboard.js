/* Kōan Dashboard — shared JavaScript
   Theme handling lives in koan.js (window.koanToggleTheme) + the no-flash boot
   script in base.html. This file owns dashboard chrome: mobile sidebar, SSE
   attention badge + favicon, project filter, and keyboard shortcuts. */

/* ========== Mobile sidebar drawer ========== */
(function () {
    document.addEventListener('DOMContentLoaded', function () {
        var shell = document.getElementById('app-shell');
        var toggle = document.getElementById('sidebar-toggle');
        var scrim = document.getElementById('sidebar-scrim');
        if (!shell) return;
        function close() { shell.classList.remove('sidebar-open'); }
        if (toggle) toggle.addEventListener('click', function () { shell.classList.toggle('sidebar-open'); });
        if (scrim) scrim.addEventListener('click', close);
        // Close after navigating on mobile
        shell.querySelectorAll('.k-nav__item').forEach(function (a) {
            a.addEventListener('click', close);
        });
    });
})();

/* ========== Nav Attention Badge + Favicon (SSE) ========== */
(function () {
    var badge = null;
    var faviconEl = null;

    function updateBadge(count) {
        if (!badge) badge = document.getElementById('nav-attention-badge');
        if (!badge) return;
        if (count > 0) {
            badge.textContent = count;
            badge.style.display = '';
        } else {
            badge.style.display = 'none';
        }
    }

    function updateFavicon(status) {
        if (!faviconEl) faviconEl = document.getElementById('favicon');
        if (!faviconEl) return;
        var base = faviconEl.getAttribute('data-base') || '/static/favicon/';
        var map = {
            'working': 'green.svg',
            'running': 'green.svg',
            'sleeping': 'green.svg',
            'contemplating': 'green.svg',
            'paused': 'orange.svg',
            'stopped': 'red.svg',
            'error_recovery': 'red.svg',
        };
        var file = map[status] || 'default.svg';
        faviconEl.href = base + file;

        var titleMap = { 'green.svg': '🟢', 'orange.svg': '🟡', 'red.svg': '🔴', 'default.svg': '⚪' };
        var prefix = titleMap[file] || '';
        var title = document.title.replace(/^[🟢🟡🔴⚪]\s*/, '');
        document.title = prefix ? (prefix + ' ' + title) : title;
    }

    function connectAttentionSSE() {
        var src = new EventSource('/api/state/stream');
        src.onmessage = function (e) {
            try {
                var data = JSON.parse(e.data);
                if (typeof data.attention_count === 'number') {
                    updateBadge(data.attention_count);
                }
                if (data.status) {
                    updateFavicon(data.status);
                }
            } catch (ex) {}
        };
        src.onerror = function () {
            src.close();
            updateFavicon('');
            setTimeout(connectAttentionSSE, 5000);
        };
    }

    document.addEventListener('DOMContentLoaded', function () {
        connectAttentionSSE();
    });
})();

/* ========== Project Filter ========== */
(function () {
    var KEY = 'koan_project';

    document.addEventListener('DOMContentLoaded', function () {
        var sel = document.getElementById('project-filter');
        if (!sel) return;

        fetch('/api/projects')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var projects = data.projects || [];
                if (projects.length < 2) { sel.style.display = 'none'; return; }
                projects.forEach(function (p) {
                    var opt = document.createElement('option');
                    opt.value = p;
                    opt.textContent = p;
                    sel.appendChild(opt);
                });
                var saved;
                try { saved = localStorage.getItem(KEY) || ''; } catch (e) { saved = ''; }
                var params = new URLSearchParams(window.location.search);
                var current = params.get('project') || '';
                var hasProjectParam = params.has('project');
                if (hasProjectParam && !current) {
                    try { localStorage.removeItem(KEY); } catch (e) {}
                    if (window.location.search) {
                        window.location.href = window.location.pathname;
                    }
                } else if (current) {
                    sel.value = current;
                    try { localStorage.setItem(KEY, current); } catch (e) {}
                } else if (saved) {
                    sel.value = saved;
                    params.set('project', saved);
                    window.location.search = params.toString();
                }
            })
            .catch(function () {});

        sel.addEventListener('change', function () {
            var val = sel.value;
            try {
                if (val) { localStorage.setItem(KEY, val); }
                else { localStorage.removeItem(KEY); }
            } catch (e) {}
            var params = new URLSearchParams(window.location.search);
            if (val) { params.set('project', val); }
            else { params.delete('project'); }
            var qs = params.toString();
            window.location.href = window.location.pathname + (qs ? '?' + qs : '');
        });
    });
})();

/* ========== Keyboard Shortcuts ========== */
(function () {
    var SHORTCUTS = {
        'm': '/missions',
        'p': '/prs',
        'u': '/usage',
        'j': '/journal',
        'l': '/plans',
        'c': '/chat',
        'd': '/',
        'g': '/progress',
        'a': '/agent',
        'r': '/rules',
    };

    function isInputFocused() {
        var el = document.activeElement;
        if (!el) return false;
        var tag = el.tagName.toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
    }

    function showHelp() {
        var overlay = document.getElementById('shortcuts-help');
        if (overlay) overlay.classList.add('visible');
    }
    function hideHelp() {
        var overlay = document.getElementById('shortcuts-help');
        if (overlay) overlay.classList.remove('visible');
    }

    document.addEventListener('keydown', function (e) {
        if (isInputFocused()) return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;

        var key = e.key.toLowerCase();
        if (key === '?' || (e.shiftKey && e.key === '?')) {
            e.preventDefault();
            showHelp();
            return;
        }
        if (key === 'escape') { hideHelp(); return; }

        var dest = SHORTCUTS[key];
        if (dest) {
            e.preventDefault();
            window.location.href = dest;
        }
    });

    document.addEventListener('DOMContentLoaded', function () {
        var openBtn = document.getElementById('shortcuts-open');
        if (openBtn) openBtn.addEventListener('click', showHelp);
        var overlay = document.getElementById('shortcuts-help');
        if (overlay) {
            overlay.addEventListener('click', function (e) {
                if (e.target === overlay) hideHelp();
            });
        }
    });
})();
