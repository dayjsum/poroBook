const USER_KEY = "porobook_user_id";

export function getUserId(): string | null {
  return localStorage.getItem(USER_KEY);
}

export function setUserId(id: string) {
  localStorage.setItem(USER_KEY, id);
}

export function clearUserId() {
  localStorage.removeItem(USER_KEY);
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const uid = getUserId();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (uid) headers["X-User-Id"] = uid;
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      detail = JSON.parse(text).detail ?? text;
    } catch {
      /* ignore */
    }
    throw new Error(detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

export type PredictionRecord = {
  id: string;
  game_id: string;
  prediction: string;
  status: string;
  created_at: string;
};

export type User = {
  id: string;
  username: string;
  prediction_score: { correct: number; resolved: number };
  accuracy_pct: number | null;
  leaderboard_eligible: boolean;
  leaderboard_rank: number | null;
  leaderboard_min_resolved: number;
  predictions: PredictionRecord[];
};

export type LeaderboardEntry = {
  rank: number;
  user_id: string;
  username: string;
  correct: number;
  resolved: number;
  accuracy_pct: number;
};

export type LobbyMatch = {
  game_id: string;
  queue: string;
  rank_bracket: string;
  game_length_seconds: number;
  game_start_time?: number;
  map_id?: number;
  blue: { champions: { championId: number; iconUrl: string }[] };
  red: { champions: { championId: number; iconUrl: string }[] };
  live_stats?: {
    blue: Record<string, number>;
    red: Record<string, number>;
    source?: string;
  } | null;
  live_stats_note?: string | null;
};

export async function registerUser(username: string) {
  clearUserId();
  const u = await api<{ id: string; username: string }>("/api/users", {
    method: "POST",
    body: JSON.stringify({ username }),
  });
  setUserId(u.id);
  return u;
}

export async function fetchMe(): Promise<User> {
  try {
    return await api<User>("/api/me");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("User not found")) {
      clearUserId();
    }
    throw e;
  }
}

export type LobbyResponse = {
  matches: LobbyMatch[];
  notice?: string | null;
  harvested_at?: string | null;
  harvester_status?: string;
  quota_hit?: boolean;
};

export async function fetchLobby(): Promise<LobbyResponse> {
  return api("/api/lobby");
}

export async function fetchLeaderboard(limit?: number): Promise<{
  min_resolved: number;
  entries: LeaderboardEntry[];
}> {
  const q = limit != null ? `?limit=${encodeURIComponent(String(limit))}` : "";
  return api(`/api/leaderboard${q}`);
}

export type LiveStatsBlock = {
  blue: Record<string, number>;
  red: Record<string, number>;
  source?: string;
} | null;

export type WarRoom = {
  game_id: string;
  queue: string;
  rank_bracket: string;
  game_length_seconds: number;
  game_start_time?: number;
  blue: { champions: { championId: number; iconUrl: string }[] };
  red: { champions: { championId: number; iconUrl: string }[] };
  phase: "ANALYSIS" | "LOCKOUT" | "LOCKED";
  live_stats?: LiveStatsBlock;
  spectator_delay_note: string;
  live_stats_note?: string | null;
};

export async function fetchWarRoom(gameId: string): Promise<WarRoom> {
  return api(`/api/matches/${encodeURIComponent(gameId)}/war-room`);
}

export async function placePrediction(gameId: string, prediction: "BLUE" | "RED") {
  return api<{ prediction: unknown; prediction_score: { correct: number; resolved: number } }>(
    "/api/predictions",
    {
      method: "POST",
      body: JSON.stringify({ game_id: gameId, prediction }),
    },
  );
}

export async function fetchPublicConfig() {
  return api<{
    war_room_cutoff_seconds: number;
    leaderboard_min_resolved: number;
    leaderboard_default_limit: number;
    discovery_mode: string;
  }>("/api/config/public");
}
