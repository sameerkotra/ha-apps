/* Voice calls (SPEC §15.12): the 📞 button, the ringing and call screens, and WebRTC between the two
   browsers. The sound goes straight between them; the app only passes their descriptions and network
   candidates along (api/calls…, and live `call` events). One call at a time. */
"use strict";

const GATHER_MS = 3000;          // wait this long for network candidates before sending the offer / answer
const CONNECT_MS = 30000;        // answered but not connected after this: give up
let call = null;                 // the call this page is in: {id, role, conversationId, peerId, peerName, pc, stream, …}
let incoming = null;             // a call ringing for me, shown but not answered: {id, conversationId, peerId, peerName}

function callsOn() { return !!(state.me && state.me.app && state.me.app.callsEnabled); }
function canCallIn(c) { return callsOn() && !!c && c.kind === "direct" && !c.readOnly; }
function micPossible() { return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.RTCPeerConnection); }
function noMicDialog() {
  openModal("Voice calls", h("p", null, "Calls need the microphone, and this browser can't use it here. It needs a secure (https) connection and permission — inside the Home Assistant app it may be blocked. Try Home Assistant in your phone's browser, or on a computer."));
}
const MIC_KEY = "hchat.callMic", OUT_KEY = "hchat.callOut";
function getMic(deviceId) {
  const want = deviceId || lsGet(MIC_KEY);
  const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
  if (want) audio.deviceId = deviceId ? { exact: deviceId } : { ideal: want };
  return navigator.mediaDevices.getUserMedia({ audio });
}

// ---------- tones (made in the browser, no sound files) ----------
let toneCtx = null, toneTimer = null;
function beep(freq, ms, at = 0, gain = 0.12) {
  try {
    toneCtx = toneCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (toneCtx.state === "suspended") toneCtx.resume().catch(() => {});
    const o = toneCtx.createOscillator(), g = toneCtx.createGain();
    o.frequency.value = freq; g.gain.value = gain;
    o.connect(g); g.connect(toneCtx.destination);
    const t = toneCtx.currentTime + at / 1000;
    o.start(t); o.stop(t + ms / 1000);
  } catch (e) { /* no sound here */ }
}
const TONES = {
  ring: { every: 3000, play: () => { beep(660, 400); beep(880, 400, 500); if (navigator.vibrate) navigator.vibrate([400, 200, 400]); } },
  back: { every: 4000, play: () => beep(425, 1000, 0, 0.06) },
  busy: { every: 700, times: 4, play: () => beep(425, 350, 0, 0.08) },
};
function startTone(name) {
  stopTone();
  const t = TONES[name];
  let n = 0;
  const go = () => { t.play(); n += 1; if (t.times && n >= t.times) stopTone(); };
  go();
  toneTimer = setInterval(go, t.every);
}
function stopTone() { if (toneTimer) { clearInterval(toneTimer); toneTimer = null; } if (navigator.vibrate) navigator.vibrate(0); }
function closeTones() { stopTone(); if (toneCtx) { toneCtx.close().catch(() => {}); toneCtx = null; } }

// ---------- the other person's sound ----------
// Phones (and the Home Assistant app) only let a page play sound that a tap started. The call's <audio>
// is therefore made and started in the tap on 📞 or Answer, and the other person's sound goes into that
// same element when it arrives; if it's still refused, the screen offers "Tap to hear".
let callAudio = null;
function unlockAudio() {
  if (!callAudio) {
    callAudio = h("audio", { autoplay: true, playsinline: true, class: "call-audio" });
    callAudio.setAttribute("playsinline", "");
    document.body.appendChild(callAudio);
  }
  try { callAudio.srcObject = new MediaStream(); } catch (e) { /* old browser */ }
  callAudio.muted = false;
  const p = callAudio.play();
  if (p && p.catch) p.catch(() => {});
}
function playRemote(stream) {
  if (!call) return;
  call.remote = stream;
  if (!callAudio) unlockAudio();
  callAudio.srcObject = stream;
  applyOutput();
  const p = callAudio.play();
  if (p && p.then) p.then(() => { if (call) { call.blocked = false; renderCallButtons(); } })
    .catch(() => { if (call) { call.blocked = true; renderCallButtons(); setCallHint("Your browser held back the sound — tap 🔈 to hear " + call.peerName + "."); } });
}
function tapToHear() {
  if (!call || !callAudio) return;
  callAudio.play().then(() => { call.blocked = false; renderCallButtons(); setCallHint(""); }).catch(() => {});
}

// ---------- sound output and microphone ----------
const CAN_PICK_OUTPUT = typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype;
async function audioDevices(kind) {
  try { return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === kind); } catch (e) { return []; }
}
function applyOutput() {
  const id = call && call.outputId ? call.outputId : lsGet(OUT_KEY);
  if (CAN_PICK_OUTPUT && callAudio && id) callAudio.setSinkId(id).catch(() => {});
}
async function setOutput(id) {
  if (!call) return;
  call.outputId = id;
  lsSet(OUT_KEY, id || "");
  if (CAN_PICK_OUTPUT && callAudio) { try { await callAudio.setSinkId(id || ""); } catch (e) { toast("Couldn't switch to that output.", { error: true }); } }
}
async function switchMic(deviceId) {
  if (!call || !call.stream) return;
  let fresh;
  try { fresh = await getMic(deviceId); } catch (e) { toast("Couldn't use that microphone.", { error: true }); return; }
  if (!call) { fresh.getTracks().forEach((t) => t.stop()); return; }
  const track = fresh.getAudioTracks()[0];
  track.enabled = !call.muted;
  const sender = call.pc && call.pc.getSenders().find((x) => x.track && x.track.kind === "audio");
  if (sender) await sender.replaceTrack(track);
  call.stream.getTracks().forEach((t) => t.stop());
  call.stream = fresh;
  lsSet(MIC_KEY, deviceId);
  call.quietMic = 0;
  setCallHint("");
}
async function audioPanel() {
  if (!call) return;
  const box = $("#callPanel");
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  const outs = CAN_PICK_OUTPUT ? await audioDevices("audiooutput") : [];
  const opt = (d, i, word) => h("option", { value: d.deviceId }, d.label || `${word} ${i + 1}`);
  const outNow = call.outputId || lsGet(OUT_KEY) || "default";
  const outSel = h("select", { "aria-label": "Sound output", onchange: (e) => setOutput(e.target.value) }, outs.map((d, i) => opt(d, i, "Output")));
  outSel.value = outs.some((d) => d.deviceId === outNow) ? outNow : (outs[0] || {}).deviceId || "";
  mount(box,
    outs.length ? field("Sound comes out of", outSel)
      : h("p", { class: "hint" }, "This browser plays the call through the phone's current output. Use your phone's volume buttons, or its sound or Bluetooth menu, to change it."));
  box.hidden = false;
}

// ---------- is sound flowing? (levels from the connection's own statistics) ----------
const SILENT = 0.002;
async function watchSound() {
  const c = call;
  if (!c || !c.pc) return;
  let stats;
  try { stats = await c.pc.getStats(); } catch (e) { return; }
  if (call !== c) return;
  let inPackets = null, inLevel = null, micLevel = null;
  stats.forEach((x) => {
    if (x.type === "inbound-rtp" && x.kind === "audio") { inPackets = x.packetsReceived; if (typeof x.audioLevel === "number") inLevel = x.audioLevel; }
    if (x.type === "media-source" && x.kind === "audio" && typeof x.audioLevel === "number") micLevel = x.audioLevel;
  });
  meter("#meterThem", inLevel);
  meter("#meterMe", c.muted ? 0 : micLevel);
  const secs = (Date.now() - c.connectedAt) / 1000;
  const arriving = inPackets != null && inPackets > (c.lastPackets || 0);
  c.lastPackets = inPackets || 0;
  c.noPackets = arriving ? 0 : (c.noPackets || 0) + 1;
  c.quietThem = inLevel != null && inLevel < SILENT ? (c.quietThem || 0) + 1 : 0;
  c.quietMic = !c.muted && micLevel != null && micLevel < SILENT ? (c.quietMic || 0) + 1 : 0;
  let hint = "";
  if (c.blocked) hint = "Your browser held back the sound — tap 🔈 to hear " + c.peerName + ".";
  else if (secs > 5 && c.noPackets >= 5) hint = "No sound is arriving from " + c.peerName + ". The connection may be blocked one way — try again, or both on the same Wi-Fi.";
  else if (c.peerMuted) hint = c.peerName + " has muted their microphone.";
  else if (secs > 5 && c.quietThem >= 6) hint = c.peerName + "'s microphone seems silent — it may be muted or blocked on their phone.";
  else if (secs > 5 && c.quietMic >= 6) hint = "Your microphone seems silent. Check it isn't muted or used by another app, or pick another in ⚙.";
  setCallHint(hint);
}
function meter(sel, level) {
  const el = $(sel);
  if (!el) return;
  el.hidden = level == null;
  const bar = el.querySelector("span");
  if (bar) bar.style.width = Math.min(100, Math.round(Math.sqrt(level || 0) * 140)) + "%";
}
function setCallHint(text) { const el = $("#callHint"); if (el && el.textContent !== text) el.textContent = text; }

// ---------- telling the other side about mute (a small data channel next to the sound) ----------
function useControl(ch) {
  if (!call) return;
  call.ctl = ch;
  ch.onopen = () => sendControl();
  ch.onmessage = (e) => {
    let d = null; try { d = JSON.parse(e.data); } catch (x) { return; }
    if (call && d && typeof d.muted === "boolean") { call.peerMuted = d.muted; }
  };
}
function sendControl() { if (call && call.ctl && call.ctl.readyState === "open") call.ctl.send(JSON.stringify({ muted: !!call.muted })); }

// ---------- the screen ----------
function callScreen(peerId, peerName, status, buttons) {
  let el = $("#callScreen");
  if (!el) { el = h("div", { class: "call-screen", id: "callScreen", role: "dialog", "aria-modal": "true", "aria-label": "Call" }); document.body.appendChild(el); }
  const inCall = !!(call && call.peerId === peerId && buttons.length && !incoming);
  mount(el, h("div", { class: "call-box" },
    avatar(peerName, peerId, { big: true, noDot: true }),
    h("div", { class: "call-name" }, peerName),
    h("div", { class: "call-status", id: "callStatus", role: "status" }, status),
    inCall ? h("div", { class: "call-meters" },
      h("div", { class: "call-meter", id: "meterMe", hidden: true, title: "Your microphone" }, h("b", null, "You"), h("i", null, h("span"))),
      h("div", { class: "call-meter", id: "meterThem", hidden: true, title: peerName + "'s sound" }, h("b", null, peerName), h("i", null, h("span")))) : null,
    h("div", { class: "call-hint", id: "callHint", role: "status" }),
    h("div", { class: "call-actions", id: "callActions" }, buttons),
    inCall ? h("div", { class: "call-panel", id: "callPanel", hidden: true }) : null));
  const first = el.querySelector("button.answer") || el.querySelector("button.hangup");
  if (first && FINE_POINTER) first.focus();
}
function setCallStatus(text) { const s = $("#callStatus"); if (s) s.textContent = text; }
function closeCallScreen() { const el = $("#callScreen"); if (el) el.remove(); }
// a microphone, with a line across it when muted (no emoji shows that)
function micIcon(off) {
  const NS = "http://www.w3.org/2000/svg";
  const el = (name, attrs) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const svg = el("svg", { viewBox: "0 0 24 24", width: "26", height: "26", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "mic-icon" });
  svg.appendChild(el("rect", { x: "9", y: "2", width: "6", height: "12", rx: "3" }));
  svg.appendChild(el("path", { d: "M5 10v1a7 7 0 0 0 14 0v-1" }));
  svg.appendChild(el("path", { d: "M12 18v4M8 22h8" }));
  if (off) svg.appendChild(el("path", { d: "M3 3l18 18" }));
  return svg;
}
function callBtn(label, cls, run, title) { return h("button", { class: "call-btn " + cls, type: "button", "aria-label": title || label, title: title || label, onclick: run }, label); }
function inCallButtons() {
  return [
    callBtn(micIcon(!!(call && call.muted)), "mute" + (call && call.muted ? " on" : ""), toggleMute, call && call.muted ? "Unmute" : "Mute"),
    callBtn("⚙", "devices", audioPanel, "Sound output"),
    call && call.blocked ? callBtn("🔈", "hear on", tapToHear, "Tap to hear") : null,
    callBtn("📞", "hangup", () => hangUp(), "Hang up"),
  ].filter(Boolean);
}
function renderCallButtons() { const el = $("#callActions"); if (el && call) mount(el, inCallButtons()); }
function showCallScreen(status) { if (call) callScreen(call.peerId, call.peerName, status, inCallButtons()); }
function toggleMute() {
  if (!call || !call.stream) return;
  call.muted = !call.muted;
  call.stream.getAudioTracks().forEach((t) => { t.enabled = !call.muted; });
  sendControl();
  renderCallButtons();
}

// ---------- WebRTC ----------
function newPeer(iceServers) {
  const pc = new RTCPeerConnection({ iceServers: iceServers || [] });
  call.stream.getTracks().forEach((t) => pc.addTrack(t, call.stream));
  pc.ontrack = (e) => { if (call && call.pc === pc) playRemote(e.streams[0] || new MediaStream([e.track])); };
  pc.ondatachannel = (e) => { if (call && call.pc === pc) useControl(e.channel); };
  pc.onicecandidate = (e) => {
    // candidates found before the description went are inside it; later ones are passed on
    if (e.candidate && call && call.pc === pc && call.descSent && call.id) {
      api(`api/calls/${call.id}/candidate`, { method: "POST", body: { candidate: e.candidate.toJSON() } }).catch(() => {});
    }
  };
  const changed = () => {
    if (!call || call.pc !== pc) return;
    const s = pc.connectionState || pc.iceConnectionState;
    if ((s === "connected" || s === "completed") && !call.connectedAt) onConnected();
    else if (s === "failed") hangUp("failed");
  };
  pc.onconnectionstatechange = changed;
  pc.oniceconnectionstatechange = changed;
  return pc;
}
function gathered(pc) {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") { resolve(); return; }
    const done = () => { if (pc.iceGatheringState === "complete") { pc.removeEventListener("icegatheringstatechange", done); resolve(); } };
    pc.addEventListener("icegatheringstatechange", done);
    setTimeout(resolve, GATHER_MS);
  });
}
function addCandidates(list) {
  if (!call || !call.pc) return;
  for (const c of list || []) call.pc.addIceCandidate(c).catch(() => {});
}
function onConnected() {
  closeTones();
  call.connectedAt = Date.now();
  clearTimeout(call.connectTimer);
  const tick = () => { if (call && call.connectedAt) { setCallStatus(fmtDuration((Date.now() - call.connectedAt) / 1000)); watchSound(); } };
  tick();
  call.clock = setInterval(tick, 1000);
  keepAwake();
}
function waitForConnection() {
  call.connectTimer = setTimeout(() => { if (call && !call.connectedAt) hangUp("failed"); }, CONNECT_MS);
}
async function keepAwake() {
  try { if (navigator.wakeLock && call) call.wake = await navigator.wakeLock.request("screen"); } catch (e) { /* not here */ }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && call && call.connectedAt) keepAwake(); });

// ---------- calling someone ----------
async function startCall(conv) {
  if (call || incoming) { toast("You're already in a call."); return; }
  if (!micPossible()) { noMicDialog(); return; }
  unlockAudio();                          // in the tap: phones then allow the other person's sound
  let stream;
  try { stream = await getMic(); } catch (e) { toast("Microphone permission was refused.", { error: true }); return; }
  if (call) { stream.getTracks().forEach((t) => t.stop()); return; }
  call = { id: null, role: "caller", conversationId: conv.id, peerId: conv.otherUserId, peerName: conv.name, stream };
  const mine = call;
  showCallScreen("Calling…");
  let r;
  try { r = await api("api/calls", { method: "POST", body: { conversationId: conv.id } }); }
  catch (e) { if (call === mine) finish(e.message, /another call/.test(e.message) ? "busy" : null); return; }
  if (call !== mine) { api(`api/calls/${r.id}/end`, { method: "POST", body: {} }).catch(() => {}); return; }   // hung up meanwhile
  call.id = r.id;
  try {
    call.pc = newPeer(r.iceServers);
    useControl(call.pc.createDataChannel("hchat"));
    await call.pc.setLocalDescription(await call.pc.createOffer());
    await gathered(call.pc);
    if (call !== mine) return;
    await api(`api/calls/${call.id}/offer`, { method: "POST", body: { sdp: call.pc.localDescription.sdp } });
    call.descSent = true;
    if (!call.answered) { setCallStatus("Ringing…"); startTone("back"); }
  } catch (e) { if (call === mine) { fail(e); hangUp("failed"); } }
  watchWithoutLive();
}
async function applyAnswer(sdp, candidates) {
  if (!call || call.role !== "caller" || call.answered || !call.pc) return;
  call.answered = true;
  stopTone();
  setCallStatus("Connecting…");
  try { await call.pc.setRemoteDescription({ type: "answer", sdp }); addCandidates(candidates); waitForConnection(); }
  catch (e) { hangUp("failed"); }
}

// ---------- someone is calling ----------
function showIncoming(d) {
  if (incoming || (call && call.id === d.id)) return;
  if (call) return;                       // the server says busy before ringing; nothing to show
  incoming = { id: d.id, conversationId: d.conversationId, peerId: d.peerId, peerName: d.peerName || nameOf(d.peerId) };
  callScreen(incoming.peerId, incoming.peerName, "📞 Incoming call", [
    callBtn("✕", "decline", () => declineCall(), "Decline"),
    callBtn("📞", "answer", () => answerCall(), "Answer"),
  ]);
  startTone("ring");
  clearTimeout(incoming.timer);
  incoming.timer = setTimeout(() => dismissIncoming(), ((d.ringSeconds || d.ringLeft || state.me.app.callRingSeconds || 30) + 5) * 1000);
}
function dismissIncoming(text) {
  if (!incoming) return;
  clearTimeout(incoming.timer);
  incoming = null;
  stopTone();
  if (!call) closeCallScreen();
  if (text) toast(text);
}
async function declineCall() {
  const r = incoming;
  dismissIncoming();
  if (r) api(`api/calls/${r.id}/decline`, { method: "POST" }).catch(() => {});
}
async function answerCall() {
  const r = incoming;
  if (!r) return;
  unlockAudio();                          // in the tap: phones then allow the other person's sound
  clearTimeout(r.timer);
  incoming = null;
  closeTones();
  const noMic = () => {
    api(`api/calls/${r.id}/end`, { method: "POST", body: { reason: "no_microphone" } }).catch(() => {});
    closeCallScreen();
    noMicDialog();
  };
  if (!micPossible()) { noMic(); return; }
  let stream;
  try { stream = await getMic(); } catch (e) { noMic(); return; }
  call = { id: r.id, role: "callee", conversationId: r.conversationId, peerId: r.peerId, peerName: r.peerName, stream };
  const mine = call;
  showCallScreen("Connecting…");
  try {
    const cur = (await api("api/calls/current")).call;
    if (!cur || cur.id !== r.id || cur.state !== "ringing") { finish("The call has ended."); return; }
    call.pc = newPeer(cur.iceServers);
    await call.pc.setRemoteDescription({ type: "offer", sdp: cur.offer });
    addCandidates(cur.candidates);
    await call.pc.setLocalDescription(await call.pc.createAnswer());
    await gathered(call.pc);
    if (call !== mine) return;
    await api(`api/calls/${call.id}/answer`, { method: "POST", body: { sdp: call.pc.localDescription.sdp } });
    call.descSent = true;
    waitForConnection();
  } catch (e) { if (call === mine) { fail(e); hangUp("failed"); } }
  watchWithoutLive();
}

// ---------- ending ----------
function hangUp(reason) {
  if (!call) return;
  if (call.id) api(`api/calls/${call.id}/end`, { method: "POST", body: reason ? { reason } : {} }).catch(() => {});
  finish(reason === "failed" ? "Couldn't connect. For now calls work when both phones are on the home network." : "Call ended");
}
function finish(text, tone) {
  const c = call;
  call = null;
  if (!c) return;
  stopTone();
  clearInterval(c.clock); clearTimeout(c.connectTimer); clearInterval(c.poll);
  if (c.pc) { try { c.pc.close(); } catch (e) { /* closed */ } }
  if (c.stream) c.stream.getTracks().forEach((t) => t.stop());
  if (callAudio) { callAudio.srcObject = null; callAudio.muted = false; }
  if (c.wake) c.wake.release().catch(() => {});
  if (tone) startTone(tone);
  callScreen(c.peerId, c.peerName, text || "Call ended", []);
  setTimeout(() => { if (!call && !incoming) closeCallScreen(); }, 2000);
}
function endedText(d) {
  if (d.reason === "no_microphone") return "They couldn't answer here (no microphone).";
  if (d.outcome === "declined") return call && call.role === "caller" ? "Declined" : "Call ended";
  if (d.outcome === "missed") return call && call.role === "caller" ? "No answer" : "Call ended";
  if (d.outcome === "failed") return "Couldn't connect";
  return "Call ended";
}

// ---------- live `call` events, and the call when the page opens ----------
function onCallEvent(d) {
  if (d.state === "ringing" && d.peerId) { showIncoming(d); return; }
  if (d.state === "answered") {
    if (call && call.id === d.id && call.role === "caller" && d.sdp) applyAnswer(d.sdp, d.candidates);
    else if (incoming && incoming.id === d.id) dismissIncoming("Answered on another device");
    return;
  }
  if (d.state === "candidate") { if (call && call.id === d.id) addCandidates([d.candidate]); return; }
  if (d.state === "ended") {
    if (call && call.id === d.id) { if (d.by !== state.me.id) finish(endedText(d)); }
    else if (incoming && incoming.id === d.id) dismissIncoming(d.outcome === "missed" ? `Missed call from ${incoming.peerName}` : null);
  }
}
async function checkCurrentCall() {
  if (!state.me || state.me.disabled) return;
  let cur;
  try { cur = (await api("api/calls/current")).call; } catch (e) { return; }
  if (!cur) {
    if (call && call.id && call.descSent) finish("Call ended");
    if (incoming) dismissIncoming();
    return;
  }
  if (cur.role === "callee" && cur.state === "ringing" && !call) showIncoming(cur);
  else if (call && call.id === cur.id && cur.role === "caller" && cur.state === "active" && cur.answer) applyAnswer(cur.answer, []);
}
// while live updates are down, the call is followed by asking every second
function watchWithoutLive() {
  if (!call || call.poll) return;
  const mine = call;
  call.poll = setInterval(() => { if (call !== mine) return; if (!state.sse) checkCurrentCall(); }, 1000);
}

// ---------- call notes in the chat ----------
function callMissedByMe(m) { return !!(m.call && m.call.calleeId === state.me.id && (m.call.outcome === "missed" || m.call.outcome === "busy")); }
function callNoteText(m) {
  const c = m.call;
  if (!c) return "📞 Call";
  const out = c.callerId === state.me.id;
  const len = c.seconds == null ? "" : " · " + (c.seconds >= 60 ? Math.floor(c.seconds / 60) + " min" : c.seconds + " s");
  switch (c.outcome) {
    case "answered": return (out ? "📞 Outgoing call" : "📞 Incoming call") + len;
    case "missed": return out ? "📞 No answer" : "📞 Missed call";
    case "busy": return out ? "📞 Busy" : "📞 Missed call";
    case "declined": return out ? "📞 Declined" : "📞 You declined a call";
    default: return "📞 Call couldn't connect";
  }
}
function callNoteEl(m) {
  const c = convById(m.conversationId) || state.detail;
  return h("div", { class: "sys call-note" + (callMissedByMe(m) ? " missed" : ""), id: "m" + m.id },
    h("span", null, callNoteText(m), " · ", fmtTime(m.createdAt),
      canCallIn(c) && !m.deleted ? h("button", { class: "link-btn call-back", type: "button", onclick: () => startCall(c) }, "Call back") : null));
}
