/* Household Arcade — Klondike Solitaire rules (pure: no DOM, deterministic for a given seed).

   Cards are numbers 0–51: suit = ⌊id / 13⌋ (0 spades, 1 hearts, 2 diamonds, 3 clubs), rank = id % 13 + 1 (1 = ace,
   13 = king). Seven tableau columns (the first has 1 card, the last 7, only the last card of each face up), a
   stock turned over one card (draw1) or three (draw3) at a time onto the waste, and four foundations built up
   by suit from the ace. Build tableau columns down in alternating colours; only a king goes on an empty column.
   The stock and the waste are one list `pile` with a pointer `p`: pile[0..p) is the waste (its last card on top),
   pile[p..] the stock; turning the waste back over is p = 0, so the order never changes.
   Ranked and race deals are always winnable: the seed picks a deal, and a search (solve) that has all the cards
   in view must find a way to win it, otherwise the next deal from the same seed is tried.
   Score: +10 a card to a foundation, +5 a card from the waste to a column, +5 for turning over a column card,
   −15 for taking a card back off a foundation (never below 0); a win adds up to 1,000 for speed
   (30,000 ÷ seconds, at most 1,000 for 30 s or less). Undo gives the points back too.
   One call to step() is one update (60 a second). */
(function () {
  "use strict";

  var UPS = 60, STATE_VERSION = 1, HISTORY_MAX = 60, SAVED_HISTORY = 20, HINT_UPDATES = 240, MESSAGE_UPDATES = 150;
  var MODES = { draw1: { draw: 1, name: "Draw one", nodes: 12000 }, draw3: { draw: 3, name: "Draw three", nodes: 12000 } };
  var MODE_IDS = ["draw1", "draw3"];
  var MAX_DEALS = 40;
  var SUIT_NAMES = ["spades", "hearts", "diamonds", "clubs"], RANK_NAMES = ["", "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];

  function rand(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function suit(id) { return Math.floor(id / 13); }
  function rank(id) { return id % 13 + 1; }
  function red(id) { var s = suit(id); return s === 1 || s === 2; }
  function cardName(id) { return RANK_NAMES[rank(id)] + " of " + SUIT_NAMES[suit(id)]; }

  function deck(seed) {
    var r = rand(seed), d = [], i;
    for (i = 0; i < 52; i++) d.push(i);
    for (i = 51; i > 0; i--) { var j = Math.floor(r() * (i + 1)), t = d[i]; d[i] = d[j]; d[j] = t; }
    return d;
  }
  /** A fresh board from a shuffled deck: { tab, down, found (counts per suit), pile, p }. */
  function layout(d) {
    var tab = [], down = [], k = 0;
    for (var c = 0; c < 7; c++) { tab.push(d.slice(k, k + c + 1)); down.push(c); k += c + 1; }
    return { tab: tab, down: down, found: [0, 0, 0, 0], pile: d.slice(k), p: 0 };
  }

  // ---------------------------------------------------------------------------------------------- the solver
  // Works on { tab, down, found (counts per suit), pile, p } and the draw size. Depth-first with the states
  // already seen remembered; the safe moves to a foundation are made without branching.

  function cloneB(b) {
    return { tab: b.tab.map(function (c) { return c.slice(); }), down: b.down.slice(), found: b.found.slice(), pile: b.pile.slice(), p: b.p };
  }
  function key(b) {
    var cols = [];
    for (var c = 0; c < 7; c++) cols.push(b.down[c] + ":" + b.tab[c].join(","));
    cols.sort();
    return cols.join("|") + "#" + b.found.join("") + "#" + b.pile.join(",") + "#" + b.p;
  }
  function safeUp(b, id) {
    var r = rank(id);
    if (r === 1) return true;
    if (b.found[suit(id)] !== r - 1) return false;
    if (r === 2) return true;
    var o1 = red(id) ? 0 : 1, o2 = red(id) ? 3 : 2;       // the two suits of the other colour
    return b.found[o1] >= r - 1 && b.found[o2] >= r - 1;
  }
  function wasteTop(b) { return b.p > 0 ? b.pile[b.p - 1] : -1; }
  function flip(b, c) { if (b.tab[c].length && b.down[c] >= b.tab[c].length) { b.down[c] = b.tab[c].length - 1; return true; } return false; }
  function won(b) { return b.found[0] + b.found[1] + b.found[2] + b.found[3] === 52; }
  function removeWaste(b) { b.pile.splice(b.p - 1, 1); b.p--; }

  /** Make every safe move to a foundation; returns the moves made. */
  function settle(b, moves) {
    var again = true;
    while (again) {
      again = false;
      var w = wasteTop(b);
      if (w >= 0 && b.found[suit(w)] === rank(w) - 1 && safeUp(b, w)) {
        removeWaste(b); b.found[suit(w)]++; if (moves) moves.push({ from: "waste", to: "found" }); again = true; continue;
      }
      for (var c = 0; c < 7; c++) {
        var col = b.tab[c], t = col[col.length - 1];
        if (t !== undefined && b.found[suit(t)] === rank(t) - 1 && safeUp(b, t)) {
          col.pop(); b.found[suit(t)]++; flip(b, c); if (moves) moves.push({ from: "tab", c: c, to: "found" }); again = true;
        }
      }
    }
  }

  /** The branching moves from a board, best first: [{ kind, ... }]. */
  function moves(b, draw) {
    var out = [], c, d, k, col, t, w = wasteTop(b);
    // from the tableau: to a foundation, or a run onto another column
    for (c = 0; c < 7; c++) {
      col = b.tab[c];
      if (!col.length) continue;
      t = col[col.length - 1];
      if (b.found[suit(t)] === rank(t) - 1) out.push({ pri: b.down[c] === col.length - 1 && col.length > 1 ? 1 : 3, from: "tab", c: c, to: "found" });
      for (k = b.down[c]; k < col.length; k++) {
        var card = col[k];
        for (d = 0; d < 7; d++) {
          if (d === c) continue;
          var dest = b.tab[d], top = dest[dest.length - 1];
          if (top === undefined) {
            if (rank(card) !== 13 || (k === 0)) continue;           // a king already at the bottom of its column gains nothing
          } else if (!(rank(top) === rank(card) + 1 && red(top) !== red(card))) continue;
          out.push({ pri: k === b.down[c] && b.down[c] > 0 ? 0 : 4, from: "tab", c: c, k: k, to: "tab", d: d });
        }
      }
    }
    if (w >= 0) {
      if (b.found[suit(w)] === rank(w) - 1) out.push({ pri: 2, from: "waste", to: "found" });
      for (d = 0; d < 7; d++) {
        var dst = b.tab[d], tp = dst[dst.length - 1];
        if (tp === undefined ? rank(w) === 13 : (rank(tp) === rank(w) + 1 && red(tp) !== red(w))) out.push({ pri: 2, from: "waste", to: "tab", d: d });
      }
    }
    if (b.p < b.pile.length) out.push({ pri: 6, from: "draw" });
    else if (b.pile.length > 0) out.push({ pri: 7, from: "draw" });
    out.sort(function (x, y) { return x.pri - y.pri; });
    return out;
  }

  function apply(b, m, draw) {
    var t, run;
    if (m.from === "draw") {
      if (b.p < b.pile.length) b.p = Math.min(b.pile.length, b.p + draw); else b.p = 0;
      return;
    }
    if (m.from === "waste") {
      t = wasteTop(b); removeWaste(b);
      if (m.to === "found") b.found[suit(t)]++; else b.tab[m.d].push(t);
      return;
    }
    if (m.to === "found") { t = b.tab[m.c].pop(); b.found[suit(t)]++; flip(b, m.c); return; }
    run = b.tab[m.c].splice(m.k);
    for (var i = 0; i < run.length; i++) b.tab[m.d].push(run[i]);
    flip(b, m.c);
  }

  /** Search for a win from this board: { solved, moves (the winning line), nodes }. `limit` caps the boards looked at. */
  function solve(board, draw, limit) {
    var seen = Object.create(null), nodes = 0, line = [], exhausted = false;
    function go(b) {
      if (won(b)) return true;
      if (++nodes > limit) { exhausted = true; return false; }
      var k = key(b);
      if (seen[k]) return false;
      seen[k] = 1;
      var list = moves(b, draw);
      for (var i = 0; i < list.length; i++) {
        var m = list[i], n = cloneB(b), made = [];
        apply(n, m, draw);
        made.push(m);
        settle(n, made);
        var before = line.length;
        for (var j = 0; j < made.length; j++) line.push(made[j]);
        if (go(n)) return true;
        line.length = before;
        if (exhausted) return false;
      }
      return false;
    }
    var start = cloneB(board), first = [];
    settle(start, first);
    line = first.slice();
    var ok = go(start);
    return { solved: ok, moves: ok ? line : [], nodes: nodes };
  }

  /** A winnable deal for the seed: { deck, attempt, nodes }. */
  function dealFor(mode, seed) {
    var m = MODES[mode], last = null;
    for (var attempt = 0; attempt < MAX_DEALS; attempt++) {
      var d = deck(attempt === 0 ? seed : (Math.imul(seed >>> 0, 2654435761) + attempt * 40503) >>> 0), res = solve(layout(d), m.draw, m.nodes);
      last = { deck: d, attempt: attempt, nodes: res.nodes };
      if (res.solved) return last;
    }
    return last;
  }

  // -------------------------------------------------------------------------------------------------- the game

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "draw1";
    var d = o.deck || dealFor(mode, o.seed == null ? 1 : o.seed).deck, b = layout(d);
    return {
      v: STATE_VERSION, mode: mode, draw: MODES[mode].draw, tab: b.tab, down: b.down, found: [[], [], [], []], pile: b.pile, p: 0,
      score: 0, moves: 0, history: [], hint: null, hintT: 0, message: "", messageT: 0,
      updates: 0, won: false, over: false, level: 1, plan: null, stats: { cause: "", undone: 0, hints: 0, flips: 0 },
    };
  }

  function seconds(s) { return Math.floor(s.updates / UPS); }
  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function bonusOf(s) { return Math.min(1000, Math.floor(30000 / Math.max(30, seconds(s)))); }
  function say(s, t) { s.message = t; s.messageT = MESSAGE_UPDATES; }
  function foundCounts(s) { return s.found.map(function (f) { return f.length; }); }
  function suitCounts(s) { var n = [0, 0, 0, 0]; s.found.forEach(function (f) { if (f.length) n[suit(f[0])] = f.length; }); return n; }
  function total(s) { return s.found[0].length + s.found[1].length + s.found[2].length + s.found[3].length; }
  function boardOf(s) { return { tab: s.tab, down: s.down, found: suitCounts(s), pile: s.pile, p: s.p }; }
  function waste(s) { return s.p > 0 ? s.pile[s.p - 1] : -1; }
  function wasteFan(s) { return s.pile.slice(Math.max(0, s.p - (s.draw === 3 ? 3 : 1)), s.p); }

  function snapshot(s) {
    return JSON.stringify({ tab: s.tab, down: s.down, found: s.found, pile: s.pile, p: s.p, score: s.score, moves: s.moves });
  }
  function remember(s) { s.history.push(snapshot(s)); if (s.history.length > HISTORY_MAX) s.history.shift(); }

  function step(s) {
    if (s.over) return [];
    s.updates++;
    if (s.hintT > 0 && --s.hintT === 0) s.hint = null;
    if (s.messageT > 0 && --s.messageT === 0) s.message = "";
    return [];
  }

  /** Where a card could go on a foundation: the slot of its suit, or the first empty slot for an ace; -1 if nowhere. */
  function slotFor(s, id) {
    var i;
    for (i = 0; i < 4; i++) if (s.found[i].length && suit(s.found[i][0]) === suit(id)) return s.found[i].length === rank(id) - 1 ? i : -1;
    if (rank(id) !== 1) return -1;
    for (i = 0; i < 4; i++) if (!s.found[i].length) return i;
    return -1;
  }
  function fits(s, id, c) {
    var col = s.tab[c], top = col[col.length - 1];
    if (top === undefined) return rank(id) === 13;
    return s.down[c] < col.length && rank(top) === rank(id) + 1 && red(top) !== red(id);
  }
  /** The cards a `from` ({t: "waste"} | {t: "tab", c, k} | {t: "found", f}) would carry, or [] if it can't be lifted. */
  function carried(s, from) {
    if (!from) return [];
    if (from.t === "waste") return waste(s) >= 0 ? [waste(s)] : [];
    if (from.t === "found") { var f = s.found[from.f]; return f && f.length ? [f[f.length - 1]] : []; }
    if (from.t === "tab") { var col = s.tab[from.c]; return col && from.k >= s.down[from.c] && from.k < col.length ? col.slice(from.k) : []; }
    return [];
  }

  function gain(s, n) { s.score = Math.max(0, s.score + n); }

  function finishIfWon(s, evs) {
    if (total(s) === 52) {
      s.won = true; s.over = true; s.stats.cause = "won"; s.hint = null;
      gain(s, bonusOf(s));
      evs.push({ type: "win", score: s.score });
    }
  }

  /** Move what `from` carries to `to` ({t: "tab", c} | {t: "found", f}); the events, or [] if it isn't allowed. */
  function move(s, from, to) {
    var evs = [], cards = carried(s, from);
    if (s.over || !cards.length || !to) return evs;
    if (to.t === "found") {
      if (cards.length !== 1) return evs;
      var id = cards[0], slot = to.f >= 0 && to.f < 4 && slotFor(s, id) === to.f ? to.f : -1;
      if (slot < 0) return evs;
      remember(s);
      takeFrom(s, from, evs);
      s.found[slot].push(id);
      if (from.t === "found") gain(s, -15); else gain(s, 10);
      evs.push({ type: "foundation", card: id, slot: slot });
    } else if (to.t === "tab") {
      if (from.t === "tab" && from.c === to.c) return evs;
      if (!fits(s, cards[0], to.c)) return evs;
      if (from.t === "tab" && s.tab[to.c].length === 0 && from.k === 0) return evs;
      remember(s);
      takeFrom(s, from, evs);
      for (var i = 0; i < cards.length; i++) s.tab[to.c].push(cards[i]);
      if (from.t === "waste") gain(s, 5); else if (from.t === "found") gain(s, -15);
      evs.push({ type: "move", cards: cards.length, to: to.c });
    } else return evs;
    s.moves++; s.hint = null; s.hintT = 0;
    finishIfWon(s, evs);
    return evs;
  }
  function takeFrom(s, from, evs) {
    if (from.t === "waste") { s.pile.splice(s.p - 1, 1); s.p--; }
    else if (from.t === "found") s.found[from.f].pop();
    else {
      s.tab[from.c].splice(from.k);
      if (s.down[from.c] >= s.tab[from.c].length && s.tab[from.c].length) { s.down[from.c] = s.tab[from.c].length - 1; gain(s, 5); s.stats.flips++; evs.push({ type: "flip", c: from.c }); }
    }
  }

  /** Turn the next card(s) of the stock over; with the stock empty, turn the waste back over. */
  function draw(s) {
    var evs = [];
    if (s.over) return evs;
    if (s.p < s.pile.length) {
      remember(s);
      s.p = Math.min(s.pile.length, s.p + s.draw);
      s.moves++; s.hint = null;
      evs.push({ type: "draw" });
    } else if (s.pile.length > 0) {
      remember(s);
      s.p = 0; s.moves++; s.hint = null;
      evs.push({ type: "recycle" });
    } else say(s, "The stock is empty.");
    return evs;
  }

  /** A tap on a card: to a foundation if it can go, else to the first column it fits (the longer column first). */
  function tapCard(s, from) {
    var cards = carried(s, from), i;
    if (!cards.length || s.over) return [];
    if (cards.length === 1 && from.t !== "found") {
      var slot = slotFor(s, cards[0]);
      if (slot >= 0) return move(s, from, { t: "found", f: slot });
    }
    var best = -1, bestLen = -1;
    for (i = 0; i < 7; i++) {
      if (from.t === "tab" && from.c === i) continue;
      if (!fits(s, cards[0], i)) continue;
      if (from.t === "tab" && s.tab[i].length === 0 && from.k === 0) continue;
      var len = s.tab[i].length;
      if (len > bestLen) { best = i; bestLen = len; }
    }
    if (best >= 0) return move(s, from, { t: "tab", c: best });
    say(s, "That card can't go anywhere yet.");
    return [{ type: "nomove" }];
  }

  function undo(s) {
    if (s.over || !s.history.length) return [];
    var d = JSON.parse(s.history.pop());
    s.tab = d.tab; s.down = d.down; s.found = d.found; s.pile = d.pile; s.p = d.p; s.score = d.score; s.moves = d.moves;
    s.hint = null; s.hintT = 0; s.stats.undone++;
    return [{ type: "undo" }];
  }

  /** The next helpful move: from the search, else a simple one. Shows it (s.hint = { from, to, text }). */
  function hint(s) {
    if (s.over) return [];
    var b = boardOf(s), k0 = key(b), m = null, from, to, text;
    if (s.plan) {              // keep following the line already found while the board is still on it
      var at = s.plan.keys.indexOf(k0);
      if (at >= 0) m = s.plan.moves[at]; else s.plan = null;
    }
    if (!m) {
      var res = solve(b, s.draw, 20000);
      if (res.solved && res.moves.length) {
        var walk = cloneB(b), keys = [];
        res.moves.forEach(function (mv) { keys.push(key(walk)); apply(walk, mv, s.draw); });
        s.plan = { moves: res.moves, keys: keys };
        m = res.moves[0];
      }
    }
    if (!m) {
      var list = moves(boardOf(s), s.draw).filter(function (x) { return x.from !== "draw"; });
      m = list[0] || { from: "draw" };
    }
    if (m.from === "draw") {
      from = { t: "stock" }; to = null;
      text = s.p < s.pile.length ? "Turn over the stock." : s.pile.length ? "Turn the waste back over." : "No moves are left. Try Undo.";
    } else {
      from = m.from === "waste" ? { t: "waste" } : { t: "tab", c: m.c, k: m.k === undefined ? s.tab[m.c].length - 1 : m.k };
      if (m.to === "found") { var id = carried(s, from)[0]; to = { t: "found", f: Math.max(0, slotFor(s, id)) }; text = cardName(id) + " goes up to a foundation."; }
      else { to = { t: "tab", c: m.d }; text = cardName(carried(s, from)[0]) + " can move to the highlighted column."; }
    }
    s.hint = { from: from, to: to, text: text }; s.hintT = HINT_UPDATES; s.stats.hints++;
    say(s, text);
    return [{ type: "hint" }];
  }

  /** Auto: every card that is safe to move up goes up (when the stock is empty and all cards are face up, all of them). */
  function auto(s) {
    var evs = [];
    if (s.over) return evs;
    var easy = s.pile.length === 0 && s.down.every(function (d) { return d === 0; });
    var again = true;
    while (again && !s.over) {
      again = false;
      var sources = [{ t: "waste" }];
      for (var c = 0; c < 7; c++) if (s.tab[c].length) sources.push({ t: "tab", c: c, k: s.tab[c].length - 1 });
      for (var i = 0; i < sources.length; i++) {
        var from = sources[i], id = carried(s, from)[0];
        if (id === undefined || (from.t === "tab" && from.k < s.down[from.c])) continue;
        var slot = slotFor(s, id);
        if (slot < 0) continue;
        if (!easy && !safeUp(boardOf(s), id)) continue;
        var r = move(s, from, { t: "found", f: slot });
        if (r.length) { for (var j = 0; j < r.length; j++) evs.push(r[j]); again = true; break; }
      }
    }
    if (!evs.length) { say(s, "Nothing is safe to move up yet."); evs.push({ type: "nomove" }); }
    return evs;
  }

  function press(s, action, down) {
    if (!down || s.over) return [];
    if (action === "undo") return undo(s);
    if (action === "hint") return hint(s);
    if (action === "auto") return auto(s);
    return [];
  }

  function status(s) { return { score: s.score, level: 1, over: s.over, done: s.won, seconds: seconds(s), cards: total(s) }; }
  function summary(s) {
    var out = [total(s) + " of 52 cards on the foundations", "Time " + clock(seconds(s)), s.moves + (s.moves === 1 ? " move" : " moves")];
    if (s.won) out.push("Speed bonus " + bonusOf(s));
    return out;
  }
  function result(s) {
    return { score: s.score, level: 1, stats: { won: s.won, cause: s.stats.cause, mode: s.mode, cards: total(s), moves: s.moves, hints: s.stats.hints, undone: s.stats.undone, seconds: seconds(s), summary: summary(s) } };
  }

  function save(s) {
    var c = JSON.parse(JSON.stringify(s));
    c.history = c.history.slice(-SAVED_HISTORY); c.plan = null; c.hint = null; c.hintT = 0; c.message = ""; c.messageT = 0;
    return c;
  }
  function restore(data) {
    var m = data && MODES[data.mode], bad = new Error("That saved game can't be continued.");
    if (!m || data.draw !== m.draw || !Array.isArray(data.tab) || data.tab.length !== 7 || !Array.isArray(data.down) || data.down.length !== 7 ||
        !Array.isArray(data.found) || data.found.length !== 4 || !Array.isArray(data.pile) || !Array.isArray(data.history) ||
        typeof data.updates !== "number" || !(data.updates >= 0) || !isFinite(data.updates) || typeof data.score !== "number" || !(data.score >= 0) || !isFinite(data.score) ||
        typeof data.moves !== "number" || !(data.moves >= 0) || typeof data.p !== "number" || data.p % 1 !== 0 || data.p < 0 || data.p > data.pile.length) throw bad;
    var seen = {}, count = 0;
    function take(id) { if (typeof id !== "number" || id % 1 !== 0 || id < 0 || id > 51 || seen[id]) throw bad; seen[id] = true; count++; }
    data.tab.forEach(function (col, c) {
      if (!Array.isArray(col)) throw bad;
      col.forEach(take);
      if (typeof data.down[c] !== "number" || data.down[c] % 1 !== 0 || data.down[c] < 0 || data.down[c] > Math.max(0, col.length - 1) || (col.length === 0 && data.down[c] !== 0)) throw bad;
    });
    data.found.forEach(function (f) {
      if (!Array.isArray(f)) throw bad;
      f.forEach(function (id, i) { take(id); if (rank(id) !== i + 1 || suit(id) !== suit(f[0])) throw bad; });
    });
    data.pile.forEach(take);
    if (count !== 52) throw bad;
    if (!data.history.every(function (h) { return typeof h === "string" && h.length < 4000; })) throw bad;
    var s = JSON.parse(JSON.stringify(data));
    s.hint = null; s.hintT = 0; s.message = ""; s.messageT = 0; s.over = false; s.won = false; s.level = 1; s.plan = null;
    s.stats = { cause: "", undone: data.stats && data.stats.undone > 0 ? Math.floor(data.stats.undone) : 0, hints: data.stats && data.stats.hints > 0 ? Math.floor(data.stats.hints) : 0, flips: data.stats && data.stats.flips > 0 ? Math.floor(data.stats.flips) : 0 };
    if (total(s) === 52) throw bad;
    return s;
  }

  var SolitaireLogic = {
    UPS: UPS, MODES: MODES, MODE_IDS: MODE_IDS, STATE_VERSION: STATE_VERSION, SUIT_NAMES: SUIT_NAMES, RANK_NAMES: RANK_NAMES,
    rand: rand, suit: suit, rank: rank, red: red, cardName: cardName, deck: deck, layout: layout, solve: solve, dealFor: dealFor, moves: moves,
    create: create, step: step, slotFor: slotFor, fits: fits, carried: carried, move: move, draw: draw, tapCard: tapCard, undo: undo, hint: hint, auto: auto,
    press: press, waste: waste, wasteFan: wasteFan, total: total, seconds: seconds, clock: clock, bonusOf: bonusOf, status: status, result: result,
    save: save, restore: restore, safeUp: safeUp,
  };
  if (typeof module === "object" && module.exports) module.exports = SolitaireLogic; else self.SolitaireLogic = SolitaireLogic;
})();
