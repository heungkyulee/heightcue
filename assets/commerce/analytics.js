/* HeightCue commerce measurement, no ad or analytics cookies. */
(()=>{'use strict';
if(window.__hcMeasured)return;window.__hcMeasured=true;
const id='G-1LW7HW1R4M',p=location.pathname;
const market=p.startsWith('/us/')?'US':p.startsWith('/kr/')?'KR':'global';
const product=p.includes('flip-light')?'flip-light':p.includes('chomchom')?'chomchom':'none';
const experiment=p.endsWith('/flip-light-photo01.html')?'kr-flip-photo-01':p.endsWith('/chomchom-photo01.html')?'us-chomchom-photo-01':'unattributed';
const qa=new URLSearchParams(location.search).get('hc_qa')==='1';
window.dataLayer=window.dataLayer||[];function g(){window.dataLayer.push(arguments)}window.gtag=g;
g('consent','default',{ad_storage:'denied',analytics_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});
let accepted=false;try{accepted=localStorage.getItem('hc_analytics_choice')==='allow'}catch(_){}
if(accepted)g('consent','update',{analytics_storage:'granted'});
g('js',new Date());
// Strip arbitrary query strings and referrer paths to avoid collecting personal data.
let ref='';try{ref=new URL(document.referrer).origin}catch(_){}
g('config',id,{send_page_view:false,page_location:location.origin+p,page_referrer:ref,allow_google_signals:false,allow_ad_personalization_signals:false});
const common={market,product_id:product,experiment_id:experiment,traffic_type:qa?'internal':'external',page_location:location.origin+p,page_referrer:ref};
g('event',qa?'hc_qa_page_view':'page_view',common);
// A small optional control enables reportable analytics only after a visitor chooses it.
const panel=document.createElement('div');panel.id='hc-analytics-choice';
panel.style.cssText='max-width:900px;margin:12px auto;padding:12px 25px;font:13px/1.5 system-ui;color:#536052';
const en=market==='US';
const label=document.createElement('span');label.textContent=en?'Optional visit statistics: ':'선택 방문 통계: ';panel.appendChild(label);
for(const choice of ['allow','deny']){const b=document.createElement('button');b.type='button';b.textContent=en?(choice==='allow'?'Allow analytics cookies':'Decline'):(choice==='allow'?'분석 쿠키 허용':'거절');b.style.cssText='margin:4px;padding:6px 10px;cursor:pointer';b.addEventListener('click',()=>{const grant=choice==='allow';try{localStorage.setItem('hc_analytics_choice',choice)}catch(_){}g('consent','update',{analytics_storage:grant?'granted':'denied'});status.textContent=en?(grant?'Allowed':'Declined'):(grant?'허용됨':'거절됨');if(grant&&!accepted)g('event',qa?'hc_qa_consent':'analytics_consent',common);accepted=grant;});panel.appendChild(b)}
const status=document.createElement('span');status.setAttribute('aria-live','polite');status.textContent=accepted?(en?'Allowed':'허용됨'):(en?'No analytics cookies':'분석 쿠키 사용 안 함');panel.appendChild(status);document.body.appendChild(panel);
const s=document.createElement('script');s.async=true;s.src='https://www.googletagmanager.com/gtag/js?id='+id;document.head.appendChild(s);
document.addEventListener('click',e=>{
 const a=e.target.closest&&e.target.closest('a[href]');if(!a)return;
 let u;try{u=new URL(a.href,location.href)}catch(_){return}
 if(!['link.coupang.com','www.amazon.com'].includes(u.hostname)||!a.rel.split(/\s+/).includes('sponsored'))return;
 g('event',qa?'hc_qa_affiliate_click':'affiliate_click',Object.assign({},common,{retailer:u.hostname==='www.amazon.com'?'amazon':'coupang',link_position:a.classList.contains('button')?'button':'product_image',transport_type:'beacon'}));
},true);
})();
