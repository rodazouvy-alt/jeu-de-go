#!/usr/bin/env python3
"""Labo fuseki — rapport final."""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fuseki_lib as fl

REPORT = fl.FUSEKI_LAB_DIR / "fuseki_report.md"
CSV_OUT = fl.FUSEKI_LAB_DIR / "fuseki_results.csv"


def _norm(m):
    return (m or "pass").upper()


def _p95(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[max(0, int(len(s) * 0.95) - 1)]


def top3_set(pos: dict) -> set[str]:
    tops = pos.get("top_moves") or []
    return {_norm(t.get("move")) for t in tops[:3]}


def aggregate(sid: str, gids: list[int]) -> dict:
    comps = []
    gpu = 0
    n_deep = 0
    for gid in gids:
        sp = fl.strategy_path(sid, gid)
        if not sp.exists():
            continue
        data = fl.load_json(sp)
        gpu += float(data.get("gpu_seconds") or 0)
        n_deep += int(data.get("n_deep") or 0)
        ref = {p["move_number"]: p for p in fl.oracle_positions(gid)}
        for pos in data["positions"]:
            mn = pos["move_number"]
            if mn not in ref:
                continue
            rp = ref[mn]
            rb = _norm(rp.get("best_move") or (rp["top_moves"][0]["move"] if rp.get("top_moves") else None))
            sb = _norm(pos.get("best_move") or (pos["top_moves"][0]["move"] if pos.get("top_moves") else None))
            d = abs(float(pos.get("point_loss") or 0) - float(rp.get("point_loss") or 0))
            rr, sr = rp.get("played_rank"), pos.get("played_rank")
            comps.append({
                "best_match": sb == rb,
                "severity_match": pos.get("severity") == rp.get("severity"),
                "rank_flip": rr is not None and sr is not None and rr != sr,
                "delta": d,
                "top3_match": top3_set(pos) == top3_set(rp),
            })
    n = len(comps)
    if not n:
        return {"strategy_id": sid, "n_positions": 0}
    deltas = [c["delta"] for c in comps]
    return {
        "strategy_id": sid,
        "strategy_name": fl.STRATEGY_LABELS[sid],
        "n_positions": n,
        "n_deep": n_deep,
        "best_move_pct": 100 * sum(c["best_match"] for c in comps) / n,
        "top3_pct": 100 * sum(c["top3_match"] for c in comps) / n,
        "delta_mean": statistics.mean(deltas),
        "delta_p95": _p95(deltas),
        "severity_pct": 100 * sum(c["severity_match"] for c in comps) / n,
        "rank_flip_pct": 100 * sum(c["rank_flip"] for c in comps) / n,
        "gpu_total": gpu,
    }


def recommend(aggs: dict[str, dict]) -> tuple[str, str]:
    best = None
    for sid in fl.STRATEGIES:
        if sid == "F0":
            continue
        a = aggs.get(sid, {})
        if not a.get("n_positions"):
            continue
        quality = (
            a["best_move_pct"] * 0.35
            + a["top3_pct"] * 0.25
            + a["severity_pct"] * 0.25
            - a["delta_mean"] * 8
            - a["rank_flip_pct"] * 0.15
        )
        gpu = max(a["gpu_total"], 0.5)
        score = quality / (gpu ** 0.25)
        if best is None or score > best[0]:
            best = (score, sid, quality, gpu)
    if not best:
        return "F3", "Donnees insuffisantes — player_1000 par defaut."
    _, sid, _, gpu = best
    f0 = aggs.get("F0", {})
    gain_bm = aggs[sid]["best_move_pct"] - f0.get("best_move_pct", 0)
    gain_t3 = aggs[sid]["top3_pct"] - f0.get("top3_pct", 0)
    return sid, (
        f"{sid} ({fl.STRATEGY_LABELS[sid]}) : best_move {aggs[sid]['best_move_pct']:.1f}% "
        f"(+{gain_bm:.1f} vs quick), top3 {aggs[sid]['top3_pct']:.1f}% (+{gain_t3:.1f}), "
        f"GPU {gpu:.0f}s sur 3 parties."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", action="store_true")
    args = parser.parse_args()

    gids = fl.load_selected_game_ids()
    aggs = {sid: aggregate(sid, gids) for sid in fl.STRATEGIES}
    rec_sid, rec_reason = recommend(aggs)
    selected = fl.load_json(fl.SELECTED_JSON)

    lines = [
        "# Labo Fuseki — Rapport",
        "",
        f"*Genere le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "## Resume executif",
        "",
        f"**Recommandation : {rec_sid} ({fl.STRATEGY_LABELS[rec_sid]})**",
        "",
        rec_reason,
        "",
        "Perimetre : **coups joueur 1-30**, oracle **4000v**, 3 parties recentes.",
        "",
        "## Parties",
        "",
        "| ID | Adv | Date | Coups fuseki | Err fuseki | Zone grise |",
        "|----|-----|------|--------------|------------|------------|",
    ]
    for g in selected.get("selected", []):
        date = f"{g.get('year')}-{g.get('month')}"
        lines.append(
            f"| {g['id']} | {g.get('opponent','?')} | {date} | "
            f"{g.get('fuseki_player_moves')} | {g.get('fuseki_errors')} | "
            f"{g.get('fuseki_grey')} |"
        )

    lines.extend([
        "",
        "## Tableau comparatif",
        "",
        "| ID | Nom | best_move% | top3% | dloss moy | dloss p95 | severity% | flip% | GPU(s) | n_deep |",
        "|----|-----|------------|-------|-----------|-----------|-----------|-------|--------|--------|",
    ])
    for sid in fl.STRATEGIES:
        a = aggs.get(sid, {})
        if not a.get("n_positions"):
            continue
        lines.append(
            f"| {sid} | {a['strategy_name']} | {a['best_move_pct']:.1f} | "
            f"{a['top3_pct']:.1f} | {a['delta_mean']:.2f} | {a['delta_p95']:.2f} | "
            f"{a['severity_pct']:.1f} | {a['rank_flip_pct']:.1f} | "
            f"{a['gpu_total']:.0f} | {a['n_deep']} |"
        )

    f0 = aggs.get("F0", {})
    lines.extend([
        "",
        "## Interpretation",
        "",
        f"- **F0 (quick)** : {f0.get('best_move_pct', 0):.1f}% best_move, "
        f"{f0.get('top3_pct', 0):.1f}% top3 — baseline sans GPU supplementaire",
        "- **top3%** : stabilite des 3 meilleurs coups (crucial pour etude joseki)",
        "- Voir `ROADMAP.md` section Decisions labo GPU pour le contexte milieu/fin",
        "",
        "## Proposition config.yaml",
        "",
        "```yaml",
        "opening_analysis:",
        "  max_moves: 30",
        f"  max_visits: 1000  # ajuster selon {rec_sid}",
        "",
        "analysis:",
        "  mark_opening_on_complete: false",
        "```",
        "",
        "## Verdict",
        "",
        f"**{rec_sid} — {fl.STRATEGY_LABELS[rec_sid]}**",
        "",
        rec_reason,
    ])

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Rapport: {REPORT}")

    if args.csv:
        rows = [aggs[sid] for sid in fl.STRATEGIES if aggs[sid].get("n_positions")]
        if rows:
            with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"CSV: {CSV_OUT}")


if __name__ == "__main__":
    main()
