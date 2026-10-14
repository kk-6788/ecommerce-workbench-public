/* Local workflow forms. User text is inserted using textContent, never HTML. */
(async () => {
  const $ = id => document.getElementById(id);
  const result = $('operation-result');
  const tell = text => { result.textContent = text; };
  const api = async (path,method='GET',body) => (await xpApi('/api/v1'+path,method,body)).data;
  const productId = () => { if (!$('active-product').value) throw new Error('请先选择商品'); return $('active-product').value; };
  let product;
  const fill = (select, items, label, value) => { if (!select) return; select.replaceChildren(new Option('请选择','')); for (const item of items) select.add(new Option(label(item),value(item))); };
  const submit = (id, handler) => { const form=$(id); if(form) form.onsubmit=async e=>{ e.preventDefault(); const btn=form.querySelector('button'); btn.disabled=true; try { await handler(Object.fromEntries(new FormData(form)),form); await load(); } catch(err) { tell(err.message); } finally {btn.disabled=false;} }; };
  const button = (label,action) => { const b=document.createElement('button');b.className='btn'; b.type='button';b.textContent=label;b.onclick=async()=>{b.disabled=true;try{await action();await load();}catch(e){tell(e.message);}finally{b.disabled=false;}};return b; };
  async function load() {
    const items=await api('/products'); const previous=$('active-product').value || new URLSearchParams(location.search).get('id') || sessionStorage.getItem('last-product');
    fill($('active-product'),items,x=>x.title,x=>x.id); $('active-product').value=previous || '';
    if($('active-product').value) {
      product=await api('/products/'+productId());
      if($('save-facts')) for(const el of $('save-facts').elements) if(el.name && product.facts[el.name]!==undefined) el.value=String(product.facts[el.name]??'');
      fill($('offer-evidence'),product.evidence,x=>x.source_key.slice(0,12),x=>x.id);
      fill($('review-prediction'),product.predictions,x=>x.created_at+' · '+x.decision,x=>x.id);
      if($('asset-actions')) {
        $('asset-actions').replaceChildren();
        for(const a of product.assets){const row=document.createElement('p');row.textContent=`${a.role} · ${a.sku_key||'通用'} · ${a.review_status} `;
          const link=document.createElement('a');link.href='/api/v1/assets/'+a.id+'/preview';link.textContent='查看素材';link.target='_blank';row.append(link);
          for(const [name,status] of [['审阅通过','APPROVED'],['驳回','REJECTED']]) row.append(button(name,async()=>{await api('/assets/'+a.id+'/review','POST',{status,usage_id:a.usage_id});tell('该商品用途的审阅已保存');}));$('asset-actions').append(row);}
      }
    }
    if($('active-batch')) {const batches=await api('/batches');fill($('active-batch'),batches,x=>x.batch.name,x=>x.batch.id);$('batch-progress').replaceChildren(); for(const b of batches){const line=document.createElement('p');line.textContent=`${b.batch.name}：真实达标 ${b.ready_count}/${b.target_min}。`+b.products.map(p=>`${p.title}：${p.reason||p.stage}`).join('；');$('batch-progress').append(line);}}
    if($('plan-actions')) { $('plan-actions').replaceChildren(); const plans=await api('/plans');for(const p of plans.filter(p=>p.product_id===$('active-product').value)){const panel=document.createElement('div');const text=document.createElement('pre');text.textContent=JSON.stringify({动作:p.action,状态:p.state,账号:p.account_id,费用上限分:p.cost_cap_minor,内容:p.payload},null,2);panel.append(text);
      for(const kind of ['CONTENT',...(p.requires_cost?['COST']:[])])panel.append(button(kind==='CONTENT'?'批准内容':'批准本笔费用',async()=>{await api('/plans/'+p.id+'/approve','POST',{kind});tell('审批已保存');}));
      panel.append(button('拒绝',()=>api('/plans/'+p.id+'/approve','POST',{kind:'CONTENT',decision:'REJECT'})),button('撤销内容批准',()=>api('/plans/'+p.id+'/revoke','POST',{kind:'CONTENT'})),button('执行已批准方案',async()=>{const a=await api('/plans/'+p.id+'/execute','POST');tell('已排队，任务状态：'+a.state);}));$('plan-actions').append(panel);}}
  }
  $('active-product').onchange=()=>{sessionStorage.setItem('last-product',$('active-product').value);load().catch(e=>tell(e.message));};
  submit('create-product',async d=>{const p=await api('/products','POST',{title:d.title,category:d.category,is_synthetic:d.is_synthetic==='true'});sessionStorage.setItem('last-product',p.id);fill($('active-product'),[p],x=>x.title,x=>x.id);$('active-product').value=p.id;tell('商品已创建，可以在商品档案补充证据和货源');});
  submit('create-batch',async d=>{await api('/batches','POST',d);tell('批次已创建');});
  if($('assign-batch'))$('assign-batch').onclick=async()=>{try{await api('/batches/'+$('active-batch').value+'/products','POST',{product_ids:[productId()]});await load();tell('已加入批次');}catch(e){tell(e.message);}};
  submit('save-facts',async d=>{const facts={...product.facts};for(const [k,v] of Object.entries(d))facts[k]=['top_payments','taobao_samples','supply_samples'].includes(k)?(v===''?null:Number(v)):k==='needs_supply_verify'?v==='true':v===''?null:v;await api('/products/'+productId(),'PATCH',{revision:product.revision,facts});tell('事实已保存；旧预测保留，需要重新预测');});
  if($('predict'))$('predict').onclick=async()=>{try{const p=await api('/products/'+productId()+'/predictions','POST',{});tell('预测：'+p.snapshot.recommended_action+'；'+p.snapshot.rule_hits.join('、'));await load();}catch(e){tell(e.message);}};
  submit('add-evidence',async d=>{await api('/products/'+productId()+'/evidence','POST',{source_key:d.source_key,source_url:d.source_url||null,observed_at:new Date(d.observed_at).toISOString(),content:{note:d.note},is_synthetic:!!product.is_synthetic});tell('证据已保存');});
  submit('add-offer',async d=>{await api('/products/'+productId()+'/offers','POST',{supplier:d.supplier,sku_key:d.sku_key,specification:{description:d.specification},procurement_minor:Math.round(Number(d.price)*100),sample_count:d.sample_count===''?null:Number(d.sample_count),verification_status:d.verification_status,evidence_id:d.evidence_id||null});tell('报价已保存');});
  submit('upload-asset',async(d,form)=>{const data=new FormData(form);if(!data.get('sku_key'))data.delete('sku_key');data.set('is_synthetic',product.is_synthetic?'1':'0');const r=await fetch('/api/v1/products/'+productId()+'/assets',{method:'POST',headers:{'X-CSRF-Token':sessionStorage.getItem('xp_csrf')||''},body:data});const out=await r.json();if(!r.ok)throw new Error(out.detail);tell('素材已上传，请打开查看后审阅');});
  submit('create-plan',async d=>{const assets=product.assets.filter(a=>a.review_status==='APPROVED');const hashes=assets.filter(a=>a.kind==='IMAGE').map(a=>a.file_hash);const payload={title:d.title,prompt:d.prompt,sku_key:d.sku_key,facts:product.facts,input_asset_hashes:hashes,asset_hashes:hashes,script:d.prompt,shot_list:d.shots.split('\n').filter(Boolean).map((description,i)=>({id:i+1,description})),sku_map:d.sku_key?{[d.sku_key]:{}}:{},price_minor:d.price?Math.round(Number(d.price)*100):null,stock:d.stock?Number(d.stock):0,video_asset_id:assets.find(a=>a.kind==='VIDEO')?.id};await api('/plans','POST',{product_id:productId(),action:d.action,provider:d.provider,account_id:d.account_id||null,payload});tell('方案已保存；批准内容不代表已批准费用或已发布');});
  submit('save-metric',async d=>{const metrics={...d,period_start:new Date(d.period_start).toISOString(),period_end:new Date(d.period_end).toISOString(),source_timezone:'UTC',granularity:'day',measure_mode:'INTERVAL'};for(const k of ['impressions','clicks','orders'])metrics[k]=d[k]===''?null:Number(d[k]);const m=await api('/products/'+productId()+'/metrics','POST',{source_key:d.source_key,metrics,is_synthetic:!!product.is_synthetic});tell('指标已保存，快照标识：'+m.id);});
  submit('create-review',async d=>{const r=await api('/products/'+productId()+'/reviews','POST',{prediction_id:d.prediction_id,outcome:d.outcome,hypotheses:[{statement:d.statement,supporting_evidence:[d.support],opposing_evidence:[],unknowns:[d.unknowns]}],verification:{action:d.action},metric_ids:d.metric_ids.split(',').map(s=>s.trim()).filter(Boolean)});tell('复盘已保存：'+r.outcome+'；'+r.findings.evidence_reasons.join('；'));});
  try{await load();}catch(e){tell(e.message);}
})();
