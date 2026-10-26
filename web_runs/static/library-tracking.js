/* Success-only interaction counts. Never send draft content or search text. */
(() => {
  'use strict';
  if(document.querySelector('meta[name="library-analytics"]')?.content!=='production')return;
  const id=()=>crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+Math.random().toString(36).slice(2);
  let fingerprint;
  try{fingerprint=localStorage.getItem('_bz_fp');if(!fingerprint){fingerprint=id();localStorage.setItem('_bz_fp',fingerprint);}}catch(_){fingerprint=id();}
  const session=id(),seen=new Set();
  async function track(action,detail='',duration_ms){
    if(action==='view'&&seen.has(action))return;
    if(action==='view')seen.add(action);
    const payload={id:id(),session,action,detail};
    if(Number.isFinite(duration_ms))payload.duration_ms=Math.min(3600000,Math.max(0,Math.round(duration_ms)));
    const options={method:'POST',headers:{'Content-Type':'application/json','X-Fingerprint':fingerprint},body:JSON.stringify(payload),keepalive:true};
    try{
      const response=await fetch('/api/track/library',options);
      if(response.status>=500)await fetch('/api/track/library',options);
    }catch(_){/* Analytics must never block editing. */}
    if(action==='view')fetch('/api/track/pv',{method:'POST',headers:options.headers,body:JSON.stringify({tab:'library'}),keepalive:true}).catch(()=>{});
  }
  window.BazaarLibraryAnalytics=Object.freeze({track});
})();
