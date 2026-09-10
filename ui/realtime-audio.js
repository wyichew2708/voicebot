/* Per-request fencing also rejects a response whose audio_begin had not yet
 * arrived when the caller interrupted. Binary v2: three little-endian uint32s
 * (generation, audio_id, sequence), followed by mono PCM16. */
(function (root) {
  "use strict";
  class VoiceAudioProtocol {
    constructor() { this.turn = 0; this.active = null; this.origin_ms = null; }
    request(origin = null) {
      this.active = null;
      this.origin_ms = Number.isFinite(origin) && origin >= 0 ? origin : null;
      return ++this.turn;
    }
    accepts(event) {
      return event.client_turn === undefined || event.client_turn === this.turn;
    }
    begin(event) {
      if (!this.accepts(event) || event.audio_protocol !== 2) return false;
      this.active = { generation: event.generation, audio_id: event.audio_id,
                      sequence: 0, final: false, turn: this.turn, started: false };
      return true;
    }
    frame(buffer) {
      const a = this.active;
      if (!a || a.final || buffer.byteLength < 12 || buffer.byteLength % 2) return null;
      const h = new DataView(buffer);
      if (h.getUint32(0, true) !== a.generation ||
          h.getUint32(4, true) !== a.audio_id ||
          h.getUint32(8, true) !== a.sequence) return null;
      a.sequence++;
      return new Int16Array(buffer, 12);
    }
    end(event) {
      const a = this.active;
      if (!a || !this.accepts(event) || event.generation !== a.generation ||
          event.audio_id !== a.audio_id) return false;
      a.final = true;
      return true;
    }
    started(at, method) {
      const a = this.active;
      const elapsed = at - this.origin_ms;
      if (!a || !a.sequence || a.started || this.origin_ms === null ||
          !Number.isFinite(at) || elapsed < 0 || elapsed > 600000) return null;
      a.started = true;
      return {type: "playback_started", generation: a.generation, audio_id: a.audio_id,
              first_audio_ms: elapsed, method: method};
    }
    complete() {
      const a = this.active;
      if (!a || !a.final || a.turn !== this.turn) return null;
      this.active = null;
      const result = { type: "playback_done", generation: a.generation, audio_id: a.audio_id };
      if (Number.isFinite(a.underrun_ms)) result.underrun_ms = a.underrun_ms;
      return result;
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = VoiceAudioProtocol;
  else root.VoiceAudioProtocol = VoiceAudioProtocol;
})(typeof globalThis !== "undefined" ? globalThis : window);
