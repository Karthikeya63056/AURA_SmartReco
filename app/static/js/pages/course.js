/**
 * Course detail — view tracking + wishlist toggle (real store, not just events)
 */
(function () {
  'use strict';

  function init() {
    const detail = document.getElementById('courseDetail');
    if (!detail) return;

    const courseId = detail.dataset.courseId;
    const courseTitle = detail.dataset.courseTitle || '';

    // Track the initial page view
    if (window.SmartTracker) {
      SmartTracker.trackCourseView(courseId, courseTitle);
    }

    const interestBtn = document.getElementById('interestBtn');
    if (interestBtn) {
      interestBtn.addEventListener('click', function () {
        fetch('/api/wishlist/' + courseId, { method: 'POST', credentials: 'include' })
          .then(function (r) {
            if (!r.ok) throw new Error('wishlist toggle failed');
            return r.json();
          })
          .then(function (data) {
            const saved = !!data.added;
            
            // 1. Update local state attributes
            interestBtn.dataset.saved = saved ? '1' : '0';
            
            // 2. Update UI text and button classes
            interestBtn.textContent = saved ? 'Saved to wishlist ✓' : 'Save to wishlist';
            interestBtn.classList.toggle('btn-cyan', saved);
            interestBtn.classList.toggle('btn-outline', !saved);

            // 3. Track the appropriate behavioral signal
            if (window.SmartTracker) {
              if (saved) {
                // High-intent signal for the TriggerEngine
                SmartTracker.trackWishlist(courseId, courseTitle);
              } else {
                SmartTracker.track('wishlist_remove', {
                  course_id: parseInt(courseId, 10),
                  title: courseTitle,
                });
              }
            }
            
            // 4. Show user feedback
            if (window.AURA_UI) {
              AURA_UI.toast(
                saved ? 'Saved to your wishlist' : 'Removed from wishlist',
                'success'
              );
            }
          })
          .catch(function () {
            // API failed or user is unauthenticated
            if (window.AURA_UI) {
              AURA_UI.toast('Sign in to save courses to your wishlist', 'error');
            }
          });
      });
    }

    // Syllabus expanders tracking
    document.querySelectorAll('[data-track-action="syllabus_view"]').forEach(function (el) {
      el.addEventListener('click', function () {
        if (window.SmartTracker) {
          SmartTracker.trackSyllabusView(courseId);
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();