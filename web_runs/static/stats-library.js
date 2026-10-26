(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  if(!el('library-stats-content'))return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>Number(v||0).toLocaleString('zh-CN'),rate=v=>v==null?'—':(100*v).toFixed(1)+'%';
  let days=7,sequence=0,controller,loaded=false;
  const visible=()=>!el('panel-features').hidden;
  const table=(headers,rows)=>`<div class="table-card table-scroll"><table class="tbl"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  async function load(){
    const serial=++sequence;controller?.abort();
    if(!visible()){loaded=false;return;}
    controller=new AbortController();el('library-stats-status').textContent='正在加载图鉴构筑统计…';
    try{
      const response=await fetch('/api/stats/library?days='+days,{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw Error(response.status===401?'登录已失效，请刷新页面':'统计暂不可用，请重试');
      const data=await response.json();if(serial!==sequence)return;
      const s=data.summary,actions=Object.fromEntries(data.actions.map(row=>[row.action,row]));
      const cards=[[num(s.visitors),'图鉴访客'],[rate(s.penetration),'全站访客使用占比'],[rate(s.build_rate),'构筑使用率'],[rate(s.export_rate),'导出使用率']];
      el('library-stats-content').innerHTML=`<div class="kpi-grid">${cards.map(([value,label])=>`<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div></div>`).join('')}</div>
        <p class="panel-note">图鉴访问 ${num(s.views)} 次；构筑用户 ${num(s.builders)} 人；成功导出用户 ${num(s.exporters)} 人；全站已埋点访客 ${s.site_visitors==null?'—':num(s.site_visitors)} 人。</p>
        ${table(['操作','次数','独立访客'],data.actions.map(row=>[row.label,num(row.count),num(row.visitors)]))}
        <h3>每日使用情况</h3>${table(['北京时间日期','访问次数','图鉴访客','构筑用户','导出用户'],data.daily.length?data.daily.map(row=>[row.day,num(row.views),num(row.visitors),num(row.builders),num(row.exporters)]):[['暂无记录','0','0','0','0']])}
        <p class="panel-note">导出开始 ${num(actions.export_start.count)} 次、成功 ${num(actions.export_success.count)} 次、失败 ${num(actions.export_error.count)} 次。成功导出平均耗时 ${actions.export_success.avg_ms==null?'—':(actions.export_success.avg_ms/1000).toFixed(2)+' 秒'}。</p>`;
      el('library-stats-status').textContent='采集起点：'+new Date(data.tracking_since).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）。历史使用无法补录。';loaded=true;
    }catch(error){if(serial!==sequence||error.name==='AbortError')return;loaded=false;el('library-stats-status').textContent=error.message;}
  }
  el('library-stats-refresh').onclick=()=>void load();
  window.addEventListener('stats:period-change',event=>{days=event.detail.days;loaded=false;void load();});
  window.addEventListener('stats:panel-change',event=>{days=event.detail.days;if(event.detail.panel==='features'&&!loaded)void load();});
  if(visible())void load();
})();
