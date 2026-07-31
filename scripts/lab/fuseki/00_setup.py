#!/usr/bin/env python3
"""Labo fuseki — etape 0 : selection des parties (reutilise la DB lab)."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fuseki_lib as fl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=3)
    args = parser.parse_args()

    print("=== Labo Fuseki — Setup ===\n")
    fl.ensure_dirs()
    db = fl.open_lab_db()

    selected, candidates = fl.select_games(db, n=args.n)
    if not selected:
        print("ERREUR: aucune partie fuseki eligible.")
        sys.exit(1)

    fl.save_json(fl.CANDIDATES_JSON, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fuseki_max_move": 30,
        "top_candidates": [c.to_dict() for c in candidates[:10]],
    })
    fl.save_json(fl.SELECTED_JSON, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected": [c.to_dict() for c in selected],
    })

    print(f"{'ID':>5} {'Adv':<14} {'Date':<8} {'Fuseki':>6} {'Err':>4} {'Gris':>4}  Notes")
    print("-" * 65)
    for c in selected:
        date = f"{c.year or '?'}-{c.month or '?'}"
        print(
            f"{c.id:5d} {c.opponent[:14]:<14} {date:<8} "
            f"{c.fuseki_player_moves:6d} {c.fuseki_errors:4d} {c.fuseki_grey:4d}  "
            f"{', '.join(c.score_notes)}"
        )
    print(f"\nFichiers: {fl.SELECTED_JSON}")
    print("Suivant: python scripts/lab/fuseki/01_oracle_ref.py")
    db.close()


if __name__ == "__main__":
    main()
