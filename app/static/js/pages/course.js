/**
 * Course detail — view tracking + CTA feedback
 * Events: course_view, enroll_preview, wishlist, syllabus_view,
 *         share / faq_expand / instructor_view (via data-track-action;
 *         require allowlist updates in schemas/event.py).
 */
(function () {
  'use strict';

  function toast(message, type) {
    if (window.AURA_UI && typeof AURA_UI.toast === 'function') {
      AURA_UI.toast(message, type || 'success');
      return;
    }
    console.log('[course]', message);
  }

  function setBusy(btn, label) {
    if (!btn) return;
    if (window.AURA_UI && typeof AURA_UI.setButtonLoading === 'function') {
      AURA_UI.setButtonLoading(btn, true, label || 'Saved');
      setTimeout(function () {
        AURA_UI.setButtonLoading(btn, false);
        if (label) {
          btn.textContent = label;
          btn.disabled = true;
        }
      }, 500);
      return;
    }
    if (label) {
      btn.textContent = label;
      btn.disabled = true;
    }
  }

  function track(eventType, payload) {
    if (window.SmartTracker && typeof SmartTracker.track === 'function') {
      SmartTracker.track(eventType, payload || {});
      return;
    }
    // Fallback helpers when present
    if (eventType === 'wishlist' && window.SmartTracker && SmartTracker.trackWishlist) {
      SmartTracker.trackWishlist(payload.course_id, payload.title);
    } else if (eventType === 'syllabus_view' && window.SmartTracker && SmartTracker.trackSyllabusView) {
      SmartTracker.trackSyllabusView(payload.course_id);
    } else if (eventType === 'course_view' && window.SmartTracker && SmartTracker.trackCourseView) {
      SmartTracker.trackCourseView(payload.course_id, payload.title);
    }
  }

  function coursePayload(detail) {
    return {
      course_id: parseInt(detail.dataset.courseId, 10) || null,
      title: detail.dataset.courseTitle || '',
    };
  }

  function init() {
    const detail = document.getElementById('courseDetail');
    if (!detail) return;

    const base = coursePayload(detail);
    const courseId = base.course_id;
    const courseTitle = base.title;

    // Always record a course view on detail open
    if (window.SmartTracker && SmartTracker.trackCourseView) {
      SmartTracker.trackCourseView(courseId, courseTitle);
    } else {
      track('course_view', base);
    }

    // Enroll Preview → high-intent enroll_preview
    const enrollBtn = document.getElementById('enrollBtn');
    if (enrollBtn) {
      enrollBtn.addEventListener('click', function () {
        track('enroll_preview', {
          course_id: courseId,
          title: courseTitle,
        });
        toast('Preview interest recorded — AURA will use this signal', 'success');
        setBusy(enrollBtn, 'Preview noted');
      });
    }

    // Save → wishlist (high-intent)
    const wishlistBtn = document.getElementById('wishlistBtn');
    if (wishlistBtn) {
      wishlistBtn.addEventListener('click', function () {
        if (window.SmartTracker && SmartTracker.trackWishlist) {
          SmartTracker.trackWishlist(courseId, courseTitle);
        } else {
          track('wishlist', { course_id: courseId, title: courseTitle });
        }
        toast('Saved to your list — recommendations will update', 'success');
        setBusy(wishlistBtn, 'Saved');
      });
    }

    // Share → share event (+ Web Share API when available)
    const shareBtn = document.getElementById('shareBtn');
    if (shareBtn) {
      shareBtn.addEventListener('click', function () {
        track('share', {
          course_id: courseId,
          title: courseTitle,
          url: window.location.href,
        });

        if (navigator.share) {
          navigator.share({
            title: courseTitle,
            url: window.location.href,
          }).catch(function () {
            /* user cancelled */
          });
        } else if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(window.location.href).then(function () {
            toast('Link copied', 'success');
          }).catch(function () {
            toast('Share signal recorded', 'success');
          });
          return;
        }
        toast('Share signal recorded', 'success');
      });
    }

    // Syllabus rows — explicit helper + data-track-action still works via tracker
    document.querySelectorAll('[data-track-action="syllabus_view"]').forEach(function (el) {
      el.addEventListener('click', function () {
        if (window.SmartTracker && SmartTracker.trackSyllabusView) {
          SmartTracker.trackSyllabusView(courseId);
        } else {
          track('syllabus_view', { course_id: courseId });
        }
      });
    });

    // FAQ accordion — fire once per open (not on close)
    document.querySelectorAll('details.faq-item').forEach(function (el) {
      el.addEventListener('toggle', function () {
        if (!el.open) return;
        track('faq_expand', {
          course_id: courseId,
          title: courseTitle,
          question: (el.querySelector('summary') || {}).textContent || '',
        });
      });
    });

    // Instructor card — one view signal on first click
    const instructor = document.getElementById('instructorSection');
    if (instructor) {
      let instructorTracked = false;
      instructor.addEventListener('click', function () {
        if (instructorTracked) return;
        instructorTracked = true;
        track('instructor_view', {
          course_id: courseId,
          title: courseTitle,
        });
      });
    }

    // Legacy "I'm Interested" button if an older template is still used
    const interestBtn = document.getElementById('interestBtn');
    if (interestBtn) {
      interestBtn.addEventListener('click', function () {
        if (window.SmartTracker && SmartTracker.trackWishlist) {
          SmartTracker.trackWishlist(courseId, courseTitle);
        } else {
          track('wishlist', { course_id: courseId, title: courseTitle });
        }
        toast('AURA noted your interest — recommendations will update', 'success');
        setBusy(interestBtn, 'Interest saved');
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();