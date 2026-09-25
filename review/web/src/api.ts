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
  /** The site's sound for each position (the action that led to it); null: the standard turn sound. */
  sounds?: (string | null)[];
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
    /** Set once the labeller hands the session in; it is read-only from then on. */
    submitted: string | null;
    game_id: number;
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

export type SessionStatus = "active" | "submitted";

export interface SessionSummary {
  session_id: string;
  game_id: number;
  labeller: string;
  status: SessionStatus;
  started: string;
  /** An active session expires then (24 hours after it started): its moves are dropped and the seat opens again. */
  expires: string;
  submitted: string | null;
  last_active: string;
  /** Played to the end (ready to submit, if still active). */
  game_over: boolean;
  players: number;
  variant: string;
  you: string;
  turn: number;
  labels: number;
  own_turns: number;
}

/** A game on the lobby board. */
export interface BoardGame {
  game_id: number;
  players: number;
  variant: string;
  seats: {
    name: string;
    status: SessionStatus | "open";
    labellers: string[];
    /** Your session on this seat, if any. */
    session_id: string | null;
    /** Open: nobody has an active or submitted session on it. */
    available: boolean;
  }[];
}

export interface AdminSession extends SessionSummary {
  seat: number;
  chosen_by: string | null;
  turns: number;
  hints: number;
  hint_labels: number;
  after_reveal: number;
  median_ms: number | null;
}

export interface AdminReport {
  generated: string;
  totals: {
    games: number;
    labelable_games: number;
    seats: number;
    open: number;
    active: number;
    submitted: number;
    own_turns: number;
    own_turns_submitted: number;
    labellers: number;
    labels: number;
    sessions_active: number;
    sessions_submitted: number;
  };
  labellers: {
    labeller: string;
    active: number;
    submitted: number;
    labels: number;
    labels_submitted: number;
    hint_labels: number;
    after_reveal: number;
    median_ms: number | null;
    first: string;
    last_active: string;
  }[];
  sessions: AdminSession[];
  games: {
    id: number;
    players: string[];
    variant: string;
    turns: number;
    errors: number;
    seats: {
      seat: number;
      player: string;
      anon: string;
      status: SessionStatus | "open";
      own_turns: number;
      sessions: Pick<SessionSummary, "session_id" | "labeller" | "status" | "turn" | "labels" | "last_active">[];
    }[];
  }[];
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
export const getBoard = (labeller: string): Promise<BoardGame[]> =>
  call(`/api/board?labeller=${encodeURIComponent(labeller)}`);
/** A random open seat, or the given one from the board. */
export const startSession = (labeller: string, pick?: { game_id: number; seat: number }): Promise<LabelView> =>
  call("/api/sessions", { labeller, ...pick });
export const getAdmin = (): Promise<AdminReport> => call("/api/admin");
export const getSession = (sid: string): Promise<LabelView> => call(`/api/sessions/${sid}`);
export const sessionAction = (sid: string, action: string, body: Record<string, unknown> = {}): Promise<LabelView> =>
  call(`/api/sessions/${sid}/${action}`, body);
