# Labo Deep Visits — Rapport

*Généré le 2026-07-29 10:10 UTC*

## Résumé exécutif

**Recommandation : S5 (tiered_loss)**

S5 (tiered_loss) bat S1 au ratio qualité/temps : best_move 75% vs 74%, GPU 4s vs 175s.

## Parties sélectionnées

| ID | Adversaire | Date | Coups | Erreurs | Zone grise | Raison |
|----|------------|------|-------|---------|------------|--------|
| 109 | wade | 2026-7 | 143 | 32 | 16 | récence, mix sévérités, zone grise (16), longueur idéale |
| 108 | Alkapone | 2026-7 | 163 | 26 | 14 | récence, mix sévérités, zone grise (14), longueur idéale |
| 106 | Zarybot2 | 2026-7 | 112 | 13 | 9 | récence, mix sévérités, zone grise (9), longueur idéale |

### Top 10 candidates (avant sélection)

| ID | Adv | Date | Coups | Err | Big | Gris | Statut |
|----|-----|------|-------|-----|-----|------|--------|
| 109 | wade | 2026-7 | 143 | 32 | 6 | 16 | score=202869 * |
| 108 | Alkapone | 2026-7 | 163 | 26 | 7 | 14 | score=202869 * |
| 106 | Zarybot2 | 2026-7 | 112 | 13 | 1 | 9 | score=202869 * |
| 103 | Zarybot2 | 2026-7 | 141 | 16 | 3 | 10 | score=202869  |
| 101 | Shaomi | 2026-7 | 168 | 40 | 8 | 22 | score=202869  |
| 100 | Shaomi | 2026-7 | 149 | 23 | 4 | 11 | score=202869  |
| 99 | Shaomi | 2026-7 | 145 | 19 | 4 | 7 | score=202869  |
| 96 | Grizzly | 2026-7 | 153 | 20 | 4 | 10 | score=202866  |
| 89 | LifeDeath | 2026-7 | 136 | 22 | 8 | 10 | score=202859  |
| 138 | sunnyboy | 2026-6 | 113 | 12 | 1 | 6 | score=202859  |

## Tableau comparatif

| Strat | Nom | best_move% | Δloss moy | Δloss p95 | severity% | rank_flip% | GPU(s) | n_deep |
|-------|-----|------------|-----------|-----------|-----------|------------|--------|--------|
| S0 | quick_only | 76.1 | 0.39 | 1.24 | 71.7 | 17.4 | 0 | 0 |
| S1 | current | 73.9 | 0.47 | 1.42 | 69.6 | 33.7 | 175 | 38 |
| S2 | flat_1000 | 73.9 | 0.56 | 1.52 | 71.7 | 33.7 | 23 | 38 |
| S3 | skip_obvious | 72.8 | 0.49 | 1.47 | 68.5 | 34.8 | 22 | 39 |
| S4 | top3_loss_2000 | 76.1 | 0.40 | 1.30 | 69.6 | 18.5 | 24 | 9 |
| S5 | tiered_loss | 75.0 | 0.43 | 1.32 | 69.6 | 23.9 | 4 | 12 |
| S6 | top2_ambiguous_2000 | 73.9 | 0.54 | 1.47 | 70.7 | 32.6 | 15 | 38 |

## Où S1 gaspille du GPU

- ~3 positions où S3 (skip obvious) matche l'oracle aussi bien que S1 avec moins de GPU
- S3 économise ~87% de temps GPU vs S1

## Exemples concrets

- **Coup #144** (M18) : ref 1.5pt (inaccuracy), S1 Δ=2.1pt
- **Coup #114** (J9) : ref 10.0pt (mega_blunder), S1 Δ=1.8pt
- **Coup #128** (N9) : ref 4.7pt (blunder), S1 Δ=1.6pt
- **Coup #136** (J11) : ref 1.1pt (inaccuracy), S1 Δ=1.5pt
- **Coup #78** (M9) : ref 2.1pt (mistake), S1 Δ=1.5pt

## Proposition config.yaml

```yaml
katago:
  deep_max_visits: 1000
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake
  # + paliers loss 5–12 / 12–25 en code
```

## Verdict

**S5 — tiered_loss**

S5 (tiered_loss) bat S1 au ratio qualité/temps : best_move 75% vs 74%, GPU 4s vs 175s.

> Ne pas implémenter en prod avant validation manuelle de ce rapport.