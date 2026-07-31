#!/usr/bin/env python3
"""Étape 0 — Setup labo : copie DB prod, reconcile, sélection des 3 parties.

Usage (depuis la racine du repo, venv activé) :
    $env:PYTHONPATH="src"
    python scripts/lab/00_setup_lab.py
    python scripts/lab/00_setup_lab.py --force   # recopier la DB prod
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lab"))

from _common import (  # noqa: E402
    CANDIDATES_JSON,
    LAB_STATE_JSON,
    SELECTED_GAMES_JSON,
    copy_prod_db,
    load_lab_config,
    open_lab_db,
    reconcile_lab_db,
    save_json,
    select_games,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup labo deep visits")
    parser.add_argument("--force", action="store_true", help="Recopier la DB prod")
    parser.add_argument("-n", type=int, default=3, help="Nombre de parties (défaut 3)")
    args = parser.parse_args()

    print("=== Labo Deep Visits — Setup ===\n")

    copy_prod_db(force=args.force)

    cfg = load_lab_config()
    db = open_lab_db()

    print("Reconcile scores sur copie lab...")
    stats = reconcile_lab_db(db, cfg)
    print(
        f"  {stats['updated']} coups mis à jour "
        f"({stats['loss_fixed']} pertes, {stats['best_fixed']} best_move) "
        f"sur {stats['total']}"
    )

    print(f"\nSélection des {args.n} parties...")
    selected, all_candidates = select_games(db, n=args.n)

    if not selected:
        print("ERREUR : aucune partie ne passe les filtres.")
        db.close()
        sys.exit(1)

    candidates_out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "min_total_moves": 80,
            "min_player_moves": 35,
            "min_errors": 5,
            "exclude_early_dead": True,
        },
        "top_candidates": [c.to_dict() for c in all_candidates[:10]],
        "all_evaluated": len(all_candidates),
    }
    save_json(CANDIDATES_JSON, candidates_out)

    selected_out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected": [c.to_dict() for c in selected],
        "selection_reason": (
            "Top score : récence + mix sévérités + zone grise + longueur"
        ),
    }
    save_json(SELECTED_GAMES_JSON, selected_out)

    state = {
        "setup_done": True,
        "reconcile_stats": stats,
        "game_ids": [c.id for c in selected],
    }
    save_json(LAB_STATE_JSON, state)

    print(f"\n{'='*60}")
    print("CANDIDATES (top 10) :")
    print(f"{'ID':>5} {'Adv':<15} {'Date':<8} {'Coups':>5} {'Err':>4} {'Big':>4} {'Gris':>4}  Statut")
    print("-" * 60)
    for c in all_candidates[:10]:
        date = f"{c.year or '?'}-{c.month or '?'}"
        status = c.exclude_reason if c.excluded else f"score={c.score}"
        print(
            f"{c.id:5d} {c.opponent[:15]:<15} {date:<8} "
            f"{c.total_moves:5d} {c.errors:4d} {c.big_errors:4d} {c.grey_zone:4d}  {status}"
        )

    print(f"\n{'='*60}")
    print(f"RETENUES ({len(selected)}) :")
    for c in selected:
        date = f"{c.year or '?'}-{c.month or '?'}"
        notes = ", ".join(c.score_notes)
        print(
            f"  #{c.id} vs {c.opponent} ({date}) — "
            f"{c.total_moves} coups, {c.errors} err, {c.grey_zone} zone grise"
        )
        print(f"    -> {notes}")

    print(f"\nFichiers écrits :")
    print(f"  {CANDIDATES_JSON}")
    print(f"  {SELECTED_GAMES_JSON}")
    print(f"\nProchaine étape : python scripts/lab/01_oracle_ref.py")

    db.close()


if __name__ == "__main__":
    main()
