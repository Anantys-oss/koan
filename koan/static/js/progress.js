/* Live mission timeline for /progress.
   Consumes /api/progress/stream (entries + content) and /api/state/stream
   (elapsed / label / execution). All agent text is set via textContent. */
(function () {
    'use strict';

    var MAX_THINKING_DOTS = 50;

    var timeline = document.getElementById('progress-timeline');
    var output = document.getElementById('progress-output');
    var status = document.getElementById('progress-status');
    var autoscroll = document.getElementById('autoscroll');
    var rawToggle = document.getElementById('raw-toggle');
    var missionHeader = document.getElementById('mission-header');
    var missionTitle = document.getElementById('mission-title');
    var missionProject = document.getElementById('mission-project');
    var missionElapsed = document.getElementById('mission-elapsed');
    var missionState = document.getElementById('mission-state');

    var lastPayload = null;
    var lastState = null;

    function setStatus(text, kind) {
        status.textContent = '';
        var dot = document.createElement('span');
        dot.className = 'k-status__dot';
        status.className = 'k-status k-status--' + kind;
        status.appendChild(dot);
        status.appendChild(document.createTextNode(' ' + text));
    }

    function formatElapsed(sec) {
        if (sec == null || sec < 0 || !isFinite(sec)) return '—';
        sec = Math.floor(sec);
        if (sec < 60) return sec + 's';
        var m = Math.floor(sec / 60);
        var s = sec % 60;
        if (m < 60) return m + 'm ' + s + 's';
        var h = Math.floor(m / 60);
        m = m % 60;
        return h + 'h ' + m + 'm';
    }

    function scrollActive() {
        if (!autoscroll || !autoscroll.checked) return;
        if (rawToggle && rawToggle.checked) {
            output.scrollTop = output.scrollHeight;
        } else {
            timeline.scrollTop = timeline.scrollHeight;
        }
    }

    function kindClass(kind) {
        switch (kind) {
            case 'tool_use': return 'tl-row tl-tool';
            case 'text': return 'tl-row tl-text';
            case 'thinking': return 'tl-row tl-thinking';
            case 'result': return 'tl-row tl-result';
            case 'tool_error': return 'tl-row tl-error';
            case 'warning': return 'tl-row tl-warn';
            case 'tool_end': return 'tl-row tl-tool-end';
            case 'session': return 'tl-row tl-session';
            case 'meta': return 'tl-row tl-raw';
            default: return 'tl-row tl-raw';
        }
    }

    function renderThinkingRow(entry) {
        var row = document.createElement('div');
        row.className = kindClass('thinking');
        var count = Math.min(entry.count || 1, MAX_THINKING_DOTS);
        var dots = document.createElement('span');
        dots.className = 'tl-dots';
        dots.textContent = '•'.repeat(count);
        row.appendChild(dots);
        if ((entry.count || 1) > 1) {
            var label = document.createElement('span');
            label.className = 'tl-count';
            label.textContent = '×' + entry.count;
            row.appendChild(label);
        }
        return row;
    }

    function renderEntry(entry) {
        if (entry.kind === 'thinking') {
            return renderThinkingRow(entry);
        }
        var row = document.createElement('div');
        row.className = kindClass(entry.kind);

        if (entry.icon) {
            var icon = document.createElement('span');
            icon.className = 'tl-icon';
            icon.textContent = entry.icon;
            row.appendChild(icon);
        }

        if (entry.kind === 'tool_use') {
            var name = document.createElement('span');
            name.className = 'tl-tool-name';
            name.textContent = entry.tool_name || entry.label || '';
            row.appendChild(name);
            if (entry.preview) {
                var preview = document.createElement('span');
                preview.className = 'tl-preview';
                preview.textContent = entry.preview;
                row.appendChild(preview);
            }
            return row;
        }

        var text = document.createElement('span');
        text.className = 'tl-body';
        text.textContent = entry.preview || entry.label || entry.raw || '';
        row.appendChild(text);
        return row;
    }

    function renderTimeline(entries) {
        timeline.textContent = '';
        if (!entries || !entries.length) {
            timeline.textContent = lastPayload && lastPayload.active
                ? 'Mission started — waiting for agent output…'
                : 'No active mission. Waiting for output…';
            return;
        }
        var frag = document.createDocumentFragment();
        for (var i = 0; i < entries.length; i++) {
            frag.appendChild(renderEntry(entries[i]));
        }
        timeline.appendChild(frag);
    }

    function updateHeader() {
        var header = (lastPayload && lastPayload.header) || {};
        var active = lastPayload && lastPayload.active;
        var title = header.title || '';
        var project = header.project || '';
        var stateLabel = '—';
        var elapsedSec = null;

        if (lastState) {
            if (lastState.label) stateLabel = lastState.label;
            var ex = lastState.execution || {};
            if (ex.state === 'working' || ex.state === 'stalled' || ex.state === 'zombie') {
                elapsedSec = ex.elapsed;
                if (ex.state === 'stalled' || ex.state === 'zombie') {
                    stateLabel = (lastState.label || 'Working') + ' (' + ex.state + ')';
                }
            } else if (typeof lastState.elapsed === 'number') {
                elapsedSec = lastState.elapsed;
            }
            if (!project && lastState.project) project = lastState.project;
        }

        if (active || title) {
            missionHeader.hidden = false;
            missionTitle.textContent = title || '—';
            missionProject.textContent = project || '—';
            missionElapsed.textContent = formatElapsed(elapsedSec);
            missionState.textContent = active ? stateLabel : 'Idle';
        } else {
            missionHeader.hidden = true;
        }
    }

    function applyView() {
        if (!lastPayload) return;
        var raw = rawToggle && rawToggle.checked;
        if (raw) {
            timeline.hidden = true;
            output.hidden = false;
            output.textContent = lastPayload.content || (
                lastPayload.active
                    ? 'Mission started — waiting for agent output…'
                    : 'Waiting for output…'
            );
        } else {
            output.hidden = true;
            timeline.hidden = false;
            renderTimeline(lastPayload.entries || []);
        }
        updateHeader();
        scrollActive();
    }

    function onProgressPayload(data) {
        lastPayload = data;
        applyView();
    }

    function onStatePayload(data) {
        lastState = data;
        updateHeader();
    }

    function connectProgress() {
        var source = new EventSource('/api/progress/stream');
        source.onopen = function () { setStatus('Connected', 'success'); };
        source.onmessage = function (event) {
            try {
                onProgressPayload(JSON.parse(event.data));
            } catch (e) { /* ignore malformed */ }
        };
        source.onerror = function () {
            setStatus('Disconnected — reconnecting…', 'queued');
            source.close();
            setTimeout(connectProgress, 3000);
        };
    }

    function connectState() {
        var source = new EventSource('/api/state/stream');
        source.onmessage = function (event) {
            try {
                onStatePayload(JSON.parse(event.data));
            } catch (e) { /* ignore malformed */ }
        };
        source.onerror = function () {
            source.close();
            setTimeout(connectState, 3000);
        };
    }

    if (rawToggle) {
        rawToggle.addEventListener('change', applyView);
    }
    if (autoscroll) {
        autoscroll.addEventListener('change', scrollActive);
    }

    connectProgress();
    connectState();
})();
