// events.js — drives the Live Events Feed panel on the dashboard.
//
// Polls GET /api/events (with optional type/severity filters) every 5
// seconds and renders results into #eventsFeedContainer. This is a new,
// standalone script — it does not touch attendance table logic, student
// table logic, fire-test logic, or after-hours-test logic in script.js.
//
// Depends on nothing else on the page except the DOM elements defined in
// dashboard/_events_feed.html. If those elements aren't present (e.g. this
// script accidentally loads on a page without the panel), everything here
// no-ops safely.

document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('eventsFeedContainer');
    if (!container) return; // panel not on this page — nothing to do

    const typeFilter = document.getElementById('eventsTypeFilter');
    const severityFilter = document.getElementById('eventsSeverityFilter');
    const refreshBtn = document.getElementById('eventsRefreshBtn');
    const liveIndicator = document.getElementById('eventsLiveIndicator');

    const POLL_INTERVAL_MS = 5000;
    let pollTimer = null;
    let isFetching = false;

    const SEVERITY_META = {
        critical: { color: '#ef4444', bg: 'rgba(239, 68, 68, 0.15)', border: 'rgba(239, 68, 68, 0.3)', label: 'CRITICAL' },
        warning: { color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.15)', border: 'rgba(245, 158, 11, 0.3)', label: 'WARNING' },
        info: { color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.15)', border: 'rgba(59, 130, 246, 0.3)', label: 'INFO' }
    };

    const TYPE_ICON = {
        fire: '🔥',
        after_hours: '⚠️',
        attendance: '👤',
        system: '⚙️'
    };

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    }

    function renderEvents(events) {
        if (!events || events.length === 0) {
            container.innerHTML = '<div class="events-feed-empty">No events recorded yet.</div>';
            return;
        }

        let html = '';
        events.forEach(function (ev) {
            const sev = SEVERITY_META[ev.severity] || SEVERITY_META.info;
            const icon = TYPE_ICON[ev.event_type] || '•';
            const confText = (ev.confidence !== null && ev.confidence !== undefined)
                ? `<span class="events-feed-conf">${(ev.confidence * 100).toFixed(0)}%</span>`
                : '';
            const sourceText = ev.camera_source
                ? `<span class="events-feed-source">${escapeHtml(ev.camera_source)}</span>`
                : '';

            html += `
                <div class="events-feed-item" style="border-left-color: ${sev.color};">
                    <div class="events-feed-icon" style="background:${sev.bg}; border-color:${sev.border};">${icon}</div>
                    <div class="events-feed-body">
                        <div class="events-feed-top-row">
                            <span class="events-feed-badge" style="background:${sev.bg}; color:${sev.color}; border-color:${sev.border};">${sev.label}</span>
                            <span class="events-feed-message">${escapeHtml(ev.message)}</span>
                        </div>
                        <div class="events-feed-meta-row">
                            <span class="events-feed-time">${escapeHtml(ev.timestamp)}</span>
                            ${sourceText}
                            ${confText}
                        </div>
                    </div>
                </div>`;
        });

        container.innerHTML = html;
    }

    function buildQueryString() {
        const params = new URLSearchParams();
        if (typeFilter && typeFilter.value) params.set('type', typeFilter.value);
        if (severityFilter && severityFilter.value) params.set('severity', severityFilter.value);
        params.set('limit', '50');
        return params.toString();
    }

    function fetchEvents() {
        if (isFetching) return; // avoid overlapping requests if one is slow
        isFetching = true;
        if (liveIndicator) liveIndicator.classList.add('pulsing');

        fetch('/api/events?' + buildQueryString())
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.success) {
                    renderEvents(data.events);
                } else {
                    container.innerHTML = '<div class="events-feed-empty error">Failed to load events.</div>';
                }
            })
            .catch(function (err) {
                console.error('events.js: fetch error:', err);
                container.innerHTML = '<div class="events-feed-empty error">Server error while loading events.</div>';
            })
            .finally(function () {
                isFetching = false;
            });
    }

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(fetchEvents, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    if (refreshBtn) refreshBtn.addEventListener('click', fetchEvents);
    if (typeFilter) typeFilter.addEventListener('change', fetchEvents);
    if (severityFilter) severityFilter.addEventListener('change', fetchEvents);

    // Pause polling when the tab isn't visible, resume when it is —
    // avoids piling up background requests for a hidden dashboard tab.
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            stopPolling();
        } else {
            fetchEvents();
            startPolling();
        }
    });

    window.addEventListener('beforeunload', stopPolling);

    fetchEvents();
    startPolling();
});