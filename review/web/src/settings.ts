// Label mode settings, per labeller and kept in this browser (like the labeller's name).

const KEY = "review.advance";

export interface AdvanceSetting {
  /** Reveal other players' moves on a timer instead of with Space / →. */
  auto: boolean;
  /** Seconds each other player's turn stays on screen before the next move is revealed. */
  seconds: number;
}

export const MIN_SECONDS = 0.5;
export const MAX_SECONDS = 60;
const DEFAULT: AdvanceSetting = { auto: false, seconds: 2 };

export function clampSeconds(v: number): number {
  return Number.isFinite(v) ? Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, v)) : DEFAULT.seconds;
}

/** One step of the − / + buttons: 0.5 s up to 3 s, then 1 s up to 10 s, then 5 s. */
export function stepSeconds(v: number, dir: 1 | -1): number {
  const size = (x: number): number => (x < 3 ? 0.5 : x < 10 ? 1 : 5);
  // Going down, the step is the one just below v (so 10 → 9, not 10 → 5).
  const step = size(dir > 0 ? v : v - 0.01);
  const next = dir > 0 ? Math.floor(v / step + 1e-9) * step + step : Math.ceil(v / step - 1e-9) * step - step;
  return clampSeconds(next);
}

let current: AdvanceSetting | null = null;

export function getAdvance(): AdvanceSetting {
  if (current === null) {
    try {
      const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<AdvanceSetting> | null;
      current = { auto: raw?.auto === true, seconds: clampSeconds(Number(raw?.seconds ?? DEFAULT.seconds)) };
    } catch {
      current = { ...DEFAULT };
    }
  }
  return current;
}

export function setAdvance(changes: Partial<AdvanceSetting>): AdvanceSetting {
  current = { ...getAdvance(), ...changes };
  current.seconds = clampSeconds(current.seconds);
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    // Not remembered after a reload; it still applies until then.
  }
  return current;
}
