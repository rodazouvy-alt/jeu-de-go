from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import resolve_path
from .db import Database
from .sgf_parse import parse_sgf, player_rank, opponent_rank
from .sgf_timing import parse_game_timing
from .themes import theme_for_move


def enrich_game_timing(db: Database, game_id: int, sgf_path: Path) -> bool:
    if not sgf_path.exists():
        return False
    try:
        timing = parse_game_timing(sgf_path)
    except Exception:
        return False
    db.update_game_timing(
        game_id,
        timing.get("time_control"),
        timing.get("duration_sec"),
        timing.get("avg_move_sec"),
        timing.get("moves_per_min"),
        timing.get("speed"),
        bool(timing.get("duration_estimated")),
    )
    return True


def enrich_game_result(db: Database, game_id: int, sgf_path: Path) -> bool:
    if not sgf_path.exists():
        return False
    try:
        parsed = parse_sgf(sgf_path)
    except Exception:
        return False
    if not parsed.result:
        return False
    db.update_game_result(game_id, parsed.result)
    return True


def enrich_analyzed_games(
    db: Database, cfg: dict[str, Any], since_year: int | None = None,
) -> tuple[int, int, int, int]:
    """Rangs depuis SGF + thèmes sur coups déjà analysés."""
    username = cfg["player"]["kgs_username"]
    year_sql, year_params = Database._since_year_sql(since_year, alias=None)
    games = db.conn.execute(
        f"SELECT id, sgf_path FROM games WHERE analyzed_quick = 1{year_sql}",
        year_params,
    ).fetchall()

    ranks_done = 0
    themes_done = 0
    timing_done = 0
    results_done = 0
    for g in games:
        sgf_path = resolve_path(g["sgf_path"])
        if not sgf_path.exists():
            continue
        try:
            parsed = parse_sgf(sgf_path)
        except Exception:
            continue

        db.update_game_ranks(
            g["id"],
            player_rank(parsed, username),
            opponent_rank(parsed, username),
        )
        ranks_done += 1
        if enrich_game_timing(db, g["id"], sgf_path):
            timing_done += 1

        moves = db.conn.execute(
            "SELECT move_number FROM moves WHERE game_id = ? ORDER BY move_number",
            (g["id"],),
        ).fetchall()
        for m in moves:
            idx = m["move_number"] - 1
            if idx < 0 or idx >= len(parsed.moves):
                continue
            theme = theme_for_move(
                parsed.moves, idx, parsed.board_size,
                initial_stones=parsed.initial_stones,
            )
            db.conn.execute(
                "UPDATE moves SET theme = ? WHERE game_id = ? AND move_number = ?",
                (theme, g["id"], m["move_number"]),
            )
            themes_done += 1
        db.conn.commit()

    all_games = db.conn.execute(
        f"SELECT id, sgf_path FROM games WHERE 1=1{year_sql}",
        year_params,
    ).fetchall()
    for g in all_games:
        sgf_path = resolve_path(g["sgf_path"])
        if enrich_game_result(db, g["id"], sgf_path):
            results_done += 1

    return ranks_done, themes_done, timing_done, results_done
