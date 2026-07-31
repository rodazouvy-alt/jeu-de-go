from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sgfmill import sgf, sgf_moves


def _root_text(game: sgf.Sgf_game, prop: str) -> str:
    root = game.get_root()
    if not root.has_property(prop):
        return ""
    val = root.get(prop)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    if isinstance(val, list) and val:
        v = val[0]
        return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
    return str(val)


def classify_speed(time_control: str | None) -> str:
    """Catégorie de vitesse pour les statistiques."""
    if not time_control:
        return "inconnu"
    tc = time_control.lower()
    if "byo" in tc or re.search(r"\d+\s*x\s*\d+", tc):
        m = re.search(r"(\d+)\s*x\s*(\d+)", tc)
        if m:
            periods, sec = int(m.group(1)), int(m.group(2))
            if sec <= 10 and periods <= 3:
                return "blitz"
            if sec <= 15 and periods <= 5:
                return "rapide"
            if sec <= 30:
                return "normal"
            return "long"
    m = re.search(r"(\d+)\s*m", tc)
    if m:
        minutes = int(m.group(1))
        if minutes <= 10:
            return "rapide"
        if minutes <= 30:
            return "normal"
        return "long"
    if re.search(r"(\d+)\s*s", tc):
        return "blitz"
    return "inconnu"


def _estimate_duration_sec(move_count: int, speed: str) -> int | None:
    if move_count <= 0:
        return None
    sec_per_move = {
        "blitz": 14,
        "rapide": 24,
        "normal": 42,
        "long": 75,
        "inconnu": 30,
    }.get(speed, 30)
    return move_count * sec_per_move


def parse_game_timing(path: Path) -> dict[str, Any]:
    """
    Extrait contrôle de temps KGS et durée (réelle ou estimée) depuis le SGF.
    KGS enregistre souvent le temps restant (BL/WL) sans décompte de réflexion :
    dans ce cas la durée est estimée à partir du nombre de coups et de la cadence.
    """
    with path.open("rb") as f:
        game = sgf.Sgf_game.from_bytes(f.read())

    tm = _root_text(game, "TM")
    ot = _root_text(game, "OT")
    tm_usable = tm and tm not in ("0", "0.0")
    time_control = (ot or (f"{tm}s" if tm_usable else "")).strip() or None

    _, plays = sgf_moves.get_setup_and_moves(game)
    move_count = len(plays)

    elapsed = 0.0
    last_bl: float | None = None
    last_wl: float | None = None

    def walk(node) -> None:
        nonlocal elapsed, last_bl, last_wl
        color, _move = node.get_move()
        if color is not None:
            if node.has_property("BL"):
                bl = float(node.get("BL"))
                if last_bl is not None and last_bl > bl:
                    elapsed += last_bl - bl
                last_bl = bl
            if node.has_property("WL"):
                wl = float(node.get("WL"))
                if last_wl is not None and last_wl > wl:
                    elapsed += last_wl - wl
                last_wl = wl
        for child in node:
            walk(child)

    walk(game.get_root())

    speed = classify_speed(time_control)
    duration_estimated = elapsed <= 0
    if elapsed > 0:
        duration_sec = int(round(elapsed))
    else:
        duration_sec = _estimate_duration_sec(move_count, speed)

    avg_move_sec = (
        round(duration_sec / move_count, 1)
        if duration_sec and move_count
        else None
    )
    moves_per_min = (
        round(move_count / (duration_sec / 60), 1)
        if duration_sec and duration_sec >= 60
        else None
    )

    return {
        "time_control": time_control,
        "speed": speed,
        "duration_sec": duration_sec,
        "duration_estimated": duration_estimated,
        "avg_move_sec": avg_move_sec,
        "moves_per_min": moves_per_min,
        "move_count": move_count,
    }


def format_duration(seconds: int | None, *, estimated: bool = False) -> str:
    if not seconds:
        return "—"
    prefix = "~" if estimated else ""
    if seconds < 60:
        return f"{prefix}{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{prefix}{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{prefix}{h}h{m:02d}m"


def format_speed_label(speed: str | None) -> str:
    labels = {
        "blitz": "Blitz",
        "rapide": "Rapide",
        "normal": "Normal",
        "long": "Long",
        "inconnu": "—",
    }
    return labels.get(speed or "", speed or "—")
