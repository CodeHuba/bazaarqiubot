(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  if(!el('comp-stats-content'))return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>Number(v||0).toLocaleString('zh-CN');
  const rate=v=>v==null?'—':(100*v).toFixed(1)+'%';
  const ms=v=>v==null?'—':(v/1000).toFixed(2)+' 秒';
  const sortNames={reliable:'稳健推荐',rate:'10 胜率',appearance:'出现次数'};
  const stageNames={prepare:'准备组件',art:'加载卡图',render:'绘制长图',encode:'导出图片'};
  const outcomeNames={success:'有推荐组合',partial:'部分有结果',empty:'无匹配',insufficient:'样本不足',unrecognized:'卡牌未识别',error:'查询异常',unverified:'未验证'};
  let days=7,sequence=0,controller,loaded=false;
  const visible=()=>!el('panel-features').hidden;
  const table=(heading,rows)=>`<div class="table-card table-scroll"><table class="tbl"><thead><tr>${heading.map(v=>`<th>${esc(v)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  async function load(){
    const id=++sequence;controller?.abort();
    if(!visible()){loaded=false;return;}
    controller=new AbortController();
    el('comp-stats-status').textContent='正在加载阵容分析统计…';
    el('comp-stats-content').replaceChildren();
    try{
      const response=await fetch('/api/stats/comp?days='+days,{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw Error(response.status===401?'登录已失效，请刷新页面':'统计暂不可用，请点击刷新重试');
      const data=await response.json();
      if(id!==sequence)return;
      const actions=Object.fromEntries(data.actions.map(row=>[row.action,row]));
      const q=data.queries,success=actions.share_generated,started=actions.share_generate;
      const cards=[[num(q.count),'主动查询'],[num(q.visitors),'查询独立访客'],[rate(q.result_rate),'有推荐组合率'],[num(success.count),'分享图生成成功']];
      const items=Object.entries(q.item_counts).map(([n,count])=>`${n} 件：${num(count)} 次`).join('；')||'暂无记录';
      const summary=[['接口请求量',num(data.requests.count)],['接口异常',num(data.requests.errors)],['接口平均响应',ms(data.requests.avg_ms)],['成功生成平均耗时',ms(success.avg_ms)]];
      el('comp-stats-content').innerHTML=`<div class="kpi-grid">${cards.map(([value,label])=>`<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div></div>`).join('')}</div>
        <p class="panel-note">查询物品数量：${esc(items)}。有推荐组合率 = 有推荐组合的主动查询 / 全部主动查询。</p>
        ${table(['查询结果','次数'],Object.entries(outcomeNames).map(([key,label])=>[label,num(q.outcomes[key])]))}
        <h3>交互与分享</h3>${table(['操作','次数','独立访客'],data.actions.map(row=>[row.label,num(row.count),num(row.visitors)]))}
        <p class="panel-note">本期开始生成 ${num(started.count)} 次；成功 ${num(success.count)} 次、失败 ${num(actions.share_error.count)} 次、主动关闭 ${num(actions.share_cancel.count)} 次。跨时段或离开页面未送达的事件可能导致起止数量不一致，不据此计算转化率。</p>
        ${table(['性能与运行','数值'],summary)}
        <p class="panel-note">排序使用：${esc(data.sorts.map(row=>`${sortNames[row.detail]||row.detail} ${num(row.count)} 次`).join('；')||'暂无记录')}。生成失败阶段：${esc(data.issues.filter(row=>row.action==='share_error').map(row=>`${stageNames[row.detail]} ${num(row.count)} 次`).join('；')||'暂无记录')}。使用占位卡图生成：${num(data.issues.find(row=>row.action==='share_generated'&&row.detail==='missing_art')?.count)} 次。</p>`;
      el('comp-stats-status').textContent='交互埋点采集起点：'+new Date(data.tracking_since).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）。查询统计沿用已有历史数据；接口性能自本次接入起记录。';
      loaded=true;
    }catch(error){
      if(id!==sequence||error.name==='AbortError')return;
      loaded=false;el('comp-stats-status').textContent='加载失败：'+error.message;
    }
  }
  el('comp-stats-refresh').onclick=()=>void load();
  window.addEventListener('stats:period-change',event=>{days=event.detail.days;loaded=false;void load();});
  window.addEventListener('stats:panel-change',event=>{days=event.detail.days;if(event.detail.panel==='features'&&!loaded)void load();});
  if(visible())void load();
})();
