/**
 * AURA / SmartReco — UI helpers
 * Toast notifications, button loading states, simple markdown narrative render.
 */
(function (global) {
  'use strict';

  const TOAST_DURATION_MS = 3200;

  function ensureToastContainer() {
    let el = document.getElementById('toastContainer');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toastContainer';
      el.className = 'toast-container';
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    return el;
  }

  /**
   * @param {string} message
   * @param {'info'|'success'|'error'} [type='info']
   */
  function toast(message, type) {
    type = type || 'info';
    const container = ensureToastContainer();
    const node = document.createElement('div');
    node.className = 'toast toast-' + type;
    node.textContent = message;
    container.appendChild(node);

    const timer = setTimeout(function () {
      node.style.opacity = '0';
      node.style.transform = 'translateY(8px)';
      node.style.transition = 'opacity 200ms ease, transform 200ms ease';
      setTimeout(function () {
        if (node.parentNode) node.parentNode.removeChild(node);
      }, 220);
    }, TOAST_DURATION_MS);

    node.addEventListener('click', function () {
      clearTimeout(timer);
      if (node.parentNode) node.parentNode.removeChild(node);
    });
  }

  /**
   * Set loading state on a button
   * @param {HTMLElement|null} btn
   * @param {boolean} loading
   * @param {string} [loadingText]
   */
  function setButtonLoading(btn, loading, loadingText) {
    if (!btn) return;

    if (loading) {
      if (!btn.dataset.originalText) {
        btn.dataset.originalText = btn.textContent || '';
      }
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      if (loadingText) btn.textContent = loadingText;
    } else {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
      if (btn.dataset.originalText) {
        btn.textContent = btn.dataset.originalText;
        delete btn.dataset.originalText;
      }
    }
  }

  /**
   * Render markdown into an element if marked.js is available
   * @param {HTMLElement|string} elOrId
   * @param {string} [rawMarkdown] - if omitted, uses element textContent
   */
  function renderMarkdown(elOrId, rawMarkdown) {
  const el = typeof elOrId === 'string'
    ? document.getElementById(elOrId)
    : elOrId;

  if (!el) return;

  const source = rawMarkdown != null ? String(rawMarkdown) : (el.textContent || '');
  if (!source.trim()) return;

  if (typeof global.marked !== 'undefined' && typeof global.marked.parse === 'function') {
    const html = global.marked.parse(source);
    // Sanitize to prevent stored XSS from LLM narratives
    el.innerHTML = (typeof global.DOMPurify !== 'undefined')
      ? global.DOMPurify.sanitize(html)
      : html;
  } else {
    // Fallback: keep plain text
    el.textContent = source;
  }
}

  /**
   * Show a simple full-block loading state inside a container
   * @param {HTMLElement|string} container
   * @param {string} [label='Loading…']
   */
  function showLoading(container, label) {
    const el = typeof container === 'string'
      ? document.getElementById(container)
      : container;
    if (!el) return;

    el.innerHTML =
      '<div class="loading-overlay">' +
      '<div class="spinner spinner-lg" aria-hidden="true"></div>' +
      '<div>' + (label || 'Loading…') + '</div>' +
      '</div>';
  }

  function markJsReady() {
    document.body.classList.add('js-ready');
  }

  function init() {
    markJsReady();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.AURA_UI = {
    toast,
    setButtonLoading,
    renderMarkdown,
    showLoading,
  };
})(window);