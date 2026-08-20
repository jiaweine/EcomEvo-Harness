const byId=id=>document.getElementById(id);
const currentId=()=>new URLSearchParams(location.search).get('conversation');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let refreshTimer=null,rendering=false,lastDetail=null;

function ensureStyles(){if(document.querySelector('link[data-evo-lifecycle]'))return;const link=document.createElement('link');link.rel='stylesheet';link.href='/assets/lifecycle.css';link.dataset.evoLifecycle='1';document.head.appendChild(link)}
function toast(text){const el=byId('toast');if(!el)return;el.textContent=text;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),3200)}
function saveDraft(){const cid=currentId(),input=byId('messageInput');if(!cid||!input)return;try{sessionStorage.setItem(`ecomevo.draft.${cid}`,input.value||'')}catch{}}
function restoreDraft(){const cid=currentId(),input=byId('messageInput');if(!cid||!input||input.value)return;try{const value=sessionStorage.getItem(`ecomevo.draft.${cid}`);if(value){input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))}sessionStorage.removeItem(`ecomevo.draft.${cid}`)}catch{}}
function reloadClean(){saveDraft();const u=new URL(location.href);u.searchParams.delete('tour');location.replace(u.toString())}
async function api(url,opts={}){const r=await fetch(url,opts);let data=null;try{data=await r.json()}catch{}if(!r.ok)throw new Error(data?.detail||`请求失败 ${r.status}`);return data}

function assetControls(asset){
  const active=asset.active!==false;
  const state=active?'<span class="asset-scope-state active">参与后续分析</span>':'<span class="asset-scope-state excluded">已排除 · 保留历史</span>';
  const toggle=`<button type="button" class="asset-scope-btn" data-asset-scope="${esc(asset.id)}" data-next-active="${active?'0':'1'}">${active?'排除后续分析':'重新启用'}</button>`;
  const remove=`<button type="button" class="asset-delete-btn" data-asset-delete="${esc(asset.id)}">永久删除</button>`;
  return `<div class="asset-lifecycle-row">${state}<div>${toggle}${remove}</div></div>`;
}
function decorateAssets(detail){
  const box=byId('assetList');if(!box)return;
  const assets=detail.assets||[];const cards=[...box.querySelectorAll('.asset-card')];
  if(cards.length!==assets.length)return;
  let activeCount=0;
  cards.forEach((card,index)=>{
    const asset=assets[index];if(asset.active!==false)activeCount++;
    card.classList.toggle('asset-excluded',asset.active===false);card.dataset.assetId=asset.id;
    card.querySelector('.asset-lifecycle-row')?.remove();card.insertAdjacentHTML('beforeend',assetControls(asset));
  });
  if(byId('assetCountChip'))byId('assetCountChip').textContent=String(activeCount);
  box.querySelectorAll('[data-asset-scope]').forEach(button=>button.onclick=()=>changeScope(button));
  box.querySelectorAll('[data-asset-delete]').forEach(button=>button.onclick=()=>deleteAsset(button));
}
async function changeScope(button){
  const id=button.dataset.assetScope,active=button.dataset.nextActive==='1';button.disabled=true;
  try{
    await api(`/api/assets/${encodeURIComponent(id)}/scope`,{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({active,reason:active?'':'用户从任务资料面板排除'})});
    toast(active?'资料已重新加入后续分析':'资料已排除后续分析，历史记录仍保留');reloadClean();
  }catch(err){button.disabled=false;toast(err.message)}
}
async function deleteAsset(button){
  const now=Date.now(),until=Number(button.dataset.confirmUntil||0);
  if(now>until){button.dataset.confirmUntil=String(now+4000);button.textContent='再次点击确认删除';button.classList.add('confirming');setTimeout(()=>{if(document.contains(button)&&Date.now()>Number(button.dataset.confirmUntil||0)){button.textContent='永久删除';button.classList.remove('confirming')}},4100);return}
  button.disabled=true;
  try{await api(`/api/assets/${encodeURIComponent(button.dataset.assetDelete)}`,{method:'DELETE'});toast('资料已永久删除');reloadClean()}
  catch(err){button.disabled=false;button.textContent='永久删除';button.classList.remove('confirming');button.dataset.confirmUntil='0';toast(err.message)}
}

function simulatedCard(action){
  const risk=({low:'低影响',medium:'中等影响',high:'高影响'})[action.risk_level]||action.risk_level;
  return `<div class="action-card simulated-card" data-simulated-id="${esc(action.id)}"><span class="action-status simulated">演示已完成</span><h4>${esc(action.title)}</h4><p>${esc(action.description)}</p><p class="simulation-note">本次仅完成 EcomEvo 本地演示流程，没有调用真实业务系统，也没有改变真实商品、商家、订单或风险状态。</p><div class="action-meta"><span class="risk-chip ${esc(action.risk_level)}">${esc(risk)}</span><span class="risk-chip">未产生真实业务副作用</span></div></div>`;
}
function decorateActions(detail){
  const box=byId('actionList');if(!box)return;const simulated=(detail.actions||[]).filter(x=>x.status==='simulated');
  box.querySelectorAll('[data-simulated-id]').forEach(x=>x.remove());
  if(simulated.length){if(box.querySelector('.empty-side'))box.innerHTML='';simulated.slice(0,3).forEach(action=>box.insertAdjacentHTML('beforeend',simulatedCard(action)));const t=byId('toast');if(t?.textContent==='操作已完成并留痕')t.textContent='本地演示已完成，未改变真实业务状态'}
}
async function refresh(){
  const cid=currentId();if(!cid||rendering)return;rendering=true;
  try{lastDetail=await api(`/api/conversations/${encodeURIComponent(cid)}`);decorateAssets(lastDetail);decorateActions(lastDetail)}catch{}finally{rendering=false}
}
function scheduleRefresh(){clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,80)}
function bind(){
  ensureStyles();restoreDraft();scheduleRefresh();
  [byId('assetList'),byId('actionList')].filter(Boolean).forEach(el=>new MutationObserver(scheduleRefresh).observe(el,{childList:true,subtree:true}));
  byId('assetLibraryBtn')?.addEventListener('click',scheduleRefresh);document.querySelector('.right-tab[data-panel="assets"]')?.addEventListener('click',scheduleRefresh);window.addEventListener('focus',scheduleRefresh);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind,{once:true});else bind();
