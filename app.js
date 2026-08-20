(()=>{
  window.heightcueTelemetry={version:'20260821v5',ready:true};
  const endpoint="https://kctgnsptmtdnvlivnnio.supabase.co/rest/v1/rpc/hc_record_public_outbound";
  const publishableKey="sb_publishable_rIhgcXj8RK3vMqSUtx6Ieg_gBxvavtu";
  const sendOutbound=(anchor,destination)=>{
    const market=(anchor.dataset.market||'').toUpperCase();
    const productKey=(anchor.dataset.productKey||'').toLowerCase();
    const trackingKey=(anchor.dataset.track||'').toLowerCase();
    window.heightcueTelemetry.last={market,productKey,trackingKey,state:'validated'};
    if(market!=='US'||!productKey||!trackingKey||!crypto.randomUUID){window.heightcueTelemetry.last.state='skipped';return;}
    const payload={
      p_event_key:crypto.randomUUID(),
      p_tracking_key:trackingKey,
      p_market:market,
      p_product_key:productKey,
      p_source_path:location.pathname.slice(0,200),
      p_destination_host:destination.hostname.toLowerCase()
    };
    window.heightcueTelemetry.last.state='sending';
    fetch(endpoint,{method:'POST',headers:{apikey:publishableKey,Authorization:'Bearer '+publishableKey,'Content-Type':'application/json'},body:JSON.stringify(payload),keepalive:true})
      .then(response=>{window.heightcueTelemetry.last.state=response.ok?'accepted':'rejected';window.heightcueTelemetry.last.status=response.status;})
      .catch(()=>{window.heightcueTelemetry.last.state='network-error';});
  };
  document.addEventListener('click',event=>{
    const anchor=event.target.closest('a[data-track]');
    if(!anchor)return;
    let destination;
    try{destination=new URL(anchor.href,location.href)}catch{return}
    const detail={label:anchor.dataset.track,market:anchor.dataset.market||'',product_key:anchor.dataset.productKey||'',destination_host:destination.hostname,source_path:location.pathname,at:new Date().toISOString()};
    window.dispatchEvent(new CustomEvent('heightcue:outbound',{detail}));
    if(typeof window.gtag==='function')window.gtag('event','outbound_click',detail);
    sendOutbound(anchor,destination);
  });
  window.heightcueTelemetry.listenerRegistered=true;
})();
