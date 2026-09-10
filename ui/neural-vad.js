(function(root){
  class NeuralVoiceDetector {
    constructor(worker, deliver, failed) {
      this.worker=worker; this.deliver=deliver; this.failed=failed;
      this.pending=[]; this.partial=new Float32Array(512); this.n=0;
      this.busy=false; this.closed=false;
      worker.onmessage=e=>{
        if(this.closed) return;
        if(e.data.type==='score') {
          clearTimeout(this.timer); this.busy=false;
          this.deliver(e.data.pcm,e.data.at,e.data.probability); this.dispatch();
        } else if(e.data.type==='error') this.fail();
      };
      worker.onerror=()=>this.fail();
    }
    push(pcm, at) {
      if(this.closed) return;
      for(let i=0;i<pcm.length;i++) {
        this.partial[this.n++]=pcm[i];
        if(this.n===512) {
          if(this.pending.length>=4) { this.fail(); return; }
          this.pending.push({pcm:this.partial,at:at-(pcm.length-i-1)/16});
          this.partial=new Float32Array(512); this.n=0; this.dispatch();
        }
      }
    }
    dispatch() {
      if(this.closed || this.busy || !this.pending.length) return;
      const frame=this.pending.shift(); this.busy=true;
      this.timer=setTimeout(()=>this.fail(),1000);
      this.worker.postMessage({type:'frame',...frame},[frame.pcm.buffer]);
    }
    fail() { if(!this.closed) { this.close(); this.failed(); } }
    close() { this.closed=true; clearTimeout(this.timer); this.pending=[]; this.worker.terminate(); }
    static async create(deliver,failed) {
      let worker;
      try {
        const response=await fetch('/vad-assets/manifest.json',{signal:AbortSignal.timeout(2000)});
        if(!response.ok) return null;
        worker=new Worker('/audio-assets/vad-worker.js');
        await new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>reject(Error('VAD startup timeout')),10000);
          worker.onmessage=e=>{clearTimeout(timer); e.data.type==='ready' ? resolve() : reject(Error('VAD startup failed'));};
          worker.onerror=()=>{clearTimeout(timer);reject(Error('VAD startup failed'));};
          worker.postMessage({type:'init'});
        });
        return new NeuralVoiceDetector(worker,deliver,failed);
      } catch(error) { if(worker) worker.terminate(); return null; }
    }
  }
  if(typeof module!=='undefined' && module.exports) module.exports=NeuralVoiceDetector;
  else root.NeuralVoiceDetector=NeuralVoiceDetector;
})(typeof globalThis!=='undefined' ? globalThis : window);
