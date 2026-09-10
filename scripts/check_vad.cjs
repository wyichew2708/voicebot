// Real CPU/WASM compatibility smoke check, not a speech-accuracy benchmark.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../models/vad');
(async()=>{
 const ort=require(root+'/ort.wasm.min.js'); ort.env.wasm.numThreads=1; ort.env.wasm.wasmPaths=root+'/';
 const session=await ort.InferenceSession.create(fs.readFileSync(root+'/silero_vad_v5.onnx'),{executionProviders:['wasm']});
 let state=new ort.Tensor('float32',new Float32Array(256),[2,1,128]);
 const sr=new ort.Tensor('int64',BigInt64Array.from([16000n]),[]);
 const input=new ort.Tensor('float32',new Float32Array(512),[1,512]);
 for(let i=0;i<32;i++){
  const out=await session.run({input,state,sr}); const p=out.output.data[0];
  assert.ok(Number.isFinite(p)&&p>=0&&p<=1); assert.deepEqual(out.stateN.dims,[2,1,128]);
  state.dispose();state=out.stateN;out.output.dispose();
 }
 input.dispose();sr.dispose();state.dispose();await session.release();
 console.log('PASS: 32 real Silero v5 frames, recurrent state, single-thread CPU WASM.');
})().catch(e=>{console.error(e);process.exitCode=1;});
