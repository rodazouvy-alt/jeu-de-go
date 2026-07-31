"""Utilitaires partagés — labo deep visits."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from goprogress.config import load_config, resolve_path  # noqa: E402
from goprogress.db import Database  # noqa: E402
from goprogress.katago import KataGoAnalysis  # noqa: E402
from goprogress.sgf_parse import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    effective_move_quality,
    katago_score_for_player,
    parse_sgf,
    player_color,
    point_loss_from_stored_scores,
    severity_for_loss,
)

LAB_DIR = ROOT / "data" / "lab"
LAB_DB = LAB_DIR / "goprogress_lab.db"
SELECTED_GAMES_JSON = LAB_DIR / "selected_games.json"
CANDIDATES_JSON = LAB_DIR / "game_candidates.json"
LAB_STATE_JSON = LAB_DIR / "lab_state.json"

SEVERITY_ORDER = ("ok", "inaccuracy", "mistake", "blunder", "mega_blunder")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

PROD_DB_CANDIDATES = (
    "data/db/go_progress.db",
    "data/goprogress.db",
)

STRATEGIES = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")

STRATEGY_LABELS = {
    "S0": "quick_only",
    "S1": "current",
    "S2": "flat_1000",
    "S3": "skip_obvious",
    "S4": "top3_loss_2000",
    "S5": "tiered_loss",
    "S6": "top2_ambiguous_2000",
}


def load_lab_config() -> dict[str, Any]:
    cfg = load_config()
    cfg.setdefault("paths", {})["db_path"] = str(LAB_DB.relative_to(ROOT))
    return cfg


def ensure_lab_dirs() -> None:
    LAB_DIR.mkdir(parents=True, exist_ok=True)


def find_prod_db() -> Path:
    cfg = load_config()
    candidates = [resolve_path(cfg["paths"]["db_path"])]
    candidates.extend(resolve_path(p) for p in PROD_DB_CANDIDATES)
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            return path
    raise FileNotFoundError(
        "DB prod introuvable. Essayé : "
        + ", ".join(str(p) for p in candidates)
    )


def copy_prod_db(*, force: bool = False) -> Path:
    ensure_lab_dirs()
    src = find_prod_db()
    if LAB_DB.exists() and not force:
        print(f"DB lab déjà présente : {LAB_DB} (utiliser --force pour recopier)")
        return LAB_DB
    print(f"Copie {src} -> {LAB_DB}")
    shutil.copy2(src, LAB_DB)
    return LAB_DB


def open_lab_db() -> Database:
    ensure_lab_dirs()
    if not LAB_DB.exists():
        raise FileNotFoundError(
            f"{LAB_DB} absent — lancer d'abord 00_setup_lab.py"
        )
    return Database(LAB_DB)


def reconcile_lab_db(db: Database, cfg: dict[str, Any]) -> dict[str, int]:
    return db.reconcile_move_severities(cfg.get("thresholds"))


def _norm_gtp(coord: str | None) -> str:
    if not coord or coord.lower() == "pass":
        return "PASS"
    return coord.upper().strip()


def move_record_from_db_row(row: sqlite3.Row, player_color_val: str) -> dict[str, Any]:
    return {
        "move_number": row["move_number"],
        "played_move": row["played_move"],
        "best_move": row["best_move"],
        "point_loss": row["point_loss"],
        "severity": row["severity"] or "ok",
        "top_moves_json": row["top_moves_json"],
        "move_scores_json": row["move_scores_json"],
        "color": row["color"],
        "_player_color": player_color_val,
    }


def effective_pl_sev(
    move: dict[str, Any],
    player_color_val: str,
    thresholds: dict[str, float],
) -> tuple[float, str]:
    return effective_move_quality(
        move, player_color=player_color_val, thresholds=thresholds,
    )


def is_suspect(
    move: dict[str, Any],
    player_color_val: str,
    thresholds: dict[str, float],
    *,
    min_severity: str = "inaccuracy",
) -> bool:
    pl, sev = effective_pl_sev(move, player_color_val, thresholds)
    min_rank = SEVERITY_RANK.get(min_severity, 1)
    return SEVERITY_RANK.get(sev, 0) >= min_rank or pl > 0


def played_move_rank(move_infos: list[dict], played: str | None) -> int | None:
    played_gtp = _norm_gtp(played)
    for i, info in enumerate(move_infos):
        if _norm_gtp(info.get("move")) == played_gtp:
            return i + 1
    return None


def extract_position_result(
    turn_data: dict[str, Any],
    move_number: int,
    played: str,
    player_color_val: str,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    move_infos = turn_data.get("moveInfos", [])
    top_moves = KataGoAnalysis.top_moves_from_infos(move_infos, n=5)
    move_scores = KataGoAnalysis.scores_map_from_infos(move_infos)
    _, best_move = KataGoAnalysis.score_lead(move_infos, played)

    pseudo = {
        "played_move": played,
        "top_moves_json": json.dumps(top_moves, ensure_ascii=False),
        "move_scores_json": json.dumps(move_scores, ensure_ascii=False),
    }
    pl = point_loss_from_stored_scores(pseudo, player_color_val)
    if pl is None:
        pl = KataGoAnalysis.point_loss_vs_best(move_infos, played, player_color_val)
    pl = round(float(pl or 0), 2)
    if pl < 0.12:
        pl = 0.0
    sev = severity_for_loss(pl, thresholds) if pl > 0 else "ok"

    return {
        "move_number": move_number,
        "played_move": played,
        "best_move": best_move or (top_moves[0]["move"] if top_moves else None),
        "point_loss": pl,
        "severity": sev,
        "top_moves": top_moves,
        "move_scores": move_scores,
        "played_rank": played_move_rank(move_infos, played),
    }


def get_player_moves(db: Database, game_id: int) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT m.*, g.player_color, g.sgf_path, g.opponent, g.year, g.month
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.game_id = ? AND m.color = g.player_color
        ORDER BY m.move_number
        """,
        (game_id,),
    ).fetchall()
    return [move_record_from_db_row(row, row["player_color"]) for row in rows]


def get_total_moves(db: Database, game_id: int) -> int:
    row = db.conn.execute(
        "SELECT COUNT(*) AS c FROM moves WHERE game_id = ?", (game_id,),
    ).fetchone()
    return int(row["c"] or 0)


def quick_top12_gap(move: dict[str, Any], player_color_val: str) -> float | None:
    """Écart score #1 - #2 depuis les données quick stockées."""
    tops_raw = move.get("top_moves_json")
    if not tops_raw:
        return None
    try:
        tops = json.loads(tops_raw) if isinstance(tops_raw, str) else tops_raw
    except (json.JSONDecodeError, TypeError):
        return None
    if len(tops) < 2:
        return None
    scores: list[float] = []
    move_scores = move.get("move_scores_json")
    sc_map: dict[str, float] = {}
    if move_scores:
        try:
            raw = json.loads(move_scores) if isinstance(move_scores, str) else move_scores
            for gtp, sl in raw.items():
                conv = katago_score_for_player(sl, player_color_val)
                if conv is not None:
                    sc_map[_norm_gtp(gtp)] = conv
        except (json.JSONDecodeError, TypeError):
            pass
    for t in tops[:2]:
        mv = _norm_gtp(t.get("move"))
        if mv in sc_map:
            scores.append(sc_map[mv])
        else:
            conv = katago_score_for_player(t.get("scoreLead"), player_color_val)
            if conv is not None:
                scores.append(conv)
    if len(scores) < 2:
        return None
    return abs(scores[0] - scores[1])


@dataclass
class GameCandidate:
    id: int
    opponent: str
    year: int | None
    month: int | None
    result: str | None
    total_moves: int
    player_moves: int
    errors: int
    big_errors: int
    grey_zone: int
    small_errors: int
    mid_end_mistakes: int
    early_mega: bool
    excluded: bool
    exclude_reason: str = ""
    score: int = 0
    score_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_early_dead_game(db: Database, game_id: int) -> bool:
    early_mega = db.conn.execute(
        """
        SELECT COUNT(*) AS c FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.game_id = ? AND m.color = g.player_color
          AND m.move_number < 25
          AND m.severity = 'mega_blunder' AND m.point_loss >= 15
        """,
        (game_id,),
    ).fetchone()["c"]
    if not early_mega:
        return False
    mid_end = db.conn.execute(
        """
        SELECT COUNT(*) AS c FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.game_id = ? AND m.color = g.player_color
          AND m.move_number > 50
          AND m.severity IN ('mistake', 'blunder', 'mega_blunder')
        """,
        (game_id,),
    ).fetchone()["c"]
    return int(mid_end or 0) < 3


def score_game_candidate(c: GameCandidate) -> GameCandidate:
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

    if c.small_errors >= 1 and c.big_errors >= 1:
        score += 50
        notes.append("mix sévérités")
    if c.grey_zone >= 2:
        score += 30
        notes.append(f"zone grise ({c.grey_zone})")
    if 100 <= c.total_moves <= 200:
        score += 20
        notes.append("longueur idéale")
    elif 80 <= c.total_moves < 100 or 200 < c.total_moves <= 250:
        score += 5
        notes.append("longueur acceptable")

    c.score = score
    c.score_notes = notes
    return c


def fetch_and_score_candidates(
    db: Database,
    *,
    limit: int = 20,
    relax: bool = False,
) -> list[GameCandidate]:
    min_player = 35 if not relax else 30
    min_errors = 5 if not relax else 4
    min_total = 80 if not relax else 70

    rows = db.conn.execute(
        """
        SELECT g.id, g.opponent, g.year, g.month, g.result, g.player_color
        FROM games g
        WHERE g.analyzed_quick = 1
        ORDER BY g.year DESC, g.month DESC, g.id DESC
        LIMIT 200
        """,
    ).fetchall()

    candidates: list[GameCandidate] = []
    for g in rows:
        gid = g["id"]
        total = get_total_moves(db, gid)
        player_moves = db.conn.execute(
            """
            SELECT COUNT(*) AS c FROM moves m
            JOIN games g2 ON g2.id = m.game_id
            WHERE m.game_id = ? AND m.color = g2.player_color
            """,
            (gid,),
        ).fetchone()["c"]

        stats = db.conn.execute(
            """
            SELECT
              SUM(CASE WHEN m.severity != 'ok' OR m.point_loss > 0.5 THEN 1 ELSE 0 END) AS errors,
              SUM(CASE WHEN m.severity IN ('blunder','mega_blunder') THEN 1 ELSE 0 END) AS big_errors,
              SUM(CASE WHEN m.severity IN ('inaccuracy','mistake') THEN 1 ELSE 0 END) AS small_errors,
              SUM(CASE WHEN m.point_loss BETWEEN 1.5 AND 10 THEN 1 ELSE 0 END) AS grey_zone,
              SUM(CASE WHEN m.move_number > 50
                        AND m.severity IN ('mistake','blunder','mega_blunder') THEN 1 ELSE 0 END) AS mid_end_mistakes
            FROM moves m
            JOIN games g2 ON g2.id = m.game_id
            WHERE m.game_id = ? AND m.color = g2.player_color
            """,
            (gid,),
        ).fetchone()

        errors = int(stats["errors"] or 0)
        big_errors = int(stats["big_errors"] or 0)
        small_errors = int(stats["small_errors"] or 0)
        grey_zone = int(stats["grey_zone"] or 0)
        mid_end = int(stats["mid_end_mistakes"] or 0)
        early_dead = _is_early_dead_game(db, gid)

        excluded = False
        reason = ""
        if total < min_total:
            excluded, reason = True, f"trop court ({total} coups)"
        elif int(player_moves or 0) < min_player:
            excluded, reason = True, f"peu de coups joueur ({player_moves})"
        elif errors < min_errors:
            excluded, reason = True, f"peu d'erreurs ({errors})"
        elif early_dead:
            excluded, reason = True, "partie morte dès l'ouverture"

        c = GameCandidate(
            id=gid,
            opponent=g["opponent"] or "?",
            year=g["year"],
            month=g["month"],
            result=g["result"],
            total_moves=total,
            player_moves=int(player_moves or 0),
            errors=errors,
            big_errors=big_errors,
            grey_zone=grey_zone,
            small_errors=small_errors,
            mid_end_mistakes=mid_end,
            early_mega=early_dead,
            excluded=excluded,
            exclude_reason=reason,
        )
        candidates.append(score_game_candidate(c))

    candidates.sort(
        key=lambda x: (
            x.excluded,
            -x.score,
            -(x.year or 0),
            -(x.month or 0),
            -x.id,
        ),
    )
    return candidates[:limit]


def select_games(db: Database, *, n: int = 3) -> tuple[list[GameCandidate], list[GameCandidate]]:
    all_scored = fetch_and_score_candidates(db, limit=20)
    valid = [c for c in all_scored if not c.excluded]
    if len(valid) < n:
        print(f"  Seulement {len(valid)} candidates — assouplissement...")
        relaxed = fetch_and_score_candidates(db, limit=20, relax=True)
        seen = {c.id for c in all_scored}
        for c in relaxed:
            if c.id not in seen:
                all_scored.append(c)
                seen.add(c.id)
                if not c.excluded:
                    valid.append(c)
        valid.sort(key=lambda x: (-x.score, -(x.year or 0), -(x.month or 0), -x.id))
        all_scored.sort(
            key=lambda x: (
                x.excluded,
                -x.score,
                -(x.year or 0),
                -(x.month or 0),
                -x.id,
            ),
        )
    return valid[:n], all_scored[:20]


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_selected_game_ids() -> list[int]:
    data = load_json(SELECTED_GAMES_JSON)
    return [g["id"] for g in data["selected"]]


def ref_path(game_id: int) -> Path:
    return LAB_DIR / f"ref_game_{game_id}.json"


def oracle_positions(game_id: int) -> list[dict[str, Any]]:
    data = load_json(ref_path(game_id))
    return data.get("positions", [])


def strategy_path(strategy_id: str, game_id: int) -> Path:
    return LAB_DIR / f"strategy_{strategy_id}_game_{game_id}.json"


def get_sgf_path(db: Database, game_id: int) -> Path:
    row = db.conn.execute(
        "SELECT sgf_path FROM games WHERE id = ?", (game_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Partie #{game_id} introuvable")
    return resolve_path(row["sgf_path"])


def quick_position_from_db(
    move: dict[str, Any],
    player_color_val: str,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    """Convertit une ligne DB quick en format position lab."""
    tops_raw = move.get("top_moves_json")
    tops = []
    if tops_raw:
        try:
            tops = json.loads(tops_raw) if isinstance(tops_raw, str) else tops_raw
        except (json.JSONDecodeError, TypeError):
            tops = []
    pl, sev = effective_pl_sev(move, player_color_val, thresholds)
    played = move.get("played_move") or "pass"
    best = move.get("best_move") or (tops[0]["move"] if tops else None)
    rank = None
    if tops:
        for i, t in enumerate(tops):
            if _norm_gtp(t.get("move")) == _norm_gtp(played):
                rank = i + 1
                break
    return {
        "move_number": move["move_number"],
        "played_move": played,
        "best_move": best,
        "point_loss": pl,
        "severity": sev,
        "top_moves": tops[:5],
        "played_rank": rank,
        "visits": 400,
        "source": "quick_db",
    }


def analyze_positions(
    engine: KataGoAnalysis,
    parsed,
    game_id: int,
    positions: list[tuple[int, int, int]],
    *,
    tag: str,
    player_color_val: str,
    thresholds: dict[str, float],
) -> tuple[list[dict[str, Any]], float]:
    """positions: [(move_number, turn_idx, visits), ...]"""
    by_visits: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for move_num, turn_idx, visits in positions:
        if visits > 0:
            by_visits[visits].append((move_num, turn_idx))

    all_results: dict[int, dict[str, Any]] = {}
    t0 = time.perf_counter()

    for visits, items in sorted(by_visits.items()):
        turns = [turn_idx for _, turn_idx in items]
        print(f"    {visits}v × {len(turns)} coups...", flush=True)
        responses = engine.analyze_game(
            moves=parsed.moves,
            komi=parsed.komi,
            board_size=parsed.board_size,
            rules=parsed.rules or "chinese",
            max_visits=visits,
            game_id=f"lab_{tag}_g{game_id}_{visits}v",
            analyze_turns=turns,
            timeout_seconds=max(900, len(turns) * visits // 100 + 300),
        )
        by_turn = {r.get("turnNumber"): r for r in responses}
        for move_num, turn_idx in items:
            color, coord = parsed.moves[turn_idx]
            played = coord if coord else "pass"
            turn_data = by_turn.get(turn_idx)
            if not turn_data:
                continue
            all_results[move_num] = extract_position_result(
                turn_data, move_num, played, player_color_val, thresholds,
            )
            all_results[move_num]["visits"] = visits
            all_results[move_num]["source"] = "katago"

    gpu_sec = time.perf_counter() - t0
    ordered = [all_results[mn] for mn in sorted(all_results)]
    return ordered, gpu_sec


def strategy_visits(
    strategy_id: str,
    move: dict[str, Any],
    player_color_val: str,
    thresholds: dict[str, float],
    *,
    game_moves: list[dict[str, Any]],
) -> int:
    """Retourne 0 = skip (utiliser quick), ou nb visits deep."""
    pl, sev = effective_pl_sev(move, player_color_val, thresholds)
    sev_rank = SEVERITY_RANK.get(sev, 0)
    min_mistake = SEVERITY_RANK["mistake"]

    if strategy_id == "S0":
        return 0

    if sev_rank < SEVERITY_RANK["inaccuracy"] and pl <= 0:
        return 0

    if strategy_id == "S1":
        if sev == "mega_blunder":
            return 2000
        if sev_rank >= min_mistake:
            return 1000
        return 0

    if strategy_id == "S2":
        return 1000 if sev_rank >= min_mistake else 0

    if strategy_id == "S3":
        if 1.5 <= pl <= 15:
            return 1000
        return 0

    if strategy_id == "S4":
        ranked = sorted(
            game_moves,
            key=lambda m: effective_pl_sev(m, player_color_val, thresholds)[0],
            reverse=True,
        )
        top3_nums = {m["move_number"] for m in ranked[:3]}
        if move["move_number"] in top3_nums and sev_rank >= min_mistake:
            return 2000
        return 0

    if strategy_id == "S5":
        if pl < 1.5 or pl > 25:
            return 0
        if pl < 5:
            return 0
        if pl < 12:
            return 1000
        return 2000

    if strategy_id == "S6":
        gap = quick_top12_gap(move, player_color_val)
        ranked_by_loss = sorted(
            game_moves,
            key=lambda m: effective_pl_sev(m, player_color_val, thresholds)[0],
            reverse=True,
        )
        top2_nums = {m["move_number"] for m in ranked_by_loss[:2]}
        if (
            move["move_number"] in top2_nums
            and gap is not None and gap < 0.5
            and 2 <= pl <= 15
        ):
            return 2000
        if sev_rank >= min_mistake:
            return 1000
        return 0

    raise ValueError(f"Stratégie inconnue : {strategy_id}")


def suspect_moves_for_game(
    db: Database,
    game_id: int,
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    moves = get_player_moves(db, game_id)
    row = db.conn.execute(
        "SELECT player_color FROM games WHERE id = ?", (game_id,),
    ).fetchone()
    pc = row["player_color"]
    return [m for m in moves if is_suspect(m, pc, thresholds)]
