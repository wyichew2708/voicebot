/* Pinned Silero v5 package model: 512 samples at 16 kHz, recurrent [2,1,128]. */
importScripts('/vad-assets/ort.wasm.min.js');
let session, state, sr, busy=false;
self.onmessage=async event=>{
  const m=event.data;
  try {
    if(m.type==='init') {
      ort.env.wasm.numThreads=1;
      ort.env.wasm.wasmPaths=new URL('/vad-assets/',self.location.href).href;
      session=await ort.InferenceSession.create('/vad-assets/silero_vad_v5.onnx',
        {executionProviders:['wasm'],graphOptimizationLevel:'all'});
      state=new ort.Tensor('float32',new Float32Array(256),[2,1,128]);
      sr=new ort.Tensor('int64',BigInt64Array.from([16000n]),[]);
      self.postMessage({type:'ready'});
      return;
    }
    if(m.type!=='frame' || !session || busy || m.pcm.length!==512) throw Error('Invalid VAD sequence');
    busy=true;
    const input=new ort.Tensor('float32',m.pcm,[1,512]);
    const output=await session.run({input,state,sr});
    const probability=output.output.data[0];
    input.dispose(); state.dispose(); output.output.dispose();
    state=output.stateN;
    if(!Number.isFinite(probability) || probability<0 || probability>1) throw Error('Invalid VAD score');
    self.postMessage({type:'score',pcm:m.pcm,at:m.at,probability},[m.pcm.buffer]);
    busy=false;
  } catch(error) {
    self.postMessage({type:'error',message:String(error)});
  }
};
