/**
 * Dashboard — refresh recommendations + render narrative markdown
 */
(function () {
  'use strict';

  let refreshInFlight = false;

  function renderNarrative(narrativeEl) {
    if (window.AURA_UI) {
      AURA_UI.renderMarkdown(narrativeEl);
    } else if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
      const html = marked.parse(narrativeEl.textContent || '');
      narrativeEl.innerHTML = DOMPurify.sanitize(html);
    }
    // No sanitizer available — leave plain text in place
  }

  function updateRecommendationsInPlace(payload) {
    if (!payload || typeof payload.narrative !== 'string' || !Array.isArray(payload.product_ids)) {
      return false;
    }

    const narrativeEl = document.getElementById('narrativeContent');
    const recCard = document.querySelector('.rec-side-card');
    if (!narrativeEl || !recCard) return false;

    narrativeEl.textContent = payload.narrative;
    renderNarrative(narrativeEl);
    if (payload.id != null) recCard.dataset.recommendationId = String(payload.id);

    // Update the real recommendation links (.rec-courses), not .rec-chips
    const productIds = payload.product_ids.slice(0, 4);
    const reasons = Array.isArray(payload.product_reasons) ? payload.product_reasons : [];

    function updateCourseLink(btn, productId) {
      if (!btn || productId == null) return;
      btn.href = '/course/' + encodeURIComponent(productId);
      btn.dataset.productId = String(productId);
      if (payload.id != null) btn.dataset.recommendationId = String(payload.id);
    }

    const primaryWrap = recCard.querySelector('.rec-primary');
    if (primaryWrap) {
      updateCourseLink(primaryWrap.querySelector('.rec-primary-btn'), productIds[0]);
      const chip = primaryWrap.querySelector('.rec-reason-chip');
      if (chip && reasons[0]) chip.textContent = reasons[0];
    }

    recCard.querySelectorAll('.rec-secondary').forEach(function (wrap, index) {
      updateCourseLink(wrap.querySelector('.rec-secondary-btn'), productIds[index + 1]);
      const chip = wrap.querySelector('.rec-reason-chip');
      const reason = reasons[index + 1];
      if (chip && reason) chip.textContent = reason;
    });

    const statusBadges = recCard.querySelector('.rec-status-badges');
    if (statusBadges) {
      statusBadges.replaceChildren();
      if (payload.quality_score != null) {
        const score = document.createElement('span');
        score.className = 'badge badge-cyan';
        score.textContent = 'score ' + payload.quality_score;
        statusBadges.appendChild(score);
      }
      if (payload.trigger_reason) {
        const trigger = document.createElement('span');
        trigger.className = 'badge';
        trigger.textContent = payload.trigger_reason;
        statusBadges.appendChild(trigger);
      }
    }

    return true;
  }

  async function refreshRecs(options) {
    if (refreshInFlight) return;
    refreshInFlight = true;
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
        if (!updateRecommendationsInPlace(result.data)) {
          window.location.reload();
        }
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
      refreshInFlight = false;
      if (window.AURA_UI) {
        AURA_UI.setButtonLoading(btn, false);
      } else if (btn) {
        btn.disabled = false;
        btn.textContent = 'Refresh Recommendations';
      }
    }
  }

  // Single entry point for manual and tracker-triggered refreshes.
  window.triggerRecommendationRefresh = refreshRecs;
  window.refreshRecs = refreshRecs;

  function init() {
    const narrativeEl = document.getElementById('narrativeContent');
    if (narrativeEl) renderNarrative(narrativeEl);

    // Poll for fresh recommendations every 30s; never fight a manual refresh
    if (document.getElementById('narrativeContent')) {
      setInterval(async () => {
        if (refreshInFlight) return;
        try {
          const res = await window.AURA_API.get('/api/recommendations/current');
          // Ignore 204 (nothing yet) and non-OK statuses silently
          if (res.status === 200 && res.data && !refreshInFlight) {
            updateRecommendationsInPlace(res.data);
          }
        } catch (err) {
          /* transient network error — ignore */
        }
      }, 30000);
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
