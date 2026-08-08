/**
 * Dashboard — refresh recommendations + render narrative markdown
 */
(function () {
  'use strict';

  let refreshInFlight = false;

  function renderNarrative(narrativeEl) {
    if (window.AURA_UI) {
      AURA_UI.renderMarkdown(narrativeEl);
    } else if (typeof marked !== 'undefined') {
      const html = marked.parse(narrativeEl.textContent || '');
      narrativeEl.innerHTML = (typeof DOMPurify !== 'undefined') ? DOMPurify.sanitize(html) : html;
    }
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

    const chips = recCard.querySelector('.rec-chips');
    if (chips) {
      const reasons = Array.isArray(payload.product_reasons) ? payload.product_reasons : [];
      const fragment = document.createDocumentFragment();
      payload.product_ids.slice(0, 4).forEach(function (productId, index) {
        const group = document.createElement('div');
        group.className = 'rec-chip-group';

        const link = document.createElement('a');
        link.className = 'rec-chip';
        link.href = '/course/' + encodeURIComponent(productId);
        link.textContent = 'View course';
        link.dataset.recommendationId = String(payload.id || 0);
        link.dataset.productId = String(productId);
        link.addEventListener('click', function () {
          if (window.trackRecommendationClick) {
            window.trackRecommendationClick(payload.id || 0, productId);
          }
        });
        group.appendChild(link);

        if (reasons[index]) {
          const reason = document.createElement('span');
          reason.className = 'rec-reason-chip';
          reason.textContent = reasons[index];
          group.appendChild(reason);
        }
        fragment.appendChild(group);
      });
      chips.replaceChildren(fragment);
    }

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
