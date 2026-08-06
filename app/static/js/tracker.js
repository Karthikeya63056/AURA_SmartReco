/**
 * SmartReco Frontend Behavior Tracker (tracker.js)
 * Non-blocking, batched event ingestion with sendBeacon and IntersectionObserver.
 */

(function () {
    'use strict';

    const BATCH_FLUSH_INTERVAL_MS = 5000;
    const MAX_QUEUE_SIZE = 20;
    const ENDPOINT = '/api/events/batch';

    // Session Management
    function getOrCreateSessionId() {
        let sid = sessionStorage.getItem('smartreco_session_id');
        if (!sid) {
            sid = 'sess_' + Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
            sessionStorage.setItem('smartreco_session_id', sid);
        }
        return sid;
    }

    const sessionId = getOrCreateSessionId();
    let queue = [];
    let flushTimer = null;
    let pageStartTime = Date.now();

    // Helper for generating UUID/Idempotency key
    function generateIdempotencyKey() {
        return 'evt_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
    }

    // Core Tracker Object
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
                SmartTracker.flush();
            }
        },

        flush: function () {
            if (queue.length === 0) return;

            const eventsToSend = queue.splice(0, queue.length);
            const payload = JSON.stringify({ events: eventsToSend });

            if (navigator.sendBeacon) {
                const blob = new Blob([payload], { type: 'application/json' });
                navigator.sendBeacon(ENDPOINT, blob);
            } else {
                fetch(ENDPOINT, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: payload,
                    keepalive: true
                }).catch(err => console.error('SmartTracker flush failed:', err));
            }
        }
    };

    // Auto-flush on interval
    flushTimer = setInterval(function () {
        SmartTracker.flush();
    }, BATCH_FLUSH_INTERVAL_MS);

    // Page Unload / Visibility Change handling
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            const timeSpentSeconds = Math.round((Date.now() - pageStartTime) / 1000);
            SmartTracker.track('time_on_page', {
                url: window.location.pathname,
                time_spent_seconds: timeSpentSeconds
            });
            SmartTracker.flush();
        }
    });

    window.addEventListener('beforeunload', function () {
        SmartTracker.flush();
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
                        const courseTitle = card.getAttribute('data-course-title');
                        if (courseId) {
                            SmartTracker.track('course_viewability', {
                                course_id: parseInt(courseId, 10),
                                title: courseTitle
                            });
                            observer.unobserve(card); // Only track viewability once per page load
                        }
                    }
                });
            }, { threshold: 0.5 });

            document.querySelectorAll('[data-course-id]').forEach(card => {
                cardObserver.observe(card);
            });
        }
    });

    // Throttled Handlers
    window.SmartTrackerUtils = {
        throttle: function (func, limit) {
            let inThrottle;
            return function (...args) {
                if (!inThrottle) {
                    func.apply(this, args);
                    inThrottle = true;
                    setTimeout(() => inThrottle = false, limit);
                }
            };
        },

        debounce: function (func, delay) {
            let debounceTimer;
            return function (...args) {
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(() => func.apply(this, args), delay);
            };
        }
    };
})();
