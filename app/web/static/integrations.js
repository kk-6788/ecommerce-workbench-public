document.addEventListener('DOMContentLoaded', () => {
  const providerMessage = document.getElementById('provider-message');
  const providerName = document.getElementById('provider-name');
  const providerKey = document.getElementById('provider-key');
  const providerModel = document.getElementById('provider-model');
  let configs = [];
  const loadProviders = async () => {
    configs = (await xpApi('/api/v1/integrations/providers')).data;
    const list = document.getElementById('provider-list');
    list.replaceChildren();
    configs.forEach(config => {
      const item = document.createElement('li');
      item.textContent = config.label + '：' + (config.configured ? '已保存，尚未验证调用' : '未配置') + ' · ' + config.base_url;
      list.append(item);
    });
  };
  providerName.addEventListener('change', () => {
    providerKey.value = '';
    providerModel.value = configs.find(c => c.provider === providerName.value)?.model || '';
  });
  document.getElementById('provider-form').addEventListener('submit', async event => {
    event.preventDefault();
    const key = providerKey.value;
    providerKey.value = '';
    try {
      await xpApi('/api/v1/integrations/providers/' + providerName.value, 'PUT', {api_key: key, model: providerModel.value});
      providerMessage.textContent = '配置已加密保存，未发起收费调用。';
      await loadProviders();
    } catch (error) { providerMessage.textContent = error.message; }
  });
  document.getElementById('provider-delete').addEventListener('click', async () => {
    providerKey.value = '';
    try {
      await xpApi('/api/v1/integrations/providers/' + providerName.value, 'DELETE');
      providerMessage.textContent = '所选服务的本机密钥已删除。';
      await loadProviders();
    } catch (error) { providerMessage.textContent = error.message; }
  });
  loadProviders().catch(error => { providerMessage.textContent = error.message; });
  const status = document.getElementById('connection-status');
  const message = document.getElementById('libtv-message');
  const output = document.getElementById('libtv-result');
  const buttons = Array.from(document.querySelectorAll('button[id^="libtv-"]'));
  const load = async () => {
    const response = await xpApi('/api/v1/integrations/libtv');
    status.textContent = response.data.connected ? '已授权 · 可查询项目与模型' : '尚未连接或授权已过期';
  };
  const outcome = new URLSearchParams(location.search).get('libtv');
  if (outcome) {
    message.textContent = ({connected: '授权成功。可检查连接并查询项目。', cancelled: '未完成授权，可重新连接。', failed: '授权未完成或已过期，请重新连接。'})[outcome] || '';
    history.replaceState(null, '', '/integrations');
  }
  buttons.forEach(button => button.addEventListener('click', async () => {
    buttons.forEach(b => b.disabled = true);
    message.textContent = '处理中…'; output.textContent = '';
    try {
      const action = button.id.replace('libtv-', '');
      const result = await xpApi('/api/v1/integrations/libtv/' + action, 'POST', action === 'models' ? {query: 'Seedance', node_type: 'video'} : {});
      if (action === 'connect') { location.assign(result.data.authorization_url); return; }
      if (action === 'disconnect') {
        message.textContent = result.data.remote_revoked ? '已断开并撤销授权。' : '本机已断开。远端撤销未确认，可在 LibTV 账号内管理授权。';
      } else {
        const content = result.data.structuredContent || result.data.content || result.data;
        output.textContent = JSON.stringify(content, null, 2);
        message.textContent = '查询完成。';
      }
      await load();
    } catch (error) { message.textContent = error.message; }
    finally { buttons.forEach(b => b.disabled = false); }
  }));
  load().catch(error => { status.textContent = error.message; });
});
