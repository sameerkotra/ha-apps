/* Household Arcade — Car Park's Classic and Hard boards made off the page's main thread (a Web Worker; strict CSP:
   this same-origin file, no blob: or eval). Loads the rules with the same ?v= as itself and answers { id, mode, seed }
   with { id, board } (ParkingLogic.boardFor: the same board the page would make for that seed). */
/* global importScripts, ParkingLogic */
(function () {
  "use strict";
  var q = self.location && self.location.search ? self.location.search : "";
  importScripts("parking-logic.js" + q);
  self.onmessage = function (e) {
    var d = e.data || {}, board = null, error = null;
    try { board = ParkingLogic.boardFor(String(d.mode), d.seed >>> 0); }
    catch (err) { error = String((err && err.message) || err); }
    self.postMessage({ id: d.id, board: board, error: error });
  };
})();
