from __future__ import annotations

import math
import uuid
from typing import Any

from sgfmill.boards import Board
_GTP_COLS = "ABCDEFGHJKLMNOPQRST"
_STAR_19 = {(3, 3), (3, 9), (3, 15), (9, 3), (9, 9), (9, 15), (15, 3), (15, 9), (15, 15)}
# Position des coords dans la bande bord→grille (0 = bord ext., 1 = grille)
_COORD_BORDER_FRAC = 0.28

_SVG_DEFS_TEMPLATE = """
<defs>
  <linearGradient id="{uid}-bg" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="#3d3832"/>
    <stop offset="55%" stop-color="#2a2622"/>
    <stop offset="100%" stop-color="#1a1816"/>
  </linearGradient>
  <linearGradient id="{uid}-frameWood" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="#6b4a1e"/>
    <stop offset="40%" stop-color="#4a3214"/>
    <stop offset="100%" stop-color="#2e1f0c"/>
  </linearGradient>
  <linearGradient id="{uid}-wood" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="#f2d98a"/>
    <stop offset="18%" stop-color="#e8c468"/>
    <stop offset="42%" stop-color="#d4a84a"/>
    <stop offset="68%" stop-color="#c99538"/>
    <stop offset="88%" stop-color="#ddb85c"/>
    <stop offset="100%" stop-color="#b8872e"/>
  </linearGradient>
  <linearGradient id="{uid}-woodShine" x1="0%" y1="0%" x2="0%" y2="100%">
    <stop offset="0%" stop-color="#fff" stop-opacity="0.18"/>
    <stop offset="45%" stop-color="#fff" stop-opacity="0.04"/>
    <stop offset="100%" stop-color="#000" stop-opacity="0.14"/>
  </linearGradient>
  <radialGradient id="{uid}-woodVignette" cx="48%" cy="42%" r="72%" fx="45%" fy="38%">
    <stop offset="55%" stop-color="#000" stop-opacity="0"/>
    <stop offset="100%" stop-color="#000" stop-opacity="0.22"/>
  </radialGradient>
  <filter id="{uid}-woodGrain" x="-2%" y="-2%" width="104%" height="104%">
    <feTurbulence type="fractalNoise" baseFrequency="0.018 0.32" numOctaves="4" seed="17" result="noise"/>
    <feColorMatrix in="noise" type="matrix"
      values="0 0 0 0 0.45  0 0 0 0 0.28  0 0 0 0 0.08  0 0 0 0.14 0" result="grain"/>
    <feBlend in="SourceGraphic" in2="grain" mode="multiply"/>
  </filter>
  <filter id="{uid}-boardShadow" x="-20%" y="-10%" width="140%" height="150%">
    <feDropShadow dx="0" dy="5" stdDeviation="6" flood-color="#000" flood-opacity="0.55"/>
    <feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#000" flood-opacity="0.35"/>
  </filter>
  <radialGradient id="{uid}-blackStone" cx="34%" cy="28%" r="68%" fx="28%" fy="22%">
    <stop offset="0%" stop-color="#5a5a5a"/>
    <stop offset="28%" stop-color="#2e2e2e"/>
    <stop offset="62%" stop-color="#121212"/>
    <stop offset="88%" stop-color="#050505"/>
    <stop offset="100%" stop-color="#000000"/>
  </radialGradient>
  <radialGradient id="{uid}-whiteStone" cx="30%" cy="26%" r="70%" fx="26%" fy="20%">
    <stop offset="0%" stop-color="#fffffc"/>
    <stop offset="32%" stop-color="#f7f2e8"/>
    <stop offset="58%" stop-color="#ebe3d4"/>
    <stop offset="82%" stop-color="#d9cfbe"/>
    <stop offset="100%" stop-color="#b8aa96"/>
  </radialGradient>
  <filter id="{uid}-stoneDepth" x="-50%" y="-50%" width="200%" height="200%">
    <feDropShadow dx="0.6" dy="1.8" stdDeviation="1.3" flood-color="#1a1008" flood-opacity="0.55"/>
    <feDropShadow dx="0" dy="0.4" stdDeviation="0.4" flood-color="#000" flood-opacity="0.25"/>
  </filter>
  <linearGradient id="{uid}-legendBg" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="#f2e4c4"/>
    <stop offset="40%" stop-color="#e8d4a8"/>
    <stop offset="100%" stop-color="#d9c08a"/>
  </linearGradient>
  <linearGradient id="{uid}-legendShine" x1="0%" y1="0%" x2="0%" y2="100%">
    <stop offset="0%" stop-color="#fff" stop-opacity="0.28"/>
    <stop offset="55%" stop-color="#fff" stop-opacity="0.05"/>
    <stop offset="100%" stop-color="#4a3210" stop-opacity="0.1"/>
  </linearGradient>
  <filter id="{uid}-markHalo" x="-50%" y="-50%" width="200%" height="200%">
    <feDropShadow dx="0" dy="0" stdDeviation="1.2" flood-color="#fff" flood-opacity="0.9"/>
  </filter>
</defs>
"""


def _svg_defs(uid: str) -> str:
    return _SVG_DEFS_TEMPLATE.format(uid=uid)
from .rank_colors import (
    human_consensus_label,
    rank_compact_label,
    rank_display_label,
    rank_swatch_style,
    sort_ranks_by_strength,
    strongest_rank_label,
    _norm_rank_label,
)

def _stars(size: int) -> set[tuple[int, int]]:
    if size == 19:
        return _STAR_19
    if size == 13:
        return {(3, 3), (3, 9), (9, 3), (9, 9), (6, 6)}
    if size == 9:
        return {(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)}
    return set()


def _draw_stone(
    parts: list[str],
    x: float,
    y: float,
    stone: str,
    *,
    uid: str,
    cell: float,
) -> None:
    """Pierre type ardoise / coquillage avec reflets et ombre portée."""
    rad = cell * 0.465
    parts.append(
        f'<ellipse cx="{x + 0.55:.1f}" cy="{y + 1.35:.1f}" '
        f'rx="{rad * 0.9:.1f}" ry="{rad * 0.52:.1f}" fill="#1a1208" opacity="0.26"/>'
    )
    grad = f"{uid}-blackStone" if stone == "b" else f"{uid}-whiteStone"
    sw = "0.35" if stone == "b" else "0.55"
    sc = "#0a0a0a" if stone == "b" else "#9a8a78"
    parts.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad:.1f}" '
        f'fill="url(#{grad})" stroke="{sc}" stroke-width="{sw}" '
        f'filter="url(#{uid}-stoneDepth)"/>'
    )
    if stone == "b":
        parts.append(
            f'<ellipse cx="{x - rad * 0.28:.1f}" cy="{y - rad * 0.32:.1f}" '
            f'rx="{rad * 0.36:.1f}" ry="{rad * 0.2:.1f}" fill="#9a9a9a" opacity="0.13"/>'
        )
        parts.append(
            f'<ellipse cx="{x - rad * 0.14:.1f}" cy="{y - rad * 0.4:.1f}" '
            f'rx="{rad * 0.11:.1f}" ry="{rad * 0.07:.1f}" fill="#d8d8d8" opacity="0.2"/>'
        )
    else:
        parts.append(
            f'<ellipse cx="{x - rad * 0.24:.1f}" cy="{y - rad * 0.34:.1f}" '
            f'rx="{rad * 0.4:.1f}" ry="{rad * 0.26:.1f}" fill="#ffffff" opacity="0.58"/>'
        )
        parts.append(
            f'<ellipse cx="{x + rad * 0.18:.1f}" cy="{y + rad * 0.22:.1f}" '
            f'rx="{rad * 0.22:.1f}" ry="{rad * 0.13:.1f}" fill="#c8baa8" opacity="0.32"/>'
        )


def _draw_board_surface(
    parts: list[str],
    *,
    ox: float,
    oy: float,
    board_w: float,
    uid: str,
    bevel: float = 7,
) -> None:
    """Goban type kaya : cadre bois, grain, vignette."""
    fx, fy = ox - bevel, oy - bevel
    fw, fh = board_w + bevel * 2, board_w + bevel * 2
    parts.append(
        f'<rect x="{fx:.1f}" y="{fy:.1f}" width="{fw:.1f}" height="{fh:.1f}" '
        f'rx="11" fill="url(#{uid}-frameWood)" filter="url(#{uid}-boardShadow)"/>'
    )
    parts.append(
        f'<rect x="{ox:.1f}" y="{oy:.1f}" width="{board_w:.1f}" height="{board_w:.1f}" '
        f'rx="5" fill="url(#{uid}-wood)" filter="url(#{uid}-woodGrain)"/>'
    )
    parts.append(
        f'<rect x="{ox:.1f}" y="{oy:.1f}" width="{board_w:.1f}" height="{board_w:.1f}" '
        f'rx="5" fill="url(#{uid}-woodShine)"/>'
    )
    parts.append(
        f'<rect x="{ox:.1f}" y="{oy:.1f}" width="{board_w:.1f}" height="{board_w:.1f}" '
        f'rx="5" fill="url(#{uid}-woodVignette)"/>'
    )
    parts.append(
        f'<rect x="{ox + 3:.1f}" y="{oy + 3:.1f}" width="{board_w - 6:.1f}" height="{board_w - 6:.1f}" '
        f'fill="none" stroke="#6b4f12" stroke-width="1.2" rx="4" opacity="0.35"/>'
    )


_MARK_RADIUS_FRAC = 0.42


def _mark_radius(cell: float) -> float:
    return cell * _MARK_RADIUS_FRAC


def _bottom_arc_angles(n: int, *, ring_r: float, pr: float) -> list[float]:
    """Arc concentré en bas ; s'étend vers le haut si beaucoup de pastilles."""
    center = math.pi / 2
    if n <= 1:
        return [center]
    chord = 2.4 * pr
    min_step = chord / ring_r if ring_r > 0 else 0.34
    min_step = max(min_step, 0.32)
    span = min(3.05, min_step * (n - 1))
    span = max(span, min_step)
    start = center - span / 2
    return [start + span * i / (n - 1) for i in range(n)]


def _pill_radius_for_count(cell: float, n: int) -> float:
    if n <= 2:
        return cell * 0.132
    if n <= 4:
        return cell * 0.122
    if n <= 6:
        return cell * 0.112
    return cell * 0.102


def _fit_font_in_pill(cell: float, text: str, pr: float) -> float:
    """Police max dans une mini-pastille."""
    n = max(len(text), 1)
    by_width = (1.78 * pr) / (n * 0.50)
    by_height = 1.55 * pr
    return min(by_width, by_height, cell * 0.24)


def _append_rank_pills_on_ring(
    parts: list[str],
    x: float,
    y: float,
    cell: float,
    ranks: list[str],
    *,
    ring_r: float,
    exclude_strongest: bool = False,
) -> None:
    """Pastilles sur l'arc bas ; optionnellement sans le rang le plus fort (déjà dans le cercle)."""
    sorted_ranks = sort_ranks_by_strength(ranks)
    if exclude_strongest and sorted_ranks:
        sorted_ranks = sorted_ranks[1:]
    n = len(sorted_ranks)
    if not n:
        return
    pr = _pill_radius_for_count(cell, n)
    orbit = ring_r + pr * 0.62
    for angle, rank in zip(_bottom_arc_angles(n, ring_r=orbit, pr=pr), sorted_ranks):
        px = x + orbit * math.cos(angle)
        py = y + orbit * math.sin(angle)
        _mini_rank_pill(parts, px, py, cell, rank, radius=pr)


def _fit_font_in_circle(
    cell: float,
    text: str,
    *,
    mr: float,
    line_share: float = 1.0,
) -> float:
    """Taille de police max pour tenir dans un cercle de rayon mr."""
    n = max(len(text), 1)
    by_width = (1.82 * mr) / (n * 0.56)
    by_height = 1.62 * mr * line_share
    return min(by_width, by_height, cell * 0.30)


def _dual_line_layout(
    cell: float,
    top: str,
    bottom: str,
    *,
    mr: float,
) -> tuple[float, float, float, float]:
    """Police + décalages verticaux (depuis le centre) pour deux lignes."""
    gap = cell * 0.015
    fs_top = _fit_font_in_circle(cell, top, mr=mr, line_share=0.48)
    fs_bottom = _fit_font_in_circle(cell, bottom, mr=mr, line_share=0.44)
    total = fs_top + fs_bottom + gap
    budget = 1.72 * mr
    if total > budget:
        scale = budget / total
        fs_top *= scale
        fs_bottom *= scale
    y_top_off, y_bottom_off = _dual_line_offsets(fs_top, fs_bottom, gap)
    return fs_top, fs_bottom, y_top_off, y_bottom_off


def _dual_line_offsets(
    fs_top: float,
    fs_bottom: float,
    gap: float,
) -> tuple[float, float]:
    half = (fs_top + fs_bottom + gap) / 2
    return -(half - fs_top / 2), half - fs_bottom / 2


def _katago_mark(x: float, y: float, cell: float) -> list[str]:
    """KataGo #1 (rétrocompat)."""
    return _katago_top_mark(x, y, cell, 1)


def _katago_top_mark(
    x: float,
    y: float,
    cell: float,
    rank: int,
    *,
    human_labels: list[str] | None = None,
) -> list[str]:
    """Pastille IA1–IA5. Pur KataGo : IA1 vert/blanc, IA2–5 crème. Avec humain : IA + rang dessous."""
    mr = _mark_radius(cell)
    has_human = bool(human_labels)
    label = f"IA{rank}"
    out: list[str] = []

    if has_human:
        humans = sort_ranks_by_strength(human_labels or [])
        if rank == 1:
            fill, ia_tc, stroke, sw = "#22c55e", "#ffffff", "#15803d", 2.0
        else:
            fill, ia_tc, stroke, sw = "#f5edd8", "#5c4a28", "#c4a86a", 1.6
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{mr:.1f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw:.1f}"/>'
        )
        fs_ia = _fit_font_in_circle(cell, label, mr=mr, line_share=0.45)
        y_ia = y - cell * 0.09 if humans else y
        out.append(
            f'<text x="{x:.1f}" y="{y_ia:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fs_ia:.1f}" font-weight="800" '
            f'font-family="system-ui,sans-serif" fill="{ia_tc}">{label}</text>'
        )
        if humans:
            _append_rank_pills_on_ring(out, x, y, cell, humans, ring_r=mr)
        return out

    if rank == 1:
        fill, tc, stroke, sw = "#22c55e", "#ffffff", "#15803d", 2.0
    else:
        fill, tc, stroke, sw = "#f5edd8", "#5c4a28", "#c4a86a", 1.6
    fs = _fit_font_in_circle(cell, label, mr=mr)
    out.extend([
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{mr:.1f}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw:.1f}"/>',
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="central" '
        f'font-size="{fs:.1f}" font-weight="800" font-family="system-ui,sans-serif" '
        f'fill="{tc}">{label}</text>',
    ])
    return out


def _played_mark(parts: list[str], x: float, y: float, cell: float) -> None:
    """Anneau rouge autour du coup joué (au-dessus de la pierre)."""
    r = cell * 0.51
    sw_outer = max(1.8, cell * 0.07)
    sw_inner = max(1.4, cell * 0.045)
    parts.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="none" '
        f'stroke="#ffffff" stroke-width="{sw_outer:.1f}" opacity="0.92"/>'
    )
    parts.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="none" '
        f'stroke="#ef4444" stroke-width="{sw_inner:.1f}"/>'
    )


def _human_rank_mark(
    x: float, y: float, labels: list[str], cell: float,
) -> list[str]:
    """Coup humain alternatif : cercle = couleur du plus fort, pastilles pour les autres."""
    sorted_ranks = sort_ranks_by_strength(labels)
    if not sorted_ranks:
        return []
    strongest = sorted_ranks[0]
    fill, tc = rank_swatch_style(strongest)
    display = rank_compact_label(strongest)
    mr = _mark_radius(cell)
    out: list[str] = []
    out.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{mr:.1f}" fill="{fill}" '
        f'stroke="{fill}" stroke-width="1"/>'
    )
    fs = _fit_font_in_circle(cell, display, mr=mr)
    out.append(
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="central" '
        f'font-size="{fs:.1f}" font-weight="800" font-family="system-ui,sans-serif" '
        f'fill="{tc}">{display}</text>'
    )
    if len(sorted_ranks) > 1:
        _append_rank_pills_on_ring(
            out, x, y, cell, sorted_ranks, ring_r=mr, exclude_strongest=True,
        )
    return out


def _rank_mark(x: float, y: float, label: str, cell: float) -> list[str]:
    """Rétrocompat : un seul libellé."""
    return _human_rank_mark(x, y, [label], cell)


def _mini_rank_pill(
    parts: list[str],
    x: float,
    y: float,
    cell: float,
    rank: str,
    *,
    radius: float | None = None,
) -> None:
    """Mini pastille colorée (rang humain), sans pourtour."""
    fill, tc = rank_swatch_style(rank)
    pr = radius if radius is not None else cell * 0.10
    compact = rank_compact_label(rank)
    if _norm_rank_label(rank) in ("20", "23"):
        pr *= 1.08
    parts.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{pr:.1f}" fill="{fill}" stroke="none"/>'
    )
    fs = _fit_font_in_pill(cell, compact, pr)
    parts.append(
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="central" '
        f'font-size="{fs:.1f}" font-weight="800" font-family="system-ui,sans-serif" '
        f'fill="{tc}">{compact}</text>'
    )


def _played_sl_badge(
    parts: list[str], x: float, y: float, cell: float, ranks: list[str],
) -> None:
    """Pastilles sur le pourtour de l'anneau rouge (arc bas)."""
    if not ranks:
        return
    ring_r = cell * 0.51
    _append_rank_pills_on_ring(parts, x, y, cell, ranks, ring_r=ring_r)


def _draw_coords(
    parts: list[str],
    n: int,
    *,
    ox: float,
    oy: float,
    margin: int,
    cell: int,
    board_w: float,
) -> None:
    """Lettres/chiffres dans la bande entre le bord du goban et la grille."""
    fs = min(8.0, margin * 0.42)
    col = "#3d2810"
    inset = margin * _COORD_BORDER_FRAC
    mid_top = oy + inset
    mid_bot = oy + board_w - inset
    mid_left = ox + inset
    mid_right = ox + board_w - inset
    for c in range(n):
        x = ox + margin + c * cell
        letter = _GTP_COLS[c] if c < len(_GTP_COLS) else "?"
        parts.append(
            f'<text x="{x:.1f}" y="{mid_top:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fs:.1f}" font-weight="700" '
            f'fill="{col}" opacity="0.88">{letter}</text>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{mid_bot:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fs:.1f}" font-weight="700" '
            f'fill="{col}" opacity="0.88">{letter}</text>'
        )
    for r in range(n):
        y = oy + margin + r * cell
        num = str(n - r)
        parts.append(
            f'<text x="{mid_left:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fs:.1f}" font-weight="700" '
            f'fill="{col}" opacity="0.88">{num}</text>'
        )
        parts.append(
            f'<text x="{mid_right:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fs:.1f}" font-weight="700" '
            f'fill="{col}" opacity="0.88">{num}</text>'
        )


def _legend_rank_swatch_width(label: str) -> float:
    """Largeur réservée pour une pastille légende (planification)."""
    display = rank_display_label(label)
    gap = 5.0
    if display.startswith("pro "):
        fs = 5.5
        return max(34.0, len(display) * fs * 0.62 + 12) + gap
    return 13.0 + gap


def _legend_ranks_for_display(labels: list[str]) -> list[str]:
    """Dans d'abord, pros en fin de ligne (évite le chevauchement)."""
    ordered = sort_ranks_by_strength(labels)
    dans: list[str] = []
    pros: list[str] = []
    for lbl in ordered:
        if rank_display_label(lbl).startswith("pro "):
            pros.append(lbl)
        else:
            dans.append(lbl)
    return dans + pros


def _legend_rank_swatch(parts: list[str], x_left: float, cy: float, label: str) -> float:
    """Pastille légende alignée à gauche ; retourne la largeur consommée."""
    display = rank_display_label(label)
    fill, text_color = rank_swatch_style(label)
    gap = 5.0
    if display.startswith("pro "):
        fs = 5.5
        w = max(34.0, len(display) * fs * 0.62 + 12)
        h = 11.5
        stroke = "#44403c" if fill.lower() in ("#fafaf9", "#f4f4f5", "#ffffff") else "none"
        parts.append(
            f'<rect x="{x_left:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{h / 2:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>'
        )
        parts.append(
            f'<text x="{x_left + w / 2:.1f}" y="{cy + fs * 0.35:.1f}" text-anchor="middle" '
            f'font-size="{fs:.1f}" font-weight="800" font-family="system-ui,sans-serif" '
            f'fill="{text_color}">{display}</text>'
        )
        return w + gap
    r = 6.0
    cx = x_left + r
    stroke = "#44403c" if fill.lower() in ("#fafaf9", "#f4f4f5", "#ffffff") else "none"
    parts.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="0.8"/>'
    )
    parts.append(
        f'<text x="{cx:.1f}" y="{cy + 3.4:.1f}" text-anchor="middle" '
        f'font-size="6.8" font-weight="800" font-family="system-ui,sans-serif" '
        f'fill="{text_color}">{display}</text>'
    )
    return r * 2 + gap


def _legend_text(
    parts: list[str],
    x: float,
    y: float,
    text: str,
    *,
    size: float = 9,
    weight: str = "400",
    fill: str = "#475569",
) -> None:
    parts.append(
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" font-family="system-ui,sans-serif">{text}</text>'
    )


_LEGEND_ROW_TOP = 14.0
_LEGEND_SWATCH_HALF_H = 7.0
_LEGEND_ROW_GAP = 4.0


def _legend_swatch_available_width(inner_w: float, section: str) -> float:
    """Largeur dispo pour les pastilles de rang, alignée sur _draw_context_legend."""
    if section == "ranks_same":
        return inner_w - min(128.0, inner_w * 0.46)
    return inner_w - 96.0


def _swatch_bottom_offset(
    labels: list[str], available_w: float, ly: float, row_h: float,
) -> float:
    """Bas des pastilles (sans dessiner), aligné sur _draw_legend_swatches_wrapped."""
    ordered = _legend_ranks_for_display(labels)
    dans = [l for l in ordered if not rank_display_label(l).startswith("pro ")]
    pros = [l for l in ordered if rank_display_label(l).startswith("pro ")]
    all_w = sum(_legend_rank_swatch_width(l) for l in ordered) + (8 if pros and dans else 0)
    if all_w <= available_w or not ordered:
        return ly + _LEGEND_SWATCH_HALF_H
    ly2 = ly + row_h * 0.78
    return ly2 + _LEGEND_SWATCH_HALF_H


def _legend_advance_after_row(
    row_top: float,
    swatch_bottom: float,
    row_h: float,
    *,
    pill_font: float | None = None,
) -> float:
    """Prochaine ligne après une rangée légende (évite chevauchements)."""
    content_bottom = swatch_bottom
    if pill_font is not None:
        content_bottom = max(content_bottom, row_top + (pill_font + 9) / 2)
    return max(row_top + row_h, content_bottom + _LEGEND_ROW_GAP)


def _legend_top_ia_height_delta(
    labels: list[str], inner_w: float, row_h: float, *, label_offset: float = 38.0,
) -> float:
    """Avancement vertical de la section Top IA (aligné sur _draw_legend_top_ia)."""
    if not labels:
        return 0.0
    font = _legend_top_ia_font_size(labels, inner_w, label_offset)
    line_w = sum(_pill_width(lbl, font) + 5 for lbl in labels)
    if line_w > inner_w - 4 and len(labels) > 3:
        return row_h * 0.88 * 2 + row_h * 0.2
    return row_h


def _legend_height_from_context(ctx: dict[str, Any] | None, *, width: float = 296.0) -> int:
    """Hauteur réelle de la légende (sans marge morte en bas)."""
    if not ctx:
        return 62
    pad = 10.0
    inner_w = width - 2 * pad
    ly = _LEGEND_ROW_TOP
    row_h = 22.0

    ly += row_h
    ly += row_h

    top_moves = ctx.get("top5") or ctx.get("top3") or []
    if top_moves:
        labels = []
        for i, (mv, sc) in enumerate(top_moves[:5], 1):
            sc_s = _legend_score_str(sc)
            labels.append(f"IA{i} {mv}" + (f" {sc_s}" if sc_s else ""))
        ly += _legend_top_ia_height_delta(labels, inner_w, row_h)

    if ctx.get("ranks_same"):
        row_top = ly
        sb = _swatch_bottom_offset(
            ctx["ranks_same"],
            _legend_swatch_available_width(inner_w, "ranks_same"),
            row_top,
            row_h,
        )
        ly = _legend_advance_after_row(row_top, sb, row_h)

    for line in ctx.get("human_lines") or []:
        lbl = line.get("labels") or ""
        rank_labels = [p.strip() for p in lbl.split("·") if p.strip()]
        row_top = ly
        sb = _swatch_bottom_offset(
            rank_labels,
            _legend_swatch_available_width(inner_w, "human_alt"),
            row_top,
            row_h,
        )
        ly = _legend_advance_after_row(row_top, sb, row_h, pill_font=7.5)

    return int(ly + _LEGEND_ROW_GAP)


def _estimate_legend_width(ctx: dict[str, Any] | None) -> float:
    if not ctx:
        return 300.0
    pad = 10.0
    max_w = 300.0
    top_moves = ctx.get("top5") or ctx.get("top3") or []
    if top_moves:
        labels = []
        for i, (mv, sc) in enumerate(top_moves[:5], 1):
            sc_s = _legend_score_str(sc)
            labels.append(f"IA{i} {mv}" + (f" {sc_s}" if sc_s else ""))
        font = 7.0
        max_w = max(max_w, pad + 38 + sum(_pill_width(lbl, font) + 5 for lbl in labels) + pad)
    for line in ctx.get("human_lines") or []:
        lbl = line.get("labels") or ""
        mv = line.get("move") or "?"
        sc = _legend_score_str(line.get("score"))
        rank_labels = [p.strip() for p in lbl.split("·") if p.strip()]
        ordered = _legend_ranks_for_display(rank_labels)
        sw = sum(_legend_rank_swatch_width(l) for l in ordered)
        dans = [l for l in ordered if not rank_display_label(l).startswith("pro ")]
        pros = [l for l in ordered if rank_display_label(l).startswith("pro ")]
        if pros and dans:
            sw += 8
        move_w = _pill_width(f"{mv}  {sc}" if sc else mv, 7.5)
        max_w = max(max_w, pad + 96 + sw + move_w + pad + 8)
    if ctx.get("ranks_same"):
        ordered = _legend_ranks_for_display(ctx["ranks_same"])
        sw = sum(_legend_rank_swatch_width(l) for l in ordered)
        max_w = max(max_w, pad + 128 + sw + pad)
    return max_w


def _draw_legend_swatches_wrapped(
    parts: list[str],
    *,
    x_start: float,
    ly: float,
    width: float,
    pad: float,
    labels: list[str],
    row_h: float,
) -> float:
    """Dessine les pastilles de rang ; retourne le bas (ly) utilisé."""
    ordered = _legend_ranks_for_display(labels)
    dans = [l for l in ordered if not rank_display_label(l).startswith("pro ")]
    pros = [l for l in ordered if rank_display_label(l).startswith("pro ")]
    available = width - pad - x_start
    all_w = sum(_legend_rank_swatch_width(l) for l in ordered) + (8 if pros and dans else 0)
    if all_w <= available or not ordered:
        sx = x_start
        for i, label in enumerate(ordered):
            if i == len(dans) and pros:
                sx += 8
            sx += _legend_rank_swatch(parts, sx, ly, label)
        return ly + _LEGEND_SWATCH_HALF_H
    sx = x_start
    for label in dans:
        sx += _legend_rank_swatch(parts, sx, ly, label)
    ly2 = ly + row_h * 0.78
    sx2 = x_start
    for label in pros:
        sx2 += _legend_rank_swatch(parts, sx2, ly2, label)
    return ly2 + _LEGEND_SWATCH_HALF_H


def _legend_top_ia_font_size(labels: list[str], inner_w: float, label_offset: float = 38.0) -> float:
    font = 7.5
    while font >= 5.4:
        total = sum(_pill_width(label, font) + 5 for label in labels)
        if total <= inner_w - label_offset:
            return font
        font -= 0.25
    return 5.4


def _draw_legend_top_ia(
    parts: list[str],
    *,
    pad: float,
    ly: float,
    inner_w: float,
    width: float,
    row_h: float,
    top_moves: list[tuple[str, float | None]],
) -> float:
    if not top_moves:
        return ly
    _legend_row_sep(parts, ly - row_h / 2, width, pad)
    labels: list[str] = []
    for i, (mv, sc) in enumerate(top_moves, 1):
        sc_s = _legend_score_str(sc)
        labels.append(f"IA{i} {mv}" + (f" {sc_s}" if sc_s else ""))
    font = _legend_top_ia_font_size(labels, inner_w)
    _legend_label(parts, pad, ly, "Top IA")
    cx = pad + 38
    line_w = sum(_pill_width(label, font) + 5 for label in labels)
    if line_w > inner_w - 4 and len(labels) > 3:
        mid = (len(labels) + 1) // 2
        for row_idx, row_labels in enumerate((labels[:mid], labels[mid:])):
            cx_row = pad + 38
            for j, label in enumerate(row_labels):
                i = row_idx * mid + j + 1
                if i == 1:
                    fill, stroke, tc = "#22c55e", "#15803d", "#ffffff"
                else:
                    fill, stroke, tc = "#f5edd8", "#c4a86a", "#5c4a28"
                cx_row += _legend_pill(
                    parts, cx_row, ly, label,
                    fill=fill, stroke=stroke, text_fill=tc, font_size=font,
                )
            ly += row_h * 0.88
        return ly + row_h * 0.2
    for i, label in enumerate(labels, 1):
        if i == 1:
            fill, stroke, tc = "#22c55e", "#15803d", "#ffffff"
        else:
            fill, stroke, tc = "#f5edd8", "#c4a86a", "#5c4a28"
        cx += _legend_pill(
            parts, cx, ly, label,
            fill=fill, stroke=stroke, text_fill=tc, font_size=font,
        )
    return ly + row_h


def _legend_score_str(score: float | None) -> str:
    if score is None:
        return ""
    return f"{score:+.1f}"


def _pill_width(text: str, font_size: float = 8) -> float:
    return max(28.0, len(text) * font_size * 0.58 + 12)


def _legend_pill(
    parts: list[str],
    x: float,
    cy: float,
    text: str,
    *,
    fill: str = "#f1f5f9",
    stroke: str = "#cbd5e1",
    text_fill: str = "#334155",
    font_size: float = 8,
    font_weight: str = "600",
    max_width: float | None = None,
) -> float:
    """Pastille arrondie ; retourne la largeur occupée (+ marge)."""
    label = text
    w = _pill_width(label, font_size)
    if max_width is not None and w > max_width:
        while len(label) > 4 and w > max_width:
            label = label[:-2].rstrip(" ·(")
            w = _pill_width(label, font_size)
    h = font_size + 9
    top = cy - h / 2
    parts.append(
        f'<rect x="{x:.1f}" y="{top:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{h / 2:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="0.7"/>'
    )
    parts.append(
        f'<text x="{x + w / 2:.1f}" y="{cy + font_size * 0.35:.1f}" '
        f'text-anchor="middle" font-size="{font_size}" font-weight="{font_weight}" '
        f'font-family="system-ui,sans-serif" fill="{text_fill}">{label}</text>'
    )
    return w + 5


def _legend_pill_right(
    parts: list[str],
    *,
    right_x: float,
    cy: float,
    text: str,
    max_width: float,
    **kwargs: Any,
) -> None:
    """Pastille alignée à droite (bord droit = right_x)."""
    font_size = float(kwargs.get("font_size", 8))
    label = text
    if _pill_width(label, font_size) > max_width:
        head = text.split(" vs ")[0].strip()
        if head and _pill_width(head, font_size) <= max_width:
            label = head
        else:
            try:
                val = float(text.lstrip("−-").split()[0])
                label = f"−{val:.1f}"
            except (ValueError, IndexError):
                label = text[:12]
    w = _pill_width(label, font_size)
    _legend_pill(parts, max(4.0, right_x - w), cy, label, max_width=max_width, **kwargs)


def _legend_label(parts: list[str], x: float, cy: float, text: str) -> None:
    _legend_text(parts, x, cy + 3, text, size=7.5, weight="700", fill="#7a6248")


def _legend_row_sep(parts: list[str], y: float, width: float, pad: float) -> None:
    parts.append(
        f'<line x1="{pad:.1f}" y1="{y:.1f}" x2="{width - pad:.1f}" y2="{y:.1f}" '
        f'stroke="#6b4f12" stroke-width="0.6" opacity="0.22"/>'
    )


def _draw_legend_background(
    parts: list[str],
    *,
    width: float,
    y: float,
    height: float,
    uid: str,
) -> None:
    """Panneau légende style parchemin / bois clair."""
    parts.append(
        f'<rect x="0" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'fill="url(#{uid}-legendBg)" filter="url(#{uid}-woodGrain)"/>'
    )
    parts.append(
        f'<rect x="0" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'fill="url(#{uid}-legendShine)"/>'
    )
    parts.append(
        f'<line x1="0" y1="{y:.1f}" x2="{width:.1f}" y2="{y:.1f}" '
        f'stroke="#3d2810" stroke-width="1.2" opacity="0.35"/>'
    )
    parts.append(
        f'<rect x="5" y="{y + 4:.1f}" width="{width - 10:.1f}" height="{height - 8:.1f}" '
        f'fill="none" stroke="#8b6914" stroke-width="0.7" rx="5" opacity="0.4"/>'
    )


def _draw_context_legend(
    parts: list[str],
    *,
    width: float,
    y: float,
    height: float,
    player_color: str | None,
    game_outcome: str | None,
    ctx: dict[str, Any] | None,
    uid: str = "",
) -> None:
    """Légende contextuelle, tons bois chauds assortis au goban."""
    pad = 10.0
    inner_w = width - 2 * pad
    _draw_legend_background(parts, width=width, y=y, height=height, uid=uid)
    if not ctx:
        _legend_text(parts, pad, y + 20, "Légende indisponible", size=9, fill="#5c4a32")
        return

    ly = y + _LEGEND_ROW_TOP
    row_h = 22
    scores: dict[str, float] = ctx.get("scores") or {}
    played = ctx.get("played") or "?"
    best = ctx.get("best") or "?"
    played_u = played.upper().strip()
    best_u = best.upper().strip()
    p_sc = _legend_score_str(scores.get(played_u))
    b_sc = _legend_score_str(scores.get(best_u))

    if player_color:
        parts.append(_legend_stone(pad + 6, ly - 2, player_color, cell=7, uid=uid))
        you = "Noir" if player_color.upper() == "B" else "Blanc"
        _legend_text(parts, pad + 17, ly + 3, f"Vous · {you}", size=8.5, weight="700", fill="#4a3828")
    outcome_labels = {
        "win": ("Victoire", "#3d7a3a"),
        "loss": ("Défaite", "#a84a3a"),
        "draw": ("Nulle", "#7a6248"),
    }
    if game_outcome in outcome_labels:
        ol, oc = outcome_labels[game_outcome]
        _legend_text(parts, pad + 72, ly + 3, ol, size=8.5, weight="700", fill=oc)
    loss = ctx.get("point_loss")
    if loss is not None and float(loss) > 0.05:
        loss_txt = f"−{float(loss):.1f} pts"
        best_sc = _legend_score_str(scores.get(best_u))
        if best_sc:
            loss_txt = f"−{float(loss):.1f} vs IA1 ({best_sc})"
        _legend_pill_right(
            parts,
            right_x=width - pad,
            cy=ly,
            text=loss_txt,
            max_width=inner_w * 0.48,
            fill="#edd5c8", stroke="#b85c4a", text_fill="#7a2820", font_size=6.8,
        )

    ly += row_h
    _legend_row_sep(parts, ly - row_h / 2, width, pad)
    played_txt = f"{played}  {p_sc}" if p_sc else played
    best_txt = f"{best}  {b_sc}" if b_sc else best
    pw = _pill_width(played_txt)
    bw = _pill_width(best_txt)
    arrow_w = 14
    total = pw + arrow_w + bw
    sx = pad + max(0.0, (inner_w - total) / 2)
    _legend_pill(
        parts, sx, ly, played_txt,
        fill="#f5e0d8", stroke="#c97a5a", text_fill="#6b2e1f",
    )
    _legend_text(parts, sx + pw + 3, ly + 3, "→", size=9, weight="600", fill="#a89070")
    _legend_pill(
        parts, sx + pw + arrow_w, ly, best_txt,
        fill="#e8f0e0", stroke="#6b8f5a", text_fill="#2d4a22",
    )

    ly += row_h

    top_moves = ctx.get("top5") or ctx.get("top3") or []
    ly = _draw_legend_top_ia(
        parts,
        pad=pad,
        ly=ly,
        inner_w=inner_w,
        width=width,
        row_h=row_h,
        top_moves=top_moves,
    )

    ranks_same = ctx.get("ranks_same") or []
    played_coord = ctx.get("played") or ""
    consensus = ctx.get("consensus_note")
    if ranks_same:
        _legend_row_sep(parts, ly - row_h / 2, width, pad)
        if consensus:
            same_lbl = f"Humain · {consensus}"
            if played_coord:
                same_lbl += f" ({played_coord})"
        else:
            same_lbl = (
                f"Humain · votre coup ({played_coord})"
                if played_coord else "Humain · votre coup"
            )
        _legend_label(parts, pad, ly, same_lbl[:34])
        labels = _legend_ranks_for_display(ranks_same)
        row_top = ly
        swatch_bottom = _draw_legend_swatches_wrapped(
            parts,
            x_start=pad + min(128, inner_w * 0.46),
            ly=row_top,
            width=width,
            pad=pad,
            labels=labels,
            row_h=row_h,
        )
        ly = _legend_advance_after_row(row_top, swatch_bottom, row_h)

    for line in ctx.get("human_lines") or []:
        _legend_row_sep(parts, ly - row_h / 2, width, pad)
        lbl = line.get("labels") or ""
        mv = line.get("move") or "?"
        sc = _legend_score_str(line.get("score"))
        _legend_label(parts, pad, ly, "Humain · autre coup")
        rank_labels = [p.strip() for p in lbl.split("·") if p.strip()]
        row_top = ly
        swatch_bottom = _draw_legend_swatches_wrapped(
            parts,
            x_start=pad + 96,
            ly=row_top,
            width=width,
            pad=pad,
            labels=rank_labels,
            row_h=row_h,
        )
        move_txt = f"{mv}  {sc}" if sc else mv
        _legend_pill_right(
            parts,
            right_x=width - pad,
            cy=row_top,
            text=move_txt,
            max_width=inner_w * 0.34,
            fill="#e5edd8", stroke="#7a9a6a", text_fill="#2d4028", font_size=7.5,
        )
        ly = _legend_advance_after_row(row_top, swatch_bottom, row_h, pill_font=7.5)


def _legend_height(ranks_same_as_played: list[str] | None) -> int:
    _ = ranks_same_as_played
    return 70


def _draw_legend_panel(
    parts: list[str],
    *,
    width: float,
    y: float,
    height: float,
    player_color: str | None,
    game_outcome: str | None,
    ranks_same_as_played: list[str] | None = None,
    legend_context: dict[str, Any] | None = None,
    uid: str = "",
) -> None:
    _draw_context_legend(
        parts,
        width=width,
        y=y,
        height=height,
        player_color=player_color,
        game_outcome=game_outcome,
        ctx=legend_context,
        uid=uid,
    )

def _legend_stone(x: float, y: float, color: str, cell: float, *, uid: str) -> str:
    r = cell * 0.55
    grad = f"{uid}-blackStone" if color.upper() == "B" else f"{uid}-whiteStone"
    sw = "0.35" if color.upper() == "B" else "0.55"
    sc = "#0a0a0a" if color.upper() == "B" else "#9a8a78"
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="url(#{grad})" '
        f'stroke="{sc}" stroke-width="{sw}" filter="url(#{uid}-stoneDepth)"/>'
    )


def full_board_svg(
    board: Board,
    *,
    size: int | None = None,
    highlight: set[tuple[int, int]] | None = None,
    played: tuple[int, int] | None = None,
    katago_marks: list[tuple[int, int, str, list[str]]] | None = None,
    rank_marks: list[tuple[int, int, list[str]]] | None = None,
    ranks_same_as_played: list[str] | None = None,
    legend_context: dict[str, Any] | None = None,
    player_color: str | None = None,
    game_outcome: str | None = None,
    cell: int = 14,
    margin: int = 18,
    display_width: int = 360,
) -> str:
    """Plateau 19x19 complet, rendu soigné."""
    uid = f"g{uuid.uuid4().hex[:8]}"
    n = size or board.side
    frame_pad = 8
    board_w = margin * 2 + cell * (n - 1)
    board_area_w = frame_pad + board_w + frame_pad
    legend_min_w = _estimate_legend_width(legend_context)
    total_w = max(board_area_w, legend_min_w)
    ox = (total_w - board_w) / 2
    oy = frame_pad
    legend_h = _legend_height_from_context(legend_context, width=total_w)
    board_block_h = frame_pad + board_w + frame_pad
    total_h = board_block_h + legend_h
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" width="{display_width}" '
        f'preserveAspectRatio="xMidYMid meet" class="go-board-full" role="img">',
        _svg_defs(uid),
        f'<rect width="{total_w}" height="{total_h}" fill="url(#{uid}-bg)"/>',
    ]
    _draw_board_surface(parts, ox=ox, oy=oy, board_w=board_w, uid=uid)

    def xy(r: int, c: int) -> tuple[float, float]:
        return ox + margin + c * cell, oy + margin + r * cell

    h_parts: list[str] = []
    v_parts: list[str] = []
    for r in range(n):
        x0, y = xy(r, 0)
        x1, _ = xy(r, n - 1)
        h_parts.append(f"M{x0:.1f},{y:.1f}L{x1:.1f},{y:.1f}")
    for c in range(n):
        x, y0 = xy(0, c)
        _, y1 = xy(n - 1, c)
        v_parts.append(f"M{x:.1f},{y0:.1f}L{x:.1f},{y1:.1f}")
    parts.append(
        f'<path d="{" ".join(h_parts)}" stroke="#3d2817" stroke-width="1.05" '
        f'fill="none" opacity="0.82"/>'
    )
    parts.append(
        f'<path d="{" ".join(v_parts)}" stroke="#3d2817" stroke-width="1.05" '
        f'fill="none" opacity="0.82"/>'
    )

    for r, c in _stars(n):
        x, y = xy(r, c)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#2a1a0c" opacity="0.85"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.2" fill="#4a3220" opacity="0.5"/>')

    _draw_coords(parts, n, ox=ox, oy=oy, margin=margin, cell=cell, board_w=board_w)

    if highlight:
        rows_h = [r for r, _ in highlight]
        cols_h = [c for _, c in highlight]
        r0, r1 = min(rows_h), max(rows_h)
        c0, c1 = min(cols_h), max(cols_h)
        x0, y0 = xy(r0, c0)
        x1, y1 = xy(r1, c1)
        pad = cell * 0.52
        parts.append(
            f'<rect x="{x0-pad:.1f}" y="{y0-pad:.1f}" '
            f'width="{x1-x0+pad*2:.1f}" height="{y1-y0+pad*2:.1f}" '
            f'rx="8" fill="#fbbf24" fill-opacity="0.38" stroke="#d97706" '
            f'stroke-width="2" stroke-opacity="0.75"/>'
        )

    for r in range(n):
        for c in range(n):
            stone = board.get(r, c)
            if not stone:
                continue
            x, y = xy(r, c)
            _draw_stone(parts, x, y, stone, uid=uid, cell=cell)

    for r, c, _label, human_labels in katago_marks or []:
        x, y = xy(r, c)
        rank: int | None = None
        if isinstance(_label, str):
            if _label.startswith("IA") and _label[2:].isdigit():
                rank = int(_label[2:])
            elif _label.startswith("#") and _label[1:].isdigit():
                rank = int(_label[1:])
            elif _label.isdigit():
                rank = int(_label)
        elif isinstance(_label, int):
            rank = _label
        if rank is not None:
            parts.extend(_katago_top_mark(
                x, y, cell, rank, human_labels=human_labels or None,
            ))
        else:
            parts.extend(_katago_mark(x, y, cell))

    for r, c, labels in rank_marks or []:
        x, y = xy(r, c)
        parts.extend(_human_rank_mark(x, y, labels, cell))

    if played:
        pr, pc = played
        x, y = xy(pr, pc)
        _played_mark(parts, x, y, cell)
        if ranks_same_as_played:
            _played_sl_badge(parts, x, y, cell, ranks_same_as_played)

    _draw_legend_panel(
        parts,
        width=total_w,
        y=board_block_h,
        height=legend_h,
        player_color=player_color,
        game_outcome=game_outcome,
        ranks_same_as_played=ranks_same_as_played,
        legend_context=legend_context,
        uid=uid,
    )
    parts.append("</svg>")
    return "\n".join(parts)

def patch_ascii_to_svg(ascii_patch: str, *, display_width: int = 220) -> str:
    """Petit diagramme local (fallback si pas de move_id)."""
    lines = [ln for ln in (ascii_patch or "").strip().splitlines() if ln]
    if not lines:
        return ""
    uid = f"g{uuid.uuid4().hex[:8]}"
    n = len(lines)
    cell = 12
    margin = 14
    pad = 6
    w = margin * 2 + cell * (n - 1)
    h = w
    total_w = w + pad * 2
    total_h = h + pad * 2
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" '
        f'width="{display_width}" preserveAspectRatio="xMidYMid meet" class="go-board-patch">',
        _svg_defs(uid),
        f'<rect width="{total_w}" height="{total_h}" fill="url(#{uid}-bg)"/>',
    ]
    _draw_board_surface(parts, ox=pad, oy=pad, board_w=w, uid=uid, bevel=5)

    def xy(r: int, c: int) -> tuple[float, float]:
        return pad + margin + c * cell, pad + margin + r * cell

    for r in range(n):
        x0, y = xy(r, 0)
        x1, _ = xy(r, n - 1)
        parts.append(
            f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" '
            f'stroke="#3d2817" stroke-width="0.85" opacity="0.78"/>'
        )
    for c in range(n):
        x, y0 = xy(0, c)
        _, y1 = xy(n - 1, c)
        parts.append(
            f'<line x1="{x:.1f}" y1="{y0:.1f}" x2="{x:.1f}" y2="{y1:.1f}" '
            f'stroke="#3d2817" stroke-width="0.85" opacity="0.78"/>'
        )

    stone_chars = {"●": "b", "○": "w", "o": "b", "x": "w", "b": "b", "w": "w"}
    for r, row in enumerate(lines):
        for c, ch in enumerate(row):
            if ch in (".", "+", "*"):
                if ch == "*":
                    x, y = xy(r, c)
                    parts.append(
                        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" '
                        f'fill="#f59e0b" stroke="#fff" stroke-width="0.8"/>'
                    )
                continue
            stone = stone_chars.get(ch)
            if not stone:
                continue
            x, y = xy(r, c)
            _draw_stone(parts, x, y, stone, uid=uid, cell=cell)
    parts.append("</svg>")
    return "\n".join(parts)
