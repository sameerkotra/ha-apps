/* Household Arcade — the Games page search (SPEC §9): which games match what was typed.
   Pure: no DOM. Used by app.js (window.GameSearch) and the tests (module.exports).

   Every typed word must match the start of a word in the game's name, one of its tags or one of its mode labels.
   Case, accents and punctuation are ignored ("tic" finds Tic-tac-toe, "7 x 7" a 7 × 7 mode), words in any order.
   A few everyday words lead to a tag: kids, children, easy → gentle; 2, two, multiplayer, together → two players;
   strategy → board. */
(function (root) {
  "use strict";

  var SYNONYMS = {
    kids: "gentle", kid: "gentle", children: "gentle", child: "gentle", easy: "gentle",
    "2": "two players", two: "two players", multiplayer: "two players", together: "two players",
    strategy: "board",
  };

  /** Lower case, accents gone, × as x, everything but letters and digits a space. */
  function normalise(text) {
    var s = String(text == null ? "" : text).toLowerCase().replace(/×/g, " x ");
    if (s.normalize) s = s.normalize("NFD").replace(/[̀-ͯ]/g, "");
    return s.replace(/[^a-z0-9]+/g, " ").trim();
  }
  function words(text) { var n = normalise(text); return n ? n.split(" ") : []; }

  /** True when `word` starts some word of `list` (a plural "puzzles" also matches "puzzle"). */
  function startsAny(word, list) {
    for (var i = 0; i < list.length; i++) if (list[i].indexOf(word) === 0) return true;
    if (word.length > 3 && word.charAt(word.length - 1) === "s") return startsAny(word.slice(0, -1), list);
    return false;
  }

  /** Prepare a game ({name, tags, modes}) once for matching. */
  function index(game) {
    return {
      name: words(game.name),
      tags: (game.tags || []).map(function (t) { return { label: t, words: words(t) }; }),
      modes: (game.modes || []).map(function (m) { return { label: m.label, words: words(m.label) }; }),
    };
  }

  /** Does `game` match `query`? → {ok, via}: `via` names the tag or mode that matched when the name alone doesn't
      ("Mode: 7 × 7", "Tag: gentle"); null when the name matches every word or nothing is typed. */
  function match(game, query, prepared) {
    var q = words(query);
    if (!q.length) return { ok: true, via: null };
    var ix = prepared || index(game), via = null, i, k;
    for (i = 0; i < q.length; i++) {
      var w = q[i];
      if (startsAny(w, ix.name)) continue;
      var found = null;
      for (k = 0; k < ix.tags.length && !found; k++) if (startsAny(w, ix.tags[k].words)) found = "Tag: " + ix.tags[k].label;
      for (k = 0; k < ix.modes.length && !found; k++) if (startsAny(w, ix.modes[k].words)) found = "Mode: " + ix.modes[k].label;
      if (!found && SYNONYMS[w]) {
        for (k = 0; k < ix.tags.length && !found; k++) if (ix.tags[k].label === SYNONYMS[w]) found = "Tag: " + ix.tags[k].label;
      }
      if (!found) return { ok: false, via: null };
      if (!via) via = found;
    }
    return { ok: true, via: via };
  }

  /** The games that match, in their order: [{game, via}]. */
  function filter(games, query) {
    var out = [];
    for (var i = 0; i < games.length; i++) {
      var m = match(games[i], query);
      if (m.ok) out.push({ game: games[i], via: m.via });
    }
    return out;
  }

  var api = { normalise: normalise, words: words, index: index, match: match, filter: filter, SYNONYMS: SYNONYMS };
  root.GameSearch = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
