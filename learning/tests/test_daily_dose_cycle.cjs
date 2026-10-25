const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor(){this.events={};this.style={};this.classList={add(){},remove(){},toggle(){}};this.value='';}
  addEventListener(name,fn){this.events[name]=fn;}
  focus(){}
  removeAttribute(){}
}
const ids={};
const get=id=>ids[id]??=new Element();
const words=Array.from({length:10},(_,i)=>({id:'word-'+i,english:'word '+i,tagalog:'answer '+i}));
const config={course:true,cycle_id:'cycle-1',next_word_id:'word-0',cycle_position:1,cycle_total:10,
  words,progress:{},level:1,dashboard:{levels:[]},urls:{answer:'/answer',dashboard:'/path',continue:'/path'}};
get('daily-dose-config').textContent=JSON.stringify(config);
const timers=[];
let destination, answers=0;
const context=vm.createContext({console,document:{getElementById:get},
  window:{addEventListener(){},location:{assign(url){destination=url;}}},
  setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},
  async fetch(url,options){
    const data=JSON.parse(options.body);
    assert.equal(data.cycle_id,'cycle-1');
    assert.equal(data.word_id,words[answers].id,'the client must use the server-owned word order');
    answers++;
    return {ok:true,async json(){return {ok:true,correct:answers!==1,stats:{mastered:false,recall_streak:1},
      cycle_id:'cycle-1',next_word_id:words[answers]?.id,cycle_position:answers+1,cycle_total:10,
      next_url:answers===10?'/learn/level-1-cafe/shooter':null};}};
  }});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/daily-dose.js'),'utf8'),context);
(async()=>{
  for(let i=0;i<10;i++){
    timers.shift()(); // Finish the preview and enable typed recall.
    get('answer-input').value='answer';
    await get('answer-form').events.submit({preventDefault(){}});
    if(i<9){assert.equal(destination,undefined);get('continue-button').events.click();}
  }
  assert.equal(destination,'/learn/level-1-cafe/shooter');
  assert.equal(ids['support-link'],undefined,'required practice must not be an optional link');
  console.log('Daily Dose: server-owned cycle order and automatic required shooter transition passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
