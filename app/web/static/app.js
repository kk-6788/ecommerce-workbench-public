/* 电商作业平台 · 前端辅助 JS
   所有写操作走 API（带 CSRF header），页面渲染由后端模板完成。 */
(function () {
  'use strict';

  // 从 cookie 读取 CSRF（login 接口返回 csrf 后由页面存储）
  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }

  // 统一的 API 请求封装：写操作自动带 CSRF
  window.xpApi = async function (url, method, body) {
    const opts = { method: method || 'GET', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const csrf = sessionStorage.getItem('xp_csrf');
    if (csrf && method && method !== 'GET') {
      opts.headers['X-CSRF-Token'] = csrf;
    }
    const resp = await fetch(url, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error((data && data.detail) || ('请求失败 ' + resp.status));
    }
    return data;
  };

  // 登出表单：携带 CSRF header（后端 logout 已纳入写请求保护）
  document.addEventListener('DOMContentLoaded', function () {
    const logoutForm = document.querySelector('form[action="/api/v1/auth/logout"]');
    if (logoutForm) {
      logoutForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        const csrf = sessionStorage.getItem('xp_csrf') || '';
        await fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: {'X-CSRF-Token': csrf},
        });
        sessionStorage.removeItem('xp_csrf');
        location.href = '/login';
      });
    }
  });
})();
