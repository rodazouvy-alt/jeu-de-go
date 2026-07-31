from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sgf_parse import effective_move_quality, DEFAULT_THRESHOLDS, resolved_engine_best


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
    player_rank TEXT,
    opponent_rank TEXT,
    player_sl_estimated TEXT,
    opponent_sl_estimated TEXT,
    human_sl_game_json TEXT,
    analyzed_quick INTEGER DEFAULT 0,
    analyzed_deep INTEGER DEFAULT 0,
    analyzed_opening INTEGER DEFAULT 0
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
    theme TEXT,
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
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table, column, coltype in (
            ("games", "player_rank", "TEXT"),
            ("games", "opponent_rank", "TEXT"),
            ("games", "player_sl_estimated", "TEXT"),
            ("games", "opponent_sl_estimated", "TEXT"),
            ("games", "human_sl_game_json", "TEXT"),
            ("moves", "theme", "TEXT"),
        ):
            cols = {
                row[1]
                for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in cols:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"
                )
        for col, coltype in (
            ("human_prior_json", "TEXT"),
            ("study_priority", "REAL"),
            ("stretch_score", "REAL"),
            ("stretch_top_move", "TEXT"),
            ("top_moves_json", "TEXT"),
            ("review_sgf_path", "TEXT"),
            ("pattern_hash", "TEXT"),
            ("pattern_cluster_id", "INTEGER"),
            ("pro_context_json", "TEXT"),
            ("human_rank_moves_json", "TEXT"),
            ("move_scores_json", "TEXT"),
            ("line_loss", "REAL"),
        ):
            cols = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(moves)")
            }
            if col not in cols:
                self.conn.execute(
                    f"ALTER TABLE moves ADD COLUMN {col} {coltype}"
                )
        for col, coltype in (
            ("analyzed_opening", "INTEGER"),
            ("time_control", "TEXT"),
            ("duration_sec", "INTEGER"),
            ("avg_move_sec", "REAL"),
            ("moves_per_min", "REAL"),
            ("speed", "TEXT"),
            ("duration_estimated", "INTEGER"),
            ("analysis_skip_reason", "TEXT"),
        ):
            cols = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(games)")
            }
            if col not in cols:
                self.conn.execute(
                    f"ALTER TABLE games ADD COLUMN {col} {coltype}"
                )
        self.conn.execute(
            "UPDATE games SET analyzed_opening = 0 WHERE analyzed_opening IS NULL"
        )
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT NOT NULL UNIQUE,
                move_count INTEGER NOT NULL DEFAULT 0,
                total_point_loss REAL DEFAULT 0,
                dominant_theme TEXT,
                patch_ascii TEXT,
                sample_json TEXT,
                updated_at TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_moves_pattern_hash ON moves(pattern_hash)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_clusters_count "
            "ON pattern_clusters(move_count DESC)"
        )
        cols = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(pattern_clusters)")
        }
        if "pattern_kind" not in cols:
            self.conn.execute(
                "ALTER TABLE pattern_clusters ADD COLUMN pattern_kind TEXT DEFAULT 'pattern'"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_clusters_kind "
            "ON pattern_clusters(pattern_kind, move_count DESC)"
        )

    def close(self) -> None:
        self.conn.close()

    def get_sync_value(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def is_month_synced(self, year: int, month: int) -> bool:
        return self.get_sync_value(f"synced_month:{year}-{month}") == "done"

    def mark_month_synced(self, year: int, month: int, count: int) -> None:
        self.set_sync_value(f"synced_month:{year}-{month}", "done")
        self.set_sync_value(
            f"synced_month_count:{year}-{month}",
            str(count),
        )
        self.set_sync_value("last_sync_at", datetime.now(timezone.utc).isoformat())

    def synced_months_summary(self) -> list[sqlite3.Row]:
        return self.conn.execute("""
            SELECT key, value FROM sync_state
            WHERE key LIKE 'synced_month:%' AND key NOT LIKE 'synced_month_count:%'
            ORDER BY key DESC
        """).fetchall()

    @staticmethod
    def _since_year_sql(
        since_year: int | None, alias: str | None = "g",
    ) -> tuple[str, list[Any]]:
        if since_year is None:
            return "", []
        col = "year" if not alias else f"{alias}.year"
        return f" AND {col} >= ?", [since_year]

    def pending_analysis_count(
        self, since_year: int | None = None, *, min_game_id: int | None = None,
    ) -> int:
        year_sql, year_params = self._since_year_sql(since_year, alias=None)
        id_sql, id_params = "", []
        if min_game_id is not None:
            id_sql = " AND id >= ?"
            id_params = [min_game_id]
        return self.conn.execute(
            f"SELECT COUNT(*) AS c FROM games WHERE analyzed_quick = 0"
            f"{self._not_skipped_sql()}{year_sql}{id_sql}",
            (*year_params, *id_params),
        ).fetchone()["c"]

    def pending_deep_count(
        self, since_year: int | None = None, *, min_game_id: int | None = None,
    ) -> int:
        year_sql, year_params = self._since_year_sql(since_year, alias=None)
        id_sql, id_params = "", []
        if min_game_id is not None:
            id_sql = " AND id >= ?"
            id_params = [min_game_id]
        return self.conn.execute(
            f"SELECT COUNT(*) AS c FROM games WHERE analyzed_deep = 0"
            f"{self._not_skipped_sql()}{year_sql}{id_sql}",
            (*year_params, *id_params),
        ).fetchone()["c"]

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
            "player_rank", "opponent_rank",
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

    def mark_opening_analyzed(self, game_id: int) -> None:
        self.conn.execute(
            "UPDATE games SET analyzed_opening = 1 WHERE id = ?",
            (game_id,),
        )
        self.conn.commit()

    @staticmethod
    def _not_skipped_sql(alias: str | None = None) -> str:
        prefix = f"{alias}." if alias else ""
        return f" AND COALESCE({prefix}analysis_skip_reason, '') = ''"

    def mark_analysis_skipped(self, game_id: int, reason: str) -> None:
        """Exclut une partie non analysable (SGF invalide, etc.) des files d'attente."""
        self.conn.execute(
            """
            UPDATE games SET
                analyzed_quick = 1,
                analyzed_deep = 1,
                analyzed_opening = 1,
                analysis_skip_reason = ?
            WHERE id = ?
            """,
            (reason[:500], game_id),
        )
        self.conn.commit()

    def _opening_scope_sql(
        self,
        *,
        min_game_id: int | None = None,
        max_game_id: int | None = None,
        since_year: int | None = None,
        force: bool = False,
        alias: str | None = None,
    ) -> tuple[str, list[Any]]:
        parts: list[str] = []
        params: list[Any] = []
        prefix = f"{alias}." if alias else ""
        if not force:
            parts.append(f"COALESCE({prefix}analyzed_opening, 0) = 0")
            parts.append(f"COALESCE({prefix}analyzed_quick, 0) = 1")
        if min_game_id is not None:
            parts.append(f"{prefix}id >= ?")
            params.append(min_game_id)
        if max_game_id is not None:
            parts.append(f"{prefix}id <= ?")
            params.append(max_game_id)
        if since_year is not None:
            parts.append(f"{prefix}year >= ?")
            params.append(since_year)
        if not parts:
            return "", []
        return " AND " + " AND ".join(parts), params

    def games_pending_opening(
        self,
        limit: int = 100,
        *,
        min_game_id: int | None = None,
        max_game_id: int | None = None,
        since_year: int | None = None,
        force: bool = False,
        require_quick: bool = True,
    ) -> list[sqlite3.Row]:
        scope_sql, scope_params = self._opening_scope_sql(
            min_game_id=min_game_id,
            max_game_id=max_game_id,
            since_year=since_year,
            force=force,
        )
        quick_sql = " AND analyzed_quick = 1" if require_quick else ""
        return self.conn.execute(
            f"""
            SELECT * FROM games
            WHERE 1=1{scope_sql}{quick_sql}
            ORDER BY id ASC
            LIMIT ?
            """,
            (*scope_params, limit),
        ).fetchall()

    def pending_opening_count(
        self,
        *,
        min_game_id: int | None = None,
        max_game_id: int | None = None,
        since_year: int | None = None,
        force: bool = False,
        require_quick: bool = True,
    ) -> int:
        scope_sql, scope_params = self._opening_scope_sql(
            min_game_id=min_game_id,
            max_game_id=max_game_id,
            since_year=since_year,
            force=force,
        )
        quick_sql = " AND analyzed_quick = 1" if require_quick else ""
        return self.conn.execute(
            f"SELECT COUNT(*) AS c FROM games WHERE 1=1{scope_sql}{quick_sql}",
            scope_params,
        ).fetchone()["c"]

    def opening_progress(
        self,
        *,
        min_game_id: int | None = None,
        max_game_id: int | None = None,
        since_year: int | None = None,
    ) -> dict[str, int]:
        scope_sql, scope_params = self._opening_scope_sql(
            min_game_id=min_game_id,
            max_game_id=max_game_id,
            since_year=since_year,
            force=True,
        )
        row = self.conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(analyzed_opening, 0) = 1 THEN 1 ELSE 0 END) AS done
            FROM games
            WHERE 1=1{scope_sql}
            """,
            scope_params,
        ).fetchone()
        total = int(row["total"] or 0)
        done = int(row["done"] or 0)
        return {"total": total, "done": done, "pending": total - done}

    def reset_analysis(self) -> dict[str, int]:
        """Efface toutes les analyses (garde les parties et SGF)."""
        moves_n = self.conn.execute("SELECT COUNT(*) AS c FROM moves").fetchone()["c"]
        clusters_n = self.conn.execute(
            "SELECT COUNT(*) AS c FROM pattern_clusters"
        ).fetchone()["c"]
        games_n = self.conn.execute("SELECT COUNT(*) AS c FROM games").fetchone()["c"]
        self.conn.execute("DELETE FROM moves")
        self.conn.execute("DELETE FROM pattern_clusters")
        self.conn.execute(
            """
            UPDATE games SET analyzed_quick = 0, analyzed_deep = 0,
                analyzed_opening = 0,
                human_sl_game_json = NULL, player_sl_estimated = NULL,
                opponent_sl_estimated = NULL
            """
        )
        self.conn.commit()
        return {
            "moves_deleted": moves_n,
            "clusters_deleted": clusters_n,
            "games_reset": games_n,
        }

    def prepare_v2_reanalysis(
        self,
        *,
        since_year: int | None = None,
        min_game_id: int | None = None,
        full: bool = False,
    ) -> dict[str, int]:
        """Prepare la re-analyse pipeline v2 (labo GPU juillet 2026).

        full=True  : efface les coups des parties ciblees (re-quick + fuseki F6).
        full=False : garde le quick, re-ouvre fuseki + deep pour re-passer F6.
        """
        if full:
            if since_year is None and min_game_id is None:
                return self.reset_analysis()

            year_sql, year_params = self._since_year_sql(since_year, alias="g")
            id_sql, id_params = "", []
            if min_game_id is not None:
                id_sql = " AND g.id >= ?"
                id_params = [min_game_id]

            scope = f"WHERE 1=1{year_sql}{id_sql}"
            moves_n = self.conn.execute(
                f"""
                DELETE FROM moves WHERE game_id IN (
                    SELECT g.id FROM games g {scope}
                )
                """,
                (*year_params, *id_params),
            ).rowcount
            self.conn.execute("DELETE FROM pattern_clusters")
            year_sql_u, _ = self._since_year_sql(since_year, alias=None)
            id_sql_u, id_params_u = "", []
            if min_game_id is not None:
                id_sql_u = " AND id >= ?"
                id_params_u = [min_game_id]
            self.conn.execute(
                f"""
                UPDATE games SET analyzed_quick = 0, analyzed_deep = 0,
                    analyzed_opening = 0,
                    human_sl_game_json = NULL, player_sl_estimated = NULL,
                    opponent_sl_estimated = NULL
                WHERE 1=1{year_sql_u}{id_sql_u}
                """,
                (*year_params, *id_params_u),
            )
            games_n = self.conn.execute(
                f"SELECT COUNT(*) AS c FROM games WHERE 1=1{year_sql_u}{id_sql_u}",
                (*year_params, *id_params_u),
            ).fetchone()["c"]
            self.conn.commit()
            return {
                "moves_deleted": moves_n if moves_n >= 0 else 0,
                "clusters_deleted": 0,
                "games_reset": int(games_n or 0),
                "full_reset": 1,
                "since_year": since_year,
            }

        year_sql, year_params = self._since_year_sql(since_year, alias=None)
        id_sql, id_params = "", []
        if min_game_id is not None:
            id_sql = " AND id >= ?"
            id_params = [min_game_id]

        n = self.conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM games
            WHERE analyzed_quick = 1{year_sql}{id_sql}
              AND (COALESCE(analyzed_opening, 0) = 0 OR COALESCE(analyzed_deep, 0) = 0)
            """,
            (*year_params, *id_params),
        ).fetchone()["c"]
        self.conn.execute(
            f"""
            UPDATE games
            SET analyzed_deep = 0, analyzed_opening = 0
            WHERE analyzed_quick = 1{year_sql}{id_sql}
            """,
            (*year_params, *id_params),
        )
        self.conn.commit()
        return {"games_reopened": int(n or 0), "full_reset": 0}

    def games_pending_analysis(
        self,
        mode: str = "quick",
        limit: int = 100,
        since_year: int | None = None,
        min_game_id: int | None = None,
    ) -> list[sqlite3.Row]:
        year_sql, year_params = self._since_year_sql(since_year, alias=None)
        id_sql, id_params = "", []
        if min_game_id is not None:
            id_sql = " AND id >= ?"
            id_params = [min_game_id]
        if mode == "deep":
            return self.conn.execute(
                f"""
                SELECT * FROM games
                WHERE analyzed_deep = 0{self._not_skipped_sql()}{year_sql}{id_sql}
                ORDER BY id ASC
                LIMIT ?
                """,
                (*year_params, *id_params, limit),
            ).fetchall()
        return self.conn.execute(
            f"""
            SELECT * FROM games
            WHERE analyzed_quick = 0{self._not_skipped_sql()}{year_sql}{id_sql}
            ORDER BY id ASC
            LIMIT ?
            """,
            (*year_params, *id_params, limit),
        ).fetchall()

    def games_needing_opponent_backfill(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT g.* FROM games g
            WHERE g.analyzed_quick = 1
              AND g.player_color IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM moves m
                WHERE m.game_id = g.id AND m.color != g.player_color
              )
            ORDER BY g.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_move(self, move_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT m.*, g.sgf_path, g.opponent, g.played_at, g.year, g.player_color
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.id = ?
            """,
            (move_id,),
        ).fetchone()

    def get_pattern_cluster(self, cluster_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pattern_clusters WHERE id = ?",
            (cluster_id,),
        ).fetchone()

    def pattern_clusters_occurrences(
        self, cluster_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        if not cluster_ids:
            return {}
        placeholders = ", ".join("?" for _ in cluster_ids)
        rows = self.conn.execute(
            f"""
            SELECT m.pattern_cluster_id AS cluster_id, g.id AS game_id,
                   g.opponent, m.move_number, m.point_loss, m.id AS move_id
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.pattern_cluster_id IN ({placeholders})
            ORDER BY g.id ASC, m.move_number ASC
            """,
            cluster_ids,
        ).fetchall()
        out: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            out[int(row["cluster_id"])].append(dict(row))
        return dict(out)

    def update_move_review_path(self, move_id: int, review_path: str) -> None:
        self.conn.execute(
            "UPDATE moves SET review_sgf_path = ? WHERE id = ?",
            (review_path, move_id),
        )
        self.conn.commit()

    def save_move_analysis(self, game_id: int, move: dict[str, Any]) -> None:
        cols = [
            "game_id", "move_number", "color", "coord", "player",
            "score_before", "score_after", "point_loss", "line_loss",
            "winrate_before", "winrate_after", "best_move", "played_move",
            "severity", "phase", "theme",
            "top_moves_json", "move_scores_json",
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
            SELECT m.*, g.opponent, g.opponent_rank, g.player_rank,
                   g.played_at, g.result, g.sgf_path, g.year
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.point_loss IS NOT NULL AND m.point_loss >= 1.5
        """
        params: list[Any] = []
        if player:
            query += " AND m.player = ?"
            params.append(player)
        query += " ORDER BY COALESCE(m.study_priority, m.point_loss) DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def stretch_moves(self, limit: int = 25, min_stretch: float = 0.05) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT m.*, g.opponent, g.opponent_rank, g.player_rank,
                   g.player_sl_estimated, g.opponent_sl_estimated,
                   g.sgf_path, g.year
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.stretch_score >= ?
              AND m.human_prior_json IS NOT NULL
            ORDER BY m.stretch_score DESC
            LIMIT ?
            """,
            (min_stretch, limit),
        ).fetchall()

    def analyzed_games_summary(self, limit: int = 40) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT g.id, g.opponent, g.player_rank, g.opponent_rank,
                   g.player_sl_estimated, g.opponent_sl_estimated,
                   g.year, g.month, g.result,
                   COUNT(m.id) AS move_errors,
                   SUM(CASE WHEN m.severity IN ('blunder','mega_blunder') THEN 1 ELSE 0 END) AS blunders,
                   AVG(m.point_loss) AS avg_loss,
                   MAX(m.point_loss) AS max_loss
            FROM games g
            LEFT JOIN moves m ON m.game_id = g.id AND m.point_loss > 0
            WHERE g.analyzed_quick = 1
            GROUP BY g.id
            ORDER BY g.year DESC, g.month DESC, g.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def update_game_result(self, game_id: int, result: str) -> None:
        if not result:
            return
        self.conn.execute(
            "UPDATE games SET result = ? WHERE id = ?",
            (result.strip(), game_id),
        )
        self.conn.commit()

    def count_analyzed_games(self) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) AS c FROM games WHERE analyzed_quick = 1",
            ).fetchone()["c"]
        )

    def dashboard_games(self, limit: int | None = 80) -> list[dict[str, Any]]:
        inaccuracy = 0.5  # aligné config thresholds.inaccuracy
        sql = f"""
            SELECT g.id, g.opponent, g.player_rank, g.opponent_rank,
                   g.year, g.month, g.result, g.player_color,
                   g.time_control, g.duration_sec, g.avg_move_sec, g.moves_per_min,
                   g.speed, g.duration_estimated,
                   SUM(CASE WHEN m.color = g.player_color AND m.played_move IS NOT NULL
                        AND m.best_move IS NOT NULL
                        AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move))
                        THEN 1 ELSE 0 END) AS ply_ia,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'ok'
                        AND NOT (m.played_move IS NOT NULL AND m.best_move IS NOT NULL
                        AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move)))
                        AND m.point_loss < 0.15 THEN 1 ELSE 0 END) AS ply_tres_bon,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'ok'
                        AND m.point_loss >= 0.15 AND m.point_loss < 0.35 THEN 1 ELSE 0 END) AS ply_bon,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'ok'
                        AND m.point_loss >= 0.35 THEN 1 ELSE 0 END) AS ply_ok,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'inaccuracy' THEN 1 ELSE 0 END) AS inaccuracy,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'mistake' THEN 1 ELSE 0 END) AS mistake,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'blunder' THEN 1 ELSE 0 END) AS blunder,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity = 'mega_blunder' THEN 1 ELSE 0 END) AS mega_blunder,
                   SUM(CASE WHEN m.color = g.player_color AND m.severity != 'ok' THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'inaccuracy' THEN 1 ELSE 0 END) AS opp_inaccuracy,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'mistake' THEN 1 ELSE 0 END) AS opp_mistake,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'blunder' THEN 1 ELSE 0 END) AS opp_blunder,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'mega_blunder' THEN 1 ELSE 0 END) AS opp_mega_blunder,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity != 'ok' THEN 1 ELSE 0 END) AS opp_errors,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.played_move IS NOT NULL
                        AND m.best_move IS NOT NULL
                        AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move))
                        THEN 1 ELSE 0 END) AS opp_ia,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'ok'
                        AND NOT (m.played_move IS NOT NULL AND m.best_move IS NOT NULL
                        AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move)))
                        AND m.point_loss < 0.15 THEN 1 ELSE 0 END) AS opp_tres_bon,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'ok'
                        AND m.point_loss >= 0.15 AND m.point_loss < 0.35 THEN 1 ELSE 0 END) AS opp_bon,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.severity = 'ok'
                        AND m.point_loss >= 0.35 THEN 1 ELSE 0 END) AS opp_ok,
                   AVG(CASE WHEN m.color = g.player_color AND m.point_loss > 0
                        THEN m.point_loss END) AS avg_loss,
                   AVG(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.point_loss > 0
                        THEN m.point_loss END) AS opp_avg_loss,
                   (SELECT m2.score_after FROM moves m2
                    WHERE m2.game_id = g.id AND m2.score_after IS NOT NULL
                    ORDER BY m2.move_number DESC LIMIT 1) AS final_score_lead
            FROM games g
            LEFT JOIN moves m ON m.game_id = g.id
            WHERE g.analyzed_quick = 1
            GROUP BY g.id
            ORDER BY g.year DESC, g.month DESC, g.id DESC
            """
        if limit is not None:
            sql += "\n            LIMIT ?"
            rows = self.conn.execute(sql, (limit,)).fetchall()
        else:
            rows = self.conn.execute(sql).fetchall()
        games = [dict(r) for r in rows]
        for g in games:
            prev_by_num: dict[int, str | None] = {}
            prev_sev: str | None = None
            for row in self.conn.execute(
                """
                SELECT m.move_number, m.severity
                FROM moves m
                JOIN games g ON g.id = m.game_id
                WHERE m.game_id = ? AND m.color = g.player_color
                ORDER BY m.move_number
                """,
                (g["id"],),
            ).fetchall():
                prev_by_num[row["move_number"]] = prev_sev
                prev_sev = row["severity"] or "ok"

            g["_moves"] = []
            for m in self.conn.execute(
                """
                SELECT m.id, m.move_number, m.color, m.severity, m.point_loss, m.line_loss,
                       m.phase, m.theme, m.played_move, m.best_move,
                       m.score_before, m.score_after,
                       m.human_prior_json, m.human_rank_moves_json,
                       m.top_moves_json, m.stretch_top_move,
                       m.pattern_cluster_id,
                       pc.move_count AS cluster_move_count,
                       COALESCE(pc.pattern_kind, 'pattern') AS pattern_kind
                FROM moves m
                JOIN games g ON g.id = m.game_id
                LEFT JOIN pattern_clusters pc ON pc.id = m.pattern_cluster_id
                WHERE m.game_id = ? AND m.color = g.player_color
                  AND (
                    m.severity != 'ok'
                    OR COALESCE(m.line_loss, 0) >= 0.8
                  )
                ORDER BY m.move_number
                """,
                (g["id"],),
            ).fetchall():
                row = dict(m)
                pl, sev = effective_move_quality(
                    row, player_color=g.get("player_color"),
                )
                row["point_loss"] = pl
                row["severity"] = sev
                line_loss = float(row.get("line_loss") or 0)
                if sev == "ok" and line_loss < 0.8:
                    continue
                row["_prev_player_severity"] = prev_by_num.get(row["move_number"])
                g["_moves"].append(row)
        return games

    def reconcile_move_severities(
        self, thresholds: dict | None = None,
    ) -> dict[str, int]:
        """Recalcule best_move / point_loss / severity depuis les scores stockés."""
        th = thresholds or DEFAULT_THRESHOLDS
        rows = self.conn.execute(
            """
            SELECT m.id, m.played_move, m.best_move, m.point_loss, m.severity,
                   m.top_moves_json, m.move_scores_json, m.score_after, m.color,
                   g.player_color
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.color = g.player_color
            """
        ).fetchall()
        updated = 0
        loss_fixed = 0
        best_fixed = 0
        for row in rows:
            move = dict(row)
            new_best = resolved_engine_best(move)
            pl, sev = effective_move_quality(
                move, player_color=move.get("player_color"), thresholds=th,
            )
            old_pl = float(move.get("point_loss") or 0)
            old_sev = move.get("severity") or "ok"
            old_best = move.get("best_move") or ""
            changed = (
                abs(pl - old_pl) > 0.05
                or sev != old_sev
                or (new_best and new_best != old_best)
            )
            if changed:
                self.conn.execute(
                    "UPDATE moves SET point_loss = ?, severity = ?, best_move = ? WHERE id = ?",
                    (pl, sev, new_best or old_best, move["id"]),
                )
                updated += 1
                if abs(pl - old_pl) > 0.05:
                    loss_fixed += 1
                if new_best and new_best != old_best:
                    best_fixed += 1
        if updated:
            self.conn.commit()
        return {
            "updated": updated,
            "loss_fixed": loss_fixed,
            "best_fixed": best_fixed,
            "total": len(rows),
        }

    def dashboard_drilldown(self) -> dict[str, list[dict[str, Any]]]:
        """Stats agrégées avec liens vers les parties concernées."""
        out: dict[str, list[dict[str, Any]]] = {}
        total_errors = int(
            self.conn.execute(
                """
                SELECT COUNT(*) AS c FROM moves
                WHERE point_loss > 0 AND severity != 'ok'
                """
            ).fetchone()["c"] or 0
        )
        for key, col in (
            ("themes", "theme"),
            ("phases", "phase"),
            ("severities", "severity"),
        ):
            rows = self.conn.execute(
                f"""
                SELECT {col} AS label, COUNT(*) AS cnt,
                       AVG(point_loss) AS avg_loss, MAX(point_loss) AS max_loss
                FROM moves
                WHERE point_loss > 0 AND severity != 'ok' AND {col} IS NOT NULL
                GROUP BY {col}
                ORDER BY cnt DESC
                """
            ).fetchall()
            items = []
            for r in rows:
                games = self.conn.execute(
                    f"""
                    SELECT DISTINCT g.id, g.opponent, MAX(m.point_loss) AS worst
                    FROM moves m
                    JOIN games g ON g.id = m.game_id
                    WHERE m.{col} = ? AND m.severity != 'ok'
                    GROUP BY g.id
                    ORDER BY worst DESC
                    LIMIT 5
                    """,
                    (r["label"],),
                ).fetchall()
                cnt = int(r["cnt"] or 0)
                pct = round(100 * cnt / total_errors, 1) if total_errors else 0.0
                items.append({
                    "label": r["label"],
                    "cnt": cnt,
                    "pct": pct,
                    "avg_loss": r["avg_loss"],
                    "max_loss": r["max_loss"],
                    "games": [dict(g) for g in games],
                })
            out[key] = items
        out["total_errors"] = total_errors
        return out

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

    def update_game_ranks(
        self, game_id: int, player_rank: str, opponent_rank: str
    ) -> None:
        self.conn.execute(
            "UPDATE games SET player_rank = ?, opponent_rank = ? WHERE id = ?",
            (player_rank or None, opponent_rank or None, game_id),
        )
        self.conn.commit()

    def update_game_timing(
        self,
        game_id: int,
        time_control: str | None,
        duration_sec: int | None,
        avg_move_sec: float | None,
        moves_per_min: float | None,
        speed: str | None = None,
        duration_estimated: bool = False,
    ) -> None:
        self.conn.execute(
            """
            UPDATE games SET time_control = ?, duration_sec = ?,
                avg_move_sec = ?, moves_per_min = ?,
                speed = ?, duration_estimated = ?
            WHERE id = ?
            """,
            (
                time_control,
                duration_sec,
                avg_move_sec,
                moves_per_min,
                speed,
                1 if duration_estimated else 0,
                game_id,
            ),
        )
        self.conn.commit()

    def timing_stats(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT speed AS label, COUNT(*) AS cnt,
                   AVG(duration_sec) AS avg_duration,
                   AVG(moves_per_min) AS avg_mpm
            FROM games
            WHERE analyzed_quick = 1 AND speed IS NOT NULL
            GROUP BY speed
            ORDER BY cnt DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def opening_error_summary(
        self,
        player: str,
        *,
        max_move: int = 30,
        min_loss: float = 0.5,
    ) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN m.severity = 'inaccuracy' THEN 1 ELSE 0 END) AS inaccuracy,
                SUM(CASE WHEN m.severity = 'mistake' THEN 1 ELSE 0 END) AS mistake,
                SUM(CASE WHEN m.severity = 'blunder' THEN 1 ELSE 0 END) AS blunder,
                SUM(CASE WHEN m.severity = 'mega_blunder' THEN 1 ELSE 0 END) AS mega_blunder,
                COUNT(DISTINCT m.game_id) AS games
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.player = ?
              AND m.move_number <= ?
              AND COALESCE(m.point_loss, 0) >= ?
              AND (g.analyzed_quick = 1 OR COALESCE(g.analyzed_opening, 0) = 1)
            """,
            (player, max_move, min_loss),
        ).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    def top_pattern_clusters(
        self, limit: int = 10, kind: str | None = None,
    ) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                """
                SELECT * FROM pattern_clusters
                WHERE COALESCE(pattern_kind, 'pattern') = ?
                ORDER BY move_count DESC, total_point_loss DESC
                LIMIT ?
                """,
                (kind, limit),
            ).fetchall()
        return self.conn.execute(
            """
            SELECT * FROM pattern_clusters
            ORDER BY move_count DESC, total_point_loss DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def count_pattern_clusters(self, kind: str | None = None) -> int:
        if kind:
            return self.conn.execute(
                """
                SELECT COUNT(*) AS c FROM pattern_clusters
                WHERE COALESCE(pattern_kind, 'pattern') = ?
                """,
                (kind,),
            ).fetchone()["c"]
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM pattern_clusters"
        ).fetchone()["c"]

    def pattern_clusters_list(
        self, kind: str, limit: int | None = None,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT * FROM pattern_clusters
            WHERE COALESCE(pattern_kind, 'pattern') = ?
            ORDER BY move_count DESC, total_point_loss DESC
        """
        if limit is not None:
            return self.conn.execute(sql + " LIMIT ?", (kind, limit)).fetchall()
        return self.conn.execute(sql, (kind,)).fetchall()

    def all_pattern_clusters(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                """
                SELECT * FROM pattern_clusters
                WHERE COALESCE(pattern_kind, 'pattern') = ?
                ORDER BY move_count DESC, total_point_loss DESC
                """,
                (kind,),
            ).fetchall()
        return self.conn.execute(
            """
            SELECT * FROM pattern_clusters
            ORDER BY COALESCE(pattern_kind, 'pattern'), move_count DESC,
                     total_point_loss DESC
            """
        ).fetchall()

    def pattern_other_games_count(
        self, cluster_ids: list[int], exclude_game_id: int | None = None,
    ) -> dict[int, int]:
        if not cluster_ids:
            return {}
        placeholders = ", ".join("?" for _ in cluster_ids)
        exclude_sql = ""
        params: list[Any] = list(cluster_ids)
        if exclude_game_id is not None:
            exclude_sql = " AND m.game_id != ?"
            params.append(exclude_game_id)
        rows = self.conn.execute(
            f"""
            SELECT m.pattern_cluster_id AS cluster_id,
                   COUNT(DISTINCT m.game_id) AS other_games
            FROM moves m
            WHERE m.pattern_cluster_id IN ({placeholders}){exclude_sql}
            GROUP BY m.pattern_cluster_id
            """,
            params,
        ).fetchall()
        return {int(r["cluster_id"]): int(r["other_games"] or 0) for r in rows}

    def pattern_recurrence_for_games(
        self, game_ids: list[int],
    ) -> dict[tuple[int, int], int]:
        """(game_id, cluster_id) -> nombre d'autres parties avec la même forme."""
        if not game_ids:
            return {}
        gid_ph = ", ".join("?" for _ in game_ids)
        pairs = self.conn.execute(
            f"""
            SELECT DISTINCT m.game_id, m.pattern_cluster_id AS cluster_id
            FROM moves m
            WHERE m.game_id IN ({gid_ph})
              AND m.pattern_cluster_id IS NOT NULL
            """,
            game_ids,
        ).fetchall()
        if not pairs:
            return {}
        cluster_ids = list({int(p["cluster_id"]) for p in pairs})
        cid_ph = ", ".join("?" for _ in cluster_ids)
        cross_rows = self.conn.execute(
            f"""
            SELECT src.game_id AS source_game, src.cluster_id,
                   COUNT(DISTINCT m.game_id) AS other_games
            FROM (
                SELECT DISTINCT m.game_id, m.pattern_cluster_id AS cluster_id
                FROM moves m
                WHERE m.game_id IN ({gid_ph})
                  AND m.pattern_cluster_id IN ({cid_ph})
            ) src
            JOIN moves m ON m.pattern_cluster_id = src.cluster_id
              AND m.game_id != src.game_id
            GROUP BY src.game_id, src.cluster_id
            """,
            (*game_ids, *cluster_ids),
        ).fetchall()
        return {
            (int(cr["source_game"]), int(cr["cluster_id"])): int(cr["other_games"] or 0)
            for cr in cross_rows
        }

    def fuseki_patterns_for_games(
        self,
        game_ids: list[int],
        *,
        max_move: int = 30,
    ) -> dict[int, list[dict[str, Any]]]:
        if not game_ids:
            return {}
        placeholders = ", ".join("?" for _ in game_ids)
        rows = self.conn.execute(
            f"""
            SELECT m.game_id, m.id AS move_id, m.move_number, m.point_loss,
                   m.severity, m.pattern_cluster_id AS cluster_id,
                   pc.move_count AS cluster_move_count,
                   pc.total_point_loss AS cluster_total_loss,
                   pc.dominant_theme, pc.patch_ascii, pc.sample_json,
                   COALESCE(pc.pattern_kind, 'pattern') AS pattern_kind
            FROM moves m
            JOIN games g ON g.id = m.game_id
            JOIN pattern_clusters pc ON pc.id = m.pattern_cluster_id
            WHERE m.game_id IN ({placeholders})
              AND m.color = g.player_color
              AND m.move_number <= ?
              AND m.pattern_cluster_id IS NOT NULL
              AND COALESCE(pc.pattern_kind, 'pattern') = 'fuseki'
            ORDER BY m.game_id ASC, m.move_number ASC
            """,
            (*game_ids, max_move),
        ).fetchall()
        cluster_ids = list({int(r["cluster_id"]) for r in rows})
        other_by_cluster: dict[tuple[int, int], int] = {}
        if cluster_ids:
            cid_ph = ", ".join("?" for _ in cluster_ids)
            gid_ph = ", ".join("?" for _ in game_ids)
            cross_rows = self.conn.execute(
                f"""
                SELECT src.game_id AS source_game, src.cluster_id,
                       COUNT(DISTINCT m.game_id) AS other_games
                FROM (
                    SELECT DISTINCT m.game_id, m.pattern_cluster_id AS cluster_id
                    FROM moves m
                    WHERE m.game_id IN ({gid_ph})
                      AND m.pattern_cluster_id IN ({cid_ph})
                ) src
                JOIN moves m ON m.pattern_cluster_id = src.cluster_id
                  AND m.game_id != src.game_id
                GROUP BY src.game_id, src.cluster_id
                """,
                (*game_ids, *cluster_ids),
            ).fetchall()
            for cr in cross_rows:
                other_by_cluster[
                    (int(cr["source_game"]), int(cr["cluster_id"]))
                ] = int(cr["other_games"] or 0)
        out: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            gid = int(r["game_id"])
            cid = int(r["cluster_id"])
            item = dict(r)
            item["other_games"] = other_by_cluster.get((gid, cid), 0)
            out[gid].append(item)
        return dict(out)

    def opponent_suspicion_stats(
        self, *, min_games: int = 2,
    ) -> list[dict[str, Any]]:
        from .cheater_alert import enrich_opponent_row

        rows = self.conn.execute(
            """
            SELECT g.opponent,
                   MAX(g.opponent_rank) AS opponent_rank,
                   COUNT(DISTINCT g.id) AS games,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color THEN 1 ELSE 0 END) AS opp_moves,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color
                        AND m.played_move IS NOT NULL AND m.best_move IS NOT NULL
                        AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move))
                        THEN 1 ELSE 0 END) AS opp_ia,
                   SUM(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color
                        AND m.severity IN ('blunder', 'mega_blunder')
                        THEN 1 ELSE 0 END) AS opp_blunders,
                   AVG(CASE WHEN m.color IS NOT NULL AND g.player_color IS NOT NULL
                        AND m.color != g.player_color AND m.point_loss > 0
                        THEN m.point_loss END) AS opp_avg_loss
            FROM games g
            LEFT JOIN moves m ON m.game_id = g.id
            WHERE g.analyzed_quick = 1 AND g.opponent IS NOT NULL AND g.opponent != ''
            GROUP BY g.opponent
            HAVING games >= ?
            """,
            (min_games,),
        ).fetchall()
        out = [enrich_opponent_row(dict(r)) for r in rows]
        out.sort(
            key=lambda x: (-x["suspicion_score"], -x["ia_pct"], -x["games"]),
        )
        return out

    def suspicious_opponent_moves(
        self,
        *,
        opponent: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Coups adverses = coup IA KataGo (signal le plus fort)."""
        opp_sql, opp_params = "", []
        if opponent:
            opp_sql = " AND g.opponent = ?"
            opp_params.append(opponent)
        rows = self.conn.execute(
            f"""
            SELECT m.id AS move_id, m.game_id, m.move_number, m.played_move,
                   m.best_move, m.point_loss, m.severity, m.phase,
                   g.opponent, g.opponent_rank, g.year, g.month, g.result,
                   g.player_color
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE g.analyzed_quick = 1
              AND g.player_color IS NOT NULL
              AND m.color != g.player_color
              AND m.played_move IS NOT NULL AND m.best_move IS NOT NULL
              AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move))
              {opp_sql}
            ORDER BY m.move_number DESC, m.id DESC
            LIMIT ?
            """,
            (*opp_params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_suspicious_opponents(self, *, min_score: float = 50.0) -> int:
        return sum(
            1 for o in self.opponent_suspicion_stats()
            if o["suspicion_score"] >= min_score
        )

    def player_move_quality_stats(self) -> dict[str, Any]:
        """Mêmes métriques que opponent_suspicion_stats, pour vos coups (comparaison alerte)."""
        from .cheater_alert import enrich_opponent_row

        row = self.conn.execute(
            """
            SELECT
                COUNT(DISTINCT g.id) AS games,
                SUM(CASE WHEN m.color = g.player_color THEN 1 ELSE 0 END) AS moves,
                SUM(CASE WHEN m.color = g.player_color
                    AND m.played_move IS NOT NULL AND m.best_move IS NOT NULL
                    AND UPPER(TRIM(m.played_move)) = UPPER(TRIM(m.best_move))
                    THEN 1 ELSE 0 END) AS ia_moves,
                SUM(CASE WHEN m.color = g.player_color
                    AND m.severity IN ('blunder', 'mega_blunder')
                    THEN 1 ELSE 0 END) AS blunders,
                AVG(CASE WHEN m.color = g.player_color AND m.point_loss > 0
                    THEN m.point_loss END) AS avg_loss
            FROM games g
            LEFT JOIN moves m ON m.game_id = g.id
            WHERE g.analyzed_quick = 1
            """,
        ).fetchone()
        payload = {
            "opponent": "Vous",
            "opponent_rank": "—",
            "games": int(row["games"] or 0),
            "opp_moves": int(row["moves"] or 0),
            "opp_ia": int(row["ia_moves"] or 0),
            "opp_blunders": int(row["blunders"] or 0),
            "opp_avg_loss": row["avg_loss"],
        }
        return enrich_opponent_row(payload)

    def filter_metadata(self) -> dict[str, list[Any]]:
        opponents = self.conn.execute(
            """
            SELECT DISTINCT opponent FROM games
            WHERE analyzed_quick = 1 AND opponent IS NOT NULL
            ORDER BY opponent
            """
        ).fetchall()
        years = self.conn.execute(
            """
            SELECT DISTINCT year FROM games
            WHERE analyzed_quick = 1 AND year IS NOT NULL
            ORDER BY year DESC
            """
        ).fetchall()
        return {
            "opponents": [r["opponent"] for r in opponents],
            "years": [int(r["year"]) for r in years],
        }

    def _outcome_game_ids(self, outcome: str) -> list[int]:
        from .sgf_parse import game_outcome
        rows = self.conn.execute(
            "SELECT id, result, player_color FROM games WHERE analyzed_quick = 1"
        ).fetchall()
        return [
            int(r["id"]) for r in rows
            if game_outcome(r["result"], r["player_color"]) == outcome
        ]

    def _move_filter_sql(
        self,
        *,
        opponent: str | None = None,
        year: int | None = None,
        player_color: str | None = None,
        error_scope: str | None = None,
        fuseki_max_move: int = 30,
        game_ids: list[int] | None = None,
        game_alias: str = "g",
        move_alias: str = "m",
    ) -> tuple[str, list[Any]]:
        parts: list[str] = []
        params: list[Any] = []
        if opponent:
            parts.append(f"{game_alias}.opponent = ?")
            params.append(opponent)
        if year is not None:
            parts.append(f"{game_alias}.year = ?")
            params.append(year)
        if player_color:
            parts.append(f"{game_alias}.player_color = ?")
            params.append(player_color)
        if game_ids:
            placeholders = ", ".join("?" for _ in game_ids)
            parts.append(f"{game_alias}.id IN ({placeholders})")
            params.extend(game_ids)
        scope = (error_scope or "all").lower()
        if scope == "fuseki":
            parts.append(f"{move_alias}.move_number <= ?")
            params.append(fuseki_max_move)
        elif scope == "joseki":
            parts.append(
                f"COALESCE(pc.pattern_kind, 'pattern') = 'joseki'"
            )
        elif scope == "opening":
            parts.append(f"{move_alias}.move_number <= ?")
            params.append(fuseki_max_move)
        if not parts:
            return "", []
        return " AND " + " AND ".join(parts), params

    def dashboard_drilldown_filtered(
        self,
        *,
        opponent: str | None = None,
        year: int | None = None,
        player_color: str | None = None,
        outcome: str | None = None,
        error_scope: str | None = None,
        fuseki_max_move: int = 30,
    ) -> dict[str, list[dict[str, Any]]]:
        scope = (error_scope or "all").lower()
        join_pc = scope == "joseki"
        pc_join = (
            " JOIN pattern_clusters pc ON pc.id = m.pattern_cluster_id"
            if join_pc else ""
        )
        game_ids: list[int] | None = None
        if outcome in ("win", "loss", "draw"):
            game_ids = self._outcome_game_ids(outcome)
            if not game_ids:
                return {
                    "themes": [], "phases": [], "severities": [],
                    "total_errors": 0,
                }
        filt_sql, filt_params = self._move_filter_sql(
            opponent=opponent,
            year=year,
            player_color=player_color,
            error_scope=error_scope,
            fuseki_max_move=fuseki_max_move,
            game_ids=game_ids,
        )
        out: dict[str, list[dict[str, Any]]] = {}
        total_errors = self.conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM moves m
            JOIN games g ON g.id = m.game_id{pc_join}
            WHERE m.point_loss > 0 AND m.severity != 'ok'{filt_sql}
            """,
            filt_params,
        ).fetchone()["c"]
        total_errors = int(total_errors or 0)
        for key, col in (
            ("themes", "theme"),
            ("phases", "phase"),
            ("severities", "severity"),
        ):
            rows = self.conn.execute(
                f"""
                SELECT {col} AS label, COUNT(*) AS cnt,
                       AVG(m.point_loss) AS avg_loss, MAX(m.point_loss) AS max_loss
                FROM moves m
                JOIN games g ON g.id = m.game_id{pc_join}
                WHERE m.point_loss > 0 AND m.severity != 'ok'
                  AND m.{col} IS NOT NULL{filt_sql}
                GROUP BY m.{col}
                ORDER BY cnt DESC
                """,
                filt_params,
            ).fetchall()
            items = []
            for r in rows:
                cnt = int(r["cnt"] or 0)
                pct = round(100 * cnt / total_errors, 1) if total_errors else 0.0
                games = self.conn.execute(
                    f"""
                    SELECT DISTINCT g.id, g.opponent, MAX(m.point_loss) AS worst
                    FROM moves m
                    JOIN games g ON g.id = m.game_id{pc_join}
                    WHERE m.{col} = ? AND m.severity != 'ok'{filt_sql}
                    GROUP BY g.id
                    ORDER BY worst DESC
                    LIMIT 5
                    """,
                    (r["label"], *filt_params),
                ).fetchall()
                items.append({
                    "label": r["label"],
                    "cnt": cnt,
                    "pct": pct,
                    "avg_loss": r["avg_loss"],
                    "max_loss": r["max_loss"],
                    "games": [dict(g) for g in games],
                })
            out[key] = items
        out["total_errors"] = total_errors
        return out

    def stats_enhanced(
        self,
        player: str,
        *,
        fuseki_max_move: int = 30,
        min_opening_loss: float = 0.5,
    ) -> dict[str, Any]:
        base = self.stats_summary()
        opening_prog = self.opening_progress()
        row = self.conn.execute(
            """
            SELECT
                COUNT(DISTINCT g.id) AS games_analyzed,
                SUM(CASE WHEN m.color = g.player_color THEN 1 ELSE 0 END) AS player_moves,
                SUM(CASE WHEN m.color = g.player_color AND m.severity != 'ok'
                    THEN 1 ELSE 0 END) AS player_errors,
                SUM(CASE WHEN m.color = g.player_color AND m.move_number <= ?
                    THEN 1 ELSE 0 END) AS opening_moves,
                SUM(CASE WHEN m.color = g.player_color AND m.move_number <= ?
                    AND m.severity != 'ok' THEN 1 ELSE 0 END) AS opening_error_moves,
                SUM(CASE WHEN m.color = g.player_color AND m.severity IN
                    ('blunder', 'mega_blunder') THEN 1 ELSE 0 END) AS player_blunders,
                COUNT(DISTINCT CASE WHEN m.color = g.player_color
                    AND m.severity IN ('blunder', 'mega_blunder') THEN g.id END)
                    AS games_with_blunder,
                SUM(CASE WHEN m.color = g.player_color
                    AND COALESCE(pc.pattern_kind, '') = 'joseki' THEN 1 ELSE 0 END) AS joseki_moves,
                SUM(CASE WHEN m.color = g.player_color AND m.severity != 'ok'
                    AND COALESCE(pc.pattern_kind, '') = 'joseki' THEN 1 ELSE 0 END) AS joseki_error_moves,
                COUNT(DISTINCT CASE WHEN m.color = g.player_color AND m.severity != 'ok'
                    AND COALESCE(pc.pattern_kind, '') = 'joseki' THEN g.id END)
                    AS games_joseki_errors
            FROM games g
            LEFT JOIN moves m ON m.game_id = g.id
            LEFT JOIN pattern_clusters pc ON pc.id = m.pattern_cluster_id
            WHERE g.analyzed_quick = 1
            """,
            (fuseki_max_move, fuseki_max_move),
        ).fetchone()
        opening_summary = self.opening_error_summary(
            player, max_move=fuseki_max_move, min_loss=min_opening_loss,
        )
        games_analyzed = int(row["games_analyzed"] or 0)
        player_moves = int(row["player_moves"] or 0)
        player_errors = int(row["player_errors"] or 0)
        opening_moves = int(row["opening_moves"] or 0)
        opening_error_moves = int(row["opening_error_moves"] or 0)
        player_blunders = int(row["player_blunders"] or 0)
        games_with_blunder = int(row["games_with_blunder"] or 0)
        joseki_moves = int(row["joseki_moves"] or 0)
        joseki_error_moves = int(row["joseki_error_moves"] or 0)
        games_joseki_errors = int(row["games_joseki_errors"] or 0)
        games_fuseki_errors = int(opening_summary.get("games", 0))
        fuseki_clusters = self.count_pattern_clusters("fuseki")
        joseki_clusters = self.count_pattern_clusters("joseki")
        pct = lambda n, d: round(100 * n / d, 1) if d else 0.0
        games_total = int(base["games_total"] or 0)
        return {
            **base,
            "opening_done": opening_prog["done"],
            "opening_total": opening_prog["total"],
            "games_analyzed": games_analyzed,
            "pct_analyzed": pct(games_analyzed, games_total),
            "pct_opening_done": pct(opening_prog["done"], opening_prog["total"]),
            "pct_error_moves": pct(player_errors, player_moves),
            "pct_opening_error_moves": pct(opening_error_moves, opening_moves),
            "pct_games_fuseki_errors": pct(games_fuseki_errors, games_analyzed),
            "pct_games_with_blunder": pct(games_with_blunder, games_analyzed),
            "pct_blunders_of_errors": pct(player_blunders, player_errors),
            "opening_moves": opening_moves,
            "opening_error_moves": opening_error_moves,
            "games_fuseki_errors": games_fuseki_errors,
            "fuseki_clusters": fuseki_clusters,
            "joseki_clusters": joseki_clusters,
            "joseki_moves": joseki_moves,
            "joseki_error_moves": joseki_error_moves,
            "games_joseki_errors": games_joseki_errors,
            "pct_joseki_error_moves": pct(joseki_error_moves, joseki_moves),
            "pct_games_joseki_errors": pct(games_joseki_errors, games_analyzed),
            "player_moves": player_moves,
            "player_errors": player_errors,
            "player_blunders": player_blunders,
            "games_with_blunder": games_with_blunder,
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

    def export_opening_json(
        self, game_id: int, max_moves: int, out_path: Path,
    ) -> None:
        moves = self.conn.execute(
            """
            SELECT * FROM moves
            WHERE game_id = ? AND move_number <= ?
            ORDER BY move_number
            """,
            (game_id, max_moves),
        ).fetchall()
        game = self.conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        payload = {
            "game": dict(game) if game else {},
            "max_moves": max_moves,
            "moves": [dict(m) for m in moves],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    _MOVE_RESTORE_COLS = (
        "game_id", "move_number", "color", "coord", "player",
        "score_before", "score_after", "point_loss", "line_loss",
        "winrate_before", "winrate_after", "best_move", "played_move",
        "severity", "phase", "theme",
        "human_prior_json", "study_priority", "stretch_score", "stretch_top_move",
        "top_moves_json", "move_scores_json", "review_sgf_path", "pattern_hash",
        "pattern_cluster_id", "pro_context_json", "human_rank_moves_json",
    )

    def restore_game_from_analysis_json(self, path: Path) -> bool:
        """Réimporte une partie depuis un export JSON (sauvegarde disque)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        game = data.get("game") or {}
        game_id = game.get("id")
        if not game_id:
            return False
        if not self.conn.execute(
            "SELECT id FROM games WHERE id = ?", (game_id,),
        ).fetchone():
            return False

        self.conn.execute("DELETE FROM moves WHERE game_id = ?", (game_id,))
        cols = self._MOVE_RESTORE_COLS
        placeholders = ", ".join("?" for _ in cols)
        upd = ", ".join(f"{c}=excluded.{c}" for c in cols[2:])
        for move in data.get("moves") or []:
            row = {c: move.get(c) for c in cols}
            row["game_id"] = game_id
            values = [row.get(c) for c in cols]
            self.conn.execute(
                f"INSERT INTO moves ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(game_id, move_number) DO UPDATE SET {upd}",
                values,
            )

        self.conn.execute(
            """
            UPDATE games SET
                analyzed_quick = 1,
                analyzed_deep = 1,
                player_rank = COALESCE(?, player_rank),
                opponent_rank = COALESCE(?, opponent_rank),
                human_sl_game_json = COALESCE(?, human_sl_game_json)
            WHERE id = ?
            """,
            (
                game.get("player_rank"),
                game.get("opponent_rank"),
                game.get("human_sl_game_json"),
                game_id,
            ),
        )
        self.conn.commit()
        return True

    def restore_analysis_exports(
        self,
        analysis_dir: Path,
        *,
        game_id: int | None = None,
    ) -> tuple[int, int]:
        """Restaure les exports game_*_deep.json dans la base."""
        paths = sorted(analysis_dir.glob("game_*_deep.json"))
        if game_id is not None:
            paths = [p for p in paths if p.stem == f"game_{game_id}_deep"]
        restored = 0
        skipped = 0
        for path in paths:
            if self.restore_game_from_analysis_json(path):
                restored += 1
            else:
                skipped += 1
        return restored, skipped

    def reanalysis_progress(
        self, since_year: int | None = None, *, min_game_id: int | None = None,
    ) -> dict[str, int]:
        year_sql, year_params = self._since_year_sql(since_year, alias=None)
        id_sql, id_params = "", []
        if min_game_id is not None:
            id_sql = " AND id >= ?"
            id_params = [min_game_id]
        row = self.conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN analyzed_deep = 1 THEN 1 ELSE 0 END) AS done
            FROM games
            WHERE 1=1{year_sql}{id_sql}
            """,
            (*year_params, *id_params),
        ).fetchone()
        total = int(row["total"] or 0)
        done = int(row["done"] or 0)
        return {
            "total": total,
            "done": done,
            "pending": total - done,
        }
