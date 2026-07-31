"""Analyse ponctuelle des résultats labo (usage interne)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import load_selected_game_ids, ref_path, strategy_path, load_json


def main() -> None:
    gids = load_selected_game_ids()
    print("Positions ou S1 rapproche de l'oracle vs quick (delta > 0.2pt):")
    for gid in gids:
        ref = {p["move_number"]: p for p in load_json(ref_path(gid))["positions"]}
        s0 = {p["move_number"]: p for p in load_json(strategy_path("S0", gid))["positions"]}
        s1 = {p["move_number"]: p for p in load_json(strategy_path("S1", gid))["positions"]}
        s4 = {p["move_number"]: p for p in load_json(strategy_path("S4", gid))["positions"]}
        for mn in sorted(ref):
            d0 = abs(float(s0[mn]["point_loss"]) - float(ref[mn]["point_loss"]))
            d1 = abs(float(s1[mn]["point_loss"]) - float(ref[mn]["point_loss"]))
            d4 = abs(float(s4[mn]["point_loss"]) - float(ref[mn]["point_loss"]))
            if d1 < d0 - 0.2:
                print(
                    f"  #{gid} coup {mn:3d} ref={ref[mn]['point_loss']:5.1f} "
                    f"quick_d={d0:.2f} S1_d={d1:.2f} S4_d={d4:.2f} "
                    f"visits={s1[mn].get('visits', 400)} "
                    f"sev={s0[mn]['severity']}"
                )

    print("\nCoups deep S4 (2000v):")
    for gid in gids:
        sd = load_json(strategy_path("S4", gid))
        for p in sd["positions"]:
            if p.get("visits", 400) > 400:
                print(
                    f"  #{gid} coup {p['move_number']:3d} "
                    f"loss={p['point_loss']:5.1f} {p['severity']} "
                    f"visits={p.get('visits')}"
                )

    print("\nMetriques par phase (vs oracle 4000v):")
    for label, max_move in [("Fuseki 1-30", 30), ("Milieu/fin 31+", None)]:
        print(f"\n{label}:")
        for sid in ("S0", "S1", "S4", "S5"):
            n = bm = rf = 0
            deltas = []
            for gid in gids:
                ref = {
                    p["move_number"]: p
                    for p in load_json(ref_path(gid))["positions"]
                }
                sd = load_json(strategy_path(sid, gid))
                for sp in sd["positions"]:
                    mn = sp["move_number"]
                    if max_move and mn > max_move:
                        continue
                    if max_move is None and mn <= 30:
                        continue
                    if mn not in ref:
                        continue
                    rp = ref[mn]
                    rb = (rp.get("best_move") or rp["top_moves"][0]["move"]).upper()
                    sb = (sp.get("best_move") or sp["top_moves"][0]["move"]).upper()
                    bm += int(sb == rb)
                    rr, sr = rp.get("played_rank"), sp.get("played_rank")
                    if rr is not None and sr is not None and rr != sr:
                        rf += 1
                    deltas.append(
                        abs(float(sp["point_loss"]) - float(rp["point_loss"]))
                    )
                    n += 1
            if n:
                print(
                    f"  {sid}: n={n:2d} bm={100*bm/n:5.1f}% "
                    f"d={sum(deltas)/n:.3f} flip={100*rf/n:5.1f}%"
                )


if __name__ == "__main__":
    main()
