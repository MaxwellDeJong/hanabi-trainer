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
  tile("Turns labelled", `${t.own_turns_submitted} / ${t.own_turns}`, `${pct(t.own_turns_submitted, t.own_turns)} (submitted seats)`);
  tile("Labellers", String(t.labellers), `${t.sessions_active} active · ${t.sessions_submitted} submitted sessions`);
  tile("Games", `${t.labelable_games} / ${t.games}`,
    t.games > t.labelable_games ? `${t.games - t.labelable_games} excluded (check errors)` : "all open for labelling");
  page.append(tiles, meter(t));

  // ---- Labellers --------------------------------------------------------------------------------
  page.append(el("h2", "", "Labellers"));
  if (r.labellers.length === 0) {
    page.append(el("p", "hint", "No sessions yet."));
  } else {
    page.append(table(
      ["Labeller", "Submitted", "Active", "Moves", "In submitted", "Hint used", "After undo", "Median time", "First seen", "Last active"],
      r.labellers.map((p) => [p.labeller, String(p.submitted), String(p.active), String(p.labels), String(p.labels_submitted),
        `${p.hint_labels} (${pct(p.hint_labels, p.labels)})`, String(p.after_reveal), seconds(p.median_ms),
        p.first.slice(0, 10), ago(p.last_active)]),
      ["", "num", "num", "num", "num", "num", "num", "num", "", ""]));
  }

  // ---- Sessions ---------------------------------------------------------------------------------
  page.append(el("h2", "", "Sessions"), el("p", "hint",
    "Most recent first. An active session claims its seat until it's submitted, or until it expires 24 hours after it " +
    "started: then its moves are dropped and the seat is open again."));
  if (r.sessions.length > 0) {
    const rows = r.sessions.map((x) => {
      return [x.labeller, `${x.game_id}`, `${x.you} (seat ${x.seat})`,
        `${ICON[x.status]} ${x.status === "active" && x.game_over ? "ready to submit" : WORD[x.status]}${x.status === "active" ? ` · expires ${until(x.expires)}` : ""}`,
        `${Math.min(x.turn, x.turns + 1)} / ${x.turns + 1}`, `${x.labels} / ${x.own_turns}`, String(x.hints),
        x.chosen_by ?? "", ago(x.last_active)];
    });
    const tbl = table(["Labeller", "Game", "Seat", "Status", "Turn", "Moves", "Hints", "Chosen", "Last active"], rows,
      ["", "num", "", "", "num", "num", "num", "", ""]);
    r.sessions.forEach((x, i) => {
      const tr = tbl.rows[i + 1]!;
      tr.classList.add(x.status);
      linkGame(tr.cells[1]!, x.game_id, x.seat);
    });
    page.append(tbl);
  }

  // ---- Coverage ---------------------------------------------------------------------------------
  page.append(el("h2", "", "Coverage by game"), el("p", "hint",
    "Each seat: status, who labelled it and how far they got. Games with check errors are never handed out."));
  const width = Math.max(0, ...r.games.map((g) => g.players.length));
  const heads = ["Game", "Variant", "Turns", ...Array.from({ length: width }, (_, k) => `Seat ${k}`)];
  const cov = el("table", "games coverage");
  const head = el("tr");
  for (const h of heads) head.append(el("th", "", h));
  cov.append(head);
  for (const g of [...r.games].sort((a, b) => b.id - a.id)) {
    const tr = el("tr");
    const id = el("td", "num");
    linkGame(id, g.id, 0);
    tr.append(id, el("td", "", g.variant), el("td", "num", String(g.turns)));
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

function table(headers: string[], rows: string[][], cls: string[]): HTMLTableElement {
  const t = el("table", "games");
  const head = el("tr");
  headers.forEach((h, i) => head.append(el("th", cls[i] ?? "", h)));
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
