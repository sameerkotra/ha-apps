/* Household Arcade — sound effects (window.ArcadeSound).

   Short tones made in the browser with Web Audio; no sound files. Off by default:
   nothing plays and no audio is created until setEnabled(true). The AudioContext
   is only created during (or after) a user gesture, as browsers require.
   Each look has its own "sound set" (wave shape and colour of the tone). */
(function (root) {
  "use strict";

  var enabled = false, ctx = null, master = null, last = {};

  // Sound sets per look: the oscillator wave, a loudness trim and an optional low-pass.
  var SETS = {
    modern: { wave: "triangle", gain: 1 },
    lcd: { wave: "square", gain: 0.45 },          // simple handheld beeps
    neon: { wave: "sawtooth", gain: 0.5, cutoff: 2600 },
    pixel: { wave: "square", gain: 0.5, cutoff: 5000 },
    paper: { wave: "sine", gain: 1.2 },
    contrast: { wave: "triangle", gain: 1 },
  };

  // Each sound: notes of [frequency Hz, duration s, optional slide-to Hz].
  var SOUNDS = {
    eat: [[660, 0.05], [990, 0.07]],
    bonus: [[784, 0.05], [988, 0.05], [1319, 0.09]],
    speed: [[880, 0.04], [1175, 0.06]],
    turn: [[520, 0.02]],
    bounce: [[440, 0.05]],
    wall: [[300, 0.03]],
    hit: [[560, 0.04]],
    "break": [[720, 0.05, 360]],
    powerup: [[523, 0.05], [659, 0.05], [784, 0.05], [1047, 0.09]],
    launch: [[392, 0.06, 784]],
    lose: [[330, 0.16, 140]],
    level: [[523, 0.07], [659, 0.07], [784, 0.07], [1047, 0.16]],
    row: [[660, 0.05], [880, 0.05], [1100, 0.1]],
    win: [[523, 0.08], [659, 0.08], [784, 0.08], [1047, 0.08], [1319, 0.2]],
    gameover: [[392, 0.14], [330, 0.14], [262, 0.3, 196]],
  };

  function canStartAudio() {
    var ua = root.navigator && root.navigator.userActivation;
    return ua ? !!ua.hasBeenActive : false;
  }
  function ensure(fromGesture) {
    if (!enabled) return null;
    if (!ctx) {
      if (!fromGesture && !canStartAudio()) return null;
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try {
        ctx = new AC();
        master = ctx.createGain();
        master.gain.value = 0.16;
        master.connect(ctx.destination);
      } catch (e) { ctx = null; master = null; return null; }
    }
    if (ctx.state === "suspended" && ctx.resume) { try { var p = ctx.resume(); if (p && p.catch) p.catch(function () {}); } catch (e) { /* ignore */ } }
    return ctx;
  }
  function onGesture() { if (enabled) ensure(true); }
  if (root.addEventListener) {
    ["pointerdown", "keydown", "touchend"].forEach(function (t) {
      root.addEventListener(t, onGesture, { capture: true, passive: true });
    });
  }

  function play(name, lookId) {
    if (!enabled) return false;
    var notes = SOUNDS[name];
    if (!notes) return false;
    var c = ensure(false);
    if (!c || c.state !== "running") return false;
    var t = c.currentTime;
    if (last[name] != null && t - last[name] < 0.04) return false; // no machine-gun repeats
    last[name] = t;
    var set = SETS[lookId] || SETS.modern;
    var out = master, filter = null;
    if (set.cutoff) {
      filter = c.createBiquadFilter(); filter.type = "lowpass"; filter.frequency.value = set.cutoff;
      filter.connect(master); out = filter;
    }
    var at = t + 0.005;
    for (var i = 0; i < notes.length; i++) {
      var n = notes[i], f = n[0], d = n[1];
      var osc = c.createOscillator(), env = c.createGain();
      osc.type = set.wave;
      osc.frequency.setValueAtTime(f, at);
      if (n[2]) osc.frequency.exponentialRampToValueAtTime(n[2], at + d);
      env.gain.setValueAtTime(0.0001, at);
      env.gain.exponentialRampToValueAtTime(set.gain, at + 0.006);
      env.gain.exponentialRampToValueAtTime(0.0001, at + d);
      osc.connect(env); env.connect(out);
      osc.start(at); osc.stop(at + d + 0.02);
      at += d * 0.92;
    }
    return true;
  }

  root.ArcadeSound = {
    SOUNDS: SOUNDS,
    SETS: SETS,
    names: function () { return Object.keys(SOUNDS); },
    /** Turn sound on or off (off by default). Call it from the speaker button's click so audio can start. */
    setEnabled: function (on) {
      enabled = !!on;
      if (enabled) ensure(false);
      else if (ctx && ctx.state === "running" && ctx.suspend) { try { var p = ctx.suspend(); if (p && p.catch) p.catch(function () {}); } catch (e) { /* ignore */ } }
    },
    isEnabled: function () { return enabled; },
    /** Play a named effect in the given look's sound set; silent when disabled. */
    play: play,
    get context() { return ctx; },
  };
})(typeof window !== "undefined" ? window : this);
