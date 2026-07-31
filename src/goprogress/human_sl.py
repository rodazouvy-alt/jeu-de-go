from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import resolve_path
from .db import Database
from .katago import KataGoAnalysis
from .sgf_parse import parse_sgf, player_color

RANK_LABELS = {
    "rank_1d": "1d",
    "rank_2d": "2d",
    "rank_3d": "3d",
    "rank_4d": "4d",
    "rank_5d": "5d",
    "rank_6d": "6d",
    "rank_7d": "7d",
    "rank_8d": "8d",
    "rank_9d": "9d",
    "proyear_2020": "pro 20",
    "proyear_2023": "pro 23",
}

RANK_ORDER = [
    "rank_3d", "rank_4d", "rank_5d", "rank_6d",
    "rank_7d", "rank_8d", "rank_9d",
    "proyear_2020", "proyear_2023",
]


def _gtp_played(played: str | None) -> str:
    if not played or played.lower() == "pass":
        return "pass"
    return played.upper()


def _human_override(rank: str) -> dict[str, Any]:
    return {
        "humanSLProfile": rank,
        "ignorePreRootHistory": False,
        "rootNumSymmetriesToSample": 2,
    }


def _estimate_rank(mean_priors: dict[str, float]) -> str:
    if not mean_priors:
        return "?"
    best = max(mean_priors.items(), key=lambda x: x[1])
    return RANK_LABELS.get(best[0], best[0])


def _stretch_score(
    priors: dict[str, float],
    top_7d: str | None,
    top_9d: str | None,
    played: str,
) -> tuple[float, str | None]:
    """Score élevé = un 7d/9d jouerait très différemment d'un 3d."""
    p3 = priors.get("rank_3d", 0.0)
    p7 = priors.get("rank_7d", 0.0)
    p9 = priors.get("rank_9d", 0.0)
    played_u = _gtp_played(played)
    stretch_move = None
    stretch = 0.0
    if top_7d and top_7d != played_u and p3 < 0.12:
        stretch = max(stretch, (1.0 - p3) * max(0.0, 0.25 - p7))
        stretch_move = top_7d
    if top_9d and top_9d != played_u and p3 < 0.10:
        stretch = max(stretch, (1.0 - p3) * max(0.0, 0.20 - p9))
        if not stretch_move:
            stretch_move = top_9d
    if top_7d and top_7d != played_u and p7 > p3 + 0.08:
        stretch = max(stretch, p7 - p3)
        stretch_move = top_7d
    return round(stretch, 4), stretch_move


def _query_rank_priors(
    engine: KataGoAnalysis,
    parsed,
    turns: list[int],
    rank: str,
    game_id: str,
    board_size: int,
) -> dict[int, dict[str, Any]]:
    """Retourne turn -> {prior, top_moves, humanPolicy}."""
    if not turns:
        return {}
    responses = engine.analyze_game(
        moves=parsed.moves,
        komi=parsed.komi,
        board_size=board_size,
        rules=parsed.rules or "chinese",
        max_visits=1,
        game_id=f"hum_{game_id}_{rank}",
        analyze_turns=sorted(set(turns)),
        timeout_seconds=max(60, len(turns) * 5),
        include_policy=True,
        override_settings=_human_override(rank),
        initial_stones=parsed.initial_stones,
    )
    out: dict[int, dict[str, Any]] = {}
    for resp in responses:
        turn = resp.get("turnNumber")
        if turn is None:
            continue
        hp = resp.get("humanPolicy")
        top_moves: list[tuple[str, float]] = []
        if hp:
            top_moves = KataGoAnalysis.policy_top_moves(hp, board_size, n=3)
        out[turn] = {
            "humanPolicy": hp,
            "top_moves": top_moves,
        }
    return out


def _prior_for_turn(
    rank_data: dict[int, dict[str, Any]],
    turn: int,
    played: str,
    board_size: int,
) -> float | None:
    data = rank_data.get(turn)
    if not data:
        return None
    hp = data.get("humanPolicy")
    if hp:
        return KataGoAnalysis.policy_prior(hp, _gtp_played(played), board_size)
    return None


def _rank_top_moves(
    rank_cache: dict[str, dict[int, dict[str, Any]]],
    turn: int,
    ranks: list[str],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for rank in ranks:
        tops = rank_cache.get(rank, {}).get(turn, {}).get("top_moves") or []
        if tops:
            out[rank] = tops[0][0]
    return out


def _human_sl_fields(
    rank_cache: dict[str, dict[int, dict[str, Any]]],
    turn: int,
    ranks: list[str],
    played: str,
    board_size: int,
    player_profile: str,
    point_loss: float,
) -> dict[str, Any] | None:
    priors: dict[str, float] = {}
    for rank in ranks:
        p = _prior_for_turn(rank_cache[rank], turn, played, board_size)
        if p is not None:
            priors[rank] = round(p, 4)
    if not priors:
        return None
    rank_moves = _rank_top_moves(rank_cache, turn, ranks)
    top_7d = rank_moves.get("rank_7d")
    top_9d = rank_moves.get("rank_9d")
    player_prior = priors.get(player_profile, 0.0)
    stretch, stretch_move = _stretch_score(priors, top_7d, top_9d, played)
    return {
        "human_prior_json": json.dumps(priors, ensure_ascii=False),
        "human_rank_moves_json": json.dumps(rank_moves, ensure_ascii=False),
        "study_priority": round(point_loss * (1.0 - player_prior), 2),
        "stretch_score": stretch,
        "stretch_top_move": stretch_move,
        "player_prior": player_prior,
    }


def human_sl_loss_sql(hcfg: dict[str, Any]) -> tuple[str, list[Any]]:
    opening_moves = int(hcfg.get("opening_moves", 80))
    opening_min = float(hcfg.get("opening_min_point_loss", 0.5))
    min_loss = float(hcfg.get("min_point_loss", 1.5))
    sql = """(
        (m.move_number <= ? AND m.point_loss >= ?)
        OR (m.move_number > ? AND m.point_loss >= ?)
    )"""
    return sql, [opening_moves, opening_min, opening_moves, min_loss]


def enrich_human_sl(
    cfg: dict[str, Any],
    db: Database,
    *,
    limit: int = 200,
    min_point_loss: float | None = None,
    game_limit: int = 30,
    since_year: int | None = None,
) -> int:
    """Human SL sur coups suspects + estimation niveau par partie."""
    hcfg = cfg.get("human_sl", {})
    if min_point_loss is not None:
        hcfg = {**hcfg, "min_point_loss": min_point_loss}
    loss_sql, loss_params = human_sl_loss_sql(hcfg)
    year_sql, year_params = Database._since_year_sql(since_year)
    human_model = hcfg.get("model") or cfg["katago"].get("human_model")
    if not human_model or not Path(human_model).exists():
        raise FileNotFoundError(
            f"Modèle Human SL introuvable : {human_model}\n"
            "Lancez scripts\\occasionnel\\run_install_human_sl.bat"
        )

    ranks: list[str] = hcfg.get("compare_ranks", RANK_ORDER)
    player_profile: str = hcfg.get("player_profile", "rank_3d")
    analysis_config = hcfg.get("config", "config/katago_human.cfg")
    username = cfg["player"]["kgs_username"]

    rows = db.conn.execute(
        f"""
        SELECT m.id, m.game_id, m.move_number, m.played_move, m.point_loss, g.sgf_path
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.color = g.player_color
          AND {loss_sql}
          AND (
            m.human_prior_json IS NULL OR m.human_prior_json = ''
            OR m.human_rank_moves_json IS NULL OR m.human_rank_moves_json = ''
          ){year_sql}
        ORDER BY m.point_loss DESC
        LIMIT ?
        """,
        (*loss_params, *year_params, limit),
    ).fetchall()

    by_game: dict[int, list] = defaultdict(list)
    for row in rows:
        by_game[row["game_id"]].append(row)

    if not by_game:
        return _enrich_game_strengths(
            cfg, db, engine=None, ranks=ranks, limit=game_limit,
        )

    engine = KataGoAnalysis(
        cfg, analysis_config=analysis_config, human_model=human_model,
    )
    engine.start()
    done = 0

    try:
        for game_id, game_rows in by_game.items():
            sgf_path = resolve_path(game_rows[0]["sgf_path"])
            if not sgf_path.exists():
                continue
            try:
                parsed = parse_sgf(sgf_path)
            except Exception:
                continue

            turns = [r["move_number"] - 1 for r in game_rows]
            rank_cache: dict[str, dict[int, dict]] = {}
            for rank in ranks:
                rank_cache[rank] = _query_rank_priors(
                    engine, parsed, turns, rank, str(game_id), parsed.board_size,
                )

            for row in game_rows:
                turn = row["move_number"] - 1
                fields = _human_sl_fields(
                    rank_cache, turn, ranks, row["played_move"],
                    parsed.board_size, player_profile, float(row["point_loss"] or 0),
                )
                if not fields:
                    continue

                db.conn.execute(
                    """
                    UPDATE moves SET human_prior_json = ?, human_rank_moves_json = ?,
                        study_priority = ?, stretch_score = ?, stretch_top_move = ?
                    WHERE id = ?
                    """,
                    (
                        fields["human_prior_json"],
                        fields["human_rank_moves_json"],
                        fields["study_priority"],
                        fields["stretch_score"],
                        fields["stretch_top_move"],
                        row["id"],
                    ),
                )
                done += 1
                label = RANK_LABELS.get(player_profile, player_profile)
                print(
                    f"  #{game_id} coup {row['move_number']}: "
                    f"{label}={fields['player_prior']:.1%} prio={fields['study_priority']} "
                    f"stretch={fields['stretch_score']:.2f}",
                    flush=True,
                )
            db.conn.commit()
            _estimate_game_from_moves(db, game_id, parsed, username, ranks)

        done += _enrich_game_strengths(
            cfg, db, engine=engine, ranks=ranks, limit=game_limit,
        )
    finally:
        engine.stop()

    return done


def _sample_turns(moves: list, color: str, max_n: int = 15) -> list[int]:
    indices = [i for i, (c, _) in enumerate(moves) if c == color]
    if len(indices) <= max_n:
        return indices
    step = len(indices) / max_n
    return [indices[int(i * step)] for i in range(max_n)]


def _estimate_game_from_moves(
    db: Database,
    game_id: int,
    parsed,
    username: str,
    ranks: list[str],
) -> None:
    """Estime le niveau SL depuis les coups déjà enrichis en base."""
    my_color = player_color(parsed, username)
    if not my_color:
        return
    opp_color = "W" if my_color == "B" else "B"

    player_priors: dict[str, list[float]] = {r: [] for r in ranks}
    rows = db.conn.execute(
        """
        SELECT human_prior_json FROM moves
        WHERE game_id = ? AND human_prior_json IS NOT NULL AND human_prior_json != ''
        """,
        (game_id,),
    ).fetchall()
    for row in rows:
        try:
            pdata = json.loads(row["human_prior_json"])
        except json.JSONDecodeError:
            continue
        for rank in ranks:
            if rank in pdata:
                player_priors[rank].append(pdata[rank])

    player_means = {
        r: sum(v) / len(v) for r, v in player_priors.items() if v
    }
    player_est = _estimate_rank(player_means)

    game_row = db.conn.execute(
        "SELECT player_rank, opponent_rank, human_sl_game_json FROM games WHERE id = ?",
        (game_id,),
    ).fetchone()
    existing = {}
    if game_row and game_row["human_sl_game_json"]:
        try:
            existing = json.loads(game_row["human_sl_game_json"])
        except json.JSONDecodeError:
            pass

    payload = {
        **existing,
        "player_sl_estimated": player_est,
        "player_mean_priors": {
            RANK_LABELS.get(k, k): round(v, 4) for k, v in player_means.items()
        },
        "player_rank_sgf": game_row["player_rank"] if game_row else None,
        "opponent_rank_sgf": game_row["opponent_rank"] if game_row else None,
    }

    db.conn.execute(
        """
        UPDATE games SET player_sl_estimated = ?, human_sl_game_json = ?
        WHERE id = ?
        """,
        (player_est, json.dumps(payload, ensure_ascii=False), game_id),
    )
    db.conn.commit()


def _enrich_game_strengths(
    cfg: dict[str, Any],
    db: Database,
    *,
    engine: KataGoAnalysis | None,
    ranks: list[str],
    limit: int,
) -> int:
    """Estime niveau joueur + adversaire sur échantillon de coups SGF."""
    username = cfg["player"]["kgs_username"]
    hcfg = cfg.get("human_sl", {})
    human_model = hcfg.get("model") or cfg["katago"].get("human_model")
    analysis_config = hcfg.get("config", "config/katago_human.cfg")

    games = db.conn.execute(
        """
        SELECT id, sgf_path, player_rank, opponent_rank, opponent_sl_estimated
        FROM games
        WHERE analyzed_quick = 1
          AND (opponent_sl_estimated IS NULL OR opponent_sl_estimated = '')
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    if not games:
        return 0

    own_engine = engine is None
    if own_engine:
        engine = KataGoAnalysis(
            cfg, analysis_config=analysis_config, human_model=human_model,
        )
        engine.start()

    done = 0
    try:
        for g in games:
            sgf_path = resolve_path(g["sgf_path"])
            if not sgf_path.exists():
                continue
            try:
                parsed = parse_sgf(sgf_path)
            except Exception:
                continue

            my_color = player_color(parsed, username)
            if not my_color:
                continue
            opp_color = "W" if my_color == "B" else "B"

            player_turns = _sample_turns(parsed.moves, my_color)
            opp_turns = _sample_turns(parsed.moves, opp_color)
            all_turns = sorted(set(player_turns + opp_turns))
            if not all_turns:
                continue

            rank_cache: dict[str, dict[int, dict]] = {}
            for rank in ranks:
                rank_cache[rank] = _query_rank_priors(
                    engine, parsed, all_turns, rank, f"g{g['id']}", parsed.board_size,
                )

            def means_for_color(color: str, turns: list[int]) -> dict[str, float]:
                means: dict[str, float] = {}
                for rank in ranks:
                    vals = []
                    for turn in turns:
                        if turn >= len(parsed.moves):
                            continue
                        _, coord = parsed.moves[turn]
                        played = coord if coord else "pass"
                        p = _prior_for_turn(
                            rank_cache[rank], turn, played, parsed.board_size,
                        )
                        if p is not None:
                            vals.append(p)
                    if vals:
                        means[rank] = sum(vals) / len(vals)
                return means

            player_means = means_for_color(my_color, player_turns)
            opp_means = means_for_color(opp_color, opp_turns)
            player_est = _estimate_rank(player_means)
            opp_est = _estimate_rank(opp_means)

            payload = {
                "player_sl_estimated": player_est,
                "opponent_sl_estimated": opp_est,
                "player_mean_priors": {
                    RANK_LABELS.get(k, k): round(v, 4) for k, v in player_means.items()
                },
                "opponent_mean_priors": {
                    RANK_LABELS.get(k, k): round(v, 4) for k, v in opp_means.items()
                },
                "player_rank_sgf": g["player_rank"],
                "opponent_rank_sgf": g["opponent_rank"],
                "sample_moves": len(all_turns),
            }

            db.conn.execute(
                """
                UPDATE games SET player_sl_estimated = ?, opponent_sl_estimated = ?,
                    human_sl_game_json = ?
                WHERE id = ?
                """,
                (
                    player_est,
                    opp_est,
                    json.dumps(payload, ensure_ascii=False),
                    g["id"],
                ),
            )
            db.conn.commit()
            done += 1
            print(
                f"  Partie #{g['id']}: vous ~{player_est} (SGF {g['player_rank'] or '?'}) "
                f"vs {g['opponent_rank'] or '?'} joué ~{opp_est}",
                flush=True,
            )
    finally:
        if own_engine and engine is not None:
            engine.stop()

    return done


def enrich_human_sl_for_game(
    cfg: dict[str, Any],
    db: Database,
    game_id: int,
    *,
    min_point_loss: float | None = None,
    engine: KataGoAnalysis | None = None,
) -> int:
    """Human SL sur les blunders d'une seule partie (pipeline par partie)."""
    hcfg = cfg.get("human_sl", {})
    if min_point_loss is not None:
        hcfg = {**hcfg, "min_point_loss": min_point_loss}
    loss_sql, loss_params = human_sl_loss_sql(hcfg)
    human_model = hcfg.get("model") or cfg["katago"].get("human_model")
    if not human_model or not Path(human_model).exists():
        return 0

    ranks: list[str] = hcfg.get("compare_ranks", RANK_ORDER)
    player_profile: str = hcfg.get("player_profile", "rank_3d")
    analysis_config = hcfg.get("config", "config/katago_human.cfg")

    game_rows = db.conn.execute(
        f"""
        SELECT m.id, m.game_id, m.move_number, m.played_move, m.point_loss, g.sgf_path
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.game_id = ? AND m.color = g.player_color AND {loss_sql}
        """,
        (game_id, *loss_params),
    ).fetchall()
    if not game_rows:
        return 0

    own_engine = engine is None
    if own_engine:
        engine = KataGoAnalysis(
            cfg, analysis_config=analysis_config, human_model=human_model,
        )
        engine.start()

    done = 0
    try:
        sgf_path = resolve_path(game_rows[0]["sgf_path"])
        if not sgf_path.exists():
            return 0
        parsed = parse_sgf(sgf_path)
        turns = [r["move_number"] - 1 for r in game_rows]
        rank_cache: dict[str, dict[int, dict]] = {}
        for rank in ranks:
            rank_cache[rank] = _query_rank_priors(
                engine, parsed, turns, rank, str(game_id), parsed.board_size,
            )

        for row in game_rows:
            turn = row["move_number"] - 1
            fields = _human_sl_fields(
                rank_cache, turn, ranks, row["played_move"],
                parsed.board_size, player_profile, float(row["point_loss"] or 0),
            )
            if not fields:
                continue
            db.conn.execute(
                """
                UPDATE moves SET human_prior_json = ?, human_rank_moves_json = ?,
                    study_priority = ?, stretch_score = ?, stretch_top_move = ?
                WHERE id = ?
                """,
                (
                    fields["human_prior_json"],
                    fields["human_rank_moves_json"],
                    fields["study_priority"],
                    fields["stretch_score"],
                    fields["stretch_top_move"],
                    row["id"],
                ),
            )
            done += 1
        db.conn.commit()
        _estimate_game_from_moves(
            db, game_id, parsed, cfg["player"]["kgs_username"], ranks,
        )
    finally:
        if own_engine and engine is not None:
            engine.stop()
    return done


def backfill_human_sl_games(
    cfg: dict[str, Any],
    db: Database,
    *,
    since_year: int | None = None,
    limit: int = 500,
) -> int:
    """Human SL partie par partie (parties analysées sans données SL)."""
    year_sql, year_params = Database._since_year_sql(since_year, alias="g")
    game_ids = [
        row["id"]
        for row in db.conn.execute(
            f"""
            SELECT DISTINCT g.id
            FROM games g
            WHERE g.analyzed_deep = 1{year_sql}
              AND EXISTS (
                SELECT 1 FROM moves m
                WHERE m.game_id = g.id AND m.color = g.player_color
                  AND (
                    m.human_prior_json IS NULL OR m.human_prior_json = ''
                    OR m.human_rank_moves_json IS NULL OR m.human_rank_moves_json = ''
                  )
              )
            ORDER BY g.id
            LIMIT ?
            """,
            (*year_params, limit),
        ).fetchall()
    ]
    if not game_ids:
        return 0

    human_model = cfg.get("human_sl", {}).get("model") or cfg["katago"].get("human_model")
    if not human_model or not Path(human_model).exists():
        raise FileNotFoundError(
            "Modèle Human SL introuvable. Lancez scripts\\occasionnel\\run_install_human_sl.bat"
        )

    analysis_config = cfg.get("human_sl", {}).get("config", "config/katago_human.cfg")
    engine = KataGoAnalysis(
        cfg, analysis_config=analysis_config, human_model=human_model,
    )
    engine.start()
    enriched = 0
    try:
        for game_id in game_ids:
            n = enrich_human_sl_for_game(cfg, db, game_id, engine=engine)
            if n:
                enriched += n
                print(f"  Partie #{game_id} : {n} coup(s) Human SL", flush=True)
                out_json = resolve_path(cfg["paths"]["analysis_dir"]) / f"game_{game_id}_deep.json"
                db.export_analysis_json(game_id, out_json)
    finally:
        engine.stop()
    return enriched
