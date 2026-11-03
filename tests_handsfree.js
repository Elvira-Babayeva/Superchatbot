// End-to-end test of the hands-free glue in static/index.html with fake mic / SpeechRecognition / speechSynthesis / clock.
// It checks the state machine (VAD <-> recogniser <-> TTS <-> /api/chat), NOT real audio hardware. Run: node tests_handsfree.js
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(__dirname + '/static/index.html', 'utf8');
const script = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];

let clock = 0, amp = 0.004, timers = [], nextId = 1; const sent = [], srs = [], spoken = []; let cancels = 0, stopped = 0;
const el = () => { const e = { style: {}, value: '', textContent: '', innerHTML: '', disabled: false, title: '', children: [], attrs: {},
  classes: new Set(), appendChild(c) { this.children.push(c); return c; }, remove() {}, addEventListener() {}, setAttribute(k, v) { this.attrs[k] = v; },
  classList: null }; e.classList = { add: c => e.classes.add(c), remove: c => e.classes.delete(c), toggle: (c, on) => on ? e.classes.add(c) : e.classes.delete(c) }; return e; };
const els = {};  const document = { getElementById: id => els[id] || (els[id] = el()), createElement: () => el(), addEventListener() {} };
class FakeSR { constructor() { this.started = false; srs.push(this); } start() { this.started = true; } stop() { this.started = false; timers.push({ at: clock, fn: () => this.onend && this.onend() }); }
  abort() { this.aborted = true; this.started = false; timers.push({ at: clock, fn: () => this.onend && this.onend() }); }
  hear(text) { this.onresult({ results: [[{ transcript: text }]] }); } }
class Utter { constructor(t) { this.text = t; } }
const sb = { document, navigator: { language: 'en-US', mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() { stopped++; } }] }) } },
  isSecureContext: true, SpeechRecognition: FakeSR, Vad: require('./static/vad.js'), performance: { now: () => clock },
  AudioContext: class { constructor() { this.state = 'running'; } createMediaStreamSource() { return { connect() {} }; }
    createAnalyser() { return { fftSize: 1024, getFloatTimeDomainData: b => b.fill(amp) }; } close() {} resume() {} },
  speechSynthesis: { cancel() { cancels++; }, getVoices: () => [], speak: u => spoken.push(u) }, SpeechSynthesisUtterance: Utter,
  setInterval: (fn, ms) => { const id = nextId++; timers.push({ id, every: ms, at: clock + ms, fn }); return id; }, clearInterval: id => { timers = timers.filter(t => t.id !== id); },
  setTimeout: (fn, ms) => { timers.push({ at: clock + ms, fn }); }, URL: {}, Image: class {}, Math, console,
  fetch: async (url, o) => { sent.push(JSON.parse(o.body).message); return { ok: true, json: async () => sb.nextReply }; }, nextReply: { reply: 'ok', domains: [], lang: 'en', choices: [] } };
document.getElementById('vl').value = 'auto'; sb.window = sb; vm.createContext(sb); vm.runInContext(script, sb);
const get = (code) => vm.runInContext(code, sb);
function step(ms, a) { if (a !== undefined) amp = a; for (let i = 0; i < ms / 30; i++) { clock += 30;
  for (const t of timers.slice()) if (t.at <= clock) { if (t.every) t.at += t.every; else timers.splice(timers.indexOf(t), 1); t.fn(); } } }
const flush = () => new Promise(r => setImmediate(r));
const live = () => srs.filter(s => s.started && !s.aborted);
let n = 0; const ok = (m) => { n++; console.log('ok -', m); };

(async () => {
  els.hf.onclick(); await flush(); await flush();
  assert.strictEqual(get('hf.on'), true); assert.strictEqual(live().length, 1); assert.strictEqual(live()[0].lang, 'en-US'); ok('hands-free starts: mic open, recogniser listening in en-US');

  step(600, 0.004); assert.strictEqual(get('hf.heard'), false); ok('silence: nothing happens');
  step(450, 0.1); assert.strictEqual(get('hf.heard'), true); assert.ok(els.hf.classes.has('hearing')); live()[0].hear('show me flights to Istanbul'); ok('speech START detected automatically');
  step(600, 0.1); assert.deepStrictEqual(sent, []); ok('still talking: nothing sent yet');
  step(1100, 0.004); await flush(); assert.deepStrictEqual(sent, ['show me flights to Istanbul']); assert.strictEqual(get('hf.heard'), false); ok('speech END detected -> transcript finalised and sent to /api/chat');
  step(300, 0.004); assert.strictEqual(live().length, 1); ok('recogniser restarts for the next utterance');

  els.spk.onclick(); sb.nextReply = { reply: 'Line one\nLine two', domains: [], lang: 'en', choices: [] };
  live()[0].hear('what is playing tonight'); step(450, 0.1); step(1100, 0.004); await flush(); await flush();
  assert.strictEqual(sent.length, 2); assert.strictEqual(sent[1], 'what is playing tonight');
  assert.strictEqual(get('hfSpeaking'), true); assert.strictEqual(spoken.length, 1); assert.strictEqual(live().length, 0); ok('reply is spoken; recogniser paused so the bot does not transcribe itself');

  const before = cancels; step(2000, 0.035); assert.strictEqual(cancels, before); assert.strictEqual(get('hfSpeaking'), true); ok('speaker leakage while TTS plays does NOT interrupt it');
  step(600, 0.16); assert.ok(cancels > before); assert.strictEqual(get('hfSpeaking'), false); step(300, 0.16); assert.strictEqual(live().length, 1); assert.strictEqual(get('hf.heard'), true); ok('talking over the assistant = barge-in: TTS stops, listening resumes');

  step(1500, 0.004); await flush(); step(300, 0.004);
  sb.nextReply = { reply: 'Mon item\n\nIs that right?', domains: [], lang: 'en', choices: ['Yes', 'No'] }; get('send("x")'); await flush(); await flush();
  assert.strictEqual(spoken[spoken.length - 1].text, 'Is that right?'); ok('clarification replies speak the QUESTION, not the list before it');
  spoken[spoken.length - 1].onend(); step(300, 0.004); assert.strictEqual(get('hfSpeaking'), false); assert.strictEqual(live().length, 1); ok('after TTS ends naturally the recogniser resumes');

  els.mic.onclick(); assert.ok(/Hands-free is on/.test(els.status.textContent)); ok('push-to-talk button is inert while hands-free is on');
  els.hf.onclick(); assert.strictEqual(get('hf.on'), false); assert.ok(stopped > 0); assert.strictEqual(els.hf.attrs['aria-pressed'], 'false'); ok('stop: microphone tracks released');
  console.log(`\n${n} hands-free glue checks passed`);
})().catch(e => { console.error('FAIL', e); process.exit(1); });
