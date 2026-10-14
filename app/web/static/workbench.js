/* Progressive presentation helpers. Business forms retain their original IDs. */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.nav a.active').forEach(link => link.setAttribute('aria-current', 'page'));
  if (window.matchMedia('(max-width: 800px)').matches) {
    document.querySelector('.nav a.active')?.scrollIntoView({block: 'nearest', inline: 'center'});
  }
  document.querySelectorAll('table.table').forEach(table => {
    if (table.parentElement.classList.contains('table-scroll')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll';
    wrapper.tabIndex = 0;
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('aria-label', (table.closest('section')?.querySelector('h2')?.textContent || '数据表') + '，窄屏可横向滚动');
    table.before(wrapper); wrapper.append(table);
  });
});

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-password-target]').forEach(button => {
    button.addEventListener('click', () => {
      const show = button.getAttribute('aria-pressed') !== 'true';
      button.dataset.passwordTarget.split(',').forEach(id => {
        document.getElementById(id).type = show ? 'text' : 'password';
      });
      button.setAttribute('aria-pressed', String(show));
      button.textContent = show ? '隐藏密码' : '显示密码';
    });
  });
});
