// Types for the game bundle (docs/review-tool.md §6.1) and the calls that fetch it.

export interface ExportCard {
  suitIndex: number;
  rank: number;
}

export interface ExportAction {
  type: number;
  target: number;
  value?: number;
}

export interface Export {
  id: number;
  players: string[];
  /** Label mode: null for cards the seat hasn't seen. */
  deck: (ExportCard | null)[];
  actions: ExportAction[];
  options: Record<string, unknown> & { variant?: string };
  seed?: string;
}

export interface Board {
  turn: number;
  score: number;
  clues: number;
  strikes: number;
  deck: number;
  stacks: Record<string, number>;
  discards: number[];
  pace: number | null;
  gone: string[];
}

export interface Know {
  suits: string;
  ranks: string;
}

export interface Slot {
  slot: number;
  card: number;
  id: string | null;
  status: string[] | null;
  drawn_t: number;
  touched_t: number[];
  know: Know;
}

export interface Obs {
  rules: { players: number; suits: number; hand_size: number; all_or_nothing: boolean; max_score: number };
  board: Board;
  cards: (string | null)[];
  hands: { seat: number; slots: Slot[] }[];
  history: Record<string, unknown>[];
  unseen: Record<string, number[]>;
  legal: Record<string, unknown>[];
}

export interface Decision {
  schema: string;
  key: { server: string; game_id: number; turn: number };
  obs: Obs;
  label: Record<string, unknown> | null;
  private: { own_hand: string[] };
  meta: Record<string, unknown>;
}

export interface ClueRecord {
  turn: number;
  giver: number;
  target: number;
  kind: "color" | "rank";
  value: string | number;
  touched: number[];
  missed: number[];
}

export interface Check {
  turn: number | null;
  check: string;
  ok: boolean;
  kind: "error" | "wording";
  detail?: string;
}

export interface Bundle {
  schema: string;
  engine: string;
  game: { export: Export; listing?: Record<string, unknown>; source?: Record<string, unknown> };
  turns: number;
  log: string[];
  clues: ClueRecord[];
  positions: { turn: number; board: Board; max_score: number; hands: number[][] }[];
  seat_views: Obs[][];
  decisions: Decision[];
  checks: Check[];
}

export interface GameSummary {
  id: number;
  players: string[];
  variant: string;
  turns: number;
  final_score: number;
  errors: number;
  wording: number;
}

// ---- Label mode (docs/review-tool.md §4, §8). Turns are UI turns (1-based). ----------------------------

/** A move as a label records it: absolute seats and permanent card IDs. */
export type Choice =
  | { type: "play" | "discard"; card: number }
  | { type: "clue"; to_seat: number; kind: "color" | "rank"; value: string | number };

export interface LabelInfo {
  choice: Choice;
  also_ok: Choice[];
  hint_used: boolean;
  after_reveal: boolean;
}

/** What the server sends in Label mode: the bundle cut off at the frontier and anonymised. */
export interface LabelView {
  session: {
    session_id: string;
    seat: number;
    labeller: string;
    /** The furthest turn revealed so far. */
    frontier: number;
    ended: string | null;
    hints: number[];
  };
  game_over: boolean;
  own_turns: number[];
  labels: Record<string, LabelInfo>;
  /** The real move at your turns that are already public or were revealed with the hint. */
  actual: Record<string, Choice>;
  bundle: Bundle;
  /** After an undo: the turn whose label was removed. */
  undone_turn?: number;
}

export interface SessionSummary {
  session_id: string;
  labeller: string;
  started: string;
  ended: string | null;
  players: number;
  variant: string;
  you: string;
  turn: number;
  labels: number;
}

async function call<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { error?: string } | null;
    throw new Error(error?.error ?? `${url}: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const listGames = (): Promise<GameSummary[]> => call("/api/games");
export const getInspectBundle = (id: number): Promise<Bundle> => call(`/api/games/${id}/inspect`);

export const listSessions = (labeller: string): Promise<SessionSummary[]> =>
  call(`/api/sessions?labeller=${encodeURIComponent(labeller)}`);
export const startSession = (labeller: string): Promise<LabelView> => call("/api/sessions", { labeller });
export const getSession = (sid: string): Promise<LabelView> => call(`/api/sessions/${sid}`);
export const sessionAction = (sid: string, action: string, body: Record<string, unknown> = {}): Promise<LabelView> =>
  call(`/api/sessions/${sid}/${action}`, body);
