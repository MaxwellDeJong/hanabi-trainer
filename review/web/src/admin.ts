// Admin view (#/admin): labelling coverage and each labeller's contributions at a glance, and the games to
// check in Inspect mode. Unlike the lobby it shows real player names, so it's for admins only
// (docs/review-tool.md §4.4).

import { getAdmin, listGames, type AdminReport, type GameSummary } from "./api";
import { el } from "./table";
import { heading, headRow, tip } from "./tips";

const ICON = { open: "○", active: "✎", submitted: "✓" } as const;
const WORD = { open: "open", active: "in progress", submitted: "submitted" } as const;

export async function showAdmin(app: HTMLElement): Promise<void> {
  app.replaceChildren(el("div", "message", "Loading…"));
  let r: AdminReport;
  let games: GameSummary[];
  try {
    [r, games] = await Promise.all([getAdmin(), listGames()]);
  } catch (error) {
    app.replaceChildren(el("div", "message", `Could not load the admin report: ${String(error)}`));
    return;
  }
  const page = el("div", "lobby-page admin");
  const nav = el("div", "control-row nav");
  const lobby = el("a", "chip", "← Lobby") as HTMLAnchorElement;
  lobby.href = "#/";
  const refresh = el("button", "chip", "Refresh");
  refresh.addEventListener("click", () => void showAdmin(app));
  nav.append(lobby, refresh, el("span", "hint", `as of ${r.generated.slice(0, 19).replace("T", " ")} UTC`));
  page.append(nav, el("h1", "", "Admin"));

  const t = r.totals;
  const pct = (a: number, b: number): string => (b === 0 ? "–" : `${Math.round((100 * a) / b)}%`);
  const tiles = el("div", "tiles");
  const tile = (label: string, value: string, note: string): void => {
    const d = el("div", "tile");
    d.append(el("div", "tile-label", label), el("div", "tile-value", value), el("div", "tile-note", note));
    tiles.append(d);
  };
  tile("Seats submitted", `${t.submitted} / ${t.seats}`, pct(t.submitted, t.seats));
  tile("Seats in progress", String(t.active), `${t.open} open`);
  tile("Turns labeled", `${t.own_turns_submitted} / ${t.own_turns}`, `${pct(t.own_turns_submitted, t.own_turns)} (submitted seats)`);
  tile("Labelers", String(t.labellers), `${t.sessions_active} active · ${t.sessions_submitted} submitted sessions`);
  tile("Games", `${t.labelable_games} / ${t.games}`,
    t.games > t.labelable_games ? `${t.games - t.labelable_games} excluded (check errors)` : "all open for labeling");
  page.append(tiles, meter(t), coverageCharts(r.games));

  // ---- Labelers ---------------------------------------------------------------------------------
  page.append(heading("h2", "Labelers",
    "One row per labeler: their sessions still in force (submitted or active) and the moves chosen in them. " +
    "Expired sessions don't count. Most moves first."));
  if (r.labellers.length === 0) {
    page.append(el("p", "hint", "No sessions yet."));
  } else {
    page.append(table(
      ["Labeler", "Submitted", "Active", "Moves", "In submitted", "Hint used", "After undo", "Median time", "First seen", "Last active"],
      r.labellers.map((p) => [p.labeller, String(p.submitted), String(p.active), String(p.labels), String(p.labels_submitted),
        `${p.hint_labels} (${pct(p.hint_labels, p.labels)})`, String(p.after_reveal), seconds(p.median_ms),
        p.first.slice(0, 10), ago(p.last_active)]),
      ["", "num", "num", "num", "num", "num", "num", "num", "", ""], LABELER_TIPS));
  }

  // ---- Sessions ---------------------------------------------------------------------------------
  page.append(heading("h2", "Sessions",
    "Most recent first. An active session claims its seat until it's submitted, or until it expires 24 hours after it " +
    "started: then its moves are dropped and the seat is open again."));
  if (r.sessions.length > 0) {
    const rows = r.sessions.map((x) => {
      return [x.labeller, `${x.game_id}`, `${x.you} (seat ${x.seat})`,
        `${ICON[x.status]} ${x.status === "active" && x.game_over ? "ready to submit" : WORD[x.status]}${x.status === "active" ? ` · expires ${until(x.expires)}` : ""}`,
        `${Math.min(x.turn, x.turns + 1)} / ${x.turns + 1}`, `${x.labels} / ${x.own_turns}`, String(x.hints),
        x.chosen_by ?? "", ago(x.last_active)];
    });
    const tbl = table(["Labeler", "Game", "Seat", "Status", "Turn", "Moves", "Hints", "Chosen", "Last active"], rows,
      ["", "num", "", "", "num", "num", "num", "", ""], SESSION_TIPS);
    r.sessions.forEach((x, i) => {
      const tr = tbl.rows[i + 1]!;
      tr.classList.add(x.status);
      linkGame(tr.cells[1]!, x.game_id, x.seat);
    });
    page.append(tbl);
  }

  // ---- Coverage ---------------------------------------------------------------------------------
  page.append(heading("h2", "Coverage by game",
    "Newest game first, with the players' real names. Each seat: its status (✓ submitted, ✎ in progress, ○ open), " +
    "who labeled it and how far they got. Games with check errors are never handed out."));
  const width = Math.max(0, ...r.games.map((g) => g.players.length));
  const heads = ["Game", "Variant", "Turns", "Score", "Bombs", ...Array.from({ length: width }, (_, k) => `Seat ${k}`)];
  const cov = el("table", "games coverage");
  cov.append(headRow(heads, COVERAGE_TIPS));
  for (const g of [...r.games].sort((a, b) => b.id - a.id)) {
    const tr = el("tr");
    const id = el("td", "num");
    linkGame(id, g.id, 0);
    tr.append(id, el("td", "", g.variant), el("td", "num", String(g.turns)), el("td", "num", `${g.score} / ${g.max_score}`),
      el("td", "num", String(g.bombs)));
    if (g.errors > 0) {
      const td = el("td", "bad", `excluded: ${g.errors} check errors`);
      td.colSpan = width;
      tr.append(td);
    } else {
      for (const x of g.seats) {
        const td = el("td", `seat ${x.status}`);
        td.append(el("div", "seat-head", `${ICON[x.status]} ${x.player}`));
        const who = x.sessions.map((q) => `${q.labeller}: ${q.status === "submitted" ? "done" : `turn ${q.turn}`}, ${q.labels}/${x.own_turns} moves`);
        td.append(el("div", "seat-who", who.length > 0 ? who.join("; ") : `${WORD[x.status]} · ${x.own_turns} turns`));
        tr.append(td);
      }
      for (let k = g.seats.length; k < width; k++) tr.append(el("td"));
    }
    cov.append(tr);
  }
  page.append(cov, inspectSection(games));
  app.replaceChildren(page);
}

const LABELER_TIPS: Record<string, string> = {
  Labeler: "The name they entered in the lobby",
  Submitted: "Sessions handed in: seats done",
  Active: "Sessions started but not submitted yet",
  Moves: "Moves chosen in all their sessions, submitted and active",
  "In submitted": "Moves chosen in submitted sessions only",
  "Hint used": "Moves chosen after revealing the real move with the hint, and their share of all their moves",
  "After undo": "Moves chosen at a turn whose real move had already been revealed: re-chosen after an undo",
  "Median time": "Median time from a position being shown to a move being chosen",
  "First seen": "When their first session still in force started (UTC)",
  "Last active": "Their latest activity in any session",
};

const SESSION_TIPS: Record<string, string> = {
  Labeler: "Who the session belongs to",
  Game: "The game's ID. Click it to inspect the game from this seat's view",
  Seat: "The anonymous name the labeler plays as, and the seat number",
  Status: "✓ submitted, or ✎ in progress with when it expires. \"Ready to submit\": played to the end, not handed in yet",
  Turn: "The furthest turn reached, out of the game's turns plus the final position",
  Moves: "Moves chosen, out of the seat's own turns",
  Hints: "Times the labeler revealed the real move with the hint",
  Chosen: "How the seat was taken: random (the Start button) or picked from the lobby's board",
  "Last active": "Latest activity in this session",
};

const COVERAGE_TIPS: Record<string, string> = {
  Game: "The game's ID. Click it to inspect the game",
  Turns: "Number of turns the game lasted",
  Score: "Cards played correctly (the sum of the stacks), out of the maximum. This is not the site's score, " +
    "which is 0 for any game short of the maximum under All or Nothing",
  Bombs: "Misplays (strikes) by the end of the game",
};

const INSPECT_TIPS: Record<string, string> = {
  Game: "The game's ID on the site",
  Players: "The players' real names, in seat order",
  Turns: "Number of turns the game lasted",
  Score: "Final score",
  Checks: "✓: the replay matches the site's own game engine. ✗: how many checks failed. " +
    "\"Wording\" counts log lines worded differently from the site's, which is harmless",
};

/** Every game with a bundle, to open in Inspect mode. */
function inspectSection(games: GameSummary[]): HTMLElement {
  const box = el("div");
  box.append(heading("h2", "Inspect",
    "Replay a downloaded game from any player's view, to check that it was rebuilt correctly. Click a game to open it.\n\n" +
    "Keys: ← → one turn · [ ] one round · Home / End first or last turn · O show or hide the top row's cards · " +
    "J the turn's JSON · hover a card for its details"));
  if (games.length === 0) {
    box.append(el("p", "", "No bundles yet. Run: python3 review/server/bundle.py examples/export_*.json"));
    return box;
  }
  const t = el("table", "games");
  t.append(headRow(["Game", "Players", "Variant", "Turns", "Score", "Checks"], INSPECT_TIPS));
  for (const g of games) {
    const row = tip(el("tr", "clickable"), `Inspect game ${g.id}`);
    const status = g.errors > 0 ? `✗ ${g.errors} errors` : "✓" + (g.wording > 0 ? ` (${g.wording} wording)` : "");
    for (const cell of [String(g.id), g.players.join(", "), g.variant, String(g.turns), String(g.final_score), status]) {
      row.append(el("td", "", cell));
    }
    row.lastElementChild?.classList.add(g.errors > 0 ? "bad" : "good");
    row.addEventListener("click", () => { location.hash = `#/game/${g.id}/1`; });
    t.append(row);
  }
  box.append(t);
  return box;
}

// ---- Coverage charts -------------------------------------------------------------------------------

const STATUSES = ["submitted", "active", "open"] as const;
type Status = (typeof STATUSES)[number];
type Counts = Record<Status, number>;
type Games = AdminReport["games"];

/** Seats of the labelable games by suit count, final score and bombs, each column stacked by seat status.
 * A seat is one labelling task, so a 3-player game counts 3 times. */
function coverageCharts(games: Games): HTMLElement {
  const box = el("div");
  box.append(heading("h2", "Coverage at a glance",
    "Every seat of every game open for labeling, split by status: a seat is one labeling task, so a " +
    "3-player game counts 3 times. Games with check errors are left out. Hover a column for its numbers."));
  const seats = games.filter((g) => g.errors === 0).flatMap((g) => g.seats.map((x) => ({ g, status: x.status })));
  if (seats.length === 0) {
    box.append(el("p", "hint", "No games open for labeling."));
    return box;
  }
  const legend = el("div", "control-row meter-legend");
  for (const st of STATUSES) {
    const item = el("span", `legend ${st}`);
    item.append(el("span", "swatch"), el("span", "", `${ICON[st]} ${WORD[st]}`));
    legend.append(item);
  }
  /** Seats counted by `key`, one bin per value in `values`. */
  const bins = (values: number[], key: (g: Games[number]) => number): Map<number, Counts> => {
    const out = new Map(values.map((v) => [v, { submitted: 0, active: 0, open: 0 }]));
    for (const x of seats) {
      const c = out.get(key(x.g));
      if (c !== undefined) c[x.status]++;
    }
    return out;
  };
  const range = (lo: number, hi: number): number[] => Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
  const suits = [...new Set(seats.map((x) => x.g.suits))].sort((a, b) => a - b);
  const maxScore = Math.max(...seats.map((x) => x.g.max_score));
  const maxBombs = Math.max(3, ...seats.map((x) => x.g.bombs));
  const row = el("div", "charts");
  row.append(
    columnChart("Variant", "small", bins(suits, (g) => g.suits), (v) => `${v} suits`, (v) => `${v} suits`),
    columnChart("Final score (cards played)", "wide", bins(range(0, maxScore), (g) => g.score), (v) => `Score ${v}`,
      (v) => (v % 5 === 0 ? String(v) : "")),
    columnChart("Bombs", "small", bins(range(0, maxBombs), (g) => g.bombs), (v) => `${v} bomb${v === 1 ? "" : "s"}`,
      String),
  );
  box.append(legend, row);
  return box;
}

/** One chart card: a column per bin, stacked bottom-up submitted / in progress / open. `name` titles a bin in
 * its tooltip; `axis` is its label under the axis ("" to skip one where the axis is crowded). */
function columnChart(title: string, size: "small" | "wide", data: Map<number, Counts>, name: (v: number) => string,
  axis: (v: number) => string): HTMLElement {
  const card = el("div", `chart ${size}`);
  card.append(el("div", "chart-title", title));
  const total = (c: Counts): number => c.submitted + c.active + c.open;
  const { top, step } = yAxis(Math.max(1, ...[...data.values()].map(total)));
  const plot = el("div", "chart-plot");
  for (let y = 0; y <= top; y += step) {
    const line = el("div", "chart-grid");
    line.style.bottom = `${(100 * y) / top}%`;
    line.append(el("span", "chart-tick", String(y)));
    plot.append(line);
  }
  const cols = el("div", "chart-cols");
  const labels = el("div", "chart-labels");
  const direct = data.size <= 8;  // a total on every cap only while there's room for it
  for (const [v, c] of data) {
    const n = total(c);
    const slot = el("div", "chart-slot");
    const bar = el("div", "chart-bar");
    bar.style.height = `${(100 * n) / top}%`;
    for (const st of STATUSES) {
      if (c[st] === 0) continue;
      const seg = el("div", `chart-seg ${st}`);
      seg.style.flexGrow = String(c[st]);
      bar.append(seg);
    }
    if (direct && n > 0) slot.append(el("div", "chart-cap", String(n)));
    slot.append(bar);
    const parts = STATUSES.map((st) => `${ICON[st]} ${WORD[st]}: ${c[st]}`).join("\n");
    tip(slot, `${name(v)}: ${n} seat${n === 1 ? "" : "s"}\n${parts}`).tabIndex = 0;
    cols.append(slot);
    labels.append(el("div", "chart-label", axis(v)));
  }
  plot.append(cols);
  card.append(plot, labels);
  return card;
}

/** The y axis: a round top at or above `n`, and the gridline step (at most five steps above 0). */
function yAxis(n: number): { top: number; step: number } {
  let step = 1;
  for (let k = 0; Math.ceil(n / step) > 5; k++) step = [2, 5, 10][k % 3]! * 10 ** Math.floor(k / 3);
  return { top: step * Math.ceil(n / step), step };
}

/** Seats by status as one stacked bar, with a legend (status is never colour alone: icon + word). */
function meter(t: AdminReport["totals"]): HTMLElement {
  const box = el("div", "meter-box");
  const bar = el("div", "meter");
  const legend = el("div", "control-row meter-legend");
  for (const st of ["submitted", "active", "open"] as const) {
    const n = t[st];
    if (t.seats > 0 && n > 0) {
      const seg = el("div", `meter-seg ${st}`);
      seg.style.flexGrow = String(n);
      seg.title = `${WORD[st]}: ${n} of ${t.seats} seats`;
      bar.append(seg);
    }
    const item = el("span", `legend ${st}`);
    item.append(el("span", "swatch"), el("span", "", `${ICON[st]} ${WORD[st]} ${n}`));
    legend.append(item);
  }
  box.append(bar, legend);
  return box;
}

function table(headers: string[], rows: string[][], cls: string[], tips: Record<string, string>): HTMLTableElement {
  const t = el("table", "games");
  const head = headRow(headers, tips);
  [...head.cells].forEach((th, i) => { if (cls[i]) th.classList.add(cls[i]!); });
  t.append(head);
  for (const r of rows) {
    const tr = el("tr");
    r.forEach((c, i) => tr.append(el("td", cls[i] ?? "", c)));
    t.append(tr);
  }
  return t;
}

/** Opens the game in Inspect mode from that seat's view. */
function linkGame(td: HTMLTableCellElement, id: number, seat: number): void {
  const a = el("a", "", String(id)) as HTMLAnchorElement;
  a.href = `#/game/${id}/1${seat === 0 ? "" : `?pov=${seat}`}`;
  td.replaceChildren(a);
}

function seconds(ms: number | null): string {
  return ms === null ? "–" : `${(ms / 1000).toFixed(1)} s`;
}

/** "5 min ago", "3 h ago", "2 d ago". */
/** How long until `iso`, e.g. "in 5 h". */
export function until(iso: string): string {
  const s = (Date.parse(iso) - Date.now()) / 1000;
  if (!Number.isFinite(s)) return "–";
  if (s < 60) return "now";
  if (s < 3600) return `in ${Math.floor(s / 60)} min`;
  return `in ${Math.floor(s / 3600)} h`;
}

export function ago(iso: string): string {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return "–";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
