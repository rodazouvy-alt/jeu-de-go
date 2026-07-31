from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import resolve_path
from .db import Database
from .human_sl import enrich_human_sl_for_game
from .katago import KataGoAnalysis
from .report import generate_report
from .sgf_parse import (
    parse_sgf, game_phase, player_color,
    player_rank, opponent_rank, line_loss_from_scores, finalize_move_quality,
)
from .themes import theme_for_move, THEME_LABELS

SEVERITY_ORDER = ("ok", "inaccuracy", "mistake", "blunder", "mega_blunder")


def _print_katago_startup(engine: KataGoAnalysis) -> None:
    for line in engine.startup_log:
        low = line.lower()
        if "running with following config" in low:
            continue
        if any(
            key in line
            for key in (
                "numSearchThreads",
                "nnMaxBatchSize",
                "numAnalysisThreads",
                "cuda",
                "loaded neural net",
                "analysis engine",
            )
        ):
            print(f"  {line}", flush=True)


class GameAnalyzer:
    def __init__(self, cfg: dict[str, Any], db: Database):
        self.cfg = cfg
        self.db = db
        self.username = cfg["player"]["kgs_username"]
        self.thresholds = cfg["thresholds"]
        self.katago_cfg = cfg["katago"]
        self.analysis_dir = resolve_path(cfg["paths"]["analysis_dir"])
        self.vps = self.katago_cfg.get("visits_per_second", 600)
        self.auto_report = cfg.get("report", {}).get("auto_refresh", True)

    def _refresh_report(self) -> None:
        if not self.auto_report:
            return
        generate_report(self.db, self.cfg)
        latest = resolve_path(self.cfg["paths"]["reports_dir"]) / "latest.html"
        print(f"  Rapport mis à jour : {latest}", flush=True)

    def _eta_seconds(self, visits: int, n_positions: int) -> int:
        if self.vps <= 0:
            return 0
        return max(1, int(visits * n_positions / self.vps))

    def _min_severity_rank(self, name: str) -> int:
        min_sev = self.katago_cfg.get("deep_rerun_min_severity", name)
        try:
            return SEVERITY_ORDER.index(min_sev)
        except ValueError:
            return SEVERITY_ORDER.index("mistake")

    def _deep_middle_enabled(self) -> bool:
        return bool(self.cfg.get("analysis", {}).get("deep_middle_enabled", False))

    def _parallel_pipeline_enabled(self) -> bool:
        return bool(self.cfg.get("analysis", {}).get("parallel_pipeline", True))

    def _run_fuseki_pass(
        self, game_id: int, sgf_path: Path, engine: KataGoAnalysis,
    ) -> None:
        from .opening_analysis import OpeningAnalyzer

        opening = OpeningAnalyzer(self.cfg, self.db)
        opening.analyze_game_fuseki(game_id, sgf_path, engine=engine)

    def _complete_full_analysis(self, game_id: int) -> None:
        """Marque la passe complete (quick + fuseki, sans deep milieu legacy)."""
        self.db.mark_analyzed(game_id, "deep")

    @staticmethod
    def _format_analysis_error(exc: Exception) -> str:
        msg = str(exc).strip()
        if msg:
            return msg
        return f"{exc.__class__.__name__} (sans message)"

    @staticmethod
    def _is_unrecoverable_analysis_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "illegal move" in msg or "katago error" in msg

    def _skip_broken_game(self, game_id: int, exc: Exception) -> None:
        reason = self._format_analysis_error(exc)
        self.db.mark_analysis_skipped(game_id, reason)
        print(
            f"  Partie #{game_id} ignoree (non reessayee) : {reason}",
            flush=True,
        )

    def analyze_pending(
        self,
        mode: str = "quick",
        limit: int = 5,
        backfill_opponent: bool = False,
        since_year: int | None = None,
        katago_restart_sec: float | None = None,
        min_game_id: int | None = None,
    ) -> int:
        if katago_restart_sec is None:
            katago_restart_sec = float(
                self.katago_cfg.get("restart_every_seconds", 3600)
            )
        if backfill_opponent:
            pending = self.db.games_needing_opponent_backfill(limit=limit)
            if not pending:
                print("Aucune partie à compléter (stats adversaire déjà présentes).")
                return 0
            print(f"Complément stats adversaire : {len(pending)} partie(s)...", flush=True)
        else:
            pending = self.db.games_pending_analysis(
                mode=mode, limit=limit, since_year=since_year,
                min_game_id=min_game_id,
            )
            if not pending:
                scope = f" depuis {since_year}" if since_year else ""
                if min_game_id is not None:
                    scope += f" (id>={min_game_id})"
                print(f"Aucune partie en attente d'analyse{scope}.")
                return 0
            if since_year or min_game_id is not None:
                remaining = self.db.pending_deep_count(
                    since_year, min_game_id=min_game_id,
                ) if mode == "deep" else self.db.pending_analysis_count(
                    since_year, min_game_id=min_game_id,
                )
                extra = []
                if since_year:
                    extra.append(f"{since_year}+")
                if min_game_id is not None:
                    extra.append(f"id>={min_game_id}")
                label = " ".join(extra)
                print(f"Période {label} : ~{remaining} partie(s) en attente", flush=True)

        if (
            mode == "deep"
            and not backfill_opponent
            and self._parallel_pipeline_enabled()
        ):
            from .pipeline import run_pipelined_batch
            return run_pipelined_batch(
                self,
                list(pending),
                katago_restart_sec=katago_restart_sec,
            )

        analyzed = 0
        quick_config = self.katago_cfg["config"]
        deep_config = self.katago_cfg.get("deep_config", quick_config)
        quick_engine: KataGoAnalysis | None = None
        deep_engine: KataGoAnalysis | None = None
        shared_human_engine: KataGoAnalysis | None = None
        katago_session_start: float | None = None

        def _stop_all() -> None:
            _stop_quick()
            _stop_deep()
            _stop_human()

        def _mark_katago_session() -> None:
            nonlocal katago_session_start
            if katago_session_start is None:
                katago_session_start = time.perf_counter()

        def _ensure_katago_fresh() -> None:
            nonlocal katago_session_start
            if not katago_restart_sec or katago_restart_sec <= 0:
                return
            if katago_session_start is None:
                return
            elapsed = time.perf_counter() - katago_session_start
            if elapsed < katago_restart_sec:
                return
            mins = int(elapsed // 60)
            print(
                f"  [KataGo] Redemarrage preventif ({mins} min ecoulees)...",
                flush=True,
            )
            _stop_all()
            katago_session_start = None
            time.sleep(3)

        def _reset_katago_session() -> None:
            nonlocal katago_session_start
            katago_session_start = None

        def human_sl_engine() -> KataGoAnalysis | None:
            nonlocal shared_human_engine
            _ensure_katago_fresh()
            hcfg = self.cfg.get("human_sl", {})
            human_model = hcfg.get("model") or self.katago_cfg.get("human_model")
            if not human_model or not Path(human_model).exists():
                return None
            if shared_human_engine is None:
                analysis_config = hcfg.get("config", "config/katago_human.cfg")
                print("Démarrage KataGo (Human SL)...", flush=True)
                shared_human_engine = KataGoAnalysis(
                    self.cfg,
                    analysis_config=analysis_config,
                    human_model=human_model,
                )
                shared_human_engine.start()
                _print_katago_startup(shared_human_engine)
                _mark_katago_session()
            return shared_human_engine

        def quick() -> KataGoAnalysis:
            nonlocal quick_engine
            _ensure_katago_fresh()
            if quick_engine is None:
                print("Démarrage KataGo (quick)...", flush=True)
                quick_engine = KataGoAnalysis(self.cfg, analysis_config=quick_config)
                quick_engine.start()
                _print_katago_startup(quick_engine)
                _mark_katago_session()
            return quick_engine

        def deep() -> KataGoAnalysis:
            nonlocal deep_engine
            _ensure_katago_fresh()
            if deep_engine is None:
                print("Démarrage KataGo (deep)...", flush=True)
                deep_engine = KataGoAnalysis(self.cfg, analysis_config=deep_config)
                deep_engine.start()
                _print_katago_startup(deep_engine)
                _mark_katago_session()
            return deep_engine

        def _stop_quick() -> None:
            nonlocal quick_engine
            if quick_engine is not None:
                quick_engine.stop()
                quick_engine = None

        def _stop_deep() -> None:
            nonlocal deep_engine
            if deep_engine is not None:
                deep_engine.stop()
                deep_engine = None

        def _stop_human() -> None:
            nonlocal shared_human_engine
            if shared_human_engine is not None:
                shared_human_engine.stop()
                shared_human_engine = None

        try:
            if self.auto_report:
                self._refresh_report()
            lot_size = len(pending)
            for i, row in enumerate(pending, 1):
                _ensure_katago_fresh()
                game_id = row["id"]
                sgf_path = resolve_path(row["sgf_path"])
                print(
                    f"\nAnalyse [{mode}] partie #{game_id} ({i}/{lot_size}): "
                    f"{sgf_path.name}",
                    flush=True,
                )
                try:
                    if backfill_opponent:
                        self._analyze_one(
                            quick(), game_id, sgf_path,
                            self.katago_cfg["quick_max_visits"], "quick",
                            only_opponent_moves=True,
                        )
                    elif mode == "deep":
                        if not row["analyzed_quick"]:
                            print("  -> pass quick prealable...", flush=True)
                            self._analyze_one(
                                quick(), game_id, sgf_path,
                                self.katago_cfg["quick_max_visits"], "quick",
                            )
                        g_row = self.db.conn.execute(
                            "SELECT analyzed_opening FROM games WHERE id = ?",
                            (game_id,),
                        ).fetchone()
                        if not g_row or not g_row["analyzed_opening"]:
                            print("  -> passe fuseki F6...", flush=True)
                            self._run_fuseki_pass(game_id, sgf_path, deep())
                        if self._deep_middle_enabled():
                            self._analyze_deep(deep(), game_id, sgf_path)
                        elif not row["analyzed_deep"]:
                            print("  -> deep milieu desactive (quick conserve)", flush=True)
                            self._complete_full_analysis(game_id)
                    else:
                        self._analyze_one(
                            quick(), game_id, sgf_path,
                            self.katago_cfg["quick_max_visits"], mode,
                        )
                    if backfill_opponent:
                        analyzed += 1
                    else:
                        print("  -> Human SL + patterns...", flush=True)
                        self._enrich_after_analysis(game_id, human_sl_engine())
                        analyzed += 1
                    print(
                        f"  Partie #{game_id} terminee ({analyzed}/{lot_size} du lot).",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"  ERREUR partie #{game_id}: {exc}", flush=True)
                    if self._is_unrecoverable_analysis_error(exc):
                        self._skip_broken_game(game_id, exc)
                        analyzed += 1
                    _stop_all()
                    _reset_katago_session()
        finally:
            _stop_all()

        return analyzed

    def _enrich_after_analysis(
        self,
        game_id: int,
        human_engine: KataGoAnalysis | None,
    ) -> None:
        if human_engine is not None:
            n_hs = enrich_human_sl_for_game(
                self.cfg, self.db, game_id, engine=human_engine,
            )
            if n_hs:
                print(f"  Human SL: {n_hs} coup(s) enrichi(s)", flush=True)
            else:
                print("  Human SL: rien a enrichir sur cette partie", flush=True)
        acfg = self.cfg.get("analysis", {})
        if acfg.get("mark_opening_on_complete", False):
            self.db.mark_opening_analyzed(game_id)
        out_json = self.analysis_dir / f"game_{game_id}_deep.json"
        self.db.export_analysis_json(game_id, out_json)
        print(f"  Partie #{game_id} enrichie — suivante...", flush=True)

    def _analyze_deep(self, engine: KataGoAnalysis, game_id: int, sgf_path: Path) -> None:
        """Re-analyse les coups suspects (mistake+ à visits_std, mega à visits_hard)."""
        parsed = parse_sgf(sgf_path)
        my_color = player_color(parsed, self.username)
        if not my_color:
            raise RuntimeError("Joueur non trouvé dans le SGF")

        min_rank = self._min_severity_rank("mistake")
        suspects = []
        for r in self.db.conn.execute(
            "SELECT move_number, severity, point_loss FROM moves WHERE game_id = ?",
            (game_id,),
        ):
            try:
                if SEVERITY_ORDER.index(r["severity"] or "ok") >= min_rank:
                    suspects.append(r)
            except ValueError:
                continue

        if not suspects:
            print("  Aucun coup suspect — deep ignoré")
            self.db.mark_analyzed(game_id, "deep")
            return

        visits_std = self.katago_cfg["deep_max_visits"]
        visits_hard = self.katago_cfg["deep_hard_max_visits"]

        turns_std = [
            r["move_number"] - 1 for r in suspects
            if r["severity"] in ("mistake", "blunder")
        ]
        turns_hard = [
            r["move_number"] - 1 for r in suspects
            if r["severity"] == "mega_blunder"
        ]

        all_responses: dict[int, dict] = {}

        if turns_std:
            eta = self._eta_seconds(visits_std, len(turns_std))
            print(f"  Deep {visits_std} visits × {len(turns_std)} coups (~{eta}s)...")
            for batch in self._analyze_turns(
                engine, parsed, game_id, turns_std, visits_std
            ):
                all_responses[batch.get("turnNumber")] = batch

        if turns_hard:
            eta = self._eta_seconds(visits_hard, len(turns_hard))
            print(f"  Deep {visits_hard} visits × {len(turns_hard)} coups durs (~{eta}s)...")
            for batch in self._analyze_turns(
                engine, parsed, game_id, turns_hard, visits_hard
            ):
                all_responses[batch.get("turnNumber")] = batch

        self._apply_responses(game_id, parsed, my_color, all_responses, suspects)
        self.db.mark_analyzed(game_id, "deep")
        out_json = self.analysis_dir / f"game_{game_id}_deep.json"
        self.db.export_analysis_json(game_id, out_json)
        print(f"  Deep terminé — {len(suspects)} coup(s) re-analysé(s)")

    def _analyze_turns(
        self,
        engine: KataGoAnalysis,
        parsed,
        game_id: int,
        turns: list[int],
        max_visits: int,
    ) -> list[dict]:
        return engine.analyze_game(
            moves=parsed.moves,
            komi=parsed.komi,
            board_size=parsed.board_size,
            rules=parsed.rules or "chinese",
            max_visits=max_visits,
            game_id=f"game_{game_id}_deep_{max_visits}",
            analyze_turns=turns,
            timeout_seconds=max(600, self._eta_seconds(max_visits, len(turns)) + 120),
            initial_stones=parsed.initial_stones,
        )

    def _apply_responses(
        self,
        game_id: int,
        parsed,
        my_color: str,
        turn_data_by_turn: dict[int, dict],
        suspects: list,
    ) -> None:
        suspect_turns = {r["move_number"] - 1 for r in suspects}

        for move_idx in sorted(suspect_turns):
            turn_data = turn_data_by_turn.get(move_idx)
            if not turn_data:
                continue

            color, coord = parsed.moves[move_idx]
            if color != my_color:
                continue

            played = coord if coord else "pass"
            move_infos = turn_data.get("moveInfos", [])
            played_score, best_move = KataGoAnalysis.score_lead(move_infos, played)
            played_wr = KataGoAnalysis.winrate(move_infos, played)
            top_moves = KataGoAnalysis.top_moves_from_infos(move_infos, n=5)
            move_scores = KataGoAnalysis.scores_map_from_infos(move_infos)

            prev_row = self.db.conn.execute(
                "SELECT score_before, winrate_before FROM moves WHERE game_id=? AND move_number=?",
                (game_id, move_idx + 1),
            ).fetchone()
            prev_score = prev_row["score_before"] if prev_row else None
            prev_wr = prev_row["winrate_before"] if prev_row else None

            position_score = KataGoAnalysis.turn_position_score(turn_data)
            point_loss = KataGoAnalysis.point_loss_vs_best(move_infos, played, color)
            ref_score = position_score if position_score is not None else prev_score
            line_loss = line_loss_from_scores(ref_score, played_score, color)

            player_name = parsed.black if color == "B" else parsed.white

            record = finalize_move_quality({
                "move_number": move_idx + 1,
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
                "phase": game_phase(move_idx + 1),
                "top_moves_json": json.dumps(top_moves, ensure_ascii=False),
                "move_scores_json": json.dumps(move_scores, ensure_ascii=False),
            }, player_color=color, thresholds=self.thresholds)
            self.db.save_move_analysis(game_id, record)

    def _analyze_one(
        self,
        engine: KataGoAnalysis,
        game_id: int,
        sgf_path: Path,
        max_visits: int,
        mode: str,
        only_opponent_moves: bool = False,
    ) -> None:
        parsed = parse_sgf(sgf_path)
        my_color = player_color(parsed, self.username)
        if not my_color:
            raise RuntimeError("Joueur non trouvé dans le SGF")
        n_moves = len(parsed.moves)
        self.db.update_game_ranks(
            game_id,
            player_rank(parsed, self.username),
            opponent_rank(parsed, self.username),
        )
        eta = self._eta_seconds(max_visits, n_moves)
        print(f"  {max_visits} visits × {n_moves} coups (~{eta}s)...", flush=True)
        t0 = time.perf_counter()

        responses = engine.analyze_game(
            moves=parsed.moves,
            komi=parsed.komi,
            board_size=parsed.board_size,
            rules=parsed.rules or "chinese",
            max_visits=max_visits,
            game_id=f"game_{game_id}",
            timeout_seconds=max(300, eta + 60),
            initial_stones=parsed.initial_stones,
        )

        if not responses:
            raise RuntimeError("KataGo n'a renvoyé aucune analyse")

        turn_data_by_turn = {r.get("turnNumber"): r for r in responses}
        prev_score: float | None = None
        prev_winrate: float | None = None

        if 0 in turn_data_by_turn:
            root_infos = turn_data_by_turn[0].get("moveInfos", [])
            if root_infos:
                prev_score = root_infos[0].get("scoreLead")
                prev_winrate = root_infos[0].get("winrate")

        for move_idx, (color, coord) in enumerate(parsed.moves):
            turn = move_idx
            turn_data = turn_data_by_turn.get(turn)
            if not turn_data:
                continue

            played = coord if coord else "pass"
            move_infos = turn_data.get("moveInfos", [])

            played_score, best_move = KataGoAnalysis.score_lead(move_infos, played)
            played_wr = KataGoAnalysis.winrate(move_infos, played)
            top_moves = KataGoAnalysis.top_moves_from_infos(move_infos, n=5)
            move_scores = (
                KataGoAnalysis.scores_map_from_infos(move_infos)
                if color == my_color else None
            )

            position_score = KataGoAnalysis.turn_position_score(turn_data)
            point_loss = KataGoAnalysis.point_loss_vs_best(move_infos, played, color)
            ref_score = position_score if position_score is not None else prev_score
            line_loss = line_loss_from_scores(ref_score, played_score, color)

            player_name = parsed.black if color == "B" else parsed.white
            move_theme = (
                theme_for_move(
                    parsed.moves, move_idx, parsed.board_size,
                    initial_stones=parsed.initial_stones,
                )
                if color == my_color else None
            )
            if only_opponent_moves and color == my_color:
                if played_score is not None:
                    prev_score = played_score
                if played_wr is not None:
                    prev_winrate = played_wr
                continue

            record = finalize_move_quality({
                "move_number": move_idx + 1,
                "color": color,
                "coord": coord or "pass",
                "player": player_name,
                "score_before": ref_score,
                "score_after": played_score,
                "point_loss": round(point_loss, 2),
                "line_loss": round(line_loss, 2),
                "winrate_before": prev_winrate,
                "winrate_after": played_wr,
                "best_move": best_move,
                "played_move": played,
                "phase": game_phase(move_idx + 1),
                "theme": move_theme,
                "top_moves_json": (
                    json.dumps(top_moves, ensure_ascii=False) if color == my_color else None
                ),
                "move_scores_json": (
                    json.dumps(move_scores, ensure_ascii=False) if move_scores else None
                ),
            }, player_color=color, thresholds=self.thresholds)
            self.db.save_move_analysis(game_id, record)

            if played_score is not None:
                prev_score = played_score
            if played_wr is not None:
                prev_winrate = played_wr

        if only_opponent_moves:
            n_opp = self.db.conn.execute(
                "SELECT COUNT(*) AS c FROM moves m JOIN games g ON g.id=m.game_id "
                "WHERE m.game_id=? AND m.color!=g.player_color",
                (game_id,),
            ).fetchone()["c"]
            print(
                f"  Adversaire complété — {n_opp} coup(s) ({time.perf_counter() - t0:.0f}s GPU)",
                flush=True,
            )
            return

        self.db.mark_analyzed(game_id, mode)
        out_json = self.analysis_dir / f"game_{game_id}_{mode}.json"
        self.db.export_analysis_json(game_id, out_json)
        blunders = self.db.conn.execute(
            "SELECT COUNT(*) AS c FROM moves WHERE game_id = ? AND severity IN ('blunder','mega_blunder')",
            (game_id,),
        ).fetchone()["c"]
        print(
            f"  Terminé — {blunders} blunder(s) détecté(s) ({time.perf_counter() - t0:.0f}s GPU)",
            flush=True,
        )
