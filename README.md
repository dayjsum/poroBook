# PoroBook

Small League of Legends companion: browse **live spectator games** (Riot API), open a **war room** with a delayed clock and comp view, and lock **blue/red win predictions** tracked in a local leaderboard.

### “Random” real games (recommended)

Riot does not offer a true random-match API. The closest is **`POROBOOK_DISCOVERY_MODE=featured`**: a **background harvester** calls Riot on a timer (~60–90s), shuffles the featured snapshot, and stores enriched cards in memory. **`GET /api/lobby` only returns that cache** (no Riot call per browser refresh), so the UI stays fast and respects dev-key limits. Leave **`SEEDER_PUUIDS` empty** for the simplest setup. The harvester backs off longer when spectator **403** or quota signals appear. You still need a working dev key with **spectator** access (no **403** on featured).

## How it fits together

| Piece | Source |
|--------|--------|
| Live match list (`/games`) | Riot **Spectator** (featured games and/or **seeder** Puuids). Does **not** read your SQLite user table. Featured games are a **small rotating list per platform shard**, not every live match globally; **403** usually means the key is missing the **League of Legends** product or spectator access, not that League is empty. This app uses **spectator-v5** for active games by PUUID and tries **v5 then v4** for featured games. |
| Your login / predictions | **PoroBook accounts** stored in SQLite (`users`, `predictions`). Created from the **Account** page — not Riot login. |
| Leaderboard | Only players with enough **resolved** predictions (default: 10). Until matches finish and the resolver scores picks, the board can stay empty even while live games appear. |

If the browser still has an old `porobook_user_id` (for example after deleting `porobook.db` or pointing the app at a new database), `/api/me` returns **User not found** while the lobby keeps working. The client clears that stale id when it sees that error so you can create a fresh account.

## Requirements

- Python 3.11+ (recommended)
- Node 18+ for the Vite frontend
- A Riot developer API key with access to **League of Legends** spectator APIs (**spectator-v5**, with **v4** fallback for featured games in this repo) and the regions you configure

### Riot developer portal (League product and key)

1. Sign in at [developer.riotgames.com](https://developer.riotgames.com/).
2. Under **Apps** (or **Register Product**), ensure **League of Legends** is registered for your application.
3. Open **API keys** (development key for testing), copy the key into `RIOT_API_KEY`, and restart the backend.
4. If you see **403** on spectator routes, regenerate the key after adding the product, confirm no IP lockout mismatches your machine, and note that **personal development keys** must stay within Riot’s rate limits.

There is no separate checkbox named “spectator-v4” — access is bundled with the **League of Legends** product on your key. Riot documents **spectator-v5** (e.g. active game by PUUID); **v4** may still serve some routes by region.

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt
```

Create `backend/.env` (see variables below), then:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check: `http://127.0.0.1:8000/api/health`

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies API calls to the backend (see `frontend/vite.config.ts`).

## Environment variables (backend)

| Variable | Purpose |
|----------|---------|
| `RIOT_API_KEY` | Required for lobby and war room. |
| `RIOT_PLATFORM` | Shard for spectator/summoner calls (default `na1`). |
| `RIOT_REGION` | Regional route for match-v5 (default `americas`). |
| `POROBOOK_DISCOVERY_MODE` | `featured` (default), `seeders`, or `both`. |
| `SEEDER_PUUIDS` | Comma-separated **Riot API PUUIDs** when using `seeders` or `both`. **`RIOT_PLATFORM` must match that account’s home shard** (EUW → `euw1`, Korea → `kr`, etc.). Discovery mode must be **`seeders` or `both`** if you want seeders used. **Do not paste dozens of ladder Puuids** — development keys rate-limit (~20/s, ~100/2 min); huge lists cause **HTTP 429**, not “wrong shard.” Prefer a **small** list (streamers you follow). Puuids are normalized (quotes/BOM/newlines). If spectator-v5 returns **400**, the server retries via summoner-v4 + spectator-v4 for that PUUID. |
| `SEEDER_MAX_LOOKUPS_PER_POLL` | Max seeders queried per lobby refresh (default **10**). Put important Puuids **first** in `SEEDER_PUUIDS`. |
| `SEEDER_REQUEST_DELAY_SECONDS` | Pause between seeder spectator calls (default **0.18**) to reduce 429; raise further if needed. |
| `DATABASE_PATH` | SQLite file (default `porobook.db` under `backend/`). |
| `POROBOOK_DEMO_STATS` | Synthetic team stats when Riot snapshot has no totals (UI prototyping). |
| `LEADERBOARD_MIN_RESOLVED` | Minimum resolved predictions to appear on the leaderboard. |
| `WAR_ROOM_CUTOFF_SECONDS` | Analysis window on the delayed spectator clock before lock phase. |
| `SPECTATOR_LOBBY_CACHE_SECONDS` | How long to keep each match’s raw spectator snapshot after a successful lobby harvest (default **300**), so `/war-room` can still open a card if Riot drops it from featured/active seconds later. |
| `LOBBY_HARVEST_INTERVAL_SECONDS` | Base seconds between background Riot harvest cycles (default **75**). |
| `LOBBY_HARVEST_JITTER_SECONDS` | Random ± jitter added to the interval (default **15**). |

## Repository layout

- `backend/app` — FastAPI app, Riot client, match enrichment, SQLite access
- `frontend/src` — React UI (Vite + Tailwind)
- `scripts/` — optional helpers for Puuids / ladder sampling
- `docs/PROJECT_JOURNEY.md` — detailed narrative of goals, architecture, problems faced (403/429/featured vs “no games”), and mitigations
