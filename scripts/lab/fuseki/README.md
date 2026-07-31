# Labo Fuseki — Guide

Compare les strategies d'analyse fuseki (coups **joueur** 1-30) vs oracle 4000v.

## Execution

```powershell
cd "C:\Users\rodaz\jeu de go"
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"

python scripts/lab/fuseki/00_setup.py
python scripts/lab/fuseki/01_oracle_ref.py
python scripts/lab/fuseki/02_run_strategies.py
python scripts/lab/fuseki/03_generate_report.py --csv
```

## Strategies

| ID | Regle |
|----|-------|
| F0 | quick_only |
| F1 | tous coups 1-30 x 400v |
| F2 | tes coups 1-30 x 800v |
| F3 | tes coups 1-30 x 1000v |
| F4 | tous coups 1-30 x 1000v |
| F5 | erreurs fuseki x 1000v |
| F6 | ambigus x 2000v + erreurs x 1000v |

## Sorties

`data/lab/fuseki/fuseki_report.md`, `fuseki_results.csv`
