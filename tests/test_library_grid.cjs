const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../web_runs/static/library.js'),'utf8');
const render=source.slice(source.indexOf('  function renderGrid('),source.indexOf('  function refresh('));
let matches=[],sequence=0,frames=new Map(),batchSizes=[];
const grid={children:[],attributes:{},
  set innerHTML(value){this.children=[];this.empty=value;},
  setAttribute(key,value){this.attributes[key]=value;},
  insertAdjacentHTML(position,html){
    assert.equal(position,'beforeend');
    const ids=html.split(',').filter(Boolean);batchSizes.push(ids.length);
    this.children.push(...ids.map(id=>({id,querySelector(){return {};}})));
  }};
const elements={'card-grid':grid,'result-count':{},'result-label':{}};
const context={$:id=>elements[id],filter:{type:'Item'},filteredCards:()=>matches,
  cardHTML:card=>card.id+',',observeCatalogImages(){},
  requestAnimationFrame(callback){const id=++sequence;frames.set(id,callback);return id;},
  cancelAnimationFrame(id){frames.delete(id);}};
vm.runInNewContext('const RENDER_BATCH_SIZE=48;let catalogRenderFrame=0,catalogRenderSerial=0,renderedCards=[];'+render,context);
function cards(n,prefix='card'){return Array.from({length:n},(_,i)=>({id:prefix+i}));}
function flush(){let count=0;while(frames.size){assert.ok(++count<100);const [id,callback]=frames.entries().next().value;frames.delete(id);callback();}}
matches=cards(1760);context.renderGrid();
assert.equal(grid.children.length,48,'first frame paints a bounded batch immediately');
assert.equal(grid.attributes['aria-busy'],'true');
flush();
assert.deepEqual(grid.children.map(card=>card.id),matches.map(card=>card.id),'all results appear in order without clicking');
assert.ok(batchSizes.every(size=>size<=48),'rendering yields between bounded batches');
assert.equal(grid.attributes['aria-busy'],'false');
matches=cards(300,'old');context.renderGrid();
const stale=frames.values().next().value;
matches=cards(2,'search');context.renderGrid();stale();flush();
assert.deepEqual(grid.children.map(card=>card.id),['search0','search1'],'new filters cancel old scheduled work');
matches=[];context.renderGrid();flush();
assert.equal(grid.children.length,0);assert.match(grid.empty,/还没有找到/);
assert.equal(grid.attributes['aria-busy'],'false');
console.log('Verified all 1760 results, bounded frames, ordering, no duplicates, stale filter cancellation and empty results.');
