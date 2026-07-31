from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

# Permet d'exécuter sans installation pip : python -m goprogress
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goprogress.analyzer import GameAnalyzer
from goprogress.config import load_config, ensure_data_dirs, resolve_path
from goprogress.db import Database
from goprogress.enrich import enrich_analyzed_games
from goprogress.human_sl import enrich_human_sl, backfill_human_sl_games, RANK_LABELS
from goprogress.katago_check import check_katago
from goprogress.local_pattern import run_patterns
from goprogress.opening_analysis import OpeningAnalyzer
from goprogress.pro_patterns import build_patch_index, run_pro_patterns
from goprogress.report import generate_report
from goprogress.import_gogod import import_gogod
from goprogress.cloudflare_sync import maybe_sync_after_patterns, maybe_sync_on_finalize, sync_cloudflare_mirror
from goprogress.publish import publish_dashboard
from goprogress.review_sgf import (
    generate_review_for_move,
    open_move_review,
    open_review,
)


def _since_year_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--since-year", type=int, metavar="YEAR",
        help="Limiter aux parties depuis cette année (ex. 2024)",
    )


def cmd_sync(args: argparse.Namespace) -> None:
    from goprogress.kgs_sync import KgsSync
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    sync = KgsSync(cfg, db)
    try:
        if args.since_year:
            count = sync.sync_since_year(
                args.since_year, use_zip=args.zip, force=args.force,
            )
        elif args.zip and args.year and args.month:
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
    count = analyzer.analyze_pending(
        mode=mode,
        limit=args.limit or 5,
        backfill_opponent=bool(args.backfill_opponent),
        since_year=args.since_year,
    )
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


def cmd_human_sl(args: argparse.Namespace) -> None:
    cfg = load_config()
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    print("=== Human SL — comparaison par rang ===\n")
    try:
        if args.by_game:
            count = backfill_human_sl_games(
                cfg, db,
                since_year=args.since_year,
                limit=args.limit or 500,
            )
            print(f"\n{count} coup(s) enrichi(s) (mode par partie)")
        else:
            count = enrich_human_sl(
                cfg, db,
                limit=args.limit or 100,
                min_point_loss=args.min_loss,
                since_year=args.since_year,
            )
            print(f"\n{count} coup(s) enrichi(s) avec humanPrior multi-rangs")
    except FileNotFoundError as exc:
        print(exc)
        db.close()
        raise SystemExit(1) from exc
    if args.report or count:
        path = generate_report(db, cfg)
        print(f"Rapport : {path.parent / 'latest.html'}")
    db.close()


def cmd_enrich(args: argparse.Namespace) -> None:
    cfg = load_config()
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    games, themes, timing, results = enrich_analyzed_games(
        db, cfg, since_year=args.since_year,
    )
    print(f"Enrichissement : {games} partie(s), {themes} coup(s) thématisés, {timing} cadence(s), {results} résultat(s)")
    if args.report:
        path = generate_report(db, cfg)
        print(f"Rapport : {path.parent / 'latest.html'}")
    db.close()


def cmd_patterns(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    run_patterns(db, cfg, since_year=args.since_year)
    if args.report:
        path = generate_report(db, cfg)
        print(f"Rapport : {path.parent / 'latest.html'}")
    db.close()


def cmd_pro_patterns(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    if args.rebuild_index:
        build_patch_index(cfg, force=True)
    run_pro_patterns(
        db, cfg, limit=args.limit, rebuild_index=False,
        since_year=args.since_year,
    )
    if args.report:
        path = generate_report(db, cfg)
        print(f"Rapport : {path.parent / 'latest.html'}")
    db.close()


def cmd_build_pro_index(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    path = build_patch_index(cfg, force=args.force)
    print(f"Index pro : {path}")


def cmd_katago_check(args: argparse.Namespace) -> None:
    cfg = load_config()
    raise SystemExit(check_katago(cfg, benchmark=not args.no_benchmark))


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
    pending_deep = db.pending_deep_count(2024)
    prog = db.reanalysis_progress(2024)
    opening = db.opening_progress(min_game_id=48)
    last_sync = db.get_sync_value("last_sync_at")
    synced = db.synced_months_summary()
    if last_sync:
        print(f"\nDernière sync : {last_sync}")
    print(f"Parties en attente (quick) : {pending}")
    print(
        f"Ré-analyse 2024+ : {prog['done']}/{prog['total']} terminées, "
        f"{prog['pending']} en attente (deep)"
    )
    print(
        f"Ouverture 48+ : {opening['done']}/{opening['total']} terminées, "
        f"{opening['pending']} en attente"
    )
    nxt = db.conn.execute(
        """
        SELECT id FROM games
        WHERE analyzed_deep = 0 AND year >= 2024
        ORDER BY analyzed_quick DESC, played_at DESC, id ASC
        LIMIT 1
        """
    ).fetchone()
    if nxt:
        print(f"Prochaine partie : #{nxt['id']}")
    if synced:
        print(f"Mois synchronisés : {len(synced)}")
    if months:
        print("\nDerniers mois collectés :")
        for m in months:
            print(f"  {m['year']}-{m['month']:02d} : {m['c']} parties")
    db.close()


def cmd_reset(args: argparse.Namespace) -> None:
    cfg = load_config()
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    if not args.yes:
        print("ATTENTION : efface toutes les analyses (coups, patterns, flags).")
        print("Les SGF et la liste des parties sont conservés.")
        confirm = input("Tapez OUI pour confirmer : ").strip()
        if confirm != "OUI":
            print("Annulé.")
            db.close()
            return
    stats = db.reset_analysis()
    print(
        f"Reset : {stats['moves_deleted']} coup(s), "
        f"{stats['clusters_deleted']} cluster(s), "
        f"{stats['games_reset']} partie(s) remise(s) à zéro"
    )
    db.close()


def cmd_prepare_v2(args: argparse.Namespace) -> None:
    """Prepare la re-analyse pipeline v2 (labo GPU juillet 2026)."""
    cfg = load_config()
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    since = args.since_year
    min_id = args.from_game_id
    if min_id is None and not args.full:
        min_id = cfg.get("analysis", {}).get("continue_from_game_id")
    if min_id is not None:
        min_id = int(min_id)

    if args.full and not args.yes:
        scope = "toutes les parties"
        if args.since_year:
            scope = f"parties {args.since_year}+"
        print(f"ATTENTION : --full efface les coups analyses ({scope}).")
        confirm = input("Tapez OUI pour confirmer : ").strip()
        if confirm != "OUI":
            print("Annule.")
            db.close()
            return

    stats = db.prepare_v2_reanalysis(
        since_year=since, min_game_id=min_id, full=bool(args.full),
    )
    if stats.get("full_reset"):
        yr = stats.get("since_year") or since
        label = f" ({yr}+)" if yr else ""
        print(
            f"Reset complet{label} : {stats.get('moves_deleted', 0)} coups effaces, "
            f"{stats.get('games_reset', 0)} parties remises a zero."
        )
        print("\n>>> Lancez scripts\\quotidien\\run_analysis_loop.bat <<<")
    else:
        print(
            f"Re-ouvert {stats.get('games_reopened', 0)} partie(s) "
            f"(quick conserve, fuseki F6 a relancer)."
        )
        print("Lancer : scripts\\quotidien\\run_analysis_loop.bat")
    db.close()


def cmd_restore_analysis(args: argparse.Namespace) -> None:
    """Réimporte les analyses sauvegardées en JSON (après un reset accidentel)."""
    cfg = load_config()
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    analysis_dir = resolve_path(cfg["paths"]["analysis_dir"])
    if not analysis_dir.exists():
        print(f"Dossier introuvable : {analysis_dir}")
        db.close()
        return
    restored, skipped = db.restore_analysis_exports(
        analysis_dir, game_id=args.game_id,
    )
    since = args.since_year or 2024
    prog = db.reanalysis_progress(since)
    print(f"Restauré : {restored} partie(s), ignoré : {skipped}")
    print(
        f"Ré-analyse {since}+ : {prog['done']}/{prog['total']} terminées, "
        f"{prog['pending']} en attente"
    )
    db.close()


def cmd_opening_analysis(args: argparse.Namespace) -> None:
    """Analyse fuseki F6 : re-analyse ciblée après quick (labo GPU)."""
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    ocfg = cfg.get("opening_analysis", {})
    since = args.since_year
    min_id = args.from_game_id
    max_id = args.to_game_id
    if args.supplement:
        max_id = max_id or int(ocfg.get("supplement_max_game_id", 47))
        min_id = None
        since = None  # complement 1–N : toutes les parties de la plage, sans filtre année
    else:
        since = args.since_year
        if min_id is None and not args.all_games:
            min_id = int(ocfg.get("continue_from_game_id", 48))

    analyzer = OpeningAnalyzer(cfg, db)
    restart_sec = (args.katago_restart_hours or 1) * 3600
    refresh_patterns = bool(ocfg.get("run_patterns_after_batch", True))

    def _refresh_patterns_if_needed(n_done: int) -> None:
        if n_done > 0 and refresh_patterns:
            print("  -> Recalcul patterns fuseki/joseki...", flush=True)
            run_patterns(db, cfg, since_year=since)

    if args.loop:
        prog = db.opening_progress(
            min_game_id=min_id, max_game_id=max_id, since_year=since,
        )
        print(
            f"\n=== Boucle fuseki F6 (batch {args.batch}, "
            f"{analyzer.max_visits}/{analyzer.hard_max_visits} visits) ===",
            flush=True,
        )
        print(
            f"Progression : {prog['done']}/{prog['total']} "
            f"({prog['pending']} restantes)",
            flush=True,
        )
        if min_id is not None:
            print(f"Depuis partie #{min_id}", flush=True)
        if max_id is not None:
            print(f"Jusqu'a partie #{max_id}", flush=True)
        print("Ctrl+C pour arreter. Fermez Lizzie.", flush=True)
        while True:
            remaining = db.pending_opening_count(
                min_game_id=min_id,
                max_game_id=max_id,
                since_year=since,
                force=args.force,
            )
            if remaining == 0:
                print("\nToutes les parties ouverture sont analysees.")
                break
            print(f"\n--- ~{remaining} partie(s) restantes ---", flush=True)
            batch = remaining if args.supplement else args.batch
            try:
                n = analyzer.analyze_pending(
                    limit=batch,
                    min_game_id=min_id,
                    max_game_id=max_id,
                    since_year=since,
                    katago_restart_sec=restart_sec,
                    force=args.force,
                )
                left = db.pending_opening_count(
                    min_game_id=min_id,
                    max_game_id=max_id,
                    since_year=since,
                    force=args.force,
                )
                if args.supplement:
                    print(f"  {n} partie(s) traitee(s) — reste ~{left}", flush=True)
                else:
                    print(f"  Lot: {n} partie(s) — reste ~{left}", flush=True)
                _refresh_patterns_if_needed(n)
            except Exception as exc:
                print(f"ERREUR lot (reprise dans 60 s): {exc}", flush=True)
                time.sleep(60)
                continue
            if n == 0:
                time.sleep(30)
            else:
                time.sleep(5)
        db.close()
        return

    n = analyzer.analyze_pending(
        limit=args.batch,
        min_game_id=min_id,
        max_game_id=max_id,
        since_year=since,
        katago_restart_sec=restart_sec,
        force=args.force,
    )
    _refresh_patterns_if_needed(n)
    prog = db.opening_progress(
        min_game_id=min_id, max_game_id=max_id, since_year=since,
    )
    print(f"\nOuverture : {n} partie(s) — {prog['done']}/{prog['total']} total")
    db.close()


def cmd_reanalysis(args: argparse.Namespace) -> None:
    """Lot d'analyse complète (quick+deep) + enrichissements optionnels."""
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    since = args.since_year
    acfg = cfg.get("analysis", {})
    min_id = args.from_game_id
    if min_id is None:
        min_id = acfg.get("continue_from_game_id")
    if min_id is not None:
        min_id = int(min_id)
    if args.batch == 5:  # défaut argparse — préférer config si présente
        args.batch = int(acfg.get("batch_size", args.batch))
    analyzer = GameAnalyzer(cfg, db)
    analyzer.auto_report = False
    patterns_every = int(acfg.get("patterns_every_n_batches", 0) or 0)
    refresh_each = bool(acfg.get("patterns_after_each_batch", False))
    batch_counter = 0

    def _refresh_patterns(n_done: int, *, force: bool = False) -> None:
        nonlocal batch_counter
        if n_done <= 0:
            return
        batch_counter += 1
        should_run = force or refresh_each
        if not should_run and patterns_every > 0:
            should_run = batch_counter % patterns_every == 0
        if should_run:
            print("  -> Recalcul patterns fuseki/joseki...", flush=True)
            run_patterns(db, cfg, since_year=since)
            maybe_sync_after_patterns(db, cfg)
        elif patterns_every > 0:
            next_in = patterns_every - (batch_counter % patterns_every)
            print(
                f"  -> Patterns dans {next_in} lot(s) "
                f"(tous les {patterns_every} lots)",
                flush=True,
            )

    if args.reset:
        stats = db.reset_analysis()
        print(
            f"Reset : {stats['moves_deleted']} coup(s) effacés, "
            f"{stats['games_reset']} parties"
        )

    pending = db.pending_deep_count(since, min_game_id=min_id)
    if pending == 0 and not args.finalize_only:
        print("Aucune partie en attente — lancement des enrichissements finaux.")
        args.finalize_only = True

    if not args.finalize_only and args.loop:
        restart_sec = (args.katago_restart_hours or 1) * 3600
        prog = db.reanalysis_progress(since, min_game_id=min_id)
        parallel = bool(acfg.get("parallel_pipeline", True))
        print(
            f"\n=== Boucle analyse complete (batch {args.batch}, "
            f"pipeline {'lot optimise' if parallel else 'sequentiel'}, "
            f"patterns / {patterns_every or 'chaque'} lot(s), "
            f"KataGo restart / {int(restart_sec // 60)} min) ===",
            flush=True,
        )
        print(
            f"Progression : {prog['done']}/{prog['total']} parties terminées "
            f"({prog['pending']} restantes)",
            flush=True,
        )
        if min_id is not None:
            print(f"Depuis partie #{min_id}", flush=True)
        scope_sql, scope_params = db._since_year_sql(since, alias=None)
        id_sql, id_params = "", []
        if min_id is not None:
            id_sql = " AND id >= ?"
            id_params = [min_id]
        nxt_row = db.conn.execute(
            f"""
            SELECT id FROM games
            WHERE analyzed_deep = 0{scope_sql}{id_sql}
            ORDER BY id ASC
            LIMIT 1
            """,
            (*scope_params, *id_params),
        ).fetchone()
        if nxt_row:
            print(f"Prochaine partie : #{nxt_row['id']}", flush=True)
        print("Ctrl+C pour arreter. Fermez Lizzie.", flush=True)
        print("NE PAS lancer scripts\\archive\\run_prepare_reanalysis.bat (efface tout).", flush=True)
        while True:
            remaining = db.pending_deep_count(since, min_game_id=min_id)
            if remaining == 0:
                print("\nToutes les parties sont analysees — enrichissements finaux...")
                _refresh_patterns(1, force=True)
                args.finalize_only = True
                break
            print(f"\n--- ~{remaining} partie(s) restantes ---", flush=True)
            try:
                n = analyzer.analyze_pending(
                    mode="deep",
                    limit=args.batch,
                    since_year=since,
                    katago_restart_sec=restart_sec,
                    min_game_id=min_id,
                )
                print(
                    f"  Lot: {n} partie(s) — reste ~{db.pending_deep_count(since, min_game_id=min_id)}",
                    flush=True,
                )
                _refresh_patterns(n)
            except Exception as exc:
                print(f"ERREUR lot (reprise dans 60 s): {exc}", flush=True)
                time.sleep(60)
                continue
            if n == 0:
                time.sleep(30)
            else:
                time.sleep(5)
        if not args.finalize_only:
            db.close()
            return

    if not args.finalize_only:
        print(f"\n=== Analyse quick+deep ({args.batch} partie(s) max) ===")
        remaining = db.pending_deep_count(since, min_game_id=min_id)
        print(f"Période {since}+ : ~{remaining} partie(s) restantes (deep)", flush=True)
        n = analyzer.analyze_pending(
            mode="deep",
            limit=args.batch,
            since_year=since,
            min_game_id=min_id,
        )
        print(f"  {n} partie(s) traitée(s) — reste ~{db.pending_deep_count(since, min_game_id=min_id)}")
        db.close()
        return

    print("\n=== Enrichissements finaux ===")
    enrich_analyzed_games(db, cfg, since_year=since)
    try:
        enrich_human_sl(cfg, db, limit=args.human_limit or 500, since_year=since)
    except FileNotFoundError as exc:
        print(exc)
    run_patterns(db, cfg, since_year=since)
    run_pro_patterns(db, cfg, since_year=since)
    path = generate_report(db, cfg)
    print(f"\nRapport : {path}")
    maybe_sync_on_finalize(db, cfg)
    db.close()


def cmd_reconcile_scores(args: argparse.Namespace) -> None:
    """Recalcule point_loss / severity / best_move depuis les scores KataGo stockés."""
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    stats = db.reconcile_move_severities(cfg.get("thresholds"))
    print(
        f"Recalcul terminé : {stats['updated']} coups mis à jour "
        f"({stats['loss_fixed']} pertes corrigées, "
        f"{stats['best_fixed']} best_move alignés) "
        f"sur {stats['total']} coups joueur analysés."
    )
    if stats["updated"] and not args.no_publish:
        print("\nRegénération du miroir Cloudflare...", flush=True)
        sync_cloudflare_mirror(db, cfg, reason="reconcile-scores")
    db.close()


def cmd_sync_cloudflare(args: argparse.Namespace) -> None:
    """Pousse le dashboard local vers Cloudflare Pages (miroir 24/7)."""
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    ok = sync_cloudflare_mirror(db, cfg, reason="manuel")
    db.close()
    if not ok:
        raise SystemExit(1)


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Sync récent + analyse + rapport en une commande."""
    from goprogress.kgs_sync import KgsSync
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


def cmd_import_gogod(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    dl = Path(args.downloads_dir) if args.downloads_dir else None
    import_gogod(cfg, downloads_dir=dl, skip_extract=args.skip_extract)


def cmd_review(args: argparse.Namespace) -> None:
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    path = generate_review_for_move(db, cfg, args.move_id)
    print(f"SGF review : {path}")
    if args.open:
        tool = args.tool or "lizzie"
        open_review(path, cfg, tool=tool)
        print(f"Ouverture {tool}...")
    db.close()


def cmd_publish(args: argparse.Namespace) -> None:
    """Exporte un snapshot HTML statique pour Cloudflare Pages."""
    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    if args.deploy:
        sync_cloudflare_mirror(db, cfg, reason="publish")
    else:
        path = publish_dashboard(db, cfg, static_export=True)
        print(f"Snapshot publié : {path}")
        print("  index.html, patterns.html")
        print("  Deploy : python -m goprogress sync-cloudflare")
    db.close()


def cmd_serve(args: argparse.Namespace) -> None:
    """Mini-dashboard local (Phase 6 — version basique)."""
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse

    from goprogress.dashboard_ui import (
        render_dashboard,
        render_games_page,
        render_patterns_kind_page,
        render_patterns_explorer,
        render_alerte_page,
        parse_list_limit,
    )
    from goprogress.local_pattern import FUSEKI_MAX_MOVE

    cfg = load_config()
    ensure_data_dirs(cfg)
    db = Database(resolve_path(cfg["paths"]["db_path"]))
    app = FastAPI(title="Go Progress")

    @app.get("/", response_class=HTMLResponse)
    def home():
        username = cfg["player"]["kgs_username"]
        opening_prog = db.opening_progress()
        stats = {
            **db.stats_enhanced(username, fuseki_max_move=FUSEKI_MAX_MOVE),
            "pending_analysis": db.pending_analysis_count(),
            "opening_pending": opening_prog["pending"],
        }
        pattern_totals = {
            "pattern": db.count_pattern_clusters("pattern"),
            "joseki": db.count_pattern_clusters("joseki"),
            "fuseki": db.count_pattern_clusters("fuseki"),
        }
        drilldown = db.dashboard_drilldown()
        html = render_dashboard(
            stats, [], {}, username, drilldown, db,
            db.timing_stats(),
            pattern_totals=pattern_totals,
            filter_meta=db.filter_metadata(),
            games_analyzed=db.count_analyzed_games(),
            suspect_count=db.count_suspicious_opponents(),
        )
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/games", response_class=HTMLResponse)
    def games_page(limit: int | str = 25):
        username = cfg["player"]["kgs_username"]
        page_limit = parse_list_limit(limit)
        total = db.count_analyzed_games()
        games = db.dashboard_games(limit=page_limit)
        game_ids = [g["id"] for g in games]
        pattern_recurrence = db.pattern_recurrence_for_games(game_ids)
        html = render_games_page(
            games, username, db, pattern_recurrence,
            limit=page_limit, total=total,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/patterns/kind/{kind_key}", response_class=HTMLResponse)
    def patterns_kind_page(kind_key: str, limit: int | str = 25):
        username = cfg["player"]["kgs_username"]
        meta_kind = {
            "recurrents": "pattern",
            "joseki": "joseki",
            "fuseki": "fuseki",
        }.get(kind_key)
        if not meta_kind:
            raise HTTPException(404, "Section inconnue")
        page_limit = parse_list_limit(limit)
        total = db.count_pattern_clusters(meta_kind)
        patterns = db.pattern_clusters_list(meta_kind, limit=page_limit)
        cluster_ids = [int(c["id"]) for c in patterns]
        occ = db.pattern_clusters_occurrences(cluster_ids) if cluster_ids else {}
        html = render_patterns_kind_page(
            kind_key, patterns, username, db, occ,
            limit=page_limit, total=total,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/alerte", response_class=HTMLResponse)
    def alerte_page(limit: int | str = 25, opponent: str | None = None):
        username = cfg["player"]["kgs_username"]
        all_opponents = db.opponent_suspicion_stats()
        total = len(all_opponents)
        page_limit = parse_list_limit(limit)
        if opponent:
            shown = [o for o in all_opponents if o.get("opponent") == opponent]
            if not shown:
                shown = all_opponents[: page_limit or len(all_opponents)]
        else:
            shown = all_opponents if page_limit is None else all_opponents[:page_limit]
        moves_limit = 100 if opponent else 50
        moves = db.suspicious_opponent_moves(
            opponent=opponent, limit=moves_limit,
        )
        player_stats = db.player_move_quality_stats()
        html = render_alerte_page(
            shown, moves, username, db,
            limit=page_limit, total=total,
            selected_opponent=opponent,
            player_stats=player_stats,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/patterns", response_class=HTMLResponse)
    def patterns_page():
        username = cfg["player"]["kgs_username"]
        all_patterns = db.all_pattern_clusters()
        cluster_ids = [int(c["id"]) for c in all_patterns]
        occurrences = db.pattern_clusters_occurrences(cluster_ids) if cluster_ids else {}
        html = render_patterns_explorer(
            all_patterns, username, db,
            occurrences_by_cluster=occurrences,
        )
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/api/drilldown")
    def api_drilldown(
        opponent: str | None = None,
        year: int | None = None,
        outcome: str | None = None,
        color: str | None = None,
        scope: str | None = None,
    ):
        return db.dashboard_drilldown_filtered(
            opponent=opponent,
            year=year,
            outcome=outcome,
            player_color=color,
            error_scope=scope,
            fuseki_max_move=FUSEKI_MAX_MOVE,
        )

    @app.get("/api/stats")
    def api_stats():
        return {
            **db.stats_summary(),
            "pending_analysis": db.pending_analysis_count(),
            "last_sync": db.get_sync_value("last_sync_at"),
            "synced_months": len(db.synced_months_summary()),
        }

    @app.get("/api/games")
    def api_games():
        return db.dashboard_games(limit=80)

    @app.get("/api/blunders")
    def api_blunders():
        rows = db.top_blunders(limit=20, player=cfg["player"]["kgs_username"])
        return [dict(r) for r in rows]

    @app.get("/api/patterns/{cluster_id}/diagram", response_class=HTMLResponse)
    def pattern_diagram(cluster_id: int, width: int = 400):
        from goprogress.board_diagram import patch_ascii_to_svg

        row = db.get_pattern_cluster(cluster_id)
        if not row or not row["patch_ascii"]:
            raise HTTPException(404, "Pattern introuvable")
        w = max(180, min(880, width))
        return patch_ascii_to_svg(row["patch_ascii"], display_width=w) or "<!-- empty -->"

    @app.get("/api/moves/{move_id}/diagram", response_class=HTMLResponse)
    def move_diagram(move_id: int, cell: int = 11):
        from goprogress.position_diagram import svg_for_move_cached
        cell = max(8, min(22, cell))
        return svg_for_move_cached(db, move_id, cell=cell) or "<!-- empty -->"

    @app.get("/api/moves/{move_id}")
    def api_move(move_id: int):
        row = db.get_move(move_id)
        if not row:
            raise HTTPException(404, "Coup introuvable")
        return dict(row)

    @app.get("/review/{move_id}/sgf")
    def review_sgf(move_id: int):
        try:
            path = generate_review_for_move(db, cfg, move_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="application/x-go-sgf", filename=path.name)

    @app.get("/review/{move_id}/open", response_class=HTMLResponse)
    def review_open(move_id: int, tool: str = "lizzie"):
        try:
            path = open_move_review(db, cfg, move_id, tool=tool)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(500, str(exc)) from exc
        tool_label = "Lizzie" if tool == "lizzie" else "KaTrain"
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>{tool_label} ouvert</title>
<style>body{{font-family:"Source Sans 3",system-ui,sans-serif;max-width:600px;margin:3rem auto;padding:1.5rem;background:#e8dfd0;color:#2a2118;line-height:1.5}}
h1{{font-family:"Fraunces",Georgia,serif;color:#2a2118}}
a{{color:#6b4423;font-weight:600}}</style></head><body>
<h1>{tool_label} lancé</h1>
<p>Position du coup #{move_id} — fichier <code>{path.name}</code></p>
<p>Si {tool_label} ne s'ouvre pas, vérifiez <code>config.yaml</code> → section <code>review.lizzie</code>.</p>
<p><a href="/review/{move_id}/sgf">Télécharger le SGF review</a> · <a href="/">Retour dashboard</a></p>
</body></html>""")

    host = cfg["dashboard"]["host"]
    port = cfg["dashboard"]["port"]
    print(f"Dashboard local : http://{host}:{port}")
    print("  Boutons Lizzie -> ouvrent la position au bon coup (serveur doit rester actif).")
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
    p_sync.add_argument("--since-year", type=int, help="Sync tous les mois depuis cette année")
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
    p_analyze.add_argument(
        "--backfill-opponent",
        action="store_true",
        help="Compléter les stats adversaire sur parties déjà analysées",
    )
    _since_year_arg(p_analyze)
    p_analyze.set_defaults(func=cmd_analyze)

    p_reset = sub.add_parser(
        "reset-analysis",
        help="Effacer toutes les analyses (garde SGF et parties)",
    )
    p_reset.add_argument("--yes", action="store_true", help="Sans confirmation")
    p_reset.set_defaults(func=cmd_reset)

    p_v2 = sub.add_parser(
        "prepare-v2-analysis",
        help="Prepare re-analyse pipeline v2 (labo GPU : quick + fuseki F6)",
    )
    p_v2.add_argument("--since-year", type=int, help="Limiter a une annee")
    p_v2.add_argument(
        "--from-game-id", type=int,
        help="Reprendre a partir de cette partie",
    )
    p_v2.add_argument(
        "--full", action="store_true",
        help="Reset complet (efface tous les coups, re-quick obligatoire)",
    )
    p_v2.add_argument("--yes", action="store_true", help="Sans confirmation (--full)")
    p_v2.set_defaults(func=cmd_prepare_v2)

    p_restore = sub.add_parser(
        "restore-analysis",
        help="Réimporter les analyses depuis data/analysis/*.json",
    )
    p_restore.add_argument("--game-id", type=int, help="Une seule partie (#id)")
    p_restore.add_argument("--since-year", type=int, default=2024)
    p_restore.set_defaults(func=cmd_restore_analysis)

    p_re = sub.add_parser(
        "reanalysis",
        help="Lot re-analyse : quick + fuseki F6 (+ enrichissements finaux)",
    )
    p_re.add_argument("--since-year", type=int, default=2024)
    p_re.add_argument("--batch", type=int, default=5, help="Parties par lot")
    p_re.add_argument(
        "--from-game-id", type=int,
        help="Reprendre a partir de cette partie (defaut: analysis.continue_from_game_id)",
    )
    p_re.add_argument("--reset", action="store_true", help="Reset avant ce lot")
    p_re.add_argument(
        "--finalize-only",
        action="store_true",
        help="Enrich + Human SL + patterns (quand tout est analysé)",
    )
    p_re.add_argument("--human-limit", type=int, default=500)
    p_re.add_argument(
        "--loop",
        action="store_true",
        help="Boucle infinie jusqu'a fin de la periode",
    )
    p_re.add_argument(
        "--katago-restart-hours",
        type=float,
        default=1.0,
        help="Redemarrage preventif KataGo (defaut: 1 h)",
    )
    p_re.set_defaults(func=cmd_reanalysis)

    p_open = sub.add_parser(
        "opening-analysis",
        help="Analyse fuseki F6 : re-analyse ciblee apres quick (1000/2000v)",
    )
    p_open.add_argument("--since-year", type=int, default=2024, help="Limiter par annee")
    p_open.add_argument("--batch", type=int, default=5, help="Parties par lot")
    p_open.add_argument(
        "--supplement",
        action="store_true",
        help="Complement parties 1-47 (config supplement_max_game_id)",
    )
    p_open.add_argument(
        "--from-game-id", type=int,
        help="ID min (defaut 48 pour la boucle principale)",
    )
    p_open.add_argument("--to-game-id", type=int, help="ID max (ex. 47 pour supplement)")
    p_open.add_argument(
        "--all-games",
        action="store_true",
        help="Toutes les parties (sans filtre id min)",
    )
    p_open.add_argument(
        "--loop",
        action="store_true",
        help="Boucle infinie jusqu'a fin de la periode",
    )
    p_open.add_argument(
        "--force",
        action="store_true",
        help="Re-analyser meme si deja marque analyzed_opening",
    )
    p_open.add_argument(
        "--katago-restart-hours",
        type=float,
        default=1.0,
        help="Redemarrage preventif KataGo (defaut: 1 h)",
    )
    p_open.set_defaults(func=cmd_opening_analysis)

    p_report = sub.add_parser("report", help="Générer rapport HTML")
    p_report.add_argument("--open", action="store_true", help="Ouvrir dans le navigateur")
    p_report.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status", help="Afficher le statut")
    p_status.set_defaults(func=cmd_status)

    p_katago = sub.add_parser("katago-check", help="Vérifier config et débit GPU KataGo")
    p_katago.add_argument("--no-benchmark", action="store_true", help="Sans test de débit")
    p_katago.set_defaults(func=cmd_katago_check)

    p_enrich = sub.add_parser("enrich", help="Rangs + thèmes sur parties déjà analysées")
    p_enrich.add_argument("--report", action="store_true", help="Régénérer le rapport")
    _since_year_arg(p_enrich)
    p_enrich.set_defaults(func=cmd_enrich)

    p_human = sub.add_parser("human-sl", help="Human SL : prior par rang sur vos erreurs")
    p_human.add_argument("--limit", type=int, default=100, help="Nb coups suspects")
    p_human.add_argument("--min-loss", type=float, help="Perte min globale (pts)")
    p_human.add_argument(
        "--by-game",
        action="store_true",
        help="Remplir Human SL partie par partie (après restore)",
    )
    p_human.add_argument("--report", action="store_true", help="Régénérer le rapport")
    _since_year_arg(p_human)
    p_human.set_defaults(func=cmd_human_sl)

    p_patterns = sub.add_parser("patterns", help="Clustering local des blunders")
    p_patterns.add_argument("--report", action="store_true", help="Régénérer le rapport")
    _since_year_arg(p_patterns)
    p_patterns.set_defaults(func=cmd_patterns)

    p_pro = sub.add_parser("pro-patterns", help="Contexte pro GoGoD sur blunders thématiques")
    p_pro.add_argument("--limit", type=int, help="Nb max de blunders à traiter")
    p_pro.add_argument("--rebuild-index", action="store_true", help="Reconstruire l'index patch")
    p_pro.add_argument("--report", action="store_true", help="Régénérer le rapport")
    _since_year_arg(p_pro)
    p_pro.set_defaults(func=cmd_pro_patterns)

    p_pro_idx = sub.add_parser("build-pro-index", help="Construire l'index patch GoGoD")
    p_pro_idx.add_argument("--force", action="store_true", help="Reconstruire même si existant")
    p_pro_idx.set_defaults(func=cmd_build_pro_index)

    p_review = sub.add_parser("review", help="Générer SGF review d'un blunder")
    p_review.add_argument("--move-id", type=int, required=True, help="ID du coup (table moves)")
    p_review.add_argument("--open", action="store_true", help="Ouvrir Lizzie ou KaTrain")
    p_review.add_argument("--tool", choices=("lizzie", "katrain"), default="lizzie")
    p_review.set_defaults(func=cmd_review)

    p_gogod = sub.add_parser("import-gogod", help="Importer corpus GoGoD depuis Downloads")
    p_gogod.add_argument(
        "--downloads-dir",
        help="Dossier des zip GoGoD (défaut: ~/Downloads ou config)",
    )
    p_gogod.add_argument(
        "--skip-extract",
        action="store_true",
        help="Ne pas extraire — seulement recompter le manifeste",
    )
    p_gogod.set_defaults(func=cmd_import_gogod)

    p_serve = sub.add_parser("serve", help="Lancer dashboard local")
    p_serve.set_defaults(func=cmd_serve)

    p_publish = sub.add_parser(
        "publish",
        help="Exporter snapshot HTML statique (data/publish/)",
    )
    p_publish.add_argument(
        "--deploy", action="store_true",
        help="Pousser aussi sur Cloudflare Pages (miroir)",
    )
    p_publish.set_defaults(func=cmd_publish)

    p_sync_cf = sub.add_parser(
        "sync-cloudflare",
        help="Miroir dashboard → Cloudflare Pages (24/7 mobile)",
    )
    p_sync_cf.set_defaults(func=cmd_sync_cloudflare)

    p_reconcile = sub.add_parser(
        "reconcile-scores",
        help="Recalculer pertes/sévérités depuis les scores KataGo stockés",
    )
    p_reconcile.add_argument(
        "--no-publish", action="store_true",
        help="Ne pas regénérer data/publish/ après le recalcul",
    )
    p_reconcile.set_defaults(func=cmd_reconcile_scores)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
