# Analyse & Progression Perso au Go

Feuille de route pour l'analyse automatisée des parties KGS de **Rodolphe** (3 dan),
la détection de patterns d'erreurs, et la génération d'exercices ciblés.

---

## Contexte

| Élément | Détail |
|---------|--------|
| Serveur principal | KGS — pseudo `Rodolphe` |
| Niveau | 3 dan |
| Historique | ~20 ans, énorme volume, 19×19 rapide |
| IA locale | Lizzie + KataGo28b (RTX 3060) |
| École | European Yunguseng Dojang (saison pas encore démarrée) |
| Contrainte infra | **100 % local** — pas de charge sur Cloudflare KV (projet jobhunter) |
| Développement | Entièrement automatisé par l'agent ; l'utilisateur ne code pas |

---

## Principes directeurs

1. **Local-first** : SQLite + fichiers sur disque. Aucune dépendance cloud obligatoire.
2. **Analyse en deux passes** : scan rapide de tout l'historique, puis analyse profonde sur les parties/coups suspects.
3. **Progression incrémentale** : chaque nouvelle partie KGS est collectée et analysée automatiquement.
4. **Actionnable** : chaque insight doit mener à un exercice, un chapitre YD, ou une revue ciblée.
5. **Respect des serveurs** : collecte KGS lente, cache agressif, reprise sur interruption.

---

## Architecture cible

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ANALYSE & PROGRESSION GO                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │  Collecteur  │───►│   Analyseur  │───►│  Agrégateur  │───►│ Exercices │ │
│  │  KGS (SGF)   │    │  KataGo batch│    │  + Patterns  │    │ + SRS     │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│         │                   │                   │                  │        │
│         └───────────────────┴───────────────────┴──────────────────┘        │
│                                    │                                        │
│                            ┌───────▼────────┐                               │
│                            │  SQLite + SGF  │                               │
│                            │  (data/local/) │                               │
│                            └───────┬────────┘                               │
│                                    │                                        │
│                     ┌──────────────┼──────────────┐                         │
│                     ▼              ▼              ▼                         │
│              ┌──────────┐  ┌────────────┐  ┌─────────────┐                  │
│              │ Dashboard│  │ Export SGF │  │ Lien thèmes │                  │
│              │ localhost│  │ KaTrain /  │  │ YD (manuel  │                  │
│              │ :8787    │  │ AI Sensei  │  │ puis auto)  │                  │
│              └──────────┘  └────────────┘  └─────────────┘                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Stack technique prévue**

| Composant | Choix | Raison |
|-----------|-------|--------|
| Langage | Python 3.11+ | Écosystème Go/KataGo mature, scripts batch |
| Base de données | SQLite | Local, zéro config, pas de cloud |
| Moteur IA | KataGo Analysis Engine | Déjà installé, API JSON, batch parallèle |
| Interface | FastAPI + HTML statique sur `localhost:8787` | Pas de déploiement Cloudflare |
| SRS | Algorithme SM-2 maison ou export Anki | Répétition espacée des positions |
| Config KataGo | Chemin vers install Lizzie/KataGo existante | Réutiliser le modèle 28b |

---

## Estimation du volume

Hypothèse conservative pour 20 ans de rapide régulier :

| Métrique | Estimation |
|----------|------------|
| Parties totales | 3 000 – 15 000 |
| Taille SGF | ~50–200 Ko/partie → 150 Mo – 3 Go |
| Analyse rapide (200 visits/coup) | ~30–90 s/partie |
| Analyse profonde (2000 visits/coup) | ~5–15 min/partie |
| Temps total analyse rapide (10k parties) | ~3–10 jours GPU continu |
| Temps total analyse profonde (sélection 20 %) | ~2–5 jours GPU supplémentaires |

**Stratégie** : ne pas tout analyser en profondeur d'un coup. Pass 1 identifie les 20 % de parties les plus « riches en erreurs ».

---

## Phases du projet

### Phase 0 — Fondations (semaine 1)

**Objectif** : structure du projet, config, premier test bout-en-bout sur 5 parties.

- [ ] Arborescence `data/sgf/`, `data/analysis/`, `data/db/go_progress.db`
- [ ] Fichier `config.yaml` : pseudo KGS, chemins KataGo/Lizzie, paramètres GPU
- [ ] Script de test : 1 SGF → KataGo → JSON → rapport HTML minimal
- [ ] Documentation `README.md` : lancement en un clic (`.bat` Windows)

**Livrable** : « Analyser 5 parties et voir un rapport » fonctionne.

---

### Phase 1 — Collecte KGS (semaines 1–3, tourne en arrière-plan)

**Objectif** : télécharger la majorité des SGF de Rodolphe depuis les archives KGS.

- [ ] Scraper poli des archives `gokgs.com/archives.jsp?user=Rodolphe`
- [ ] Téléchargement par mois quand disponible, sinon partie par partie
- [ ] Cache local + reprise après interruption (fichier d'état `sync_state.json`)
- [ ] Métadonnées extraites : date, adversaire, rang, résultat, handicap, komi
- [ ] Déduplication (même partie téléchargée deux fois)
- [ ] Filtre : 19×19 uniquement (option rapide vs classique si identifiable)
- [ ] Rate limit : 1–3 req/s max, pause nocturne configurable

**Outils de référence** : inspiration [go-archiver](https://github.com/Sven-C/go-archiver), [kgs-rip](https://github.com/sadakatsu/kgs-rip).

**Livrable** : bibliothèque SGF locale indexée en SQLite.

---

### Phase 2 — Pipeline d'analyse KataGo (semaines 2–4)

**Objectif** : analyser chaque coup de chaque partie, stocker les métriques.

#### Pass 1 — Scan rapide (tout l'historique)

- [ ] Wrapper Python autour du KataGo Analysis Engine
- [ ] Config optimisée RTX 3060 : TensorRT, `numAnalysisThreads` élevé, batching
- [ ] Par coup : winrate, score, meilleur coup IA, perte en points (`point_loss`)
- [ ] Flag automatique : blunder si perte ≥ 5 pts, mistake ≥ 2 pts (ajustable par rang 3d)
- [ ] File d'attente avec reprise : analyse incrémentale des nouvelles parties

#### Pass 2 — Analyse profonde (sélective)

- [x] **Labo deep visits (juil. 2026)** — voir section [Décisions labo GPU](#décisions-labo-gpu-juillet-2026)
- [ ] Parties avec ≥ 3 blunders ou perte totale ≥ 15 pts *(révisé : ne plus deep massif)*
- [ ] Coups flaggés en pass 1 : ciblage top-N pertes ou fuseki dédié
- [ ] Variations principales stockées pour étude

**Livrable** : table `moves` avec ~coups × parties analysées, prête pour l'agrégation.

#### Pass 2b — Fuseki systématique (nouveau, juil. 2026)

- [x] **Labo fuseki** — `scripts/lab/fuseki/` + `data/lab/fuseki/fuseki_report.md` *(juil. 2026, terminé)*
- [ ] Implémenter **F6** dans `opening_analysis.py` (erreurs 1000v + ambigus 2000v)
- [ ] `mark_opening_on_complete: false` — forcer passe fuseki sur le backlog
- [ ] Human SL + patterns fuseki déjà en place (seuil 0,5 pt ouverture)

**Livrable** : couverture fuseki 100 %, dashboard « erreurs fuseki » fiable pour l'étude YD.

---

### Phase 3 — Agrégation & détection de patterns (semaines 4–6)

**Objectif** : répondre à « où est-ce que je me plante, et est-ce que ça se répète ? »

#### 3a — Statistiques globales

- [ ] Distribution des pertes par phase de partie (ouverture ≤50, milieu, fin)
- [ ] Perte moyenne par tranche de 10 coups
- [ ] Classement des adversaires où la perte est maximale
- [ ] Évolution temporelle (par année / par mois)
- [ ] Top 50 blunders de toute la carrière

#### 3b — Clustering de patterns

- [ ] Extraction de contexte autour de chaque blunder (fenêtre ±8 coups, zone 15×15)
- [ ] Empreinte positionnelle (hash du plateau local + phase)
- [ ] Regroupement : « ce fuseau revient 23 fois »
- [ ] Catégorisation heuristique des thèmes :

| Thème | Signal de détection |
|-------|---------------------|
| Vie et mort | Groupes ≤ 3 libertés, séquences de capture |
| Invasion | Pierre isolée en territoire adverse |
| Réduction / connexion | Pierre sur la 3e ligne en zone d'influence |
| Tenuki puni | Gros swing après coup ailleurs |
| Fin de partie | Coup > 180, petites pertes cumulées |
| Fuseau / joseki | Divergence des 20 premiers coups vs base joseki |
| Combat | Voir phase 4 |

**Livrable** : rapport « vos 10 faiblesses récurrentes » avec exemples de parties.

---

### Phase 4 — Détection des combats (semaines 6–8)

**Objectif** : isoler les séquences de combat pour analyse dédiée.

> Pas d'intuition humaine — heuristiques + métriques KataGo.

- [ ] Détection de **fenêtre de combat** : ≥ 4 coups consécutifs avec :
  - variance du score > seuil
  - et/ou unsettledness élevée (via katawrap ou calcul ownership)
  - et/ou pierres en atari ou libertés ≤ 4 dans un rayon de 3
- [ ] Classification du type de combat :
  - Course de capture
  - Bataille de ko
  - Réduction dans un groupe fort
  - Sacrifice / échange
- [ ] Score de « qualité du combat » : perte totale dans la fenêtre vs résultat final
- [ ] Rapport : « vos combats les plus coûteux » + taux de réussite par type

**Limite connue** : les combats lents (influence) seront parfois classés en « milieu de partie ». Affinage itératif.

**Livrable** : section dashboard « Combats » + export SGF des séquences.

---

### Phase 5 — Génération d'exercices (semaines 8–10)

**Objectif** : transformer les stats en entraînement concret.

#### 5a — Tsumego personnalisés (depuis vos parties)

- [ ] Extraire position au moment du blunder (avant votre coup)
- [ ] Générer SGF problème : position + bon coup en solution
- [ ] Tag : thème, date, adversaire, perte en points
- [ ] Export vers dossier `exercises/` compatible KaTrain / AI Sensei upload

#### 5b — Répétition espacée (SRS)

- [ ] Algorithme SM-2 : intervalles 1j → 3j → 7j → 21j selon réussite
- [ ] Session quotidienne : 10–20 problèmes tirés de vos erreurs
- [ ] Interface locale « réviser du jour » dans le dashboard
- [ ] Option export deck Anki (.apkg) pour révision mobile

#### 5c — Lien thèmes Yunguseng Dojang

- [ ] Base manuelle initiale : mapping thème → mots-clés chapitres YD
- [ ] Quand un pattern revient ≥ N fois, suggestion : « revoir leçon X »
- [ ] **Après démarrage saison EYD** : intégrer SGF parties de ligue + rapports In-seong
- [ ] Investigation vidéos EYD (stream Vimeo) : **pas de scraping massif** — liens manuels ou métadonnées publiques uniquement, respect ToS

**Livrable** : routine d'étude quotidienne générée automatiquement.

---

### Phase 6 — Dashboard local (semaines 10–12)

**Objectif** : interface unique pour tout visualiser, sans cloud.

- [ ] Serveur FastAPI sur `http://localhost:8787` (jamais exposé sur internet)
- [ ] Pages :
  - **Vue d'ensemble** : rang estimé IA, blunder rate, progression temporelle
  - **Patterns** : nuage de thèmes, fuseaux récurrents
  - **Parties** : liste filtrable, clic → revue coup par coup
  - **Combats** : séquences tactiques
  - **Exercices du jour** : SRS intégré
  - **Sync** : état collecte KGS + file d'analyse
- [ ] Graphiques : perte moyenne par phase, heatmap du plateau (où vous blunder)
- [ ] Export : rapport PDF mensuel « bilan de progression »

**Livrable** : application locale complète, lancement via `start.bat`.

---

### Phase 7 — Automatisation continue (ongoing)

**Objectif** : nouvelles parties analysées sans intervention.

- [ ] Tâche planifiée Windows (Task Scheduler) : sync KGS toutes les 24h
- [ ] Analyse auto des nouvelles parties (pass 1 immédiat, pass 2 si blunders)
- [ ] Notification locale (optionnel) : « 3 nouvelles parties, 2 blunders détectés »
- [ ] Mise à jour SRS avec nouvelles positions

---

## Décisions labo GPU (juillet 2026)

> Labos sur RTX 3060, KataGo 28b, 3 parties récentes (#109, #108, #106).  
> Rapports : `data/lab/deep_visits_report.md`, `data/lab/fuseki/fuseki_report.md`

### Labo 1 — Deep visits (milieu/fin)

**Question** : comment allouer les visits en phase deep sur les coups suspects ?

| Constat | Détail |
|---------|--------|
| Quick 400v suffit en milieu/fin | 73 % best_move vs oracle 4000v ; meilleur que S1 actuel (74 % → en fait 73,9 % < 76,1 % quick) |
| S1 actuel gaspille le GPU | 175 s / 3 parties, **pire** que quick seul sur best_move et rank_flip |
| Fuseki dans ce labo | 93 % best_move dès le quick — le problème n'est pas là |
| Meilleur deep ciblé (si un jour) | S4 top-3 pertes × 2000v — égalité qualité, ~7× moins de GPU |

**Décision** : **ne pas implémenter** de deep massif milieu/fin. Garder quick 400v. Réserver le GPU au fuseki.

### Labo 2 — Fuseki (coups joueur 1–30)

**Question** : quelle passe fuseki systématique (visits, périmètre) pour bosser l'ouverture ?

Parties testées : **#109** (wade), **#101** / **#99** (Shaomi) — 45 coups joueur fuseki au total.

| Strat | Règle | best_move% | top3% | GPU (3 parties) |
|-------|-------|------------|-------|-----------------|
| F0 | quick 400v (contrôle) | 77,8 % | 62,2 % | 0 s |
| F3 | tes coups 1–30 × 1000v | **82,2 %** | 66,7 % | 50 s |
| F5 | erreurs fuseki seulement × 1000v | **82,2 %** | 64,4 % | **4 s** |
| F6 | ambigus × 2000v + erreurs × 1000v | **82,2 %** | **68,9 %** | 28 s |
| F4 | tous coups 1–30 × 1000v | 77,8 % | 68,9 % | 133 s |

**Constats** :
- Le quick seul **sous-estime** le fuseki vs oracle : −4,4 pt sur best_move.
- Re-analyser **tous** les coups joueur à 1000v (F3) ou **seulement les erreurs** (F5) donne le même best_move — mais F5 coûte **12× moins** de GPU.
- Pour l'**étude joseki** (stabilité du top 3), **F6** est optimal : top3 68,9 % (comme F4) pour 5× moins de GPU.
- F1/F2/F4 analysent aussi l'adversaire ou tous les coups : peu de gain, beaucoup de GPU.

**Décision fuseki** :
- **Prod cible** : **F6** — erreurs fuseki (`point_loss > 0,5`) à 1000v, positions ambiguës (écart #1–#2 < 0,5 pt) à 2000v.
- **Budget minimal** : F5 si GPU très contraint (~1 s/partie).
- **À faire** : `mark_opening_on_complete: false` + passe fuseki systématique sur chaque partie.

Rapport complet : `data/lab/fuseki/fuseki_report.md`

### Budget GPU cible (par partie)

```
Quick 400v      → partie entière           ~2–3 min   [inchangé]
Fuseki F6       → erreurs + ambigus 1–30   ~10–30 s   [prioritaire — labo validé]
Deep milieu     → OFF                      ~0 s       [labo 1 : pas rentable]
```

### Principes révisés

1. **Deux pipelines** : fuseki (étude) ≠ milieu/fin (détection blunders).
2. **Couverture > visits** en fuseki : toutes les parties, pas seulement le quick global.
3. **Pas de deep milieu massif** : le labo l'a invalidé sur données réelles.

### Setup optimal (synthèse croisée)

Rapport comparatif : **`data/lab/analysis_setup_report.md`** (généré par `scripts/lab/generate_setup_report.py`).

| Passe | Rôle | Visits | Quand |
|-------|------|--------|-------|
| 1 Quick | Toute la partie | 400v | Toujours |
| 2 Fuseki F6 | Tes coups 1–30 | 1000v (erreurs) / 2000v (ambigus) | Après quick |
| 3 Deep milieu | — | **OFF** | Jamais (quick suffit) |

**vs actuel** : +3 % best_move, −8,5 % rank_flip, −49 s GPU/partie.

---

| Outil | Rôle | Mode |
|-------|------|------|
| **KataGo** (via Lizzie) | Moteur d'analyse | CLI Analysis Engine |
| **Lizzie** | Revue manuelle ponctuelle | Export SGF depuis le dashboard |
| **AI Sensei** | Étude approfondie 1 partie | Upload manuel SGF exporté |
| **KaTrain** | Alternative revue + tsumego | Import dossier `exercises/` |
| **European YD** | Cours vidéo + parties ligue | Liens manuels → auto quand SGF dispo |
| **Cloudflare** | ❌ Non utilisé | Zéro impact KV jobhunter |

---

## Calibrage 3 dan — seuils d'analyse

À 3 dan, tout n'est pas un blunder. Seuils initiaux (affinables) :

| Niveau | Perte en points | Action |
|--------|-----------------|--------|
| Inaccuracy | 0.5 – 1.5 | Log seulement |
| Mistake | 1.5 – 4.0 | Compté dans stats |
| Blunder | 4.0 – 8.0 | Candidat exercice |
| Méga-blunder | > 8.0 | Priorité SRS + revue |

Option avancée : utiliser le **profil humain KataGo** pour calibrer « erreur à niveau 3d » vs « erreur objective ».

---

## Risques & mitigations

| Risque | Impact | Mitigation |
|--------|--------|------------|
| Volume énorme de parties | Analyse très longue | Pass 1 rapide + priorisation |
| Rate limit KGS | Collecte lente | Cache, reprise, téléchargement nocturne |
| Faux positifs IA | Exercices non pertinents | Seuil 3d, validation manuelle top patterns |
| Combats mal détectés | Stats combat imprécises | Itération heuristiques, phase 4 non bloquante |
| Vidéos EYD stream-only | Pas d'extraction auto | Liens manuels + mapping thèmes |
| RTX 3060 saturée | Lizzie + batch en conflit | File d'analyse, pause quand Lizzie ouvert |
| Disque plein | 3+ Go SGF + JSON | Compression, analyse JSON > SGF archivé |

---

## Ordre de démarrage recommandé

```
Semaine 1   ████████░░  Phase 0 + début Phase 1 (collecte)
Semaine 2   ██████░░░░  Phase 1 (collecte continue) + Phase 2 (premiers batchs)
Semaine 3   ████░░░░░░  Phase 2 (analyse masse) — GPU tourne
Semaine 4   ████░░░░░░  Phase 2 fin + Phase 3 début
Semaine 5-6 ██████░░░░  Phase 3 (patterns)
Semaine 7-8 ████░░░░░░  Phase 4 (combats)
Semaine 9+  ██████░░░░  Phase 5-6-7 (exercices + dashboard + auto)
```

La collecte KGS et l'analyse GPU peuvent **tourner en parallèle** dès la semaine 2.

---

## Prochaine action immédiate

1. Créer la structure du projet (`src/`, `data/`, `config.yaml`)
2. Localiser l'installation KataGo utilisée par Lizzie sur cette machine
3. Télécharger 10 parties test depuis KGS archives Rodolphe
4. Premier pipeline : SGF → analyse → rapport HTML

**Commande de lancement future** : double-clic sur `start.bat` → dashboard localhost.

---

## Nom du projet

Dossier actuel : `jeu de go`  
Nom affiché : **Analyse & Progression Go** (ou **GoProgress** en interne)

---

*Document généré le 26/07/2026 — à mettre à jour au fil des phases.*
