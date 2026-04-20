import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings


_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    settings = get_settings()
    path = Path(settings.database_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _lock, get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                balance INTEGER NOT NULL,
                last_daily_claim_date TEXT
            );

            CREATE TABLE IF NOT EXISTS bets (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                bet_amount INTEGER NOT NULL,
                prediction TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, game_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);

            CREATE TABLE IF NOT EXISTS predictions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                prediction TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, game_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
            """
        )
        _migrate_legacy_bets_to_predictions(conn)


def _migrate_legacy_bets_to_predictions(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='bets'"
    ).fetchone()
    if not row:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO predictions (id, user_id, game_id, prediction, status, created_at)
        SELECT id, user_id, game_id, prediction, status, created_at FROM bets
        """
    )


def create_user(username: str) -> dict:
    uid = str(uuid.uuid4())
    with _lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, username, balance) VALUES (?, ?, 0)",
            (uid, username),
        )
    return {"id": uid, "username": username}


def get_user(user_id: str) -> dict | None:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return dict(row)


def list_predictions_for_user(user_id: str) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_prediction(user_id: str, game_id: str) -> dict | None:
    with _lock, get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM predictions WHERE user_id = ? AND game_id = ?",
            (user_id, game_id),
        ).fetchone()
    return dict(row) if row else None


def place_prediction(user_id: str, game_id: str, prediction: str) -> dict:
    if prediction not in ("BLUE", "RED"):
        raise ValueError("prediction must be BLUE or RED")
    pred_id = str(uuid.uuid4())
    with _lock, get_conn() as conn:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise LookupError("user not found")
        existing = conn.execute(
            "SELECT id FROM predictions WHERE user_id = ? AND game_id = ?",
            (user_id, game_id),
        ).fetchone()
        if existing:
            raise ValueError("prediction already locked for this match")
        conn.execute(
            """
            INSERT INTO predictions (id, user_id, game_id, prediction, status, created_at)
            VALUES (?, ?, ?, ?, 'PENDING', ?)
            """,
            (pred_id, user_id, game_id, prediction, _now_iso()),
        )
    return get_prediction(user_id, game_id)  # type: ignore[return-value]


def list_pending_predictions() -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT * FROM predictions WHERE status = 'PENDING'").fetchall()
    return [dict(r) for r in rows]


def mark_prediction_result(prediction_id: str, correct: bool) -> None:
    status = "WON" if correct else "LOST"
    with _lock, get_conn() as conn:
        conn.execute("UPDATE predictions SET status = ? WHERE id = ?", (status, prediction_id))


def prediction_score(user_id: str) -> dict[str, int]:
    with _lock, get_conn() as conn:
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) AS correct,
              SUM(CASE WHEN status IN ('WON', 'LOST') THEN 1 ELSE 0 END) AS resolved
            FROM predictions
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    c = int(row["correct"] or 0) if row else 0
    r = int(row["resolved"] or 0) if row else 0
    return {"correct": c, "resolved": r}


def user_leaderboard_rank(user_id: str, min_resolved: int) -> int | None:
    """RANK() (1-based); None if user is not eligible or has no rows."""
    with _lock, get_conn() as conn:
        row = conn.execute(
            """
            WITH per_user AS (
              SELECT
                p.user_id AS user_id,
                SUM(CASE WHEN p.status = 'WON' THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN p.status IN ('WON', 'LOST') THEN 1 ELSE 0 END) AS resolved
              FROM predictions p
              GROUP BY p.user_id
              HAVING resolved >= ?
            ),
            ranked AS (
              SELECT user_id,
                RANK() OVER (
                  ORDER BY
                    (CAST(correct AS REAL) / resolved) DESC,
                    resolved DESC,
                    correct DESC
                ) AS rank
              FROM per_user
            )
            SELECT rank FROM ranked WHERE user_id = ?
            """,
            (min_resolved, user_id),
        ).fetchone()
    if not row or row["rank"] is None:
        return None
    return int(row["rank"])


def prediction_leaderboard(min_resolved: int, limit: int) -> list[dict[str, Any]]:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """
            WITH per_user AS (
              SELECT
                p.user_id AS user_id,
                SUM(CASE WHEN p.status = 'WON' THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN p.status IN ('WON', 'LOST') THEN 1 ELSE 0 END) AS resolved
              FROM predictions p
              GROUP BY p.user_id
              HAVING resolved >= ?
            ),
            ranked AS (
              SELECT user_id, correct, resolved,
                (CAST(correct AS REAL) / resolved) AS accuracy,
                RANK() OVER (
                  ORDER BY
                    (CAST(correct AS REAL) / resolved) DESC,
                    resolved DESC,
                    correct DESC
                ) AS rank
              FROM per_user
            )
            SELECT r.rank, r.user_id, u.username, r.correct, r.resolved, r.accuracy
            FROM ranked r
            JOIN users u ON u.id = r.user_id
            ORDER BY r.rank ASC, u.username ASC
            LIMIT ?
            """,
            (min_resolved, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "rank": int(row["rank"]),
                "user_id": row["user_id"],
                "username": row["username"],
                "correct": int(row["correct"]),
                "resolved": int(row["resolved"]),
                "accuracy_pct": round(float(row["accuracy"]) * 100.0, 2),
            }
        )
    return out