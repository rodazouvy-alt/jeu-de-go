#!/usr/bin/env python3
"""Rapport comparatif — croise labo deep visits + labo fuseki (sans GPU)."""
from __future__ import annotations

import csv
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "lab"))
sys.path.insert(0, str(ROOT / "scripts" / "lab" / "fuseki"))

import fuseki_lib as fl
from _common import strategy_path as deep_strategy_path, ref_path as deep_ref_path, load_json

OUT = ROOT / "data" / "lab" / "analysis_setup_report.md"
OUT_CSV = ROOT / "data" / "lab" / "analysis_setup_matrix.csv"

FUSEKI_MAX = 30

# Pipelines composés à évaluer
PIPELINES = {
    "P0_actuel": {
        "label": "Actuel (prod)",
        "desc": "Quick 400v + deep S1 (mistake+ 1000/2000v) + opening via quick",
        "fuseki": "F0",
        "middle": "S1",
        "gpu_note": "quick + deep massif",
    },
    "P1_quick_seul": {
        "label": "Quick seul",
        "desc": "Quick 400v uniquement, pas de passe supplementaire",
        "fuseki": "F0",
        "middle": "S0",
        "gpu_note": "quick only",
    },
    "P2_quick_fuseki_f6": {
        "label": "Quick + fuseki F6",
        "desc": "Quick 400v + fuseki cible (erreurs 1000v, ambigus 2000v), pas de deep milieu",
        "fuseki": "F6",
        "middle": "S0",
        "gpu_note": "quick + fuseki ~10s",
    },
    "P3_quick_fuseki_f5": {
        "label": "Quick + fuseki F5",
        "desc": "Quick 400v + erreurs fuseki 1000v, pas de deep milieu",
        "fuseki": "F5",
        "middle": "S0",
        "gpu_note": "quick + fuseki ~1s",
    },
    "P4_quick_f6_s4": {
        "label": "Quick + F6 + top3 milieu",
        "desc": "Quick + fuseki F6 + top-3 pertes milieu/fin a 2000v (S4)",
        "fuseki": "F6",
        "middle": "S4",
        "gpu_note": "quick + fuseki + 3 deep/partie",
    },
    "P5_quick_f6_s5": {
        "label": "Quick + F6 + tiered milieu",
        "desc": "Quick + fuseki F6 + paliers loss milieu (S5)",
        "fuseki": "F6",
        "middle": "S5",
        "gpu_note": "quick + fuseki + deep leger",
    },
    "P6_quick_f3": {
        "label": "Quick + fuseki F3",
        "desc": "Quick + tous tes coups fuseki 1000v, pas de deep milieu",
        "fuseki": "F3",
        "middle": "S0",
        "gpu_note": "quick + fuseki ~17s",
    },
}


def _norm(m):
    return (m or "pass").upper()


def load_deep_csv() -> dict[str, dict]:
    p = ROOT / "data" / "lab" / "deep_visits_results.csv"
    out = {}
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["strategy_id"]] = row
    return out


def load_fuseki_csv() -> dict[str, dict]:
    p = ROOT / "data" / "lab" / "fuseki" / "fuseki_results.csv"
    out = {}
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["strategy_id"]] = row
    return out


def position_metrics(strat_pos: dict, ref_pos: dict) -> dict:
    rb = _norm(strat_pos.get("best_move") or (strat_pos["top_moves"][0]["move"] if strat_pos.get("top_moves") else None))
    ref_best = _norm(ref_pos.get("best_move") or (ref_pos["top_moves"][0]["move"] if ref_pos.get("top_moves") else None))
    d = abs(float(strat_pos.get("point_loss") or 0) - float(ref_pos.get("point_loss") or 0))
    rr, sr = ref_pos.get("played_rank"), strat_pos.get("played_rank")
    tops_s = {_norm(t.get("move")) for t in (strat_pos.get("top_moves") or [])[:3]}
    tops_r = {_norm(t.get("move")) for t in (ref_pos.get("top_moves") or [])[:3]}
    return {
        "best_match": rb == ref_best,
        "top3_match": tops_s == tops_r and bool(tops_r),
        "severity_match": strat_pos.get("severity") == ref_pos.get("severity"),
        "rank_flip": rr is not None and sr is not None and rr != sr,
        "delta": d,
        "move_number": ref_pos["move_number"],
        "phase": "fuseki" if ref_pos["move_number"] <= FUSEKI_MAX else "middle",
    }


def merge_pipeline_on_game(
    gid: int,
    fuseki_sid: str,
    middle_sid: str,
) -> list[dict]:
    """Fusionne resultats fuseki + deep pour une partie (oracle par zone)."""
    metrics: list[dict] = []

    # Fuseki zone — oracle fuseki
    fref = {p["move_number"]: p for p in fl.oracle_positions(gid)}
    fstrat = fl.load_json(fl.strategy_path(fuseki_sid, gid))
    fpos = {p["move_number"]: p for p in fstrat["positions"]}

    for mn, rp in fref.items():
        if mn in fpos:
            m = position_metrics(fpos[mn], rp)
            m["game_id"] = gid
            metrics.append(m)

    # Middle zone — oracle deep (suspects only in deep lab)
    if deep_ref_path(gid).exists() and deep_strategy_path(middle_sid, gid).exists():
        dref = {p["move_number"]: p for p in load_json(deep_ref_path(gid))["positions"]}
        dstrat = load_json(deep_strategy_path(middle_sid, gid))
        dpos = {p["move_number"]: p for p in dstrat["positions"]}
        for mn, rp in dref.items():
            if mn <= FUSEKI_MAX:
                continue
            if mn in dpos:
                m = position_metrics(dpos[mn], rp)
                m["game_id"] = gid
                metrics.append(m)

    return metrics


def aggregate_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {}
    n = len(rows)
    fuseki = [r for r in rows if r["phase"] == "fuseki"]
    middle = [r for r in rows if r["phase"] == "middle"]
    deltas = [r["delta"] for r in rows]

    def _agg(subset):
        if not subset:
            return {}
        sn = len(subset)
        return {
            "n": sn,
            "best_move_pct": 100 * sum(r["best_match"] for r in subset) / sn,
            "top3_pct": 100 * sum(r["top3_match"] for r in subset) / sn,
            "delta_mean": statistics.mean(r["delta"] for r in subset),
            "flip_pct": 100 * sum(r["rank_flip"] for r in subset) / sn,
        }

    return {
        "n_total": n,
        "best_move_pct": 100 * sum(r["best_match"] for r in rows) / n,
        "top3_pct": 100 * sum(r["top3_match"] for r in rows) / n,
        "delta_mean": statistics.mean(deltas),
        "delta_p95": sorted(deltas)[max(0, int(n * 0.95) - 1)],
        "severity_pct": 100 * sum(r["severity_match"] for r in rows) / n,
        "flip_pct": 100 * sum(r["rank_flip"] for r in rows) / n,
        "fuseki": _agg(fuseki),
        "middle": _agg(middle),
    }


def estimate_gpu_per_game(pipe: dict, deep_csv: dict, fuseki_csv: dict) -> float:
    """Estimation GPU/partie a partir des labos (3 parties)."""
    mid = deep_csv.get(pipe["middle"], {})
    fus = fuseki_csv.get(pipe["fuseki"], {})
    mid_gpu = float(mid.get("gpu_total") or 0) / 3
    fus_gpu = float(fus.get("gpu_total") or 0) / 3
    # quick ~120s/partie (estimation depuis config 400v x ~70 coups / 200 vps)
    quick_est = 140.0
    if pipe["middle"] == "S0" and pipe["fuseki"] == "F0":
        return quick_est
    extra = 0.0
    if pipe["fuseki"] != "F0":
        extra += fus_gpu
    if pipe["middle"] != "S0":
        extra += mid_gpu
    return quick_est + extra


def theoretical_aggregate(pipe: dict, deep_csv: dict, fuseki_csv: dict) -> dict:
    """Agregation theorique ponderee (metrics lab globales par zone)."""
    fus = fuseki_csv[pipe["fuseki"]]
    mid = deep_csv[pipe["middle"]]
    # Poids typiques par partie : ~15 coups fuseki eval, ~25 suspects milieu
    wf, wm = 15, 25
    bm = (
        float(fus["best_move_pct"]) * wf + float(mid["best_move_match_pct"]) * wm
    ) / (wf + wm)
    t3 = (
        float(fus["top3_pct"]) * wf + float(mid.get("best_move_match_pct", mid["best_move_match_pct"])) * wm
    ) / (wf + wm)
    # top3 only in fuseki csv — for middle use best_move as proxy in theoretical
    if "top3_pct" not in mid:
        t3 = float(fus["top3_pct"])  # simplified
    d = (
        float(fus["delta_mean"]) * wf + float(mid["point_loss_delta_mean"]) * wm
    ) / (wf + wm)
    flip = (
        float(fus["rank_flip_pct"]) * wf + float(mid["rank_flip_pct"]) * wm
    ) / (wf + wm)
    return {
        "best_move_pct": bm,
        "top3_pct": float(fus["top3_pct"]) if pipe["fuseki"] != "F0" else float(fus["top3_pct"]),
        "delta_mean": d,
        "flip_pct": flip,
        "gpu_est": estimate_gpu_per_game(pipe, deep_csv, fuseki_csv),
    }


def main() -> None:
    deep_csv = load_deep_csv()
    fuseki_csv = load_fuseki_csv()

    # Merge exact sur #109 (seule partie commune)
    common_gid = 109
    pipeline_rows: list[dict] = []

    for pid, pipe in PIPELINES.items():
        exact = merge_pipeline_on_game(common_gid, pipe["fuseki"], pipe["middle"])
        exact_agg = aggregate_metrics(exact)
        theo = theoretical_aggregate(pipe, deep_csv, fuseki_csv)
        pipeline_rows.append({
            "id": pid,
            "label": pipe["label"],
            "desc": pipe["desc"],
            "fuseki": pipe["fuseki"],
            "middle": pipe["middle"],
            "exact_109": exact_agg,
            "theoretical": theo,
            "gpu_est": theo["gpu_est"],
        })

    # Score composite : qualite / cout
    for row in pipeline_rows:
        t = row["theoretical"]
        quality = (
            t["best_move_pct"] * 0.4
            + t["top3_pct"] * 0.2
            - t["delta_mean"] * 10
            - t["flip_pct"] * 0.2
        )
        row["quality_score"] = quality
        row["value_score"] = quality / (row["gpu_est"] ** 0.2)

    pipeline_rows.sort(key=lambda r: -r["value_score"])
    winner = pipeline_rows[0]
    actuel = next(r for r in pipeline_rows if r["id"] == "P0_actuel")
    best_quality = max(pipeline_rows, key=lambda r: r["theoretical"]["best_move_pct"])

    lines = [
        "# Setup d'analyse optimal — Rapport comparatif",
        "",
        f"*Genere le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "Croisement des labos **deep visits** (milieu/fin, 92 positions, oracle 4000v) "
        "et **fuseki** (coups joueur 1-30, 45 positions, oracle 4000v). "
        "Aucun test GPU supplementaire.",
        "",
        "## Verdict",
        "",
        f"**Setup recommande : {winner['label']}** (`{winner['id']}`)",
        "",
        winner["desc"],
        "",
        f"- best_move theorique pondere : **{winner['theoretical']['best_move_pct']:.1f}%**",
        f"- top3 fuseki : **{winner['theoretical']['top3_pct']:.1f}%**",
        f"- GPU estime/partie : **~{winner['gpu_est']:.0f}s** (vs ~{actuel['gpu_est']:.0f}s actuel)",
        "",
        "### vs setup actuel (P0)",
        "",
        f"| Metrique | Actuel | Recommande | Delta |",
        f"|----------|--------|------------|-------|",
        f"| best_move (theo.) | {actuel['theoretical']['best_move_pct']:.1f}% | "
        f"{winner['theoretical']['best_move_pct']:.1f}% | "
        f"{winner['theoretical']['best_move_pct'] - actuel['theoretical']['best_move_pct']:+.1f} |",
        f"| flip rank | {actuel['theoretical']['flip_pct']:.1f}% | "
        f"{winner['theoretical']['flip_pct']:.1f}% | "
        f"{winner['theoretical']['flip_pct'] - actuel['theoretical']['flip_pct']:+.1f} |",
        f"| GPU/partie | ~{actuel['gpu_est']:.0f}s | ~{winner['gpu_est']:.0f}s | "
        f"{winner['gpu_est'] - actuel['gpu_est']:+.0f}s |",
        "",
        "## Principe : 3 passes, 3 roles",
        "",
        "```",
        "Passe 1 — QUICK 400v     Toute la partie      Detection + base de donnees",
        "Passe 2 — FUSEKI F6      Tes coups 1-30       Etude ouverture (erreurs + ambigus)",
        "Passe 3 — MILIEU OFF     Coups 31+            Quick suffit (labo deep)",
        "```",
        "",
        "Le deep milieu actuel (S1) **degrade** la qualite vs quick seul :",
        "- best_move 73,9% vs 76,1% (oracle)",
        "- rank_flip 33,7% vs 17,4%",
        "- 175s GPU / 3 parties gaspilles",
        "",
        "Le fuseki **beneficie** d'une passe dediee :",
        "- F6 : best_move 82,2% (+4,4 vs quick), top3 68,9% (+6,7), ~9s/partie",
        "",
        "## Matrice des pipelines",
        "",
        "| Pipeline | Fuseki | Milieu | best_move% | top3% | dloss | flip% | GPU~ |",
        "|----------|--------|--------|------------|-------|-------|-------|------|",
    ]

    for row in sorted(pipeline_rows, key=lambda r: -r["value_score"]):
        t = row["theoretical"]
        star = " **" if row["id"] == winner["id"] else ""
        end = "**" if row["id"] == winner["id"] else ""
        lines.append(
            f"|{star}{row['label']}{end} | {row['fuseki']} | {row['middle']} | "
            f"{t['best_move_pct']:.1f} | {t['top3_pct']:.1f} | {t['delta_mean']:.2f} | "
            f"{t['flip_pct']:.1f} | {row['gpu_est']:.0f}s |"
        )

    # Partie #109 validation exacte
    lines.extend([
        "",
        "## Validation sur partie #109 (seule partie commune aux 2 labos)",
        "",
        "| Pipeline | Pos. | best_move | top3 | fuseki bm | milieu bm |",
        "|----------|------|-----------|------|-----------|-----------|",
    ])
    for row in pipeline_rows:
        e = row.get("exact_109") or {}
        if not e:
            continue
        f = e.get("fuseki", {})
        m = e.get("middle", {})
        lines.append(
            f"| {row['label']} | {e.get('n_total', 0)} | "
            f"{e.get('best_move_pct', 0):.0f}% | {e.get('top3_pct', 0):.0f}% | "
            f"{f.get('best_move_pct', 0):.0f}% | {m.get('best_move_pct', 0):.0f}% |"
        )

    lines.extend([
        "",
        "## Regle par coup — decision tree",
        "",
        "```",
        "Pour chaque coup de la partie :",
        "",
        "  [1] Quick 400v (obligatoire, tous les coups)",
        "        |",
        "        v",
        "  move_number <= 30 ET couleur joueur ?",
        "    |oui                          |non",
        "    v                             v",
        "  point_loss > 0.5               FIN (garder quick)",
        "  OU severity != ok ?                  (milieu/fin : quick suffit)",
        "    |oui       |non",
        "    v          v",
        "  ecart #1-#2   FIN (quick OK)",
        "  < 0.5 pt ?",
        "    |oui    |non",
        "    v       v",
        "  2000v   1000v",
        "  (F6)    (F6)",
        "```",
        "",
        "## Config.yaml cible",
        "",
        "```yaml",
        "katago:",
        "  quick_max_visits: 400",
        "  deep_max_visits: 1000      # inutilise milieu — reserve fuseki",
        "  deep_hard_max_visits: 2000",
        "  deep_rerun_min_severity: mistake  # desactiver deep milieu en pratique",
        "",
        "opening_analysis:",
        "  max_moves: 30",
        "  max_visits: 1000",
        "  hard_max_visits: 2000",
        "  min_point_loss: 0.5",
        "  ambiguous_gap: 0.5         # ecart #1-#2 pour 2000v",
        "  player_moves_only: true",
        "",
        "analysis:",
        "  mark_opening_on_complete: false",
        "  deep_middle_enabled: false  # labo : quick >= deep milieu",
        "```",
        "",
        "## Ce qu'on abandonne",
        "",
        "| Element | Raison |",
        "|---------|--------|",
        "| Deep S1 milieu (mistake+ 1000v) | Pire que quick vs oracle, 33% rank_flip |",
        "| opening via quick seul | -4,4% best_move fuseki vs oracle |",
        "| F3/F4 (tous coups fuseki/adversaire) | GPU x3-x5 pour gain top3 marginal |",
        "| S4 top3 milieu (option P4) | +24s GPU, best_move = quick seul (76,1%) |",
        "",
        "## Sources",
        "",
        "- `data/lab/deep_visits_report.md` — parties #109, #108, #106",
        "- `data/lab/fuseki/fuseki_report.md` — parties #109, #101, #99",
        "- `ROADMAP.md` section Decisions labo GPU",
        "",
        "## Limites",
        "",
        "- Oracle 4000v != verite absolue ; echantillon 3+3 parties, 3d KGS rapide",
        "- Partie #109 seule validation croisee exacte",
        "- Estimation GPU basee sur debits labo reels (200 v/s configure)",
        "- Human SL / patterns non mesures ici (enrichissement post-analyse)",
    ])

    OUT.write_text("\n".join(lines), encoding="utf-8")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "pipeline_id", "label", "fuseki", "middle",
            "best_move_pct", "top3_pct", "delta_mean", "flip_pct",
            "gpu_est_s", "quality_score", "value_score",
        ])
        w.writeheader()
        for row in pipeline_rows:
            t = row["theoretical"]
            w.writerow({
                "pipeline_id": row["id"],
                "label": row["label"],
                "fuseki": row["fuseki"],
                "middle": row["middle"],
                "best_move_pct": round(t["best_move_pct"], 2),
                "top3_pct": round(t["top3_pct"], 2),
                "delta_mean": round(t["delta_mean"], 3),
                "flip_pct": round(t["flip_pct"], 2),
                "gpu_est_s": round(row["gpu_est"], 1),
                "quality_score": round(row["quality_score"], 2),
                "value_score": round(row["value_score"], 3),
            })

    print(f"Rapport: {OUT}")
    print(f"CSV: {OUT_CSV}")
    print(f"Gagnant: {winner['id']} — {winner['label']}")


if __name__ == "__main__":
    main()
