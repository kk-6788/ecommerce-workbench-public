(() => {
  'use strict';
  const schema = JSON.parse(document.getElementById('import-schema').textContent);
  const kind = document.getElementById('table-kind');
  const sheet = document.getElementById('table-sheet');
  const status = document.getElementById('import-status');
  const confirm = document.getElementById('table-confirm');
  const upload = document.getElementById('table-upload');
  const help = {
    products: '商品资料进入“商品档案”，新建为待核验商品；其他列作为来源记录保留。',
    suppliers: '货源进入对应商品档案，标为未核验。请先创建商品，再填写其完整商品 ID。',
    market: '选品与市场资料进入“选品货源”词池，原始评分保留，不自动判为通过。',
    orders: '订单明细存入导入资料，可在“经营复盘”入口查看；不自动合并或算作已核验指标。',
    sales: '销售汇总存入导入资料，可在“经营复盘”入口查看；不会与订单明细重复累计。'
  };
  let batch = null;
  let generation = 0;
  function element(tag, text) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); return node; }
  async function api(url, options={}) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '请求失败，请检查输入');
    return result.data;
  }
  function headers() { return {'X-CSRF-Token': sessionStorage.getItem('xp_csrf') || ''}; }
  function grid(target, columns, rows) {
    target.replaceChildren();
    const table = element('table'); table.className = 'table';
    const head = element('thead'), hr = element('tr'); columns.forEach(c => hr.append(element('th', c))); head.append(hr); table.append(head);
    const body = element('tbody'); rows.forEach(row => {const tr = element('tr'); row.forEach(v => tr.append(element('td', v == null ? '' : v))); body.append(tr);}); table.append(body); target.append(table);
    if (!rows.length) target.append(element('p','暂无导入记录'));
  }
  async function refresh() {
    const selected = kind.value;
    const [history, records] = await Promise.all([api('/api/v1/table-imports'), api('/api/v1/table-imports/records/' + selected)]);
    if (kind.value !== selected) return;
    const historyNode = document.getElementById('import-history'); historyNode.replaceChildren();
    history.forEach(item => {
      const report = JSON.parse(item.report_json);
      historyNode.append(element('p', item.filename + ' · ' + schema[item.kind].label + ' · ' +
        (item.state === 'IMPORTED' ? '已导入 ' + report.imported + ' 条，跳过重复 ' + report.duplicates + ' 条' : '待确认') + ' · ' + item.created_at.slice(0,16).replace('T',' ')));
    });
    const columns = Object.keys(schema[selected].fields);
    grid(document.getElementById('import-records'), ['目标 ID', ...columns.map(k => schema[selected].fields[k][0]), '原始资料'],
      records.map(row => {const v = JSON.parse(row.normalized_json); return [row.target_id || '资料记录', ...columns.map(k => v[k]), row.raw_json];}));
  }
  function renderPreview() {
    if (!batch) return;
    const table = batch.tables.find(t => t.name === sheet.value);
    const mapping = document.getElementById('table-mapping'); mapping.replaceChildren();
    Object.entries(schema[kind.value].fields).forEach(([key, aliases]) => {
      const label = element('label', aliases[0] + (schema[kind.value].required.includes(key) ? ' *' : ''));
      const select = element('select'); select.dataset.field = key; select.append(new Option('不导入此字段', ''));
      table.headers.forEach(h => select.append(new Option(h,h)));
      select.value = table.mapping[key] || ''; label.append(select); mapping.append(label);
    });
    grid(document.getElementById('preview-grid'), ['行号', ...table.headers], table.preview.map(row => [row.number, ...table.headers.map(h => row.values[h])]));
    confirm.disabled = false;
    status.textContent = '已读取 ' + table.count + ' 条，展示前 10 条。请核对列名后确认导入。';
  }
  kind.addEventListener('change', () => {
    generation++; batch = null; document.getElementById('table-preview').hidden = true;
    document.getElementById('kind-help').textContent = help[kind.value];
    document.getElementById('table-template').href = '/api/v1/table-imports/template/' + kind.value;
    document.getElementById('table-export').href = '/api/v1/table-imports/export/' + kind.value;
    renderEditor();
    status.textContent = ''; refresh().catch(error => {status.textContent = error.message;});
  });
  sheet.addEventListener('change', renderPreview);
  upload.addEventListener('submit', async event => {
    event.preventDefault();
    const current = ++generation;
    batch = null; document.getElementById('table-preview').hidden = true;
    const button = upload.querySelector('button');
    button.disabled = true; status.textContent = '正在读取表格…';
    try {
      const result = await api('/api/v1/table-imports/preview', {method:'POST', headers:headers(), body:new FormData(upload)});
      if (current !== generation) return;
      batch = result; sheet.replaceChildren(); result.tables.forEach(t => sheet.append(new Option(t.name,t.name)));
      document.getElementById('table-preview').hidden = false; renderPreview();
    } catch(error) {status.textContent = error.message;} finally {button.disabled = false;}
  });
  confirm.addEventListener('click', async () => {
    if (!batch) return;
    const mapping = {}; document.querySelectorAll('#table-mapping select').forEach(s => {mapping[s.dataset.field] = s.value;});
    confirm.disabled = true; kind.disabled = true; sheet.disabled = true;
    upload.querySelector('button').disabled = true;
    status.textContent = '正在校验并导入…';
    try {
      const result = await api('/api/v1/table-imports/' + batch.id + '/commit', {method:'POST', headers:{...headers(),'Content-Type':'application/json'}, body:JSON.stringify({sheet:sheet.value,mapping})});
      if (result.errors.length) {
        status.textContent = '未写入任何数据。' + result.errors.slice(0,20).map(e => '第 ' + e.row + ' 行：' + e.message).join('；') + (result.errors.length > 20 ? '；另有更多错误，请修改原表后重试' : '');
        confirm.disabled = false;
      } else {
        status.textContent = '完成：导入 ' + result.imported + ' 条，跳过重复 ' + result.duplicates + ' 条。' + help[kind.value];
        batch = null; document.getElementById('table-preview').hidden = true; await refresh();
      }
    } catch(error) {status.textContent = error.message; confirm.disabled = false;}
    finally {kind.disabled = false; sheet.disabled = false; upload.querySelector('button').disabled = false;}
  });
  const drafts = {};
  function renderEditor() {
    const root = document.getElementById('editable-template'); root.replaceChildren();
    if (!drafts[kind.value]) drafts[kind.value] = [{}];
    const table = element('table'); table.className = 'table';
    const head = element('tr');
    Object.entries(schema[kind.value].fields).forEach(([key, aliases]) => head.append(element('th', aliases[0] + (schema[kind.value].required.includes(key) ? ' *' : ''))));
    const thead = element('thead'); thead.append(head); table.append(thead);
    const body = element('tbody');
    drafts[kind.value].forEach((row, index) => {
      const tr = element('tr');
      Object.entries(schema[kind.value].fields).forEach(([key, aliases]) => {
        const cell = element('td'), input = element('input');
        input.type = 'text'; input.maxLength = 200; input.value = row[key] || '';
        input.setAttribute('aria-label', '第 ' + (index + 1) + ' 行 ' + aliases[0]);
        input.addEventListener('input', () => {row[key] = input.value;});
        cell.append(input); tr.append(cell);
      });
      body.append(tr);
    });
    table.append(body); root.append(table);
  }
  document.getElementById('template-add').addEventListener('click', () => {
    if (drafts[kind.value].length >= 100) {status.textContent = '在线模板最多 100 行，更多数据请在 Excel 中填写。'; return;}
    drafts[kind.value].push({}); renderEditor();
  });
  document.getElementById('template-save').addEventListener('click', () => {
    const fields = Object.keys(schema[kind.value].fields);
    const rows = drafts[kind.value].filter(row => Object.values(row).some(v => v.trim()));
    const escape = value => {
      let text = String(value || '');
      if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
      return '"' + text.replaceAll('"','""') + '"';
    };
    const content = [fields.map(k => schema[kind.value].fields[k][0]), ...rows.map(row => fields.map(k => row[k] || ''))].map(row => row.map(escape).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff',content],{type:'text/csv;charset=utf-8'}));
    const link = element('a'); link.href = url; link.download = kind.value + '-filled.csv'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = '已下载填写好的模板。下一步：在上方选择此文件并预览导入。';
  });
  const initial = new URLSearchParams(location.search).get('kind');
  if (schema[initial]) kind.value = initial;
  kind.dispatchEvent(new Event('change'));
})();
