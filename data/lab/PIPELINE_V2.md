# Pipeline analyse v2 — implémenté (juillet 2026)

Basé sur les labos GPU — voir `RAPPORT_COMPLET_LABO_GPU.md`.

## Comportement

1. **Quick 400v** — toute la partie (joueur + adversaire)
2. **Fuseki F6** — tes coups 1-30 : erreurs à 1000v, ambigus à 2000v
3. **Deep milieu OFF** — quick conservé pour coups 31+
4. **Human SL, patterns, backfill adversaire** — inchangés

## Pipeline lot optimise (RTX 3060 12 Go)

Un seul moteur GPU actif a la fois (evite les blocages CUDA) :
1. **Phase quick** : enchaîne les quick du lot (moteur garde en memoire)
2. **Phase post** : fuseki F6 + Human SL pour chaque partie du lot

Configs : `katago_quick_parallel.cfg`, `katago_deep_parallel.cfg`

Desactiver : `analysis.parallel_pipeline: false` dans `config.yaml`

## Re-analyser la collection

### Option A — garder le quick existant, refaire fuseki seulement

```powershell
python -m goprogress prepare-v2-analysis --since-year 2024
python -m goprogress reanalysis --since-year 2024 --loop
```

### Option B — tout repartir de zéro sur 2024+ (~2100 parties)

**Étape 1** — double-clic :

```
scripts\quotidien\run_prepare_v2_full.bat
```

**Étape 2** — lancer l'analyse (laisser tourner plusieurs jours) :

```
scripts\quotidien\run_analysis_loop.bat
```

**Étape 3** — quand la boucle est finie :

```
scripts\quotidien\run_finalize_reanalysis.bat
```

Ou en ligne de commande :

```powershell
python -m goprogress prepare-v2-analysis --full --yes --since-year 2024
python -m goprogress reanalysis --since-year 2024 --loop
```

### Test sur 1 partie

```powershell
python -m goprogress analyze --limit 1
python -m goprogress opening-analysis --limit 1 --force
```

## Config (`config.yaml`)

- `analysis.deep_middle_enabled: false`
- `analysis.mark_opening_on_complete: false`
- `opening_analysis.strategy: f6`
