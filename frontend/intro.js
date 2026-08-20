const TOUR_KEY='ecomevo.product-tour.v1';
const byId=id=>document.getElementById(id);
let returnFocus=null;

const SCENE_COPY={
  product_governance:{name:'商品治理',meta:'核对商品信息、素材、资质与风险声明。',prompt:'帮我核对这批商品的标题、主图和详情，找出需要下架或补资质的高风险项，并说明依据。'},
  merchant_review:{name:'商家审核',meta:'核对主体、资质、授权与历史风险。',prompt:'帮我审核这个商家的主体、授权和历史风险，给出通过、补件或拒绝的建议。'},
  aftersales:{name:'售后判责',meta:'结合订单、履约、沟通记录和举证还原事实。',prompt:'结合订单、物流、沟通记录和用户举证，帮我还原事实并给出售后判责建议。'},
  risk_review:{name:'风险核查',meta:'交叉确认交易、账户、商品与履约异常。',prompt:'帮我核对这组异常交易和账户信息，区分强风险证据与普通线索，给出是否升级复核的建议。'},
  content_audit:{name:'内容审核',meta:'检查图片、视频、文案与商品事实的一致性。',prompt:'帮我审核这组商品图片、视频和文案，核对它们与商品事实是否一致，标出违规、误导或需要补证据的内容。'}
};

function readSeen(){try{return localStorage.getItem(TOUR_KEY)==='seen'}catch{return false}}
function markSeen(){try{localStorage.setItem(TOUR_KEY,'seen')}catch{}}
function focusables(){
  const tour=byId('productTour');
  if(!tour)return[];
  return [...tour.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')].filter(el=>!el.hidden&&el.getClientRects().length);
}
function openTour(source='manual'){
  const tour=byId('productTour');if(!tour||!tour.hidden)return;
  returnFocus=document.activeElement;tour.hidden=false;tour.dataset.source=source;document.body.classList.add('tour-open');requestAnimationFrame(()=>byId('tourCloseBtn')?.focus());
}
function closeTour({remember=true,restoreFocus=true}={}){
  const tour=byId('productTour');if(!tour||tour.hidden)return;
  if(remember)markSeen();tour.hidden=true;document.body.classList.remove('tour-open');const target=returnFocus;returnFocus=null;
  if(restoreFocus&&target&&document.contains(target))requestAnimationFrame(()=>target.focus());
}
function activeScene(){return document.querySelector('.scene.active')?.dataset.scene||'product_governance'}
function startExample(){
  markSeen();closeTour({remember:false,restoreFocus:false});const input=byId('messageInput');if(!input)return;
  input.value=(SCENE_COPY[activeScene()]||SCENE_COPY.product_governance).prompt;input.dispatchEvent(new Event('input',{bubbles:true}));requestAnimationFrame(()=>input.focus());
}
async function copyText(value){try{await navigator.clipboard.writeText(value);return true}catch{}try{const ta=document.createElement('textarea');ta.value=value;ta.style.cssText='position:fixed;opacity:0;pointer-events:none';document.body.appendChild(ta);ta.select();const ok=document.execCommand('copy');ta.remove();return ok}catch{return false}}
function showToast(text){const toast=byId('toast');if(!toast)return;toast.textContent=text;toast.classList.add('show');clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>toast.classList.remove('show'),3000)}
function cleanShareLink(){const u=new URL(location.href);u.searchParams.delete('tour');return u.toString()}
function openAssetsPanel(){
  document.querySelector('.right-tab[data-panel="assets"]')?.click();
  if(matchMedia('(max-width:1080px)').matches){byId('leftbar')?.classList.remove('open');byId('rightbar')?.classList.add('open');byId('navToggle')?.setAttribute('aria-expanded','false');byId('detailToggle')?.setAttribute('aria-expanded','true');if(byId('drawerScrim'))byId('drawerScrim').hidden=false}
}
async function reuseEmptyTaskForScene(button,original,event){
  const scene=button.dataset.scene;const cid=new URLSearchParams(location.search).get('conversation');const hasMessages=Boolean(byId('messageList')?.children.length);
  if(!cid||hasMessages)return original?.call(button,event);event?.preventDefault();
  try{
    const r=await fetch(`/api/conversations/${encodeURIComponent(cid)}`,{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({scene})});if(!r.ok)throw new Error('切换失败');
    document.querySelectorAll('.scene').forEach(x=>x.classList.toggle('active',x===button));const copy=SCENE_COPY[scene]||SCENE_COPY.product_governance;
    if(byId('sceneEyebrow'))byId('sceneEyebrow').textContent=copy.name;if(byId('conversationMeta'))byId('conversationMeta').textContent=copy.meta;
    if(matchMedia('(max-width:820px)').matches){byId('leftbar')?.classList.remove('open');if(byId('drawerScrim'))byId('drawerScrim').hidden=true;byId('navToggle')?.setAttribute('aria-expanded','false')}
  }catch{original?.call(button,event)}
}
function syncOutcomeVisual(){
  const label=byId('taskReadyChip')?.textContent||'';const ring=document.querySelector('.status-ring');const unit=ring?.querySelector('small');const percent=byId('statusPercent');if(!ring||!percent)return;
  if(label.includes('待补资料')){percent.textContent='待补';ring.classList.add('word');if(unit)unit.hidden=true}
  else if(label.includes('需要重试')){percent.textContent='重试';ring.classList.add('word');if(unit)unit.hidden=true}
}
function ensureAssetPolicy(){
  const panel=byId('panel-assets');const copy=panel?.querySelector('.panel-copy');if(!copy||panel.querySelector('.asset-policy-note'))return;
  const note=document.createElement('div');note.className='asset-policy-note';
  note.innerHTML='<b>证据资产范围</b><p>资料默认持续参与后续核对。你可以将错误资料排除未来分析；若它尚未进入历史消息或证据链，还可以永久删除。</p><button type="button">新建干净任务</button>';
  note.querySelector('button').onclick=()=>byId('newTaskBtn')?.click();copy.after(note);
}

let searchConversations=[];
function searchSceneRows(){return Object.entries(SCENE_COPY).map(([key,v])=>({type:'scene',key,title:v.name,sub:v.meta,icon:v.name.slice(0,1)}))}
function fmtSearchTime(ts){try{return new Date(Number(ts)*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}catch{return''}}
async function loadExtendedSearch(){try{const r=await fetch('/api/conversations?limit=100');if(r.ok)searchConversations=await r.json()}catch{}}
function renderExtendedSearch(query=''){
  const box=byId('commandResults');if(!box)return;const q=query.trim().toLowerCase();
  const rows=[...searchSceneRows(),...searchConversations.map(c=>({type:'conversation',key:c.id,title:c.title,sub:`${SCENE_COPY[c.scene]?.name||'业务任务'} · ${fmtSearchTime(c.updated_at)}`,icon:'任'}))];
  const filtered=q?rows.filter(x=>(x.title+' '+x.sub).toLowerCase().includes(q)):rows.slice(0,16);
  box.innerHTML=filtered.length?filtered.map((r,i)=>`<button class="command-result ${i===0?'active':''}" data-evo-search="${i}"><span>${r.icon}</span><div><b>${r.title.replace(/[&<>\"']/g,'')}</b><small>${r.sub.replace(/[&<>\"']/g,'')}</small></div></button>`).join(''):'<div class="empty-side">没有找到匹配项。</div>';
  box.querySelectorAll('[data-evo-search]').forEach(btn=>btn.onclick=()=>runExtendedSearch(filtered[Number(btn.dataset.evoSearch)]));
}
function closeCommandModal(){const modal=byId('commandModal');if(modal)modal.hidden=true;const input=byId('commandInput');if(input)input.value=''}
function runExtendedSearch(row){
  if(!row)return;if(row.type==='scene'){document.querySelector(`.scene[data-scene="${row.key}"]`)?.click();closeCommandModal();requestAnimationFrame(()=>byId('messageInput')?.focus());return}
  const u=new URL(location.href);u.searchParams.set('conversation',row.key);u.searchParams.delete('tour');location.assign(u.toString());
}
function setupExtendedCommandSearch(){
  const trigger=byId('commandBtn'),input=byId('commandInput');if(!trigger||!input)return;
  trigger.addEventListener('click',()=>{loadExtendedSearch().then(()=>renderExtendedSearch(input.value))});input.oninput=event=>renderExtendedSearch(event.target.value);
  input.onkeydown=event=>{const buttons=[...byId('commandResults').querySelectorAll('.command-result')];let idx=buttons.findIndex(b=>b.classList.contains('active'));if(idx<0)idx=0;if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();if(!buttons.length)return;buttons[idx]?.classList.remove('active');idx=event.key==='ArrowDown'?(idx+1)%buttons.length:(idx-1+buttons.length)%buttons.length;buttons[idx].classList.add('active');buttons[idx].scrollIntoView({block:'nearest'})}else if(event.key==='Enter'){event.preventDefault();buttons[Math.max(0,idx)]?.click()}};
  loadExtendedSearch();
}

function bindProductPolish(){
  const guide=byId('productGuideBtn');if(guide)guide.onclick=()=>openTour('manual');const assets=byId('assetLibraryBtn');if(assets)assets.onclick=openAssetsPanel;
  const share=byId('shareBtn');if(share)share.onclick=async()=>showToast(await copyText(cleanShareLink())?'任务链接已复制':'复制失败');
  document.querySelectorAll('.scene').forEach(button=>{const original=button.onclick;button.onclick=event=>reuseEmptyTaskForScene(button,original,event)});
  ensureAssetPolicy();setupExtendedCommandSearch();syncOutcomeVisual();
  const observer=new MutationObserver(()=>{syncOutcomeVisual();ensureAssetPolicy()});
  ['taskReadyChip','panel-assets'].forEach(id=>{const el=byId(id);if(el)observer.observe(el,{childList:true,subtree:true,characterData:true})});
}
function bindTour(){
  const tour=byId('productTour');if(!tour)return;
  byId('tourCloseBtn')?.addEventListener('click',()=>closeTour());byId('tourSkipBtn')?.addEventListener('click',()=>closeTour());byId('tourStartBtn')?.addEventListener('click',startExample);tour.addEventListener('click',event=>{if(event.target===tour)closeTour()});
  window.addEventListener('keydown',event=>{if(tour.hidden)return;if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closeTour();return}if(event.key!=='Tab')return;const rows=focusables();if(!rows.length)return;const first=rows[0],last=rows.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}},true);
  const params=new URLSearchParams(location.search);const forced=params.get('tour')==='1';const sharedTask=params.has('conversation');if(forced||(!sharedTask&&!readSeen()))requestAnimationFrame(()=>openTour(forced?'forced':'first-run'));
}
function bootProductLayer(){bindTour();bindProductPolish();import('/assets/lifecycle.js').catch(()=>{})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bootProductLayer,{once:true});else bootProductLayer();
