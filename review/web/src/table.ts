// The game table in hanab.live's default ("row", non-Keldon) layout. Positions are fractions of the
// stage, taken from hanab.live's drawUI.ts, drawHands.ts and drawReplayArea.ts (c1d970b).
//
// Everything shown comes from the bundle (docs/review-tool.md §6.1); nothing about the game state is
// computed here.

import background from "hanabi-live-img/background.jpg";
import replayBackFull from "hanabi-live-img/replay-back-full.png";
import replayBackFullDisabled from "hanabi-live-img/replay-back-full-disabled.png";
import replayBack from "hanabi-live-img/replay-back.png";
import replayBackDisabled from "hanabi-live-img/replay-back-disabled.png";
import replayForward from "hanabi-live-img/replay-forward.png";
import replayForwardDisabled from "hanabi-live-img/replay-forward-disabled.png";
import replayForwardFull from "hanabi-live-img/replay-forward-full.png";
import replayForwardFullDisabled from "hanabi-live-img/replay-forward-full-disabled.png";
import trashcan from "hanabi-live-img/trashcan.png";
import strikeX from "hanabi-live-img/x.png";
import type { Bundle, ClueRecord, Slot } from "./api";
import { CARD_H, CARD_W, cardArt, type CardArt } from "./cards";
import { hideTooltip, showTooltip } from "./tips";

const LABEL_COLOR = "#d8d5ef";
const CLUED_COLOR = "orange";
const STRIKE_FADE = 0.175;
const SUIT_LETTERS = "RYGBPT";
const ASPECT = 16 / 9;
const SITE = "https://new.playhanabi.com";
/** How long a card takes to move (draw, play, discard, hands shifting over). */
const MOVE_MS = 400;

export interface View {
  bundle: Bundle;
  /** Position index: 0 = before the first action, `bundle.turns` = the final position. UI turn = pos + 1. */
  pos: number;
  /** Absolute seat whose hand is the top row ("us" on the site). */
  pov: number;
  /** Show the top row's cards face-up (spectator) or as that player sees them. */
  showOwn: boolean;
}

export interface Handlers {
  goTo(pos: number): void;
  setPov(seat: number): void;
  toggleOwn(): void;
  lobby(): void;
  showChecks(): void;
  showJSON(): void;
  showFullLog(): void;
}

/**
 * A small tag drawn on a card: the chosen move, an "equally good" move, or (ghost) your move where the
 * recorded one was different.
 */
export interface Mark {
  text: string;
  cls: "chosen" | "alt" | "ghost";
}

/** An arrow like the site's clue arrows, pointing at a card (the hint). */
export interface Pointer {
  card: number;
  fill: string;
  text: string;
}

/** Label mode's additions to the table. Without one, the table is in Inspect mode. */
export interface TableMode {
  /** Identifies what is shown (the session), so cards only animate between positions of the same one. */
  key: string;
  /** Whether the last position is the end of the game (in Label mode the last position is the frontier). */
  lastIsEnd: boolean;
  /** Fills the controls area, where Inspect mode has its controls. */
  controls(box: HTMLElement): void;
  /** Mouse down on a card in a hand; `holder` is the absolute seat. */
  cardDown(card: number, holder: number, e: MouseEvent): void;
  marks: Map<number, Mark[]>;
  pointers: Pointer[];
  /** Extra text after action log line `k`. */
  logSuffix(k: number): { text: string; cls: string } | null;
  /** A hand to pulse once (your move differed from the recorded one); `elapsed` ms of it already shown. */
  pulse?: { seat: number; elapsed: number };
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Where each card was drawn last time (stage fractions), to animate the move to the next position. */
let lastLayout: { key: string; pos: number; boxes: Map<number, Box> } | null = null;

export function renderTable(root: HTMLElement, view: View, on: Handlers, mode?: TableMode): void {
  const { bundle, pos, pov, showOwn } = view;
  const ex = bundle.game.export;
  const n = ex.players.length;
  const art = cardArt(ex.options.variant ?? "No Variant");
  const numSuits = art.variant.suits.length;
  const position = bundle.positions[pos];
  const seatView = bundle.seat_views[pov]?.[pos];
  if (position === undefined || seatView === undefined) {
    throw new Error(`no position ${pos} for seat ${pov}`);
  }
  const board = position.board;
  const finished = pos === bundle.turns && (mode?.lastIsEnd ?? true);
  const actor = (pos + (Number(ex.options["startingPlayer"] ?? 0))) % n;

  // Stage: the largest 16:9 rectangle that fits.
  const winW = Math.min(root.clientWidth, root.clientHeight * ASPECT);
  const winH = winW / ASPECT;
  root.replaceChildren();
  const stage = el("div", "stage");
  Object.assign(stage.style, { width: px(winW), height: px(winH), backgroundImage: `url(${background})` });
  root.append(stage);

  const place = <T extends HTMLElement>(e: T, b: Box): T => {
    Object.assign(e.style, { left: px(b.x * winW), top: px(b.y * winH), width: px(b.w * winW), height: px(b.h * winH) });
    stage.append(e);
    return e;
  };
  const rect = (b: Box, opacity: number, radius = 0.01 * winW): HTMLElement => {
    const r = place(el("div", "rect"), b);
    Object.assign(r.style, { background: `rgba(0,0,0,${opacity})`, borderRadius: px(radius) });
    return r;
  };
  const label = (text: string, b: Box, fontSize: number, cls = "label"): HTMLElement => {
    const l = place(el("div", cls), b);
    l.textContent = text;
    l.style.fontSize = px(fontSize * winH);
    return l;
  };
  const cardImg = (src: string, b: Box, cls = "card"): HTMLImageElement => {
    const img = place(el("img", cls), b);
    img.src = src;
    img.draggable = false;
    return img;
  };
  /** Width of a card of height `h`, as a fraction of the stage width. */
  const cardW = (h: number): number => (h * winH * CARD_W) / CARD_H / winW;
  /** Where each card is drawn at this position (for the animations). */
  const boxes = new Map<number, Box>();

  // ---- Action log (top left) ----------------------------------------------------------------------
  const actionLog: Box = { x: 0.01, y: 0.01, w: 0.4, h: 0.25 };
  const logRect = rect(actionLog, 0.3, 0.01 * winH);
  logRect.classList.add("clickable");
  logRect.title = "Full log";
  logRect.addEventListener("click", on.showFullLog);
  const lines = bundle.log.slice(0, pos + 1).slice(-8);
  const logText = label("", { x: 0.02, y: 0.013, w: 0.38, h: 0.247 }, 0.028, "label action-log");
  const firstLine = Math.max(0, pos + 1 - 8);
  lines.forEach((line, i) => {
    const row = el("div", "", line);
    const suffix = mode?.logSuffix(firstLine + i);
    if (suffix) row.append(el("span", suffix.cls, suffix.text));
    logText.append(row);
  });

  // ---- Play stacks ---------------------------------------------------------------------------------
  const stackCardW = 0.06;
  const stackCardH = 0.151;
  const stackY = actionLog.y + actionLog.h + 0.02;
  const stackSpacing = (actionLog.w - stackCardW * numSuits) / (numSuits - 1);
  // The card on top of each stack is the one dealt card of that identity that is in no hand and not
  // discarded (only needed to animate it; unknown cards are never on a stack).
  const inHands = new Set(seatView.hands.flatMap((h) => h.slots.map((sl) => sl.card)));
  const discarded = new Set(board.discards);
  const dealt = seatView.cards.length;
  for (let s = 0; s < numSuits; s++) {
    const top = board.stacks[SUIT_LETTERS[s] ?? ""] ?? 0;
    const b = { x: actionLog.x + (stackCardW + stackSpacing) * s, y: stackY, w: stackCardW, h: stackCardH };
    // The card underneath stays visible while the top card flies in.
    cardImg(top <= 1 ? art.stackBase(s) : art.face(s, top - 1), b, top <= 1 ? "card stack-base" : "card");
    if (top > 0) {
      const img = cardImg(art.face(s, top), b);
      const id = ex.deck.findIndex((c, i) => i < dealt && c?.suitIndex === s && c.rank === top && !inHands.has(i) && !discarded.has(i));
      if (id >= 0) track(img, id, b);
    }
  }

  // ---- Replay area ---------------------------------------------------------------------------------
  const replay = { x: 0.01, y: 0.49, w: 0.4 };
  place(el("div", "replay-bar"), { x: replay.x, y: replay.y + 0.0425, w: replay.w, h: 0.01 });
  const barHit = place(el("div", "replay-bar-hit"), { x: replay.x, y: replay.y, w: replay.w, h: 0.07 });
  barHit.addEventListener("click", (e) => {
    const frac = (e.offsetX / barHit.clientWidth) * bundle.turns;
    on.goTo(Math.round(frac));
  });
  const shuttleX = replay.x + (bundle.turns === 0 ? 0 : (pos / bundle.turns) * (replay.w - 0.03));
  const shuttle = place(el("div", "shuttle"), { x: shuttleX, y: replay.y + 0.0475 - 0.015, w: 0.03, h: 0.03 });
  shuttle.style.borderRadius = px(0.01 * winW);
  const buttons: [string, string, number, boolean][] = [
    [replayBackFull, replayBackFullDisabled, 0, pos > 0],
    [replayBack, replayBackDisabled, pos - 1, pos > 0],
    [replayForward, replayForwardDisabled, pos + 1, pos < bundle.turns],
    [replayForwardFull, replayForwardFullDisabled, bundle.turns, pos < bundle.turns],
  ];
  buttons.forEach(([img, disabledImg, target, enabled], i) => {
    const b = place(el("button", "button"), { x: replay.x + 0.05 + 0.08 * i, y: replay.y + 0.07, w: 0.06, h: 0.08 });
    b.style.borderRadius = px(0.004 * winW);
    const icon = el("img", "button-icon");
    icon.src = enabled ? img : disabledImg;
    b.append(icon);
    b.disabled = !enabled;
    b.addEventListener("click", () => on.goTo(target));
  });

  // ---- Inspect / Label controls (where the site has "Enter Hypothetical") ----------------------------
  const controls = place(el("div", "controls"), { x: 0.01, y: 0.655, w: 0.4, h: 0.13 });
  controls.style.fontSize = px((mode ? 0.018 : 0.02) * winH);
  if (mode) {
    mode.controls(controls);
  } else {
    inspectControls();
  }
  function inspectControls(): void {
    const seatRow = el("div", "control-row");
    seatRow.append(el("span", "control-label", "View from"));
    ex.players.forEach((name, seat) => {
      const b = el("button", seat === pov ? "chip active" : "chip", name);
      b.addEventListener("click", () => on.setPov(seat));
      seatRow.append(b);
    });
    const toggles = el("div", "control-row");
    const own = el("button", "chip", showOwn ? "Own cards: shown" : "Own cards: hidden");
    own.title = "Show the top row face-up (as a spectator) or as that player sees it (O)";
    own.addEventListener("click", on.toggleOwn);
    const errors = bundle.checks.filter((c) => !c.ok && c.kind === "error").length;
    const wording = bundle.checks.filter((c) => !c.ok && c.kind === "wording").length;
    const checks = el("button", errors > 0 ? "chip bad" : "chip good",
      errors > 0 ? `✗ ${errors} check errors` : `✓ ${bundle.checks.length - wording} checks` + (wording ? ` · ${wording} wording` : ""));
    checks.addEventListener("click", on.showChecks);
    const json = el("button", "chip", "JSON");
    json.title = "DecisionRecord and seat view for this position (J)";
    json.addEventListener("click", on.showJSON);
    const site = el("a", "chip", "Open on site ↗") as HTMLAnchorElement;
    site.href = `${SITE}/replay/${ex.id}#${pos + 1}`;
    site.target = "_blank";
    site.rel = "noopener";
    toggles.append(own, checks, json, site);
    controls.append(seatRow, toggles);
  }

  // ---- Bottom left: lobby button, deck, score area --------------------------------------------------
  const lobby = place(el("button", "button lobby"), { x: 0.01, y: 0.8 + 2 * 0.0563 + 0.02, w: 0.07, h: 0.0563 });
  lobby.textContent = "Lobby";
  lobby.style.fontSize = px(0.026 * winH);
  lobby.style.borderRadius = px(0.004 * winW);
  lobby.addEventListener("click", on.lobby);

  const deck: Box = { x: 0.09, y: 0.8, w: 0.075, h: 0.189 };
  if (board.deck > 0) {
    cardImg(art.deckBack(), deck);
    label(`ID: ${ex.id}`, { x: deck.x, y: deck.y + 0.012, w: deck.w, h: 0.03 }, 0.018, "label deck-id");
    label(String(board.deck), { x: deck.x, y: deck.y + 0.05, w: deck.w, h: 0.09 }, 0.07, "label deck-count");
  } else {
    rect(deck, 0.2, 0.006 * winW);
  }

  const score: Box = { x: deck.x + deck.w + 0.01, y: 0.81, w: 0.13, h: 0.18 };
  rect(score, 0.2);
  const rows: [string, string, string?][] = [
    ["Turn", String(pos + 1)],
    ["Score", String(board.score), ` / ${position.max_score}`],
    ["Clues", String(board.clues)],
  ];
  rows.forEach(([name, value, small], i) => {
    const y = score.y + [0.01, 0.045, 0.08][i]!;
    label(name, { x: score.x + 0.02, y, w: 0.06, h: 0.03 }, 0.026);
    const v = label(value, { x: score.x + 0.08, y, w: 0.05, h: 0.03 }, 0.026);
    if (name === "Clues" && board.clues === 0) v.style.color = "#df1c2d";
    if (small !== undefined) {
      const s = el("span", "small", small);
      s.style.fontSize = px(0.017 * winH);
      v.append(s);
    }
  });
  const finalStrikes = bundle.positions[bundle.turns]?.board.strikes ?? 0;
  for (let i = 0; i < 3; i++) {
    const sq = rect({ x: score.x + 0.01 + 0.04 * i, y: score.y + 0.115, w: 0.03, h: 0.053 }, 0.5, 0.005 * winW);
    sq.classList.add("strike-square");
    if (i < finalStrikes) {
      // Like the site's replays: strikes that happen later in the game are shown faded.
      const x = cardImg(strikeX, { x: score.x + 0.013 + 0.04 * i, y: score.y + 0.12, w: 0.024, h: 0.043 }, "strike");
      x.style.opacity = i < board.strikes ? "1" : String(STRIKE_FADE);
    }
  }

  // ---- Clue log (top right) and statistics ----------------------------------------------------------
  const clueLog: Box = { x: 0.8, y: 0.01, w: 0.19, h: 0.51 };
  rect(clueLog, 0.2);
  const entryH = 0.018;
  const shown = bundle.clues.filter((c) => c.turn <= pos).slice(-27);
  const entries: { clue: ClueRecord; row: HTMLElement }[] = [];
  shown.forEach((clue, i) => {
    const row = place(el("div", "clue-entry"), { x: clueLog.x + 0.01, y: clueLog.y + 0.01 + entryH * i, w: clueLog.w - 0.02, h: entryH });
    row.style.fontSize = px(0.9 * entryH * winH);
    const value = clue.kind === "color" ? (art.variant.suits[SUIT_LETTERS.indexOf(String(clue.value))]?.name ?? "?") : String(clue.value);
    row.append(el("span", "giver", ex.players[clue.giver] ?? "?"), el("span", "target", ex.players[clue.target] ?? "?"), el("span", "value", value));
    // Like the site: clicking an entry goes to the position just after that clue.
    row.addEventListener("click", () => on.goTo(clue.turn));
    entries.push({ clue, row });
  });
  const highlight = (card: number | null): void => {
    for (const { clue, row } of entries) {
      row.classList.toggle("touched", card !== null && clue.touched.includes(card));
      row.classList.toggle("missed", card !== null && clue.missed.includes(card));
    }
  };

  rect({ x: clueLog.x, y: 0.53, w: clueLog.w, h: 0.09 }, 0.2);
  const variantLabel = label(art.variant.name.replace(/\s*\(\d Suits\)/, ""), { x: 0.825, y: 0.535, w: 0.15, h: 0.028 }, 0.02, "label centered underlined");
  variantLabel.title = ex.options.allOrNothing ? "All or Nothing" : "";
  label("Pace", { x: 0.825, y: 0.567, w: 0.07, h: 0.025 }, 0.02);
  label(board.pace === null ? "-" : board.pace > 0 ? `+${board.pace}` : String(board.pace), { x: 0.9, y: 0.567, w: 0.07, h: 0.025 }, 0.02);
  const eff = label("Efficiency", { x: 0.825, y: 0.59, w: 0.07, h: 0.025 }, 0.02);
  eff.title = "Not computed yet";
  label("–", { x: 0.9, y: 0.59, w: 0.07, h: 0.025 }, 0.02);

  // ---- Discard pile ----------------------------------------------------------------------------------
  rect({ x: 0.8, y: 0.63, w: 0.19, h: 0.36 }, 0.2);
  const can = cardImg(trashcan, { x: 0.82, y: 0.65, w: 0.15, h: 0.33 }, "trashcan");
  can.style.opacity = "0.2";
  const discardSpacing = numSuits === 6 ? 0.038 : 0.047;
  const discardH = 0.15;
  const dW = cardW(discardH);
  for (let s = 0; s < numSuits; s++) {
    const pile = board.discards.filter((c) => ex.deck[c]?.suitIndex === s)
      .sort((a, b) => (ex.deck[a]?.rank ?? 0) - (ex.deck[b]?.rank ?? 0));
    const step = pile.length > 1 ? Math.min(dW * 1.05, (0.17 - dW) / (pile.length - 1)) : 0;
    pile.forEach((c, i) => {
      const card = ex.deck[c];
      if (!card) return;
      const b = { x: 0.81 + step * i, y: 0.64 + discardSpacing * s, w: dW, h: discardH };
      const img = cardImg(art.face(card.suitIndex, card.rank), b);
      track(img, c, b);
      hoverInfo(img, c, null);
    });
  }

  // ---- Hands -----------------------------------------------------------------------------------------
  const handSize = seatView.rules.hand_size;
  const leftX = n >= 4 ? 0.44 : 0.43;
  const rightX = 0.78;
  const topY = n >= 5 ? 0.025 : 0.03;
  const bottomY = 0.96;
  const cardSpacing = handSize <= 4 ? 0.2 : 0.1;
  const handSpacing = 0.45;
  const widthRatio = handSize * (1 + cardSpacing) - cardSpacing;
  const maxCardWidth = (rightX - leftX) / widthRatio;
  const heightRatio = n * (1 + handSpacing) - handSpacing;
  const maxCardHeight = (bottomY - topY) / heightRatio;
  const relativeCardRatio = CARD_W / winW / (CARD_H / winH);
  let hand: Box;
  if (maxCardWidth / maxCardHeight <= relativeCardRatio) {
    hand = { x: leftX, y: topY, w: rightX - leftX, h: maxCardWidth / relativeCardRatio };
  } else {
    const w = maxCardHeight * relativeCardRatio * widthRatio;
    hand = { x: (rightX + leftX - w) / 2, y: topY, w, h: maxCardHeight };
  }
  const cw = hand.h * relativeCardRatio;
  const lastClue = bundle.clues.find((c) => c.turn === pos);
  const cardBoxes = new Map<number, Box>();

  seatView.hands.forEach((h, j) => {
    const seat = (pov + j) % n;
    const y = hand.y + hand.h * (1 + handSpacing) * j;
    const name: Box = { x: hand.x - 0.005, y: y + hand.h + 0.01, w: hand.w + 0.01, h: 0.02 };
    if (seat === actor && !finished) {
      // Whose turn it is: the site's dark box behind the hand, plus a marker and a coloured edge.
      const box = rect({ x: hand.x - 0.008, y: y - 0.012, w: hand.w + 0.016, h: hand.h + 0.012 + 0.045 }, 0.25);
      box.classList.add("turn-box");
      if (mode && seat === pov) box.classList.add("yours");
      const marker = label("▶", { x: hand.x - 0.03, y: y + hand.h / 2 - 0.025, w: 0.02, h: 0.05 }, 0.04, "label turn-marker");
      if (mode && seat === pov) marker.classList.add("yours");
    }
    if (mode?.pulse?.seat === seat) {
      // Redraws continue the pulse where it was rather than starting it again.
      const glow = place(el("div", "mismatch-pulse"), { x: hand.x - 0.008, y: y - 0.012, w: hand.w + 0.016, h: hand.h + 0.012 + 0.045 });
      glow.style.borderRadius = px(0.01 * winW);
      glow.style.animationDelay = `${-mode.pulse.elapsed}ms`;
    }
    // Slot 1 (newest) is on the left; a shorter hand stays centred.
    const used = h.slots.length * cw + Math.max(0, h.slots.length - 1) * cw * cardSpacing;
    const x0 = hand.x + (hand.w - used) / 2;
    h.slots.forEach((slot, i) => {
      const b = { x: x0 + i * cw * (1 + cardSpacing), y, w: cw, h: hand.h };
      const hidden = j === 0 && !showOwn;
      const img = cardImg(hidden ? hiddenImage(art, slot) : faceOf(art, ex.deck, slot.card), b);
      if (slot.touched_t.length > 0) {
        img.classList.add("clued");
        img.style.boxShadow = `0 0 0 ${px(0.0035 * winW)} ${CLUED_COLOR}`;
      }
      img.style.borderRadius = px(0.1 * cw * winW);
      track(img, slot.card, b);
      if (hidden) {
        possibilities(art, slot, b).dataset.anim = String(slot.card);
      }
      hoverInfo(img, slot.card, slot);
      cardBoxes.set(slot.card, b);
      Object.assign(img.dataset, { card: String(slot.card), holder: String(seat) });
      if (mode) {
        img.addEventListener("mousedown", (e) => mode.cardDown(slot.card, seat, e));
      }
      if (lastClue?.touched.includes(slot.card)) {
        arrow(b, clueFill(lastClue), clueText(lastClue));
      }
    });
    nameFrame(ex.players[seat] ?? "?", name, seat === actor && !finished);
  });

  // ---- Label mode: tags on cards, hint arrows ---------------------------------------------------------
  if (mode) {
    stage.addEventListener("contextmenu", (e) => e.preventDefault());
    for (const [card, marks] of mode.marks) {
      // A ghost tag can be on a card that has just gone to the stacks or the discard pile.
      const b = cardBoxes.get(card) ?? boxes.get(card);
      if (b !== undefined) drawMarks(b, marks);
    }
    for (const p of mode.pointers) {
      const b = cardBoxes.get(p.card);
      if (b !== undefined) arrow(b, p.fill, p.text);
    }
  }
  animateMoves();

  // ---- Helpers that need the stage -------------------------------------------------------------------
  function track(e: HTMLElement, card: number, b: Box): void {
    e.dataset.anim = String(card);
    boxes.set(card, b);
  }

  function animateMoves(): void {
    // Like the site: after one step forward or back, every card that changed place slides from where it
    // was (a newly drawn card from the deck) to where it is now. Longer jumps don't animate.
    const key = `${mode?.key ?? `inspect/${ex.id}`}/${pov}`;
    const prev = lastLayout;
    lastLayout = { key, pos, boxes };
    if (prev === null || prev.key !== key || Math.abs(prev.pos - pos) !== 1) return;
    for (const e of stage.querySelectorAll<HTMLElement>("[data-anim]")) {
      const card = Number(e.dataset.anim);
      const to = boxes.get(card);
      const from = prev.boxes.get(card) ?? (pos > prev.pos && inHands.has(card) ? deck : undefined);
      if (to === undefined || from === undefined) continue;
      if (Math.abs(from.x - to.x) < 1e-6 && Math.abs(from.y - to.y) < 1e-6 && Math.abs(from.w - to.w) < 1e-6) continue;
      const dx = (from.x - to.x) * winW;
      const dy = (from.y - to.y) * winH;
      e.style.transformOrigin = "0 0";
      e.style.zIndex = "4";
      e.animate(
        [{ transform: `translate(${dx}px, ${dy}px) scale(${from.w / to.w}, ${from.h / to.h})` }, { transform: "none" }],
        { duration: MOVE_MS, easing: "cubic-bezier(0.25, 0.8, 0.35, 1)" },
      ).finished.then(() => { e.style.zIndex = ""; }, () => {});
    }
  }

  function drawMarks(b: Box, marks: Mark[]): HTMLElement {
    const box = place(el("div", "marks"), { x: b.x - 0.01, y: b.y + b.h * 0.04, w: b.w + 0.02, h: b.h * 0.9 });
    box.style.fontSize = px(0.017 * winH);
    for (const m of marks) box.append(el("div", `mark ${m.cls}`, m.text));
    return box;
  }

  function clueFill(clue: ClueRecord): string {
    return clue.kind === "color" ? art.fill(SUIT_LETTERS.indexOf(String(clue.value))) : "black";
  }

  function clueText(clue: ClueRecord): string {
    return clue.kind === "rank" ? String(clue.value) : "";
  }

  function possibilities(a: CardArt, slot: Slot, b: Box): HTMLElement {
    // What clues say about a hidden card: remaining suits (pips) and ranks, as the site shows them
    // with pip updating off. Only shown when something is ruled out.
    const suits = [...slot.know.suits].map((l) => SUIT_LETTERS.indexOf(l));
    const ranks = slot.know.ranks;
    const box = place(el("div", "possibilities"), b);
    if (suits.length > 1 && suits.length < numSuits) {
      const row = el("div", "pips");
      for (const s of suits) {
        const p = el("img", "pip");
        p.src = a.pip(s);
        row.append(p);
      }
      box.append(row);
    }
    if (ranks.length > 1 && ranks.length < 5) {
      const r = el("div", "ranks", [...ranks].join(" "));
      r.style.fontSize = px(0.22 * b.w * winW);
      box.append(r);
    }
    return box;
  }

  function arrow(b: Box, color: string, text: string): void {
    // A clue arrow like the site's: pointing up at the card, with the clue in a circle below it.
    const w = b.w * winW;
    const svgBox = { x: b.x, y: b.y + b.h - 0.012, w: b.w, h: 0.12 };
    const svg = place(document.createElementNS("http://www.w3.org/2000/svg", "svg") as unknown as HTMLElement, svgBox);
    svg.classList.add("arrow");
    const H = svgBox.h * winH;
    const cx = w / 2;
    const r = w * 0.2;
    const head = w * 0.17;
    svg.innerHTML = `
      <path d="M ${cx} 2 L ${cx - head} ${head * 1.3} L ${cx - head * 0.35} ${head * 1.3} L ${cx - head * 0.35} ${H - 2 * r}
               L ${cx + head * 0.35} ${H - 2 * r} L ${cx + head * 0.35} ${head * 1.3} L ${cx + head} ${head * 1.3} Z"
            fill="white" stroke="black" stroke-width="${w * 0.02}"/>
      <circle cx="${cx}" cy="${H - r - 2}" r="${r}" fill="${color}" stroke="white" stroke-width="${w * 0.025}"/>
      ${text !== "" ? `<text x="${cx}" y="${H - r - 2}" fill="white" font-size="${r * 1.3}" font-family="Verdana"
            text-anchor="middle" dominant-baseline="central">${text}</text>` : ""}`;
  }

  function nameFrame(name: string, b: Box, active: boolean): void {
    const frame = place(el("div", active ? "name-frame active" : "name-frame"), b);
    frame.style.fontSize = px(b.h * winH);
    frame.append(el("div", "bracket left"), el("span", "name", name), el("div", "bracket right"));
  }

  function hoverInfo(img: HTMLElement, card: number, slot: Slot | null): void {
    // Clue log highlight, as on the site. Inspect mode adds a tooltip with the card's details.
    img.addEventListener("mouseenter", () => {
      highlight(card);
      if (!mode) showTooltip(img, cardInfo(ex.deck, card, slot));
    });
    img.addEventListener("mouseleave", () => {
      highlight(null);
      hideTooltip();
    });
  }
}

function faceOf(art: CardArt, deck: ({ suitIndex: number; rank: number } | null)[], card: number): string {
  const c = deck[card];
  return c == null ? art.unknown() : art.face(c.suitIndex, c.rank);
}

function hiddenImage(art: CardArt, slot: Slot): string {
  const suits = slot.know.suits;
  const ranks = slot.know.ranks;
  const s = SUIT_LETTERS.indexOf(suits[0] ?? "");
  const r = Number(ranks[0]);
  if (suits.length === 1 && ranks.length === 1) return art.face(s, r);
  if (suits.length === 1) return art.suitOnly(s);
  if (ranks.length === 1) return art.rankOnly(r);
  return art.unknown();
}

function cardInfo(deck: ({ suitIndex: number; rank: number } | null)[], card: number, slot: Slot | null): string {
  const c = deck[card];
  const id = c == null ? "?" : `${SUIT_LETTERS[c.suitIndex]}${c.rank}`;
  const lines = [`card #${card} · ${id}`];
  if (slot !== null) {
    lines.push(`slot ${slot.slot} · drawn turn ${slot.drawn_t}`);
    lines.push(`touched: ${slot.touched_t.length > 0 ? slot.touched_t.map((t) => `turn ${t}`).join(", ") : "never"}`);
    lines.push(`holder knows: ${slot.know.suits} × ${slot.know.ranks}`);
    if (slot.status !== null && slot.status.length > 0) lines.push(`status: ${slot.status.join(", ")}`);
  }
  return lines.join("\n");
}

// ---- DOM helpers -------------------------------------------------------------------------------------

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls !== "") e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function px(v: number): string {
  return `${v}px`;
}
