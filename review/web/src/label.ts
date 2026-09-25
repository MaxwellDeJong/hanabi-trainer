// Label mode (docs/review-tool.md §4): sit in one seat of a game and choose a move at each of its turns,
// with the site's speedrun controls. Route: #/label/<session>/<turn>.
//
// The server sends only what the seat can know up to the furthest turn reached (the "frontier"), and
// checks every label against the legal moves. Turns are UI turns (1-based); `pos` = turn − 1.

import { getSession, sessionAction, type Choice, type LabelView, type Obs } from "./api";
import { cardArt, type CardArt } from "./cards";
import { celebrate } from "./fireworks";
import { closeOverlay, openOverlay, overlayOpen } from "./overlay";
import { getAdvance, setAdvance, stepSeconds } from "./settings";
import { playTurnSound } from "./sounds";
import { el, renderTable, type Mark, type Pointer, type TableMode } from "./table";
import { tip } from "./tips";

const SUIT_LETTERS = "RYGBPT";
const PLAY_FILL = "#1b7f2e";
const DISCARD_FILL = "#8c1c1c";

interface State {
  sid: string;
  data: LabelView;
  pos: number;
  /** Shift-clicked moves ("equally good") waiting for the main choice at this position. */
  pending: Choice[];
  /** Hint toggle (Tab) for the current position. */
  hint: boolean;
  message: string;
  /** When the current position was first shown (for `ms_to_choice`). */
  shownAt: number;
  busy: boolean;
}

let st: State | null = null;
let root: HTMLElement | null = null;

export function labelActive(): boolean {
  return st !== null;
}

export function leaveLabel(): void {
  st = null;
  scheduleAuto();
}

export async function showLabel(app: HTMLElement, sid: string, turn: number | null): Promise<void> {
  root = app;
  if (st?.sid !== sid) {
    app.replaceChildren(el("div", "message", "Loading session…"));
    let data: LabelView;
    try {
      data = await getSession(sid);
    } catch (error) {
      st = null;
      app.replaceChildren(el("div", "message", `Could not load session: ${String(error)}`));
      return;
    }
    st = { sid, data, pos: 0, pending: [], hint: false, message: "", shownAt: performance.now(), busy: false };
    // As on the site, a sound on first load only at the start of the game.
    if (data.bundle.turns === 0 && data.session.submitted === null) playSound();
  }
  setPos(turn === null ? st.data.bundle.turns : turn - 1);
}

export function redrawLabel(): void {
  if (st !== null) draw();
}

// ---- Navigation and server calls ----------------------------------------------------------------------

function setPos(pos: number): void {
  if (st === null) return;
  const p = Math.max(0, Math.min(st.data.bundle.turns, Number.isFinite(pos) ? pos : 0));
  if (p !== st.pos) {
    st.pending = [];
    st.hint = false;
    st.shownAt = performance.now();
  }
  st.pos = p;
  history.replaceState(null, "", `#/label/${st.sid}/${p + 1}`);
  draw();
}

async function act(action: string, body: Record<string, unknown> = {}): Promise<LabelView | null> {
  if (st === null || st.busy) return null;
  const s = st;
  s.busy = true;
  try {
    const before = s.data.bundle.turns;
    s.data = await sessionAction(s.sid, action, body);
    s.message = "";
    if (s.data.bundle.turns > before) playSound();
    return s.data;
  } catch (error) {
    flash(error instanceof Error ? error.message : String(error));
    return null;
  } finally {
    s.busy = false;
    if (st === s) draw();
  }
}

async function advance(): Promise<void> {
  if (st === null) return;
  if (isOwnTurn(st.pos) && st.data.labels[st.pos + 1] === undefined) {
    flash("Choose a move first.");
    return;
  }
  if (st.pos < st.data.bundle.turns) {
    setPos(st.pos + 1);
    return;
  }
  if (st.data.game_over) {
    flash(isSubmitted() ? "Submitted. Go to the Lobby for a new session." : "The game is over: submit your moves.");
    return;
  }
  const data = await act("advance");
  if (data !== null) setPos(data.bundle.turns);
}

/** Your move: recorded, and the game goes on to the next turn straight away, as in a live game. */
async function choose(choice: Choice): Promise<void> {
  if (st === null) return;
  const pos = st.pos;
  const data = await act("labels", {
    turn: pos + 1,
    choice,
    also_ok: st.pending.filter((c) => !sameChoice(c, choice)),
    ms_to_choice: Math.round(performance.now() - st.shownAt),
    advance: true,
  });
  if (data !== null && st !== null) setPos(pos + 1);
}

async function toggleAlso(choice: Choice): Promise<void> {
  if (st === null) return;
  const turn = st.pos + 1;
  const current = st.data.labels[turn];
  if (current === undefined) {
    // No main choice yet: keep it until the plain click.
    const i = st.pending.findIndex((c) => sameChoice(c, choice));
    if (i >= 0) st.pending.splice(i, 1);
    else st.pending.push(choice);
    draw();
    return;
  }
  if (sameChoice(current.choice, choice)) return;
  const also = current.also_ok.some((c) => sameChoice(c, choice))
    ? current.also_ok.filter((c) => !sameChoice(c, choice))
    : [...current.also_ok, choice];
  await act("labels", { turn, choice: current.choice, also_ok: also, ms_to_choice: Math.round(performance.now() - st.shownAt) });
}

async function toggleHint(): Promise<void> {
  if (st === null) return;
  if (!isOwnTurn(st.pos)) {
    flash("The hint shows the real move at your own turns.");
    return;
  }
  st.hint = !st.hint;
  if (st.hint && st.data.actual[st.pos + 1] === undefined) {
    await act("hint", { turn: st.pos + 1 });
  } else {
    draw();
  }
}

/** Undo your latest move and go back to that turn to choose again. */
async function undo(): Promise<void> {
  if (isSubmitted()) {
    flash("This session is submitted and can't be changed.");
    return;
  }
  const data = await act("retract");
  if (data?.undone_turn !== undefined) setPos(data.undone_turn - 1);
}

/** Hand the session in: only once the game is over and every one of your turns has a move. */
async function submit(): Promise<void> {
  if (st === null) return;
  const missing = st.data.own_turns.filter((t) => st!.data.labels[t] === undefined);
  if (missing.length > 0) {
    setPos(missing[0]! - 1);
    flash(`Choose a move at turn ${missing.join(", ")} before submitting.`);
    return;
  }
  const data = await act("submit");
  if (data?.session.submitted == null || st === null) return;
  // The reward, then back to the lobby for the next seat (unless the labeller already left).
  const sid = st.sid;
  const art = cardArt(data.bundle.game.export.options.variant ?? "No Variant");
  const turns = data.own_turns.length;
  // Fireworks in the game's suit colours (a rainbow-like suit's fill is "multi": use its colours).
  const colors = art.variant.suits.flatMap((x) => (x.fill === "multi" ? [...x.fillColors] : [x.fill]));
  await celebrate("Submitted!", `${turns} move${turns === 1 ? "" : "s"} labelled. Thank you!`, colors);
  if (st?.sid === sid) location.hash = "#/";
}

/** The site's sound for the newest position (a move was just revealed). */
function playSound(): void {
  if (st === null) return;
  const pos = st.data.bundle.turns;
  playTurnSound(st.data.bundle.sounds?.[pos], actorAt(pos) === st.data.session.seat);
}

// ---- Auto-advance ------------------------------------------------------------------------------------

let autoTimer: number | undefined;
/** What the running timer is for: session, position and seconds ("" when none is running). */
let autoKey = "";

/**
 * With auto-advance on, reveal the next move after the set time whenever the newest position is another
 * player's turn. Never while looking back, on your own turn or once the game is over. Called on every
 * draw; the timer only restarts when the position or the setting changes.
 */
function scheduleAuto(): void {
  const s = st;
  const a = getAdvance();
  const waiting = s !== null && a.auto && s.pos === s.data.bundle.turns && !s.data.game_over && !isOwnTurn(s.pos) &&
    s.data.session.submitted === null;
  const key = waiting ? `${s.sid}/${s.pos}/${a.seconds}` : "";
  if (key === autoKey) return;
  autoKey = key;
  window.clearTimeout(autoTimer);
  if (!waiting) return;
  const fire = (): void => {
    if (st !== s || autoKey !== key) return;
    if (s.busy || overlayOpen()) {
      autoTimer = window.setTimeout(fire, 250);
      return;
    }
    void advance().then(() => {
      // Still here: the reveal failed (the error is flashed). Try again after the same wait.
      if (autoKey === key) {
        autoKey = "";
        scheduleAuto();
      }
    });
  };
  autoTimer = window.setTimeout(fire, a.seconds * 1000);
}

function isSubmitted(): boolean {
  return st !== null && st.data.session.submitted !== null;
}

let flashTimer: number | undefined;

/** A short message under the buttons. */
function flash(message: string): void {
  if (st === null) return;
  st.message = message;
  window.clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => {
    if (st !== null && st.message === message) {
      st.message = "";
      draw();
    }
  }, 3000);
  draw();
}

// ---- Keys --------------------------------------------------------------------------------------------

/** Handles a key in Label mode; returns whether it was used. */
export function labelKey(e: KeyboardEvent): boolean {
  if (st === null) return false;
  const n = st.data.bundle.game.export.players.length;
  switch (e.key) {
    case "ArrowLeft": setPos(st.pos - 1); break;
    case "ArrowRight":
    case " ": void advance(); break;
    case "[": setPos(st.pos - n); break;
    case "]": setPos(st.pos + n); break;
    case "Home": setPos(0); break;
    case "End": setPos(st.data.bundle.turns); break;
    case "Tab": void toggleHint(); break;
    case "Backspace": void undo(); break;
    default: return false;
  }
  return true;
}

// ---- Drawing -----------------------------------------------------------------------------------------

function draw(): void {
  if (st === null || root === null) return;
  scheduleAuto();
  const s = st;
  const { data, pos } = s;
  const bundle = data.bundle;
  const ex = bundle.game.export;
  const seat = data.session.seat;
  const n = ex.players.length;
  const names = ex.players;
  const art = cardArt(ex.options.variant ?? "No Variant");
  const turn = pos + 1;
  const obs = obsAt(pos);
  const own = isOwnTurn(pos);
  const label = data.labels[turn];
  const atFrontier = pos === bundle.turns;
  const actual = data.actual[turn];
  const submitted = data.session.submitted !== null;

  const describe = (c: Choice, p = pos): string => describeChoice(c, obsAt(p), names, art);

  // Tags for the chosen move and the "equally good" moves, and the hint's arrows.
  const marks = new Map<number, Mark[]>();
  const addMarks = (c: Choice, cls: Mark["cls"]): void => {
    const text = c.type === "clue" ? valueName(c, art) : c.type === "play" ? "Play" : "Discard";
    for (const card of cardsOf(c, obs, seat, n)) {
      marks.set(card, [...(marks.get(card) ?? []), { text: cls === "alt" ? `≈ ${text}` : text, cls }]);
    }
  };
  if (own) {
    if (label !== undefined) addMarks(label.choice, "chosen");
    for (const c of [...(label?.also_ok ?? []), ...s.pending]) addMarks(c, "alt");
  }
  const pointers: Pointer[] = [];
  if (own && s.hint && actual !== undefined) {
    for (const card of cardsOf(actual, obs, seat, n)) {
      if (actual.type === "clue") {
        const color = actual.kind === "color";
        pointers.push({ card, fill: color ? art.fill(SUIT_LETTERS.indexOf(String(actual.value))) : "black", text: color ? "" : String(actual.value) });
      } else {
        pointers.push({ card, fill: actual.type === "play" ? PLAY_FILL : DISCARD_FILL, text: actual.type === "play" ? "P" : "D" });
      }
    }
  }

  const mode: TableMode = {
    key: `label/${s.sid}`,
    lastIsEnd: data.game_over,
    marks,
    pointers,
    controls(box) {
      const status = el("div", "control-row status");
      status.append(el("span", "you", `You are ${names[seat]}`), el("span", "sep", "·"));
      if (submitted && atFrontier) {
        status.append(el("span", "chosen-text", "Submitted ✓ Thanks! This session is now read only."));
      } else if (data.game_over && atFrontier) {
        const missing = data.own_turns.filter((t) => data.labels[t] === undefined).length;
        status.append(el("span", "your-turn", missing > 0
          ? `Game over: ${missing} of your turns still need a move`
          : "Game over: submit your moves when you're happy with them"));
      } else if (own && label === undefined) {
        status.append(el("span", "your-turn", "Your turn"));
      } else if (own) {
        status.append(el("span", "chosen-text", `Your move: ${describe(label!.choice)}`));
        if (label!.also_ok.length > 0) status.append(el("span", "alt-text", `(also OK: ${label!.also_ok.map((c) => describe(c)).join(", ")})`));
      } else {
        status.append(el("span", "", `${names[actorAt(pos)]}'s turn`));
      }
      if (own && s.pending.length > 0) status.append(el("span", "alt-text", `(also OK: ${s.pending.map((c) => describe(c)).join(", ")})`));
      if (own && actual !== undefined && (s.hint || !atFrontier)) {
        status.append(el("span", "actual-text", `Actual: ${describe(actual)}`));
      }

      const buttons = el("div", "control-row");
      const button = (text: string, title: string, onClick: () => void, active = false, into: HTMLElement = buttons): void => {
        const b = tip(el("button", active ? "chip active" : "chip", text), title);
        // Keep focus off the button, so Space and Tab stay game keys.
        b.addEventListener("mousedown", (e) => e.preventDefault());
        b.addEventListener("click", onClick);
        into.append(b);
      };
      button("Hint (Tab)", "Show the move that was actually made at this turn (recorded as hint used)", () => void toggleHint(), s.hint);
      if (!submitted) button("Undo (⌫)", "Take back your latest move and choose again", () => void undo());
      if (!submitted && !data.game_over) {
        // The lobby's "Turn advance" setting, changeable mid-game.
        const a = getAdvance();
        button("Auto-advance", a.auto
          ? `On: the other players' moves are revealed by themselves, one every ${a.seconds} s. Click for manual (Space / →)`
          : "Off: reveal the other players' moves with Space or →. Click to reveal them by themselves",
        () => { setAdvance({ auto: !a.auto }); draw(); }, a.auto);
        const stepper = el("span", a.auto ? "stepper" : "stepper off");
        const step = (dir: 1 | -1): void => { setAdvance({ seconds: stepSeconds(getAdvance().seconds, dir) }); draw(); };
        button("−", "Faster: fewer seconds per turn", () => step(-1), false, stepper);
        stepper.append(tip(el("span", "stepper-value", `${a.seconds} s`), "Seconds each of the other players' turns stays on screen with Auto-advance on"));
        button("+", "Slower: more seconds per turn", () => step(1), false, stepper);
        buttons.append(stepper);
      }
      if (data.game_over && !submitted) {
        button("Submit", "Hand in your moves for this seat. The session can't be changed afterwards", () => void submit(), true);
      }
      if (submitted && !atFrontier) status.append(el("span", "alt-text", "(read only)"));

      const line = el("div", "control-row message-line", s.message);
      box.append(status, buttons, line);
    },
    cardDown(card, holder, e) {
      if (e.button !== 0 && e.button !== 2) return;
      if (e.ctrlKey || e.altKey || e.metaKey) return;
      if (submitted) {
        flash("This session is submitted and can't be changed.");
        return;
      }
      if (!own) {
        flash(atFrontier ? `It's ${names[actorAt(pos)]}'s turn: press Space to continue.` : "Not your turn.");
        return;
      }
      const choice = clickChoice(card, holder, e.button === 0);
      if (choice === null) return;
      if (!isLegal(choice, obs, seat, n)) {
        flash(choice.type === "discard" ? "You can't discard at 8 clues." : choice.type === "clue" ? "No clue tokens left." : "Not a legal move.");
        return;
      }
      if (e.shiftKey) void toggleAlso(choice);
      else void choose(choice);
    },
    logSuffix(k) {
      // Line k is the move made at turn k; show how the label compares once it's public.
      const lab = data.labels[k];
      const act = data.actual[k];
      if (k === 0 || k >= data.session.frontier || lab === undefined || act === undefined) return null;
      if (sameChoice(lab.choice, act)) return { text: " ✓", cls: "you same" };
      if (lab.also_ok.some((c) => sameChoice(c, act))) return { text: " ≈✓", cls: "you same" };
      return { text: ` · you: ${describe(lab.choice, k - 1)}`, cls: "you differ" };
    },
  };

  renderTable(root, { bundle, pos, pov: seat, showOwn: false }, {
    goTo: setPos,
    setPov: () => {},
    toggleOwn: () => {},
    lobby: () => { location.hash = "#/"; },
    showChecks: () => {},
    showJSON: () => {},
    showFullLog: () => showFullLog(describe),
  }, mode);

  function clickChoice(card: number, holder: number, left: boolean): Choice | null {
    if (holder === seat) return { type: left ? "play" : "discard", card };
    const id = obs.cards[card];
    if (id == null) return null;
    return left
      ? { type: "clue", to_seat: holder, kind: "color", value: id[0]! }
      : { type: "clue", to_seat: holder, kind: "rank", value: Number(id.slice(1)) };
  }
}

function showFullLog(describe: (c: Choice, p?: number) => string): void {
  if (st === null) return;
  const { data } = st;
  const list = el("ol", "full-log");
  list.start = 0;
  data.bundle.log.forEach((line, k) => {
    const item = el("li", "clickable", line);
    item.value = k;
    const lab = data.labels[k];
    const act = data.actual[k];
    if (k > 0 && lab !== undefined && act !== undefined) {
      const same = sameChoice(lab.choice, act) || lab.also_ok.some((c) => sameChoice(c, act));
      item.append(el("span", same ? "you same" : "you differ", same ? " ✓" : ` · you: ${describe(lab.choice, k - 1)}`));
    }
    item.addEventListener("click", () => { closeOverlay(); setPos(Math.max(0, k - 1)); });
    list.append(item);
  });
  openOverlay("Full log (number = turn)", list);
}

// ---- Moves -------------------------------------------------------------------------------------------

function obsAt(pos: number): Obs {
  const s = st!;
  const obs = s.data.bundle.seat_views[s.data.session.seat]?.[pos];
  if (obs === undefined) throw new Error(`no seat view at position ${pos}`);
  return obs;
}

function actorAt(pos: number): number {
  const ex = st!.data.bundle.game.export;
  return (pos + Number(ex.options["startingPlayer"] ?? 0)) % ex.players.length;
}

function isOwnTurn(pos: number): boolean {
  return st !== null && st.data.own_turns.includes(pos + 1);
}

function sameChoice(a: Choice, b: Choice): boolean {
  return JSON.stringify(normal(a)) === JSON.stringify(normal(b));
}

function normal(c: Choice): unknown[] {
  return c.type === "clue" ? [c.type, c.to_seat, c.kind, c.value] : [c.type, c.card];
}

/** The move in the DecisionRecord's `legal` format (relative seats, slots). */
function isLegal(c: Choice, obs: Obs, seat: number, n: number): boolean {
  let legal: Record<string, unknown>;
  if (c.type === "clue") {
    legal = { type: "clue", to: (c.to_seat - seat + n) % n, kind: c.kind, value: c.value };
  } else {
    const slot = obs.hands[0]?.slots.find((sl) => sl.card === c.card)?.slot;
    legal = { type: c.type, slot };
  }
  const key = JSON.stringify(legal);
  return obs.legal.some((l) => JSON.stringify(l) === key);
}

/** The cards a move is about: the card played or discarded, or every card a clue touches. */
function cardsOf(c: Choice, obs: Obs, seat: number, n: number): number[] {
  if (c.type !== "clue") return [c.card];
  const hand = obs.hands.find((h) => (seat + h.seat) % n === c.to_seat);
  return (hand?.slots ?? []).filter((sl) => {
    const id = obs.cards[sl.card];
    if (id == null) return false;
    return c.kind === "color" ? id[0] === c.value : Number(id.slice(1)) === c.value;
  }).map((sl) => sl.card);
}

function valueName(c: Extract<Choice, { type: "clue" }>, art: CardArt): string {
  return c.kind === "color" ? art.variant.suits[SUIT_LETTERS.indexOf(String(c.value))]?.name ?? String(c.value) : String(c.value);
}

function describeChoice(c: Choice, obs: Obs, names: string[], art: CardArt): string {
  if (c.type === "clue") return `tell ${names[c.to_seat] ?? "?"} ${valueName(c, art)}`;
  const slot = obs.hands[0]?.slots.find((sl) => sl.card === c.card)?.slot;
  return `${c.type} slot ${slot ?? "?"}`;
}
