from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import resolve_path
from .db import Database
from .katago import KataGoAnalysis
from .sgf_parse import parse_sgf, game_phase, severity_for_loss, player_color


class GameAnalyzer:
    def __init__(self, cfg: dict[str, Any], db: Database):
        self.cfg = cfg
        self.db = db
        self.username = cfg["player"]["kgs_username"]
        self.thresholds = cfg["thresholds"]
        self.analysis_dir = resolve_path(cfg["paths"]["analysis_dir"])

    def analyze_pending(self, mode: str = "quick", limit: int = 5) -> int:
        max_visits = (
            self.cfg["katago"]["deep_max_visits"]
            if mode == "deep"
            else self.cfg["katago"]["quick_max_visits"]
        )
        pending = self.db.games_pending_analysis(mode=mode, limit=limit)
        if not pending:
            print("Aucune partie en attente d'analyse.")
            return 0

        analyzed = 0
        with KataGoAnalysis(self.cfg) as engine:
            for row in pending:
                game_id = row["id"]
                sgf_path = resolve_path(row["sgf_path"])
                print(f"\nAnalyse [{mode}] partie #{game_id}: {sgf_path.name}")
                try:
                    self._analyze_one(engine, game_id, sgf_path, max_visits, mode)
                    analyzed += 1
                except Exception as exc:
                    print(f"  ERREUR: {exc}")
        return analyzed

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

        responses = engine.analyze_game(
            moves=parsed.moves,
            komi=parsed.komi,
            board_size=parsed.board_size,
            rules=parsed.rules or "chinese",
            max_visits=max_visits,
            game_id=f"game_{game_id}",
        )

        if not responses:
            raise RuntimeError("KataGo n'a renvoyé aucune analyse")

        turn_data_by_turn = {r.get("turnNumber"): r for r in responses}
        prev_score: float | None = None
        prev_winrate: float | None = None

        # Position initiale
        if 0 in turn_data_by_turn:
            root_infos = turn_data_by_turn[0].get("moveInfos", [])
            if root_infos:
                prev_score = root_infos[0].get("scoreLead")
                prev_winrate = root_infos[0].get("winrate")

        for move_idx, (color, coord) in enumerate(parsed.moves):
            turn = move_idx  # position AVANT le coup move_idx
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
        print(f"  Terminé — {blunders} blunder(s) détecté(s)")
