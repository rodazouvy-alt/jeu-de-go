# Rapport complet — Labos GPU KataGo (juillet 2026)

**Joueur** : Rodolphe (3d KGS)  
**Matériel** : RTX 3060, KataGo 28b (`E:/lizzie/KataGo28b.gz`)  
**DB labo** : `data/lab/goprogress_lab.db` (copie prod, non modifiée en prod)  
**Date** : 29 juillet 2026  
**Statut** : labos terminés — **rien implémenté en prod** (`analyzer.py`, `opening_analysis.py` intacts)

---

## 1. Résumé exécutif

Deux labos + une synthèse croisée ont permis de définir le **meilleur setup d'analyse par coup** sur une partie complète.

### Verdict final

| Passe | Action | Visits | Statut prod actuel |
|-------|--------|--------|-------------------|
| **1. Quick** | Toute la partie | 400v | ✅ En place |
| **2. Fuseki F6** | Tes coups 1–30 (erreurs + ambigus) | 1000v / 2000v | ❌ Absent (quick seul + `mark_opening_on_complete: true`) |
| **3. Deep milieu** | Coups 31+ suspects | — | ❌ **À désactiver** (S1 actuel dégrade vs quick) |

### Gains attendus vs setup actuel

| Métrique | Actuel | Cible (Quick + F6) | Delta |
|----------|--------|-------------------|-------|
| best_move vs oracle 4000v | 75,4 % | **78,4 %** | +3,0 pt |
| rank_flip (stabilité classement) | 26,1 % | **17,5 %** | −8,5 pt |
| top3 fuseki (variations joseki) | 62,2 % | **68,9 %** | +6,7 pt |
| GPU / partie | ~198 s | **~149 s** | −49 s (~25 %) |

**En une phrase** : garder le quick partout, ajouter une passe fuseki ciblée F6, supprimer le deep milieu mistake+.

---

## 2. Méthodologie commune

### Oracle de référence
- **4000 visits** (`config/katago_lab_ref.cfg`)
- Métriques vs oracle : `best_move_match`, `point_loss_delta`, `severity_match`, `rank_flip`
- Recalcul `point_loss` via `point_loss_from_stored_scores()` (bug corrigé avant labos)

### Contraintes
- 1 processus KataGo à la fois (RTX 3060)
- DB prod `data/db/go_progress.db` jamais modifiée
- 3 parties pertinentes par labo (récentes, ≥80 coups, ≥5 erreurs joueur)

### Fichiers labo

```
data/lab/
  goprogress_lab.db              # copie DB
  deep_visits_report.md          # labo 1
  deep_visits_results.csv
  ref_game_{id}.json             # oracle deep
  strategy_S{0-6}_game_{id}.json
  fuseki/
    fuseki_report.md             # labo 2
    fuseki_results.csv
    ref_fuseki_{id}.json
    strategy_F{0-6}_game_{id}.json
  analysis_setup_report.md       # synthèse croisée
  analysis_setup_matrix.csv
  RAPPORT_COMPLET_LABO_GPU.md    # ce document
  PROMPT_AGENT_PRINCIPALE.md     # prompt implémentation

scripts/lab/
  00_setup_lab.py … 03_generate_report.py    # deep visits
  fuseki/00_setup.py … 03_generate_report.py # fuseki
  generate_setup_report.py                   # synthèse (sans GPU)
```

---

## 3. Labo 1 — Deep visits (milieu / fin de partie)

### Question
Comment allouer les visits KataGo en phase **deep** sur les coups suspects (inaccuracy+) ?

### Parties testées
| ID | Adversaire | Coups | Erreurs | Zone grise |
|----|------------|-------|---------|------------|
| 109 | wade | 143 | 32 | 16 |
| 108 | Alkapone | 163 | 26 | 14 |
| 106 | Zarybot2 | 112 | 13 | 9 |

**92 positions** évaluées (inaccuracy+ post-reconcile). Oracle : ~24 min GPU.

### Résultats (vs oracle 4000v)

| Strat | Règle | best_move% | Δloss moy | rank_flip% | GPU (3 parties) |
|-------|-------|------------|-----------|------------|-----------------|
| **S0 quick_only** | Pas de deep | **76,1** | **0,39** | **17,4** | 0 s |
| S1 current (prod) | mistake/blunder→1000v, mega→2000v | 73,9 | 0,47 | 33,7 | **175 s** |
| S2 flat_1000 | Tous mistake+ → 1000v | 73,9 | 0,56 | 33,7 | 23 s |
| S3 skip_obvious | 1,5–15 pt → 1000v | 72,8 | 0,49 | 34,8 | 22 s |
| **S4 top3_loss_2000** | Top 3 pertes/partie → 2000v | **76,1** | 0,40 | 18,5 | 24 s |
| S5 tiered_loss | Paliers 5–12 / 12–25 | 75,0 | 0,43 | 23,9 | 4 s |
| S6 top2_ambiguous_2000 | Top 2 ambigus → 2000v | 73,9 | 0,54 | 32,6 | 15 s |

### Découpage par phase (labo 1, données croisées)

| Zone | S0 quick | S1 actuel | S4 top3×2000 |
|------|----------|-----------|--------------|
| **Fuseki 1–30** (14 pos.) | 92,9 % bm | 92,9 % | 92,9 % |
| **Milieu/fin 31+** (78 pos.) | **73,1 %** | 70,5 % | 73,1 % |

### Conclusions labo 1

1. **Le quick 400v bat le deep 1000v** sur le milieu/fin (meilleur best_move, moins de rank_flip).
2. Le deep actuel **S1** ajoute du bruit : le classement des coups bouge dans 34 % des cas sans rapprocher l'oracle.
3. **S4** (top 3 pires coups × 2000v) égale le quick en qualité pour 7× moins de GPU que S1 — mais **aucun gain** vs quick seul sur best_move global.
4. En fuseki (dans ce labo), le quick est déjà excellent (93 %) — le problème fuseki est traité au labo 2.

**Décision** : ne pas dépenser de GPU en deep milieu/fin. Quick 400v suffit.

---

## 4. Labo 2 — Fuseki (coups joueur 1–30)

### Question
Quelle passe fuseki **systématique** pour bosser l'ouverture (variations joseki, erreurs fuseki) ?

### Parties testées
| ID | Adversaire | Coups fuseki joueur | Erreurs fuseki |
|----|------------|---------------------|----------------|
| 109 | wade | 15 | 3 |
| 101 | Shaomi | 15 | 4 |
| 99 | Shaomi | 15 | 4 |

**45 coups joueur** en zone fuseki. Oracle : ~14 min GPU.

### Résultats (vs oracle 4000v)

| Strat | Règle | best_move% | top3% | Δloss moy | rank_flip% | GPU (3 parties) |
|-------|-------|------------|-------|-----------|------------|-----------------|
| F0 quick_only | Données quick | 77,8 | 62,2 | 0,26 | 13,3 | 0 s |
| F1 opening_400_all | Tous coups 1–30 × 400v | 82,2 | 64,4 | 0,46 | 31,1 | 161 s |
| F2 player_800 | Tes coups × 800v | 77,8 | 62,2 | 0,29 | 28,9 | 80 s |
| F3 player_1000 | Tes coups × 1000v | 82,2 | 66,7 | 0,25 | 42,2 | 50 s |
| F4 all_turns_1000 | Tous coups (2 couleurs) × 1000v | 77,8 | 68,9 | 0,24 | 31,1 | 133 s |
| F5 errors_1000 | Erreurs fuseki × 1000v | 82,2 | 64,4 | 0,27 | 24,4 | **4 s** |
| **F6 ambig_2000_rest_1000** | Ambigus × 2000v + erreurs × 1000v | **82,2** | **68,9** | 0,26 | **17,8** | 28 s |

### Conclusions labo 2

1. Le quick **sous-estime** le fuseki : −4,4 pt best_move vs oracle.
2. **F5** et **F6** atteignent le même best_move (82,2 %) en ne re-analysant que ~11 coups/partie (erreurs).
3. **F6** est optimal pour l'**étude joseki** : top3 à 68,9 % (= F4 qui coûte 5× plus de GPU).
4. Re-analyser **tous** les coups joueur (F3) ou **l'adversaire** (F4) : GPU gaspillé, flip élevé (F3 : 42 %).

**Décision fuseki** : stratégie **F6** en prod.

### Règle F6 (à implémenter)

Pour chaque **coup joueur** avec `move_number ≤ 30`, après le quick :

```
SI point_loss > 0.5 OU severity != 'ok' :
    SI écart score #1 − #2 < 0.5 pt (données quick) :
        → 2000 visits
    SINON :
        → 1000 visits
SINON :
    → garder quick (0 visit supplémentaire)
```

Environ **~4 coups deep/partie** en pratique (erreurs fuseki), ~10–30 s GPU.

---

## 5. Synthèse croisée — Setup optimal par coup

Rapport détaillé : `data/lab/analysis_setup_report.md`  
Généré par : `python scripts/lab/generate_setup_report.py` (sans GPU)

### 7 pipelines comparés

| Pipeline | Description | best_move% | top3% | GPU~ | Recommandation |
|----------|-------------|------------|-------|------|----------------|
| **P2 Quick + F6** | Quick + fuseki F6, pas de deep milieu | **78,4** | **68,9** | 149 s | ✅ **Retenu** |
| P4 Quick + F6 + S4 | + top 3 pertes milieu × 2000v | 78,4 | 68,9 | 157 s | Pas de gain vs P2 |
| P3 Quick + F5 | Fuseki minimal GPU | 78,4 | 64,4 | 141 s | Fallback GPU contraint |
| P1 Quick seul | Aucune passe supplémentaire | 76,7 | 62,2 | 140 s | Insuffisant fuseki |
| P0 Actuel (prod) | Quick + deep S1 + opening via quick | 75,4 | 62,2 | 198 s | ❌ À remplacer |

### Arbre de décision par coup

```
[1] Quick 400v — TOUS les coups (obligatoire)
         │
         ▼
    move_number ≤ 30 ET couleur joueur ?
         │                    │
        oui                  non
         │                    └──► FIN (garder quick)
         ▼
    point_loss > 0.5 OU severity ≠ ok ?
         │              │
        oui             non
         │              └──► FIN (quick OK en fuseki)
         ▼
    écart #1−#2 < 0.5 pt ?
         │         │
        oui        non
         │         │
      2000v     1000v
```

---

## 6. Setup actuel vs cible

### Actuel (`config.yaml` + code)

```yaml
katago:
  quick_max_visits: 400
  deep_max_visits: 1000
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake

opening_analysis:
  max_moves: 30
  max_visits: 400          # = quick, pas de passe dédiée

analysis:
  mark_opening_on_complete: true   # skip opening-analysis
```

**Comportement** :
1. Quick 400v sur toute la partie
2. Deep `_analyze_deep()` sur mistake+ (1000v) et mega (2000v) — **milieu + fuseki mélangés**
3. Opening marquée « faite » sans passe fuseki dédiée

### Cible (post-implémentation)

```yaml
katago:
  quick_max_visits: 400
  deep_max_visits: 1000
  deep_hard_max_visits: 2000
  deep_rerun_min_severity: mistake   # conservé mais deep milieu désactivé

opening_analysis:
  max_moves: 30
  max_visits: 1000
  hard_max_visits: 2000
  min_point_loss: 0.5
  ambiguous_gap: 0.5
  player_moves_only: true

analysis:
  mark_opening_on_complete: false
  deep_middle_enabled: false         # nouveau flag
```

**Comportement cible** :
1. Quick 400v — toute la partie (inchangé)
2. `OpeningAnalyzer` — règle F6 sur tes coups 1–30 (systématique après quick)
3. `_analyze_deep()` — **désactivé** ou no-op pour milieu/fin
4. Human SL + patterns — inchangés (enrichissement post-analyse)

---

## 7. Implémentation prod — périmètre suggéré

### Fichiers à modifier

| Fichier | Changement |
|---------|------------|
| `src/goprogress/opening_analysis.py` | Règle F6 : visits 1000/2000 selon erreur + ambiguïté ; coups joueur seulement |
| `src/goprogress/analyzer.py` | Désactiver `_analyze_deep()` si `deep_middle_enabled: false` |
| `config.yaml` | Nouveaux paramètres `opening_analysis` + `analysis.deep_middle_enabled` |
| `scripts/quotidien/run_analysis_loop.bat` | S'assurer que opening-analysis tourne après quick |

### Ne pas modifier
- `data/db/go_progress.db` (prod)
- Seuils `thresholds` (inchangés)
- Human SL, patterns, dashboard (hors scope labo)

### Backfill suggéré
1. `mark_opening_on_complete: false`
2. Relancer `opening-analysis` sur parties `analyzed_quick=1` avec `--force`
3. Ne pas relancer deep sur le backlog milieu

---

## 8. Ce qu'on abandonne (justifié par données)

| Élément | Raison mesurée |
|---------|----------------|
| Deep S1 milieu (mistake+ 1000v) | 73,9 % bm vs 76,1 % quick ; flip 33,7 % ; 175 s GPU / 3 parties |
| Opening via quick seul | 77,8 % bm fuseki vs 82,2 % avec passe dédiée |
| F3 — tous coups fuseki 1000v | Même bm que F5/F6, flip 42 %, 50 s GPU |
| F4 — tous coups + adversaire 1000v | 77,8 % bm, 133 s GPU |
| S4 top3 milieu (option) | = quick en bm global, +24 s inutiles |
| Oracle 4000v en prod | Référence labo uniquement — trop lent |

---

## 9. Limites et réserves

- **Oracle 4000v** ≠ vérité absolue ; c'est la meilleure référence disponible sur 3060.
- **Échantillon** : 3 parties par labo, joueur 3d KGS rapide 19×19 — pas généralisable à tout rang/format sans re-calibrage.
- **Validation croisée exacte** : seule la partie **#109** est commune aux deux labos.
- **Human SL / patterns** : non mesurés dans ces labos (enrichissement aval inchangé).
- **Adversaire** : analyse des coups adverses hors scope fuseki F6 (quick suffit).
- **Estimation GPU** : basée sur ~200 visits/s configuré ; varie selon charge GPU.

---

## 10. Budget GPU par partie (estimation)

| Étape | Actuel | Cible |
|-------|--------|-------|
| Quick 400v (~70 coups) | ~140 s | ~140 s |
| Deep milieu (~13 coups mistake+) | ~58 s | **0 s** |
| Fuseki F6 (~4 coups) | 0 s | ~10 s |
| **Total** | **~198 s** | **~149 s** |

Sur une collection de 200 parties : **~2,7 h GPU économisées** vs setup actuel, avec une meilleure qualité fuseki.

---

## 11. Prochaines étapes

1. ✅ Labos terminés et validés par Rodolphe
2. ✅ **Implémenté** — pipeline v2 : quick + fuseki F6, deep milieu OFF
3. ⬜ Lancer `prepare-v2-analysis` puis boucle `reanalysis` sur la collection
4. ⬜ Régénérer dashboard / rapport après re-analyse
5. ⬜ Backfill opponent + Human SL inchangés (conservés)

---

## 12. Index des rapports

| Document | Contenu |
|----------|---------|
| **`data/lab/RAPPORT_COMPLET_LABO_GPU.md`** | Ce document — synthèse complète |
| `data/lab/analysis_setup_report.md` | Matrice pipelines + config cible |
| `data/lab/deep_visits_report.md` | Détail labo deep S0–S6 |
| `data/lab/fuseki/fuseki_report.md` | Détail labo fuseki F0–F6 |
| `data/lab/PROMPT_AGENT_PRINCIPALE.md` | Prompt pour l'agent d'implémentation |
| `ROADMAP.md` | Section « Décisions labo GPU » |

---

*Rapport validé en labo — ne pas déployer en prod sans relecture du diff code.*
