# PoroBook — project journey and problem log

This document describes what **PoroBook** is, what you set out to do, how the system is wired, the problems that showed up while building and running it, and how those problems were addressed (or what still blocks progress).

---

## 1. What you are building

**PoroBook** is a small **League of Legends** companion app (FastAPI backend + React/Vite frontend) that:

- Shows **real live games** using Riot’s **Spectator** APIs (featured list and/or optional “seeder” accounts).
- Lets a signed-in user open a **war room** for a match: delayed spectator clock, team comps, optional live-style stats, then a **blue vs red win** prediction locked after an analysis window.
- Stores **PoroBook accounts** and predictions in **SQLite** (not Riot login).
- Resolves predictions when Riot publishes the finished match via **Match-v5**, and surfaces a **leaderboard** once users have enough resolved picks.

So the product is: **spectate-oriented picks on real matches**, with a local identity and scoreboard—not a full Riot OAuth integration.

---

## 2. How the pieces fit together (mental model)

| Concern | Where it lives | Notes |
|--------|----------------|--------|
| “What live games exist?” | **Riot Spectator** (featured and/or active game by player) | Not “every game on Earth”—a **shard-scoped** snapshot (featured) or **specific Puuids** (seeders). |
| “Who am I on PoroBook?” | **SQLite** `users` + browser `localStorage` user id | Created on the Account page; unrelated to summoner names in the match. |
| “Who leads?” | **SQLite** `predictions` + leaderboard query | Only players with enough **resolved** picks (default: 10) appear. |

Confusing these layers caused a lot of early “no users” / “no games” misunderstandings: **live opponents in a match are not PoroBook users**, and **an empty leaderboard does not mean League has no players**.

---

## 3. Progress you made (capabilities shipped)

Rough chronological arc of what got built or hardened:

1. **Core flow**: lobby → war room → lock prediction → background resolver scores when match-v5 shows a winner.
2. **Clarity on “no users”**: stale `localStorage` after DB resets → clear id on “User not found”; registration clears old id first.
3. **Riot realism**: surfaced **403** on featured instead of treating it like an empty list; documented that **featured ≠ all live games**.
4. **Spectator versions**: **spectator-v5** for active game by PUUID, **v5 then v4** for featured; **seeder path** uses `seeder_active_game` (v5, then on **400** falls back to summoner-v4 + spectator-v4).
5. **Rate limits (429)**: dev keys are strict (~20/s, ~100/2 min per routing); added delays, capped seeder lookups per poll, **stop further seeder calls after first 429** in a poll, slowed Games auto-refresh, and recommended **short** seeder lists.
6. **War room 404 after lobby showed a card**: Riot can drop a game between polls; added a **short-lived server cache** of raw spectator payloads from successful `/api/lobby` responses so war room can still open a recently listed match.
7. **“Random real games” workflow**: default toward **featured-only**, empty seeders, **shuffle** each lobby response, README guidance; **Games page** now **auto-polls every 10s while empty**, **45s when games exist**, and **5 min** when spectator **403** is detected so the client does not hammer Riot.

---

## 4. Problems you dealt with (and what they actually were)

### 4.1 “No users” / account feels broken while games load

**Symptom:** Games API works; `/api/me` fails or UI shows no signed-in user.

**Cause:** **PoroBook users** live in SQLite. If the DB was recreated or `DATABASE_PATH` changed, the browser could still hold an old `porobook_user_id`. The server then returned **404 User not found** for `/api/me` while the lobby (no user header required) still worked.

**Mitigation:** Clear stored user id when `/api/me` returns “User not found”; clear before registering a new account.

---

### 4.2 “Millions of games per day—why is the list empty?”

**Symptom:** Empty lobby with a message about no featured / offline seeders.

**Cause:** Riot’s **featured spectator** endpoint returns a **small rotating list per platform**, not a catalog of all live matches globally. It can legitimately be empty at quiet times.

**Additional trap:** The code originally treated **403/404** on featured as an empty list, which looked like “no games” when it was really **“key cannot call spectator”**.

**Mitigation:** Explicit error strings for **401 / 403 / 429**; try **v5 featured then v4**; README explains the difference between global match volume and this API surface.

---

### 4.3 Streamer on Twitch + op.gg “live,” but seeder never shows

**Symptom:** Puuid in `SEEDER_PUUIDS`, still no game.

**Causes (common):**

- **`RIOT_PLATFORM` mismatch** — summoner/active game are **per-shard** (e.g. EUW account needs `euw1`, not `na1`).
- **`POROBOOK_DISCOVERY_MODE`** — with **`featured` only**, seeders are **never queried** (must be `seeders` or `both`).
- **Id type** — value must be Riot’s **PUUID** for that region’s APIs, not an arbitrary site id.
- **Timing** — “active game” often means **in map**, not champ select.

**Mitigation:** Seeder diagnostics in lobby notices; saner parsing of `SEEDER_PUUIDS`; v5→v4 fallback on **400** for seeder active lookup.

---

### 4.4 Development API key: 403 on spectator featured

**Symptom:** Notice that the key cannot use League spectator (v5/v4).

**Cause:** Riot rejects the call—typically **League product not on the app that owns the key**, **wrong/expired key**, **dev vs prod key confusion**, or paste issues (quotes/spaces).

**Mitigation:** Clearer copy pointing at the developer portal; **no** per-endpoint “enable spectator-v4” toggles—the product + key must be valid.

**Important:** Auto-refreshing every few seconds **does not** fix 403; it only wastes quota. The UI therefore **backs off to 5 minutes** when a spectator **403** pattern is detected in the notice.

---

### 4.5 HTTP 429 on seeders (and “20× 429” spam)

**Symptom:** Every seeder line shows **429**.

**Cause:** **Too many** spectator calls in a short window vs dev key limits (especially **~100 requests / 2 minutes**). A long `SEEDER_PUUIDS` list × frequent lobby polls × featured attempts exhausts the budget.

**Mitigation:** Cap seeders per poll, delay between calls, **abort remaining seeders after first 429** in a poll, slower default lobby interval, README telling you to keep **few** Puuids; optional move to **featured-only** to avoid seeder traffic entirely.

---

### 4.6 HTTP 400 on spectator-v5 by PUUID

**Symptom:** Seeder checks show **400** for many Puuids.

**Cause:** Riot sometimes returns **400** on the v5 path even when the same identity works through **summoner-v4 + spectator-v4**.

**Mitigation:** `seeder_active_game`: on **400**, retry via **summoner by PUUID → v4 active game**.

---

### 4.7 War room 404 for a `game_id` that was just in the lobby

**Symptom:** `GET /api/matches/{game_id}/war-room` returns **404** while `/api/lobby` had returned matches.

**Cause:** War room **re-queries** Riot; the match can leave featured/active between the lobby response and the war-room request.

**Mitigation:** **Server-side cache** of raw spectator payloads from the last successful lobby merge (TTL configurable, default ~5 minutes), checked before live Riot lookup.

---

### 4.8 Stale settings after `.env` edits

**Symptom:** Changed `.env` but behavior unchanged.

**Cause:** Backend uses **`get_settings()` with `@lru_cache`** in this project—settings are read once per process. **`uvicorn --reload` often does not restart** on `.env` changes alone.

**Mitigation:** Restart the API process after changing `.env`, or touch a Python file to force reload.

---

## 5. What you are doing *now* (intended workflow)

You pivoted toward:

- **`POROBOOK_DISCOVERY_MODE=featured`** — rely on Riot’s **real** featured pool.
- **Empty or minimal `SEEDER_PUUIDS`** — avoid rate-limit storms and PUUID/shard debugging unless you explicitly want “follow these accounts.”
- **Shuffle + refresh** — each lobby response is a new ordering over whatever Riot returned; the UI **auto-retries every 10s** while empty (unless spectator **403** slows polling).

That is the closest honest implementation of “random real games”: **random order over Riot’s featured snapshot**, not a non-existent “random global match” API.

---

## 6. What is still blocking or fragile

1. **Spectator 403** — until the **developer portal + key** issue is fixed, **featured will stay empty** regardless of refresh logic.
2. **Featured emptiness at quiet times** — even with a perfect key, the list can be empty; patience or peak hours.
3. **Match volatility** — games appear and disappear quickly; cache and messaging reduce confusion but cannot keep a finished game “live” forever.
4. **Dev key limits** — aggressive polling anywhere (multiple tabs, scripts + UI) can still 429; featured-only + backoff is the sustainable default.

---

## 7. Quick reference — environment knobs that mattered in this journey

| Variable | Role in the problems above |
|----------|----------------------------|
| `RIOT_API_KEY` | Must be a valid **development** key for an app with **League**; fixes **403** when correct. |
| `RIOT_PLATFORM` | Wrong value → **404**/no summoner for Puuids on that shard. |
| `POROBOOK_DISCOVERY_MODE` | `featured` vs `both` vs `seeders` controls whether **PUUID list** is used at all. |
| `SEEDER_PUUIDS` | Long lists → **429**; must be real Puuids on the right shard. |
| `SEEDER_MAX_LOOKUPS_PER_POLL` / `SEEDER_REQUEST_DELAY_SECONDS` | Tuning to reduce **429**. |
| `SPECTATOR_LOBBY_CACHE_SECONDS` | Helps **war room** after lobby showed a card. |

---

## 8. How to use this document

- **Onboarding** a future you (or a teammate): read §1–2 for intent, §4 for “why it broke,” §5–6 for current stance and limits.
- **Incident triage**: map symptoms to §4 headings (403 vs 429 vs 404 vs empty featured).
- **Product pitch**: §1 + “real Riot spectator data, local accounts, picks resolved by match-v5.”

This file is descriptive history and troubleshooting context; operational setup remains in the root **`README.md`**.
