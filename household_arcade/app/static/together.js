"use strict";
/* Playing together from two phones: races (spec/SPEC.md §13.3) and turn by turn (§13.5).

   This file is everything about it that isn't the game page itself:
   - the live link between the two phones (a WebSocket, or when it can't open, a long poll),
   - the invite sheet ("Play with someone"), the invite that arrives (a sheet, and a card on the Games page),
   - the race bar and the result card, and "Against others" on My scores,
   - "Your games" on the Games page: the turn-by-turn matches going on, whose move, the last move.
   play.js owns the game page and calls into this; app.js calls homeCards(), againstCard(), afterHome() and watch().
   Every piece of text from the server or a person goes in with textContent (via h()), never innerHTML. */

const Together = (() => {
  const INVITE_POLL_MS = 10000;
  const STATE_EVERY_MS = 400;          // my score goes out this often, when it changed
  const KEEPALIVE_MS = 2000;           // and at least this often, so the other phone sees I'm still here
  const WS_OPEN_MS = 3000;             // a socket that isn't open by now isn't going to be

  let pending = null;                  // a match to take into the game page (set before showTab("play"))
  let shown = new Set();               // invites already shown as a sheet
  let watchTimer = null;

  const now = () => performance.now();
  function first(name) { return String(name || "").trim().split(" ")[0] || name; }
  function other(m) { return (m.players || []).find((p) => !p.you) || null; }
  function me(m) { return (m.players || []).find((p) => p.you) || null; }
  function clock(sec) {
    sec = Math.max(0, Math.round(sec));
    // a turn-by-turn invite lasts 7 days: days and hours, not minutes
    if (sec >= 2 * 86400) return `${Math.floor(sec / 86400)} days`;
    if (sec >= 3600) return `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min`;
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }
  // "3 min ago", "2 h ago", "yesterday", "4 days ago" (an ISO time from the server)
  function ago(iso) {
    const t = Date.parse(iso || "");
    if (!isFinite(t)) return "";
    const sec = Math.max(0, (Date.now() - t) / 1000);
    if (sec < 90) return "just now";
    if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
    if (sec < 2 * 86400) return "yesterday";
    return `${Math.floor(sec / 86400)} days ago`;
  }
  // The time left in an invite, from when this picture arrived.
  function inviteLeft(m) { return m.expiresIn === null || m.expiresIn === undefined ? null : Math.max(0, m.expiresIn - (now() - (m._at || now())) / 1000); }
  function stamp(m) { if (m) m._at = now(); return m; }
  // How long until both phones start, from when this picture arrived (relative: the two clocks never need to agree).
  function startsIn(m) { return m && m.startsInMs !== null && m.startsInMs !== undefined ? m.startsInMs - (now() - (m._at || now())) : null; }

  // =====================================================================
  // The live link
  // =====================================================================
  // handlers: { match(m), transport(kind) }.  Returns { send(state), close(), transport }.
  function link(id, handlers) {
    const L = { transport: "connecting", send, close, get open() { return !closed; } };
    let closed = false, ws = null, since = 0, lastState = null, lastSent = "", lastSentAt = 0, timer = null, polling = false, inflight = false;

    function got(m) {
      if (closed || !m) return;
      since = m.v;
      handlers.match(stamp(m));
    }
    function setTransport(kind) { L.transport = kind; if (handlers.transport) handlers.transport(kind); }

    function transmit(force) {
      if (closed || !lastState) return;
      const body = JSON.stringify(lastState);
      const t = now();
      if (!force && body === lastSent && t - lastSentAt < KEEPALIVE_MS) return;
      if (ws && ws.readyState === 1) {
        try { ws.send(JSON.stringify(Object.assign({ t: "state" }, lastState))); lastSent = body; lastSentAt = t; } catch (e) { /* the socket is going */ }
      } else if (polling && !inflight) {
        inflight = true; lastSent = body; lastSentAt = t;
        api(`api/matches/${id}/state`, { method: "POST", body: lastState }).then(got, () => {}).then(() => { inflight = false; });
      }
    }
    function send(state) { lastState = state; }

    async function pollLoop() {
      while (!closed) {
        try {
          const m = await api(`api/matches/${id}?since=${since}&wait=20`);
          got(m);
        } catch (e) {
          if (e && e.status === 404) return;
          await new Promise((r) => setTimeout(r, 1500));
        }
      }
    }
    function startPolling() {
      if (closed || polling) return;
      polling = true;
      try { if (ws) { ws.onclose = ws.onerror = ws.onmessage = null; ws.close(); } } catch (e) { /* ignore */ }
      ws = null;
      setTransport("poll");
      pollLoop();
    }
    function openSocket() {
      let url;
      try {
        url = new URL(`api/matches/${id}/live`, location.href);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(url.href);
      } catch (e) { startPolling(); return; }
      let spoke = false;
      const giveUp = setTimeout(() => { if (!spoke) startPolling(); }, WS_OPEN_MS);
      ws.onmessage = (ev) => {
        let m = null;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (!m || m.t !== "match") return;
        if (!spoke) { spoke = true; clearTimeout(giveUp); setTransport("websocket"); }
        got(m);
      };
      ws.onclose = ws.onerror = () => { clearTimeout(giveUp); if (!closed) startPolling(); };
    }
    function close() {
      closed = true;
      clearInterval(timer);
      try { if (ws) { ws.onclose = ws.onerror = ws.onmessage = null; ws.close(); } } catch (e) { /* ignore */ }
      ws = null;
    }

    timer = setInterval(() => transmit(false), STATE_EVERY_MS);
    // a first picture over plain HTTP so the screen never waits for the socket, then the socket (or the poll)
    api(`api/matches/${id}`).then(got, () => {});
    if (typeof WebSocket === "function") openSocket(); else startPolling();
    return L;
  }

  // =====================================================================
  // Invite sheet: "Play with someone"
  // =====================================================================
  // opts: { game, gameName, mode, modeLabel, practice, turns, options }. Resolves with the new match (an invite is
  // out) or null. A turn-by-turn game for more than two (maxPlayers from the server) lets the inviter tick up to three.
  function invite(opts) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; resolve(v); } };
      const list = h("div", { class: "tg-people", id: "tgPeople" }, spinner());
      const err = h("div", { class: "hint warn", role: "alert", hidden: true });
      const sheet = openModal("Play with someone", h("div", null,
        h("p", { class: "hint" }, `${opts.gameName} · ${opts.modeLabel}${opts.practice ? " · Practice (not saved)" : ""}. ` +
          (opts.turns ? "You take turns from your own phones — they needn't be open at the same time; each of you is told when it's your move."
              : "You both play the same game at the same time on your own phones; the better score wins.")),
        list, err), { sheet: true, onClose: () => finish(null) });
      const send = async (ids, btn) => {
        btn.disabled = true; err.hidden = true;
        try {
          const body = { game: opts.game, mode: opts.mode, practice: !!opts.practice, opponents: ids, kind: opts.turns ? "turns" : "race" };
          if (opts.turns && opts.options) body.options = opts.options;
          const m = await api("api/matches", { method: "POST", body });
          finish(stamp(m)); sheet.close();
        } catch (e) { err.textContent = e.message; err.hidden = false; btn.disabled = false; }
      };
      const load = async () => {
        let data;
        try { data = await api(`api/players?game=${encodeURIComponent(opts.game)}&mode=${encodeURIComponent(opts.mode)}`); }
        catch (e) { mount(list, h("div", { class: "hint warn" }, e.message)); return; }
        if (!data.players.length) { mount(list, h("div", { class: "empty" }, "Nobody else has opened the app yet.")); return; }
        if ((data.maxPlayers || 2) > 2) {           // 2–4 players: tick up to three, then Invite
          const most = data.maxPlayers - 1, picked = new Set();
          const go = h("button", { class: "btn-primary", type: "button", id: "inviteManyBtn", disabled: true, onclick: (ev) => send([...picked], ev.target) }, "Invite");
          mount(list, data.players.map((p) => {
            const box = h("input", { type: "checkbox", disabled: !p.canPlay, "aria-label": `Invite ${p.name}` });
            box.addEventListener("change", () => {
              if (box.checked && picked.size >= most) { box.checked = false; toast(`At most ${most} others.`, true); return; }
              if (box.checked) picked.add(p.id); else picked.delete(p.id);
              go.disabled = !picked.size;
            });
            return h("label", { class: "tg-person" + (p.canPlay ? "" : " off"), dataset: { id: p.id } },
              h("div", { class: "tg-who" }, h("strong", null, p.name), p.canPlay ? (p.active ? h("span", { class: "chip on" }, "here now") : null)
                : h("div", { class: "hint" }, p.reason)), box);
          }), h("div", { class: "hint" }, `Up to ${most} others; the game starts when all have joined, or when you start it with those who have.`),
          h("div", { class: "actions" }, go));
          return;
        }
        mount(list, data.players.map((p) => h("div", { class: "tg-person" + (p.canPlay ? "" : " off"), dataset: { id: p.id } },
          h("div", { class: "tg-who" }, h("strong", null, p.name), p.canPlay ? (p.active ? h("span", { class: "chip on" }, "here now") : null)
            : h("div", { class: "hint" }, p.reason)),
          p.canPlay ? h("button", { class: "btn-primary btn-small", type: "button", "aria-label": `Invite ${p.name}`, onclick: (ev) => send([p.id], ev.target) }, "Invite") : null)));
      };
      load();
    });
  }

  // =====================================================================
  // Invites that arrive
  // =====================================================================
  async function acceptInvite(m) {
    const r = await api(`api/matches/${m.id}/accept`, { method: "POST" });
    return stamp(r);
  }
  // Join: accept, then open the game page on that game (it picks the match up and counts in).
  async function join(m) {
    try {
      pending = await acceptInvite(m);
    } catch (e) { fail(e); return false; }
    showTab("play", { arg: m.game });
    return true;
  }
  async function notNow(m) {
    try { await api(`api/matches/${m.id}/decline`, { method: "POST" }); }
    catch (e) { fail(e); return false; }
    return true;
  }
  function take(game) {
    const m = pending && pending.game === game ? pending : null;
    if (m) pending = null;
    return m;
  }
  function modeLine(m) { return `${m.gameName} · ${m.modeLabel}${m.practice ? " · Practice" : ""}`; }
  function who(m) { const o = other(m); return o ? o.name : "Someone"; }

  function inviteRow(m, after) {
    const left = inviteLeft(m);
    return h("div", { class: "tg-invite", dataset: { match: m.id } },
      h("div", { class: "tg-text" }, h("strong", null, `${m.icon} ${who(m)}`), m.rematchOf ? " wants a rematch" : " challenges you", h("div", { class: "hint" }, modeLine(m), left !== null ? ` · ${clock(left)} left` : "")),
      h("div", { class: "tg-actions" },
        h("button", { class: "btn-primary btn-small", type: "button", id: "joinBtn", onclick: async (ev) => { ev.target.disabled = true; if (!(await join(m))) { ev.target.disabled = false; if (after) after(); } } }, "Join"),
        h("button", { class: "btn-ghost btn-small", type: "button", id: "notNowBtn", onclick: async (ev) => { ev.target.disabled = true; if (await notNow(m)) { if (after) after(); } else ev.target.disabled = false; } }, "Not now")));
  }

  // The cards at the top of the Games page: invites waiting for me ("Waiting for you"), my own invite out, and a
  // race that has started that I haven't joined. Refreshes itself while it is on the page.
  function homeCards() {
    const box = h("div", { class: "tg-cards", id: "togetherCards" });
    let timer = null;
    async function load() {
      let data;
      try { data = await api("api/matches"); } catch (e) { return; }
      if (!box.isConnected) { clearInterval(timer); return; }
      data.waiting.forEach(stamp); data.sent.forEach(stamp); data.playing.forEach(stamp);
      const cards = [];
      if (data.waiting.length) {
        cards.push(h("div", { class: "card tg-card", id: "waitingForYou" }, h("h3", null, "Waiting for you"), data.waiting.map((m) => inviteRow(m, load))));
      }
      data.sent.forEach((m) => cards.push(h("div", { class: "card tg-card", id: "sentInvite" },
        h("div", { class: "tg-text" }, h("strong", null, `Waiting for ${who(m)} to join…`), h("div", { class: "hint" }, modeLine(m))),
        h("div", { class: "tg-actions" },
          h("button", { class: "btn-ghost btn-small", type: "button", id: "cancelInviteBtn", onclick: async (ev) => {
            ev.target.disabled = true;
            try { await api(`api/matches/${m.id}/cancel`, { method: "POST" }); } catch (e) { fail(e); }
            load();
          } }, "Cancel")))));
      const games = data.playing.filter((m) => m.kind === "turns");
      if (games.length) cards.push(yourGames(games));
      data.playing.filter((m) => { const p = me(m); return m.kind !== "turns" && p && !p.started; }).forEach((m) => cards.push(h("div", { class: "card tg-card", id: "raceStarted" },
        h("div", { class: "tg-text" }, h("strong", null, `Race with ${who(m)} is starting`), h("div", { class: "hint" }, modeLine(m))),
        h("div", { class: "tg-actions" }, h("button", { class: "btn-primary btn-small", type: "button", onclick: () => { pending = m; showTab("play", { arg: m.game }); } }, "Join the race")))));
      mount(box, cards);
    }
    timer = setInterval(() => { if (!document.hidden) load(); }, INVITE_POLL_MS / 2);
    load();
    return box;
  }

  // "Your games": the turn-by-turn matches going on — my move first, then theirs, each with the last move's time.
  function yourGames(list) {
    const mine = (m) => !!(m.turns && m.turns.myTurn);
    list = list.slice().sort((a, b) => (mine(b) - mine(a)) || String(b.turns && b.turns.lastMoveAt).localeCompare(String(a.turns && a.turns.lastMoveAt)));
    return h("div", { class: "card tg-card", id: "yourGames" }, h("h3", null, "Your games"),
      list.map((m) => {
        const others = (m.players || []).filter((p) => !p.you).map((p) => first(p.name));
        const toMove = (m.players || []).filter((p) => m.turns && m.turns.toMove.indexOf(p.seat) >= 0 && !p.you).map((p) => first(p.name));
        const whose = mine(m) ? "Your move" : toMove.length ? `${toMove.join(" and ")}'s move` : "Waiting";
        return h("div", { class: "tg-invite tg-game" + (mine(m) ? " my-turn" : ""), dataset: { match: m.id } },
          h("div", { class: "tg-text" }, h("strong", null, `${m.icon} ${m.gameName}`), ` with ${others.join(", ")}`,
            h("div", { class: "hint" }, h("span", { class: mine(m) ? "tg-turn mine" : "tg-turn" }, whose),
              m.turns && m.turns.number ? ` · last move ${ago(m.turns.lastMoveAt)}` : ` · started ${ago(m.turns && m.turns.lastMoveAt)}`,
              m.practice ? " · Practice" : "")),
          h("div", { class: "tg-actions" }, h("button", { class: mine(m) ? "btn-primary btn-small" : "btn-secondary btn-small", type: "button",
            "aria-label": `Open ${m.gameName} with ${others.join(", ")}`, onclick: () => { pending = m; showTab("play", { arg: m.game }); } }, mine(m) ? "Play" : "Open")));
      }));
  }

  // A hash link (a phone notification's Join / Not now, or a your-move notification): #/home/join/<id>,
  // #/home/decline/<id>, #/home/turn/<id>.
  async function afterHome(arg, id) {
    if (arg !== "join" && arg !== "decline" && arg !== "turn") return;
    try { history.replaceState(history.state, "", "#/home"); } catch (e) { /* ignore */ }
    state.arg = null; state.arg2 = null;
    let m;
    try { m = await api(`api/matches/${encodeURIComponent(id)}`); } catch (e) { fail(e); return; }
    if (arg === "turn") {
      if (m.status === "playing" && m.kind === "turns") { pending = stamp(m); showTab("play", { arg: m.game }); }
      else toast(m.status === "done" ? "That match is over." : "That match isn't being played.", true);
      return;
    }
    if (m.status !== "invited" || m.mine) {
      toast(m.status === "expired" ? "That invite has run out." : "That invite isn't open any more.", true);
      return;
    }
    stamp(m);
    shown.add(m.id);
    if (arg === "join") join(m);
    else if (await notNow(m)) { toast("Not now. They'll be told."); const c = $("#togetherCards"); if (c) c.replaceWith(homeCards()); }
  }

  // While the app is open anywhere (not in the middle of a game), an invite for me pops up as a sheet.
  function watch() {
    if (watchTimer) return;
    const tick = async () => {
      if (document.hidden || (window.Play && Play.isRunning && Play.isRunning())) return;
      if (UI.dialogs().length || (state.tab === "play" && Play._state && Play._state.phase !== "idle")) return;
      let data;
      try { data = await api("api/matches"); } catch (e) { return; }
      const m = data.waiting.map(stamp).find((x) => !shown.has(x.id));
      if (!m || UI.dialogs().length) return;
      shown.add(m.id);
      let handle;
      const body = h("div", null, h("p", null, h("strong", null, `${who(m)}`), m.rematchOf ? " wants a rematch in " : " challenges you to ", h("strong", null, m.gameName), m.practice ? " (Practice)." : "."),
        h("div", { class: "hint" }, modeLine(m)),
        h("div", { class: "actions" },
          h("button", { class: "btn-ghost", type: "button", id: "sheetNotNow", onclick: async () => { if (await notNow(m)) { handle.close(); if (state.tab === "home") renderHome(); } } }, "Not now"),
          h("button", { class: "btn-primary", type: "button", id: "sheetJoin", onclick: async () => { if (await join(m)) handle.close(); else handle.close(); } }, "Join")));
      handle = openModal("Play together", body, { sheet: true });
    };
    watchTimer = setInterval(tick, INVITE_POLL_MS);
    setTimeout(tick, 1500);
  }

  // =====================================================================
  // On the game page
  // =====================================================================
  // The race bar: the other player's name, score, level and whether they are still playing.
  function barContent(m, transport) {
    const o = m ? other(m) : null;
    if (!o) return [];
    let text, cls = "";
    if (m.kind === "turns") {
      const tm = (m.turns && m.turns.toMove) || [], you = me(m);
      const others = (m.players || []).filter((p) => !p.you && (m.status !== "playing" || p.invite !== "declined"));
      const many = others.length > 1;
      const winner = (m.players || []).find((p) => p.result === "won");
      const mover = others.find((p) => tm.indexOf(p.seat) >= 0);
      if (m.status === "invited") { text = many ? "have been invited" : "has been invited"; cls = "wait"; }
      else if (m.status === "done") {
        text = you && you.result === "won" ? "you won" : you && you.result === "draw" ? "a draw" : winner && many ? `${first(winner.name)} won` : you && you.result === "lost" ? "won" : "over";
        cls = "done";
      } else if (you && tm.indexOf(you.seat) >= 0) { text = "your move"; cls = "live"; }
      else { text = many && mover ? `${first(mover.name)}'s move` : "their move"; cls = "wait"; }
      // 3–4 players: a dot for each of the others (here now or not), their first names
      const dots = (many ? others : [o]).map((p) => h("span", { class: "rb-dot " + (p.connected ? "on" : "off"), "aria-hidden": "true",
        title: `${first(p.name)}: ${p.connected ? "here now" : "not here now"}${p.computer ? " (the computer plays)" : ""}` }));
      return dots.concat([
        h("span", { class: "rb-name" }, many ? others.map((p) => first(p.name) + (p.computer ? "🤖" : "")).join(", ") : first(o.name)),
        h("span", { class: "rb-state " + cls, id: "raceState" }, text),
      ]);
    }
    if (m.status === "invited") { text = "has been invited"; cls = "wait"; }
    else if (o.final || o.over) {
      text = o.final ? (o.seconds !== null && o.seconds !== undefined ? `finished in ${clock(o.seconds)}` : "finished") : "finishing…";
      cls = "done";
    }
    else if (o.paused) { text = "paused"; cls = "wait"; }
    else if (!o.started && m.status === "playing") { text = "getting ready"; cls = "wait"; }
    else if (!o.connected) { text = "connection lost?"; cls = "lost"; }
    else { text = "playing"; cls = "live"; }
    const here = o.connected || o.final;
    return [
      h("span", { class: "rb-dot " + (here ? "on" : "off"), "aria-hidden": "true", title: transport ? `Live link: ${transport}` : "" }),
      h("span", { class: "rb-name" }, first(o.name)),
      h("span", { class: "rb-score", id: "raceScore" }, fmtNum(o.score)),
      h("span", { class: "rb-level" }, `L${o.level}`),
      h("span", { class: "rb-state " + cls, id: "raceState" }, text),
    ];
  }

  function headline(m) {
    const you = me(m), o = other(m);
    if (!you || !o) return "";
    if (m.endReason === "timeout" && !you.result) return "Ended — no result";
    if (m.endReason === "timeout" && you.result === "won") return `🏆 ${first(o.name)} didn't move for 7 days. You win!`;
    if (m.endReason === "timeout" && you.result === "lost") return "No move for 7 days — a loss";
    if (m.endReason === "time_limit") return "Play time ran out — a draw";
    const others = (m.players || []).filter((p) => !p.you);
    if (others.length > 1 && (m.endReason === "resigned" || m.endReason === "timeout")) {
      if (you.result === "won") return "🏆 The others left the game. You win!";
      const w = others.find((p) => p.result === "won");
      return w ? `${first(w.name)} won${you.left ? " (you left the game)" : ""}.` : "Ended — no result";
    }
    if (m.endReason === "resigned" && you.result === "won") return `🏆 ${first(o.name)} gave up. You win!`;
    if (m.endReason === "resigned" && you.result === "lost") return `${first(o.name)} won (you gave up).`;
    if (m.endReason === "left" && !you.result) return "Both left — no result";
    if (m.endReason === "left" && you.result === "won") return `🏆 ${first(o.name)} left the game. You win!`;
    if (m.endReason === "left" && you.result === "lost") return `${first(o.name)} won (you left the game).`;
    if (you.result === "won") return "🏆 You won!";
    const winner = (m.players || []).find((p) => p.result === "won");
    if (you.result === "lost" && winner && !winner.you) return `${first(winner.name)} won`;
    if (you.result === "lost") return `${first(o.name)} won`;
    if (you.result === "draw") return "A draw";
    return "";
  }
  // The result card: both results side by side. `mine` = my own final numbers (they may be ahead of the match's).
  function resultTable(m) {
    const turns = m.kind === "turns";       // no levels; the time is the time each spent on the match's page
    const rows = (m.players || []).map((p) => h("tr", { class: p.you ? "me" : "", dataset: { id: p.id } },
      h("td", null, p.you ? "You" : first(p.name), p.result === "won" ? " 🏆" : "", p.left ? " (left)" : ""),
      h("td", { class: "num" }, p.final ? h("strong", null, fmtNum(p.score)) : (p.over ? "…" : fmtNum(p.score))),
      turns ? null : h("td", { class: "num" }, p.final || p.over ? `L${p.level}` : "playing"),
      h("td", { class: "num" }, p.final ? fmtDuration(p.seconds) : "")));
    return h("table", { class: "data tg-result", id: "raceResult" },
      h("thead", null, h("tr", null, h("th", null, ""), h("th", { class: "num" }, "Score"), turns ? null : h("th", { class: "num" }, "Level"), h("th", { class: "num" }, "Time"))),
      h("tbody", null, rows));
  }

  // "Against others" on My scores.
  function againstCard() {
    const box = h("div", { class: "card", id: "againstCard" }, h("h3", null, "Against others"), spinner());
    api("api/against").then((data) => {
      if (!box.isConnected) return;
      if (!data.people.length) { mount(box, h("h3", null, "Against others"), h("div", { class: "empty" }, "Play a race with someone from the start screen's “Play with someone”, and your wins and losses show here.")); return; }
      mount(box, h("h3", null, "Against others"), data.people.map((p) => h("details", { class: "tg-vs", dataset: { id: p.id } },
        h("summary", null, h("strong", null, p.name), h("span", { class: "tg-tally" }, `${p.games} game${p.games === 1 ? "" : "s"} · ${p.wins} won · ${p.losses} lost · ${p.draws} drawn`)),
        h("table", { class: "data" },
          h("thead", null, h("tr", null, h("th", null, "Game"), h("th", { class: "num" }, "Games"), h("th", { class: "num" }, "Won"), h("th", { class: "num" }, "Lost"), h("th", { class: "num" }, "Drawn"))),
          h("tbody", null, p.perGame.map((g) => h("tr", null, h("td", null, g.name), h("td", { class: "num" }, g.games), h("td", { class: "num" }, g.wins), h("td", { class: "num" }, g.losses), h("td", { class: "num" }, g.draws))))))));
    }, (e) => { if (box.isConnected) mount(box, h("h3", null, "Against others"), h("div", { class: "hint warn" }, e.message)); });
    return box;
  }

  return { link, invite, join, notNow, take, homeCards, afterHome, watch, againstCard, barContent, headline, resultTable,
    inviteLeft, startsIn, stamp, other, me, first, clock, ago, acceptInvite, yourGames, _internal: { get pending() { return pending; }, set pending(v) { pending = v; } } };
})();
window.Together = Together;
