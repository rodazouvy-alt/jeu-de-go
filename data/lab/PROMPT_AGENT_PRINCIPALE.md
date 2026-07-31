# Prompt — Agent principale (implémentation setup analyse)

Copier-coller le bloc ci-dessous dans une nouvelle conversation avec l'agent principale.

---

## Prompt à copier

```
Contexte : labos GPU KataGo terminés sur le projet Go Progress (Rodolphe, 3d KGS, RTX 3060).

Lis d'abord le rapport complet :
  C:\Users\rodaz\jeu de go\data\lab\RAPPORT_COMPLET_LABO_GPU.md

Rapports secondaires si besoin :
  - data/lab/analysis_setup_report.md  (synthèse pipelines)
  - data/lab/deep_visits_report.md       (labo milieu/fin)
  - data/lab/fuseki/fuseki_report.md     (labo fuseki)
  - ROADMAP.md section "Décisions labo GPU"

MISSION : implémenter le setup d'analyse validé par les labos.

Verdict labo (ne pas re-discuter sans nouvelles données) :
  1. Quick 400v — toute la partie (inchangé)
  2. Fuseki F6 — tes coups 1-30 après quick :
       - point_loss > 0.5 ou severity != ok → re-analyser
       - écart score #1−#2 < 0.5 pt (quick) → 2000v
       - sinon → 1000v
       - coups joueur seulement, move_number ≤ 30
  3. Deep milieu OFF — désactiver _analyze_deep() mistake+ (quick suffit, labo prouvé)

Fichiers probables :
  - src/goprogress/opening_analysis.py  (règle F6)
  - src/goprogress/analyzer.py          (deep_middle_enabled)
  - config.yaml                         (nouveaux paramètres)

Config cible (extrait) :
  opening_analysis:
    max_moves: 30
    max_visits: 1000
    hard_max_visits: 2000
    min_point_loss: 0.5
    ambiguous_gap: 0.5
    player_moves_only: true
  analysis:
    mark_opening_on_complete: false
    deep_middle_enabled: false

Contraintes :
  - NE PAS modifier data/db/go_progress.db directement
  - Réutiliser point_loss_from_stored_scores() / effective_move_quality()
  - Pas de parallélisme GPU KataGo
  - Diff minimal, conventions existantes
  - Proposer backfill opening (--force) sans lancer sur toute la collection sans accord

Livrables attendus :
  1. Code + config.yaml
  2. Résumé des changements
  3. Commande pour tester sur 1-2 parties récentes
```

---

## Variante courte

```
Implémente le setup analyse validé par les labos GPU.
Rapport : data/lab/RAPPORT_COMPLET_LABO_GPU.md

Quick 400v partout + fuseki F6 (coups joueur 1-30, erreurs 1000v / ambigus 2000v) + deep milieu OFF.
Modifier opening_analysis.py, analyzer.py, config.yaml. Ne pas toucher la DB prod.
```
