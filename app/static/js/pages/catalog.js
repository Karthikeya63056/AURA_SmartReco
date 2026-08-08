/**
 * Catalog — category filters + course click tracking
 */
(function () {
  'use strict';

  function updateFilterUrl(filter) {
    const url = new URL(window.location.href);
    if (filter === 'all') {
      url.searchParams.delete('category');
    } else {
      url.searchParams.set('category', filter);
    }
    window.history.pushState({}, '', url.pathname + url.search + url.hash);
  }

  function applyFilter(filter, options) {
    options = options || {};
    document.querySelectorAll('.course-card').forEach(function (card) {
      const category = card.getAttribute('data-category') || '';
      const show = filter === 'all' || category === filter;
      card.style.display = show ? '' : 'none';
    });

    if (options.updateUrl) updateFilterUrl(filter);
    if (!options.silent && window.SmartTracker) {
      SmartTracker.track('filter', { category: filter });
    }
  }

  function initFromQuery() {
    try {
      const params = new URLSearchParams(window.location.search);
      const category = params.get('category');
      if (!category) return;

      const btn = document.querySelector('.filter-btn[data-filter="' + CSS.escape(category) + '"]');
      if (btn) {
        document.querySelectorAll('.filter-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        btn.classList.add('active');
        applyFilter(category, { silent: true });
      }
    } catch (e) {
      console.warn('[catalog] query filter failed', e);
    }
  }

  function init() {
    document.querySelectorAll('.filter-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        document.querySelectorAll('.filter-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        btn.classList.add('active');
        applyFilter(btn.dataset.filter || 'all', { updateUrl: true });
      });
    });

    document.querySelectorAll('.course-card[data-course-id]').forEach(function (card) {
      card.addEventListener('click', function () {
        if (window.SmartTracker) {
          SmartTracker.trackCourseClick(
            card.dataset.courseId,
            card.dataset.courseTitle || '',
            { source: 'catalog' }
          );
        }
      });
    });

    initFromQuery();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
