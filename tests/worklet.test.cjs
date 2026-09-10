const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function processor(name, rate=48000) {
  const classes = {}, messages = [];
  const context = {sampleRate:rate, currentTime:0, Float32Array, Int16Array,
    AudioWorkletProcessor:class {constructor(){this.port={postMessage:m=>messages.push(m)};}},
    registerProcessor:(n,c)=>classes[n]=c};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../ui/voice-worklet.js'),'utf8'), context);
  const node = new classes[name]();
  return {node, messages, context};
}
test('capture resamples 44.1/48 kHz into exact 20 ms frames across variable quanta', () => {
  for (const rate of [44100,48000]) {
    const {node,messages} = processor('voice-capture',rate);
    let n=0;
    while (n<rate) {
      const size=Math.min(n%2 ? 256:128,rate-n);
      node.process([[new Float32Array(size).fill(.25)]]);
      n+=size;
      while(node.outstanding) node.port.onmessage({data:{type:'ack'}});
    }
    assert.equal(messages.length,50);
    assert.ok(messages.every(m=>m.pcm.length===320 && Math.abs(m.pcm[0]-.25)<1e-5));
  }
});
test('capture stops explicitly when the main thread cannot keep up', () => {
  const {node,messages} = processor('voice-capture',16000);
  for(let i=0;i<20;i++) node.process([[new Float32Array(320)]]);
  assert.equal(messages.filter(m=>m.type==='capture').length,8);
  assert.equal(messages.filter(m=>m.type==='capture_error').length,1);
});
test('playback resamples and completes only after final audio is consumed', () => {
  const {node,messages,context} = processor('voice-playback');
  node.message({type:'begin',id:'1:1',rate:16000});
  node.message({type:'pcm',id:'1:1',pcm:new Int16Array(1600).fill(16384)});
  let nonzero=0;
  for(let i=0;i<25;i++) {
    const out=new Float32Array(256);
    node.process([],[[out]]); context.currentTime+=256/48000;
    nonzero+=out.filter(x=>x!==0).length;
  }
  assert.equal(messages.filter(m=>m.type==='done').length,0);
  node.message({type:'end',id:'1:1'});
  for(let i=0;i<2;i++) {
    const out=new Float32Array(256); node.process([],[[out]]);
    nonzero+=out.filter(x=>x!==0).length;
  }
  assert.equal(nonzero,4800);
  assert.equal(messages.filter(m=>m.type==='started').length,1);
  assert.equal(messages.filter(m=>m.type==='done').length,1);
  assert.equal(messages.filter(m=>m.type==='consumed').at(-1).samples,1600);
  assert.ok(messages.find(m=>m.type==='done').underrun_ms>0);
});
test('stop drops buffered audio and stale packets; overflow fails explicitly', () => {
  const {node,messages} = processor('voice-playback');
  node.message({type:'begin',id:'1',rate:16000});
  node.message({type:'pcm',id:'1',pcm:new Int16Array(1600).fill(100)});
  node.message({type:'stop'});
  node.message({type:'pcm',id:'1',pcm:new Int16Array(1600).fill(100)});
  const out=new Float32Array(128); node.process([],[[out]]);
  assert.ok(out.every(x=>x===0));
  node.message({type:'begin',id:'2',rate:16000});
  node.message({type:'pcm',id:'2',pcm:new Int16Array(40000)});
  assert.equal(messages.at(-1).type,'playback_error');
  assert.equal(node.active,null);
});
