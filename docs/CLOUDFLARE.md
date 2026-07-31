# Architecture miroir — Go Progress + Cloudflare

## Principe

Deux couches **indépendantes** : l'analyse locale ne change pas ; le téléphone consulte une **copie** toujours en ligne.

```
┌─────────────────────────────────────┐     sync auto      ┌──────────────────────────┐
│  PC (quand il tourne)               │  ───────────────►  │  Cloudflare Pages (24/7) │
│  • run_analysis_loop.bat            │   HTML + diagrammes│  https://xxx.pages.dev   │
│  • SQLite + KataGo                  │   (~quelques MB)   │  favori sur téléphone   │
│  • start.bat (dashboard live)       │                    │                          │
└─────────────────────────────────────┘                    └──────────────────────────┘
```

- **PC éteint** → le miroir reste en ligne avec la **dernière** sync.
- **PC qui analyse** → à chaque recalcul patterns (~15 parties), le miroir est mis à jour.
- **Rien à changer** dans votre workflow : sync, analyse, finalize comme avant.

## Mise en place (une fois, ~15 min)

### 1. Projet Cloudflare Pages

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages** → **Create**
2. **Pages** → **Upload assets** → nom du projet ex. `go-progress-rodolphe`
3. Uploadez un fichier vide ou un `index.html` temporaire (le vrai contenu viendra de wrangler)

### 2. Config locale (secrets hors git)

```powershell
copy config.local.yaml.example config.local.yaml
```

Éditez `config.local.yaml` :

```yaml
cloudflare:
  pages_project: "go-progress-rodolphe"
  pages_url: "https://go-progress-rodolphe.pages.dev"
  auto_deploy_pages: true
```

### 3. Wrangler (déploiement auto)

```powershell
npm install -g wrangler
wrangler login
```

Ou token API (recommandé) : Cloudflare → **My Profile** → **API Tokens** → template **Edit Cloudflare Workers** →

```powershell
$env:CLOUDFLARE_API_TOKEN = "votre-token"
```

### 4. Premier push

```powershell
sync_cloudflare.bat
```

Ajoutez l'URL `pages_url` en favori sur votre téléphone.

## Sync automatique

| Événement | Action |
|-----------|--------|
| Recalcul patterns (boucle analyse) | `maybe_sync_after_patterns` → export + deploy |
| Fin `run_finalize_reanalysis` | `maybe_sync_on_finalize` |
| Manuel | `sync_cloudflare.bat` ou `python -m goprogress sync-cloudflare` |

## Coût

| Composant | Coût |
|-----------|------|
| Cloudflare Pages (miroir HTML) | **0 €** |
| Données poussées (~5–20 MB HTML) | **0 €** |
| Corpus local GoGoD (~16 GB) | **reste sur le PC**, pas envoyé |

## Live vs miroir

| | Miroir Pages | start.bat / tunnel |
|--|--------------|-------------------|
| PC éteint | ✅ | ❌ |
| Stats, parties, coups, patterns | ✅ | ✅ |
| Diagrammes (export statique) | ✅ intégrés | ✅ + zoom live |
| Lizzie | ❌ | ✅ |
| Filtres dynamiques drilldown | ❌ | ✅ |

Pour l'usage téléphone quotidien, le **miroir** suffit. Le live reste sur le PC pour l'étude approfondie.
