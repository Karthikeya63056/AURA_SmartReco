/**
 * AURA / SmartReco — Auth helpers (cookie session)
 * Logout, 401 handling, optional client-side nav hints.
 * Server remains source of truth via HttpOnly cookie.
 */
(function (global) {
  'use strict';

  const LOGIN_PATH = '/login';
  const HOME_PATH = '/';

  let unauthorizedHandled = false;

  function onUnauthorized(requestUrl) {
    // Avoid redirect loops / spam
    if (unauthorizedHandled) return;
    if (typeof requestUrl === 'string' && requestUrl.includes('/auth/login')) return;

    // Don't bounce public pages
    const path = window.location.pathname;
    if (path === LOGIN_PATH || path === '/register' || path === '/' || path === '/landing') {
      return;
    }

    unauthorizedHandled = true;

    if (global.AURA_UI && typeof global.AURA_UI.toast === 'function') {
      global.AURA_UI.toast('Please sign in to continue', 'info');
    }

    // Short delay so toast can show
    setTimeout(function () {
      window.location.href = LOGIN_PATH + '?next=' + encodeURIComponent(path);
    }, 400);
  }

  async function logout() {
    try {
      if (global.AURA_API) {
        await global.AURA_API.logout();
      } else {
        await fetch('/auth/logout', {
          method: 'POST',
          credentials: 'include',
        });
      }
    } catch (err) {
      console.warn('[AURA_AUTH] logout request failed', err);
    }

    // Clear any legacy localStorage token from older versions
    try {
      localStorage.removeItem('access_token');
    } catch (_) {}

    window.location.href = HOME_PATH;
  }

  function bindLogoutButtons() {
    document.querySelectorAll('[data-auth-logout], #logoutBtn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        logout();
      });
    });
  }

  function init() {
    bindLogoutButtons();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.AURA_AUTH = {
    logout,
    onUnauthorized,
    bindLogoutButtons,
  };
})(window);