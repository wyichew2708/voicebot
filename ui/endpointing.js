/* Audio-duration based endpoint policy; clocks and neural inference are external. */
(function(root) {
  class VoiceEndpointing {
    constructor(mode='balanced') { this.mode=mode; this.reset(); }
    reset(armed=0) {
      this.voiced=Math.max(0,armed); this.silence=0; this.total=0;
      this.hesitated=false; this.uncertain=false; this.uncertainMs=0; this.snr=0;
    }
    observe(voice, ms, snr, probability=null) {
      if (!(Number.isFinite(ms) && ms>0 && ms<=100)) return;
      this.total+=ms;
      if (voice) {
        if(this.silence>=180) this.hesitated=true;
        this.voiced+=ms; this.silence=0;
        this.snr=this.snr ? this.snr*.9+snr*.1 : snr;
      } else this.silence+=ms;
      if (probability!==null && probability>.25 && probability<.6) this.uncertainMs+=ms;
      if(this.uncertainMs>=160) this.uncertain=true;
    }
    target() {
      if(this.mode==='patient') return this.hesitated ? 1400 : 1000;
      if(this.hesitated) return 1100;
      if(this.voiced<300) return 700; // do not rush short yes/no or isolated digits
      if(this.uncertain || this.snr<3) return 850;
      if(this.voiced>8000) return 700;
      return 450;
    }
    decision() {
      // Bound capture below the server's 30-second ceiling, including pre-roll.
      if(this.total>=25000) return 'limit';
      return this.silence>=this.target() ? 'silence' : null;
    }
  }
  if(typeof module!=='undefined' && module.exports) module.exports=VoiceEndpointing;
  else root.VoiceEndpointing=VoiceEndpointing;
})(typeof globalThis!=='undefined' ? globalThis : window);
