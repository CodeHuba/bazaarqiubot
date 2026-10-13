'use strict';
(async () => {
 const $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const params=new URLSearchParams(location.search), space=params.get('space'), run=params.get('run');
 $('#back').href='/zaza#'+encodeURIComponent(space||'');
 try {
  if(!space||!run) throw Error('回放链接缺少空间或对局信息。');
  const response=await fetch('/api/zaza/spaces/'+encodeURIComponent(space)+'/runs/'+encodeURIComponent(run));
  const data=await response.json(); if(!response.ok) throw Error(data.error||'加载失败');
  const final=data.run_display, days=data.days||[], frames=[...days,{...data.run_display,final:true}]; let index=frames.length-1;
  $('#replay-title').textContent=(data.space.name||'扎扎空间')+' · 阵容回放';
  $('#replay-meta').textContent=`${final.hero||'未知职业'} · ${final.wins??0}胜${final.losses??0}负 · 第 ${data.scope.season} 赛季 ${data.scope.phase}`;
  $('#day-tabs').innerHTML=`<div class="timeline-track" aria-hidden="true"></div><div class="timeline-nodes">${frames.map((f,i)=>`<button type="button" class="timeline-node${f.final?' timeline-final':''}" data-frame="${i}" aria-label="${f.final?'终局':'Day '+esc(f.day)}"><span class="timeline-dot"></span><span class="timeline-label">${f.final?'终局':'Day '+esc(f.day)}</span></button>`).join('')}</div>`;
  function show(i) {
   index=i;const frame=frames[i];
   $('#board-title').textContent=frame.final?'终局阵容':`Day ${frame.day} · ${({win:'胜利',loss:'失败'})[String(frame.outcome||'').toLowerCase()]||frame.outcome||'结果未知'}`;
   $('#board').innerHTML=(frame.items||[]).map(c=>window.zazaCardMarkup(c)).join('')||'<span class="muted">暂无阵容数据</span>';
   $('#skills').innerHTML=(frame.skills||[]).map(c=>window.zazaCardMarkup(c,true)).join('')||'<span class="muted">暂无已收集技能</span>';
   document.querySelectorAll('[data-frame]').forEach((b,j)=>{b.classList.toggle('active',j===i);b.setAttribute('aria-pressed',String(j===i));});
   $('#prev-day').disabled=i===0;$('#next-day').disabled=i===frames.length-1;
  }
  $('#day-tabs').onclick=e=>{const b=e.target.closest('[data-frame]');if(b)show(Number(b.dataset.frame));};
  $('#prev-day').onclick=()=>show(Math.max(0,index-1));$('#next-day').onclick=()=>show(Math.min(frames.length-1,index+1));
  show(index);
 } catch(e) {$('#replay-error').textContent=e.message;}
})();
