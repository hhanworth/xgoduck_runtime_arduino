// Run: node --test tests/gamepad.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window:{}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../assets/gamepad.js'),'utf8'), context);
const {decode,dead} = context.window.XboxGamepad;
function pad(axes=[0,0,0,0], pressed=[]) {
  return {connected:true,mapping:'standard',axes,
    buttons:Array.from({length:17}, (_,i)=>({pressed:pressed.includes(i),value:pressed.includes(i)?1:0}))};
}
test('deadzone and saturation',()=>{
  assert.equal(dead(0.1),0);
  assert.equal(dead(1),1);
  assert.equal(dead(-1),-1);
  assert.equal(dead(Number.NaN),0);
});
test('stick directions and limits',()=>{
  const r=decode(pad([1,-1,-1,0]));
  assert.deepEqual(Array.from(r.twist),[0.4,-0.3,1]);
});
test('LB switches head inputs and cancels walking',()=>{
  const r=decode(pad([1,-1,0.5,-0.5],[4]));
  assert.deepEqual(Array.from(r.twist),[0,0,0]);
  assert.equal(r.head[0],1);
  assert.equal(r.head[1],1);
  assert.ok(r.head[2]>0);
  assert.ok(r.head[3]>0);
});
test('button mapping',()=>{
  const r=decode(pad([0,0,0,0],[0,1,2,3,9,7]));
  assert.equal(r.hold,true);
  assert.equal(r.stop,true);
  assert.equal(r.pick,true);
  assert.equal(r.roulade,true);
  assert.equal(r.walk,true);
  assert.equal(r.mouth,30);
});
test('rejects non-standard, missing and disconnected pads',()=>{
  assert.equal(decode(null),null);
  const nonstandard=pad();nonstandard.mapping='';
  assert.equal(decode(nonstandard),null);
  const disconnected=pad();disconnected.connected=false;
  assert.equal(decode(disconnected),null);
});
