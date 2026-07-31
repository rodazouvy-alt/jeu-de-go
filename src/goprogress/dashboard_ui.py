from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from typing import Any
from urllib.parse import quote

from .position_diagram import svg_for_move_cached
from .human_sl import RANK_LABELS
from .board_diagram import patch_ascii_to_svg
from .local_pattern import FUSEKI_MAX_MOVE, PATCH_RADIUS, PATCH_SIZE
from .rank_colors import rank_swatch_style
from .sgf_parse import (
    game_outcome, katago_score_for_player, classify_human_engine_verdict,
    katago_scores_by_move, classify_move_context, line_loss_from_scores,
    strong_ranks_score_gap, player_result_score, effective_move_quality,
    resolved_engine_best, point_loss_from_stored_scores,
)
from .sgf_timing import format_duration, format_speed_label
from .themes import THEME_LABELS

PHASE_LABELS = {
    "opening": "Ouverture",
    "middlegame": "Milieu de partie",
    "endgame": "Fin de partie",
}

SEVERITY_LABELS = {
    "mega_blunder": "Méga-blunder",
    "blunder": "Blunder",
    "mistake": "Erreur",
    "inaccuracy": "Imprécision",
    "ok": "OK",
}

SEVERITY_COLORS = {
    "mega_blunder": "#b91c1c",
    "blunder": "#dc2626",
    "mistake": "#ea580c",
    "inaccuracy": "#ca8a04",
    "ok": "#16a34a",
}


def _severity_cell(m: dict[str, Any], sev: str, *, player_color: str | None = None) -> str:
    html = ""
    if sev and sev != "ok":
        color = SEVERITY_COLORS.get(sev, "#666")
        label = escape(SEVERITY_LABELS.get(sev, sev))
        html += f'<span class="badge" style="background:{color}">{label}</span>'
        verdict = classify_human_engine_verdict(
            severity=sev,
            played_move=m.get("played_move"),
            human_rank_moves_json=m.get("human_rank_moves_json"),
        )
        if verdict:
            html += (
                f' <span class="badge badge-verdict" style="background:{verdict["color"]}" '
                f'title="Comparaison Human SL vs KataGo">{escape(verdict["label"])}</span>'
            )
    for tag in classify_move_context(
        m,
        player_color=player_color,
        prev_player_severity=m.get("_prev_player_severity"),
    ):
        html += (
            f' <span class="badge badge-context" style="background:{tag["color"]}" '
            f'title="Moment important dans la partie">{escape(tag["label"])}</span>'
        )
    return html or '<span class="badge" style="background:#16a34a">OK</span>'


def _format_priors(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        pdata = json.loads(raw)
        return " · ".join(
            f"{RANK_LABELS.get(k, k)}:{v:.0%}" for k, v in pdata.items()
        )
    except json.JSONDecodeError:
        return "—"


def _rank_mini_chip(lbl: str) -> str:
    bg, tc = rank_swatch_style(lbl)
    return (
        f'<span class="human-rank-mini" style="background:{bg};color:{tc}">'
        f'{escape(lbl)}</span>'
    )


def _katago_scores_by_move(
    move: dict[str, Any],
    player_color: str | None,
) -> dict[str, float]:
    return katago_scores_by_move(move, player_color)


def _score_label(scores: dict[str, float], gtp: str | None) -> str:
    if not gtp:
        return ""
    sl = scores.get(gtp.upper().strip())
    if sl is None:
        return ""
    return f" ({sl:+.1f})"


def _format_played_vs_best(move: dict[str, Any], player_color: str | None) -> str:
    played = move.get("played_move") or "?"
    best = resolved_engine_best(move) or "?"
    scores = _katago_scores_by_move(move, player_color)
    if move.get("top_moves_json"):
        try:
            tops = json.loads(move["top_moves_json"])
            if tops:
                top_mv = (tops[0].get("move") or "").upper().strip()
                sl = katago_score_for_player(tops[0].get("scoreLead"), player_color)
                if sl is not None and top_mv:
                    scores[top_mv] = sl
        except (json.JSONDecodeError, TypeError):
            pass

    played_key = played.upper().strip()
    best_key = best.upper().strip()
    played_sc = scores.get(played_key)
    best_sc = scores.get(best_key)
    if best_sc is None and scores:
        best_sc = max(scores.values())
    p_sc = f'<span class="sc">{played_sc:+.1f}</span>' if played_sc is not None else ""
    b_sc = f'<span class="sc">{best_sc:+.1f}</span>' if best_sc is not None else ""
    delta = point_loss_from_stored_scores(move, player_color)
    delta_html = ""
    if delta is not None and delta >= 0.05:
        delta_html = f'<span class="cmp-delta" title="Écart vs coup #1 IA">Δ −{delta:.1f}</span>'

    return (
        f'<div class="cmp-h">'
        f'<span class="cmp-played">{escape(played)}{p_sc}</span>'
        f'<span class="cmp-arrow">→</span>'
        f'<span class="cmp-best"><strong>{escape(best)}</strong>{b_sc}</span>'
        f'{delta_html}'
        f'</div>'
    )


def _format_top_moves_chips(
    raw: str | None,
    player_color: str | None = None,
    best_move: str | None = None,
) -> str:
    if not raw:
        if best_move:
            return f'<span class="chip chip-best">{escape(best_move)}</span>'
        return ""
    try:
        moves = json.loads(raw)
        chips = []
        for i, m in enumerate(moves[:5], 1):
            mv = escape(m.get("move") or "?")
            sl = katago_score_for_player(m.get("scoreLead"), player_color)
            sc = f" {sl:+.1f}" if sl is not None else ""
            cls = "chip chip-best" if i == 1 else "chip"
            chips.append(f'<span class="{cls}">#{i} {mv}{sc}</span>')
        return "".join(chips) if chips else ""
    except (json.JSONDecodeError, TypeError):
        return ""


def _format_katago_cell(move: dict[str, Any], player_color: str | None) -> str:
    compare = _format_played_vs_best(move, player_color)
    chips = _format_top_moves_chips(
        move.get("top_moves_json"), player_color, move.get("best_move"),
    )
    top_html = f'<div class="katago-top">{chips}</div>' if chips else ""
    return f'<div class="katago-block">{compare}{top_html}</div>'


def _format_priors_compact(raw: str | None) -> str:
    if not raw:
        return '<span class="muted">—</span>'
    try:
        data = json.loads(raw)
        chips: list[str] = []
        for k in (
            "rank_3d", "rank_4d", "rank_5d", "rank_6d", "rank_7d",
            "rank_8d", "rank_9d", "proyear_2020", "proyear_2023",
        ):
            if k not in data:
                continue
            v = float(data[k] or 0)
            if v <= 0.01:
                continue
            lbl = RANK_LABELS.get(k, k)
            swatch_key = lbl
            bg, _ = rank_swatch_style(swatch_key)
            chips.append(
                f'<span class="human-chip" title="{escape(lbl)}" style="border-color:{bg}">'
                f'<span class="human-chip-rank" style="color:{bg}">{escape(lbl)}</span>'
                f'<span class="human-chip-pct">{v:.0%}</span></span>'
            )
        if not chips:
            return '<span class="human-zero">0% — aucun rang ne jouerait votre coup</span>'
        return f'<div class="human-chips">{"".join(chips)}</div>'
    except (json.JSONDecodeError, TypeError):
        return "—"


def _format_rank_moves_compact(
    raw: str | None,
    scores: dict[str, float] | None = None,
) -> str:
    if not raw:
        return '<span class="muted">—</span>'
    try:
        data = json.loads(raw)
        by_move: dict[str, list[str]] = {}
        order = (
            "rank_3d", "rank_4d", "rank_5d", "rank_6d", "rank_7d",
            "rank_8d", "rank_9d", "proyear_2020", "proyear_2023",
        )
        for rank in order:
            if rank not in data:
                continue
            gtp = data[rank]
            label = RANK_LABELS.get(rank, rank)
            by_move.setdefault(gtp, []).append(label)
        if not by_move:
            return "—"
        if len(by_move) == 1:
            gtp = next(iter(by_move))
            sc = (scores or {}).get(gtp.upper().strip())
            sc_s = f' <span class="sc">{sc:+.1f}</span>' if sc is not None else ""
            return f'<span class="human-consensus">Tous → <strong>{escape(gtp)}</strong>{sc_s}</span>'

        parts = []
        for gtp, labels in by_move.items():
            sc = (scores or {}).get(gtp.upper().strip())
            sc_s = f' <span class="sc">{sc:+.1f}</span>' if sc is not None else ""
            chip_labels = "".join(_rank_mini_chip(lbl) for lbl in labels)
            parts.append(
                f'<div class="human-rank-line">'
                f'<div class="human-rank-grp">{chip_labels}</div>'
                f'<div class="human-rank-move">'
                f'<span class="human-rank-branch" aria-hidden="true">↳</span>'
                f'<strong>{escape(gtp)}</strong>{sc_s}'
                f'</div></div>'
            )
        return "".join(parts)
    except (json.JSONDecodeError, TypeError):
        return "—"


def _format_human_cell(move: dict[str, Any], player_color: str | None) -> str:
    scores = _katago_scores_by_move(move, player_color)
    priors = _format_priors_compact(move.get("human_prior_json"))
    ranks = _format_rank_moves_compact(move.get("human_rank_moves_json"), scores)
    return (
        f'<div class="human-block">'
        f'<div class="human-line"><span class="lbl">Votre coup :</span> {priors}</div>'
        f'<div class="human-line"><span class="lbl">Coups rangs :</span> {ranks}</div>'
        f'</div>'
    )


def _move_summary_cell(
    m: dict[str, Any], sev: str, phase: str, theme: str,
    *, player_color: str | None = None,
) -> str:
    theme_bit = ""
    if theme and theme != "—":
        theme_bit = f'<span class="move-theme" title="{escape(theme)}">{escape(theme)}</span>'
    point_loss = float(m.get("point_loss") or 0)
    line_loss = m.get("line_loss")
    if line_loss is None:
        line_loss = line_loss_from_scores(
            m.get("score_before"), m.get("score_after"), player_color or m.get("color") or "B",
        )
    else:
        line_loss = float(line_loss)
    if point_loss > 0.05:
        loss_html = (
            f'<div class="move-loss">{point_loss:.1f}'
            f'<span class="move-loss-u">pts vs IA</span></div>'
        )
    elif line_loss >= 0.5:
        loss_html = (
            f'<div class="move-loss move-loss-position">{line_loss:.1f}'
            f'<span class="move-loss-u">pts position</span></div>'
        )
    else:
        loss_html = '<div class="move-loss move-loss-ok">—</div>'
    strong_gap = strong_ranks_score_gap(m, player_color)
    if strong_gap is not None and abs(strong_gap) >= 0.5:
        loss_html += (
            f'<div class="move-loss move-loss-strong">{strong_gap:+.1f}'
            f'<span class="move-loss-u">pts vs moy. forts</span></div>'
        )
    return (
        f'<div class="move-summary">'
        f'<div class="move-summary-head">'
        f'<span class="move-num">#{m["move_number"]}</span>'
        f'<span class="move-phase">{escape(phase)}</span>'
        f'{theme_bit}'
        f'</div>'
        f'{loss_html}'
        f'<div class="move-badges">{_severity_cell(m, sev, player_color=player_color)}</div>'
        f'</div>'
    )


def _format_top_moves(
    raw: str | None,
    player_color: str | None = None,
    best_move: str | None = None,
) -> str:
    if not raw:
        if best_move:
            return f"1.{best_move} (meilleur IA)"
        return "—"
    try:
        moves = json.loads(raw)
        parts = []
        for i, m in enumerate(moves[:5], 1):
            mv = m.get("move", "?")
            sl = katago_score_for_player(m.get("scoreLead"), player_color)
            if sl is not None:
                parts.append(f"{i}.{mv} ({sl:+.1f})")
            else:
                parts.append(f"{i}.{mv}")
        return " · ".join(parts) if parts else "—"
    except (json.JSONDecodeError, TypeError):
        return "—"


def _format_rank_moves(
    raw: str | None,
    scores: dict[str, float] | None = None,
) -> str:
    if not raw:
        return "—"
    try:
        data = json.loads(raw)
        parts = []
        for rank in (
            "rank_3d", "rank_4d", "rank_5d", "rank_6d", "rank_7d",
            "rank_8d", "rank_9d", "proyear_2020", "proyear_2023",
        ):
            if rank not in data:
                continue
            gtp = data[rank]
            label = RANK_LABELS.get(rank, rank)
            sc = (scores or {}).get((gtp or "").upper().strip())
            if sc is not None:
                parts.append(f"{label}:{gtp} ({sc:+.1f})")
            else:
                parts.append(f"{label}:{gtp}")
        return " · ".join(parts) if parts else "—"
    except json.JSONDecodeError:
        return "—"


def _outcome_label(result: str | None, player_color: str | None) -> str:
    outcome = game_outcome(result, player_color)
    labels = {"win": "Victoire", "loss": "Défaite", "draw": "Nulle", "unknown": "?"}
    return labels.get(outcome, "?")


def _result_cell(
    result: str | None,
    player_color: str | None,
    *,
    final_score_lead: float | None = None,
) -> str:
    badge = _outcome_badge(result, player_color)
    res = escape(result or "").strip()
    label = _outcome_label(result, player_color)
    score_text, _ = player_result_score(result, player_color, final_score_lead)
    score_html = ""
    if score_text and score_text.startswith("~"):
        score_html = (
            f' <span class="result-score" title="Score final KataGo (approx.)">'
            f'{escape(score_text)}</span>'
        )
    if res:
        return f'{badge} <span class="result-text" title="{label}">{res}</span>{score_html}'
    return f'{badge} <span class="result-text muted">{label}</span>{score_html}'


def _outcome_badge(result: str | None, player_color: str | None) -> str:
    outcome = game_outcome(result, player_color)
    if outcome == "win":
        return '<span class="outcome outcome-win" title="Victoire">V</span>'
    if outcome == "loss":
        return '<span class="outcome outcome-loss" title="Défaite">D</span>'
    if outcome == "draw":
        return '<span class="outcome outcome-draw" title="Nulle">=</span>'
    return '<span class="outcome outcome-unknown" title="Résultat inconnu">?</span>'


def _game_links(games: list[Any]) -> str:
    if not games:
        return "—"
    links = []
    for g in games:
        gid = g["id"]
        opp = escape(g.get("opponent") or "?")
        links.append(
            f'<a href="#" class="game-link" data-game-id="{gid}">#{gid} vs {opp}</a>'
        )
    return " ".join(links)


def _patch_stone_count(patch_ascii: str | None) -> int:
    if not patch_ascii:
        return 0
    return sum(1 for c in patch_ascii if c in ("●", "○", "o", "x", "b", "w"))


def _stone_length_label(n: int) -> str:
    if n <= 5:
        return "1–5 pierres"
    if n <= 10:
        return "6–10 pierres"
    if n <= 15:
        return "11–15 pierres"
    if n <= 25:
        return "16–25 pierres"
    if n <= 35:
        return "26–35 pierres"
    if n <= 45:
        return "36–45 pierres"
    if n <= 55:
        return "46–55 pierres"
    if n <= 70:
        return "56–70 pierres"
    if n <= 90:
        return "71–90 pierres"
    return "91+ pierres"


EXPLORER_BUCKET_ORDER = [
    "1–5 pierres", "6–10 pierres", "11–15 pierres",
    "16–25 pierres", "26–35 pierres", "36–45 pierres",
    "46–55 pierres", "56–70 pierres", "71–90 pierres", "91+ pierres",
]

EXPLORER_BUCKET_FILES: dict[str, str] = {
    "1–5 pierres": "patterns-stones-1-5.html",
    "6–10 pierres": "patterns-stones-6-10.html",
    "11–15 pierres": "patterns-stones-11-15.html",
    "16–25 pierres": "patterns-stones-16-25.html",
    "26–35 pierres": "patterns-stones-26-35.html",
    "36–45 pierres": "patterns-stones-36-45.html",
    "46–55 pierres": "patterns-stones-46-55.html",
    "56–70 pierres": "patterns-stones-56-70.html",
    "71–90 pierres": "patterns-stones-71-90.html",
    "91+ pierres": "patterns-stones-91plus.html",
}

RECURRENTS_BUCKET_FILES: dict[str, str] = {
    "1–5 pierres": "patterns-kind-recurrents-1-5.html",
    "6–10 pierres": "patterns-kind-recurrents-6-10.html",
    "11–15 pierres": "patterns-kind-recurrents-11-15.html",
    "16–25 pierres": "patterns-kind-recurrents-16-25.html",
    "26–35 pierres": "patterns-kind-recurrents-26-35.html",
    "36–45 pierres": "patterns-kind-recurrents-36-45.html",
    "46–55 pierres": "patterns-kind-recurrents-46-55.html",
    "56–70 pierres": "patterns-kind-recurrents-56-70.html",
    "71–90 pierres": "patterns-kind-recurrents-71-90.html",
    "91+ pierres": "patterns-kind-recurrents-91plus.html",
}


def _pct_cell(cnt: int, total: int) -> str:
    if total <= 0:
        return '<span class="muted">—</span>'
    pct = round(100 * cnt / total, 1)
    return f'<span class="pct-cell">{pct:.1f}%</span>'


def _pattern_recurrence_cell(
    move: dict[str, Any],
    game_id: int,
    recurrence_map: dict[tuple[int, int], int],
) -> str:
    cluster_id = move.get("pattern_cluster_id")
    if not cluster_id:
        return '<span class="muted">—</span>'
    total = int(move.get("cluster_move_count") or 0)
    other = recurrence_map.get((game_id, int(cluster_id)), 0)
    kind = move.get("pattern_kind") or "pattern"
    kind_badge = (
        f'<span class="badge kind-badge kind-{escape(kind)}">{escape(kind)}</span> '
        if kind != "pattern" else ""
    )
    if total <= 1 and other == 0:
        return f'{kind_badge}<span class="muted">unique</span>'
    warn = other > 0 or total > 1
    cls = "pattern-recurrence-warn" if warn else ""
    freq = f'<strong class="pattern-freq" title="Occurrences totales (sections Patterns)">×{total}</strong>'
    other_part = ""
    if other > 0:
        other_part = (
            f'<span class="pattern-warn-tag" title="Même forme dans d\'autres parties">'
            f"⚠ {other} autre(s)</span>"
        )
    return f'<span class="{cls}">{kind_badge}{freq} {other_part}</span>'.strip()


def _diagram_cell_html(
    move_id: int | None,
    db: Any | None,
    *,
    static_export: bool = False,
    extra_class: str = "",
    pattern_cluster_id: int | None = None,
) -> str:
    cls = f"diagram-cell {extra_class}".strip()
    if not move_id:
        return f'<td class="{cls}">—</td>'
    svg = ""
    if db and static_export:
        svg = svg_for_move_cached(db, int(move_id), cell=12)
    if svg:
        return (
            f'<td class="{cls}" data-move-id="{move_id}" '
            f'data-diagram-prerendered="1" title="Diagramme">'
            f'{svg}</td>'
        )
    pat_attr = (
        f' data-pattern-cluster-id="{pattern_cluster_id}"'
        if pattern_cluster_id else ""
    )
    return (
        f'<td class="{cls}" data-move-id="{move_id}"{pat_attr}>'
        f'<span class="muted">chargement…</span></td>'
    )


def _pattern_diagram_td(
    cluster_id: int,
    patch_ascii: str | None,
    *,
    static_export: bool = False,
    full_diagrams: bool = False,
    display_width: int = 220,
) -> str:
    """Diagramme de forme locale (patch) — pas le plateau entier."""
    patch = (patch_ascii or "").strip()
    if static_export and not full_diagrams:
        inner = (
            f'<pre class="patch-ascii-static">{escape(patch)}</pre>'
            if patch else '<span class="muted">—</span>'
        )
    else:
        inner = patch_ascii_to_svg(patch, display_width=display_width) if patch else ""
        if not inner:
            inner = '<span class="muted">—</span>'
    return (
        f'<td class="diagram diagram-cell diagram-pattern" '
        f'data-pattern-cluster-id="{cluster_id}" data-diagram-kind="patch" '
        f'data-diagram-prerendered="1" '
        f'title="Cliquer pour agrandir la forme">{inner}</td>'
    )


def _move_error_row(
    m: dict[str, Any],
    game: dict[str, Any],
    db: Any | None,
    recurrence_map: dict[tuple[int, int], int],
    *,
    static_export: bool = False,
) -> str:
    gid = int(game["id"])
    you_color = game.get("player_color") or m.get("color")
    point_loss, sev = effective_move_quality(m, player_color=you_color)
    m_eff = {**m, "point_loss": point_loss, "severity": sev}
    theme = THEME_LABELS.get(m["theme"] or "", m["theme"] or "—")
    phase = PHASE_LABELS.get(m["phase"] or "", m["phase"] or "—")
    summary = _move_summary_cell(m_eff, sev, phase, theme, player_color=you_color)
    katago = _format_katago_cell(m, you_color)
    human = _format_human_cell(m, you_color)
    pattern_cell = _pattern_recurrence_cell(m, gid, recurrence_map)
    diagram = _diagram_cell_html(
        m.get("id"), db, static_export=static_export,
    )
    return f"""
    <tr class="move-error-row" data-move-number="{m['move_number']}" data-point-loss="{point_loss}">
      {diagram}
      <td class="move-summary-cell">{summary}</td>
      <td class="pattern-recurrence-cell">{pattern_cell}</td>
      <td class="move-katago-cell">{katago}</td>
      <td class="move-human-cell">{human}</td>
      <td class="review-links">{_review_links(m['id'])}</td>
    </tr>"""


def _moves_table_header() -> str:
    return """
    <tr>
      <th>Diagramme</th>
      <th class="move-sort-head">Coup · perte
        <span class="move-sort-btns">
          <button type="button" class="move-sort-btn" data-sort="loss" title="Trier par perte de points">↓ perte</button>
          <button type="button" class="move-sort-btn active" data-sort="move" title="Ordre chronologique des coups"># coup</button>
        </span>
      </th>
      <th>Pattern ×</th>
      <th>KataGo</th>
      <th>Human SL</th>
      <th>Étude</th>
    </tr>"""


def _game_moves_tables(
    moves: list[dict[str, Any]],
    game: dict[str, Any],
    db: Any | None,
    recurrence_map: dict[tuple[int, int], int],
    *,
    opening_max: int = FUSEKI_MAX_MOVE,
    static_export: bool = False,
) -> str:
    opening = [m for m in moves if int(m["move_number"]) <= opening_max]
    rest = [m for m in moves if int(m["move_number"]) > opening_max]
    if not opening and not rest:
        return '<p class="muted">Aucun coup notable</p>'

    def _section(title: str, section_moves: list[dict[str, Any]]) -> str:
        if not section_moves:
            return (
                f'<h5 class="detail-subheading">{escape(title)}</h5>'
                f'<p class="hint fuseki-empty">Aucun coup notable dans cette phase.</p>'
            )
        rows = "".join(
            _move_error_row(
                m, game, db, recurrence_map, static_export=static_export,
            )
            for m in section_moves
        )
        return f"""
        <h5 class="detail-subheading">{escape(title)}</h5>
        {_table_scroll_wrap(f"""<table class="moves-table">
          {_moves_table_header()}
          {rows}
        </table>""")}
        """

    return (
        _section(f"Ouverture — coups 1 à {opening_max}", opening)
        + _section(f"Suite — coups {opening_max + 1}+", rest)
    )


def _drilldown_table(
    items: list[dict[str, Any]],
    label_fn,
    *,
    total: int | None = None,
) -> str:
    rows = ""
    for item in items:
        label = label_fn(item["label"])
        pct = item.get("pct")
        if pct is None and total:
            pct = round(100 * item["cnt"] / total, 1)
        pct_html = (
            f'<td class="num">{pct:.1f}%</td>'
            if pct is not None
            else _pct_cell(item["cnt"], total or 0)
        )
        rows += f"""
        <tr>
          <td>{escape(label)}</td>
          <td class="num">{item['cnt']}</td>
          {pct_html}
          <td>{(item['avg_loss'] or 0):.2f}</td>
          <td>{(item['max_loss'] or 0):.1f}</td>
          <td class="game-links">{_game_links(item.get('games', []))}</td>
        </tr>"""
    return rows


def _review_links(move_id: int) -> str:
    mid = int(move_id)
    return (
        f'<a class="btn-lizzie" href="/review/{mid}/open?tool=lizzie">Lizzie</a> '
        f'<a class="btn-link" href="/review/{mid}/open?tool=katrain">KaTrain</a> '
        f'<a class="btn-link" href="/review/{mid}/sgf">SGF</a>'
    )


def _quality_cells(g: dict[str, Any], prefix: str, css: str) -> str:
    if prefix == "opp":
        keys = (
            "opp_ia", "opp_tres_bon", "opp_bon", "opp_ok",
            "opp_inaccuracy", "opp_mistake", "opp_blunder", "opp_mega_blunder",
        )
        err_key = "opp_errors"
        col_cls = ""
    else:
        keys = (
            "ply_ia", "ply_tres_bon", "ply_bon", "ply_ok",
            "inaccuracy", "mistake", "blunder", "mega_blunder",
        )
        err_key = "errors"
        col_cls = " games-col-ply"
    cells = ""
    for key in keys:
        cells += f"<td class='num {css}{col_cls}'>{g.get(key, 0) or 0}</td>"
    cells += f"<td class='num {css}{col_cls}'><strong>{g.get(err_key, 0) or 0}</strong></td>"
    return cells


def static_game_parts_script(game_parts: dict[int, str]) -> str:
    """Inject game id → part filename map for static mirror cross-links."""
    data = json.dumps(
        {str(k): v for k, v in game_parts.items()},
        separators=(",", ":"),
    )
    return f"<script>window.GP_GAME_PARTS={data};</script>"


def _opp_subrow(g: dict[str, Any], gid: int) -> str:
    opp = escape(g.get("opponent") or "?")
    rank = escape(g.get("opponent_rank") or "?")
    avg = (g.get("opp_avg_loss") or 0)
    stats = _quality_cells(g, "opp", "opp")
    return f"""
        <tr class="opp-row" id="game-{gid}-opp" hidden>
          <td></td><td></td><td></td>
          <td colspan="5" class="opp-label">↳ <strong>{opp}</strong> · {rank}</td>
          {stats}
          <td class="num opp">{(avg):.1f}</td>
          <td></td>
        </tr>"""


def _format_game_timing(g: dict[str, Any]) -> tuple[str, str]:
    tc = g.get("time_control") or "—"
    speed = g.get("speed")
    speed_label = format_speed_label(speed) if speed else "—"
    cadence = tc if tc != "—" else speed_label
    if tc != "—" and speed and speed != "inconnu":
        cadence = f"{format_speed_label(speed)} · {tc}"
    duration = format_duration(
        g.get("duration_sec"),
        estimated=bool(g.get("duration_estimated")),
    )
    return cadence, duration


def _pattern_games_cell(occurrences: list[dict[str, Any]]) -> str:
    if not occurrences:
        return '<span class="muted">—</span>'
    by_game: dict[int, list[dict[str, Any]]] = {}
    for occ in occurrences:
        gid = int(occ["game_id"])
        by_game.setdefault(gid, []).append(occ)
    links: list[str] = []
    for gid in sorted(by_game):
        occs = by_game[gid]
        opp = escape(occs[0].get("opponent") or "?")
        moves = ", ".join(str(o["move_number"]) for o in occs)
        label = f"#{gid} vs {opp}"
        if len(occs) > 1:
            label += f" (×{len(occs)})"
        links.append(
            f'<a href="#" class="game-link" data-game-id="{gid}" '
            f'title="Coups {escape(moves)}">{label}</a>'
        )
    return '<span class="pattern-games">' + " ".join(links) + "</span>"


def _pattern_table_rows(
    patterns: list[Any],
    db: Any | None,
    *,
    show_region: bool = False,
    occurrences_by_cluster: dict[int, list[dict[str, Any]]] | None = None,
    static_export: bool = False,
    full_diagrams: bool = False,
) -> str:
    rows = ""
    for i, c in enumerate(patterns, 1):
        label = THEME_LABELS.get(c["dominant_theme"] or "", c["dominant_theme"] or "?")
        cluster_id = int(c["id"])
        sample_move_id = None
        region_label = ""
        try:
            samples = json.loads(c["sample_json"] or "[]")
            if samples:
                sample_move_id = samples[0].get("move_id")
            if show_region and samples:
                from .local_pattern import dominant_corner_label
                region = dominant_corner_label(samples)
                if region:
                    region_label = f'<span class="pattern-region">{escape(region)}</span>'
        except json.JSONDecodeError:
            pass
        diagram_td = _pattern_diagram_td(
            cluster_id, c["patch_ascii"],
            static_export=static_export,
            full_diagrams=full_diagrams,
        )
        lizzie = (
            _review_links(sample_move_id) if sample_move_id else "<span class='muted'>—</span>"
        )
        region_td = f"<td>{region_label or '<span class=\"muted\">—</span>'}</td>" if show_region else ""
        occs = (occurrences_by_cluster or {}).get(cluster_id, [])
        games_cell = _pattern_games_cell(occs)
        total_loss = float(c["total_point_loss"] or 0)
        move_count = int(c["move_count"] or 0)
        rows += f"""
        <tr class="pattern-row" data-total-loss="{total_loss:.2f}" data-move-count="{move_count}">
          <td>{i}</td>
          <td><strong>{move_count}</strong></td>
          <td class="num pattern-loss-cell">{total_loss:.1f} pts</td>
          <td>{escape(label)}</td>
          {region_td}
          <td class="pattern-games-cell">{games_cell}</td>
          {diagram_td}
          <td class="review-links">{lizzie}</td>
        </tr>"""
    return rows


def _pattern_section(
    title: str,
    hint: str,
    patterns: list[Any],
    db: Any | None,
    *,
    section_id: str,
    show_region: bool = False,
    empty_msg: str = "Aucun cluster pour l'instant",
    colspan: int = 7,
    occurrences_by_cluster: dict[int, list[dict[str, Any]]] | None = None,
    total_clusters: int | None = None,
) -> str:
    region_head = "<th>Zone</th>" if show_region else ""
    if show_region:
        colspan = 8
    rows = _pattern_table_rows(
        patterns, db, show_region=show_region,
        occurrences_by_cluster=occurrences_by_cluster,
    )
    shown = len(patterns)
    total = total_clusters if total_clusters is not None else shown
    badge = f"{shown}/{total}" if total > shown else str(shown)
    return f"""
    <details class="pattern-panel" id="pattern-{escape(section_id)}">
      <summary class="pattern-panel-head">
        <span class="pattern-panel-chevron" aria-hidden="true">▸</span>
        <span class="pattern-panel-title">{escape(title)}</span>
        <span class="pattern-panel-count" title="Affichées / total détectées">{badge}</span>
      </summary>
      <div class="pattern-panel-body">
        <p class="hint">{hint}</p>
        <div class="pattern-toolbar">
          <label class="pattern-filter-label">Perte cum. min.
            <input type="number" class="pattern-filter-loss" min="0" step="0.5" placeholder="0">
          </label>
          <span class="pattern-sort-btns">
            <button type="button" class="pattern-sort-btn active" data-sort="loss" title="Trier par perte cumulée">↓ perte</button>
            <button type="button" class="pattern-sort-btn" data-sort="count" title="Trier par fréquence">↓ ×</button>
          </span>
        </div>
        {_table_scroll_wrap(f"""<table class="pattern-table">
          <tr>
            <th>#</th><th>×</th>
            <th class="pattern-sort-head">Perte cum.</th>
            <th>Thème</th>{region_head}
            <th>Parties</th><th>Diagramme</th><th>Étude</th>
          </tr>
          {rows or f'<tr class="pattern-empty"><td colspan="{colspan}">{escape(empty_msg)}</td></tr>'}
        </table>""")}
      </div>
    </details>"""


def parse_list_limit(raw: int | str | None, *, default: int = 25) -> int | None:
    if raw is None:
        return default
    if isinstance(raw, str):
        if raw.lower() in ("all", "tout", "0"):
            return None
        try:
            raw = int(raw)
        except ValueError:
            return default
    if int(raw) <= 0:
        return None
    return int(raw)


def _dashboard_font_links() -> str:
    return (
        '  <link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '  <link href="https://fonts.googleapis.com/css2?family='
        'Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family='
        'Source+Sans+3:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap" '
        'rel="stylesheet">'
    )


def _table_scroll_wrap(table_html: str, *, extra_class: str = "") -> str:
    cls = f"table-scroll {extra_class}".strip()
    return (
        f'<div class="{cls}">{table_html}'
        f'<p class="table-scroll-hint">← glisser pour voir les colonnes →</p></div>'
    )


def _diagram_lightbox_html() -> str:
    return """
  <div id="diagram-lightbox" class="diagram-lightbox" hidden>
    <div class="diagram-lightbox-backdrop" id="diagram-lightbox-backdrop"></div>
    <div class="diagram-lightbox-panel" role="dialog" aria-modal="true" aria-label="Diagramme agrandi">
      <button type="button" class="diagram-lightbox-close" id="diagram-lightbox-close" aria-label="Fermer">×</button>
      <p class="diagram-lightbox-hint">Échap ou clic à l'extérieur pour fermer</p>
      <div id="diagram-lightbox-content"><span class="muted">Chargement…</span></div>
      <div id="diagram-lightbox-actions" class="diagram-lightbox-actions" hidden></div>
    </div>
  </div>"""


def _diagram_lightbox_script() -> str:
    return """
    const diagramLightbox = document.getElementById('diagram-lightbox');
    const diagramLightboxContent = document.getElementById('diagram-lightbox-content');
    const diagramLightboxActions = document.getElementById('diagram-lightbox-actions');
    const isStaticMirror = !!document.getElementById('static-mirror-marker');
    function closeDiagramLightbox() {
      if (!diagramLightbox) return;
      diagramLightbox.hidden = true;
      document.body.style.overflow = '';
      if (diagramLightboxContent) diagramLightboxContent.innerHTML = '';
      if (diagramLightboxActions) {
        diagramLightboxActions.hidden = true;
        diagramLightboxActions.innerHTML = '';
      }
    }
    function cloneDiagramSvg(cell) {
      const svg = cell && cell.querySelector('svg');
      if (!svg) return null;
      const clone = svg.cloneNode(true);
      const isPatch = cell.dataset.diagramKind === 'patch'
        || cell.classList.contains('diagram-pattern')
        || svg.classList.contains('go-board-patch');
      clone.removeAttribute('width');
      clone.removeAttribute('height');
      if (isPatch) {
        clone.style.width = 'min(88vw, 560px)';
        clone.style.height = 'auto';
      } else {
        clone.style.width = '100%';
        clone.style.maxHeight = '85vh';
      }
      return clone;
    }
    function showInLightbox(node) {
      if (!diagramLightbox || !diagramLightboxContent || !node) return false;
      diagramLightbox.hidden = false;
      document.body.style.overflow = 'hidden';
      diagramLightboxContent.innerHTML = '';
      diagramLightboxContent.appendChild(node);
      if (diagramLightboxActions) {
        diagramLightboxActions.hidden = true;
        diagramLightboxActions.innerHTML = '';
      }
      return true;
    }
    function isSvgMarkup(text) {
      return text && text.indexOf('<svg') >= 0 && text.indexOf('<html') < 0 && text.indexOf('<!DOCTYPE') < 0;
    }
    function fetchDiagramIntoLightbox(url, cell) {
      if (!diagramLightbox || !diagramLightboxContent) return;
      diagramLightbox.hidden = false;
      document.body.style.overflow = 'hidden';
      diagramLightboxContent.innerHTML = '<span class="muted">Chargement…</span>';
      fetch(url)
        .then(r => r.ok ? r.text() : Promise.reject())
        .then(svg => {
          if (!isSvgMarkup(svg)) throw new Error('not svg');
          diagramLightboxContent.innerHTML = svg;
        })
        .catch(() => {
          const fallback = cell && (cloneDiagramSvg(cell) || cell.querySelector('pre.patch-ascii-static')?.cloneNode(true));
          if (fallback) showInLightbox(fallback);
          else diagramLightboxContent.innerHTML = '<span class="muted">Diagramme indisponible</span>';
        });
    }
    function activateDiagramCell(cell) {
      if (!cell) return;
      const svg = cloneDiagramSvg(cell);
      if (svg) {
        showInLightbox(svg);
        return;
      }
      const pre = cell.querySelector('pre.patch-ascii-static');
      if (pre) {
        const clone = pre.cloneNode(true);
        clone.classList.add('patch-ascii-lightbox');
        showInLightbox(clone);
        return;
      }
      if (isStaticMirror) return;
      const moveId = cell.dataset.moveId;
      if (moveId && cell.dataset.diagramKind !== 'patch') {
        fetchDiagramIntoLightbox('/api/moves/' + moveId + '/diagram?cell=17', cell);
      }
    }
    document.getElementById('diagram-lightbox-close')?.addEventListener('click', closeDiagramLightbox);
    document.getElementById('diagram-lightbox-backdrop')?.addEventListener('click', closeDiagramLightbox);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeDiagramLightbox();
    });
    document.addEventListener('click', e => {
      if (e.target.closest('a, button')) return;
      const cell = e.target.closest('td.diagram-cell, td.diagram');
      if (!cell) return;
      e.preventDefault();
      e.stopPropagation();
      activateDiagramCell(cell);
    });
"""

def _dashboard_css() -> str:
    return """
    :root {
      --bg: #e8dfd0;
      --bg-top: #f0e9de;
      --bg-bottom: #ddd2c0;
      --card: #faf6f0;
      --card-raised: #fffdf9;
      --text: #2a2118;
      --muted: #6b5e4f;
      --border: #d4c4a8;
      --border-strong: #b8a88c;
      --accent: #6b4423;
      --accent-hover: #523318;
      --accent-soft: #9a7b4f;
      --accent-bg: #ede0c8;
      --stone-black: #1a1a1a;
      --stone-white: #f8f4ec;
      --shadow: 0 2px 8px rgba(42, 33, 24, 0.07);
      --shadow-hover: 0 8px 24px rgba(42, 33, 24, 0.12);
      --radius: 12px;
      --radius-sm: 8px;
      --font-display: "Fraunces", Georgia, "Times New Roman", serif;
      --font-body: "Source Sans 3", system-ui, -apple-system, sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { max-width: 100%; overflow-x: hidden; }
    body {
      font-family: var(--font-body);
      margin: 0;
      background: var(--bg);
      background-image: linear-gradient(180deg, var(--bg-top) 0%, var(--bg) 35%, var(--bg-bottom) 100%);
      background-attachment: fixed;
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    h1, h2, h3, h4, h5 {
      font-family: var(--font-display);
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.01em;
    }
    header {
      padding: 1.25rem 2rem;
      background: var(--card-raised);
      border-bottom: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    h1 { margin: 0; font-size: 1.5rem; }
    h2 { margin: 2rem 0 0.75rem; font-size: 1.2rem; }
    h3 { margin: 1.25rem 0 0.5rem; font-size: 1.05rem; }
    .sub { color: var(--muted); font-size: 0.9rem; margin-top: 0.3rem; }
    main { padding: 1.5rem 2rem; max-width: 1500px; }
    a.nav {
      color: var(--accent);
      margin-right: 1rem;
      text-decoration: none;
      font-weight: 600;
    }
    a.nav:hover { color: var(--accent-hover); text-decoration: underline; }
    .sub-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.65rem;
      align-items: center;
    }
    .sub-nav a.nav { margin-right: 0; font-size: 0.88rem; }
    .muted { color: var(--muted); }
    .hint {
      color: var(--muted);
      font-size: 0.88rem;
      margin-bottom: 0.75rem;
      max-width: 900px;
      line-height: 1.55;
    }
    .cards {
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
      margin-bottom: 1.5rem;
    }
    .cards.stats-row { margin-bottom: 0.5rem; }
    .card {
      background: var(--card-raised);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1rem 1.25rem;
      min-width: 120px;
      box-shadow: var(--shadow);
      transition: box-shadow 0.2s ease, transform 0.15s ease;
    }
    .card:hover { box-shadow: var(--shadow-hover); }
    .card .num {
      font-family: var(--font-display);
      font-size: 1.75rem;
      font-weight: 800;
      color: var(--accent-soft);
      line-height: 1.15;
      font-variant-numeric: tabular-nums;
    }
    .card .sub-pct { font-size: 0.78rem; color: var(--muted); margin-top: 0.2rem; }
    .card.card-tip { position: relative; cursor: help; }
    .card.card-tip::after {
      content: "i";
      position: absolute;
      top: 0.45rem;
      right: 0.55rem;
      width: 1.1rem;
      height: 1.1rem;
      border-radius: 50%;
      background: var(--accent-bg);
      color: var(--accent);
      font-size: 0.7rem;
      font-weight: 700;
      font-style: italic;
      font-family: var(--font-display);
      line-height: 1.1rem;
      text-align: center;
    }
    .player-ref-row { background: var(--accent-bg); font-weight: 600; }
    .player-ref-row td { border-top: 2px solid var(--accent); }
    .hub-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
      gap: 1rem;
      margin: 0.5rem 0 1.25rem;
    }
    .hub-card {
      display: block;
      text-decoration: none;
      color: inherit;
      background: var(--card-raised);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1rem 1.05rem;
      box-shadow: var(--shadow);
      transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;
    }
    .hub-card:hover {
      border-color: var(--accent-soft);
      box-shadow: var(--shadow-hover);
      transform: translateY(-3px);
    }
    .hub-card-soon { opacity: 0.5; cursor: default; pointer-events: none; }
    .hub-card-num {
      font-family: var(--font-display);
      font-size: 1.6rem;
      font-weight: 800;
      color: var(--accent-soft);
      font-variant-numeric: tabular-nums;
    }
    .hub-card-title { font-weight: 700; margin-top: 0.3rem; font-size: 0.95rem; }
    .hub-card-sub { font-size: 0.78rem; color: var(--muted); margin-top: 0.2rem; line-height: 1.35; }
    .hub-card-alert { border-color: #c9a227; background: linear-gradient(145deg, #fffdf6 0%, #faf3e0 100%); }
    .hub-card-alert:hover { border-color: #a67c00; }
    .hub-card-alert .hub-card-num { color: #9a7b00; }
    .hub-view-toggle {
      display: flex;
      gap: 0.5rem;
      margin: 0.75rem 0 1rem;
      flex-wrap: wrap;
    }
    .hub-view-btn {
      padding: 0.45rem 1rem;
      min-height: 2.75rem;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      background: var(--card);
      color: var(--text);
      font-family: var(--font-body);
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }
    .hub-view-btn:hover { background: var(--accent-bg); border-color: var(--accent-soft); }
    .hub-view-btn.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--stone-white);
    }
    .hub-panel[hidden] { display: none !important; }
    .games-filter-banner {
      background: var(--accent-bg);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius);
      padding: 0.65rem 1rem;
      margin-bottom: 0.85rem;
      font-size: 0.88rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 1rem;
    }
    .games-filter-banner[hidden] { display: none !important; }
    .games-filter-banner a { color: var(--accent); font-weight: 600; }
    .filter-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.65rem 1rem;
      align-items: center;
      background: var(--card-raised);
      border-radius: var(--radius);
      padding: 0.85rem 1.1rem;
      margin-bottom: 1rem;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    .filter-bar label {
      font-size: 0.82rem;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 0.35rem;
      font-weight: 500;
    }
    .filter-bar select,
    .pattern-filter-loss,
    .explorer-toolbar input,
    .explorer-toolbar select {
      padding: 0.3rem 0.5rem;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-strong);
      background: var(--card);
      color: var(--text);
      font-family: var(--font-body);
      font-size: 0.82rem;
    }
    .filter-bar .btn-reset {
      padding: 0.35rem 0.75rem;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-strong);
      background: var(--accent-bg);
      color: var(--accent);
      cursor: pointer;
      font-size: 0.8rem;
      font-weight: 600;
      font-family: var(--font-body);
      transition: background 0.15s;
    }
    .filter-bar .btn-reset:hover { background: #e2d4b8; }
    .limit-toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 0.75rem;
      margin: 0.5rem 0 1rem;
      font-size: 0.85rem;
    }
    .limit-btn {
      padding: 0.25rem 0.6rem;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      background: var(--card);
      color: var(--text);
      text-decoration: none;
      font-size: 0.8rem;
      font-weight: 500;
      transition: background 0.15s, border-color 0.15s;
    }
    .limit-btn:hover { background: var(--accent-bg); border-color: var(--accent-soft); }
    .limit-btn.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--stone-white);
      font-weight: 600;
    }
    .limit-meta { color: var(--muted); margin-left: auto; }
    .table-scroll {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      margin-bottom: 1rem;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      background: var(--card-raised);
    }
    .table-scroll > table {
      margin-bottom: 0;
      border: none;
      box-shadow: none;
      border-radius: 0;
    }
    .table-scroll-hint {
      display: none;
      text-align: center;
      font-size: 0.72rem;
      color: var(--muted);
      padding: 0.35rem 0.5rem;
      background: var(--accent-bg);
      border-top: 1px solid var(--border);
    }
    .games-table { min-width: 980px; }
    .pattern-table, .explorer-table { min-width: 760px; }
    .moves-table { min-width: 680px; }
    .alert-table { min-width: 640px; }
    table {
      border-collapse: collapse;
      width: 100%;
      background: var(--card-raised);
      border-radius: var(--radius);
      overflow: hidden;
      margin-bottom: 1rem;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    th, td {
      border: 1px solid var(--border);
      padding: 0.5rem 0.65rem;
      text-align: left;
      font-size: 0.85rem;
      vertical-align: middle;
    }
    th {
      background: var(--accent-bg);
      color: var(--text);
      font-weight: 700;
      font-size: 0.8rem;
      letter-spacing: 0.02em;
    }
    tbody tr:nth-child(even) { background: rgba(42, 33, 24, 0.025); }
    tbody tr:hover { background: rgba(154, 123, 79, 0.08); }
    .num { text-align: center; font-variant-numeric: tabular-nums; }
    th.ply-head { background: #e2e8d4; color: #3d4a2e; }
    tr.opp-row td { background: #f5ebe8 !important; border-color: rgba(155, 44, 44, 0.15); }
    tr.opp-row .opp-label { color: #7a3030; font-size: 0.85rem; padding-left: 1.5rem; }
    tr.game-row-click { cursor: pointer; }
    tr.game-row-click.expanded { outline: 2px solid rgba(107, 68, 35, 0.35); outline-offset: -2px; }
    td.opp { color: #8b4040; }
    td.ply { color: #2d5a3a; }
    tr.game-row-filtered { display: none; }
    tr.game-row-win { background: rgba(45, 106, 79, 0.1) !important; }
    tr.game-row-win td { border-color: rgba(45, 106, 79, 0.2); }
    tr.game-row-loss { background: rgba(155, 44, 44, 0.08) !important; }
    tr.game-row-loss td { border-color: rgba(155, 44, 44, 0.18); }
    tr.game-row-draw { background: rgba(107, 94, 79, 0.1) !important; }
    tr.game-row-win:hover { background: rgba(45, 106, 79, 0.16) !important; }
    tr.game-row-loss:hover { background: rgba(155, 44, 44, 0.14) !important; }
    .result-cell { white-space: nowrap; font-size: 0.8rem; }
    .result-text { margin-left: 0.25rem; }
    .result-score {
      display: inline-block;
      margin-left: 0.35rem;
      padding: 0.1rem 0.35rem;
      border-radius: 4px;
      background: var(--accent-bg);
      color: var(--accent);
      font-weight: 600;
      font-size: 0.78rem;
    }
    .games-sort-btn {
      background: none;
      border: none;
      padding: 0.35rem 0.2rem;
      min-height: 2.75rem;
      font: inherit;
      font-weight: 700;
      color: inherit;
      cursor: pointer;
      text-decoration: underline dotted transparent;
    }
    .games-sort-btn:hover { color: var(--accent); text-decoration-color: var(--accent-soft); }
    .games-sort-btn.active { color: var(--accent); text-decoration-color: var(--accent); }
    .badge {
      color: white;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.76rem;
      font-weight: 600;
    }
    .badge-verdict { font-size: 0.68rem; font-weight: 600; white-space: nowrap; }
    .btn-toggle {
      background: var(--accent);
      color: var(--stone-white);
      border: none;
      padding: 0.45rem 0.85rem;
      min-height: 2.75rem;
      min-width: 2.75rem;
      border-radius: var(--radius-sm);
      cursor: pointer;
      font-weight: 600;
      font-size: 0.8rem;
      font-family: var(--font-body);
      box-shadow: 0 1px 3px rgba(42, 33, 24, 0.15);
      transition: background 0.15s, transform 0.1s;
    }
    .btn-toggle:hover { background: var(--accent-hover); }
    .btn-lizzie {
      background: #4a3728;
      color: var(--stone-white);
      padding: 0.25rem 0.55rem;
      border-radius: var(--radius-sm);
      text-decoration: none;
      font-size: 0.76rem;
      font-weight: 600;
    }
    .btn-lizzie:hover { background: #3a2a1e; }
    .btn-link {
      color: var(--accent);
      text-decoration: none;
      font-size: 0.78rem;
      margin-left: 0.25rem;
      font-weight: 600;
    }
    .btn-link:hover { text-decoration: underline; color: var(--accent-hover); }
    .detail-row td { background: #f3ece2 !important; padding: 0.85rem; }
    .detail-heading { margin: 0.5rem 0 0.35rem; font-size: 0.95rem; color: var(--accent); }
    .detail-hint { margin-top: 0; margin-bottom: 0.5rem; }
    .detail-subheading { margin: 0.75rem 0 0.35rem; font-size: 0.88rem; color: var(--accent-soft); }
    .moves-table { font-size: 0.8rem; table-layout: fixed; width: 100%; }
    .moves-table th { background: #e8dfd0; font-size: 0.76rem; }
    .move-error-row td { vertical-align: top; padding: 0.55rem 0.65rem; }
    .move-sort-head { white-space: nowrap; }
    .move-sort-btns { display: inline-flex; gap: 0.2rem; margin-left: 0.35rem; vertical-align: middle; }
    .move-sort-btn {
      font-size: 0.62rem;
      padding: 0.12rem 0.4rem;
      border-radius: 6px;
      border: 1px solid var(--border-strong);
      background: var(--card);
      color: var(--muted);
      cursor: pointer;
      font-weight: 600;
      font-family: var(--font-body);
    }
    .move-sort-btn:hover { border-color: var(--accent-soft); color: var(--text); }
    .move-sort-btn.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--stone-white);
    }
    .move-katago-cell { width: 14rem; }
    .move-human-cell { width: auto; }
    .move-summary-head {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.5rem;
      align-items: baseline;
      margin-bottom: 0.25rem;
    }
    .move-num { font-weight: 800; font-size: 0.95rem; color: var(--text); font-family: var(--font-display); }
    .move-phase { font-size: 0.72rem; color: var(--muted); }
    .move-theme { font-size: 0.7rem; color: var(--accent-soft); }
    .move-loss {
      font-size: 1.35rem;
      font-weight: 800;
      color: #9b2c2c;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
      font-family: var(--font-display);
    }
    .move-loss-position { color: #5c4a8a; }
    .move-loss-strong { font-size: 1.05rem; font-weight: 800; color: #6b5080; line-height: 1.15; }
    .move-loss-ok { font-size: 1rem; color: var(--muted); font-weight: 500; }
    .move-loss-u { font-size: 0.65rem; font-weight: 600; color: var(--muted); margin-left: 0.15rem; }
    .move-badges { margin-top: 0.35rem; display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .katago-block { display: flex; flex-direction: column; gap: 0.45rem; }
    .cmp-h {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0.35rem 0.5rem;
      font-weight: 600;
      line-height: 1.35;
    }
    .cmp-played { color: #8b4040; }
    .cmp-best { color: #3d5a6e; }
    .cmp-arrow { color: var(--muted); font-size: 0.85rem; }
    .cmp-delta {
      font-size: 0.72rem;
      font-weight: 700;
      color: #b45309;
      background: #fef3c7;
      padding: 0.1rem 0.4rem;
      border-radius: 4px;
      font-variant-numeric: tabular-nums;
    }
    .sc {
      font-size: 0.72rem;
      font-weight: 500;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      margin-left: 0.15rem;
    }
    .katago-top { display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .chip {
      display: inline-block;
      padding: 0.12rem 0.45rem;
      border-radius: 6px;
      background: var(--card);
      border: 1px solid var(--border);
      font-size: 0.7rem;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .chip-best {
      background: #e8dfd0;
      border-color: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }
    .human-block { display: flex; flex-direction: column; gap: 0.35rem; font-size: 0.74rem; line-height: 1.4; }
    .human-line .lbl {
      color: var(--muted);
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      display: block;
      margin-bottom: 0.1rem;
      font-weight: 600;
    }
    .human-chips { display: flex; flex-wrap: wrap; gap: 0.2rem; }
    .human-chip {
      display: inline-flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 0.12rem 0.32rem;
      border-radius: 6px;
      background: var(--card);
      border: 1px solid var(--border);
      min-width: 2rem;
    }
    .human-chip-rank { font-size: 0.6rem; color: var(--muted); line-height: 1.1; }
    .human-chip-pct { font-size: 0.72rem; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text); }
    .human-rank-grp { display: flex; flex-wrap: wrap; gap: 0.15rem; }
    .human-rank-mini {
      display: inline-block;
      padding: 0.05rem 0.3rem;
      border-radius: 4px;
      background: var(--accent-bg);
      font-size: 0.62rem;
      font-weight: 700;
      color: var(--text);
    }
    .human-zero { color: var(--muted); font-style: italic; }
    .human-consensus { color: #2d6a4f; }
    .human-rank-line { display: block; margin-bottom: 0.35rem; }
    .human-rank-line:last-child { margin-bottom: 0; }
    .human-rank-move {
      display: block;
      margin-top: 0.12rem;
      padding-left: 0.05rem;
      font-size: 0.74rem;
      line-height: 1.35;
    }
    .human-rank-branch { color: var(--muted); margin-right: 0.22rem; font-size: 0.9rem; }
    .priors, .alts { font-size: 0.75rem; max-width: 220px; }
    .diagram svg, .diagram-cell svg, .go-board-full, .go-board-patch {
      display: block;
      border-radius: var(--radius-sm);
      width: 300px;
      height: auto;
      max-width: 100%;
      box-shadow: 0 4px 16px rgba(42, 33, 24, 0.18);
      border: 1px solid var(--border-strong);
    }
    .patch-ascii-static {
      font-family: ui-monospace, monospace;
      font-size: 0.62rem;
      line-height: 1.05;
      margin: 0;
      white-space: pre;
      color: var(--text);
    }
    .diagram-cell { width: 310px; max-width: 310px; cursor: zoom-in; }
    .diagram-cell[title] { position: relative; }
    .diagram-cell svg, td.diagram svg {
      max-width: 100%;
      height: auto;
      display: block;
    }
    .diagram-lightbox[hidden] { display: none !important; }
    .diagram-lightbox {
      position: fixed;
      inset: 0;
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }
    .diagram-lightbox-backdrop { position: absolute; inset: 0; background: rgba(42, 33, 24, 0.78); }
    .diagram-lightbox-panel {
      position: relative;
      z-index: 1;
      max-width: min(96vw, 920px);
      max-height: 96vh;
      overflow: auto;
      background: var(--card-raised);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius);
      padding: 0.85rem 0.85rem 1.1rem;
      box-shadow: 0 24px 48px rgba(42, 33, 24, 0.35);
    }
    .diagram-lightbox-close {
      position: absolute;
      top: 0.4rem;
      right: 0.5rem;
      z-index: 2;
      width: 2rem;
      height: 2rem;
      border: none;
      border-radius: var(--radius-sm);
      background: var(--accent-bg);
      color: var(--text);
      font-size: 1.35rem;
      line-height: 1;
      cursor: pointer;
      font-weight: 700;
    }
    .diagram-lightbox-close:hover { background: #e2d4b8; }
    .diagram-lightbox-hint {
      text-align: center;
      font-size: 0.75rem;
      color: var(--muted);
      margin-bottom: 0.5rem;
    }
    #diagram-lightbox-content svg {
      display: block;
      width: min(88vw, 880px);
      height: auto;
      max-width: 100%;
      margin: 0 auto;
      border-radius: var(--radius-sm);
    }
    #diagram-lightbox-content pre.patch-ascii-lightbox {
      font-size: 0.55rem;
      line-height: 1.12;
      margin: 0 auto;
      overflow: auto;
      max-height: 85vh;
      white-space: pre;
    }
    .timing { font-size: 0.78rem; max-width: 140px; }
    .outcome {
      display: inline-block;
      width: 1.6rem;
      height: 1.6rem;
      line-height: 1.6rem;
      text-align: center;
      border-radius: 6px;
      font-weight: 800;
      font-size: 0.85rem;
    }
    .outcome-win { background: #2d6a4f; color: white; }
    .outcome-loss { background: #9b2c2c; color: white; }
    .outcome-draw { background: #6b5e4f; color: white; }
    .outcome-unknown { background: #b8a88c; color: var(--text); }
    .game-link {
      color: var(--accent);
      margin-right: 0.5rem;
      white-space: nowrap;
      font-weight: 600;
      text-decoration: none;
    }
    .game-link:hover { text-decoration: underline; color: var(--accent-hover); }
    .game-links { font-size: 0.78rem; }
    .highlight { outline: 2px solid var(--accent-soft); outline-offset: -2px; }
    .pattern-region { font-size: 0.78rem; color: var(--accent-soft); }
    .pattern-recurrence-cell { font-size: 0.78rem; white-space: nowrap; }
    .pattern-recurrence-warn { color: #9a7b00; }
    .pattern-warn-tag {
      display: inline-block;
      margin-left: 0.2rem;
      font-size: 0.72rem;
      background: #faf3e0;
      color: #7a5c00;
      padding: 0.1rem 0.4rem;
      border-radius: 6px;
      border: 1px solid #e8d48a;
    }
    .pattern-freq { color: var(--accent); font-weight: 700; }
    .pct-cell { color: var(--accent); font-weight: 600; }
    .pattern-panels { display: flex; flex-direction: column; gap: 0.65rem; margin-top: 0.75rem; }
    .pattern-panel {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--card);
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .pattern-panel-head {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 1rem;
      cursor: pointer;
      list-style: none;
      font-weight: 700;
      font-size: 1rem;
      user-select: none;
      font-family: var(--font-display);
      transition: background 0.15s;
    }
    .pattern-panel-head::-webkit-details-marker { display: none; }
    .pattern-panel-head:hover { background: var(--accent-bg); }
    .pattern-panel[open] .pattern-panel-head {
      border-bottom: 1px solid var(--border);
      background: var(--accent-bg);
    }
    .pattern-panel-chevron {
      display: inline-block;
      width: 1rem;
      color: var(--muted);
      transition: transform 0.15s ease;
    }
    .pattern-panel[open] .pattern-panel-chevron { transform: rotate(90deg); }
    .pattern-panel-title { flex: 1; }
    .pattern-panel-count {
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--muted);
      background: var(--card-raised);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.12rem 0.6rem;
      min-width: 1.5rem;
      text-align: center;
      font-family: var(--font-body);
    }
    .pattern-panel-body { padding: 0.75rem 1rem 1rem; }
    .pattern-panel-body .hint { margin-top: 0; }
    .pattern-toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem 1rem;
      margin-bottom: 0.55rem;
      font-size: 0.82rem;
    }
    .pattern-filter-label { color: var(--muted); display: flex; align-items: center; gap: 0.4rem; font-weight: 500; }
    .pattern-sort-btns { display: flex; gap: 0.35rem; }
    .pattern-sort-btn {
      padding: 0.22rem 0.6rem;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      background: var(--card);
      color: var(--text);
      cursor: pointer;
      font-size: 0.78rem;
      font-family: var(--font-body);
      font-weight: 500;
      transition: background 0.15s;
    }
    .pattern-sort-btn:hover { background: var(--accent-bg); }
    .pattern-sort-btn.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--stone-white);
      font-weight: 600;
    }
    .pattern-games {
      display: flex;
      flex-wrap: wrap;
      gap: 0.25rem 0.45rem;
      font-size: 0.76rem;
      max-width: 280px;
    }
    .pattern-games-cell { vertical-align: top; }
    .pattern-row.pattern-hidden { display: none; }
    .diagram-pattern.diagram-cell { width: 310px; max-width: 310px; }
    .diagram-lightbox-actions { text-align: center; margin-top: 0.65rem; font-size: 0.85rem; }
    .diagram-lightbox-actions button {
      margin: 0 0.35rem;
      padding: 0.35rem 0.8rem;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-strong);
      background: var(--accent-bg);
      color: var(--text);
      cursor: pointer;
      font-family: var(--font-body);
      font-weight: 600;
    }
    .diagram-lightbox-actions button:hover { background: #e2d4b8; }
    .suspect-high { color: #9b2c2c; font-weight: 700; }
    .suspect-mid { color: #9a7b00; font-weight: 600; }
    .suspect-badge {
      display: inline-block;
      font-size: 0.72rem;
      padding: 0.12rem 0.45rem;
      border-radius: 6px;
      background: #faf3e0;
      color: #7a5c00;
      border: 1px solid #e8d48a;
      font-weight: 600;
    }
    .alert-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    .explorer-toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.65rem 1rem;
      align-items: center;
      background: var(--card-raised);
      border-radius: var(--radius);
      padding: 0.85rem 1.1rem;
      margin-bottom: 1rem;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    .explorer-toolbar label { font-size: 0.82rem; color: var(--muted); font-weight: 500; }
    .explorer-row.explorer-hidden { display: none; }
    .kind-badge { background: var(--accent-bg); color: var(--accent); font-size: 0.72rem; border: 1px solid var(--border); }
    code {
      background: var(--accent-bg);
      padding: 0.1rem 0.35rem;
      border-radius: 4px;
      font-size: 0.85em;
      color: var(--accent);
    }
    @media (max-width: 768px) {
      header { padding: 1rem 1.1rem; }
      main { padding: 1rem 1.1rem; max-width: 100%; }
      h1 { font-size: 1.2rem; line-height: 1.3; }
      h2 { font-size: 1.05rem; margin-top: 1.5rem; }
      .sub { font-size: 0.82rem; }
      .hub-grid { grid-template-columns: repeat(2, 1fr); gap: 0.65rem; }
      .hub-card { padding: 0.85rem 0.75rem; min-height: 5.5rem; }
      .hub-card-num { font-size: 1.35rem; }
      .hub-card-title { font-size: 0.88rem; }
      .hub-card-sub { font-size: 0.72rem; }
      .card { min-width: calc(50% - 0.5rem); flex: 1 1 calc(50% - 0.5rem); padding: 0.75rem 0.85rem; }
      .card .num { font-size: 1.35rem; }
      .cards { gap: 0.65rem; }
      table { font-size: 0.78rem; }
      th, td { padding: 0.38rem 0.4rem; }
      .filter-bar, .explorer-toolbar, .pattern-toolbar, .limit-toolbar {
        flex-direction: column;
        align-items: stretch;
        gap: 0.55rem;
      }
      .filter-bar label, .explorer-toolbar label, .pattern-filter-label {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
        width: 100%;
      }
      .filter-bar select, .filter-bar input, .explorer-toolbar input, .explorer-toolbar select {
        width: 100%;
        min-height: 2.75rem;
        font-size: 1rem;
      }
      .limit-meta { margin-left: 0; margin-top: 0.25rem; }
      .limit-btn, .pattern-sort-btn, .move-sort-btn, .games-sort-btn, .btn-toggle {
        min-height: 2.75rem;
        padding: 0.45rem 0.75rem;
      }
      .pattern-sort-btns, .move-sort-btns { flex-wrap: wrap; }
      .table-scroll-hint { display: block; }
      .diagram-cell svg { max-width: 64px; max-height: 64px; }
      .result-cell { white-space: normal; }
      .timing { max-width: 7rem; white-space: normal; font-size: 0.72rem; }
      tr.opp-row .opp-label { padding-left: 0.75rem; font-size: 0.78rem; }
      .detail-row td { padding: 0.65rem 0.5rem; }
      .diagram-lightbox-panel {
        width: calc(100vw - 1.25rem);
        max-width: none;
        max-height: calc(100vh - 1.5rem);
        padding: 0.75rem 0.5rem 1rem;
        margin: 0.75rem;
      }
      .diagram-lightbox-close {
        min-width: 2.75rem;
        min-height: 2.75rem;
        font-size: 1.35rem;
      }
      #diagram-lightbox-content svg {
        max-width: 100%;
        height: auto;
      }
      .diagram-lightbox-hint { font-size: 0.72rem; }
      .move-summary-cell { font-size: 0.78rem; }
      .review-links a { display: inline-block; padding: 0.35rem 0.5rem; min-height: 2.75rem; }
      .games-table { min-width: 0; }
      .games-table .games-col-ply,
      .games-table th.ply-head,
      .games-table .games-col-secondary { display: none; }
      .games-table th[rowspan="2"]:nth-child(6),
      .games-table td:nth-child(6) { display: none; }
      .games-table th[rowspan="2"]:nth-child(7),
      .games-table td:nth-child(7) { display: none; }
      .games-table th[rowspan="2"]:nth-child(8),
      .games-table td:nth-child(8) { display: none; }
      .games-table th[rowspan="2"]:nth-child(5),
      .games-table td:nth-child(5) { display: none; }
      .table-scroll { -webkit-overflow-scrolling: touch; border-radius: var(--radius-sm); }
      .hub-view-toggle { width: 100%; }
      .hub-view-btn { flex: 1; text-align: center; }
    }
    @media (max-width: 480px) {
      .hub-grid { grid-template-columns: 1fr 1fr; }
      .card { min-width: 100%; flex: 1 1 100%; }
      h1 { font-size: 1.1rem; }
      .sub-nav a.nav { font-size: 0.8rem; }
    }
    """


def _limit_picker_html(
    base_path: str, current: int | None, total: int, *, label: str = "Afficher",
) -> str:
    links = []
    for val, text in [(25, "25"), (50, "50"), (100, "100"), (None, "Tout")]:
        q = "all" if val is None else str(val)
        active = "active" if current == val else ""
        links.append(
            f'<a class="limit-btn {active}" href="{escape(base_path)}?limit={q}">'
            f"{text}</a>"
        )
    shown = total if current is None else min(current or 0, total)
    return (
        f'<div class="limit-toolbar"><span class="limit-label">{escape(label)} :</span>'
        f'{"".join(links)}<span class="limit-meta">{shown} / {total}</span></div>'
    )


def _header_nav() -> str:
    return (
        '<p class="sub sub-nav">'
        '<a class="nav" href="/">Dashboard</a>'
        ' · <a class="nav" href="/games">Parties</a>'
        ' · <a class="nav" href="/patterns/kind/recurrents">Patterns</a>'
        ' · <a class="nav" href="/patterns">Explorateur</a>'
        ' · <a class="nav" href="/alerte">Alerte</a>'
        '</p>'
    )


def _hub_nav_cards(
    games_total: int,
    pattern_totals: dict[str, int],
    *,
    suspect_count: int = 0,
) -> str:
    cards = [
        ("Parties", str(games_total), "Liste avec coups détaillés", "/games", True, ""),
        ("Patterns récurrents", str(pattern_totals.get("pattern", 0)), "Blunders locaux", "/patterns/kind/recurrents", True, ""),
        ("Joseki", str(pattern_totals.get("joseki", 0)), "Séquences répétées", "/patterns/kind/joseki", True, ""),
        ("Fuseki", str(pattern_totals.get("fuseki", 0)), "Ouvertures", "/patterns/kind/fuseki", True, ""),
        ("Explorateur", "∞", "Par taille de forme", "/patterns", True, ""),
        (
            "Alerte aux trous du cul",
            str(suspect_count),
            "Adversaires suspects · coups IA",
            "/alerte",
            True,
            " hub-card-alert",
        ),
        ("Stats avancées", "—", "Bientôt", "#", False, ""),
        ("Exercices", "—", "Bientôt", "#", False, ""),
    ]
    out = '<div class="hub-grid">'
    for title, num, sub, href, on, extra_cls in cards:
        tag = "a" if on else "div"
        href_attr = f' href="{escape(href)}"' if on else ""
        cls = "hub-card" if on else "hub-card hub-card-soon"
        cls += extra_cls
        out += (
            f'<{tag} class="{cls}"{href_attr}>'
            f'<div class="hub-card-num">{escape(num)}</div>'
            f'<div class="hub-card-title">{escape(title)}</div>'
            f'<div class="hub-card-sub">{escape(sub)}</div></{tag}>'
        )
    return out + "</div>"


def _build_games_table_rows(
    games: list[Any],
    db: Any | None,
    pattern_recurrence: dict[tuple[int, int], int],
    *,
    static_export: bool = False,
) -> str:
    games_rows = ""
    for g in games:
        gid = g["id"]
        date = f"{g['year']}-{g['month']:02d}" if g.get("year") and g.get("month") else "?"
        ply_cells = _quality_cells(g, "ply", "ply")
        moves_html = g.get("_moves", [])
        moves_block = ""
        if moves_html:
            moves_tables = _game_moves_tables(
                moves_html, g, db, pattern_recurrence,
                static_export=static_export,
            )
            moves_block = f"""
            <tr class="detail-row" id="game-{gid}-detail" hidden>
              <td colspan="19">
                <h4 class="detail-heading">Coups problématiques</h4>
                <p class="hint detail-hint">Ouverture (1–{FUSEKI_MAX_MOVE}) puis suite. <strong>Pattern ×</strong> = fréquence globale ; ⚠ = même forme ailleurs.</p>
                {moves_tables}
              </td>
            </tr>"""
        outcome_val = game_outcome(g.get("result"), g.get("player_color"))
        outcome = _outcome_badge(g.get("result"), g.get("player_color"))
        row_cls = f"game-row game-row-{outcome_val}" if outcome_val in ("win", "loss", "draw") else "game-row"
        cadence, duration = _format_game_timing(g)
        _, score_sort = player_result_score(
            g.get("result"), g.get("player_color"), g.get("final_score_lead"),
        )
        result_cell = _result_cell(
            g.get("result"), g.get("player_color"),
            final_score_lead=g.get("final_score_lead"),
        )
        opp_block = _opp_subrow(g, gid)
        ply_color = escape(g.get("player_color") or "")
        year = int(g.get("year") or 0)
        month = int(g.get("month") or 0)
        sort_date = year * 100 + month
        duration_sec = int(g.get("duration_sec") or 0)
        avg_loss = float(g.get("avg_loss") or 0)
        errors = int(g.get("errors") or 0)
        games_rows += f"""
        <tr class="{row_cls} game-row-click" id="game-row-{gid}" data-game="{gid}"
            data-opponent="{escape(g.get('opponent') or '')}"
            data-year="{g.get('year') or ''}"
            data-outcome="{outcome_val}"
            data-color="{ply_color}"
            data-sort-id="{gid}"
            data-sort-date="{sort_date}"
            data-sort-opponent="{(g.get('opponent') or '').lower()}"
            data-sort-rank="{(g.get('player_rank') or '').lower()}"
            data-sort-result="{(score_sort if score_sort is not None else '')}"
            data-sort-duration="{duration_sec}"
            data-sort-avg-loss="{avg_loss:.2f}"
            data-sort-errors="{errors}"
            data-sort-ply-ia="{int(g.get('ply_ia') or 0)}"
            data-sort-ply-tres-bon="{int(g.get('ply_tres_bon') or 0)}"
            data-sort-ply-bon="{int(g.get('ply_bon') or 0)}"
            data-sort-ply-ok="{int(g.get('ply_ok') or 0)}"
            data-sort-ply-inaccuracy="{int(g.get('inaccuracy') or 0)}"
            data-sort-ply-mistake="{int(g.get('mistake') or 0)}"
            data-sort-ply-blunder="{int(g.get('blunder') or 0)}"
            data-sort-ply-mega="{int(g.get('mega_blunder') or 0)}"
            title="Cliquer pour voir les stats adversaire">
          <td>#{gid}</td>
          <td class="num">{outcome}</td>
          <td>{escape(date)}</td>
          <td>vs {escape(g['opponent'] or '?')}</td>
          <td class="games-col-secondary">{escape(g['player_rank'] or '?')}</td>
          <td class="result-cell">{result_cell}</td>
          <td class="timing games-col-secondary" title="{escape(g.get('time_control') or '')}">{escape(cadence)}</td>
          <td class="num games-col-secondary" title="Durée estimée si préfixe ~">{escape(duration)}</td>
          {ply_cells}
          <td>{(g.get('avg_loss') or 0):.1f}</td>
          <td>{(
            f'<button type="button" class="btn-toggle" data-target="game-{gid}-detail">Coups</button>'
            if moves_html else '<span class="muted">—</span>'
          )}</td>
        </tr>
        {opp_block}
        {moves_block}"""
    return games_rows


PATTERN_KIND_PAGES: dict[str, dict[str, Any]] = {
    "recurrents": {
        "kind": "pattern",
        "title": "Patterns récurrents",
        "hint": "Formes locales identiques autour de vos blunders.",
    },
    "joseki": {
        "kind": "joseki",
        "title": "Joseki récurrents",
        "hint": "Séquences locales répétées au même endroit sur le plateau.",
        "show_region": True,
    },
    "fuseki": {
        "kind": "fuseki",
        "title": "Fuseki",
        "hint": f"Formes d'ouverture récurrentes (coups 1–{FUSEKI_MAX_MOVE}).",
    },
}


def render_dashboard(
    stats: dict[str, Any],
    games: list[Any],
    patterns: list[Any] | dict[str, list[Any]],
    username: str,
    drilldown: dict[str, list[dict[str, Any]]] | None = None,
    db: Any | None = None,
    timing_stats: list[dict[str, Any]] | None = None,
    pattern_totals: dict[str, int] | None = None,
    filter_meta: dict[str, list[Any]] | None = None,
    pattern_recurrence: dict[tuple[int, int], int] | None = None,
    static_export: bool = False,
    *,
    games_analyzed: int | None = None,
    suspect_count: int = 0,
) -> str:
    drilldown = drilldown or {}
    filter_meta = filter_meta or {"opponents": [], "years": []}
    pattern_totals = pattern_totals or {}
    drill_total = int(drilldown.get("total_errors") or 0)
    games_analyzed = games_analyzed if games_analyzed is not None else len(games)
    hub_html = _hub_nav_cards(games_analyzed, pattern_totals, suspect_count=suspect_count)
    timing_rows = ""
    for t in timing_stats or []:
        label = format_speed_label(t.get("label"))
        avg_d = format_duration(int(t["avg_duration"]) if t.get("avg_duration") else None)
        mpm = f"{t['avg_mpm']:.1f}" if t.get("avg_mpm") else "—"
        timing_rows += f"""
        <tr>
          <td>{escape(label)}</td>
          <td class="num">{t.get('cnt', 0)}</td>
          <td class="num">{escape(avg_d)}</td>
          <td class="num">{mpm}</td>
        </tr>"""

    theme_rows = _drilldown_table(
        drilldown.get("themes", []),
        lambda x: THEME_LABELS.get(x, x),
        total=drill_total,
    )
    phase_rows = _drilldown_table(
        drilldown.get("phases", []),
        lambda x: PHASE_LABELS.get(x, x),
        total=drill_total,
    )
    sev_rows = _drilldown_table(
        drilldown.get("severities", []),
        lambda x: SEVERITY_LABELS.get(x, x),
        total=drill_total,
    )

    opponent_opts = "".join(
        f'<option value="{escape(o)}">{escape(o)}</option>'
        for o in filter_meta.get("opponents", [])
    )
    year_opts = "".join(
        f'<option value="{y}">{y}</option>'
        for y in filter_meta.get("years", [])
    )

    pct_analyzed = stats.get("pct_analyzed", 0)
    pct_opening = stats.get("pct_opening_done", 0)
    pct_err_moves = stats.get("pct_error_moves", 0)
    pct_open_err = stats.get("pct_opening_error_moves", 0)
    pct_fuseki_games = stats.get("pct_games_fuseki_errors", 0)
    pct_blunder_games = stats.get("pct_games_with_blunder", 0)
    pct_joseki_err = stats.get("pct_joseki_error_moves", 0)
    pct_joseki_games = stats.get("pct_games_joseki_errors", 0)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Go Progress — {escape(username)}</title>
{_dashboard_font_links()}
  <style>
{_dashboard_css()}
  </style>
</head>
<body>
  <header>
    <h1>Go Progress — {escape(username)}</h1>
    {_header_nav()}
  </header>
  <main>
    <div class="cards">
      <div class="card"><div class="num">{stats['games_total']}</div>collectées</div>
      <div class="card">
        <div class="num">{stats['games_analyzed']}</div>analysées
        <div class="sub-pct">{pct_analyzed:.1f}% du corpus</div>
      </div>
      <div class="card">
        <div class="num">{stats.get('opening_done', 0)}</div>ouverture
        <div class="sub-pct">{pct_opening:.1f}% des parties</div>
      </div>
      <div class="card"><div class="num">{stats.get('pending_analysis', 0)}</div>en attente</div>
      <div class="card">
        <div class="num">{stats.get('player_blunders', stats['blunders_total'])}</div>blunders
        <div class="sub-pct">{pct_blunder_games:.1f}% des parties</div>
      </div>
      <div class="card">
        <div class="num">{pct_err_moves:.1f}%</div>coups erronés
        <div class="sub-pct">{stats.get('player_errors', 0)} / {stats.get('player_moves', 0)} coups</div>
      </div>
      <div class="card">
        <div class="num">{pct_open_err:.1f}%</div>erreurs fuseki
        <div class="sub-pct">{pct_fuseki_games:.1f}% parties · {stats.get('fuseki_clusters', 0)} formes</div>
      </div>
      <div class="card card-tip" title="Coups erronés sur des formes classées joseki (séquences locales récurrentes détectées par l'analyse).">
        <div class="num">{pct_joseki_err:.1f}%</div>erreurs joseki
        <div class="sub-pct">{pct_joseki_games:.1f}% parties · {stats.get('joseki_clusters', 0)} formes</div>
      </div>
    </div>

    <h2>Explorer</h2>
    <p class="hint">Cartes vers les listes détaillées — choix 25 / 50 / 100 / tout sur chaque page.</p>
    {hub_html}

    <h2 id="stats">Statistiques</h2>
    <div class="filter-bar" id="stats-filter-bar">
      <label>Adversaire <select id="filter-opponent"><option value="">Tous</option>{opponent_opts}</select></label>
      <label>Année <select id="filter-year"><option value="">Toutes</option>{year_opts}</select></label>
      <label>Résultat <select id="filter-outcome">
        <option value="">Tous</option>
        <option value="win">Victoire</option>
        <option value="loss">Défaite</option>
        <option value="draw">Nulle</option>
      </select></label>
      <label>Couleur <select id="filter-color">
        <option value="">Toutes</option>
        <option value="B">Noir</option>
        <option value="W">Blanc</option>
      </select></label>
      <label>Erreurs <select id="filter-scope">
        <option value="all">Toutes erreurs</option>
        <option value="opening">Ouverture (coups 1–{FUSEKI_MAX_MOVE})</option>
        <option value="fuseki">Fuseki (formes)</option>
        <option value="joseki">Joseki (formes)</option>
      </select></label>
      <button type="button" class="btn-reset" id="filter-reset">Réinitialiser</button>
    </div>
    <p class="hint">Filtres croisés. Parties détaillées sur <a href="/games">/games</a>. Liens partie → ouvre la fiche sur la page Parties.</p>
    <h3>Par cadence de jeu</h3>
    <p class="hint">Durée moyenne avec préfixe <strong>~</strong> quand elle est estimée (KGS ne stocke pas toujours le temps de réflexion).</p>
    {_table_scroll_wrap(f"""<table>
      <tr><th>Cadence</th><th>Parties</th><th>Durée moy.</th><th>Coups/min moy.</th></tr>
      {timing_rows or '<tr><td colspan="4">Lancez enrich pour remplir les cadences</td></tr>'}
    </table>""")}
    <h3>Par thème</h3>
    {_table_scroll_wrap(f"""<table id="drilldown-themes">
      <tr><th>Thème</th><th>Nb</th><th>% erreurs</th><th>Perte moy.</th><th>Pire</th><th>Parties</th></tr>
      {theme_rows or '<tr><td colspan="6">—</td></tr>'}
    </table>""")}
    <h3>Par phase</h3>
    {_table_scroll_wrap(f"""<table id="drilldown-phases">
      <tr><th>Phase</th><th>Nb</th><th>% erreurs</th><th>Perte moy.</th><th>Pire</th><th>Parties</th></tr>
      {phase_rows or '<tr><td colspan="6">—</td></tr>'}
    </table>""")}
    <h3>Par sévérité</h3>
    {_table_scroll_wrap(f"""<table id="drilldown-severities">
      <tr><th>Niveau</th><th>Nb</th><th>% erreurs</th><th>Perte moy.</th><th>Pire</th><th>Parties</th></tr>
      {sev_rows or '<tr><td colspan="6">—</td></tr>'}
    </table>""")}
  </main>
{_diagram_lightbox_html()}
  <script>
    function openGame(gameId) {{
      const gid = String(gameId);
      const row = document.getElementById('game-row-' + gid);
      const detail = document.getElementById('game-' + gid + '-detail');
      if (row && detail) {{
        detail.hidden = false;
        const btn = row.querySelector('.btn-toggle');
        if (btn) btn.textContent = 'Masquer';
        if (typeof loadDiagrams === 'function') loadDiagrams(detail);
        row.classList.add('highlight');
        row.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        return;
      }}
      if (document.getElementById('static-mirror-marker')) {{
        const parts = window.GP_GAME_PARTS || {{}};
        const partFile = parts[gid];
        if (partFile) {{
          window.location.href = partFile + '#game-row-' + gid;
          return;
        }}
        window.location.href = 'games.html#game-row-' + gid;
        return;
      }}
      window.location.href = '/games?limit=50#game-row-' + gid;
    }}
    document.querySelectorAll('.btn-toggle').forEach(btn => {{
      if (btn.dataset.gpBound) return;
      btn.dataset.gpBound = '1';
      btn.addEventListener('click', e => {{
        e.stopPropagation();
        const row = document.getElementById(btn.dataset.target);
        if (!row) return;
        const open = row.hidden;
        row.hidden = !open;
        btn.textContent = open ? 'Masquer' : 'Coups';
        if (open) loadDiagrams(row);
      }});
    }});
    function initMoveTableSort(table) {{
      if (!table || table.dataset.sortInit) return;
      table.dataset.sortInit = '1';
      sortMoveRows(table, 'move');
    }}
    document.querySelectorAll('.game-row-click').forEach(row => {{
      row.addEventListener('click', () => {{
        const gid = row.dataset.game;
        const opp = document.getElementById('game-' + gid + '-opp');
        if (!opp) return;
        const open = opp.hidden;
        opp.hidden = !open;
        row.classList.toggle('expanded', open);
      }});
    }});
    function sortMoveRows(table, mode) {{
      const rows = Array.from(table.querySelectorAll('tr.move-error-row'));
      rows.sort((a, b) => {{
        if (mode === 'move') {{
          return (+a.dataset.moveNumber || 0) - (+b.dataset.moveNumber || 0);
        }}
        const lossDiff = (+b.dataset.pointLoss || 0) - (+a.dataset.pointLoss || 0);
        if (lossDiff !== 0) return lossDiff;
        return (+a.dataset.moveNumber || 0) - (+b.dataset.moveNumber || 0);
      }});
      rows.forEach(r => table.appendChild(r));
      table.querySelectorAll('.move-sort-btn').forEach(btn => {{
        btn.classList.toggle('active', btn.dataset.sort === mode);
      }});
    }}
    document.addEventListener('click', e => {{
      const btn = e.target.closest('.move-sort-btn');
      if (!btn) return;
      e.stopPropagation();
      const table = btn.closest('table.moves-table');
      if (table) sortMoveRows(table, btn.dataset.sort);
    }});
    function loadDiagrams(detailRow) {{
      detailRow.querySelectorAll('td[data-move-id]:not([data-pattern-cluster-id])').forEach(td => {{
        if (td.dataset.loaded) return;
        if (td.dataset.diagramPrerendered === '1') {{
          td.dataset.loaded = '1';
          return;
        }}
        const id = td.dataset.moveId;
        fetch('/api/moves/' + id + '/diagram')
          .then(r => r.ok ? r.text() : '')
          .then(svg => {{
            if (svg) {{
              td.innerHTML = svg;
              td.title = 'Cliquer pour agrandir le diagramme';
            }} else {{
              td.innerHTML = '<span class="muted">Diagramme indisponible</span>';
            }}
            td.dataset.loaded = '1';
          }})
          .catch(() => {{
            td.innerHTML = '<span class="muted">Erreur de chargement</span>';
            td.dataset.loaded = '1';
          }});
      }});
      detailRow.querySelectorAll('table.moves-table').forEach(initMoveTableSort);
    }}
    document.querySelectorAll('.pattern-panel').forEach(panel => {{
      panel.addEventListener('toggle', () => {{
        if (!panel.open) return;
        panel.querySelectorAll('table.pattern-table').forEach(initPatternTable);
      }});
    }});
    function applyPatternFilter(table) {{
      const wrap = table.closest('main') || table.parentElement;
      const minInput = wrap?.querySelector('.pattern-filter-loss');
      const minLoss = minInput && minInput.value !== '' ? parseFloat(minInput.value) : 0;
      table.querySelectorAll('tr.pattern-row').forEach(row => {{
        const loss = parseFloat(row.dataset.totalLoss || '0');
        row.classList.toggle('pattern-hidden', loss < minLoss);
      }});
    }}
    function sortPatternRows(table, mode) {{
      const tbodyRows = Array.from(table.querySelectorAll('tr.pattern-row'));
      tbodyRows.sort((a, b) => {{
        if (mode === 'count') {{
          return parseInt(b.dataset.moveCount || '0', 10) - parseInt(a.dataset.moveCount || '0', 10);
        }}
        return parseFloat(b.dataset.totalLoss || '0') - parseFloat(a.dataset.totalLoss || '0');
      }});
      const header = table.querySelector('tr');
      tbodyRows.forEach((row, i) => {{
        row.querySelector('td').textContent = String(i + 1);
        table.appendChild(row);
      }});
      applyPatternFilter(table);
    }}
    function initPatternTable(table) {{
      if (!table || table.dataset.patternInit) return;
      table.dataset.patternInit = '1';
      const wrap = table.closest('main') || table.parentElement;
      sortPatternRows(table, 'loss');
      wrap?.querySelectorAll('.pattern-sort-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
          wrap.querySelectorAll('.pattern-sort-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          sortPatternRows(table, btn.dataset.sort || 'loss');
        }});
      }});
      wrap?.querySelector('.pattern-filter-loss')?.addEventListener('input', () => {{
        applyPatternFilter(table);
      }});
    }}
{_diagram_lightbox_script()}
    document.querySelectorAll('.game-link').forEach(a => {{
      a.addEventListener('click', e => {{
        e.preventDefault();
        openGame(a.dataset.gameId);
      }});
    }});
    const LABELS = {{
      opening: 'Ouverture', middlegame: 'Milieu de partie', endgame: 'Fin de partie',
      mega_blunder: 'Méga-blunder', blunder: 'Blunder', mistake: 'Erreur',
      inaccuracy: 'Imprécision', ok: 'OK'
    }};
    function themeLabel(k) {{ return LABELS[k] || k; }}
    function applyGameFilters() {{
      const opp = document.getElementById('filter-opponent')?.value || '';
      const year = document.getElementById('filter-year')?.value || '';
      const outcome = document.getElementById('filter-outcome')?.value || '';
      const color = document.getElementById('filter-color')?.value || '';
      document.querySelectorAll('tr.game-row-click').forEach(row => {{
        const match = (!opp || row.dataset.opponent === opp)
          && (!year || row.dataset.year === String(year))
          && (!outcome || row.dataset.outcome === outcome)
          && (!color || row.dataset.color === color);
        row.classList.toggle('game-row-filtered', !match);
        const gid = row.dataset.game;
        if (!match) {{
          const oppRow = document.getElementById('game-' + gid + '-opp');
          const detail = document.getElementById('game-' + gid + '-detail');
          if (oppRow) oppRow.hidden = true;
          if (detail) detail.hidden = true;
          row.classList.remove('expanded');
        }}
      }});
    }}
    function drilldownRows(items, labelFn) {{
      if (!items || !items.length) return '<tr><td colspan="6" class="muted">—</td></tr>';
      return items.map(item => {{
        const games = (item.games || []).map(g =>
          `<a href="#" class="game-link" data-game-id="${{g.id}}">#${{g.id}} vs ${{
            (g.opponent || '?').replace(/</g, '&lt;')
          }}</a>`
        ).join(' ');
        const pct = item.pct != null ? item.pct.toFixed(1) + '%' : '—';
        return `<tr>
          <td>${{labelFn(item.label)}}</td>
          <td class="num">${{item.cnt}}</td>
          <td class="num">${{pct}}</td>
          <td>${{(item.avg_loss || 0).toFixed(2)}}</td>
          <td>${{(item.max_loss || 0).toFixed(1)}}</td>
          <td class="game-links">${{games || '—'}}</td>
        </tr>`;
      }}).join('');
    }}
    async function refreshDrilldown() {{
      const params = new URLSearchParams();
      const opp = document.getElementById('filter-opponent')?.value;
      const year = document.getElementById('filter-year')?.value;
      const outcome = document.getElementById('filter-outcome')?.value;
      const color = document.getElementById('filter-color')?.value;
      const scope = document.getElementById('filter-scope')?.value;
      if (opp) params.set('opponent', opp);
      if (year) params.set('year', year);
      if (outcome) params.set('outcome', outcome);
      if (color) params.set('color', color);
      if (scope && scope !== 'all') params.set('scope', scope);
      try {{
        const res = await fetch('/api/drilldown?' + params.toString());
        if (!res.ok) return;
        const data = await res.json();
        const themes = document.getElementById('drilldown-themes');
        const phases = document.getElementById('drilldown-phases');
        const sev = document.getElementById('drilldown-severities');
        if (themes) {{
          const hdr = themes.querySelector('tr');
          themes.innerHTML = '';
          if (hdr) themes.appendChild(hdr);
          themes.insertAdjacentHTML('beforeend', drilldownRows(data.themes, k => themeLabel(k)));
        }}
        if (phases) {{
          const hdr = phases.querySelector('tr');
          phases.innerHTML = '';
          if (hdr) phases.appendChild(hdr);
          phases.insertAdjacentHTML('beforeend', drilldownRows(data.phases, k => LABELS[k] || k));
        }}
        if (sev) {{
          const hdr = sev.querySelector('tr');
          sev.innerHTML = '';
          if (hdr) sev.appendChild(hdr);
          sev.insertAdjacentHTML('beforeend', drilldownRows(data.severities, k => LABELS[k] || k));
        }}
        document.querySelectorAll('#drilldown-themes .game-link, #drilldown-phases .game-link, #drilldown-severities .game-link').forEach(a => {{
          a.addEventListener('click', e => {{
            e.preventDefault();
            openGame(a.dataset.gameId);
          }});
        }});
      }} catch (_) {{ /* ignore */ }}
    }}
    function onStatsFilterChange() {{
      refreshDrilldown();
    }}
    ['filter-opponent','filter-year','filter-outcome','filter-color','filter-scope'].forEach(id => {{
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', onStatsFilterChange);
    }});
    document.getElementById('filter-reset')?.addEventListener('click', () => {{
      ['filter-opponent','filter-year','filter-outcome','filter-color','filter-scope'].forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.value = el.id === 'filter-scope' ? 'all' : '';
      }});
      onStatsFilterChange();
    }});
  </script>
</body>
</html>"""


def _subpage_shell(title: str, username: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — {escape(username)}</title>
{_dashboard_font_links()}
  <style>
{_dashboard_css()}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    {_header_nav()}
  </header>
  <main>{body}</main>
</body>
</html>"""


def _alerte_opponent_row(
    o: dict[str, Any],
    username: str,
    *,
    highlight: bool = False,
    limit: int | None = None,
) -> str:
    score = float(o.get("suspicion_score") or 0)
    cls = "suspect-high" if score >= 70 else ("suspect-mid" if score >= 50 else "")
    label = escape(o.get("suspicion_label") or "")
    if highlight:
        name = escape(username)
        rank = "—"
        row_cls = "player-ref-row"
        name_cell = f"<strong>{name}</strong> (vous)"
    else:
        name = escape(o.get("opponent") or "?")
        rank = escape(o.get("opponent_rank") or "?")
        row_cls = ""
        lim_q = limit if limit is not None else "all"
        raw_name = o.get("opponent") or ""
        qname = quote(raw_name, safe="")
        name_cell = f'<a href="/alerte?limit={lim_q}&amp;opponent={qname}">{name}</a>'
    return f"""
        <tr class="{row_cls}">
          <td>{name_cell}</td>
          <td>{rank}</td>
          <td class="num">{o.get('games', 0)}</td>
          <td class="num">{o.get('opp_moves', 0)}</td>
          <td class="num">{o.get('ia_pct', 0):.1f}%</td>
          <td class="num">{o.get('blunder_pct', 0):.1f}%</td>
          <td class="num">{(o.get('opp_avg_loss') or 0):.1f}</td>
          <td class="num {cls}">{score:.0f}</td>
          <td><span class="suspect-badge">{label}</span></td>
        </tr>"""


def render_alerte_page(
    opponents: list[dict[str, Any]],
    moves: list[dict[str, Any]],
    username: str,
    db: Any | None,
    *,
    limit: int | None,
    total: int,
    selected_opponent: str | None = None,
    player_stats: dict[str, Any] | None = None,
    static_export: bool = False,
) -> str:
    picker = _limit_picker_html("/alerte", limit, total, label="Adversaires")
    opp_filter = ""
    if selected_opponent:
        lim_q = limit if limit is not None else "all"
        opp_filter = (
            f'<p class="hint">Filtre : <strong>{escape(selected_opponent)}</strong> '
            f'· <a href="/alerte?limit={lim_q}">tous</a></p>'
        )
    opp_rows = ""
    for o in opponents:
        opp_rows += _alerte_opponent_row(o, username, limit=limit)

    move_rows = ""
    for m in moves:
        date = f"{m.get('year')}-{m.get('month', 0):02d}" if m.get("year") else "?"
        opp = escape(m.get("opponent") or "?")
        gid = int(m["game_id"])
        mid = int(m["move_id"])
        diagram = '<td class="diagram-cell"><span class="muted">—</span></td>'
        if db and not static_export:
            svg = svg_for_move_cached(db, mid, cell=10)
            if svg:
                diagram = (
                    f'<td class="diagram-cell" data-move-id="{mid}" '
                    f'data-diagram-prerendered="1">{svg}</td>'
                )
        move_rows += f"""
        <tr>
          <td class="num">#{m['move_number']}</td>
          <td><a href="/games#game-row-{gid}">#{gid}</a> vs {opp}</td>
          <td>{escape(date)}</td>
          <td><code>{escape(m.get('played_move') or '')}</code></td>
          <td class="num">{(m.get('point_loss') or 0):.1f}</td>
          {diagram}
          <td class="review-links">{_review_links(mid)}</td>
        </tr>"""

    body = f"""
    {picker}
    {opp_filter}
    <p class="hint">Heuristique locale (pas une preuve) : fort taux de coups = coup IA KataGo,
    peu de blunders, faible perte moyenne. Score ≥ 50 = suspect.
    Comparez avec vos propres stats ci-dessous.</p>

    <h2>Vos stats (référence)</h2>
    {_table_scroll_wrap(f"""<table class="alert-table">
      <tr>
        <th>Joueur</th><th>Rang</th><th>Parties</th><th>Coups</th>
        <th>% coup IA</th><th>% blunder</th><th>Perte moy.</th><th>Score</th><th>Niveau</th>
      </tr>
      {_alerte_opponent_row(player_stats or {}, username, highlight=True)}
    </table>""")}

    <h2>Adversaires les plus suspects</h2>
    {_table_scroll_wrap(f"""<table class="alert-table">
      <tr>
        <th>Adversaire</th><th>Rang</th><th>Parties</th><th>Coups</th>
        <th>% coup IA</th><th>% blunder</th><th>Perte moy.</th><th>Score</th><th>Niveau</th>
      </tr>
      {opp_rows or '<tr><td colspan="9">Pas assez de données</td></tr>'}
    </table>""")}

    <h2>Coups les plus suspects</h2>
    <p class="hint">Coups adverses = meilleur coup KataGo
    {f" — <strong>{escape(selected_opponent)}</strong>" if selected_opponent else ""}.</p>
    {_table_scroll_wrap(f"""<table class="alert-table">
      <tr>
        <th>Coup</th><th>Partie</th><th>Date</th><th>Joué</th><th>Perte</th>
        <th>Diagramme</th><th>Étude</th>
      </tr>
      {move_rows or '<tr><td colspan="7">Aucun coup</td></tr>'}
    </table>""")}
    """
    html = render_dashboard(
        {"games_total": 0, "games_analyzed": 0, "blunders_total": 0,
         "pending_analysis": 0, "opening_done": 0},
        [], {}, username, games_analyzed=0, suspect_count=0,
    )
    idx = html.find("<main>")
    end = html.find("</main>", idx)
    out = html[: idx + 6] + body + html[end:]
    return out.replace(
        f"<title>Go Progress — {escape(username)}</title>",
        f"<title>Alerte aux trous du cul — {escape(username)}</title>",
        1,
    ).replace(
        f"<h1>Go Progress — {escape(username)}</h1>",
        "<h1>Alerte aux trous du cul</h1>",
        1,
    )


def render_games_hub(
    username: str,
    chunk_links: list[tuple[str, str, int] | tuple[str, str, int, str]],
    *,
    total: int,
    opponent_links: list[tuple[str, str, int]] | None = None,
) -> str:
    opponent_links = opponent_links or []
    lot_cards = ""
    for link in chunk_links:
        label, href, count = link[0], link[1], link[2]
        subtitle = link[3] if len(link) > 3 else "Coups détaillés + diagrammes"
        lot_cards += (
            f'<a class="hub-card" href="{escape(href)}">'
            f'<div class="hub-card-num">{count}</div>'
            f'<div class="hub-card-title">{escape(label)}</div>'
            f'<div class="hub-card-sub">{escape(subtitle)}</div></a>'
        )
    opp_cards = ""
    for name, href, count in opponent_links:
        party_word = "partie" if count == 1 else "parties"
        opp_cards += (
            f'<a class="hub-card" href="{escape(href)}">'
            f'<div class="hub-card-num">{count}</div>'
            f'<div class="hub-card-title">{escape(name)}</div>'
            f'<div class="hub-card-sub">{count} {party_word}</div></a>'
        )
    toggle_html = ""
    opp_panel = ""
    hub_script = ""
    if opponent_links:
        toggle_html = """
    <div class="hub-view-toggle" role="tablist">
      <button type="button" class="hub-view-btn active" data-hub-view="lot" role="tab" aria-selected="true">Par lot</button>
      <button type="button" class="hub-view-btn" data-hub-view="opponent" role="tab" aria-selected="false">Par adversaire</button>
    </div>"""
        opp_panel = f"""
    <div id="hub-panel-opponent" class="hub-panel" hidden>
      <p class="hint">Cliquez un adversaire pour filtrer la liste des parties correspondantes.</p>
      <div class="hub-grid">{opp_cards}</div>
    </div>"""
        hub_script = """
  <script>
    (function() {
      const KEY = 'gp-games-hub-view';
      const lotPanel = document.getElementById('hub-panel-lot');
      const oppPanel = document.getElementById('hub-panel-opponent');
      const btns = document.querySelectorAll('.hub-view-btn');
      function setView(view) {
        const isLot = view !== 'opponent';
        if (lotPanel) lotPanel.hidden = !isLot;
        if (oppPanel) oppPanel.hidden = isLot;
        btns.forEach(btn => {
          const active = btn.dataset.hubView === (isLot ? 'lot' : 'opponent');
          btn.classList.toggle('active', active);
          btn.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        try { localStorage.setItem(KEY, isLot ? 'lot' : 'opponent'); } catch (e) {}
      }
      let initial = 'lot';
      try { initial = localStorage.getItem(KEY) || 'lot'; } catch (e) {}
      if (initial === 'opponent' && oppPanel) setView('opponent');
      btns.forEach(btn => {
        btn.addEventListener('click', () => setView(btn.dataset.hubView || 'lot'));
      });
    })();
  </script>"""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Parties — {escape(username)}</title>
{_dashboard_font_links()}
  <style>
{_dashboard_css()}
  </style>
</head>
<body>
  <header>
    <h1>Parties — {escape(username)}</h1>
    {_header_nav()}
    <p class="sub">{total} parties analysées · miroir mobile (fichiers &lt; 25 Mo)</p>
  </header>
  <main>
    <div class="cards">
      <div class="card"><div class="num">{total}</div>parties</div>
      <div class="card"><div class="num">{len(chunk_links)}</div>tranches</div>
      {f'<div class="card"><div class="num">{len(opponent_links)}</div>adversaires</div>' if opponent_links else ''}
    </div>
    <p class="hint">Parcourez les parties par tranche chronologique ou par adversaire.</p>
    {toggle_html}
    <div id="hub-panel-lot" class="hub-panel">
      <p class="hint">Lots numérotés du plus ancien (Lot 1) au plus récent — chaque page contient la liste complète avec coups détaillés.</p>
      <div class="hub-grid">{lot_cards}</div>
    </div>
    {opp_panel}
  </main>
{hub_script}
</body>
</html>"""


def render_games_page(
    games: list[Any],
    username: str,
    db: Any | None,
    pattern_recurrence: dict[tuple[int, int], int],
    *,
    limit: int | None,
    total: int,
    static_export: bool = False,
    chunk_label: str | None = None,
) -> str:
    """Page dédiée liste des parties (réutilise le HTML complet via render_dashboard styles)."""
    picker = "" if chunk_label else _limit_picker_html("/games", limit, total)
    chunk_nav = (
        f'<p class="hint"><a href="games.html">← Toutes les tranches</a> · {escape(chunk_label)}</p>'
        if chunk_label else ""
    )
    rows = _build_games_table_rows(
        games, db, pattern_recurrence, static_export=static_export,
    )
    body = f"""
    {picker}
    {chunk_nav}
    <div id="games-opp-filter-banner" class="games-filter-banner" hidden>
      Filtre adversaire : <strong class="filter-opp-name"></strong>
      · <a href="#" id="games-opp-filter-clear">Tout afficher</a>
    </div>
    <p class="hint">Cliquez une ligne pour l'adversaire · <strong>Coups</strong> pour le détail.
    Tri : cliquez un en-tête de colonne.</p>
    {_table_scroll_wrap(f"""<table class="games-table" id="games-table">
      <tr>
        <th rowspan="2"><button type="button" class="games-sort-btn" data-sort="id">#</button></th>
        <th rowspan="2"></th>
        <th rowspan="2"><button type="button" class="games-sort-btn" data-sort="date">Date</button></th>
        <th rowspan="2"><button type="button" class="games-sort-btn" data-sort="opponent">Adversaire</button></th>
        <th rowspan="2" class="games-col-secondary"><button type="button" class="games-sort-btn" data-sort="rank">Rang</button></th>
        <th rowspan="2"><button type="button" class="games-sort-btn" data-sort="result">Résultat</button></th>
        <th rowspan="2" class="games-col-secondary">Cadence</th>
        <th rowspan="2" class="games-col-secondary"><button type="button" class="games-sort-btn" data-sort="duration">Durée</button></th>
        <th colspan="9" class="ply-head games-col-ply">Vous</th>
        <th rowspan="2"><button type="button" class="games-sort-btn" data-sort="avg-loss">Perte moy.</button></th>
        <th rowspan="2"></th>
      </tr>
      <tr>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-ia">IA</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-tres-bon">Très bon</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-bon">Bon</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-ok">OK</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-inaccuracy">Impr.</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-mistake">Err.</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-blunder">Blund.</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="ply-mega">Méga</button></th>
        <th class="ply-head games-col-ply"><button type="button" class="games-sort-btn" data-sort="errors">Σ err.</button></th>
      </tr>
      {rows or '<tr><td colspan="19">Aucune partie analysée</td></tr>'}
    </table>""")}
    """
    html = render_dashboard(
        {"games_total": total, "games_analyzed": total, "blunders_total": 0,
         "pending_analysis": 0, "opening_done": 0, "player_blunders": 0,
         "player_errors": 0, "player_moves": 0, "fuseki_clusters": 0},
        [], {}, username, db=db, pattern_recurrence=pattern_recurrence,
        games_analyzed=total,
    )
    idx = html.find("<main>")
    end = html.find("</main>", idx)
    if idx < 0:
        return _subpage_shell("Parties", username, body)
    extra = """
    <script>
    function sortGamesTable(table, key, dir) {
      if (!table) return;
      const attr = 'data-sort-' + key;
      const rows = Array.from(table.querySelectorAll('tr.game-row-click'));
      const groups = rows.map(row => {
        const gid = row.dataset.game;
        return {
          row,
          opp: document.getElementById('game-' + gid + '-opp'),
          detail: document.getElementById('game-' + gid + '-detail'),
        };
      });
      groups.sort((a, b) => {
        const av = a.row.getAttribute(attr) ?? '';
        const bv = b.row.getAttribute(attr) ?? '';
        const an = parseFloat(av);
        const bn = parseFloat(bv);
        if (av !== '' && bv !== '' && !Number.isNaN(an) && !Number.isNaN(bn)) {
          return dir * (an - bn);
        }
        return dir * String(av).localeCompare(String(bv), 'fr', { sensitivity: 'base' });
      });
      groups.forEach(g => {
        table.appendChild(g.row);
        if (g.opp) table.appendChild(g.opp);
        if (g.detail) table.appendChild(g.detail);
      });
    }
    function initGamesTableSort(table) {
      if (!table || table.dataset.sortInit) return;
      table.dataset.sortInit = '1';
      let sortKey = 'date';
      let sortDir = -1;
      table.querySelectorAll('.games-sort-btn').forEach(btn => {
        const label = (btn.textContent || '').replace(/ [↑↓]$/, '').trim();
        btn.dataset.sortLabel = label;
        btn.addEventListener('click', e => {
          e.stopPropagation();
          const key = btn.dataset.sort || 'id';
          if (sortKey === key) sortDir *= -1;
          else { sortKey = key; sortDir = (key === 'opponent' || key === 'rank') ? 1 : -1; }
          sortGamesTable(table, sortKey, sortDir);
          table.querySelectorAll('.games-sort-btn').forEach(b => {
            const base = b.dataset.sortLabel || b.textContent || '';
            const active = b.dataset.sort === sortKey;
            b.classList.toggle('active', active);
            b.textContent = active ? base + (sortDir > 0 ? ' ↑' : ' ↓') : base;
          });
        });
      });
      sortGamesTable(table, sortKey, sortDir);
      table.querySelectorAll('.games-sort-btn').forEach(btn => {
        if (btn.dataset.sort === sortKey) {
          btn.classList.add('active');
          const base = btn.dataset.sortLabel || btn.textContent || '';
          btn.textContent = base + (sortDir > 0 ? ' ↑' : ' ↓');
        }
      });
    }
    initGamesTableSort(document.getElementById('games-table'));
    function applyOpponentUrlFilter() {
      const params = new URLSearchParams(location.search);
      const raw = params.get('opp');
      if (!raw) return;
      const decoded = decodeURIComponent(raw.replace(/\\+/g, ' '));
      const banner = document.getElementById('games-opp-filter-banner');
      if (banner) {
        banner.hidden = false;
        const nameEl = banner.querySelector('.filter-opp-name');
        if (nameEl) nameEl.textContent = decoded;
      }
      document.querySelectorAll('tr.game-row-click').forEach(row => {
        const match = row.dataset.opponent === decoded;
        row.classList.toggle('game-row-filtered', !match);
        const gid = row.dataset.game;
        if (!match) {
          const oppRow = document.getElementById('game-' + gid + '-opp');
          const detail = document.getElementById('game-' + gid + '-detail');
          if (oppRow) oppRow.hidden = true;
          if (detail) detail.hidden = true;
          row.classList.remove('expanded');
        }
      });
    }
    applyOpponentUrlFilter();
    document.getElementById('games-opp-filter-clear')?.addEventListener('click', e => {
      e.preventDefault();
      location.href = location.pathname;
    });
    document.querySelectorAll('.btn-toggle').forEach(btn => {
      if (btn.dataset.gpBound) return;
      btn.dataset.gpBound = '1';
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const row = document.getElementById(btn.dataset.target);
        if (!row) return;
        const open = row.hidden;
        row.hidden = !open;
        btn.textContent = open ? 'Masquer' : 'Coups';
        if (open && typeof loadDiagrams === 'function') loadDiagrams(row);
      });
    });
    function openGame(gameId) {
      const gid = String(gameId);
      const detail = document.getElementById('game-' + gid + '-detail');
      const row = document.getElementById('game-row-' + gid);
      const btn = row?.querySelector('.btn-toggle');
      if (detail) {
        detail.hidden = false;
        if (typeof loadDiagrams === 'function') loadDiagrams(detail);
      }
      if (btn) btn.textContent = 'Masquer';
      row?.classList.add('highlight');
      row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    if (location.hash) {
      const m = location.hash.match(/game-row-(\\d+)/);
      if (m) setTimeout(() => openGame(m[1]), 250);
    }
    </script>"""
    out = html[: idx + 6] + body + extra + html[end:]
    return out.replace(
        f"<title>Go Progress — {escape(username)}</title>",
        f"<title>{escape(chunk_label or 'Parties')} — {escape(username)}</title>",
        1,
    ).replace(
        f"<h1>Go Progress — {escape(username)}</h1>",
        f"<h1>{escape(chunk_label or 'Parties')}</h1>",
        1,
    )


def render_patterns_kind_hub(
    kind_key: str,
    username: str,
    title: str,
    hint: str,
    bucket_links: list[tuple[str, str, int]],
    *,
    total: int,
) -> str:
    cards = ""
    for label, href, count in bucket_links:
        cards += (
            f'<a class="hub-card" href="{escape(href)}">'
            f'<div class="hub-card-num">{count}</div>'
            f'<div class="hub-card-title">{escape(label)}</div>'
            f'<div class="hub-card-sub">Diagrammes complets</div></a>'
        )
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — {escape(username)}</title>
{_dashboard_font_links()}
  <style>
{_dashboard_css()}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)} — {escape(username)}</h1>
    {_header_nav()}
    <p class="sub">{total} formes · diagrammes plateau complets (miroir mobile)</p>
  </header>
  <main>
    <div class="cards">
      <div class="card"><div class="num">{total}</div>total</div>
    </div>
    <p class="hint">{escape(hint)}</p>
    <p class="hint">Choisissez une tranche par <strong>nombre de pierres</strong> dans la forme locale.</p>
    <div class="hub-grid">{cards}</div>
  </main>
</body>
</html>"""


def render_patterns_kind_page(
    kind_key: str,
    patterns: list[Any],
    username: str,
    db: Any | None,
    occurrences_by_cluster: dict[int, list[dict[str, Any]]],
    *,
    limit: int | None,
    total: int,
    static_export: bool = False,
    full_diagrams: bool = False,
    only_bucket: str | None = None,
) -> str:
    meta = PATTERN_KIND_PAGES.get(kind_key)
    if not meta:
        return _subpage_shell("Introuvable", username, "<p>Section inconnue.</p>")
    base = f"/patterns/kind/{kind_key}"
    if only_bucket:
        patterns = [
            c for c in patterns
            if _stone_length_label(_patch_stone_count(c["patch_ascii"])) == only_bucket
        ]
    picker = ""
    if not (static_export and only_bucket):
        picker = _limit_picker_html(base, limit, total)
    show_region = bool(meta.get("show_region"))
    region_head = "<th>Région</th>" if show_region else ""
    colspan = 8 if show_region else 7
    rows = _pattern_table_rows(
        patterns, db, show_region=show_region,
        occurrences_by_cluster=occurrences_by_cluster,
        static_export=static_export,
        full_diagrams=full_diagrams,
    )
    back_link = ""
    if only_bucket:
        back_link = (
            f'<p class="sub">{escape(only_bucket)} · '
            f'<a href="patterns-kind-{escape(kind_key)}.html">Toutes les tranches</a></p>'
        )
    body = f"""
    {back_link}
    {picker}
    <p class="hint">{meta['hint']}</p>
    <div class="pattern-toolbar">
      <label class="pattern-filter-label">Perte cum. min.
        <input type="number" class="pattern-filter-loss" min="0" step="0.5" placeholder="0">
      </label>
      <span class="pattern-sort-btns">
        <button type="button" class="pattern-sort-btn active" data-sort="loss">↓ perte</button>
        <button type="button" class="pattern-sort-btn" data-sort="count">↓ ×</button>
      </span>
    </div>
    {_table_scroll_wrap(f"""<table class="pattern-table">
      <tr>
        <th>#</th><th>×</th><th>Perte cum.</th><th>Thème</th>{region_head}
        <th>Parties</th><th>Diagramme</th><th>Étude</th>
      </tr>
      {rows or f'<tr><td colspan="{colspan}">Aucun pattern</td></tr>'}
    </table>""")}
    <script>document.querySelectorAll('.pattern-table').forEach(initPatternTable);</script>
    """
    html = render_dashboard(
        {"games_total": 0, "games_analyzed": 0, "blunders_total": 0,
         "pending_analysis": 0, "opening_done": 0},
        [], {}, username, db=db, pattern_totals={kind_key: total},
        games_analyzed=0,
    )
    idx = html.find("<main>")
    end = html.find("</main>", idx)
    title = str(meta["title"])
    if only_bucket:
        title = f"{title} — {only_bucket}"
    out = html[: idx + 6] + body + html[end:]
    return out.replace(
        f"<title>Go Progress — {escape(username)}</title>",
        f"<title>{escape(title)} — {escape(username)}</title>",
        1,
    ).replace(
        f"<h1>Go Progress — {escape(username)}</h1>",
        f"<h1>{escape(title)}</h1>",
        1,
    )


def _explorer_pattern_row(
    c: Any,
    db: Any | None,
    occurrences_by_cluster: dict[int, list[dict[str, Any]]],
    index: int,
    *,
    static_export: bool = False,
    full_diagrams: bool = False,
) -> str:
    label = THEME_LABELS.get(c["dominant_theme"] or "", c["dominant_theme"] or "?")
    cluster_id = int(c["id"])
    kind = (c["pattern_kind"] if "pattern_kind" in c.keys() else None) or "pattern"
    sample_move_id = None
    try:
        samples = json.loads(c["sample_json"] or "[]")
        if samples:
            sample_move_id = samples[0].get("move_id")
    except json.JSONDecodeError:
        pass
    diagram_td = _pattern_diagram_td(
        cluster_id, c["patch_ascii"],
        static_export=static_export,
        full_diagrams=full_diagrams,
        display_width=200,
    )
    occs = occurrences_by_cluster.get(cluster_id, [])
    games_cell = _pattern_games_cell(occs)
    stones = _patch_stone_count(c["patch_ascii"])
    total_loss = float(c["total_point_loss"] or 0)
    move_count = int(c["move_count"] or 0)
    games_n = len({int(o["game_id"]) for o in occs}) if occs else 0
    theme_key = c["dominant_theme"] or ""
    return f"""
    <tr class="explorer-row pattern-row" data-kind="{escape(kind)}"
        data-stones="{stones}" data-theme="{escape(theme_key)}"
        data-total-loss="{total_loss:.2f}" data-move-count="{move_count}"
        data-search="{escape(label.lower())}">
      <td>{index}</td>
      <td><span class="badge kind-badge">{escape(kind)}</span></td>
      <td><strong>{move_count}</strong></td>
      <td class="num">{games_n}</td>
      <td class="num pattern-loss-cell">{total_loss:.1f}</td>
      <td>{stones}</td>
      <td>{escape(label)}</td>
      <td class="pattern-games-cell">{games_cell}</td>
      {diagram_td}
      <td class="review-links">{
        _review_links(sample_move_id) if sample_move_id
        else '<span class="muted">—</span>'
      }</td>
    </tr>"""


def render_patterns_explorer_hub(
    username: str,
    bucket_links: list[tuple[str, str, int]],
    *,
    total: int,
    fuseki_n: int,
    joseki_n: int,
) -> str:
    cards = ""
    for label, href, count in bucket_links:
        cards += (
            f'<a class="hub-card" href="{escape(href)}">'
            f'<div class="hub-card-num">{count}</div>'
            f'<div class="hub-card-title">{escape(label)}</div>'
            f'<div class="hub-card-sub">Diagrammes complets</div></a>'
        )
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Explorateur — {escape(username)}</title>
{_dashboard_font_links()}
  <style>
{_dashboard_css()}
  </style>
</head>
<body>
  <header>
    <h1>Explorateur de patterns — {escape(username)}</h1>
    {_header_nav()}
    <p class="sub">{total} formes · diagrammes plateau complets (miroir mobile)</p>
  </header>
  <main>
    <div class="cards">
      <div class="card"><div class="num">{total}</div>total</div>
      <div class="card"><div class="num">{fuseki_n}</div>fuseki</div>
      <div class="card"><div class="num">{joseki_n}</div>joseki</div>
    </div>
    <p class="hint">Choisissez une tranche par <strong>nombre de pierres</strong> dans la forme locale.</p>
    <div class="hub-grid">{cards}</div>
  </main>
</body>
</html>"""


def render_patterns_explorer(
    patterns: list[Any],
    username: str,
    db: Any | None = None,
    *,
    occurrences_by_cluster: dict[int, list[dict[str, Any]]] | None = None,
    static_export: bool = False,
    full_diagrams: bool = False,
    only_bucket: str | None = None,
) -> str:
    occurrences_by_cluster = occurrences_by_cluster or {}
    by_length: dict[str, list[Any]] = defaultdict(list)
    kind_labels = {
        "fuseki": "Fuseki",
        "joseki": "Joseki",
        "pattern": "Patterns généraux",
    }
    for c in patterns:
        stones = _patch_stone_count(c["patch_ascii"])
        bucket = _stone_length_label(stones)
        by_length[bucket].append(c)

    length_sections = ""
    order = EXPLORER_BUCKET_ORDER if only_bucket is None else [only_bucket]
    for bucket in order:
        items = sorted(
            by_length.get(bucket, []),
            key=lambda c: (-int(c["move_count"] or 0), -(float(c["total_point_loss"] or 0))),
        )
        if not items:
            continue
        rows = ""
        for i, c in enumerate(items, 1):
            rows += _explorer_pattern_row(
                c, db, occurrences_by_cluster, i,
                static_export=static_export,
                full_diagrams=full_diagrams,
            )
        length_sections += f"""
        <details class="pattern-panel explorer-length-panel" open>
          <summary class="pattern-panel-head">
            <span class="pattern-panel-chevron" aria-hidden="true">▸</span>
            <span class="pattern-panel-title">{escape(bucket)}</span>
            <span class="pattern-panel-count">{len(items)}</span>
          </summary>
          <div class="pattern-panel-body">
            {_table_scroll_wrap(f"""<table class="pattern-table explorer-table">
              <tr>
                <th>#</th><th>Type</th><th>×</th><th>Nb parties</th>
                <th>Perte cum.</th><th>Pierres</th><th>Thème</th>
                <th>Détail</th><th>Diagramme</th><th>Étude</th>
              </tr>
              {rows}
            </table>""")}
          </div>
        </details>"""

    total = len(patterns)
    fuseki_n = sum(
        1 for c in patterns
        if (c["pattern_kind"] if "pattern_kind" in c.keys() else "pattern") == "fuseki"
    )
    joseki_n = sum(
        1 for c in patterns
        if (c["pattern_kind"] if "pattern_kind" in c.keys() else "pattern") == "joseki"
    )
    page_title = f"Patterns — {escape(username)}"
    header_sub = (
        f"Patch {PATCH_SIZE}×{PATCH_SIZE} (rayon {PATCH_RADIUS}) · {total} formes détectées "
        f"({fuseki_n} fuseki, {joseki_n} joseki)"
    )
    if only_bucket:
        page_title = f"{escape(only_bucket)} — {escape(username)}"
        header_sub = (
            f'{escape(only_bucket)} · <a href="patterns.html">Toutes les tranches</a>'
        )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
{_dashboard_font_links()}
  <style>
{_dashboard_css()}
  </style>
</head>
<body>
  <header>
    <h1>Explorateur de patterns — {escape(username)}</h1>
    {_header_nav()}
    <p class="sub">{header_sub}</p>
  </header>
  <main>
    {'' if only_bucket else f'''
    <div class="cards">
      <div class="card"><div class="num">{total}</div>total</div>
      <div class="card"><div class="num">{fuseki_n}</div>fuseki</div>
      <div class="card"><div class="num">{joseki_n}</div>joseki</div>
      <div class="card"><div class="num">{PATCH_SIZE}</div>taille patch</div>
    </div>
    <p class="hint">Groupés par <strong>nombre de pierres</strong> dans la forme locale.
    Filtrez par type, thème ou perte minimale.</p>
    <div class="explorer-toolbar">
      <label>Recherche thème
        <input type="search" id="explorer-search" placeholder="ex. invasion">
      </label>
      <label>Type
        <select id="explorer-kind">
          <option value="">Tous</option>
          <option value="fuseki">Fuseki</option>
          <option value="joseki">Joseki</option>
          <option value="pattern">Général</option>
        </select>
      </label>
      <label>Perte min.
        <input type="number" id="explorer-min-loss" min="0" step="0.5" placeholder="0">
      </label>
      <label>Fréquence min.
        <input type="number" id="explorer-min-count" min="1" step="1" placeholder="1">
      </label>
    </div>'''}
    <div class="pattern-panels">
      {length_sections or '<p class="hint">Aucun pattern — lancez run_patterns.bat</p>'}
    </div>
  </main>
{_diagram_lightbox_html()}
  <script>{_diagram_lightbox_script()}</script>
  {'' if only_bucket else '''
  <script>
    (function() {
      const searchEl = document.getElementById('explorer-search');
      if (!searchEl) return;
      function applyExplorerFilters() {
        const q = (searchEl.value || '').toLowerCase();
        const kind = document.getElementById('explorer-kind')?.value || '';
        const minLoss = parseFloat(document.getElementById('explorer-min-loss')?.value || '0') || 0;
        const minCount = parseInt(document.getElementById('explorer-min-count')?.value || '1', 10) || 1;
        document.querySelectorAll('.explorer-row').forEach(row => {
          const match = (!q || (row.dataset.search || '').includes(q))
            && (!kind || row.dataset.kind === kind)
            && parseFloat(row.dataset.totalLoss || '0') >= minLoss
            && parseInt(row.dataset.moveCount || '0', 10) >= minCount;
          row.classList.toggle('explorer-hidden', !match);
        });
        document.querySelectorAll('.explorer-length-panel').forEach(panel => {
          const visible = panel.querySelectorAll('.explorer-row:not(.explorer-hidden)').length;
          panel.style.display = visible ? '' : 'none';
        });
      }
      ['explorer-search','explorer-kind','explorer-min-loss','explorer-min-count'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', applyExplorerFilters);
        if (el) el.addEventListener('change', applyExplorerFilters);
      });
    })();
  </script>'''}
</body>
</html>"""
