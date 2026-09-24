// Review tool. Routes:
//   #/                                   lobby: Label sessions and the list of games with bundles
//   #/game/<id>/<turn>?pov=<seat>&own=hidden   Inspect mode: a game at UI turn <turn> (1-based)
//   #/label/<session>/<turn>             Label mode (label.ts)

import { getInspectBundle, listGames, listSessions, startSession, type Bundle, type GameSummary, type SessionSummary } from "./api";
import { labelActive, labelKey, leaveLabel, redrawLabel, showLabel } from "./label";
import { closeOverlay, openOverlay, overlayOpen } from "./overlay";
import { el, renderTable, type View } from "./table";

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
  const match = /^#\/game\/(\d+)(?:\/(\d+))?(?:\?(.*))?$/.exec(location.hash);
  if (match === null) {
    view = null;
    await showLobby();
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
    lobby: () => { location.hash = "#/"; },
    showChecks: () => showChecks(v.bundle),
    showJSON: () => showJSON(v),
    showFullLog: () => showFullLog(v),
  });
}

// ---- Lobby ---------------------------------------------------------------------------------------

async function showLobby(): Promise<void> {
  app.replaceChildren(el("div", "message", "Loading games…"));
  let games: GameSummary[];
  try {
    games = await listGames();
  } catch (error) {
    app.replaceChildren(el("div", "message", `Could not load games: ${String(error)}`));
    return;
  }
  const page = el("div", "lobby-page");
  page.append(labelSection());
  page.append(el("h1", "", "Inspect"), el("p", "hint",
    "Inspect mode: check that games replay correctly. ← → move one turn, [ ] one round, Home/End jump, O shows or hides the top row, J shows the JSON."));
  const table = el("table", "games");
  const head = el("tr");
  for (const h of ["Game", "Players", "Variant", "Turns", "Score", "Checks"]) head.append(el("th", "", h));
  table.append(head);
  for (const g of games) {
    const row = el("tr", "clickable");
    const status = g.errors > 0 ? `✗ ${g.errors} errors` : "✓" + (g.wording > 0 ? ` (${g.wording} wording)` : "");
    for (const cell of [String(g.id), g.players.join(", "), g.variant, String(g.turns), String(g.final_score), status]) {
      row.append(el("td", "", cell));
    }
    row.lastElementChild?.classList.add(g.errors > 0 ? "bad" : "good");
    row.addEventListener("click", () => { location.hash = `#/game/${g.id}/1`; });
    table.append(row);
  }
  if (games.length === 0) {
    page.append(el("p", "", "No bundles yet. Run: python3 review/server/bundle.py prototype/examples/export_*.json"));
  }
  page.append(table);
  app.replaceChildren(page);
}

function labelSection(): HTMLElement {
  const section = el("div", "label-section");
  section.append(el("h1", "", "Label"), el("p", "hint",
    "Sit in one seat of a random game (players anonymised) and choose a move at each of your turns, with speedrun controls."));
  const row = el("div", "control-row");
  const name = el("input", "name-input");
  name.placeholder = "Your name";
  name.value = loadName();
  const start = el("button", "chip active", "Start a new session");
  const status = el("span", "hint");
  row.append(el("span", "control-label", "Labeller"), name, start, status);
  const list = el("div");
  section.append(row, list);

  const refresh = async (): Promise<void> => {
    const who = name.value.trim();
    list.replaceChildren();
    if (who === "") return;
    let sessions: SessionSummary[];
    try {
      sessions = await listSessions(who);
    } catch (error) {
      status.textContent = String(error);
      return;
    }
    if (sessions.length === 0) return;
    const table = el("table", "games");
    const head = el("tr");
    for (const h of ["Session", "Started", "You are", "Players", "Variant", "Turn", "Labels", ""]) head.append(el("th", "", h));
    table.append(head);
    for (const s of sessions) {
      const tr = el("tr", "clickable");
      const cells = [s.session_id, s.started.slice(0, 16).replace("T", " "), s.you, String(s.players), s.variant,
        String(s.turn), String(s.labels), s.ended === null ? "resume →" : "done ✓"];
      for (const c of cells) tr.append(el("td", "", c));
      if (s.ended !== null) tr.lastElementChild?.classList.add("good");
      tr.addEventListener("click", () => { location.hash = `#/label/${s.session_id}/${s.turn}`; });
      table.append(tr);
    }
    list.append(table);
  };
  name.addEventListener("change", () => { saveName(name.value.trim()); void refresh(); });
  start.addEventListener("click", async () => {
    const who = name.value.trim();
    if (who === "") {
      status.textContent = "Enter your name first.";
      name.focus();
      return;
    }
    saveName(who);
    try {
      const data = await startSession(who);
      location.hash = `#/label/${data.session.session_id}/1`;
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error);
    }
  });
  void refresh();
  return section;
}

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
