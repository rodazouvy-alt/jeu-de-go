from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from .board_diagram import patch_ascii_to_svg
from .position_diagram import svg_for_move_cached
from .config import resolve_path
from .db import Database
from .themes import THEME_LABELS
from .sgf_parse import game_outcome
from .human_sl import RANK_LABELS


PHASE_LABELS = {
    "opening": "Ouverture",
    "middlegame": "Milieu",
    "endgame": "Yose",
}

SEVERITY_LABELS = {
    "mega_blunder": "Méga-blunder",
    "blunder": "Blunder",
    "mistake": "Erreur",
    "inaccuracy": "Imprécision",
    "ok": "OK",
}

SEVERITY_COLORS = {
    "mega_blunder": "#b91c1c",
    "blunder": "#dc2626",
    "mistake": "#ea580c",
    "inaccuracy": "#ca8a04",
    "ok": "#16a34a",
}


def _format_priors(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        pdata = json.loads(raw)
        return " ".join(
            f"{RANK_LABELS.get(k, k)}:{v:.0%}" for k, v in pdata.items()
        )
    except json.JSONDecodeError:
        return "—"


def _format_pro_match(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        ctx = json.loads(raw)
        count = ctx.get("match_count", 0)
        if count:
            match_type = ctx.get("match_type", "?")
            return f"{count} ({match_type})"
    except json.JSONDecodeError:
        pass
    return "—"


def generate_report(db: Database, cfg: dict[str, Any]) -> Path:
    reports_dir = resolve_path(cfg["paths"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)

    stats = db.stats_summary()
    pending = db.pending_analysis_count()
    blunders = db.top_blunders(limit=40, player=cfg["player"]["kgs_username"])
    games = db.dashboard_games(limit=40)
    pattern_clusters = db.top_pattern_clusters(
        limit=cfg.get("patterns", {}).get("cluster_top_n", 10)
    )
    has_pro_context = any(b["pro_context_json"] for b in blunders)

    severity_stats = db.conn.execute("""
        SELECT severity, COUNT(*) AS cnt, AVG(point_loss) AS avg_loss
        FROM moves WHERE point_loss > 0 AND severity != 'ok'
        GROUP BY severity
        ORDER BY avg_loss DESC
    """).fetchall()
    phase_stats = db.conn.execute("""
        SELECT phase, COUNT(*) AS cnt, AVG(point_loss) AS avg_loss, MAX(point_loss) AS max_loss
        FROM moves
        WHERE point_loss > 0 AND severity != 'ok'
        GROUP BY phase
        ORDER BY avg_loss DESC
    """).fetchall()
    theme_stats = db.conn.execute("""
        SELECT theme, COUNT(*) AS cnt, AVG(point_loss) AS avg_loss, MAX(point_loss) AS max_loss
        FROM moves
        WHERE point_loss > 0 AND severity != 'ok' AND theme IS NOT NULL
        GROUP BY theme
        ORDER BY cnt DESC
    """).fetchall()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"rapport_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    out_path = reports_dir / filename
    dash_port = cfg.get("dashboard", {}).get("port", 8787)
    dash_base = f"http://127.0.0.1:{dash_port}"

    games_html = ""
    for g in games:
        date = f"{g['year']}-{g['month']:02d}" if g.get("year") and g.get("month") else "?"
        oc = game_outcome(g.get("result"), g.get("player_color"))
        if oc == "win":
            outcome = '<span class="outcome outcome-win">V</span>'
        elif oc == "loss":
            outcome = '<span class="outcome outcome-loss">D</span>'
        elif oc == "draw":
            outcome = '<span class="outcome outcome-draw">=</span>'
        else:
            outcome = '<span class="outcome outcome-unknown">?</span>'
        games_html += f"""
        <tr>
          <td>#{g['id']}</td>
          <td class="num">{outcome}</td>
          <td>{escape(date)}</td>
          <td>vs {escape(g['opponent'] or '?')}</td>
          <td>{escape(g['player_rank'] or '?')}</td>
          <td>{escape(g['result'] or '')}</td>
          <td class="num">{g.get('ok', 0) or 0}</td>
          <td class="num">{g.get('inaccuracy', 0) or 0}</td>
          <td class="num">{g.get('mistake', 0) or 0}</td>
          <td class="num">{g.get('blunder', 0) or 0}</td>
          <td class="num">{g.get('mega_blunder', 0) or 0}</td>
          <td class="num"><strong>{g.get('errors', 0) or 0}</strong></td>
          <td>{(g.get('avg_loss') or 0):.1f}</td>
          <td><a href="{dash_base}/">Dashboard</a></td>
        </tr>"""

    rows_html = ""
    pro_header = "<th>Match pro</th>" if has_pro_context else ""
    for i, b in enumerate(blunders, 1):
        sev = b["severity"] or "ok"
        color = SEVERITY_COLORS.get(sev, "#666")
        priors = _format_priors(b["human_prior_json"])
        pro_match = (
            f"<td>{escape(_format_pro_match(b['pro_context_json']))}</td>"
            if has_pro_context else ""
        )
        priority = b["study_priority"]
        prio_str = f"{priority:.1f}" if priority is not None else "—"
        mid = b["id"]
        rows_html += f"""
        <tr>
          <td>{i}</td>
          <td><strong>{prio_str}</strong></td>
          <td><span class="badge" style="background:{color}">{escape(SEVERITY_LABELS.get(sev, sev))}</span></td>
          <td><strong>{b['point_loss']:.1f}</strong> pts</td>
          {pro_match}
          <td>{escape(priors)}</td>
          <td>{escape(THEME_LABELS.get(b['theme'] or '', b['theme'] or '?'))}</td>
          <td>coup {b['move_number']}</td>
          <td>{escape(b['player_rank'] or '?')}</td>
          <td>{escape(b['opponent_rank'] or '?')}</td>
          <td>{escape(b['played_move'] or '')} → {escape(b['best_move'] or '?')}</td>
          <td>vs {escape(b['opponent'] or '?')}</td>
          <td class="review-links">
            <a href="{dash_base}/review/{mid}/open?tool=lizzie" title="Lizzie">Lizzie</a>
            · <a href="{dash_base}/review/{mid}/open?tool=katrain" title="KaTrain / SGF">KaTrain</a>
            · <a href="{dash_base}/review/{mid}/sgf" title="Télécharger SGF review">SGF</a>
          </td>
        </tr>"""

    patterns_html = ""
    for i, c in enumerate(pattern_clusters, 1):
        label = THEME_LABELS.get(c["dominant_theme"] or "", c["dominant_theme"] or "?")
        sample_move_id = None
        try:
            samples = json.loads(c["sample_json"] or "[]")
            if samples:
                sample_move_id = samples[0].get("move_id")
        except json.JSONDecodeError:
            pass
        if sample_move_id:
            svg = svg_for_move_cached(db, int(sample_move_id), cell=12)
        else:
            svg = patch_ascii_to_svg(c["patch_ascii"] or "")
        lizzie = (
            f'<a href="{dash_base}/review/{sample_move_id}/open?tool=lizzie">Lizzie</a>'
            if sample_move_id else "—"
        )
        patterns_html += f"""
        <tr>
          <td>{i}</td>
          <td><strong>{c['move_count']}</strong></td>
          <td>{(c['total_point_loss'] or 0):.1f} pts</td>
          <td>{escape(label)}</td>
          <td class="diagram">{svg}</td>
          <td class="review-links">{lizzie}</td>
        </tr>"""

    blunder_colspan = 13 if has_pro_context else 12

    theme_html = ""
    for t in theme_stats:
        label = THEME_LABELS.get(t["theme"] or "", t["theme"] or "?")
        theme_html += f"""
        <tr>
          <td>{escape(label)}</td>
          <td>{t['cnt']}</td>
          <td>{(t['avg_loss'] or 0):.2f} pts</td>
          <td>{(t['max_loss'] or 0):.1f} pts</td>
        </tr>"""

    phase_html = ""
    for p in phase_stats:
        phase_html += f"""
        <tr>
          <td>{escape(p['phase'] or '')}</td>
          <td>{p['cnt']}</td>
          <td>{(p['avg_loss'] or 0):.2f} pts</td>
          <td>{(p['max_loss'] or 0):.1f} pts</td>
        </tr>"""

    severity_html = ""
    sev_labels = {
        "mega_blunder": "Méga-blunder",
        "blunder": "Blunder",
        "mistake": "Erreur",
        "inaccuracy": "Imprécision",
    }
    for s in severity_stats:
        sev = s["severity"] or "ok"
        color = SEVERITY_COLORS.get(sev, "#666")
        severity_html += f"""
        <tr>
          <td><span class="badge" style="background:{color}">{escape(sev_labels.get(sev, sev))}</span></td>
          <td>{s['cnt']}</td>
          <td>{(s['avg_loss'] or 0):.2f} pts</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Rapport Go — {escape(cfg['player']['kgs_username'])}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #f8fafc; color: #1e293b; }}
    h1 {{ color: #0f172a; }}
    h2 {{ margin-top: 2rem; }}
    .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }}
    .card {{ background: white; border-radius: 8px; padding: 1rem 1.5rem; box-shadow: 0 1px 3px #0002; min-width: 140px; }}
    .card .num {{ font-size: 2rem; font-weight: bold; color: #2563eb; }}
    table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px #0002; margin-bottom: 1rem; }}
    th, td {{ border: 1px solid #e2e8f0; padding: 0.5rem 0.75rem; text-align: left; font-size: 0.9rem; }}
    th {{ background: #1e293b; color: white; }}
    tr:nth-child(even) {{ background: #f1f5f9; }}
    .badge {{ color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; }}
    .footer {{ margin-top: 2rem; color: #64748b; font-size: 0.9rem; }}
    .hint {{ color: #64748b; font-size: 0.95rem; max-width: 900px; }}
    .review-links a {{ color: #2563eb; text-decoration: none; font-weight: 500; }}
    .review-links a:hover {{ text-decoration: underline; }}
    pre.patch {{ font-family: monospace; font-size: 0.7rem; margin: 0; white-space: pre; }}
    .diagram svg {{ display: block; border-radius: 4px; }}
    .num {{ text-align: center; }}
    .outcome {{ display: inline-block; width: 1.5rem; height: 1.5rem; line-height: 1.5rem;
      text-align: center; border-radius: 4px; font-weight: 800; font-size: 0.85rem; }}
    .outcome-win {{ background: #16a34a; color: white; }}
    .outcome-loss {{ background: #dc2626; color: white; }}
    .outcome-draw {{ background: #64748b; color: white; }}
    .outcome-unknown {{ background: #94a3b8; color: white; }}
    .banner {{ background: #dbeafe; border: 1px solid #93c5fd; padding: 0.75rem 1rem; border-radius: 8px; margin-bottom: 1.5rem; }}
  </style>
</head>
<body>
  <h1>Analyse &amp; Progression Go</h1>
  <p>Joueur : <strong>{escape(cfg['player']['kgs_username'])}</strong> ({escape(cfg['player']['rank'])})
     — généré le {timestamp}</p>

  <div class="banner">
    <strong>Étude interactive :</strong> lancez <code>start.bat</code> puis ouvrez
    <a href="{dash_base}/"><strong>{dash_base}</strong></a> — une ligne par partie,
    détail des coups, Lizzie au bon moment. (Les liens Lizzie ci-dessous nécessitent ce serveur.)
  </div>

  <div class="cards">
    <div class="card"><div class="num">{stats['games_total']}</div>parties collectées</div>
    <div class="card"><div class="num">{stats['games_analyzed']}</div>parties analysées</div>
    <div class="card"><div class="num">{pending}</div>en attente</div>
    <div class="card"><div class="num">{stats['blunders_total']}</div>blunders</div>
    <div class="card"><div class="num">{stats['avg_point_loss']}</div>perte moy. (pts)</div>
  </div>

  <h2>Synthèse par partie</h2>
  <p class="hint">Comptage de vos coups par sévérité. Détail coups + Lizzie →
  <a href="{dash_base}/">dashboard live</a>.</p>
  <table>
    <tr>
      <th>#</th><th></th><th>Date</th><th>Adversaire</th><th>Rang</th><th>Résultat</th>
      <th>OK</th><th>Impr.</th><th>Err.</th><th>Blund.</th><th>Méga</th>
      <th>Total err.</th><th>Perte moy.</th><th></th>
    </tr>
    {games_html or '<tr><td colspan="14">Lancez scripts\\occasionnel\\run_analyze.bat</td></tr>'}
  </table>

  <h2>Erreurs par sévérité</h2>
  <table>
    <tr><th>Niveau</th><th>Nombre</th><th>Perte moyenne</th></tr>
    {severity_html or '<tr><td colspan="3">Pas encore de données</td></tr>'}
  </table>

  <h2>Patterns récurrents</h2>
  <p class="hint">Formes locales identiques autour de vos blunders. Diagramme Go réel — bouton Lizzie sur un exemple.</p>
  <table>
    <tr><th>#</th><th>Occurrences</th><th>Perte cumulée</th><th>Thème</th><th>Diagramme</th><th>Étude</th></tr>
    {patterns_html or '<tr><td colspan="6">Lancez: python -m goprogress patterns</td></tr>'}
  </table>

  <h2>Erreurs par thème</h2>
  <table>
    <tr><th>Thème</th><th>Nb erreurs</th><th>Perte moyenne</th><th>Pire coup</th></tr>
    {theme_html or '<tr><td colspan="4">Lancez: python -m goprogress enrich</td></tr>'}
  </table>

  <h2>Erreurs par phase</h2>
  <table>
    <tr><th>Phase</th><th>Nb erreurs</th><th>Perte moyenne</th><th>Pire coup</th></tr>
    {phase_html or '<tr><td colspan="4">Pas encore de données</td></tr>'}
  </table>

  <h2>Priorités d'étude (Human SL)</h2>
  <p class="hint">Priorité = perte × (1 − probabilité qu'un 3d joue ce coup).
  <strong>Lizzie :</strong> lancez <code>start.bat</code> puis cliquez — ouvre la position au coup exact.</p>
  <table>
    <tr>
      <th>#</th><th>Priorité</th><th>Sévérité</th><th>Perte</th>
      {pro_header}
      <th>humanPrior 3d/5d/7d/9d</th><th>Thème</th><th>Coup</th>
      <th>Votre rang</th><th>Rang adv.</th><th>Joué → Meilleur</th><th>Adv.</th><th>Étude</th>
    </tr>
    {rows_html or f'<tr><td colspan="{blunder_colspan}">Lancez scripts\\occasionnel\\run_human_sl.bat</td></tr>'}
  </table>

  <p class="footer">
    100% local — <code>data/reports/latest.html</code> (F5 pour rafraîchir).<br>
    Human SL = enrichissement sur analyse 28b existante (pas de re-scan complet).
  </p>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    latest = reports_dir / "latest.html"
    latest.write_text(html, encoding="utf-8")
    return out_path
