from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sgfmill import sgf, sgf_moves
from sgfmill.common import format_vertex


_GTP_COLS = "ABCDEFGHJKLMNOPQRST"


def _rowcol_to_gtp(row: int, col: int, board_size: int) -> str:
    """sgfmill row/col sont déjà en coordonnées GTP (rangée 0 = bas)."""
    del board_size  # taille implicite dans format_vertex
    return format_vertex((row, col))


@dataclass
class ParsedGame:
    black: str
    white: str
    black_rank: str
    white_rank: str
    komi: float
    handicap: int
    board_size: int
    result: str
    moves: list[tuple[str, str | None]]  # (color, coord GTP) coord None = pass
    initial_stones: list[tuple[str, str]]  # (B|W, GTP) pierres de départ (handicap)
    rules: str


def _decode_prop(game: sgf.Sgf_game, prop: str, default: str = "") -> str:
    root = game.get_root()
    if not root.has_property(prop):
        return default
    val = root.get(prop)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    if isinstance(val, list) and val:
        v = val[0]
        return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
    return str(val)


def parse_sgf(path: Path) -> ParsedGame:
    with path.open("rb") as f:
        game = sgf.Sgf_game.from_bytes(f.read())

    size = game.get_size()
    komi = game.get_komi() if game.get_komi() is not None else 6.5
    handicap = game.get_handicap() or 0
    black = game.get_player_name("b") or _decode_prop(game, "PB", "Black")
    white = game.get_player_name("w") or _decode_prop(game, "PW", "White")
    black_rank = _decode_prop(game, "BR", "")
    white_rank = _decode_prop(game, "WR", "")
    result = _decode_prop(game, "RE", "")
    rules = _decode_prop(game, "RU", "chinese").lower()

    setup_board, plays = sgf_moves.get_setup_and_moves(game)
    initial_stones: list[tuple[str, str]] = []
    for row in range(size):
        for col in range(size):
            stone = setup_board.get(row, col)
            if stone in ("b", "w"):
                initial_stones.append(
                    (stone.upper(), _rowcol_to_gtp(row, col, size)),
                )
    moves: list[tuple[str, str | None]] = []
    for color, move in plays:
        c = color.upper()
        if move is None:
            moves.append((c, None))
        else:
            row, col = move
            moves.append((c, _rowcol_to_gtp(row, col, size)))

    return ParsedGame(
        black=black,
        white=white,
        black_rank=black_rank,
        white_rank=white_rank,
        komi=float(komi),
        handicap=handicap,
        board_size=size,
        result=result,
        moves=moves,
        initial_stones=initial_stones,
        rules=rules,
    )


def player_color(parsed: ParsedGame, username: str) -> str | None:
    user = username.lower()
    if user in parsed.black.lower():
        return "B"
    if user in parsed.white.lower():
        return "W"
    return None


def opponent_name(parsed: ParsedGame, username: str) -> str:
    color = player_color(parsed, username)
    if color == "B":
        return parsed.white
    if color == "W":
        return parsed.black
    return "?"


def player_rank(parsed: ParsedGame, username: str) -> str:
    color = player_color(parsed, username)
    if color == "B":
        return parsed.black_rank
    if color == "W":
        return parsed.white_rank
    return ""


def opponent_rank(parsed: ParsedGame, username: str) -> str:
    color = player_color(parsed, username)
    if color == "B":
        return parsed.white_rank
    if color == "W":
        return parsed.black_rank
    return ""


def game_phase(move_number: int) -> str:
    if move_number <= 50:
        return "opening"
    if move_number <= 150:
        return "middlegame"
    return "endgame"


def severity_for_loss(point_loss: float, thresholds: dict) -> str:
    if point_loss >= thresholds["mega_blunder"]:
        return "mega_blunder"
    if point_loss >= thresholds["blunder"]:
        return "blunder"
    if point_loss >= thresholds["mistake"]:
        return "mistake"
    if point_loss >= thresholds["inaccuracy"]:
        return "inaccuracy"
    return "ok"


DEFAULT_THRESHOLDS: dict[str, float] = {
    "inaccuracy": 0.5,
    "mistake": 1.5,
    "blunder": 4.0,
    "mega_blunder": 8.0,
}


def point_loss_from_stored_scores(
    move: dict[str, Any],
    player_color: str | None,
) -> float | None:
    """Perte vs le coup #1 KataGo, recalculée depuis les scores JSON stockés."""
    played = _norm_gtp(move.get("played_move"))
    if not played or played == "PASS":
        return None
    scores = katago_scores_by_move(move, player_color)
    if not scores or played not in scores:
        return None
    played_sc = scores[played]
    best_sc: float | None = None
    tops_raw = move.get("top_moves_json")
    if tops_raw:
        try:
            tops = json.loads(tops_raw) if isinstance(tops_raw, str) else tops_raw
            if tops:
                best_mv = _norm_gtp(tops[0].get("move"))
                if best_mv in scores:
                    best_sc = scores[best_mv]
                else:
                    converted = katago_score_for_player(
                        tops[0].get("scoreLead"), player_color,
                    )
                    if converted is not None and best_mv:
                        best_sc = converted
        except (json.JSONDecodeError, TypeError):
            pass
    if best_sc is None:
        best_sc = max(scores.values())
    return max(0.0, best_sc - played_sc)


def resolved_engine_best(move: dict[str, Any]) -> str | None:
    """Meilleur coup IA = #1 dans top_moves_json (source d'affichage)."""
    tops_raw = move.get("top_moves_json")
    if not tops_raw:
        return move.get("best_move")
    try:
        tops = json.loads(tops_raw) if isinstance(tops_raw, str) else tops_raw
        if tops:
            return tops[0].get("move") or move.get("best_move")
    except (json.JSONDecodeError, TypeError):
        pass
    return move.get("best_move")


def effective_move_quality(
    move: dict[str, Any],
    *,
    player_color: str | None = None,
    thresholds: dict | None = None,
) -> tuple[float, str]:
    """Perte et sévérité cohérentes avec le coup #1 KataGo affiché."""
    th = thresholds or DEFAULT_THRESHOLDS
    recalc = point_loss_from_stored_scores(move, player_color)
    point_loss = recalc if recalc is not None else float(move.get("point_loss") or 0)

    if point_loss < 0.12:
        point_loss = 0.0

    severity = severity_for_loss(point_loss, th) if point_loss > 0 else "ok"
    return round(point_loss, 2), severity


def finalize_move_quality(
    move: dict[str, Any],
    *,
    player_color: str | None = None,
    thresholds: dict | None = None,
) -> dict[str, Any]:
    """Aligne best_move / point_loss / severity avant enregistrement."""
    tops_raw = move.get("top_moves_json")
    if isinstance(tops_raw, str):
        try:
            tops = json.loads(tops_raw)
        except (json.JSONDecodeError, TypeError):
            tops = []
    else:
        tops = tops_raw or []
    if tops:
        move["best_move"] = tops[0].get("move") or move.get("best_move")
    pl, sev = effective_move_quality(
        move, player_color=player_color, thresholds=thresholds,
    )
    move["point_loss"] = pl
    move["severity"] = sev
    return move


def katago_score_for_player(
    score_lead: float | None, player_color: str | None,
) -> float | None:
    """scoreLead KataGo = avantage Noir ; retourne le score du joueur."""
    if score_lead is None:
        return None
    if (player_color or "B").upper() == "W":
        return -score_lead
    return score_lead


def katago_scores_by_move(
    move: dict,
    player_color: str | None,
) -> dict[str, float]:
    """Scores KataGo indexés par coup (perspective joueur)."""
    scores: dict[str, float] = {}
    raw = move.get("move_scores_json")
    if raw:
        try:
            for gtp, sl in json.loads(raw).items():
                key = (gtp or "").upper().strip()
                converted = katago_score_for_player(sl, player_color)
                if key and key != "PASS" and converted is not None:
                    scores[key] = converted
        except (json.JSONDecodeError, TypeError):
            pass
    raw = move.get("top_moves_json")
    if raw:
        try:
            for item in json.loads(raw):
                gtp = (item.get("move") or "").upper().strip()
                sl = katago_score_for_player(item.get("scoreLead"), player_color)
                if gtp and gtp != "PASS" and sl is not None:
                    scores[gtp] = sl
        except (json.JSONDecodeError, TypeError):
            pass
    played = (move.get("played_move") or move.get("coord") or "").upper().strip()
    if played and played not in scores:
        after = katago_score_for_player(move.get("score_after"), player_color)
        if after is not None:
            scores[played] = after
    return scores


STRONG_RANK_KEYS: tuple[str, ...] = (
    "rank_7d", "rank_8d", "rank_9d", "proyear_2020", "proyear_2023",
)


def strong_ranks_score_gap(
    move: dict,
    player_color: str | None,
) -> float | None:
    """Écart entre votre coup et la moyenne des coups 7d–pro23 (négatif = pire)."""
    raw = move.get("human_rank_moves_json")
    if not raw:
        return None
    try:
        ranks = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    scores = katago_scores_by_move(move, player_color)
    played = (move.get("played_move") or move.get("coord") or "").upper().strip()
    if not played or played not in scores:
        return None
    played_sc = scores[played]
    strong_scores: list[float] = []
    for key in STRONG_RANK_KEYS:
        gtp = (ranks.get(key) or "").upper().strip()
        if not gtp or gtp == "PASS":
            continue
        sc = scores.get(gtp)
        if sc is not None:
            strong_scores.append(sc)
    if len(strong_scores) < 2:
        return None
    return played_sc - (sum(strong_scores) / len(strong_scores))


def quick_top12_gap(move: dict[str, Any], player_color: str | None) -> float | None:
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
    sc_map = katago_scores_by_move(move, player_color)
    for t in tops[:2]:
        mv = _norm_gtp(t.get("move"))
        if mv in sc_map:
            scores.append(sc_map[mv])
        else:
            conv = katago_score_for_player(t.get("scoreLead"), player_color)
            if conv is not None:
                scores.append(conv)
    if len(scores) < 2:
        return None
    return abs(scores[0] - scores[1])


def line_loss_from_scores(
    score_before: float | None,
    score_after: float | None,
    color: str,
) -> float:
    """Baisse de score le long de la partie (obligé de jouer), pas vs meilleur coup."""
    if score_before is None or score_after is None:
        return 0.0
    if color.upper() == "B":
        return max(0.0, float(score_before) - float(score_after))
    return max(0.0, float(score_after) - float(score_before))


def _norm_gtp(gtp: str | None) -> str:
    return (gtp or "").upper().strip()


def classify_move_context(
    move: dict[str, Any],
    *,
    player_color: str | None = None,
    prev_player_severity: str | None = None,
) -> list[dict[str, str]]:
    """Étiquettes contextuelles (pas des erreurs) pour coups importants."""
    color = player_color or move.get("color") or "B"
    played = _norm_gtp(move.get("played_move"))
    best = _norm_gtp(move.get("best_move"))
    if not played or played == "PASS":
        return []

    point_loss = float(move.get("point_loss") or 0)
    line_loss = move.get("line_loss")
    if line_loss is None:
        line_loss = line_loss_from_scores(
            move.get("score_before"), move.get("score_after"), color,
        )
    else:
        line_loss = float(line_loss)

    played_best = played == best or point_loss < 0.12
    tags: list[dict[str, str]] = []

    bad_prev = prev_player_severity in (
        "inaccuracy", "mistake", "blunder", "mega_blunder",
    )
    if bad_prev and line_loss >= 0.5 and played_best:
        tags.append({
            "key": "after_sequence",
            "label": "Suite d'une séquence difficile",
            "color": "#7c3aed",
        })

    if played_best and line_loss >= 0.5 and point_loss < 0.35:
        tags.append({
            "key": "forced",
            "label": f"Coup forcé · −{line_loss:.1f} pts position",
            "color": "#6366f1",
        })

    human_json = move.get("human_rank_moves_json")
    if played_best and point_loss < 0.15 and human_json:
        try:
            from .human_sl import RANK_ORDER
            ranks = json.loads(human_json)
            same = sum(
                1 for k in RANK_ORDER
                if ranks.get(k) and _norm_gtp(ranks[k]) == played
            )
            if same >= 3:
                tags.append({
                    "key": "aligned_ia",
                    "label": "Même logique que l'IA",
                    "color": "#0d9488",
                })
        except (json.JSONDecodeError, TypeError):
            pass

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for t in tags:
        if t["key"] not in seen:
            seen.add(t["key"])
            out.append(t)
    return out


def classify_human_engine_verdict(
    *,
    severity: str | None,
    played_move: str | None,
    human_rank_moves_json: str | None,
) -> dict[str, str] | None:
    """Distingue erreur humainement plausible vs erreur nette (écart IA)."""
    if not severity or severity == "ok":
        return None
    played = (played_move or "").upper().strip()
    if not played or played == "PASS":
        return None
    if not human_rank_moves_json:
        return None
    try:
        ranks = json.loads(human_rank_moves_json)
    except (json.JSONDecodeError, TypeError):
        return None

    def norm(gtp: str | None) -> str:
        return (gtp or "").upper().strip()

    from .human_sl import RANK_LABELS, RANK_ORDER

    same_labels: list[str] = []
    other_by_move: dict[str, list[str]] = {}
    for rank_key in RANK_ORDER:
        gtp = ranks.get(rank_key)
        if not gtp:
            continue
        lbl = RANK_LABELS.get(rank_key, rank_key)
        g = norm(gtp)
        if g == played:
            same_labels.append(lbl)
        else:
            other_by_move.setdefault(g, []).append(lbl)

    if len(same_labels) >= 3 and other_by_move:
        alt_move, alt_labels = max(other_by_move.items(), key=lambda x: len(x[1]))
        alt_short = alt_move if alt_move != "PASS" else "passe"
        pros = [x for x in alt_labels if x.startswith("pro")]
        if len(pros) >= 2:
            detail = f"pros → {alt_short}"
        elif len(alt_labels) <= 2:
            detail = f"{'+'.join(alt_labels)} → {alt_short}"
        else:
            detail = f"{'+'.join(alt_labels[:3])}… → {alt_short}"
        return {
            "key": "human_pro_split",
            "label": f"Bon coup humain · {detail}",
            "color": "#7c3aed",
        }

    if len(same_labels) >= 3:
        return {
            "key": "human_ok",
            "label": "Imprécision IA · bon coup humain",
            "color": "#0d9488",
        }

    if not same_labels:
        return {
            "key": "clear_miss",
            "label": "Erreur nette",
            "color": "#991b1b",
        }

    return None


def parse_result_margin(result: str | None) -> float | None:
    """Extrait l'écart en points d'un résultat SGF (ex. B+21.5 → 21.5)."""
    if not result:
        return None
    m = re.search(r"[BW]\+(\d+(?:\.\d+)?)", result.strip(), re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def game_outcome(result: str | None, player_color: str | None) -> str:
    """Retourne win, loss, draw ou unknown."""
    if not result or not player_color:
        return "unknown"
    r = result.strip().upper()
    if not r or r in ("?", "0"):
        return "unknown"
    if "DRAW" in r or "JIGO" in r or r == "0":
        return "draw"
    winner: str | None = None
    if r.startswith("B"):
        winner = "B"
    elif r.startswith("W"):
        winner = "W"
    if not winner:
        return "unknown"
    return "win" if winner == player_color.upper() else "loss"


def player_result_score(
    result: str | None,
    player_color: str | None,
    final_score_lead: float | None = None,
) -> tuple[str, float | None]:
    """Score affiché et valeur de tri, du point de vue du joueur (+ = avantage)."""
    margin = parse_result_margin(result)
    if margin is not None and player_color:
        outcome = game_outcome(result, player_color)
        if outcome == "win":
            signed = margin
        elif outcome == "loss":
            signed = -margin
        else:
            signed = 0.0
        text = f"{'+' if signed >= 0 else ''}{signed:.1f}"
        return text, signed
    if final_score_lead is not None and player_color:
        player_sc = katago_score_for_player(final_score_lead, player_color)
        if player_sc is not None:
            text = f"~{'+' if player_sc >= 0 else ''}{player_sc:.1f}"
            return text, player_sc
    return "", None
