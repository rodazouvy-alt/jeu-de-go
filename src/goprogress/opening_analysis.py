from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .analyzer import _print_katago_startup
from .config import resolve_path
from .db import Database
from .katago import KataGoAnalysis
from .sgf_parse import (
    parse_sgf,
    game_phase,
    player_color,
    player_rank,
    opponent_rank,
    line_loss_from_scores,
    finalize_move_quality,
    effective_move_quality,
    quick_top12_gap,
)
from .themes import theme_for_move


class OpeningAnalyzer:
    """Passe fuseki ciblee (strategie F6 labo) sur les coups joueur 1-N."""

    def __init__(self, cfg: dict[str, Any], db: Database):
        self.cfg = cfg
        self.db = db
        self.username = cfg["player"]["kgs_username"]
        self.thresholds = cfg["thresholds"]
        self.katago_cfg = cfg["katago"]
        ocfg = cfg.get("opening_analysis", {})
        self.max_moves = int(ocfg.get("max_moves", 30))
        self.max_visits = int(ocfg.get("max_visits", 1000))
        self.hard_max_visits = int(
            ocfg.get("hard_max_visits", self.katago_cfg.get("deep_hard_max_visits", 2000))
        )
        self.min_point_loss = float(ocfg.get("min_point_loss", 0.5))
        self.ambiguous_gap = float(ocfg.get("ambiguous_gap", 0.5))
        self.player_moves_only = bool(ocfg.get("player_moves_only", True))
        self.strategy = str(ocfg.get("strategy", "f6")).lower()
        self.analysis_dir = resolve_path(cfg["paths"]["analysis_dir"])
        self.vps = self.katago_cfg.get("visits_per_second", 600)
        self.deep_config = self.katago_cfg.get("deep_config", self.katago_cfg["config"])

    def _eta_seconds(self, visits: int, n_positions: int) -> int:
        if self.vps <= 0:
            return 0
        return max(1, int(visits * n_positions / self.vps))

    def fuseki_visits_for_move(
        self, move: dict[str, Any], player_color_val: str,
    ) -> int | None:
        """Visits KataGo pour ce coup fuseki, ou None = garder le quick."""
        if self.strategy in ("legacy", "all_400", "full_400"):
            return self.max_visits if self.max_visits != 400 else 400

        pl, sev = effective_move_quality(
            move, player_color=player_color_val, thresholds=self.thresholds,
        )
        if pl <= self.min_point_loss and sev == "ok":
            return None

        if self.strategy == "f5":
            return self.max_visits

        # F6 (defaut) : ambigus -> hard, sinon standard
        gap = quick_top12_gap(move, player_color_val)
        if gap is not None and gap < self.ambiguous_gap:
            return self.hard_max_visits
        return self.max_visits

    def _plan_fuseki_turns(
        self, parsed, game_id: int, my_color: str,
    ) -> list[tuple[int, int, int]]:
        """[(move_number, turn_idx, visits), ...]"""
        rows = self.db.conn.execute(
            """
            SELECT m.* FROM moves m
            WHERE m.game_id = ? AND m.color = ? AND m.move_number <= ?
            ORDER BY m.move_number
            """,
            (game_id, my_color, self.max_moves),
        ).fetchall()

        plan: list[tuple[int, int, int]] = []
        for row in rows:
            move = dict(row)
            visits = self.fuseki_visits_for_move(move, my_color)
            if visits is None:
                continue
            mn = int(move["move_number"])
            plan.append((mn, mn - 1, visits))
        return plan

    def analyze_pending(
        self,
        *,
        limit: int = 5,
        min_game_id: int | None = None,
        max_game_id: int | None = None,
        since_year: int | None = None,
        katago_restart_sec: float | None = None,
        force: bool = False,
    ) -> int:
        if katago_restart_sec is None:
            katago_restart_sec = float(
                self.katago_cfg.get("restart_every_seconds", 3600)
            )

        pending = self.db.games_pending_opening(
            limit=limit,
            min_game_id=min_game_id,
            max_game_id=max_game_id,
            since_year=since_year,
            force=force,
            require_quick=not force,
        )
        if not pending:
            print("Aucune partie en attente d'analyse fuseki.")
            return 0

        remaining = self.db.pending_opening_count(
            min_game_id=min_game_id,
            max_game_id=max_game_id,
            since_year=since_year,
            force=force,
            require_quick=not force,
        )
        print(
            f"Fuseki ({self.strategy}) : ~{remaining} partie(s) — "
            f"{self.max_visits}/{self.hard_max_visits}v, coups 1-{self.max_moves}",
            flush=True,
        )

        analyzed = 0
        engine: KataGoAnalysis | None = None
        katago_session_start: float | None = None

        def _stop_engine() -> None:
            nonlocal engine
            if engine is not None:
                engine.stop()
                engine = None

        def _ensure_fresh() -> None:
            nonlocal katago_session_start
            if not katago_restart_sec or katago_session_start is None:
                return
            if time.perf_counter() - katago_session_start < katago_restart_sec:
                return
            print("  [KataGo] Redemarrage preventif fuseki...", flush=True)
            _stop_engine()
            katago_session_start = None
            time.sleep(3)

        def _engine() -> KataGoAnalysis:
            nonlocal engine, katago_session_start
            _ensure_fresh()
            if engine is None:
                print("Demarrage KataGo (fuseki)...", flush=True)
                engine = KataGoAnalysis(
                    self.cfg, analysis_config=resolve_path(self.deep_config),
                )
                engine.start()
                _print_katago_startup(engine)
                katago_session_start = time.perf_counter()
            return engine

        try:
            for i, row in enumerate(pending, 1):
                game_id = row["id"]
                sgf_path = resolve_path(row["sgf_path"])
                print(
                    f"\nFuseki #{game_id} ({i}/{len(pending)}): {sgf_path.name}",
                    flush=True,
                )
                try:
                    self.analyze_game_fuseki(game_id, sgf_path, engine=_engine())
                    analyzed += 1
                except Exception as exc:
                    print(f"  ERREUR #{game_id}: {exc}", flush=True)
                    _stop_engine()
                    katago_session_start = None
        finally:
            _stop_engine()

        return analyzed

    def analyze_game_fuseki(
        self,
        game_id: int,
        sgf_path: Path,
        *,
        engine: KataGoAnalysis | None = None,
    ) -> int:
        """Re-analyse fuseki F6. Retourne le nombre de coups re-analyses."""
        own_engine = False
        if engine is None:
            engine = KataGoAnalysis(
                self.cfg, analysis_config=resolve_path(self.deep_config),
            )
            engine.start()
            own_engine = True

        try:
            return self._analyze_fuseki_one(engine, game_id, sgf_path)
        finally:
            if own_engine:
                engine.stop()

    def _analyze_fuseki_one(
        self,
        engine: KataGoAnalysis,
        game_id: int,
        sgf_path: Path,
    ) -> int:
        parsed = parse_sgf(sgf_path)
        my_color = player_color(parsed, self.username)
        if not my_color:
            raise RuntimeError("Joueur non trouve dans le SGF")

        quick_ok = self.db.conn.execute(
            "SELECT analyzed_quick FROM games WHERE id = ?", (game_id,),
        ).fetchone()
        if not quick_ok or not quick_ok["analyzed_quick"]:
            raise RuntimeError(f"Partie #{game_id} : quick requis avant fuseki")

        plan = self._plan_fuseki_turns(parsed, game_id, my_color)
        if not plan:
            print("  Aucun coup fuseki a approfondir — quick conserve")
            self.db.mark_opening_analyzed(game_id)
            return 0

        by_visits: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for mn, turn_idx, visits in plan:
            by_visits[visits].append((mn, turn_idx))

        total_eta = sum(
            self._eta_seconds(v, len(items)) for v, items in by_visits.items()
        )
        print(
            f"  Fuseki {len(plan)} coup(s) cible(s) (~{total_eta}s)...",
            flush=True,
        )
        t0 = time.perf_counter()
        turn_data_by_turn: dict[int, dict] = {}

        for visits, items in sorted(by_visits.items()):
            turns = [ti for _, ti in items]
            print(f"    {visits}v x {len(turns)} coups...", flush=True)
            responses = engine.analyze_game(
                moves=parsed.moves,
                komi=parsed.komi,
                board_size=parsed.board_size,
                rules=parsed.rules or "chinese",
                max_visits=visits,
                game_id=f"game_{game_id}_fuseki_{visits}",
                analyze_turns=turns,
                timeout_seconds=max(300, self._eta_seconds(visits, len(turns)) + 120),
                initial_stones=parsed.initial_stones,
            )
            for r in responses:
                turn_data_by_turn[r.get("turnNumber")] = r

        n_updated = 0
        for mn, turn_idx, _visits in plan:
            turn_data = turn_data_by_turn.get(turn_idx)
            if not turn_data:
                continue
            color, coord = parsed.moves[turn_idx]
            if color != my_color:
                continue

            played = coord if coord else "pass"
            move_infos = turn_data.get("moveInfos", [])
            played_score, best_move = KataGoAnalysis.score_lead(move_infos, played)
            played_wr = KataGoAnalysis.winrate(move_infos, played)
            top_moves = KataGoAnalysis.top_moves_from_infos(move_infos, n=5)
            move_scores = KataGoAnalysis.scores_map_from_infos(move_infos)

            prev_row = self.db.conn.execute(
                "SELECT score_before, winrate_before FROM moves "
                "WHERE game_id=? AND move_number=?",
                (game_id, mn),
            ).fetchone()
            prev_score = prev_row["score_before"] if prev_row else None
            prev_wr = prev_row["winrate_before"] if prev_row else None

            position_score = KataGoAnalysis.turn_position_score(turn_data)
            point_loss = KataGoAnalysis.point_loss_vs_best(move_infos, played, color)
            ref_score = position_score if position_score is not None else prev_score
            line_loss = line_loss_from_scores(ref_score, played_score, color)

            player_name = parsed.black if color == "B" else parsed.white
            move_theme = theme_for_move(
                parsed.moves, turn_idx, parsed.board_size,
                initial_stones=parsed.initial_stones,
            )

            record = finalize_move_quality({
                "move_number": mn,
                "color": color,
                "coord": coord or "pass",
                "player": player_name,
                "score_before": ref_score,
                "score_after": played_score,
                "point_loss": round(point_loss, 2),
                "line_loss": round(line_loss, 2),
                "winrate_before": prev_wr,
                "winrate_after": played_wr,
                "best_move": best_move,
                "played_move": played,
                "phase": game_phase(mn),
                "theme": move_theme,
                "top_moves_json": json.dumps(top_moves, ensure_ascii=False),
                "move_scores_json": json.dumps(move_scores, ensure_ascii=False),
            }, player_color=color, thresholds=self.thresholds)
            self.db.save_move_analysis(game_id, record)
            n_updated += 1

        self.db.mark_opening_analyzed(game_id)
        out_json = self.analysis_dir / f"game_{game_id}_opening.json"
        self.db.export_opening_json(game_id, self.max_moves, out_json)
        print(
            f"  Fuseki termine — {n_updated} coup(s) ({time.perf_counter() - t0:.0f}s GPU)",
            flush=True,
        )
        return n_updated
