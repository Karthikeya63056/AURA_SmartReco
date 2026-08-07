/**
 * Search page — track search queries + result clicks
 */
(function () {
  'use strict';

  function init() {
    const results = document.getElementById('searchResults');
    const query = results
      ? results.dataset.query || ''
      : (document.querySelector('[name="q"]') && document.querySelector('[name="q"]').value) || '';

    if (query && window.SmartTracker) {
      SmartTracker.trackSearch(query);
    }

    // Form submit also tracks (for client-side navigation edge cases)
    const form = document.querySelector('form[action="/search"]');
    if (form) {
      form.addEventListener('submit', function () {
        const input = form.querySelector('[name="q"]');
        const q = input && input.value ? input.value.trim() : '';
        if (q && window.SmartTracker) {
          SmartTracker.trackSearch(q);
        }
      });
    }

    document.querySelectorAll('.course-card[data-course-id]').forEach(function (card) {
      card.addEventListener('click', function () {
        if (window.SmartTracker) {
          SmartTracker.trackCourseClick(
            card.dataset.courseId,
            card.dataset.courseTitle || '',
            { source: 'search', query: query }
          );
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