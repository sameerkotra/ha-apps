/* Household Arcade — the chess computer's thinking, off the page's main thread (a Web Worker; strict CSP: the worker
   is this same-origin file, no blob: or eval). It loads the shared board kit and the chess rules with the same
   ?v= as itself, and answers { id, fen, level, seed, past } with { id, move } (ChessLogic.think: deterministic, so the
   move is the same as the main thread would find). */
/* global importScripts, ChessLogic */
(function () {
  "use strict";
  var q = self.location && self.location.search ? self.location.search : "";
  importScripts("boardkit.js" + q, "chess-logic.js" + q);
  self.onmessage = function (e) {
    var d = e.data || {};
    var move = null, error = null;
    try { move = ChessLogic.think(String(d.fen), d.level | 0, d.seed >>> 0, Array.isArray(d.past) ? d.past : []); }
    catch (err) { error = String((err && err.message) || err); }
    self.postMessage({ id: d.id, move: move, error: error });
  };
})();
