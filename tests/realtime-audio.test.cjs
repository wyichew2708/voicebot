const { test } = require('node:test');
const assert = require('node:assert/strict');
const Protocol = require('../ui/realtime-audio.js');
function begin(turn, gen=4, id=7) {
  return {client_turn: turn, generation:gen, audio_id:id, audio_protocol:2};
}
function frame(gen=4,id=7,seq=0) {
  const buf = new ArrayBuffer(16), view = new DataView(buf);
  [gen,id,seq].forEach((v,i) => view.setUint32(i*4,v,true));
  view.setInt16(12, 123, true); view.setInt16(14,-123,true);
  return buf;
}
test('interrupt before audio_begin rejects an unseen stale generation', () => {
  const p = new Protocol();
  p.request(); p.request();
  assert.equal(p.begin(begin(1)), false);
  assert.equal(p.frame(frame()), null);
  assert.equal(p.accepts({client_turn:1,kind:'audio_cancel'}), false);
  assert.equal(p.begin(begin(2,6)), true);
  assert.deepEqual(Array.from(p.frame(frame(6))), [123,-123]);
});
test('interrupt flushes old frames, final markers and acknowledgements', () => {
  const p = new Protocol(); p.request(); p.begin(begin(1));
  p.frame(frame()); p.request();
  assert.equal(p.frame(frame(4,7,1)), null);
  assert.equal(p.end(begin(1)), false);
  assert.equal(p.complete(), null);
});
test('duplicate and wrong-generation packets cannot enter playback', () => {
  const p = new Protocol(); p.request(); p.begin(begin(1));
  assert.equal(p.frame(frame(3)), null);
  assert.equal(p.frame(frame(4,8)), null);
  assert.equal(p.frame(frame(4,7,1)), null);
  assert.ok(p.frame(frame()));
  assert.equal(p.frame(frame()), null);
  assert.ok(p.frame(frame(4,7,1)));
});
test('playback acknowledgement is final-only, once, and uses current audio id', () => {
  const p = new Protocol(); p.request(); p.begin(begin(1));
  assert.equal(p.complete(), null);
  assert.equal(p.end(begin(1,4,8)), false);
  assert.ok(p.end(begin(1)));
  assert.equal(p.frame(frame()), null);
  assert.deepEqual(p.complete(), {type:'playback_done',generation:4,audio_id:7});
  assert.equal(p.complete(), null);
});
test('malformed packets are ignored', () => {
  const p = new Protocol(); p.request(); p.begin(begin(1));
  for (const n of [0,4,11,13]) assert.equal(p.frame(new ArrayBuffer(n)), null);
});

// Execute the console's actual socket handler with a small audio/DOM harness.
// This checks the wiring as well as the standalone packet fence above.
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../ui/demo-console.html'), 'utf8');
function consoleHarness() {
  const socketCode = html.slice(html.indexOf('  var audioProtocol ='),
                               html.indexOf('  // ------------------------------------------------------------- controls'));
  const sent = [], played = [], nodes = [];
  const context = {
    VoiceAudioProtocol:Protocol, location:{protocol:'http:',host:'localhost'},
    globalThis:null, live:false, running:false, ws:null, incoming:null, streamOn:false,
    streamSrcs:nodes, pendN:0, speaker:null, performance:{now:()=>10},
    setTimeout:()=>1, clearTimeout:()=>{}, setConn:()=>{}, refreshHealth:()=>{},
    setWorking:()=>{}, setSpeaker:()=>{}, stop:()=>{},
    $:()=>({classList:{add(){},remove(){}}}), apply:e=>played.push(e),
    stopPlayback:()=> { nodes.length=0; context.incoming=null; context.streamOn=false;
                        context.audioProtocol.active=null; },
    beginStream:()=>true, feedStream:()=>{}, endStream:()=>{},
    loadForReplay:()=>{}, play:()=>{throw new Error('unexpected media fallback');},
    WebSocket:class { constructor(){ this.readyState=1; }
                     send(s){sent.push(JSON.parse(s));} },
    DataView,Int16Array,ArrayBuffer,JSON,Date
  };
  vm.createContext(context); vm.runInContext(socketCode, context);
  context.connect(); context.ws.onopen();
  const event=e=>context.ws.onmessage({data:JSON.stringify(e)});
  return {context,sent,event};
}
test('console stops locally before sending barge-in and ignores late audio_begin', () => {
  const {context:c,sent,event} = consoleHarness();
  c.send({type:'start'});
  event({...begin(1),kind:'audio_begin',sample_rate:16000});
  c.streamSrcs.push({playing:true});
  c.send({type:'barge_in'});
  assert.equal(c.streamSrcs.length,0);
  assert.equal(c.incoming,null);
  assert.equal(sent.at(-1).client_turn,2);
  event({...begin(1),kind:'audio_begin',sample_rate:16000});
  c.ws.onmessage({data:frame()});
  assert.equal(c.incoming,null);
});
test('console acknowledges final audio even if all chunks finished before audio_end', () => {
  const {context:c,sent,event} = consoleHarness();
  c.send({type:'start'});
  event({...begin(1),kind:'audio_begin',sample_rate:16000});
  c.ws.onmessage({data:frame()});
  // An output gap let the browser finish every scheduled source already.
  assert.equal(c.streamSrcs.length,0);
  event({...begin(1),kind:'audio_end'});
  assert.equal(sent.at(-1).type,'playback_done');
  assert.equal(sent.at(-1).audio_id,7);
});
test('console does not acknowledge audio still queued for playback', () => {
  const {context:c,sent,event} = consoleHarness();
  c.send({type:'start'});
  event({...begin(1),kind:'audio_begin',sample_rate:16000});
  c.ws.onmessage({data:frame()});
  c.streamSrcs.push({playing:true});
  event({...begin(1),kind:'audio_end'});
  assert.equal(sent.at(-1).type,'start');
  c.streamSrcs.length=0; c.playbackDone();
  assert.equal(sent.at(-1).type,'playback_done');
});

test('client latency keeps the request clock across acknowledgement and answer audio', () => {
  const p = new Protocol(); p.request(1000); p.begin(begin(1)); p.frame(frame());
  assert.equal(p.started(1350,'media_playing_event').first_audio_ms,350);
  assert.equal(p.started(1351,'media_playing_event'),null);
  p.end(begin(1)); p.complete();
  p.begin(begin(1,4,8)); p.frame(frame(4,8));
  assert.equal(p.started(2400,'webaudio_schedule_estimate').first_audio_ms,1400);
});
test('missing or stale playback timing does not become zero latency', () => {
  const p = new Protocol(); p.request(); p.begin(begin(1)); p.frame(frame());
  assert.equal(p.started(100,'media_playing_event'),null);
  p.request(100); p.begin(begin(2)); p.frame(frame());
  assert.equal(p.started(99,'media_playing_event'),null);
  assert.equal(p.started(NaN,'media_playing_event'),null);
  p.request(200);
  assert.equal(p.started(250,'media_playing_event'),null);
});

test('console suppresses a playback estimate cancelled during the audio lead', () => {
  const {context:c,sent,event} = consoleHarness();
  c.send({type:'start'});
  event({...begin(1),kind:'audio_begin',sample_rate:16000});
  c.ws.onmessage({data:frame()});
  let callback;
  c.setTimeout = fn => { callback = fn; };
  c.schedulePlaybackMeasurement({currentTime:0,state:'running'}, 0.1);
  c.send({type:'barge_in'});
  callback();
  assert.equal(sent.some(e=>e.type==='playback_started'),false);
});

test('console falls back to scheduling when device timestamp lookup throws', () => {
  const {context:c,sent,event} = consoleHarness();
  c.send({type:'start'});
  event({...begin(1),kind:'audio_begin',sample_rate:16000});
  c.ws.onmessage({data:frame()});
  c.setTimeout = fn => fn();
  c.schedulePlaybackMeasurement({currentTime:0,state:'running',
    getOutputTimestamp(){throw new Error('unavailable');}}, 0.1);
  assert.equal(sent.at(-1).type,'playback_started');
  assert.equal(sent.at(-1).method,'webaudio_schedule_estimate');
  assert.equal(sent.at(-1).first_audio_ms,100);
});

test('console worklet wiring waits for consumption and done, not audio_end', async () => {
  const {context:c,sent,event} = consoleHarness();
  const posted=[];
  c.AudioWorkletNode=class {
    constructor(ctx){this.context=ctx;this.port={postMessage:m=>posted.push(m)};}
    connect(){}
  };
  const playbackCode=html.slice(html.indexOf('  var actx = null, playHead'),
                               html.indexOf('  // The visible player is still handed'));
  vm.runInContext(playbackCode,c);
  c.actx={state:'running',audioWorklet:{addModule:async()=>{}},destination:{}};
  assert.equal(await c.ensureWorklet(c.actx),true);
  c.send({type:'start'});
  assert.equal(sent.at(-1).audio_flow,true);
  event({...begin(1),kind:'audio_begin',sample_rate:16000,flow_control:true});
  c.ws.onmessage({data:frame()});
  assert.equal(posted.at(-1).type,'pcm');
  event({...begin(1),kind:'audio_end'});
  assert.equal(posted.at(-1).type,'end');
  assert.equal(sent.some(m=>m.type==='playback_done'),false);
  c.workletPlayback.port.onmessage({data:{type:'consumed',id:'4:7',samples:2}});
  assert.equal(sent.at(-1).type,'audio_consumed');
  c.workletPlayback.port.onmessage({data:{type:'done',id:'4:7',underrun_ms:12}});
  assert.equal(sent.at(-1).type,'playback_done');
  assert.equal(sent.at(-1).underrun_ms,12);
});
