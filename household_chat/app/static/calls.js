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
function getMic() { return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }

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

// ---------- the screen ----------
function callScreen(peerId, peerName, status, buttons) {
  let el = $("#callScreen");
  if (!el) { el = h("div", { class: "call-screen", id: "callScreen", role: "dialog", "aria-modal": "true", "aria-label": "Call" }); document.body.appendChild(el); }
  mount(el, h("div", { class: "call-box" },
    avatar(peerName, peerId, { big: true, noDot: true }),
    h("div", { class: "call-name" }, peerName),
    h("div", { class: "call-status", id: "callStatus", role: "status" }, status),
    h("div", { class: "call-actions" }, buttons)));
  const first = el.querySelector("button.answer") || el.querySelector("button.hangup");
  if (first && FINE_POINTER) first.focus();
}
function setCallStatus(text) { const s = $("#callStatus"); if (s) s.textContent = text; }
function closeCallScreen() { const el = $("#callScreen"); if (el) el.remove(); }
function callBtn(label, cls, run, title) { return h("button", { class: "call-btn " + cls, type: "button", "aria-label": title || label, title: title || label, onclick: run }, label); }
function inCallButtons() {
  return [
    callBtn(call && call.muted ? "🔇" : "🎤", "mute" + (call && call.muted ? " on" : ""), toggleMute, call && call.muted ? "Unmute" : "Mute"),
    callBtn("📞", "hangup", () => hangUp(), "Hang up"),
  ];
}
function showCallScreen(status) { if (call) callScreen(call.peerId, call.peerName, status, inCallButtons()); }
function toggleMute() {
  if (!call || !call.stream) return;
  call.muted = !call.muted;
  call.stream.getAudioTracks().forEach((t) => { t.enabled = !call.muted; });
  const status = $("#callStatus") ? $("#callStatus").textContent : "";
  showCallScreen(status);
}

// ---------- WebRTC ----------
function newPeer(iceServers) {
  const pc = new RTCPeerConnection({ iceServers: iceServers || [] });
  call.stream.getTracks().forEach((t) => pc.addTrack(t, call.stream));
  pc.ontrack = (e) => {
    if (!call.audio) { call.audio = h("audio", { autoplay: true }); call.audio.hidden = true; document.body.appendChild(call.audio); }
    call.audio.srcObject = e.streams[0];
    call.audio.play().catch(() => {});
  };
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
  stopTone();
  call.connectedAt = Date.now();
  clearTimeout(call.connectTimer);
  const tick = () => { if (call && call.connectedAt) setCallStatus(fmtDuration((Date.now() - call.connectedAt) / 1000)); };
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
  clearTimeout(r.timer);
  incoming = null;
  stopTone();
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
  if (c.audio) { c.audio.srcObject = null; c.audio.remove(); }
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
