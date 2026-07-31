#!/usr/bin/env python3
"""Étape 1 — Oracle 4000 visits sur les coups suspects des 3 parties.

Usage :
    $env:PYTHONPATH="src"
    python scripts/lab/01_oracle_ref.py
    python scripts/lab/01_oracle_ref.py --game-id 42   # une seule partie
    python scripts/lab/01_oracle_ref.py --skip-existing
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lab"))

from goprogress.config import resolve_path  # noqa: E402
from goprogress.katago import KataGoAnalysis  # noqa: E402
from goprogress.sgf_parse import parse_sgf, player_color  # noqa: E402

from _common import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    SELECTED_GAMES_JSON,
    analyze_positions,
    get_sgf_path,
    is_suspect,
    load_json,
    load_lab_config,
    load_selected_game_ids,
    open_lab_db,
    ref_path,
    save_json,
    suspect_moves_for_game,
)


ORACLE_VISITS = 4000
ORACLE_CONFIG = "config/katago_lab_ref.cfg"


def run_oracle_for_game(
    engine: KataGoAnalysis,
    db,
    game_id: int,
    cfg: dict,
) -> dict:
    thresholds = cfg.get("thresholds", DEFAULT_THRESHOLDS)
    sgf_path = get_sgf_path(db, game_id)
    parsed = parse_sgf(sgf_path)
    username = cfg["player"]["kgs_username"]
    my_color = player_color(parsed, username)
    if not my_color:
        raise RuntimeError(f"Joueur {username} introuvable dans {sgf_path}")

    suspects = suspect_moves_for_game(db, game_id, thresholds)
    positions = []
    for move in suspects:
        move_num = move["move_number"]
        turn_idx = move_num - 1
        positions.append((move_num, turn_idx, ORACLE_VISITS))

    print(f"  {len(positions)} coups suspects -> oracle {ORACLE_VISITS}v", flush=True)
    if not positions:
        return {
            "game_id": game_id,
            "visits": ORACLE_VISITS,
            "gpu_seconds": 0.0,
            "n_positions": 0,
            "positions": [],
        }

    results, gpu_sec = analyze_positions(
        engine, parsed, game_id, positions,
        tag="oracle",
        player_color_val=my_color,
        thresholds=thresholds,
    )
    return {
        "game_id": game_id,
        "visits": ORACLE_VISITS,
        "gpu_seconds": round(gpu_sec, 1),
        "n_positions": len(results),
        "positions": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Oracle 4000v — labo deep visits")
    parser.add_argument("--game-id", type=int, action="append", help="Limiter à ces IDs")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    if not SELECTED_GAMES_JSON.exists():
        print("ERREUR : lancer d'abord 00_setup_lab.py")
        sys.exit(1)

    cfg = load_lab_config()
    db = open_lab_db()
    game_ids = args.game_id or load_selected_game_ids()

    print("=== Labo Deep Visits — Oracle 4000v ===\n")
    print(f"Parties : {game_ids}")
    print(f"Config  : {ORACLE_CONFIG}\n")

    engine = KataGoAnalysis(cfg, analysis_config=resolve_path(ORACLE_CONFIG))
    print("Démarrage KataGo (oracle)...", flush=True)
    engine.start()
    for line in engine.startup_log[-5:]:
        print(f"  {line}", flush=True)

    total_gpu = 0.0
    t_all = time.perf_counter()

    try:
        for gid in game_ids:
            out = ref_path(gid)
            if args.skip_existing and out.exists():
                print(f"\nPartie #{gid} — skip (déjà {out.name})")
                continue

            print(f"\nPartie #{gid}...", flush=True)
            data = run_oracle_for_game(engine, db, gid, cfg)
            data["generated_at"] = datetime.now(timezone.utc).isoformat()
            save_json(out, data)
            total_gpu += data["gpu_seconds"]
            print(
                f"  -> {data['n_positions']} positions, "
                f"{data['gpu_seconds']:.0f}s GPU — sauvé {out.name}",
                flush=True,
            )
    finally:
        engine.stop()

    elapsed = time.perf_counter() - t_all
    print(f"\n{'='*60}")
    print(f"Oracle terminé — GPU cumulé ~{total_gpu:.0f}s, wall {elapsed:.0f}s")
    print("Prochaine étape : python scripts/lab/02_run_strategies.py")

    db.close()


if __name__ == "__main__":
    main()
