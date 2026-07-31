from __future__ import annotations

from sgfmill.boards import Board

from .sgf_parse import _GTP_COLS

THEME_LABELS = {
    "corner_opening": "Coin / ouverture",
    "invasion": "Invasion",
    "reduction": "Réduction",
    "fight": "Combat",
    "defense": "Défense",
    "endgame": "Fin de partie",
    "other": "Autre",
}


def gtp_to_rowcol(coord: str, board_size: int) -> tuple[int, int] | None:
    if not coord or coord.lower() == "pass":
        return None
    col = _GTP_COLS.index(coord[0].upper())
    row = int(coord[1:]) - 1
    if not (0 <= row < board_size and 0 <= col < board_size):
        return None
    return row, col


def _in_corner(row: int, col: int, size: int, margin: int = 4) -> bool:
    return row < margin or row >= size - margin or col < margin or col >= size - margin


def _on_third_fourth_line(row: int, col: int, size: int) -> bool:
    bands = {2, 3, 4, size - 5, size - 4, size - 3}
    return row in bands or col in bands


def _count_in_radius(board: Board, row: int, col: int, radius: int) -> tuple[int, int]:
    own = opp = 0
    size = board.side
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            r, c = row + dr, col + dc
            if 0 <= r < size and 0 <= c < size:
                stone = board.get(r, c)
                if stone == "b":
                    own += 1
                elif stone == "w":
                    opp += 1
    return own, opp


def _neighbors(board: Board, row: int, col: int) -> list[tuple[int, int]]:
    size = board.side
    out = []
    for r, c in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
        if 0 <= r < size and 0 <= c < size:
            out.append((r, c))
    return out


def _group_liberties(board: Board, row: int, col: int) -> int:
    color = board.get(row, col)
    if color is None:
        return 99
    seen: set[tuple[int, int]] = set()
    libs: set[tuple[int, int]] = set()
    stack = [(row, col)]
    while stack:
        r, c = stack.pop()
        if (r, c) in seen:
            continue
        seen.add((r, c))
        for nr, nc in _neighbors(board, r, c):
            stone = board.get(nr, nc)
            if stone is None:
                libs.add((nr, nc))
            elif stone == color and (nr, nc) not in seen:
                stack.append((nr, nc))
    return len(libs)


def _weak_own_group_nearby(board: Board, row: int, col: int, color: str, radius: int = 3) -> bool:
    size = board.side
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            r, c = row + dr, col + dc
            if 0 <= r < size and 0 <= c < size and board.get(r, c) == color:
                if _group_liberties(board, r, c) <= 4:
                    return True
    return False


def classify_move_theme(
    board: Board,
    color: str,
    row: int,
    col: int,
    move_number: int,
) -> str:
    """Heuristique locale — Phase 3, affinage itératif prévu."""
    size = board.side
    me = color.lower()
    opp = "w" if me == "b" else "b"

    if move_number > 180:
        return "endgame"
    if move_number <= 50 and _in_corner(row, col, size, margin=5):
        return "corner_opening"

    own_near, opp_near = _count_in_radius(board, row, col, radius=3)

    if _weak_own_group_nearby(board, row, col, me):
        return "defense"

    if opp_near >= 3 and own_near <= 1 and not _in_corner(row, col, size, margin=3):
        return "invasion"

    if _on_third_fourth_line(row, col, size) and opp_near >= 2 and own_near <= 2:
        return "reduction"

    if opp_near >= 2 and own_near >= 1:
        return "fight"

    return "other"


def build_board_before_move(
    moves: list[tuple[str, str | None]],
    move_index: int,
    board_size: int,
    *,
    initial_stones: list[tuple[str, str]] | None = None,
) -> tuple[Board, str, int | None, int | None] | None:
    """Plateau juste avant le coup move_index (0-based). row/col None = passe."""
    if move_index < 0 or move_index >= len(moves):
        return None
    board = Board(board_size)
    if initial_stones:
        for color, coord in initial_stones:
            rc = gtp_to_rowcol(coord, board_size)
            if rc:
                board.play(rc[0], rc[1], color.lower())
    for i in range(move_index):
        color, coord = moves[i]
        rc = gtp_to_rowcol(coord, board_size) if coord else None
        if rc:
            board.play(rc[0], rc[1], color.lower())

    color, coord = moves[move_index]
    if not coord:
        return board, color, None, None
    rc = gtp_to_rowcol(coord, board_size)
    if not rc:
        return board, color, None, None
    return board, color, rc[0], rc[1]


def theme_for_move(
    moves: list[tuple[str, str | None]],
    move_index: int,
    board_size: int,
    *,
    initial_stones: list[tuple[str, str]] | None = None,
) -> str:
    built = build_board_before_move(
        moves, move_index, board_size, initial_stones=initial_stones,
    )
    if not built:
        return "other"
    board, color, row, col = built
    if row is None or col is None:
        return "endgame"
    return classify_move_theme(board, color, row, col, move_index + 1)


def patch_center(
    board: Board,
    color: str,
    row: int,
    col: int,
    theme: str,
) -> tuple[int, int]:
    """Centre du patch local (rayon fixe) selon le thème du coup."""
    size = board.side
    me = color.lower()

    if theme == "corner_opening":
        corners = ((0, 0), (0, size - 1), (size - 1, 0), (size - 1, size - 1))
        return min(corners, key=lambda rc: abs(rc[0] - row) + abs(rc[1] - col))

    if theme == "invasion":
        opp = "w" if me == "b" else "b"
        best_r, best_c, best_cnt = row, col, -1
        for dr in range(-3, 4):
            for dc in range(-3, 4):
                r, c = row + dr, col + dc
                if 0 <= r < size and 0 <= c < size:
                    cnt = sum(
                        1
                        for rr in range(r - 2, r + 3)
                        for cc in range(c - 2, c + 3)
                        if 0 <= rr < size and 0 <= cc < size and board.get(rr, cc) == opp
                    )
                    if cnt > best_cnt:
                        best_r, best_c, best_cnt = r, c, cnt
        return best_r, best_c

    if theme in ("fight", "reduction"):
        stones: list[tuple[int, int]] = []
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                r, c = row + dr, col + dc
                if 0 <= r < size and 0 <= c < size and board.get(r, c):
                    stones.append((r, c))
        if stones:
            sr = round(sum(s[0] for s in stones) / len(stones))
            sc = round(sum(s[1] for s in stones) / len(stones))
            return sr, sc

    if theme == "defense":
        for dr in range(-3, 4):
            for dc in range(-3, 4):
                r, c = row + dr, col + dc
                if 0 <= r < size and 0 <= c < size and board.get(r, c) == me:
                    if _group_liberties(board, r, c) <= 4:
                        return r, c

    return row, col
