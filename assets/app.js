const $=id=>document.getElementById(id);
let statusBusy=false, commandBusy=false, commandPending=false;
for(let i=0;i<4;i++) $('head-controls').insertAdjacentHTML('beforeend',`<label>head ${i+1}<input id="head${i}" type="range" min="-1" max="1" value="0" step="0.02"></label>`);
$('head-controls').insertAdjacentHTML('beforeend','<label>Mouth 34 (°)<input id="mouth" type="range" min="0" max="30" value="0" step="1"></label>');
async function post(path, body){const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(x.detail||'request failed');return x;}
function showError(e){$('message').textContent=e.message;}
function showBattery(b){
  const valid=b?.valid===true&&Number.isFinite(b.percent)&&Number.isFinite(b.voltage_v);
  const pct=valid?Math.max(0,Math.min(100,Math.round(b.percent))):null;
  $('battery-percent').textContent=valid?pct+'%':'Unknown';
  $('battery-voltage').textContent=valid?b.voltage_v.toFixed(2)+' V · servo rail':'No valid servo voltage';
  $('battery-meter').style.width=valid?pct+'%':'0%';
  $('battery-card').dataset.level=!valid?'unknown':pct<25?'low':pct<60?'medium':'high';
  if(valid)$('battery-progress').setAttribute('aria-valuenow',String(pct));
  else $('battery-progress').removeAttribute('aria-valuenow');
}

async function setMode(mode){try{await post('mode',{mode});$('message').textContent='Mode updated';}catch(e){showError(e);}}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
$('pick').onclick=async()=>{try{const r=await post('pick',{});$('message').textContent=`Pick started · ${r.model}`;}catch(e){showError(e);}};
$('roulade').onclick=async()=>{try{const r=await post('roulade',{});$('message').textContent=`Roulade · ${r.model}`;}catch(e){showError(e);}};
$('stop').onclick=()=>{zero();setMode('off')};
const keys=new Set(), sticks={left:{x:0,y:0},right:{x:0,y:0}}, resetSticks=[];
let motion=[0,0,0], hostGamepadOwnsControl=false;
let gamepadInput=null;
const deadzone=v=>Math.abs(v)<.1?0:v;
function updateMotion(){
  const key=(a,b)=>keys.has(a)-keys.has(b);
  motion=[keys.has('w')||keys.has('s')?key('w','s')*.2:deadzone(sticks.left.y)*.4,
    keys.has('a')||keys.has('d')?key('a','d')*.15:deadzone(-sticks.left.x)*.3,
    keys.has('q')||keys.has('e')?key('q','e')*.5:deadzone(-sticks.right.x)];
  if(gamepadInput&&!hostGamepadOwnsControl)motion=[...gamepadInput.twist];
  ['vx','vy','wz'].forEach((id,i)=>$(id+'Value').textContent=motion[i].toFixed(2));
}
async function sendCommand(){
  if(hostGamepadOwnsControl)return;
  if(commandBusy){commandPending=true;return;}
  commandBusy=true;commandPending=false;
  try{await post('command',{twist:[...motion],head:gamepadInput?[...gamepadInput.head]:[0,1,2,3].map(i=>+$('head'+i).value),mouth:gamepadInput?gamepadInput.mouth:+$('mouth').value});}
  catch(e){showError(e);}
  finally{commandBusy=false;if(commandPending)sendCommand();}
}
function joystick(id,side){
  const zone=$(id),knob=zone.firstElementChild;
  let pointer=null;
  function reset(){
    const old=pointer;pointer=null;
    sticks[side]={x:0,y:0};knob.style.transform='translate(0px,0px)';
    if(old!==null&&zone.hasPointerCapture(old))zone.releasePointerCapture(old);
  }
  resetSticks.push(reset);
  function move(e){
    const b=zone.getBoundingClientRect(),radius=(zone.clientWidth-knob.offsetWidth)/2;
    let dx=e.clientX-b.left-b.width/2,dy=e.clientY-b.top-b.height/2;
    const distance=Math.hypot(dx,dy);
    if(distance>radius){dx*=radius/distance;dy*=radius/distance;}
    knob.style.transform=`translate(${dx}px,${dy}px)`;
    sticks[side]={x:dx/radius,y:-dy/radius};updateMotion();
  }
  zone.onpointerdown=e=>{if(pointer!==null||e.button!==0)return;e.preventDefault();pointer=e.pointerId;zone.setPointerCapture(pointer);move(e);sendCommand();};
  zone.onpointermove=e=>{if(e.pointerId===pointer)move(e);};
  const release=e=>{if(e.pointerId!==pointer)return;reset();updateMotion();sendCommand();};
  zone.onpointerup=zone.onpointercancel=zone.onlostpointercapture=release;
}
joystick('joy-left','left');joystick('joy-right','right');
function zero(){keys.clear();resetSticks.forEach(reset=>reset());gamepadInput=null;updateMotion();sendCommand();}
$('zero').onclick=zero;
['leg-alpha','head-alpha'].forEach(id=>$(id).onchange=()=>post('filter',{leg:+$('leg-alpha').value,head:+$('head-alpha').value}).catch(showError));
function keyCommand(){updateMotion();sendCommand();}
window.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();$('stop').click();return;}const k=e.key.toLowerCase();if('wasdqe'.includes(k)&&k.length===1){keys.add(k);keyCommand();}});
window.addEventListener('keyup',e=>{const k=e.key.toLowerCase();if(keys.delete(k))keyCommand();});
window.addEventListener('blur',()=>{disableGamepad(true);keys.clear();zero();if(!hostGamepadOwnsControl)post('mode',{mode:'off'}).catch(()=>{});});
document.addEventListener('visibilitychange',()=>{if(document.hidden){disableGamepad(true);keys.clear();zero();if(!hostGamepadOwnsControl)post('mode',{mode:'off'}).catch(()=>{});}});
window.addEventListener('pagehide',()=>{if(!hostGamepadOwnsControl)navigator.sendBeacon('/api/mode',new Blob([JSON.stringify({mode:'off'})],{type:'application/json'}));});
setInterval(()=>{if(!document.hidden&&!commandBusy&&!hostGamepadOwnsControl)sendCommand();},50);
setInterval(async()=>{if(statusBusy||document.hidden)return;statusBusy=true;try{const r=await fetch('/api/status');if(!r.ok)throw new Error('status read failed');const s=await r.json();showBattery(s.battery);hostGamepadOwnsControl=s.input_owner==='gamepad';if(hostGamepadOwnsControl&&gamepadIndex!==null)disableGamepad(false);$('gamepad-enable').disabled=hostGamepadOwnsControl||gamepadIndex!==null;const modeLabel={shadow:'inference only',off:'torque off',hold:'default pose',policy:'walk',calibrate:'calibrate',servo_debug:'servo setup'};$('rx').textContent=s.feedback_hz.toFixed(1)+' Hz';$('rl').textContent=s.inference_hz.toFixed(1)+' Hz';$('servos').textContent=(s.servo_ids?.length||0)+'/15';$('imu').textContent=s.imu_ok?'online':'offline';$('mode').textContent=(modeLabel[s.mode]||s.mode)+(s.enabled?' · enabled':' · torque off')+(hostGamepadOwnsControl?' · Xbox on Uno Q':'');$('message').textContent=s.last_error||`Bridge ${(s.bridge_baud/1e6).toFixed(0)} Mbps · state age ${(s.feedback_age_ms||0).toFixed(1)} ms`;
document.querySelectorAll('[data-mode="hold"],[data-mode="policy"]').forEach(b=>b.disabled=!s.control_ready||hostGamepadOwnsControl);
const recovery=s.recovery, pick=s.pick, roulade=s.roulade;
const upright=(s.mode==='shadow'||s.mode==='policy')&&recovery&&recovery.active&&recovery.phase==='walk';
$('pick').disabled=hostGamepadOwnsControl||!upright||(pick&&pick.active)||(roulade&&roulade.active);
$('roulade').disabled=$('pick').disabled;
if(recovery){const phaseLabel=roulade&&roulade.active?'roulade':pick&&pick.active?'pick':({walk:'walk',default_pose:'get-up · action = 0',getup:'get-up'}[recovery.phase]||recovery.phase);
$('recovery-phase').textContent=recovery.active?phaseLabel:'idle';$('active-model').textContent=s.active_model||'—';$('tilt-angle').textContent=recovery.angle_deg==null?'—':recovery.angle_deg.toFixed(1)+'°';
$('recovery-timer').textContent=!recovery.active?'—':roulade&&roulade.active?`roulade ${roulade.remaining_s.toFixed(2)} / ${roulade.seconds.toFixed(2)} s`:pick&&pick.active?`pick φ ${pick.phi.toFixed(2)} / 1.00`:recovery.phase==='default_pose'?`hold ${recovery.hold_remaining_s.toFixed(2)} s left`:recovery.phase==='getup'?`upright ${recovery.stand_elapsed_s.toFixed(2)} / 1.00 s`:`fallen ${recovery.fall_elapsed_s.toFixed(2)} / 0.15 s`;
if(pick&&pick.active)$('mouth').value=Math.round(s.mouth||0);}
for(const [id,values]of [['acc-values',s.acc],['gyro-values',s.gyro],['gravity-values',s.gravity]])$(id).textContent=values?values.map(x=>x.toFixed(3)).join(' / '):'—';
$('command-health').textContent=`sent ${s.sent} · MCU echo ${s.mcu_command_seq??'—'} · command age ${s.mcu_command_age_us<1e9?(s.mcu_command_age_us/1000).toFixed(1)+' ms':'—'} · gaps ${s.sequence_gaps} · rejected ${s.mcu_invalid_commands??0}`;
if(s.angles)$('joints').innerHTML=s.motors.map((id,i)=>`<tr class="${s.servo_ids.includes(id)?'':'missing'}"><td>${id}</td><td>${s.angles[i].toFixed(1)}</td><td>${s.targets[i].toFixed(1)}</td></tr>`).join('');
}catch(e){showBattery(null);showError(e);}finally{statusBusy=false;}},250);

/* Xbox controller is deliberately opt-in: a newly connected gamepad never arms motors. */
let gamepadIndex=null, gamepadButtons={}, gamepadLastPoll=0;
let gamepadDisarming=false;
const padStatus=$('gamepad-status');
const usePad=$('gamepad-enable');
const releasePad=$('gamepad-disable');
function connectedPad(){
  if(!navigator.getGamepads)return null;
  const pads=navigator.getGamepads();
  if(gamepadIndex!==null)return pads[gamepadIndex]||null;
  return Array.from(pads).find(p=>p?.connected&&p.mapping==='standard')||null;
}
function disableGamepad(disarm=false){
  const wasActive=gamepadIndex!==null;
  gamepadIndex=null;gamepadButtons={};gamepadInput=null;updateMotion();
  usePad.disabled=hostGamepadOwnsControl;releasePad.disabled=true;
  padStatus.textContent=wasActive?'Control released · torque off':'Connect a standard Xbox gamepad, then enable';
  if(wasActive&&disarm&&!hostGamepadOwnsControl&&!gamepadDisarming){
    gamepadDisarming=true;
    post('mode',{mode:'off'}).catch(showError).finally(()=>{gamepadDisarming=false;});
  }
}
usePad.onclick=()=>{
  if(hostGamepadOwnsControl){padStatus.textContent='Uno Q Xbox owns control; release it first.';return;}
  const pad=connectedPad();
  if(!pad||!window.XboxGamepad?.decode(pad)){
    padStatus.textContent='No standard-mapped gamepad detected. Press a button and retry.';
    return;
  }
  const data=window.XboxGamepad.decode(pad);
  if(!data.neutral){padStatus.textContent='Release both sticks to neutral before enabling.';return;}
  gamepadIndex=pad.index;
  gamepadButtons={};
  // Latch existing buttons to prevent a held button from invoking an action.
  for(const key of ['stop','hold','walk','pick','roulade'])gamepadButtons[key]=data[key];
  gamepadLastPoll=performance.now();
  usePad.disabled=true;releasePad.disabled=false;
  padStatus.textContent=pad.id+' · enabled (B: torque off)';
};
releasePad.onclick=()=>disableGamepad(true);
window.addEventListener('gamepaddisconnected',e=>{
  if(e.gamepad.index===gamepadIndex)disableGamepad(true);
});
function pollGamepad(){
  if(gamepadIndex===null)return;
  if(hostGamepadOwnsControl){disableGamepad(false);return;}
  if(document.hidden){disableGamepad(true);return;}
  const now=performance.now();
  const pad=connectedPad();
  const data=window.XboxGamepad?.decode(pad);
  if(!data||now-gamepadLastPoll>500){disableGamepad(true);return;}
  gamepadLastPoll=now;
  gamepadInput=data;
  updateMotion();
  // Stop has precedence. The button is edge-triggered to avoid repeated HTTP requests.
  if(data.stop&&!gamepadButtons.stop){disableGamepad(true);return;}
  for(const [name,action] of [
    ['hold',()=>setMode('hold')],
    ['walk',()=>setMode('policy')],
    ['pick',()=>$('pick').click()],
    ['roulade',()=>$('roulade').click()]]) {
    if(data[name]&&!gamepadButtons[name])action();
  }
  for(const key of ['stop','hold','walk','pick','roulade'])gamepadButtons[key]=data[key];
}
setInterval(pollGamepad,20);
