"""Playing turn by turn from two (or up to four) phones (SPEC §13.5): invites (7 days, several at once, not in the way of
other games), the start, moves checked by the server (out of turn, illegal, a stale board, strangers), children's
limits on every move, play time on the match's page, Sea Battle's hidden fleets through the API, resigning, 7 days
without a move, the end and the scores, your-move notifications (throttled, quiet hours, the switches) with the fake
Home Assistant, and the 2–4 player plumbing (seats, starting with those who joined, server dice, the computer
standing in for a player who left) with a dummy dice game."""
import _env  # noqa: F401  (must be first)

import json
import types

from app import config, db, games, housekeeping, rules, settings, turns
from base import ASHA, DEV, KABIR, MEERA, ApiBase


def seats_of(test, mid):
    p = test.get(f"/api/matches/{mid}/turns", KABIR).json()
    return {pl["seat"]: {"u_kabir": KABIR, "u_meera": MEERA, "u_asha": ASHA, "u_dev": DEV}[pl["id"]] for pl in p["players"]}


class Turns(ApiBase):
    def invite(self, game="tictactoe", who=KABIR, to=("u_meera",), mode="phones", **extra):
        return self.post("/api/matches", {"game": game, "mode": mode, "opponents": list(to), **extra}, who)

    def new_match(self, game="tictactoe", a=KABIR, b=MEERA, **extra):
        r = self.invite(game, a, (b["X-Remote-User-Id"],), **extra)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        r = self.post(f"/api/matches/{mid}/accept", None, b)
        self.assertEqual(r.status_code, 200, r.text)
        return mid

    def pic(self, mid, who=KABIR):
        r = self.get(f"/api/matches/{mid}/turns", who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def mover(self, mid):
        p = self.pic(mid)
        return seats_of(self, mid)[p["toMove"][0]], p

    def move(self, mid, move, who=None, **extra):
        if who is None:
            who, _ = self.mover(mid)
        return self.post(f"/api/matches/{mid}/move", {"move": move, **extra}, who)


class TestInvites(Turns):
    def test_kind_from_the_mode_and_seven_days(self):
        r = self.invite()
        self.assertEqual(r.status_code, 201, r.text)
        m = r.json()
        self.assertEqual((m["kind"], m["status"]), ("turns", "invited"))
        self.assertGreater(m["expiresIn"], 6 * 86400)
        self.assertEqual(m["turns"]["maxPlayers"], 2)
        self.assertEqual(self.get("/api/players?game=tictactoe&mode=phones").json()["kind"], "turns")
        self.assertEqual(self.get("/api/players?game=tictactoe").json()["kind"], "turns")
        self.clock.advance(days=6, hours=23)
        self.assertEqual(self.get(f"/api/matches/{m['id']}").json()["status"], "invited")
        self.clock.advance(hours=2)
        self.assertEqual(self.get(f"/api/matches/{m['id']}").json()["status"], "expired")

    def test_validation(self):
        self.assertEqual(self.invite(mode="hard").status_code, 409)                        # can't be raced
        self.assertEqual(self.invite(mode="hard", kind="turns").status_code, 422)          # not a two-phone mode
        self.assertEqual(self.invite(kind="race").status_code, 409)                         # not raced
        self.assertEqual(self.invite(kind="live").status_code, 422)                        # no such kind (1.8.0)
        self.assertEqual(self.invite(to=("u_meera", "u_asha")).status_code, 422)            # two players only
        self.assertEqual(self.invite(to=("u_kabir",)).status_code, 422)
        self.assertEqual(self.invite(to=("u_nobody",)).status_code, 404)
        self.assertEqual(self.post("/api/matches", {"game": "snake", "mode": "walls-normal", "opponents": ["u_meera"],
                                                    "kind": "turns"}).status_code, 409)

    def test_several_at_once_and_not_in_the_way_of_other_games(self):
        a = self.invite("tictactoe").json()["id"]
        b = self.invite("fourrow").json()["id"]
        self.assertNotEqual(a, b)
        self.post(f"/api/matches/{a}/accept", None, MEERA)
        self.post(f"/api/matches/{b}/accept", None, MEERA)
        # a race invite and ordinary games still work while two turn-by-turn matches are on
        r = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_meera"], "kind": "race"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.start("snake").status_code, 201)
        playing = self.get("/api/matches", MEERA).json()["playing"]
        self.assertEqual(sorted(m["game"] for m in playing if m["kind"] == "turns"), ["fourrow", "tictactoe"])

    def test_children(self):
        self.make_child(MEERA, quietSchoolFrom="11:00", quietSchoolTo="13:00")
        # quiet hours don't stop an invite to a game played over days …
        players = {p["id"]: p for p in self.get("/api/players?game=tictactoe&mode=phones").json()["players"]}
        self.assertTrue(players["u_meera"]["canPlay"])
        mid = self.new_match()
        # … but they do stop a move
        meera_seat = next(s for s, w in seats_of(self, mid).items() if w is MEERA)
        if self.pic(mid)["toMove"] != [meera_seat]:
            self.assertEqual(self.move(mid, {"cell": 0}).status_code, 200)
        r = self.move(mid, {"cell": 4}, MEERA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("break", r.json()["detail"])
        self.clock.advance(hours=1, minutes=5)
        self.assertEqual(self.move(mid, {"cell": 4}, MEERA).status_code, 200)
        # a game that isn't one of theirs can't be played at all
        self.put("/api/admin/users/u_meera/limits", {"allowedGames": ["snake"]}, ASHA)
        self.assertEqual(self.invite("fourrow").status_code, 409)

    def test_invited_game_switched_off(self):
        mid = self.new_match()
        self.settings({"disabled_games": ["tictactoe"]})
        who, _ = self.mover(mid)
        self.assertEqual(self.move(mid, {"cell": 0}, who).status_code, 409)


class TestMoves(Turns):
    def test_the_start(self):
        mid = self.new_match()
        p = self.pic(mid)
        self.assertEqual(p["status"], "playing")
        self.assertEqual(p["number"], 0)
        self.assertEqual(sorted(pl["seat"] for pl in p["players"]), [1, 2])
        with db.get_conn() as conn:
            seed = conn.execute("SELECT seed FROM matches WHERE id = ?", (mid,)).fetchone()["seed"]
        self.assertEqual(p["toMove"], [seed % 2 + 1])              # who moves first: from the seed
        self.assertEqual(p["seed"], seed)
        self.assertEqual(p["view"]["board"], [0] * 9)
        m = self.get("/api/matches", KABIR).json()["playing"][0]
        self.assertEqual(m["turns"]["number"], 0)
        self.assertEqual(m["turns"]["myTurn"], p["toMove"] == [p["seat"]])

    def test_out_of_turn_illegal_stale_strangers(self):
        mid = self.new_match()
        who, p = self.mover(mid)
        other = MEERA if who is KABIR else KABIR
        self.assertEqual(self.move(mid, {"cell": 4}, other).status_code, 409)              # not your move
        for bad in ({"cell": 9}, {"cell": "4"}, {"x": 1}, [], "4", None):
            r = self.post(f"/api/matches/{mid}/move", {"move": bad}, who)
            self.assertEqual(r.status_code, 422, (bad, r.text))
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {}, who).status_code, 422)
        self.assertEqual(self.move(mid, {"cell": 4}, who, n=3).status_code, 409)            # the board moved on
        self.assertEqual(self.move(mid, {"cell": 4}, DEV).status_code, 404)                 # a stranger
        self.assertEqual(self.get(f"/api/matches/{mid}/turns", DEV).status_code, 404)
        r = self.move(mid, {"cell": 4}, who, n=0)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["number"], 1)
        self.assertEqual(self.move(mid, {"cell": 4}, other).status_code, 422)               # taken
        self.assertEqual(self.move(mid, {"cell": 0}, who).status_code, 409)                 # twice in a row
        with db.get_conn() as conn:
            rows = conn.execute("SELECT number, seat, move FROM match_moves WHERE match_id = ?", (mid,)).fetchall()
        self.assertEqual([(r["number"], json.loads(r["move"])) for r in rows], [(1, {"cell": 4})])

    def test_a_whole_game_scores_and_head_to_head(self):
        mid = self.new_match()
        first, _ = self.mover(mid)
        second = MEERA if first is KABIR else KABIR
        sess = self.post("/api/sessions", {"game": "tictactoe", "matchId": mid}, first).json()
        self.assertEqual(sess["mode"], "phones")
        self.clock.advance(seconds=40)
        self.post(f"/api/sessions/{sess['id']}/end", {"activeSeconds": 40}, first)
        # a turn-by-turn match's session never takes a score
        sess2 = self.post("/api/sessions", {"game": "tictactoe", "matchId": mid}, first).json()
        self.assertEqual(self.post("/api/scores", {"sessionId": sess2["id"], "score": 600, "level": 1, "seconds": 3}, first).status_code, 409)
        for cell, who in ((0, first), (3, second), (1, first), (4, second)):
            self.assertEqual(self.move(mid, {"cell": cell}, who).status_code, 200)
        r = self.move(mid, {"cell": 2}, first).json()
        self.assertTrue(r["over"])
        self.assertEqual(r["result"]["winner"], r["seat"])
        m = self.get(f"/api/matches/{mid}", first).json()
        self.assertEqual((m["status"], m["endReason"]), ("done", "finished"))
        me = next(p for p in m["players"] if p["you"])
        self.assertEqual((me["result"], me["score"]), ("won", 500 + 25 * 4))
        self.assertGreaterEqual(me["seconds"], 40)
        mine = self.get("/api/scores/mine", first).json()["recent"]
        self.assertEqual((mine[0]["game"], mine[0]["mode"], mine[0]["score"]), ("tictactoe", "phones", 600))
        self.assertEqual(self.get("/api/scores/mine", second).json()["recent"], [])        # a loss keeps nothing
        vs = self.get("/api/against", first).json()["people"][0]
        self.assertEqual((vs["games"], vs["wins"]), (1, 1))
        self.assertEqual(self.move(mid, {"cell": 5}, second).status_code, 409)               # it's over
        self.assertEqual(self.post("/api/sessions", {"game": "tictactoe", "matchId": mid}, first).status_code, 409)

    def test_practice_keeps_no_score(self):
        mid = self.new_match(practice=True)
        first, _ = self.mover(mid)
        second = MEERA if first is KABIR else KABIR
        for cell, who in ((0, first), (3, second), (1, first), (4, second), (2, first)):
            self.move(mid, {"cell": cell}, who)
        self.assertEqual(self.get("/api/scores/mine", first).json()["recent"], [])

    def test_children_time_on_the_page_counts(self):
        self.make_child(MEERA, minutesSchool=30)
        mid = self.new_match()
        s = self.post("/api/sessions", {"game": "tictactoe", "matchId": mid}, MEERA).json()
        self.clock.advance(minutes=31)
        self.post(f"/api/sessions/{s['id']}/end", {"activeSeconds": 31 * 60}, MEERA)
        self.assertEqual(self.post("/api/sessions", {"game": "tictactoe", "matchId": mid}, MEERA).status_code, 409)
        meera_seat = next(s_ for s_, w in seats_of(self, mid).items() if w is MEERA)
        if self.pic(mid)["toMove"] != [meera_seat]:
            self.move(mid, {"cell": 0})
        r = self.move(mid, {"cell": 4}, MEERA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("No play time left", r.json()["detail"])
        self.assertEqual(self.get(f"/api/matches/{mid}/turns", MEERA).status_code, 200)     # the board can still be seen

    def test_resign(self):
        mid = self.new_match()
        r = self.post(f"/api/matches/{mid}/resign", None, MEERA)
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "resigned", "u_kabir"))
        self.assertEqual(self.get("/api/scores/mine", KABIR).json()["recent"][0]["score"], 500)
        p = self.pic(mid, KABIR)
        self.assertEqual(p["result"], {"winner": p["seat"], "reason": "resigned"})
        self.assertEqual(self.post(f"/api/matches/{mid}/resign", None, KABIR).status_code, 409)

    def test_seven_days_without_a_move(self):
        mid = self.new_match()
        who, _ = self.mover(mid)
        self.move(mid, {"cell": 4}, who)
        stale, _ = self.mover(mid)
        self.clock.advance(days=6, hours=23)
        housekeeping.run_blocking()
        self.assertEqual(self.get(f"/api/matches/{mid}").json()["status"], "playing")
        self.clock.advance(hours=2)
        housekeeping.run_blocking()
        m = self.get(f"/api/matches/{mid}").json()
        self.assertEqual((m["status"], m["endReason"]), ("done", "timeout"))
        self.assertEqual(m["winner"], who["X-Remote-User-Id"])
        self.assertNotEqual(m["winner"], stale["X-Remote-User-Id"])

    def test_nobody_moved_no_result(self):
        mid = self.new_match("seabattle")
        self.clock.advance(days=8)
        m = self.get(f"/api/matches/{mid}").json()
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "timeout", None))


FLEET = [[0, 0, 5, 0], [2, 0, 4, 0], [4, 0, 3, 0], [6, 0, 3, 0], [8, 0, 2, 0]]
FLEET_B = [[0, 9, 5, 1], [1, 7, 4, 1], [5, 5, 3, 0], [7, 5, 3, 0], [9, 0, 2, 0]]


class TestSeaBattleHiddenFleets(Turns):
    def cells(self, fleet):
        return [r * 10 + c + (i * 10 if d else i) for r, c, n, d in fleet for i in range(n)]

    def test_the_api_never_sends_the_other_fleet(self):
        mid = self.new_match("seabattle", options={"fleet": "classic"})
        self.assertEqual(self.pic(mid)["options"], {"fleet": "classic"})
        self.assertEqual(sorted(self.pic(mid)["toMove"]), [1, 2])                          # both place
        self.assertEqual(self.move(mid, {"place": FLEET}, KABIR).status_code, 200)
        self.assertEqual(self.move(mid, {"place": FLEET}, KABIR).status_code, 409)          # once
        pm = self.pic(mid, MEERA)
        self.assertEqual(pm["view"]["placed"].count(True), 1)
        self.assertNotIn(str(FLEET[0]), json.dumps(pm))
        self.assertEqual([m["move"] for m in pm["moves"]], [{"placed": True}])
        self.assertEqual(self.move(mid, {"place": FLEET_B}, MEERA).status_code, 200)
        kabir, meera = set(self.cells(FLEET)), set(self.cells(FLEET_B))
        shots = {"u_kabir": iter(range(100)), "u_meera": iter(range(99, -1, -1))}
        n = 0
        while True:
            p = self.pic(mid)
            if p["over"]:
                break
            who = seats_of(self, mid)[p["toMove"][0]]
            r = self.move(mid, {"shot": next(shots[who["X-Remote-User-Id"]])}, who)
            self.assertEqual(r.status_code, 200, r.text)
            n += 1
            for viewer, theirs in ((KABIR, meera), (MEERA, kabir)):
                pv = self.pic(mid, viewer)
                v = pv["view"]
                # everything this phone gets: its own fleet, the shots and what they hit, the ships it sank
                known = {c for c, hit in v["myShots"] if hit} | {c for ship in v["sunk"] for c in ship}
                sent = json.dumps(pv)
                self.assertNotIn("theirShips", v)
                hidden = theirs - known
                self.assertNotIn(str(sorted(theirs)), sent)
                self.assertTrue(all(c in theirs for c in known))
                self.assertTrue(hidden)
            if n > 10:
                break
        # the whole fleet is only shown when it's over
        self.post(f"/api/matches/{mid}/resign", None, MEERA)
        v = self.pic(mid, KABIR)["view"]
        self.assertEqual(sorted(c for s in v["theirShips"] for c in s), sorted(meera))


class TestNotifications(Turns):
    def setUp(self):
        super().setUp()
        self.ha.notify_services = ["meera_phone", "kabir_phone"]
        self.post("/api/admin/users/u_meera/notify", {"service": "notify.meera_phone"}, ASHA)
        self.post("/api/admin/users/u_kabir/notify", {"service": "notify.kabir_phone"}, ASHA)
        self._panel = config.INGRESS_PANEL
        config.INGRESS_PANEL = "/local_household_arcade"

    def tearDown(self):
        config.INGRESS_PANEL = self._panel

    def turn_notes(self):
        return [(n, b) for n, b in self.ha.notifications() if "Your move" in b["message"]]

    def test_your_move_once_every_fifteen_minutes(self):
        mid = self.new_match()
        first, _ = self.mover(mid)
        second = MEERA if first is KABIR else KABIR
        svc, svc_first = ("meera_phone", "kabir_phone") if second is MEERA else ("kabir_phone", "meera_phone")
        self.clock.advance(minutes=16)                                # (the start's own notification is long past)
        self.ha.requests.clear()
        self.move(mid, {"cell": 4}, first)
        notes = self.turn_notes()
        self.assertEqual(len(notes), 1)
        other = "Kabir" if first is KABIR else "Meera"
        self.assertEqual(notes[0][0], svc)
        self.assertEqual(notes[0][1]["message"], f"Your move in Tic-tac-toe against {other}.")
        self.assertEqual(notes[0][1]["data"]["url"], f"/local_household_arcade/home/turn/{mid}")
        self.assertEqual(notes[0][1]["data"]["tag"], f"arcade-turn-{mid}")
        self.ha.requests.clear()
        self.move(mid, {"cell": 0}, second)
        self.assertEqual([n for n, _ in self.turn_notes()], [svc_first])
        self.ha.requests.clear()
        self.move(mid, {"cell": 8}, first)                            # within 15 minutes of the last: owed, not sent
        self.assertEqual(self.turn_notes(), [])
        housekeeping.run_blocking()
        self.assertEqual(self.turn_notes(), [])
        self.clock.advance(minutes=16)
        housekeeping.run_blocking()
        self.assertEqual([n for n, _ in self.turn_notes()], [svc])
        self.ha.requests.clear()
        self.clock.advance(minutes=16)
        housekeeping.run_blocking()
        self.assertEqual(self.turn_notes(), [])                       # once

    def test_opening_the_match_settles_it(self):
        mid = self.new_match()
        first, _ = self.mover(mid)
        second = MEERA if first is KABIR else KABIR
        self.move(mid, {"cell": 4}, first)
        self.move(mid, {"cell": 0}, second)
        self.move(mid, {"cell": 8}, first)                            # owed to `second`
        self.get(f"/api/matches/{mid}/turns", second)                # they looked
        self.ha.requests.clear()
        self.clock.advance(minutes=20)
        housekeeping.run_blocking()
        self.assertEqual(self.turn_notes(), [])

    def test_quiet_hours_wait(self):
        self.make_child(MEERA, quietSchoolFrom="11:00", quietSchoolTo="13:00")
        mid = self.new_match()
        meera_seat = next(s for s, w in seats_of(self, mid).items() if w is MEERA)
        self.ha.requests.clear()
        if self.pic(mid)["toMove"] != [meera_seat]:
            self.move(mid, {"cell": 4}, KABIR)
        else:
            with db.get_conn() as conn:
                conn.execute("UPDATE match_players SET notify_due = 1 WHERE match_id = ? AND user_id = 'u_meera'", (mid,))
            turns.notify_blocking(mid)
        self.assertEqual(self.turn_notes(), [])                       # noon: quiet hours
        self.clock.advance(hours=1, minutes=5)
        housekeeping.run_blocking()
        self.assertEqual([n for n, _ in self.turn_notes()], ["meera_phone"])

    def test_the_switches(self):
        self.settings({"notify_turns": False})
        mid = self.new_match()
        first, _ = self.mover(mid)
        self.ha.requests.clear()
        self.move(mid, {"cell": 4}, first)
        self.assertEqual(self.turn_notes(), [])
        self.settings({"notify_turns": True})
        second = MEERA if first is KABIR else KABIR
        self.put("/api/prefs", {"receiveNotifications": False}, first)
        self.move(mid, {"cell": 0}, second)
        self.assertEqual(self.turn_notes(), [])
        self.assertTrue(self.get("/api/admin/settings", ASHA).json()["defaults"]["notify_turns"])

    def test_the_first_move_after_the_start(self):
        r = self.invite()
        mid = r.json()["id"]
        self.ha.requests.clear()
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        first, _ = self.mover(mid)
        # the one who just joined isn't told; the inviter is, when it's theirs
        expected = ["kabir_phone"] if first is KABIR else []
        self.assertEqual([n for n, _ in self.turn_notes()], expected)


# ---------------------------------------------------------------------------
# 2–4 players: a dummy dice game ("race to 20": roll and move; the server rolls)
# ---------------------------------------------------------------------------

def _dummy():
    m = types.ModuleType("app.rules.dicerace")
    m.PLAYERS, m.OPTIONS, m.DICE, m.BASE = (2, 4), {}, 6, 100

    def new(seed, players=2, options=None):
        return {"n": players, "turn": int(seed) % players, "pos": [0] * players, "over": False, "winner": None, "order": []}

    def to_move(st):
        return [] if st["over"] else [st["turn"]]

    def moves(st, seat):
        return [] if st["over"] or seat != st["turn"] else [{"go": True}]

    def apply(st, seat, move):
        if st["over"] or seat != st["turn"]:
            raise rules.Illegal("It isn't your move.")
        if move != {"go": True}:
            raise rules.Illegal("Send {go: true}.")
        roll = st.get("_roll")
        if not roll or roll["seat"] != seat + 1:
            raise rules.Illegal("Roll first.")
        s = rules.copy(st)
        s["pos"][seat] += roll["value"]
        if s["pos"][seat] >= 20:
            s["over"], s["winner"] = True, seat
        else:
            s["turn"] = (seat + 1) % s["n"]
        return s, {"go": True, "to": s["pos"][seat]}

    def result(st):
        if not st["over"]:
            return None
        order = sorted(range(st["n"]), key=lambda i: -st["pos"][i])
        return {"winner": st["winner"], "places": order}

    m.new, m.to_move, m.moves, m.apply, m.result = new, to_move, moves, apply, result
    m.score = lambda st, seat: 100 if st["winner"] == seat else 0
    m.view = lambda st, seat: {"pos": st["pos"], "turn": st["turn"]}
    m.visible = lambda rec, seat, mover: rec
    m.digest = lambda st: st
    m.computer = lambda st, seat, rnd: {"go": True}
    return m


class TestManyPlayers(Turns):
    def setUp(self):
        super().setUp()
        self.mod = _dummy()
        rules.register("dicerace", self.mod)
        games.GAMES["dicerace"] = {"name": "Dice Race", "icon": "🎲", "modes": [{"id": "phones", "label": "Phones"}],
                                   "level_modes": [], "default_mode": "phones", "state_version": 0, "race": False,
                                   "turns": {"modes": ["phones"]}, "unfinished_zero": True,
                                   "max_score": 100, "per_second": 100, "base": 100}
        self.get("/api/me", DEV)

    def tearDown(self):
        rules.REGISTRY.pop("dicerace", None)
        games.GAMES.pop("dicerace", None)

    def four(self):
        r = self.invite("dicerace", KABIR, ("u_meera", "u_asha", "u_dev"))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def test_up_to_three_others(self):
        self.assertEqual(self.get("/api/players?game=dicerace").json()["maxPlayers"], 4)
        r = self.invite("dicerace", KABIR, ("u_meera", "u_asha", "u_dev", "u_kabir"))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.invite("dicerace", KABIR, ("u_meera", "u_meera")).status_code, 422)

    def test_starts_when_all_have_joined(self):
        mid = self.four()
        for who in (MEERA, ASHA):
            self.post(f"/api/matches/{mid}/accept", None, who)
        self.assertEqual(self.get(f"/api/matches/{mid}").json()["status"], "invited")
        # joined, waiting for the others: on the sent list, not waiting-for-you
        self.assertEqual(len(self.get("/api/matches", MEERA).json()["sent"]), 1)
        self.post(f"/api/matches/{mid}/accept", None, DEV)
        p = self.pic(mid)
        self.assertEqual(p["status"], "playing")
        self.assertEqual([pl["seat"] for pl in p["players"]], [1, 2, 3, 4])

    def test_start_with_those_who_joined(self):
        mid = self.four()
        self.assertEqual(self.post(f"/api/matches/{mid}/start", None, KABIR).status_code, 409)     # nobody yet
        self.post(f"/api/matches/{mid}/accept", None, ASHA)
        self.assertEqual(self.post(f"/api/matches/{mid}/start", None, ASHA).status_code, 409)      # not the inviter
        self.post(f"/api/matches/{mid}/decline", None, DEV)
        self.assertEqual(self.get(f"/api/matches/{mid}").json()["status"], "invited")              # Meera may still join
        r = self.post(f"/api/matches/{mid}/start", None, KABIR)
        self.assertEqual(r.status_code, 200, r.text)
        p = self.pic(mid)
        self.assertEqual([(pl["seat"], pl["id"]) for pl in p["players"]], [(1, "u_kabir"), (2, "u_asha")])
        self.assertEqual(self.get(f"/api/matches/{mid}/turns", MEERA).status_code, 404)

    def test_all_decline_or_the_last_one(self):
        mid = self.four()
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.post(f"/api/matches/{mid}/decline", None, ASHA)
        self.post(f"/api/matches/{mid}/decline", None, DEV)
        self.assertEqual(self.get(f"/api/matches/{mid}").json()["status"], "playing")     # the last answer: play
        mid2 = self.invite("dicerace", KABIR, ("u_meera", "u_asha")).json()["id"]
        self.post(f"/api/matches/{mid2}/decline", None, MEERA)
        self.post(f"/api/matches/{mid2}/decline", None, ASHA)
        self.assertEqual(self.get(f"/api/matches/{mid2}").json()["status"], "declined")

    def test_server_dice(self):
        mid = self.four()
        for who in (MEERA, ASHA, DEV):
            self.post(f"/api/matches/{mid}/accept", None, who)
        who, p = self.mover(mid)
        others = [w for w in (KABIR, MEERA, ASHA, DEV) if w is not who]
        self.assertEqual(self.post(f"/api/matches/{mid}/roll", None, others[0]).status_code, 409)   # not theirs
        r = self.move(mid, {"go": True}, who)
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "Roll the dice first."))
        rolled = self.post(f"/api/matches/{mid}/roll", None, who).json()
        roll = {k: rolled[k] for k in ("roll", "seat")}
        self.assertTrue(1 <= roll["roll"] <= 6)
        self.assertFalse(rolled["moved"])                            # the dummy game has no forced moves
        self.assertEqual(rolled["picture"]["roll"]["value"], roll["roll"])
        for _ in range(5):                                           # asking again gives the same roll
            again = self.post(f"/api/matches/{mid}/roll", None, who).json()
            self.assertEqual({k: again[k] for k in ("roll", "seat")}, roll)
        self.assertEqual(self.pic(mid, who)["roll"]["value"], roll["roll"])
        self.assertIsNone(self.pic(mid, others[0])["roll"])
        out = self.move(mid, {"go": True}, who).json()
        self.assertEqual(out["moves"][0]["dice"], roll["roll"])
        self.assertEqual(out["moves"][0]["move"]["to"], roll["roll"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT dice FROM match_moves WHERE match_id = ?", (mid,)).fetchone()["dice"], roll["roll"])
        # a phone can't send its own roll
        nxt, _ = self.mover(mid)
        r = self.post(f"/api/matches/{mid}/move", {"move": {"go": True, "roll": 6}}, nxt)
        self.assertIn(r.status_code, (409, 422))
        tm = self.post("/api/matches", {"game": "tictactoe", "mode": "phones", "opponents": ["u_meera"]}).json()["id"]
        self.post(f"/api/matches/{tm}/accept", None, MEERA)
        w, _ = self.mover(tm)
        self.assertEqual(self.post(f"/api/matches/{tm}/roll", None, w).status_code, 409)       # no dice in that game

    def test_a_leaver_is_played_by_the_computer(self):
        mid = self.invite("dicerace", KABIR, ("u_meera", "u_asha")).json()["id"]
        for who in (MEERA, ASHA):
            self.post(f"/api/matches/{mid}/accept", None, who)
        r = self.post(f"/api/matches/{mid}/resign", None, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()
        self.assertEqual(m["status"], "playing")                     # the others play on
        asha = next(p for p in m["players"] if p["id"] == "u_asha")
        self.assertTrue(asha["computer"] and asha["left"])
        self.assertEqual(asha["result"], "lost")
        self.assertEqual(self.get(f"/api/matches/{mid}/turns", ASHA).status_code, 200)
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {"move": {"go": True}}, ASHA).status_code, 409)
        asha_seat = asha["seat"]
        guard = 0
        while guard < 60:
            p = self.pic(mid)
            if p["over"]:
                break
            self.assertNotEqual(p["toMove"], [asha_seat])            # the computer's seat never waits for a person
            who = seats_of(self, mid)[p["toMove"][0]]
            self.post(f"/api/matches/{mid}/roll", None, who)
            self.assertEqual(self.move(mid, {"go": True}, who).status_code, 200)
            guard += 1
        p = self.pic(mid)
        self.assertTrue(p["over"])
        by_asha = [mv for mv in p["moves"] if mv["seat"] == asha_seat]
        self.assertTrue(by_asha and all(mv["dice"] for mv in by_asha))     # the computer rolled the server's dice
        m = self.get(f"/api/matches/{mid}").json()
        self.assertNotEqual(m["winner"], "u_asha")
        self.assertIn(m["winner"], ("u_kabir", "u_meera"))
        # the last two: one gives up, the other wins
        mid2 = self.invite("dicerace", KABIR, ("u_meera", "u_asha")).json()["id"]
        for who in (MEERA, ASHA):
            self.post(f"/api/matches/{mid2}/accept", None, who)
        self.post(f"/api/matches/{mid2}/resign", None, ASHA)
        m2 = self.post(f"/api/matches/{mid2}/resign", None, MEERA).json()
        self.assertEqual((m2["status"], m2["winner"]), ("done", "u_kabir"))

    def test_timeout_with_more_players(self):
        mid = self.invite("dicerace", KABIR, ("u_meera", "u_asha")).json()["id"]
        for who in (MEERA, ASHA):
            self.post(f"/api/matches/{mid}/accept", None, who)
        stale, p = self.mover(mid)
        self.clock.advance(days=7, minutes=1)
        housekeeping.run_blocking()
        m = self.get(f"/api/matches/{mid}").json()
        self.assertEqual(m["status"], "playing")
        gone = next(pl for pl in m["players"] if pl["id"] == stale["X-Remote-User-Id"])
        self.assertTrue(gone["computer"])
        self.assertNotEqual(self.pic(mid)["toMove"], [gone["seat"]])


class TestMigration(ApiBase):
    def test_tables(self):
        with db.get_conn() as conn:
            self.assertEqual(db.schema_version(conn), 8)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(match_moves)")}
            self.assertEqual(cols, {"match_id", "number", "seat", "move", "dice", "at"})
            m = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
            self.assertTrue({"options", "state", "turn_at"} <= m)
            p = {r[1] for r in conn.execute("PRAGMA table_info(match_players)")}
            self.assertTrue({"notified_at", "notify_due", "left_at", "computer"} <= p)
        self.assertTrue(settings.get("notify_turns"))
