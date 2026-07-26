from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kgs_url TEXT UNIQUE,
    sgf_path TEXT NOT NULL,
    played_at TEXT,
    year INTEGER,
    month INTEGER,
    black TEXT,
    white TEXT,
    player_color TEXT,
    opponent TEXT,
    result TEXT,
    handicap INTEGER DEFAULT 0,
    board_size INTEGER DEFAULT 19,
    downloaded_at TEXT,
    analyzed_quick INTEGER DEFAULT 0,
    analyzed_deep INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL REFERENCES games(id),
    move_number INTEGER NOT NULL,
    color TEXT NOT NULL,
    coord TEXT,
    player TEXT,
    score_before REAL,
    score_after REAL,
    point_loss REAL,
    winrate_before REAL,
    winrate_after REAL,
    best_move TEXT,
    played_move TEXT,
    severity TEXT,
    phase TEXT,
    UNIQUE(game_id, move_number)
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_games_played_at ON games(played_at);
CREATE INDEX IF NOT EXISTS idx_moves_point_loss ON moves(point_loss DESC);
CREATE INDEX IF NOT EXISTS idx_moves_severity ON moves(severity);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_sync_value(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_sync_value(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def upsert_game(self, game: dict[str, Any]) -> int:
        cols = [
            "kgs_url", "sgf_path", "played_at", "year", "month",
            "black", "white", "player_color", "opponent", "result",
            "handicap", "board_size", "downloaded_at",
        ]
        existing = None
        if game.get("kgs_url"):
            existing = self.conn.execute(
                "SELECT id FROM games WHERE kgs_url = ?", (game["kgs_url"],)
            ).fetchone()
        if not existing and game.get("sgf_path"):
            existing = self.conn.execute(
                "SELECT id FROM games WHERE sgf_path = ?", (game["sgf_path"],)
            ).fetchone()

        values = [game.get(c) for c in cols]
        if existing:
            game_id = existing["id"]
            set_clause = ", ".join(f"{c}=?" for c in cols[1:])
            self.conn.execute(
                f"UPDATE games SET {set_clause} WHERE id = ?",
                values[1:] + [game_id],
            )
        else:
            placeholders = ", ".join("?" for _ in cols)
            cur = self.conn.execute(
                f"INSERT INTO games ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            game_id = cur.lastrowid
        self.conn.commit()
        return int(game_id)

    def mark_analyzed(self, game_id: int, mode: str) -> None:
        col = "analyzed_deep" if mode == "deep" else "analyzed_quick"
        self.conn.execute(f"UPDATE games SET {col} = 1 WHERE id = ?", (game_id,))
        self.conn.commit()

    def games_pending_analysis(self, mode: str = "quick", limit: int = 100) -> list[sqlite3.Row]:
        col = "analyzed_deep" if mode == "deep" else "analyzed_quick"
        return self.conn.execute(
            f"SELECT * FROM games WHERE {col} = 0 ORDER BY played_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def save_move_analysis(self, game_id: int, move: dict[str, Any]) -> None:
        cols = [
            "game_id", "move_number", "color", "coord", "player",
            "score_before", "score_after", "point_loss",
            "winrate_before", "winrate_after", "best_move", "played_move",
            "severity", "phase",
        ]
        values = [game_id] + [move.get(c) for c in cols[1:]]
        placeholders = ", ".join("?" for _ in cols)
        self.conn.execute(
            f"INSERT INTO moves ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(game_id, move_number) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in cols[2:]),
            values,
        )
        self.conn.commit()

    def top_blunders(self, limit: int = 30, player: str | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT m.*, g.opponent, g.played_at, g.result, g.sgf_path
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.point_loss IS NOT NULL AND m.point_loss >= 1.5
        """
        params: list[Any] = []
        if player:
            query += " AND m.player = ?"
            params.append(player)
        query += " ORDER BY m.point_loss DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def stats_summary(self) -> dict[str, Any]:
        games = self.conn.execute("SELECT COUNT(*) AS c FROM games").fetchone()["c"]
        analyzed = self.conn.execute(
            "SELECT COUNT(*) AS c FROM games WHERE analyzed_quick = 1"
        ).fetchone()["c"]
        blunders = self.conn.execute(
            "SELECT COUNT(*) AS c FROM moves WHERE severity IN ('blunder', 'mega_blunder')"
        ).fetchone()["c"]
        avg_loss = self.conn.execute(
            "SELECT AVG(point_loss) AS a FROM moves WHERE point_loss > 0"
        ).fetchone()["a"]
        return {
            "games_total": games,
            "games_analyzed": analyzed,
            "blunders_total": blunders,
            "avg_point_loss": round(avg_loss or 0, 2),
        }

    def export_analysis_json(self, game_id: int, out_path: Path) -> None:
        moves = self.conn.execute(
            "SELECT * FROM moves WHERE game_id = ? ORDER BY move_number",
            (game_id,),
        ).fetchall()
        game = self.conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        payload = {
            "game": dict(game) if game else {},
            "moves": [dict(m) for m in moves],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
