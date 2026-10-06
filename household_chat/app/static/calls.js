/* Voice and video calls (SPEC §15.12): the 📞 / 📹 buttons, the ringing and call screens, and WebRTC between
   the browsers. Each person in a call connects to each other (a mesh of up to four); the sound and picture go
   straight between them, and the app only passes offers, answers and network candidates along (api/calls…,
   and live `call` events). One call at a time. */
"use strict";

const CONNECT_MS = 30000;        // joined but not connected to anyone after this: give up
let call = null;                 // the call this page is in (see newCall)
let incoming = null;             // a call ringing for me, shown but not answered: {id, conversationId, kind, group, name, peerId, peerName}

function callsOn() { return !!(state.me && state.me.app && state.me.app.callsEnabled); }
function canCallIn(c) { return callsOn() && !!c && (c.kind === "direct" || c.kind === "group") && !c.readOnly; }
function micPossible() { return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.RTCPeerConnection); }
function noMicDialog() {
  openModal("Calls", h("p", null, "Calls need the microphone, and this browser can't use it here. It needs a secure (https) connection and permission — inside the Home Assistant app it may be blocked. Try Home Assistant in your phone's browser, or on a computer."));
}
const MIC_KEY = "hchat.callMic", OUT_KEY = "hchat.callOut";
function getMic(deviceId) {
  const want = deviceId || lsGet(MIC_KEY);
  const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
  if (want) audio.deviceId = deviceId ? { exact: deviceId } : { ideal: want };
  return navigator.mediaDevices.getUserMedia({ audio });
}
function getCamera(facing) {
  return navigator.mediaDevices.getUserMedia({ video: { facingMode: facing || "user", width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { max: 24 } } });
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

// ---------- the other people's sound ----------
// Phones (and the Home Assistant app) only let a page play sound that a tap started. Three <audio> elements
// (one per other person, at most) are made and started in the tap on 📞 or Answer, and each person's sound goes
// into one of them when it arrives; if playing is still refused, the screen offers "Tap to hear".
const audioPool = [];
let callAudio = null;            // the first of them (the output choice applies to all)
function unlockAudio() {
  while (audioPool.length < 3) {
    const a = h("audio", { autoplay: true, playsinline: true, class: "call-audio" });
    a.setAttribute("playsinline", "");
    document.body.appendChild(a);
    audioPool.push(a);
  }
  callAudio = audioPool[0];
  for (const a of audioPool) {
    try { a.srcObject = new MediaStream(); } catch (e) { /* old browser */ }
    a.muted = false;
    const p = a.play();
    if (p && p.catch) p.catch(() => {});
  }
}
function takeAudio() { return audioPool.find((a) => !a._peer) || null; }
function playRemote(peer) {
  if (!call) return;
  if (!peer.audio) { if (!audioPool.length) unlockAudio(); peer.audio = takeAudio(); if (peer.audio) peer.audio._peer = peer.id; }
  if (!peer.audio) return;
  peer.audio.srcObject = peer.stream;
  applyOutput();
  const p = peer.audio.play();
  if (p && p.then) p.then(() => { if (call) { peer.blocked = false; renderCallButtons(); } })
    .catch(() => { if (call) { peer.blocked = true; renderCallButtons(); setCallHint("Your browser held back the sound — tap 🔈 to hear " + peer.name + "."); } });
}
function tapToHear() {
  if (!call) return;
  for (const p of call.peers.values()) if (p.audio) p.audio.play().then(() => { p.blocked = false; renderCallButtons(); setCallHint(""); }).catch(() => {});
}

// ---------- sound output and microphone ----------
// whether a page may choose the output: the list is shown whatever the browser claims (some say no and still
// switch), and choosing tries it on the call's <audio>
const CAN_PICK_OUTPUT = typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype;
function canSetSink() { return !!(callAudio && typeof callAudio.setSinkId === "function"); }
async function audioDevices(kind) {
  try { return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === kind); } catch (e) { return []; }
}
function applyOutput() {
  const id = call && call.outputId ? call.outputId : lsGet(OUT_KEY);
  if (canSetSink() && id) for (const a of audioPool) a.setSinkId(id).catch(() => {});
}
async function setOutput(id) {
  if (!call) return;
  call.outputId = id;
  lsSet(OUT_KEY, id || "");
  if (!canSetSink()) { toast("This browser doesn't let the app switch the output.", { error: true }); return; }
  try { for (const a of audioPool) await a.setSinkId(id || ""); } catch (e) { toast("Couldn't switch to that output.", { error: true }); }
}
async function switchMic(deviceId) {
  if (!call || !call.stream) return;
  let fresh;
  try { fresh = await getMic(deviceId); } catch (e) { toast("Couldn't use that microphone.", { error: true }); return; }
  if (!call) { fresh.getTracks().forEach((t) => t.stop()); return; }
  const track = fresh.getAudioTracks()[0];
  track.enabled = !call.muted;
  for (const p of call.peers.values()) { const s = p.pc && p.pc.getSenders().find((x) => x.track && x.track.kind === "audio"); if (s) await s.replaceTrack(track); }
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
  // on Android the "microphones" are the phone's sound routes — Speakerphone, Earpiece, Bluetooth headset —
  // and picking one switches the whole call there; on a computer they're the microphones
  const [mics, outs] = await Promise.all([audioDevices("audioinput"), audioDevices("audiooutput")]);
  const micNow = call.stream && call.stream.getAudioTracks()[0] ? call.stream.getAudioTracks()[0].getSettings().deviceId : "";
  const opt = (d, i, word) => h("option", { value: d.deviceId }, d.label || `${word} ${i + 1}`);
  const micSel = h("select", { "aria-label": "Microphone", onchange: (e) => switchMic(e.target.value) }, mics.map((d, i) => opt(d, i, "Microphone")));
  micSel.value = micNow;
  const outNow = call.outputId || lsGet(OUT_KEY) || "default";
  const outSel = h("select", { "aria-label": "Sound output", onchange: (e) => setOutput(e.target.value) }, outs.map((d, i) => opt(d, i, "Output")));
  outSel.value = outs.some((d) => d.deviceId === outNow) ? outNow : (outs[0] || {}).deviceId || "";
  mount(box,
    field("Microphone", micSel, /Android/.test(navigator.userAgent) ? "On a phone this also switches the call to the speakerphone, earpiece or headset." : null),
    outs.length ? field("Sound comes out of", outSel) : null);
  box.hidden = false;
}

// ---------- is sound flowing? (levels from each connection's statistics) ----------
const SILENT = 0.002;
async function watchSound() {
  const c = call;
  if (!c) return;
  let micLevel = null;
  for (const peer of c.peers.values()) {
    if (!peer.pc) continue;
    let stats;
    try { stats = await peer.pc.getStats(); } catch (e) { continue; }
    if (call !== c) return;
    let inPackets = null, inLevel = null;
    stats.forEach((x) => {
      if (x.type === "inbound-rtp" && x.kind === "audio") { inPackets = x.packetsReceived; if (typeof x.audioLevel === "number") inLevel = x.audioLevel; }
      if (x.type === "media-source" && x.kind === "audio" && typeof x.audioLevel === "number") micLevel = x.audioLevel;
    });
    meter(`#meter-${peer.id}`, inLevel);
    const arriving = inPackets != null && inPackets > (peer.lastPackets || 0);
    peer.lastPackets = inPackets || 0;
    peer.noPackets = arriving ? 0 : (peer.noPackets || 0) + 1;
    peer.quiet = inLevel != null && inLevel < SILENT ? (peer.quiet || 0) + 1 : 0;
  }
  meter("#meterMe", c.muted ? 0 : micLevel);
  c.quietMic = !c.muted && micLevel != null && micLevel < SILENT ? (c.quietMic || 0) + 1 : 0;
  const secs = (Date.now() - c.connectedAt) / 1000;
  let hint = "";
  const blocked = [...c.peers.values()].find((p) => p.blocked);
  const silentPeer = [...c.peers.values()].find((p) => p.connected && secs > 5 && p.noPackets >= 5);
  const mutedPeer = [...c.peers.values()].find((p) => p.muted);
  const quietPeer = [...c.peers.values()].find((p) => p.connected && secs > 5 && p.quiet >= 6 && !p.muted);
  if (blocked) hint = "Your browser held back the sound — tap 🔈 to hear " + blocked.name + ".";
  else if (silentPeer) hint = "No sound is arriving from " + silentPeer.name + ". The connection may be blocked one way — try again, or both on the same Wi-Fi.";
  else if (mutedPeer) hint = mutedPeer.name + " has muted their microphone.";
  else if (quietPeer) hint = quietPeer.name + "'s microphone seems silent — it may be muted or blocked on their phone.";
  else if (secs > 5 && c.quietMic >= 6) hint = "Your microphone seems silent. Check it isn't muted or used by another app.";
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

// ---------- telling the others about mute and the camera (a small data channel next to the sound) ----------
function useControl(peer, ch) {
  peer.ctl = ch;
  ch.onopen = () => sendControl(peer);
  ch.onmessage = (e) => {
    let d = null; try { d = JSON.parse(e.data); } catch (x) { return; }
    if (!call || !d) return;
    if (typeof d.muted === "boolean") peer.muted = d.muted;
    if (typeof d.video === "boolean") { peer.videoOn = d.video; renderTiles(); }
  };
}
function sendControl(peer) {
  const msg = JSON.stringify({ muted: !!(call && call.muted), video: !!(call && call.cameraOn) });
  for (const p of peer ? [peer] : (call ? call.peers.values() : [])) if (p.ctl && p.ctl.readyState === "open") p.ctl.send(msg);
}

// ---------- the screen ----------
function callScreen(title, status, buttons, opts = {}) {
  let el = $("#callScreen");
  if (!el) { el = h("div", { class: "call-screen", id: "callScreen", role: "dialog", "aria-modal": "true", "aria-label": "Call" }); document.body.appendChild(el); }
  const inCall = !!(call && buttons.length && !incoming);
  const video = inCall && call.kind === "video" || (call && call.cameraOn) || opts.video;
  el.className = "call-screen" + (video ? " video" : "") + (inCall && call.group ? " group" : "");
  mount(el, h("div", { class: "call-box" },
    opts.avatar ? avatar(opts.avatar.name, opts.avatar.id, { big: true, noDot: true }) : null,
    h("div", { class: "call-name" }, title),
    h("div", { class: "call-status", id: "callStatus", role: "status" }, status),
    inCall ? h("div", { class: "call-stage", id: "callStage" }) : null,
    inCall ? h("div", { class: "call-meter me", id: "meterMe", hidden: true, title: "Your microphone" }, h("b", null, "You"), h("i", null, h("span"))) : null,
    h("div", { class: "call-hint", id: "callHint", role: "status" }),
    h("div", { class: "call-actions", id: "callActions" }, buttons),
    inCall ? h("div", { class: "call-panel", id: "callPanel", hidden: true }) : null));
  if (inCall) renderTiles();
  const first = el.querySelector("button.answer") || el.querySelector("button.hangup");
  if (first && FINE_POINTER) first.focus();
}
function setCallStatus(text) { const s = $("#callStatus"); if (s) s.textContent = text; }
function closeCallScreen() { const el = $("#callScreen"); if (el) el.remove(); }
// one tile per other person (and my own picture while the camera is on); a tile shows their video when it's
// on, else their photo, with a level bar and what's known about them
function renderTiles() {
  const stage = $("#callStage");
  if (!stage || !call) return;
  const tiles = [];
  for (const p of call.peers.values()) {
    const tile = h("div", { class: "call-tile" + (p.videoOn && p.videoTrack ? " has-video" : "") + (p.state === "ringing" ? " ringing" : ""), id: "tile-" + p.id });
    if (p.videoOn && p.videoTrack) {
      if (!p.video) { p.video = h("video", { autoplay: true, playsinline: true, class: "call-video" }); p.video.setAttribute("playsinline", ""); p.video.muted = true; }
      if (p.video.srcObject !== p.videoStream) p.video.srcObject = p.videoStream;
      p.video.play().catch(() => {});
      tile.appendChild(p.video);
    } else tile.appendChild(avatar(p.name, p.id, { big: true, noDot: true }));
    tile.appendChild(h("div", { class: "tile-name" }, p.name + (p.state === "ringing" ? " · ringing…" : p.muted ? " · muted" : !p.connected ? " · connecting…" : "")));
    tile.appendChild(h("div", { class: "call-meter", id: "meter-" + p.id, hidden: true, title: p.name + "'s sound" }, h("i", null, h("span"))));
    tiles.push(tile);
  }
  if (call.cameraOn && call.videoTrack) {
    if (!call.preview) { call.preview = h("video", { autoplay: true, playsinline: true, class: "call-video mine" }); call.preview.setAttribute("playsinline", ""); call.preview.muted = true; }
    const ms = new MediaStream([call.videoTrack]);
    if (!call.preview.srcObject || call.preview.srcObject.getVideoTracks()[0] !== call.videoTrack) call.preview.srcObject = ms;
    call.preview.play().catch(() => {});
    tiles.push(h("div", { class: "call-tile mine has-video" + (call.facing === "environment" ? " back" : "") }, call.preview, h("div", { class: "tile-name" }, "You")));
  }
  stage.className = "call-stage n" + Math.min(4, tiles.length);
  mount(stage, tiles);
}
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
function camIcon(off) {
  const NS = "http://www.w3.org/2000/svg";
  const el = (name, attrs) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const svg = el("svg", { viewBox: "0 0 24 24", width: "26", height: "26", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  svg.appendChild(el("rect", { x: "3", y: "6", width: "13", height: "12", rx: "2" }));
  svg.appendChild(el("path", { d: "M16 10l5-3v10l-5-3" }));
  if (off) svg.appendChild(el("path", { d: "M3 3l18 18" }));
  return svg;
}
function callBtn(label, cls, run, title) { return h("button", { class: "call-btn " + cls, type: "button", "aria-label": title || label, title: title || label, onclick: run }, label); }
function inCallButtons() {
  if (!call) return [];
  const blocked = [...call.peers.values()].some((p) => p.blocked);
  return [
    callBtn(micIcon(!!call.muted), "mute" + (call.muted ? " on" : ""), toggleMute, call.muted ? "Unmute" : "Mute"),
    callBtn(camIcon(!call.cameraOn), "camera" + (call.cameraOn ? " on" : ""), toggleCamera, call.cameraOn ? "Turn the camera off" : "Turn the camera on"),
    call.cameraOn && call.canFlip ? callBtn("🔄", "flip", flipCamera, "Switch camera") : null,
    callBtn("⚙", "devices", audioPanel, "Sound and microphone"),
    blocked ? callBtn("🔈", "hear on", tapToHear, "Tap to hear") : null,
    callBtn("📞", "hangup", () => hangUp(), "Hang up"),
  ].filter(Boolean);
}
function renderCallButtons() { const el = $("#callActions"); if (el && call) mount(el, inCallButtons()); }
function showCallScreen(status) {
  if (!call) return;
  callScreen(call.name, status, inCallButtons(), call.group ? {} : { avatar: { id: call.peerId, name: call.name } });
}
function toggleMute() {
  if (!call || !call.stream) return;
  call.muted = !call.muted;
  call.stream.getAudioTracks().forEach((t) => { t.enabled = !call.muted; });
  sendControl();
  renderCallButtons();
}
// the camera: a video slot is in every connection from the start, so turning it on or off never needs a new
// negotiation — the track is just put in (or taken out) with replaceTrack
async function toggleCamera() {
  if (!call) return;
  if (call.cameraOn) {
    call.cameraOn = false;
    for (const p of call.peers.values()) if (p.videoSender) p.videoSender.replaceTrack(null).catch(() => {});
    if (call.videoTrack) call.videoTrack.stop();
    call.videoTrack = null;
    if (call.preview) { call.preview.srcObject = null; call.preview = null; }
  } else {
    let s;
    try { s = await getCamera(call.facing); } catch (e) { toast("Couldn't use the camera (permission, or no camera here).", { error: true }); return; }
    if (!call) { s.getTracks().forEach((t) => t.stop()); return; }
    call.videoTrack = s.getVideoTracks()[0];
    call.cameraOn = true;
    for (const p of call.peers.values()) if (p.videoSender) p.videoSender.replaceTrack(call.videoTrack).catch(() => {});
    audioDevices("videoinput").then((cams) => { if (call) { call.canFlip = cams.length > 1; renderCallButtons(); } });
  }
  sendControl();
  showCallScreen($("#callStatus") ? $("#callStatus").textContent : "");
}
async function flipCamera() {
  if (!call || !call.cameraOn) return;
  const facing = call.facing === "environment" ? "user" : "environment";
  let s;
  try { s = await getCamera(facing); } catch (e) { toast("Couldn't switch camera.", { error: true }); return; }
  if (!call) { s.getTracks().forEach((t) => t.stop()); return; }
  const track = s.getVideoTracks()[0];
  for (const p of call.peers.values()) if (p.videoSender) p.videoSender.replaceTrack(track).catch(() => {});
  if (call.videoTrack) call.videoTrack.stop();
  call.videoTrack = track;
  call.facing = facing;
  renderTiles();
}

// ---------- WebRTC: one connection per other person ----------
function newCall(fields) {
  return Object.assign({ id: null, kind: "audio", group: false, conversationId: null, name: "", peerId: null, role: "caller",
    stream: null, videoTrack: null, cameraOn: false, facing: "user", canFlip: false, muted: false,
    peers: new Map(), seen: new Set(), connectedAt: null, iceServers: [] }, fields);
}
function addPeer(id, name, memberState) {
  if (!call) return null;
  let p = call.peers.get(id);
  if (!p) { p = { id, name, state: memberState || "joined", pc: null, stream: new MediaStream(), videoStream: new MediaStream(), pending: [] }; call.peers.set(id, p); }
  else if (memberState) p.state = memberState;
  return p;
}
function makePc(peer, offering) {
  if (peer.pc) return peer.pc;
  const pc = new RTCPeerConnection({ iceServers: call.iceServers || [] });
  peer.pc = pc;
  const audio = call.stream.getAudioTracks()[0];
  if (audio) pc.addTrack(audio, call.stream);
  // the video slot: the side that offers adds it; the side that answers takes the one in the offer (a browser
  // only reuses slots made by addTrack, so one added here would be left unused and the answer receive-only)
  if (offering) {
    const tr = pc.addTransceiver("video", { direction: "sendrecv" });
    peer.videoSender = tr.sender;
    if (call.cameraOn && call.videoTrack) tr.sender.replaceTrack(call.videoTrack).catch(() => {});
  }
  pc.ontrack = (e) => {
    if (!call || peer.pc !== pc) return;
    if (e.track.kind === "audio") { peer.stream.addTrack(e.track); playRemote(peer); }
    else { peer.videoTrack = e.track; peer.videoStream = e.streams[0] || new MediaStream([e.track]); e.track.onunmute = () => renderTiles(); renderTiles(); }
  };
  pc.ondatachannel = (e) => { if (call && peer.pc === pc) useControl(peer, e.channel); };
  pc.onicecandidate = (e) => { if (e.candidate && call && peer.pc === pc) signal(peer.id, "candidate", { candidate: e.candidate.toJSON() }); };
  const changed = () => {
    if (!call || peer.pc !== pc) return;
    const s = pc.connectionState || pc.iceConnectionState;
    if (s === "connected" || s === "completed") { peer.connected = true; if (!call.connectedAt) onConnected(); renderTiles(); }
    else if (s === "failed") { if (call.group) dropPeer(peer.id, "couldn't connect"); else hangUp("failed"); }
  };
  pc.onconnectionstatechange = changed;
  pc.oniceconnectionstatechange = changed;
  return pc;
}
function signal(to, type, data) {
  if (!call || !call.id) return Promise.resolve();
  return api(`api/calls/${call.id}/signal`, { method: "POST", body: { to, type, ...data } }).catch(() => {});
}
async function offerTo(peer) {
  const pc = makePc(peer, true);
  useControl(peer, pc.createDataChannel("hchat"));
  try {
    await pc.setLocalDescription(await pc.createOffer());
    await signal(peer.id, "offer", { sdp: pc.localDescription.sdp });
  } catch (e) { if (call && !call.group) hangUp("failed"); }
}
async function onSignal(sig) {
  if (!call || !sig || call.seen.has(sig.id)) return;
  call.seen.add(sig.id);
  const peer = addPeer(sig.from, nameOf(sig.from) === "Someone" ? (sig.fromName || "Someone") : nameOf(sig.from));
  const pc = makePc(peer);
  try {
    if (sig.type === "offer") {
      await pc.setRemoteDescription({ type: "offer", sdp: sig.sdp });
      const vt = pc.getTransceivers().find((x) => x.receiver && x.receiver.track && x.receiver.track.kind === "video");
      if (vt) {
        vt.direction = "sendrecv";
        peer.videoSender = vt.sender;
        if (call.cameraOn && call.videoTrack) await vt.sender.replaceTrack(call.videoTrack).catch(() => {});
      }
      await pc.setLocalDescription(await pc.createAnswer());
      await signal(peer.id, "answer", { sdp: pc.localDescription.sdp });
      flushCandidates(peer);
    } else if (sig.type === "answer") {
      await pc.setRemoteDescription({ type: "answer", sdp: sig.sdp });
      flushCandidates(peer);
    } else if (sig.type === "candidate") {
      if (pc.remoteDescription) await pc.addIceCandidate(sig.candidate).catch(() => {});
      else peer.pending.push(sig.candidate);
    }
  } catch (e) { if (!call.group) hangUp("failed"); }
}
function flushCandidates(peer) {
  const list = peer.pending; peer.pending = [];
  for (const c of list) peer.pc.addIceCandidate(c).catch(() => {});
}
function dropPeer(id, why) {
  if (!call) return;
  const p = call.peers.get(id);
  if (!p) return;
  if (p.pc) { try { p.pc.close(); } catch (e) { /* closed */ } }
  if (p.audio) { p.audio.srcObject = null; p.audio._peer = null; }
  call.peers.delete(id);
  renderTiles();
  if (why && call.group) toast(p.name + (why === "left" ? " left the call" : " " + why));
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
  clearTimeout(call.connectTimer);
  call.connectTimer = setTimeout(() => { if (call && !call.connectedAt) hangUp("failed"); }, CONNECT_MS);
}
async function keepAwake() {
  try { if (navigator.wakeLock && call) call.wake = await navigator.wakeLock.request("screen"); } catch (e) { /* not here */ }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && call && call.connectedAt) keepAwake(); });

// ---------- calling someone ----------
async function getMedia(video) {
  const stream = await getMic();
  let videoTrack = null;
  if (video) { try { videoTrack = (await getCamera("user")).getVideoTracks()[0]; } catch (e) { toast("No camera here — the call goes on with sound only.", { error: true }); } }
  return { stream, videoTrack };
}
async function startCall(conv, kind = "audio") {
  if (call || incoming) { toast("You're already in a call."); return; }
  if (!micPossible()) { noMicDialog(); return; }
  unlockAudio();                          // in the tap: phones then allow the other people's sound
  let media;
  try { media = await getMedia(kind === "video"); } catch (e) { toast("Microphone permission was refused.", { error: true }); return; }
  if (call) { media.stream.getTracks().forEach((t) => t.stop()); if (media.videoTrack) media.videoTrack.stop(); return; }
  call = newCall({ kind, group: conv.kind === "group", conversationId: conv.id, peerId: conv.otherUserId || null, name: conv.name,
    stream: media.stream, videoTrack: media.videoTrack, cameraOn: !!media.videoTrack });
  const mine = call;
  showCallScreen("Calling…");
  let r;
  try { r = await api("api/calls", { method: "POST", body: { conversationId: conv.id, kind } }); }
  catch (e) { if (call === mine) finish(e.message, /another call/.test(e.message) ? "busy" : null); return; }
  if (call !== mine) { api(`api/calls/${r.id}/end`, { method: "POST", body: {} }).catch(() => {}); return; }   // hung up meanwhile
  call.id = r.id; call.iceServers = r.iceServers || []; call.name = r.name || call.name;
  for (const m of r.members || []) if (m.id !== state.me.id) addPeer(m.id, m.name, m.state);
  setCallStatus("Ringing…");
  startTone("back");
  renderTiles();
  if (call.cameraOn) audioDevices("videoinput").then((cams) => { if (call) { call.canFlip = cams.length > 1; renderCallButtons(); } });
  watchWithoutLive();
}

// ---------- someone is calling ----------
function showIncoming(d) {
  if (incoming || (call && call.id === d.id)) return;
  if (call) return;                       // busy: the server never rings someone in a call
  incoming = { id: d.id, conversationId: d.conversationId, kind: d.kind || "audio", group: !!d.group, name: d.name || d.peerName,
    peerId: d.peerId, peerName: d.peerName || nameOf(d.peerId) };
  const what = (incoming.kind === "video" ? "📹 Incoming video call" : "📞 Incoming call") + (incoming.group ? " from " + incoming.peerName : "");
  callScreen(incoming.name, what, [
    callBtn("✕", "decline", () => declineCall(), "Decline"),
    callBtn(incoming.kind === "video" ? "📹" : "📞", "answer", () => answerCall(), "Answer"),
  ], { avatar: { id: incoming.group ? null : incoming.peerId, name: incoming.name }, video: incoming.kind === "video" });
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
  unlockAudio();                          // in the tap: phones then allow the other people's sound
  clearTimeout(r.timer);
  incoming = null;
  closeTones();
  const noMic = () => {
    api(`api/calls/${r.id}/end`, { method: "POST", body: { reason: "no_microphone" } }).catch(() => {});
    closeCallScreen();
    noMicDialog();
  };
  if (!micPossible()) { noMic(); return; }
  let media;
  try { media = await getMedia(r.kind === "video"); } catch (e) { noMic(); return; }
  call = newCall({ id: r.id, kind: r.kind, group: r.group, conversationId: r.conversationId, name: r.name, peerId: r.peerId, role: "callee",
    stream: media.stream, videoTrack: media.videoTrack, cameraOn: !!media.videoTrack });
  const mine = call;
  showCallScreen("Connecting…");
  let a;
  try { a = await api(`api/calls/${r.id}/answer`, { method: "POST" }); }
  catch (e) { if (call === mine) finish(e.message); return; }
  if (call !== mine) return;
  call.iceServers = a.iceServers || [];
  for (const m of a.members || []) if (m.id !== state.me.id) addPeer(m.id, m.name, m.state);
  renderTiles();
  // the newcomer offers to everyone already in
  for (const p of a.peers || []) offerTo(addPeer(p.id, p.name, "joined"));
  waitForConnection();
  if (call.cameraOn) audioDevices("videoinput").then((cams) => { if (call) { call.canFlip = cams.length > 1; renderCallButtons(); } });
  watchWithoutLive();
}

// ---------- ending ----------
function hangUp(reason) {
  if (!call) return;
  if (call.id) api(`api/calls/${call.id}/end`, { method: "POST", body: reason ? { reason } : {} }).catch(() => {});
  finish(reason === "failed" ? "Couldn't connect. Away from home, calls need the address lookup and a relay (App settings → Voice calls)." : "Call ended");
}
function finish(text, tone) {
  const c = call;
  call = null;
  if (!c) return;
  stopTone();
  clearInterval(c.clock); clearTimeout(c.connectTimer); clearInterval(c.poll);
  for (const p of c.peers.values()) { if (p.pc) { try { p.pc.close(); } catch (e) { /* closed */ } } if (p.audio) { p.audio.srcObject = null; p.audio._peer = null; } }
  if (c.stream) c.stream.getTracks().forEach((t) => t.stop());
  if (c.videoTrack) c.videoTrack.stop();
  for (const a of audioPool) { a.srcObject = null; a.muted = false; a._peer = null; }
  if (c.wake) c.wake.release().catch(() => {});
  if (tone) startTone(tone);
  callScreen(c.name, text || "Call ended", [], c.group ? {} : { avatar: { id: c.peerId, name: c.name } });
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
  if (d.state === "joined") { if (incoming && incoming.id === d.id) dismissIncoming("Answered on another device"); return; }
  if (d.state === "signal") { if (call && call.id === d.id) onSignal(d.signal); return; }
  if (d.state === "member") {
    if (!call || call.id !== d.id) return;
    for (const m of d.members || []) if (m.id !== state.me.id) { if (m.state === "joined" || m.state === "ringing") addPeer(m.id, m.name, m.state); else if (call.peers.has(m.id)) dropPeer(m.id, call.peers.get(m.id).state === "joined" ? "left" : null); }
    renderTiles();
    if (call.group && d.memberState === "joined" && d.userId !== state.me.id) toast(nameOf(d.userId) + " joined the call");
    return;
  }
  if (d.state === "ended") {
    if (call && call.id === d.id) { if (d.by !== state.me.id) finish(endedText(d)); }
    else if (incoming && incoming.id === d.id) dismissIncoming(d.outcome === "missed" || d.reason === "missed" ? `Missed call from ${incoming.peerName}` : null);
  }
}
async function checkCurrentCall() {
  if (!state.me || state.me.disabled) return;
  let cur;
  try { cur = (await api("api/calls/current")).call; } catch (e) { return; }
  if (!cur) {
    if (call && call.id) finish("Call ended");
    if (incoming) dismissIncoming();
    return;
  }
  if (cur.state === "ringing" && !call) { showIncoming(cur); return; }
  if (call && call.id === cur.id) {
    for (const m of cur.members || []) if (m.id !== state.me.id && (m.state === "joined" || m.state === "ringing")) addPeer(m.id, m.name, m.state);
    for (const s of cur.signals || []) onSignal(s);
  }
}
// while live updates are down, the call is followed by asking every second
function watchWithoutLive() {
  if (!call || call.poll) return;
  const mine = call;
  call.poll = setInterval(() => { if (call !== mine) return; if (!state.sse) checkCurrentCall(); }, 1000);
}

// ---------- deep links (§15.14): "/<page>/chat/<id>" opens that chat, "/<page>/call/<id>" the ringing screen ----------
// A notification opens the app's page with the route after it. Home Assistant hands the route to the page in a
// "home-assistant/properties" message (current versions) and the top page's address says it too (same origin).
const ROUTE_ID = /^[A-Za-z0-9_-]{1,64}$/;
function routeOf(path, page) {
  if (typeof path !== "string") return null;
  const p = page && path.startsWith(page + "/") ? path.slice(page.length) : path;
  const parts = p.split("?")[0].split("#")[0].split("/").filter(Boolean);
  if (parts.length === 2 && (parts[0] === "chat" || parts[0] === "call") && ROUTE_ID.test(parts[1])) return { kind: parts[0], id: parts[1] };
  return null;
}
function parentPath() { try { return window.parent !== window ? window.parent.location.pathname : null; } catch (e) { return null; } }
function followRoute(r) {
  if (!r) return false;
  if (r.kind === "chat") { if (convById(r.id)) openChat(r.id); else toast("That chat isn't here any more."); }
  else checkCurrentCall();                  // a ringing call shows its screen; an ended one is gone
  // the address goes back to the bare page, so a reload doesn't open it again
  const page = state.me && state.me.page;
  const pp = parentPath();
  if (page && pp && pp.startsWith(page + "/")) { try { window.parent.history.replaceState(window.parent.history.state, "", page); } catch (e) { /* not reachable */ } }
  return true;
}
function openFromRoute() {
  const page = state.me && state.me.page;
  let done = followRoute(routeOf(parentPath(), page)) || followRoute(routeOf(location.pathname, page));
  if (window.parent === window) return done;
  const until = Date.now() + 5000;
  window.addEventListener("message", (e) => {
    if (done || e.origin !== location.origin || e.source !== window.parent || Date.now() > until) return;
    const d = e.data;
    if (!d || d.type !== "home-assistant/properties" || !d.route || typeof d.route.path !== "string") return;
    const r = routeOf(d.route.path, page);
    if (r) done = followRoute(r);
  });
  try { window.parent.postMessage({ type: "home-assistant/subscribe-properties" }, location.origin); } catch (e) { /* older frames */ }
  return done;
}

// ---------- call notes in the chat ----------
function myCallState(m) { const me = m.call && (m.call.members || []).find((x) => x.id === state.me.id); return me ? me.state : null; }
function callMissedByMe(m) { const s = myCallState(m); return s === "missed" || s === "busy"; }
function callNoteText(m) {
  const c = m.call;
  if (!c) return "📞 Call";
  const icon = c.kind === "video" ? "📹" : "📞";
  const what = c.kind === "video" ? "video call" : "call";
  const out = c.callerId === state.me.id;
  const len = c.seconds == null ? "" : " · " + (c.seconds >= 60 ? Math.floor(c.seconds / 60) + " min" : c.seconds + " s");
  if (c.group) {
    const who = (c.members || []).filter((x) => x.state === "left").map((x) => x.id === state.me.id ? "you" : x.name);
    const mine = myCallState(m);
    switch (c.outcome) {
      case "answered": return `${icon} Group ${what}` + len + (who.length ? " · " + who.join(", ") : "");
      case "missed": return mine === "missed" || mine === "busy" ? `${icon} Missed group ${what}` : `${icon} Group ${what} · no answer`;
      case "declined": return out ? `${icon} Group ${what} · declined` : `${icon} Group ${what} · you declined`;
      default: return `${icon} Group ${what} couldn't connect`;
    }
  }
  switch (c.outcome) {
    case "answered": return (out ? `${icon} Outgoing ${what}` : `${icon} Incoming ${what}`) + len;
    case "missed": return out ? `${icon} No answer` : `${icon} Missed ${what}`;
    case "busy": return out ? `${icon} Busy` : `${icon} Missed ${what}`;
    case "declined": return out ? `${icon} Declined` : `${icon} You declined a ${what}`;
    default: return `${icon} ${what[0].toUpperCase() + what.slice(1)} couldn't connect`;
  }
}
function callNoteEl(m) {
  const c = convById(m.conversationId) || state.detail;
  return h("div", { class: "sys call-note" + (callMissedByMe(m) ? " missed" : ""), id: "m" + m.id },
    h("span", null, callNoteText(m), " · ", fmtTime(m.createdAt),
      canCallIn(c) && !m.deleted ? h("button", { class: "link-btn call-back", type: "button", onclick: () => startCall(c, m.call ? m.call.kind : "audio") }, "Call back") : null));
}
