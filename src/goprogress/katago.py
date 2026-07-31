from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import resolve_path

_GTP_COLS = "ABCDEFGHJKLMNOPQRST"


def gtp_to_policy_index(coord: str | None, board_size: int) -> int:
    if not coord or coord.lower() == "pass":
        return board_size * board_size
    col = _GTP_COLS.index(coord[0].upper())
    row = int(coord[1:]) - 1
    return row * board_size + col


def policy_index_to_gtp(index: int, board_size: int) -> str:
    if index >= board_size * board_size:
        return "pass"
    row, col = divmod(index, board_size)
    return f"{_GTP_COLS[col]}{row + 1}"


class KataGoAnalysis:
    """Wrapper autour du KataGo Analysis Engine (stdin/stdout JSON)."""

    STARTUP_TIMEOUT = 120
    STARTUP_MARKERS = ("ready to begin", "loaded neural net")

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        analysis_config: str | None = None,
        human_model: str | Path | None = None,
    ):
        katago = cfg["katago"]
        self.executable = Path(katago["executable"])
        self.model = Path(katago["model"])
        human_path = human_model or katago.get("human_model")
        self.human_model = Path(human_path) if human_path else None
        config_path = analysis_config or katago["config"]
        self.config = (
            resolve_path(config_path)
            if not Path(config_path).is_absolute()
            else Path(config_path)
        )
        self.working_dir = Path(katago["working_dir"])
        self.startup_wait = katago.get("startup_wait_seconds", 25)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stderr_thread: threading.Thread | None = None
        self.startup_log: list[str] = []

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return

        cmd = [
            str(self.executable),
            "analysis",
            "-config", str(self.config),
            "-model", str(self.model),
        ]
        if self.human_model and self.human_model.exists():
            cmd.extend(["-human-model", str(self.human_model)])
        self._ready.clear()
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(self.working_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(target=self._consume_stderr, daemon=True)
        self._stderr_thread.start()

        if not self._ready.wait(timeout=self.STARTUP_TIMEOUT):
            # Fallback : délai fixe pour chargement du modèle 28b
            time.sleep(self.startup_wait)
            self._ready.set()

    def _consume_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self.startup_log.append(line.rstrip())
            low = line.lower()
            if any(m in low for m in self.STARTUP_MARKERS):
                self._ready.set()
            if "loaded neural net" in low:
                time.sleep(0.5)
                self._ready.set()
            elif "cuda backend" in low:
                time.sleep(1)
                self._ready.set()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write('{"id":"quit","action":"quit"}\n')
                self._proc.stdin.flush()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        self._proc = None

    def __enter__(self) -> "KataGoAnalysis":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def analyze_game(
        self,
        moves: list[tuple[str, str | None]],
        komi: float,
        board_size: int = 19,
        rules: str = "chinese",
        max_visits: int = 200,
        game_id: str = "game",
        analyze_turns: list[int] | None = None,
        timeout_seconds: int = 600,
        *,
        initial_stones: list[tuple[str, str]] | None = None,
        include_policy: bool = False,
        override_settings: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self._proc or self._proc.poll() is not None:
            raise RuntimeError("KataGo analysis engine non démarré")

        katago_moves = []
        for color, coord in moves:
            katago_moves.append([color, coord if coord else "pass"])

        turns = analyze_turns if analyze_turns is not None else list(range(len(moves) + 1))
        expected = set(turns)

        query = {
            "id": game_id,
            "moves": katago_moves,
            "rules": rules,
            "komi": komi,
            "boardXSize": board_size,
            "boardYSize": board_size,
            "analyzeTurns": turns,
            "maxVisits": max_visits,
            "includeOwnership": False,
            "includePolicy": include_policy,
        }
        if initial_stones:
            query["initialStones"] = [
                [color, coord] for color, coord in initial_stones
            ]
        if override_settings:
            query["overrideSettings"] = override_settings

        with self._lock:
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(json.dumps(query) + "\n")
            self._proc.stdin.flush()

            responses: list[dict[str, Any]] = []
            deadline = time.time() + timeout_seconds

            while expected and time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    if self._proc.poll() is not None:
                        break
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("id") != game_id:
                    continue
                if "error" in data:
                    raise RuntimeError(f"KataGo error: {data['error']}")
                if "turnNumber" in data:
                    responses.append(data)
                    expected.discard(data["turnNumber"])

            return sorted(responses, key=lambda r: r.get("turnNumber", 0))

    @staticmethod
    def top_moves_from_infos(
        move_infos: list[dict], n: int = 5,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for info in move_infos[:n]:
            out.append({
                "move": info.get("move"),
                "scoreLead": info.get("scoreLead"),
                "winrate": info.get("winrate"),
                "visits": info.get("visits"),
                "pv": info.get("pv"),
            })
        return out

    @staticmethod
    def scores_map_from_infos(move_infos: list[dict]) -> dict[str, float]:
        """scoreLead brut (perspective Noir) pour chaque coup dans moveInfos."""
        out: dict[str, float] = {}
        for info in move_infos:
            mv = info.get("move")
            sl = info.get("scoreLead")
            if mv and sl is not None:
                out[str(mv).upper().strip()] = float(sl)
        return out

    @staticmethod
    def best_score_lead(move_infos: list[dict]) -> float | None:
        if not move_infos:
            return None
        sl = move_infos[0].get("scoreLead")
        return float(sl) if sl is not None else None

    @staticmethod
    def point_loss_vs_best(
        move_infos: list[dict], played: str | None, color: str,
    ) -> float:
        """Perte en points vs le coup #1 KataGo (perspective joueur, unifié)."""
        import json as _json
        from .sgf_parse import point_loss_from_stored_scores

        pseudo = {
            "played_move": played,
            "top_moves_json": _json.dumps(
                KataGoAnalysis.top_moves_from_infos(move_infos, n=5),
                ensure_ascii=False,
            ),
            "move_scores_json": _json.dumps(
                KataGoAnalysis.scores_map_from_infos(move_infos),
                ensure_ascii=False,
            ),
        }
        pl = point_loss_from_stored_scores(pseudo, color)
        return pl if pl is not None else 0.0

    @staticmethod
    def turn_position_score(turn_data: dict[str, Any]) -> float | None:
        """Score de la position avant de jouer (rootInfo KataGo si dispo)."""
        root = turn_data.get("rootInfo") or {}
        sl = root.get("scoreLead")
        return float(sl) if sl is not None else None

    @staticmethod
    def score_lead(move_infos: list[dict], played: str | None) -> tuple[float | None, str | None]:
        if not move_infos:
            return None, None
        best = move_infos[0]
        best_move = best.get("move")
        played_score = None
        for info in move_infos:
            mv = info.get("move")
            if mv == played or (played in (None, "pass") and mv == "pass"):
                played_score = info.get("scoreLead")
                break
        if played_score is None:
            played_score = best.get("scoreLead")
        return played_score, best_move

    @staticmethod
    def winrate(move_infos: list[dict], played: str | None) -> float | None:
        for info in move_infos:
            mv = info.get("move")
            if mv == played or (played in (None, "pass") and mv == "pass"):
                return info.get("winrate")
        return move_infos[0].get("winrate") if move_infos else None

    @staticmethod
    def human_prior(move_infos: list[dict], played: str | None) -> float | None:
        for info in move_infos:
            mv = info.get("move")
            if mv == played or (played in (None, "pass") and mv == "pass"):
                val = info.get("humanPrior")
                if val is not None:
                    return float(val)
        return None

    @staticmethod
    def policy_prior(
        policy: list[float], played: str | None, board_size: int,
    ) -> float | None:
        idx = gtp_to_policy_index(played, board_size)
        if idx < 0 or idx >= len(policy):
            return None
        val = policy[idx]
        return float(val) if val >= 0 else None

    @staticmethod
    def policy_top_moves(
        policy: list[float], board_size: int, n: int = 3,
    ) -> list[tuple[str, float]]:
        legal = [
            (policy_index_to_gtp(i, board_size), float(v))
            for i, v in enumerate(policy)
            if v >= 0
        ]
        legal.sort(key=lambda x: x[1], reverse=True)
        return legal[:n]

    @staticmethod
    def human_prior_from_response(
        response: dict[str, Any],
        played: str | None,
        board_size: int,
    ) -> float | None:
        played_gtp = played.upper() if played and played.lower() != "pass" else "pass"
        prior = KataGoAnalysis.human_prior(
            response.get("moveInfos", []), played_gtp,
        )
        if prior is not None:
            return prior
        hp = response.get("humanPolicy")
        if hp:
            return KataGoAnalysis.policy_prior(hp, played_gtp, board_size)
        return None
