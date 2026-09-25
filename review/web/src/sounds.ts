// Sound effects in Label mode, as on the site (hanab.live's sounds.ts and soundView.ts at c1d970b): one
// sound each time a new move is revealed. Which one is worked out by the server (bundle.py
// sound_effects); `null` there means the standard sound, turn-us or turn-other. The mp3 files are
// hanab.live's, unmodified, from vendor/ (review/setup.sh). GPL-3.0.

import finishedFail from "hanabi-live-sounds/finished-fail.mp3";
import finishedPerfect from "hanabi-live-sounds/finished-perfect.mp3";
import finishedSuccess from "hanabi-live-sounds/finished-success.mp3";
import turnBlind1 from "hanabi-live-sounds/turn-blind1.mp3";
import turnBlind2 from "hanabi-live-sounds/turn-blind2.mp3";
import turnBlind3 from "hanabi-live-sounds/turn-blind3.mp3";
import turnBlind4 from "hanabi-live-sounds/turn-blind4.mp3";
import turnBlind5 from "hanabi-live-sounds/turn-blind5.mp3";
import turnBlind6 from "hanabi-live-sounds/turn-blind6.mp3";
import turnFail1 from "hanabi-live-sounds/turn-fail1.mp3";
import turnFail2 from "hanabi-live-sounds/turn-fail2.mp3";
import turnOther from "hanabi-live-sounds/turn-other.mp3";
import turnSad from "hanabi-live-sounds/turn-sad.mp3";
import turnUs from "hanabi-live-sounds/turn-us.mp3";

const FILES: Record<string, string> = {
  "finished-fail": finishedFail,
  "finished-perfect": finishedPerfect,
  "finished-success": finishedSuccess,
  "turn-blind1": turnBlind1,
  "turn-blind2": turnBlind2,
  "turn-blind3": turnBlind3,
  "turn-blind4": turnBlind4,
  "turn-blind5": turnBlind5,
  "turn-blind6": turnBlind6,
  "turn-fail1": turnFail1,
  "turn-fail2": turnFail2,
  "turn-other": turnOther,
  "turn-sad": turnSad,
  "turn-us": turnUs,
};

/** The site's default volume (its setting is 0–100). */
const VOLUME = 0.5;

/** `serve.py --mute` marks the page so automated UI checks don't play every move over the speakers. */
const MUTED = document.documentElement.hasAttribute("data-mute");

// Preload the common ones, as the site does.
if (!MUTED) for (const file of [turnUs, turnOther, turnBlind1, turnFail1]) new Audio(file).load();

/**
 * Play the sound for a newly revealed position. `sound` is the server's choice for it; `ourTurn`
 * picks the standard sound when there is no special one.
 */
export function playTurnSound(sound: string | null | undefined, ourTurn: boolean): void {
  const file = FILES[sound ?? (ourTurn ? "turn-us" : "turn-other")];
  if (MUTED || file === undefined) return;
  const audio = new Audio(file);
  audio.volume = VOLUME;
  audio.play().catch(() => {
    // Browsers block audio until the page has had a click or key press; nothing to do.
  });
}
