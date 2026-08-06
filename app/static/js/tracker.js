/**
 * SmartReco 2026 — Non-Blocking Behavior Tracking Library
 * Batches user behavior events (views, clicks, searches, dwell time)
 * and flushes every 5s or 20 events.
 */
(function () {
    'use strict';

    const ENDPOINT = '/api/events/batch';
    const BATCH_FLUSH_INTERVAL_MS = 5000;
    const MAX_QUEUE_SIZE = 20;

    let queue = [];
    let sessionId = getOrCreateSessionId();
    let pageStartTime = Date.now();
    let flushTimer = null;

    function getOrCreateSessionId() {
        let id = localStorage.getItem('smartreco_session_id');
        if (!id) {
            id = 'sess_' + Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
            localStorage.setItem('smartreco_session_id', id);
        }
        return id;
    }

    function generateIdempotencyKey() {
        return 'evt_' + Math.random().toString(36).substring(2, 11) + Date.now().toString(36);
    }

    function triggerRecommendationRefresh() {
        // Trigger UI refresh if refresh button or handler exists
        const refreshBtn = document.getElementById('refresh-recs-btn');
        if (refreshBtn) {
            console.log('[SmartTracker] High intent trigger detected! Auto-refreshing recommendations UI...');
            refreshBtn.click();
        } else if (typeof window.loadRecommendation === 'function') {
            window.loadRecommendation();
        }
    }

    window.SmartTracker = {
        getSessionId: function () {
            return sessionId;
        },

        track: function (eventType, payload = {}, idempotencyKey = null) {
            const eventItem = {
                session_id: sessionId,
                event_type: eventType,
                payload_json: payload,
                idempotency_key: idempotencyKey || generateIdempotencyKey(),
                created_at: new Date().toISOString()
            };

            queue.push(eventItem);

            if (queue.length >= MAX_QUEUE_SIZE) {
                SmartTracker.flush(false);
            }
        },

        flush: function (isUnload = false) {
            if (queue.length === 0) return;

            const eventsToSend = queue.splice(0, queue.length);
            const payload = JSON.stringify({ events: eventsToSend });

            if (isUnload && navigator.sendBeacon) {
                const blob = new Blob([payload], { type: 'application/json' });
                navigator.sendBeacon(ENDPOINT, blob);
            } else {
                fetch(ENDPOINT, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: payload
                })
                .then(response => {
                    if (response.ok) return response.json();
                    throw new Error('Batch event ingestion status ' + response.status);
                })
                .then(data => {
                    if (data && data.trigger && data.trigger.should_run_agent === true) {
                        triggerRecommendationRefresh();
                    }
                })
                .catch(err => console.warn('SmartTracker flush error:', err));
            }
        }
    };

    // Auto-flush on interval
    flushTimer = setInterval(function () {
        SmartTracker.flush(false);
    }, BATCH_FLUSH_INTERVAL_MS);

    // Page Unload / Visibility Change handling
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            const timeSpentSeconds = Math.round((Date.now() - pageStartTime) / 1000);
            SmartTracker.track('time_on_page', {
                url: window.location.pathname,
                time_spent_seconds: timeSpentSeconds
            });
            SmartTracker.flush(true);
        }
    });

    window.addEventListener('beforeunload', function () {
        SmartTracker.flush(true);
    });

    // Auto-track Page Views
    document.addEventListener('DOMContentLoaded', function () {
        SmartTracker.track('page_view', {
            url: window.location.pathname,
            referrer: document.referrer || null,
            title: document.title
        });

        // IntersectionObserver for Course Card Viewability
        if ('IntersectionObserver' in window) {
            const cardObserver = new IntersectionObserver((entries, observer) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const card = entry.target;
                        const courseId = card.getAttribute('data-course-id');
                        if (courseId) {
                            SmartTracker.track('course_impression', {
                                course_id: parseInt(courseId, 10),
                                title: card.getAttribute('data-course-title') || ''
                            });
                            observer.unobserve(card); // Track once per page load
                        }
                    }
                });
            }, { threshold: 0.5 });

            document.querySelectorAll('[data-course-id]').forEach(card => cardObserver.observe(card));
        }

        // Global click tracker for high-intent actions
        document.body.addEventListener('click', function (e) {
            const target = e.target.closest('[data-track-action]');
            if (target) {
                const action = target.getAttribute('data-track-action');
                const courseId = target.getAttribute('data-course-id');
                SmartTracker.track(action, {
                    course_id: courseId ? parseInt(courseId, 10) : null,
                    text: target.innerText.trim()
                });
            }
        });
    });
})();
