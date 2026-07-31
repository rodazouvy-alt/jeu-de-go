#!/usr/bin/env python3
"""Labo fuseki — strategies F0-F6."""
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


def run_strategy(engine, db, gid: int, sid: str, cfg: dict) -> dict:
    thresholds = cfg.get("thresholds", fl.DEFAULT_THRESHOLDS)
    parsed = parse_sgf(fl.get_sgf_path(db, gid))
    my_color = player_color(parsed, cfg["player"]["kgs_username"])
    fuseki_moves = fl.player_fuseki_moves(db, gid, thresholds)
    plan = fl.strategy_plan(sid, parsed, my_color, fuseki_moves, thresholds)

    quick_only: dict[int, dict] = {}
    to_run: list[tuple[int, int, int]] = []
    move_by_num = {m["move_number"]: m for m in fuseki_moves}

    for mn, ti, visits in plan:
        if visits <= 0:
            m = move_by_num.get(mn)
            if m:
                quick_only[mn] = fl.quick_position_from_db_wrapper(
                    m, my_color, thresholds,
                )
        else:
            to_run.append((mn, ti, visits))

    katago_results: list[dict] = []
    gpu = 0.0
    if to_run and engine is not None:
        katago_results, gpu = fl.analyze_positions(
            engine, parsed, gid, to_run,
            tag=f"fuseki_{sid}",
            player_color_val=my_color,
            thresholds=thresholds,
        )

    by_num = {r["move_number"]: r for r in katago_results}
    positions = []
    for mn, _, _ in plan:
        if mn in by_num:
            positions.append(by_num[mn])
        elif mn in quick_only:
            positions.append(quick_only[mn])

    positions.sort(key=lambda p: p["move_number"])
    return {
        "game_id": gid,
        "strategy_id": sid,
        "strategy_name": fl.STRATEGY_LABELS[sid],
        "gpu_seconds": round(gpu, 1),
        "n_positions": len(positions),
        "n_deep": len(katago_results),
        "n_quick": len(quick_only),
        "positions": positions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=list(fl.STRATEGIES), action="append")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    cfg = fl.load_lab_config()
    db = fl.open_lab_db()
    game_ids = fl.load_selected_game_ids()
    strategies = args.strategy or list(fl.STRATEGIES)

    for gid in game_ids:
        if not fl.ref_path(gid).exists():
            print(f"ERREUR: oracle manquant #{gid}")
            sys.exit(1)

    print("=== Labo Fuseki — Strategies ===\n")
    needs_gpu = any(s != "F0" for s in strategies)
    engine = None
    if needs_gpu:
        engine = KataGoAnalysis(cfg, analysis_config=resolve_path(fl.DEEP_CONFIG))
        engine.start()

    total_gpu = 0.0
    try:
        for sid in strategies:
            print(f"\n--- {sid} ({fl.STRATEGY_LABELS[sid]}) ---")
            for gid in game_ids:
                out = fl.strategy_path(sid, gid)
                if args.skip_existing and out.exists():
                    print(f"  #{gid} skip")
                    continue
                eng = None if sid == "F0" else engine
                data = run_strategy(eng, db, gid, sid, cfg)
                data["generated_at"] = datetime.now(timezone.utc).isoformat()
                fl.save_json(out, data)
                total_gpu += data["gpu_seconds"]
                print(
                    f"  #{gid}: {data['n_deep']} deep + {data['n_quick']} quick, "
                    f"{data['gpu_seconds']:.0f}s"
                )
    finally:
        if engine:
            engine.stop()

    print(f"\nStrategies fuseki terminees — GPU ~{total_gpu:.0f}s")
    print("Suivant: python scripts/lab/fuseki/03_generate_report.py")
    db.close()


if __name__ == "__main__":
    main()
