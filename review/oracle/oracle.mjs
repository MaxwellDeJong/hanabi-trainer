// Replays exports through hanab.live's own reducer (@hanabi-live/game, built by setup.sh) and prints
// the public state before every turn as JSON lines, one line per game.
//
//   node oracle.mjs ../../examples/export_78921.json [more exports...]
//
// Adapted from hanabi-live packages/client/test/loadGameJSON.ts (GPL-3.0). Differences from it:
// options come from the export (not defaults), player names are real (so log lines match the site),
// no fake "gameOver" is appended, and a snapshot is taken after every export action.
//
// The reducer does not decide when a game ends (hanab.live's server does), so this checks per-turn
// state only, not end conditions.

import { readFileSync } from "node:fs";
import * as g from "../vendor/hanabi-game.mjs";

export const HANABI_LIVE_COMMIT = "c1d970b";

// Export action types (docs/representation.md §3.2).
const PLAY = 0, DISCARD = 1, COLOR_CLUE = 2, RANK_CLUE = 3, GAME_OVER = 4, VOTE_END = 5;

function reduce(state, action, metadata) {
  return g.gameReducer(state, action, false, false, false, false, metadata);
}

function makeMetadata(ex) {
  const n = ex.players.length;
  const base = g.getDefaultMetadata(n, ex.options?.variant ?? "No Variant");
  // speedrun only changes when the server ends the game (SpeedrunFail), which the reducer doesn't decide.
  const known = new Set(["variant", "timed", "timeBase", "timePerTurn", "allOrNothing", "speedrun", "startingPlayer"]);
  const unknown = Object.keys(ex.options ?? {}).filter((k) => !known.has(k));
  if (unknown.length > 0) {
    // Options such as oneExtraCard change hand sizes; fail rather than replay them wrongly.
    throw new Error(`unsupported options: ${unknown.join(", ")}`);
  }
  return {
    ...base,
    playerNames: ex.players,
    options: {
      ...base.options,
      allOrNothing: ex.options?.allOrNothing ?? false,
      speedrun: ex.options?.speedrun ?? false,
      startingPlayer: ex.options?.startingPlayer ?? 0,
    },
  };
}

function drawAction(playerIndex, order, deck) {
  const { suitIndex, rank } = deck[order];
  return { type: "draw", playerIndex, order, suitIndex, rank };
}

function snapshot(state, ex, turn) {
  const deck = ex.deck;
  return {
    turn, // 1-based, as in the UI: the number of export actions already applied + 1
    score: state.score,
    clues: state.clueTokens,
    strikes: state.strikes.length,
    deck: state.cardsRemainingInTheDeck,
    pace: state.stats.pace,
    max_score: state.stats.maxScore,
    // Top rank per suit.
    stacks: state.playStacks.map((stack) => (stack.length === 0 ? 0 : deck[stack.at(-1)].rank)),
    // Card orders per suit (the reducer does not keep a chronological discard list).
    discard_piles: state.discardStacks.map((pile) => [...pile]),
    // The reducer appends drawn cards, so reverse to get UI slot order (slot 1 = newest).
    hands: state.hands.map((hand) => [...hand].reverse()),
    clue_log: state.clues.map((c) => ({
      turn: c.segment + 1,
      giver: c.giver,
      target: c.target,
      kind: c.type === g.ClueType.Color ? "color" : "rank",
      value: c.value,
      touched: [...c.list],
      missed: [...c.negativeList],
    })),
    log: state.log.map((entry) => entry.text),
  };
}

export function replayExport(ex) {
  const metadata = makeMetadata(ex);
  const variant = g.getVariant(metadata.options.variantName);
  const n = ex.players.length;
  const deck = ex.deck;
  let state = g.getInitialGameState(metadata);

  // Initial deal, in deck order.
  const cardsPerHand = g.getCardsPerHand(metadata.options);
  let topOfDeck = 0;
  for (let p = 0; p < n; p++) {
    for (let i = 0; i < cardsPerHand; i++) {
      state = reduce(state, drawAction(p, topOfDeck++, deck), metadata);
    }
  }

  const positions = [snapshot(state, ex, 1)];
  let player = metadata.options.startingPlayer;

  ex.actions.forEach((a, k) => {
    switch (a.type) {
      case PLAY:
      case DISCARD: {
        const { suitIndex, rank } = deck[a.target];
        let action = { type: a.type === PLAY ? "play" : "discard", playerIndex: player, order: a.target, suitIndex, rank };
        if (a.type === PLAY) {
          const playable = g.getNextPlayableRanks(
            suitIndex, state.playStacks[suitIndex], state.playStackDirections[suitIndex],
            state.playStackStarts, variant, state.deck,
          );
          if (!playable.includes(rank)) {
            // A misplay is a failed discard followed by a strike, as the server sends it.
            state = reduce(state, { ...action, type: "discard", failed: true }, metadata);
            action = { type: "strike", num: state.strikes.length + 1, order: a.target, turn: k };
          }
        }
        state = reduce(state, action, metadata);
        if (topOfDeck < deck.length) {
          state = reduce(state, drawAction(player, topOfDeck++, deck), metadata);
        }
        break;
      }

      case COLOR_CLUE:
      case RANK_CLUE: {
        const msgClue = { type: a.type === COLOR_CLUE ? g.ClueType.Color : g.ClueType.Rank, value: a.value };
        const clue = g.msgClueToClue(msgClue, variant);
        const list = state.hands[a.target].filter((order) =>
          g.isCardTouchedByClue(variant, clue, deck[order].suitIndex, deck[order].rank));
        state = reduce(state, {
          type: "clue", clue: msgClue, giver: player, target: a.target, turn: k, list, ignoreNegative: false,
        }, metadata);
        break;
      }

      case GAME_OVER:
      case VOTE_END:
        // Not a turn: nobody moves, and the engine's positions stop before it (the reducer would only
        // add "… terminated the game!" and set the recorded score to 0). So no snapshot.
        return;

      default:
        throw new Error(`unknown action type ${a.type} at index ${k}`);
    }
    positions.push(snapshot(state, ex, k + 2));
    player = (player + 1) % n;
  });

  return { game_id: ex.id, oracle: `hanabi-live@${HANABI_LIVE_COMMIT}`, players: ex.players, positions };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  for (const path of process.argv.slice(2)) {
    const doc = JSON.parse(readFileSync(path, "utf8"));
    const ex = doc.export ?? doc; // accept a GameRecord (docs/representation.md §6) or a raw export
    process.stdout.write(JSON.stringify(replayExport(ex)) + "\n");
  }
}
