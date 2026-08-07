/**
 * Login + Register form handlers
 * Expects cookie auth: server sets HttpOnly cookie on success.
 */
(function () {
  'use strict';

  function getQueryParam(name) {
    try {
      return new URLSearchParams(window.location.search).get(name);
    } catch {
      return null;
    }
  }

  function safeNextPath() {
    const next = getQueryParam('next');
    if (next && next.startsWith('/') && !next.startsWith('//')) {
      return next;
    }
    return '/dashboard';
  }

  async function handleLogin(form) {
    const emailEl = form.querySelector('#email, [name="username"], [name="email"]');
    const passwordEl = form.querySelector('#password, [name="password"]');
    const submitBtn = form.querySelector('[type="submit"]');

    const email = (emailEl && emailEl.value || '').trim();
    const password = passwordEl && passwordEl.value || '';

    if (!email || !password) {
      if (window.AURA_UI) AURA_UI.toast('Email and password are required', 'error');
      return;
    }

    if (window.AURA_UI) AURA_UI.setButtonLoading(submitBtn, true, 'Signing in…');

    try {
      const result = await window.AURA_API.login(email, password);

      if (!result.ok) {
        const msg =
          (result.data && (result.data.detail || result.data.message)) ||
          'Incorrect email or password';
        if (window.AURA_UI) AURA_UI.toast(typeof msg === 'string' ? msg : 'Login failed', 'error');
        return;
      }

      // Auth is cookie-only (HttpOnly). Do not store JWT in localStorage.
      if (window.AURA_UI) AURA_UI.toast('Welcome back', 'success');
      window.location.href = safeNextPath();
    } catch (err) {
      console.error(err);
      if (window.AURA_UI) AURA_UI.toast('Something went wrong. Try again.', 'error');
    } finally {
      if (window.AURA_UI) AURA_UI.setButtonLoading(submitBtn, false);
    }
  }

  async function handleRegister(form) {
    const emailEl = form.querySelector('#email, [name="email"]');
    const passwordEl = form.querySelector('#password, [name="password"]');
    const nameEl = form.querySelector('#full_name, [name="full_name"]');
    const submitBtn = form.querySelector('[type="submit"]');

    const email = (emailEl && emailEl.value || '').trim();
    const password = passwordEl && passwordEl.value || '';
    const full_name = nameEl ? (nameEl.value || '').trim() : undefined;

    if (!email || !password) {
      if (window.AURA_UI) AURA_UI.toast('Email and password are required', 'error');
      return;
    }
    if (password.length < 6) {
      if (window.AURA_UI) AURA_UI.toast('Password must be at least 6 characters', 'error');
      return;
    }

    if (window.AURA_UI) AURA_UI.setButtonLoading(submitBtn, true, 'Creating account…');

    try {
      const payload = { email: email, password: password };
      if (full_name) payload.full_name = full_name;

      const reg = await window.AURA_API.register(payload);
      if (!reg.ok) {
        const msg =
          (reg.data && (reg.data.detail || reg.data.message)) ||
          'Registration failed';
        if (window.AURA_UI) AURA_UI.toast(typeof msg === 'string' ? msg : 'Registration failed', 'error');
        return;
      }

      // Auto-login after register (cookie is set by /auth/login; no localStorage)
      const login = await window.AURA_API.login(email, password);
      if (login.ok) {
        if (window.AURA_UI) AURA_UI.toast('Account created', 'success');
        window.location.href = '/dashboard';
      } else {
        if (window.AURA_UI) AURA_UI.toast('Account created — please sign in', 'info');
        window.location.href = '/login';
      }
    } catch (err) {
      console.error(err);
      if (window.AURA_UI) AURA_UI.toast('Something went wrong. Try again.', 'error');
    } finally {
      if (window.AURA_UI) AURA_UI.setButtonLoading(submitBtn, false);
    }
  }

  function init() {
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
      loginForm.addEventListener('submit', function (e) {
        e.preventDefault();
        handleLogin(loginForm);
      });
    }

    const registerForm = document.getElementById('registerForm');
    if (registerForm) {
      registerForm.addEventListener('submit', function (e) {
        e.preventDefault();
        handleRegister(registerForm);
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();