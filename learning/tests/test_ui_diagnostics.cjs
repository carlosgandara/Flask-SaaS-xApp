const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const events={},elements={},reports=[];
const document={
  currentScript:{dataset:{reportUrl:'/diagnostics/browser-error'}},
  body:{append(panel){elements[panel.id]=panel;}},
  getElementById(id){return elements[id];},
  createElement(){return {style:{},setAttribute(){}};}
};
const window={
  addEventListener(name,callback){events[name]=callback;},
  async fetch(url,options){
    if(url==='/diagnostics/browser-error'){
      reports.push(JSON.parse(options.body));
      return {ok:true,async json(){return {request_id:'browser-reference'};}};
    }
    if(url==='/offline')throw new Error('offline');
    return {status:503,headers:{get(){return 'server-reference';}}};
  }
};
const context=vm.createContext({window,document,URL,location:{origin:'http://localhost',href:'http://localhost/menu'}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/ui-diagnostics.js'),'utf8'),context);
(async()=>{
  await window.fetch('/broken');
  assert.match(elements['diagnostic-banner'].textContent,/server-reference/);
  await assert.rejects(window.fetch('/offline'));
  assert.match(elements['diagnostic-banner'].textContent,/connection/);
  events.error({filename:'http://localhost/static/word-match.js?secret=private',lineno:42,colno:8,message:'sensitive content'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(reports[0].kind,'script_error');
  assert.equal(reports[0].file,'/static/word-match.js');
  assert.equal(reports[0].line,42);
  assert.doesNotMatch(JSON.stringify(reports),/secret|private|sensitive/);
  assert.match(elements['diagnostic-banner'].textContent,/browser-reference/);
  events.unhandledrejection({reason:'private'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(reports[1].kind,'unhandled_rejection');
  for(let i=0;i<10;i++)events.error({filename:'http://localhost/static/broken.js'});
  assert.equal(reports.length,5);
  console.log('Browser diagnostics: server references, network failures, runtime reporting, sensitive-field exclusion and report limit passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
