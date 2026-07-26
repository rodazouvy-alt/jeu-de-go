from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import resolve_path
from .db import Database
from .katago import KataGoAnalysis
from .sgf_parse import parse_sgf, game_phase, severity_for_loss, player_color

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

    def _eta_seconds(self, visits: int, n_positions: int) -> int:
        if self.vps <= 0:
            return 0
        return max(1, int(visits * n_positions / self.vps))

    def _min_severity_rank(self, name: str) -> int:
        min_sev = self.katago_cfg.get("deep_rerun_min_severity", "mistake")
        try:
            return SEVERITY_ORDER.index(min_sev)
        except ValueError:
            return SEVERITY_ORDER.index("mistake")

    def analyze_pending(self, mode: str = "quick", limit: int = 5) -> int:
        pending = self.db.games_pending_analysis(mode=mode, limit=limit)
        if not pending:
            print("Aucune partie en attente d'analyse.")
            return 0

        analyzed = 0
        quick_config = self.katago_cfg["config"]
        deep_config = self.katago_cfg.get("deep_config", quick_config)
        quick_engine: KataGoAnalysis | None = None
        deep_engine: KataGoAnalysis | None = None

        def quick() -> KataGoAnalysis:
            nonlocal quick_engine
            if quick_engine is None:
                print("Démarrage KataGo (quick) — chargement du modèle une seule fois...", flush=True)
                quick_engine = KataGoAnalysis(self.cfg, analysis_config=quick_config)
                quick_engine.start()
                _print_katago_startup(quick_engine)
            return quick_engine

        def deep() -> KataGoAnalysis:
            nonlocal deep_engine
            if deep_engine is None:
                print("Démarrage KataGo (deep) — chargement du modèle une seule fois...", flush=True)
                deep_engine = KataGoAnalysis(self.cfg, analysis_config=deep_config)
                deep_engine.start()
                _print_katago_startup(deep_engine)
            return deep_engine

        try:
            for row in pending:
                game_id = row["id"]
                sgf_path = resolve_path(row["sgf_path"])
                print(f"\nAnalyse [{mode}] partie #{game_id}: {sgf_path.name}", flush=True)
                try:
                    if mode == "deep":
                        if not row["analyzed_quick"]:
                            print("  → pass quick préalable...", flush=True)
                            self._analyze_one(
                                quick(), game_id, sgf_path,
                                self.katago_cfg["quick_max_visits"], "quick",
                            )
                        self._analyze_deep(deep(), game_id, sgf_path)
                    else:
                        self._analyze_one(
                            quick(), game_id, sgf_path,
                            self.katago_cfg["quick_max_visits"], mode,
                        )
                    analyzed += 1
                except Exception as exc:
                    print(f"  ERREUR: {exc}", flush=True)
        finally:
            if quick_engine is not None:
                quick_engine.stop()
            if deep_engine is not None:
                deep_engine.stop()

        return analyzed

    def _analyze_deep(self, engine: KataGoAnalysis, game_id: int, sgf_path: Path) -> None:
        """Re-analyse uniquement les coups suspects à 12k-24k visits."""
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

            prev_row = self.db.conn.execute(
                "SELECT score_before, winrate_before FROM moves WHERE game_id=? AND move_number=?",
                (game_id, move_idx + 1),
            ).fetchone()
            prev_score = prev_row["score_before"] if prev_row else None
            prev_wr = prev_row["winrate_before"] if prev_row else None

            point_loss = 0.0
            if prev_score is not None and played_score is not None:
                if my_color == "B":
                    point_loss = max(0.0, prev_score - played_score)
                else:
                    point_loss = max(0.0, played_score - prev_score)

            player_name = parsed.black if color == "B" else parsed.white
            severity = severity_for_loss(point_loss, self.thresholds)

            self.db.save_move_analysis(game_id, {
                "move_number": move_idx + 1,
                "color": color,
                "coord": coord or "pass",
                "player": player_name,
                "score_before": prev_score,
                "score_after": played_score,
                "point_loss": round(point_loss, 2),
                "winrate_before": prev_wr,
                "winrate_after": played_wr,
                "best_move": best_move,
                "played_move": played,
                "severity": severity,
                "phase": game_phase(move_idx + 1),
            })

    def _analyze_one(
        self,
        engine: KataGoAnalysis,
        game_id: int,
        sgf_path: Path,
        max_visits: int,
        mode: str,
    ) -> None:
        parsed = parse_sgf(sgf_path)
        my_color = player_color(parsed, self.username)
        n_moves = len(parsed.moves)
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

            point_loss = 0.0
            if prev_score is not None and played_score is not None and color == my_color:
                if my_color == "B":
                    point_loss = max(0.0, prev_score - played_score)
                elif my_color == "W":
                    point_loss = max(0.0, played_score - prev_score)

            player_name = parsed.black if color == "B" else parsed.white

            if color == my_color:
                severity = severity_for_loss(point_loss, self.thresholds)
                self.db.save_move_analysis(game_id, {
                    "move_number": move_idx + 1,
                    "color": color,
                    "coord": coord or "pass",
                    "player": player_name,
                    "score_before": prev_score,
                    "score_after": played_score,
                    "point_loss": round(point_loss, 2),
                    "winrate_before": prev_winrate,
                    "winrate_after": played_wr,
                    "best_move": best_move,
                    "played_move": played,
                    "severity": severity,
                    "phase": game_phase(move_idx + 1),
                })

            if played_score is not None:
                prev_score = played_score
            if played_wr is not None:
                prev_winrate = played_wr

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
