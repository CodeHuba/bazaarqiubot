const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../web_runs/static/library.js'),'utf8');
const helpers=source.slice(source.indexOf('  function draftSignature('),source.indexOf('  function renderDrafts('));
const handlers=source.split('\n').filter(line=>line.includes("$('save-draft').onclick=")||line.includes("$('new-build').onclick=")).join('\n');
const original={schema:1,name:'测试构筑',hero:'Jules',items:[
  {uid:'one',id:'pie',start:0,quality:'Gold',enchantment:'Icy'},
  {uid:'two',id:'potato',start:2,quality:'Bronze',enchantment:''}],
  skills:[{uid:'skill-one',id:'s1',quality:'Silver'},{uid:'skill-two',id:'s2',quality:'Gold'}]};
function fixture(){
  let saved=[],writes=0,commits=0,fail=false,closed=0;
  const controls={'save-draft':{},'new-build':{},'draft-dialog':{close(){closed++;}}};
  const context={build:structuredClone(original),DRAFT_KEY:'drafts',clone:structuredClone,
    storageGet:()=>structuredClone(saved),storageSet:(key,value)=>{if(fail)return false;saved=structuredClone(value);writes++;return true;},
    track(){},notify(){},renderDrafts(){},commit(change){commits++;change();},$:id=>controls[id]};
  vm.runInNewContext(helpers+handlers,context);
  return {context,save:()=>controls['save-draft'].onclick(),newBuild:()=>controls['new-build'].onclick(),
    get saved(){return saved;},get writes(){return writes;},get commits(){return commits;},get closed(){return closed;},set fail(value){fail=value;}};
}
const f=fixture();f.save();f.save();assert.equal(f.saved.length,1,'repeat save must not duplicate');
f.context.build.items.reverse();f.context.build.items.forEach(e=>e.uid+='-restored');
f.context.build.skills.forEach(e=>e.uid+='-restored');
f.context.build.version='new label';f.context.build.fingerprint='new fingerprint';
f.newBuild();assert.equal(f.saved.length,1,'new after saving must not insert a second copy');
assert.equal(f.context.build.items.length,0);assert.equal(f.context.build.skills.length,0);
assert.equal(f.writes,1,'no redundant storage write');
f.newBuild();assert.equal(f.saved.length,1,'new from an empty build does not auto-save');
for(const change of [b=>b.name+=' changed',b=>b.items[0].quality='Diamond',b=>b.items[0].enchantment='Turbo',b=>b.items[1].start=4,b=>b.skills.reverse()]){
  const changed=fixture();changed.save();changed.context.build=structuredClone(original);change(changed.context.build);
  changed.newBuild();assert.equal(changed.saved.length,2,'unsaved changes must be protected');assert.equal(changed.context.build.items.length,0);
}
const failure=fixture();failure.fail=true;failure.newBuild();
assert.deepEqual(failure.context.build,original,'failed save must preserve the current build');
assert.equal(failure.commits,0);assert.equal(failure.closed,0);
console.log('Verified save → new, repeated save, restored UIDs, item ordering, changed content, empty new and storage failure.');
