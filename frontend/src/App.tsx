import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  LeaderboardEntry,
  LobbyMatch,
  User,
  WarRoom,
  fetchLeaderboard,
  fetchLobby,
  fetchMe,
  fetchPublicConfig,
  fetchWarRoom,
  clearUserId,
  getUserId,
  placePrediction,
  registerUser,
} from "./api";

function formatClock(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function useDocumentTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-gradient-to-b from-poro-ink via-poro-mist to-poro-ink">
      <div className="mx-auto max-w-6xl px-4 py-8">{children}</div>
    </div>
  );
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-lg px-3 py-2 text-sm font-medium transition ${
    isActive ? "bg-poro-gold/20 text-poro-gold" : "text-slate-400 hover:text-white"
  }`;

function NavBar() {
  return (
    <header className="mb-8 border-b border-white/10 pb-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-slate-500">League simulation</p>
          <Link to="/" className="font-display text-2xl font-semibold text-white hover:text-poro-gold">
            PoroBook
          </Link>
        </div>
        <nav className="flex flex-wrap gap-2">
          <NavLink to="/" end className={navClass}>
            Account
          </NavLink>
          <NavLink to="/leaderboard" className={navClass}>
            Leaderboard
          </NavLink>
          <NavLink to="/games" className={navClass}>
            Games
          </NavLink>
        </nav>
      </div>
    </header>
  );
}

function AppLayout() {
  return (
    <Shell>
      <NavBar />
      <Outlet />
    </Shell>
  );
}

function AccountPage() {
  useDocumentTitle("PoroBook · Account");
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const boot = async () => {
      try {
        if (getUserId()) {
          setUser(await fetchMe());
        }
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    };
    void boot();
  }, []);

  const onRegister = async () => {
    try {
      setError(null);
      await registerUser(username.trim());
      setUser(await fetchMe());
      setUsername("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Registration failed");
    }
  };

  const onSignOut = () => {
    clearUserId();
    setUser(null);
  };

  if (loading) {
    return <p className="text-slate-400">Loading…</p>;
  }

  return (
    <>
      <div className="mb-8">
        <h1 className="font-display text-3xl font-semibold text-white">Account</h1>
        <p className="mt-2 max-w-xl text-slate-400">
          Create a display name to save predictions and appear on the leaderboard. No Riot login required here.
        </p>
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          {error}
        </div>
      )}

      <div className="mx-auto max-w-md">
        <div className="glass rounded-2xl p-6">
          {!user ? (
            <div className="space-y-4">
              <label className="block text-sm text-slate-300">Display name</label>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none ring-poro-gold/40 focus:ring"
                placeholder="e.g. LuckyPoro"
              />
              <button
                type="button"
                onClick={() => void onRegister()}
                className="w-full rounded-lg bg-poro-gold px-3 py-2 text-sm font-semibold text-poro-ink transition hover:brightness-110"
              >
                Create account
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs uppercase text-slate-500">Signed in as</p>
                  <p className="text-lg font-semibold text-white">{user.username}</p>
                </div>
                <button
                  type="button"
                  onClick={onSignOut}
                  className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-slate-300 hover:border-white/30"
                >
                  Sign out
                </button>
              </div>
              <div className="rounded-xl border border-white/10 bg-black/20 p-4">
                <p className="text-xs uppercase text-slate-500">Prediction record</p>
                <p className="mt-1 text-2xl font-semibold text-poro-gold">
                  {user.prediction_score.correct}/{user.prediction_score.resolved}
                </p>
                <p className="text-xs text-slate-500">correct · resolved</p>
                {user.prediction_score.resolved > 0 && user.accuracy_pct != null && (
                  <p className="mt-2 text-sm text-slate-300">{user.accuracy_pct}% accuracy</p>
                )}
                {user.leaderboard_eligible && user.leaderboard_rank != null ? (
                  <p className="mt-2 text-xs text-poro-gold">Standings rank #{user.leaderboard_rank}</p>
                ) : (
                  <p className="mt-2 text-xs text-slate-500">
                    {Math.max(0, user.leaderboard_min_resolved - user.prediction_score.resolved)} more scored picks to
                    join the board (min {user.leaderboard_min_resolved})
                  </p>
                )}
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <button
                  type="button"
                  onClick={() => nav("/games")}
                  className="flex-1 rounded-lg bg-poro-gold px-3 py-2 text-sm font-semibold text-poro-ink hover:brightness-110"
                >
                  Go to games
                </button>
                <button
                  type="button"
                  onClick={() => nav("/leaderboard")}
                  className="flex-1 rounded-lg border border-white/15 px-3 py-2 text-sm text-white hover:border-poro-gold/50"
                >
                  View leaderboard
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function LeaderboardPage() {
  useDocumentTitle("PoroBook · Leaderboard");
  const [user, setUser] = useState<User | null>(null);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [lbMinResolved, setLbMinResolved] = useState(10);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadUser = async () => {
      if (!getUserId()) return;
      try {
        setUser(await fetchMe());
      } catch {
        setUser(null);
      }
    };
    void loadUser();
  }, []);

  const refreshLeaderboard = async () => {
    try {
      const lb = await fetchLeaderboard(50);
      setLeaderboard(lb.entries);
      setLbMinResolved(lb.min_resolved);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refreshLeaderboard();
    const id = window.setInterval(() => void refreshLeaderboard(), 15000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-3xl font-semibold text-white">Leaderboard</h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-400">
            Ranked by accuracy (higher first), then more resolved picks, then more correct. At least{" "}
            <span className="text-poro-gold">{lbMinResolved}</span> scored predictions required to appear.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refreshLeaderboard()}
          className="text-sm text-slate-400 hover:text-white"
        >
          Refresh
        </button>
      </div>

      {loading ? (
        <p className="text-slate-400">Loading standings…</p>
      ) : (
        <div className="glass overflow-x-auto rounded-2xl">
          {leaderboard.length === 0 ? (
            <p className="p-5 text-sm text-slate-500">
              No players on the board yet. Lock predictions from Games and wait for matches to finish.
            </p>
          ) : (
            <table className="w-full min-w-[28rem] text-left text-sm text-slate-300">
              <thead className="border-b border-white/10 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">#</th>
                  <th className="px-4 py-3 font-medium">Player</th>
                  <th className="px-4 py-3 font-medium text-right">Accuracy</th>
                  <th className="px-4 py-3 font-medium text-right">Correct</th>
                  <th className="px-4 py-3 font-medium text-right">Resolved</th>
                </tr>
              </thead>
              <tbody>
                {leaderboard.map((row) => (
                  <tr
                    key={row.user_id}
                    className={
                      user?.id === row.user_id
                        ? "border-t border-poro-gold/40 bg-poro-gold/10"
                        : "border-t border-white/5"
                    }
                  >
                    <td className="px-4 py-3 font-mono text-white">{row.rank}</td>
                    <td className="px-4 py-3 font-medium text-white">{row.username}</td>
                    <td className="px-4 py-3 text-right text-poro-gold">{row.accuracy_pct}%</td>
                    <td className="px-4 py-3 text-right">{row.correct}</td>
                    <td className="px-4 py-3 text-right text-slate-400">{row.resolved}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}

function GamesLobbyPage() {
  useDocumentTitle("PoroBook · Games");
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [lobby, setLobby] = useState<LobbyMatch[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [discoveryMode, setDiscoveryMode] = useState<string>("featured");

  useEffect(() => {
    void fetchPublicConfig()
      .then((c) => setDiscoveryMode(c.discovery_mode ?? "featured"))
      .catch(() => null);
  }, []);

  useEffect(() => {
    const boot = async () => {
      try {
        if (getUserId()) {
          setUser(await fetchMe());
        }
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    };
    void boot();
  }, []);

  const refreshLobby = async () => {
    try {
      setError(null);
      const data = await fetchLobby();
      setLobby(data.matches);
      setNotice(data.notice ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lobby fetch failed");
    }
  };

  useEffect(() => {
    void refreshLobby();
    const id = window.setInterval(() => void refreshLobby(), 15000);
    return () => window.clearInterval(id);
  }, []);

  if (loading) {
    return <p className="text-slate-400">Loading…</p>;
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-semibold text-white">Games</h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-400">
          {discoveryMode === "seeders"
            ? "Live matches discovered only through your configured seeder accounts (any rank they play)."
            : discoveryMode === "both"
              ? "Live matches from Riot’s featured spectator list plus your seeders — order shuffles each refresh."
              : "Live matches from Riot’s featured spectator list (mixed skill levels on your shard). Order shuffles each refresh. Open a match, read the delayed clock and comps, then lock a prediction after the analysis window."}
        </p>
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          {error}
        </div>
      )}

      {notice && (
        <div className="mb-6 rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-50">
          {notice}
        </div>
      )}

      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-display text-lg text-white">Live lobbies</h2>
        <button type="button" onClick={() => void refreshLobby()} className="text-sm text-slate-400 hover:text-white">
          Refresh
        </button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {lobby.length === 0 && !notice && (
          <p className="text-slate-500">
            {discoveryMode === "seeders"
              ? "No live games from your seeders right now — only a few are in queue at any moment. Try POROBOOK_DISCOVERY_MODE=featured or both, or try again at peak hours."
              : discoveryMode === "both"
                ? "No live games from featured games or seeders this refresh. Try again in a minute or check that spectator-v4 (featured) is enabled on your API key."
                : "No featured games returned this refresh. Riot’s list can be empty at quiet times; try again later or enable spectator-v4 on your developer key."}
          </p>
        )}
        {lobby.map((m) => (
          <article key={m.game_id} className="glass rounded-2xl p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">Match</p>
                <p className="font-mono text-sm text-slate-300">{m.game_id}</p>
              </div>
              <div className="text-right">
                <p className="text-xs uppercase text-slate-500">Clock (delayed)</p>
                <p className="text-lg font-semibold text-white">{formatClock(m.game_length_seconds)}</p>
              </div>
            </div>
            <p className="mt-3 text-sm text-slate-300">{m.queue}</p>
            <p className="text-xs text-slate-500">{m.rank_bracket}</p>
            <div className="mt-4 flex items-center justify-between gap-2">
              <TeamStrip side="Blue" champions={m.blue.champions} />
              <span className="text-xs text-slate-500">vs</span>
              <TeamStrip side="Red" champions={m.red.champions} />
            </div>
            <button
              type="button"
              disabled={!user}
              onClick={() => nav(`/games/war/${encodeURIComponent(m.game_id)}`)}
              className="mt-4 w-full rounded-lg bg-white/10 px-3 py-2 text-sm font-semibold text-white transition enabled:hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {user ? "Enter war room" : "Create an account first"}
            </button>
            {!user && (
              <p className="mt-2 text-center text-xs text-slate-500">
                <Link to="/" className="text-poro-gold hover:underline">
                  Account
                </Link>{" "}
                to sign in
              </p>
            )}
          </article>
        ))}
      </div>
    </>
  );
}

function TeamStrip({
  side,
  champions,
}: {
  side: "Blue" | "Red";
  champions: { championId: number; iconUrl: string }[];
}) {
  const color = side === "Blue" ? "text-poro-blue" : "text-poro-red";
  return (
    <div className="flex flex-col items-center gap-2">
      <p className={`text-xs uppercase ${color}`}>{side}</p>
      <div className="flex -space-x-2">
        {champions.map((c) => (
          <img
            key={`${side}-${c.championId}`}
            src={c.iconUrl}
            alt="champion"
            className="h-10 w-10 rounded-full border border-white/10 bg-black/40"
            title={`Champion ${c.championId}`}
          />
        ))}
      </div>
    </div>
  );
}

function WarRoomPage() {
  const { gameId = "" } = useParams();
  const decoded = useMemo(() => decodeURIComponent(gameId), [gameId]);
  useDocumentTitle(`War Room · ${decoded}`);
  const nav = useNavigate();
  const [data, setData] = useState<WarRoom | null>(null);
  const [cfg, setCfg] = useState<{ war_room_cutoff_seconds: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pickSide, setPickSide] = useState<"BLUE" | "RED">("BLUE");
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    void fetchPublicConfig().then(setCfg).catch(() => null);
  }, []);

  useEffect(() => {
    const loadUser = async () => {
      if (!getUserId()) return;
      try {
        setUser(await fetchMe());
      } catch {
        setUser(null);
      }
    };
    void loadUser();
  }, []);

  useEffect(() => {
    if (!decoded) return;
    const tick = async () => {
      try {
        setError(null);
        setData(await fetchWarRoom(decoded));
      } catch (e) {
        setError(e instanceof Error ? e.message : "War room unavailable");
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 30000);
    return () => window.clearInterval(id);
  }, [decoded]);

  const onLockPrediction = async () => {
    if (!data || !user) return;
    try {
      setError(null);
      await placePrediction(data.game_id, pickSide);
      setUser(await fetchMe());
      setData(await fetchWarRoom(decoded));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not lock prediction");
    }
  };

  const cutoff = cfg?.war_room_cutoff_seconds ?? 301;
  const stats = data?.live_stats;
  const demo = stats && stats.source && stats.source !== "riot_snapshot";

  return (
    <>
      <div className="mb-6 flex items-center justify-between gap-3">
        <button type="button" onClick={() => nav("/games")} className="text-sm text-slate-400 hover:text-white">
          ← Back to games
        </button>
        {user && (
          <div className="text-right text-sm text-slate-300">
            Record{" "}
            <span className="font-semibold text-poro-gold">
              {user.prediction_score.correct}/{user.prediction_score.resolved}
            </span>
            {user.prediction_score.resolved > 0 && user.accuracy_pct != null && (
              <span className="ml-2 text-poro-gold">({user.accuracy_pct}%)</span>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          {error}
        </div>
      )}

      {!data && !error && <p className="text-slate-400">Syncing spectator feed…</p>}

      {data && (
        <div className="space-y-6">
          <div className="glass rounded-2xl p-6">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-xs uppercase text-slate-500">War Room</p>
                <h2 className="font-display text-3xl text-white">{data.queue}</h2>
                <p className="text-sm text-slate-400">{data.rank_bracket}</p>
              </div>
              <div className="text-right">
                <p className="text-xs uppercase text-slate-500">Delayed clock</p>
                <p className="text-4xl font-semibold text-white">{formatClock(data.game_length_seconds)}</p>
                <p className="text-xs text-slate-500">Cutoff {formatClock(cutoff)}</p>
              </div>
            </div>
            <p className="mt-4 text-xs text-slate-500">{data.spectator_delay_note}</p>
            <div className="mt-6 flex flex-col gap-6 md:flex-row md:items-start md:justify-between">
              <TeamBoard label="Blue side" tint="blue" champions={data.blue.champions} />
              <TeamBoard label="Red side" tint="red" champions={data.red.champions} />
            </div>
          </div>

          {data.phase === "ANALYSIS" && (
            <div className="glass rounded-2xl p-6">
              <div className="flex items-center justify-between gap-3">
                <h3 className="font-display text-lg text-white">Live analysis window</h3>
                {demo && (
                  <span className="rounded-full border border-amber-400/40 px-3 py-1 text-xs text-amber-100">
                    Demo stats enabled on server
                  </span>
                )}
              </div>
              {stats && (
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <StatsCard label="Blue economy" team={stats.blue} />
                  <StatsCard label="Red economy" team={stats.red} />
                </div>
              )}
              {!stats && (
                <p className="mt-3 text-sm text-slate-400">
                  Riot’s Spectator snapshot for this queue did not include team totals. Enable{" "}
                  <code className="text-poro-gold">POROBOOK_DEMO_STATS</code> on the server for synthetic numbers
                  while prototyping UI.
                </p>
              )}
              {data.live_stats_note && <p className="mt-3 text-xs text-slate-500">{data.live_stats_note}</p>}
            </div>
          )}

          {(data.phase === "LOCKOUT" || data.phase === "LOCKED") && (
            <div className="glass rounded-2xl p-6">
              <h3 className="font-display text-lg text-white">Prediction lock</h3>
              <p className="mt-2 text-sm text-slate-300">
                The 5:00 analysis window on the delayed clock has closed. Pick which side wins — no coins or payouts,
                just a call. Once you lock in, this match hides live totals so you cannot late-scout the game.
              </p>
              {data.phase === "LOCKED" ? (
                <p className="mt-4 text-sm text-poro-gold">You are locked in. We will score it when Riot posts the match.</p>
              ) : (
                <div className="mt-4 space-y-4">
                  <div className="flex gap-3">
                    <button
                      type="button"
                      onClick={() => setPickSide("BLUE")}
                      className={`flex-1 rounded-xl border px-3 py-3 text-sm font-semibold ${
                        pickSide === "BLUE"
                          ? "border-poro-blue bg-poro-blue/20 text-white"
                          : "border-white/10 text-slate-300 hover:border-white/30"
                      }`}
                    >
                      Blue wins
                    </button>
                    <button
                      type="button"
                      onClick={() => setPickSide("RED")}
                      className={`flex-1 rounded-xl border px-3 py-3 text-sm font-semibold ${
                        pickSide === "RED"
                          ? "border-poro-red bg-poro-red/20 text-white"
                          : "border-white/10 text-slate-300 hover:border-white/30"
                      }`}
                    >
                      Red wins
                    </button>
                  </div>
                  <button
                    type="button"
                    disabled={!user}
                    onClick={() => void onLockPrediction()}
                    className="w-full rounded-lg bg-poro-gold px-3 py-3 text-sm font-semibold text-poro-ink transition enabled:hover:brightness-110 disabled:opacity-40"
                  >
                    {user ? `Lock prediction: ${pickSide} wins` : "Create an account to lock"}
                  </button>
                  {!user && (
                    <p className="text-center text-xs text-slate-500">
                      <Link to="/" className="text-poro-gold hover:underline">
                        Account
                      </Link>
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function StatsCard({ label, team }: { label: string; team: Record<string, number> }) {
  const rows = [
    ["Gold", team.gold],
    ["Kills", team.kills],
    ["Towers", team.towers],
    ["Dragons", team.dragons],
  ].filter(([, v]) => typeof v === "number" && !Number.isNaN(v));
  return (
    <div className="rounded-xl border border-white/10 bg-black/30 p-4">
      <p className="text-sm font-semibold text-white">{label}</p>
      <dl className="mt-3 space-y-2 text-sm text-slate-300">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between">
            <dt>{k}</dt>
            <dd className="font-mono text-white">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function TeamBoard({
  label,
  tint,
  champions,
}: {
  label: string;
  tint: "blue" | "red";
  champions: { championId: number; iconUrl: string }[];
}) {
  const border = tint === "blue" ? "border-poro-blue/40" : "border-poro-red/40";
  return (
    <div className={`flex-1 rounded-2xl border ${border} bg-white/5 p-4`}>
      <p className="text-sm font-semibold text-white">{label}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {champions.map((c) => (
          <img
            key={c.championId}
            src={c.iconUrl}
            alt="champion"
            className="h-14 w-14 rounded-xl border border-white/10 bg-black/40"
          />
        ))}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<AccountPage />} />
        <Route path="/leaderboard" element={<LeaderboardPage />} />
        <Route path="/games" element={<GamesLobbyPage />} />
        <Route path="/games/war/:gameId" element={<WarRoomPage />} />
        <Route path="/war/:gameId" element={<LegacyWarRedirect />} />
      </Route>
    </Routes>
  );
}

function LegacyWarRedirect() {
  const { gameId = "" } = useParams();
  return <Navigate to={`/games/war/${encodeURIComponent(gameId)}`} replace />;
}
