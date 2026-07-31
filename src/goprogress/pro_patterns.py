from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sgfmill.boards import Board

from .config import resolve_path
from .db import Database
from .local_pattern import (
    PATCH_RADIUS,
    canonical_hash,
    extract_blunder_patch,
    extract_patch,
    _color_relative,
    _patch_key,
    _mirror_patch,
    _rotate_patch,
    _stone_count,
)
from .sgf_parse import parse_sgf
from .themes import gtp_to_rowcol, patch_center

PRO_THEMES = frozenset({"corner_opening", "invasion", "reduction", "fight"})
INDEX_VERSION = 1
FUZZY_TOLERANCE = 2
FUZZY_MIN_STONES = 6


def _canonical_patch(patch: list[list[str]], to_play: str) -> list[list[str]]:
    rel = _color_relative(patch, to_play)
    best = rel
    current = rel
    for mirror in (False, True):
        work = _mirror_patch(current) if mirror else current
        for _ in range(4):
            key = _patch_key(work)
            if key < _patch_key(best):
                best = work
            work = _rotate_patch(work)
        if not mirror:
            current = work
    return best


def _stone_signature(patch: list[list[str]], to_play: str) -> str:
    canon = _canonical_patch(patch, to_play)
    n = len(canon)
    mid = n // 2
    parts: list[str] = []
    for r in range(n):
        for c in range(n):
            cell = canon[r][c]
            if cell in ("o", "x"):
                parts.append(f"{cell}:{r - mid},{c - mid}")
    return "|".join(sorted(parts))


def _gogod_paths(cfg: dict[str, Any]) -> dict[str, Path]:
    pro = cfg.get("pro_corpus", {}).get("gogod", {})
    return {
        "sgf": resolve_path(pro.get("sgf_dir", "data/pro_corpus/gogod/sgf")),
        "index": resolve_path(
            pro.get("patch_index_path", "data/pro_corpus/gogod/patch_index.json")
        ),
    }


def build_patch_index(cfg: dict[str, Any], *, force: bool = False) -> Path:
    paths = _gogod_paths(cfg)
    index_path = paths["index"]
    sgf_dir = paths["sgf"]

    if index_path.exists() and not force:
        print(f"Index existant : {index_path}")
        return index_path

    if not sgf_dir.exists():
        raise FileNotFoundError(f"Corpus GoGoD introuvable : {sgf_dir}")

    sgf_files = sorted(sgf_dir.rglob("*.sgf"))
    print(f"Construction index patch ({len(sgf_files)} SGF)…")

    conts: dict[str, Counter[str]] = defaultdict(Counter)
    pos_count: Counter[str] = Counter()
    stone_sigs: dict[str, str] = {}
    by_stone_count: dict[str, list[str]] = defaultdict(list)
    processed = 0
    errors = 0

    for i, sgf_path in enumerate(sgf_files, 1):
        if i % 5000 == 0:
            print(f"  {i}/{len(sgf_files)}…", flush=True)
        try:
            parsed = parse_sgf(sgf_path)
        except Exception:
            errors += 1
            continue
        if parsed.board_size != 19:
            continue
        board = Board(19)
        game_conts: dict[str, Counter[str]] = defaultdict(Counter)
        game_pos: Counter[str] = Counter()
        game_sigs: dict[str, str] = {}
        game_buckets: dict[str, list[str]] = defaultdict(list)
        try:
            for idx, (color, coord) in enumerate(parsed.moves):
                if not coord:
                    continue
                rc = gtp_to_rowcol(coord, parsed.board_size)
                if not rc:
                    continue
                row, col = rc
                center = patch_center(board, color, row, col, "other")
                patch = extract_patch(board, center[0], center[1])
                phash = canonical_hash(patch, color)
                game_pos[phash] += 1
                if phash not in game_sigs:
                    sig = _stone_signature(patch, color)
                    game_sigs[phash] = sig
                    sc = str(sig.count("|") + 1 if sig else 0)
                    game_buckets[sc].append(phash)
                next_coord = (
                    parsed.moves[idx + 1][1] if idx + 1 < len(parsed.moves) else None
                )
                if next_coord:
                    game_conts[phash][next_coord.upper()] += 1
                board.play(row, col, color.lower())
        except ValueError:
            errors += 1
            continue

        for phash, counter in game_conts.items():
            conts[phash].update(counter)
        pos_count.update(game_pos)
        for phash, sig in game_sigs.items():
            if phash not in stone_sigs:
                stone_sigs[phash] = sig
                sc = str(sig.count("|") + 1 if sig else 0)
                by_stone_count[sc].append(phash)
        processed += 1

    entries: dict[str, dict[str, Any]] = {}
    for phash, counter in conts.items():
        entries[phash] = {
            "count": pos_count[phash],
            "continuations": dict(counter.most_common(20)),
            "stone_sig": stone_sigs.get(phash, ""),
        }

    payload = {
        "version": INDEX_VERSION,
        "radius": PATCH_RADIUS,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sgf_count": processed,
        "position_count": sum(pos_count.values()),
        "unique_hashes": len(entries),
        "by_stone_count": {k: v[:2000] for k, v in by_stone_count.items()},
        "entries": entries,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        f"Index écrit : {index_path} "
        f"({payload['unique_hashes']} hashes, {payload['position_count']} positions)"
    )
    if errors:
        print(f"  ({errors} SGF ignorés)")
    return index_path


_PATCH_INDEX_CACHE: dict[str, Any] | None = None


def load_patch_index(cfg: dict[str, Any]) -> dict[str, Any]:
    global _PATCH_INDEX_CACHE
    if _PATCH_INDEX_CACHE is not None:
        return _PATCH_INDEX_CACHE
    paths = _gogod_paths(cfg)
    index_path = paths["index"]
    if not index_path.exists():
        build_patch_index(cfg)
    print("  Chargement index GoGoD...", flush=True)
    _PATCH_INDEX_CACHE = json.loads(index_path.read_text(encoding="utf-8"))
    return _PATCH_INDEX_CACHE


def _top_continuations(entry: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    conts = entry.get("continuations") or {}
    total = sum(conts.values()) or 1
    ranked = sorted(conts.items(), key=lambda x: (-x[1], x[0]))
    return [
        {"move": mv, "count": cnt, "pct": round(100.0 * cnt / total, 1)}
        for mv, cnt in ranked[:limit]
    ]


def _sig_match_score(sig_a: str, sig_b: str) -> int:
    if not sig_a or not sig_b:
        return 0
    set_a = set(sig_a.split("|"))
    set_b = set(sig_b.split("|"))
    return len(set_a & set_b)


def match_pro_context(
    patch: list[list[str]],
    phash: str,
    to_play: str,
    index: dict[str, Any],
) -> dict[str, Any]:
    entries = index.get("entries", {})
    exact = entries.get(phash)
    if exact:
        return {
            "match_count": exact.get("count", 0),
            "match_type": "exact",
            "top_continuations": _top_continuations(exact),
        }

    query_sig = _stone_signature(patch, to_play)
    stone_cnt = str(_stone_count(patch, to_play))
    candidates = index.get("by_stone_count", {}).get(stone_cnt, [])
    best_hash = None
    best_score = 0
    for chash in candidates[:800]:
        entry = entries.get(chash)
        if not entry:
            continue
        score = _sig_match_score(query_sig, entry.get("stone_sig", ""))
        if score > best_score:
            best_score = score
            best_hash = chash

    min_needed = max(1, _stone_count(patch, to_play) - FUZZY_TOLERANCE)
    if best_hash and best_score >= min_needed:
        entry = entries[best_hash]
        return {
            "match_count": entry.get("count", 0),
            "match_type": "fuzzy",
            "stone_matches": best_score,
            "top_continuations": _top_continuations(entry),
        }

    return {
        "match_count": 0,
        "match_type": "none",
        "top_continuations": [],
    }


def pro_blunder_moves(
    db: Database, limit: int | None = None, since_year: int | None = None,
) -> list[Any]:
    year_sql, year_params = Database._since_year_sql(since_year)
    query = f"""
        SELECT m.*, g.sgf_path, g.board_size
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE g.analyzed_quick = 1
          AND m.severity IN ('blunder', 'mega_blunder')
          AND m.theme IN ('corner_opening', 'invasion', 'reduction', 'fight')
          AND m.coord IS NOT NULL
          AND (m.pro_context_json IS NULL OR m.pro_context_json = ''){year_sql}
        ORDER BY m.point_loss DESC
    """
    params: list[Any] = list(year_params)
    if limit:
        query += " LIMIT ?"
        params.append(int(limit))
    return db.conn.execute(query, params).fetchall()


def run_pro_patterns(
    db: Database,
    cfg: dict[str, Any],
    *,
    limit: int | None = None,
    rebuild_index: bool = False,
    since_year: int | None = None,
) -> dict[str, int]:
    print("=== Patterns pro — contexte GoGoD ===")
    if rebuild_index:
        build_patch_index(cfg, force=True)
    index = load_patch_index(cfg)

    moves = pro_blunder_moves(db, limit=limit, since_year=since_year)
    if not moves:
        print("  Aucun blunder thématique en attente.")
        return {"processed": 0, "matched": 0}

    processed = matched = 0
    for m in moves:
        sgf_path = resolve_path(m["sgf_path"])
        if not sgf_path.exists():
            continue
        result = extract_blunder_patch(
            sgf_path, m["move_number"], m["theme"], m["board_size"] or 19
        )
        if not result:
            continue
        patch, phash, color = result
        ctx = match_pro_context(patch, phash, color, index)
        if ctx["match_count"] > 0:
            matched += 1
        db.conn.execute(
            "UPDATE moves SET pro_context_json = ? WHERE id = ?",
            (json.dumps(ctx, ensure_ascii=False), m["id"]),
        )
        processed += 1
        if processed % 10 == 0:
            db.conn.commit()
            print(f"  {processed}/{len(moves)}…", flush=True)

    db.conn.commit()
    print(f"  {processed} blunder(s) enrichi(s), {matched} avec match pro")
    return {"processed": processed, "matched": matched}


def enrich_game_patterns(
    cfg: dict[str, Any],
    db: Database,
    game_id: int,
) -> int:
    """Enrichit les blunders thématiques d'une partie avec le contexte pro GoGoD."""
    index_path = _gogod_paths(cfg)["index"]
    if not index_path.exists():
        return 0
    index = load_patch_index(cfg)

    moves = db.conn.execute(
        """
        SELECT m.*, g.sgf_path, g.board_size
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.game_id = ?
          AND m.severity IN ('blunder', 'mega_blunder')
          AND m.theme IN ('corner_opening', 'invasion', 'reduction', 'fight')
          AND m.coord IS NOT NULL
        """,
        (game_id,),
    ).fetchall()
    if not moves:
        return 0

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
        patch, phash, color = result
        ctx = match_pro_context(patch, phash, color, index)
        db.conn.execute(
            "UPDATE moves SET pro_context_json = ? WHERE id = ?",
            (json.dumps(ctx, ensure_ascii=False), m["id"]),
        )
        processed += 1
    if processed:
        db.conn.commit()
    return processed


build_pro_index = build_patch_index
enrich_pro_patterns = run_pro_patterns
