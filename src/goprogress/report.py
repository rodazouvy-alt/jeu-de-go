from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from .config import resolve_path
from .db import Database


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


def generate_report(db: Database, cfg: dict[str, Any]) -> Path:
    reports_dir = resolve_path(cfg["paths"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)

    stats = db.stats_summary()
    blunders = db.top_blunders(limit=40, player=cfg["player"]["kgs_username"])
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

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"rapport_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    out_path = reports_dir / filename

    rows_html = ""
    for i, b in enumerate(blunders, 1):
        sev = b["severity"] or "ok"
        color = SEVERITY_COLORS.get(sev, "#666")
        rows_html += f"""
        <tr>
          <td>{i}</td>
          <td><span class="badge" style="background:{color}">{escape(SEVERITY_LABELS.get(sev, sev))}</span></td>
          <td><strong>{b['point_loss']:.1f}</strong> pts</td>
          <td>coup {b['move_number']}</td>
          <td>{escape(b['phase'] or '')}</td>
          <td>{escape(b['played_move'] or '')} → {escape(b['best_move'] or '?')}</td>
          <td>vs {escape(b['opponent'] or '?')}</td>
          <td>{escape(b['played_at'] or '')}</td>
          <td>{escape(Path(b['sgf_path']).name if b['sgf_path'] else '')}</td>
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
    .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }}
    .card {{ background: white; border-radius: 8px; padding: 1rem 1.5rem; box-shadow: 0 1px 3px #0002; min-width: 140px; }}
    .card .num {{ font-size: 2rem; font-weight: bold; color: #2563eb; }}
    table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px #0002; }}
    th, td {{ border: 1px solid #e2e8f0; padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: #1e293b; color: white; }}
    tr:nth-child(even) {{ background: #f1f5f9; }}
    .badge {{ color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; }}
    .footer {{ margin-top: 2rem; color: #64748b; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Analyse &amp; Progression Go</h1>
  <p>Joueur : <strong>{escape(cfg['player']['kgs_username'])}</strong> ({escape(cfg['player']['rank'])})
     — généré le {timestamp}</p>

  <div class="cards">
    <div class="card"><div class="num">{stats['games_total']}</div>parties collectées</div>
    <div class="card"><div class="num">{stats['games_analyzed']}</div>parties analysées</div>
    <div class="card"><div class="num">{stats['blunders_total']}</div>blunders</div>
    <div class="card"><div class="num">{stats['avg_point_loss']}</div>perte moy. (pts)</div>
  </div>

  <h2>Erreurs par sévérité</h2>
  <table>
    <tr><th>Niveau</th><th>Nombre</th><th>Perte moyenne</th></tr>
    {severity_html or '<tr><td colspan="3">Pas encore de données</td></tr>'}
  </table>

  <h2>Erreurs par phase de partie</h2>
  <table>
    <tr><th>Phase</th><th>Nb erreurs</th><th>Perte moyenne</th><th>Pire coup</th></tr>
    {phase_html or '<tr><td colspan="4">Pas encore de données</td></tr>'}
  </table>

  <h2>Top erreurs ({escape(cfg['player']['kgs_username'])})</h2>
  <table>
    <tr>
      <th>#</th><th>Sévérité</th><th>Perte</th><th>Coup</th><th>Phase</th>
      <th>Joué → Meilleur</th><th>Adversaire</th><th>Date</th><th>Partie</th>
    </tr>
    {rows_html or '<tr><td colspan="9">Aucune erreur analysée pour l instant</td></tr>'}
  </table>

  <p class="footer">
    Outil 100% local — ouvrez ce fichier dans votre navigateur.<br>
  </p>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")

    latest = reports_dir / "latest.html"
    latest.write_text(html, encoding="utf-8")
    return out_path
