// Unit tests for the VAD logic. Run: node tests_vad.js   (no browser, no microphone: synthetic loudness values)
const assert = require('assert');
const { VadCore } = require('./static/vad.js');
const FR = 30;                                                     // ms per frame
let seed = 1; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const noise = (lvl) => lvl * (0.7 + 0.6 * rnd());
function run(vad, segs) {                                          // segs: [[ms, level], ...] -> events with timestamps
  const ev = []; let t = 0;
  for (const [ms, lvl] of segs) for (let i = 0; i < ms / FR; i++) { for (const e of vad.feed(noise(lvl))) ev.push({ ...e, t }); t += FR; }
  return ev;
}
const types = (ev) => ev.map(e => e.type);
let n = 0; const test = (name, fn) => { fn(); n++; console.log('ok -', name); };

test('silence produces nothing and learns a low noise floor', () => {
  const v = new VadCore(); assert.deepStrictEqual(run(v, [[3000, 0.004]]), []); assert.ok(v.noise < 0.01);
});
test('one utterance -> exactly one start and one end, end ~endMs after speech stops', () => {
  const v = new VadCore(); const ev = run(v, [[600, 0.004], [1500, 0.1], [1500, 0.004]]);
  assert.deepStrictEqual(types(ev), ['start', 'end']);
  assert.ok(ev[0].t >= 600 && ev[0].t <= 600 + 250, 'start soon after speech begins: ' + ev[0].t);
  const gap = ev[1].t - (600 + 1500); assert.ok(gap >= 800 && gap <= 1100, 'end ~0.9 s after speech stops: ' + gap);
});
test('a short pause between words does not end the utterance', () => {
  const v = new VadCore(); const ev = run(v, [[500, 0.004], [800, 0.1], [500, 0.004], [800, 0.1], [1500, 0.004]]);
  assert.deepStrictEqual(types(ev), ['start', 'end']);
});
test('a pause longer than endMs splits into two utterances', () => {
  const v = new VadCore(); const ev = run(v, [[500, 0.004], [800, 0.1], [1500, 0.004], [800, 0.1], [1500, 0.004]]);
  assert.deepStrictEqual(types(ev), ['start', 'end', 'start', 'end']);
});
test('a click / cough shorter than startMs is ignored', () => {
  const v = new VadCore(); assert.deepStrictEqual(run(v, [[500, 0.004], [60, 0.3], [2000, 0.004]]), []);
});
test('speech that is voiced for less than minSpeechMs is cancelled, not sent', () => {
  const v = new VadCore(); const ev = run(v, [[500, 0.004], [180, 0.1], [1500, 0.004]]); assert.deepStrictEqual(types(ev), ['start', 'cancel']);
});
test('steady loud background noise (fan) is learned and does not trigger', () => {
  const v = new VadCore(); const ev = run(v, [[200, 0.004], [8000, 0.012]]);
  assert.deepStrictEqual(ev, [], 'no false triggers'); assert.ok(v.noise > 0.008, 'noise floor adapted: ' + v.noise);
});
test('speech over steady background noise is still detected', () => {
  const v = new VadCore(); const ev = run(v, [[6000, 0.012], [1200, 0.12], [1500, 0.012]]); assert.deepStrictEqual(types(ev), ['start', 'end']);
});
test('quiet speaker (just above threshold) is detected', () => {
  const v = new VadCore(); const ev = run(v, [[500, 0.004], [1000, 0.04], [1500, 0.004]]); assert.deepStrictEqual(types(ev), ['start', 'end']);
});
test('suppressed mode (assistant talking): echo-level audio is ignored, a loud voice barges in', () => {
  const v = new VadCore(); run(v, [[500, 0.004]]); v.setSuppressed(true);
  assert.deepStrictEqual(run(v, [[2000, 0.035]]), [], 'speaker leak at 0.035 must not trigger');
  const ev = run(v, [[1000, 0.16]]); assert.deepStrictEqual(types(ev), ['start']);
});
test('very long monologue is force-ended at maxSpeechMs', () => {
  const v = new VadCore({ maxSpeechMs: 5000 }); const ev = run(v, [[300, 0.004], [8000, 0.1]]);
  assert.strictEqual(types(ev)[0], 'start'); assert.strictEqual(types(ev)[1], 'end'); assert.ok(ev[1].t < 300 + 5500);
});
test('reset() returns to idle', () => {
  const v = new VadCore(); run(v, [[300, 0.004], [500, 0.1]]); assert.strictEqual(v.state, 'speaking'); v.reset(); assert.strictEqual(v.state, 'idle');
});
console.log(`\n${n} VAD tests passed`);
