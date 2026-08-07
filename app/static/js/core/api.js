/**
 * AURA / SmartReco — API helper
 * All requests send cookies (credentials: 'include').
 * Use for auth, events, recommendations, products.
 */
(function (global) {
  'use strict';

  const DEFAULT_HEADERS = {
    'Accept': 'application/json',
  };

  /**
   * @param {string} url
   * @param {RequestInit & { json?: any, formData?: FormData }} options
   * @returns {Promise<{ ok: boolean, status: number, data: any, response: Response }>}
   */
  async function request(url, options = {}) {
    const {
      method = 'GET',
      json,
      formData,
      headers = {},
      ...rest
    } = options;

    const finalHeaders = { ...DEFAULT_HEADERS, ...headers };
    let body = rest.body;

    if (json !== undefined) {
      finalHeaders['Content-Type'] = 'application/json';
      body = JSON.stringify(json);
    } else if (formData instanceof FormData) {
      // Let browser set multipart boundary — do not set Content-Type
      body = formData;
      delete finalHeaders['Content-Type'];
    }

    const response = await fetch(url, {
      method,
      headers: finalHeaders,
      body,
      credentials: 'include', // critical for cookie auth
      ...rest,
    });

    let data = null;
    const contentType = response.headers.get('content-type') || '';

    if (contentType.includes('application/json')) {
      try {
        data = await response.json();
      } catch {
        data = null;
      }
    } else {
      try {
        data = await response.text();
      } catch {
        data = null;
      }
    }

    // Global 401 handling (optional redirect)
    if (response.status === 401 && global.AURA_AUTH && typeof global.AURA_AUTH.onUnauthorized === 'function') {
      global.AURA_AUTH.onUnauthorized(url);
    }

    return {
      ok: response.ok,
      status: response.status,
      data,
      response,
    };
  }

  function get(url, options = {}) {
    return request(url, { ...options, method: 'GET' });
  }

  function post(url, options = {}) {
    return request(url, { ...options, method: 'POST' });
  }

  function put(url, options = {}) {
    return request(url, { ...options, method: 'PUT' });
  }

  function del(url, options = {}) {
    return request(url, { ...options, method: 'DELETE' });
  }

  /**
   * OAuth2 password form login (application/x-www-form-urlencoded via FormData)
   * Backend expects username + password fields.
   */
  function login(email, password) {
    const formData = new FormData();
    formData.append('username', email);
    formData.append('password', password);
    return post('/auth/login', { formData });
  }

  function register(payload) {
    // payload: { email, password, full_name? }
    return post('/auth/register', { json: payload });
  }

  function logout() {
    return post('/auth/logout');
  }

  function me() {
    return get('/auth/me');
  }

  function refreshRecommendations() {
    return post('/api/recommendations/refresh', { json: {} });
  }

  function getRecommendations() {
    return get('/api/recommendations');
  }

  function postEvents(events) {
    return post('/api/events/batch', { json: { events } });
  }

  global.AURA_API = {
    request,
    get,
    post,
    put,
    del,
    login,
    register,
    logout,
    me,
    refreshRecommendations,
    getRecommendations,
    postEvents,
  };
})(window);