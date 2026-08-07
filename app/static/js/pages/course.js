/**
 * Course detail — view tracking + wishlist ("I'm interested")
 */
(function () {
  'use strict';

  function init() {
    const detail = document.getElementById('courseDetail');
    if (!detail) return;

    const courseId = detail.dataset.courseId;
    const courseTitle = detail.dataset.courseTitle || '';

    if (window.SmartTracker) {
      SmartTracker.trackCourseView(courseId, courseTitle);
    }

    const interestBtn = document.getElementById('interestBtn');
    if (interestBtn) {
      interestBtn.addEventListener('click', function () {
        if (window.SmartTracker) {
          // Must be "wishlist" — TriggerEngine high-intent set
          SmartTracker.trackWishlist(courseId, courseTitle);
        }

        if (window.AURA_UI) {
          AURA_UI.toast('AURA noted your interest — recommendations will update', 'success');
          AURA_UI.setButtonLoading(interestBtn, true, 'Noted');
          setTimeout(function () {
            AURA_UI.setButtonLoading(interestBtn, false);
            interestBtn.textContent = 'Interest saved';
            interestBtn.disabled = true;
          }, 600);
        } else {
          interestBtn.textContent = 'Interest saved';
          interestBtn.disabled = true;
        }
      });
    }

    // Optional syllabus expanders
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