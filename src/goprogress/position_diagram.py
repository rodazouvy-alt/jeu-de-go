from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .board_diagram import full_board_svg
from .config import resolve_path
from .db import Database
from .human_sl import RANK_ORDER
from .local_pattern import PATCH_RADIUS
from .rank_colors import (
    human_consensus_label,
    rank_diagram_label,
    ranks_by_gtp,
)
from .sgf_parse import parse_sgf, player_color, game_outcome, katago_score_for_player, katago_scores_by_move
from .themes import build_board_before_move, gtp_to_rowcol, patch_center


def _patch_cells(center_row: int, center_col: int, radius: int, size: int) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            r, c = center_row + dr, center_col + dc
            if 0 <= r < size and 0 <= c < size:
                cells.add((r, c))
    return cells


def _gtp_rc(gtp: str | None, size: int) -> tuple[int, int] | None:
    if not gtp or gtp.lower() == "pass":
        return None
    return gtp_to_rowcol(gtp.upper(), size)


def _katago_scores_for_move(
    move: dict[str, Any],
    player_color: str | None,
) -> dict[str, float]:
    return katago_scores_by_move(move, player_color)


def _build_legend_context(
    move: dict[str, Any],
    *,
    player_color: str | None,
    played: str | None,
    best: str | None,
    ranks_same_as_played: list[str],
    human_ranks: dict[str, str] | None,
) -> dict[str, Any]:
    scores = _katago_scores_for_move(move, player_color)
    played_u = (played or "").upper().strip()
    best_u = (best or "").upper().strip()
    if best_u and best_u not in scores and move.get("top_moves_json"):
        try:
            tops = json.loads(move["top_moves_json"])
            if tops:
                sl = katago_score_for_player(tops[0].get("scoreLead"), player_color)
                if sl is not None:
                    best_u = (tops[0].get("move") or best or "").upper().strip()
                    scores[best_u] = sl
        except (json.JSONDecodeError, TypeError):
            pass

    top5: list[tuple[str, float | None]] = []
    if move.get("top_moves_json"):
        try:
            for item in json.loads(move["top_moves_json"])[:5]:
                gtp = item.get("move") or "?"
                sl = katago_score_for_player(item.get("scoreLead"), player_color)
                top5.append((gtp, sl))
        except (json.JSONDecodeError, TypeError):
            pass

    human_lines: list[dict[str, Any]] = []
    if human_ranks:
        by_move: dict[str, list[str]] = defaultdict(list)
        for rank_key in RANK_ORDER:
            if rank_key not in human_ranks:
                continue
            gtp = human_ranks[rank_key]
            by_move[gtp.upper().strip()].append(rank_diagram_label(rank_key))
        for gtp, labels in sorted(by_move.items()):
            if played_u and gtp == played_u:
                continue
            display_gtp = next(
                (
                    human_ranks[k]
                    for k in RANK_ORDER
                    if k in human_ranks and human_ranks[k].upper().strip() == gtp
                ),
                gtp,
            )
            human_lines.append({
                "labels": "·".join(
                    sorted(labels, key=lambda x: int(x) if x.isdigit() else 99),
                ),
                "move": display_gtp,
                "score": scores.get(gtp),
            })

    return {
        "played": played or "?",
        "best": best or "?",
        "scores": scores,
        "point_loss": move.get("point_loss"),
        "score_before": katago_score_for_player(move.get("score_before"), player_color),
        "ranks_same": ranks_same_as_played,
        "consensus_note": human_consensus_label(ranks_same_as_played, human_ranks),
        "human_lines": human_lines,
        "top5": top5,
    }


def _diagram_focus_rc(
    row: int | None,
    col: int | None,
    *,
    move: dict[str, Any],
    size: int,
) -> tuple[int, int]:
    """Point d'ancrage du zoom : coup joué, ou meilleur coup IA si passe."""
    if row is not None and col is not None:
        return row, col
    if move.get("top_moves_json"):
        try:
            tops = json.loads(move["top_moves_json"])
            if tops:
                rc = _gtp_rc(tops[0].get("move"), size)
                if rc:
                    return rc
        except (json.JSONDecodeError, TypeError):
            pass
    if move.get("best_move"):
        rc = _gtp_rc(move["best_move"], size)
        if rc:
            return rc
    mid = size // 2
    return mid, mid


def _bbox_cells(cells: set[tuple[int, int]], *, pad: int = 1, size: int = 19) -> set[tuple[int, int]]:
    """Boîte englobante (intersections) autour des cellules marquées."""
    if not cells:
        return set()
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    r0, r1 = max(0, min(rows) - pad), min(size - 1, max(rows) + pad)
    c0, c1 = max(0, min(cols) - pad), min(size - 1, max(cols) + pad)
    return {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}


def svg_for_move_row(
    move: dict[str, Any],
    *,
    game_player_color: str | None = None,
    sgf_path: str | None = None,
    game_result: str | None = None,
    cell: int = 13,
) -> str:
    """Diagramme plateau complet pour un coup (blunder / erreur)."""
    if not sgf_path:
        return ""
    path = resolve_path(sgf_path)
    if not path.exists():
        return ""

    try:
        parsed = parse_sgf(path)
    except Exception:
        return ""

    move_number = int(move["move_number"])
    idx = move_number - 1
    built = build_board_before_move(
        parsed.moves, idx, parsed.board_size,
        initial_stones=parsed.initial_stones,
    )
    if not built:
        return ""

    board, to_play, play_row, play_col = built
    size = parsed.board_size
    theme = move.get("theme") or "other"
    focus_row, focus_col = _diagram_focus_rc(play_row, play_col, move=move, size=size)
    center = patch_center(board, to_play, focus_row, focus_col, theme)

    you_color = game_player_color or player_color(parsed, move.get("player") or "")
    played_rc = _gtp_rc(move.get("played_move") or move.get("coord"), size)

    human_ranks_raw: dict[str, str] | None = None
    if move.get("human_rank_moves_json"):
        try:
            human_ranks_raw = json.loads(move["human_rank_moves_json"])
        except (json.JSONDecodeError, TypeError):
            human_ranks_raw = None
    gtp_ranks = ranks_by_gtp(human_ranks_raw)

    katago_marks: list[tuple[int, int, str, list[str]]] = []
    mark_cells: set[tuple[int, int]] = set()
    if played_rc:
        mark_cells.add(played_rc)
    if move.get("top_moves_json"):
        try:
            for i, item in enumerate(json.loads(move["top_moves_json"])[:5], 1):
                gtp = (item.get("move") or "").upper().strip()
                rc = _gtp_rc(gtp, size)
                if not rc:
                    continue
                mark_cells.add(rc)
                labels = gtp_ranks.get(gtp, [])
                katago_marks.append((*rc, f"IA{i}", labels))
        except (json.JSONDecodeError, TypeError):
            pass
    if not katago_marks and move.get("best_move"):
        gtp = (move.get("best_move") or "").upper().strip()
        rc = _gtp_rc(gtp, size)
        if rc:
            mark_cells.add(rc)
            labels = gtp_ranks.get(gtp, [])
            katago_marks.append((*rc, "IA1", labels))

    rank_by_cell: dict[tuple[int, int], list[str]] = defaultdict(list)
    ranks_same_as_played: list[str] = []
    if human_ranks_raw:
        try:
            for rank_key in RANK_ORDER:
                if rank_key not in human_ranks_raw:
                    continue
                gtp = human_ranks_raw[rank_key]
                rc = _gtp_rc(gtp, size)
                if not rc:
                    continue
                mark_cells.add(rc)
                label = rank_diagram_label(rank_key)
                if played_rc and rc == played_rc:
                    ranks_same_as_played.append(label)
                    continue
                rank_by_cell[rc].append(label)
        except (json.JSONDecodeError, TypeError):
            pass

    highlight = _bbox_cells(
        _patch_cells(center[0], center[1], PATCH_RADIUS, size) | mark_cells,
        size=size,
    )

    rank_marks: list[tuple[int, int, list[str]]] = []
    katago_cells = {(r, c) for r, c, _, _ in katago_marks}
    for rc, labels in rank_by_cell.items():
        r, c = rc
        if rc in katago_cells:
            # Pourtour coloré déjà sur la pastille IA
            continue
        rank_marks.append((r, c, labels))

    outcome = game_outcome(game_result, you_color)

    legend_context = _build_legend_context(
        move,
        player_color=you_color,
        played=move.get("played_move") or move.get("coord"),
        best=move.get("best_move"),
        ranks_same_as_played=ranks_same_as_played,
        human_ranks=human_ranks_raw,
    )

    return full_board_svg(
        board,
        size=size,
        highlight=highlight,
        played=played_rc,
        katago_marks=katago_marks,
        rank_marks=rank_marks,
        ranks_same_as_played=ranks_same_as_played,
        legend_context=legend_context,
        player_color=you_color,
        game_outcome=outcome,
        cell=cell,
    )


def svg_for_move_id(db: Database, move_id: int, *, cell: int = 13) -> str:
    row = db.conn.execute(
        """
        SELECT m.*, g.sgf_path, g.player_color, g.result, g.board_size
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.id = ?
        """,
        (move_id,),
    ).fetchone()
    if not row:
        return ""
    return svg_for_move_row(
        dict(row),
        game_player_color=row["player_color"],
        sgf_path=row["sgf_path"],
        game_result=row["result"],
        cell=cell,
    )


_svg_cache: dict[tuple[int, float, int], str] = {}


def svg_for_move_cached(db: Database, move_id: int, *, cell: int = 13) -> str:
    row = db.conn.execute(
        """
        SELECT m.*, g.sgf_path, g.player_color, g.result
        FROM moves m JOIN games g ON g.id = m.game_id WHERE m.id = ?
        """,
        (move_id,),
    ).fetchone()
    if not row:
        return ""
    path = resolve_path(row["sgf_path"])
    mtime = path.stat().st_mtime if path.exists() else 0.0
    key = (move_id, mtime, cell, 36)  # v36: légende hauteur/empilement corrigés
    if key in _svg_cache:
        return _svg_cache[key]
    svg = svg_for_move_row(
        dict(row),
        game_player_color=row["player_color"],
        sgf_path=row["sgf_path"],
        game_result=row["result"],
        cell=cell,
    )
    if len(_svg_cache) > 400:
        _svg_cache.clear()
    _svg_cache[key] = svg
    return svg