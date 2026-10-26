const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../web_runs/static/library.js'),'utf8');
const functions=source.slice(source.indexOf('  function pumpCatalogImages()'),source.indexOf('  function renderGrid('));
function fixture(withObserver=true){
  let active=0,peak=0,sequence=0,current=[];
  const started=[],observers=[];
  const builder={offsetHeight:240};
  class FakeImage {
    constructor(){this.id=sequence++;this.dataset={src:'image-'+this.id};this.isConnected=true;this.handlers={};this.parentElement={classList:{remove(){}}};}
    set src(value){this.source=value;this.loading=true;active++;peak=Math.max(peak,active);started.push(this);}
    addEventListener(type,handler){this.handlers[type]=handler;}
    removeEventListener(type,handler){if(this.handlers[type]===handler)delete this.handlers[type];}
    removeAttribute(name){assert.equal(name,'src');if(this.loading){this.loading=false;active--;}}
    finish(type='load'){assert.ok(this.loading);this.loading=false;active--;this.handlers[type]?.();}
  }
  class Observer {
    constructor(callback,options){this.callback=callback;this.options=options;this.observed=new Set();observers.push(this);}
    observe(image){this.observed.add(image);}
    unobserve(image){this.observed.delete(image);}
    disconnect(){this.observed.clear();}
    trigger(images,isIntersecting=true){this.callback(images.map(target=>({target,isIntersecting})));}
  }
  const context={window:withObserver?{IntersectionObserver:Observer}:{},IntersectionObserver:Observer,
    $:id=>id==='builder'?builder:{querySelectorAll:()=>current.filter(image=>image.dataset.src)}};
  vm.runInNewContext('const IMAGE_CONCURRENCY=4;let catalogImageObserver=null,catalogImageMargin="";const imageQueue=new Set(),imageJobs=new Map();'+functions+';this.observe=observeCatalogImages;',context);
  return {context,builder,started,observers,images(n){return Array.from({length:n},()=>new FakeImage());},setCurrent(images){current=images;},get active(){return active;},get peak(){return peak;}};
}
const f=fixture(),first=f.images(12);f.setCurrent(first);f.context.observe();
assert.equal(f.started.length,0,'offscreen images must not request a source');
assert.equal(f.observers[0].options.rootMargin,'100px 0px -140px 0px','exclude the fixed builder');
f.observers[0].trigger(first.slice(0,8));assert.equal(f.active,4);
f.observers[0].trigger([first[4]],false);
first[0].finish();assert.equal(f.started.at(-1),first[5],'skip images that leave the viewport');
first[1].finish('error');assert.equal(f.started.at(-1),first[6],'failed images free a request slot');
const before=f.started.length,more=f.images(12);f.setCurrent([...first,...more]);f.context.observe();
assert.equal(f.started.length,before,'appending cards does not restart existing image requests');
assert.equal(f.active,4);
first.forEach(image=>image.isConnected=false);more.forEach(image=>image.isConnected=false);
const searched=f.images(3);f.setCurrent(searched);f.context.observe();
assert.equal(f.active,0,'a new search cancels stale image jobs');
f.observers.at(-1).trigger(searched);assert.equal(f.active,3);
assert.ok(f.peak<=4,'never exceed four concurrent grid requests');
f.builder.offsetHeight=50;f.context.observe();
assert.equal(f.observers.at(-1).options.rootMargin,'100px 0px 50px 0px','adapt after collapsing the builder');
const fallback=fixture(false);fallback.setCurrent(fallback.images(12));fallback.context.observe();
assert.equal(fallback.active,4,'fallback still limits concurrency');
console.log('Verified deferred sources, 4-request limit, scroll-away, errors, append, search cancellation, builder resize and observer fallback.');
