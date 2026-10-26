const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require.resolve('../web_runs/static/library-tracking.js'),'utf8');
function fixture(enabled,retry=false){
  let sequence=0;const calls=[],storage=new Map();
  const context={document:{querySelector:()=>enabled?{content:'production'}:null},crypto:{randomUUID:()=>`test-uuid-${++sequence}`},
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},window:{},
    fetch:async(url,options)=>{calls.push({url,...options});return {status:retry&&calls.length===1?503:200};}};
  vm.runInNewContext(code,context);return {context,calls};
}
(async()=>{
  const preview=fixture(false);assert.equal(preview.context.window.BazaarLibraryAnalytics,undefined);assert.equal(preview.calls.length,0);
  const live=fixture(true,true);const track=live.context.window.BazaarLibraryAnalytics.track;
  await track('view');await track('view');await track('draft_save','manual');await track('export_success','complete',1234.5);
  const reports=live.calls.filter(c=>c.url==='/api/track/library').map(c=>JSON.parse(c.body));
  assert.equal(reports.length,4);assert.equal(reports[0].id,reports[1].id,'retry must reuse the same event ID');
  assert.equal(new Set(reports.map(p=>p.session)).size,1);assert.equal(reports[3].duration_ms,1235);
  assert.equal(live.calls.filter(c=>c.url==='/api/track/pv').length,1,'view only once per page');
  assert.ok(reports.every(p=>Object.keys(p).every(k=>['id','session','action','detail','duration_ms'].includes(k))));
  console.log('Verified production opt-in, no staging collection, stable anonymous IDs, retry idempotency, page-view once and minimal payloads.');
})().catch(error=>{console.error(error);process.exitCode=1;});
