# Labo Fuseki — Rapport

*Genere le 2026-07-29 10:54 UTC*

## Resume executif

**Recommandation : F5 (errors_1000)**

F5 (errors_1000) : best_move 82.2% (+4.4 vs quick), top3 64.4% (+2.2), GPU 4s sur 3 parties.

Perimetre : **coups joueur 1-30**, oracle **4000v**, 3 parties recentes.

## Parties

| ID | Adv | Date | Coups fuseki | Err fuseki | Zone grise |
|----|-----|------|--------------|------------|------------|
| 109 | wade | 2026-7 | 15 | 3 | 2 |
| 101 | Shaomi | 2026-7 | 15 | 4 | 4 |
| 99 | Shaomi | 2026-7 | 15 | 4 | 4 |

## Tableau comparatif

| ID | Nom | best_move% | top3% | dloss moy | dloss p95 | severity% | flip% | GPU(s) | n_deep |
|----|-----|------------|-------|-----------|-----------|-----------|-------|--------|--------|
| F0 | quick_only | 77.8 | 62.2 | 0.26 | 1.30 | 84.4 | 13.3 | 0 | 0 |
| F1 | opening_400_all | 82.2 | 64.4 | 0.46 | 1.31 | 82.2 | 31.1 | 161 | 90 |
| F2 | player_800 | 77.8 | 62.2 | 0.29 | 1.30 | 80.0 | 28.9 | 80 | 45 |
| F3 | player_1000 | 82.2 | 66.7 | 0.25 | 0.87 | 82.2 | 42.2 | 50 | 45 |
| F4 | all_turns_1000 | 77.8 | 68.9 | 0.24 | 0.81 | 84.4 | 31.1 | 133 | 90 |
| F5 | errors_1000 | 82.2 | 64.4 | 0.27 | 1.30 | 82.2 | 24.4 | 4 | 11 |
| F6 | ambig_2000_rest_1000 | 82.2 | 68.9 | 0.26 | 1.30 | 84.4 | 17.8 | 28 | 11 |

## Interpretation

- **F0 (quick)** : 77.8% best_move, 62.2% top3 — baseline sans GPU supplementaire
- **top3%** : stabilite des 3 meilleurs coups (crucial pour etude joseki)
- Voir `ROADMAP.md` section Decisions labo GPU pour le contexte milieu/fin

## Proposition config.yaml

```yaml
opening_analysis:
  max_moves: 30
  max_visits: 1000  # ajuster selon F5

analysis:
  mark_opening_on_complete: false
```

## Verdict

**F5 — errors_1000**

F5 (errors_1000) : best_move 82.2% (+4.4 vs quick), top3 64.4% (+2.2), GPU 4s sur 3 parties.