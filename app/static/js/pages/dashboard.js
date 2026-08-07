/**
 * Dashboard — refresh recommendations + render narrative markdown
 */
(function () {
  'use strict';

  async function refreshRecs(options) {
    options = options || {};
    const silent = !!options.silent;
    const btn = document.getElementById('refreshBtn');

    if (window.AURA_UI) {
      AURA_UI.setButtonLoading(btn, true, 'Analyzing…');
    } else if (btn) {
      btn.disabled = true;
      btn.textContent = 'Analyzing…';
    }

    try {
      const result = await window.AURA_API.refreshRecommendations();

      if (result.ok) {
        if (!silent && window.AURA_UI) {
          AURA_UI.toast('Recommendations updated', 'success');
        }
        // Reload so server-rendered narrative + chips stay in sync
        window.location.reload();
        return;
      }

      const status = result.status;
      if (status === 401) {
        if (window.AURA_UI) AURA_UI.toast('Please sign in to refresh', 'info');
        window.location.href = '/login?next=/dashboard';
        return;
      }

      const msg =
        (result.data && result.data.detail) ||
        'Could not refresh right now. Explore more courses first.';
      if (window.AURA_UI) {
        AURA_UI.toast(typeof msg === 'string' ? msg : 'Refresh failed', 'error');
      }
    } catch (err) {
      console.error(err);
      if (window.AURA_UI) {
        AURA_UI.toast('Something went wrong while refreshing', 'error');
      }
    } finally {
      if (window.AURA_UI) {
        AURA_UI.setButtonLoading(btn, false);
      } else if (btn) {
        btn.disabled = false;
        btn.textContent = 'Refresh Recommendations';
      }
    }
  }

  // Expose for SmartTracker auto-refresh
  window.refreshRecs = refreshRecs;

  function init() {
    const narrativeEl = document.getElementById('narrativeContent');
    if (narrativeEl && window.AURA_UI) {
        AURA_UI.renderMarkdown(narrativeEl);
    } else if (narrativeEl && typeof marked !== 'undefined') {
      const html = marked.parse(narrativeEl.textContent || '');
      narrativeEl.innerHTML = (typeof DOMPurify !== 'undefined') ? DOMPurify.sanitize(html) : html;
    }

    const btn = document.getElementById('refreshBtn');
    if (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        refreshRecs();
      });
    }

    // Course card clicks on dashboard
    document.querySelectorAll('.course-card[data-course-id]').forEach(function (card) {
      card.addEventListener('click', function () {
        if (window.SmartTracker) {
          SmartTracker.trackCourseClick(
            card.dataset.courseId,
            card.dataset.courseTitle || '',
            { source: 'dashboard' }
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