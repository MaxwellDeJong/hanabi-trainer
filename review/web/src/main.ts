// Review tool. Routes:
//   #/                                   lobby: Label sessions and the board
//   #/game/<id>/<turn>?pov=<seat>&own=hidden   Inspect mode: a game at UI turn <turn> (1-based), from the admin view
//   #/label/<session>/<turn>             Label mode (label.ts)
//   #/admin                              coverage, labellers' contributions and the games to inspect (admin.ts)

import { ago, showAdmin, until } from "./admin";
import { getBoard, getInspectBundle, listSessions, startSession, type BoardGame, type Bundle, type SessionSummary } from "./api";
import { labelActive, labelKey, leaveLabel, redrawLabel, showLabel } from "./label";
import { closeOverlay, openOverlay, overlayOpen } from "./overlay";
import { getAdvance, MAX_SECONDS, MIN_SECONDS, setAdvance } from "./settings";
import { el, renderTable, type View } from "./table";
import { headRow, heading, infoIcon, tip } from "./tips";

const app = document.getElementById("app")!;
let view: View | null = null;
const bundles = new Map<number, Bundle>();
const LABELLER_KEY = "review.labeller";

// ---- Routing -------------------------------------------------------------------------------------

async function route(): Promise<void> {
  closeOverlay();
  const label = /^#\/label\/(\w+)(?:\/(\d+))?$/.exec(location.hash);
  if (label !== null) {
    view = null;
    await showLabel(app, label[1]!, label[2] === undefined ? null : Number(label[2]));
    return;
  }
  leaveLabel();
  if (location.hash === "#/admin") {
    view = null;
    await showAdmin(app);
    return;
  }
  const match = /^#\/game\/(\d+)(?:\/(\d+))?(?:\?(.*))?$/.exec(location.hash);
  if (match === null) {
    view = null;
    showLobby();
    return;
  }
  const id = Number(match[1]);
  const params = new URLSearchParams(match[3] ?? "");
  let bundle = bundles.get(id);
  if (bundle === undefined) {
    app.replaceChildren(el("div", "message", `Loading game ${id}…`));
    try {
      bundle = await getInspectBundle(id);
    } catch (error) {
      app.replaceChildren(el("div", "message", `Could not load game ${id}: ${String(error)}`));
      return;
    }
    bundles.set(id, bundle);
  }
  const n = bundle.game.export.players.length;
  const turn = Number(match[2] ?? 1);
  view = {
    bundle,
    pos: clamp(turn - 1, 0, bundle.turns),
    pov: clamp(Number(params.get("pov") ?? 0), 0, n - 1),
    showOwn: params.get("own") !== "hidden",
  };
  draw();
}

function update(changes: Partial<View>): void {
  if (view === null) return;
  view = { ...view, ...changes };
  const { bundle, pos, pov, showOwn } = view;
  const query = new URLSearchParams();
  if (pov !== 0) query.set("pov", String(pov));
  if (!showOwn) query.set("own", "hidden");
  const q = query.toString();
  history.replaceState(null, "", `#/game/${bundle.game.export.id}/${pos + 1}${q ? `?${q}` : ""}`);
  draw();
}

function draw(): void {
  if (view === null) return;
  const v = view;
  renderTable(app, v, {
    goTo: (pos) => update({ pos: clamp(pos, 0, v.bundle.turns) }),
    setPov: (pov) => update({ pov }),
    toggleOwn: () => update({ showOwn: !v.showOwn }),
    lobby: () => { location.hash = "#/admin"; },
    showChecks: () => showChecks(v.bundle),
    showJSON: () => showJSON(v),
    showFullLog: () => showFullLog(v),
  });
}

// ---- Lobby ---------------------------------------------------------------------------------------

function showLobby(): void {
  const page = el("div", "lobby-page");
  const nav = el("div", "control-row nav");
  const admin = tip(el("a", "chip", "Admin view →") as HTMLAnchorElement,
    "Labeling progress at a glance, what each labeler has done, and Inspect mode for checking games");
  admin.href = "#/admin";
  nav.append(admin);
  page.append(nav, labelSection());
  app.replaceChildren(page);
}

function labelSection(): HTMLElement {
  const section = el("div", "label-section");
  section.append(heading("h1", "Label",
    "Sit in one seat of a real game, with the players' names hidden, and choose the move you'd make at each of your turns " +
    "(speedrun controls, as on the site). The other players' moves are then revealed as they happened.\n\n" +
    "When the game is over, submit your moves. Each seat is labeled by one person, so pick an open one: " +
    "a random one with the button, or a specific one from the board below."));
  const row = el("div", "control-row");
  const name = tip(el("input", "name-input labeller-input"), "Your sessions are kept under this name. It's remembered in this browser");
  name.placeholder = "Your name";
  name.value = loadName();
  const start = tip(el("button", "chip active", "Start a random open seat"),
    "Start labeling a seat nobody has taken yet, in a game chosen at random");
  const status = el("span", "hint");
  // Without a name, everything that starts a session is greyed out under a hatch (see .no-name in style.css)
  // whose tooltip asks for one; clicking the hatch goes to the name field. The rest of the lobby works as usual.
  const noName = (): void => { section.classList.toggle("no-name", name.value.trim() === ""); };
  const needsName = (control: HTMLElement): HTMLElement => {
    const hatch = tip(el("span", "hatch"), "Enter your name to get started.");
    hatch.addEventListener("click", () => name.focus());
    const wrap = el("span", "needs-name");
    wrap.append(control, hatch);
    return wrap;
  };
  row.append(el("span", "control-label", "Labeler"), name, needsName(start),
    el("span", "name-prompt", "← Enter your name to start labeling"), status);
  const list = el("div");
  section.append(row, advanceRow(), list);
  noName();

  const begin = async (pick?: { game_id: number; seat: number }): Promise<void> => {
    const who = name.value.trim();
    if (who === "") {
      status.textContent = "Enter your name first.";
      name.focus();
      return;
    }
    saveName(who);
    try {
      const data = await startSession(who, pick);
      location.hash = `#/label/${data.session.session_id}/1`;
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error);
    }
  };

  const refresh = async (): Promise<void> => {
    const who = name.value.trim();
    status.textContent = "";
    // Without a name there are no sessions of yours, but the board still shows what's left to label.
    let sessions: SessionSummary[];
    let board: BoardGame[];
    try {
      [sessions, board] = await Promise.all([who === "" ? [] : listSessions(who), getBoard(who)]);
    } catch (error) {
      list.replaceChildren();
      status.textContent = String(error);
      return;
    }
    list.replaceChildren();
    // Without a turn, Label mode opens at the furthest turn reached.
    const open = (id: string, turn?: number): void => { location.hash = `#/label/${id}${turn === undefined ? "" : `/${turn}`}`; };
    const active = sessions.filter((x) => x.status === "active");
    const submitted = sessions.filter((x) => x.status === "submitted");
    if (active.length > 0) {
      list.append(heading("h2", "Your active sessions",
        "Games you've started but not submitted. Click one to carry on where you left off.\n\n" +
        "A session not submitted within 24 hours of its start expires: its moves are dropped and the seat is open again."));
      list.append(sessionTable(["Game", "You are", "Players", "Variant", "Turn", "Your moves", "Last activity", "Expires", ""], active.map((x) => ({
        cells: [String(x.game_id), x.you, String(x.players), x.variant, String(x.turn), `${x.labels} / ${x.own_turns}`, ago(x.last_active),
          until(x.expires),
          x.game_over ? "submit →" : "resume →"],
        cls: x.game_over ? "ready" : "",
        onClick: () => open(x.session_id),
      }))));
    }
    if (submitted.length > 0) {
      list.append(heading("h2", "Your submitted sessions",
        "Seats you've handed in. They're read only: click one to look back through it, with your moves next to the real ones."));
      list.append(sessionTable(["Game", "You were", "Players", "Variant", "Moves", "Submitted", ""], submitted.map((x) => ({
        cells: [String(x.game_id), x.you, String(x.players), x.variant, String(x.labels), ago(x.submitted!), "view"],
        cls: "good",
        onClick: () => open(x.session_id, 1),
      }))));
    }
    list.append(boardSection(board, begin, open, needsName));
  };
  name.addEventListener("input", noName);
  name.addEventListener("change", () => { saveName(name.value.trim()); void refresh(); });
  start.addEventListener("click", () => void begin());
  void refresh();
  return section;
}

/** Manual (Space / →) or automatic reveal of other players' moves, every so many seconds. */
function advanceRow(): HTMLElement {
  const row = el("div", "control-row");
  const manual = tip(el("button", "chip", "Manual"), "Reveal each of the other players' moves yourself, with Space or →");
  const auto = tip(el("button", "chip", "Auto"), "Reveal the other players' moves by themselves, one every so many seconds");
  const seconds = tip(el("input", "name-input seconds-input"), "Seconds each of the other players' turns stays on screen");
  seconds.type = "number";
  seconds.min = String(MIN_SECONDS);
  seconds.max = String(MAX_SECONDS);
  seconds.step = "0.5";
  const render = (): void => {
    const a = getAdvance();
    manual.className = a.auto ? "chip" : "chip active";
    auto.className = a.auto ? "chip active" : "chip";
    seconds.value = String(a.seconds);
    seconds.disabled = !a.auto;
  };
  manual.addEventListener("click", () => { setAdvance({ auto: false }); render(); });
  auto.addEventListener("click", () => { setAdvance({ auto: true }); render(); });
  seconds.addEventListener("change", () => { setAdvance({ seconds: Number(seconds.value) }); render(); });
  render();
  row.append(el("span", "control-label", "Turn advance"), infoIcon(
    "How the game moves on after your turn, while the other players make their moves. " +
    "You can also change this during a game, with the Auto-advance button and its − / +."),
  manual, auto, seconds, el("span", "hint", "seconds per turn"));
  return row;
}

interface Row {
  cells: string[];
  cls: string;
  onClick: () => void;
}

function sessionTable(headers: string[], rows: Row[]): HTMLElement {
  const table = el("table", "games");
  table.append(headRow(headers, COLUMN_TIPS));
  for (const r of rows) {
    const tr = el("tr", "clickable");
    for (const c of r.cells) tr.append(el("td", "", c));
    if (r.cls !== "") tr.lastElementChild?.classList.add(r.cls);
    tr.addEventListener("click", r.onClick);
    table.append(tr);
  }
  return table;
}

const SEAT_ICON = { open: "○", active: "✎", submitted: "✓" } as const;
const SEAT_WORD = { open: "open", active: "in progress", submitted: "submitted" } as const;

/** Every game with each seat's status, so labellers can see what's left and don't overlap. */
function boardSection(board: BoardGame[], begin: (pick: { game_id: number; seat: number }) => Promise<void>,
  open: (sid: string, turn?: number) => void, needsName: (control: HTMLElement) => HTMLElement): HTMLElement {
  const box = el("div", "board");
  const seats = board.flatMap((g) => g.seats);
  const count = (st: string): number => seats.filter((x) => x.status === st).length;
  const games = board.filter((g) => g.seats.some((x) => x.available));
  box.append(heading("h2", "Board",
    "Every game, and who is labeling each of its seats, so that nobody does the same seat twice.\n\n" +
    "○ take: open, click to start labeling that seat\n✎ in progress: someone's active session\n✓ submitted: done\n\n" +
    "Your own seats show as buttons, to resume or view them."), el("p", "hint",
    `${seats.length} seats in ${board.length} games: ${count("submitted")} submitted, ${count("active")} in progress, ` +
    `${count("open")} open.`));
  let all = false;
  const controls = el("div", "control-row");
  const toggles = el("span", "control-row");
  const find = tip(el("input", "name-input"), "Show only games whose ID contains this");
  find.placeholder = "Game ID";
  find.inputMode = "numeric";
  controls.append(toggles, find);
  const table = el("div");
  const render = (): void => {
    toggles.replaceChildren();
    for (const [text, value, help] of [
      [`Games with open seats (${games.length})`, false, "Only games that still have a seat to take"],
      [`All games (${board.length})`, true, "Every game, including those whose seats are all taken"],
    ] as const) {
      const b = tip(el("button", all === value ? "chip active" : "chip", text), help);
      b.addEventListener("click", () => { all = value; render(); });
      toggles.append(b);
    }
    const query = find.value.trim();
    const shown = (all ? board : games).filter((g) => String(g.game_id).includes(query));
    const width = Math.max(0, ...board.map((g) => g.players));
    const t = el("table", "games board-table");
    const head = headRow(["Game", "Players", "Variant"], COLUMN_TIPS);
    for (let k = 0; k < width; k++) {
      head.append(tip(el("th", "", board.find((g) => g.players > k)?.seats[k]?.name ?? ""),
        `Seat ${k + 1}. Players' real names are hidden: each seat has the same stand-in name in every game`));
    }
    t.append(head);
    for (const g of shown) {
      const tr = el("tr");
      for (const c of [String(g.game_id), String(g.players), g.variant]) tr.append(el("td", "", c));
      g.seats.forEach((x, k) => {
        const td = el("td", `seat ${x.status}`);
        if (x.session_id !== null) {
          const b = tip(el("button", "chip active", x.status === "submitted" ? "✓ you" : "✎ you: resume"),
            x.status === "submitted" ? "You submitted this seat. Click to look back through it" : "Your session on this seat. Click to carry on");
          b.addEventListener("click", () => open(x.session_id!, x.status === "submitted" ? 1 : undefined));
          td.append(b);
        } else if (x.available) {
          const b = tip(el("button", "chip", "○ take"), `Start labeling ${x.name}'s seat in game ${g.game_id}`);
          b.addEventListener("click", () => void begin({ game_id: g.game_id, seat: k }));
          td.append(needsName(b));
        } else {
          td.textContent = `${SEAT_ICON[x.status]} ${x.labellers.join(", ") || SEAT_WORD[x.status]}`;
          tip(td, x.status === "submitted" ? `Submitted by ${x.labellers.join(", ")}` : `Being labeled by ${x.labellers.join(", ")}`);
        }
        tr.append(td);
      });
      for (let k = g.players; k < width; k++) tr.append(el("td"));
      t.append(tr);
    }
    table.replaceChildren(shown.length > 0 ? t : el("p", "",
      query !== "" ? `No ${all ? "" : "open "}game matches ${query}.` : all ? "No games to label yet." : "No seats left open."));
  };
  find.addEventListener("input", render);
  render();
  box.append(controls, table);
  return box;
}

/** What each column holds, shown on hover over its heading (lobby tables). */
const COLUMN_TIPS: Record<string, string> = {
  Game: "The game's ID on the site",
  Players: "Number of players",
  "You are": "Your seat in this game (players' real names are hidden)",
  "You were": "Your seat in this game (players' real names are hidden)",
  Turn: "The furthest turn you've reached",
  "Your moves": "Your turns with a move chosen, out of all your turns in the game",
  Moves: "Your turns with a move chosen",
  "Last activity": "When you last did anything in this session",
  Expires: "When this session expires if it isn't submitted: its moves are dropped and the seat opens again",
  Submitted: "When you submitted it",
};

function loadName(): string {
  try {
    return localStorage.getItem(LABELLER_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveName(name: string): void {
  try {
    localStorage.setItem(LABELLER_KEY, name);
  } catch {
    // Not remembered; the name field still works.
  }
}

// ---- Overlays ------------------------------------------------------------------------------------

function showFullLog(v: View): void {
  const list = el("ol", "full-log");
  list.start = 0;
  v.bundle.log.slice(0, v.pos + 1).forEach((line, k) => {
    const item = el("li", "clickable", line);
    item.value = k;
    item.title = k === 0 ? "" : `Go to turn ${k}`;
    item.addEventListener("click", () => { closeOverlay(); update({ pos: Math.max(0, k - 1) }); });
    list.append(item);
  });
  openOverlay("Full log (number = turn)", list);
}

function showChecks(bundle: Bundle): void {
  const body = el("div");
  const failed = bundle.checks.filter((c) => !c.ok);
  const passed = bundle.checks.length - failed.length;
  body.append(el("p", "", `${passed} of ${bundle.checks.length} checks passed. Engine: ${bundle.engine}.`));
  if (failed.length > 0) {
    const list = el("ul", "checks");
    for (const c of failed) {
      const item = el("li", c.kind === "error" ? "bad clickable" : "wording clickable",
        `${c.turn === null ? "game" : `turn ${c.turn}`} · ${c.check} [${c.kind}] ${c.detail ?? ""}`);
      if (c.turn !== null) {
        const turn = c.turn;
        item.addEventListener("click", () => { closeOverlay(); update({ pos: turn - 1 }); });
      }
      list.append(item);
    }
    body.append(list);
  }
  const names = [...new Set(bundle.checks.map((c) => c.check))].sort();
  body.append(el("p", "hint", `Checks run: ${names.join(", ")}.`));
  openOverlay(`Checks · game ${bundle.game.export.id}`, body);
}

function showJSON(v: View): void {
  const body = el("div");
  const decision = v.bundle.decisions[v.pos];
  const seatView = v.bundle.seat_views[v.pov]?.[v.pos];
  const name = v.bundle.game.export.players[v.pov];
  if (decision !== undefined) {
    body.append(el("h3", "", `DecisionRecord, turn ${v.pos + 1} (acting player's view; label = the move made)`),
      el("pre", "json", JSON.stringify(decision, null, 1)));
  } else {
    body.append(el("p", "", "Final position: no decision."));
  }
  body.append(el("h3", "", `Seat view of ${name ?? "?"} (obs)`), el("pre", "json", JSON.stringify(seatView, null, 1)));
  const action = v.bundle.game.export.actions[v.pos];
  if (action !== undefined) {
    body.append(el("h3", "", "Raw export action"), el("pre", "json", JSON.stringify(action)));
  }
  openOverlay(`JSON · game ${v.bundle.game.export.id}, turn ${v.pos + 1}`, body);
}

// ---- Keys and resize -----------------------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeOverlay();
    return;
  }
  if (overlayOpen() || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target instanceof HTMLInputElement) return;
  if (labelActive()) {
    if (labelKey(e)) e.preventDefault();
    return;
  }
  if (view === null) return;
  const n = view.bundle.game.export.players.length;
  const moves: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, "[": -n, "]": n };
  if (e.key in moves) {
    update({ pos: clamp(view.pos + moves[e.key]!, 0, view.bundle.turns) });
  } else if (e.key === "Home") {
    update({ pos: 0 });
  } else if (e.key === "End") {
    update({ pos: view.bundle.turns });
  } else if (e.key === "o" || e.key === "O") {
    update({ showOwn: !view.showOwn });
  } else if (e.key === "j" || e.key === "J") {
    showJSON(view);
  } else {
    return;
  }
  e.preventDefault();
});

window.addEventListener("resize", () => { draw(); redrawLabel(); });
window.addEventListener("hashchange", () => void route());

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));
}

void route();
