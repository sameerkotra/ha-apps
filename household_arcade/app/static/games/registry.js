/* Household Arcade — the game registry (window.ArcadeGames).

   Each game file calls ArcadeGames.register(def) once:
     { id, name, modes: [{ id, label }], defaultMode, controls: "dpad" | "paddle" | "buttons" | "touch",
       buttons: [{ action, label, aria, wide, place: [col, row, colSpan, rowSpan] }] (on-screen buttons of
       "buttons" games, optional for "touch" games that are played on the game itself), padLabel (the
       paddle games' button, default "Launch"), help: "short controls text",
       stateVersion (saved games' format, 0 = can't be saved), create(canvas, opts) }
   Adding a game later = its files in games/ plus one register() call (and its
   entry in the server's game table). */
(function (root) {
  "use strict";

  var defs = [];
  var CONTROLS = ["dpad", "paddle", "buttons", "touch"];
  var ACTIONS = ["up", "down", "left", "right", "fire", "alt", "up2", "down2", "left2", "right2", "fire2"];

  function fail(msg) { throw new Error("ArcadeGames.register: " + msg); }

  function register(def) {
    if (!def || typeof def !== "object") fail("a game definition object is needed");
    if (typeof def.id !== "string" || !/^[a-z][a-z0-9_-]*$/.test(def.id)) fail("id must be a short lower-case string");
    if (typeof def.name !== "string" || !def.name) fail("name is needed (" + def.id + ")");
    if (!Array.isArray(def.modes) || !def.modes.length) fail("at least one mode is needed (" + def.id + ")");
    def.modes.forEach(function (m) {
      if (!m || typeof m.id !== "string" || typeof m.label !== "string") fail("each mode needs an id and a label (" + def.id + ")");
    });
    if (typeof def.create !== "function") fail("create(canvas, opts) is needed (" + def.id + ")");
    var buttons = [];
    if (def.controls === "buttons" && (!Array.isArray(def.buttons) || !def.buttons.length)) fail("buttons games need at least one button (" + def.id + ")");
    if ((def.controls === "buttons" || def.controls === "touch") && Array.isArray(def.buttons)) {
      def.buttons.forEach(function (b) {
        if (!b || ACTIONS.indexOf(b.action) < 0 || typeof b.label !== "string") fail("each button needs an action and a label (" + def.id + ")");
        var place = null;
        if (Array.isArray(b.place) && b.place.length === 4 && b.place.every(function (n) { return n === (n | 0) && n >= 1 && n <= 6; })) place = b.place.slice();
        buttons.push({ action: b.action, label: b.label, aria: typeof b.aria === "string" ? b.aria : b.label, wide: !!b.wide, place: place });
      });
    }
    var modeIds = def.modes.map(function (m) { return m.id; });
    var entry = {
      id: def.id,
      name: def.name,
      modes: def.modes.map(function (m) { return { id: m.id, label: m.label }; }),
      defaultMode: modeIds.indexOf(def.defaultMode) >= 0 ? def.defaultMode : modeIds[0],
      controls: CONTROLS.indexOf(def.controls) >= 0 ? def.controls : "dpad",
      buttons: buttons,
      players: def.players === 2 ? 2 : 1,
      tap: ACTIONS.indexOf(def.tap) >= 0 ? def.tap : null,   // dpad games: the action a tap (no swipe) sends     // 2: same-screen two-player game (WASD + Q/E are player 2)
      padLabel: typeof def.padLabel === "string" && def.padLabel ? def.padLabel : "Launch",
      help: typeof def.help === "string" ? def.help : "",
      stateVersion: typeof def.stateVersion === "number" ? def.stateVersion : 0,   // 0: games can't be saved
      create: def.create,
    };
    var at = -1;
    for (var i = 0; i < defs.length; i++) if (defs[i].id === entry.id) at = i;
    if (at >= 0) defs[at] = entry; else defs.push(entry);
    return entry;
  }

  root.ArcadeGames = {
    register: register,
    get: function (id) {
      for (var i = 0; i < defs.length; i++) if (defs[i].id === id) return defs[i];
      return null;
    },
    list: function () { return defs.slice(); },
  };
})(typeof window !== "undefined" ? window : this);
