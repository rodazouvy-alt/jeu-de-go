"""Utilitaires partages — labo fuseki."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LAB_ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from _common import (  # noqa: E402
    LAB_DIR,
    DEFAULT_THRESHOLDS,
    analyze_positions,
    effective_pl_sev,
    get_player_moves,
    get_sgf_path,
    get_total_moves,
    load_json,
    load_lab_config,
    open_lab_db,
    quick_top12_gap,
    quick_position_from_db,
    save_json,
)

FUSEKI_LAB_DIR = LAB_DIR / "fuseki"
FUSEKI_MAX_MOVE = 30
ORACLE_VISITS = 4000
ORACLE_CONFIG = "config/katago_lab_ref.cfg"
DEEP_CONFIG = "config/katago_deep.cfg"

SELECTED_JSON = FUSEKI_LAB_DIR / "selected_games.json"
CANDIDATES_JSON = FUSEKI_LAB_DIR / "game_candidates.json"

STRATEGIES = ("F0", "F1", "F2", "F3", "F4", "F5", "F6")
STRATEGY_LABELS = {
    "F0": "quick_only",
    "F1": "opening_400_all",
    "F2": "player_800",
    "F3": "player_1000",
    "F4": "all_turns_1000",
    "F5": "errors_1000",
    "F6": "ambig_2000_rest_1000",
}


def ensure_dirs() -> None:
    FUSEKI_LAB_DIR.mkdir(parents=True, exist_ok=True)


def ref_path(game_id: int) -> Path:
    return FUSEKI_LAB_DIR / f"ref_fuseki_{game_id}.json"


def strategy_path(strategy_id: str, game_id: int) -> Path:
    return FUSEKI_LAB_DIR / f"strategy_{strategy_id}_game_{game_id}.json"


def load_selected_game_ids() -> list[int]:
    return [g["id"] for g in load_json(SELECTED_JSON)["selected"]]


def player_fuseki_moves(
    db, game_id: int, thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    return [
        m for m in get_player_moves(db, game_id)
        if m["move_number"] <= FUSEKI_MAX_MOVE
    ]


def quick_position_from_db_wrapper(
    move: dict[str, Any], player_color_val: str, thresholds: dict[str, float],
) -> dict[str, Any]:
    return quick_position_from_db(move, player_color_val, thresholds)


def fuseki_turns(parsed, my_color: str, *, all_colors: bool = False) -> list[tuple[int, int]]:
    """[(move_number, turn_idx), ...] dans la zone fuseki."""
    out = []
    for idx, (color, _) in enumerate(parsed.moves):
        mn = idx + 1
        if mn > FUSEKI_MAX_MOVE:
            break
        if all_colors or color == my_color:
            out.append((mn, idx))
    return out


@dataclass
class FusekiCandidate:
    id: int
    opponent: str
    year: int | None
    month: int | None
    result: str | None
    total_moves: int
    fuseki_player_moves: int
    fuseki_errors: int
    fuseki_grey: int
    corner_moves: int
    excluded: bool
    exclude_reason: str = ""
    score: int = 0
    score_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_candidate(c: FusekiCandidate) -> FusekiCandidate:
    if c.excluded:
        return c
    notes: list[str] = []
    score = 0
    if c.year:
        score += c.year * 100
    if c.month:
        score += c.month * 10
    score += min(c.id, 99)
    notes.append("récence")
    if c.fuseki_errors >= 3:
        score += 40
        notes.append(f"erreurs fuseki ({c.fuseki_errors})")
    if c.fuseki_grey >= 2:
        score += 25
        notes.append(f"zone grise ({c.fuseki_grey})")
    if c.corner_moves >= 2:
        score += 15
        notes.append("coins/joseki")
    if 100 <= c.total_moves <= 200:
        score += 10
    c.score = score
    c.score_notes = notes
    return c


def fetch_candidates(db, *, limit: int = 20, relax: bool = False) -> list[FusekiCandidate]:
    min_fuseki_moves = 8 if not relax else 6
    min_errors = 2 if not relax else 1

    rows = db.conn.execute(
        """
        SELECT g.id, g.opponent, g.year, g.month, g.result
        FROM games g WHERE g.analyzed_quick = 1
        ORDER BY g.year DESC, g.month DESC, g.id DESC LIMIT 150
        """,
    ).fetchall()

    out: list[FusekiCandidate] = []
    for g in rows:
        gid = g["id"]
        total = get_total_moves(db, gid)
        stats = db.conn.execute(
            """
            SELECT
              COUNT(*) AS fuseki_player,
              SUM(CASE WHEN m.severity != 'ok' OR m.point_loss > 0.5 THEN 1 ELSE 0 END) AS errors,
              SUM(CASE WHEN m.point_loss BETWEEN 0.5 AND 8 THEN 1 ELSE 0 END) AS grey,
              SUM(CASE WHEN m.theme = 'corner_opening' THEN 1 ELSE 0 END) AS corners
            FROM moves m
            JOIN games g2 ON g2.id = m.game_id
            WHERE m.game_id = ? AND m.color = g2.player_color
              AND m.move_number <= ?
            """,
            (gid, FUSEKI_MAX_MOVE),
        ).fetchone()
        fp = int(stats["fuseki_player"] or 0)
        err = int(stats["errors"] or 0)
        grey = int(stats["grey"] or 0)
        corners = int(stats["corners"] or 0)

        excluded, reason = False, ""
        if fp < min_fuseki_moves:
            excluded, reason = True, f"peu de coups fuseki ({fp})"
        elif err < min_errors:
            excluded, reason = True, f"peu d'erreurs fuseki ({err})"

        out.append(score_candidate(FusekiCandidate(
            id=gid, opponent=g["opponent"] or "?", year=g["year"], month=g["month"],
            result=g["result"], total_moves=total, fuseki_player_moves=fp,
            fuseki_errors=err, fuseki_grey=grey, corner_moves=corners,
            excluded=excluded, exclude_reason=reason,
        )))

    out.sort(key=lambda x: (x.excluded, -x.score, -(x.year or 0), -x.id))
    return out[:limit]


def select_games(db, *, n: int = 3) -> tuple[list[FusekiCandidate], list[FusekiCandidate]]:
    all_c = fetch_candidates(db, limit=20)
    valid = [c for c in all_c if not c.excluded]
    if len(valid) < n:
        relaxed = fetch_candidates(db, limit=20, relax=True)
        seen = {c.id for c in all_c}
        for c in relaxed:
            if c.id not in seen:
                all_c.append(c)
                seen.add(c.id)
                if not c.excluded:
                    valid.append(c)
        valid.sort(key=lambda x: (-x.score, -(x.year or 0), -x.id))
    return valid[:n], all_c[:20]


def strategy_plan(
    strategy_id: str,
    parsed,
    my_color: str,
    fuseki_moves: list[dict[str, Any]],
    thresholds: dict[str, float],
) -> list[tuple[int, int, int]]:
    """[(move_number, turn_idx, visits), ...] — visits=0 => quick DB."""
    turns = fuseki_turns(parsed, my_color)
    move_by_num = {m["move_number"]: m for m in fuseki_moves}

    if strategy_id == "F0":
        return [(mn, ti, 0) for mn, ti in turns]

    if strategy_id == "F1":
        all_t = fuseki_turns(parsed, my_color, all_colors=True)
        return [(mn, ti, 400) for mn, ti in all_t]

    if strategy_id == "F2":
        return [(mn, ti, 800) for mn, ti in turns]

    if strategy_id == "F3":
        return [(mn, ti, 1000) for mn, ti in turns]

    if strategy_id == "F4":
        all_t = fuseki_turns(parsed, my_color, all_colors=True)
        return [(mn, ti, 1000) for mn, ti in all_t]

    if strategy_id == "F5":
        plan = []
        for mn, ti in turns:
            m = move_by_num.get(mn)
            if not m:
                continue
            pl, sev = effective_pl_sev(m, my_color, thresholds)
            if pl > 0.5 or sev != "ok":
                plan.append((mn, ti, 1000))
            else:
                plan.append((mn, ti, 0))
        return plan

    if strategy_id == "F6":
        ambig_nums: set[int] = set()
        for m in fuseki_moves:
            gap = quick_top12_gap(m, my_color)
            pl, sev = effective_pl_sev(m, my_color, thresholds)
            if gap is not None and gap < 0.5 and pl >= 0.5:
                ambig_nums.add(m["move_number"])
        plan = []
        for mn, ti in turns:
            m = move_by_num.get(mn)
            if not m:
                plan.append((mn, ti, 0))
                continue
            pl, sev = effective_pl_sev(m, my_color, thresholds)
            if mn in ambig_nums:
                plan.append((mn, ti, 2000))
            elif pl > 0.5 or sev != "ok":
                plan.append((mn, ti, 1000))
            else:
                plan.append((mn, ti, 0))
        return plan

    raise ValueError(f"Strategie inconnue: {strategy_id}")


def oracle_positions(game_id: int) -> list[dict[str, Any]]:
    return load_json(ref_path(game_id)).get("positions", [])
