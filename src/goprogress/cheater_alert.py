"""Heuristiques « adversaire suspect » (coups type IA, peu d'erreurs)."""

from __future__ import annotations

from typing import Any


def suspicion_score(
    opp_moves: int,
    opp_ia: int,
    opp_blunders: int,
    opp_avg_loss: float | None,
) -> float:
    """Score 0–100 (heuristique, pas une preuve)."""
    if opp_moves < 15:
        return 0.0
    ia_pct = opp_ia / opp_moves
    blunder_pct = opp_blunders / opp_moves
    avg_loss = float(opp_avg_loss or 0.0)
    score = min(55.0, ia_pct * 110.0)
    score += max(0.0, 25.0 - blunder_pct * 250.0)
    score += max(0.0, 20.0 - avg_loss * 12.0)
    return round(min(100.0, score), 1)


def suspicion_label(score: float) -> str:
    if score >= 70:
        return "très suspect"
    if score >= 50:
        return "suspect"
    if score >= 35:
        return "à surveiller"
    return "normal"


def enrich_opponent_row(row: dict[str, Any]) -> dict[str, Any]:
    opp_moves = int(row.get("opp_moves") or 0)
    opp_ia = int(row.get("opp_ia") or 0)
    opp_blunders = int(row.get("opp_blunders") or 0)
    score = suspicion_score(
        opp_moves, opp_ia, opp_blunders, row.get("opp_avg_loss"),
    )
    ia_pct = (100.0 * opp_ia / opp_moves) if opp_moves else 0.0
    bl_pct = (100.0 * opp_blunders / opp_moves) if opp_moves else 0.0
    return {
        **row,
        "suspicion_score": score,
        "suspicion_label": suspicion_label(score),
        "ia_pct": round(ia_pct, 1),
        "blunder_pct": round(bl_pct, 1),
    }
