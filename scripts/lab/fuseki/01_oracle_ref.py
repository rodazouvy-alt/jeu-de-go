#!/usr/bin/env python3
"""Labo fuseki — oracle 4000v sur tous les coups joueur 1-30."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from goprogress.config import resolve_path
from goprogress.katago import KataGoAnalysis
from goprogress.sgf_parse import parse_sgf, player_color

import fuseki_lib as fl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-id", type=int, action="append")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    cfg = fl.load_lab_config()
    db = fl.open_lab_db()
    game_ids = args.game_id or fl.load_selected_game_ids()

    print("=== Labo Fuseki — Oracle 4000v ===\n")
    print(f"Parties: {game_ids} | coups joueur 1-30\n")

    engine = KataGoAnalysis(cfg, analysis_config=resolve_path(fl.ORACLE_CONFIG))
    print("Demarrage KataGo...", flush=True)
    engine.start()

    total_gpu = 0.0
    try:
        for gid in game_ids:
            out = fl.ref_path(gid)
            if args.skip_existing and out.exists():
                print(f"#{gid} skip")
                continue

            parsed = parse_sgf(fl.get_sgf_path(db, gid))
            my_color = player_color(parsed, cfg["player"]["kgs_username"])
            turns = fl.fuseki_turns(parsed, my_color)
            positions = [(mn, ti, fl.ORACLE_VISITS) for mn, ti in turns]

            print(f"\n#{gid}: {len(positions)} coups joueur fuseki -> {fl.ORACLE_VISITS}v")
            results, gpu = fl.analyze_positions(
                engine, parsed, gid, positions,
                tag="fuseki_oracle",
                player_color_val=my_color,
                thresholds=cfg.get("thresholds", fl.DEFAULT_THRESHOLDS),
            )
            total_gpu += gpu
            fl.save_json(out, {
                "game_id": gid,
                "visits": fl.ORACLE_VISITS,
                "fuseki_max_move": fl.FUSEKI_MAX_MOVE,
                "gpu_seconds": round(gpu, 1),
                "n_positions": len(results),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "positions": results,
            })
            print(f"  -> {len(results)} pos, {gpu:.0f}s GPU")
    finally:
        engine.stop()

    print(f"\nOracle fuseki termine — GPU ~{total_gpu:.0f}s")
    print("Suivant: python scripts/lab/fuseki/02_run_strategies.py")
    db.close()


if __name__ == "__main__":
    main()
