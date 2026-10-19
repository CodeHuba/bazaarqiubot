// NODE_PATH must include playwright. Executes the shipped renderer and QR code generator.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync, mkdirSync} = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname,'../web_runs/static');
const art = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="160"><defs><linearGradient id="a" x2="1" y2="1"><stop stop-color="#365b7e"/><stop offset="1" stop-color="#d89e57"/></linearGradient></defs><rect width="120" height="160" fill="url(#a)"/><path d="M20 115L55 30 95 110Z" fill="#f8de9a"/><circle cx="60" cy="80" r="16" fill="#d05d5f"/></svg>';
function fixture() {
  const cards = ['Bronze','Silver','Gold','Diamond','Legendary',null].map((tier,i) => ({
    cardId:'card-'+i,name:'隐藏中文卡名'+i,img:'/art.svg',size:['Small','Medium','Large'][i%3],tier,
    ...(i===2 ? {enchantment:'Fiery',enchantment_zh:'炽焰',matched:true} : {}),
  }));
  const skills = Array.from({length:48},(_,i) => ({cardId:'skill-'+i,name:'隐藏技能名'+i,img:'/art.svg',type:'Skill',tier:['Bronze','Silver','Gold','Diamond'][i%4],matched:i===47}));
  return {id:'stable-run',hero:'Vanessa',username:'测试玩家',created_at:'2026-10-16T03:30:00Z',season:18,phase:'18.2',wins:10,losses:2,day:13,cards,skills,screenshot:'/shot.svg'};
}
async function setup(t,width=1280,options={}) {
  const browser = await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {channel:'chromium'})});
  t.after(() => browser.close());
  const context = await browser.newContext({viewport:{width,height:950},acceptDownloads:true});
  const page = await context.newPage(), errors=[],requests=[];
  page.on('pageerror',e => errors.push(e.message));
  await page.route('**/*',async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://runs.test') return route.abort();
    if (url.pathname === '/api/runs') return route.fulfill({json:{runs:[fixture()],total:1,pages:1,page:1,min_wins:10,scope:{season:18,phase:'18.2'}}});
    if (url.pathname.startsWith('/api/runs/')) {
      requests.push(url.pathname);
      return route.fulfill({status:options.missing ? 404 : 200,json:options.missing ? {error:'这条对局不存在或已移除'} : {run:fixture()}});
    }
    if (['/art.svg','/shot.svg'].includes(url.pathname)) {
      if (options.brokenArt && url.pathname === '/art.svg') return route.fulfill({status:404,body:''});
      return route.fulfill({contentType:'image/svg+xml',body:art});
    }
    if (url.pathname.startsWith('/api/card-preview/')) return route.fulfill({json:{
      cardId:'card-2',name:'隐藏中文卡名2',type:'Item',size:'Large',quality:'Gold',qualityLabel:'黄金',enchantment:'Fiery',
      availableQualities:[{value:'Gold',label:'黄金'}],effects:[{source:'base',text:'造成20伤害'}],img:'/art.svg',
    }});
    if (url.pathname.startsWith('/api/')) return route.fulfill({json:{}});
    const file = url.pathname === '/runs' ? 'runs.html' : url.pathname.startsWith('/static/') ? url.pathname.slice(8) : null;
    if (!file) return route.fulfill({status:404,body:''});
    if (options.failComponent && file.includes('html2canvas-')) return route.abort();
    return route.fulfill({body:readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  return {page,errors,requests,context};
}

async function assertShareCentered(page) {
  const box = await page.locator('.run-share-dialog').evaluate(dialog => {
    const r = dialog.getBoundingClientRect();
    return {dx:Math.abs(r.x+r.width/2-innerWidth/2),dy:Math.abs(r.y+r.height/2-innerHeight/2),
      within:r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight};
  });
  assert.ok(box.dx<=1&&box.dy<=1&&box.within,JSON.stringify(box));
}

test('all items, actual quality frames, hidden names, matching skills and expanded screenshot',async t => {
  const {page,errors} = await setup(t,375);
  await page.goto('http://runs.test/runs?auto=1');
  await page.locator('.run-result').waitFor();
  assert.equal(await page.locator('.run-items .run-card-chip').count(),6);
  assert.equal(await page.locator('.run-skills-list .run-card-chip').count(),48);
  assert.doesNotMatch(await page.locator('.run-result').textContent(),/隐藏中文卡名|隐藏技能名/);
  assert.match(await page.locator('.run-result-head').textContent(),/Day 13/);
  assert.equal(await page.locator('.run-art.tier-gold:not(.is-skill)').count(),1);
  assert.equal(await page.locator('.run-art.tier-unknown').count(),1);
  assert.equal(await page.locator('.run-skills-list .run-card-chip').first().getAttribute('data-card-preview'),'skill-47');
  assert.equal(await page.locator('.run-screenshot img').isVisible(),true);
  assert.equal(await page.locator('.run-skills-toggle').isVisible(),true);
  const before = await page.locator('.run-skills-list').evaluate(el => el.clientHeight);
  await page.locator('.run-skills-toggle').click();
  assert.equal(await page.locator('.run-skills-toggle').getAttribute('aria-expanded'),'true');
  assert.ok(await page.locator('.run-skills-list').evaluate(el => el.clientHeight) > before);
  await page.locator('.run-skills-toggle').click();
  assert.equal(await page.locator('.run-skills-list').evaluate(el => el.clientHeight),before);
  await page.setViewportSize({width:1280,height:950});
  await page.waitForTimeout(80);
  await page.setViewportSize({width:375,height:950});
  await page.waitForTimeout(80);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
  await page.locator('[data-card-preview="card-2"]').click();
  await page.locator('.bcp-name').waitFor();
  assert.equal(await page.locator('.bcp-name').textContent(),'隐藏中文卡名2');
  assert.deepEqual(errors,[]);
});

test('direct run link bypasses current search and preserves original scope',async t => {
  const {page,errors,requests} = await setup(t);
  await page.goto('http://runs.test/runs?run=stable-run&hero=Dooley&days=1');
  await page.locator('.run-result').waitFor();
  assert.deepEqual(requests,['/api/runs/stable-run']);
  assert.match(await page.locator('#runs-result').textContent(),/分享的阵容|18\.2/);
  await page.getByRole('button',{name:'分享阵容',exact:true}).click();
  await assertShareCentered(page);
  assert.equal(await page.locator('.run-share-url').inputValue(),'http://runs.test/runs?run=stable-run');
  await page.getByRole('button',{name:'复制阵容链接',exact:true}).click();
  assert.match(await page.locator('.run-share-status').textContent(),/复制|选中/);
  await page.getByRole('button',{name:'关闭分享'}).click();
  await page.locator('.run-share-dialog').waitFor({state:'detached'});
  assert.equal(await page.locator('.run-share-dialog').count(),0);
  assert.deepEqual(errors,[]);
});

test('real PNG export includes all skills, QR, enchantment, and omits names and search highlights',async t => {
  const {page,errors} = await setup(t);
  await page.goto('http://runs.test/runs?run=stable-run');
  await page.locator('.run-result').waitFor();
  await page.getByRole('button',{name:'分享阵容',exact:true}).click();
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await page.waitForFunction(() => Boolean(document.querySelector('.run-share-sheet')));
  const sheet = page.locator('.run-share-sheet');
  // Inspect the independent export DOM while real image loads/rendering are in flight.
  assert.equal(await sheet.locator('.run-share-skill-grid .run-card-chip').count(),48);
  assert.equal(await sheet.locator('.is-filter-match').count(),0);
  assert.doesNotMatch(await sheet.textContent(),/隐藏中文卡名|隐藏技能名/);
  assert.match(await sheet.locator('.run-card-enchantment').textContent(),/炽焰/);
  assert.match(await sheet.locator('.run-share-qr img').getAttribute('src'),/^data:image\/png;base64,/);
  await page.locator('[data-share-download]:not([hidden])').waitFor({timeout:30000});
  assert.match(await page.locator('.run-share-status').textContent(),/已生成/);
  const image = page.locator('.run-share-preview img');
  await image.evaluate(img => img.decode());
  await assertShareCentered(page);
  assert.equal(await image.evaluate(img => img.naturalWidth),1600);
  assert.ok(await image.evaluate(img => img.naturalHeight) > 1000);
  // The PNG must contain the actual quality frame pixels.
  const framePixels = await image.evaluate(img => {
    const canvas=document.createElement('canvas'); canvas.width=img.naturalWidth; canvas.height=img.naturalHeight;
    const ctx=canvas.getContext('2d'); ctx.drawImage(img,0,0);
    const data=ctx.getImageData(0,0,canvas.width,canvas.height).data;
    return [[184,125,80],[195,203,214],[229,180,60],[104,214,236],[178,133,237]].map(rgb => {
      let n=0; for(let i=0;i<data.length;i+=4) if(rgb.every((v,k)=>data[i+k]===v)) n++;
      return n;
    });
  });
  assert.ok(framePixels.every(n => n > 500),JSON.stringify(framePixels));
  const download = page.waitForEvent('download');
  await page.locator('[data-share-download]').click();
  const file = await download;
  assert.equal(file.suggestedFilename(),'bazaarqiubot-stable-run.png');
  const out = path.resolve(__dirname,'../.tmp/runs-share-validation'); mkdirSync(out,{recursive:true});
  await file.saveAs(path.join(out,'share.png'));
  assert.equal(readFileSync(path.join(out,'share.png')).subarray(1,4).toString(),'PNG');
  await page.screenshot({path:path.join(out,'share-dialog.png'),fullPage:true});
  await page.getByRole('button',{name:'关闭分享'}).click();
  await page.locator('.run-share-dialog').waitFor({state:'detached'});
  await page.evaluate(() => scrollTo(0,0));
  await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
  await page.setViewportSize({width:375,height:950});
  await page.evaluate(() => scrollTo(0,0));
  await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
  assert.equal(await page.locator('.run-share-render-host').count(),0);
  assert.deepEqual(errors,[]);
});

test('missing images export placeholders and component failure can retry without losing link',async t => {
  const {page,errors} = await setup(t,375,{brokenArt:true});
  await page.goto('http://runs.test/runs?run=stable-run');
  await page.locator('.run-result').waitFor();
  await page.getByRole('button',{name:'分享阵容',exact:true}).click();
  await assertShareCentered(page);
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await page.locator('[data-share-download]:not([hidden])').waitFor({timeout:30000});
  await assertShareCentered(page);
  assert.match(await page.locator('.run-share-status').textContent(),/54 张卡图使用占位/);
  assert.equal(await page.locator('.run-share-url').inputValue(),'http://runs.test/runs?run=stable-run');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
  assert.deepEqual(errors,[]);
  const failed = await setup(t,375,{failComponent:true});
  await failed.page.goto('http://runs.test/runs?run=stable-run');
  await failed.page.locator('.run-result').waitFor();
  await failed.page.getByRole('button',{name:'分享阵容',exact:true}).click();
  await failed.page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await failed.page.getByRole('button',{name:'重新生成分享图',exact:true}).waitFor();
  assert.match(await failed.page.locator('.run-share-status').textContent(),/加载失败/);
  assert.equal(await failed.page.locator('.run-share-url').inputValue(),'http://runs.test/runs?run=stable-run');
  assert.deepEqual(failed.errors,[]);
});

test('removed and invalid links display a recovery path',async t => {
  const {page,errors,requests} = await setup(t,375,{missing:true});
  await page.goto('http://runs.test/runs?run=removed');
  await page.getByText('这条对局不存在或已移除',{exact:false}).waitFor();
  assert.equal(await page.getByRole('link',{name:'返回阵容查询',exact:true}).getAttribute('href'),'/runs');
  await page.goto('http://runs.test/runs?run=bad%27id');
  await page.getByText('无效的对局链接',{exact:false}).waitFor();
  assert.deepEqual(requests,['/api/runs/removed']);
  assert.deepEqual(errors,[]);
});
