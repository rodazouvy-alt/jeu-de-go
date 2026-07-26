# Analyse & Progression Go

Outil **100 % local** pour collecter vos parties KGS, les analyser avec KataGo, et repérer vos erreurs récurrentes.

Joueur configuré : **Rodolphe** (3 dan)  
KataGo : `E:\lizzie\katago.exe` + modèle 28b

## Démarrage rapide

### 1. Installation (une seule fois)

Double-cliquez sur **`setup.bat`**

### 2. Test complet (Phase 0)

Double-cliquez sur **`run_test.bat`**

Cela va :
1. Télécharger 5 parties récentes depuis KGS
2. Analyser 3 parties avec KataGo (~1 min/partie)
3. Générer un rapport HTML et l'ouvrir dans le navigateur

### 3. Commandes utiles

| Fichier | Action |
|---------|--------|
| `run_sync.bat` | Télécharger des parties KGS |
| `run_sync.bat --year 2026 --month 7 --zip` | Archive ZIP du mois entier |
| `run_sync.bat --all-months --zip` | Tout l'historique (long !) |
| `run_analyze.bat` | Analyser les parties en attente |
| `run_analyze.bat --limit 10` | Analyser 10 parties |
| `start.bat` | Dashboard local http://127.0.0.1:8787 |

### 4. Rapport HTML

Après analyse, ouvrez :
`data\reports\latest.html`

## Structure du projet

```
jeu de go/
├── config.yaml          ← réglages (pseudo KGS, chemins KataGo, seuils 3d)
├── config/katago_quick.cfg  ← config KataGo optimisée RTX 3060
├── data/
│   ├── sgf/             ← parties téléchargées
│   ├── analysis/        ← résultats JSON par partie
│   ├── db/go_progress.db ← base SQLite (stats, erreurs)
│   └── reports/         ← rapports HTML
├── src/goprogress/      ← code Python
├── setup.bat
├── run_test.bat
└── start.bat
```

## Ce que vous apprenez en regardant le code

| Fichier | Rôle pédagogique |
|---------|------------------|
| `kgs_sync.py` | Télécharger des données depuis un site web (scraping poli) |
| `katago.py` | Parler à un programme externe (KataGo) via stdin/stdout |
| `sgf_parse.py` | Lire un format de fichier (SGF = une partie de Go) |
| `db.py` | Stocker des données dans SQLite (comme un Excel structuré) |
| `analyzer.py` | Logique métier : calculer la perte en points par coup |
| `report.py` | Générer une page web à partir des données |

## Notes importantes

- **Fermez Lizzie** pendant l'analyse batch (le GPU est partagé)
- La collecte complète de 20 ans peut tourner plusieurs jours en arrière-plan
- Aucune donnée ne quitte votre PC (sauf requêtes vers KGS pour télécharger les SGF)

## Feuille de route

Voir [ROADMAP.md](ROADMAP.md) pour les phases suivantes (patterns, combats, exercices SRS, dashboard avancé).
