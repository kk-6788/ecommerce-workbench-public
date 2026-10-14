document.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const api = async (path, method, body) => (await xpApi('/api/v1/' + path, method, body)).data;
  const message = text => { $('ai-message').textContent = text; };
  const labels = {PLANNED: '待审批与执行', WAIT_EVIDENCE: '需要补充证据', HUMAN_REQUIRED: '必须人工复核', REVIEW_REQUIRED: '建议人工复核', SHADOW_CANDIDATE: '模型建议可接受（未自动通过）', ERROR: '服务或格式异常', STALE: '资料或配置已变化', HUMAN_PASSED: '人工已接受', HUMAN_REJECTED: '人工已拒绝'};
  const add = (parent, tag, text) => { const el = document.createElement(tag); el.textContent = text; parent.append(el); return el; };
  const action = (parent, text, fn) => {
    const button = add(parent, 'button', text); button.className = 'btn btn-sm';
    button.onclick = async () => { button.disabled = true; try { await fn(); await refresh(); } catch (error) { message(error.message); } finally { button.disabled = false; } };
  };
  let products = [], recheckParent = null;
  async function sources() {
    $('ai-sources').replaceChildren();
    if (!$('ai-product').value) return;
    const rows = await api('ai-sources/' + encodeURIComponent($('ai-product').value));
    for (const row of rows) {
      const label = add($('ai-sources'), 'label', ' ' + row.source_key + ' · ' + row.evidence_kind);
      const input = document.createElement('input'); input.type = 'checkbox'; input.value = row.id; label.prepend(input);
    }
    if (!rows.length) add($('ai-sources'), 'p', '尚无来源，请先在商品档案登记原文证据。');
  }
  async function refresh() {
    const runs = await api('ai-checks'); $('ai-runs').replaceChildren();
    for (const run of runs) {
      const box = add($('ai-runs'), 'details', '');
      add(box, 'summary', run.request.state.subject.title + ' · ' + (labels[run.state] || run.state));
      add(box, 'p', run.reason);
      add(box, 'p', '方案：' + run.plan_id + ' · 费用上限：' + (run.plan.cost_cap_minor / 100) + ' 元 · 执行状态：' + (run.attempt?.state || '尚未提交'));
      add(box, 'p', '模型：' + (run.returned_model || run.model) + ' · 规则：' + run.policy_version);
      const preview = add(box, 'pre', JSON.stringify(run.request, null, 2)); preview.style.whiteSpace = 'pre-wrap';
      for (const answer of run.answers) add(box, 'p', answer.question_id + '：' + answer.answer.choice + '；置信度 ' + answer.answer.confidence + '；' + (labels[answer.state] || answer.state));
      if (run.response) add(box, 'p', '用量：' + JSON.stringify(run.response.usage) + '。用量不等于账单，费用状态以执行回执为准。');
      if (run.state === 'PLANNED') {
        action(box, '批准本次发送的资料', () => api('ai-checks/' + run.id + '/approve', 'POST', {kind: 'CONTENT', plan_hash: run.plan.plan_hash}));
        action(box, '批准本次费用方案', () => api('ai-checks/' + run.id + '/approve', 'POST', {kind: 'COST', plan_hash: run.plan.plan_hash}));
        action(box, '提交已审批检查', () => api('ai-checks/' + run.id + '/execute', 'POST'));
      }
      const reason = add(box, 'textarea', ''); reason.placeholder = '人工判断理由（必填）'; reason.maxLength = 2000;
      for (const [label, act] of [['接受判断', 'HUMAN_PASSED'], ['拒绝判断', 'HUMAN_REJECTED'], ['要求补证据', 'REQUEST_EVIDENCE'], ['撤销人工结论', 'REVOKE']]) {
        action(box, label, () => api('ai-checks/' + run.id + '/review', 'POST', {action: act, reason: reason.value, expected_revision: run.revision, snapshot_hash: run.snapshot_hash, idempotency_key: crypto.randomUUID()}));
      }
      action(box, '编辑资料后复查', async () => {
        products = await api('products');
        const product = products.find(p => p.id === run.product_id);
        if (!product) throw new Error('商品已不存在');
        const state = run.request.state;
        recheckParent = run.id;
        $('ai-product').value = product.id;
        const form = $('ai-create');
        for (const [key, value] of Object.entries({use_case: run.use_case, claim: state.claim, sku: state.subject.sku, keyword: state.keyword, ...run.snapshot.budget})) form.elements.namedItem(key).value = value;
        await sources();
        form.querySelector('button[type="submit"]').textContent = '建立复查方案，不调用模型';
        form.scrollIntoView({behavior: 'smooth'});
        message('已载入复查表单。请勾选最新证据并修改需核对内容，然后建立复查方案。');
      });
      run.reviews.forEach(review => add(box, 'p', '人工记录：' + review.action + ' · ' + review.reason));
    }
    if (!runs.length) add($('ai-runs'), 'p', '尚无审核记录。');
    $('ai-notifications').replaceChildren();
    for (const notification of await api('ai-notifications')) {
      const box = add($('ai-notifications'), 'div', (labels[notification.kind] || notification.kind) + (notification.resolved_at ? ' · 已处理' : ' · 未处理'));
      if (!notification.read_at) action(box, '标记已读', () => api('ai-notifications/' + notification.id + '/read', 'POST'));
    }
  }
  $('ai-product').onchange = () => sources().catch(e => message(e.message));
  $('ai-refresh').onclick = () => refresh().catch(e => message(e.message));
  $('ai-reset').onclick = () => { recheckParent = null; $('ai-create').querySelector('button[type="submit"]').textContent = '建立方案，不调用模型'; message('已切换为新检查。'); };
  $('ai-create').onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button'); button.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(event.target));
      const product = products.find(p => p.id === $('ai-product').value);
      if (!product) throw new Error('请先创建并选择商品');
      const endpoint = recheckParent ? 'ai-checks/' + recheckParent + '/recheck-plan' : 'ai-check-plans';
      await api(endpoint, 'POST', {...data, product_id: product.id, expected_revision: product.revision, evidence_ids: Array.from($('ai-sources').querySelectorAll('input:checked')).map(e => e.value)});
      message('审核方案已建立，未调用模型。展开记录核对实际要发送的资料。'); await refresh();
    } catch (error) { message(error.message); } finally { button.disabled = false; }
  };
  try {
    $('ai-policy').textContent = (await api('ai-policy')).execution_blocker;
    products = await api('products');
    products.forEach(product => { const option = add($('ai-product'), 'option', product.title); option.value = product.id; });
    await sources(); await refresh();
  } catch (error) { message(error.message); }
});
