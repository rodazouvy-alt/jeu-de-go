#!/usr/bin/env python3
"""Étape 2 — Exécution des stratégies S0–S6 sur les 3 parties.

Usage :
    $env:PYTHONPATH="src"
    python scripts/lab/02_run_strategies.py
    python scripts/lab/02_run_strategies.py --strategy S3
    python scripts/lab/02_run_strategies.py --skip-existing
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
    STRATEGIES,
    STRATEGY_LABELS,
    analyze_positions,
    get_player_moves,
    get_sgf_path,
    is_suspect,
    load_lab_config,
    load_selected_game_ids,
    open_lab_db,
    oracle_positions,
    quick_position_from_db,
    ref_path,
    save_json,
    strategy_path,
    strategy_visits,
    suspect_moves_for_game,
)


def run_strategy_for_game(
    engine: KataGoAnalysis | None,
    db,
    game_id: int,
    strategy_id: str,
    cfg: dict,
) -> dict:
    thresholds = cfg.get("thresholds", DEFAULT_THRESHOLDS)
    sgf_path = get_sgf_path(db, game_id)
    parsed = parse_sgf(sgf_path)
    username = cfg["player"]["kgs_username"]
    my_color = player_color(parsed, username)

    game_moves = get_player_moves(db, game_id)
    suspects = suspect_moves_for_game(db, game_id, thresholds)
    suspect_nums = {m["move_number"] for m in suspects}

    # Positions évaluées = intersection oracle ref (même périmètre)
    if ref_path(game_id).exists():
        ref_nums = {p["move_number"] for p in oracle_positions(game_id)}
        suspects = [m for m in suspects if m["move_number"] in ref_nums]

    to_analyze: list[tuple[int, int, int]] = []
    quick_only: dict[int, dict] = {}

    for move in suspects:
        visits = strategy_visits(
            strategy_id, move, my_color, thresholds, game_moves=game_moves,
        )
        move_num = move["move_number"]
        if visits <= 0:
            quick_only[move_num] = quick_position_from_db(
                move, my_color, thresholds,
            )
        else:
            to_analyze.append((move_num, move_num - 1, visits))

    gpu_sec = 0.0
    katago_results: list[dict] = []
    if to_analyze and engine is not None:
        katago_results, gpu_sec = analyze_positions(
            engine, parsed, game_id, to_analyze,
            tag=f"{strategy_id}",
            player_color_val=my_color,
            thresholds=thresholds,
        )

    all_positions: list[dict] = []
    katago_by_num = {r["move_number"]: r for r in katago_results}
    for move in suspects:
        mn = move["move_number"]
        if mn in katago_by_num:
            all_positions.append(katago_by_num[mn])
        elif mn in quick_only:
            all_positions.append(quick_only[mn])

    all_positions.sort(key=lambda p: p["move_number"])
    n_deep = len(katago_results)
    n_quick = len(quick_only)

    return {
        "game_id": game_id,
        "strategy_id": strategy_id,
        "strategy_name": STRATEGY_LABELS[strategy_id],
        "gpu_seconds": round(gpu_sec, 1),
        "n_positions": len(all_positions),
        "n_deep": n_deep,
        "n_quick": n_quick,
        "positions": all_positions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stratégies S0-S6 — labo deep visits")
    parser.add_argument(
        "--strategy", choices=list(STRATEGIES), action="append",
        help="Limiter à certaines stratégies",
    )
    parser.add_argument("--game-id", type=int, action="append")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    cfg = load_lab_config()
    db = open_lab_db()
    game_ids = args.game_id or load_selected_game_ids()
    strategies = args.strategy or list(STRATEGIES)

    # Vérifier oracle
    for gid in game_ids:
        if not ref_path(gid).exists():
            print(f"ERREUR : oracle manquant pour partie #{gid} — lancer 01_oracle_ref.py")
            sys.exit(1)

    print("=== Labo Deep Visits — Stratégies ===\n")
    print(f"Parties    : {game_ids}")
    print(f"Stratégies : {strategies}\n")

    deep_config = cfg["katago"].get("deep_config", "config/katago_deep.cfg")
    engine: KataGoAnalysis | None = None
    needs_gpu = any(s != "S0" for s in strategies)

    if needs_gpu:
        engine = KataGoAnalysis(cfg, analysis_config=resolve_path(deep_config))
        print("Démarrage KataGo (deep)...", flush=True)
        engine.start()
        for line in engine.startup_log[-5:]:
            print(f"  {line}", flush=True)

    total_gpu = 0.0
    t_all = time.perf_counter()

    try:
        for sid in strategies:
            label = STRATEGY_LABELS[sid]
            print(f"\n--- {sid} ({label}) ---", flush=True)

            for gid in game_ids:
                out = strategy_path(sid, gid)
                if args.skip_existing and out.exists():
                    print(f"  #{gid} skip (existe)")
                    continue

                print(f"  Partie #{gid}...", flush=True)
                eng = None if sid == "S0" else engine
                data = run_strategy_for_game(eng, db, gid, sid, cfg)
                data["generated_at"] = datetime.now(timezone.utc).isoformat()
                save_json(out, data)
                total_gpu += data["gpu_seconds"]
                print(
                    f"    {data['n_deep']} deep + {data['n_quick']} quick, "
                    f"{data['gpu_seconds']:.0f}s GPU",
                    flush=True,
                )
    finally:
        if engine:
            engine.stop()

    elapsed = time.perf_counter() - t_all
    print(f"\n{'='*60}")
    print(f"Stratégies terminées — GPU cumulé ~{total_gpu:.0f}s, wall {elapsed:.0f}s")
    print("Prochaine étape : python scripts/lab/03_generate_report.py")

    db.close()


if __name__ == "__main__":
    main()
