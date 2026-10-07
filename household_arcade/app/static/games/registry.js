/* Household Arcade — the game registry (window.ArcadeGames).

   Each game file calls ArcadeGames.register(def) once:
     { id, name, modes: [{ id, label }], defaultMode, controls: "dpad" | "paddle" | "buttons" | "touch",
       buttons: [{ action, label, aria, wide, place: [col, row, colSpan, rowSpan] }] (on-screen buttons of
       "buttons" games, optional for "touch" games that are played on the game itself), padLabel (the
       paddle games' button, default "Launch"), help: "short controls text",
       stateVersion (saved games' format, 0 = can't be saved), options (start-screen choices), typed (keyboard
       typing goes to the game), turns + turnModes (the modes played turn by turn from two
       phones, SPEC §13.5: the server checks every move; the game's opts.turns carries the match), create(canvas, opts) }
   Adding a game later = its files in games/ plus one register() call (and its
   entry in the server's game table). */
(function (root) {
  "use strict";

  var defs = [];
  var CONTROLS = ["dpad", "paddle", "buttons", "touch"];
  var ACTIONS = ["up", "down", "left", "right", "fire", "alt", "up2", "down2", "left2", "right2", "fire2",
    // puzzle games' buttons (Sudoku's number pad and tools)
    "n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "notes", "fill", "hint", "undo", "erase", "auto",
    // Tile Match
    "shuffle"];

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
    // Start-screen choices a game offers besides its modes (remembered per person): { id, label, default, choices: [{ id, label }], offInRaces? }
    var options = [];
    if (Array.isArray(def.options)) {
      def.options.forEach(function (op) {
        if (!op || typeof op.id !== "string" || !/^[a-z][a-z0-9_]{0,19}$/.test(op.id) || typeof op.label !== "string" || !Array.isArray(op.choices) || !op.choices.length) fail("each option needs an id, a label and choices (" + def.id + ")");
        var ids = op.choices.map(function (c) { return String(c.id); });
        var item = { id: op.id, label: op.label, choices: op.choices.map(function (c) { return { id: String(c.id), label: String(c.label) }; }),
          default: ids.indexOf(String(op.default)) >= 0 ? String(op.default) : ids[0] };
        if (op.offInRaces) item.offInRaces = true;      // the shell uses the default in races and daily challenges
        options.push(item);
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
      options: options,
      typed: def.typed === true,        // true: the shell passes typed keys to input() as "key:A", "key:7", "key:ENTER", "key:BACKSPACE", "key:DELETE"
      stateVersion: typeof def.stateVersion === "number" ? def.stateVersion : 0,   // 0: games can't be saved
      race: def.race !== false && def.players !== 2,   // can be raced on two phones (SPEC §13.3); two-player games can't
      // turn by turn from two phones (SPEC §13.5): these modes are played only with someone, each move checked by the server
      turns: def.turns === true,
      turnModes: def.turns === true && Array.isArray(def.turnModes) ? def.turnModes.filter(function (m) { return modeIds.indexOf(m) >= 0; }) : [],
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
