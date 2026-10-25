const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() {this.events={};this.style={};this.children=[];this.classList={add(){},remove(){}};}
  addEventListener(name,callback) {(this.events[name]??=[]).push(callback);}
  emit(name,event={}) {(this.events[name]||[]).forEach(callback=>callback({preventDefault(){},pointerId:1,...event}));}
  setPointerCapture() {}
  getBoundingClientRect() {return {left:0,top:300,width:40,height:40};}
  appendChild(child) {this.children.push(child);}
}
const ids={};
const document=new Element();
document.getElementById=id=>ids[id]??=(new Element());
document.createElement=()=>new Element();
document.getElementById('wordShooterData').textContent='[]';
document.getElementById('wordShooterConfig').textContent='{}';
const window=new Element();
let destination;
window.location={assign(url){destination=url;}};
const timers=[];
const context=vm.createContext({document,window,console,setTimeout(fn,ms){timers.push({fn,ms});},requestAnimationFrame(){}});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/word-shooter.js'),'utf8'),context);
vm.runInContext('gameRunning=true;canShoot=true;',context);
const state=()=>vm.runInContext('({left:keys.left,right:keys.right,bullets:bullets.length})',context);
ids.moveLeft.emit('pointerdown');
assert.equal(state().left,true);
ids.shootButton.emit('pointerdown',{pointerId:2});
assert.equal(state().bullets,1);
assert.equal(state().left,true,'shooting must not release a simultaneous movement pointer');
ids.moveLeft.emit('pointerup');
assert.equal(state().left,false);
ids.moveRight.emit('pointerdown');
assert.equal(state().right,true);
ids.moveRight.emit('pointercancel');
assert.equal(state().right,false);
ids.moveLeft.emit('pointerdown');
ids.moveLeft.emit('lostpointercapture');
assert.equal(state().left,false);
ids.moveRight.emit('pointerdown');
window.emit('blur');
assert.equal(state().right,false);
ids.moveLeft.emit('pointerdown');
document.hidden=true;
document.emit('visibilitychange');
assert.equal(state().left,false);
vm.runInContext('CONFIG.returnUrl="/learn/level-1-cafe/daily-dose"; finishLevel();',context);
assert.equal(ids.practiceTransitionText.textContent,'Nice practice! Let’s recall those words again.');
timers.find(timer=>timer.ms===1100).fn();
assert.equal(destination,'/learn/level-1-cafe/daily-dose','a completed support round must automatically return to Daily Dose');
console.log('Shooter pointer controls: movement, firing, simultaneous touch, cancellation and focus loss passed.');

(async()=>{
  context.fetch=async()=>({ok:true,async json(){return {ok:true,stats:{mastered:false},level_complete:false};}});
  vm.runInContext('createExplosion=()=>{}; showMessage=()=>{}; currentWord={id:"coffee",answer:"kape"}; gameRunning=true; levelComplete=false; lives=3;',context);
  for(let i=0;i<3;i++){
    await vm.runInContext('roundLocked=false; handleHit({dataset:{word:"wrong"},classList:{add(){}}});',context);
  }
  assert.equal(vm.runInContext('lives',context),3,'required support must not run out of arcade lives');
  assert.equal(vm.runInContext('gameRunning',context),true);
  assert.equal(timers.some(timer=>timer.fn.name==='endGame'),false,'three wrong shots must not schedule Play Again');
  // A fresh support page starts with the transition, before gameplay runs.
  document.getElementById('wordShooterData').textContent='[{"id":"coffee","answer":"kape","english":"coffee"}]';
  document.getElementById('wordShooterConfig').textContent='{"returnUrl":"/daily-dose"}';
  const introTimers=[];
  const introContext=vm.createContext({document,window,console,setTimeout(fn,ms){introTimers.push({fn,ms});},requestAnimationFrame(){}});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/word-shooter.js'),'utf8'),introContext);
  assert.equal(vm.runInContext('gameRunning',introContext),false);
  assert.equal(ids.practiceTransitionText.textContent,'Let’s practice what you missed.');
  vm.runInContext('var starts=0; startRound=()=>starts++; gameLoop=()=>{};',introContext);
  introTimers.find(timer=>timer.ms===1600).fn();
  assert.equal(vm.runInContext('starts',introContext),1);
  assert.equal(vm.runInContext('gameRunning',introContext),true);
  console.log('Required shooter: intro transition, no game-over after wrong shots, and automatic Daily Dose return passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
