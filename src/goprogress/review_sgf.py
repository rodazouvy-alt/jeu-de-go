from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sgfmill import sgf

from .config import resolve_path
from .db import Database
from .sgf_parse import ParsedGame, parse_sgf, katago_score_for_player
from .themes import gtp_to_rowcol

LABEL_COLORS = ("1", "2", "3", "4", "5", "6", "7", "8", "9")


def _move_comment(
    rank: int,
    move: str,
    score_lead: float | None,
    winrate: float | None,
    played: bool,
    point_loss: float | None,
    player_color: str | None = None,
) -> str:
    parts = [f"#{rank} {move}"]
    if score_lead is not None:
        sl = katago_score_for_player(score_lead, player_color)
        if sl is not None:
            parts.append(f"score {sl:+.1f}")
    if winrate is not None:
        parts.append(f"WR {winrate * 100:.1f}%")
    if played:
        parts.append(f"VOTRE COUP (-{point_loss or 0:.1f} pts)")
    return " | ".join(parts)


def build_review_sgf(
    parsed: ParsedGame,
    move_number: int,
    top_moves: list[dict[str, Any]],
    played_move: str | None,
    point_loss: float | None,
    output_path: Path,
    player_color: str | None = None,
) -> Path:
    """SGF jusqu'au coup précédent le blunder, avec variations colorées (top N)."""
    size = parsed.board_size
    move_idx = move_number - 1
    if move_idx < 0 or move_idx >= len(parsed.moves):
        raise ValueError(f"Coup invalide: {move_number}")

    game = sgf.Sgf_game(size=size)
    root = game.get_root()
    root.set("PB", parsed.black)
    root.set("PW", parsed.white)
    if parsed.black_rank:
        root.set("BR", parsed.black_rank)
    if parsed.white_rank:
        root.set("WR", parsed.white_rank)
    root.set("KM", parsed.komi)
    root.set("RE", parsed.result or "?")
    root.set("GN", f"Review coup {move_number}")
    root.set(
        "C",
        f"Go Progress — position avant coup {move_number}. "
        f"Variations = alternatives KataGo (1=meilleur).",
    )

    node = root
    for i in range(move_idx):
        color, coord = parsed.moves[i]
        node = node.new_child()
        c = color.lower()
        if coord and coord.lower() != "pass":
            rc = gtp_to_rowcol(coord, size)
            if rc:
                node.set_move(c, rc)
            else:
                node.set_move(c, None)
        else:
            node.set_move(c, None)

    color_to_play, _ = parsed.moves[move_idx]
    c_play = color_to_play.lower()
    played_gtp = (played_move or "").upper()
    if played_gtp == "PASS":
        played_gtp = "pass"

    seen: set[str | None] = set()
    alternatives: list[dict[str, Any]] = list(top_moves)
    if played_gtp and played_gtp.lower() != "pass":
        if not any((a.get("move") or "").upper() == played_gtp for a in alternatives):
            alternatives.append({"move": played_gtp, "scoreLead": None, "winrate": None})

    for rank, alt in enumerate(alternatives[:8], start=1):
        gtp = alt.get("move")
        if not gtp:
            continue
        key = gtp.upper()
        if key in seen:
            continue
        seen.add(key)

        child = node.new_child()
        is_played = key == played_gtp or (
            played_gtp == "pass" and gtp.lower() == "pass"
        )
        if gtp.lower() == "pass":
            child.set_move(c_play, None)
        else:
            rc = gtp_to_rowcol(gtp, size)
            if not rc:
                continue
            child.set_move(c_play, rc)

        comment = _move_comment(
            rank,
            gtp,
            alt.get("scoreLead"),
            alt.get("winrate"),
            is_played,
            point_loss,
            player_color,
        )
        child.set("C", comment)
        if rank <= len(LABEL_COLORS) and gtp.lower() != "pass":
            rc = gtp_to_rowcol(gtp, size)
            if rc:
                row, col = rc
                child.set("LB", [((row, col), LABEL_COLORS[rank - 1])])

        pv = alt.get("pv") or []
        pv_node = child
        for step in pv[:6]:
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                break
            pv_color, pv_move = step[0], step[1]
            pv_c = str(pv_color).lower()
            pv_node = pv_node.new_child()
            if pv_move and str(pv_move).lower() != "pass":
                pv_rc = gtp_to_rowcol(str(pv_move), size)
                if pv_rc:
                    pv_node.set_move(pv_c, pv_rc)
                else:
                    pv_node.set_move(pv_c, None)
            else:
                pv_node.set_move(pv_c, None)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(game.serialise())
    return output_path


def review_output_path(cfg: dict[str, Any], game_id: int, move_number: int) -> Path:
    review_dir = resolve_path(cfg.get("review", {}).get("output_dir", "data/review_sgf"))
    return review_dir / f"game_{game_id}_move_{move_number}.sgf"


def generate_review_for_move(db: Database, cfg: dict[str, Any], move_id: int) -> Path:
    row = db.get_move(move_id)
    if not row:
        raise ValueError(f"Coup #{move_id} introuvable")

    sgf_path = resolve_path(row["sgf_path"])
    parsed = parse_sgf(sgf_path)

    top_moves: list[dict[str, Any]] = []
    if row["top_moves_json"]:
        try:
            top_moves = json.loads(row["top_moves_json"])
        except json.JSONDecodeError:
            top_moves = []

    if not top_moves and row["best_move"]:
        top_moves = [{"move": row["best_move"], "scoreLead": row["score_after"]}]

    out = review_output_path(cfg, row["game_id"], row["move_number"])
    build_review_sgf(
        parsed=parsed,
        move_number=row["move_number"],
        top_moves=top_moves,
        played_move=row["played_move"] or row["coord"],
        point_loss=row["point_loss"],
        output_path=out,
        player_color=row["player_color"] if "player_color" in row.keys() else None,
    )

    rel = str(out.relative_to(resolve_path(".")))
    db.update_move_review_path(move_id, rel)
    return out


def _lizzie_command(cfg: dict[str, Any], sgf_path: Path) -> list[str]:
    review = cfg.get("review", {})
    lizzie = review.get("lizzie", {})
    java = lizzie.get("java") or review.get("java")
    jar = lizzie.get("jar")
    if java and jar:
        return [str(java), "-jar", str(jar), str(sgf_path)]

    executable = lizzie.get("executable")
    if executable:
        return [str(executable), str(sgf_path)]

    raise RuntimeError(
        "Lizzie non configuré — ajoutez review.lizzie dans config.yaml "
        "(java + jar, ou executable)"
    )


def open_review(sgf_path: Path, cfg: dict[str, Any], tool: str = "lizzie") -> None:
    sgf_path = sgf_path.resolve()
    if not sgf_path.exists():
        raise FileNotFoundError(sgf_path)

    if tool == "katrain":
        katrain = cfg.get("review", {}).get("katrain", {}).get("executable")
        if katrain:
            subprocess.Popen([str(katrain), str(sgf_path)], cwd=str(sgf_path.parent))
            return
        if sys.platform == "win32":
            os.startfile(str(sgf_path))  # type: ignore[attr-defined]
            return
        raise RuntimeError("KaTrain non configuré")

    cmd = _lizzie_command(cfg, sgf_path)
    cwd = cfg.get("review", {}).get("lizzie", {}).get("working_dir")
    subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
    )


def open_move_review(db: Database, cfg: dict[str, Any], move_id: int, tool: str = "lizzie") -> Path:
    row = db.get_move(move_id)
    if row and row["review_sgf_path"]:
        existing = resolve_path(row["review_sgf_path"])
        if existing.exists():
            open_review(existing, cfg, tool=tool)
            return existing
    path = generate_review_for_move(db, cfg, move_id)
    open_review(path, cfg, tool=tool)
    return path
