from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

# Permet d'exécuter sans installation pip : python -m goprogress
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goprogress.analyzer import GameAnalyzer
from goprogress.config import load_config, ensure_data_dirs, resolve_path
from goprogress.db import Database
from goprogress.kgs_sync import KgsSync
from goprogress.report import generate_report


def cmd_sync(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    sync = KgsSync(cfg, db)
    try:
        if args.zip and args.year and args.month:
            count = sync.sync_month_zip(args.year, args.month, force=args.force)
        elif args.year and args.month:
            count = sync.sync_month(args.year, args.month, limit=args.limit)
        elif args.all_months:
            count = sync.sync_all_months(
                use_zip=args.zip,
                force=args.force,
                recent_first=not args.oldest_first,
            )
        else:
            count = sync.sync_recent(limit=args.limit or 10)
        print(f"\nSync terminée : {count} partie(s) traitée(s)")
    finally:
        sync.close()
        db.close()


def cmd_analyze(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    analyzer = GameAnalyzer(cfg, db)
    mode = "deep" if args.deep else "quick"
    count = analyzer.analyze_pending(mode=mode, limit=args.limit or 5)
    print(f"\nAnalyse terminée : {count} partie(s)")
    db.close()


def cmd_report(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    path = generate_report(db, cfg)
    print(f"Rapport généré : {path}")
    if args.open:
        webbrowser.open(path.as_uri())
    db.close()


def cmd_status(args: argparse.Namespace) -> None:
    cfg = load_config()
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    stats = db.stats_summary()
    print("=== Statut Go Progress ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    months = db.conn.execute(
        "SELECT year, month, COUNT(*) AS c FROM games GROUP BY year, month ORDER BY year DESC, month DESC LIMIT 12"
    ).fetchall()
    pending = db.pending_analysis_count()
    last_sync = db.get_sync_value("last_sync_at")
    synced = db.synced_months_summary()
    if last_sync:
        print(f"\nDernière sync : {last_sync}")
    print(f"Parties en attente d'analyse : {pending}")
    if synced:
        print(f"Mois synchronisés : {len(synced)}")
    if months:
        print("\nDerniers mois collectés :")
        for m in months:
            print(f"  {m['year']}-{m['month']:02d} : {m['c']} parties")
    db.close()


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Sync récent + analyse + rapport en une commande."""
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    sync = KgsSync(cfg, db)
    try:
        print("=== 1/3 Sync parties récentes ===")
        synced = sync.sync_recent(limit=args.sync_limit or 20)
        print(f"  {synced} partie(s) synchronisée(s)")
    finally:
        sync.close()

    analyzer = GameAnalyzer(cfg, db)
    print("\n=== 2/3 Analyse KataGo ===")
    analyzed = analyzer.analyze_pending(mode="quick", limit=args.analyze_limit or 5)
    print(f"  {analyzed} partie(s) analysée(s)")

    print("\n=== 3/3 Rapport HTML ===")
    path = generate_report(db, cfg)
    print(f"  {path}")
    if args.open:
        webbrowser.open(path.as_uri())
    db.close()


def cmd_serve(args: argparse.Namespace) -> None:
    """Mini-dashboard local (Phase 6 — version basique)."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, FileResponse

    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    app = FastAPI(title="Go Progress")

    @app.get("/", response_class=HTMLResponse)
    def home():
        path = resolve_path(cfg["paths"]["reports_dir"]) / "latest.html"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "<h1>Go Progress</h1><p>Pas encore de rapport. Lancez: python -m goprogress report</p>"

    @app.get("/api/stats")
    def api_stats():
        return {
            **db.stats_summary(),
            "pending_analysis": db.pending_analysis_count(),
            "last_sync": db.get_sync_value("last_sync_at"),
            "synced_months": len(db.synced_months_summary()),
        }

    @app.get("/api/blunders")
    def api_blunders():
        rows = db.top_blunders(limit=20, player=cfg["player"]["kgs_username"])
        return [dict(r) for r in rows]

    @app.get("/rapport")
    def rapport():
        path = resolve_path(cfg["paths"]["reports_dir"]) / "latest.html"
        return FileResponse(path) if path.exists() else HTMLResponse("Pas de rapport")

    host = cfg["dashboard"]["host"]
    port = cfg["dashboard"]["port"]
    print(f"Dashboard local : http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse & Progression Go — outil local",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Télécharger parties KGS")
    p_sync.add_argument("--limit", type=int, default=10, help="Nb parties (mode récent)")
    p_sync.add_argument("--year", type=int)
    p_sync.add_argument("--month", type=int)
    p_sync.add_argument("--zip", action="store_true", help="Archive ZIP mensuelle")
    p_sync.add_argument("--all-months", action="store_true", help="Tout l historique")
    p_sync.add_argument("--force", action="store_true", help="Re-telecharger meme si deja fait")
    p_sync.add_argument("--oldest-first", action="store_true", help="Du plus ancien au plus recent")
    p_sync.set_defaults(func=cmd_sync)

    p_pipeline = sub.add_parser("pipeline", help="Sync + analyse + rapport")
    p_pipeline.add_argument("--sync-limit", type=int, default=20)
    p_pipeline.add_argument("--analyze-limit", type=int, default=5)
    p_pipeline.add_argument("--open", action="store_true")
    p_pipeline.set_defaults(func=cmd_pipeline)

    p_analyze = sub.add_parser("analyze", help="Analyser avec KataGo")
    p_analyze.add_argument("--limit", type=int, default=3, help="Nb parties")
    p_analyze.add_argument("--deep", action="store_true", help="Analyse profonde")
    p_analyze.set_defaults(func=cmd_analyze)

    p_report = sub.add_parser("report", help="Générer rapport HTML")
    p_report.add_argument("--open", action="store_true", help="Ouvrir dans le navigateur")
    p_report.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status", help="Afficher le statut")
    p_status.set_defaults(func=cmd_status)

    p_serve = sub.add_parser("serve", help="Lancer dashboard local")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
