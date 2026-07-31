# Setup d'analyse optimal — Rapport comparatif

*Genere le 2026-07-29 10:58 UTC*

Croisement des labos **deep visits** (milieu/fin, 92 positions, oracle 4000v) et **fuseki** (coups joueur 1-30, 45 positions, oracle 4000v). Aucun test GPU supplementaire.

## Verdict

**Setup recommande : Quick + fuseki F6** (`P2_quick_fuseki_f6`)

Quick 400v + fuseki cible (erreurs 1000v, ambigus 2000v), pas de deep milieu

- best_move theorique pondere : **78.4%**
- top3 fuseki : **68.9%**
- GPU estime/partie : **~149s** (vs ~198s actuel)

### vs setup actuel (P0)

| Metrique | Actuel | Recommande | Delta |
|----------|--------|------------|-------|
| best_move (theo.) | 75.4% | 78.4% | +3.0 |
| flip rank | 26.1% | 17.5% | -8.5 |
| GPU/partie | ~198s | ~149s | -49s |

## Principe : 3 passes, 3 roles

```
Passe 1 — QUICK 400v     Toute la partie      Detection + base de donnees
Passe 2 — FUSEKI F6      Tes coups 1-30       Etude ouverture (erreurs + ambigus)
Passe 3 — MILIEU OFF     Coups 31+            Quick suffit (labo deep)
```

Le deep milieu actuel (S1) **degrade** la qualite vs quick seul :
- best_move 73,9% vs 76,1% (oracle)
- rank_flip 33,7% vs 17,4%
- 175s GPU / 3 parties gaspilles

Le fuseki **beneficie** d'une passe dediee :
- F6 : best_move 82,2% (+4,4 vs quick), top3 68,9% (+6,7), ~9s/partie

## Matrice des pipelines

| Pipeline | Fuseki | Milieu | best_move% | top3% | dloss | flip% | GPU~ |
|----------|--------|--------|------------|-------|-------|-------|------|
| **Quick + fuseki F6** | F6 | S0 | 78.4 | 68.9 | 0.34 | 17.5 | 149s |
| Quick + F6 + top3 milieu | F6 | S4 | 78.4 | 68.9 | 0.35 | 18.2 | 157s |
| Quick + fuseki F5 | F5 | S0 | 78.4 | 64.4 | 0.34 | 20.0 | 141s |
| Quick seul | F0 | S0 | 76.7 | 62.2 | 0.34 | 15.9 | 140s |
| Quick + F6 + tiered milieu | F6 | S5 | 77.7 | 68.9 | 0.36 | 21.6 | 151s |
| Quick + fuseki F3 | F3 | S0 | 78.4 | 66.7 | 0.34 | 26.7 | 156s |
| Actuel (prod) | F0 | S1 | 75.4 | 62.2 | 0.39 | 26.1 | 198s |

## Validation sur partie #109 (seule partie commune aux 2 labos)

| Pipeline | Pos. | best_move | top3 | fuseki bm | milieu bm |
|----------|------|-----------|------|-----------|-----------|
| Quick + fuseki F6 | 47 | 72% | 57% | 87% | 66% |
| Quick + F6 + top3 milieu | 47 | 72% | 60% | 87% | 66% |
| Quick + fuseki F5 | 47 | 72% | 55% | 87% | 66% |
| Quick seul | 47 | 72% | 57% | 87% | 66% |
| Quick + F6 + tiered milieu | 47 | 72% | 57% | 87% | 66% |
| Quick + fuseki F3 | 47 | 74% | 57% | 93% | 66% |
| Actuel (prod) | 47 | 74% | 60% | 87% | 69% |

## Regle par coup — decision tree

```
Pour chaque coup de la partie :

  [1] Quick 400v (obligatoire, tous les coups)
        |
        v
  move_number <= 30 ET couleur joueur ?
    |oui                          |non
    v                             v
  point_loss > 0.5               FIN (garder quick)
  OU severity != ok ?                  (milieu/fin : quick suffit)
    |oui       |non
    v          v
  ecart #1-#2   FIN (quick OK)
  < 0.5 pt ?
    |oui    |non
    v       v
  2000v   1000v
  (F6)    (F6)
```

## Config.yaml cible

```yaml
katago:
  quick_max_visits: 400
  deep_max_visits: 1000      # inutilise milieu — reserve fuseki
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake  # desactiver deep milieu en pratique

opening_analysis:
  max_moves: 30
  max_visits: 1000
  hard_max_visits: 2000
  min_point_loss: 0.5
  ambiguous_gap: 0.5         # ecart #1-#2 pour 2000v
  player_moves_only: true

analysis:
  mark_opening_on_complete: false
  deep_middle_enabled: false  # labo : quick >= deep milieu
```

## Ce qu'on abandonne

| Element | Raison |
|---------|--------|
| Deep S1 milieu (mistake+ 1000v) | Pire que quick vs oracle, 33% rank_flip |
| opening via quick seul | -4,4% best_move fuseki vs oracle |
| F3/F4 (tous coups fuseki/adversaire) | GPU x3-x5 pour gain top3 marginal |
| S4 top3 milieu (option P4) | +24s GPU, best_move = quick seul (76,1%) |

## Sources

- `data/lab/deep_visits_report.md` — parties #109, #108, #106
- `data/lab/fuseki/fuseki_report.md` — parties #109, #101, #99
- `ROADMAP.md` section Decisions labo GPU

## Limites

- Oracle 4000v != verite absolue ; echantillon 3+3 parties, 3d KGS rapide
- Partie #109 seule validation croisee exacte
- Estimation GPU basee sur debits labo reels (200 v/s configure)
- Human SL / patterns non mesures ici (enrichissement post-analyse)