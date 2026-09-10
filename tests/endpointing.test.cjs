const test=require('node:test'), assert=require('node:assert/strict');
const Endpoint=require('../ui/endpointing.js'), Detector=require('../ui/neural-vad.js');
const fs=require('node:fs'), vm=require('node:vm');
function feed(e,voice,ms,snr=5,p=null) { for(let t=0;t<ms;t+=20) e.observe(voice,20,snr,p); }
test('clean speech, short replies and long pauses use distinct endpoint waits',()=>{
  const e=new Endpoint(); feed(e,true,200); assert.equal(e.target(),700);
  feed(e,true,200); assert.equal(e.target(),450);
  feed(e,false,440); assert.equal(e.decision(),null); feed(e,false,20); assert.equal(e.decision(),'silence');
  e.reset(); feed(e,true,400); feed(e,false,200); feed(e,true,100); assert.equal(e.target(),1100);
  e.mode='patient'; assert.equal(e.target(),1400); e.reset(); assert.equal(e.target(),1000);
});
test('low signal and sustained uncertainty gain pause tolerance',()=>{
  const e=new Endpoint(); feed(e,true,400,2); assert.equal(e.target(),850);
  e.reset(); feed(e,true,400,5,.5); assert.equal(e.target(),850);
  e.reset(); feed(e,true,400,5,.9); feed(e,false,20,5,.4); assert.equal(e.target(),450);
});
test('audio duration bounds capture without trusting wall clocks',()=>{
  const e=new Endpoint(); e.observe(true,Infinity,5); e.observe(true,5000,5); assert.equal(e.total,0);
  feed(e,true,25000); assert.equal(e.decision(),'limit');
  const n=new Endpoint(); for(let i=0;i<16;i++)n.observe(true,32,5);
  for(let i=0;i<14;i++)n.observe(false,32,5); assert.equal(n.decision(),null);
  n.observe(false,32,5); assert.equal(n.decision(),'silence');
});
function worker() {return {sent:[],postMessage(m){this.sent.push(m);},terminate(){this.terminated=true;}};}
test('neural frames retain sample order and capture timestamps across input boundaries',()=>{
  const w=worker(), out=[]; const d=new Detector(w,(...a)=>out.push(a),()=>assert.fail('fallback'));
  d.push(Float32Array.from({length:320},(_,i)=>i),20);
  d.push(Float32Array.from({length:320},(_,i)=>i+320),40);
  const m=w.sent[0]; assert.equal(m.pcm.length,512); assert.equal(m.pcm[511],511); assert.equal(m.at,32);
  w.onmessage({data:{type:'score',...m, type:'score', probability:.9}});
  assert.equal(out.length,1); assert.equal(out[0][1],32); d.close();
});
test('slow inference has a bounded queue and fails once, ignoring late results',()=>{
  const w=worker(); let failed=0,delivered=0; const d=new Detector(w,()=>delivered++,()=>failed++);
  for(let i=0;i<6;i++) d.push(new Float32Array(512),i*32);
  assert.equal(failed,1); assert.equal(w.sent.length,1); assert.equal(w.terminated,true);
  assert.equal(d.pending.length,0); w.onmessage({data:{type:'score'}}); d.fail(); assert.equal(delivered,0); assert.equal(failed,1);
});
const html=fs.readFileSync(require('node:path').join(__dirname,'../ui/demo-console.html'),'utf8');
function micHarness(){
 const sent=[],buffers=[]; let clock=0;
 const c={SR:16000,PREROLL:44,RUNUP_MS:320,MIN_VOICED_MS:80,BARGE_MARGIN:7,BARGE_MS:260,
  NOISE_MARGIN:2.6,RELEASE_MARGIN:1.7,FLOOR_MIN:.004,FLOOR_MAX:.06,floor:.006,calibN:0,
  talking:false,speaker:null,responsePending:false,micOn:true,armedMs:0,voicedMs:0,lastVoiceMono:null,preroll:[],
  endpointer:new Endpoint(),Float32Array,Int16Array,performance:{now:()=>clock},
  $:()=>({value:'balanced'}),setOrb(){},setWorking(){},send:m=>sent.push(m),sendBin:b=>{buffers.push(b);return true;}};
 vm.createContext(c); vm.runInContext(html.slice(html.indexOf('  function downsample('),html.indexOf('  async function micStart(')),c);
 return {c,sent,buffers,feed(amp,frames,p){for(let i=0;i<frames;i++){clock+=20;c.onFrame(new Float32Array(320).fill(amp),16000,clock,p);}}};
}
test('console preserves short neural replies and counts onset frames exactly once',()=>{
 const h=micHarness();h.feed(.001,5,.9);assert.equal(h.c.voicedMs,100);
 h.feed(0,34,.01);assert.equal(h.sent.length,0);h.feed(0,1,.01);
 assert.equal(h.sent[0].type,'utterance_end'); assert.equal(h.sent[0].endpoint_target_ms,700);
 assert.equal(h.sent[0].speech_end_ms,100); assert.equal(h.sent[0].speech_detector,'silero-v5');
});
test('console rejects a short transient and energy fallback still ends clean speech',()=>{
 const h=micHarness();h.feed(.05,2);h.feed(0,40);assert.equal(h.sent[0].type,'discard_utterance');
 const e=micHarness();e.feed(.05,25);e.feed(0,23);assert.equal(e.sent[0].endpoint_target_ms,450);
 assert.equal(e.sent[0].speech_detector,'energy');
});
test('neural barge-in still requires loud sustained speech and sends pre-roll',()=>{
 const h=micHarness();h.c.speaker='a';h.feed(.001,30,.9);assert.equal(h.sent.length,0);
 h.feed(.08,12,.9);assert.equal(h.sent.length,0);h.feed(.08,1,.9);
 assert.equal(h.sent[0].type,'barge_in');assert.equal(h.c.voicedMs,260);
 assert.ok(h.buffers.length>=29); // run-up plus dwell, including current frame
});
test('microphone shutdown terminates detector and discards endpoint state',()=>{
 const h=micHarness(); let closed=0;
 Object.assign(h.c,{micEpoch:1,vadDetector:{close(){closed++;}},micNode:null,micSrc:null,micStream:null,running:true});
 vm.runInContext(html.slice(html.indexOf('  function micStop()'),html.indexOf('  // ---------------------------------------------------------------- orb')),h.c);
 h.feed(.08,10,.9);h.c.micStop();
 assert.equal(closed,1);assert.equal(h.c.vadDetector,null);assert.equal(h.c.talking,false);
 assert.equal(h.c.endpointer.total,0);assert.equal(h.c.lastVoiceMono,null);assert.equal(h.c.micOn,false);
});
test('missing model assets select fallback without creating a worker',async()=>{
 const saved=global.fetch;global.fetch=async()=>({ok:false});
 try{assert.equal(await Detector.create(()=>assert.fail(),()=>assert.fail()),null);}finally{global.fetch=saved;}
});
test('cancel during neural startup closes the stream and late worker; repeated start does not leak',async()=>{
 const h=micHarness();let acquire=0,stopped=0,closed=0,ready;
 Object.assign(h.c,{micOn:false,micStarting:false,micEpoch:0,live:true,running:true,
   micNode:null,micSrc:null,micStream:null,vadDetector:null,addSys(){},
   navigator:{mediaDevices:{async getUserMedia(){acquire++;return {getTracks:()=>[{stop(){stopped++;}}]};}}},
   ensureCtx:()=>({async resume(){}}),ensureWorklet:async()=>true,
   NeuralVoiceDetector:{create:()=>new Promise(resolve=>{ready=resolve;})}});
 vm.runInContext(html.slice(html.indexOf('  async function micStart()'),html.indexOf('  // ---------------------------------------------------------------- orb')),h.c);
 const pending=h.c.micStart();await h.c.micStart();
 while(!ready)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(acquire,1);assert.equal(h.c.micStarting,true);
 h.c.micStop();ready({close(){closed++;}});await pending;
 assert.equal(stopped,1);assert.equal(closed,1);assert.equal(h.c.micOn,false);assert.equal(h.c.micStarting,false);
});
