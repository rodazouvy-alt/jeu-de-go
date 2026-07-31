#!/usr/bin/env python3
"""Étape 3 — Agrégation métriques et rapport final.

Usage :
    $env:PYTHONPATH="src"
    python scripts/lab/03_generate_report.py
    python scripts/lab/03_generate_report.py --csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lab"))

from _common import (  # noqa: E402
    CANDIDATES_JSON,
    LAB_DIR,
    SELECTED_GAMES_JSON,
    STRATEGIES,
    STRATEGY_LABELS,
    load_json,
    load_lab_config,
    load_selected_game_ids,
    oracle_positions,
    ref_path,
    strategy_path,
)


REPORT_PATH = LAB_DIR / "deep_visits_report.md"
CSV_PATH = LAB_DIR / "deep_visits_results.csv"


def _norm_move(mv: str | None) -> str:
    if not mv or mv.lower() == "pass":
        return "PASS"
    return mv.upper().strip()


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(len(s) * 0.95) - 1
    return s[max(0, min(idx, len(s) - 1))]


def build_ref_index(game_id: int) -> dict[int, dict]:
    return {p["move_number"]: p for p in oracle_positions(game_id)}


def compare_position(strat_pos: dict, ref_pos: dict) -> dict:
    ref_best = _norm_move(
        ref_pos["best_move"]
        or (ref_pos["top_moves"][0]["move"] if ref_pos.get("top_moves") else None)
    )
    strat_best = _norm_move(
        strat_pos["best_move"]
        or (strat_pos["top_moves"][0]["move"] if strat_pos.get("top_moves") else None)
    )
    ref_pl = float(ref_pos.get("point_loss") or 0)
    strat_pl = float(strat_pos.get("point_loss") or 0)
    ref_sev = ref_pos.get("severity", "ok")
    strat_sev = strat_pos.get("severity", "ok")
    ref_rank = ref_pos.get("played_rank")
    strat_rank = strat_pos.get("played_rank")

    return {
        "move_number": ref_pos["move_number"],
        "best_move_match": strat_best == ref_best,
        "point_loss_delta": abs(strat_pl - ref_pl),
        "severity_match": strat_sev == ref_sev,
        "rank_flip": (
            ref_rank is not None
            and strat_rank is not None
            and ref_rank != strat_rank
        ),
        "ref_pl": ref_pl,
        "strat_pl": strat_pl,
        "ref_sev": ref_sev,
        "strat_sev": strat_sev,
        "ref_best": ref_best,
        "strat_best": strat_best,
        "played_move": ref_pos.get("played_move"),
    }


def aggregate_strategy(strategy_id: str, game_ids: list[int]) -> dict:
    comparisons: list[dict] = []
    gpu_by_game: dict[int, float] = {}
    n_deep = 0
    n_quick = 0

    for gid in game_ids:
        sp = strategy_path(strategy_id, gid)
        if not sp.exists():
            continue
        data = load_json(sp)
        gpu_by_game[gid] = float(data.get("gpu_seconds") or 0)
        n_deep += int(data.get("n_deep") or 0)
        n_quick += int(data.get("n_quick") or 0)

        ref_idx = build_ref_index(gid)
        for pos in data.get("positions", []):
            mn = pos["move_number"]
            if mn not in ref_idx:
                continue
            comparisons.append(compare_position(pos, ref_idx[mn]))

    n = len(comparisons)
    if n == 0:
        return {"strategy_id": strategy_id, "n_positions": 0}

    deltas = [c["point_loss_delta"] for c in comparisons]
    return {
        "strategy_id": strategy_id,
        "strategy_name": STRATEGY_LABELS[strategy_id],
        "n_positions": n,
        "n_deep": n_deep,
        "n_quick": n_quick,
        "best_move_match_pct": 100 * sum(c["best_move_match"] for c in comparisons) / n,
        "point_loss_delta_mean": statistics.mean(deltas),
        "point_loss_delta_p95": _p95(deltas),
        "severity_match_pct": 100 * sum(c["severity_match"] for c in comparisons) / n,
        "rank_flip_pct": 100 * sum(c["rank_flip"] for c in comparisons) / n,
        "gpu_total": sum(gpu_by_game.values()),
        "gpu_by_game": gpu_by_game,
        "comparisons": comparisons,
    }


def find_redundant(aggregates: dict[str, dict]) -> list[tuple[str, str]]:
    redundant: list[tuple[str, str]] = []
    ids = [s for s in STRATEGIES if s in aggregates and aggregates[s]["n_positions"]]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ca, cb = aggregates[a], aggregates[b]
            if (
                abs(ca["best_move_match_pct"] - cb["best_move_match_pct"]) < 0.5
                and abs(ca["severity_match_pct"] - cb["severity_match_pct"]) < 0.5
                and abs(ca["point_loss_delta_mean"] - cb["point_loss_delta_mean"]) < 0.05
                and ca["n_deep"] == cb["n_deep"]
            ):
                redundant.append((a, b))
    return redundant


def pick_examples(
    aggregates: dict[str, dict],
    game_ids: list[int],
    *,
    n: int = 5,
) -> list[dict]:
    """Exemples intéressants : gros écart quick vs ref, ou S1 gaspille."""
    examples: list[dict] = []
    s1 = aggregates.get("S1", {})
    s3 = aggregates.get("S3", {})
    s5 = aggregates.get("S5", {})

    for c in s1.get("comparisons", []):
        if c["point_loss_delta"] >= 1.0:
            s3c = next(
                (x for x in s3.get("comparisons", []) if x["move_number"] == c["move_number"]),
                None,
            )
            s5c = next(
                (x for x in s5.get("comparisons", []) if x["move_number"] == c["move_number"]),
                None,
            )
            examples.append({
                "move_number": c["move_number"],
                "played": c["played_move"],
                "ref_pl": c["ref_pl"],
                "ref_sev": c["ref_sev"],
                "s1_pl": c["strat_pl"],
                "s1_delta": c["point_loss_delta"],
                "s3_ok": s3c and s3c["point_loss_delta"] < 0.5,
                "s5_ok": s5c and s5c["point_loss_delta"] < 0.5,
            })

    examples.sort(key=lambda x: -x["s1_delta"])
    return examples[:n]


def recommend_strategy(aggregates: dict[str, dict]) -> tuple[str, str]:
    """Heuristique : meilleur ratio qualité/temps parmi S1-S6 (exclut S0)."""
    candidates = []
    for sid in STRATEGIES:
        if sid == "S0":
            continue
        agg = aggregates.get(sid)
        if not agg or not agg.get("n_positions"):
            continue
        quality = (
            agg["best_move_match_pct"] * 0.4
            + agg["severity_match_pct"] * 0.4
            - agg["point_loss_delta_mean"] * 5
            - agg["rank_flip_pct"] * 0.2
        )
        gpu = max(agg["gpu_total"], 1)
        score = quality / (gpu ** 0.3)
        candidates.append((sid, score, quality, gpu))

    if not candidates:
        return "S1", "Pas assez de données — garder l'actuel par défaut."

    candidates.sort(key=lambda x: -x[1])
    best = candidates[0]
    sid = best[0]
    name = STRATEGY_LABELS[sid]

    # Comparer à S1
    s1 = aggregates.get("S1", {})
    if sid == "S1":
        reason = (
            f"S1 ({name}) reste optimal : "
            f"{s1.get('best_move_match_pct', 0):.0f}% best_move, "
            f"Δloss moy {s1.get('point_loss_delta_mean', 0):.2f}pt, "
            f"~{s1.get('gpu_total', 0):.0f}s GPU."
        )
    else:
        agg = aggregates[sid]
        reason = (
            f"{sid} ({name}) bat S1 au ratio qualité/temps : "
            f"best_move {agg['best_move_match_pct']:.0f}% vs "
            f"{s1.get('best_move_match_pct', 0):.0f}%, "
            f"GPU {agg['gpu_total']:.0f}s vs {s1.get('gpu_total', 0):.0f}s."
        )
    return sid, reason


def config_yaml_proposal(sid: str) -> str:
    proposals = {
        "S1": """katago:
  deep_max_visits: 1000
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake""",
        "S2": """katago:
  deep_max_visits: 1000
  deep_hard_max_visits: 1000
  deep_rerun_min_severity: mistake""",
        "S3": """katago:
  deep_max_visits: 1000
  deep_rerun_min_severity: mistake
  # + filtre point_loss 1.5–15 en code (_analyze_deep)""",
        "S5": """katago:
  deep_max_visits: 1000
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake
  # + paliers loss 5–12 / 12–25 en code""",
        "S6": """katago:
  deep_max_visits: 1000
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake
  # + top2 ambigus (écart <0.5pt) en code""",
    }
    return proposals.get(sid, proposals["S1"])


def generate_report(aggregates: dict[str, dict], game_ids: list[int]) -> str:
    selected = load_json(SELECTED_GAMES_JSON)
    candidates = load_json(CANDIDATES_JSON) if CANDIDATES_JSON.exists() else {}
    redundant = find_redundant(aggregates)
    rec_sid, rec_reason = recommend_strategy(aggregates)
    examples = pick_examples(aggregates, game_ids)

    lines = [
        "# Labo Deep Visits — Rapport",
        "",
        f"*Généré le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "## Résumé exécutif",
        "",
        f"**Recommandation : {rec_sid} ({STRATEGY_LABELS[rec_sid]})**",
        "",
        rec_reason,
        "",
        "## Parties sélectionnées",
        "",
        "| ID | Adversaire | Date | Coups | Erreurs | Zone grise | Raison |",
        "|----|------------|------|-------|---------|------------|--------|",
    ]

    for g in selected.get("selected", []):
        date = f"{g.get('year', '?')}-{g.get('month', '?')}"
        notes = ", ".join(g.get("score_notes", []))
        lines.append(
            f"| {g['id']} | {g.get('opponent', '?')} | {date} | "
            f"{g.get('total_moves', '?')} | {g.get('errors', '?')} | "
            f"{g.get('grey_zone', '?')} | {notes} |"
        )

    if candidates.get("top_candidates"):
        lines.extend([
            "",
            "### Top 10 candidates (avant sélection)",
            "",
            "| ID | Adv | Date | Coups | Err | Big | Gris | Statut |",
            "|----|-----|------|-------|-----|-----|------|--------|",
        ])
        for c in candidates["top_candidates"][:10]:
            date = f"{c.get('year', '?')}-{c.get('month', '?')}"
            status = c.get("exclude_reason") or f"score={c.get('score', 0)}"
            sel = "*" if c["id"] in game_ids else ""
            lines.append(
                f"| {c['id']} | {c.get('opponent', '?')[:12]} | {date} | "
                f"{c.get('total_moves', '?')} | {c.get('errors', '?')} | "
                f"{c.get('big_errors', '?')} | {c.get('grey_zone', '?')} | "
                f"{status} {sel} |"
            )

    lines.extend([
        "",
        "## Tableau comparatif",
        "",
        "| Strat | Nom | best_move% | Δloss moy | Δloss p95 | severity% | rank_flip% | GPU(s) | n_deep |",
        "|-------|-----|------------|-----------|-----------|-----------|------------|--------|--------|",
    ])

    for sid in STRATEGIES:
        agg = aggregates.get(sid, {})
        if not agg.get("n_positions"):
            lines.append(f"| {sid} | {STRATEGY_LABELS[sid]} | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {sid} | {agg['strategy_name']} | "
            f"{agg['best_move_match_pct']:.1f} | "
            f"{agg['point_loss_delta_mean']:.2f} | "
            f"{agg['point_loss_delta_p95']:.2f} | "
            f"{agg['severity_match_pct']:.1f} | "
            f"{agg['rank_flip_pct']:.1f} | "
            f"{agg['gpu_total']:.0f} | "
            f"{agg['n_deep']} |"
        )

    if redundant:
        lines.extend(["", "### Stratégies redondantes", ""])
        for a, b in redundant:
            lines.append(
                f"- **{a}** et **{b}** : résultats quasi identiques "
                f"({STRATEGY_LABELS[a]} ≈ {STRATEGY_LABELS[b]})"
            )

    lines.extend([
        "",
        "## Où S1 gaspille du GPU",
        "",
    ])
    s1 = aggregates.get("S1", {})
    s3 = aggregates.get("S3", {})
    wasted = 0
    for c in s1.get("comparisons", []):
        s3c = next(
            (x for x in s3.get("comparisons", []) if x["move_number"] == c["move_number"]),
            None,
        )
        if s3c and s3c["point_loss_delta"] < 0.3 and c["point_loss_delta"] > 0.5:
            wasted += 1
    lines.append(
        f"- ~{wasted} positions où S3 (skip obvious) matche l'oracle "
        f"aussi bien que S1 avec moins de GPU"
    )
    s1_gpu = s1.get("gpu_total", 0)
    s3_gpu = s3.get("gpu_total", 0)
    if s1_gpu and s3_gpu:
        saved = 100 * (1 - s3_gpu / s1_gpu)
        lines.append(f"- S3 économise ~{saved:.0f}% de temps GPU vs S1")

    if examples:
        lines.extend(["", "## Exemples concrets", ""])
        for ex in examples:
            lines.append(
                f"- **Coup #{ex['move_number']}** ({ex['played']}) : "
                f"ref {ex['ref_pl']:.1f}pt ({ex['ref_sev']}), "
                f"S1 Δ={ex['s1_delta']:.1f}pt"
                + (", S3 suffit" if ex.get("s3_ok") else "")
                + (", S5 suffit" if ex.get("s5_ok") else "")
            )

    lines.extend([
        "",
        "## Proposition config.yaml",
        "",
        "```yaml",
        config_yaml_proposal(rec_sid),
        "```",
        "",
        "## Verdict",
        "",
        f"**{rec_sid} — {STRATEGY_LABELS[rec_sid]}**",
        "",
        rec_reason,
        "",
        "> Ne pas implémenter en prod avant validation manuelle de ce rapport.",
    ])

    return "\n".join(lines)


def write_csv(aggregates: dict[str, dict]) -> None:
    rows = []
    for sid in STRATEGIES:
        agg = aggregates.get(sid, {})
        rows.append({
            "strategy_id": sid,
            "strategy_name": STRATEGY_LABELS[sid],
            "n_positions": agg.get("n_positions", 0),
            "n_deep": agg.get("n_deep", 0),
            "best_move_match_pct": round(agg.get("best_move_match_pct", 0), 2),
            "point_loss_delta_mean": round(agg.get("point_loss_delta_mean", 0), 3),
            "point_loss_delta_p95": round(agg.get("point_loss_delta_p95", 0), 3),
            "severity_match_pct": round(agg.get("severity_match_pct", 0), 2),
            "rank_flip_pct": round(agg.get("rank_flip_pct", 0), 2),
            "gpu_total": round(agg.get("gpu_total", 0), 1),
        })
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rapport labo deep visits")
    parser.add_argument("--csv", action="store_true", help="Exporter aussi le CSV")
    args = parser.parse_args()

    game_ids = load_selected_game_ids()

    for gid in game_ids:
        if not ref_path(gid).exists():
            print(f"ERREUR : oracle manquant pour #{gid}")
            sys.exit(1)

    print("=== Labo Deep Visits — Rapport ===\n")

    aggregates: dict[str, dict] = {}
    for sid in STRATEGIES:
        aggregates[sid] = aggregate_strategy(sid, game_ids)
        agg = aggregates[sid]
        if agg.get("n_positions"):
            print(
                f"  {sid}: {agg['best_move_match_pct']:.0f}% best_move, "
                f"Δloss {agg['point_loss_delta_mean']:.2f}, "
                f"GPU {agg['gpu_total']:.0f}s"
            )

    report = generate_report(aggregates, game_ids)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nRapport : {REPORT_PATH}")

    if args.csv:
        write_csv(aggregates)
        print(f"CSV     : {CSV_PATH}")


if __name__ == "__main__":
    main()
