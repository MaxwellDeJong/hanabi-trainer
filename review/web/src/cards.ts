// Card images, drawn by hanab.live's own card-drawing code (GPL-3.0, pinned in review/setup.sh).

import { getVariant } from "@hanabi-live/game";
import type { Suit, Variant } from "@hanabi-live/game";
import { CARD_H, CARD_W } from "hanabi-live-ui/constants";
import { drawCards } from "hanabi-live-ui/drawCards";
import { cardRendererBackend } from "hanabi-live-ui/drawCardsBrowser";
import { drawPip } from "hanabi-live-ui/drawPip";

export { CARD_H, CARD_W };

export interface CardArt {
  variant: Variant;
  /** Card face for a known identity. */
  face(suitIndex: number, rank: number): string;
  /** Known suit, unknown rank. */
  suitOnly(suitIndex: number): string;
  /** Known rank, unknown suit (white card). */
  rankOnly(rank: number): string;
  /** Completely unknown card (gray). */
  unknown(): string;
  stackBase(suitIndex: number): string;
  deckBack(): string;
  /** A small image of a suit's pip (for the possibilities overlay on hidden cards). */
  pip(suitIndex: number): string;
  /** The suit's fill colour (for clue arrows). */
  fill(suitIndex: number): string;
}

const cache = new Map<string, CardArt>();

export function cardArt(variantName: string): CardArt {
  const cached = cache.get(variantName);
  if (cached !== undefined) {
    return cached;
  }

  const variant = getVariant(variantName);
  // Same settings as screenshots 1–3: colourblind mode off, stylized numbers off, shadows on.
  const canvases = drawCards(variant, false, false, true, cardRendererBackend);
  const urls = new Map<string, string>();
  const url = (name: string): string => {
    let u = urls.get(name);
    if (u === undefined) {
      const canvas = canvases.get(name);
      if (canvas === undefined) {
        throw new Error(`no card image named ${name}`);
      }
      u = canvas.toDataURL();
      urls.set(name, u);
    }
    return u;
  };
  const suit = (i: number): Suit => {
    const s = variant.suits[i];
    if (s === undefined) {
      throw new Error(`no suit ${i} in ${variant.name}`);
    }
    return s;
  };
  const pips = new Map<number, string>();

  const art: CardArt = {
    variant,
    face: (s, r) => url(`card-${suit(s).name}-${r}`),
    suitOnly: (s) => url(`card-${suit(s).name}-UnknownRank`),
    rankOnly: (r) => url(`card-Unknown-${r}`),
    unknown: () => url("card-Unknown-UnknownRank"),
    stackBase: (s) => url(`card-${suit(s).name}-StackBase`),
    deckBack: () => url("deck-back"),
    pip: (s) => {
      let u = pips.get(s);
      if (u === undefined) {
        const { cvs, ctx } = cardRendererBackend.createCanvas();
        cvs.width = 240;
        cvs.height = 240;
        ctx.translate(120, 120);
        ctx.scale(0.9, 0.9);
        drawPip(ctx, suit(s), variant, true);
        u = cvs.toDataURL();
        pips.set(s, u);
      }
      return u;
    },
    fill: (s) => suit(s).fill,
  };
  cache.set(variantName, art);
  return art;
}
