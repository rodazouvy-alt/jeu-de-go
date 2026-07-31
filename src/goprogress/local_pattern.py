from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sgfmill.boards import Board

from .config import resolve_path
from .db import Database
from .sgf_parse import parse_sgf
from .themes import build_board_before_move, gtp_to_rowcol, patch_center

PATCH_RADIUS = 7
FUSEKI_MAX_MOVE = 30
JOSEKI_CORNER_MARGIN = 6
JOSEKI_MIN_REGION_RATIO = 0.6
FUSEKI_MIN_EARLY_RATIO = 0.5
JOSEKI_LOCAL_THEMES = frozenset({"corner_opening", "fight", "invasion", "reduction"})

CORNER_LABELS = {
    "tl": "Coin haut-gauche",
    "tr": "Coin haut-droite",
    "bl": "Coin bas-gauche",
    "br": "Coin bas-droite",
}
PATCH_SIZE = PATCH_RADIUS * 2 + 1
OFF_BOARD = "#"
EMPTY = "."
CENTER = "*"


def extract_patch(
    board: Board,
    center_row: int,
    center_col: int,
    radius: int = PATCH_RADIUS,
) -> list[list[str]]:
    size = board.side
    patch: list[list[str]] = []
    for dr in range(-radius, radius + 1):
        row_cells: list[str] = []
        for dc in range(-radius, radius + 1):
            if dr == 0 and dc == 0:
                row_cells.append(CENTER)
                continue
            r, c = center_row + dr, center_col + dc
            if 0 <= r < size and 0 <= c < size:
                stone = board.get(r, c)
                row_cells.append(stone if stone else EMPTY)
            else:
                row_cells.append(OFF_BOARD)
        patch.append(row_cells)
    return patch


def _color_relative(patch: list[list[str]], to_play: str) -> list[list[str]]:
    me, opp = "b", "w"
    if to_play.lower() == "w":
        me, opp = "w", "b"
    out: list[list[str]] = []
    for row in patch:
        rel: list[str] = []
        for cell in row:
            if cell == me:
                rel.append("o")
            elif cell == opp:
                rel.append("x")
            else:
                rel.append(cell)
        out.append(rel)
    return out


def _rotate_patch(patch: list[list[str]]) -> list[list[str]]:
    n = len(patch)
    return [[patch[n - 1 - c][r] for c in range(n)] for r in range(n)]


def _mirror_patch(patch: list[list[str]]) -> list[list[str]]:
    return [list(reversed(row)) for row in patch]


def _patch_key(patch: list[list[str]]) -> str:
    return "/".join("".join(row) for row in patch)


def canonical_hash(patch: list[list[str]], to_play: str) -> str:
    """Hash canonique avec 8 symétries (4 rotations × miroir)."""
    rel = _color_relative(patch, to_play)
    variants: list[str] = []
    current = rel
    for mirror in (False, True):
        work = _mirror_patch(current) if mirror else current
        for _ in range(4):
            variants.append(_patch_key(work))
            work = _rotate_patch(work)
        if not mirror:
            current = work
    return min(variants)


def patch_to_ascii(patch: list[list[str]]) -> str:
    display = {EMPTY: ".", OFF_BOARD: "+", CENTER: "*", "b": "●", "w": "○", "o": "●", "x": "○"}
    lines = []
    for row in patch:
        lines.append("".join(display.get(c, c) for c in row))
    return "\n".join(lines)


def fuzzy_stone_match(
    patch_a: list[list[str]],
    patch_b: list[list[str]],
    to_play: str,
) -> int:
    """Nombre de pierres (o/x) identiques entre deux patches normalisés."""
    rel_a = _color_relative(patch_a, to_play)
    rel_b = _color_relative(patch_b, to_play)
    n = len(rel_a)
    score = 0
    for r in range(n):
        for c in range(n):
            ca, cb = rel_a[r][c], rel_b[r][c]
            if ca in ("o", "x") and ca == cb:
                score += 1
    return score


def _stone_count(patch: list[list[str]], to_play: str) -> int:
    rel = _color_relative(patch, to_play)
    return sum(1 for row in rel for c in row if c in ("o", "x"))


def corner_region(row: int, col: int, size: int, margin: int = JOSEKI_CORNER_MARGIN) -> str | None:
    """Quadrant coin si le coup est dans la zone locale (joseki)."""
    top = row < margin
    bottom = row >= size - margin
    left = col < margin
    right = col >= size - margin
    if top and left:
        return "tl"
    if top and right:
        return "tr"
    if bottom and left:
        return "bl"
    if bottom and right:
        return "br"
    return None


def classify_cluster_kind(
    items: list[dict[str, Any]],
    *,
    fuseki_max_move: int = FUSEKI_MAX_MOVE,
) -> str:
    """
    Classifie un cluster de blunders :
    - joseki : séquences locales répétées au même endroit (coin/côté)
    - fuseki : coups d'ouverture (≈ 1–fuseki_max_move), cadre de partie
    - pattern : formes récurrentes générales
    """
    if not items:
        return "pattern"
    n = len(items)
    early = sum(
        1 for i in items if int(i.get("move_number") or 0) <= fuseki_max_move
    )
    early_ratio = early / n

    corners: list[str] = []
    for item in items:
        row, col = item.get("coord_row"), item.get("coord_col")
        size = int(item.get("board_size") or 19)
        if row is None or col is None:
            continue
        region = corner_region(int(row), int(col), size)
        if region:
            corners.append(region)

    corner_ratio = 0.0
    if corners:
        dominant = max(set(corners), key=corners.count)
        corner_ratio = corners.count(dominant) / n

    local_theme = sum(
        1 for i in items if (i.get("theme") or "other") in JOSEKI_LOCAL_THEMES
    ) / n

    is_joseki = corner_ratio >= JOSEKI_MIN_REGION_RATIO and local_theme >= 0.3
    is_fuseki = early_ratio >= FUSEKI_MIN_EARLY_RATIO

    if is_joseki:
        return "joseki"
    if is_fuseki:
        return "fuseki"
    return "pattern"


def dominant_corner_label(items: list[dict[str, Any]]) -> str | None:
    corners: list[str] = []
    for item in items:
        row, col = item.get("coord_row"), item.get("coord_col")
        size = int(item.get("board_size") or 19)
        if row is None or col is None:
            continue
        region = corner_region(int(row), int(col), size)
        if region:
            corners.append(region)
    if not corners:
        return None
    dominant = max(set(corners), key=corners.count)
    return CORNER_LABELS.get(dominant, dominant)


def _min_point_loss_for_move(move_number: int, cfg: dict[str, Any]) -> float:
    pcfg = cfg.get("patterns", {})
    fuseki_max = int(pcfg.get("fuseki_max_move", FUSEKI_MAX_MOVE))
    if int(move_number) <= fuseki_max:
        return float(pcfg.get("fuseki_min_point_loss", 0.5))
    return float(pcfg.get("min_point_loss", 4.0))


def pattern_candidate_moves(
    db: Database,
    cfg: dict[str, Any],
    player: str | None = None,
    since_year: int | None = None,
) -> list[Any]:
    """Coups éligibles au clustering : seuil bas en ouverture, blunder hors ouverture."""
    year_sql, year_params = Database._since_year_sql(since_year)
    query = f"""
        SELECT m.*, g.sgf_path, g.board_size
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE (g.analyzed_quick = 1 OR COALESCE(g.analyzed_opening, 0) = 1)
          AND m.coord IS NOT NULL
          AND m.point_loss IS NOT NULL{year_sql}
    """
    params: list[Any] = list(year_params)
    if player:
        query += " AND m.player = ?"
        params.append(player)
    query += " ORDER BY m.point_loss DESC"
    rows = db.conn.execute(query, params).fetchall()
    out: list[Any] = []
    for m in rows:
        if (m["point_loss"] or 0) < _min_point_loss_for_move(m["move_number"], cfg):
            continue
        out.append(m)
    return out


def blunder_moves(
    db: Database, player: str | None = None, since_year: int | None = None,
) -> list[Any]:
    year_sql, year_params = Database._since_year_sql(since_year)
    query = f"""
        SELECT m.*, g.sgf_path, g.board_size
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE g.analyzed_quick = 1
          AND m.severity IN ('blunder', 'mega_blunder')
          AND m.coord IS NOT NULL{year_sql}
    """
    params: list[Any] = list(year_params)
    if player:
        query += " AND m.player = ?"
        params.append(player)
    query += " ORDER BY m.point_loss DESC"
    return db.conn.execute(query, params).fetchall()


def extract_blunder_patch(
    sgf_path: Path,
    move_number: int,
    theme: str | None,
    board_size: int = 19,
) -> tuple[list[list[str]], str, str] | None:
    parsed = parse_sgf(sgf_path)
    idx = move_number - 1
    if idx < 0 or idx >= len(parsed.moves):
        return None
    built = build_board_before_move(
        parsed.moves, idx, parsed.board_size,
        initial_stones=parsed.initial_stones,
    )
    if not built:
        return None
    board, color, row, col = built
    if row is None or col is None:
        row = col = parsed.board_size // 2
    center = patch_center(board, color, row, col, theme or "other")
    patch = extract_patch(board, center[0], center[1])
    phash = canonical_hash(patch, color)
    return patch, phash, color


def cluster_patterns(
    db: Database, cfg: dict[str, Any], since_year: int | None = None,
) -> dict[str, int]:
    fuseki_max = int(cfg.get("patterns", {}).get("fuseki_max_move", FUSEKI_MAX_MOVE))
    player = cfg["player"]["kgs_username"]
    moves = pattern_candidate_moves(db, cfg, player=player, since_year=since_year)

    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    processed = 0
    for m in moves:
        sgf_path = resolve_path(m["sgf_path"])
        if not sgf_path.exists():
            continue
        result = extract_blunder_patch(
            sgf_path, m["move_number"], m["theme"], m["board_size"] or 19
        )
        if not result:
            continue
        patch, phash, _color = result
        coord_row = coord_col = None
        if m["coord"]:
            rc = gtp_to_rowcol(m["coord"], m["board_size"] or 19)
            if rc:
                coord_row, coord_col = rc
        by_hash[phash].append({
            "move_id": m["id"],
            "move_number": m["move_number"],
            "point_loss": m["point_loss"] or 0.0,
            "theme": m["theme"],
            "played_move": m["played_move"],
            "best_move": m["best_move"],
            "patch_ascii": patch_to_ascii(patch),
            "coord_row": coord_row,
            "coord_col": coord_col,
            "board_size": m["board_size"] or 19,
        })
        processed += 1

    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute("DELETE FROM pattern_clusters")
    db.conn.execute(
        "UPDATE moves SET pattern_hash = NULL, pattern_cluster_id = NULL"
    )

    cluster_map: dict[str, int] = {}
    for phash, items in by_hash.items():
        total_loss = sum(i["point_loss"] for i in items)
        themes: dict[str, int] = defaultdict(int)
        for i in items:
            themes[i["theme"] or "other"] += 1
        dominant_theme = max(themes.items(), key=lambda x: x[1])[0] if themes else "other"
        pattern_kind = classify_cluster_kind(items, fuseki_max_move=fuseki_max)
        sample = items[:5]
        cur = db.conn.execute(
            """
            INSERT INTO pattern_clusters
                (pattern_hash, move_count, total_point_loss, dominant_theme,
                 patch_ascii, sample_json, pattern_kind, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                phash,
                len(items),
                total_loss,
                dominant_theme,
                items[0]["patch_ascii"],
                json.dumps(sample, ensure_ascii=False),
                pattern_kind,
                now,
            ),
        )
        cluster_id = cur.lastrowid
        cluster_map[phash] = int(cluster_id)
        for item in items:
            db.conn.execute(
                """
                UPDATE moves
                SET pattern_hash = ?, pattern_cluster_id = ?
                WHERE id = ?
                """,
                (phash, cluster_id, item["move_id"]),
            )
    db.conn.commit()
    return {"processed": processed, "clusters": len(cluster_map)}


def top_pattern_clusters(db: Database, limit: int = 10) -> list[Any]:
    return db.top_pattern_clusters(limit=limit)


def run_patterns(
    db: Database, cfg: dict[str, Any], since_year: int | None = None,
) -> dict[str, int]:
    print("=== Patterns locaux — clustering des erreurs ===")
    stats = cluster_patterns(db, cfg, since_year=since_year)
    print(f"  {stats['processed']} coup(s) traité(s)")
    print(f"  {stats['clusters']} cluster(s) distinct(s)")
    top = top_pattern_clusters(db, limit=5)
    if top:
        print("\n  Top clusters :")
        for i, c in enumerate(top, 1):
            print(
                f"    {i}. {c['move_count']}× "
                f"({c['total_point_loss']:.1f} pts) — {c['dominant_theme']}"
            )
    return stats
