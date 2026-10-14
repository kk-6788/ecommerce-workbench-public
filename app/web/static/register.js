document.getElementById('register-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById('register-status');
  const button = form.querySelector('[type="submit"]');
  const body = Object.fromEntries(new FormData(form));
  if (body.password !== body.password_confirm) {
    status.textContent = '两次输入的密码不一致，请重新核对。';
    return;
  }
  button.disabled = true;
  status.textContent = '正在创建…';
  try {
    const response = await fetch('/api/v1/auth/register', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': sessionStorage.getItem('xp_csrf') || ''},
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '创建失败，请检查输入');
    if (result.data.csrf) {
      sessionStorage.setItem('xp_csrf', result.data.csrf);
      location.href = '/';
    } else {
      form.reset();
      form.querySelectorAll('input[name^="password"]').forEach(input => { input.type = 'password'; });
      const toggle = form.querySelector('.password-toggle');
      toggle.setAttribute('aria-pressed', 'false');
      toggle.textContent = '显示密码';
      status.textContent = '账号已创建，可由对方使用新账号登录。';
    }
  } catch (error) {
    status.textContent = error.message || '连接失败，请稍后重试';
  } finally {
    button.disabled = false;
  }
});
