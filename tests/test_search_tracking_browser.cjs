const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {chromium} = require('playwright');
const root = path.resolve(__dirname,'../web_runs/static');

async function setup(t,entry='runs',query='') {
  const browser=await chromium.launch({headless:true,channel:'chromium'});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  page.setDefaultTimeout(8000);
  const searches=[],reports=[],errors=[];
  let telemetryStatus=200;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!=='http://search.test') return route.abort();
    if(url.pathname==='/api/track/search') {
      reports.push({payload:req.postDataJSON(),headers:req.headers()});
      return route.fulfill({status:telemetryStatus,json:{ok:telemetryStatus===200}});
    }
    const headers={'X-Search-Receipt':'backend-signed-receipt'};
    if(['/api/runs','/api/comp/card','/api/winrate','/api/partner'].includes(url.pathname)) searches.push({url,headers:req.headers()});
    if(url.pathname==='/api/runs') return route.fulfill({headers,json:{total:10,pages:2,page:Number(url.searchParams.get('page')||1),runs:[{hero:'Vanessa',username:'tester',wins:10,losses:1,cards:[],url:'#'}]}});
    if(url.pathname==='/api/comp/card') return route.fulfill({headers,json:{anchors:[{cardId:'a',name:'甲'}],scope:{phase:'19.2',rank:'all'},baseline:{count:3,rate:0,average_wins:4},candidate_count:0,groups:[],examples:[]}});
    if(url.pathname==='/api/winrate') return route.fulfill({headers,json:{results:[{card_names:['甲'],total:3,ten_win:2,rate:.66},{card_names:['乙'],total:0,ten_win:0,rate:0}]}});
    if(url.pathname==='/api/partner') return route.fulfill({headers,json:{card_name:'甲',target_total:3,by_winrate:[],by_appear:[]}});
    if(url.pathname.startsWith('/api/')) return route.fulfill({json:{}});
    const file=url.pathname==='/'+entry?entry+'.html':url.pathname.startsWith('/static/')?url.pathname.slice(8):null;
    if(!file) return route.fulfill({status:404,body:''});
    return route.fulfill({body:readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://search.test/'+entry+query);
  return {page,searches,reports,errors,setTelemetryStatus:status=>{telemetryStatus=status;}};
}
const eventually = async (page,read,expected) => {
  for(let i=0;i<100;i++) {if(read()===expected) return; await page.waitForTimeout(20);}
  assert.equal(read(),expected);
};

test('run list counts manual submit, skips automatic links and every pagination action',async t=>{
  const {page,searches,reports,errors}=await setup(t,'runs','?cards=甲&auto=1');
  await page.locator('.run-item').waitFor();
  assert.equal(searches.length,1);
  assert.equal(searches[0].headers['x-search-action'],undefined);
  assert.equal(reports.length,0);
  await page.evaluate(()=>{lastQuery=0;});
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await eventually(page,()=>reports.length,1);
  assert.equal(reports[0].payload.feature,'runs');
  assert.equal(reports[0].payload.receipt,'backend-signed-receipt');
  assert.equal(searches[1].headers['x-search-action'],'user');
  assert.equal(searches[1].headers['x-fingerprint'],reports[0].headers['x-fingerprint']);
  await page.locator('.run-item').waitFor();
  await page.evaluate(()=>{lastQuery=0;});
  await page.getByRole('button',{name:'2',exact:true}).click();
  await page.waitForFunction(()=>location.search.includes('page=2'));
  await page.evaluate(()=>{lastQuery=0;});
  await page.getByRole('button',{name:'1',exact:true}).click();
  await page.waitForFunction(()=>location.search.includes('page=1'));
  assert.equal(reports.length,1);
  assert.equal(searches.at(-1).headers['x-search-action'],undefined);
  assert.deepEqual(errors,[]);
});

test('analysis records a valid multi-item AND query; enchantment rejection stays local',async t=>{
  const {page,reports,searches,errors}=await setup(t);
  await page.evaluate(()=>{addRunCard('甲','Fiery');setViewMode('comp');lastQuery=0;});
  await page.getByRole('button',{name:'查询',exact:true}).click();
  assert.match(await page.locator('#comp-result').textContent(),/附魔筛选/);
  assert.equal(reports.length,0);
  assert.equal(searches.length,0);
  await page.evaluate(()=>{setRunCardEnchantment(0,'');addRunCard('乙');lastQuery=0;});
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await eventually(page,()=>reports.length,1);
  assert.equal(reports[0].payload.feature,'comp_card');
  assert.deepEqual(JSON.parse(reports[0].payload.params.cards),['甲','乙']);
  assert.deepEqual(errors,[]);
});

test('winrate comparison remains comma separated, and failed telemetry does not retry the query',async t=>{
  const {page,reports,searches,errors,setTelemetryStatus}=await setup(t,'winrate');
  setTelemetryStatus(503);
  await page.evaluate(()=>{_wrSelect({zh:'甲',en:'A'});_wrSelect({zh:'乙',en:'B'});lastQuery=0;performance.getEntriesByName=undefined;});
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await eventually(page,()=>reports.length,2);
  assert.equal(searches.length,1);
  assert.equal(reports[0].payload.params.cards,'甲,乙');
  assert.equal(reports[0].payload.params.multi,'1');
  assert.equal(reports[0].payload.feature,'winrate');
  assert.equal(reports[0].payload.receipt,'backend-signed-receipt');
  assert.equal(reports[0].payload.source,'unknown');
  assert.equal(reports[0].payload.id,reports[1].payload.id);
  assert.equal(await page.locator('#wr-error').textContent(),'');
  assert.match(await page.locator('#wr-result').textContent(),/甲/);
  assert.deepEqual(errors,[]);
});

test('partner validates locally, counts a cached receipt again with a new action id, falls back on expired receipts',async t=>{
  const {page,reports,searches,errors,setTelemetryStatus}=await setup(t,'partner');
  await page.getByRole('button',{name:'查询',exact:true}).click();
  assert.equal(searches.length,0);
  await page.locator('#pt-card').fill('甲');
  await page.evaluate(()=>{lastQuery=0;});
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await eventually(page,()=>reports.length,1);
  assert.equal(reports[0].payload.feature,'partner');
  assert.match(await page.locator('#pt-result').textContent(),/暂无满足条件/);
  setTelemetryStatus(400);
  await page.evaluate(()=>{lastQuery=0;});
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await eventually(page,()=>reports.length,3);
  assert.notEqual(reports[0].payload.id,reports[1].payload.id);
  assert.equal(reports[1].payload.receipt,reports[0].payload.receipt);
  assert.equal(reports[2].payload.fallback,'unverified');
  assert.equal(reports[2].payload.receipt,null);
  assert.equal(reports[2].payload.id,reports[1].payload.id);
  assert.deepEqual(errors,[]);
});

test('real HTTP cache hits still emit a separate manual action with a cache source',async t=>{
  const http=require('node:http');
  let queries=0;
  const reports=[];
  const server=http.createServer((req,res)=>{
    if(req.url==='/static/search-tracking.js') {
      res.setHeader('Content-Type','application/javascript');
      return res.end(readFileSync(path.join(root,'search-tracking.js')));
    }
    if(req.url.startsWith('/api/comp/card')) {
      queries++;
      res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'public,max-age=60','Vary':'X-Search-Action','X-Search-Receipt':'same-cached-receipt'});
      return res.end(JSON.stringify({total_runs:4,recommendations:[]}));
    }
    if(req.url==='/api/track/search') {
      let body='';
      req.on('data',chunk=>{body+=chunk;});
      return req.on('end',()=>{reports.push(JSON.parse(body));res.setHeader('Content-Type','application/json');res.end('{"ok":true}');});
    }
    res.setHeader('Content-Type','text/html');
    res.end('<script src="/static/search-tracking.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const browser=await chromium.launch({headless:true,channel:'chromium'});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const submit=()=>page.evaluate(async()=>{const response=await BazaarSearch.query('comp_card','/api/comp/card?card=A');return response.json();});
  assert.equal((await submit()).total_runs,4);
  await eventually(page,()=>reports.length,1);
  assert.equal((await submit()).total_runs,4);
  await eventually(page,()=>reports.length,2);
  assert.equal(queries,1,'second query is served entirely from Chromium HTTP cache');
  assert.notEqual(reports[0].id,reports[1].id);
  assert.equal(reports[0].source,'network');
  assert.equal(reports[1].source,'browser_cache');
  assert.equal(reports[0].receipt,reports[1].receipt);
});
