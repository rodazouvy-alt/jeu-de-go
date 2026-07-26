from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgfmill import sgf, sgf_moves


_GTP_COLS = "ABCDEFGHJKLMNOPQRST"


def _rowcol_to_gtp(row: int, col: int, board_size: int) -> str:
    return f"{_GTP_COLS[col]}{board_size - row}"


@dataclass
class ParsedGame:
    black: str
    white: str
    komi: float
    handicap: int
    board_size: int
    result: str
    moves: list[tuple[str, str | None]]  # (color, coord GTP) coord None = pass
    rules: str


def _decode_prop(game: sgf.Sgf_game, prop: str, default: str = "") -> str:
    root = game.get_root()
    if prop in root:
        val = root[prop]
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace")
        if isinstance(val, list) and val:
            v = val[0]
            return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
        return str(val)
    return default


def parse_sgf(path: Path) -> ParsedGame:
    with path.open("rb") as f:
        game = sgf.Sgf_game.from_bytes(f.read())

    size = game.get_size()
    komi = game.get_komi() if game.get_komi() is not None else 6.5
    handicap = game.get_handicap() or 0
    black = game.get_player_name("b") or _decode_prop(game, "PB", "Black")
    white = game.get_player_name("w") or _decode_prop(game, "PW", "White")
    result = _decode_prop(game, "RE", "")
    rules = _decode_prop(game, "RU", "chinese").lower()

    _, plays = sgf_moves.get_setup_and_moves(game)
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
        komi=float(komi),
        handicap=handicap,
        board_size=size,
        result=result,
        moves=moves,
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
