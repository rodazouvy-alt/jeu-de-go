# Labo Deep Visits — Guide d'exécution

Scripts séquentiels pour comparer les stratégies d'allocation GPU en phase deep.
**Ne modifie pas** `data/db/go_progress.db` (prod) — tout passe par `data/lab/`.

## Prérequis

```powershell
cd "C:\Users\rodaz\jeu de go"
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
```

KataGo doit être accessible (`E:/lizzie/katago.exe`, modèle 28b).

## Ordre d'exécution

| Étape | Script | GPU | Durée estimée (3060) |
|-------|--------|-----|----------------------|
| 0 | `00_setup_lab.py` | Non | ~10 s |
| 1 | `01_oracle_ref.py` | Oui | **long** (~30–90 min) |
| 2 | `02_run_strategies.py` | Oui | ~20–60 min |
| 3 | `03_generate_report.py` | Non | ~2 s |

### Étape 0 — Setup

```powershell
python scripts/lab/00_setup_lab.py
```

- Copie la DB prod → `data/lab/goprogress_lab.db`
- `reconcile-scores` sur la copie
- Sélectionne 3 parties → `data/lab/selected_games.json`

Options : `--force` (recopier la DB), `-n 5` (5 parties max).

### Étape 1 — Oracle 4000v

```powershell
python scripts/lab/01_oracle_ref.py
```

Référence haute précision sur tous les coups suspects (inaccuracy+).
Sortie : `data/lab/ref_game_{id}.json` par partie.

Options : `--game-id 42`, `--skip-existing`.

### Étape 2 — Stratégies S0–S6

```powershell
python scripts/lab/02_run_strategies.py
```

| ID | Règle |
|----|-------|
| S0 | quick_only (contrôle, pas de re-run) |
| S1 | current (mistake/blunder→1000v, mega→2000v) |
| S2 | flat_1000 (tous mistake+ → 1000v) |
| S3 | skip_obvious (1.5–15pt → 1000v) |
| S4 | top3_loss_2000 |
| S5 | tiered_loss (5–12→1000, 12–25→2000) |
| S6 | top2_ambiguous_2000 |

Sortie : `data/lab/strategy_{Sx}_game_{id}.json`.

Options : `--strategy S3`, `--skip-existing`.

### Étape 3 — Rapport

```powershell
python scripts/lab/03_generate_report.py --csv
```

Sortie : `data/lab/deep_visits_report.md` (+ CSV optionnel).

## Reprise après interruption

Chaque étape GPU supporte `--skip-existing` pour reprendre là où ça s'est arrêté.

## Fichiers produits

```
data/lab/
  goprogress_lab.db          # copie lab (safe)
  selected_games.json        # 3 parties retenues
  game_candidates.json       # top 10 candidates
  ref_game_{id}.json         # oracle 4000v
  strategy_{Sx}_game_{id}.json
  deep_visits_report.md      # rapport final
  deep_visits_results.csv    # métriques agrégées
```

## Après les tests

Quand les étapes 0–3 sont terminées, envoyer les résultats (ou juste dire « c'est fini »)
pour relecture du rapport et validation avant implémentation dans `analyzer.py`.

## Rapport setup global

Croise deep visits + fuseki (sans GPU) :

```powershell
python scripts/lab/generate_setup_report.py
```

Sortie : `data/lab/analysis_setup_report.md`
