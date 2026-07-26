from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import resolve_path


class KataGoAnalysis:
    """Wrapper autour du KataGo Analysis Engine (stdin/stdout JSON)."""

    STARTUP_TIMEOUT = 120
    STARTUP_MARKERS = ("ready to begin", "analysis engine starting", "loaded neural net")

    def __init__(self, cfg: dict[str, Any], *, analysis_config: str | None = None):
        katago = cfg["katago"]
        self.executable = Path(katago["executable"])
        self.model = Path(katago["model"])
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

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return

        cmd = [
            str(self.executable),
            "analysis",
            "-config", str(self.config),
            "-model", str(self.model),
        ]
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
            low = line.lower()
            if any(m in low for m in self.STARTUP_MARKERS):
                self._ready.set()
            if "loaded neural net" in low or "cuda backend" in low:
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
            "includePolicy": False,
        }

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
