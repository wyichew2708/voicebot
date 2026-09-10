/* Audio-thread capture and bounded PCM playback. No assumed render quantum. */
class VoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(320);
    this.n = 0; this.weight = 0; this.sum = 0; this.outstanding = 0;
    this.active = true;
    this.port.onmessage = e => {
      if (e.data.type === 'ack') this.outstanding = Math.max(0, this.outstanding - 1);
      if (e.data.type === 'stop') this.active = false;
    };
  }
  process(inputs) {
    if (!this.active) return false;
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    const ratio = sampleRate / 16000;
    for (let i = 0; i < input.length; i++) {
      let left = 1;
      while (left > 1e-9) {
        const take = Math.min(left, ratio - this.weight);
        this.sum += input[i] * take; this.weight += take; left -= take;
        if (this.weight >= ratio - 1e-9) {
          this.frame[this.n++] = this.sum / ratio;
          this.sum = 0; this.weight = 0;
          if (this.n === this.frame.length) {
            if (this.outstanding >= 8) {
              this.port.postMessage({type:'capture_error', reason:'Microphone processing fell behind'});
              this.active = false; return false;
            }
            this.outstanding++;
            this.port.postMessage({type:'capture', pcm:this.frame,
              context_time:currentTime + (i + 1) / sampleRate}, [this.frame.buffer]);
            this.frame = new Float32Array(320); this.n = 0;
          }
        }
      }
    }
    return true; // output remains silent; microphone is never monitored locally
  }
}

class VoicePlayback extends AudioWorkletProcessor {
  constructor() {
    super(); this.active = null;
    this.port.onmessage = e => this.message(e.data);
  }
  message(m) {
    if (m.type === 'stop') { this.active = null; return; }
    if (m.type === 'begin') {
      if (![16000, 24000, 48000].includes(m.rate)) return;
      this.active = {id:m.id, rate:m.rate, ring:new Int16Array(Math.ceil(m.rate * 2.1)),
        read:0, size:0, phase:0, consumed:0, reported:0, started:false, final:false,
        underrun:0};
      return;
    }
    const a = this.active;
    if (!a || a.id !== m.id) return;
    if (m.type === 'end') { a.final = true; return; }
    if (m.type !== 'pcm' || a.final) return;
    if (a.size + m.pcm.length > a.ring.length) {
      this.port.postMessage({type:'playback_error', id:a.id, reason:'Playback buffer overflow'});
      this.active = null; return;
    }
    for (let i = 0; i < m.pcm.length; i++) {
      a.ring[(a.read + a.size++) % a.ring.length] = m.pcm[i];
    }
  }
  process(inputs, outputs) {
    const out = outputs[0] && outputs[0][0], a = this.active;
    if (!out || !a) return true;
    if (!a.started && a.size < a.rate * .08 && !a.final) return true;
    for (let i = 0; i < out.length; i++) {
      if (a.size && (a.size > 1 || a.final)) {
        if (!a.started) {
          a.started = true;
          this.port.postMessage({type:'started', id:a.id, context_time:currentTime + i/sampleRate});
        }
        const next = a.size > 1 ? (a.read + 1) % a.ring.length : a.read;
        out[i] = (a.ring[a.read] * (1-a.phase) + a.ring[next] * a.phase) / 32768;
        a.phase += a.rate / sampleRate;
        const used = Math.min(a.size, Math.floor(a.phase + 1e-9));
        a.phase -= used; a.read = (a.read + used) % a.ring.length;
        a.size -= used; a.consumed += used;
      } else if (a.started && !a.final) a.underrun++;
      if (a.consumed - a.reported >= a.rate * .05 || (a.final && !a.size)) {
        this.port.postMessage({type:'consumed', id:a.id, samples:a.consumed});
        a.reported = a.consumed;
      }
      if (a.final && !a.size) {
        this.port.postMessage({type:'done', id:a.id, underrun_ms:a.underrun/sampleRate*1000});
        this.active = null; break;
      }
    }
    return true;
  }
}
registerProcessor('voice-capture', VoiceCapture);
registerProcessor('voice-playback', VoicePlayback);
