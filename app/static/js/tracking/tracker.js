/**
 * AURA / SmartReco — Behavior Tracker
 * Batches events and flushes every 5s or 20 events.
 * Uses sendBeacon on unload so tracking never blocks the UI.
 *
 * Event types:
 *   page_view | course_impression | course_click | course_view
 *   search | wishlist | syllabus_view | enroll_preview | time_on_page
 *   rec_click | rec_dismiss
 *   faq_expand | instructor_view | share
 *
 * High-intent (TriggerEngine): wishlist | enroll_preview | syllabus_view
 */
(function (global) {
  'use strict';

  const ENDPOINT = '/api/events/batch';
  const BATCH_FLUSH_INTERVAL_MS = 5000;
  const MAX_QUEUE_SIZE = 20;
  const SESSION_KEY = 'smartreco_session_id';

  let queue = [];
  let sessionId = getOrCreateSessionId();
  let pageStartTime = Date.now();
  let flushTimer = null;
  let started = false;
  const impressedCourseIds = new Set();

  function getOrCreateSessionId() {
    try {
      let id = localStorage.getItem(SESSION_KEY);
      if (!id) {
        id = 'sess_' + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
        localStorage.setItem(SESSION_KEY, id);
      }
      return id;
    } catch {
      return 'sess_' + Date.now().toString(36);
    }
  }

  function generateIdempotencyKey() {
    return 'evt_' + Math.random().toString(36).slice(2, 10) + '_' + Date.now().toString(36);
  }

  function safeParseInt(value) {
    if (value == null || value === '') return null;
    const n = parseInt(value, 10);
    return Number.isFinite(n) ? n : null;
  }

  /**
   * Optional: after a high-intent trigger, nudge UI refresh
   */
  function maybeRefreshRecommendations(trigger) {
    if (!trigger || !trigger.should_run_agent) return;

    if (typeof global.triggerRecommendationRefresh === 'function') {
      try {
        global.triggerRecommendationRefresh({ silent: true });
        return;
      } catch (e) {
        console.warn('[SmartTracker] triggerRecommendationRefresh failed', e);
      }
    }

    if (typeof global.refreshRecs === 'function') {
      try {
        global.refreshRecs({ silent: true });
        return;
      } catch (e) {
        console.warn('[SmartTracker] refreshRecs failed', e);
      }
    }

    const btn =
      document.getElementById('refreshBtn') ||
      document.getElementById('refresh-recs-btn');
    if (btn && !btn.disabled) {
      btn.click();
    }
  }

  function buildEvent(eventType, payload, idempotencyKey) {
    return {
      session_id: sessionId,
      event_type: eventType,
      payload_json: payload && typeof payload === 'object' ? payload : {},
      idempotency_key: idempotencyKey || generateIdempotencyKey(),
    };
  }

  function flush(isUnload) {
    if (queue.length === 0) return;

    const eventsToSend = queue.splice(0, queue.length);
    const body = JSON.stringify({ events: eventsToSend });

    if (isUnload && typeof navigator.sendBeacon === 'function') {
      try {
        const blob = new Blob([body], { type: 'application/json' });
        navigator.sendBeacon(ENDPOINT, blob);
      } catch (e) {
        console.warn('[SmartTracker] sendBeacon failed', e);
      }
      return;
    }

    const send = function () {
      if (global.AURA_API && typeof global.AURA_API.postEvents === 'function') {
        return global.AURA_API.postEvents(eventsToSend);
      }
      return fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: body,
        credentials: 'include',
        keepalive: true,
      }).then(function (res) {
        if (!res.ok) throw new Error('events status ' + res.status);
        return res.json().catch(function () {
          return null;
        });
      }).then(function (data) {
        return { ok: true, data: data };
      });
    };

    send()
      .then(function (result) {
        const data = result && result.data ? result.data : result;
        if (data && data.trigger) {
          maybeRefreshRecommendations(data.trigger);
        }
      })
      .catch(function (err) {
        console.warn('[SmartTracker] flush error', err);
        if (queue.length < 100) {
          queue = eventsToSend.concat(queue).slice(0, 100);
        }
      });
  }

  function track(eventType, payload, idempotencyKey) {
    if (!eventType || typeof eventType !== 'string') return;

    queue.push(buildEvent(eventType, payload || {}, idempotencyKey));

    if (queue.length >= MAX_QUEUE_SIZE) {
      flush(false);
    }
  }

  function trackCourseClick(courseId, title, extra) {
    const payload = Object.assign(
      {
        course_id: safeParseInt(courseId),
        title: title || '',
      },
      extra || {}
    );
    track('course_click', payload);
  }

  function trackCourseView(courseId, title) {
    track('course_view', {
      course_id: safeParseInt(courseId),
      title: title || '',
    });
  }

  function trackSearch(query) {
    if (!query || !String(query).trim()) return;
    track('search', { query: String(query).trim() });
  }

  function trackWishlist(courseId, title) {
    track('wishlist', {
      course_id: safeParseInt(courseId),
      title: title || '',
    });
  }

  function trackSyllabusView(courseId) {
    track('syllabus_view', {
      course_id: safeParseInt(courseId),
    });
  }

  function trackEnrollPreview(courseId, title) {
    track('enroll_preview', {
      course_id: safeParseInt(courseId),
      title: title || '',
    });
  }

  function trackFaqExpand(courseId, title, question) {
    track('faq_expand', {
      course_id: safeParseInt(courseId),
      title: title || '',
      question: question || '',
    });
  }

  function trackInstructorView(courseId, title) {
    track('instructor_view', {
      course_id: safeParseInt(courseId),
      title: title || '',
    });
  }

  function trackShare(courseId, title, url) {
    track('share', {
      course_id: safeParseInt(courseId),
      title: title || '',
      url: url || (typeof window !== 'undefined' ? window.location.href : ''),
    });
  }

  function trackRecommendationClick(recommendationId, productId) {
    track('rec_click', {
      recommendation_id: safeParseInt(recommendationId),
      product_id: safeParseInt(productId),
    });
  }

  function trackRecommendationDismiss(recommendationId) {
    track('rec_dismiss', {
      recommendation_id: safeParseInt(recommendationId),
    });
  }

  function bindImpressionObserver() {
    if (!('IntersectionObserver' in global)) return;

    const observer = new IntersectionObserver(
      function (entries, obs) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          const card = entry.target;
          const courseId = card.getAttribute('data-course-id');
          if (!courseId) return;
          if (impressedCourseIds.has(courseId)) {
            obs.unobserve(card);
            return;
          }

          impressedCourseIds.add(courseId);
          track('course_impression', {
            course_id: safeParseInt(courseId),
            title: card.getAttribute('data-course-title') || '',
          });
          obs.unobserve(card);
        });
      },
      { threshold: 0.5 }
    );

    document.querySelectorAll('[data-course-id]').forEach(function (el) {
      observer.observe(el);
    });
  }

  function bindDelegatedClicks() {
    document.body.addEventListener('click', function (e) {
      const target = e.target.closest('[data-track-action]');
      if (!target) return;

      // Prefer <details> toggle handler for FAQ (course.js) — still allow click signal
      const action = target.getAttribute('data-track-action');
      if (!action) return;

      const courseId = target.getAttribute('data-course-id');
      const title = target.getAttribute('data-course-title') || '';

      const payload = {
        course_id: safeParseInt(courseId),
        title: title,
      };

      if (action === 'share') {
        payload.url = window.location.href;
      }

      track(action, payload);
    });
  }

  function bindUnload() {
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState !== 'hidden') return;

      const seconds = Math.round((Date.now() - pageStartTime) / 1000);
      track('time_on_page', {
        url: window.location.pathname,
        time_spent_seconds: seconds,
      });
      flush(true);
    });

    window.addEventListener('pagehide', function () {
      flush(true);
    });
  }

  function start() {
    if (started) return;
    started = true;

    track('page_view', {
      url: window.location.pathname,
      referrer: document.referrer || null,
      title: document.title || '',
    });

    bindImpressionObserver();
    bindDelegatedClicks();
    bindUnload();

    flushTimer = setInterval(function () {
      flush(false);
    }, BATCH_FLUSH_INTERVAL_MS);
  }

  function init() {
    start();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.SmartTracker = {
    track: track,
    flush: flush,
    getSessionId: function () {
      return sessionId;
    },
    trackCourseClick: trackCourseClick,
    trackCourseView: trackCourseView,
    trackSearch: trackSearch,
    trackWishlist: trackWishlist,
    trackSyllabusView: trackSyllabusView,
    trackEnrollPreview: trackEnrollPreview,
    trackFaqExpand: trackFaqExpand,
    trackInstructorView: trackInstructorView,
    trackShare: trackShare,
    trackRecommendationClick: trackRecommendationClick,
    trackRecommendationDismiss: trackRecommendationDismiss,
  };

  global.trackRecommendationClick = trackRecommendationClick;
  global.trackRecommendationDismiss = trackRecommendationDismiss;
})(window);