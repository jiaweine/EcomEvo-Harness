const TOUR_KEY='ecomevo.product-tour.v1';

const byId=id=>document.getElementById(id);
let returnFocus=null;

function readSeen(){
  try{return localStorage.getItem(TOUR_KEY)==='seen'}catch{return false}
}
function markSeen(){
  try{localStorage.setItem(TOUR_KEY,'seen')}catch{}
}
function focusables(){
  const tour=byId('productTour');
  if(!tour)return[];
  return [...tour.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')]
    .filter(el=>!el.hidden&&el.getClientRects().length);
}
function openTour(source='manual'){
  const tour=byId('productTour');
  if(!tour||!tour.hidden)return;
  returnFocus=document.activeElement;
  tour.hidden=false;
  tour.dataset.source=source;
  document.body.classList.add('tour-open');
  requestAnimationFrame(()=>byId('tourCloseBtn')?.focus());
}
function closeTour({remember=true,restoreFocus=true}={}){
  const tour=byId('productTour');
  if(!tour||tour.hidden)return;
  if(remember)markSeen();
  tour.hidden=true;
  document.body.classList.remove('tour-open');
  const target=returnFocus;
  returnFocus=null;
  if(restoreFocus&&target&&document.contains(target))requestAnimationFrame(()=>target.focus());
}
function startExample(){
  markSeen();
  closeTour({remember:false,restoreFocus:false});
  const input=byId('messageInput');
  if(!input)return;
  input.value='帮我核对这批商品的标题、主图和详情，找出需要下架或补资质的高风险项，并说明依据。';
  input.dispatchEvent(new Event('input',{bubbles:true}));
  requestAnimationFrame(()=>input.focus());
}
function bindTour(){
  const tour=byId('productTour');
  if(!tour)return;

  byId('productGuideBtn')?.addEventListener('click',()=>openTour('manual'));
  byId('tourCloseBtn')?.addEventListener('click',()=>closeTour());
  byId('tourSkipBtn')?.addEventListener('click',()=>closeTour());
  byId('tourStartBtn')?.addEventListener('click',startExample);
  tour.addEventListener('click',event=>{
    if(event.target===tour)closeTour();
  });

  window.addEventListener('keydown',event=>{
    if(tour.hidden)return;
    if(event.key==='Escape'){
      event.preventDefault();
      event.stopImmediatePropagation();
      closeTour();
      return;
    }
    if(event.key!=='Tab')return;
    const rows=focusables();
    if(!rows.length)return;
    const first=rows[0],last=rows.at(-1);
    if(event.shiftKey&&document.activeElement===first){
      event.preventDefault();
      last.focus();
    }else if(!event.shiftKey&&document.activeElement===last){
      event.preventDefault();
      first.focus();
    }
  },true);

  const params=new URLSearchParams(location.search);
  const forced=params.get('tour')==='1';
  const sharedTask=params.has('conversation');
  if(forced||(!sharedTask&&!readSeen())){
    requestAnimationFrame(()=>openTour(forced?'forced':'first-run'));
  }
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',bindTour,{once:true});
}else{
  bindTour();
}
