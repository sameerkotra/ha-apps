// Test helpers: load the browser game files into a sandbox with a small fake DOM
// (canvas 2D contexts that record calls), and drive requestAnimationFrame by hand.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const GAMES_DIR = path.join(__dirname, "..", "..", "app", "static", "games");
const LOAD_ORDER = ["kit.js", "sound.js", "registry.js", "snake-logic.js", "snake.js", "brick-logic.js", "brick.js",
  "blocks-logic.js", "blocks.js", "duel-logic.js", "duel.js", "racer-logic.js", "racer.js", "flap-logic.js", "flap.js",
  "mines-logic.js", "mines.js", "merge-logic.js", "merge.js", "colours-logic.js", "colours.js", "cards-logic.js", "cards.js", "mole-logic.js", "mole.js", "numbers-logic.js", "numbers.js",
  "tanks-logic.js", "tanks.js", "invaders-logic.js", "invaders.js", "rocks-logic.js", "rocks.js", "hop-logic.js", "hop.js", "snakeduel-logic.js", "snakeduel.js"];

function fakeContext(canvas, counts) {
  const state = { font: "10px sans-serif" };
  const methods = {
    getImageData(x, y, w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; },
    createImageData(w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; },
    measureText(s) { return { width: String(s).length * 6 }; },
  };
  return new Proxy(state, {
    get(target, prop) {
      if (prop === "canvas") return canvas;
      if (prop in methods) return (...a) => { counts[prop] = (counts[prop] || 0) + 1; return methods[prop](...a); };
      if (prop in target) return target[prop];
      if (typeof prop === "string" && /^[a-z]/.test(prop)) {
        return (...args) => {
          counts[prop] = (counts[prop] || 0) + 1;
          for (const v of args) if (typeof v === "number" && !Number.isFinite(v)) counts.nonFinite = (counts.nonFinite || 0) + 1;
        };
      }
      return undefined;
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}

function fakeCanvas(counts, cssW = 390, cssH = 487) {
  const c = {
    width: 300, height: 150, clientWidth: cssW, clientHeight: cssH, ownerDocument: null,
    _ctx: null,
    getContext() { if (!this._ctx) this._ctx = fakeContext(this, counts); return this._ctx; },
  };
  return c;
}

function makeSandbox(opts = {}) {
  const counts = {};
  const listeners = {};
  let rafQueue = [];
  let rafId = 0;
  let clock = 0;
  const doc = {
    hidden: false,
    documentElement: { getAttribute() { return null; } },
    createElement(tag) { if (tag !== "canvas") throw new Error("unexpected element " + tag); const c = fakeCanvas(counts); c.ownerDocument = doc; return c; },
    addEventListener(t, fn) { (listeners["doc:" + t] = listeners["doc:" + t] || []).push(fn); },
    removeEventListener(t, fn) { const l = listeners["doc:" + t] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); },
  };
  const audio = { created: 0, oscillators: 0 };
  class FakeAudioContext {
    constructor() { audio.created++; this.state = "running"; this.currentTime = 0; this.destination = {}; }
    createGain() { return { gain: { value: 1, setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
    createOscillator() { audio.oscillators++; return { type: "", frequency: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {}, start() {}, stop() {} }; }
    createBiquadFilter() { return { type: "", frequency: { value: 0 }, connect() {} }; }
    resume() { this.state = "running"; return Promise.resolve(); }
    suspend() { this.state = "suspended"; return Promise.resolve(); }
  }
  const win = {
    document: doc,
    devicePixelRatio: opts.dpr || 3,
    navigator: { userActivation: { hasBeenActive: true, isActive: true } },
    performance: { now: () => clock },
    console,
    Math, Date, JSON, Array, Object, Number, String, Uint8Array, Uint32Array, Uint8ClampedArray, Infinity, NaN, isFinite, parseInt, Error, Promise,
    setTimeout, clearTimeout,
    requestAnimationFrame(cb) { rafQueue.push({ id: ++rafId, cb }); return rafId; },
    cancelAnimationFrame(id) { rafQueue = rafQueue.filter((r) => r.id !== id); },
    matchMedia(q) { return { matches: !!(opts.media && opts.media[q]), addEventListener() {}, removeEventListener() {} }; },
    getComputedStyle() { return { colorScheme: opts.colorScheme || "" }; },
    addEventListener(t, fn) { (listeners["win:" + t] = listeners["win:" + t] || []).push(fn); },
    removeEventListener(t, fn) { const l = listeners["win:" + t] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); },
    AudioContext: FakeAudioContext,
  };
  win.window = win; win.self = win; win.globalThis = win;
  const context = vm.createContext(win);
  for (const f of LOAD_ORDER.concat((opts.extra || []).filter((f) => !LOAD_ORDER.includes(f)))) {
    vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, f), "utf8"), context, { filename: f });
  }
  return {
    win, doc, counts, audio, listeners,
    canvas(cssW, cssH) { const c = fakeCanvas(counts, cssW, cssH); c.ownerDocument = doc; return c; },
    /** Run n animation frames, each `ms` apart. */
    frames(n, ms = 1000 / 60) {
      for (let i = 0; i < n; i++) {
        clock += ms;
        const q = rafQueue; rafQueue = [];
        for (const r of q) r.cb(clock);
      }
    },
    pending() { return rafQueue.length; },
    fire(target, type) { for (const fn of (listeners[target + ":" + type] || []).slice()) fn({ type }); },
  };
}

function loadLogic(name) { return require(path.join(GAMES_DIR, name)); }

module.exports = { makeSandbox, loadLogic, GAMES_DIR, LOAD_ORDER };
