const assert = require('node:assert/strict');
const {planPlacement} = require('../web_runs/static/library-layout.js');
const item = (uid,start,width) => ({uid,start,width});
function result(items,uid,width,target) {
  const original=structuredClone(items), plan=planPlacement(items,uid,width,target);
  assert.deepEqual(items,original,'preview must not mutate the saved build');
  if(!plan)return null;
  const others=items.filter(e=>e.uid!==uid).sort((a,b)=>a.start-b.start);
  const placed=plan.positions.map(p=>({...p,width:others.find(e=>e.uid===p.uid).width}));
  assert.equal(placed.length,others.length);
  assert.deepEqual(placed.map(e=>e.uid),others.map(e=>e.uid),'keep other items in order');
  placed.push(item('incoming',plan.start,width));
  const cells=new Set();
  for(const e of placed){
    assert.ok(Number.isInteger(e.start)&&e.start>=0&&e.start+e.width<=10);
    for(let x=e.start;x<e.start+e.width;x++){assert.ok(!cells.has(x),'no overlap');cells.add(x);}
  }
  return plan;
}
let plan=result([item('a',0,2),item('b',2,3)],'',1,0);
assert.equal(plan.start,0);assert.deepEqual(plan.positions,[{uid:'a',start:1},{uid:'b',start:3}]);
plan=result([item('a',6,2),item('b',8,2)],'',3,7);
assert.equal(plan.start,7);assert.deepEqual(plan.positions,[{uid:'a',start:3},{uid:'b',start:5}]);
plan=result([item('a',0,1),item('b',1,2),item('c',3,3)],'a',1,1);
assert.equal(plan.start,1);assert.equal(plan.positions.find(e=>e.uid==='b').start,2);
assert.equal(result([item('a',0,3),item('b',3,3),item('c',6,3)],'',2,2),null);
plan=result([item('a',0,3),item('b',3,2),item('c',5,3),item('d',8,2)],'a',3,4);
assert.equal(plan.start,5,'snap to nearest feasible boundary when full');
assert.equal(result([], '',3,99).start,7);
assert.equal(result([], '',3,-20).start,0);
let checked=0;
// Enumerate every valid ten-slot board (gaps and all three sizes).
function walk(items,cursor){
  for(let width=1;width<=3;width++)for(let target=0;target<10;target++){
    const plan=result(items,'',width,target);
    assert.equal(!!plan,items.reduce((n,e)=>n+e.width,0)+width<=10);
    checked++;
  }
  for(const entry of items)for(const target of [0,4,9]){
    assert.ok(result(items,entry.uid,entry.width,target));checked++;
  }
  for(let start=cursor;start<10;start++)for(let width=1;width<=3&&start+width<=10;width++){
    walk([...items,item(String(items.length),start,width)],start+width);
  }
}
walk([],0);
console.log(`Verified ${checked} placements: bounds, capacity, order, no overlap, immutable previews.`);
