"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const L = loadLogic("solitaire-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const types = (evs) => evs.map((e) => e.type);

/** Play one solver move through the game's own move functions. */
function play(s, m) {
  if (m.from === "draw") return L.draw(s);
  const from = m.from === "waste" ? { t: "waste" } : { t: "tab", c: m.c, k: m.k === undefined ? s.tab[m.c].length - 1 : m.k };
  const id = L.carried(s, from)[0];
  const to = m.to === "found" ? { t: "found", f: L.slotFor(s, id) } : { t: "tab", c: m.d };
  const evs = L.move(s, from, to);
  assert.ok(evs.length, "the solver's move is legal: " + JSON.stringify(m));
  return evs;
}
function fixed(cols, o = {}) {
  // a hand-made game: cols = arrays of card ids (all face up unless down is given)
  const s = L.create({ mode: o.mode || "draw1", seed: 1 });
  s.tab = cols.map((c) => c.slice()); while (s.tab.length < 7) s.tab.push([]);
  s.down = (o.down || []).concat([0, 0, 0, 0, 0, 0, 0]).slice(0, 7);
  s.found = (o.found || [[], [], [], []]).map((f) => f.slice());
  s.pile = (o.pile || []).slice(); s.p = o.p || 0;
  s.history = []; s.score = 0; s.moves = 0;
  return s;
}
const card = (suit, rank) => suit * 13 + rank - 1;     // suits: 0 spades, 1 hearts, 2 diamonds, 3 clubs

test("cards: suit, rank and colour", () => {
  assert.equal(L.suit(0), 0); assert.equal(L.rank(0), 1); assert.equal(L.rank(51), 13); assert.equal(L.suit(51), 3);
  assert.equal(L.red(card(1, 5)), true); assert.equal(L.red(card(2, 5)), true); assert.equal(L.red(card(0, 5)), false); assert.equal(L.red(card(3, 5)), false);
  assert.equal(L.cardName(card(1, 1)), "A of hearts"); assert.equal(L.cardName(card(0, 12)), "Q of spades");
});

test("a deal holds every card once, laid out 1–7 with the last of each column face up", () => {
  for (const mode of L.MODE_IDS) {
    const s = L.create({ mode, seed: 12345 });
    assert.deepEqual(s.tab.map((c) => c.length), [1, 2, 3, 4, 5, 6, 7]);
    assert.deepEqual(s.down, [0, 1, 2, 3, 4, 5, 6]);
    assert.equal(s.pile.length, 24); assert.equal(s.p, 0); assert.equal(s.draw, L.MODES[mode].draw);
    const all = [].concat(...s.tab, s.pile).sort((a, b) => a - b);
    assert.deepEqual(all, Array.from({ length: 52 }, (_, i) => i));
  }
  assert.deepEqual(L.deck(5), L.deck(5)); assert.notDeepEqual(L.deck(5), L.deck(6));
});

test("ranked deals are winnable: the solver's line wins when played through the game's own moves", () => {
  for (const mode of L.MODE_IDS) {
    for (let seed = 1; seed <= 6; seed++) {
      const d = L.dealFor(mode, seed * 7777), again = L.dealFor(mode, seed * 7777);
      assert.deepEqual(d, again, "the same seed gives the same deal");
      const res = L.solve(L.layout(d.deck), L.MODES[mode].draw, L.MODES[mode].nodes);
      assert.ok(res.solved, `${mode} ${seed} is winnable`);
      const s = L.create({ mode, deck: d.deck });
      for (const m of res.moves) { if (s.over) break; play(s, m); }
      assert.ok(s.won && s.over, `${mode} ${seed}: the line wins (${L.total(s)} cards up)`);
      assert.equal(L.total(s), 52);
    }
  }
});

test("dealing is quick and nearly every seed wins first or second time", () => {
  const t0 = Date.now(); let attempts = 0;
  for (let seed = 1; seed <= 12; seed++) attempts += L.dealFor("draw3", seed * 31).attempt;
  assert.ok(Date.now() - t0 < 12000, "twelve draw-three deals in " + (Date.now() - t0) + " ms");
  assert.ok(attempts <= 12 * 6);
  const un = L.solve(L.layout(L.deck(3)), 1, 50);
  assert.equal(typeof un.solved, "boolean"); assert.ok(un.nodes <= 52);
});

test("the stock: draw one or three, the waste goes back over in the same order", () => {
  const one = L.create({ mode: "draw1", seed: 3 }), three = L.create({ mode: "draw3", seed: 3 });
  const first = one.pile.slice();
  assert.deepEqual(types(L.draw(one)), ["draw"]); assert.equal(one.p, 1); assert.equal(L.waste(one), first[0]);
  assert.deepEqual(types(L.draw(three)), ["draw"]); assert.equal(three.p, 3); assert.equal(L.waste(three), three.pile[2]);
  assert.equal(L.wasteFan(three).length, 3); assert.equal(L.wasteFan(one).length, 1);
  while (three.p < three.pile.length) L.draw(three);
  assert.equal(three.p, 24);
  assert.deepEqual(types(L.draw(three)), ["recycle"]); assert.equal(three.p, 0); assert.deepEqual(three.pile, L.create({ mode: "draw3", seed: 3 }).pile);
  const empty = fixed([[card(0, 13)]]);
  assert.deepEqual(L.draw(empty), []); assert.match(empty.message, /empty/);
});

test("columns take a card one lower in the other colour; only a king goes on an empty column", () => {
  const s = fixed([[card(0, 9)], [card(1, 8)], [card(3, 8)], [], [card(1, 13)]]);
  assert.equal(L.fits(s, card(1, 8), 0), true);
  assert.equal(L.fits(s, card(2, 8), 0), true);
  assert.equal(L.fits(s, card(0, 8), 0), false, "same colour");
  assert.equal(L.fits(s, card(1, 7), 0), false, "not one lower");
  assert.equal(L.fits(s, card(2, 13), 3), true); assert.equal(L.fits(s, card(2, 12), 3), false);
  const t = fixed([[card(0, 9), card(1, 8)], [card(3, 9)], []], { down: [1] });
  assert.equal(L.fits(t, card(1, 8), 0), false, "a face-down top never takes a card (not reachable in play)");
});

test("moving a run: the next card turns over (+5), the move counts, and bad moves are refused", () => {
  // column 0: face-down 4D under 9S 8H 7C; column 1: 10H
  const s = fixed([[card(2, 4), card(0, 9), card(1, 8), card(3, 7)], [card(1, 10)], []], { down: [1] });
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 0 }, { t: "tab", c: 1 }), [], "face-down cards can't be lifted");
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 2 }, { t: "tab", c: 1 }), [], "8H doesn't go on 10H");
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 1 }, { t: "tab", c: 1 }).map((e) => e.type), ["flip", "move"], "9S 8H 7C goes on 10H");
  assert.deepEqual(s.tab[1], [card(1, 10), card(0, 9), card(1, 8), card(3, 7)]);
  assert.deepEqual(s.tab[0], [card(2, 4)]); assert.equal(s.down[0], 0);
  assert.equal(s.score, 5); assert.equal(s.moves, 1);
  assert.deepEqual(L.move(s, { t: "tab", c: 1, k: 1 }, { t: "tab", c: 1 }), [], "not onto itself");
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 0 }, { t: "tab", c: 2 }), [], "a 4 isn't a king");
  assert.deepEqual(L.move(s, { t: "waste" }, { t: "tab", c: 0 }), [], "nothing on the waste");
  assert.deepEqual(L.move(s, null, { t: "tab", c: 0 }), []);
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 0 }, null), []);
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 0 }, { t: "nowhere" }), []);
});

test("a king moves to an empty column, but a king already at the bottom of a column doesn't bother", () => {
  const s = fixed([[card(2, 4), card(0, 13), card(1, 12)], [card(3, 13)], []], { down: [1] });
  assert.equal(L.move(s, { t: "tab", c: 1, k: 0 }, { t: "tab", c: 2 }).length, 0);
  assert.equal(L.move(s, { t: "tab", c: 0, k: 1 }, { t: "tab", c: 2 }).length > 0, true);
  assert.deepEqual(s.tab[2], [card(0, 13), card(1, 12)]);
  assert.deepEqual(L.tapCard(s, { t: "tab", c: 1, k: 0 }).map((e) => e.type), ["nomove"], "tapping a lone king: nowhere to go");
});

test("foundations: an ace goes to the first empty slot, then the same suit upward; +10 up, -15 back down", () => {
  const s = fixed([[card(1, 2)], [card(1, 1)], [card(2, 1)], [card(1, 3)]], { pile: [card(0, 1)], p: 1 });
  assert.equal(L.slotFor(s, card(1, 2)), -1);
  assert.deepEqual(L.move(s, { t: "tab", c: 1, k: 0 }, { t: "found", f: 2 }), [], "an ace must take the first empty slot");
  assert.deepEqual(types(L.move(s, { t: "tab", c: 1, k: 0 }, { t: "found", f: 0 })), ["foundation"]);
  assert.deepEqual(s.found[0], [card(1, 1)]); assert.equal(s.score, 10);
  assert.equal(L.slotFor(s, card(1, 2)), 0); assert.equal(L.slotFor(s, card(2, 1)), 1); assert.equal(L.slotFor(s, card(1, 3)), -1);
  assert.deepEqual(L.move(s, { t: "tab", c: 0, k: 0 }, { t: "found", f: 1 }), [], "the wrong slot");
  assert.deepEqual(types(L.move(s, { t: "tab", c: 0, k: 0 }, { t: "found", f: 0 })), ["foundation"]);
  assert.equal(s.score, 20);
  assert.deepEqual(types(L.move(s, { t: "waste" }, { t: "found", f: 1 })), ["foundation"]);
  assert.deepEqual(s.pile, []); assert.equal(s.p, 0); assert.equal(s.score, 30);
  assert.deepEqual(L.move(s, { t: "found", f: 0 }, { t: "tab", c: 4 }), [], "a 2 isn't a king");
  s.tab[4] = [card(3, 3)];
  assert.deepEqual(types(L.move(s, { t: "found", f: 0 }, { t: "tab", c: 4 })), ["move"]);
  assert.equal(s.score, 15);
  const zero = fixed([[card(0, 3)]], { found: [[card(1, 1), card(1, 2)], [], [], []] });
  assert.equal(L.move(zero, { t: "found", f: 0 }, { t: "tab", c: 0 }).length, 1);
  assert.equal(zero.score, 0, "never below zero");
  const run = fixed([[card(0, 4), card(1, 3)]]);
  assert.deepEqual(L.move(run, { t: "tab", c: 0, k: 0 }, { t: "found", f: 0 }), [], "two cards can't go up at once");
});

test("from the waste to a column is +5; tapping a card goes up first, else onto a column that takes it", () => {
  const s = fixed([[card(0, 9)], [card(3, 9), card(1, 10)], [card(1, 1)]], { pile: [card(1, 8), card(0, 1)], p: 1 });
  assert.deepEqual(types(L.tapCard(s, { t: "waste" })), ["move"]);
  assert.equal(s.score, 5);
  assert.deepEqual(s.tab[0], [card(0, 9), card(1, 8)], "8H goes on the black nine");
  L.draw(s);
  assert.deepEqual(types(L.tapCard(s, { t: "waste" })), ["foundation"], "an ace prefers the foundation");
  assert.equal(L.total(s), 1);
  const lone = fixed([[card(0, 5)], [card(1, 6)]]);
  assert.deepEqual(types(L.tapCard(lone, { t: "tab", c: 0, k: 0 })), ["move"]);
  assert.deepEqual(types(L.tapCard(lone, { t: "tab", c: 1, k: 1 })), ["nomove"]);
  assert.match(lone.message, /can't go anywhere/);
  assert.deepEqual(L.tapCard(lone, { t: "tab", c: 1, k: 9 }), []);
  assert.deepEqual(L.tapCard(lone, null), []);
});

test("undo puts back the cards, the stock and the score; there are at most 60 steps", () => {
  const s = L.create({ mode: "draw1", seed: 77 });
  const before = JSON.stringify([s.tab, s.down, s.found, s.pile, s.p, s.score, s.moves]);
  L.draw(s); L.draw(s);
  assert.deepEqual(types(L.undo(s)), ["undo"]); L.undo(s);
  assert.equal(JSON.stringify([s.tab, s.down, s.found, s.pile, s.p, s.score, s.moves]), before);
  assert.deepEqual(L.undo(s), []);
  const t = fixed([[card(0, 9)], [card(1, 8)]]);
  L.move(t, { t: "tab", c: 1, k: 0 }, { t: "tab", c: 0 }); assert.equal(t.tab[0].length, 2);
  L.undo(t); assert.deepEqual(t.tab[0], [card(0, 9)]); assert.deepEqual(t.tab[1], [card(1, 8)]);
  const u = fixed([[]], { pile: Array.from({ length: 24 }, (_, i) => i), p: 0 });
  for (let i = 0; i < 100; i++) L.draw(u);
  assert.equal(u.history.length, 60);
  assert.equal(t.stats.undone, 1);
});

test("hint: a move from the search on a real deal, a simple one otherwise; the hint fades", () => {
  const s = L.create({ mode: "draw1", seed: 5 });
  const evs = L.hint(s);
  assert.deepEqual(types(evs), ["hint"]); assert.ok(s.hint && s.hint.text); assert.equal(s.stats.hints, 1);
  assert.equal(s.message, s.hint.text);
  for (let i = 0; i < 400; i++) L.step(s);
  assert.equal(s.hint, null); assert.equal(s.message, "");
  const t = fixed([[card(0, 9)], [card(1, 8)]]);
  L.hint(t);
  assert.deepEqual(t.hint.from, { t: "tab", c: 1, k: 0 }); assert.deepEqual(t.hint.to, { t: "tab", c: 0 });
  assert.match(t.hint.text, /8 of hearts/);
  L.move(t, { t: "tab", c: 1, k: 0 }, { t: "tab", c: 0 }); assert.equal(t.hint, null, "a move clears the hint");
  const stuck = fixed([[card(0, 9)]]);
  L.hint(stuck); assert.deepEqual(stuck.hint.from, { t: "stock" }); assert.match(stuck.hint.text, /No moves are left/);
  const up = fixed([[card(0, 9)]], { pile: [card(0, 1)], p: 0 });
  L.hint(up); assert.match(up.hint.text, /Turn over the stock/);
  const ace = fixed([[card(1, 1)]]);
  L.hint(ace); assert.deepEqual(ace.hint.to, { t: "found", f: 0 }); assert.match(ace.hint.text, /foundation/);
  assert.deepEqual(L.press(s, "hint", false), []);
});

test("every hint on a winnable deal keeps it winnable: following hints wins", () => {
  const s = L.create({ mode: "draw1", seed: 21 });
  for (let i = 0; i < 400 && !s.over; i++) {
    L.hint(s);
    const h = s.hint;
    if (h.from.t === "stock") L.draw(s);
    else L.move(s, h.from, h.to);
  }
  assert.ok(s.won, "following the hints wins (" + L.total(s) + " cards up)");
});

test("auto sends safe cards up; with the stock empty and every card face up it sends all", () => {
  const spades = [card(0, 1), card(0, 2), card(0, 3)];
  const s = fixed([[card(0, 5)], [card(0, 4)]], { found: [spades, [], [], []], pile: [card(2, 9)], p: 1 });
  assert.deepEqual(types(L.auto(s)), ["nomove"], "a 4 with no red 3s up isn't safe");
  assert.match(s.message, /safe/);
  const all = fixed([[card(0, 5)], [card(0, 4)]], { found: [spades, [], [], []] });
  const evs = L.auto(all);
  assert.deepEqual(types(evs), ["foundation", "foundation"]); assert.deepEqual(all.found[0].length, 5); assert.equal(all.score, 20);
  const safe = fixed([[card(0, 2)], [card(1, 1)]], { found: [[card(0, 1)], [], [], []], pile: [card(3, 9)], p: 0, down: [0, 0] });
  assert.equal(types(L.auto(safe)).filter((t) => t === "foundation").length, 2, "an ace and a 2 are always safe");
  const hidden = fixed([[card(2, 7), card(0, 2)], [card(1, 1)]], { down: [1], found: [[card(0, 1)], [], [], []] });
  const h = L.auto(hidden);
  assert.ok(h.some((e) => e.type === "flip"), "turning a card over on the way counts");
  assert.deepEqual(L.press(hidden, "auto", false), []);
});

test("winning: all 52 up scores the cards plus a speed bonus (1,000 at 30 s or less, falling after)", () => {
  const full = (n) => [[], [], [], []].map((_, su) => Array.from({ length: su === 3 ? 12 : 13 }, (_, r) => card(su, r + 1)).slice(0, n));
  const s = fixed([[card(3, 13)]], { found: full(13) });
  for (let i = 0; i < 60 * 10; i++) L.step(s);
  const evs = L.move(s, { t: "tab", c: 0, k: 0 }, { t: "found", f: 3 });
  assert.deepEqual(types(evs), ["foundation", "win"]);
  assert.ok(s.won && s.over); assert.equal(s.score, 10 + 1000);
  assert.ok(s.score <= 2000);
  assert.deepEqual(L.draw(s), []); assert.deepEqual(L.undo(s), []); assert.deepEqual(L.step(s), []); assert.deepEqual(L.press(s, "auto", true), []);
  const slow = fixed([[card(3, 13)]], { found: full(13) });
  slow.updates = 60 * 300; L.move(slow, { t: "tab", c: 0, k: 0 }, { t: "found", f: 3 });
  assert.equal(slow.score, 10 + 100);
  assert.equal(L.bonusOf({ updates: 0 }), 1000); assert.equal(L.bonusOf({ updates: 60 * 60 }), 500); assert.equal(L.bonusOf({ updates: 60 * 60 * 24 }), 20);
  const r = L.result(s);
  assert.equal(r.score, s.score); assert.equal(r.stats.won, true); assert.equal(r.stats.cards, 52);
  assert.match(r.stats.summary[0], /52 of 52/); assert.ok(r.stats.summary.some((l) => /bonus 1000/.test(l)));
  assert.equal(L.status(s).done, true);
});

test("a game ended early keeps its score so far and says how many cards are up", () => {
  const s = L.create({ mode: "draw1", seed: 3 });
  const r = L.result(s);
  assert.equal(r.score, 0); assert.equal(r.stats.won, false); assert.match(r.stats.summary[0], /0 of 52/);
  assert.equal(L.status(s).done, false); assert.equal(L.status(s).over, false);
});

test("save and restore keep the game; bad data is refused", () => {
  const s = L.create({ mode: "draw3", seed: 9 });
  L.draw(s); L.draw(s); L.hint(s);
  for (let i = 0; i < 300; i++) L.step(s);
  const data = JSON.parse(JSON.stringify(L.save(s)));
  const c = L.restore(data);
  assert.deepEqual(c.tab, s.tab); assert.deepEqual(c.down, s.down); assert.deepEqual(c.pile, s.pile); assert.equal(c.p, s.p);
  assert.equal(c.score, s.score); assert.equal(c.updates, s.updates); assert.equal(c.hint, null); assert.equal(c.draw, 3);
  assert.equal(c.history.length, 2); L.undo(c); assert.equal(c.p, 3);
  const bad = (f) => { const d = JSON.parse(JSON.stringify(data)); f(d); assert.throws(() => L.restore(d), /can't be continued/); };
  assert.throws(() => L.restore(null), /can't be continued/);
  bad((d) => { d.mode = "nope"; }); bad((d) => { d.draw = 1; });
  bad((d) => { d.tab[0][0] = d.tab[1][0]; });
  bad((d) => { d.pile.pop(); });
  bad((d) => { d.p = 99; }); bad((d) => { d.p = 0.5; });
  bad((d) => { d.down[6] = 9; }); bad((d) => { d.down[0] = 1; });
  bad((d) => { d.tab.pop(); });
  bad((d) => { const id = d.pile.pop(); d.found[0].push(id); });
  bad((d) => { d.updates = -3; }); bad((d) => { d.score = "x"; });
  bad((d) => { d.history = [5]; });
  bad((d) => { d.tab[0][0] = 99; });
});

test("a full set of foundations can't be restored as a game in progress", () => {
  const s = fixed([[]], { found: [0, 1, 2, 3].map((su) => Array.from({ length: 13 }, (_, r) => card(su, r + 1))) });
  s.history = []; s.stats = { cause: "", undone: 0, hints: 0, flips: 0 };
  assert.throws(() => L.restore(JSON.parse(JSON.stringify(s))), /can't be continued/);
});

test("registry contract", () => {
  const sb = makeSandbox({ extra: ["solitaire-logic.js", "solitaire.js"] });
  const def = sb.win.ArcadeGames.get("solitaire");
  assert.equal(def.controls, "touch");
  assert.deepEqual(Array.from(def.modes, (m) => m.id), ["draw1", "draw3"]);
  assert.deepEqual(Array.from(def.buttons, (b) => b.action), ["undo", "hint", "auto"]);
});

for (const look of LOOKS) {
  test(`solitaire renders and plays in the ${look} look with taps and drags`, () => {
    const sb = makeSandbox({ extra: ["solitaire-logic.js", "solitaire.js"] });
    const def = sb.win.ArcadeGames.get("solitaire");
    const canvas = sb.canvas(390, 487);
    const ends = [], events = [];
    const inst = def.create(canvas, { mode: "draw3", look, seed: 6, onEnd: (r) => ends.push(r), onEvent: (t) => events.push(t) });
    inst.start();
    const G = inst.logic, k = 390 / 240;
    const colX = (c) => 3.5 + c * 33.8 + 15;
    const P = (x, y) => [x * k, y * k];
    const tapAt = (x, y) => { inst.pointer("down", ...P(x, y)); inst.pointer("up", ...P(x, y)); sb.frames(2); };
    tapAt(colX(0), 55); assert.equal(G.p, 3); assert.ok(events.includes("draw"));
    tapAt(colX(0), 55); tapAt(colX(0), 55);
    inst.pointer("down", 1, 1); inst.pointer("up", 1, 1); inst.pointer("move", 5, 5);
    // drag the last card of column 6 somewhere else, and drop it nowhere in particular
    inst.pointer("down", ...P(colX(6), 86 + 6 * 4 + 5 + 4)); sb.frames(1);
    inst.pointer("move", ...P(colX(4), 150)); sb.frames(2);
    inst.pointer("move", ...P(colX(3), 120)); sb.frames(2);
    inst.pointer("up", ...P(colX(3), 120)); sb.frames(2);
    inst.input("hint", true); sb.frames(5);
    inst.input("undo", true); inst.input("auto", true); sb.frames(5);
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    canvas.clientWidth = 390; canvas.clientHeight = 487; inst.resize(); sb.frames(2);
    // the last king: tap it up and win
    G.found = [0, 1, 2, 3].map((su) => Array.from({ length: su === 3 ? 12 : 13 }, (_, r) => su * 13 + r));
    G.tab = [[51], [], [], [], [], [], []]; G.down = [0, 0, 0, 0, 0, 0, 0]; G.pile = []; G.p = 0; G.hint = null;
    sb.frames(3);
    tapAt(colX(0), 86 + 15);
    sb.frames(5);
    assert.equal(ends.length, 1);
    assert.ok(ends[0].stats.won); assert.ok(ends[0].score >= 1000);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
    assert.equal(sb.pending(), 0);
  });
}

test("dragging a card onto a column moves it there; onto a foundation sends it up", () => {
  const sb = makeSandbox({ extra: ["solitaire-logic.js", "solitaire.js"] });
  const def = sb.win.ArcadeGames.get("solitaire");
  const inst = def.create(sb.canvas(390, 487), { mode: "draw1", look: "modern", seed: 6 });
  inst.start();
  const G = inst.logic, k = 390 / 240, colX = (c) => 3.5 + c * 33.8 + 15, P = (x, y) => [x * k, y * k];
  // 9S in column 0, 8H in column 1, AS in column 2
  G.down = [0, 0, 0, 0, 0, 0, 0];
  G.tab[0] = [card(0, 9)]; G.tab[1] = [card(1, 8)]; G.tab[2] = [card(0, 1)];
  G.pile = []; G.p = 0; G.found = [[], [], [], []];
  const drag = (x0, y0, x1, y1) => { inst.pointer("down", ...P(x0, y0)); inst.pointer("move", ...P((x0 + x1) / 2, (y0 + y1) / 2)); inst.pointer("move", ...P(x1, y1)); inst.pointer("up", ...P(x1, y1)); };
  drag(colX(1), 100, colX(0), 120);
  assert.deepEqual(G.tab[0], [card(0, 9), card(1, 8)]); assert.equal(G.tab[1].length, 0);
  drag(colX(2), 100, colX(5), 55);
  assert.deepEqual(G.found[0], [card(0, 1)]); assert.equal(G.score, 10);
  const before = JSON.stringify(G.tab);
  drag(colX(0), 100, colX(5), 120);       // an illegal drop: the card goes back
  assert.equal(JSON.stringify(G.tab), before);
  inst.pointer("down", ...P(colX(3), 200)); inst.pointer("up", ...P(colX(3), 200));
  inst.destroy();
});
