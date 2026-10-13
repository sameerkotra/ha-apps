/* Household Arcade — the computer's thinking for Four in a Row, Reversi and Checkers, off the page's main thread (a
   Web Worker; strict CSP: this same-origin file, no blob: or eval). It loads the board kit and the game's rules with
   the same ?v= as itself and answers { id, file, name, s, seat, level } with { id, move, rng }: the move R.ai makes
   and the random state after it, so the page's game goes on exactly as if it had thought itself. */
/* global importScripts */
(function () {
  "use strict";
  var q = self.location && self.location.search ? self.location.search : "";
  importScripts("boardkit.js" + q);
  var loaded = {};
  self.onmessage = function (e) {
    var d = e.data || {};
    try {
      if (!/^[a-z]+-logic\.js$/.test(String(d.file)) || !/^[A-Za-z]+Logic$/.test(String(d.name))) throw new Error("not a game's rules");
      if (!loaded[d.file]) { importScripts(d.file + q); loaded[d.file] = true; }
      var L = self[d.name], s = d.s;
      var move = L.R.ai(s, d.seat | 0, d.level | 0);
      self.postMessage({ id: d.id, move: move, rng: s.rng });
    } catch (err) {
      self.postMessage({ id: d.id, error: String((err && err.message) || err) });
    }
  };
})();
