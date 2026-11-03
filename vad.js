/* Voice-activity detection (VAD) for SuperChat hands-free mode.
 *
 * VadCore is pure logic (no audio APIs): feed it one loudness value (RMS, 0..1) per frame and it tells you when speech
 * STARTS and ENDS. The browser glue (microphone -> AnalyserNode -> feed) lives in index.html, so this file can be unit-tested
 * in Node (tests_vad.js).
 *
 *  - adaptive noise floor: learned from non-speech frames only, so a fan or street noise does not trigger it
 *  - hysteresis: a higher threshold to start speaking than to keep speaking
 *  - start needs `startMs` of voiced audio (clicks/coughs are ignored); end needs `endMs` of silence (short pauses between
 *    words do not cut you off); utterances with less than `minSpeechMs` of real voice are cancelled
 *  - suppressed mode (while the assistant is talking): thresholds x duckFactor, so only a clearly louder voice can barge in
 */
(function (root) {
  const DEFAULTS = {
    frameMs: 30, startMs: 150, endMs: 900, minSpeechMs: 300, maxSpeechMs: 25000,
    minStart: 0.02, minEnd: 0.012, startRatio: 3.0, endRatio: 1.8, noiseInit: 0.004, noiseMax: 0.05, duckFactor: 2.5,
  };

  class VadCore {
    constructor(opts) {
      this.o = Object.assign({}, DEFAULTS, opts || {});
      this.reset();
    }
    reset() {
      this.noise = this.o.noiseInit; this.state = 'idle';
      this.voiceMs = 0; this.silMs = 0; this.speechMs = 0; this.suppressed = false;
    }
    setSuppressed(b) { this.suppressed = !!b; }
    thresholds() {
      const k = this.suppressed ? this.o.duckFactor : 1;
      return { start: Math.max(this.o.minStart, this.noise * this.o.startRatio) * k,
               end: Math.max(this.o.minEnd, this.noise * this.o.endRatio) * (this.suppressed ? 1.5 : 1) };
    }
    /** feed one frame (dtMs = real time since the previous frame, optional); returns a list of events: {type:'start'} | {type:'end', ms} | {type:'cancel'} */
    feed(rms, dtMs) {
      const dt = dtMs ? Math.min(100, Math.max(10, dtMs)) : this.o.frameMs, th = this.thresholds(), out = [];
      if (this.state === 'idle') {
        if (rms >= th.start) {
          this.voiceMs += dt;
          if (this.voiceMs >= this.o.startMs) { this.state = 'speaking'; this.speechMs = this.voiceMs; this.silMs = 0; this.voiceMs = 0; out.push({ type: 'start' }); }
        } else {
          this.voiceMs = Math.max(0, this.voiceMs - dt);                                  // tolerate a dip inside a word
          if (!this.suppressed) {                                                          // never learn our own TTS leaking into the mic
            const a = rms < this.noise ? 0.1 : 0.02;                                       // fall fast, rise slowly
            this.noise = Math.min(this.o.noiseMax, this.noise * (1 - a) + rms * a);
          }
        }
        return out;
      }
      this.speechMs += dt;
      if (rms >= th.end) this.silMs = 0; else this.silMs += dt;
      const tooLong = this.speechMs >= this.o.maxSpeechMs;
      if (this.silMs >= this.o.endMs || tooLong) {
        const voiced = this.speechMs - this.silMs;
        this.state = 'idle'; this.silMs = 0; this.voiceMs = 0;
        out.push(voiced >= this.o.minSpeechMs ? { type: 'end', ms: voiced, forced: tooLong && this.silMs === 0 } : { type: 'cancel' });
      }
      return out;
    }
  }

  /** RMS loudness of a Float32Array time-domain buffer */
  function rms(buf) { let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i]; return Math.sqrt(s / (buf.length || 1)); }

  const api = { VadCore, rms, DEFAULTS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.Vad = api;
})(typeof window !== 'undefined' ? window : globalThis);
