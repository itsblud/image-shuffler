const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
const storage = {};
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    checked: true, style: {}, classList: {remove(){}, add(){}, contains(){return false;}},
    addEventListener(){}, setAttribute(){}, selectedOptions:[{dataset:{from:'1994',to:'2008'}}], value:'8'
  });
  return nodes.get(id);
}
const context = vm.createContext({document:{querySelector:node,documentElement:{dataset:{}},body:node('body')},
  window:{addEventListener(){}}, localStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v},
  setTimeout,clearTimeout,console,assert});
vm.runInContext(fs.readFileSync(__dirname+'/app.js','utf8').replace(/boot\(\);\s*$/, ''),context);
vm.runInContext(`
const sample={year:2005,tags:[],source:'BlackPlanet',region:'north-america'};
assert(itemAllowed(sample));
prefs.filters['social-media']=false;
for (const source of ['BlackPlanet','MiGente','51.com','QQ','Xiaonei','Cyworld','Orkut']) assert(!itemAllowed({...sample,source}));
for (const source of ['Rave.ca','FortuneCity','GeoCities','Tripod','Angelfire']) assert(itemAllowed({...sample,source}));
savePrefs(); assert.equal(loadPrefs().filters['social-media'],false);
console.log('Social-media filter and saved preference checks passed.');
`,context);
