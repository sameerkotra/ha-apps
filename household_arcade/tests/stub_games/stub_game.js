/* A stand-in for static/games/ used only by the shell's browser check (served in place of
   games/kit.js; the other game files are served empty). It follows the games contract
   (spec/GAMES.md): ArcadeKit, ArcadeSound, ArcadeGames and two games, "snake" (dpad) and
   "brick" (paddle). It counts inputs as points and records what the shell sent, so a test can
   check the controls; window.__stub is the running instance, and __stub.end() finishes the game. */
(function () {
  "use strict";
  var LOOKS = { modern: { label: "Modern" }, lcd: { label: "Retro LCD" }, neon: { label: "Neon" },
    pixel: { label: "Pixel" }, paper: { label: "Paper" }, contrast: { label: "High contrast" } };
  var COLORS = { modern: "#20304a", lcd: "#9aa98a", neon: "#000000", pixel: "#2b1d3a", paper: "#f4efe2", contrast: "#000000" };
  window.ArcadeKit = { LOOKS: LOOKS, createRenderer: function () { return {}; } };
  var soundOn = false;
  window.ArcadeSound = { setEnabled: function (b) { soundOn = !!b; }, play: function () {}, isEnabled: function () { return soundOn; } };
  var registry = {};
  window.ArcadeGames = {
    register: function (d) { registry[d.id] = d; },
    get: function (id) { return registry[id] || null; },
    list: function () { return Object.keys(registry).map(function (k) { return registry[k]; }); },
  };

  function game(id, name, modes, controls) {
    return {
      id: id, name: name, modes: modes, defaultMode: modes[0].id, controls: controls,
      help: controls === "dpad" ? "Arrow keys or the arrow pad." : "Drag or arrow keys; Space launches.",
      create: function (canvas, opts) {
        var ctx = canvas.getContext("2d");
        var raf = null, paused = false, ended = false, score = 0, level = 1, active = 0, last = null, frames = 0;
        var look = opts.look;
        function size() {
          var r = canvas.getBoundingClientRect(), d = window.devicePixelRatio || 1;
          canvas.width = Math.max(1, Math.round(r.width * d));
          canvas.height = Math.max(1, Math.round(r.height * d));
        }
        function draw() {
          ctx.setTransform(canvas.width / 240, 0, 0, canvas.height / 300, 0, 0);
          ctx.fillStyle = COLORS[look] || "#333";
          ctx.fillRect(0, 0, 240, 300);
          ctx.fillStyle = look === "paper" || look === "lcd" ? "#111" : "#fff";
          ctx.font = "16px sans-serif";
          ctx.fillText(name + " " + score, 10, 24);
          ctx.fillRect(10 + (frames % 200), 150, 20, 20);
        }
        function frame(t) {
          raf = null;
          if (paused || ended) return;
          if (last !== null) active += (t - last) / 1000;
          last = t;
          frames++;
          draw();
          raf = requestAnimationFrame(frame);
        }
        function stop() { if (raf) cancelAnimationFrame(raf); raf = null; last = null; }
        var inst = {
          inputs: [], pointers: [], opts: opts,
          start: function () { size(); last = null; raf = requestAnimationFrame(frame); },
          pause: function () { if (paused || ended) return; paused = true; stop(); },
          resume: function () { if (!paused || ended) return; paused = false; raf = requestAnimationFrame(frame); },
          get paused() { return paused; },
          get running() { return raf !== null; },
          get frames() { return frames; },
          get look() { return look; },
          setLook: function (l) { look = l; draw(); },
          setSound: function (b) { inst.sound = b; },
          setReduceMotion: function (b) { inst.reduceMotion = b; },
          input: function (a, down) { inst.inputs.push([a, down]); if (down) { score += 10; opts.onScore(score, level); } },
          pointer: function (k, x, y) { inst.pointers.push([k, Math.round(x), Math.round(y)]); },
          resize: function () { size(); draw(); },
          destroy: function () { ended = true; stop(); },
          end: function () { if (ended) return; ended = true; stop(); opts.onEnd({ score: score, level: level, seconds: active, stats: {} }); },
        };
        window.__stub = inst;
        return inst;
      },
    };
  }
  ArcadeGames.register(game("snake", "Snake", [
    { id: "walls-slow", label: "Walls · Slow" }, { id: "walls-normal", label: "Walls · Normal" },
    { id: "walls-fast", label: "Walls · Fast" }, { id: "wrap-slow", label: "Wrap · Slow" },
    { id: "wrap-normal", label: "Wrap · Normal" }, { id: "wrap-fast", label: "Wrap · Fast" }], "dpad"));
  ArcadeGames.register(game("brick", "Brick Breaker", [{ id: "powerups", label: "Power-ups" }, { id: "classic", label: "Classic" }], "paddle"));
})();
