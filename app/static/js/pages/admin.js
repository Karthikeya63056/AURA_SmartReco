/**
 * Admin product forms — basic UX helpers
 * Actual CRUD still posts to your API or form actions.
 */
(function () {
  'use strict';

  function init() {
    const deleteBtns = document.querySelectorAll('[data-admin-delete]');
    deleteBtns.forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        const title = btn.dataset.title || 'this product';
        if (!window.confirm('Delete "' + title + '"? This cannot be undone.')) {
          e.preventDefault();
          return;
        }
      });
    });

    // Toast on query flags e.g. ?saved=1
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get('saved') === '1' && window.AURA_UI) {
        AURA_UI.toast('Product saved', 'success');
      }
      if (params.get('deleted') === '1' && window.AURA_UI) {
        AURA_UI.toast('Product deleted', 'success');
      }
    } catch (_) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();