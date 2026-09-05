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
g('js',new Date());
// Strip arbitrary query strings and referrer paths to avoid collecting personal data.
let ref='';try{ref=new URL(document.referrer).origin}catch(_){}
g('config',id,{send_page_view:false,page_location:location.origin+p,page_referrer:ref,allow_google_signals:false,allow_ad_personalization_signals:false});
const common={market,product_id:product,experiment_id:experiment,traffic_type:qa?'internal':'external',page_location:location.origin+p,page_referrer:ref};
g('event',qa?'hc_qa_page_view':'page_view',common);
const s=document.createElement('script');s.async=true;s.src='https://www.googletagmanager.com/gtag/js?id='+id;document.head.appendChild(s);
document.addEventListener('click',e=>{
 const a=e.target.closest&&e.target.closest('a[href]');if(!a)return;
 let u;try{u=new URL(a.href,location.href)}catch(_){return}
 if(!['link.coupang.com','www.amazon.com'].includes(u.hostname)||!a.rel.split(/\s+/).includes('sponsored'))return;
 g('event',qa?'hc_qa_affiliate_click':'affiliate_click',Object.assign({},common,{retailer:u.hostname==='www.amazon.com'?'amazon':'coupang',link_position:a.classList.contains('button')?'button':'product_image',transport_type:'beacon'}));
},true);
})();
