/* ============================================================
   EV Lab dashboard - frontend logic
   ============================================================
   - Top tabs: Dashboard | Bot | Config
   - Control button events -> POST /api/control/{ch}
   - WebSocket subscription to /ws/state for authoritative state
   - Mic push-to-talk: Web Audio capture -> 16 kHz PCM16 -> /api/transcribe
   - Chat: /api/ask + configurable TTS (off / browser / server pyttsx3 / server gtts)
   - Config tab: live STT/TTS backend selection via /api/voice/config
   - Clock + status pill
   ============================================================ */

(() => {
'use strict';

// ---------- helpers ----------
const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

const ICON_BY_CHANNEL = {
  headlight:     '#iconHeadlight',
  reverse:       '#iconCar',
  hazard:        '#iconHazard',
  all_lamp:      '#iconAllLamp',
  left_ind:      '#iconLeft',
  right_ind:     '#iconRight',
  parking_brake: '#iconParkingBrake',
};

// ---------- state ----------
const state = {
  channels: {},
  ignition: false,
  ready: false,
  tab: 'dashboard',          // 'dashboard' | 'bot' | 'config'
  recording: false,
  busy: false,
  voice: {                   // mirrors /api/voice/config
    stt: 'vosk',
    tts: 'browser',
    sttOptions: ['off', 'vosk', 'google'],
    ttsOptions: ['off', 'browser', 'pyttsx3', 'gtts'],
    sttBackends: {},
    ttsBackends: {},
    ttsRate: 160,            // offline (pyttsx3) words/min
  },
  display: {                 // client-only, persisted in localStorage
    fontSize: 'md',          // FONT_SIZES id
    fontFamily: 'system',    // FONT_FAMILIES id
  },
  configUnlocked: false,     // Config tab is password-gated per session
  kbUnlocked: false,         // Knowledge Base tab is password-gated per session
};

const TAB_TITLES = {
  dashboard: 'IOT ELECTRIC VEHICLE DASHBOARD',
  bot:       'VEHICLE VOICE CONTROL & CHAT BOT',
  config:    'CONFIGURATION',
  kb:        'KNOWLEDGE BASE',
};

// ---------- tab navigation ----------
function setTab(tab) {
  if (!TAB_TITLES[tab]) return;
  state.tab = tab;
  $('#viewDashboard').classList.toggle('active', tab === 'dashboard');
  $('#viewBot').classList.toggle('active', tab === 'bot');
  $('#viewConfig').classList.toggle('active', tab === 'config');
  $('#viewKb').classList.toggle('active', tab === 'kb');
  for (const t of $$('.tab')) {
    const on = t.dataset.tab === tab;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  }
  $('#titleText').textContent = TAB_TITLES[tab];
  $('#inputText').placeholder = (tab === 'dashboard')
    ? 'Speak or type a command — e.g. "turn on the headlights"'
    : 'Type a question or hold MIC to speak…';
  if (tab === 'config') applyConfigLock();
  if (tab === 'kb')     applyKbLock();
}
for (const t of $$('.tab')) {
  t.addEventListener('click', () => setTab(t.dataset.tab));
}

// ---------- status pill + clock ----------
function setStatus(text, kind = '') {
  const el = $('#statusPill');
  el.textContent = text;
  el.classList.remove('ready', 'busy', 'err');
  if (kind) el.classList.add(kind);
}

function tickClock() {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  $('#clock').textContent = `${hh}:${mm}`;
}
tickClock(); setInterval(tickClock, 30 * 1000);

// ---------- apply server snapshot to UI ----------
function applyState(snap) {
  state.channels = snap.channels || {};
  state.ignition = !!snap.ignition;
  document.body.classList.toggle('ignition-on',  state.ignition);
  document.body.classList.toggle('ignition-off', !state.ignition);

  for (const btn of $$('.ctl')) {
    const ch = btn.dataset.ch;
    const on = !!state.channels[ch];
    btn.classList.toggle('on', on);
    btn.textContent = on ? btn.dataset.labelOn : btn.dataset.labelOff;
    if (ch !== 'ignition') {
      btn.disabled = !state.ignition;
    }
  }

  // Effective output mirrors server _apply_outputs(): all-lamp mode forces
  // head + tail lamps on and makes both indicators blink; hazard blinks both.
  const allLamp   = !!state.channels.all_lamp;
  const modeBlink = !!state.channels.hazard || allLamp;

  // SVG icon lit-states.
  setIcon('reverse');
  setIcon('hazard');
  setIcon('all_lamp');
  setIcon('parking_brake');

  // Headlight tell-tale: its own toggle, or forced on by all-lamp mode.
  const hl = $('#iconHeadlight');
  if (hl) hl.classList.toggle('on', !!state.channels.headlight || allLamp);

  // Brake tell-tale: brake, or all-lamp mode. (Parking brake has its own
  // tell-tale via setIcon('parking_brake') — kept separate.)
  const brakeOn = !!state.channels.brake || allLamp;
  const brakeEl = $('#iconBrake');
  if (brakeEl) brakeEl.classList.toggle('on', brakeOn);

  // Indicators: the real lamps blink via the hardware flasher, so the
  // dashboard arrows blink whenever an indicator is active (individually or
  // under hazard / all-lamp).
  const left  = $('#iconLeft');
  const right = $('#iconRight');
  left.classList.toggle('hazard',  !!state.channels.left_ind  || modeBlink);
  right.classList.toggle('hazard', !!state.channels.right_ind || modeBlink);
  left.classList.remove('on', 'blink');
  right.classList.remove('on', 'blink');
}

function setIcon(ch) {
  const sel = ICON_BY_CHANNEL[ch];
  if (!sel) return;
  const el = document.querySelector(sel);
  if (el) el.classList.toggle('on', !!state.channels[ch]);
}

// ---------- control buttons ----------
async function sendControl(ch, action) {
  try {
    const r = await fetch(`/api/control/${ch}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action}),
    });
    if (!r.ok) {
      setStatus(`control ${ch} failed: ${r.status}`, 'err');
      return;
    }
    const data = await r.json();
    applyState(data);
  } catch (e) {
    setStatus(`control ${ch} error`, 'err');
  }
}

for (const btn of $$('.ctl')) {
  const ch = btn.dataset.ch;
  if (btn.classList.contains('momentary')) {
    btn.addEventListener('pointerdown',   e => { e.preventDefault(); sendControl(ch, 'press'); });
    btn.addEventListener('pointerup',     e => { e.preventDefault(); sendControl(ch, 'release'); });
    btn.addEventListener('pointerleave',  () => { if (state.channels[ch]) sendControl(ch, 'release'); });
    btn.addEventListener('pointercancel', () => sendControl(ch, 'release'));
  } else {
    btn.addEventListener('click', () => sendControl(ch, 'toggle'));
  }
}

// ---------- WebSocket ----------
let ws = null;
function openWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/state`);
  ws.addEventListener('message', evt => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'snapshot') applyState(msg);
    } catch {}
  });
  ws.addEventListener('close', () => { setTimeout(openWS, 1500); });
  ws.addEventListener('error', () => { try { ws.close(); } catch {} });
}
openWS();

// ---------- initial state + readiness poll ----------
async function pollReady() {
  try {
    const r = await fetch('/api/state');
    const data = await r.json();
    applyState(data);
    state.ready = !!data.ready;
    if (data.error) { setStatus(data.error, 'err'); return; }
    if (state.ready) {
      setStatus('ready', 'ready');
      loadVoiceConfig();          // refresh once warm so backend availability is correct
    } else {
      setStatus('warming up…', 'busy');
      setTimeout(pollReady, 1500);
    }
  } catch {
    setStatus('server unreachable', 'err');
    setTimeout(pollReady, 2000);
  }
}
pollReady();

// ---------- chat ----------
const chatScroll = $('#chatScroll');
let chatAbort = null;   // AbortController for the in-flight /api/ask request
const INITIAL_GREETING = "Hi! I'm the EV Lab Chatbot. Ask me anything about electric vehicles. The first answer takes ~30 s while I warm up.";
function bubble(kind, text) {
  const el = document.createElement('div');
  el.className = `chat-bubble ${kind}`;
  const body = document.createElement('div');
  body.className = 'bubble-body';
  body.textContent = text;
  el.appendChild(body);
  chatScroll.appendChild(el);
  chatScroll.scrollTop = chatScroll.scrollHeight;
  return el;
}
function typingDots() {
  const el = document.createElement('div');
  el.className = 'typing-dots';
  el.innerHTML = '<span></span><span></span><span></span>';
  chatScroll.appendChild(el);
  chatScroll.scrollTop = chatScroll.scrollHeight;
  return el;
}

async function ask(question) {
  question = (question || '').trim();
  if (!question) return;
  if (state.busy) return;
  if (state.tab !== 'bot') setTab('bot');
  bubble('user', question);
  const dots = typingDots();
  state.busy = true;
  setStatus('thinking…', 'busy');
  const ac = new AbortController();
  chatAbort = ac;

  try {
    const r = await fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question}),
      signal: ac.signal,
    });
    dots.remove();
    if (!r.ok) {
      const text = await r.text();
      bubble('refuse', `error: ${r.status} ${text.slice(0, 200)}`);
      setStatus('error', 'err');
      return;
    }
    const out = await r.json();
    const kind = out.refused ? 'refuse' : 'bot';
    const el = bubble(kind, out.text);
    if (out.sources && out.sources.length) {
      const chips = document.createElement('div');
      chips.className = 'bubble-sources';
      const seen = new Set();
      for (const s of out.sources) {
        const key = `${s.source}::${s.page}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const c = document.createElement('span');
        c.className = 'source-chip';
        c.textContent = `${s.source}, p.${s.page}`;
        chips.appendChild(c);
      }
      el.appendChild(chips);
    }
    const meta = document.createElement('div');
    meta.className = 'bubble-meta';
    const bits = [`${out.latency}s`];
    if (out.top_dense_score != null) bits.push(`dense=${out.top_dense_score.toFixed(2)}`);
    if (out.gate_reason)             bits.push(`gate: ${out.gate_reason}`);
    meta.textContent = bits.join(' • ');
    el.appendChild(meta);

    setStatus('ready', 'ready');
    if (out.text && !out.refused) speak(out.text);
  } catch (e) {
    dots.remove();
    if (e.name === 'AbortError') {
      setStatus('stopped', '');
    } else {
      bubble('refuse', `network error: ${e}`);
      setStatus('error', 'err');
    }
  } finally {
    state.busy = false;
    chatAbort = null;
  }
}

// ---------- chat controls: stop + reset ----------
function stopChat() {
  if (chatAbort) { try { chatAbort.abort(); } catch {} }          // abort in-flight /api/ask
  try { speechSynthesis.cancel(); } catch {}                      // stop browser TTS
  try { if (serverAudio) serverAudio.pause(); } catch {}          // stop server TTS playback
  const dots = chatScroll.querySelector('.typing-dots');
  if (dots) dots.remove();
  state.busy = false;
  setStatus(state.ready ? 'ready' : 'stopped', state.ready ? 'ready' : '');
}

function resetChat() {
  stopChat();
  chatScroll.innerHTML = '';
  bubble('bot', INITIAL_GREETING);
}

$('#stopBtn').addEventListener('click', stopChat);
$('#resetBtn').addEventListener('click', resetChat);

// ---------- switching / IoT mode: voice/text commands -> control ----------
// On the Dashboard (switching) tab the shared input bar + MIC drive controls
// via the LLM intent classifier; on the Bot tab they do RAG chat. Success is
// shown by the button changing colour (live via /ws/state); the toast carries
// the spoken/written confirmation and any error/refusal.
let _toastTimer = null;
function showSwitchToast(msg, level) {
  const el = $('#switchToast');
  if (!el || !msg) return;
  el.textContent = msg;
  el.className = 'switch-toast show ' + (level || 'ok');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = 'switch-toast'; }, 3500);
}

async function sendSwitchCommand(text) {
  if (state.busy) return;
  state.busy = true;
  setStatus('command…', 'busy');
  try {
    const r = await fetch('/api/switch', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text}),
    });
    if (!r.ok) {
      const t = await r.text();
      showSwitchToast(`error: ${r.status} ${t.slice(0, 80)}`, 'error');
      setStatus('error', 'err');
      return;
    }
    const out = await r.json();
    applyState(out);                       // buttons reflect the new state immediately
    showSwitchToast(out.message || '', out.level || 'ok');
    setStatus('ready', 'ready');
    if (out.message) speak(out.message);   // speak-back works in switching too (if TTS on)
  } catch (e) {
    showSwitchToast('network error', 'error');
    setStatus('error', 'err');
  } finally {
    state.busy = false;
  }
}

// Route shared-input / mic text to the right handler for the active tab.
function routeInput(text) {
  text = (text || '').trim();
  if (!text) return;
  if (state.tab === 'dashboard') sendSwitchCommand(text);
  else ask(text);
}

// ---------- TTS (router: off / browser / pyttsx3 / gtts) ----------
let serverAudio = null;       // <audio> element reused for server TTS playback

function speak(text) {
  const mode = state.voice.tts;
  if (!text || mode === 'off') return;
  if (mode === 'browser') return speakBrowser(text);
  return speakServer(text);   // pyttsx3 / gtts
}

function speakBrowser(text) {
  if (!('speechSynthesis' in window)) return;
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0; u.pitch = 1.0; u.volume = 1.0;
    speechSynthesis.speak(u);
  } catch {}
}

function speakServer(text) {
  try {
    if (!serverAudio) {
      serverAudio = new Audio();
      serverAudio.preload = 'auto';
    }
    serverAudio.pause();
    const url = `/api/tts?text=${encodeURIComponent(text)}&backend=${encodeURIComponent(state.voice.tts)}&t=${Date.now()}`;
    serverAudio.src = url;
    serverAudio.play().catch(err => {
      setStatus(`TTS playback blocked: ${err.message || err}`, 'err');
    });
  } catch (e) {
    setStatus(`TTS error: ${e}`, 'err');
  }
}

// ---------- mic / push-to-talk ----------
let audioCtx = null;
let micStream = null;
let micSource = null;
let micProcessor = null;
let micBuffers = [];

const SAMPLE_RATE_TARGET = 16000;

function micEnabled() { return state.voice.stt !== 'off'; }

function refreshMicButton() {
  const btn = $('#micBtn');
  const off = !micEnabled();
  btn.classList.toggle('disabled', off);
  btn.title = off ? 'Speech-to-text disabled (see Config tab)' : 'Hold to speak';
}

async function startRec() {
  if (!micEnabled()) {
    setStatus('STT disabled — see Config', 'err');
    return;
  }
  if (state.recording) return;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({audio: true});
  } catch (e) {
    setStatus('mic denied', 'err'); return;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  micSource = audioCtx.createMediaStreamSource(micStream);
  micProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
  micBuffers = [];
  micProcessor.onaudioprocess = e => {
    const ch = e.inputBuffer.getChannelData(0);
    micBuffers.push(new Float32Array(ch));
  };
  micSource.connect(micProcessor);
  micProcessor.connect(audioCtx.destination);
  state.recording = true;
  $('#micBtn').classList.add('recording');
  setStatus('recording', 'busy');
}

async function stopRec() {
  if (!state.recording) return;
  state.recording = false;
  $('#micBtn').classList.remove('recording');

  try { micProcessor.disconnect(); } catch {}
  try { micSource.disconnect(); } catch {}
  try { micStream.getTracks().forEach(t => t.stop()); } catch {}

  if (!micBuffers.length) {
    if (audioCtx) await audioCtx.close();
    setStatus('ready', 'ready');
    return;
  }

  const float32 = mergeBuffers(micBuffers);
  const srcRate = audioCtx.sampleRate;
  if (audioCtx) await audioCtx.close();
  const downsampled = downsample(float32, srcRate, SAMPLE_RATE_TARGET);
  const pcm16 = floatToPCM16(downsampled);

  setStatus('transcribing…', 'busy');
  try {
    const r = await fetch('/api/transcribe', {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream'},
      body: pcm16,
    });
    if (!r.ok) {
      const err = await r.text();
      setStatus(`STT ${r.status}: ${err.slice(0,80)}`, 'err'); return;
    }
    const out = await r.json();
    const text = (out.text || '').trim();
    setStatus('ready', 'ready');
    if (text) routeInput(text);
    else if (state.tab === 'dashboard') showSwitchToast("Didn't catch that.", 'error');
    else bubble('bot', "(didn't catch that)");
  } catch (e) {
    setStatus('STT error', 'err');
  }
}

function mergeBuffers(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

function downsample(float32, srcRate, dstRate) {
  if (dstRate >= srcRate) return float32;
  const ratio = srcRate / dstRate;
  const newLen = Math.round(float32.length / ratio);
  const out = new Float32Array(newLen);
  let off = 0; let pos = 0;
  while (off < newLen) {
    const next = Math.round((off + 1) * ratio);
    let sum = 0, cnt = 0;
    for (let i = pos; i < next && i < float32.length; i++) { sum += float32[i]; cnt++; }
    out[off] = cnt ? sum / cnt : 0;
    off++; pos = next;
  }
  return out;
}

function floatToPCM16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return out.buffer;
}

const micBtn = $('#micBtn');
micBtn.addEventListener('pointerdown',   e => { e.preventDefault(); startRec(); });
micBtn.addEventListener('pointerup',     e => { e.preventDefault(); stopRec();  });
micBtn.addEventListener('pointerleave',  () => { if (state.recording) stopRec(); });
micBtn.addEventListener('pointercancel', () => stopRec());

// ---------- send button + Enter key ----------
const sendBtn = $('#sendBtn');
const inputEl = $('#inputText');
function submitFromInput() {
  const t = inputEl.value;
  inputEl.value = '';
  routeInput(t);
}
sendBtn.addEventListener('click', submitFromInput);
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitFromInput();
  }
});

// ---------- Config tab ----------
// "client-only" options are valid even when the server reports no backend,
// because they're handled in the browser.
const STT_META = {
  off:    {label: 'Off',                                 sub: 'Mic input disabled',      clientOnly: true},
  vosk:   {label: 'Vosk (offline)',                      sub: 'Local model, no network', clientOnly: false},
  google: {label: 'Google (SpeechRecognition)',          sub: 'Online via Google API',   clientOnly: false},
};
const TTS_META = {
  off:     {label: 'Off',                                sub: 'No spoken replies',         clientOnly: true},
  browser: {label: 'Browser (built-in)',                 sub: 'window.speechSynthesis',    clientOnly: true},
  pyttsx3: {label: 'pyttsx3 (offline, server)',          sub: 'SAPI5 on Win / espeak-ng',  clientOnly: false},
  gtts:    {label: 'gTTS (online, server)',              sub: 'Google translate voice',    clientOnly: false},
};

function renderVoiceOptions(role) {
  const meta    = role === 'stt' ? STT_META          : TTS_META;
  const options = role === 'stt' ? state.voice.sttOptions : state.voice.ttsOptions;
  const avail   = role === 'stt' ? state.voice.sttBackends : state.voice.ttsBackends;
  const current = role === 'stt' ? state.voice.stt   : state.voice.tts;
  const host    = $(role === 'stt' ? '#sttOptions' : '#ttsOptions');

  host.innerHTML = '';
  for (const id of options) {
    const m = meta[id] || {label: id, sub: '', clientOnly: false};
    const info = avail[id];
    const isAvailable = m.clientOnly || (info && info.available);

    const row = document.createElement('label');
    row.className = 'cfg-option';
    if (current === id) row.classList.add('checked');
    if (!isAvailable)   row.classList.add('disabled');

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = `voice-${role}`;
    radio.value = id;
    radio.checked = current === id;
    radio.disabled = !isAvailable;
    radio.addEventListener('change', () => setVoice(role, id));

    const labelWrap = document.createElement('span');
    labelWrap.className = 'cfg-option-label';
    labelWrap.textContent = m.label;
    if (m.sub) {
      const sub = document.createElement('span');
      sub.className = 'cfg-option-sub';
      sub.textContent = ` — ${m.sub}`;
      labelWrap.appendChild(sub);
    }

    const badge = document.createElement('span');
    badge.className = 'cfg-badge';
    if (m.clientOnly)      { badge.classList.add('na');   badge.textContent = 'CLIENT'; }
    else if (isAvailable)  { badge.classList.add('ok');   badge.textContent = 'READY'; }
    else                   { badge.classList.add('miss'); badge.textContent = 'MISSING'; }

    row.appendChild(radio);
    row.appendChild(labelWrap);
    row.appendChild(badge);
    host.appendChild(row);
  }
}

function renderConfigMeta() {
  $('#cfgServer').textContent  = location.host;
  $('#cfgEngine').textContent  = state.ready ? 'ready' : 'warming…';
  $('#cfgSttNow').textContent  = state.voice.stt;
  $('#cfgTtsNow').textContent  = state.voice.tts;
  const fsEl = $('#cfgFontSize');
  if (fsEl) fsEl.textContent = fontSizeDef(state.display.fontSize).label;
  const ffEl = $('#cfgFontFamily');
  if (ffEl) ffEl.textContent = fontFamilyDef(state.display.fontFamily).label;
}

async function loadVoiceConfig() {
  try {
    const r = await fetch('/api/voice/config');
    if (!r.ok) return;
    const cfg = await r.json();
    state.voice.stt         = cfg.stt;
    state.voice.tts         = cfg.tts;
    state.voice.sttOptions  = cfg.stt_options || state.voice.sttOptions;
    state.voice.ttsOptions  = cfg.tts_options || state.voice.ttsOptions;
    state.voice.sttBackends = cfg.stt_backends || {};
    state.voice.ttsBackends = cfg.tts_backends || {};
    if (cfg.tts_rate != null) state.voice.ttsRate = cfg.tts_rate;
    renderVoiceOptions('stt');
    renderVoiceOptions('tts');
    renderRateOptions();
    renderConfigMeta();
    refreshMicButton();
  } catch (e) {
    // leave defaults
  }
}

async function setVoice(role, value) {
  try {
    const body = {};
    body[role] = value;
    const r = await fetch('/api/voice/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      setStatus(`config save failed: ${r.status}`, 'err');
      return;
    }
    const out = await r.json();
    state.voice.stt = out.stt;
    state.voice.tts = out.tts;
    renderVoiceOptions('stt');
    renderVoiceOptions('tts');
    renderConfigMeta();
    refreshMicButton();
  } catch (e) {
    setStatus(`config error: ${e}`, 'err');
  }
}

$('#ttsTestBtn').addEventListener('click', () => {
  speak('Text to speech test. This is the EV Lab dashboard.');
});

// ---------- Config: speech rate (offline pyttsx3 voice) ----------
const TTS_RATES = [
  {id: 'slow',   label: 'Slow',   rate: 125},
  {id: 'normal', label: 'Normal', rate: 160},
  {id: 'fast',   label: 'Fast',   rate: 200},
];
function ratePresetFor(r) {            // closest preset to the current numeric rate
  let best = TTS_RATES[1];
  for (const o of TTS_RATES) if (Math.abs(o.rate - r) < Math.abs(best.rate - r)) best = o;
  return best;
}
function renderRateOptions() {
  const host = $('#ttsRateOptions');
  if (!host) return;
  host.innerHTML = '';
  const cur = ratePresetFor(state.voice.ttsRate);
  for (const o of TTS_RATES) {
    const row = document.createElement('label');
    row.className = 'cfg-option' + (o.id === cur.id ? ' checked' : '');
    const radio = document.createElement('input');
    radio.type = 'radio'; radio.name = 'tts-rate'; radio.value = o.id;
    radio.checked = (o.id === cur.id);
    radio.addEventListener('change', () => setRate(o.rate));
    const lab = document.createElement('span');
    lab.className = 'cfg-option-label';
    lab.textContent = o.label;
    const sub = document.createElement('span');
    sub.className = 'cfg-option-sub'; sub.textContent = ` — ${o.rate} wpm`;
    lab.appendChild(sub);
    row.appendChild(radio); row.appendChild(lab);
    host.appendChild(row);
  }
}
async function setRate(rate) {
  try {
    const r = await fetch('/api/voice/config', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tts_rate: rate}),
    });
    if (!r.ok) { setStatus(`rate save failed: ${r.status}`, 'err'); return; }
    const out = await r.json();
    if (out.tts_rate != null) state.voice.ttsRate = out.tts_rate;
    renderRateOptions();
  } catch (e) {
    setStatus(`rate error: ${e}`, 'err');
  }
}

// ---------- Config tab: display (font size + font style) ----------
// Client-only preferences, persisted in localStorage and applied globally
// via the --fs (size multiplier) and --font (family) CSS variables on
// <html>. Every text rule in styles.css reads these, so changing them
// affects the whole interface — dashboard, chat, config, buttons, input.
const FONT_SIZES = [
  {id: 'sm', label: 'Small',       scale: 0.85},
  {id: 'md', label: 'Medium',      scale: 1.00},
  {id: 'lg', label: 'Large',       scale: 1.18},
  {id: 'xl', label: 'Extra large', scale: 1.35},
];
const FONT_FAMILIES = [
  {id: 'system',  label: 'System (default)',  stack: '"Segoe UI","Roboto","Inter",system-ui,-apple-system,"Noto Sans","DejaVu Sans",sans-serif'},
  {id: 'sans',    label: 'Inter / Sans',      stack: '"Inter","Helvetica Neue","DejaVu Sans","Noto Sans",Arial,sans-serif'},
  {id: 'legible', label: 'Verdana (legible)', stack: 'Verdana,"DejaVu Sans","Noto Sans",Tahoma,sans-serif'},
  {id: 'serif',   label: 'Serif',             stack: 'Georgia,"Liberation Serif","DejaVu Serif","Noto Serif","Times New Roman",serif'},
  {id: 'mono',    label: 'Monospace',         stack: '"Cascadia Code",Consolas,"DejaVu Sans Mono","Liberation Mono",monospace'},
];
const LS_FONT_SIZE   = 'ev.fontSize';
const LS_FONT_FAMILY = 'ev.fontFamily';

function fontSizeDef(id)   { return FONT_SIZES.find(o => o.id === id)    || FONT_SIZES[1]; }
function fontFamilyDef(id) { return FONT_FAMILIES.find(o => o.id === id) || FONT_FAMILIES[0]; }

function loadDisplay() {
  try {
    const fs = localStorage.getItem(LS_FONT_SIZE);
    const ff = localStorage.getItem(LS_FONT_FAMILY);
    if (fs && FONT_SIZES.some(o => o.id === fs))    state.display.fontSize = fs;
    if (ff && FONT_FAMILIES.some(o => o.id === ff)) state.display.fontFamily = ff;
  } catch {}
}

function applyDisplay() {
  const root = document.documentElement;
  root.style.setProperty('--fs', String(fontSizeDef(state.display.fontSize).scale));
  root.style.setProperty('--font', fontFamilyDef(state.display.fontFamily).stack);
}

function setDisplay(kind, id) {
  if (kind === 'size')   state.display.fontSize   = id;
  if (kind === 'family') state.display.fontFamily = id;
  try {
    localStorage.setItem(LS_FONT_SIZE,   state.display.fontSize);
    localStorage.setItem(LS_FONT_FAMILY, state.display.fontFamily);
  } catch {}
  applyDisplay();
  renderFontOptions();
  renderConfigMeta();
}

function fontRow(kind, id, label, checked) {
  const row = document.createElement('label');
  row.className = 'cfg-option';
  if (checked) row.classList.add('checked');
  const radio = document.createElement('input');
  radio.type = 'radio';
  radio.name = `font-${kind}`;
  radio.value = id;
  radio.checked = checked;
  radio.addEventListener('change', () => setDisplay(kind, id));
  const labelWrap = document.createElement('span');
  labelWrap.className = 'cfg-option-label';
  labelWrap.textContent = label;
  row.appendChild(radio);
  row.appendChild(labelWrap);
  return row;
}

function renderFontOptions() {
  const sizeHost = $('#fontSizeOptions');
  if (sizeHost) {
    sizeHost.innerHTML = '';
    for (const o of FONT_SIZES) {
      const row = fontRow('size', o.id, o.label, o.id === state.display.fontSize);
      const prev = document.createElement('span');
      prev.className = 'cfg-preview';
      prev.textContent = 'Aa';
      prev.style.fontSize = Math.round(15 * o.scale) + 'px';   // absolute, shows relative size
      row.appendChild(prev);
      sizeHost.appendChild(row);
    }
  }
  const famHost = $('#fontFamilyOptions');
  if (famHost) {
    famHost.innerHTML = '';
    for (const o of FONT_FAMILIES) {
      const row = fontRow('family', o.id, o.label, o.id === state.display.fontFamily);
      row.querySelector('.cfg-option-label').style.fontFamily = o.stack;
      const prev = document.createElement('span');
      prev.className = 'cfg-preview';
      prev.textContent = 'Aa Bb 123';
      prev.style.fontFamily = o.stack;
      famHost.appendChild(row);
      row.appendChild(prev);
    }
  }
}

// Initial render with defaults so the Config tab isn't empty before the
// server config arrives.
// ---------- Config tab: password gate ----------
// Client-side lock for a kiosk. Default password below; the user can change
// it (stored in localStorage). Unlock lasts for the session (re-locks on
// reload). This is access convenience, not strong security.
const CFG_PW_KEY = 'ev.cfgPassword';
const CFG_PW_DEFAULT = 'IsieIndiaOne23';

function cfgPassword() {
  try { return localStorage.getItem(CFG_PW_KEY) || CFG_PW_DEFAULT; }
  catch { return CFG_PW_DEFAULT; }
}

function applyConfigLock() {
  const lock = $('#configLock');
  const content = $('#configContent');
  if (!lock || !content) return;
  if (state.configUnlocked) {
    lock.hidden = true;
    content.hidden = false;
  } else {
    lock.hidden = false;
    content.hidden = true;
    const inp = $('#cfgPwInput');
    if (inp) { inp.value = ''; setTimeout(() => inp.focus(), 50); }
    const err = $('#cfgPwError'); if (err) err.textContent = '';
  }
}

function tryUnlock() {
  const inp = $('#cfgPwInput');
  const err = $('#cfgPwError');
  if (!inp) return;
  if (inp.value === cfgPassword()) {
    state.configUnlocked = true;
    applyConfigLock();
  } else {
    if (err) err.textContent = 'Incorrect password.';
    inp.value = '';
    inp.focus();
  }
}

$('#cfgUnlockBtn').addEventListener('click', tryUnlock);
$('#cfgPwInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); tryUnlock(); }
});

$('#cfgPwSaveBtn').addEventListener('click', () => {
  const cur = $('#cfgCurPw'), nw = $('#cfgNewPw'), msg = $('#cfgPwChangeMsg');
  if (!cur || !nw || !msg) return;
  if (cur.value !== cfgPassword()) {
    msg.classList.remove('ok'); msg.textContent = 'Current password is incorrect.';
    return;
  }
  if (nw.value.length < 4) {
    msg.classList.remove('ok'); msg.textContent = 'New password must be at least 4 characters.';
    return;
  }
  try { localStorage.setItem(CFG_PW_KEY, nw.value); } catch {}
  cur.value = ''; nw.value = '';
  msg.classList.add('ok'); msg.textContent = 'Password updated.';
});

// ---------- Knowledge Base tab ----------
// Separate password from Config (its own localStorage key). Unlock shows the
// full bank (search-filtered) + an Add modal + a Download-handout button.
const KB_PW_KEY = 'ev.kbPassword';
const KB_PW_DEFAULT = 'IsieKB@dmin23';
function kbPassword() {
  try { return localStorage.getItem(KB_PW_KEY) || KB_PW_DEFAULT; }
  catch { return KB_PW_DEFAULT; }
}

let bankItems = [];

function applyKbLock() {
  const lock = $('#kbLock'), content = $('#kbContent');
  if (!lock || !content) return;
  if (state.kbUnlocked) {
    lock.hidden = true; content.hidden = false;
    loadBank();
  } else {
    lock.hidden = false; content.hidden = true;
    const inp = $('#kbPwInput');
    if (inp) { inp.value = ''; setTimeout(() => inp.focus(), 50); }
    const err = $('#kbPwError'); if (err) err.textContent = '';
  }
}

function tryUnlockKb() {
  const inp = $('#kbPwInput'), err = $('#kbPwError');
  if (!inp) return;
  if (inp.value === kbPassword()) { state.kbUnlocked = true; applyKbLock(); }
  else { if (err) err.textContent = 'Incorrect password.'; inp.value = ''; inp.focus(); }
}

async function loadBank() {
  try {
    const r = await fetch('/api/bank/list');
    if (!r.ok) return;
    const d = await r.json();
    bankItems = d.items || [];
    $('#kbCount').textContent = (d.count != null ? d.count : bankItems.length);
    renderBank();
  } catch {}
}

function renderBank() {
  const host = $('#kbList');
  host.innerHTML = '';
  const frag = document.createDocumentFragment();
  for (const it of bankItems) {
    const el = document.createElement('div');
    el.className = 'kb-item';
    el.dataset.text = ((it.question || '') + ' ' + (it.answer || '')).toLowerCase();

    const head = document.createElement('div'); head.className = 'kb-item-head';
    const q = document.createElement('div'); q.className = 'kb-q'; q.textContent = it.question || '';
    if (it.deletable) {
      const badge = document.createElement('span');
      badge.className = 'kb-badge-added'; badge.textContent = 'ADDED';
      q.appendChild(badge);
    }
    head.appendChild(q);
    if (it.deletable) {                       // only user-added rows get a delete button
      const del = document.createElement('button');
      del.className = 'kb-del'; del.textContent = '✕';
      del.title = 'Delete this added question';
      del.addEventListener('click', () => deleteQuestion(it.id, it.question));
      head.appendChild(del);
    }
    el.appendChild(head);

    const a = document.createElement('div'); a.className = 'kb-a'; a.textContent = it.answer || '';
    el.appendChild(a);
    if (it.reference) {
      const rf = document.createElement('div'); rf.className = 'kb-ref'; rf.textContent = it.reference;
      el.appendChild(rf);
    }
    frag.appendChild(el);
  }
  host.appendChild(frag);
  filterBank();
}

async function deleteQuestion(id, label) {
  if (!confirm('Delete this added question?\n\n' + (label || id) + '\n\nThis cannot be undone.')) return;
  try {
    const r = await fetch('/api/bank/' + encodeURIComponent(id), {method: 'DELETE'});
    if (!r.ok) { const t = await r.text(); alert('Delete failed: ' + r.status + ' ' + t.slice(0, 120)); return; }
    await loadBank();
  } catch (e) {
    alert('Delete error: ' + e);
  }
}

function filterBank() {
  const q = ($('#kbSearch').value || '').toLowerCase();
  for (const el of $('#kbList').children) {
    el.style.display = el.dataset.text.indexOf(q) >= 0 ? '' : 'none';
  }
}

function openKbModal() {
  $('#kbQ').value = ''; $('#kbA').value = ''; $('#kbR').value = '';
  const msg = $('#kbAddMsg'); msg.textContent = ''; msg.classList.remove('ok');
  $('#kbModal').hidden = false;
  setTimeout(() => $('#kbQ').focus(), 50);
}
function closeKbModal() { $('#kbModal').hidden = true; }

async function submitKbQuestion() {
  const q = $('#kbQ').value.trim(), a = $('#kbA').value.trim(), ref = $('#kbR').value.trim();
  const msg = $('#kbAddMsg'); msg.classList.remove('ok');
  if (!q || !a) { msg.textContent = 'Question and answer are required.'; return; }
  const btn = $('#kbSaveBtn'); btn.disabled = true; msg.textContent = 'Adding…';
  try {
    const r = await fetch('/api/bank/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q, answer: a, reference: ref}),
    });
    if (!r.ok) { const t = await r.text(); msg.textContent = `Failed: ${r.status} ${t.slice(0,120)}`; return; }
    closeKbModal();
    await loadBank();
  } catch (e) {
    msg.textContent = `Error: ${e}`;
  } finally {
    btn.disabled = false;
  }
}

function downloadBank() {
  const a = document.createElement('a');
  a.href = '/api/bank/download'; a.download = 'question_bank.html';
  document.body.appendChild(a); a.click(); a.remove();
}

$('#kbUnlockBtn').addEventListener('click', tryUnlockKb);
$('#kbPwInput').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); tryUnlockKb(); } });
$('#kbSearch').addEventListener('input', filterBank);
$('#kbAddBtn').addEventListener('click', openKbModal);
$('#kbCancelBtn').addEventListener('click', closeKbModal);
$('#kbSaveBtn').addEventListener('click', submitKbQuestion);
$('#kbDownloadBtn').addEventListener('click', downloadBank);
$('#kbModal').addEventListener('click', e => { if (e.target === $('#kbModal')) closeKbModal(); });

$('#kbPwSaveBtn').addEventListener('click', () => {
  const cur = $('#kbCurPw'), nw = $('#kbNewPw'), msg = $('#kbPwChangeMsg');
  if (!cur || !nw || !msg) return;
  if (cur.value !== kbPassword()) { msg.classList.remove('ok'); msg.textContent = 'Current password is incorrect.'; return; }
  if (nw.value.length < 4)        { msg.classList.remove('ok'); msg.textContent = 'New password must be at least 4 characters.'; return; }
  try { localStorage.setItem(KB_PW_KEY, nw.value); } catch {}
  cur.value = ''; nw.value = '';
  msg.classList.add('ok'); msg.textContent = 'Password updated.';
});

// ---------- final init ----------
// Font prefs are defined further up as consts, so apply them here (after
// their declarations) — calling applyDisplay() earlier would hit the
// temporal dead zone on FONT_SIZES/FONT_FAMILIES and abort init.
loadDisplay();
applyDisplay();
applyConfigLock();
applyKbLock();
renderVoiceOptions('stt');
renderVoiceOptions('tts');
renderRateOptions();
renderFontOptions();
renderConfigMeta();
refreshMicButton();
loadVoiceConfig();

})();
